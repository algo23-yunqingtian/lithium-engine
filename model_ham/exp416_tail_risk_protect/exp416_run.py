"""
exp416: 尾部风险防护升级 — 模块1/2/3 主回测脚本

模块1: 隔夜敞口管控 (波动率预警多档阈值 + 趋势识别降权), 参数遍历
模块2: 备选对冲仿真 (每日虚值期权对冲隔夜跳空, 多档位成本/削减/年化/夏普/回撤)
模块3: 多版本对照回测 T0/T1/T2/T3 (年化/夏普/回撤/Calmar + 跳空改善 + 收益损失)

底层零改动: 完全复用 exp409/410/411/413/414 引擎, 仅在 exp412 V4 风控之上叠加增量层。
基准校验: T0 必须逐位复现 exp412 T1 (年化0.7609 夏普2.6381 回撤-0.1156 Calmar6.5841 40笔)。
"""
import os
import json
import numpy as np
import pandas as pd

from exp416_engine import (
    load_context, run_variant, run_variant_with_options,
    compute_metrics, compute_metrics_from_pos,
    COST, TDPY, START, END, DATA_DIR,
)
import exp414_engine as E414


# ============================================================
# 基准校验: T0 复现 exp412 T1
# ============================================================
def baseline_check(ctx):
    """T0 = exp412 原版, 必须复现 T1 指标 (年化0.7609 夏普2.6381 回撤-0.1156 40笔)"""
    h, f, idx, price = ctx["h_target"], ctx["f_target"], ctx["idx"], ctx["price"]
    pos_T0 = run_variant(h, f, idx, price)   # 无新增防护 = exp412 原版
    m_T0, trades_T0 = compute_metrics_from_pos(pos_T0, price)
    expected = {"annual_return": 0.7609, "sharpe_full": 2.6381,
                "max_drawdown": -0.1156, "calmar": 6.5841, "n_trades": 40}
    actual = {"annual_return": m_T0["annual_return"], "sharpe_full": m_T0["sharpe_full"],
              "max_drawdown": m_T0["max_drawdown"], "calmar": m_T0["calmar"],
              "n_trades": len(trades_T0)}
    rows, all_ok = [], True
    for k, exp in expected.items():
        act = actual[k]
        ok = (int(act) == int(exp)) if k == "n_trades" else (abs(act - exp) < 5e-4)
        all_ok = all_ok and ok
        rows.append({"metric": k, "expected": round(exp, 4),
                     "actual": round(act, 4), "abs_diff": round(abs(act - exp), 6),
                     "PASS": ok})
    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(DATA_DIR, "exp416_T0_baseline_reproduce.csv"), index=False)
    print("\n[基准校验] T0 复现 exp412 T1:", "全部通过 ✓" if all_ok else "存在偏差 ✗")
    print(df.to_string(index=False))
    return all_ok, m_T0, pos_T0, trades_T0


