"""
exp406c: 归档(因子注册表 + 汇总报告 + HANDOVER)
================================================
任务: 第五步归档
产出: data/exp406_factor_registry.csv, reports/exp406_factor_summary.md
     HANDOVER_round7_fund_factor.md
"""
import os, json, warnings
import numpy as np
import pandas as pd
warnings.filterwarnings("ignore")

BASE="model_ham"; EXP=os.path.join(BASE,"exp406_fund_factor")
DATA=os.path.join(EXP,"data"); REPS=os.path.join(EXP,"reports")
FEAT_CN={"f_stock_pct20":"库存(仓单)20日环比","f_wh_basis":"仓单基差(现货-期货)",
         "f_wh_basis_x_stockpct":"仓单基差×仓单增速","f_oprate_pct20":"开工率20日环比",
         "f_prod_pct20":"产量20日环比","f_cell_pct20":"动力电池20日环比"}

# ---------- 1. 因子注册表 ----------
screen=pd.read_csv(os.path.join(DATA,"exp406a_screen.csv"))
abl=pd.read_csv(os.path.join(DATA,"exp406b_ablation.csv"))
robust=pd.read_csv(os.path.join(DATA,"exp406b_robustness.csv"))
win=pd.read_csv(os.path.join(DATA,"exp406b_window_perturb.csv"))

# 稳健性标记: 因子在T+5/10/20是否稳定(均显著且同号)
def is_robust(fc):
    sub=robust[robust["factor"]==fc]
    if len(sub)<3: return False
    ics=sub["mean_ic"].values
    return all(np.abs(ic)>=0.05 for ic in ics) and len(set(np.sign(ics)))==1

registry_rows=[]
# 消融表的组别->中文名映射(用于按因子匹配消融行)
ABL_MAP={"f_stock_pct20":"基准:库存20日环比",
         "f_wh_basis_x_stockpct":"单因子:仓单基差×仓单增速",
         "f_cell_pct20":"单因子:动力电池20日环比"}
for _,r in screen.iterrows():
    fc=r["factor"]
    abl_row=abl[abl["组别"]==ABL_MAP.get(fc,"")]
    abl_acc=abl_row.iloc[0]["accuracy"] if len(abl_row)>0 else np.nan
    abl_sharpe=abl_row.iloc[0]["sharpe"] if len(abl_row)>0 else np.nan
    registry_rows.append({
        "factor":fc,"name_cn":FEAT_CN.get(fc,fc),
        "mean_ic":round(r["mean_ic"],4),"icir":round(r["icir"],3),"pval":round(r["pval"],4),
        "screen_keep":r["keep"],"screen_reason":r["reason"],
        "backtest_accuracy":round(abl_acc,4) if not pd.isna(abl_acc) else np.nan,
        "backtest_sharpe":round(abl_sharpe,2) if not pd.isna(abl_sharpe) else np.nan,
        "robust_stable":is_robust(fc),
        "final_decision":"保留(稳健但样本少35)" if (r["keep"] and fc=="f_cell_pct20") else
                        ("保留(稳健)" if (r["keep"] and is_robust(fc)) else
                        ("保留(不稳健:周期敏感)" if r["keep"] else "淘汰"))
    })
registry=pd.DataFrame(registry_rows)
registry.to_csv(os.path.join(DATA,"exp406_factor_registry.csv"),index=False)
print("因子注册表:")
print(registry[["factor","name_cn","mean_ic","screen_keep","robust_stable","final_decision"]].to_string(index=False))

# ---------- 2. 汇总报告 ----------
rep=[]
rep.append("# exp406 新一代基本面因子挖掘与标准化回测 汇总报告\n")
rep.append("> 实验编号: exp406a(数据清点+单因子筛查) / exp406b(消融+稳健性) / exp406c(归档)")
rep.append("> 框架: 滚动180训练/20预测, 因子仅T日及更早, 无前视, Bonferroni校正")
rep.append("> 统一回测: 20日持有周期, 连续时序(逐日信号×20日前向收益/20折算, 净值累乘)")
rep.append("> 统一预处理: 缩尾(0.5%/99.5%)+平稳化+Z-score")
rep.append("> **禁用HAM任何代码与变量**(已校验); 简单线性加权, 无非线性模型\n")

