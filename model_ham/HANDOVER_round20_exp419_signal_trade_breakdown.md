# 会话交接文档：exp419 HAM因子/开平仓规则拆解 + 全交易清单导出（第二十轮）

> 生成时间：2026-09-10
> 交接对象：下一轮对话 agent
> 会话完成度：✅ 模块1/2/3/4 全部完成，主报告 + 交易清单 CSV 已就绪，commit 已就绪，**仅 GitHub push 待手动**
> 核心交付：HAM 6步公式白盒化 + T1开平仓时序链 + 51笔交易明细（含平仓原因+D_z+ADX标记）

---

## 0. 一句话状态

**任务已完成**。exp419 在 exp416 T1 基准上，把 HAM 策略白盒化拆解：完整数学公式 + 开平仓规则 + 51笔交易清单导出。
**关键发现**：强趋势区间（ADX>28）21笔交易平均净盈亏 **-1.53%**，正常区间 **+0.077%**——HAM 在强趋势区间系统性亏损，趋势盲区的交易级证据。强趋势中 15/21 笔因 reverse_signal（方向翻转）平仓，强趋势里频繁多空切换导致止损。
本地 commit 就绪，唯一遗留：**GitHub push 待用户手动**（与 exp416/417/418 同一 commit 链）。

---

## 1. 本轮做了什么

1. **模块1 HAM因子完整计算规则**：Brock&Hommes HAM 来源 + 6步数学公式 + rank/Z-score 双标准化细节 + 信号判定阈值规则 + 参数释义表。
2. **模块2 T1完整开平仓/仓位规则**：开仓条件 + 仓位计算公式（基础→组合→exp412风控→T1降仓）+ 4条平仓触发 + 时序规则（T日收盘决策+成交）+ 三层波动率干预。
3. **模块3 全交易明细导出**：51笔交易（HAM动态引擎重建，含平仓原因），关联 D_z（deviation Z-score）+ ADX 趋势强度，强趋势区间（ADX>28）单独标记。
4. **模块4 补充说明**：分歧修复假设 + 6类失效场景 + 信号计算vs成交时序确认（无前视，区分代码bug vs 模型风险）。

**底层零改动承诺已履行**：完全复用 exp409/411/413/416 引擎，不改任何信号/回测逻辑，仅读取现有数据重建交易链。

---

## 2. 核心结果速查

### 交易统计（HAM 动态引擎 51 笔）

| 维度 | 数值 |
|------|:---:|
| 总交易 | 51 |
| 多头/空头 | 38/13 |
| 胜率 | 31.4% |
| 平均持仓 | 1.39 日 |
| 强趋势交易(ADX>28) | 21 笔（41.2%） |
| 强趋势平均净盈亏 | **-1.53%** |
| 正常区间平均净盈亏 | +0.077% |
| 平仓原因 | neutral_revert 21 / gmm_transition 15 / reverse_signal 15 |

### HAM 参数速查

| 参数 | 值 |
|------|:---:|
| alpha/beta/gamma/W | 0.3/0.5/2/20 |
| ROLLING_TRAIN | 180 |
| POS_HIGH_Q/POS_MED_Q/NEUTRAL_Q | 0.95/0.90/0.40 |
| MAX_HOLD_HAM | 12 |
| COST_PER_SIDE | 0.0015 |

---

## 3. 关键方法论沉淀

### 3.1 强趋势 = 趋势盲区的交易级证据

强趋势区间 21 笔平均 -1.53%（vs 正常 +0.077%），且平仓原因 reverse_signal 占多数。机制：强趋势里 disagreement 反映动量主导，HAM 追趋势开仓，但趋势逆向时多空频繁翻转，每次切换都止损。这是 exp415/exp416/exp418 趋势盲区假设在**交易明细层**的印证。

### 3.2 D_z 标准化（本 exp419 新增）

deviation 的 180 日滚动 Z-score：$D_z = (deviation - \mu_{180}) / \sigma_{180}$。衡量"当前失衡偏离历史均值多少倍标准差"。用途：监控因子强度（与 exp418 模块2 Avg_Deviation 告警呼应）。注意 HAM 核心用 rank 分位标准化（disagreement），D_z 是额外的 Z-score 监控量。

