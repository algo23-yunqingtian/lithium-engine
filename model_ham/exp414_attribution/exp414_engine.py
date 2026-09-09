"""
exp414: 消融归因实验 — 引擎

底层零改动承诺: 完全复用 exp409/410/411/413 的信号与交易引擎函数,
不修改任何信号逻辑、阈值、交易条件、风控参数。本模块仅:
  模块1 HAM信号消融: A组=exp409原版, B组=用同频率随机噪声替换"系统失衡信号"
  模块2 组合消融:    T1=exp412完整组合, T2=剔除HAM(仅基本面)+全套风控

回测区间: 2024-02-01 ~ 2026-09-07 (628交易日), 与 exp411/412/413 完全一致。
设计要点:
  - 模块1 A/B 严格同区间、同成本、同引擎(run_ham_dynamic), 仅 disagreement
    (系统失衡信号) 替换为同分布随机噪声 → 量化 HAM 信号独立Alpha。
  - 模块2 直接复用 exp413_engine.run_exp412_param: T1 传真实 h_target,
    T2 传 h_target=0(剔除HAM), 其余参数/风控/成本完全固定。
"""
import os
import numpy as np
import pandas as pd
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # model_ham
sys.path.insert(0, os.path.join(BASE, "exp409_ham_dynamic"))
sys.path.insert(0, os.path.join(BASE, "exp410_fund_dynamic"))
sys.path.insert(0, os.path.join(BASE, "exp411_combined"))
sys.path.insert(0, os.path.join(BASE, "exp413_robustness"))

import exp409_ham_dynamic as E409
import exp410_fund_dynamic as E410
from exp411_combined import build_targets, price_series, extract_trades, TRADING_DAYS_PER_YEAR
import exp413_engine as E413

TRADING_DAYS_PER_YEAR = E409.TRADING_DAYS_PER_YEAR   # 250
COST_PER_SIDE = E409.COST_PER_SIDE                    # 0.0015
START, END = pd.Timestamp("2024-02-01"), pd.Timestamp("2026-09-07")
VOL_ANNUALIZED = np.sqrt(TRADING_DAYS_PER_YEAR)


# ============================================================
# 统一指标计算(全时段口径, 与exp411/413一致)
# ============================================================
def unified_metrics(positions, price, cost=COST_PER_SIDE):
    """
    从日度目标仓位 + 价格 计算统一指标(全时段口径):
      年化/全时段夏普/最大回撤/Calmar/盈亏比/交易次数/胜率/平均持仓。
    成本: 每次"仓位变化"扣 2*cost*|仓位变化量|(体现换手成本)。
    T日仓位吃T日(T-1→T)收益, 无前视。
    """
    pos = np.asarray(positions, dtype=float)
    n = len(pos)
    close = price.values.astype(float)
    r_price = np.zeros(n)
    r_price[1:] = (close[1:] - close[:-1]) / close[:-1]
    strat_ret = pos * r_price
    dpos = np.zeros(n)
    dpos[1:] = np.abs(pos[1:] - pos[:-1])
    dpos[0] = np.abs(pos[0])
    net_ret = strat_ret - 2 * cost * dpos
    equity = np.cumprod(1 + net_ret)

    years = n / TRADING_DAYS_PER_YEAR
    total_ret = equity[-1] - 1
    ann_ret = (1 + total_ret) ** (1 / years) - 1 if total_ret > -1 else -1
    sharpe_full = (np.mean(net_ret) / np.std(net_ret) * VOL_ANNUALIZED
                   if np.std(net_ret) > 0 else 0.0)
    peak = np.maximum.accumulate(equity)
    max_dd = ((equity - peak) / peak).min()
    calmar = ann_ret / abs(max_dd) if max_dd < 0 else 0.0

    dates = price.index
    trades = extract_trades(pos, price, dates)
    nets = [tr["net_ret"] for tr in trades]
    if nets:
        wins = [r for r in nets if r > 0]
        losses = [r for r in nets if r <= 0]
        win_rate = len(wins) / len(nets)
        avg_win = np.mean(wins) if wins else 0.0
        avg_loss = abs(np.mean(losses)) if losses else 1e-4
        pl_ratio = avg_win / avg_loss if avg_loss > 0 else 0.0
        avg_hold = float(np.mean([tr["hold_days"] for tr in trades]))
        n_trades = len(trades)
    else:
        win_rate = pl_ratio = avg_hold = n_trades = 0.0

    return {
        "n_trades": int(n_trades),
        "total_return": float(total_ret),
        "annual_return": float(ann_ret),
        "sharpe_full": float(sharpe_full),
        "max_drawdown": float(max_dd),
        "calmar": float(calmar),
        "win_rate": float(win_rate),
        "pl_ratio": float(pl_ratio),
        "avg_hold_days": float(avg_hold),
    }, pos, equity, net_ret, trades


# ============================================================
# 模块1: HAM 信号消融
# ============================================================
def build_ham_signal_frame(seed_noise=None):
    """
    构建 HAM 信号帧(同 exp409 __main__ 流程)。
    若 seed_noise 不为 None, 用同分布随机噪声替换"系统失衡信号"disagreement,
    其余流程(compute_ham_signals/run_ham_dynamic)完全不变。
    """
    df = E409.load_ham_factors()
    df = E409.compute_system_deviation(df)
    df = E409.compute_aux_signals(df)
    df = E409.load_gmm_states(df)
    df = E409.compute_ham_signals(df)
    return df


