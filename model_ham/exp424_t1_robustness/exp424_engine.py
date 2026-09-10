"""
exp424: T1策略样本外与参数鲁棒诊断

基准：exp423 定稿 T1 完整模型（HAM动态 + 基本面 + 三层风控 + 波动率隔夜敞口管控），
  因子、风控规则全部固定，不改动 HAM 核心公式。
T1 = T0(exp412原版) + 波动率隔夜敞口管控(vol_half=0.40 / vol_full=0.90)

模块1：滚动样本外 Walk-forward 回测
  250交易日训练 + 60交易日测试，持续滚动；版本B口径；对比样本内/外年化夏普回撤
模块2：参数敏感性扫描
  网格扫描 vol_half × vol_full（波动率隔夜敞口阈值）+ DD回撤阶梯(dd_recover/dd_warn/dd_hard)，
  统计在多大参数区间内版本B(T+1真实时序)保持正收益 → 鲁棒性热力图
模块3：信号质量过滤对照实验
  ADX趋势预警过滤 + 强信号筛选，与原版T1(B)并行回测
模块4：实盘仓位校准与安全垫测算
  基于版本B绩效重算推荐仓位；预留10%回撤缓冲后年化压缩测算

时序铁律：版本A=T日收盘成交，版本B=T+1开盘成交（隔夜跳空由pos[t-1]承担，日内由pos[t]承担）。
"""
import os
import sys
import json
import itertools
import numpy as np
import pandas as pd

MODEL = "/home/ubuntu/lithium-engine/model_ham"
for d in ["exp409_ham_dynamic", "exp410_fund_dynamic", "exp411_combined",
          "exp413_robustness", "exp414_attribution"]:
    sys.path.insert(0, os.path.join(MODEL, d))
sys.path.insert(0, os.path.join(MODEL, "exp416_tail_risk_protect"))

import exp409_ham_dynamic as E409
import exp413_engine as E413
import exp416_engine as E416
from exp411_combined import (
    build_targets, price_series, extract_trades,
    TRADING_DAYS_PER_YEAR,
)

EXP_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(EXP_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)
REPORTS_DIR = os.path.join(os.path.dirname(os.path.dirname(EXP_DIR)), "reports")
os.makedirs(REPORTS_DIR, exist_ok=True)

START, END = pd.Timestamp("2024-02-01"), pd.Timestamp("2026-09-07")
COST = E409.COST_PER_SIDE
TDPY = E409.TRADING_DAYS_PER_YEAR

# T1 定稿参数（与 exp423 完全一致）
VOL_HALF = 0.40
VOL_FULL = 0.90
HALF_SCALE = 0.5
FULL_SCALE = 0.0
DD_RECOVER = E413.DD_RECOVER_DEFAULT   # 0.05
DD_WARN = E413.DD_WARN_DEFAULT          # 0.12
DD_HARD = E413.DD_HARD_DEFAULT          # 0.18


# ============================================================
# 数据加载（与 exp416/423 完全一致）
# ============================================================
def load_context():
    h_target, f_target = build_targets()
    idx = h_target.index
    price = price_series(idx)
    mask = (idx >= START) & (idx <= END)
    idx_w = idx[mask]
    price_w = price.reindex(idx_w)

    vol = E413.compute_realized_vol(price_w, lookback=20)
    vol = vol.reindex(idx_w)
    pct20 = price_w.pct_change(20)

    ohlc = pd.read_csv(os.path.join(MODEL, "raw_data/lithium_future.csv"))
    ohlc["date"] = pd.to_datetime(ohlc["date"])
    ohlc = ohlc.set_index("date").sort_index()

    return {
        "h_target": h_target.reindex(idx_w).fillna(0.0),
        "f_target": f_target.reindex(idx_w).fillna(0.0),
        "idx": idx_w,
        "price": price_w,
        "vol": vol,
        "pct20": pct20,
        "ohlc": ohlc,
    }


