# 交接文档：exp430 真实行情影子盘验证

> 生成时间：2026-09-10
> 适用对象：新会话 / 新 agent
> 代码目录：`model_ham/exp430_shadow_trading/`
> 报告：`reports/exp430_real_shadow_trading.md`

---

## 0. 一句话结论

exp430 影子盘框架已完成，20 交易日回填验证通过（0 失败），cron 持续运行已就绪。策略当前全程空仓（IC 失效期），待重新开仓后才能评估绩效偏差。

---

## 1. 前置条件

| 条件 | 状态 |
|:---|:---|
| exp429 代码审计完成 | ✅ 无致命缺陷 |
| ham_pipeline.py | ✅ 可运行 |
| run_daily.py | ✅ 可运行 |
| monitoring.py | ✅ 可运行 |
| B1-B4 非致命问题 | 延后处理 |

---

## 2. 交付物清单

### 2.1 代码

| 文件 | 职责 |
|:---|:---|
| `model_ham/exp430_shadow_trading/shadow_runner.py` | 每日 akshare 行情获取 + 追加 + 流水线重跑 + 影子盘日志记录 |
| `model_ham/exp430_shadow_trading/backfill_shadow.py` | 用历史日 K 逐日回填 20 交易日验证框架完整性 |
| `model_ham/exp430_shadow_trading/report_generator.py` | 影子盘绩效 vs 回测基准对比报告生成 |
| `model_ham/exp430_shadow_trading/run_exp430.py` | 主入口（手动 / cron / 状态查询） |

### 2.2 数据产物

| 文件 | 内容 |
|:---|:---|
| `data/exp430_shadow_log.csv` | 逐日影子盘日志（20 行，信号/仓位/模拟盈亏/告警） |
| `data/exp430_run_log.json` | 运行历史（20 次成功） |
| `data/cron_log.txt` | cron 运行日志（自动追加） |

### 2.3 文档

| 文件 | 内容 |
|:---|:---|
| `reports/exp430_real_shadow_trading.md` | 正式报告（绩效对比 + 告警评估 + 偏差统计） |
| `model_ham/HAM_Project_Final_Document.md` | 结题文档（新增 §4.12） |

### 2.4 cron 定时

```cron
30 15 * * 1-5 /home/ubuntu/.hermes/scripts/exp430_shadow_cron.sh
```

- 每个交易日 15:30 自动运行
- 脚本：`~/.hermes/scripts/exp430_shadow_cron.sh`
- 日志：`model_ham/exp430_shadow_trading/data/cron_log.txt`

---

## 3. 数据链路

```
akshare Sina (LC2701 日K)
  ↓ fetch_daily_ohlc()
lithium_future.csv (OHLC 追加)
  ↓ append_ohlc()
lc_spot.db (现货价)
  ↓ fetch_spot_price()
estimate_p_fund() → exp401_ham_factors_pure.csv (追加 close + P_fund)
  ↓
update_end_date() → exp424_engine.END 动态更新
  ↓
ham_pipeline.run_pipeline() → D因子 + 信号 + 两套仓位
  ↓
monitoring.judge_drift() → 告警判定
  ↓
update_shadow_log() → exp430_shadow_log.csv (追加一行)
  ↓
report_generator.generate_report() → reports/exp430_real_shadow_trading.md
```

**关键数据文件路径**：
- OHLC：`model_ham/raw_data/lithium_future.csv`（760 行，止于 2026-09-10）
- HAM 因子：`model_ham/ham_experiment_archive/intermediate/exp401_ham_factors_pure.csv`（723 行）
- 现货价：`/home/ubuntu/lc_futures_data/data/lc_spot.db`（lc_spot.db，734 行）
- 流水线输出：`model_ham/exp429_production/data/exp429_daily_pipeline.csv`（629 行）

---

## 4. 回填验证结论（20 交易日）

### 4.1 运行统计

