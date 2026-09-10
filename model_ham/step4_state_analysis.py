"""
step4_state_analysis.py — 条件 RankIC + 收益统计 + 状态持续性
不重训 GMM，仅复用 state_id 标签
"""
import os
import numpy as np
import pandas as pd
from scipy import stats as sp_stats

BASE = "model_ham"

# ---------- 1. 读取数据 ----------
combined = pd.read_csv(os.path.join(BASE, "ham_state_combined.csv"))
combined["date"] = pd.to_datetime(combined["date"])
labels = pd.read_csv(os.path.join(BASE, "market_state_label.csv"))
labels["date"] = pd.to_datetime(labels["date"])

HAM_FACTORS = ["n_c", "n_f_minus_n_c", "Total_Demand"]
FEATURES = ["warehouse_stock", "basis_spot_main", "spread_near1_near3", "hv_20d", "oi_main"]

# 读 gmm_params.json 获取训练集切分
import json
with open(os.path.join(BASE, "gmm_params.json")) as f:
    params = json.load(f)
n_train = params["n_train"]

# 确保 combined 和 labels 日期对齐
# combined 已有 state_id_x（来自 step3 拼接），labels 有 state_id
# 直接用 labels 的 state_id 覆盖
combined = combined.drop(columns=["state_id"], errors="ignore")
combined = combined.merge(labels[["date", "state_id"]], on="date", how="inner")
# 重命名 state_id_x -> state_id 如果存在
if "state_id_x" in combined.columns:
    combined = combined.rename(columns={"state_id_x": "state_id"})
    combined = combined.drop(columns=["state_id_y"], errors="ignore")
print(f"合并后: {len(combined)} 行")
print(f"列名: {list(combined.columns)}")
print(f"state_id 分布: {combined['state_id'].value_counts().to_dict()}")

# ---------- 2. 计算未来收益率 ----------
# 用 ham 里的 close 列（LC0 主力连续）
close_col = "close"
combined["fwd_1d"] = combined[close_col].shift(-1) / combined[close_col] - 1
combined["fwd_3d"] = combined[close_col].shift(-3) / combined[close_col] - 1
combined["fwd_5d"] = combined[close_col].shift(-5) / combined[close_col] - 1

# ---------- 3. 条件 RankIC ----------
print("\n=== 3.1 条件 RankIC（训练集内） ===")
train_mask = combined.index < n_train
train_df = combined[train_mask]
print(f"训练集: {len(train_df)} 行")

def calc_rankic(subset, factor, target):
    valid = subset[[factor, target]].dropna()
    if len(valid) < 10:
        return None, None, None, len(valid)
    ic = valid[factor].rank().corr(valid[target].rank())
    n = len(valid)
    # t 检验: t = IC * sqrt((n-2)/(1-IC^2))
    if abs(ic) >= 1.0:
        return ic, None, None, n
    t_stat = ic * np.sqrt((n - 2) / (1 - ic**2))
    p_val = 2 * (1 - sp_stats.t.cdf(abs(t_stat), df=n-2))
    return ic, t_stat, p_val, n

# 训练集内分状态 RankIC
print(f"\n训练集内分状态 RankIC:")
print(f"{'State':<6} {'Factor':<18} {'RankIC':>8} {'t':>8} {'p':>8} {'n':>5}")
print("-" * 55)
train_results = []
for sid in sorted(combined["state_id"].unique()):
    sub = combined[(combined["state_id"] == sid) & (combined.index < n_train)]
    for factor in HAM_FACTORS:
        ic, t, p, n = calc_rankic(sub, factor, "fwd_1d")
        if ic is not None:
            print(f"  {sid:<5} {factor:<18} {ic:>8.4f} {t:>8.3f} {p:>8.4f} {n:>5}")
            train_results.append({"state": sid, "factor": factor, "rankic": ic, "t": t, "p": p, "n": n})

# 全样本分状态 RankIC
print(f"\n=== 3.2 条件 RankIC（全样本） ===")
full_results = []
print(f"{'State':<6} {'Factor':<18} {'RankIC':>8} {'t':>8} {'p':>8} {'n':>5}")
print("-" * 55)
for sid in sorted(combined["state_id"].unique()):
    sub = combined[combined["state_id"] == sid]
    for factor in HAM_FACTORS:
        ic, t, p, n = calc_rankic(sub, factor, "fwd_1d")
        if ic is not None:
            print(f"  {sid:<5} {factor:<18} {ic:>8.4f} {t:>8.3f} {p:>8.4f} {n:>5}")
            full_results.append({"state": sid, "factor": factor, "rankic": ic, "t": t, "p": p, "n": n})

