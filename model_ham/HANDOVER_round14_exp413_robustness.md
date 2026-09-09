# 会话交接文档：exp413 策略稳健性校验（第十四轮）

> 生成时间：2026-09-09
> 交接对象：下一轮对话 agent
> 会话完成度：✅ 全部6模块完成，本地 commit 已就绪，**仅 GitHub push 待手动**
> 核心交付：exp412 策略稳健性校验 + 交易逻辑溯源 + 未来函数排查

---

## 0. 一句话状态

**任务已完成**：exp413 对 exp412 V4 策略做6模块稳健性校验。结论：**参数非单点过拟合（参数高原宽缓）、成本耐受边界>0.4%/边（未失效）、震荡段是收益短板、三层逻辑完整可溯源、不存在未来函数/前视偏差（四重校验全通过）**。本地 commit 就绪，唯一遗留：**GitHub push 待用户手动**。

---

## 1. 本轮做了什么

1. **可参数化引擎**：把 exp412 风控引擎的 M2回撤(dd_recover/warn/hard)、M3波动(vol_upper/scale)、COST 参数外提为可注入参数，底层信号引擎完全复用 exp409/410/411，零改动
2. **模块1 参数扰动扫描**：M2 三参数 3×3×3=27组 + M3 两参数 3×2=6组，输出年化/夏普/回撤/Calmar，判定断崖恶化
3. **模块2 成本敏感性**：单边成本 0~0.4%（6档），观察组合与HAM子策略对滑点的耐受度
4. **模块3 情景压力拆分**：按 market_regime 拆分上涨/下跌/震荡三段，输出各行情年化/夏普/回撤/交易次数
5. **模块4 HAM尾部风险**：统计HAM子策略40笔单笔盈亏分布（最大单笔亏损、分位数等）
6. **模块5 逻辑溯源**：三层架构（因子信号→组合合成→账户风控）伪代码，逐条说明多空/开仓/平仓/仓位判定，区分因子信号vs账户风控
7. **模块6 未来函数排查**：四重校验（V1 min_periods静态核对 / V2 shift-equivariance动态校验 / V3 净值时序 / V4 暖机期），结论无前视偏差
8. 完整报告 + 交接文档 + 结果CSV + git commit

**底层零改动承诺已履行**：完全复用 exp409/410/411 的信号与交易引擎函数，未修改任何信号逻辑、阈值、交易条件。exp413 仅把 exp412 风控参数外提。

---

## 2. 核心结果速查

### 4个核心问题回答

| # | 问题 | 结论 |
|---|------|------|
| ① | 参数单点过拟合？ | **否。** M2/M3扰动后Calmar始终≥最优65%，无断崖。V4最优位于宽缓参数高原。 |
| ② | 成本耐受边界？ | **>0.4%/边（未触达）。** 组合年化0.921→0.532(0→0.4%)，仍为正；HAM 1.072→0.667仍为正。 |
| ③ | 行情短板？ | **震荡段收益低（年化0.506）。** 趋势段强（夏普3.57），震荡段73%天数拖累收益。 |
| ④ | 逻辑完整+未来函数？ | **逻辑完整可溯源（§6）；无前视偏差（§7四重校验全通过，信号层shift匹配率1.000000）。** |

### 关键数值

| 指标 | exp412 V4基准 | 模块1最差 | 模块2@0.4% | 模块3震荡段 |
|------|:---:|:---:|:---:|:---:|
| 年化 | 0.761 | 0.653 | 0.532 | 0.506 |
| 夏普 | 2.638 | 2.396 | 2.094 | 2.426 |
| 最大回撤 | -0.116 | -0.142 | -0.128 | -0.089 |
| Calmar | 6.584 | 4.880 | 4.163 | — |

### 模块1 参数扫描统计

| 扫描 | Calmar min/mean/max | 年化 min/max | 回撤范围 | 断崖? |
|------|:---:|:---:|:---:|:---:|
| M2 (27组) | 5.331/6.268/6.922 | 0.681/0.800 | -0.116~-0.128 | 否 ✓ |
| M3 (6组) | 4.880/5.921/6.779 | 0.653/0.736 | -0.109~-0.142 | 否 ✓ |

### 模块4 HAM尾部风险

