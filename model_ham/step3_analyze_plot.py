"""
step3_analyze_plot.py — 聚类报告 + 分布图 + HAM因子拼接
"""
import os
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

plt.rcParams["font.sans-serif"] = ["WenQuanYi Zen Hei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

BASE = "model_ham"

# ---------- 1. 读取数据 ----------
labels = pd.read_csv(os.path.join(BASE, "market_state_label.csv"))
labels["date"] = pd.to_datetime(labels["date"])
ham = pd.read_csv(os.path.join(BASE, "ham_factors_full_800d.csv"))
ham["date"] = pd.to_datetime(ham["date"])

FEATURES = ["warehouse_stock", "basis_spot_main", "spread_near1_near3", "hv_20d", "oi_main"]

# ---------- 2. 各状态统计 ----------
print("=== 各状态特征统计 ===")
state_stats = {}
for sid in sorted(labels["state_id"].unique()):
    sub = labels[labels["state_id"] == sid]
    stats = {}
    for f in FEATURES:
        stats[f] = {
            "mean": sub[f].mean(),
            "std": sub[f].std(),
            "median": sub[f].median(),
            "q25": sub[f].quantile(0.25),
            "q75": sub[f].quantile(0.75),
        }
    state_stats[sid] = stats
    print(f"\nState {sid} (n={len(sub)}):")
    for f in FEATURES:
        s = stats[f]
        print(f"  {f}: mean={s['mean']:.2f}, std={s['std']:.2f}, "
              f"median={s['median']:.2f}, Q25={s['q25']:.2f}, Q75={s['q75']:.2f}")

# ---------- 3. 出图 ----------
print("\n生成状态分布图...")
colors = ["#4ecdc4", "#ff6b6b", "#ffd93d", "#6c5ce7"]

fig, axes = plt.subplots(2, 3, figsize=(18, 10))
fig.suptitle("碳酸锂 GMM 市场状态聚类 — 5 特征分布对比 (K=4)", fontsize=16, fontweight="bold")

for i, f in enumerate(FEATURES):
    ax = axes[i // 3][i % 3]
    for sid, color in enumerate(colors):
        sub = labels[labels["state_id"] == sid]
        ax.hist(sub[f], bins=20, alpha=0.6, label=f"State {sid}", color=color, edgecolor="white")
    ax.set_title(f"{f}", fontsize=11)
    ax.legend(fontsize=8)
    ax.grid(True, alpha=0.3)

# 第6个图：状态分布柱状图
ax = axes[1][2]
counts = labels["state_id"].value_counts().sort_index()
bars = ax.bar([f"S{i}" for i in range(4)], counts.values, color=colors, edgecolor="white")
for bar, cnt in zip(bars, counts.values):
    ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 5,
            f"{cnt} ({cnt/len(labels)*100:.1f}%)", ha="center", va="bottom", fontsize=10)
ax.set_title("状态样本分布", fontsize=11)
ax.set_ylabel("天数")
ax.grid(True, alpha=0.3, axis="y")

plt.tight_layout()
plot_path = os.path.join(BASE, "state_distribution_plot.png")
plt.savefig(plot_path, dpi=150, bbox_inches="tight")
print(f"  已保存: {plot_path}")

# ---------- 4. 写聚类报告 ----------
report_lines = []
report_lines.append("# 碳酸锂 GMM 市场状态聚类报告（第二轮：补入 spread_near1_near3）\n")
report_lines.append(f"> 生成时间：2026-09-09\n")
report_lines.append(f"> **核心变化**：加入【滚动近月1-近月3跨期价差 spread_near1_near3，动态滚动合约，非固定 LC01/LC03】\n")

# BIC
report_lines.append("\n## 1. BIC 选择理由\n")
report_lines.append("| K | BIC |")
report_lines.append("|---|-----|")
with open(os.path.join(BASE, "gmm_params.json")) as f:
    import json
    params = json.load(f)
for k, bic in params["bic_results"].items():
    report_lines.append(f"| {k} | {bic:.2f} |")
report_lines.append(f"\n**BIC 在 [2,3,4] 单调下降**，选候选内最优 K=4。\n")

# 特征说明
report_lines.append("\n## 2. 聚类特征说明\n")
report_lines.append("| 特征 | 含义 | 覆盖 |")
report_lines.append("|------|------|------|")
report_lines.append(f"| warehouse_stock | 交易所仓单库存 | {labels['warehouse_stock'].notna().sum()}/{len(labels)} |")
report_lines.append(f"| basis_spot_main | 现货-主力基差 | {labels['basis_spot_main'].notna().sum()}/{len(labels)} |")
report_lines.append(f"| **spread_near1_near3** | **滚动近月1-近月3跨期价差** | {labels['spread_near1_near3'].notna().sum()}/{len(labels)} |")
report_lines.append(f"| hv_20d | 20日历史波动率 | {labels['hv_20d'].notna().sum()}/{len(labels)} |")
report_lines.append(f"| oi_main | 主力持仓量 | {labels['oi_main'].notna().sum()}/{len(labels)} |")

# spread_near1_near3 定义
report_lines.append("\n### spread_near1_near3 定义\n")
report_lines.append("- **near1** = 当日排序第1的近月合约（距离到期最近的活跃可交割合约）")
report_lines.append("- **near3** = 当日排序第3的近月合约（距离到期第三近的活跃可交割合约）")
report_lines.append("- spread = near1收盘价 − near3收盘价")
report_lines.append("- **动态滚动**：合约随时间滚动切换，非固定 LC01/LC03")
report_lines.append(f"- 缺失日：{labels['spread_near1_near3'].isna().sum()} 天（活跃合约不足3个），标记 NaN，**未插值**")

# 训练/测试切分
report_lines.append(f"\n## 3. 训练/测试切分\n")
report_lines.append(f"- 训练集：{params['n_train']} 行（前 75%，严格按日期顺序）")
report_lines.append(f"- 测试集：{params['n_test']} 行（后 25%）")
report_lines.append(f"- Z-score 标准化：仅用训练集统计量拟合")

# 各状态画像
report_lines.append(f"\n## 4. 各状态特征画像\n")
for sid in sorted(labels["state_id"].unique()):
    sub = labels[labels["state_id"] == sid]
    stats = state_stats[sid]
    report_lines.append(f"### State {sid}（{len(sub)} 天，{len(sub)/len(labels)*100:.1f}%）\n")
    report_lines.append("| 特征 | 均值 | 标准差 | 中位数 | Q25 | Q75 |")
    report_lines.append("|------|------|--------|--------|-----|-----|")
    for f in FEATURES:
        s = stats[f]
        report_lines.append(f"| {f} | {s['mean']:.2f} | {s['std']:.2f} | {s['median']:.2f} | {s['q25']:.2f} | {s['q75']:.2f} |")
    report_lines.append("")

# 训练集 vs 测试集分布
report_lines.append("\n## 5. 状态分布（训练集 vs 测试集）\n")
report_lines.append("| State | 训练集 | 测试集 | 合计 |")
report_lines.append("|-------|--------|--------|------|")
n_train = params["n_train"]
for sid in range(4):
    train_cnt = (labels.iloc[:n_train]["state_id"] == sid).sum()
    test_cnt = (labels.iloc[n_train:]["state_id"] == sid).sum()
    report_lines.append(f"| {sid} | {train_cnt} ({train_cnt/n_train*100:.1f}%) | {test_cnt} ({test_cnt/params['n_test']*100:.1f}%) | {train_cnt+test_cnt} |")

# 局限性
report_lines.append("\n## 6. 局限性\n")
report_lines.append("1. **spread_near1_near3 缺失 107 天（14.1%）**：活跃合约不足 3 个时标记 NaN，未插值")
report_lines.append("2. **测试集状态分布可能失衡**：部分 State 测试样本过少，统计可信度不足")
report_lines.append("3. **K 仅在 [2,3,4] 内选**：BIC 单调下降，非全局最优断言")
report_lines.append("4. **GMM 高斯假设**：金融序列厚尾非平稳，状态边界为概率软划分")
report_lines.append("5. **仓单库存≠社会库存**：用交易所显性仓单代理，系统性低估真实库存")
report_lines.append("6. **全部结果为样本内统计观察**，不代表样本外预测有效性")

report_path = os.path.join(BASE, "state_cluster_report.md")
with open(report_path, "w", encoding="utf-8") as f:
    f.write("\n".join(report_lines))
print(f"  已保存: {report_path}")

# ---------- 5. 拼接 HAM 因子 ----------
combined = labels.merge(ham, on="date", how="left", suffixes=("", "_ham"))
combined_path = os.path.join(BASE, "ham_state_combined.csv")
combined.to_csv(combined_path, index=False)
print(f"\n  已输出: {combined_path} ({len(combined)} 行)")
print(f"  HAM 因子覆盖: n_c={combined['n_c'].notna().sum()}, Total_Demand={combined['Total_Demand'].notna().sum()}, n_f_minus_n_c={combined['n_f_minus_n_c'].notna().sum()}")

print("\nstep3 完成")
