"""
exp416 模块4: 外样本跨品种测试

目标: 检验 HAM 因子是否属于碳酸锂专属行情拟合。
方法: 选用另一个有色大宗商品(沪锌主力合约), 完整部署 HAM + exp412 风控,
      使用与碳酸锂完全相同的一套参数, 不针对锌调参, 直接回测。

跨品种数据:
  - close: 沪锌主力合约(Sina ZN0)日频收盘价, akshare 获取, 2007-2026。
  - P_fund: HAM 基本面 Agent 的"供需公允价值"。碳酸锂用现货均价+供需分×15%调整;
    锌缺乏相同的5维基本面数据, 采用跨品种可移植的最小假设:
      P_fund = close 的 60 日滚动均值 (价格自身的长期均衡回归锚)。
    这保留了 D_f = alpha*(P_fund - close) 的核心语义(价格回归压力),
    且无需任何碳酸锂专属基本面数据, 保证"不针对新品种调参"。
  - 基本面动态策略(exp410)依赖碳酸锂专属因子, 无法跨品种, 故本模块仅测 HAM 子策略。

关键约束: HAM 信号/阈值/风控参数(alpha=0.3 beta=0.5 gamma=2 W=20, exp412 V4风控)
          与碳酸锂完全一致, 不做任何针对锌的调整。

输出: 锌 HAM 策略指标 vs 碳酸锂 HAM 策略指标(同区间)对照。
"""
import os
import sys
import json
import numpy as np
import pandas as pd

MODEL = "/home/ubuntu/lithium-engine/model_ham"
for d in ["exp409_ham_dynamic", "exp410_fund_dynamic", "exp411_combined",
          "exp413_robustness", "exp414_attribution"]:
    sys.path.insert(0, os.path.join(MODEL, d))

import exp409_ham_dynamic as E409
import exp413_engine as E413
import exp414_engine as E414
from exp411_combined import build_targets, price_series, extract_trades, TRADING_DAYS_PER_YEAR
from exp416_engine import COST, START, END, DATA_DIR

EXP_DIR = os.path.dirname(os.path.abspath(__file__))


def build_zinc_ham_factors():
    """
    构造锌 HAM 因子表 (与碳酸锂同结构: date, close, P_fund, Total_Demand)。
    P_fund = close 的 60 日滚动均值 (跨品种可移植的基本面回归锚)。
    Total_Demand 需要用于 compute_aux_signals 的 td_flip 信号 —— 这里用 HAM 原生
    Total_Demand 逻辑重算 (n_f*D_f + n_c*D_c), 保证信号链完整。
    """
    zin = pd.read_csv(os.path.join(DATA_DIR, "zinc_main_daily.csv"), parse_dates=["date"])
    zin = zin.sort_values("date").reset_index(drop=True)
    close = zin["close"].astype(float).values

    # --- 复刻 compute_system_deviation 的需求函数 ---
    alpha, beta = 0.3, 0.5
    n = len(close)
    P_t_minus_1 = np.zeros(n)
    P_t_minus_1[1:] = close[:-1]
    P_t_minus_1[0] = close[0]

    # P_fund = 60日滚动均值 (基本面回归锚, 跨品种可移植)
    P_fund = pd.Series(close).rolling(60, min_periods=20).mean().values
    P_fund = pd.Series(P_fund).ffill().bfill().values

    D_f = alpha * (P_fund - close)
    D_c = np.zeros(n)
    D_c[1:] = beta * np.diff(close)

    zin["close"] = close
    zin["P_fund"] = P_fund
    zin["D_f"] = D_f
    zin["D_c"] = D_c

    # --- 复刻 HAM 原生 logit 权重 (供 Total_Demand / td_flip 用) ---
    # 复用 exp409 的 profit / logit 逻辑, 参数与碳酸锂一致 (gamma=2, W=20)
    gamma, W = 2, 20
    price_change = np.zeros(n)
    price_change[1:] = np.diff(close)
    profit_f = np.zeros(n)
    profit_c = np.zeros(n)
    for t in range(W, n):
        window_D_f = D_f[t - W:t]
        window_D_c = D_c[t - W:t]
        window_pc = price_change[t - W:t]
        profit_f[t] = np.sum(window_D_f * window_pc)
        profit_c[t] = np.sum(window_D_c * window_pc)

    # logit 权重 (与 ham_model.py 一致)
    max_exp = 500
    sf = np.clip(gamma * profit_f, -max_exp, max_exp)
    sc = np.clip(gamma * profit_c, -max_exp, max_exp)
    mv = np.maximum(sf, sc)
    ef, ec = np.exp(sf - mv), np.exp(sc - mv)
    denom = ef + ec
    denom = np.where(denom == 0, 1e-10, denom)
    n_f, n_c = ef / denom, ec / denom

    total_demand = n_f * D_f + n_c * D_c
    zin["Total_Demand"] = total_demand

    return zin


