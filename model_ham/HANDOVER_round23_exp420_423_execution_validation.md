# 会话交接文档：exp420/420_check/423 成交假设偏差量化 + 真实时序验证

> 生成时间：2026-09-10
> 交接对象：下一轮对话 agent
> 会话完成度：✅ 全部完成，commit+push 已完成
> 核心交付：3个exp完成 — exp420（成交假设偏差）、exp420_check（BUG校验修正）、exp423（T1真实时序验证）

---

## 0. 一句话状态

**全部完成。** 三个实验形成完整证据链：exp420发现BUG（max_dd=0%）→ exp420_check定位修正（position不含exit日）→ exp423用T1完整版本验证（衰减仅11pp，T1仍盈利可实盘）。

**关键发现**：纯HAM动态引擎（1.4天持仓）对成交假设极度敏感（衰减113pp，版本B亏损），但T1完整版本（4.5天持仓）衰减仅11pp（版本B仍盈利37.6%）。**持仓周期是成交假设敏感性的决定性因素。**

---

## 1. 本轮做了什么

### 1.1 exp420：成交假设偏差量化校验

- 两套成交假设并行回测：版本A（T日收盘成交）vs 版本B（T+1开盘成交）
- 日K数据审计：OHLC全字段存在（Open/High/Low/Close 100%非空），HAM仅用Close
- 持仓周期审计：平均1.39天（78.4%持仓1天），1.4天=统计均值非半天平仓
- 新增2条风险到结题文档

**BUG**：版本A最大回撤=0%、年化+167.9%（异常）

### 1.2 exp420_check：校验修正

- 参数一致性校验：exp420与exp419参数完全一致，无差异
- BUG根因定位：`compute_equity_curve_A` 中 position 数组用 `range(ei, xi)` 不含 exit 日
  - 导致 exit 日收益重复计入 → max_dd=0% → 年化虚高
  - 修正：`range(ei, xi+1)` 含 exit 日
- 修正后版本A：年化+93.9%（非+167.9%）、max_dd=-14.68%（非0%）
- 逐笔盈亏与exp419完全一致（差异<1e-6）

### 1.3 exp423：T1完整版本真实时序验证

- 完美复现exp416 T1基准：年化48.76%、夏普2.799、回撤-6.0%、Calmar8.13
- 版本B（T+1开盘）：年化37.6%、夏普2.274、回撤-9.9%、Calmar3.80
- 衰减仅11pp（vs 纯HAM的113pp）
- 隔夜跳空非系统性不利：有利跳空56% vs 不利44%，正向贡献36.3%
- **结论：T1在真实时序下仍盈利，可实盘部署，但需按Calmar 3.80重新校准仓位**

---

## 2. 核心结果速查

### 2.1 三组对比

| 维度 | 纯HAM动态(exp420_check) | T1完整(exp423) |
|------|:----------------------:|:--------------:|
| 平均持仓 | 1.4天 | 4.5天 |
| 版本A年化 | +93.9% | +48.8% |
| 版本B年化 | **-19.5%** ❌ | **+37.6%** ✓ |
| 衰减幅度 | 113pp | **11pp** |
| 隔夜跳空影响 | 系统性不利 | 非系统性（正向贡献） |
| 可实盘？ | ❌ 不可 | ✓ 可以 |

### 2.2 隔夜跳空归因

| 维度 | 值 |
|------|:--:|
| 持仓天数 | 216 |
| 有利跳空 | 121次（56%） |
| 不利跳空 | 95次（44%） |
| 碳酸锂平均跳空 | +0.21%（正向倾向） |
| 多头持仓占比 | 68.5% |
| 隔夜贡献占价格收益 | 36.3% |

---

## 3. 关键方法论沉淀

### 3.1 BUG教训：position数组必须含exit日

```python
# BUG：不含exit日 → 收益重复计入 → max_dd=0%
for t in range(ei, xi):
    positions[t] = size

# 正确：含exit日
for t in range(ei, xi + 1):
    positions[t] = size
```

**根因**：exp409.run_ham_dynamic在exit日平仓（close[xi]），但position[xi]=0表示exit日无持仓，导致exit日的价格变动收益被重复计入两次（一次在exit日的price_change，一次在下一笔交易的entry日）。

### 3.2 持仓周期是成交假设敏感性的决定性因素

- 1.4天持仓（纯HAM）→ 隔夜跳空占比高 → 衰减113pp → 版本B亏损
- 4.5天持仓（T1完整）→ 隔夜跳空被摊薄 → 衰减11pp → 版本B仍盈利

**教训**：短持仓策略（<2天）对成交假设极度敏感，必须验证T+1开盘成交假设。长持仓策略（>3天）衰减可控。

### 3.3 版本B收益计算的正确方式

```python
# T+1开盘成交的收益分解：
# 隔夜跳空：pos[t-1] × (open[t] - close[t-1]) / close[t-1]  （旧仓位承担）
# 日内：pos[t] × (close[t] - open[t]) / open[t]  （新仓位承担）
```

