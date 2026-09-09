"""
exp405c: 归档汇总(因子保留/淘汰清单 + 汇总表 + HANDOVER)
=========================================================
任务: 任务3归档
产出: data/exp405_factor_registry.csv(因子保留/淘汰清单)
     reports/exp405_factor_summary.md(汇总对比表)
     HANDOVER_round6_factor_screen.md
"""
import os, json, warnings
import numpy as np
import pandas as pd
warnings.filterwarnings("ignore")

BASE = "model_ham"
EXP = os.path.join(BASE, "exp405_factor_screen")
DATA = os.path.join(EXP, "data")
REPS = os.path.join(EXP, "reports")

FEAT_CN = {
 "f_stock_abs":"库存绝对值","f_stock_pct_5":"库存环比5日","f_stock_pct_20":"库存环比20日",
 "f_basis_abs":"基差","f_basis_pct_5":"基差环比5日","f_basis_pct_20":"基差环比20日",
 "f_spread_1_3":"近月1-3价差","f_spread_1_3_pct_5":"近月1-3价差环比5日",
 "f_volume_pct_5":"成交量环比5日","f_oi_pct_5":"总持仓环比5日","f_oi_pct_20":"总持仓环比20日",
 "f_hv20":"波动率","f_hv20_pct_5":"波动率环比5日"}

# ---------- 1. 因子保留/淘汰清单 ----------
screen = pd.read_csv(os.path.join(DATA,"exp405a_factor_screen.csv"))
registry = pd.DataFrame({
    "factor": screen["factor"],
    "name_cn": screen["factor"].map(FEAT_CN),
    "mean_ic": screen["mean_ic"].round(4),
    "icir": screen["icir"].round(3),
    "pval": screen["pval"].round(5),
    "decision": np.where(screen["keep"]==True, "保留", "淘汰(噪声)"),
})
# 标注不可用因子(数据缺失)
unavailable = ["near1-near2跨期","near2-near3跨期","远期曲线曲率","大户净持仓","多空比"]
un_rows = [{"factor":f"NA:{u}","name_cn":u,"mean_ic":np.nan,"icir":np.nan,
            "pval":np.nan,"decision":"不可用(数据缺失)"} for u in unavailable]
registry = pd.concat([registry, pd.DataFrame(un_rows)], ignore_index=True)
registry.to_csv(os.path.join(DATA,"exp405_factor_registry.csv"), index=False)

kept = registry[registry["decision"]=="保留"]
elim = registry[registry["decision"]=="淘汰(噪声)"]
na = registry[registry["decision"].str.startswith("不可用")]
print(f"因子清单: 候选13(可用) + 5(不可用) | 保留{len(kept)} | 淘汰{len(elim)} | 不可用{len(na)}")

# ---------- 2. 对照表 ----------
comp = pd.read_csv(os.path.join(DATA,"exp405b_comparison.csv"))
print("\n对照组:")
print(comp.to_string(index=False))

# ---------- 3. 汇总报告 ----------
rep=[]
rep.append("# exp405 因子筛选与分状态条件检验 汇总报告\n")
rep.append("> 实验编号: exp405a(单因子筛查) / exp405b(分状态条件检验) / exp405c(归档)")
rep.append("> 框架: 沿用exp404a K=4滚动GMM状态标签, 停用HAM全部因子")
rep.append("> 滚动窗口: 训练180/预测20/滚动20, 前向收益窗口20日, Bonferroni校正")
rep.append("> 时序校验: 全部因子T日及之前数据生成, 无前视泄露\n")

rep.append("## 1. 因子保留/淘汰清单\n")
rep.append("| 因子 | 中文名 | 平均IC | ICIR | p值 | 决策 |")
rep.append("|------|--------|--------|------|-----|------|")
for _,r in registry.iterrows():
    ic = f"{r['mean_ic']:+.4f}" if not pd.isna(r['mean_ic']) else "NA"
    ir = f"{r['icir']:+.3f}" if not pd.isna(r['icir']) else "NA"
    pv = f"{r['pval']:.4f}" if not pd.isna(r['pval']) else "NA"
    mark = {"保留":"✅保留","淘汰(噪声)":"❌淘汰"}.get(r["decision"], "⚠️"+r["decision"])
    rep.append(f"| {r['name_cn']} | {r['factor']} | {ic} | {ir} | {pv} | {mark} |")
rep.append(f"\n**汇总: 候选13(可用), 保留{len(kept)}, 淘汰{len(elim)}, 不可用{len(na)}**\n")
rep.append("**保留因子:**")
for _,r in kept.iterrows():
    rep.append(f"- {r['name_cn']}({r['factor']}): IC={r['mean_ic']:+.4f}, p={r['pval']:.2e}")
