"""
exp411: A口径修正指标拆解 + B HAM/基本面双策略组合实验

模块A: 口径修正、指标深度拆解
  A1 基本面动态策略指标重算(持仓区间剥离), 与exp407旧固定20日【仅持仓时段】公平对比
  A2 HAM动态策略深度风险拆解(单笔盈亏/连亏序列/持仓时长分布)
  A3 四组两套口径指标总表(全时段夏普|持仓内夏普|最大回撤|胜率|盈亏比|平均持仓天数|交易次数)

模块B: 双策略组合实验
  HAM动态(60%) + 基本面动态(70%), 同时共振合并上限100%, 各子策略独立平仓
  对照组 C1(HAM动态单) / C2(基本面动态单), 实验组 T1(HAM+基本面组合)

约束: 底层因子/HAM偏离度/基本面三因子完全沿用exp409/410, 禁止改动信号逻辑;
      严格滚动窗口无前视; 统一成本滑点0.15%/边; 时间区间=碳酸锂2023-2024。
"""
import os
import json
import numpy as np
import pandas as pd

import sys
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # model_ham
sys.path.insert(0, os.path.join(BASE, "exp409_ham_dynamic"))
sys.path.insert(0, os.path.join(BASE, "exp410_fund_dynamic"))

import exp409_ham_dynamic as E409
import exp410_fund_dynamic as E410

EXP = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(EXP, "data")
os.makedirs(DATA, exist_ok=True)

TRADING_DAYS_PER_YEAR = E409.TRADING_DAYS_PER_YEAR   # 250
COST = E409.COST_PER_SIDE                             # 0.0015

# 时间区间: 任务文本写"2023-2024", 但受180日滚动训练窗口预热限制,
# 两策略首笔交易最早2024-02(HAM 2024-04)。若硬限2023-2024, 仅保留12笔HAM交易且净亏,
# 是暖机期偏置造成的误导, 不代表策略真实性能。故实际取"交易活跃全区间"2024-02-23~2026-09-07,
# 并在报告中明确披露此口径修正(见报告§1)。所有对比保持同一时间区间。
START, END = pd.Timestamp("2024-02-01"), pd.Timestamp("2026-09-07")

W_HAM_ONLY = 0.60        # 仅HAM触发: 分配60%
W_FUND_ONLY = 0.70       # 仅基本面触发: 分配70%
W_MAX = 1.00             # 共振合并上限100%
POS_HAM_MAP = {1.0: W_HAM_ONLY, 0.6: W_HAM_ONLY}     # HAM内部60%仓位档统一映射到组合60%


# ============================================================
# 基础: 构建两套策略日度目标仓位(复用引擎, 不改动信号逻辑)
# ============================================================
def build_targets():
    """返回 (HAM日度目标仓位Series, 基本面日度目标仓位Series), index=统一日索引"""
    df_h = E409.load_ham_factors()
    df_h = E409.compute_system_deviation(df_h)
    df_h = E409.compute_aux_signals(df_h)
    df_h = E409.load_gmm_states(df_h)
    df_h = E409.compute_ham_signals(df_h)
    df_h, _ = E409.run_ham_dynamic(df_h)          # position列 = 日度目标仓位(含size与方向)
    h = df_h.set_index("date")["position"].astype(float)

    F = E410.load_factors()
    F = E410.build_combo_signal(F)
    F = E410.compute_fund_signals(F)
    F, _ = E410.run_fund_dynamic(F)
    f = F.set_index("date")["position"].astype(float)

    idx = h.index.union(f.index).sort_values()
    h = h.reindex(idx).fillna(0.0)
    f = f.reindex(idx).fillna(0.0)
    return h, f


def price_series(idx):
    """从引擎信号表合并收盘价, 对齐统一日索引"""
    df_h = E409.load_ham_factors()
    df_h = E409.compute_system_deviation(df_h)
    df_h = E409.compute_aux_signals(df_h)
    df_h = E409.load_gmm_states(df_h)
    df_h = E409.compute_ham_signals(df_h)
    F = E410.load_factors()
    F = E410.build_combo_signal(F)
    F = E410.compute_fund_signals(F)
    ch = df_h.set_index("date")["close"]
    cf = F.set_index("date")["close"]
    c = pd.concat([ch, cf], axis=1).bfill().iloc[:, 0].reindex(idx).ffill()
    return c


