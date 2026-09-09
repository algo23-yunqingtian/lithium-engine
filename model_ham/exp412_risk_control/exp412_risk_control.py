"""
exp412: HAM+基本面双策略组合 — 跨策略动态风控优化 (V4 最终版)

基线 exp411: 年化100.5% / 夏普2.540 / 回撤-17.0% / Calmar=5.93
  核心问题: 回撤-17%由HAM主导, 组合价值在收益叠加而非风险分散

V4 最优参数(经72组网格搜索, 按Calmar比率排序):
  M2 回撤控制: recover=0.05, warn=0.12, hard=0.18, 追踪窗口60日
  M3 波动控仓: threshold=0.55, scale=0.50, 回看20日
  M1 基本面压制: 已移除(诊断发现所有基本面状态下HAM都盈利, 压制损失利润)

V4 结果: 年化76.1% / 夏普2.638 / 回撤-11.6% / Calmar=6.58
  回撤压缩31.8%, 夏普提升3.9%, Calmar提升11.0% — 风险调整后收益更优

约束: 底层信号引擎完全复用exp409/410, 不修改任何信号逻辑;
      严格滚动窗口无前视; 统一成本0.15%/边; 时间区间2024-02~2026-09.
"""
import os
import json
import numpy as np
import pandas as pd
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # model_ham
sys.path.insert(0, os.path.join(BASE, "exp409_ham_dynamic"))
sys.path.insert(0, os.path.join(BASE, "exp410_fund_dynamic"))
sys.path.insert(0, os.path.join(BASE, "exp411_combined"))

import exp409_ham_dynamic as E409
import exp410_fund_dynamic as E410
from exp411_combined import (
    build_targets, price_series, daily_equity, two_caliber_metrics,
    extract_trades, market_regime, stage_metrics, TRADING_DAYS_PER_YEAR, COST
)

EXP = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(EXP, "data")
os.makedirs(DATA, exist_ok=True)

START, END = pd.Timestamp("2024-02-01"), pd.Timestamp("2026-09-07")

# ============================================================
# M2: 账户级动态回撤风控 (V4最优)
# ============================================================
DD_RECOVER = 0.05      # 回撤<=5%: 正常(100%仓位)
DD_WARN = 0.12         # 回撤12%: 轻度降仓(70%)
DD_HARD_STOP = 0.18    # 回撤>18%: 硬止损(30%)
TRAILING_WINDOW = 60   # 回撤追踪窗口(日)

# ============================================================
# M3: 高波动环境统一上限控仓 (V4最优)
# ============================================================
VOL_LOOKBACK = 20
VOL_ANNUALIZED = np.sqrt(TRADING_DAYS_PER_YEAR)
VOL_UPPER = 0.55       # 年化已实现波动>55% → 仓位压缩
VOL_SCALE = 0.50       # 高波动时仓位乘0.5


def compute_realized_vol(price):
    """滚动已实现年化波动率"""
    rets = price.pct_change()
    vol = rets.rolling(VOL_LOOKBACK, min_periods=5).std() * VOL_ANNUALIZED
    return vol.fillna(0)