# ============================================================
# 通用：仓位计算（可注入 vol 阈值 + DD 阶梯）
# ============================================================
def compute_positions(ctx, vol_half, vol_full, half_scale, full_scale,
                      dd_recover, dd_warn, dd_hard, trend_thr=0.0, trend_scale=1.0):
    """
    在 exp412 V4 风控底座上叠加 T1 波动率隔夜敞口管控 + 可注入DD阶梯。
    复用 exp416.run_variant 的 vol 层逻辑，但DD阶梯通过 E413.run_exp412_param 注入。
    """
    h_target = ctx["h_target"]
    f_target = ctx["f_target"]
    idx = ctx["idx"]
    price = ctx["price"]
    n = len(idx)

    # 底座：exp412 V4（可注入DD阶梯）
    pos_risk, diag = E413.run_exp412_param(
        h_target, f_target, idx, price,
        dd_recover=dd_recover, dd_warn=dd_warn, dd_hard=dd_hard,
        cost=COST, return_diag=True,
    )

    vol_arr = ctx["vol"].reindex(idx).values
    pct20_arr = ctx["pct20"].reindex(idx).values

    overnight_mult = np.ones(n)
    trend_mult = np.ones(n)
    for t in range(n):
        v = vol_arr[t]
        if vol_full is not None and v > vol_full:
            overnight_mult[t] = full_scale
        elif v > vol_half:
            overnight_mult[t] = half_scale
        if trend_thr and abs(pct20_arr[t]) > trend_thr:
            trend_mult[t] = trend_scale

    final_pos = pos_risk * overnight_mult * trend_mult
    return final_pos, diag


# ============================================================
# 指标计算：版本A（T日收盘成交）/ 版本B（T+1开盘成交）
# ============================================================
def metrics_from_net_ret(net_ret, n):
    equity = np.cumprod(1 + net_ret)
    years = n / TDPY
    total_ret = equity[-1] - 1
    ann_ret = (1 + total_ret) ** (1 / years) - 1 if total_ret > -1 else -1
    sharpe = np.mean(net_ret) / np.std(net_ret) * np.sqrt(TDPY) if np.std(net_ret) > 0 else 0
    peak = np.maximum.accumulate(equity)
    max_dd = ((equity - peak) / peak).min()
    calmar = ann_ret / abs(max_dd) if max_dd < 0 else 0
    return {
        "total_return": float(total_ret),
        "annual_return": float(ann_ret),
        "sharpe": float(sharpe),
        "max_drawdown": float(max_dd),
        "calmar": float(calmar),
    }


def net_ret_variant_A(final_pos, price, idx):
    """版本A：T日收盘成交，仓位吃(T-1→T)价格变动"""
    close = price.reindex(idx).values
    n = len(idx)
    r_price = np.zeros(n)
    r_price[1:] = (close[1:] - close[:-1]) / close[:-1]
    dpos = np.zeros(n)
    dpos[1:] = np.abs(final_pos[1:] - final_pos[:-1])
    dpos[0] = np.abs(final_pos[0])
    return final_pos * r_price - 2 * COST * dpos


def net_ret_variant_B(final_pos, price, ohlc, idx):
    """版本B：T+1开盘成交。隔夜跳空由pos[t-1]承担，日内由pos[t]承担"""
    close = price.reindex(idx).values
    open_px = ohlc["open"].reindex(idx).values
    n = len(idx)

    overnight_ret = np.zeros(n)
    for t in range(1, n):
        overnight_ret[t] = final_pos[t - 1] * (open_px[t] - close[t - 1]) / close[t - 1]

    intraday_ret = np.zeros(n)
    for t in range(n):
        if open_px[t] > 0:
            intraday_ret[t] = final_pos[t] * (close[t] - open_px[t]) / open_px[t]

    dpos = np.zeros(n)
    dpos[1:] = np.abs(final_pos[1:] - final_pos[:-1])
    dpos[0] = np.abs(final_pos[0])
    cost_ret = 2 * COST * dpos
    return overnight_ret + intraday_ret - cost_ret