rep.append("## 1. 因子注册表(保留/淘汰清单)\n")
rep.append("| 因子 | 中文名 | 平均IC | ICIR | p值 | 筛查 | 稳健性 | 最终决策 |")
rep.append("|------|--------|--------|------|-----|------|--------|---------|")
for _,r in registry.iterrows():
    mark="✅" if r["screen_keep"] else "❌"
    rob="✅稳定" if r["robust_stable"] else "⚠️不稳定"
    rep.append(f"| {r['name_cn']} | {r['factor']} | {r['mean_ic']:+.4f} | {r['icir']:+.3f} | "
        f"{r['pval']:.4f} | {mark}{r['screen_keep']} | {rob} | **{r['final_decision']}** |")

rep.append("\n### 跳过因子(数据严重缺失)\n")
rep.append("| 因子 | 跳过原因 |")
rep.append("|------|---------|")
rep.append("| 厂库/总库存占比 | inventory_total缺失97%(仅22非空) |")
rep.append("| 仓单/社会库存比值 | 社会库存月度35条,无法20日周期化 |")
rep.append("| 地区库存结构 | 月度35条,无法日频 |")
rep.append("| 成本偏离因子 | 无成本数据源 |")
rep.append("| 储能/下游排产 | 无独立字段 |")

rep.append("\n## 2. 经济学假设与差异化改进\n")
rep.append("| 因子 | 经济学假设 | 是否经典 | 差异化改进 |")
rep.append("|------|-----------|---------|-----------|")
rep.append("| 库存20日环比 | 库存累积=供过于求利空 | 经典 | 基准因子(exp405已验证) |")
rep.append("| 仓单基差×仓单增速 | 仓单堆积+现货贴水=交割压力大,条件型利空 | 交叉项创新 | **差异化**:单用基差噪音大,交叉项捕捉条件信号 |")
rep.append("| 动力电池20日环比 | 下游需求边际,需求增利好 | 经典 | 仅35样本,置信低 |")
rep.append("| 仓单基差(单用) | 现货-期货价差,基差弱利空 | 经典 | 噪音大,未通过筛查 |")
rep.append("| 开工率/产量环比 | 供给增利空 | 经典 | 数据滞后+周频,信号弱淘汰 |")

rep.append("\n## 3. 消融测试(第三步)\n")
rep.append("| 组别 | 策略 | 准确率 | 年化 | 夏普 | 最大回撤 |")
rep.append("|------|------|--------|------|------|----------|")
for _,r in abl.iterrows():
    rep.append(f"| {r['组别']} | {r['策略']} | {r['accuracy']*100:.1f}% | "
        f"{r['ann_ret']*100:.1f}% | {r['sharpe']:.2f} | {r['max_dd']*100:.1f}% |")
rep.append("")
base_row=abl[abl["组别"]=="基准:库存20日环比"].iloc[0]
combo_row=abl[abl["组别"]=="叠加:新因子+基准"].iloc[0]
rep.append(f"**基准 vs 叠加增量:**")
rep.append(f"- 准确率: {base_row['accuracy']*100:.1f}% → {combo_row['accuracy']*100:.1f}% ({(combo_row['accuracy']-base_row['accuracy'])*100:+.1f}pp)")
rep.append(f"- 年化: {base_row['ann_ret']*100:.1f}% → {combo_row['ann_ret']*100:.1f}% ({(combo_row['ann_ret']-base_row['ann_ret'])*100:+.1f}pp)")
rep.append(f"- **夏普: {base_row['sharpe']:.2f} → {combo_row['sharpe']:.2f} (+{combo_row['sharpe']-base_row['sharpe']:.2f})**")
rep.append(f"- **最大回撤: {base_row['max_dd']*100:.1f}% → {combo_row['max_dd']*100:.1f}% (改善{abs(combo_row['max_dd']-base_row['max_dd'])*100:.1f}pp)**")
rep.append("")
rep.append("**结论**: 新因子叠加准确率微增(+1.2pp)、年化略降(-5.9pp)，但**夏普大幅提升(+0.74)、最大回撤大幅改善(-19.4pp)**。")
rep.append("叠加策略风险调整后收益(夏普)和回撤显著优于基准，属于**质量改进而非收益提升**。")
rep.append("但仓单基差×仓单增速交叉项在T+5/T+10不稳健(仅T+20有效)，动力电池样本少，**叠加增益主要来自风险分散而非新增预测信息**。\n")