# Bonferroni 校正
n_tests = len(full_results) * 3  # 4 states × 3 factors (全样本)
print(f"\nBonferroni 校正: {n_tests} 次检验, 校正阈值 p={0.05/n_tests:.4f}")

# 离散因子唯一值
print(f"\n=== 3.3 离散因子唯一值数量 ===")
for factor in HAM_FACTORS:
    nuniq = combined[factor].nunique()
    print(f"  {factor}: {nuniq} 个唯一值 (全局)")
    for sid in sorted(combined["state_id"].unique()):
        sub = combined[combined["state_id"] == sid]
        nuniq_sub = sub[factor].nunique()
        print(f"    State {sid}: {nuniq_sub} 个唯一值 (n={len(sub)})")

# ---------- 4. 状态收益统计 ----------
print(f"\n=== 4. 分状态远期收益统计 ===")
return_results = []
for sid in sorted(combined["state_id"].unique()):
    sub = combined[combined["state_id"] == sid]
    for horizon in ["fwd_1d", "fwd_3d", "fwd_5d"]:
        valid = sub[horizon].dropna()
        if len(valid) == 0:
            continue
        return_results.append({
            "state": sid,
            "horizon": horizon,
            "mean": valid.mean(),
            "std": valid.std(),
            "win_rate": (valid > 0).mean(),
            "n": len(valid)
        })

print(f"\n{'State':<6} {'Horizon':<8} {'Mean':>8} {'Std':>8} {'WinRate':>8} {'n':>5}")
print("-" * 45)
for r in return_results:
    print(f"  {r['state']:<5} {r['horizon']:<8} {r['mean']*100:>7.2f}% {r['std']*100:>7.2f}% {r['win_rate']*100:>7.1f}% {r['n']:>5}")

# ---------- 5. 状态持续性 ----------
print(f"\n=== 5. 状态持续性 ===")
states_ordered = combined.sort_values("date")["state_id"].values
transitions = pd.DataFrame({
    "from": states_ordered[:-1],
    "to": states_ordered[1:]
})
total_pairs = len(transitions)
stay = (transitions["from"] == transitions["to"]).sum()
print(f"总转移对: {total_pairs}, 保持: {stay}, 延续概率: {stay/total_pairs*100:.1f}%")

# 转移矩阵
print(f"\n转移矩阵:")
print(transitions.groupby(["from", "to"]).size().unstack(fill_value=0))

# 各状态延续概率
print(f"\n各状态延续概率:")
for sid in sorted(transitions["from"].unique()):
    sub = transitions[transitions["from"] == sid]
    stay_pct = (sub["to"] == sid).mean() * 100
    print(f"  State {sid}: {stay_pct:.1f}% (n={len(sub)})")

# ---------- 6. 输出文档 ----------
# 6a. state_ic_result.md
ic_lines = []
ic_lines.append("# 分状态 HAM 因子条件 RankIC 检验结果\n")
ic_lines.append(f"> 生成时间：2026-09-09\n")
ic_lines.append(f"> **核心变化**：加入 spread_near1_near3 后 GMM 重训，状态标签变化\n")
ic_lines.append("\n## 1. 训练集内分状态 RankIC\n")
ic_lines.append("| State | Factor | RankIC | t | p | n | 显著? |")
ic_lines.append("|-------|--------|--------|---|---|---|-------|")
for r in train_results:
    sig = "✅" if r["p"] < 0.05 else "❌"
    ic_lines.append(f"| {r['state']} | {r['factor']} | {r['rankic']:.4f} | {r['t']:.3f} | {r['p']:.4f} | {r['n']} | {sig} |")

ic_lines.append("\n## 2. 全样本分状态 RankIC\n")
ic_lines.append(f"> ⚠️ **全样本结果属于样本内分析，不能作为外推有效性证据**\n")
ic_lines.append("| State | Factor | RankIC | t | p | n | 显著? | Bonferroni显著? |")
ic_lines.append("|-------|--------|--------|---|---|---|-------|----------------|")
bonf_threshold = 0.05 / n_tests
for r in full_results:
    sig = "✅" if r["p"] < 0.05 else "❌"
    bonf_sig = "✅" if r["p"] < bonf_threshold else "❌"
    ic_lines.append(f"| {r['state']} | {r['factor']} | {r['rankic']:.4f} | {r['t']:.3f} | {r['p']:.4f} | {r['n']} | {sig} | {bonf_sig} |")

ic_lines.append(f"\n## 3. Bonferroni 校正\n")
ic_lines.append(f"- 总检验次数: {n_tests}")
ic_lines.append(f"- 校正阈值: p < {bonf_threshold:.4f}\n")