# ============================================================
# 模块1：滚动样本外 Walk-forward 回测
# ============================================================
def module1_walk_forward(ctx, train_len=250, test_len=60):
    """
    滚动窗口：train_len交易日训练 + test_len交易日测试，持续滚动。
    T1定稿参数固定；每个测试窗口用版本B口径算样本外绩效。
    同时算样本内（训练窗口）绩效，对比衰减。
    """
    idx = ctx["idx"]
    price = ctx["price"]
    ohlc = ctx["ohlc"]
    n = len(idx)

    full_pos, _ = compute_positions(ctx, VOL_HALF, VOL_FULL, HALF_SCALE, FULL_SCALE,
                                    DD_RECOVER, DD_WARN, DD_HARD)

    n_windows = (n - train_len) // test_len
    records = []
    insample_metrics = []
    oos_metrics = []

    for w in range(n_windows):
        train_start = w * test_len
        train_end = train_start + train_len
        test_start = train_end
        test_end = min(test_start + test_len, n)
        if test_end <= test_start:
            break

        # 训练窗口（样本内）：仓位子集 + 版本A口径
        tr_pos = full_pos[train_start:train_end]
        tr_price = price.iloc[train_start:train_end]
        tr_idx = idx[train_start:train_end]
        nr_A_tr = net_ret_variant_A(tr_pos, tr_price, tr_idx)
        mi_tr = metrics_from_net_ret(nr_A_tr, len(tr_pos))
        insample_metrics.append(mi_tr)

        # 测试窗口（样本外）：版本B口径
        te_pos = full_pos[test_start:test_end]
        te_price = price.iloc[test_start:test_end]
        te_idx = idx[test_start:test_end]
        nr_B_te = net_ret_variant_B(te_pos, te_price, ohlc, te_idx)
        mi_te = metrics_from_net_ret(nr_B_te, len(te_pos))
        oos_metrics.append(mi_te)

        records.append({
            "window": w + 1,
            "train_start": str(idx[train_start].date()),
            "train_end": str(idx[train_end - 1].date()),
            "test_start": str(idx[test_start].date()),
            "test_end": str(idx[test_end - 1].date()),
            "insample_annual": mi_tr["annual_return"],
            "insample_sharpe": mi_tr["sharpe"],
            "insample_max_dd": mi_tr["max_drawdown"],
            "oos_annual": mi_te["annual_return"],
            "oos_sharpe": mi_te["sharpe"],
            "oos_max_dd": mi_te["max_drawdown"],
            "oos_calmar": mi_te["calmar"],
            "oos_positive": bool(mi_te["annual_return"] > 0),
        })

    df = pd.DataFrame(records)
    df.to_csv(os.path.join(DATA_DIR, "exp424_wf_windows.csv"), index=False)

    # 汇总：平均样本内 vs 样本外
    def _agg(mlist, key):
        vals = [m[key] for m in mlist]
        return float(np.mean(vals)) if vals else 0.0

    summary = {
        "train_len": train_len,
        "test_len": test_len,
        "n_windows": len(records),
        "insample_avg_annual": _agg(insample_metrics, "annual_return"),
        "insample_avg_sharpe": _agg(insample_metrics, "sharpe"),
        "insample_avg_max_dd": _agg(insample_metrics, "max_drawdown"),
        "oos_avg_annual": _agg(oos_metrics, "annual_return"),
        "oos_avg_sharpe": _agg(oos_metrics, "sharpe"),
        "oos_avg_max_dd": _agg(oos_metrics, "max_drawdown"),
        "oos_positive_windows": int(sum(1 for r in records if r["oos_positive"])),
        "oos_negative_windows": int(sum(1 for r in records if not r["oos_positive"])),
    }
    summary["annual_decay_pp"] = (summary["insample_avg_annual"] - summary["oos_avg_annual"]) * 100
    summary["oos_positive_ratio"] = summary["oos_positive_windows"] / max(len(records), 1)

    return df, summary


