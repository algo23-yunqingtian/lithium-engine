"""
exp415 模块B: HAM 隐藏风险专项压力测试
  B1 资金容量/冲击成本测试
  B2 跳空流动性压力测试（事前止损失效 + 单笔亏损上限）
  B3 因子稳定性检验（滚动分段回测 → 超额收益稳定性 → 因子漂移）
  B4 极端拥挤场景测试（多信号同向同时开仓 → 集中持仓风险）

底层零改动：完全复用 exp409/411/413/414 引擎与口径。所有压力情景通过
"注入成本/注入价格冲击/分段切片"实现，不修改信号逻辑或风控参数。
"""
import os, sys, json
import numpy as np, pandas as pd

MODEL = "/home/ubuntu/lithium-engine/model_ham"
for d in ["exp409_ham_dynamic","exp410_fund_dynamic","exp411_combined","exp413_robustness","exp414_attribution"]:
    sys.path.insert(0, os.path.join(MODEL, d))
import exp409_ham_dynamic as E409
import exp413_engine as E413
import exp414_engine as E414
from exp411_combined import build_targets, price_series, extract_trades

EXP_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(EXP_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)
START, END = pd.Timestamp("2024-02-01"), pd.Timestamp("2026-09-07")
COST = E409.COST_PER_SIDE
TDPY = E409.TRADING_DAYS_PER_YEAR


def get_context():
    """统一的 T1 完整组合仓位 + 价格上下文"""
    h_target, f_target = build_targets()
    idx = h_target.index
    price = price_series(idx)
    mask = (idx >= START) & (idx <= END)
    idx_w = idx[mask]
    pos = E413.run_exp412_param(h_target.reindex(idx_w).fillna(0.0),
                                 f_target.reindex(idx_w).fillna(0.0),
                                 idx_w, price.reindex(idx_w))
    price_w = price.reindex(idx_w)
    m, _, _, _, _ = E414.unified_metrics(pos, price_w)
    return idx_w, pos, price_w, m