rep.append("\n**不可用因子(数据缺失):**")
for _,r in na.iterrows():
    rep.append(f"- {r['name_cn']}")

rep.append("\n## 2. 分状态条件 vs 全局 效果对比(样本外, 去S2)\n")
rep.append("| 组别 | 有效样本 | 涨跌准确率 | 年化收益 | 最大回撤 | 胜率 |")
rep.append("|------|----------|------------|----------|----------|------|")
for _,r in comp.iterrows():
    acc=f"{r['涨跌准确率']*100:.1f}%" if not pd.isna(r['涨跌准确率']) else "NA"
    ann=f"{r['年化收益']*100:.1f}%" if not pd.isna(r['年化收益']) else "NA"
    mdd=f"{r['最大回撤']*100:.1f}%" if not pd.isna(r['最大回撤']) else "NA"
    wr=f"{r['胜率']*100:.1f}%" if not pd.isna(r['胜率']) else "NA"
    rep.append(f"| {r['组别']} | {int(r['有效样本'])} | {acc} | {ann} | {mdd} | {wr} |")

# 计算分状态vs全局(方向校准后)增益
rowAdir = comp[comp["组别"].str.startswith("对照A'")].iloc[0]
rowB = comp[comp["组别"].str.startswith("对照B")].iloc[0]
diff_acc = rowB["涨跌准确率"] - rowAdir["涨跌准确率"]
diff_ann = rowB["年化收益"] - rowAdir["年化收益"]
rep.append(f"\n**全局(方向校准后) vs 分状态条件:**")
rep.append(f"- 准确率差: {diff_acc*100:+.1f}pp")
rep.append(f"- 年化差: {diff_ann*100:+.1f}pp")
if diff_acc >= 0.03:
    rep.append("- **结论: 分状态条件相对全局有显著增益, 不同状态启用对应因子有效**")
elif diff_acc <= -0.03:
    rep.append("- **结论: 分状态条件相对全局(方向校准后)无增益甚至略降, 库存类因子跨状态方向稳定, 全局方向校准已捕捉主要信息**")
else:
    rep.append("- **结论: 分状态条件相对全局增益有限, 库存类因子跨状态较为稳定**")

rep.append("\n## 3. S2状态处理声明\n")
rep.append("- **S2样本量仅26天, 统计置信不足, 结论不采信, 不用于策略推断**")
rep.append("- S2数据保留在 data/exp405b_s2_record.csv 备查, 后续所有分析排除S2")

rep.append("\n## 4. 关键发现\n")
rep.append("1. **库存类因子是核心有效因子**: 库存绝对值/环比5日/环比20日IC均为负(-0.29~-0.40), 含义=库存高→未来20日收益低, 是反向指示器")
rep.append("2. **成交量环比5日**IC为正(+0.079), 是唯一非库存类保留因子")
rep.append("3. **基差/期限结构因子未通过Bonferroni**: 基差、近月价差IC虽为正但显著性不足(p>0.004)")
rep.append("4. **分状态条件增益有限**: 库存环比20日在S0/S1/S3方向稳定(均负), 全局方向校准已捕捉主要信息")
rep.append("5. **对照B准确率69% > 基准58%**: 状态条件模式相对固定多头有约11pp准确率增益\n")

rep.append("## 5. 局限性\n")
rep.append("- 样本外仅340行/17滚动窗口, 统计效力有限")
rep.append("- State 0子样本每窗口<5天, 回退全局因子, 非S0内部真实IC")
rep.append("- 年化收益基于非重叠20日窗口折算, 为近似值")
rep.append("- **全部为历史统计事实, 不含涨跌预测**")

rep.append("\n## 6. 长期待办(本次不执行)\n")
rep.append("- 可扩展原始数据长度增加样本, 提升S2状态统计效力, 本次暂不做数据拉长")

with open(os.path.join(REPS,"exp405_factor_summary.md"),"w",encoding="utf-8") as f:
    f.write("\n".join(rep))

# ---------- 4. HANDOVER ----------
hand=[]
hand.append("# 第六轮实验交接文档：基本面+期限结构因子筛选与分状态条件检验（exp405，停用HAM）\n")
hand.append("> **交接时间**：2026-09-09")
hand.append("> **工作目录**：`/home/ubuntu/lithium-engine/model_ham/exp405_factor_screen/`")
hand.append("> **任务性质**：基本面+期限结构因子单因子滚动筛查 + 分市场状态条件因子检验")
hand.append("> **上一轮交接**：`/home/ubuntu/lithium-engine/model_ham/exp404_cluster_analysis/HANDOVER_round5_cluster_analysis.md`")
hand.append("> **接收方**：监督 agent / 后续接手者\n")