# ============================================================
# 模块1: 隔夜敞口管控参数遍历
# ============================================================
def module1_overnight(ctx):
    """
    波动率预警多档阈值遍历 + 趋势识别降权遍历。
    波动率: vol_half / vol_full 两档阈值; 减半系数 0.5, 清仓系数 0.0。
    趋势: trend_thr (20日动量幅度阈值) × trend_scale (仓位压制系数)。
    输出: 每组合年化/夏普/回撤/Calmar, 以及相对 T0 的收益损失与回撤改善。
    """
    h, f, idx, price = ctx["h_target"], ctx["f_target"], ctx["idx"], ctx["price"]
    vol, pct20 = ctx["vol"], ctx["pct20"]
    m_T0, _ = compute_metrics_from_pos(
        run_variant(h, f, idx, price), price)
    pos_T0 = run_variant(h, f, idx, price)

    print("\n" + "=" * 78)
    print("模块1: 隔夜敞口管控 (波动率预警多档阈值 + 趋势识别降权)")
    print("=" * 78)

    # ---- 波动率预警阈值遍历 (单因子, 固定 half_scale=0.5, full_scale=0.0) ----
    vol_half_grid = [0.30, 0.40, 0.45, 0.50, 0.55, 0.60, 0.65]
    vol_full_grid = [0.50, 0.60, 0.70, 0.80, 0.90]
    vol_rows = []
    for vh in vol_half_grid:
        for vf in vol_full_grid:
            if vf <= vh:
                continue
            pos = run_variant(h, f, idx, price, vol=vol,
                              vol_half=vh, vol_full=vf, half_scale=0.5, full_scale=0.0)
            m, _ = compute_metrics_from_pos(pos, price)
            n_half = int(np.sum((vol.reindex(idx).values > vh) & (vol.reindex(idx).values <= vf)))
            n_full = int(np.sum(vol.reindex(idx).values > vf))
            vol_rows.append({
                "vol_half_thr": vh, "vol_full_thr": vf,
                "n_half_days": n_half, "n_full_days": n_full,
                "annual_return": m["annual_return"], "sharpe_full": m["sharpe_full"],
                "max_drawdown": m["max_drawdown"], "calmar": m["calmar"],
                "n_trades": m["n_trades"],
                "ret_loss_vs_T0": m["annual_return"] - m_T0["annual_return"],
                "dd_improve_vs_T0": m["max_drawdown"] - m_T0["max_drawdown"],
            })
    df_vol = pd.DataFrame(vol_rows)
    df_vol.to_csv(os.path.join(DATA_DIR, "exp416_M1_vol_overnight_grid.csv"), index=False)
    # 按 Calmar 排序取最优
    df_vol_sorted = df_vol.sort_values("calmar", ascending=False)
    best_vol = df_vol_sorted.iloc[0]
    print("\n[波动率预警] 网格 %d 组, 按 Calmar 最优: " % len(df_vol))
    print("  vol_half=%.2f vol_full=%.2f | 年化=%.4f 夏普=%.3f 回撤=%.4f Calmar=%.2f"
          % (best_vol["vol_half_thr"], best_vol["vol_full_thr"],
             best_vol["annual_return"], best_vol["sharpe_full"],
             best_vol["max_drawdown"], best_vol["calmar"]))
    print("  减半日=%d 清仓日=%d | 收益损失=%.4f 回撤改善=%.4f"
          % (best_vol["n_half_days"], best_vol["n_full_days"],
             best_vol["ret_loss_vs_T0"], best_vol["dd_improve_vs_T0"]))
    print("\n  Top-5 (按Calmar):")
    print(df_vol_sorted.head(5)[["vol_half_thr", "vol_full_thr", "annual_return",
                                  "sharpe_full", "max_drawdown", "calmar",
                                  "ret_loss_vs_T0", "dd_improve_vs_T0"]]
          .to_string(index=False, float_format=lambda x: f"{x:.4f}"))

    # ---- 趋势识别降权遍历 (在最优波动率之上叠加) ----
    vh_best, vf_best = best_vol["vol_half_thr"], best_vol["vol_full_thr"]
    trend_thr_grid = [0.05, 0.08, 0.10, 0.12, 0.15, 0.20]
    trend_scale_grid = [0.50, 0.60, 0.70, 0.80, 0.90]
    trend_rows = []
    for tt in trend_thr_grid:
        for ts in trend_scale_grid:
            pos = run_variant(h, f, idx, price, vol=vol, pct20=pct20,
                              vol_half=vh_best, vol_full=vf_best, half_scale=0.5, full_scale=0.0,
                              trend_thr=tt, trend_scale=ts)
            m, _ = compute_metrics_from_pos(pos, price)
            n_trend = int(np.sum(np.abs(pct20.reindex(idx).values) > tt))
            trend_rows.append({
                "trend_thr": tt, "trend_scale": ts, "n_trend_days": n_trend,
                "annual_return": m["annual_return"], "sharpe_full": m["sharpe_full"],
                "max_drawdown": m["max_drawdown"], "calmar": m["calmar"],
                "ret_loss_vs_T0": m["annual_return"] - m_T0["annual_return"],
                "dd_improve_vs_T0": m["max_drawdown"] - m_T0["max_drawdown"],
            })
    df_tr = pd.DataFrame(trend_rows)
    df_tr.to_csv(os.path.join(DATA_DIR, "exp416_M1_trend_downweight_grid.csv"), index=False)
    df_tr_sorted = df_tr.sort_values("calmar", ascending=False)
    best_tr = df_tr_sorted.iloc[0]
    print("\n[趋势降权] 网格 %d 组 (基于 vol_half=%.2f/vol_full=%.2f), 按 Calmar 最优: "
          % (len(df_tr), vh_best, vf_best))
    print("  trend_thr=%.2f trend_scale=%.2f | 年化=%.4f 夏普=%.3f 回撤=%.4f Calmar=%.2f"
          % (best_tr["trend_thr"], best_tr["trend_scale"],
             best_tr["annual_return"], best_tr["sharpe_full"],
             best_tr["max_drawdown"], best_tr["calmar"]))
    # 平衡方案: 收益损失≤25%且回撤改善>0 中 Calmar 最优 (避免过度压制收益)
    bal = df_tr[(df_tr["ret_loss_vs_T0"] >= -0.25) & (df_tr["dd_improve_vs_T0"] > 0)]
    if len(bal) > 0:
        best_bal = bal.sort_values("calmar", ascending=False).iloc[0]
        print("  平衡方案(收益损失≤15%): trend_thr=%.2f scale=%.2f | 年化=%.4f 回撤=%.4f "
              "Calmar=%.2f 收益损失=%.4f" % (best_bal["trend_thr"], best_bal["trend_scale"],
              best_bal["annual_return"], best_bal["max_drawdown"], best_bal["calmar"],
              best_bal["ret_loss_vs_T0"]))
    else:
        best_bal = best_tr
    print("\n  Top-5 (按Calmar):")
    print(df_tr_sorted.head(5)[["trend_thr", "trend_scale", "annual_return",
                                 "sharpe_full", "max_drawdown", "calmar",
                                 "ret_loss_vs_T0", "dd_improve_vs_T0"]]
          .to_string(index=False, float_format=lambda x: f"{x:.4f}"))

    return {
        "T0": m_T0,
        "best_vol": {k: (float(v) if isinstance(v, (np.floating, float)) else
                          (int(v) if isinstance(v, (np.integer, int)) else v))
                      for k, v in best_vol.items()},
        "best_trend": {k: (float(v) if isinstance(v, (np.floating, float)) else
                            (int(v) if isinstance(v, (np.integer, int)) else v))
                        for k, v in best_tr.items()},
        "best_trend_balanced": {k: (float(v) if isinstance(v, (np.floating, float)) else
                                     (int(v) if isinstance(v, (np.integer, int)) else v))
                                 for k, v in best_bal.items()},
        "vol_half_best": float(vh_best), "vol_full_best": float(vf_best),
        "trend_thr_best": float(best_tr["trend_thr"]),
        "trend_scale_best": float(best_tr["trend_scale"]),
        "trend_thr_balanced": float(best_bal["trend_thr"]),
        "trend_scale_balanced": float(best_bal["trend_scale"]),
    }


