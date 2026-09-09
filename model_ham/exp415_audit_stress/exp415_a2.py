"""
exp415 模块A2: 完整逐笔交易日志 + 每日净值CSV + 随机抽5笔交易推理链展示

为保证推理链可追溯，本模块对 HAM 动态引擎做一个"带诊断复刻"：
逻辑与 exp409.run_ham_dynamic 逐行一致，仅额外记录每笔交易在开仓日/平仓日
的原始行情(close)、信号中间量(disagreement/deviation/q_med/q_high/signal_dir/
aux_confirm)、平仓原因判定路径。这是 A2 "当日原始数据+信号计算过程+开平仓判定
逻辑" 展示要求的最小充分实现。同时输出每日净值CSV。
"""
import os, sys
import numpy as np, pandas as pd

MODEL = "/home/ubuntu/lithium-engine/model_ham"
sys.path.insert(0, os.path.join(MODEL, "exp409_ham_dynamic"))
sys.path.insert(0, os.path.join(MODEL, "exp410_fund_dynamic"))
sys.path.insert(0, os.path.join(MODEL, "exp411_combined"))
sys.path.insert(0, os.path.join(MODEL, "exp413_robustness"))
sys.path.insert(0, os.path.join(MODEL, "exp414_attribution"))

import exp409_ham_dynamic as E409
import exp414_engine as E414
from exp411_combined import build_targets, price_series

EXP_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(EXP_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)
START, END = pd.Timestamp("2024-02-01"), pd.Timestamp("2026-09-07")
COST = E409.COST_PER_SIDE
TDPY = E409.TRADING_DAYS_PER_YEAR


def run_ham_diag(df):
    """
    与 run_ham_dynamic 逻辑逐行一致的带诊断版本。
    返回 (trades_diag, positions)。每笔交易记录含开仓/平仓日的完整信号链。
    """
    df = df.reset_index(drop=True)
    n = len(df)
    close = df["close"].values
    positions = np.zeros(n)
    trades = []
    in_pos, entry_t = False, -1
    entry_dir, entry_price, entry_size = 0, 0.0, 0.0

    def snap(t, tag):
        r = df.iloc[t]
        return {
            f"{tag}_date": str(r["date"].date()),
            f"{tag}_close": float(close[t]),
            f"{tag}_disagreement": round(float(r["disagreement"]), 6),
            f"{tag}_deviation": round(float(r["deviation"]), 6),
            f"{tag}_q_med": round(float(r["q_med"]), 6) if pd.notna(r["q_med"]) else None,
            f"{tag}_q_high": round(float(r["q_high"]), 6) if pd.notna(r["q_high"]) else None,
            f"{tag}_q_neutral": round(float(r["q_neutral"]), 6) if pd.notna(r["q_neutral"]) else None,
            f"{tag}_signal_dir": int(r["signal_dir"]),
            f"{tag}_aux_confirm": bool(r["aux_confirm"]),
        }

    for t in range(1, n):
        if in_pos:
            should_close, reason = E409.ham_close_condition(df, t, entry_t, entry_dir)
            if should_close:
                pos_ret = entry_dir * (close[t] - entry_price) / entry_price
                net_ret = pos_ret - 2 * COST
                positions[t] = entry_dir * entry_size
                rec = snap(entry_t, "open")
                rec.update(snap(t, "close"))
                rec.update({
                    "direction": entry_dir, "size": entry_size,
                    "hold_days": t - entry_t,
                    "gross_ret": round(float(pos_ret), 6),
                    "net_ret": round(float(net_ret), 6),
                    "exit_reason": reason,
                    # 平仓判定逻辑说明
                    "close_logic": _close_logic_text(df, t, entry_t, entry_dir, reason),
                })
                trades.append(rec)
                in_pos, entry_t = False, -1
            else:
                positions[t] = entry_dir * entry_size
        else:
            if df.iloc[t]["open_signal"]:
                in_pos = True
                entry_t = t
                entry_dir = int(df.iloc[t]["signal_dir"])
                entry_price = close[t]
                entry_size = float(df.iloc[t]["position_size"])
                positions[t] = entry_dir * entry_size
    df["position"] = positions
    return trades, positions


def _close_logic_text(df, t, entry_t, entry_dir, reason):
    """用代码实际值复述平仓原因判定（可被人眼核对）"""
    r = df.iloc[t]
    if reason == "neutral_revert":
        return f"偏离度 dev={r['deviation']:.4f} < 中性分位 q_neutral={r['q_neutral']:.4f} → 博弈修复完成"
    if reason == "gmm_transition":
        return f"GMM状态代理 入场={int(df.iloc[entry_t]['gmm_state_proxy'])} → 当日={int(r['gmm_state_proxy'])} (∈{{0,1,3}}) → 环境变更"
    if reason == "reverse_signal":
        return f"方向翻转: 入场dir={entry_dir} → 当日signal_dir={int(r['signal_dir'])} ≠0 → 逻辑失效"
    if reason == "max_hold":
        return f"持仓 {t-entry_t} 日 ≥ 最大持仓 {E409.MAX_HOLD_HAM} 日 → 强制平仓"
    return reason