hand.append("## 0. 给接手者的三句话速览\n")
hand.append("1. **核心有效因子=库存类**：库存绝对值/环比5日/环比20日IC均显著为负(-0.29~-0.40)，库存高→未来20日收益低，是反向指示器；成交量环比5日IC正(+0.079)亦保留。")
hand.append("2. **Bonferroni校正后保留4/13**：库存绝对值、库存环比5日、库存环比20日、成交量环比5日；基差/期限结构因子显著性不足全部淘汰。")
hand.append("3. **分状态条件增益有限**：全局因子+方向校准(对照A')准确率74%/年化107%，反而略优于状态条件模式(对照B, 69%/100%)——库存因子跨状态方向稳定，全局方向校准已捕捉主要信息。固定多头基准58%/42%。\n")

hand.append("## 1. 环境（与前几轮一致）\n")
hand.append("- **正确解释器**：`/usr/bin/python3`（pandas 3.0.3 / numpy 2.5.0 / sklearn 1.9.0）")
hand.append("- **⚠️ 坑**：`.venv_ham/` 是废 venv，别用；LSP 报 pandas 误报忽略")
hand.append("- 自检：`/usr/bin/python3 -c \"import pandas,numpy,sklearn,scipy; print('OK')\"`\n")

hand.append("## 2. 实验编号与产物清单\n")
hand.append("| 编号 | 脚本 | 内容 | 产物 |")
hand.append("|------|------|------|------|")
hand.append("| **exp405a** | `exp405a_factor_screen.py` | 候选因子池+时序校验+滚动RankIC+五分位分层 | `data/exp405a_rankic_rolling.csv`, `data/exp405a_factor_screen.csv`, `data/exp405a_quintile.csv`, `reports/exp405a_factor_screen.md` |")
hand.append("| **exp405b** | `exp405b_state_factor.py` | 分状态条件检验+对照A/A'/B | `data/exp405b_state_ic.csv`, `data/exp405b_comparison.csv`, `data/exp405b_s2_record.csv`, `reports/exp405b_state_factor_review.md` |")
hand.append("| **exp405c** | `exp405c_archive.py` | 因子清单+汇总表 | `data/exp405_factor_registry.csv`, `reports/exp405_factor_summary.md` |\n")
hand.append("### 复现命令")
hand.append("```bash")
hand.append("cd /home/ubuntu/lithium-engine")
hand.append("E=model_ham/exp405_factor_screen")
hand.append("/usr/bin/python3 $E/exp405a_factor_screen.py   # 单因子滚动RankIC筛查 (~5s)")
hand.append("/usr/bin/python3 $E/exp405b_state_factor.py       # 分状态条件检验 (~20s)")
hand.append("/usr/bin/python3 $E/exp405c_archive.py            # 归档汇总 (~2s)")
hand.append("```")
hand.append("`random_state=42` 固定，完全可复现。\n")

hand.append("## 3. 核心结果\n")
hand.append("### 3.1 候选因子池（13可用+5不可用）")
hand.append("- **可用13**：库存绝对值/环比5日/20日、基差/环比5日/20日、近月1-3价差/环比5日、成交量环比5日、总持仓环比5日/20日、波动率/环比5日")
hand.append("- **不可用5**：near1-near2、near2-near3跨期分腿、远期曲线曲率（仅有合并价差spread_near1_near3）、大户净持仓、多空比（数据集无字段）\n")
hand.append("### 3.2 保留因子（Bonferroni阈值0.0038）")
hand.append("| 因子 | 中文名 | 平均IC | ICIR | p值 |")
hand.append("|------|--------|--------|------|-----|")
hand.append("| f_stock_abs | 库存绝对值 | -0.285 | -1.056 | 4.9e-4 |")
hand.append("| f_stock_pct_5 | 库存环比5日 | -0.356 | -1.880 | 8.3e-7 |")
hand.append("| f_stock_pct_20 | 库存环比20日 | -0.402 | -1.520 | 1.1e-5 |")
hand.append("| f_volume_pct_5 | 成交量环比5日 | +0.079 | +1.218 | 1.3e-4 |\n")
hand.append("### 3.3 分状态条件 vs 全局 对照（样本外，去S2）")
hand.append("| 组别 | 准确率 | 年化 | 最大回撤 |")
hand.append("|------|--------|------|----------|")
hand.append("| 基准(固定多头) | 58.0% | +42.5% | -24.8% |")
hand.append("| 对照A(全局,无方向校准) | 28.0% | -45.1% | -34.8% |")
hand.append("| **对照A'(全局+方向校准)** | **74.2%** | **+106.7%** | -21.2% |")
hand.append("| 对照B(状态条件+方向校准) | 69.0% | +100.5% | -22.8% |")
hand.append("")
hand.append("**关键发现**：对照A'(全局+方向校准)略优于对照B(状态条件)——分状态条件增益有限，库存因子跨状态方向稳定。\n")

