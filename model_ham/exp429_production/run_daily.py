"""
exp429 主入口: 每日运行 + 绩效验证 + 影子盘记录

一次性运行产出 exp429 全部交付物:
  1. 每日流水线输出 (D因子 + 信号 + 两套仓位)  ->  ham_pipeline.run_pipeline()
  2. 监控指标 + 漂移告警                        ->  monitoring.*
  3. 两套配置绩效验证 (年化/Calmar/回撤/笔数)   ->  本文件 run_validation()
  4. 持续影子盘日志 (逐日信号快照)               ->  本文件 run_shadow_log()
  5. 汇总 JSON                                  ->  data/exp429_summary.json

运行:
  /usr/bin/python3 model_ham/exp429_production/run_daily.py
"""
import os
import json
import sys
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import ham_pipeline as HP
from ham_pipeline import run_pipeline, DATA_DIR, CONFIGS, MAIN_SLIP_BP
import monitoring as MON

MODEL = "/home/ubuntu/lithium-engine/model_ham"
for d in ["exp409_ham_dynamic", "exp410_fund_dynamic", "exp411_combined",
          "exp413_robustness", "exp414_attribution"]:
    sys.path.insert(0, os.path.join(MODEL, d))
sys.path.insert(0, os.path.join(MODEL, "exp416_tail_risk_protect"))
sys.path.insert(0, os.path.join(MODEL, "exp424_t1_robustness"))
sys.path.insert(0, os.path.join(MODEL, "exp425_minute_execution"))

from exp425_engine import (
    RNG_SEED, calibrate_slippage, build_901_exec_prices, run_variant_full,
)
from exp424_engine import metrics_from_net_ret


# ============================================================
# 绩效验证: 两套配置 (配置A/B) 全样本回测
# ============================================================
def run_validation(ctx, df_ham):
    """用 9:01 分钟均价 + 10bp 滑点, 验证两套配置绩效。"""
    idx = ctx["idx"]
    price = ctx["price"]
    # 9:01 成交价标定 (固定种子, 保证与 exp425/426 锚点一致)
    dev_path = os.path.join(MODEL, "exp425_minute_execution", "data", "min_opening_deviation.csv")
    if os.path.exists(dev_path):
        dev_df = pd.read_csv(dev_path)
    else:
        import exp425_engine
        dev_df = exp425_engine.load_minute_deviation()
    slippage_cal = calibrate_slippage(dev_df)
    _, p901_px, _ = build_901_exec_prices(ctx, slippage_cal, vol_scale=True, seed=RNG_SEED)

    h_target = HP.ham_target_from(df_ham)
    results = {}
    for ckey, cfg in CONFIGS.items():
        pos = HP.compute_position_config(ctx, h_target, cfg["adx_scale"])
        nr, mi, tr = run_variant_full(ctx, pos, p901_px, slip_bp=MAIN_SLIP_BP, gap_filter_thr=0.0)
        # IC
        close = price.reindex(idx).values.astype(float)
        n = len(idx)
        r = np.zeros(n); r[1:] = (close[1:] - close[:-1]) / close[:-1]
        sig = pos[1:]; lab = r[1:]
        m = np.abs(sig) > 1e-9
        ic = float(np.corrcoef(sig[m], lab[m])[0, 1]) if m.sum() > 2 else 0.0
        results[ckey] = {
            "name": cfg["name"],
            "adx_scale": cfg["adx_scale"],
            "annual_return": float(mi["annual_return"]),
            "sharpe": float(mi["sharpe"]),
            "max_drawdown": float(mi["max_drawdown"]),
            "calmar": float(mi["calmar"]),
            "total_return": float(mi["total_return"]),
            "n_trades": int(mi["n_trades"]),
            "ic": ic,
            "hold_days": int((np.abs(pos) > 1e-9).sum()),
        }
    return results, slippage_cal


# ============================================================
# 持续影子盘日志: 逐日信号快照 (实盘每日追加一行)
# ============================================================
def run_shadow_log(daily, ctx, config_results):
    """
    生成持续影子盘日志表: 每个交易日一行, 记录:
      日期 / D因子 / 信号方向 / 配置A仓位 / 配置B仓位 / 当日动作。
    这是"持续影子盘模拟"的落盘载体 —— 实盘每日收盘运行流水线后追加最新日。
    """
    log_cols = ["date", "deviation", "disagreement", "alpha_t", "beta_t",
                "open_signal", "signal_dir", "pos_A", "pos_B", "action_A", "action_B"]
    log = daily[log_cols].copy()
    # 标记影子盘持仓天数统计
    log["shadow_active_A"] = (np.abs(log["pos_A"].values) > 1e-9).astype(int)
    log["shadow_active_B"] = (np.abs(log["pos_B"].values) > 1e-9).astype(int)
    log.to_csv(os.path.join(DATA_DIR, "exp429_shadow_log.csv"), index=False)
    return log