rep.append("## 4. 稳健性扰动(第四步)\n")
rep.append("### 4.1 预测周期扰动(T+5/T+10/T+20)\n")
rep.append("| 因子 | T+5 IC | T+10 IC | T+20 IC | 稳定性 |")
rep.append("|------|--------|---------|---------|--------|")
for fc in ["f_stock_pct20","f_wh_basis_x_stockpct","f_cell_pct20"]:
    sub=robust[robust["factor"]==fc]
    ics={h:sub[sub["horizon"]==f"T+{h}"]["mean_ic"].values[0] for h in [5,10,20]}
    stable="✅稳定" if all(np.abs(v)>=0.05 and np.sign(v)==np.sign(ics[20]) for v in ics.values()) else "⚠️不稳定"
    rep.append(f"| {FEAT_CN[fc]} | {ics[5]:+.4f} | {ics[10]:+.4f} | {ics[20]:+.4f} | {stable} |")
rep.append("")
rep.append("### 4.2 训练窗口扰动(基准因子, T+20)\n")
rep.append("| 训练窗口 | 平均IC | 窗口数 |")
rep.append("|----------|--------|--------|")
for _,r in win.iterrows():
    rep.append(f"| {r['train_window']} | {r['mean_ic']:+.4f} | {r['n_win']} |")
rep.append("")
rep.append("**稳健性结论**:")
rep.append("- ✅ 库存20日环比: T+5/10/20 IC均负(-0.12/-0.16/-0.26)，方向稳定，**最稳健核心因子**")
rep.append("- ✅ 动力电池20日环比: T+5/10/20 IC均负(-0.22/-0.28/-0.29)，方向稳定，但样本仅35")
rep.append("- ⚠️ 仓单基差×仓单增速: T+5/10 IC接近0，仅T+20有效(+0.11)，**周期敏感，不稳定**")

rep.append("\n## 5. 关键发现\n")
rep.append("1. **库存20日环比是稳健核心**: 跨周期稳定(IC负)，单独准确率54.5%/夏普2.84，是最可靠的基本面因子")
rep.append("2. **仓单基差×仓单增速交叉项差异化有效但周期敏感**: T+20显著但T+5/10无信号，属于长周期条件信号")
rep.append("3. **动力电池20日环比方向稳健但样本不足**: T+5/10/20 IC均负，但仅35样本，置信低，结论仅供参考")
rep.append("4. **叠加改善夏普和回撤而非收益**: 新因子主要贡献风险分散，非新增预测信息")
rep.append("5. **供给边际因子(开工率/产量)失效**: 数据滞后+周频+信号弱，IC接近0，全部淘汰")
rep.append("6. **库存结构拆分不可行**: inventory_total/社会库存等严重缺失，跳过\n")

rep.append("## 6. 局限性声明\n")
rep.append(f"- 样本量{len(screen)}因子/约760行/17滚动窗口，统计效力有限")
rep.append("- 动力电池cell_total仅35样本，置信低")
rep.append("- 年化/夏普基于连续时序回测(20日收益折算逐日)，为近似")
rep.append("- 合成用简单线性等权，未做非线性优化(小样本避免过拟合)")
rep.append("- **口径说明**: exp406用全区间(2023-07~2026-09)+连续时序，与exp405(对齐状态标签子区间+非重叠窗口)口径不同，数字不可直接横向对比")
rep.append("- 固定多头年化-11.3%反映数据区间整体下跌市")
rep.append("- **全部为历史统计事实，不含涨跌预测**\n")

rep.append("## 7. 结论与后续\n")
rep.append("- **最终保留因子(稳健)**: 库存20日环比(基准核心)")
rep.append("- **可探索(待验证)**: 动力电池20日环比(样本充足后)、仓单基差×仓单增速(仅T+20)")
rep.append("- **淘汰**: 仓单基差(单用)、开工率环比、产量环比")
rep.append("- **跳过**: 库存结构拆分、仓单/社会库存比、地区库存、成本偏离、储能(数据缺失)")
rep.append("- 本次仅完成因子挖掘+单因子回测，未开启大规模多因子非线性建模，等待下一步指令")

with open(os.path.join(REPS,"exp406_factor_summary.md"),"w",encoding="utf-8") as f:
    f.write("\n".join(rep))