ic_lines.append(f"\n## 4. 离散因子统计功效\n")
for factor in HAM_FACTORS:
    nuniq = combined[factor].nunique()
    ic_lines.append(f"- **{factor}**: 全局 {nuniq} 个唯一值")
    for sid in sorted(combined["state_id"].unique()):
        sub = combined[combined["state_id"] == sid]
        nuniq_sub = sub[factor].nunique()
        ic_lines.append(f"  - State {sid}: {nuniq_sub} 个唯一值 (n={len(sub)})")

ic_lines.append(f"\n> ⚠️ 子样本内唯一值过少（<10）时，RankIC 统计功效极低，结论不可靠。\n")

ic_lines.append(f"\n## 5. 局限性\n")
ic_lines.append("1. 全样本结果 = 样本内分析，不代表样本外预测有效性")
ic_lines.append("2. n_c / n_f_minus_n_c 唯一值过少，RankIC 功效不足")
ic_lines.append("3. 多重比较未校正：部分 p<0.05 经 Bonferroni 后不显著")
ic_lines.append("4. 训练集/测试集切分严格按日期，但测试集状态可能失衡")

with open(os.path.join(BASE, "state_ic_result.md"), "w", encoding="utf-8") as f:
    f.write("\n".join(ic_lines))
print(f"\n已保存: state_ic_result.md")

# 6b. state_return_stats.md
ret_lines = []
ret_lines.append("# 分状态远期收益统计\n")
ret_lines.append(f"> 生成时间：2026-09-09\n")
ret_lines.append("| State | 天数 | 1d均值 | 1d胜率 | 3d均值 | 3d胜率 | 5d均值 | 5d胜率 |")
ret_lines.append("|-------|------|--------|--------|--------|--------|--------|--------|")
for sid in sorted(combined["state_id"].unique()):
    n = (combined["state_id"] == sid).sum()
    row = f"| {sid} | {n} |"
    for h in ["fwd_1d", "fwd_3d", "fwd_5d"]:
        r = [x for x in return_results if x["state"] == sid and x["horizon"] == h]
        if r:
            row += f" {r[0]['mean']*100:.2f}% | {r[0]['win_rate']*100:.1f}% |"
        else:
            row += f" N/A | N/A |"
    ret_lines.append(row)

ret_lines.append(f"\n## 波动率排序验证\n")
for sid in sorted(combined["state_id"].unique()):
    hv_mean = combined[combined["state_id"] == sid]["hv_20d"].mean()
    ret_std_5d = [x["std"] for x in return_results if x["state"] == sid and x["horizon"] == "fwd_5d"]
    ret_std_val = ret_std_5d[0] if ret_std_5d else 0
    ret_lines.append(f"- State {sid}: 聚类 hv_20d 均值={hv_mean:.4f}, 5d收益标准差={ret_std_val*100:.2f}%")

with open(os.path.join(BASE, "state_return_stats.md"), "w", encoding="utf-8") as f:
    f.write("\n".join(ret_lines))
print(f"已保存: state_return_stats.md")

# 6c. state_persistence.md
pers_lines = []
pers_lines.append("# 状态持续性与转移矩阵\n")
pers_lines.append(f"> 生成时间：2026-09-09\n")
pers_lines.append(f"## 整体延续概率\n")
pers_lines.append(f"- 总转移对: {total_pairs}")
pers_lines.append(f"- 保持同一状态: {stay}")
pers_lines.append(f"- **整体延续概率: {stay/total_pairs*100:.1f}%**\n")

pers_lines.append(f"## 转移矩阵（绝对频次）\n")
tm = transitions.groupby(["from", "to"]).size().unstack(fill_value=0)
header = "| from → to | " + " | ".join([f"State {c}" for c in tm.columns]) + " | 合计 |"
sep = "|---" * (len(tm.columns) + 2) + "|"
pers_lines.append(header)
pers_lines.append(sep)
for idx, row in tm.iterrows():
    row_str = f"| State {idx} | " + " | ".join([str(row[c]) for c in tm.columns]) + f" | {row.sum()} |"
    pers_lines.append(row_str)

pers_lines.append(f"\n## 各状态延续概率\n")
for sid in sorted(transitions["from"].unique()):
    sub = transitions[transitions["from"] == sid]
    stay_pct = (sub["to"] == sid).mean() * 100
    pers_lines.append(f"- State {sid}: {stay_pct:.1f}% (n={len(sub)})")

pers_lines.append(f"\n## 局限性\n")
pers_lines.append("1. 延续概率高可能反映聚类粒度（K=4 较粗），而非市场状态天然稳定")
pers_lines.append("2. 测试集状态分布可能失衡，部分状态转移样本不足")

with open(os.path.join(BASE, "state_persistence.md"), "w", encoding="utf-8") as f:
    f.write("\n".join(pers_lines))
print(f"已保存: state_persistence.md")

print("\nstep4 完成")