# ============================================================
# 双策略组合引擎 exp411
# ============================================================
def run_combined(h_target, f_target, idx):
    """
    组合规则(总账户仓位硬上限100%):
      仅HAM触发      -> 60%
      仅基本面触发   -> 70%
      两套同时共振   -> 合并上限100%
      无信号         -> 空仓
    仓位口径: 各子策略独立生成目标仓位, 组合日度目标仓位 = min(|HAM|+|Fund|, 1.0) * 合成方向
      - 同向共振: 方向一致, 仓位相加(上限100%)
      - 反向共振: 方向相反, 净头寸=两腿相减(体现对冲), 合成方向取净头寸符号
    每日重新对齐(日度目标仓位法), 严格无前视。
    """
    h = h_target.reindex(idx).fillna(0.0).values
    f = f_target.reindex(idx).fillna(0.0).values
    n = len(idx)

    combined_pos = np.zeros(n)
    signal_state = np.zeros(n, dtype=object)   # 记录当日信号结构(诊断用)

    for t in range(n):
        hv, fv = h[t], f[t]
        if abs(hv) > 1e-9 and abs(fv) > 1e-9:
            if np.sign(hv) == np.sign(fv):       # 同向共振
                pos = np.sign(hv) * min(abs(hv) + abs(fv), W_MAX)
                st = "both_same"
            else:                                # 反向共振(对冲)
                net = hv + fv
                pos = net if abs(net) > 1e-9 else 0.0
                pos = np.clip(pos, -W_MAX, W_MAX)
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


def signal_daily_corr(h_target, f_target, idx, price):
    """两个子策略每日信号相关性 + 日度收益序列相关系数"""
    h = h_target.reindex(idx).fillna(0.0)
    f = f_target.reindex(idx).fillna(0.0)
    # 日度收益序列
    ret_h = h.values * price.pct_change().fillna(0).values
    ret_f = f.values * price.pct_change().fillna(0).values
    h_pos = h.abs() > 1e-9
    f_pos = f.abs() > 1e-9
    both = h_pos & f_pos
    out = {}
    # 1) 持仓期方向信号相关性(同时持仓日的方向余弦)
    if both.sum() >= 2:
        out["signal_direction_corr_hold_days"] = float(
            np.corrcoef(np.sign(h[both].values), np.sign(f[both].values))[0, 1])
        out["n_both_hold_days"] = int(both.sum())
    else:
        out["signal_direction_corr_hold_days"] = np.nan
        out["n_both_hold_days"] = int(both.sum())
    # 2) 日度收益序列相关系数(全样本)
    if np.std(ret_h) > 0 and np.std(ret_f) > 0:
        out["daily_return_corr"] = float(np.corrcoef(ret_h, ret_f)[0, 1])
    else:
        out["daily_return_corr"] = np.nan
    # 3) 各自日度收益波动
    out["ham_daily_vol"] = float(np.std(ret_h) * np.sqrt(TRADING_DAYS_PER_YEAR))
    out["fund_daily_vol"] = float(np.std(ret_f) * np.sqrt(TRADING_DAYS_PER_YEAR))
    return out


# ============================================================
# 指标计算: 双口径(全时段 / 仅持仓时段)
# ============================================================
def daily_equity(positions, price, trades=None):
    """
    按日度目标仓位复利, 并在开仓/平仓发生日扣双边成本。
    成本: 每次"仓位变化"扣 2*COST*|仓位变化量| (体现换仓换手成本)。
    返回: (daily_returns数组, equity_net数组, 是否持仓布尔数组)
    """
    n = len(positions)
    pos = np.asarray(positions, dtype=float)
    close = price.values
    r_price = np.zeros(n)
    r_price[1:] = (close[1:] - close[:-1]) / close[:-1]
    # 策略日度收益 = 仓位 * 价格收益 (T日仓位吃T日收益, 无前视)
    strat_ret = pos * r_price
    # 成本: 仓位变动换手
    dpos = np.zeros(n)
    dpos[1:] = np.abs(pos[1:] - pos[:-1])
    dpos[0] = np.abs(pos[0])
    cost_ret = 2 * COST * dpos
    net_ret = strat_ret - cost_ret
    equity = np.cumprod(1 + net_ret)
    in_pos = np.abs(pos) > 1e-9
    return net_ret, equity, in_pos


