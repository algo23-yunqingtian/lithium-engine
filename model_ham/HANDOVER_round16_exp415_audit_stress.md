# 会话交接文档：exp415 代码真实性核验 + HAM 隐藏风险压力测试（第十六轮）

> 生成时间：2026-09-10
> 交接对象：下一轮对话 agent
> 会话完成度：✅ 模块A（A1/A2/A3）+ 模块B（B1/B2/B3/B4）全部完成，主报告 + 交接文档 + 全部 CSV/JSON 已就绪，commit 已就绪，**仅 GitHub push 待手动**
> 核心交付：代码执行真实性核验（21项单元测试+17项基准复现全通过）+ HAM隐藏风险专项压力测试（发现B2跳空止损失效为最严重风险）

---

## 0. 一句话状态

**任务已完成**。exp415 对 HAM 策略做代码真实性核验 + 隐藏风险压力测试。
**真实性结论：核验通过**——3组单元测试（白盒numpy重算21项）逐字段一致，exp412基准17项逐位复现（偏差<5e-5），代码无偷改/跳过/编造。
**风险结论：发现1严重+1中度**——🔴 B2跳空止损失效（5%跳空35%失效，12%/15%跳空82.5%失效，极端单笔亏损-15%+，历史1%分位隔夜跌幅-8.9%已超止损线）；🟡 B3轻微漂移（后半段超额14.0%→6.5%）。🟢 B1容量充足（100亿资金年化仍+72%）；🟢 B4拥挤非主因（回撤段敞口仅0.22）。
本地 commit 就绪，唯一遗留：**GitHub push 待用户手动**。

---

## 1. 本轮做了什么

1. **模块A 代码执行真实性核验**
   - **A1 手工预设单元测试**：选3个历史交易日（多头满仓/空头60%/不开仓），用numpy白盒独立重算整条信号链路，逐字段对比代码。21/21通过。
   - **A2 逐笔交易日志+净值CSV+抽样5笔推理链**：输出全区间51笔/T1 40笔交易日志（含开平仓日原始行情+信号中间量+平仓判定逻辑文本）、每日净值CSV、种子415抽样5笔完整推理链。
   - **A3 exp412基准强制复现**：作为硬门槛最先跑，17项指标全部逐位匹配（偏差<5e-5），实验组获准运行。
2. **模块B HAM隐藏风险压力测试**
   - **B1 资金容量**：平方根冲击模型，多档资金重算净收益。低换手→容量充足。
   - **B2 跳空压力**：模拟隔夜跳空下止损gap-through失效，算单笔亏损上限+组合冲击。发现最严重风险。
   - **B3 因子稳定性**：按季度分段超额收益，漂移检测。轻微漂移。
   - **B4 极端拥挤**：连续同向持仓/重仓占比/连亏/回撤段敞口。拥挤非主因。
3. 完整主报告 + 交接文档 + 13个CSV/JSON + git commit。

**底层零改动承诺已履行**：完全复用 exp409/410/411/413/414 引擎，压力测试通过「注入成本/注入价格冲击/分段切片」实现，不修改任何信号逻辑/阈值/风控参数。

---

## 2. 核心结果速查

### 报告必答五问

| # | 问题 | 结论 |
|---|------|------|
| ① | 单元测试全通过，代码与规则一致？ | **✅ 是**。A1 21项+A3 17项全通过。代码无偷改/跳过/编造。 |
| ② | HAM资金容量上限？ | **🟢 充足**。年换手16次，100亿资金年化仍+72%（衰减4.9%），夏普2.56。 |
| ③ | 跳空下事前止损失效？极端单笔亏损上限？ | **🔴 失效且严重**。5%跳空35%失效，12%/15%跳空82.5%失效。上限-15%+。 |
| ④ | 分段下超额稳定？有漂移？ | **🟡 总体稳定轻微漂移**。9/11季度正超额，后半段14.0%→6.5%(-7.6pp)，2025Q4 -32.6%。 |
| ⑤ | 收益前提/天然限制/落地约束？ | 见报告§4。核心：信号预警模型，不抗跳空，强趋势段跑输，落地须加跳空保护。 |

### 关键数值

| 维度 | 数值 |
|------|:---:|
| A1 单元测试 | 21/21 通过 |
| A3 基准复现 | 17/17 匹配（最大偏差 4e-5） |
| B1 容量上限 | ≥100亿（年化仍+72%） |
| B1 年换手 | 15.9 次/年 |
| B2 历史1%分位隔夜跌 | -8.90% |
| B2 5%跳空止损失效 | 35% |
| B2 12%跳空止损失效 | 82.5% |
| B2 极端单笔亏损上限 | -15.3% |
| B3 正超额季度 | 9/11（81.8%） |
| B3 漂移 | -7.58pp（轻微） |
| B3 分段夏普CV | 0.895 |
| B4 最大连续多头 | 33天 |
| B4 回撤段敞口 | 0.219（非拥挤） |

