#!/usr/bin/env python3
"""
exp432: HAM 行情边界量化复盘研究 —— 特征提取与判别层

【约束遵守】纯离线研究，只读数据，不修改 ham_pipeline.py / 风控阈值 / 实盘任务。

核心方法论 (基于探索性诊断修正):
  原始假设"价格单边 = 因子失效"被数据证伪 (Jaccard 仅 7.9%)。
  因此采用【双标签框架】:
    - 标签A (regime_trend): 前瞻20日价格强趋势 —— 衡量"行情边界"(价格形态)
    - 标签B (ic_fail):     IC_roll < 0 —— 衡量"因子失效"(策略可用性)
  用六维行情指标分别判别两个标签, 并对比其重合度与判别效果。
  真值标签用事后信息 (仅评估用), 判别器只用当前/历史信息 (无前视, 保证实盘可用)。

职责:
  1. 两类区间六维指标分布差异 (Mann-Whitney U + Cliff's delta)
  2. 双标签重合度分析 (本研究核心洞见)
  3. 简单阈值型判别器 + 预警提前量 + 误报/漏报
  4. 与现有 IC 告警的时间差对比

产出: data/exp432_feature_stats.json + data/exp432_discriminator_eval.json
"""
import os
import sys
import json
import numpy as np
import pandas as pd
from scipy import stats

EXP_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(EXP_DIR, "data")
sys.path.insert(0, EXP_DIR)
from exp432_build import build_dataset, classify_regime, IC_VALID_START, IC_VALID_END

SIX_DIMS = {
    "波动率(20日已实现%)": "rvol20",
    "持仓增减(20日%)": "pos_chg_20d_pct",
    "仓单环比代理(成交量20日%)": "vol_chg_20d_pct",
    "基差率(现货-期货价差%)": "basis_rate",
    "现货-期货价差绝对%": "spot_close_gap_pct",
    "成交活跃度(相对)": "vol_rel",
}


def two_sample_test(valid_vals, trend_vals):
    """两组分布差异: Mann-Whitney U + Cliff's delta 效应量."""
    v = pd.Series(valid_vals).dropna().values.astype(float)
    t = pd.Series(trend_vals).dropna().values.astype(float)
    out = {"n_valid": len(v), "n_trend": len(t)}
    if len(v) == 0 or len(t) == 0:
        return out
    out.update({
        "valid_mean": float(np.mean(v)), "valid_median": float(np.median(v)),
        "valid_p10": float(np.percentile(v, 10)), "valid_p90": float(np.percentile(v, 90)),
        "trend_mean": float(np.mean(t)), "trend_median": float(np.median(t)),
        "trend_p10": float(np.percentile(t, 10)), "trend_p90": float(np.percentile(t, 90)),
    })
    u, p = stats.mannwhitneyu(v, t, alternative="two-sided")
    delta = (np.sum(t[:, None] > v[None, :]) - np.sum(t[:, None] < v[None, :])) / (len(t) * len(v))
    out.update({"mwu_pvalue": float(p), "cliffs_delta": float(delta),
                "significant_005": bool(p < 0.05)})
    return out


def build_discriminator(d):
    """
    简单阈值型判别器 (无机器学习).

    基于双标签特征检验发现的高区分度维度设计:
      标签A (价格单边): 高波动 + 低持仓 + 基差异常
      标签B (因子失效): 高持仓 + 高成交 + 低波动 + 深贴水

    所有阈值只用当前及历史信息 (无前视), 保证实盘可用。
    """
    x = d.copy()
    # --- 标签A: 价格单边判别 ---
    x["A1_hivol"] = (x["rvol20_rel"] > 1.5).astype(int)            # 相对高波动
    x["A2_posout"] = (x["pos_chg_20d_pct"] < -8).astype(int)        # 持仓撤离
    x["A3_basisdrop"] = (x["basis_rate_chg5"] < -1.5).astype(int)   # 基差5日骤降
    x["A_trend"] = (x["A1_hivol"] + x["A2_posout"] + x["A3_basisdrop"]) > 0
    # --- 标签B: 因子失效判别 ---
    x["B1_posin"] = (x["pos_chg_20d_pct"] > 8).astype(int)          # 持仓大幅累积
    x["B2_volup"] = (x["vol_chg_20d_pct"] > 20).astype(int)         # 成交放量
    x["B3_vohigh"] = (x["vol_rel"] > 1.2).astype(int)               # 活跃度偏高
    x["B4_basisneg"] = (x["basis_rate"] < -2.0).astype(int)         # 深度贴水
    x["B5_lowvol"] = (x["rvol20_rel"] < 0.85).astype(int)           # 相对低波动
    x["B_icfail"] = (x["B1_posin"] + x["B2_volup"] + x["B3_vohigh"] +
                     x["B4_basisneg"] + x["B5_lowvol"]) >= 2        # 命中>=2项
    return x