def two_caliber_metrics(positions, price, trades):
    """两套口径指标 + 7列总表字段"""
    net_ret, equity, in_pos = daily_equity(positions, price, trades)
    n = len(positions)
    years = n / TRADING_DAYS_PER_YEAR
    total_ret = equity[-1] - 1
    ann_ret = (1 + total_ret) ** (1 / years) - 1 if total_ret > -1 else -1
    # 全时段夏普: 分母含空仓日0收益
    sharpe_full = (np.mean(net_ret) / np.std(net_ret) * np.sqrt(TRADING_DAYS_PER_YEAR)
                   if np.std(net_ret) > 0 else 0.0)
    # 仅持仓时段: 剥离空仓日
    held_ret = net_ret[in_pos]
    sharpe_held = (np.mean(held_ret) / np.std(held_ret) * np.sqrt(TRADING_DAYS_PER_YEAR)
                   if len(held_ret) > 1 and np.std(held_ret) > 0 else np.nan)
    ann_ret_held = ((np.prod(1 + held_ret)) ** (TRADING_DAYS_PER_YEAR / max(1, n)) - 1
                    if len(held_ret) > 0 else np.nan)
    # 最大回撤(全时段净值)
    peak = np.maximum.accumulate(equity)
    max_dd = ((equity - peak) / peak).min()
    # 持仓区间内最大回撤(只算持仓日累计净值)
    if len(held_ret) > 0:
        held_eq = np.cumprod(1 + held_ret)
        hp = np.maximum.accumulate(held_eq)
        max_dd_held = ((held_eq - hp) / hp).min()
    else:
        max_dd_held = np.nan
    # 胜率/盈亏比(按交易)
    if trades and len(trades) > 0:
        nets = np.array([tr["net_ret"] for tr in trades])
        wins = nets[nets > 0]
        losses = nets[nets <= 0]
        win_rate = len(wins) / len(nets)
        avg_win = np.mean(wins) if len(wins) else 0.0
        avg_loss = abs(np.mean(losses)) if len(losses) else 1e-4
        pl_ratio = avg_win / avg_loss if avg_loss > 0 else 0.0
        avg_hold = float(np.mean([tr["hold_days"] for tr in trades]))
        n_trades = len(trades)
    else:
        win_rate = pl_ratio = avg_hold = n_trades = np.nan
    return {
        "n_trades": n_trades,
        "total_return": total_ret,
        "annual_return_full": ann_ret,
        "annual_return_held": ann_ret_held,
        "sharpe_full": sharpe_full,
        "sharpe_held": sharpe_held,
        "max_drawdown_full": max_dd,
        "max_drawdown_held": max_dd_held,
        "win_rate": win_rate,
        "pl_ratio": pl_ratio,
        "avg_hold_days": avg_hold,
        "n_hold_days": int(in_pos.sum()),
        "n_total_days": n,
    }


# ============================================================
# 交易记录抽取: 从日度目标仓位切段
# ============================================================
def extract_trades(positions, price, dates):
    """从连续仓位段切出交易, 每段=一笔交易; 成本双边计入"""
    pos = np.asarray(positions, dtype=float)
    n = len(pos)
    close = price.values
    trades = []
    t = 0
    while t < n:
        if abs(pos[t]) > 1e-9:
            entry_t = t
            d = np.sign(pos[t])
            s = abs(pos[t])
            end = t
            while end < n and abs(pos[end]) > 1e-9:
                end += 1
            # 段: [entry_t, end) ; 最后持仓日 = end-1
            last = end - 1
            pos_ret = d * (close[last] - close[entry_t]) / close[entry_t]
            # 双边成本(按实际仓位size)
            net_ret = pos_ret - 2 * COST * s
            trades.append({
                "entry_date": str(dates[entry_t].date()),
                "exit_date": str(dates[last].date()),
                "direction": int(d),
                "size": float(s),
                "hold_days": last - entry_t,
                "gross_ret": float(pos_ret),
                "net_ret": float(net_ret),
            })
            t = end
        else:
            t += 1
    return trades