| 指标 | 数值 |
|------|:---:|
| 最大单笔盈利 | +22.34% |
| 最大单笔亏损 | -8.58% |
| 平均盈利/亏损 | +5.15% / -1.72% |
| 盈亏比 | 2.989 |
| 胜率 | 40.0% |
| 账户最大回撤 | -11.6% |

---

## 3. 关键方法论沉淀

### 3.1 ⚠️ 参数高原 ≠ 孤立尖峰（最重要的稳健性判据）

exp412 V4 最优参数（dd_recover=0.05, dd_warn=0.12, dd_hard=0.18, vol_upper=0.55, vol_scale=0.50）位于一个**宽缓的参数高原**：
- M2 扫描发现 `recover=0.05, warn=0.12/0.14, hard=0.16/0.18/0.20` 多组并列最优 Calmar 6.584
- M3 扫描发现 `vol_upper=0.55, vol_scale=0.40`（更激进）Calmar 6.779 **略优于** V4 最优
- 最差不低于最优的 65%（M3）/ 77%（M2），远低于「断崖」阈值（50%）

**教训**：参数网格搜索后必须做局部扰动扫描验证是否为孤立尖峰。若最优参数小幅变动就断崖恶化（最差<最优50%），说明过拟合。本次 exp412 通过此检验。

### 3.2 成本敏感性近似线性衰减

成本从 0 到 0.4%，组合年化线性衰减约 -0.045/0.05百分点成本。HAM 比组合更敏感（HAM -1.04/百分点 vs 组合 -0.90），因HAM持有短（4.5天）换手成本高。回撤对成本不敏感（-0.106→-0.128）。**成本不是该策略的失效边界。**

### 3.3 风控在趋势段是纯增益，在震荡段是必要代价

- 上涨段：exp412 夏普 3.574 > 基线 3.230，回撤 -0.123 < -0.189（-35%）→ **纯增益**
- 下跌段：风控几乎无影响（年化/回撤与基线一致）
- 震荡段：exp412 年化 0.506 < 基线 0.697（-27%），回撤 -0.089 < -0.112（-20%）→ **必要代价**

**教训**：风控的收益-风险权衡在不同行情下不同，趋势环境风控是白赚，震荡环境是代价。评估风控效果需分行情看。

### 3.4 ⚠️ shift-equivariance 是验证无前视的最硬动态证据

模块6 V2 校验：把信号整体 shift(1)，隔离风控后检验 `pos_shifted[t] == pos_orig[t-1]`，匹配率 **1.000000（完美）**。这是比静态核对更强有力的动态证据——若引擎用了未来数据，shift 后不会完美平移。**推荐作为所有滚动窗口策略的无前视验证方法。**

### 3.5 回撤两种口径的差异

- **全时段净值最大回撤**（`max_drawdown_full`）：全628日净值峰谷最大跌幅，exp412 = -0.116。本报告主用。
- **同日路径回撤**：引擎内 equity[T-1] 路径计算的日内回撤，exp412 = -0.131。
- 差异源于：全时段口径在空仓日计入0收益，平滑峰谷。**跨实验对比必须统一口径**，否则数字不可比。

---

## 4. 文件与路径清单

### 产物（本轮新增）

| 用途 | 路径 |
|------|------|
| 参数化引擎 | `model_ham/exp413_robustness/exp413_engine.py` |
| 模块1-4 运行脚本 | `model_ham/exp413_robustness/exp413_run.py` |
| 模块6 排查脚本 | `model_ham/exp413_robustness/exp413_lookahead_check.py` |
| 主报告 | `reports/exp413_robustness_check.md` |
| 参数扫描CSV | `model_ham/exp413_robustness/data/exp413_param_scan.csv` |
| 成本敏感性CSV | `model_ham/exp413_robustness/data/exp413_cost_sensitivity.csv` |
| 情景压力CSV | `model_ham/exp413_robustness/data/exp413_regime_stress.csv` |
| HAM尾部风险CSV | `model_ham/exp413_robustness/data/exp413_ham_tail_risk.csv` |
| 净值序列CSV | `model_ham/exp413_robustness/data/exp413_daily_equity.csv` |
| 风控交易日志 | `model_ham/exp413_robustness/data/exp413_risk_trades.csv` |
| HAM交易日志 | `model_ham/exp413_robustness/data/exp413_ham_trades.csv` |
| 前视排查JSON | `model_ham/exp413_robustness/data/exp413_lookahead_check.json` |
| 汇总JSON | `model_ham/exp413_robustness/data/exp413_summary.json` |