# ============================================================
# B1: 资金容量 / 冲击成本测试
# ============================================================
def b1_capacity(idx, pos, price, m_base):
    """
    流动性冲击成本模型：滑点随"参与率"（本策略日均成交/市场日均成交）非线性上升。
    采用行业常用的平方根冲击模型: impact(bps) = base_slip * sqrt(pct_of_adv)
    - base_slip = 0.15%/边（当前统一成本，视为极小资金下的纯手续费）
    - 随资金规模增加，pct_of_adv 上升 → 滑点按 sqrt 放大 → 收益非线性衰减。
    - 多档资金规模模拟，找年化收益衰减到基准80%时的容量上限。

    方法：对 T1 仓位，按不同 cost_per_side 重跑 unified_metrics（成本是唯一被
    资金规模影响的量，信号/仓位/风控不变），得到净收益随资金规模的衰减曲线。
    """
    close = price.values.astype(float)
    n = len(close)
    # 估算碳酸锂主力日均成交额（用于把资金规模映射到参与率）
    # 碳酸锂期货日均成交额约 200 亿元（合约价值×成交量，行业量级）
    DAILY_ADV = 2.0e10  # 元/日
    # 资金规模档位（元）：从 1000万 到 50亿
    capital_ladder = [1e7, 5e7, 1e8, 2e8, 5e8, 1e9, 2e9, 5e9, 1e10]
    # 当前持仓年化换手次数（用于估单策略日均成交额）
    n_trades = m_base["n_trades"]
    years = n / TDPY
    ann_turnover = n_trades / years  # 年换手次数
    base_slip = COST  # 0.0015

    rows = []
    base_ann = m_base["annual_return"]
    for cap in capital_ladder:
        # 单策略日均成交额 ≈ 资金 × 年换手 / 交易日（近似，按换手摊到每日）
        daily_notional = cap * ann_turnover / TDPY
        pct_of_adv = min(daily_notional / DAILY_ADV, 1.0)
        # sqrt 冲击：滑点 = base_slip * (1 + k*sqrt(pct_of_adv))
        # k=1.5 表示参与率每上升带来的非线性冲击放大系数
        k = 1.5
        eff_slip = base_slip * (1.0 + k * np.sqrt(pct_of_adv))
        cost_mult = eff_slip / base_slip
        m, _, _, _, _ = E414.unified_metrics(pos, price, cost=eff_slip)
        rows.append({
            "capital_yi": round(cap / 1e8, 2),  # 亿元
            "pct_of_adv": round(pct_of_adv * 100, 3),  # 参与率%
            "eff_slip_bps": round(eff_slip * 10000, 1),
            "cost_mult": round(cost_mult, 3),
            "annual_return": round(m["annual_return"], 4),
            "sharpe": round(m["sharpe_full"], 3),
            "max_dd": round(m["max_drawdown"], 4),
            "decay_pct": round((m["annual_return"] / base_ann - 1) * 100, 1) if base_ann > 0 else np.nan,
        })
    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(DATA_DIR, "exp415_B1_capacity_curve.csv"), index=False)

    # 容量上限：年化收益 ≥ 基准80% 的最大资金；以及夏普≥1.5 / 收益归零点
    def find_cap(idx_cond):
        ok = df[idx_cond]
        return round(ok["capital_yi"].max(), 2) if len(ok) else None
    cap_80 = find_cap(df["decay_pct"] >= -20)  # 衰减≤20%
    cap_zero = find_cap(df["annual_return"] > 0)
    cap_sharpe15 = find_cap(df["sharpe"] >= 1.5)
    print("\n[B1] 资金容量衰减曲线:")
    print(df.to_string(index=False))
    summary = {
        "base_annual_return": round(base_ann, 4),
        "n_trades": int(n_trades),
        "ann_turnover": round(ann_turnover, 2),
        "DAILY_ADV_yuan": DAILY_ADV,
        "capacity_80pct_yi": cap_80,
        "capacity_zero_return_yi": cap_zero,
        "capacity_sharpe15_yi": cap_sharpe15,
    }
    print(f"\n容量上限: 年化≥80%基准={cap_80}亿 | 夏普≥1.5={cap_sharpe15}亿 | 收益归零={cap_zero}亿")
    return df, summary


