"""
exp418 模块1: 趋势盲区预警指标开发

目标: 构建独立于 HAM 本身的行情识别指标, 监测市场进入持续单边趋势
(HAM 失效区间)。HAM 是"分歧修复"模型, 在强单边趋势段(动量持续主导、
不修复)会系统性跑输——这是 exp415/exp416 反复印证的结构性盲区。

设计原则 (严格独立于 HAM):
  预警指标只用价格序列构造, 不读 HAM 因子(disagreement/deviation),
  不复用 HAM 的 disagreement, 不依赖基本面数据。这样预警才能独立
  于 HAM 信号给出"市场已进入趋势盲区"的客观判断。

三个候选预警指标 (纯价格构造, 无前视):
  I1 ADX-趋势强度: 20日 ADX, 衡量趋势强度 vs 震荡。
  I2 动量持续度: 20日方向一致比 + 净动量幅度。
  I3 ATR-normalized 突破: 价格相对 N 日高低点的突破幅度 / ATR。
  综合预警 = 多指标共振 (取交集, 降低误报)。

回测验证:
  - 预警触发时段内 HAM(T0) 平均盈亏 (证明盲区判定有效)。
  - 预警准确率 (触发后 N 日 HAM 跑输买入持有)。
  - 误报率 (触发后 HAM 仍盈利)。
  - 阈值遍历 (按预警后 HAM 平均盈亏最负选阈值)。

人工干预建议: 预警触发后手动降低 HAM 仓位或暂停交易。
"""
import os
import sys
import numpy as np
import pandas as pd

MODEL = "/home/ubuntu/lithium-engine/model_ham"
for d in ["exp409_ham_dynamic", "exp411_combined"]:
    sys.path.insert(0, os.path.join(MODEL, d))

import exp409_ham_dynamic as E409
from exp411_combined import price_series, TRADING_DAYS_PER_YEAR

EXP_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(EXP_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)

START, END = pd.Timestamp("2024-02-01"), pd.Timestamp("2026-09-07")
TDPY = E409.TRADING_DAYS_PER_YEAR
COST = E409.COST_PER_SIDE


# ============================================================
# 数据加载: 价格序列 (独立于 HAM)
# ============================================================
def load_price():
    """直接复用 exp411 的 price_series 拿到与回测一致的日索引价格。"""
    from exp411_combined import build_targets as bt
    h_target, f_target = bt()
    idx = h_target.index
    price = price_series(idx)
    mask = (idx >= START) & (idx <= END)
    idx_w = idx[mask]
    price_w = price.reindex(idx_w)
    return idx_w, price_w


# ============================================================
# 预警指标 I1: ADX-趋势强度 (纯价格, 20日)
# ============================================================
def compute_adx(price, period=14):
    """Wilder ADX, 纯价格构造。返回 ADX 序列。"""
    high = price.copy()   # 单价格序列无 high/low, 用价格自身代理
    low = price.copy()
    close = price
    n = len(close)
    # 用 close 的相邻差代理 TR (无 high/low 数据, 简化)
    tr = close.diff().abs()
    up = close.diff()
    dn = -up
    gain = np.where(up > dn, np.where(up > 0, up, 0), 0)
    loss = np.where(up < dn, np.where(up < 0, -up, 0), 0)
    gain = pd.Series(gain, index=close.index)
    loss = pd.Series(loss, index=close.index)
    atr = tr.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    dgain = gain.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    dloss = loss.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    with np.errstate(divide="ignore", invalid="ignore"):
        pdi = 100 * dgain / atr
        mdi = 100 * dloss / atr
        dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    adx = dx.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    return adx, pdi, mdi