def daily_equity_csv(pos_arr, price, dates, label):
    """每日净值CSV: 日期/仓位/价格/策略收益/成本/净值"""
    close = price.reindex(dates).values.astype(float)
    n = len(close)
    r_price = np.zeros(n)
    r_price[1:] = (close[1:] - close[:-1]) / close[:-1]
    dpos = np.zeros(n)
    dpos[1:] = np.abs(pos_arr[1:] - pos_arr[:-1])
    dpos[0] = np.abs(pos_arr[0])
    strat_ret = pos_arr * r_price
    cost_ret = 2 * COST * dpos
    net_ret = strat_ret - cost_ret
    equity = np.cumprod(1 + net_ret)
    out = pd.DataFrame({
        "date": [d.date() for d in dates],
        "position": pos_arr,
        "close": close,
        "price_ret": np.round(r_price, 6),
        "strat_ret": np.round(strat_ret, 6),
        "cost_ret": np.round(cost_ret, 6),
        "net_ret": np.round(net_ret, 6),
        "equity": np.round(equity, 6),
    })
    path = os.path.join(DATA_DIR, f"exp415_A2_daily_equity_{label}.csv")
    out.to_csv(path, index=False)
    return out, equity


def main():
    print("=" * 72)
    print("exp415 模块A2: 逐笔交易日志 + 每日净值 + 随机抽5笔推理链")
    print("=" * 72)

    # --- HAM 动态（A组口径，收益主力，逻辑链最清晰）---
    df_orig = E414.build_ham_signal_frame()
    df_dyn, _ = E409.run_ham_dynamic(df_orig)  # 触发 __main__ 不跑，仅要 position 用于口径核对
    idx_full = df_orig.set_index("date").index.sort_values()
    mask = (idx_full >= START) & (idx_full <= END)
    idx_w = idx_full[mask]

    trades_diag, pos_full = run_ham_diag(df_orig)
    trades_full = pd.DataFrame(trades_diag)

    # 窗口内交易（与A组40笔一致）
    entry_dates = pd.to_datetime(trades_full["open_date"])
    trades_win = trades_full[(entry_dates >= START) & (entry_dates <= END)].reset_index(drop=True)

    # --- T1 完整组合 ---
    h_target, f_target = build_targets()
    idx = h_target.index
    price = price_series(idx)
    mask2 = (idx >= START) & (idx <= END)
    idx_w2 = idx[mask2]
    pos_T1 = E414.ham_group_positions.__wrapped__ if False else None
    # 用 exp413 引擎跑 T1
    import exp413_engine as E413
    pos_T1_arr = E413.run_exp412_param(h_target.reindex(idx_w2).fillna(0.0),
                                        f_target.reindex(idx_w2).fillna(0.0),
                                        idx_w2, price.reindex(idx_w2))

    # --- 每日净值CSV ---
    # A组(HAM动态)
    pos_A, _ = E414.ham_group_positions(df_orig, mask_window=idx_w)
    eqA_out, eqA = daily_equity_csv(pos_A.values, price_series(idx_w), idx_w, "A_ham")
    eqT1_out, eqT1 = daily_equity_csv(pos_T1_arr, price.reindex(idx_w2), idx_w2, "T1_combo")

    # --- 交易日志CSV ---
    trades_full.to_csv(os.path.join(DATA_DIR, "exp415_A2_ham_trades_full.csv"), index=False)
    trades_win.to_csv(os.path.join(DATA_DIR, "exp415_A2_ham_trades_window.csv"), index=False)
    # T1 交易（用 extract_trades 从仓位切）
    from exp411_combined import extract_trades
    t1_trades = extract_trades(pos_T1_arr, price.reindex(idx_w2).dropna(), idx_w2)
    pd.DataFrame(t1_trades).to_csv(os.path.join(DATA_DIR, "exp415_A2_T1_trades.csv"), index=False)

    print(f"\n窗口内 HAM 交易笔数: {len(trades_win)} (期望40)")
    print(f"T1 交易笔数: {len(t1_trades)} (期望40)")
    print(f"每日净值CSV: A组{len(eqA_out)}行 / T1组{len(eqT1_out)}行")

    # --- 随机抽5笔展示推理链 ---
    rng = np.random.default_rng(415)
    k = min(5, len(trades_win))
    picks = rng.choice(len(trades_win), size=k, replace=False)
    print(f"\n[随机抽样 {k} 笔交易 - 完整推理链]")
    print("-" * 72)
    pick_records = []
    for p in sorted(picks):
        tr = trades_win.iloc[p]
        print(f"\n交易#{p+1}: {tr['open_date']} → {tr['close_date']} | 方向{'多' if tr['direction']>0 else '空'} | "
              f"仓位{tr['size']:.0%} | 持仓{int(tr['hold_days'])}日 | 毛收益{tr['gross_ret']:+.2%} 净收益{tr['net_ret']:+.2%}")
        print(f"  开仓日原始数据: close={tr['open_close']:.0f} 分歧={tr['open_disagreement']:.4f} "
              f"偏离={tr['open_deviation']:.4f}")
        print(f"  开仓判定信号: q_med={tr['open_q_med']:.4f} q_high={tr['open_q_high']:.4f} "
              f"方向={tr['open_signal_dir']} 辅助确认={tr['open_aux_confirm']}")
        print(f"    → 主信号(偏离>q_med)={'T' if tr['open_deviation']>tr['open_q_med'] else 'F'} & "
              f"辅助(波动/仓单)={'T' if tr['open_aux_confirm'] else 'F'} = 开仓")
        print(f"    → 仓位档: 偏离{tr['open_deviation']:.4f} {'>' if tr['open_deviation']>tr['open_q_high'] else '<='} "
              f"q_high{tr['open_q_high']:.4f} → {tr['size']:.0%}")
        print(f"  平仓日原始数据: close={tr['close_close']:.0f}")
        print(f"  平仓判定: [{tr['exit_reason']}] {tr['close_logic']}")
        pick_records.append(tr.to_dict())
    pd.DataFrame(pick_records).to_csv(os.path.join(DATA_DIR, "exp415_A2_sampled5_trades.csv"), index=False)
    print("\n=== 模块A2 产物已保存 ===")


if __name__ == "__main__":
    main()