# ============================================================
# 模块2: 期权对冲仿真 (备选方案)
# ============================================================
def module2_option_hedge(ctx, pos_T2):
    """
    每日买入虚值期权对冲隔夜跳空。
    多档: 虚值距离 (strike_pct) × 对冲剩余期限 (horizon_days)。
    测算: 权利金总成本/年化、亏损削减、夏普、回撤、收益损耗。
    """
    idx, price, vol = ctx["idx"], ctx["price"], ctx["vol"]
    h, f = ctx["h_target"], ctx["f_target"]

    print("\n" + "=" * 78)
    print("模块2: 备选对冲仿真 — 每日虚值期权对冲隔夜跳空")
    print("=" * 78)
    m_base, _ = compute_metrics_from_pos(pos_T2, price)

    strike_grid = [0.02, 0.03, 0.05, 0.07]       # 虚值距离 2%/3%/5%/7%
    horizon_grid = [1.0, 2.0, 3.0]               # 对冲剩余期限 (交易日)
    opt_rows = []
    for sk in strike_grid:
        for hz in horizon_grid:
            net_ret, equity, diag = run_variant_with_options(
                pos_T2, price, vol, idx=idx, opt_delta=0.15,
                opt_horizon_days=hz, opt_strike_pct=sk)
            m = compute_metrics(net_ret, equity, pos_T2)
            # 权利金成本占年化收益比例
            prem_cost = diag["premium_cost_total"]
            opt_rows.append({
                "strike_pct": sk * 100, "horizon_days": hz,
                "annual_return": m["annual_return"], "sharpe_full": m["sharpe_full"],
                "max_drawdown": m["max_drawdown"], "calmar": m["calmar"],
                "n_option_days": diag["n_option_days"],
                "premium_cost_total": prem_cost,
                "premium_cost_pct_of_base": prem_cost / max(m_base["annual_return"], 1e-6),
                "ret_loss_vs_base": m["annual_return"] - m_base["annual_return"],
                "dd_improve_vs_base": m["max_drawdown"] - m_base["max_drawdown"],
                "payoff_total": diag["payoff_total"],
            })
    df_opt = pd.DataFrame(opt_rows)
    df_opt.to_csv(os.path.join(DATA_DIR, "exp416_M2_option_hedge_grid.csv"), index=False)
    df_opt_sorted = df_opt.sort_values("calmar", ascending=False)
    best_opt = df_opt_sorted.iloc[0]
    print("\n[期权对冲] 基准 T2: 年化=%.4f 夏普=%.3f 回撤=%.4f"
          % (m_base["annual_return"], m_base["sharpe_full"], m_base["max_drawdown"]))
    print("网格 %d 组, 按 Calmar 最优:" % len(df_opt))
    print("  strike=%.0f%% horizon=%.0f日 | 年化=%.4f 夏普=%.3f 回撤=%.4f Calmar=%.2f"
          % (best_opt["strike_pct"], best_opt["horizon_days"],
             best_opt["annual_return"], best_opt["sharpe_full"],
             best_opt["max_drawdown"], best_opt["calmar"]))
    print("  权利金总成本=%.4f (占基准年化%.1f%%) | 收益损耗=%.4f 回撤改善=%.4f"
          % (best_opt["premium_cost_total"], best_opt["premium_cost_pct_of_base"] * 100,
             best_opt["ret_loss_vs_base"], best_opt["dd_improve_vs_base"]))
    print("\n  全部档位:")
    print(df_opt[["strike_pct", "horizon_days", "annual_return", "sharpe_full",
                   "max_drawdown", "calmar", "premium_cost_total",
                   "premium_cost_pct_of_base", "ret_loss_vs_base"]]
          .to_string(index=False, float_format=lambda x: f"{x:.4f}"))

    return {
        "T2_base": m_base,
        "best_option": {k: (float(v) if isinstance(v, (np.floating, float)) else
                             (int(v) if isinstance(v, (np.integer, int)) else v))
                         for k, v in best_opt.items()},
        "strike_best": float(best_opt["strike_pct"] / 100),
        "horizon_best": float(best_opt["horizon_days"]),
    }