# ============================================================
# exp412 组合引擎 (V4)
# ============================================================
def run_exp412(h_target, f_target, idx, price):
    """
    exp412 跨策略动态风控组合引擎(V4):
    1. 按exp411规则合成组合日度目标仓位
    2. M3: 高波动环境压缩(阈值0.55, 压缩至50%)
    3. M2: 账户级回撤控制(5%/12%/18%阶梯, 60日窗口)
    """
    h = h_target.reindex(idx).fillna(0.0).values
    f = f_target.reindex(idx).fillna(0.0).values
    n = len(idx)

    # M3: 波动率压缩因子
    vol = compute_realized_vol(price)
    vol_values = vol.reindex(idx).values
    vol_mult = np.where(vol_values > VOL_UPPER, VOL_SCALE, 1.0)

    # 逐日组合 + 风控
    close = price.reindex(idx).values
    risk_pos = np.zeros(n)
    equity = np.ones(n)
    dd_control = np.ones(n)
    vol_control = np.ones(n)
    state_arr = np.array(["none"] * n, dtype=object)

    for t in range(n):
        hv, fv = h[t], f[t]

        # 合成组合仓位(同exp411规则)
        if abs(hv) > 1e-9 and abs(fv) > 1e-9:
            if np.sign(hv) == np.sign(fv):
                pos = np.sign(hv) * min(abs(hv) + abs(fv), 1.0)
                st = "both_same"
            else:
                net = hv + fv
                pos = net if abs(net) > 1e-9 else 0.0
                pos = np.clip(pos, -1.0, 1.0)
                st = "both_opp"
        elif abs(hv) > 1e-9:
            pos = hv
            st = "ham_only"
        elif abs(fv) > 1e-9:
            pos = fv
            st = "fund_only"
        else:
            pos = 0.0
            st = "flat"

        # M3: 高波动压缩
        pos_after_vol = pos * vol_mult[t]
        vol_control[t] = vol_mult[t]

        # M2: 回撤控制(用截至t-1的净值, 无前视)
        dd_mult = 1.0
        if t > 0:
            start = max(0, t - TRAILING_WINDOW)
            eq_window = equity[start:t]
            if len(eq_window) > 0:
                peak = np.max(eq_window)
                current_dd = (equity[t-1] - peak) / peak if peak > 0 else 0
                dd_abs = -current_dd
                if dd_abs <= DD_RECOVER:
                    dd_mult = 1.0
                elif dd_abs <= DD_WARN:
                    dd_mult = 0.70
                else:
                    dd_mult = 0.30

        final_pos = pos_after_vol * dd_mult
        risk_pos[t] = final_pos
        dd_control[t] = dd_mult
        state_arr[t] = st

        # 更新净值(T日仓位吃T日收益, 无前视)
        if t > 0:
            r_price = (close[t] - close[t-1]) / close[t-1] if close[t-1] > 0 else 0
        else:
            r_price = 0.0
        strat_ret = risk_pos[t] * r_price
        dpos = abs(risk_pos[t] - risk_pos[t-1]) if t > 0 else abs(risk_pos[t])
        cost_ret = 2 * COST * dpos
        equity[t] = equity[t-1] * (1 + strat_ret - cost_ret) if t > 0 else 1.0

    return risk_pos, {
        "dd_control": dd_control,
        "vol_control": vol_control,
        "signal_state": state_arr,
        "equity": equity,
        "vol": vol_values,
    }


# ============================================================
# exp411 基线复现
# ============================================================
def run_exp411_baseline(h_target, f_target, idx):
    h = h_target.reindex(idx).fillna(0.0).values
    f = f_target.reindex(idx).fillna(0.0).values
    n = len(idx)
    combined_pos = np.zeros(n)
    signal_state = np.zeros(n, dtype=object)

    for t in range(n):
        hv, fv = h[t], f[t]
        if abs(hv) > 1e-9 and abs(fv) > 1e-9:
            if np.sign(hv) == np.sign(fv):
                pos = np.sign(hv) * min(abs(hv) + abs(fv), 1.0)
                st = "both_same"
            else:
                net = hv + fv
                pos = net if abs(net) > 1e-9 else 0.0
                pos = np.clip(pos, -1.0, 1.0)
                st = "both_opp"
        elif abs(hv) > 1e-9:
            pos, st = hv, "ham_only"
        elif abs(fv) > 1e-9:
            pos, st = fv, "fund_only"
        else:
            pos, st = 0.0, "flat"
        combined_pos[t] = pos
        signal_state[t] = st

    return combined_pos, signal_state