### 数据源（只读，完全复用未改）

| 数据 | 路径 |
|------|------|
| HAM引擎 | `model_ham/exp409_ham_dynamic/exp409_ham_dynamic.py`（被import复用）|
| 基本面引擎 | `model_ham/exp410_fund_dynamic/exp410_fund_dynamic.py`（被import复用）|
| exp411组合引擎 | `model_ham/exp411_combined/exp411_combined.py`（被import复用）|
| exp412风控引擎 | `model_ham/exp412_risk_control/exp412_risk_control.py`（被参考，exp413参数化版重构）|

---

## 5. 复现命令

```bash
cd /home/ubuntu/lithium-engine
# 引擎用 /usr/bin/python3（pandas 3.0.3）
# 前置（信号引擎，~3s 各，import安全不触发__main__）
/usr/bin/python3 model_ham/exp409_ham_dynamic/exp409_ham_dynamic.py     # ~3s
/usr/bin/python3 model_ham/exp410_fund_dynamic/exp410_fund_dynamic.py    # ~3s
/usr/bin/python3 model_ham/exp411_combined/exp411_combined.py            # ~5s
# exp413 主实验
/usr/bin/python3 model_ham/exp413_robustness/exp413_run.py               # ~15s 模块1-4
/usr/bin/python3 model_ham/exp413_robustness/exp413_lookahead_check.py   # ~10s 模块6
```

**坑点**：
- exp413 通过 sys.path.insert import exp409/410/411 模块，**__main__不触发（import安全）**
- pandas 3.0.3 的 `pd.concat` 对 DatetimeIndex 排序报 Pandas4Warning，无害可忽略
- LSP 报 pandas import 误报 + `__getitem__` 误报（Pyright 类型推断问题），忽略（环境实际有 pandas，运行正常）
- `df[df["col"].idxmax()][other_col]` 在 pandas 3 报 KeyError（idxmax返回positional index），改用 `df.loc[df["col"].idxmax(), other_col]`
- git push 网络到 github.com:443 可能不通，需用户手动或有网络时推

---

## 6. 遗留待办（下一轮优先）

### P0：GitHub push（唯一硬阻塞）

```bash
cd /home/ubuntu/lithium-engine && git pull --no-rebase --no-edit && git push origin main
```

本地 commit 已含全部产物，只欠 push。注意：push 前先 pull 避免 non-fast-forward。

### P1：可继续优化方向

1. **样本外验证**：exp412/exp413 参数在 2024-02~2026-09 优化，需 2026 Q4+ 样本外测试（exp412遗留，exp413进一步确认无过拟合但样本外仍需验证）
2. **HAM 单笔尾部止损**：模块4发现HAM最大单笔亏损-8.58%，全局M2回撤风控是「事后降仓」非「事前止损」。可给HAM加单笔-5%止损测试是否改善尾部
3. **震荡段增强**：震荡段73%天数是收益短板（年化0.506）。可引入震荡区间方向信号或降低震荡段换手
4. **M3更激进参数**：模块1发现 vol_scale=0.40（比V4的0.50更激进）Calmar略优(6.779>6.584)。可测试 vol_upper=0.50~0.55 × vol_scale=0.30~0.40 组合
5. **成本极端阈值**：模块2在0~0.4%未失效，可测试到0.6%+找真正失效点（但超出常规期货滑点范围）
6. **多品种外推**：HAM动力学+基本面动态+组合+风控框架可推广到镍/锌，但需独立验证

---

## 7. 用户偏好备忘

- 微信/飞书偏好简洁直接，任务路径清晰时一口气执行到底再汇总
- 数据敏感，网页需反拷贝保护
- 看板风格：Dark ECharts 高密度独立页；导出报告偏好横版A4每页一图
- 跨agent协作给出可复制提示词卡（单个fenced code block）
- 看板/前端链接必须给 GitHub Pages .github.io 域名，禁止给 IP
- 每次回复末尾标注当前估算上下文占用百分比

---

*生成时间: 2026-09-09 | 第十四轮会话交接 | exp413稳健性校验 | 本地commit就绪, push待手动*