---

## 4. 文件与路径清单

### 4.1 exp420（成交假设偏差）

| 用途 | 路径 |
|------|------|
| 引擎脚本 | `model_ham/exp420_execution_assumption_bias/exp420_engine.py` |
| 主报告 | `reports/exp420_execution_assumption_bias.md` |
| 净值对比CSV | `model_ham/exp420_execution_assumption_bias/data/exp420_equity_comparison.csv` |
| 逐笔盈亏对比 | `model_ham/exp420_execution_assumption_bias/data/exp420_trade_comparison.csv` |
| 持仓天数分布 | `model_ham/exp420_execution_assumption_bias/data/exp420_hold_distribution.csv` |
| 汇总JSON | `model_ham/exp420_execution_assumption_bias/data/exp420_summary.json` |

### 4.2 exp420_check（校验修正）

| 用途 | 路径 |
|------|------|
| 校验脚本 | `model_ham/exp420_check/exp420_check.py` |
| 校验报告 | `reports/exp420_check_audit.md` |
| 参数对比表 | `model_ham/exp420_check/data/exp420_check_param_comparison.csv` |
| 逐笔盈亏核对 | `model_ham/exp420_check/data/exp420_check_trade_audit.csv` |
| 净值对比CSV | `model_ham/exp420_check/data/exp420_check_equity_comparison.csv` |
| 汇总JSON | `model_ham/exp420_check/data/exp420_check_summary.json` |

### 4.3 exp423（T1真实时序验证）

| 用途 | 路径 |
|------|------|
| 引擎脚本 | `model_ham/exp423_real_time_validation/exp423_engine.py` |
| 主报告 | `reports/exp423_real_time_validation.md` |
| 净值对比CSV | `model_ham/exp423_real_time_validation/data/exp423_equity_comparison.csv` |
| 逐笔对比CSV | `model_ham/exp423_real_time_validation/data/exp423_trade_comparison.csv` |
| 隔夜跳空统计 | `model_ham/exp423_real_time_validation/data/exp423_gap_stats.csv` |
| 汇总JSON | `model_ham/exp423_real_time_validation/data/exp423_summary.json` |

### 4.4 更新后的结题总文档

| 用途 | 路径 |
|------|------|
| 结题文档 | `model_ham/HAM_Project_Final_Document.md` |

---

## 5. 复现命令

```bash
cd /home/ubuntu/lithium-engine

# exp420（有BUG，仅存档）
/usr/bin/python3 model_ham/exp420_execution_assumption_bias/exp420_engine.py

# exp420_check（修正后）
/usr/bin/python3 model_ham/exp420_check/exp420_check.py

# exp423（T1完整版本，推荐）
/usr/bin/python3 model_ham/exp423_real_time_validation/exp423_engine.py
```

**坑点**：
- exp420_engine.py有BUG（position不含exit日），**不要直接使用**，用exp420_check或exp423
- Pyright报pandas/exp4xx import误报（sys.path动态导入），全部忽略
- exp416引擎的`extract_trades`签名是`(positions, price, dates)`，不是`cost`参数
- 版本B收益计算：隔夜跳空由pos[t-1]承担，日内由pos[t]承担

---

## 6. 遗留待办

### P0：无硬阻塞

所有任务已完成，commit+push已完成。

### P1：可继续方向（exp423衍生）

1. **🟢 实盘部署准备**：T1版本B年化37.6%、Calmar3.80，可考虑实盘部署。需按3.80校准仓位，预留10%回撤缓冲。
2. **🟡 分钟数据接入**：如需9:01分钟均价成交，需引入分钟级别行情数据。可进一步验证分钟级时序对版本B的影响。
3. **🟡 外样本验证**：2026Q4+验证T1版本B仍正超额（exp412~423共同遗留）。
4. **🟢 持仓周期优化**：持仓周期是成交假设敏感性的关键。可探索持仓周期对衰减幅度的敏感性曲线。
5. **🟡 隔夜跳空过滤**：碳酸锂整体正向跳空倾向对多头有利。可探索高波动日（隔夜跳空大的日子）的开仓过滤策略。

---

## 7. 用户偏好备忘

- 微信/飞书偏好简洁直接，任务路径清晰时一口气执行到底再汇总。
- 数据敏感，网页需反拷贝保护。
- 看板风格：Dark ECharts高密度独立页；导出报告偏好横版A4每页一图。
- 看板/前端链接必须给 GitHub Pages .github.io 域名，禁止给 IP。
- 每次回复末尾标注当前估算上下文占用百分比。

---

*生成时间：2026-09-10 | exp420/420_check/423成交假设偏差+真实时序验证 | exp420发现BUG(max_dd=0%)→exp420_check修正(position不含exit日)→exp423用T1完整版本验证(衰减11pp,仍盈利) | 关键发现:持仓周期是决定性因素(1.4天→113pp vs 4.5天→11pp) | T1可实盘(Calmar3.80) | 全部commit+push完成*