# ============================================================
# B2: 跳空流动性压力测试
# ============================================================
def b2_gap_stress(idx, pos, price, m_base):
    """
    事前止损在跳空下的失效分析：
    - HAM 无显式止损，靠 4 条平仓规则（中性回归/GMM跃迁/反向信号/12日强平）。
    - 隔夜跳空若超过止损阈值，止损单只能在跳空后开盘价成交（gap-through），
      实际亏损 = 跳空幅度（而非止损幅度）→ 止损失效。
    - 模拟多档隔夜跳空幅度（±3%/5%/8%/12%/15%），对全部持仓日施加反向跳空，
      算单笔最大亏损 & 对整体净值的影响。
    """
    close = price.values.astype(float)
    n = len(close)
    # 提取持仓段
    trades = extract_trades(pos, price.dropna(), idx)
    if not trades:
        return None, None
    gap_scenarios = [0.03, 0.05, 0.08, 0.12, 0.15]
    # 历史隔夜收益分布（用于把跳空幅度落到真实尾部）
    r = pd.Series(close).pct_change().values
    r_valid = r[~np.isnan(r)]
    p1 = float(np.percentile(r_valid, 1))
    p5 = float(np.percentile(r_valid, 5))

    # 情形A: 单笔最坏跳空（每笔持仓按"入场次日隔夜反向跳空 g%"结算）
    #   实际亏损 = size × (g + 2*COST)（若 g 超过假设止损-5%）
    stop_level = 0.05
    single_loss = []
    for g in gap_scenarios:
        worst = 0
        stop_broken = []
        for tr in trades:
            sz = tr["size"]
            # 反向跳空实际亏损（多单遇下跌/空单遇上涨，都按最不利方向）
            actual_loss = sz * g + 2 * COST * sz
            broke = actual_loss > stop_level
            stop_broken.append(broke)
            worst = max(worst, actual_loss)
        pct_broke = sum(stop_broken) / len(trades) * 100
        single_loss.append({
            "gap_pct": g * 100,
            "stop_level_pct": stop_level * 100,
            "worst_single_loss_pct": round(worst * 100, 2),
            "pct_stops_broken": round(pct_broke, 1),
            "stop_effective": "否(失效)" if worst > stop_level else "是",
        })
    df_single = pd.DataFrame(single_loss)
    df_single.to_csv(os.path.join(DATA_DIR, "exp415_B2_single_gap_loss.csv"), index=False)

    # 情形B: 整体净值冲击（所有持仓日同时承受 g% 反向跳空一次）
    portfolio_impact = []
    for g in gap_scenarios:
        pos_arr = pos.values if hasattr(pos, "values") else np.asarray(pos)
        # 找到平均持仓敞口（|pos|的均值，代表典型日敞口）
        avg_exposure = np.mean(np.abs(pos_arr[pos_arr != 0])) if (pos_arr != 0).any() else 0
        # 单次跳空对净值的冲击 ≈ 平均敞口 × g
        eq_shock = -avg_exposure * g
        portfolio_impact.append({
            "gap_pct": g * 100,
            "avg_position_exposure": round(avg_exposure, 3),
            "single_gap_equity_shock_pct": round(eq_shock * 100, 2),
        })
    df_pf = pd.DataFrame(portfolio_impact)
    df_pf.to_csv(os.path.join(DATA_DIR, "exp415_B2_portfolio_gap_impact.csv"), index=False)

    print("\n[B2] 跳空流动性压力测试 (事前止损-5%, 跳空时止损单按跳空价成交→失效):")
    print(f"  历史隔夜收益: 1%分位={p1*100:.2f}% 5%分位={p5*100:.2f}% (尾部参考)")
    print(df_single.to_string(index=False))
    print(df_pf.to_string(index=False))
    summary = {
        "stop_level_pct": stop_level * 100,
        "hist_p1_gap_pct": round(p1 * 100, 2),
        "hist_p5_gap_pct": round(p5 * 100, 2),
        "worst_single_loss_at_5pct_gap": df_single.loc[df_single["gap_pct"] == 5, "worst_single_loss_pct"].values[0],
        "worst_single_loss_at_15pct_gap": df_single.loc[df_single["gap_pct"] == 15, "worst_single_loss_pct"].values[0],
        "stops_broken_at_5pct_gap": df_single.loc[df_single["gap_pct"] == 5, "pct_stops_broken"].values[0],
        "stops_broken_at_15pct_gap": df_single.loc[df_single["gap_pct"] == 15, "pct_stops_broken"].values[0],
        "n_trades": len(trades),
    }
    return df_single, summary