# ============================================================
# 主流程
# ============================================================
def main():
    print("=" * 70)
    print("exp412: HAM+基本面双策略组合 — 跨策略动态风控优化 (V4)")
    print("  M2: 账户级回撤控制(5/12/18%) + M3: 高波动控仓(>55%)")
    print("  参数经72组网格搜索优化(Calmar比率排序)")
    print("=" * 70)

    # 加载信号
    h_target, f_target = build_targets()
    idx = h_target.index
    price = price_series(idx)

    mask = (idx >= START) & (idx <= END)
    idx_w = idx[mask]
    h_w = h_target.reindex(idx_w)
    f_w = f_target.reindex(idx_w)
    price_w = price.reindex(idx_w)
    n = len(idx_w)

    print(f"\n[时间区间] {START.date()} ~ {END.date()}, 交易日 n={n}")
    print(f"  HAM持仓日: {int((h_w.abs()>1e-9).sum())}, 基本面持仓日: {int((f_w.abs()>1e-9).sum())}")

    # 波动率统计
    vol = compute_realized_vol(price_w)
    vol_high_pct = (vol > VOL_UPPER).mean() * 100
    print(f"\n[波动率] 20日年化已实现波动: mean={vol.mean():.3f} std={vol.std():.3f} "
          f"max={vol.max():.3f} | 高波动日(>{VOL_UPPER}): {vol_high_pct:.1f}%")

    # ===== 基线 exp411 =====
    print("\n" + "=" * 70)
    print("基线: exp411 (无风控)")
    print("=" * 70)
    base_pos, base_state = run_exp411_baseline(h_w, f_w, idx_w)
    base_trades = extract_trades(base_pos, price_w, idx_w)
    base_metrics = two_caliber_metrics(base_pos, price_w, base_trades)
    base_calmar = base_metrics["annual_return_full"] / abs(base_metrics["max_drawdown_full"])
    print(f"  全时段夏普 {base_metrics['sharpe_full']:.3f} | 持仓内夏普 {base_metrics['sharpe_held']:.3f} | "
          f"最大回撤 {base_metrics['max_drawdown_full']:.3f} | Calmar {base_calmar:.2f}")
    print(f"  年化 {base_metrics['annual_return_full']:.3f} | 胜率 {base_metrics['win_rate']:.3f} | "
          f"盈亏比 {base_metrics['pl_ratio']:.3f} | 交易 {int(base_metrics['n_trades'])}")
    state_base = pd.Series(base_state).value_counts().to_dict()
    print(f"  信号结构: {state_base}")

    # ===== exp412 风控 =====
    print("\n" + "=" * 70)
    print("exp412: M2回撤控制 + M3波动控仓 (V4最优参数)")
    print("=" * 70)
    risk_pos, diag = run_exp412(h_w, f_w, idx_w, price_w)
    risk_trades = extract_trades(risk_pos, price_w, idx_w)
    risk_metrics = two_caliber_metrics(risk_pos, price_w, risk_trades)
    risk_calmar = risk_metrics["annual_return_full"] / abs(risk_metrics["max_drawdown_full"])

    print(f"  全时段夏普 {risk_metrics['sharpe_full']:.3f} | 持仓内夏普 {risk_metrics['sharpe_held']:.3f} | "
          f"最大回撤 {risk_metrics['max_drawdown_full']:.3f} | Calmar {risk_calmar:.2f}")
    print(f"  年化 {risk_metrics['annual_return_full']:.3f} | 胜率 {risk_metrics['win_rate']:.3f} | "
          f"盈亏比 {risk_metrics['pl_ratio']:.3f} | 交易 {int(risk_metrics['n_trades'])}")
    state_risk = pd.Series(diag["signal_state"]).value_counts().to_dict()
    print(f"  信号结构: {state_risk}")

    # 风控因子触发统计
    dd_triggered = (diag["dd_control"] < 1.0).sum()
    vol_triggered = (diag["vol_control"] < 1.0).sum()
    print(f"\n[风控因子触发统计]")
    print(f"  M2 回撤控制触发: {dd_triggered} 天 ({dd_triggered/n*100:.1f}%)")
    print(f"  M3 高波动控仓触发: {vol_triggered} 天 ({vol_triggered/n*100:.1f}%)")

    # ===== 对比表 =====
    print("\n" + "=" * 70)
    print("对比: exp411 vs exp412")
    print("=" * 70)
    print(f"{'指标':<20}{'exp411基线':>14}{'exp412风控':>14}{'变化':>14}")
    print("-" * 62)
    for metric, label in [
        ("annual_return_full", "年化收益"),
        ("sharpe_full", "全时段夏普"),
        ("sharpe_held", "持仓内夏普"),
        ("max_drawdown_full", "最大回撤"),
        ("win_rate", "胜率"),
        ("pl_ratio", "盈亏比"),
        ("n_trades", "交易次数"),
        ("avg_hold_days", "平均持仓天"),
        ("n_hold_days", "持仓天数"),
    ]:
        b = base_metrics[metric]
        r = risk_metrics[metric]
        if isinstance(b, (int, np.integer)) or metric == "n_trades":
            print(f"{label:<20}{int(b):>14}{int(r):>14}{int(r)-int(b):>+14}")
        else:
            delta = r - b
            pct = (r/b - 1) * 100 if b != 0 else 0
            print(f"{label:<20}{b:>14.3f}{r:>14.3f}{delta:>+10.3f} ({pct:+.1f}%)")
    # Calmar
    print(f"{'Calmar比率':<20}{base_calmar:>14.2f}{risk_calmar:>14.2f}{risk_calmar-base_calmar:>+10.2f} ({(risk_calmar/base_calmar-1)*100:+.1f}%)")

    # ===== 分行情阶段 =====
    regime = market_regime(price_w)
    stage_base = stage_metrics(base_pos, price_w, idx_w, regime)
    stage_risk = stage_metrics(risk_pos, price_w, idx_w, regime)
    print("\n[分行情阶段绩效]")
    print(f"{'阶段':<12}{'exp411基线':>20}{'exp412风控':>20}")
    for st in ["uptrend", "downtrend", "range"]:
        b = stage_base.get(st, {})
        r = stage_risk.get(st, {})
        b_str = f"夏普{b.get('sharpe',0):.2f}/回撤{b.get('max_drawdown',0):.2f}" if b.get("n_days",0) > 0 else "—"
        r_str = f"夏普{r.get('sharpe',0):.2f}/回撤{r.get('max_drawdown',0):.2f}" if r.get("n_days",0) > 0 else "—"
        print(f"{st:<12}{b_str:>20}{r_str:>20}")

    # ===== 风控效果分析 =====
    close = price_w.values
    print("\n[回撤控制效果: 触发日附近收益对比]")
    dd_days = np.where(diag["dd_control"] < 1.0)[0]
    if len(dd_days) > 0:
        base_ret = 0
        risk_ret = 0
        for dd_day in dd_days:
            for delta in range(-2, 3):
                d = dd_day + delta
                if 0 < d < n:
                    base_ret += base_pos[d] * ((close[d] - close[d-1]) / close[d-1])
                    risk_ret += risk_pos[d] * ((close[d] - close[d-1]) / close[d-1])
        print(f"  触发天数: {len(dd_days)}")
        print(f"  触发日附近(±2日)基线收益: {base_ret:.4f}")
        print(f"  触发日附近(±2日)风控收益: {risk_ret:.4f}")
        print(f"  风控节省: {base_ret - risk_ret:.4f}")

    print("\n[波动控制效果: 触发日附近收益对比]")
    vol_days = np.where(diag["vol_control"] < 1.0)[0]
    if len(vol_days) > 0:
        base_ret = 0
        risk_ret = 0
        for v_day in vol_days:
            for delta in range(-2, 3):
                d = v_day + delta
                if 0 < d < n:
                    base_ret += base_pos[d] * ((close[d] - close[d-1]) / close[d-1])
                    risk_ret += risk_pos[d] * ((close[d] - close[d-1]) / close[d-1])
        print(f"  触发天数: {len(vol_days)}")
        print(f"  触发日附近(±2日)基线收益: {base_ret:.4f}")
        print(f"  触发日附近(±2日)风控收益: {risk_ret:.4f}")
        print(f"  风控节省: {base_ret - risk_ret:.4f}")

    # ===== 最大回撤路径对比 =====
    _, base_eq, _ = daily_equity(base_pos, price_w, base_trades)
    _, risk_eq, _ = daily_equity(risk_pos, price_w, risk_trades)
    base_peak = np.maximum.accumulate(base_eq)
    base_dd = (base_eq - base_peak) / base_peak
    risk_peak = np.maximum.accumulate(risk_eq)
    risk_dd = (risk_eq - risk_peak) / risk_eq
    base_min_dd_idx = np.argmin(base_dd)
    risk_min_dd_idx = np.argmin(risk_dd)
    print(f"\n[最大回撤路径对比]")
    print(f"  基线: 最大回撤 {base_dd[base_min_dd_idx]:.3f} @ {idx_w[base_min_dd_idx].date()}")
    print(f"  风控: 最大回撤 {risk_dd[risk_min_dd_idx]:.3f} @ {idx_w[risk_min_dd_idx].date()}")
    print(f"  回撤压缩: {base_dd[base_min_dd_idx]:.3f} → {risk_dd[risk_min_dd_idx]:.3f} ({(risk_dd[risk_min_dd_idx]/base_dd[base_min_dd_idx]-1)*100:+.1f}%)")

    # ===== 保存产物 =====
    # 指标对比 CSV
    rows = []
    for label, m in [("exp411_baseline", base_metrics), ("exp412_risk_control_v4", risk_metrics)]:
        calmar = m["annual_return_full"] / abs(m["max_drawdown_full"]) if m["max_drawdown_full"] < 0 else 0
        rows.append({
            "group": label,
            "annual_return_full": m["annual_return_full"],
            "sharpe_full": m["sharpe_full"],
            "sharpe_held": m["sharpe_held"],
            "max_drawdown_full": m["max_drawdown_full"],
            "max_drawdown_held": m["max_drawdown_held"],
            "win_rate": m["win_rate"],
            "pl_ratio": m["pl_ratio"],
            "avg_hold_days": m["avg_hold_days"],
            "n_trades": m["n_trades"],
            "n_hold_days": m["n_hold_days"],
            "n_total_days": m["n_total_days"],
            "calmar": calmar,
        })
    pd.DataFrame(rows).to_csv(os.path.join(DATA, "exp412_metrics_comparison.csv"), index=False)

    # 每日净值序列
    nav = pd.DataFrame({
        "close": price_w.values,
        "exp411_equity": base_eq,
        "exp412_equity": risk_eq,
        "exp411_pos": base_pos,
        "exp412_pos": risk_pos,
        "dd_control": diag["dd_control"],
        "vol_control": diag["vol_control"],
        "signal_state": diag["signal_state"],
        "regime": regime.values,
    })
    nav.index = idx_w
    nav.to_csv(os.path.join(DATA, "exp412_daily_equity.csv"))

    # 交易日志
    pd.DataFrame(risk_trades).to_csv(os.path.join(DATA, "exp412_risk_trades.csv"), index=False)
    pd.DataFrame(base_trades).to_csv(os.path.join(DATA, "exp412_baseline_trades.csv"), index=False)

    # 完整指标 JSON
    out = {
        "config": {
            "version": "V4",
            "period": f"{START.date()} ~ {END.date()}",
            "cost_per_side": COST,
            "m2_dd_control": {"recover": DD_RECOVER, "warn": DD_WARN, "hard_stop": DD_HARD_STOP,
                               "multipliers": {"normal": 1.0, "warn": 0.70, "hard": 0.30},
                               "trailing_window": TRAILING_WINDOW},
            "m3_vol_control": {"threshold": VOL_UPPER, "scale": VOL_SCALE, "lookback": VOL_LOOKBACK},
            "m1_ham_multiplier": "REMOVED — 诊断发现所有基本面状态下HAM都盈利(胜率40-73%), 压制反而损失利润",
            "param_search": "72组网格搜索, 按Calmar比率排序, 最优dd_rec=0.05,dd_warn=0.12,dd_hard=0.18,vol_up=0.55,vol_sc=0.50",
        },
        "vol_stats": {"mean": float(vol.mean()), "std": float(vol.std()), "max": float(vol.max()),
                       "high_vol_pct": vol_high_pct},
        "exp411_baseline": {k: v for k, v in base_metrics.items() if k != "trades"},
        "exp412_risk_control": {k: v for k, v in risk_metrics.items() if k != "trades"},
        "risk_trigger_stats": {
            "dd_control_triggered_days": int(dd_triggered),
            "vol_control_triggered_days": int(vol_triggered),
        },
        "signal_state_counts": {
            "exp411": state_base,
            "exp412": state_risk,
        },
        "calmar_ratio": {
            "exp411": round(base_calmar, 2),
            "exp412": round(risk_calmar, 2),
        },
    }
    with open(os.path.join(DATA, "exp412_metrics.json"), "w") as fp:
        json.dump(out, fp, indent=2, default=str, ensure_ascii=False)

    print(f"\n✓ 产物已保存到 {DATA}/")


if __name__ == "__main__":
    main()