# ---------- 3. HANDOVER ----------
hand=[]
hand.append("# 第七轮实验交接文档：新一代基本面因子挖掘与标准化回测（exp406，禁用HAM）\n")
hand.append("> **交接时间**：2026-09-09")
hand.append("> **工作目录**：`/home/ubuntu/lithium-engine/model_ham/exp406_fund_factor/`")
hand.append("> **任务性质**：新一代碳酸锂基本面因子挖掘与标准化回测")
hand.append("> **上一轮交接**：`/home/ubuntu/lithium-engine/model_ham/exp405_factor_screen/HANDOVER_round6_factor_screen.md`")
hand.append("> **HAM状态**：已归档封存(`model_ham/ham_experiment_archive/`)，本轮全程禁用HAM\n")

hand.append("## 0. 给接手者的三句话速览\n")
hand.append("1. **库存20日环比是最稳健核心因子**: T+5/10/20 IC均负(-0.12/-0.16/-0.26)跨周期稳定，单独准确率54.5%/夏普2.84/回撤-28.6%。")
hand.append("2. **新因子叠加改善风险而非收益**: 叠加新因子后准确率+1.2pp、年化-5.9pp，但夏普+0.74(2.84→3.58)、最大回撤+19.4pp(-28.6%→-9.2%)——质量改进非收益提升。")
hand.append("3. **差异化交叉项有信号但周期敏感**: 仓单基差×仓单增速 T+20显著(IC+0.11)但T+5/10无信号；动力电池方向稳但仅35样本；供给边际(开工率/产量)全失效。\n")

hand.append("## 1. 环境\n")
hand.append("- `/usr/bin/python3`（pandas 3.0.3 / numpy / scipy / matplotlib）")
hand.append("- LSP报pandas/matplotlib误报忽略")

hand.append("\n## 2. 实验编号与产物\n")
hand.append("| 编号 | 脚本 | 内容 | 产物 |")
hand.append("|------|------|------|------|")
hand.append("| **exp406a** | `exp406a_screen.py` | 数据清点+单因子滚动筛查 | `data/exp406a_*.csv`, `reports/exp406a_factor_screen.md`, `exp406a_factor_timeseries.png` |")
hand.append("| **exp406b** | `exp406b_ablation.py` | 消融测试+稳健性扰动 | `data/exp406b_*.csv`, `reports/exp406b_ablation_robustness.md` |")
hand.append("| **exp406c** | `exp406c_archive.py` | 归档 | `data/exp406_factor_registry.csv`, `reports/exp406_factor_summary.md` |")

hand.append("\n### 复现命令")
hand.append("```bash")
hand.append("cd /home/ubuntu/lithium-engine")
hand.append("E=model_ham/exp406_fund_factor")
hand.append("/usr/bin/python3 $E/exp406a_screen.py     # 数据清点+筛查 (~10s)")
hand.append("/usr/bin/python3 $E/exp406b_ablation.py   # 消融+稳健性 (~10s)")
hand.append("/usr/bin/python3 $E/exp406c_archive.py    # 归档 (~1s)")
hand.append("```")

hand.append("\n## 3. 核心结果\n")
hand.append("### 3.1 因子注册表(最终决策)")
hand.append("| 因子 | IC | 筛查 | 稳健性 | 最终决策 |")
hand.append("|------|----|------|--------|---------|")
hand.append("| 库存20日环比 | -0.257 | ✅ | ✅稳定 | **保留(稳健核心)** |")
hand.append("| 仓单基差×仓单增速 | +0.107 | ✅ | ⚠️仅T+20 | 保留(周期敏感,探索) |")
hand.append("| 动力电池20日环比 | -0.289 | ✅ | ✅但样本少 | 保留(待验证) |")
hand.append("| 仓单基差(单用) | -0.051 | ❌ | — | 淘汰(p不显著) |")
hand.append("| 开工率20日环比 | +0.012 | ❌ | — | 淘汰(噪声) |")
hand.append("| 产量20日环比 | +0.008 | ❌ | — | 淘汰(噪声) |")
hand.append("")
hand.append("**跳过因子(数据缺失)**: 厂库/总库存占比(inventory_total缺失97%)、仓单/社会库存比(月度35条)、地区库存(月度)、成本偏离(无数据)、储能(无字段)\n")
hand.append("### 3.2 消融测试(连续时序, 20日持有)")
hand.append("| 组别 | 准确率 | 年化 | 夏普 | 最大回撤 |")
hand.append("|------|--------|------|------|----------|")
hand.append("| 固定多头 | 41.8% | -11.3% | -1.11 | -72.3% |")
hand.append("| 基准:库存20日环比 | 54.5% | +23.4% | 2.84 | -28.6% |")
hand.append("| 单因子:仓单基差×仓单增速 | 51.3% | +17.2% | 2.17 | -16.4% |")
hand.append("| 单因子:动力电池20日环比 | 54.8% | +11.6% | 2.12 | -26.2% |")
hand.append("| **叠加:新因子+基准** | **55.8%** | **+17.5%** | **3.58** | **-9.2%** |")
hand.append("")
hand.append("**关键**: 叠加夏普+0.74/回撤+19.4pp改善，但年化略降——质量改进非收益提升\n")