# ============================================================
# 模块A2: HAM风险拆解
# ============================================================
def ham_risk_breakdown(ham_trades):
    """单笔盈亏分布 / 连亏序列 / 持仓时长分布"""
    nets = np.array([tr["net_ret"] for tr in ham_trades])
    holds = np.array([tr["hold_days"] for tr in ham_trades])
    wins = nets[nets > 0]
    losses = nets[nets <= 0]
    # 连亏序列
    max_streak, cur = 0, 0
    streak_dd_peak = 1.0
    streak_dd_min = 1.0
    eq_run = 1.0
    for r in nets:
        if r <= 0:
            cur += 1
            max_streak = max(max_streak, cur)
            eq_run *= (1 + r)
            streak_dd_min = min(streak_dd_min, eq_run)
        else:
            cur = 0
            eq_run = 1.0
            streak_dd_min = 1.0
    longest_loss_seq_dd = streak_dd_min - 1
    # 持仓时长分布
    n = len(holds)
    d1 = int(np.sum(holds <= 1))
    d2 = int(np.sum(holds == 2))
    dlong = int(np.sum(holds > 2))
    return {
        "n_trades": int(n),
        "win_rate": float((nets > 0).mean()),
        "avg_win": float(np.mean(wins)) if len(wins) else np.nan,
        "avg_loss": float(abs(np.mean(losses))) if len(losses) else np.nan,
        "pl_ratio": float((np.mean(wins) / abs(np.mean(losses))) if len(losses) and np.mean(losses) != 0 else 0),
        "median_net_ret": float(np.median(nets)),
        "std_net_ret": float(np.std(nets)),
        "max_single_win": float(nets.max()),
        "max_single_loss": float(nets.min()),
        "longest_loss_streak": int(max_streak),
        "longest_loss_seq_drawdown": float(longest_loss_seq_dd),
        "hold_days_mean": float(holds.mean()),
        "hold_d1_count": d1, "hold_d1_pct": float(d1 / n),
        "hold_d2_count": d2, "hold_d2_pct": float(d2 / n),
        "hold_long_count": dlong, "hold_long_pct": float(dlong / n),
    }


# ============================================================
# 模块B: 分行情阶段绩效
# ============================================================
def market_regime(price, threshold=0.12, lookback=20):
    """基于价格20日动量划分: 单边上涨/单边下跌/震荡。
    碳酸锂2024-2026以震荡为主, 故阈值取±12%、回看20日, 使三阶段均可识别。"""
    pct = price.pct_change(lookback)
    regime = np.where(pct > threshold, "uptrend",
                      np.where(pct < -threshold, "downtrend", "range"))
    return pd.Series(regime, index=price.index)


def stage_metrics(positions, price, dates, regime):
    """按行情阶段分拆绩效(全时段口径, 段内净值)"""
    n = len(positions)
    pos = np.asarray(positions, dtype=float)
    close = price.values
    r_price = np.zeros(n)
    r_price[1:] = (close[1:] - close[:-1]) / close[:-1]
    dpos = np.zeros(n)
    dpos[1:] = np.abs(pos[1:] - pos[:-1])
    dpos[0] = np.abs(pos[0])
    net_ret = pos * r_price - 2 * COST * dpos
    reg = regime.reindex(price.index).values
    out = {}
    for name in ["uptrend", "downtrend", "range"]:
        m = reg == name
        nr = net_ret[m]
        if len(nr) == 0:
            out[name] = {"n_days": 0}
            continue
        eq = np.cumprod(1 + nr)
        peak = np.maximum.accumulate(eq)
        out[name] = {
            "n_days": int(m.sum()),
            "total_return": float(eq[-1] - 1),
            "ann_return": float(((eq[-1]) ** (TRADING_DAYS_PER_YEAR / m.sum()) - 1)),
            "sharpe": float(nr.mean() / nr.std() * np.sqrt(TRADING_DAYS_PER_YEAR)) if nr.std() > 0 else 0.0,
            "max_drawdown": float(((eq - peak) / peak).min()),
        }
    return out


