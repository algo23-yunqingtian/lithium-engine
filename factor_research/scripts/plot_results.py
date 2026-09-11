"""
因子检验结果可视化
生成因子 IC 分布对比图、信号反转对比图
"""
import json
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec

# Load results
with open("/home/ubuntu/lithium-engine/factor_research/data/factor_screen_results.json") as f:
    results = json.load(f)

# Filter valid results
valid = [r for r in results if r["n"] >= 30 and not np.isnan(r["full_ic"])]

fig = plt.figure(figsize=(14, 10))
gs = gridspec.GridSpec(3, 2, figure=fig, hspace=0.35, wspace=0.3)

# Title
fig.suptitle("碳酸锂候选因子单因子检验结果 (2026-09-11)", 
             fontsize=14, fontweight="bold", y=0.98)

# --- Plot 1: IC comparison (full/train/test) ---
ax1 = fig.add_subplot(gs[0, :])
labels = [r["factor"].replace("F", "F").split("_")[0] for r in valid]
x = np.arange(len(valid))
width = 0.25
bars_full = ax1.bar(x - width, [r["full_ic"] for r in valid], width, label="全样本IC", alpha=0.6)
bars_train = ax1.bar(x, [r["train_ic"] for r in valid], width, label="训练集IC", alpha=0.8)
bars_test = ax1.bar(x + width, [r["test_ic"] for r in valid], width, label="盲测集IC", alpha=1.0)

# Color test bars by signal flip
for r, bar in zip(valid, bars_test):
    if r.get("signal_flip", False):
        bar.set_color("red")
        bar.set_hatch("//")
    elif abs(r["test_ic"]) < 0.02:
        bar.set_color("gray")

ax1.axhline(0, color='black', linewidth=0.5)
ax1.axhline(0.05, color='green', linestyle='--', alpha=0.5, label='|IC|>0.05 显著阈值')
ax1.axhline(-0.05, color='green', linestyle='--', alpha=0.5)
ax1.set_xticks(x)
ax1.set_xticklabels([r["factor"] for r in valid], rotation=15, fontsize=9)
ax1.set_ylabel("IC (Spearman Rank)")
ax1.set_title("因子 IC 对比：全样本 vs 训练 vs 盲测（红色斜纹=信号反转）")
ax1.legend(loc="upper right", fontsize=8)
ax1.grid(axis='y', alpha=0.3)

# --- Plot 2: Coverage vs Verdict ---
ax2 = fig.add_subplot(gs[1, 0])
all_r = results
cov = [r["n"] for r in all_r]
names = [r["factor"] for r in all_r]
colors = []
for r in all_r:
    v = r.get("verdict", "")
    if "critical" in v:
        colors.append("#FF4444")
    elif "insufficient" in v:
        colors.append("#FFAA44")
    elif v == "accept":
        colors.append("#44FF44")
    else:
        colors.append("#FF8844")
    
bars = ax2.barh(range(len(all_r)), cov, color=colors, alpha=0.8)
ax2.set_yticks(range(len(all_r)))
ax2.set_yticklabels(names, fontsize=8)
ax2.set_xlabel("有效样本数")
ax2.set_title("因子覆盖率与判定")
ax2.invert_yaxis()
for i, (bar, r) in enumerate(zip(bars, all_r)):
    ax2.text(bar.get_width() + 5, i, f'{r["n"]}', va='center', fontsize=8)

# --- Plot 3: Bonferroni p-value ---
ax3 = fig.add_subplot(gs[1, 1])
bonf_labels = [r["factor"] for r in all_r if not np.isnan(r.get("bonf_p", np.nan))]
bonf_vals = [r["bonf_p"] for r in all_r if not np.isnan(r.get("bonf_p", np.nan))]
colors_p = ["#FF4444" if v > 0.05 else "#44FF44" for v in bonf_vals]
ax3.barh(range(len(bonf_labels)), bonf_vals, color=colors_p, alpha=0.8)
ax3.set_yticks(range(len(bonf_labels)))
ax3.set_yticklabels(bonf_labels, fontsize=8)
ax3.axvline(0.05, color='green', linestyle='--', alpha=0.7, label='p=0.05 显著阈值')
ax3.set_xlabel("Bonferroni 校正 p 值")
ax3.set_title("Bonferroni 校正显著性（红色=不显著）")
ax3.legend(fontsize=8)
ax3.set_xlim(0, 1.05)

# --- Plot 4: Residual IC ---
ax4 = fig.add_subplot(gs[2, 0])
res_labels = [r["factor"] for r in all_r if not np.isnan(r.get("residual_ic", np.nan))]
res_vals = [r["residual_ic"] for r in all_r if not np.isnan(r.get("residual_ic", np.nan))]
colors_res = ["#FF4444" if abs(v) < 0.03 else "#44FF44" for v in res_vals]
ax4.barh(range(len(res_labels)), res_vals, color=colors_res, alpha=0.8)
ax4.set_yticks(range(len(res_labels)))
ax4.set_yticklabels(res_labels, fontsize=8)
ax4.axvline(0, color='black', linewidth=0.5)
ax4.axvline(0.03, color='green', linestyle='--', alpha=0.5)
ax4.axvline(-0.03, color='green', linestyle='--', alpha=0.5)
ax4.set_xlabel("正交残差 IC")
ax4.set_title("正交残差 IC（|IC|<0.03=独立信息不足，绿色线=阈值）")
ax4.invert_yaxis()

# --- Plot 5: Summary table ---
ax5 = fig.add_subplot(gs[2, 1])
ax5.axis("off")
col_labels = ["因子", "训练IC", "盲测IC", "反转?", "判定"]
table_data = []
for r in all_r:
    flip = "⚠️是" if r.get("signal_flip", False) else "否"
    verdict = r.get("verdict", "N/A")
    if "critical" in verdict:
        verdict = "❌反转"
    elif "insufficient" in verdict:
        verdict = "❌不足"
    elif verdict == "accept":
        verdict = "✅通过"
    else:
        verdict = "❌拒绝"
    train_ic = f'{r["train_ic"]:.3f}' if not np.isnan(r.get("train_ic", np.nan)) else "N/A"
    test_ic = f'{r["test_ic"]:.3f}' if not np.isnan(r.get("test_ic", np.nan)) else "N/A"
    table_data.append([r["factor"].replace("_", " "), train_ic, test_ic, flip, verdict])

table = ax5.table(cellText=table_data, colLabels=col_labels, loc="center", cellLoc="center")
table.auto_set_font_size(False)
table.set_fontsize(8)
table.scale(1.2, 1.4)
# Color cells
for i, row in enumerate(table_data):
    if "❌反转" in row[4]:
        for j in range(5):
            table[i+1, j].set_facecolor("#FFCCCC")
    elif "❌" in row[4]:
        for j in range(5):
            table[i+1, j].set_facecolor("#FFDDCC")
    elif "✅" in row[4]:
        for j in range(5):
            table[i+1, j].set_facecolor("#CCFFCC")

ax5.set_title("检验结果汇总", fontsize=10, fontweight="bold")

# --- Footer ---
fig.text(0.5, 0.02, 
         "HAM+因子项目双轮验证：信号反转是碳酸锂品种结构性特征 | 763交易日 | 6因子全拒绝 | F5/F6保留为风控观察",
         ha="center", fontsize=8, style="italic", color="gray")

plt.savefig("/home/ubuntu/lithium-engine/factor_research/data/factor_ic_summary.png",
            dpi=150, bbox_inches="tight", facecolor="white")
plt.close()
print("图表已保存: factor_research/data/factor_ic_summary.png")
