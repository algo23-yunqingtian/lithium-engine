"""
exp403: 汇总归档 — 三版本总览对比表 + 方法论专章 + DataHub元数据
================================================================
整合: 旧单次切分(第二轮) / 旧滚动(第三轮exp306) / 修复后滚动(本轮exp402)
产出:
  reports/exp403_overview_table.md  — 三版本总览对比表
  reports/exp403_methodology_lesson.md — 方法论专章(幸运切点)
  data/exp403_datahub_metadata.json — DataHub元数据
硬性约束: 本报告只做汇总归档, 不引入新拟合
"""
import os, json
from datetime import datetime

BASE = "model_ham"
EXP = os.path.join(BASE, "exp4_lookahead_fix")
DATA = os.path.join(EXP, "data")
REPS = os.path.join(EXP, "reports")

# ---------- 1. 三版本核心指标 (来自 exp302/exp306/exp401/exp402 实测) ----------
versions = {
    "旧单次切分(第二轮)": {
        "口径": "75/25单次切分, 134行样本外",
        "HAM因子": "含1日前视",
        "B组HAM准确率": "54.5%",
        "训练集准确率": "未报告(掩盖83%+过拟合)",
        "多空vs多头": "未对比",
        "RankIC显著项": "2项(样本内假象)",
        "年化收益": "未测",
    },
    "旧滚动(第三轮exp306)": {
        "口径": "17窗口/340行滚动, 180训练/20预测",
        "HAM因子": "含1日前视(未修)",
        "B组HAM准确率": "50.0% (54.5%消失)",
        "训练集准确率": "83%(测试45.5%)",
        "多空vs多头": "滚动-2.3pp(不优)",
        "RankIC显著项": "Bonferroni后0项",
        "年化收益": "多头基准年化18.75%",
    },
    "修复后滚动(exp402)": {
        "口径": "滚动窗口, 180训练/20预测, 纯净因子",
        "HAM因子": "纯净版(已修前视)",
        "对照1三因子准确率": "52.1%",
        "对照2两因子准确率": "52.1%",
        "多空vs多头": "多空年化77.1% > 多头60.4%",
        "RankIC显著项": "0项",
        "年化收益": "多空77.1%/回撤-24.88%",
    },
}