def run_zinc_ham_strategy(zin_factors):
    """
    对锌 HAM 因子表跑完整信号链 + exp412 V4 风控 (参数与碳酸锂完全一致, 不调参)。
    返回 (日度目标仓位 Series, 指标, 交易列表)。
    """
    df = zin_factors.copy()
    # 复刻 exp409 信号链 (compute_system_deviation 已含 D_f/D_c/disagreement)
    close = df["close"].values.astype(float)
    dcn = pd.Series(df["D_c"]).rolling(E409.ROLLING_TRAIN, min_periods=60).rank(pct=True).values * 2 - 1
    dfn = pd.Series(df["D_f"]).rolling(E409.ROLLING_TRAIN, min_periods=60).rank(pct=True).values * 2 - 1
    df["disagreement"] = dcn - dfn
    df["deviation"] = np.abs(dcn - dfn)

    # 辅助信号
    df["ret5"] = np.log(df["close"]).diff(5)
    df["vol_abs5"] = df["ret5"].abs()
    df["td_sign"] = np.sign(df["Total_Demand"])
    df["td_flip"] = (df["td_sign"] != df["td_sign"].shift(1)) & df["td_sign"].notna()

    # GMM 状态代理
    df["gmm_state_proxy"] = 0
    q33 = df["deviation"].rolling(E409.ROLLING_TRAIN, min_periods=30).quantile(0.33)
    q66 = df["deviation"].rolling(E409.ROLLING_TRAIN, min_periods=30).quantile(0.66)
    df.loc[q66.notna(), "gmm_state_proxy"] = 1
    df.loc[q33.notna() & (df["deviation"] < q33), "gmm_state_proxy"] = 0
    df.loc[q66.notna() & (df["deviation"] > q66), "gmm_state_proxy"] = 2
    vol_mean = df["vol_abs5"].rolling(30, min_periods=10).mean()
    df.loc[q66.notna() & (df["deviation"] > q66) & (vol_mean > 0.03), "gmm_state_proxy"] = 3

    # HAM 动态信号
    df = E409.compute_ham_signals(df)

    # HAM 动态交易引擎
    df_dyn, trades = E409.run_ham_dynamic(df)
    pos = df_dyn.set_index("date")["position"].astype(float)
    price = df_dyn.set_index("date")["close"].astype(float)

    return pos, price, trades, df_dyn