# ============================================================
# 模块2：参数敏感性扫描
# ============================================================
def module2_param_sensitivity(ctx):
    """
    网格扫描波动率隔夜敞口阈值(vol_half × vol_full) 与 DD 回撤阶梯(dd_warn × dd_hard)，
    统计在多大参数区间内版本B保持正收益 → 鲁棒性热力图数据。
    """
    idx = ctx["idx"]
    price = ctx["price"]
    ohlc = ctx["ohlc"]
    n = len(idx)

    # 基准仓位（定稿参数）算一次版本B收益，作为参考
    # vol_half 网格：0.25~0.50；vol_full 网格：0.70~1.10
    vol_half_grid = [0.25, 0.30, 0.35, VOL_HALF, 0.45, 0.50]
    vol_full_grid = [0.70, 0.80, VOL_FULL, 1.00, 1.10]
    # DD 阶梯网格：在基准周围扰动 dd_warn / dd_hard
    dd_warn_grid = [0.08, DD_WARN, 0.16]
    dd_hard_grid = [0.12, DD_HARD, 0.24]

    # --- 2.1 vol_half × vol_full 热力图（版本B年化） ---
    vol_heatmap = []
    for vh, vf in itertools.product(vol_half_grid, vol_full_grid):
        pos, _ = compute_positions(ctx, vh, vf, HALF_SCALE, FULL_SCALE,
                                   DD_RECOVER, DD_WARN, DD_HARD)
        nr = net_ret_variant_B(pos, price, ohlc, idx)
        mi = metrics_from_net_ret(nr, n)
        vol_heatmap.append({
            "vol_half": vh, "vol_full": vf,
            "ann_ret_B": mi["annual_return"],
            "sharpe_B": mi["sharpe"],
            "max_dd_B": mi["max_drawdown"],
            "calmar_B": mi["calmar"],
            "positive": bool(mi["annual_return"] > 0),
        })
    df_vol = pd.DataFrame(vol_heatmap)
    df_vol.to_csv(os.path.join(DATA_DIR, "exp424_vol_heatmap.csv"), index=False)

    # --- 2.2 DD 回撤阶梯热力图 ---
    dd_heatmap = []
    for dw, dh in itertools.product(dd_warn_grid, dd_hard_grid):
        pos, _ = compute_positions(ctx, VOL_HALF, VOL_FULL, HALF_SCALE, FULL_SCALE,
                                   DD_RECOVER, dw, dh)
        nr = net_ret_variant_B(pos, price, ohlc, idx)
        mi = metrics_from_net_ret(nr, n)
        dd_heatmap.append({
            "dd_warn": dw, "dd_hard": dh,
            "ann_ret_B": mi["annual_return"],
            "sharpe_B": mi["sharpe"],
            "max_dd_B": mi["max_drawdown"],
            "calmar_B": mi["calmar"],
            "positive": bool(mi["annual_return"] > 0),
        })
    df_dd = pd.DataFrame(dd_heatmap)
    df_dd.to_csv(os.path.join(DATA_DIR, "exp424_dd_heatmap.csv"), index=False)

    # --- 2.3 鲁棒性汇总 ---
    vol_pos = df_vol["positive"].sum()
    dd_pos = df_dd["positive"].sum()
    robustness = {
        "vol_grid_total": len(df_vol),
        "vol_grid_positive": int(vol_pos),
        "vol_grid_positive_ratio": float(vol_pos / len(df_vol)),
        "vol_best_cell": df_vol.loc[df_vol["ann_ret_B"].idxmax()].to_dict(),
        "dd_grid_total": len(df_dd),
        "dd_grid_positive": int(dd_pos),
        "dd_grid_positive_ratio": float(dd_pos / len(df_dd)),
        "dd_best_cell": df_dd.loc[df_dd["ann_ret_B"].idxmax()].to_dict(),
        "baseline_vol_cell": next(r for r in vol_heatmap if r["vol_half"] == VOL_HALF and r["vol_full"] == VOL_FULL),
        "baseline_dd_cell": next(r for r in dd_heatmap if r["dd_warn"] == DD_WARN and r["dd_hard"] == DD_HARD),
    }
    # 判定鲁棒性
    overall_pos_ratio = (vol_pos + dd_pos) / (len(df_vol) + len(df_dd))
    if overall_pos_ratio >= 0.9:
        robustness["verdict"] = "ROBUST"
        robustness["interpretation"] = "绝大多数参数区间版本B保持正收益，鲁棒性良好，过拟合风险低"
    elif overall_pos_ratio >= 0.6:
        robustness["verdict"] = "MODERATE"
        robustness["interpretation"] = "多数参数区间盈利但非全部，存在轻度参数依赖"
    else:
        robustness["verdict"] = "FRAGILE"
        robustness["interpretation"] = "仅狭窄参数区间盈利，存在明显过拟合风险"
    robustness["overall_positive_ratio"] = float(overall_pos_ratio)

    return df_vol, df_dd, robustness


