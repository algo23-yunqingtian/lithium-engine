# 会话交接文档：exp418 HAM 策略实盘配套工程闭环（第十九轮）

> 生成时间：2026-09-10
> 交接对象：下一轮对话 agent
> 会话完成度：✅ 模块1/2/3 全部完成，主报告 + 结题文档 + 监控 CSV 已就绪，commit 已就绪，**仅 GitHub push 待手动**
> 核心交付：趋势盲区预警指标 + 实盘监控指标体系 + 全套结题文档

---

## 0. 一句话状态

**任务已完成**。exp418 在 exp416 T1 稳健版基准上，构建 HAM 实盘落地的完整配套工程，不改动 HAM 因子与交易逻辑。
**反直觉核心发现**：模块1趋势盲区预警是"低精度、高召回"过滤器——最优阈值 ADX>28&持续度>0.70 触发 20 天（3.2%），预警后 HAM 平均 5 日盈亏 -0.90%（盲区判定有效），但**误报率 65%**（多数单边趋势 HAM 仍盈利）。因此预警触发后**只能手动降仓，不能清仓**。
**关键验证**：模块2监控体系准确捕捉到 2025Q4 趋势逆向段（2025-11 IC=-0.904 触发 CRITICAL），证明 IC 作为因子漂移哨兵有效。
本地 commit 就绪，唯一遗留：**GitHub push 待用户手动**（与 exp416/417 同一 commit 链）。

---

## 1. 本轮做了什么

1. **模块1 趋势盲区预警指标开发**：纯价格构造（独立于 HAM）三指标（ADX趋势强度+动量持续度+ATR突破），双指标共振预警。阈值遍历 36 组，最优 ADX>28&cont>0.70。回测验证预警后 HAM 平均 5 日盈亏 -0.90%，误报率 65%。给出人工干预建议（降仓不清仓）。
2. **模块2 实盘监控指标体系**：6 项月度指标（IC/盈亏比/持仓周期/胜率/分歧均值/波动率）+ 两级告警阈值（基于 32 月统计分布 P10/P25/P75/P90）。告警判定验证：准确捕捉 2025Q4 趋势逆向段（IC=-0.904 CRITICAL）。
3. **模块3 全套结题文档**：策略完整工作流（6步迭代）、T0/T1 双策略档案、风险清单（6缺陷/5失效行情/6注意事项）、数学附录（6步公式+参数释义+跨品种约束+shift-equivariance审计）。

**底层零改动承诺已履行**：完全复用 exp409/410/411/413/414/416 引擎，不改任何信号/回测逻辑，仅读取现有数据构建预警与监控。

---

## 2. 核心结果速查

### 模块1 趋势盲区预警

| 维度 | 数值 |
|------|:---:|
| 最优阈值 | ADX>28.0 & 持续度>0.70 |
| 触发日 | 20 / 628（3.2%） |
| 预警后 HAM 3日盈亏 | -0.56% |
| 预警后 HAM 5日盈亏 | **-0.90%** |
| 预警后 HAM 10日盈亏 | -0.42% |
| 预警准确率（5日跑输BH） | 35.0% |
| 误报率 | **65.0%** |
| 人工干预 | 触发→降仓50%，连续3日→30%，+高波→20%或暂停 |

### 模块2 监控指标（告警阈值表）

| 指标 | 基准中位数 | 一级告警 | 二级告警 | 方向 |
|------|:---:|:---:|:---:|:---:|
| IC_5d | 0.206 | -0.531 | -0.835 | 低 |
| PL_Ratio | 1.60 | 0.08 | 0.00 | 低 |
| Avg_Hold_Days | 2.4 | 1.9 | 1.0 | 低 |
| Win_Rate | 0.500 | 0.312 | 0.025 | 低 |
| Avg_Deviation | 0.515 | 0.708 | 0.999 | 高 |
| Realized_Vol | 0.357 | 0.509 | 0.596 | 高 |

告警判定：OK 78 / WARNING 12 / CRITICAL 16 / N/A 86。

### 关键验证案例

**2025-11 IC=-0.904 触发 CRITICAL** —— 正是 exp415 已知趋势逆向失效段（2025Q4 超额 -32.6%），监控体系准确捕捉。

---

## 3. 关键方法论沉淀

### 3.1 预警的独立性设计

趋势盲区预警**只用价格序列构造**，不读 HAM 因子（disagreement/deviation），不依赖基本面数据。这样预警才能独立于 HAM 信号给出客观判断——若预警依赖 HAM 因子，HAM 失效时预警也失效，形成"双重失效"闭环。

### 3.2 降仓不清仓的权衡逻辑

预警误报率 65% 意味着不能"一触发就清仓"。HAM 在极端波动日本就盈利（exp416 §2.3 上行跳空 +0.1159），清仓会误伤盈利敞口。降仓保留底仓：35% 真盲区少亏、65% 误报仍盈利。这与 exp416 T1"波动率降仓有效但昂贵"一致——降仓是"保险费"，不是"断绝"。

### 3.3 IC 的持仓日口径