def main():
    print("=" * 78)
    print("exp416 模块4: 外样本跨品种测试 (沪锌主力合约)")
    print("  HAM + exp412 V4 风控, 参数与碳酸锂完全一致, 不针对锌调参")
    print("=" * 78)

    # 构造锌 HAM 因子表
    zin_factors = build_zinc_ham_factors()
    print(f"\n[锌数据] {zin_factors.date.min().date()} ~ {zin_factors.date.max().date()}, "
          f"{len(zin_factors)} 个交易日")
    print(f"  close 量级: {zin_factors.close.min():.0f} ~ {zin_factors.close.max():.0f}")
    print(f"  P_fund = close 60日滚动均值 (跨品种可移植基本面回归锚)")

    # 跑锌 HAM 策略
    pos_zinc, price_zinc, trades_zinc, df_zinc = run_zinc_ham_strategy(zin_factors)

    # 限制到碳酸锂同区间 (2024-02 ~ 2026-09) 做公平对比
    mask = (pos_zinc.index >= START) & (pos_zinc.index <= END)
    idx_w = pos_zinc.index[mask]
    pos_w = pos_zinc.reindex(idx_w).fillna(0.0)
    price_w = price_zinc.reindex(idx_w).fillna(0.0)

    print(f"\n[对比区间] {START.date()} ~ {END.date()}, 锌交易日 n={len(idx_w)}")
    print(f"  锌 HAM 持仓日: {int((pos_w.abs() > 1e-9).sum())} | 交易次数: {len(trades_zinc)}")

    # 锌 HAM 指标 (纯 HAM 子策略, 无组合无 exp412风控, 与碳酸锂 HAM-only 对比)
    m_zinc_ham, _, _, _, _ = E414.unified_metrics(pos_w.values, price_w)

    # 叠加 exp412 V4 风控 (h=pos_w, f=0 仅HAM, 与碳酸锂 HAM-only+风控口径一致)
    f_zero = pd.Series(0.0, index=idx_w)
    pos_zinc_risk = E413.run_exp412_param(pos_w, f_zero, idx_w, price_w)
    m_zinc_risk, _, _, _, tr_risk = E414.unified_metrics(pos_zinc_risk, price_w)

    print("\n[锌 HAM 策略指标] (区间 %s ~ %s)" % (START.date(), END.date()))
    print(f"  纯HAM(无风控): 年化={m_zinc_ham['annual_return']:.4f} 夏普={m_zinc_ham['sharpe_full']:.3f} "
          f"回撤={m_zinc_ham['max_drawdown']:.4f} Calmar={m_zinc_ham['calmar']:.2f} "
          f"胜率={m_zinc_ham['win_rate']:.3f} 交易={m_zinc_ham['n_trades']}")
    print(f"  HAM+exp412风控: 年化={m_zinc_risk['annual_return']:.4f} 夏普={m_zinc_risk['sharpe_full']:.3f} "
          f"回撤={m_zinc_risk['max_drawdown']:.4f} Calmar={m_zinc_risk['calmar']:.2f} "
          f"胜率={m_zinc_risk['win_rate']:.3f} 交易={m_zinc_risk['n_trades']}")

    # 碳酸锂 HAM-only 基准 (同区间, 同口径, 来自 exp415 A组)
    df_lc = E414.build_ham_signal_frame()
    idx_lc_full = df_lc.set_index("date").index.sort_values()
    mask_lc = (idx_lc_full >= START) & (idx_lc_full <= END)
    idx_lc = idx_lc_full[mask_lc]
    price_lc = price_series(idx_lc)
    pos_lc, _ = E414.ham_group_positions(df_lc, mask_window=idx_lc)
    m_lc_ham, _, _, _, _ = E414.unified_metrics(pos_lc.values, price_lc)

    print("\n[碳酸锂 HAM-only 基准] (同区间, 对照)")
    print(f"  年化={m_lc_ham['annual_return']:.4f} 夏普={m_lc_ham['sharpe_full']:.3f} "
          f"回撤={m_lc_ham['max_drawdown']:.4f} Calmar={m_lc_ham['calmar']:.2f} "
          f"胜率={m_lc_ham['win_rate']:.3f} 交易={m_lc_ham['n_trades']}")

    # 对照表
    print("\n[跨品种对照表] (HAM-only, 同区间 2024-02~2026-09, 同参数)")
    print(f"{'品种':<10}{'年化':>10}{'夏普':>9}{'回撤':>10}{'Calmar':>9}{'胜率':>8}{'交易':>6}")
    print("-" * 62)
    print(f"{'碳酸锂':<10}{m_lc_ham['annual_return']:>10.4f}{m_lc_ham['sharpe_full']:>9.3f}"
          f"{m_lc_ham['max_drawdown']:>10.4f}{m_lc_ham['calmar']:>9.2f}"
          f"{m_lc_ham['win_rate']:>8.3f}{m_lc_ham['n_trades']:>6}")
    print(f"{'沪锌(纯HAM)':<10}{m_zinc_ham['annual_return']:>10.4f}{m_zinc_ham['sharpe_full']:>9.3f}"
          f"{m_zinc_ham['max_drawdown']:>10.4f}{m_zinc_ham['calmar']:>9.2f}"
          f"{m_zinc_ham['win_rate']:>8.3f}{m_zinc_ham['n_trades']:>6}")
    print(f"{'沪锌(+风控)':<10}{m_zinc_risk['annual_return']:>10.4f}{m_zinc_risk['sharpe_full']:>9.3f}"
          f"{m_zinc_risk['max_drawdown']:>10.4f}{m_zinc_risk['calmar']:>9.2f}"
          f"{m_zinc_risk['win_rate']:>8.3f}{m_zinc_risk['n_trades']:>6}")

    # 买入持有基准 (锌同期)
    bh_ret = price_w.values[-1] / price_w.values[0] - 1
    bh_ann = (1 + bh_ret) ** (250 / len(idx_w)) - 1
    print(f"\n[锌买入持有基准] 同期收益={bh_ret:.4f} 年化={bh_ann:.4f}")

    # 判定 HAM 跨品种泛化
    zinc_alpha = m_zinc_ham["annual_return"] - bh_ann
    print(f"\n[跨品种泛化判定] 锌HAM超额年化={zinc_alpha:.4f} "
          f"(年化{m_zinc_ham['annual_return']:.4f} vs 买入持有{bh_ann:.4f})")
    if m_zinc_ham["annual_return"] > bh_ann and m_zinc_ham["sharpe_full"] > 1:
        verdict = "HAM因子在锌上仍具备正超额, 具备跨品种泛化能力"
    elif m_zinc_ham["sharpe_full"] > 0:
        verdict = "HAM因子在锌上表现弱于碳酸锂, 存在品种依赖(部分泛化)"
    else:
        verdict = "HAM因子在锌上无正超额, 倾向碳酸锂专属拟合"
    print(f"  结论: {verdict}")

    # 保存产物
    cmp_df = pd.DataFrame([
        {"variety": "碳酸锂HAM-only", "annual_return": m_lc_ham["annual_return"],
         "sharpe": m_lc_ham["sharpe_full"], "max_drawdown": m_lc_ham["max_drawdown"],
         "calmar": m_lc_ham["calmar"], "win_rate": m_lc_ham["win_rate"],
         "n_trades": m_lc_ham["n_trades"]},
        {"variety": "沪锌HAM-only", "annual_return": m_zinc_ham["annual_return"],
         "sharpe": m_zinc_ham["sharpe_full"], "max_drawdown": m_zinc_ham["max_drawdown"],
         "calmar": m_zinc_ham["calmar"], "win_rate": m_zinc_ham["win_rate"],
         "n_trades": m_zinc_ham["n_trades"]},
        {"variety": "沪锌HAM+exp412风控", "annual_return": m_zinc_risk["annual_return"],
         "sharpe": m_zinc_risk["sharpe_full"], "max_drawdown": m_zinc_risk["max_drawdown"],
         "calmar": m_zinc_risk["calmar"], "win_rate": m_zinc_risk["win_rate"],
         "n_trades": m_zinc_risk["n_trades"]},
    ])
    cmp_df.to_csv(os.path.join(DATA_DIR, "exp416_M4_cross_variety.csv"), index=False)

    # 锌每日净值
    _, _, zinc_eq_ham, _nr, _tr = E414.unified_metrics(pos_w.values, price_w)
    _, _, zinc_eq_risk, _nr2, _tr2 = E414.unified_metrics(pos_zinc_risk, price_w)
    nav = pd.DataFrame({"zinc_close": price_w.values, "ham_pos": pos_w.values,
                        "ham_equity": zinc_eq_ham, "ham_risk_pos": pos_zinc_risk,
                        "ham_risk_equity": zinc_eq_risk})
    nav.index = idx_w
    nav.to_csv(os.path.join(DATA_DIR, "exp416_M4_zinc_daily_equity.csv"))

    # 锌交易日志
    if trades_zinc:
        pd.DataFrame(trades_zinc).to_csv(os.path.join(DATA_DIR, "exp416_M4_zinc_trades.csv"), index=False)

    summary = {
        "zinc_period": f"{START.date()} ~ {END.date()}",
        "zinc_data_range": f"{zin_factors.date.min().date()} ~ {zin_factors.date.max().date()}",
        "p_fund_method": "close 60日滚动均值 (跨品种可移植基本面回归锚)",
        "params": "与碳酸锂完全一致: alpha=0.3 beta=0.5 gamma=2 W=20, exp412 V4风控",
        "zinc_ham_only": m_zinc_ham,
        "zinc_ham_risk": m_zinc_risk,
        "lithium_ham_only": m_lc_ham,
        "zinc_buyhold_annual": bh_ann,
        "zinc_ham_excess_annual": zinc_alpha,
        "verdict": verdict,
    }
    with open(os.path.join(DATA_DIR, "exp416_M4_summary.json"), "w") as fp:
        json.dump(summary, fp, indent=2, default=str, ensure_ascii=False)

    print("\n✓ 模块4 完成, 产物已保存到 data/")


if __name__ == "__main__":
    main()