hand.append("## 4. 硬性约束验证（本轮全程遵守）\n")
hand.append("- ✅ **停用HAM预测**：不引入 n_f/n_c/Total_Demand/profit/D_f/D_c 任何字段")
hand.append("- ✅ **K=4滚动GMM框架不变**：状态标签沿用exp404a样本外标签，不重训")
hand.append("- ✅ **S2仅记录不采信**：26天样本，所有分状态分析排除S2，仅保留exp405b_s2_record.csv备查")
hand.append("- ✅ **全部因子T日及之前生成**：绝对值用t日，环比用t-k..t，无未来信息")
hand.append("- ✅ **滚动窗口180训练/20预测**：与前轮一致，输出样本外指标")
hand.append("- ✅ **Bonferroni校正**：阈值0.05/13=0.0038")
hand.append("- ✅ **不含涨跌预测**：所有结果为历史统计事实\n")

hand.append("## 5. 关键文件路径速查\n")
hand.append("| 用途 | 路径 |")
hand.append("|------|------|")
hand.append("| 本轮工作目录 | `model_ham/exp405_factor_screen/` |")
hand.append("| 因子筛查报告 | `exp405_factor_screen/reports/exp405a_factor_screen.md` |")
hand.append("| 分状态检验报告 | `exp405_factor_screen/reports/exp405b_state_factor_review.md` |")
hand.append("| 汇总报告 | `exp405_factor_screen/reports/exp405_factor_summary.md` |")
hand.append("| 因子保留/淘汰清单 | `exp405_factor_screen/data/exp405_factor_registry.csv` |")
hand.append("| 对照表 | `exp405_factor_screen/data/exp405b_comparison.csv` |")
hand.append("| S2记录(不采信) | `exp405_factor_screen/data/exp405b_s2_record.csv` |")
hand.append("| 上一轮交接 | `exp404_cluster_analysis/HANDOVER_round5_cluster_analysis.md` |\n")

hand.append("## 6. 局限性与诚实声明\n")
hand.append("1. **340行样本外/17窗口**：统计效力有限，年化收益为近似折算")
hand.append("2. **State 0子样本不足**：每窗口<5天，回退全局因子，非S0内部真实IC")
hand.append("3. **对照A'(全局方向校准)略优于对照B(状态条件)**：结论是分状态条件增益有限，非失败——库存因子跨状态方向稳定是合理现象")
hand.append("4. **不可用因子5个**：near1-near2/near2-near3/曲率/大户净持仓/多空比因数据缺失未构建，非淘汰")
hand.append("5. **不含预测**：所有转移频率/准确率为历史统计事实，不作未来判断\n")

hand.append("## 7. 给后续接手者的建议\n")
hand.append("1. **库存类因子是稳健核心**：方向负(库存高→收益低)，跨状态稳定，可作基础信号")
hand.append("2. **分状态条件非失败**：增益有限是因因子跨状态稳定，换用状态敏感因子(如波动率、基差)可能状态条件更有效")
hand.append("3. **HAM定量预测已关闭**：见第五轮交接，本轮仅基本面+期限结构定性")
hand.append("4. **长期待办**：扩展原始数据长度提升S2统计效力（本次不做）\n")

hand.append("*生成时间: 2026-09-09 | 实验: exp405a-exp405c | 停用HAM | S2不采信 | 无涨跌预测 | 交接对象: 监督agent*")

with open(os.path.join(EXP,"HANDOVER_round6_factor_screen.md"),"w",encoding="utf-8") as f:
    f.write("\n".join(hand))

print("\n" + "="*55)
print("exp405c 完成")
print("="*55)
print(f"因子清单: {DATA}/exp405_factor_registry.csv")
print(f"汇总报告: {REPS}/exp405_factor_summary.md")
print(f"交接文档: {EXP}/HANDOVER_round6_factor_screen.md")