if __name__ == "__main__":
    print("=" * 78)
    print("exp429: HAM 碳酸锂实盘工程化 —— 每日运行流水线 + 绩效验证 + 影子盘")
    print("=" * 78)

    # --- 流水线 ---
    daily, ctx, df_ham, cr = run_pipeline()
    idx = ctx["idx"]
    print(f"\n[流水线] {idx[0].date()} ~ {idx[-1].date()} ({len(idx)} 日), 输出 {len(daily)} 行")

    # --- 绩效验证 ---
    print("\n" + "=" * 78)
    print("两套配置绩效验证 (9:01分钟均价 + 10bp滑点, 无前视)")
    print("=" * 78)
    results, slip_cal = run_validation(ctx, df_ham)
    for ckey, r in results.items():
        print(f"  {r['name']}: 年化={r['annual_return']*100:.1f}% 夏普={r['sharpe']:.3f} "
              f"回撤={r['max_drawdown']*100:.1f}% Calmar={r['calmar']:.2f} "
              f"笔数={r['n_trades']} IC={r['ic']:+.3f} 持仓日={r['hold_days']}")
        ref = CONFIGS[ckey]
        print(f"    锚定(exp426): 年化{ref['ref_annual']*100:.1f}% "
              f"Calmar{ref['ref_calmar']:.2f} 回撤{ref['ref_mdd']*100:.1f}% 笔数{ref['ref_trades']}")

    # --- 监控 ---
    print("\n" + "=" * 78)
    print("监控指标 + 漂移告警")
    print("=" * 78)
    ic = MON.compute_ic_series(ctx, cr["A"]["pos"])
    dd = MON.compute_deviation_dist(daily)
    wt = MON.compute_weight_series(daily)
    alerts, merged = MON.judge_drift(ic, dd, wt)
    valid_a = alerts[alerts["date"] >= pd.Timestamp("2024-08-01")]
    vc = valid_a["alert_level"].value_counts()
    print(f"  漂移告警(2024-08起): OK={vc.get('OK',0)} WARNING={vc.get('WARNING',0)} CRITICAL={vc.get('CRITICAL',0)}")
    ic_v = ic.dropna()
    print(f"  IC滚动: 均值={ic_v.mean():.4f} P10={ic_v.quantile(0.1):.4f} 正占比={(ic_v>0).mean()*100:.1f}%")

    # 保存监控
    alerts.to_csv(os.path.join(DATA_DIR, "exp429_alert_series.csv"), index=False)
    merged.to_csv(os.path.join(DATA_DIR, "exp429_monitor_metrics.csv"), index=False)

    # --- 影子盘日志 ---
    print("\n" + "=" * 78)
    print("持续影子盘日志")
    print("=" * 78)
    log = run_shadow_log(daily, ctx, cr)
    active_a = int(log["shadow_active_A"].sum())
    active_b = int(log["shadow_active_B"].sum())
    print(f"  影子盘记录 {len(log)} 日: 配置A活跃{active_a}日 配置B活跃{active_b}日")

    # --- 汇总 JSON ---
    summary = {
        "config": {
            "start": str(idx[0].date()), "end": str(idx[-1].date()),
            "n_days": int(len(idx)), "slip_bp": MAIN_SLIP_BP,
            "adx_threshold": 30.0, "est_window": HP.EST_TRAIN_WINDOW,
        },
        "slippage_calibration": {"n_days": int(slip_cal["n_days"]),
                                 "mean_dev_pct": float(slip_cal["mean_dev_pct"])},
        "performance": results,
        "monitoring": {
            "ic_mean": float(ic_v.mean()), "ic_p10": float(ic_v.quantile(0.1)),
            "ic_positive_ratio": float((ic_v > 0).mean()),
            "dev_median_mean": float(dd["dev_median"].dropna().mean()),
            "alpha_cv_mean": float(wt["alpha_cv_roll"].mean()),
            "beta_cv_mean": float(wt["beta_cv_roll"].mean()),
        },
        "drift_alerts": {
            "OK": int(vc.get("OK", 0)), "WARNING": int(vc.get("WARNING", 0)),
            "CRITICAL": int(vc.get("CRITICAL", 0)),
        },
        "shadow_log": {"n_days": int(len(log)), "active_A": active_a, "active_B": active_b},
    }
    with open(os.path.join(DATA_DIR, "exp429_summary.json"), "w") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(f"\n汇总已保存 -> data/exp429_summary.json")
    print("\n" + "=" * 78)
    print("exp429 全部交付物生成完成。")
    print("=" * 78)