### 基准T1指标（复现校验通过）

| 年化 | 夏普 | 回撤 | Calmar | 盈亏比 | 交易数 |
|:---:|:---:|:---:|:---:|:---:|:---:|
| 0.7609 | 2.6381 | -0.1156 | 6.5841 | 2.9268 | 40 |

---

## 3. 关键方法论沉淀

### 3.1 单元测试的白盒重算设计（本轮核心）

**关键**：单元测试不能直接import引擎函数对比（同源错误会漏掉bug）。必须用numpy/pandas**独立重写**整条信号链路（compute_system_deviation→compute_aux_signals→compute_ham_signals的判定逻辑），再与代码黑盒输出对比。这样代码若有偷改逻辑、阈值偏移、跳过模块，都会被独立重算捕捉。

3个样例选取原则：**覆盖所有分支**——满仓档/60%仓/不开仓（仓位3种路径）× 多头/空头（方向2种）× 主信号触发/未触发（2种）。21字段含disagreement/deviation/signal_dir/main_trigger/aux_confirm/open_signal/position_size，全面覆盖信号→方向→仓位的因果链。

### 3.2 跳空止损失效的建模（B2核心，最严重风险）

HAM**无显式止损**，靠4条信号平仓规则（中性回归/GMM跃迁/反向信号/12日强平）。这4条都是**信号驱动、T日决策T日执行**，隔夜跳空时：
- 止损单/信号平仓单**只能在跳空后开盘价成交**（gap-through），实际亏损=跳空幅度，而非止损幅度。
- 建模：实际亏损=size×g+2×COST×size（g=跳空幅度），g>止损线时止损失效。
- **历史尾部证据**：1%分位隔夜跌-8.9%，远超-5%止损线，意味着约5%交易日天然突破止损。
- **结论**：HAM"安全边际"在流动性冲击下崩塌，这是它作为"信号预警模型"的结构性软肋——**它本就不该承担隔夜方向性风险，实盘必须用期权对冲或降低隔夜敞口**。

### 3.3 容量测试的参与率建模（B1）

平方根冲击模型：eff_slip=base_slip×(1+k×√pct_of_adv)，k=1.5。
**关键发现**：HAM年换手仅15.9次，日均成交额极小，即使100亿资金参与率仅3.2%，冲击成本几乎不放大。**低换手策略的容量上限天然高**，这不是bug而是结构性优势。报告须如实说明，不能因为"没找到容量瓶颈"就认为测试无效——低换手本身就是答案。

### 3.4 漂移检测的双指标（B3）

单看正超额季度比例（9/11）会误判"完全稳定"。必须叠加**前后半段超额均值差**（14.0%→6.5%，-7.6pp）+**分段夏普变异系数**（0.895）+**最弱季度定位**（2025Q4 -32.6%）。三者结合才能识别"总体稳定但局部漂移"。2025Q4的-32.6%超额不是bug，是HAM在强单边上涨段的结构性盲区（多空切换跑输纯多头），这是实盘落地的关键约束。

### 3.5 口径差异的辨识（A2发现）

`run_ham_dynamic`原生trades 51笔 vs `extract_trades`报告口径40笔，**不是bug**。根因：原生引擎平仓日t仍设positions[t]非零，若t+1直接开新仓，positions数组连续非零，extract_trades把两段合并为一段。辨识方法：先用A1单元测试确认信号/方向/仓位严格一致（已证），再判断计数差异纯属"持仓段边界定义"，非逻辑bug。报告须如实披露两套口径，以40笔（extract_trades）为准。

---

## 4. 文件与路径清单

### 产物（本轮新增）

| 用途 | 路径 |
|------|------|
| 模块A核心（A1单元测试+A3基准复现） | `model_ham/exp415_audit_stress/exp415_core.py` |
| 模块A2（逐笔日志+净值+抽样） | `model_ham/exp415_audit_stress/exp415_a2.py` |
| 模块B（四项压力测试） | `model_ham/exp415_audit_stress/exp415_b.py` |
| 主报告 | `reports/exp415_audit_stress.md` |
| A1单元测试结果 | `model_ham/exp415_audit_stress/data/exp415_A1_unit_tests.csv` |
| A2逐笔交易日志（全51笔） | `model_ham/exp415_audit_stress/data/exp415_A2_ham_trades_full.csv` |
| A2逐笔交易日志（窗口） | `model_ham/exp415_audit_stress/data/exp415_A2_ham_trades_window.csv` |
| A2 T1交易日志 | `model_ham/exp415_audit_stress/data/exp415_A2_T1_trades.csv` |
| A2抽样5笔推理链 | `model_ham/exp415_audit_stress/data/exp415_A2_sampled5_trades.csv` |
| A2每日净值（A组） | `model_ham/exp415_audit_stress/data/exp415_A2_daily_equity_A_ham.csv` |
| A2每日净值（T1） | `model_ham/exp415_audit_stress/data/exp415_A2_daily_equity_T1_combo.csv` |
| A3基准复现校验 | `model_ham/exp415_audit_stress/data/exp415_A3_baseline_reproduce.csv` |
| B1容量衰减曲线 | `model_ham/exp415_audit_stress/data/exp415_B1_capacity_curve.csv` |
| B2单笔跳空亏损 | `model_ham/exp415_audit_stress/data/exp415_B2_single_gap_loss.csv` |
| B2组合跳空冲击 | `model_ham/exp415_audit_stress/data/exp415_B2_portfolio_gap_impact.csv` |
| B3季度稳定性 | `model_ham/exp415_audit_stress/data/exp415_B3_quarterly_stability.csv` |
| B4拥挤场景 | `model_ham/exp415_audit_stress/data/exp415_B4_crowding.csv` |
| B汇总JSON | `model_ham/exp415_audit_stress/data/exp415_B_summary.json` |
| 交接文档 | `model_ham/HANDOVER_round16_exp415_audit_stress.md` |

