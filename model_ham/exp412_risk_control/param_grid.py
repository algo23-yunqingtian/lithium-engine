"""exp412 V4: 参数网格搜索, 找收益-回撤最佳平衡点"""
import os, json, sys
import numpy as np
import pandas as pd

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "exp409_ham_dynamic"))
sys.path.insert(0, os.path.join(BASE, "exp410_fund_dynamic"))
sys.path.insert(0, os.path.join(BASE, "exp411_combined"))
import exp409_ham_dynamic as E409
import exp410_fund_dynamic as E410
from exp411_combined import (
    build_targets, price_series, daily_equity, two_caliber_metrics,
    extract_trades, market_regime, TRADING_DAYS_PER_YEAR, COST
)
EXP = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(EXP, "data")
os.makedirs(DATA, exist_ok=True)
START, END = pd.Timestamp("2024-02-01"), pd.Timestamp("2026-09-07")
VOL_LOOKBACK = 20
VOL_ANNUALIZED = np.sqrt(TRADING_DAYS_PER_YEAR)

def compute_realized_vol(price):
    rets = price.pct_change()
    vol = rets.rolling(VOL_LOOKBACK, min_periods=5).std() * VOL_ANNUALIZED
    return vol.fillna(0)

def run_exp412(h_target, f_target, idx, price, dd_recover=0.06, dd_warn=0.10,
               dd_hard=0.14, vol_upper=0.50, vol_scale=0.40, trailing_window=60):
    h = h_target.reindex(idx).fillna(0.0).values
    f = f_target.reindex(idx).fillna(0.0).values
    n = len(idx)
    vol = compute_realized_vol(price)
    vol_values = vol.reindex(idx).values
    vol_mult = np.where(vol_values > vol_upper, vol_scale, 1.0)
    close = price.reindex(idx).values
    risk_pos = np.zeros(n)
    equity = np.ones(n)
    for t in range(n):
        hv, fv = h[t], f[t]
        if abs(hv) > 1e-9 and abs(fv) > 1e-9:
            if np.sign(hv) == np.sign(fv):
                pos = np.sign(hv) * min(abs(hv) + abs(fv), 1.0)
            else:
                net = hv + fv
                pos = net if abs(net) > 1e-9 else 0.0
                pos = np.clip(pos, -1.0, 1.0)
        elif abs(hv) > 1e-9:
            pos = hv
        elif abs(fv) > 1e-9:
            pos = fv
        else:
            pos = 0.0
        pos_after_vol = pos * vol_mult[t]
        dd_mult = 1.0
        if t > 0:
            start = max(0, t - trailing_window)
            eq_window = equity[start:t]
            if len(eq_window) > 0:
                peak = np.max(eq_window)
                current_dd = (equity[t-1] - peak) / peak if peak > 0 else 0
                dd_abs = -current_dd
                if dd_abs <= dd_recover:
                    dd_mult = 1.0
                elif dd_abs <= dd_warn:
                    dd_mult = 0.70
                else:
                    dd_mult = 0.30
        final_pos = pos_after_vol * dd_mult
        risk_pos[t] = final_pos
        if t > 0:
            r_price = (close[t] - close[t-1]) / close[t-1] if close[t-1] > 0 else 0
        else:
            r_price = 0.0
        strat_ret = risk_pos[t] * r_price
        dpos = abs(risk_pos[t] - risk_pos[t-1]) if t > 0 else abs(risk_pos[t])
        cost_ret = 2 * COST * dpos
        equity[t] = equity[t-1] * (1 + strat_ret - cost_ret) if t > 0 else 1.0
    return risk_pos

def run_baseline(h_target, f_target, idx):
    h = h_target.reindex(idx).fillna(0.0).values
    f = f_target.reindex(idx).fillna(0.0).values
    n = len(idx)
    combined_pos = np.zeros(n)
    for t in range(n):
        hv, fv = h[t], f[t]
        if abs(hv) > 1e-9 and abs(fv) > 1e-9:
            if np.sign(hv) == np.sign(fv):
                pos = np.sign(hv) * min(abs(hv) + abs(fv), 1.0)
            else:
                net = hv + fv
                pos = net if abs(net) > 1e-9 else 0.0
                pos = np.clip(pos, -1.0, 1.0)
        elif abs(hv) > 1e-9:
            pos = hv
        elif abs(fv) > 1e-9:
            pos = fv
        else:
            pos = 0.0
        combined_pos[t] = pos
    return combined_pos