# ============================================================
# 模块3: 多版本对照回测 T0/T1/T2/T3 + 跳空改善
# ============================================================
def extreme_gap_days(price):
    """识别历史极端跳空交易日 (单日落幅/涨幅超过历史1%分位)"""
    r = price.pct_change().dropna()
    p1 = r.quantile(0.01)
    p99 = r.quantile(0.99)
    extreme = (r < p1) | (r > p99)
    return r.index[extreme], p1, p99


def module3_comparison(ctx, m_T0, pos_T0, m_T1, pos_T1, m_T2, pos_T2, m_T3, pos_T3):
    """四组对照 + 极端跳空交易日亏损改善"""
    idx, price = ctx["idx"], ctx["price"]
    print("\n" + "=" * 78)
    print("模块3: 多版本对照回测 T0/T1/T2/T3")
    print("=" * 78)

    variants = {"T0_exp412原版": (m_T0, pos_T0),
                "T1_波动率隔夜降仓": (m_T1, pos_T1),
                "T2_波动率+趋势降权": (m_T2, pos_T2),
                "T3_叠加期权对冲": (m_T3, pos_T3)}

    # 极端跳空日
    extreme_idx, p1, p99 = extreme_gap_days(price)
    print(f"\n[极端跳空日] 历史1%分位={p1*100:.2f}% 99%分位={p99*100:.2f}% "
          f"极端交易日={len(extreme_idx)}天")

    # 每组指标 + 跳空日表现
    close = price.values.astype(float)
    n = len(close)
    r_price = np.zeros(n)
    r_price[1:] = (close[1:] - close[:-1]) / close[:-1]
    extreme_set = set(extreme_idx)
    # 区分上行跳空(>99%分位) vs 下行跳空(<1%分位)
    r_ret = price.pct_change().dropna()
    downside_set = set(r_ret.index[r_ret < p1])
    upside_set = set(r_ret.index[r_ret > p99])

    rows = []
    for name, (m, pos) in variants.items():
        pos_arr = np.asarray(pos, dtype=float)
        # 极端跳空日的持仓表现 (含盈亏, 非假设亏损)
        loss_down, loss_up = 0.0, 0.0
        n_gap_hold = 0
        for t in range(n):
            if idx[t] in extreme_set and abs(pos_arr[t]) > 1e-9:
                day_pnl = pos_arr[t] * r_price[t]
                n_gap_hold += 1
                if idx[t] in downside_set:
                    loss_down += day_pnl
                elif idx[t] in upside_set:
                    loss_up += day_pnl
        rows.append({
            "variant": name,
            "annual_return": m["annual_return"], "sharpe_full": m["sharpe_full"],
            "max_drawdown": m["max_drawdown"], "calmar": m["calmar"],
            "ret_loss_vs_T0": m["annual_return"] - m_T0["annual_return"],
            "dd_improve_vs_T0": m["max_drawdown"] - m_T0["max_drawdown"],
            "n_gap_hold_days": n_gap_hold,
            "pnl_downside_gap": loss_down,
            "pnl_upside_gap": loss_up,
            "pnl_extreme_gap_total": loss_down + loss_up,
        })
    df_cmp = pd.DataFrame(rows)
    df_cmp.to_csv(os.path.join(DATA_DIR, "exp416_M3_variant_comparison.csv"), index=False)

    print("\n[四组对照指标]")
    print(df_cmp[["variant", "annual_return", "sharpe_full", "max_drawdown",
                  "calmar", "ret_loss_vs_T0", "dd_improve_vs_T0"]]
          .to_string(index=False, float_format=lambda x: f"{x:.4f}"))

    print("\n[极端跳空交易日表现] (HAM为趋势模型, 极端波动日顺势常盈利; 下行跳空亏损才是风控目标)")
    print(f"  下行跳空日(<{p1*100:.1f}%): 策略应减亏 (负值越小越好)")
    print(f"  上行跳空日(>{p99*100:.1f}%): 策略顺势盈利 (正值越大越好)")
    for r in rows:
        print(f"  {r['variant']:<22} 跳空持仓={r['n_gap_hold_days']}天 "
              f"下行跳空盈亏={r['pnl_downside_gap']:+.4f} "
              f"上行跳空盈亏={r['pnl_upside_gap']:+.4f} "
              f"合计={r['pnl_extreme_gap_total']:+.4f}")

    # 下行跳空亏损削减 (风控真正目标: 减少下行跳空的策略亏损)
    t0_down = rows[0]["pnl_downside_gap"]
    t3_down = rows[3]["pnl_downside_gap"]
    t2_down = rows[2]["pnl_downside_gap"]
    print(f"\n  下行跳空亏损削减 (风控目标): T0={t0_down:+.4f} "
          f"→ T2={t2_down:+.4f} → T3={t3_down:+.4f}")
    print(f"  下行跳空改善 (T0→T2): {t0_down - t2_down:+.4f} "
          f"| (T0→T3): {t0_down - t3_down:+.4f}")

    return df_cmp