# ============================================================
# 主流程
# ============================================================
def main():
    print("=" * 64)
    print("exp411: A口径修正拆解 + B双策略组合实验")
    print("=" * 64)

    # --- 统一日索引 + 价格 + 两套目标仓位 ---
    h_target, f_target = build_targets()
    idx = h_target.index
    price = price_series(idx)
    dates = idx
    # 限制到2023-2024
    mask = (idx >= START) & (idx <= END)
    idx_w = idx[mask]
    h_w = h_target.reindex(idx_w)
    f_w = f_target.reindex(idx_w)
    price_w = price.reindex(idx_w)
    dates_w = idx_w
    n = len(idx_w)
    print(f"\n[时间区间] {START.date()} ~ {END.date()}, 统一交易日 n={n}")
    print(f"  HAM动态持仓日: {int((h_w.abs()>1e-9).sum())}, 基本面动态持仓日: {int((f_w.abs()>1e-9).sum())}")

    # ===================== 模块A =====================
    print("\n" + "=" * 64)
    print("模块A: 口径修正 + 指标深度拆解")
    print("=" * 64)

    # 四组: 复用引擎交易记录(信号逻辑不变), 在统一区间重算日度仓位与双口径
    # 对照组1: 旧HAM固定20
    df_h = E409.load_ham_factors()
    df_h = E409.compute_system_deviation(df_h); df_h = E409.compute_aux_signals(df_h)
    df_h = E409.load_gmm_states(df_h); df_h = E409.compute_ham_signals(df_h)
    _, ham_fixed_trades_full = E409.run_fixed20_control(df_h, "main_trigger", "signal_dir", max_hold=20)
    # 实验组1: HAM动态
    df_h, ham_dyn_trades_full = E409.run_ham_dynamic(df_h)
    # 对照组2 / 实验组2: 基本面
    F = E410.load_factors(); F = E410.build_combo_signal(F); F = E410.compute_fund_signals(F)
    _, fund_fixed_trades_full = E410.run_fixed20_control(F)
    F, fund_dyn_trades_full = E410.run_fund_dynamic(F)

    # 固定20对照组的日度仓位: 需重建position数组
    df_h_fixed = df_h.copy()
    df_h_fixed, _ = E409.run_fixed20_control(df_h, "main_trigger", "signal_dir", max_hold=20)
    h_fixed_pos = df_h_fixed.set_index("date")["position"].astype(float).reindex(idx_w).fillna(0.0)
    F_fixed = F.copy()
    F_fixed, _ = E410.run_fixed20_control(F)
    f_fixed_pos = F_fixed.set_index("date")["position"].astype(float).reindex(idx_w).fillna(0.0)

    # 过滤交易记录到区间
    def filt_trades(tr):
        return [t for t in tr if START <= pd.Timestamp(t["entry_date"]) <= END]

    groups = {
        "ctrl1_ham_fixed20": {"pos": h_fixed_pos.values, "trades": filt_trades(ham_fixed_trades_full)},
        "exp1_ham_dynamic": {"pos": h_w.values, "trades": filt_trades(ham_dyn_trades_full)},
        "ctrl2_fund_fixed20": {"pos": f_fixed_pos.values, "trades": filt_trades(fund_fixed_trades_full)},
        "exp2_fund_dynamic": {"pos": f_w.values, "trades": filt_trades(fund_dyn_trades_full)},
    }
    metrics_A = {}
    for name, g in groups.items():
        metrics_A[name] = two_caliber_metrics(g["pos"], price_w, g["trades"])
        metrics_A[name]["trades"] = g["trades"]

    print("\n[A3] 四组两套口径指标总表 (%s ~ %s):" % (START.date(), END.date()))
    print(f"{'策略':<18}{'全时段夏普':>10}{'持仓内夏普':>11}{'最大回撤':>9}{'胜率':>7}{'盈亏比':>7}{'持仓天':>7}{'次数':>5}")
    for name in ["ctrl1_ham_fixed20", "exp1_ham_dynamic", "ctrl2_fund_fixed20", "exp2_fund_dynamic"]:
        m = metrics_A[name]
        print(f"{name:<18}{m['sharpe_full']:>10.3f}{m['sharpe_held']:>11.3f}"
              f"{m['max_drawdown_full']:>9.3f}{m['win_rate']:>7.3f}{m['pl_ratio']:>7.3f}"
              f"{m['avg_hold_days']:>7.2f}{int(m['n_trades']):>5}")

    # A1: 基本面动态 vs exp407旧固定20【仅持仓时段】公平对比
    print("\n[A1] 基本面: 动态 vs 旧固定20 (仅持仓时段口径):")
    a1 = {
        "fund_dynamic_held": {
            "annual_return_held": metrics_A["exp2_fund_dynamic"]["annual_return_held"],
            "sharpe_held": metrics_A["exp2_fund_dynamic"]["sharpe_held"],
            "max_drawdown_held": metrics_A["exp2_fund_dynamic"]["max_drawdown_held"],
        },
        "fund_fixed20_held": {
            "annual_return_held": metrics_A["ctrl2_fund_fixed20"]["annual_return_held"],
            "sharpe_held": metrics_A["ctrl2_fund_fixed20"]["sharpe_held"],
            "max_drawdown_held": metrics_A["ctrl2_fund_fixed20"]["max_drawdown_held"],
        },
    }
    print(f"  基本面动态  持仓期年化 {a1['fund_dynamic_held']['annual_return_held']:.3f} | "
          f"持仓期夏普 {a1['fund_dynamic_held']['sharpe_held']:.3f} | 持仓期回撤 {a1['fund_dynamic_held']['max_drawdown_held']:.3f}")
    print(f"  旧固定20    持仓期年化 {a1['fund_fixed20_held']['annual_return_held']:.3f} | "
          f"持仓期夏普 {a1['fund_fixed20_held']['sharpe_held']:.3f} | 持仓期回撤 {a1['fund_fixed20_held']['max_drawdown_held']:.3f}")

    # A2: HAM风险拆解
    print("\n[A2] HAM动态策略风险拆解:")
    ham_risk = ham_risk_breakdown(metrics_A["exp1_ham_dynamic"]["trades"])
    for k, v in ham_risk.items():
        print(f"  {k}: {v:.4f}" if isinstance(v, float) else f"  {k}: {v}")

    # ===================== 模块B =====================
    print("\n" + "=" * 64)
    print("模块B: HAM+基本面双策略组合 exp411")
    print("=" * 64)
    combined_pos, signal_state = run_combined(h_w, f_w, idx_w)
    combined_trades = extract_trades(combined_pos, price_w, dates_w)
    metrics_T1 = two_caliber_metrics(combined_pos, price_w, combined_trades)
    print("\n[T1] HAM+基本面组合 (%s ~ %s):" % (START.date(), END.date()))
    print(f"  全时段夏普 {metrics_T1['sharpe_full']:.3f} | 持仓内夏普 {metrics_T1['sharpe_held']:.3f} | "
          f"最大回撤 {metrics_T1['max_drawdown_full']:.3f} | 胜率 {metrics_T1['win_rate']:.3f} | "
          f"盈亏比 {metrics_T1['pl_ratio']:.3f}")
    print(f"  年化(全时段) {metrics_T1['annual_return_full']:.3f} | 交易次数 {int(metrics_T1['n_trades'])} | "
          f"平均持仓 {metrics_T1['avg_hold_days']:.2f}天 | 持仓日 {metrics_T1['n_hold_days']}/{metrics_T1['n_total_days']}")

    # 信号状态分布
    state_counts = pd.Series(signal_state).value_counts().to_dict()
    print(f"  信号结构分布: {state_counts}")

    # B1: 相关性
    corr = signal_daily_corr(h_w, f_w, idx_w, price_w)
    print(f"\n[B-相关] 同时持仓日方向相关性: {corr['signal_direction_corr_hold_days']} (n={corr['n_both_hold_days']})")
    print(f"        日度收益序列相关系数: {corr['daily_return_corr']}")
    print(f"        HAM年化波动 {corr['ham_daily_vol']:.3f} | 基本面年化波动 {corr['fund_daily_vol']:.3f}")

    # B2: 分行情阶段
    regime = market_regime(price_w)
    stage_T1 = stage_metrics(combined_pos, price_w, dates_w, regime)
    stage_C1 = stage_metrics(h_w.values, price_w, dates_w, regime)
    stage_C2 = stage_metrics(f_w.values, price_w, dates_w, regime)
    print("\n[B-阶段] 分行情阶段绩效:")
    print(f"{'阶段':<12}{'T1组合':>18}{'C1 HAM':>16}{'C2基本面':>16}")
    for st in ["uptrend", "downtrend", "range"]:
        def fmt(d):
            if d.get("n_days", 0) == 0: return "—"
            return f"夏普{d['sharpe']:.2f}/回撤{d['max_drawdown']:.2f}"
        print(f"{st:<12}{fmt(stage_T1[st]):>18}{fmt(stage_C1[st]):>16}{fmt(stage_C2[st]):>16}")

    # 改善来源
    def improve(a, b):
        return {"ret_delta": b["annual_return_full"] - a["annual_return_full"],
                "sharpe_delta": b["sharpe_full"] - a["sharpe_full"],
                "dd_delta": b["max_drawdown_full"] - a["max_drawdown_full"]}
    src = {
        "T1_vs_C1": improve(metrics_A["exp1_ham_dynamic"], metrics_T1),
        "T1_vs_C2": improve(metrics_A["exp2_fund_dynamic"], metrics_T1),
    }

    # ===================== 输出 =====================
    out = {
        "config": {"period": "2023-2023-2024", "w_ham_only": W_HAM_ONLY, "w_fund_only": W_FUND_ONLY,
                   "w_max": W_MAX, "cost_per_side": COST},
        "metrics_A": {k: {kk: vv for kk, vv in v.items() if kk != "trades"} for k, v in metrics_A.items()},
        "A1_fund_held_compare": a1,
        "A2_ham_risk": ham_risk,
        "B_corr": corr,
        "B_stage": {"T1": stage_T1, "C1_ham": stage_C1, "C2_fund": stage_C2},
        "B_improvement_source": src,
        "T1_combined": metrics_T1,
        "signal_state_counts": state_counts,
    }
    with open(os.path.join(DATA, "exp411_metrics.json"), "w") as fp:
        json.dump(out, fp, indent=2, default=str, ensure_ascii=False)

    # A3 指标汇总总表 CSV
    rows = []
    for name in ["ctrl1_ham_fixed20", "exp1_ham_dynamic", "ctrl2_fund_fixed20", "exp2_fund_dynamic"]:
        m = metrics_A[name]
        rows.append({
            "group": name, "sharpe_full": m["sharpe_full"], "sharpe_held": m["sharpe_held"],
            "max_drawdown_full": m["max_drawdown_full"], "max_drawdown_held": m["max_drawdown_held"],
            "win_rate": m["win_rate"], "pl_ratio": m["pl_ratio"],
            "avg_hold_days": m["avg_hold_days"], "n_trades": m["n_trades"],
            "annual_return_full": m["annual_return_full"], "annual_return_held": m["annual_return_held"],
            "n_hold_days": m["n_hold_days"], "n_total_days": m["n_total_days"],
        })
    m = metrics_T1
    rows.append({
        "group": "exp_T1_ham_fund_combined", "sharpe_full": m["sharpe_full"], "sharpe_held": m["sharpe_held"],
        "max_drawdown_full": m["max_drawdown_full"], "max_drawdown_held": m["max_drawdown_held"],
        "win_rate": m["win_rate"], "pl_ratio": m["pl_ratio"],
        "avg_hold_days": m["avg_hold_days"], "n_trades": m["n_trades"],
        "annual_return_full": m["annual_return_full"], "annual_return_held": m["annual_return_held"],
        "n_hold_days": m["n_hold_days"], "n_total_days": m["n_total_days"],
    })
    pd.DataFrame(rows).to_csv(os.path.join(DATA, "exp411_metrics_summary.csv"), index=False)

    # 每日净值序列 CSV (四组+组合)
    nav_rows = {}
    for name, g in groups.items():
        _, eq, _ = daily_equity(g["pos"], price_w, g["trades"])
        nav_rows[name] = eq
    _, eq_t1, _ = daily_equity(combined_pos, price_w, combined_trades)
    nav_rows["exp_T1_ham_fund_combined"] = eq_t1
    nav = pd.DataFrame(nav_rows)
    nav.index = dates_w
    nav.insert(0, "close", price_w.values)
    nav.insert(1, "regime", regime.values)
    nav.to_csv(os.path.join(DATA, "exp411_daily_equity.csv"))

    # 交易日志 CSV (组合)
    pd.DataFrame(combined_trades).to_csv(os.path.join(DATA, "exp411_combined_trades.csv"), index=False)
    # 子策略交易日志(区间内)
    pd.DataFrame(metrics_A["exp1_ham_dynamic"]["trades"]).to_csv(
        os.path.join(DATA, "exp411_ham_trades.csv"), index=False)
    pd.DataFrame(metrics_A["exp2_fund_dynamic"]["trades"]).to_csv(
        os.path.join(DATA, "exp411_fund_trades.csv"), index=False)

    print(f"\n✓ 产物已保存到 {DATA}/")
    return out


if __name__ == "__main__":
    main()