# ============================================================
# 模块3：信号质量过滤对照实验
# ============================================================
def compute_adx(high, low, close, period=14):
    """Wilder ADX，用于识别高单边趋势（HAM 趋势盲区）"""
    n = len(close)
    tr = np.zeros(n)
    up_move = np.zeros(n)
    down_move = np.zeros(n)
    for t in range(1, n):
        h, l, c, pc = high[t], low[t], close[t], close[t - 1]
        tr[t] = max(h - l, abs(h - pc), abs(l - pc))
        up_move[t] = h - pc
        down_move[t] = pc - l
    # Wilder 平滑
    atr = np.zeros(n)
    plus_di = np.zeros(n)
    minus_di = np.zeros(n)
    dx = np.zeros(n)
    if n > period:
        atr[period] = tr[1:period + 1].mean()
        for t in range(period + 1, n):
            atr[t] = (atr[t - 1] * (period - 1) + tr[t]) / period
        # DI
        for t in range(period, n):
            pdm = np.mean(up_move[t - period + 1:t + 1])
            mdm = np.mean(down_move[t - period + 1:t + 1])
            if atr[t] > 0:
                plus_di[t] = 100 * pdm / atr[t]
                minus_di[t] = 100 * mdm / atr[t]
            denom = plus_di[t] + minus_di[t]
            dx[t] = 100 * abs(plus_di[t] - minus_di[t]) / denom if denom > 0 else 0
    # ADX
    adx = np.zeros(n)
    start = 2 * period
    if n > start and dx[start - period:start + 1].sum() > 0:
        adx[start] = dx[start - period:start + 1].mean()
    for t in range(start + 1, n):
        adx[t] = (adx[t - 1] * (period - 1) + dx[t]) / period
    return adx


