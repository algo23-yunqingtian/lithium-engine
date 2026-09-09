# 第六轮实验交接文档：基本面+期限结构因子筛选与分状态条件检验（exp405，停用HAM）

> **交接时间**：2026-09-09
> **工作目录**：`/home/ubuntu/lithium-engine/model_ham/exp405_factor_screen/`
> **任务性质**：基本面+期限结构因子单因子滚动筛查 + 分市场状态条件因子检验
> **上一轮交接**：`/home/ubuntu/lithium-engine/model_ham/exp404_cluster_analysis/HANDOVER_round5_cluster_analysis.md`
> **接收方**：监督 agent / 后续接手者

## 0. 给接手者的三句话速览

1. **核心有效因子=库存类**：库存绝对值/环比5日/环比20日IC均显著为负(-0.29~-0.40)，库存高→未来20日收益低，是反向指示器；成交量环比5日IC正(+0.079)亦保留。
2. **Bonferroni校正后保留4/13**：库存绝对值、库存环比5日、库存环比20日、成交量环比5日；基差/期限结构因子显著性不足全部淘汰。
3. **分状态条件增益有限**：全局因子+方向校准(对照A')准确率74%/年化107%，反而略优于状态条件模式(对照B, 69%/100%)——库存因子跨状态方向稳定，全局方向校准已捕捉主要信息。固定多头基准58%/42%。

## 1. 环境（与前几轮一致）

- **正确解释器**：`/usr/bin/python3`（pandas 3.0.3 / numpy 2.5.0 / sklearn 1.9.0）
- **⚠️ 坑**：`.venv_ham/` 是废 venv，别用；LSP 报 pandas 误报忽略
- 自检：`/usr/bin/python3 -c "import pandas,numpy,sklearn,scipy; print('OK')"`

## 2. 实验编号与产物清单

| 编号 | 脚本 | 内容 | 产物 |
|------|------|------|------|
| **exp405a** | `exp405a_factor_screen.py` | 候选因子池+时序校验+滚动RankIC+五分位分层 | `data/exp405a_rankic_rolling.csv`, `data/exp405a_factor_screen.csv`, `data/exp405a_quintile.csv`, `reports/exp405a_factor_screen.md` |
| **exp405b** | `exp405b_state_factor.py` | 分状态条件检验+对照A/A'/B | `data/exp405b_state_ic.csv`, `data/exp405b_comparison.csv`, `data/exp405b_s2_record.csv`, `reports/exp405b_state_factor_review.md` |
| **exp405c** | `exp405c_archive.py` | 因子清单+汇总表 | `data/exp405_factor_registry.csv`, `reports/exp405_factor_summary.md` |

### 复现命令
```bash
cd /home/ubuntu/lithium-engine
E=model_ham/exp405_factor_screen
/usr/bin/python3 $E/exp405a_factor_screen.py   # 单因子滚动RankIC筛查 (~5s)
/usr/bin/python3 $E/exp405b_state_factor.py       # 分状态条件检验 (~20s)
/usr/bin/python3 $E/exp405c_archive.py            # 归档汇总 (~2s)
```
`random_state=42` 固定，完全可复现。

## 3. 核心结果

### 3.1 候选因子池（13可用+5不可用）
- **可用13**：库存绝对值/环比5日/20日、基差/环比5日/20日、近月1-3价差/环比5日、成交量环比5日、总持仓环比5日/20日、波动率/环比5日
- **不可用5**：near1-near2、near2-near3跨期分腿、远期曲线曲率（仅有合并价差spread_near1_near3）、大户净持仓、多空比（数据集无字段）

### 3.2 保留因子（Bonferroni阈值0.0038）
| 因子 | 中文名 | 平均IC | ICIR | p值 |
|------|--------|--------|------|-----|
| f_stock_abs | 库存绝对值 | -0.285 | -1.056 | 4.9e-4 |
| f_stock_pct_5 | 库存环比5日 | -0.356 | -1.880 | 8.3e-7 |
| f_stock_pct_20 | 库存环比20日 | -0.402 | -1.520 | 1.1e-5 |
| f_volume_pct_5 | 成交量环比5日 | +0.079 | +1.218 | 1.3e-4 |

### 3.3 分状态条件 vs 全局 对照（样本外，去S2）
| 组别 | 准确率 | 年化 | 最大回撤 |
|------|--------|------|----------|
| 基准(固定多头) | 58.0% | +42.5% | -24.8% |
| 对照A(全局,无方向校准) | 28.0% | -45.1% | -34.8% |
| **对照A'(全局+方向校准)** | **74.2%** | **+106.7%** | -21.2% |
| 对照B(状态条件+方向校准) | 69.0% | +100.5% | -22.8% |

**关键发现**：对照A'(全局+方向校准)略优于对照B(状态条件)——分状态条件增益有限，库存因子跨状态方向稳定。

## 4. 硬性约束验证（本轮全程遵守）

- ✅ **停用HAM预测**：不引入 n_f/n_c/Total_Demand/profit/D_f/D_c 任何字段
- ✅ **K=4滚动GMM框架不变**：状态标签沿用exp404a样本外标签，不重训
- ✅ **S2仅记录不采信**：26天样本，所有分状态分析排除S2，仅保留exp405b_s2_record.csv备查
- ✅ **全部因子T日及之前生成**：绝对值用t日，环比用t-k..t，无未来信息
- ✅ **滚动窗口180训练/20预测**：与前轮一致，输出样本外指标
- ✅ **Bonferroni校正**：阈值0.05/13=0.0038
- ✅ **不含涨跌预测**：所有结果为历史统计事实

## 5. 关键文件路径速查

| 用途 | 路径 |
|------|------|
| 本轮工作目录 | `model_ham/exp405_factor_screen/` |
| 因子筛查报告 | `exp405_factor_screen/reports/exp405a_factor_screen.md` |
| 分状态检验报告 | `exp405_factor_screen/reports/exp405b_state_factor_review.md` |
| 汇总报告 | `exp405_factor_screen/reports/exp405_factor_summary.md` |
| 因子保留/淘汰清单 | `exp405_factor_screen/data/exp405_factor_registry.csv` |
| 对照表 | `exp405_factor_screen/data/exp405b_comparison.csv` |
| S2记录(不采信) | `exp405_factor_screen/data/exp405b_s2_record.csv` |
| 上一轮交接 | `exp404_cluster_analysis/HANDOVER_round5_cluster_analysis.md` |

## 6. 局限性与诚实声明

1. **340行样本外/17窗口**：统计效力有限，年化收益为近似折算
2. **State 0子样本不足**：每窗口<5天，回退全局因子，非S0内部真实IC
3. **对照A'(全局方向校准)略优于对照B(状态条件)**：结论是分状态条件增益有限，非失败——库存因子跨状态方向稳定是合理现象
4. **不可用因子5个**：near1-near2/near2-near3/曲率/大户净持仓/多空比因数据缺失未构建，非淘汰
5. **不含预测**：所有转移频率/准确率为历史统计事实，不作未来判断

## 7. 给后续接手者的建议

1. **库存类因子是稳健核心**：方向负(库存高→收益低)，跨状态稳定，可作基础信号
2. **分状态条件非失败**：增益有限是因因子跨状态稳定，换用状态敏感因子(如波动率、基差)可能状态条件更有效
3. **HAM定量预测已关闭**：见第五轮交接，本轮仅基本面+期限结构定性
4. **长期待办**：扩展原始数据长度提升S2统计效力（本次不做）

*生成时间: 2026-09-09 | 实验: exp405a-exp405c | 停用HAM | S2不采信 | 无涨跌预测 | 交接对象: 监督agent*