| 项目 | 值 |
|:---|:---|
| 回填区间 | 2026-08-14 ~ 2026-09-10 |
| 交易日数 | 20 |
| 成功运行 | 20 |
| 失败运行 | 0 |
| 数据来源 | akshare Sina (LC2701 日K) |
| 合约 | LC2701 |

### 4.2 影子盘绩效

| 指标 | 影子盘 | 回测基准 | 偏差 |
|:---|---:|---:|---:|
| 年化收益 | 0.0% | 34.0% | -34.0pp |
| 最大回撤 | 0.0% | -6.8% | +6.8pp |
| 交易笔数 | 0 | 135 | -135 |
| 持仓日数 | 0 | 232 | -232 |
| NAV 终值 | ¥1,000,000 | - | - |

### 4.3 告警分布

| 级别 | 日数 | 占比 | 回测基准 |
|:---|---:|---:|---:|
| OK | 2 | 10% | 67% |
| WARNING | 0 | 0% | 20% |
| CRITICAL | 18 | 90% | 13% |

### 4.4 核心解读

**策略全程空仓 = 风控正确运作，非策略失效**：

1. 20 天中 18 天 IC 滚动值 < 0 → CRITICAL 告警
2. 触发 R4 规则（CRITICAL 连续 ≥ 3 日 → 暂停交易）
3. 策略正确选择空仓等待 → 零盈亏 + 零回撤
4. 根因：碳酸锂 155k→141k 暴跌 -9%，市场结构突变，因子失效

---

## 5. 常用命令

```bash
# 查看影子盘状态
cd /home/ubuntu/lithium-engine
/usr/bin/python3 model_ham/exp430_shadow_trading/run_exp430.py --status

# 手动运行一日
/usr/bin/python3 model_ham/exp430_shadow_trading/run_exp430.py

# 仅生成报告
/usr/bin/python3 model_ham/exp430_shadow_trading/run_exp430.py --report-only

# 回填验证（覆盖历史 20 日）
/usr/bin/python3 model_ham/exp430_shadow_trading/backfill_shadow.py

# 查看影子盘日志
/usr/bin/python3 -c "
import pandas as pd
log = pd.read_csv('model_ham/exp430_shadow_trading/data/exp430_shadow_log.csv')
print(log[['date','close','pos_A','action_A','nav_A','alert_level']].to_string())
"
```

---

## 6. 环境备忘

- Python：`/usr/bin/python3`（3.12，含 pandas/scipy/numpy/akshare）
- 工作目录：`/home/ubuntu/lithium-engine`
- LSP/Pyright 的 `Import ... could not be resolved` 报错为动态 sys.path 导入误报，**全部忽略**
- akshare Sina 接口：`futures_zh_daily_sina(symbol='LC2701')` 返回日 OHLC
- akshare realtime 接口不支持 GFEX（碳酸锂），只能用日 K
- `exp424_engine` 的 `END` 原硬编码为 `2026-09-07`，影子盘动态更新为最新日

---

## 7. 待办 / 后续

1. **cron 持续运行**：每交易日 15:30 自动运行，待策略重新开仓后积累有效绩效对比
2. **IC 失效期复盘**：当前 IC 持续 < 0 是因子周期性失效还是市场结构永久变化？
3. **滑点/成交价偏差评估**：待策略产生实际交易信号后才能评估
4. **B1-B4 非致命问题**：影子盘优先，待策略重新开仓后处理
5. **参数重校准**：如 IC 持续失效，可能需要重新校准告警阈值

---

## 8. 风险提示

- 影子盘为模拟成交，**未接入真实撮合**
- 成交价为日收盘代理，非真实 9:01 分钟均价
- P_fund 为现货价代理，非真实产业成本曲线
- 20 交易日样本量有限，统计显著性不足
- cron 运行依赖 akshare Sina 接口可用性

---

*生成时间：2026-09-10 | exp430 真实行情影子盘 | 20 日回填验证通过 | cron 每日 15:30 持续运行 | 策略空仓因 IC 失效（风控正确） | 待重新开仓评估绩效偏差*