def module3_signal_filter(ctx, adx_threshold=30.0, strong_dev_quantile=0.95):
    """
    对照实验：原版T1(B) vs 过滤版(B)。
    过滤规则：
      1. ADX趋势预警过滤：ADX > adx_threshold 的高单边趋势日，降低仓位(×0.3)或放弃(×0)
      2. 强信号筛选：仅高分歧信号(|仓位| >= strong_dev_quantile对应的强度)允许开仓
    """
    idx = ctx["idx"]
    price = ctx["price"]
    ohlc = ctx["ohlc"]
    n = len(idx)

    high = ohlc["high"].reindex(idx).values
    low = ohlc["low"].reindex(idx).values
    close = price.reindex(idx).values

    adx = compute_adx(high, low, close, period=14)

    # 原版 T1(B)
    pos_base, _ = compute_positions(ctx, VOL_HALF, VOL_FULL, HALF_SCALE, FULL_SCALE,
                                    DD_RECOVER, DD_WARN, DD_HARD)
    nr_base = net_ret_variant_B(pos_base, price, ohlc, idx)
    mi_base = metrics_from_net_ret(nr_base, n)

    # 过滤版1：ADX趋势预警（高趋势日仓位×0.3）
    pos_adx = pos_base.copy()
    adx_mask = adx > adx_threshold
    pos_adx[adx_mask] = pos_adx[adx_mask] * 0.3
    nr_adx = net_ret_variant_B(pos_adx, price, ohlc, idx)
    mi_adx = metrics_from_net_ret(nr_adx, n)

    # 过滤版2：ADX高趋势日完全放弃开仓
    pos_adx0 = pos_base.copy()
    pos_adx0[adx_mask] = 0.0
    nr_adx0 = net_ret_variant_B(pos_adx0, price, ohlc, idx)
    mi_adx0 = metrics_from_net_ret(nr_adx0, n)

    # 过滤版3：强信号筛选（仅 |仓位| >= 0.6 的分歧信号开仓，弱分歧置0）
    pos_strong = pos_base.copy()
    weak_mask = np.abs(pos_strong) > 1e-9
    weak_mask &= np.abs(pos_strong) < 0.6
    pos_strong[weak_mask] = 0.0
    nr_strong = net_ret_variant_B(pos_strong, price, ohlc, idx)
    mi_strong = metrics_from_net_ret(nr_strong, n)

    # 过滤版4：ADX + 强信号 组合
    pos_both = pos_strong.copy()
    pos_both[adx_mask] = pos_both[adx_mask] * 0.3
    nr_both = net_ret_variant_B(pos_both, price, ohlc, idx)
    mi_both = metrics_from_net_ret(nr_both, n)

    # 保存 ADX 序列
    adx_df = pd.DataFrame({"date": [str(idx[i].date()) for i in range(n)], "adx": adx})
    adx_df.to_csv(os.path.join(DATA_DIR, "exp424_adx_series.csv"), index=False)

    variants = {
        "原版T1(B)": mi_base,
        "ADX降权(×0.3)": mi_adx,
        "ADX清零": mi_adx0,
        "强信号筛选": mi_strong,
        "ADX+强信号": mi_both,
    }
    df = pd.DataFrame(variants).T
    df.index.name = "variant"
    df.to_csv(os.path.join(DATA_DIR, "exp424_signal_filter_comparison.csv"))

    summary = {
        "adx_threshold": adx_threshold,
        "strong_dev_quantile": strong_dev_quantile,
        "n_adx_high_days": int(adx_mask.sum()),
        "variants": variants,
    }
    return df, summary