# ============================================================
# 预警指标 I2: 动量持续度 (方向一致比 + 净动量幅度)
# ============================================================
def compute_momentum_continuation(price, window=20):
    """
    方向一致比: window 日内价格上涨/下跌占比 (单边趋势 → 接近 1 或 0)。
    净动量幅度: window 日累计涨跌幅 / window (日均)。
    """
    ret = price.pct_change()
    up_ratio = (ret > 0).rolling(window, min_periods=window).mean()
    dn_ratio = (ret < 0).rolling(window, min_periods=window).mean()
    # 持续度 = max(up, dn), 越接近 1 越单边
    continuation = pd.concat([up_ratio, dn_ratio], axis=1).max(axis=1)
    # 净动量幅度 (累计涨跌绝对值 / window, 归一化)
    cum_mom = (price / price.shift(window) - 1).abs() / window
    # 方向: 主导方向
    direction = np.where(up_ratio > dn_ratio, 1, -1)
    direction = pd.Series(np.nan_to_num(direction, nan=0.0), index=price.index)
    return continuation, cum_mom, direction


# ============================================================
# 预警指标 I3: ATR-normalized 突破
# ============================================================
def compute_atr_breakout(price, window=20, atr_period=14):
    """
    价格相对 window 日高低点的突破幅度, 用 ATR 归一化。
    单边趋势: 价格持续创新高/新低, 突破幅度大。
    """
    hh = price.rolling(window, min_periods=window).max()
    ll = price.rolling(window, min_periods=window).min()
    tr = price.diff().abs().ewm(alpha=1.0 / atr_period, adjust=False,
                                min_periods=atr_period).mean()
    # 突破位置: (close - ll) / (hh - ll), 0=最低, 1=最高
    rng = (hh - ll).replace(0, np.nan)
    position = (price - ll) / rng
    # 距高低点的距离 / ATR (突破强度)
    dist_hi = (price - hh) / tr   # 负=在区间内, 大幅正=突破上沿
    dist_lo = (ll - price) / tr   # 正=距离下沿远, 大幅正=突破下沿
    return position, dist_hi, dist_lo, tr


# ============================================================
# 构建综合预警: 多指标共振
# ============================================================
def build_alert(price, adx_thr=22.0, cont_thr=0.65, breakout_thr=1.5):
    """
    综合趋势盲区预警 (纯价格, 独立于 HAM)。
    三指标各出一票, 满足条件即预警:
      I1: ADX > adx_thr (趋势强度)
      I2: continuation > cont_thr (方向持续度)
      I3: |突破幅度| > breakout_thr (ATR 归一化突破)
    预警 = I1 & I2 (共振, 降低误报)。I3 作为强度确认。
    """
    adx, pdi, mdi = compute_adx(price, period=14)
    cont, cum_mom, direction = compute_momentum_continuation(price, window=20)
    pos, dist_hi, dist_lo, atr = compute_atr_breakout(price, window=20)

    flag_adx = (adx > adx_thr)
    flag_cont = (cont > cont_thr)
    flag_bo = ((dist_hi.abs() > breakout_thr) | (dist_lo.abs() > breakout_thr))

    alert = flag_adx & flag_cont   # 双指标共振预警

    return pd.DataFrame({
        "date": price.index,
        "close": price.values,
        "adx": adx.values,
        "pdi": pdi.values,
        "mdi": mdi.values,
        "continuation": cont.values,
        "cum_mom": cum_mom.values,
        "mom_direction": direction.values,
        "position": pos.values,
        "dist_hi": dist_hi.values,
        "dist_lo": dist_lo.values,
        "atr": atr.values,
        "flag_adx": flag_adx.values,
        "flag_cont": flag_cont.values,
        "flag_bo": flag_bo.values,
        "alert": alert.values,
    }).set_index("date").sort_index()


# ============================================================
# 回测验证: 预警触发时段内 HAM(T0) 盈亏
# ============================================================
def load_ham_t0_daily():
    """加载 T0 每日净值与仓位, 用于验证预警时段 HAM 盈亏。"""
    eq = pd.read_csv(os.path.join(
        MODEL, "exp416_tail_risk_protect/data/exp416_daily_equity.csv"))
    eq["date"] = pd.to_datetime(eq["date"])
    eq = eq.set_index("date").sort_index()
    eq["T0_ret"] = eq["T0_equity"].pct_change()
    eq["market_ret"] = eq["close"].pct_change()
    return eq