def eval_discriminator(d, cond_mask, truth_col, truth_val=True, label=""):
    """
    评估判别器预警能力 (前瞻口径, 避免用同期真值):
      - 第 t 日命中, 若 t+1~t+15 窗口内出现真值 = 有效预警 (TP)
      - 第 t 日命中, 但窗口内无真值 = 误报 (FP)
      - 真值起点日前 10 日内无命中 = 漏报
    """
    n = len(d)
    hit = cond_mask.values.astype(bool)
    truth = (d[truth_col] == truth_val).values.astype(bool)

    tp_fwd = fp_fwd = 0
    for i in range(n):
        if not hit[i]:
            continue
        window = truth[i + 1:i + 16]     # 命中后 1~15 日
        if window.any():
            tp_fwd += 1
        else:
            fp_fwd += 1

    total_hit = int(hit.sum())
    # 真值"起点": 真值日且前一日非真值
    starts = [i for i in range(n) if truth[i] and (i == 0 or not truth[i - 1])]
    total_truth = len(starts)
    missed = sum(1 for i in starts if not hit[max(0, i - 10):i + 1].any())

    precision = tp_fwd / total_hit if total_hit else 0
    recall = (1 - missed / total_truth) if total_truth else 0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0

    return {
        "name": label,
        "total_hit_days": total_hit,
        "hit_rate_pct": total_hit / n * 100,
        "total_truth_events": total_truth,
        "tp_forward": tp_fwd,
        "fp_forward": fp_fwd,
        "precision_pct": precision * 100,
        "missed_events": missed,
        "recall_pct": recall * 100,
        "f1_pct": f1 * 100,
        "false_positive_rate_pct": fp_fwd / total_hit * 100 if total_hit else 0,
        "false_negative_rate_pct": missed / total_truth * 100 if total_truth else 0,
    }


def compute_lead_time(d, cond_mask, truth_col, truth_val=True, look=15):
    """测算提前量: 每个真值起点前最近的命中日间隔."""
    hit_idx = np.where(cond_mask.values.astype(bool))[0]
    truth = (d[truth_col] == truth_val).values.astype(bool)
    starts = [i for i in range(len(d)) if truth[i] and (i == 0 or not truth[i - 1])]
    leads = []
    for ts in starts:
        prior = hit_idx[hit_idx < ts]
        if len(prior) > 0:
            leads.append(ts - prior[-1])
    if not leads:
        return {"n_events": len(starts), "n_with_prior_hit": 0,
                "avg_lead_days": None, "median_lead_days": None}
    return {
        "n_events": len(starts), "n_with_prior_hit": len(leads),
        "avg_lead_days": float(np.mean(leads)),
        "median_lead_days": float(np.median(leads)),
        "min_lead_days": int(np.min(leads)),
        "max_lead_days": int(np.max(leads)),
    }