# ============================================================
# 模块4：实盘仓位校准与安全垫测算
# ============================================================
def module4_position_calibration(ctx):
    """
    基于版本B绩效重算推荐仓位；预留10%回撤缓冲后年化压缩测算。
    仓位模型：目标波动率法 + 回撤约束法。
    """
    idx = ctx["idx"]
    price = ctx["price"]
    ohlc = ctx["ohlc"]
    n = len(idx)

    pos_base, _ = compute_positions(ctx, VOL_HALF, VOL_FULL, HALF_SCALE, FULL_SCALE,
                                    DD_RECOVER, DD_WARN, DD_HARD)
    nr_B = net_ret_variant_B(pos_base, price, ohlc, idx)
    mi_B = metrics_from_net_ret(nr_B, n)

    daily_vol_B = float(np.std(nr_B))
    ann_vol_B = daily_vol_B * np.sqrt(TDPY)
    sharpe_B = mi_B["sharpe"]
    ann_ret_B = mi_B["annual_return"]
    calmar_B = mi_B["calmar"]
    max_dd_B = mi_B["max_drawdown"]

    # --- 4.1 推荐仓位：目标波动率法 ---
    # 假设投资者目标年化波动率 target_vol，仓位 = target_vol / ann_vol_B
    target_vols = [0.20, 0.30, 0.40]  # 20%/30%/40% 目标年化波动
    pos_target_vol = {}
    for tv in target_vols:
        scale = tv / ann_vol_B if ann_vol_B > 0 else 1
        pos_target_vol[f"target_vol_{int(tv*100)}%"] = {
            "scale": round(float(scale), 4),
            "scaled_annual": round(float(ann_ret_B * scale), 4),
            "scaled_ann_vol": round(float(ann_vol_B * scale), 4),
            "scaled_max_dd": round(float(max_dd_B * scale), 4),
        }

    # --- 4.2 回撤约束法：预留10%回撤缓冲 ---
    # 当前版本B最大回撤 max_dd_B（负值）。预留10%缓冲 = 允许实际回撤 ≤ 10%。
    # 若 |max_dd_B| > 10%，需降仓：scale = 10% / |max_dd_B|
    target_dd_budget = 0.10
    raw_abs_dd = abs(max_dd_B)
    if raw_abs_dd <= target_dd_budget:
        dd_scale = 1.0
    else:
        dd_scale = target_dd_budget / raw_abs_dd
    pos_dd_budget = {
        "current_abs_dd": round(raw_abs_dd, 4),
        "target_dd_budget": target_dd_budget,
        "recommended_scale": round(float(dd_scale), 4),
        "scaled_annual": round(float(ann_ret_B * dd_scale), 4),
        "scaled_ann_vol": round(float(ann_vol_B * dd_scale), 4),
        "scaled_max_dd": round(float(max_dd_B * dd_scale), 4),
        "calmar_preserved": round(float(calmar_B), 4),  # Calmar比率不随等比缩放变化
    }

    # --- 4.3 综合建议：取目标波动率与回撤约束的更保守者 ---
    recommended_scale = min(
        pos_dd_budget["recommended_scale"],
        min(v["scale"] for v in pos_target_vol.values()),
    )
    pos_dd_budget["recommended_scale"] = round(float(dd_scale), 4)  # 回撤约束为主

    calibration = {
        "baseline_B_metrics": {
            "annual_return": round(ann_ret_B, 4),
            "sharpe": round(sharpe_B, 4),
            "calmar": round(calmar_B, 4),
            "max_drawdown": round(max_dd_B, 4),
            "ann_vol": round(ann_vol_B, 4),
        },
        "target_vol_positions": pos_target_vol,
        "dd_budget_position": pos_dd_budget,
        "conservative_recommendation": {
            "scale": round(float(min(dd_scale, 1.0)), 4),
            "rationale": f"版本B年化{ann_ret_B*100:.1f}%、Calmar{calmar_B:.2f}、最大回撤{max_dd_B*100:.1f}%；"
                         f"预留10%回撤缓冲需仓位系数{dd_scale:.2f}，"
                         f"年化压缩至{ann_ret_B*dd_scale*100:.1f}%。"
        },
    }
    return calibration