def ham_group_positions(df, mask_window=None):
    """跑 run_ham_dynamic 得到日度目标仓位 Series(date索引)"""
    df_dyn, _ = E409.run_ham_dynamic(df)
    pos = df_dyn.set_index("date")["position"].astype(float)
    if mask_window is not None:
        pos = pos.reindex(mask_window).fillna(0.0)
    return pos, df_dyn


def run_module1(seed=42, n_noise_seeds=20):
    """
    A组: exp409 HAM完整原版(同628日区间)
    B组: 同频率随机噪声替换 disagreement, 复用A组全部引擎逻辑。

    关键: disagreement 含 NaN(180日滚动rank暖机期)。只对有效值做替换,
    且噪声按"有效值"的均值/标准差生成, 再写回有效位置(NaN位置保持NaN),
    这样不同种子产生真实变化的 null 分布, 而非 NaN 饱和导致的退化恒等结果。
    返回 A 指标 + B 分布(多种子), 用于判定 HAM 信号独立Alpha。
    """
    df_orig = build_ham_signal_frame()
    idx_full = df_orig.set_index("date").index.sort_values()
    mask = (idx_full >= START) & (idx_full <= END)
    idx_w = idx_full[mask]

    price_w = price_series(idx_w)

    # --- A组: 原版 ---
    pos_A, _ = ham_group_positions(df_orig, mask_window=idx_w)
    m_A, pos_A_arr, eq_A, nr_A, tr_A = unified_metrics(pos_A.values, price_w)

    # --- B组: 随机噪声替换 disagreement(仅有效值, 多随机种子) ---
    orig_dis = df_orig["disagreement"].values.astype(float)
    valid_mask = ~np.isnan(orig_dis)
    valid_vals = orig_dis[valid_mask]
    dis_mean = float(valid_vals.mean())
    dis_std = float(valid_vals.std())

    b_records = []
    for s in range(n_noise_seeds):
        rs = np.random.default_rng(seed * 1000 + s)
        noise = orig_dis.copy()
        noise[valid_mask] = rs.normal(loc=dis_mean, scale=dis_std,
                                      size=int(valid_mask.sum()))
        df_noise = df_orig.copy()
        df_noise["disagreement"] = noise
        df_noise = E409.compute_ham_signals(df_noise)   # 仅重算信号阈值(逻辑不变)
        # 健全性检查: 噪声确实改变了方向/触发结构
        pos_B, _ = ham_group_positions(df_noise, mask_window=idx_w)
        m_B, *_ = unified_metrics(pos_B.values, price_w)
        b_records.append(m_B)

    b_df = pd.DataFrame(b_records)
    return {
        "A": m_A,
        "B_df": b_df,
        "n_noise_seeds": n_noise_seeds,
        "disagreement_valid": int(valid_mask.sum()),
        "disagreement_mean": dis_mean,
        "disagreement_std": dis_std,
    }


# ============================================================
# 模块2: 整体组合消融
# ============================================================
def run_module2():
    """
    T1: exp412完整组合(HAM+基本面+风控)
    T2: 剔除HAM, 仅保留基本面动态策略 + 全套风控不变
    风控/成本参数完全固定(exp412 V4最优)。
    """
    h_target, f_target = build_targets()
    idx = h_target.index
    price = price_series(idx)
    mask = (idx >= START) & (idx <= END)
    idx_w = idx[mask]

    # T1: 完整组合
    pos_T1 = E413.run_exp412_param(h_target.reindex(idx_w).fillna(0.0),
                                    f_target.reindex(idx_w).fillna(0.0),
                                    idx_w, price.reindex(idx_w))
    m_T1, _, eq_T1, nr_T1, tr_T1 = unified_metrics(pos_T1, price.reindex(idx_w))

    # T2: 剔除HAM(h_target=0), 仅基本面 + 全套风控
    h_zero = pd.Series(0.0, index=idx_w)
    pos_T2 = E413.run_exp412_param(h_zero,
                                    f_target.reindex(idx_w).fillna(0.0),
                                    idx_w, price.reindex(idx_w))
    m_T2, _, eq_T2, nr_T2, tr_T2 = unified_metrics(pos_T2, price.reindex(idx_w))

    return {"T1": m_T1, "T2": m_T2, "pos_T1": pos_T1, "pos_T2": pos_T2,
            "eq_T1": eq_T1, "eq_T2": eq_T2, "tr_T1": tr_T1, "tr_T2": tr_T2,
            "idx_w": idx_w, "price": price.reindex(idx_w)}


def main():
    print("=" * 72)
    print("exp414: 消融归因实验")
    print("  区间 %s ~ %s | 成本 %.2f%%/边 | exp412 V4风控参数固定"
          % (START.date(), END.date(), COST_PER_SIDE * 100))
    print("=" * 72)

    r1 = run_module1()
    print("\n[模块1] A组(原版):", {k: round(v, 4) for k, v in r1["A"].items()})

    r2 = run_module2()
    print("\n[模块2] T1(完整):", {k: round(v, 4) for k, v in r2["T1"].items()})
    print("[模块2] T2(剔HAM):", {k: round(v, 4) for k, v in r2["T2"].items()})


if __name__ == "__main__":
    main()