IC 只在持仓日（T0_pos≠0）计算，衡量实际持仓方向与未来收益的相关。HAM 持仓稀疏（40笔/628天，40%空仓），多数月 IC 为 NaN 是正常特征，不是 bug。全样本 IC 中位数 +0.206（正，持仓方向总体正确）。全日 signal_dir IC 含噪声（噪声信号拉低），不用全日 IC。

### 3.4 阈值校准的局限

PL_Ratio/Win_Rate 阈值偏低（P10≈0）因交易稀疏，实盘噪声大。波动率阈值偏高（2026碳酸锂波动放大拉高分位）。阈值需随市场状态迁移重新校准。

---

## 4. 文件与路径清单

### 产物（本轮新增）

| 用途 | 路径 |
|------|------|
| 模块1 预警脚本 | `model_ham/exp418_production_prepare/exp418_m1_trend_blindspot.py` |
| 模块2 监控脚本 | `model_ham/exp418_production_prepare/exp418_m2_monitoring.py` |
| 主报告 | `reports/exp418_production_prepare.md` |
| 完整结题文档 | `model_ham/HAM_Project_Final_Document.md` |
| 模块1 预警信号 | `data/exp418_M1_alert_signals.csv` |
| 模块1 阈值遍历 | `data/exp418_M1_alert_grid.csv` |
| 模块1 评估明细 | `data/exp418_M1_alert_eval_detail.csv` |
| 模块1 汇总 | `data/exp418_M1_summary.json` |
| 模块2 月度指标 | `data/exp418_M2_monthly_metrics.csv` |
| 模块2 告警阈值表 | `data/exp418_M2_alert_thresholds.csv` |
| 模块2 告警判定 | `data/exp418_M2_alert_judgement.csv` |
| 交接文档 | `model_ham/HANDOVER_round19_exp418_production_prepare.md` |

### 数据源（只读，完全复用未改）

| 数据 | 路径 |
|------|------|
| HAM引擎 | `model_ham/exp409_ham_dynamic/exp409_ham_dynamic.py` |
| HAM因子表 | `model_ham/ham_experiment_archive/intermediate/exp401_ham_factors_pure.csv` |
| T0净值 | `model_ham/exp416_tail_risk_protect/data/exp416_daily_equity.csv` |
| T0交易 | `model_ham/exp416_tail_risk_protect/data/exp416_T0_trades.csv` |

---

## 5. 复现命令

```bash
cd /home/ubuntu/lithium-engine
# 模块1 趋势盲区预警 (阈值遍历 36 组)
/usr/bin/python3 model_ham/exp418_production_prepare/exp418_m1_trend_blindspot.py
# 模块2 实盘监控指标 (32 月度指标 + 告警判定)
/usr/bin/python3 model_ham/exp418_production_prepare/exp418_m2_monitoring.py
```

**坑点**：
- Pyright 报 pandas/exp4xx import 误报（sys.path 动态导入），全部忽略。
- 预警指标严格用纯价格构造，不读 HAM 因子——独立性设计核心。
- IC 只在持仓日计算（HAM 持仓稀疏，多数月 NaN 正常）。
- set 不能作为 pandas indexer，用 list/DatetimeIndex。
- build_alert 返回 DataFrame 需 set_index("date").sort_index() 才能与净值索引对齐。
- grid idxmin 全 NaN 会报错，需 notna() 过滤。

---

## 6. 遗留待办（下一轮优先）

### P0：GitHub push（唯一硬阻塞，与 exp416/417 同一 commit 链）

```bash
cd /home/ubuntu/lithium-engine && git pull --no-rebase --no-edit && git push origin main
```

本地 commit 已含 exp416+417+418 全部产物，只欠 push。

### P1：可继续优化方向（exp418 衍生）

1. **🔴 趋势逆向敞口管理（最紧急，exp416 印证）**：HAM 真实尾部风险是趋势逆向回撤。模块1预警是价格构造的"事后触发器"，真正应识别趋势反转信号主动减仓（非波动率/趋势强度触发，而是反转信号）。
2. **🟡 预警阈值动态校准**：模块1阈值基于2024-2026样本，触发率偏离3-5%区间时重新校准。
3. **🟡 监控阈值市场状态迁移**：模块2阈值跨市场状态迁移需重新校准，碳酸锂低波转高波时波动率阈值会失效。
4. **🟡 外样本验证**：2026Q4+ 验证 T1 仍正超额（exp412~418 共同遗留）。
5. **🟢 跨品种独立验证**：若推广锌/铜，每品种独立回测+独立阈值校准。

---

## 7. 用户偏好备忘

- 微信/飞书偏好简洁直接，任务路径清晰时一口气执行到底再汇总。
- 数据敏感，网页需反拷贝保护。
- 看板风格：Dark ECharts 高密度独立页；导出报告偏好横版A4每页一图。
- 看板/前端链接必须给 GitHub Pages .github.io 域名，禁止给 IP。
- 每次回复末尾标注当前估算上下文占用百分比。

---

*生成时间：2026-09-10 | 第十九轮会话交接 | exp418 HAM实盘配套工程闭环 | 底层零改动，模块1趋势盲区预警(独立性设计+降仓不清仓权衡)，模块2六项监控两级告警(验证捕捉2025Q4趋势逆向段)，模块3结题文档，三模块协同操作手册，本地commit就绪push待手动*