def evaluate_alert(eq, alert_df, horizons=(3, 5, 10)):
    """
    评估预警准确率:
    - 触发日 → 未来 h 日 HAM 累计收益 vs 买入持有累计收益。
    - 准确率 = 触发后 HAM 跑输买入持有的比例。
    - 误报率 = 触发后 HAM 仍跑赢买入持有的比例。
    """
    idx = eq.index
    alert_idx = alert_df.index[alert_df["alert"] == True]
    results = []
    for t in alert_idx:
        if t not in idx:
            continue
        pos = idx.get_loc(t)
        for h in horizons:
            end = pos + h
            if end >= len(idx):
                continue
            ham_ret = eq["T0_equity"].iloc[end] / eq["T0_equity"].iloc[pos] - 1
            bh_ret = eq["close"].iloc[end] / eq["close"].iloc[pos] - 1
            results.append({
                "alert_date": t, "horizon": h,
                "ham_fwd_ret": ham_ret, "bh_fwd_ret": bh_ret,
                "ham_underperform": bool(ham_ret < bh_ret),
                "ham_abs_ret": ham_ret,
            })
    res = pd.DataFrame(results)
    return res


def compute_alert_stats(alert_df, eq, horizons=(3, 5, 10)):
    """汇总预警统计: 触发时段 HAM 平均盈亏 + 准确率 + 误报率。"""
    stats = {"n_alert_days": int(alert_df["alert"].sum()),
             "n_days": len(alert_df)}
    # 触发时段内 HAM 平均日盈亏 (按日期交集对齐)
    alert_dates = set(alert_df.index[alert_df["alert"] == True])
    common = eq.index.intersection(pd.DatetimeIndex(list(alert_dates)))
    if len(common) > 0:
        stats["ham_avg_ret_alert"] = float(eq.loc[common, "T0_ret"].mean())
    else:
        stats["ham_avg_ret_alert"] = 0.0
    non_alert = eq.index.difference(pd.DatetimeIndex(list(alert_dates)))
    if len(non_alert) > 0:
        stats["ham_avg_ret_normal"] = float(eq.loc[non_alert, "T0_ret"].mean())
    else:
        stats["ham_avg_ret_normal"] = 0.0
    # 各 horizon 准确率
    res = evaluate_alert(eq, alert_df, horizons)
    for h in horizons:
        if len(res) == 0:
            stats[f"acc_{h}d"] = np.nan
            stats[f"false_{h}d"] = np.nan
            stats[f"ham_avg_{h}d"] = np.nan
            stats[f"n_{h}d"] = 0
            continue
        sub = res[res["horizon"] == h]
        if len(sub) > 0:
            stats[f"acc_{h}d"] = float(sub["ham_underperform"].mean())
            stats[f"false_{h}d"] = float(1 - sub["ham_underperform"].mean())
            stats[f"ham_avg_{h}d"] = float(sub["ham_abs_ret"].mean())
            stats[f"n_{h}d"] = int(len(sub))
    return stats, res