def compare_with_ic_alert(d, cond_mask, truth_col, truth_val=True):
    """对比新预警 vs 现有 IC 告警 (CRITICAL) 的触发时间差."""
    hit_idx = np.where(cond_mask.values.astype(bool))[0]
    ic_crit_idx = np.where(
        (d["alert_level"].values == "CRITICAL") |
        d["flags"].astype(str).str.contains("IC_CRIT", na=False).values
    )[0]
    truth = (d[truth_col] == truth_val).values.astype(bool)
    starts = [i for i in range(len(d)) if truth[i] and (i == 0 or not truth[i - 1])]

    diffs, n_new_earlier, n_ic_earlier, detail = [], 0, 0, []
    for ts in starts:
        pn = hit_idx[hit_idx < ts]
        pi = ic_crit_idx[ic_crit_idx < ts]
        if len(pn) == 0 or len(pi) == 0:
            continue
        last_new, last_ic = pn[-1], pi[-1]
        diff = int(last_ic - last_new)     # >0 = 新预警更早
        diffs.append(diff)
        n_new_earlier += diff > 0
        n_ic_earlier += diff < 0
        detail.append({"event_start": str(d["date"].iloc[ts].date()),
                       "new_alert_day": str(d["date"].iloc[last_new].date()),
                       "ic_alert_day": str(d["date"].iloc[last_ic].date()),
                       "diff_days_new_earlier": diff})
    return {
        "n_compared": len(diffs),
        "avg_diff_days": float(np.mean(diffs)) if diffs else None,
        "median_diff_days": float(np.median(diffs)) if diffs else None,
        "n_new_earlier": n_new_earlier, "n_ic_earlier": n_ic_earlier,
        "new_earlier_pct": n_new_earlier / len(diffs) * 100 if diffs else None,
        "detail": detail[:30],
    }