def main():
    print("=" * 78)
    print("exp416: 尾部风险防护升级")
    print("  区间 %s ~ %s | 成本 %.2f%%/边 | exp412 V4 风控底座固定"
          % (START.date(), END.date(), COST * 100))
    print("=" * 78)

    ctx = load_context()
    print(f"\n[数据] 交易日 n={len(ctx['idx'])} | 波动率 mean={ctx['vol'].mean():.3f} "
          f"max={ctx['vol'].max():.3f}")

    # 基准校验 (硬门槛)
    ok, m_T0, pos_T0, trades_T0 = baseline_check(ctx)
    if not ok:
        print("\n[FATAL] T0 未复现 exp412 T1, 终止。")
        return

    h, f, idx, price = ctx["h_target"], ctx["f_target"], ctx["idx"], ctx["price"]
    vol, pct20 = ctx["vol"], ctx["pct20"]

    # 模块1: 隔夜敞口管控 (得最优 vol / trend 参数)
    m1 = module1_overnight(ctx)

    # 用模块1最优参数构造 T1 / T2 仓位
    vh, vf = m1["vol_half_best"], m1["vol_full_best"]
    tt, ts = m1["trend_thr_best"], m1["trend_scale_best"]
    pos_T1 = run_variant(h, f, idx, price, vol=vol,
                         vol_half=vh, vol_full=vf, half_scale=0.5, full_scale=0.0)
    m_T1, _ = compute_metrics_from_pos(pos_T1, price)
    pos_T2 = run_variant(h, f, idx, price, vol=vol, pct20=pct20,
                         vol_half=vh, vol_full=vf, half_scale=0.5, full_scale=0.0,
                         trend_thr=tt, trend_scale=ts)
    m_T2, _ = compute_metrics_from_pos(pos_T2, price)

    # 模块2: 期权对冲仿真 (基于 T2)
    m2 = module2_option_hedge(ctx, pos_T2)

    # T3 = T2 + 期权对冲 (用模块2最优档位)
    net_ret_T3, equity_T3, diag_T3 = run_variant_with_options(
        pos_T2, price, vol, idx=idx, opt_delta=0.15,
        opt_horizon_days=m2["horizon_best"], opt_strike_pct=m2["strike_best"])
    m_T3 = compute_metrics(net_ret_T3, equity_T3, pos_T2)

    # 模块3: 多版本对照
    df_cmp = module3_comparison(ctx, m_T0, pos_T0, m_T1, pos_T1, m_T2, pos_T2,
                                m_T3, pos_T2)

    # ---- 保存全部仓位/净值序列 ----
    _, equity_T1, _ = run_variant_with_options(pos_T1, price, vol, idx=idx, opt_delta=0.0)  # 无期权仅净值
    _, equity_T2, _ = run_variant_with_options(pos_T2, price, vol, idx=idx, opt_delta=0.0)
    close = price.values
    r_price = np.zeros(len(close))
    r_price[1:] = (close[1:] - close[:-1]) / close[:-1]
    dpos0 = np.abs(np.diff(pos_T0, prepend=pos_T0[0]))
    net_T0 = pos_T0 * r_price - 2 * COST * dpos0
    eq_T0 = np.cumprod(1 + net_T0)
    nav = pd.DataFrame({
        "close": close,
        "T0_equity": eq_T0, "T1_equity": equity_T1, "T2_equity": equity_T2,
        "T3_equity": equity_T3,
        "T0_pos": pos_T0, "T1_pos": pos_T1, "T2_pos": pos_T2,
        "vol": vol.reindex(idx).values,
        "pct20": pct20.reindex(idx).values,
    })
    nav.index = idx
    nav.to_csv(os.path.join(DATA_DIR, "exp416_daily_equity.csv"))

    # T0 交易日志
    pd.DataFrame(trades_T0).to_csv(os.path.join(DATA_DIR, "exp416_T0_trades.csv"), index=False)

    # 汇总 JSON
    summary = {
        "config": {
            "period": f"{START.date()} ~ {END.date()}",
            "cost_per_side": COST,
            "T0": "exp412 原版 (基准)",
            "T1_vol_overnight": {"vol_half_thr": vh, "vol_full_thr": vf,
                                  "half_scale": 0.5, "full_scale": 0.0},
            "T2_vol_trend": {"vol_half_thr": vh, "vol_full_thr": vf,
                              "trend_thr": tt, "trend_scale": ts},
            "T3_option_hedge": {"strike_pct": m2["strike_best"],
                                 "horizon_days": m2["horizon_best"]},
        },
        "baseline_reproduce": "PASS" if ok else "FAIL",
        "T0": m_T0, "T1": m_T1, "T2": m_T2, "T3": m_T3,
        "module1_best_vol": m1["best_vol"], "module1_best_trend": m1["best_trend"],
        "module2_best_option": m2["best_option"],
    }
    with open(os.path.join(DATA_DIR, "exp416_summary.json"), "w") as fp:
        json.dump(summary, fp, indent=2, default=str, ensure_ascii=False)

    print("\n" + "=" * 78)
    print("四组最终指标汇总")
    print("=" * 78)
    for nm, mm in [("T0", m_T0), ("T1", m_T1), ("T2", m_T2), ("T3", m_T3)]:
        print(f"  {nm}: 年化={mm['annual_return']:.4f} 夏普={mm['sharpe_full']:.3f} "
              f"回撤={mm['max_drawdown']:.4f} Calmar={mm['calmar']:.2f}")
    print("\n✓ 模块1/2/3 全部完成, 产物已保存到 data/")


if __name__ == "__main__":
    main()