# ============================================================
# 阈值遍历: 按预警后 HAM 平均盈亏最负选阈值
# ============================================================
def grid_search(alert_builder, eq, price):
    """遍历 adx_thr × cont_thr, 找预警后 HAM 平均盈亏最负的阈值。"""
    results = []
    for adx_thr in [18, 20, 22, 25, 28, 30]:
        for cont_thr in [0.55, 0.60, 0.65, 0.70, 0.75, 0.80]:
            a = alert_builder(price, adx_thr=adx_thr, cont_thr=cont_thr)
            stats, _ = compute_alert_stats(a, eq, horizons=(5,))
            results.append({
                "adx_thr": adx_thr, "cont_thr": cont_thr,
                "n_alert": stats["n_alert_days"],
                "alert_ratio": stats["n_alert_days"] / stats["n_days"],
                "acc_5d": stats.get("acc_5d", np.nan),
                "ham_avg_5d": stats.get("ham_avg_5d", np.nan),
                "ham_avg_alert": stats["ham_avg_ret_alert"],
                "ham_avg_normal": stats["ham_avg_ret_normal"],
            })
    grid = pd.DataFrame(results)
    # 最优 = 预警后 HAM 平均盈亏最负 (盲区判定最强), 同时触发率不过低
    grid_ok = grid[(grid["n_alert"] >= 20) & grid["ham_avg_5d"].notna()].copy()
    if len(grid_ok) > 0:
        best = grid_ok.loc[grid_ok["ham_avg_5d"].idxmin()]
    else:
        grid_ok = grid[grid["ham_avg_5d"].notna()]
        if len(grid_ok) > 0:
            best = grid_ok.loc[grid_ok["ham_avg_5d"].idxmin()]
        else:
            best = grid.iloc[0]
    return grid, best


if __name__ == "__main__":
    print("=" * 70)
    print("exp418 模块1: 趋势盲区预警指标开发")
    print("=" * 70)

    idx_w, price_w = load_price()
    print(f"价格区间: {idx_w[0].date()} ~ {idx_w[-1].date()} ({len(idx_w)} 交易日)")

    # 构建默认预警 (最优阈值: 盲区判定最强且触发率适中)
    alert_df = build_alert(price_w, adx_thr=28.0, cont_thr=0.70, breakout_thr=1.5)
    print(f"预警触发日: {int(alert_df['alert'].sum())} / {len(alert_df)} "
          f"({alert_df['alert'].mean()*100:.1f}%)")

    # 加载 HAM T0 日收益
    eq = load_ham_t0_daily()
    print(f"HAM T0 净值: {len(eq)} 日")

    # 评估预警
    stats, res = compute_alert_stats(alert_df, eq, horizons=(3, 5, 10))
    print("\n--- 预警统计 (最优阈值 ADX>28 & cont>0.70) ---")
    for k, v in stats.items():
        if isinstance(v, float):
            print(f"  {k}: {v:.4f}")
        else:
            print(f"  {k}: {v}")

    # 阈值遍历
    print("\n--- 阈值遍历 (找盲区判定最强阈值) ---")
    grid, best = grid_search(build_alert, eq, price_w)
    print(grid.sort_values("ham_avg_5d").head(10).to_string(index=False))
    print(f"\n最优阈值: ADX>{best['adx_thr']} & cont>{best['cont_thr']}")
    print(f"  触发日 {best['n_alert']:.0f}, 准确率(5日) {best['acc_5d']:.3f}, "
          f"HAM平均5日盈亏 {best['ham_avg_5d']:.4f}")

    # 保存
    alert_df.to_csv(os.path.join(DATA_DIR, "exp418_M1_alert_signals.csv"),
                    index=False)
    grid.to_csv(os.path.join(DATA_DIR, "exp418_M1_alert_grid.csv"), index=False)
    res.to_csv(os.path.join(DATA_DIR, "exp418_M1_alert_eval_detail.csv"),
               index=False)
    with open(os.path.join(DATA_DIR, "exp418_M1_summary.json"), "w") as f:
        import json
        json.dump({
            "default_thresholds": {"adx_thr": 28.0, "cont_thr": 0.70,
                                   "breakout_thr": 1.5},
            "default_stats": {k: (round(v, 6) if isinstance(v, float) else v)
                              for k, v in stats.items()},
            "best_thresholds": {"adx_thr": float(best["adx_thr"]),
                                "cont_thr": float(best["cont_thr"])},
            "best_stats": {k: (round(float(v), 6) if pd.notna(v) else None)
                           for k, v in best.items()},
        }, f, ensure_ascii=False, indent=2)
    print("\n模块1 数据已保存")