def main():
    h_target, f_target = build_targets()
    idx = h_target.index
    price = price_series(idx)
    mask = (idx >= START) & (idx <= END)
    idx_w = idx[mask]
    h_w = h_target.reindex(idx_w)
    f_w = f_target.reindex(idx_w)
    price_w = price.reindex(idx_w)
    n = len(idx_w)

    # 基线
    base_pos = run_baseline(h_w, f_w, idx_w)
    base_trades = extract_trades(base_pos, price_w, idx_w)
    base_metrics = two_caliber_metrics(base_pos, price_w, base_trades)

    # 参数网格
    results = []
    for dd_recover in [0.05, 0.08, 0.10]:
        for dd_warn in [0.12, 0.15]:
            for dd_hard in [0.18, 0.20]:
                for vol_upper in [0.55, 0.65, 0.80]:
                    for vol_scale in [0.50, 0.60]:
                        pos = run_exp412(h_w, f_w, idx_w, price_w,
                                        dd_recover=dd_recover, dd_warn=dd_warn,
                                        dd_hard=dd_hard, vol_upper=vol_upper,
                                        vol_scale=vol_scale)
                        trades = extract_trades(pos, price_w, idx_w)
                        m = two_caliber_metrics(pos, price_w, trades)
                        results.append({
                            "dd_recover": dd_recover, "dd_warn": dd_warn, "dd_hard": dd_hard,
                            "vol_upper": vol_upper, "vol_scale": vol_scale,
                            "annual_return": m["annual_return_full"],
                            "sharpe_full": m["sharpe_full"],
                            "max_dd": m["max_drawdown_full"],
                            "win_rate": m["win_rate"],
                            "pl_ratio": m["pl_ratio"],
                            "n_trades": int(m["n_trades"]),
                        })

    df = pd.DataFrame(results)
    # 按夏普排序(回撤/波动调整后)
    df["calmar"] = df["annual_return"] / (df["max_dd"].abs() + 1e-9)
    df = df.sort_values("calmar", ascending=False)

    print("=" * 100)
    print("exp412 V4: 参数网格搜索 — Top 20 (按Calmar比率排序)")
    print("=" * 100)
    print(f"{'排名':<4}{'dd_rec':<8}{'dd_warn':<8}{'dd_hard':<8}{'vol_up':<8}{'vol_sc':<8}"
          f"{'年化':>8}{'夏普':>8}{'回撤':>8}{'Calmar':>8}{'胜率':>6}{'盈亏比':>8}")
    print("-" * 100)
    for i, (_, row) in enumerate(df.head(20).iterrows()):
        print(f"{i+1:<4}{row['dd_recover']:<8.2f}{row['dd_warn']:<8.2f}{row['dd_hard']:<8.2f}"
              f"{row['vol_upper']:<8.2f}{row['vol_scale']:<8.2f}"
              f"{row['annual_return']:>8.3f}{row['sharpe_full']:>8.3f}{row['max_dd']:>8.3f}"
              f"{row['calmar']:>8.2f}{row['win_rate']:>6.3f}{row['pl_ratio']:>8.3f}")

    print(f"\n基线 exp411: 年化{base_metrics['annual_return_full']:.3f} 夏普{base_metrics['sharpe_full']:.3f} "
          f"回撤{base_metrics['max_drawdown_full']:.3f}")
    print(f"基线 Calmar: {base_metrics['annual_return_full'] / abs(base_metrics['max_drawdown_full']):.2f}")

    # 保存完整结果
    df.to_csv(os.path.join(DATA, "exp412_param_grid.csv"), index=False)
    print(f"\n完整结果已保存: {DATA}/exp412_param_grid.csv ({len(df)}行)")

    # 最优参数
    best = df.iloc[0]
    print(f"\n最优参数: dd_recover={best['dd_recover']:.2f}, dd_warn={best['dd_warn']:.2f}, "
          f"dd_hard={best['dd_hard']:.2f}, vol_upper={best['vol_upper']:.2f}, vol_scale={best['vol_scale']:.2f}")
    print(f"最优结果: 年化{best['annual_return']:.3f} 夏普{best['sharpe_full']:.3f} "
          f"回撤{best['max_dd']:.3f} Calmar={best['calmar']:.2f}")

if __name__ == "__main__":
    main()