# ---------- 2. 总览对比表 ----------
rep = []
rep.append("# exp403 三版本实验总览对比表\n")
rep.append(f"> 修复HAM前视后全套重跑 + 三版本结论汇总")
rep.append(f"> 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
rep.append(f"> 实验编号: exp401(重生成因子) / exp402(滚动回测+对照) / exp403(汇总归档)\n")

rep.append("## 1. 核心指标对比总览\n")
rep.append("| 维度 | 旧单次切分(第二轮) | 旧滚动(exp306) | 修复后滚动(exp402) | 稳健性 |")
rep.append("|------|-------------------|---------------|-------------------|--------|")
rep.append("| 样本外准确率 | 54.5% | 50.0% | 52.1%(三/两因子均) | ⚠️ 均在50%±2.5pp随机区间 |")
rep.append("| 训练集准确率 | 掩盖 | 83% | (滚动内fit,无暴露) | — |")
rep.append("| HAM因子前视 | 含1日(未检) | 含1日(未修) | **已修复** | ✅ 本轮已消除 |")
rep.append("| n_f_minus_n_c作用 | 预测力来源 | 过拟合翻转 | **常数噪声,剔除无影响** | ❌ 该因子无效 |")
rep.append("| 多空vs固定多头 | 未对比 | -2.3pp(不优) | +16.7pp(多空更优) | ⚠️ 口径差异,勿直接比 |")
rep.append("| 整体RankIC显著项 | 2(样本内) | 0(Bonferroni) | 0 | ✅ 稳定不显著 |")
rep.append("| 分状态RankIC | 0 | 0 | 0 | ✅ 稳定不显著 |")
rep.append("| 聚类K | 4 | 4 | 4(滚动内fit) | ✅ 稳定 |")
rep.append("| 年化收益 | 未测 | 多头18.75% | 多空77.1%/多头60.4% | ⚠️ 单边市产物 |")

rep.append("\n## 2. 修复前后关键变化\n")
rep.append("| 指标 | 修复前(旧滚动) | 修复后(exp402) | 变化 |")
rep.append("|------|--------------|----------------|------|")
rep.append("| n_c 均值/标准差 | 0.40/0.47 | 0.997/0.037 | ⚠️ 塌陷为常数 |")
rep.append("| n_f-n_c 均值 | 0.19 | -0.99 | ⚠️ 全投机主导 |")
rep.append("| 样本外准确率 | 50.0% | 52.1% | 持平(随机区间) |")
rep.append("| 剔除n_f-n_c影响 | — | 0pp(无影响) | ✅ 确认该因子为噪声 |")
rep.append("| 多空超额(年化) | -2.3pp | +16.7pp | ⚠️ 滚动窗口/区间差异 |")

rep.append("\n## 3. 稳健结论 vs 偶然噪声\n")
rep.append("### 稳健结论(可信赖)\n")
rep.append("1. **GMM聚类K=4稳健**: 三轮均BIC选K=4, 状态持续性85%")
rep.append("2. **条件RankIC普遍不显著**: Bonferroni后三轮均0项显著")
rep.append("3. **n_f_minus_n_c无预测力**: 修复前视后塌陷为常数, 剔除无影响")
rep.append("4. **样本外准确率均在50%附近**: HAM因子无稳定方向预测力")
rep.append("5. **聚类定性价值**: 市场状态标签(涨/跌/震荡/极端)对人工研判有参考价值\n")
rep.append("### 偶然噪声(不可信赖)\n")
rep.append("1. **B组54.5%**: 单次幸运切点, 滚动下消失")
rep.append("2. **旧版n_c变化**: 几乎全靠1日前视撑起, 非真实预测力")
rep.append("3. **上一轮'2项RankIC显著'**: 样本内假象")
rep.append("4. **多空优于多头(部分)**: 依赖具体滚动区间, 非稳健超额\n")

rep.append("## 4. 定量预测能力边界声明\n")
rep.append("- **市场状态聚类的定性价值**: GMM能稳定划分4种市场状态(持续性85%), 对人工研判市场阶段有参考价值——这是**定性**贡献")
rep.append("- **HAM因子定量预测能力的边界**: 修复前视后, n_c/n_f-n_c塌陷为常数, 滚动样本外准确率≈50%, RankIC不显著——**定量预测能力不成立**")
rep.append("- **结论**: 聚类可做定性辅助, HAM因子不应作为定量预测信号使用")
with open(os.path.join(REPS,"exp403_overview_table.md"),"w",encoding="utf-8") as f:
    f.write("\n".join(rep))

# ---------- 3. 方法论专章 ----------
lesson = []
lesson.append("# exp403 方法论专章: 单次静态切分的幸运切点问题\n")
lesson.append(f"> 本项目方法论经验留存 | {datetime.now().strftime('%Y-%m-%d')}")
lesson.append("> 触发事件: HAM因子前视bug修复 + 三版本对照暴露系统性偏差\n")
lesson.append("## 1. 幸运切点问题(Lucky Cut Problem)\n")
lesson.append("**现象**: 第二轮用75/25单次切分, B组HAM因子样本外准确率54.5%(看似优秀)。")
lesson.append("第三轮改用严格滚动窗口后, 同一因子准确率降至50.0%——54.5%完全消失。")
lesson.append("**根因**: 单次切分只在一个固定切点上评估。当数据存在偶然性结构时,")
lesson.append("75%训练/25%测试的切点可能恰好落在模型有利的区间(如恰好选到趋势明显的测试段)。")
lesson.append("**本质**: 单次切分的'样本外'其实是'另一个训练集', 不是真正的外推检验。\n")
lesson.append("## 2. 前视bug的隐蔽性\n")
lesson.append("**现象**: ham_model.py 的 docstring 写着'严格使用t之前数据, 不含未来信息',")
lesson.append("但代码 `price_change[t-W+1:t+1]` 实际含 `price_change[t]=P(t+1)-P(t)`——注释与代码矛盾。")
lesson.append("**修复后**: 纯净版 n_c 从 mean=0.40/std=0.47 塌陷为 mean=0.997/std=0.037(近常数)。")
lesson.append("**关键洞察**: 前视不仅是0.7pp的微小偏差, 而是旧因子n_c变化性的**唯一来源**。")
lesson.append("原版n_c的'预测力'本质是1日未来价格的回声, 而非真实的投机/产业分歧信号。\n")
lesson.append("## 3. 三条方法论铁律(本项目留存)\n")
lesson.append("1. **禁止单次静态切分做样本外结论**: 必须用滚动窗口/时序walk-forward验证外推能力")
lesson.append("2. **注释与代码必须一致核查**: 凡声称'无未来信息'的代码, 逐行核对窗口索引边界")
lesson.append("3. **修复后重生成, 不补丁绕过**: 因子有bug时, 必须从源头重算全量因子, 不可只调参掩盖\n")
lesson.append("## 4. 对后续研究的建议\n")
lesson.append("- HAM因子在当前参数(alpha=0.3,beta=0.5,gamma=2,W=20)下**无定量预测价值**")
lesson.append("- 市场状态聚类保留**定性价值**, 作为人工研判辅助, 不作自动交易信号")
lesson.append("- 如需HAM因子有预测力, 需重设参数空间或换用不同信息集(当前框架下已验证无效)")
with open(os.path.join(REPS,"exp403_methodology_lesson.md"),"w",encoding="utf-8") as f:
    f.write("\n".join(lesson))

# ---------- 4. DataHub元数据 ----------
meta = {
    "project": "lithium_engine_ham",
    "experiment_round": "round4_lookahead_fix",
    "exp_ids": ["exp401","exp402","exp403"],
    "generated_at": datetime.now().isoformat(),
    "fix_summary": "修复ham_model.py:48的1日前视(price_change窗口[t-W+1:t+1]→[t-W:t]), 重生成纯净HAM因子并重跑全套滚动回测",
    "ham_params": {"alpha":0.3,"beta":0.5,"gamma":2,"W":20},
    "rolling_config": {"train_window":180,"forecast_step":20,"roll_step":20,"random_state":42},
    "control_groups": {
        "g1_three_factors": ["n_c","n_f_minus_n_c","Total_Demand"],
        "g2_two_factors": ["n_c","Total_Demand"],
    },
    "key_results": {
        "n_c_pure": {"mean":0.997,"std":0.037,"note":"塌陷为近常数"},
        "n_f_minus_n_c_pure": {"mean":-0.99,"note":"716/720天全投机主导"},
        "g1_accuracy_out_of_sample": 0.521,
        "g2_accuracy_out_of_sample": 0.521,
        "g1_ann_return": 0.771,
        "g1_max_drawdown": -0.2488,
        "g1_rank_ic_significant": 0,
        "long_only_ann_return": 0.604,
    },
    "version_comparison": {
        "old_single_split_B_accuracy": 0.545,
        "old_rolling_B_accuracy": 0.500,
        "fixed_rolling_g1_accuracy": 0.521,
    },
    "robust_conclusions": [
        "GMM聚类K=4稳健(BIC/轮廓一致, 状态持续性85%)",
        "条件RankIC普遍不显著(Bonferroni后0项)",
        "n_f_minus_n_c无预测力(修复后塌陷为常数)",
        "样本外准确率均在50%附近(HAM无稳定预测力)",
    ],
    "accidental_noise": [
        "B组54.5%是单次幸运切点",
        "旧版n_c变化几乎全靠1日前视撑起",
        "上一轮2项RankIC显著是样本内假象",
    ],
    "lookahead_violation": "未发现新的时序泄露; 修复点已消除1日前视",
    "artifact_paths": {
        "factors_pure": "exp4_lookahead_fix/data/exp401_ham_factors_pure.csv",
        "g1_predictions": "exp4_lookahead_fix/data/exp402_g1_predictions.csv",
        "g2_predictions": "exp4_lookahead_fix/data/exp402_g2_predictions.csv",
        "overview_table": "exp4_lookahead_fix/reports/exp403_overview_table.md",
        "methodology_lesson": "exp4_lookahead_fix/reports/exp403_methodology_lesson.md",
        "rolling_report": "exp4_lookahead_fix/reports/exp402_rolling_backtest_pure.md",
    },
}
with open(os.path.join(DATA,"exp403_datahub_metadata.json"),"w",encoding="utf-8") as f:
    json.dump(meta, f, indent=2, ensure_ascii=False)

print("="*60)
print("exp403 汇总归档完成")
print("="*60)
print(f"已保存: {REPS}/exp403_overview_table.md")
print(f"已保存: {REPS}/exp403_methodology_lesson.md")
print(f"已保存: {DATA}/exp403_datahub_metadata.json")