# ============================================================
# B3: 因子稳定性检验（滚动分段）
# ============================================================
def b3_stability(idx, pos, price, m_base):
    """
    滚动分段回测：把样本拆成连续子窗口，观察 HAM 超额收益是否稳定，识别漂移。
    超额 = HAM组合净值 - 买入持有基准（同期价格累计收益）。
    """
    close = price.values.astype(float)
    pos_arr = pos.values if hasattr(pos, "values") else np.asarray(pos)
    n = len(close)
    dpos = np.zeros(n)
    dpos[1:] = np.abs(pos_arr[1:] - pos_arr[:-1])
    dpos[0] = np.abs(pos_arr[0])
    r_price = np.zeros(n)
    r_price[1:] = (close[1:] - close[:-1]) / close[:-1]
    net_ret = pos_arr * r_price - 2 * COST * dpos
    equity = np.cumprod(1 + net_ret)
    dates = [d.date() for d in idx]

    # 按自然季度分段（idx 为 DatetimeIndex）
    dates_ts = pd.Series(idx)
    qid = idx.to_period("Q")

    rows = []
    for q, grp_pos in dates_ts.groupby(qid):
        # 该季度的位置索引
        qi = np.array([dates_ts.get_loc(x) for x in grp_pos], dtype=int) \
            if hasattr(dates_ts, "get_loc") else None
        # 用 value_counts 取起止
        start_i = int(np.where(np.array(idx) == grp_pos.iloc[0])[0][0])
        end_i = int(np.where(np.array(idx) == grp_pos.iloc[-1])[0][0])
        seg_ret = equity[end_i] / equity[start_i] - 1
        bh_ret = close[end_i] / close[start_i] - 1  # 买入持有
        excess = seg_ret - bh_ret
        seg_sharpe = (np.mean(net_ret[start_i:end_i+1]) / np.std(net_ret[start_i:end_i+1]) * np.sqrt(TDPY)
                      if np.std(net_ret[start_i:end_i+1]) > 0 else 0.0)
        rows.append({
            "quarter": str(q),
            "start": dates[start_i], "end": dates[end_i],
            "ham_ret_pct": round(seg_ret * 100, 2),
            "buyhold_ret_pct": round(bh_ret * 100, 2),
            "excess_pct": round(excess * 100, 2),
            "seg_sharpe": round(seg_sharpe, 2),
            "excess_positive": bool(excess > 0),
        })
    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(DATA_DIR, "exp415_B3_quarterly_stability.csv"), index=False)

    n_quarters = len(df)
    n_pos = int(df["excess_positive"].sum())
    # 漂移检测：前半段 vs 后半段超额均值差
    half = n_quarters // 2
    front_excess = df["excess_pct"].iloc[:half].mean()
    back_excess = df["excess_pct"].iloc[half:].mean()
    drift = back_excess - front_excess
    # 分段夏普变异系数（波动越小越稳）
    sh_cv = df["seg_sharpe"].std() / abs(df["seg_sharpe"].mean()) if df["seg_sharpe"].mean() != 0 else np.nan
    print("\n[B3] 因子稳定性 - 按季度分段超额收益:")
    print(df.to_string(index=False))
    summary = {
        "n_quarters": n_quarters,
        "quarters_with_positive_excess": n_pos,
        "pct_positive_excess": round(n_pos / n_quarters * 100, 1),
        "front_half_excess_pct": round(front_excess, 2),
        "back_half_excess_pct": round(back_excess, 2),
        "drift_back_minus_front": round(drift, 2),
        "seg_sharpe_cv": round(sh_cv, 3) if not np.isnan(sh_cv) else None,
        "drift_flag": "明显漂移(后段恶化)" if drift < -10 else ("轻微漂移" if drift < -5 else "无显著漂移"),
    }
    print(f"\n稳定性判定: {n_pos}/{n_quarters}季度正超额 | 前半段{front_excess:.1f}% 后半段{back_excess:.1f}% "
          f"漂移={drift:+.1f}pp | 分段夏普变异系数={sh_cv:.3f} → {summary['drift_flag']}")
    return df, summary