### 3.3 51 笔 vs T0 40 笔的差异

HAM 动态引擎（纯 HAM-only 开平仓）重建 51 笔，T0（exp411组合+exp413风控仓位段切分）40 笔。差异来自：HAM 动态引擎直接按 open_signal/平仓条件开平仓，更频繁；T0 的组合与风控层合并了多次开平仓。时序约定一致（T日收盘决策+成交），仅交易切分粒度不同。本清单以 HAM 动态引擎为准（含平仓原因，白盒化价值更高）。

### 3.4 时序约定澄清

本策略是"T日收盘决策+T日成交+吃T日收益"，**不是**"T日算信号T+1开盘成交"。T+1开盘成交是另一套时序，本策略未采用。T日收盘决策用T日close成交无前视（exp417 shift-equivariance 8日校验已证）。

---

## 4. 文件与路径清单

### 产物（本轮新增）

| 用途 | 路径 |
|------|------|
| 导出脚本 | `model_ham/exp419_signal_trade_breakdown/exp419_trade_export.py` |
| 主报告 | `reports/exp419_ham_signal_and_trade_breakdown.md` |
| 全交易明细 | `data/exp419_full_trade_list.csv` |
| 强趋势子集 | `data/exp419_strong_trend_trades.csv` |
| 汇总 JSON | `data/exp419_summary.json` |
| 交接文档 | `model_ham/HANDOVER_round20_exp419_signal_trade_breakdown.md` |

### 数据源（只读，完全复用未改）

| 数据 | 路径 |
|------|------|
| HAM引擎 | `model_ham/exp409_ham_dynamic/exp409_ham_dynamic.py` |
| HAM因子表 | `model_ham/ham_experiment_archive/intermediate/exp401_ham_factors_pure.csv` |

---

## 5. 复现命令

```bash
cd /home/ubuntu/lithium-engine
/usr/bin/python3 model_ham/exp419_signal_trade_breakdown/exp419_trade_export.py
```

**坑点**：
- Pyright 报 pandas/exp4xx import 误报（sys.path 动态导入），全部忽略。
- reset_index 后 date 变列，需用 df["date"] 非 df.index。
- D_z 用 180 日滚动 Z-score（mean/std），min_periods=60 防前视。
- HAM 动态引擎 51 笔 ≠ T0 40 笔：HAM-only 动态 vs 组合+风控仓位段切分，时序一致。
- ADX 用单价格序列代理 TR（无 high/low），与 exp418 模块1 一致。

---

## 6. 遗留待办（下一轮优先）

### P0：GitHub push（唯一硬阻塞，与 exp416/417/418 同一 commit 链）

```bash
cd /home/ubuntu/lithium-engine && git pull --no-rebase --no-edit && git push origin main
```

### P1：可继续优化方向（exp419 衍生）

1. **🔴 强趋势交易止损优化**：强趋势区间 21 笔平均 -1.53%，reverse_signal 频繁止损。可探索强趋势检测后延迟开仓或提高入场门槛，减少强趋势里的无效切换。
2. **🟡 D_z 纳入监控**：D_z 作为因子强度监控量，可纳入 exp418 模块2 监控体系（D_z 过高=极端失衡=趋势盲区风险）。
3. **🟡 外样本验证**：2026Q4+ 验证 T1 仍正超额（exp412~419 共同遗留）。

---

## 7. 用户偏好备忘

- 微信/飞书偏好简洁直接，任务路径清晰时一口气执行到底再汇总。
- 数据敏感，网页需反拷贝保护。
- 看板风格：Dark ECharts 高密度独立页；导出报告偏好横版A4每页一图。
- 看板/前端链接必须给 GitHub Pages .github.io 域名，禁止给 IP。
- 每次回复末尾标注当前估算上下文占用百分比。

---

*生成时间：2026-09-10 | 第二十轮会话交接 | exp419 HAM因子/开平仓规则拆解+全交易清单导出 | 底层零改动，模块1 Brock&Hommes HAM 6步公式白盒化，模块2 T1三层波动率干预时序链，模块3 51笔交易清单(强趋势21笔-1.53%趋势盲区交易级证据)，模块4无前视确认，本地commit就绪push待手动*