### 数据源（只读，完全复用未改）

| 数据 | 路径 |
|------|------|
| HAM引擎 | `model_ham/exp409_ham_dynamic/exp409_ham_dynamic.py` |
| 基本面引擎 | `model_ham/exp410_fund_dynamic/exp410_fund_dynamic.py` |
| 组合目标构建 | `model_ham/exp411_combined/exp411_combined.py` |
| 风控引擎 | `model_ham/exp413_robustness/exp413_engine.py` |
| 消融引擎 | `model_ham/exp414_attribution/exp414_engine.py` |
| HAM因子表 | `model_ham/ham_experiment_archive/intermediate/exp401_ham_factors_pure.csv` |

---

## 5. 复现命令

```bash
cd /home/ubuntu/lithium-engine
# 引擎用 /usr/bin/python3（pandas 3.0.3）
# 模块A（A1单元测试+A3基准复现，基准不通过则终止）
/usr/bin/python3 model_ham/exp415_audit_stress/exp415_core.py
# 模块A2（逐笔日志+净值+抽样推理链）
/usr/bin/python3 model_ham/exp415_audit_stress/exp415_a2.py
# 模块B（四项压力测试）
/usr/bin/python3 model_ham/exp415_audit_stress/exp415_b.py
```

**坑点**：
- Pyright报pandas/exp4xx import误报（sys.path动态导入无法解析），**全部忽略**，运行正常。
- pandas 3.0.3 `pd.concat` DatetimeIndex排序 Pandas4Warning 无害。
- **口径差异**：run_ham_dynamic原生trades 51笔 vs extract_trades报告口径40笔，属持仓段边界定义差异，非信号bug（A1已证信号一致）。报告以40笔为准。
- git push网络到github.com:443可能不通，需用户手动或有网络时推。

---

## 6. 遗留待办（下一轮优先）

### P0：GitHub push（唯一硬阻塞）

```bash
cd /home/ubuntu/lithium-engine && git pull --no-rebase --no-edit && git push origin main
```

本地commit已含全部产物，只欠push。push前先pull避免non-fast-forward。

### P1：可继续优化方向（exp415衍生，按优先级）

1. **🔴 跳空保护机制（最紧急，对应B2）**：exp414 §6的"HAM单笔事前止损"在跳空下失效（本轮已证）。应改为：(a)隔夜持仓减半，(b)隔夜波动>6%次日熔断减仓，(c)用期权对冲隔夜方向风险。这是HAM实盘落地的**第一优先级**。
2. **🟡 强趋势段减仓/让位（对应B3的2025Q4 -32.6%）**：识别单边强趋势（季度涨幅>40%）时降HAM权重或临时切换纯趋势。与exp414 §6.4"震荡段/趋势段regime切换"同源。
3. **漂移监控纳入风控**：分段超额+夏普CV（当前0.895）做监控，漂移超阈值降权。
4. **降低隔夜敞口的量化测试**：当前平均敞口0.662，测试隔夜50%/25%敞口对回撤/跳空冲击的改善。
5. **样本外验证**：所有结论基于2024-02~2026-09，需2026Q4+样本外验证（exp412~415共同遗留）。

---

## 7. 用户偏好备忘

- 微信/飞书偏好简洁直接，任务路径清晰时一口气执行到底再汇总。
- 数据敏感，网页需反拷贝保护。
- 看板风格：Dark ECharts高密度独立页；导出报告偏好横版A4每页一图。
- 跨agent协作给出可复制提示词卡（单个fenced code block）。
- 看板/前端链接必须给GitHub Pages .github.io域名，禁止给IP。
- 每次回复末尾标注当前估算上下文占用百分比。

---

*生成时间：2026-09-10 | 第十六轮会话交接 | exp415 代码真实性核验 + HAM隐藏风险压力测试 | 底层零改动，A1/A2/A3全通过，B2跳空止损失效为最严重隐藏风险，本地commit就绪，push待手动*