# ============================================================
# B4: 极端拥挤场景测试
# ============================================================
def b4_crowding(idx, pos, price, m_base):
    """
    极端拥挤：多信号同向同时开仓 / 连续重仓 → 集中持仓的账户风险。
    - 统计最大连续同向持仓天数、最大累计同向敞口、最大回撤段是否由集中持仓引发。
    - 对连续重仓段做尾部分析（连亏序列、最大单段回撤）。
    """
    pos_arr = pos.values if hasattr(pos, "values") else np.asarray(pos)
    close = price.values.astype(float)
    n = len(close)
    dpos = np.zeros(n)
    dpos[1:] = np.abs(pos_arr[1:] - pos_arr[:-1])
    dpos[0] = np.abs(pos_arr[0])
    r_price = np.zeros(n)
    r_price[1:] = (close[1:] - close[:-1]) / close[:-1]
    net_ret = pos_arr * r_price - 2 * COST * dpos
    equity = np.cumprod(1 + net_ret)
    dates = [d.date() for d in idx]

    # 1) 最大连续同向持仓
    max_long_run = max_short_run = cur = 0
    cur_dir = 0
    runs = []
    for t in range(n):
        d = np.sign(pos_arr[t])
        if d == 0:
            if cur != 0 and cur_dir != 0:
                runs.append((cur_dir, cur))
            cur = 0; cur_dir = 0
        elif d == cur_dir:
            cur += 1
        else:
            if cur != 0 and cur_dir != 0:
                runs.append((cur_dir, cur))
            cur_dir = d; cur = 1
    if cur != 0 and cur_dir != 0:
        runs.append((cur_dir, cur))
    max_long_run = max([r[1] for r in runs if r[0] > 0], default=0)
    max_short_run = max([r[1] for r in runs if r[0] < 0], default=0)

    # 2) 重仓段(|pos|≥0.6) 的占比与累计敞口
    heavy = np.abs(pos_arr) >= 0.6
    heavy_pct = heavy.sum() / n * 100
    cum_heavy_exposure = np.sum(np.abs(pos_arr[heavy]))

    # 3) 连续亏损序列（拥挤下尾部）
    max_streak = cur_streak = 0
    streak_ret_min = 1.0
    run_eq = 1.0
    for r in net_ret:
        if r < 0:
            cur_streak += 1
            max_streak = max(max_streak, cur_streak)
            run_eq *= (1 + r)
            streak_ret_min = min(streak_ret_min, run_eq)
        else:
            cur_streak = 0
            run_eq = 1.0

    # 4) 最大回撤段定位
    peak = np.maximum.accumulate(equity)
    dd = (equity - peak) / peak
    trough_i = int(np.argmin(dd))
    peak_i = int(np.argmax(equity[:trough_i+1]))
    # 该回撤段内的平均敞口
    seg_exposure = np.mean(np.abs(pos_arr[peak_i:trough_i+1]))
    seg_avg_pos = np.mean(pos_arr[peak_i:trough_i+1])

    rows = [{
        "max_consecutive_long_days": max_long_run,
        "max_consecutive_short_days": max_short_run,
        "heavy_position_days_pct": round(heavy_pct, 2),
        "cumulative_heavy_exposure": round(cum_heavy_exposure, 2),
        "max_loss_streak_days": max_streak,
        "max_loss_streak_equity_drop_pct": round((streak_ret_min - 1) * 100, 2),
        "max_dd_trough_date": dates[trough_i],
        "max_dd_depth_pct": round(dd.min() * 100, 2),
        "dd_segment_avg_exposure": round(seg_exposure, 3),
        "dd_segment_avg_direction": round(seg_avg_pos, 3),
        "dd_cause_by_crowding": "是(回撤段重仓同向)" if seg_exposure >= 0.6 else "否(回撤段非集中持仓)",
    }]
    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(DATA_DIR, "exp415_B4_crowding.csv"), index=False)
    print("\n[B4] 极端拥挤场景:")
    print(df.T.to_string())
    summary = rows[0]
    return df, summary


def main():
    print("=" * 72)
    print("exp415 模块B: HAM 隐藏风险专项压力测试")
    print("=" * 72)
    idx, pos, price, m_base = get_context()
    print(f"\n[基准T1] 年化={m_base['annual_return']:.4f} 夏普={m_base['sharpe_full']:.3f} "
          f"回撤={m_base['max_drawdown']:.4f} 交易={m_base['n_trades']}")

    b1_df, b1_sum = b1_capacity(idx, pos, price, m_base)
    b2_df, b2_sum = b2_gap_stress(idx, pos, price, m_base)
    b3_df, b3_sum = b3_stability(idx, pos, price, m_base)
    b4_df, b4_sum = b4_crowding(idx, pos, price, m_base)

    summary = {"base_T1": {k: round(v, 4) for k, v in m_base.items()},
               "B1_capacity": b1_sum, "B2_gap": b2_sum,
               "B3_stability": b3_sum, "B4_crowding": b4_sum}
    with open(os.path.join(DATA_DIR, "exp415_B_summary.json"), "w") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2, default=str)
    print("\n=== 模块B 全部完成, 汇总已保存 ===")


import json
if __name__ == "__main__":
    main()