if __name__ == "__main__":
    print("=" * 78)
    print("exp432 特征提取 + 双标签判别器评估")
    print("=" * 78)

    df = build_dataset()
    df = df[(df["date"] >= IC_VALID_START) & (df["date"] <= IC_VALID_END)].reset_index(drop=True)
    df["regime_trend"] = classify_regime(df, 15, 0.08) == "TREND"
    df["ic_fail"] = df["IC_roll"] < 0
    df["ic_valid"] = df["IC_roll"].notna()

    # 双标签统计
    sub = df[df["ic_valid"]]
    both = (sub["regime_trend"] & sub["ic_fail"]).sum()
    only_trend = (sub["regime_trend"] & ~sub["ic_fail"]).sum()
    only_ic = (~sub["regime_trend"] & sub["ic_fail"]).sum()
    neither = (~sub["regime_trend"] & ~sub["ic_fail"]).sum()
    jaccard = both / (both + only_trend + only_ic) * 100 if (both + only_trend + only_ic) else 0

    print(f"\n[双标签概览] (有效IC日 {len(sub)})")
    print(f"  标签A 价格单边: {sub['regime_trend'].sum()} 日")
    print(f"  标签B 因子失效: {sub['ic_fail'].sum()} 日")
    print(f"  重合: {both} | 仅A: {only_trend} | 仅B: {only_ic} | 都不: {neither}")
    print(f"  Jaccard = {jaccard:.1f}%  << 核心洞见: 价格单边 ≠ 因子失效")

    # ============ 任务3: 六维指标分布差异 (双标签) ============
    print("\n" + "=" * 78)
    print("[任务3] 两类区间六维指标分布差异")
    print("=" * 78)
    feat_stats = {}
    for tlabel, tcol in [("标签A 价格单边", "regime_trend"), ("标签B 因子失效", "ic_fail")]:
        tdf = sub[sub[tcol]]
        vdf = sub[~sub[tcol]]
        print(f"\n--- {tlabel} (真值日 {len(tdf)}) vs 有效日 ({len(vdf)}) ---")
        feat_stats[tcol] = {}
        for name, col in SIX_DIMS.items():
            r = two_sample_test(vdf[col], tdf[col])
            feat_stats[tcol][col] = {"name": name, **r}
            sig = "***" if r.get("significant_005") else "   "
            vm = f"{r.get('valid_median',0):.3f}" if r.get('valid_median') is not None else "N/A"
            tm = f"{r.get('trend_median',0):.3f}" if r.get('trend_median') is not None else "N/A"
            pp = f"p={r['mwu_pvalue']:.4f}" if r.get('mwu_pvalue') is not None else "p=N/A"
            print(f"  {sig} {name:<26} 有效中位={vm:>9} 失效中位={tm:>9} {pp}")

    # ============ 任务4: 判别器评估 ============
    print("\n" + "=" * 78)
    print("[任务4] 简单阈值型判别器评估 (前瞻口径)")
    print("=" * 78)
    d = build_discriminator(sub.reset_index(drop=True))
    disc_eval, lead_times = {}, {}

    # 标签A: 价格单边
    for label, mask in [
        ("A1 相对高波动(rvol_rel>1.5)", d["A1_hivol"] > 0),
        ("A2 持仓撤离(pos20d<-8%)", d["A2_posout"] > 0),
        ("A3 基差骤降(chg5<-1.5)", d["A3_basisdrop"] > 0),
        ("★ A组合(OR:任一命中)", d["A_trend"] > 0),
    ]:
        e = eval_discriminator(d, mask, "regime_trend", True, label)
        disc_eval[label] = e
        lt = compute_lead_time(d, mask, "regime_trend")
        lead_times[label] = lt
        avgl = f"{lt['avg_lead_days']:.1f}" if lt['avg_lead_days'] else "N/A"
        print(f"  [A] {label}")
        print(f"      命中{e['total_hit_days']}日 精确率{e['precision_pct']:.1f}% "
              f"召回率{e['recall_pct']:.1f}% F1={e['f1_pct']:.1f}% 提前{avgl}天")

    # 标签B: 因子失效
    for label, mask in [
        ("B1 持仓累积(pos20d>8%)", d["B1_posin"] > 0),
        ("B2 成交放量(vol20d>20%)", d["B2_volup"] > 0),
        ("B3 活跃度偏高(vol_rel>1.2)", d["B3_vohigh"] > 0),
        ("B4 深度贴水(basis<-2%)", d["B4_basisneg"] > 0),
        ("B5 相对低波动(rvol_rel<0.85)", d["B5_lowvol"] > 0),
        ("★ B组合(>=2项命中)", d["B_icfail"] > 0),
    ]:
        e = eval_discriminator(d, mask, "ic_fail", True, label)
        disc_eval[label] = e
        lt = compute_lead_time(d, mask, "ic_fail")
        lead_times[label] = lt
        avgl = f"{lt['avg_lead_days']:.1f}" if lt['avg_lead_days'] else "N/A"
        print(f"  [B] {label}")
        print(f"      命中{e['total_hit_days']}日 精确率{e['precision_pct']:.1f}% "
              f"召回率{e['recall_pct']:.1f}% F1={e['f1_pct']:.1f}% 提前{avgl}天")

    # ============ 任务5: 与 IC 告警时间差 ============
    print("\n" + "=" * 78)
    print("[任务5] 新预警 vs 现有 IC 告警 时间差")
    print("=" * 78)
    ic_compare = {}
    for label, mask, tcol in [
        ("A组合(价格单边)", d["A_trend"] > 0, "regime_trend"),
        ("B组合(因子失效)", d["B_icfail"] > 0, "ic_fail"),
    ]:
        c = compare_with_ic_alert(d, mask, tcol)
        ic_compare[label] = c
        avgd = f"{c['avg_diff_days']:+.1f}" if c['avg_diff_days'] is not None else "N/A"
        pct = f"{c['new_earlier_pct']:.0f}%" if c['new_earlier_pct'] is not None else "N/A"
        print(f"  {label}: 对比{c['n_compared']}例 平均差{avgd}天 "
              f"新预警更早{c['n_new_earlier']}例({pct}) IC更早{c['n_ic_earlier']}例")

    # 保存
    out1 = os.path.join(DATA_DIR, "exp432_feature_stats.json")
    out2 = os.path.join(DATA_DIR, "exp432_discriminator_eval.json")
    with open(out1, "w") as f:
        json.dump({
            "regime_counts": {"valid_ic_days": int(len(sub)),
                             "label_A_trend": int(sub["regime_trend"].sum()),
                             "label_B_ic_fail": int(sub["ic_fail"].sum())},
            "dual_label_overlap": {
                "both": int(both), "only_trend": int(only_trend),
                "only_ic_fail": int(only_ic), "neither": int(neither),
                "jaccard_pct": float(jaccard)},
            "six_dims": feat_stats,
        }, f, ensure_ascii=False, indent=2)
    with open(out2, "w") as f:
        json.dump({"discriminator_eval": disc_eval, "lead_times": lead_times,
                   "ic_alert_compare": ic_compare}, f, ensure_ascii=False, indent=2)
    print(f"\n产出 -> {out1}")
    print(f"产出 -> {out2}")