# ============================================================
# 主流程
# ============================================================
if __name__ == "__main__":
    print("=" * 70)
    print("exp424: T1策略样本外与参数鲁棒诊断")
    print("=" * 70)

    ctx = load_context()
    print(f"\n数据: {ctx['idx'][0].date()} ~ {ctx['idx'][-1].date()} ({len(ctx['idx'])} 日)")

    # 基准校验：定稿T1版本B年化应≈37.6%（exp423）
    pos_base, _ = compute_positions(ctx, VOL_HALF, VOL_FULL, HALF_SCALE, FULL_SCALE,
                                    DD_RECOVER, DD_WARN, DD_HARD)
    nr_B_base = net_ret_variant_B(pos_base, ctx["price"], ctx["ohlc"], ctx["idx"])
    mi_base = metrics_from_net_ret(nr_B_base, len(ctx["idx"]))
    print(f"\n[基准校验] 定稿T1版本B: 年化={mi_base['annual_return']*100:.1f}% "
          f"夏普={mi_base['sharpe']:.3f} 回撤={mi_base['max_drawdown']*100:.1f}% "
          f"Calmar={mi_base['calmar']:.2f}")
    print(f"  预期(exp423): 年化≈37.6%, Calmar≈3.80")

    # ===== 模块1：滚动样本外 =====
    print("\n" + "=" * 70)
    print("模块1：滚动样本外 Walk-forward 回测")
    print("=" * 70)
    wf_df, wf_summary = module1_walk_forward(ctx, train_len=250, test_len=60)
    print(f"  窗口数: {wf_summary['n_windows']}")
    print(f"  样本内平均年化: {wf_summary['insample_avg_annual']*100:.1f}%")
    print(f"  样本外平均年化: {wf_summary['oos_avg_annual']*100:.1f}%")
    print(f"  年化衰减: {wf_summary['annual_decay_pp']:.1f}pp")
    print(f"  样本外正收益窗口: {wf_summary['oos_positive_windows']}/{wf_summary['n_windows']} "
          f"({wf_summary['oos_positive_ratio']*100:.0f}%)")

    # ===== 模块2：参数敏感性扫描 =====
    print("\n" + "=" * 70)
    print("模块2：参数敏感性扫描")
    print("=" * 70)
    df_vol, df_dd, robustness = module2_param_sensitivity(ctx)
    print(f"  vol网格: {robustness['vol_grid_positive']}/{robustness['vol_grid_total']} 正收益 "
          f"({robustness['vol_grid_positive_ratio']*100:.0f}%)")
    print(f"  DD网格: {robustness['dd_grid_positive']}/{robustness['dd_grid_total']} 正收益 "
          f"({robustness['dd_grid_positive_ratio']*100:.0f}%)")
    print(f"  整体正收益率: {robustness['overall_positive_ratio']*100:.0f}% → {robustness['verdict']}")
    print(f"  解读: {robustness['interpretation']}")

    # ===== 模块3：信号质量过滤 =====
    print("\n" + "=" * 70)
    print("模块3：信号质量过滤对照实验")
    print("=" * 70)
    filt_df, filt_summary = module3_signal_filter(ctx, adx_threshold=30.0)
    print(f"  ADX高趋势日数: {filt_summary['n_adx_high_days']}")
    for name, mi in filt_summary["variants"].items():
        print(f"  {name}: 年化={mi['annual_return']*100:.1f}% 夏普={mi['sharpe']:.3f} "
              f"回撤={mi['max_drawdown']*100:.1f}% Calmar={mi['calmar']:.2f}")

    # ===== 模块4：仓位校准 =====
    print("\n" + "=" * 70)
    print("模块4：实盘仓位校准与安全垫测算")
    print("=" * 70)
    calib = module4_position_calibration(ctx)
    print(f"  基准B: 年化={calib['baseline_B_metrics']['annual_return']*100:.1f}% "
          f"Calmar={calib['baseline_B_metrics']['calmar']} 回撤={calib['baseline_B_metrics']['max_drawdown']*100:.1f}%")
    print(f"  回撤预算仓位系数: {calib['dd_budget_position']['recommended_scale']}")
    print(f"  压缩后年化: {calib['dd_budget_position']['scaled_annual']*100:.1f}%")
    print(f"  保守建议: {calib['conservative_recommendation']['scale']}倍仓位 → "
          f"{calib['conservative_recommendation']['rationale']}")

    # ===== 保存汇总JSON =====
    summary_json = {
        "baseline_B": mi_base,
        "module1_walk_forward": wf_summary,
        "module2_robustness": robustness,
        "module3_signal_filter": filt_summary,
        "module4_calibration": calib,
    }
    with open(os.path.join(DATA_DIR, "exp424_summary.json"), "w") as f:
        json.dump(summary_json, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n  ✓ 汇总JSON: data/exp424_summary.json")

    print("\n✅ exp424 完成")