hand.append("## 4. 硬性约束验证\n")
hand.append("- ✅ **禁用HAM任何代码与变量**：已校验F表无HAM字段")
hand.append("- ✅ **滚动180训练/20预测**：与前轮一致")
hand.append("- ✅ **因子仅T日及更早**：绝对值用t日，环比用t-k..t，无前视")
hand.append("- ✅ **统一预处理**：缩尾+平稳化+Z-score")
hand.append("- ✅ **连续时序回测**：20日收益折算逐日，净值累乘（非非重叠近似）")
hand.append("- ✅ **Bonferroni校正**：阈值0.05/6=0.0083")
hand.append("- ✅ **简单线性加权**：无非线性模型")
hand.append("- ✅ **报告标注经济学假设+经典/差异化+口径风险**")
hand.append("- ✅ **GMM不作预测特征**：仅事后定性复盘")
hand.append("- ✅ **无涨跌预测**：全部历史统计事实\n")

hand.append("## 5. 关键文件路径\n")
hand.append("| 用途 | 路径 |")
hand.append("|------|------|")
hand.append("| 工作目录 | `model_ham/exp406_fund_factor/` |")
hand.append("| 汇总报告 | `exp406_factor_screen/reports/exp406_factor_summary.md` |")
hand.append("| 筛查报告 | `exp406_factor_screen/reports/exp406a_factor_screen.md` |")
hand.append("| 消融+稳健性报告 | `exp406_factor_screen/reports/exp406b_ablation_robustness.md` |")
hand.append("| 因子注册表 | `exp406_factor_screen/data/exp406_factor_registry.csv` |")
hand.append("| 消融表 | `exp406_factor_screen/data/exp406b_ablation.csv` |")
hand.append("| 稳健性表 | `exp406_factor_screen/data/exp406b_robustness.csv` |")
hand.append("| 上一轮交接 | `exp405_factor_screen/HANDOVER_round6_factor_screen.md` |")
hand.append("| HAM归档 | `ham_experiment_archive/` |")

hand.append("\n## 6. 局限性\n")
hand.append("1. 760行/17滚动窗口，统计效力有限")
hand.append("2. 动力电池仅35样本，置信低")
hand.append("3. 年化/夏普连续时序折算为近似")
hand.append("4. 与exp405口径不同(全区间+连续时序 vs 子区间+非重叠)，不可直接横向对比")
hand.append("5. 库存结构拆分因子因数据缺失全部跳过\n")

hand.append("## 7. 给后续接手者的建议\n")
hand.append("1. **库存20日环比作为稳健核心因子**：跨周期稳定，可作基础信号")
hand.append("2. **叠加改善风险非收益**：如需低回撤可叠加，但不应期待年化提升")
hand.append("3. **仓单交叉项仅长周期有效**：仅在T+20策略中考虑，短周期无效")
hand.append("4. **补数据可解锁更多因子**：库存分结构(厂库/社会)、成本、储能数据若可获得，可重新评估")
hand.append("5. **HAM已永久封存**：后续不再调用")

hand.append("\n*生成时间: 2026-09-09 | 实验: exp406a-exp406c | 禁用HAM | 无涨跌预测 | 交接对象: 监督agent*")
with open(os.path.join(EXP,"HANDOVER_round7_fund_factor.md"),"w",encoding="utf-8") as f:
    f.write("\n".join(hand))

print("\n"+"="*55); print("exp406c 完成"); print("="*55)
print(f"因子注册表: {DATA}/exp406_factor_registry.csv")
print(f"汇总报告: {REPS}/exp406_factor_summary.md")
print(f"交接文档: {EXP}/HANDOVER_round7_fund_factor.md")
