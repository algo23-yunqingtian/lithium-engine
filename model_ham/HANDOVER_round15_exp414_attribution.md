# 会话交接文档：exp414 HAM信号消融与组合归因（第十五轮）

> 生成时间：2026-09-09
> 交接对象：下一轮对话 agent
> 会话完成度：✅ 模块1/2 全部完成，主报告 + 交接文档 + 全部 CSV 已就绪，本地 commit 已就绪，**仅 GitHub push 待手动**
> 核心交付：HAM 信号独立 Alpha 量化 + 组合层 HAM 贡献占比 + 执行规则 vs 信号增益分解

---

## 0. 一句话状态

**任务已完成**：exp414 对 exp412 组合做消融归因。**结论：HAM 信号提供强且独立的 Alpha，是组合收益的绝对主力（贡献年化 89%、夏普 69%、Calmar 84%）。随机噪声替换 HAM 信号后，策略由年化 +91% 退化为平均 -6.5%（亏损），30 个随机种子 0/30 击败原版——统计学决定性证据。动态交易框架本身无 Alpha，价值仅在纪律化执行与风控降风险。** 本地 commit 就绪，唯一遗留：**GitHub push 待用户手动**。

---

## 1. 本轮做了什么

1. **零改动引擎**：exp414 完全复用 exp409/410/411/413 引擎函数，不修改任何信号逻辑、阈值、交易条件、风控参数。仅做两类纯净操作：模块1 替换输入列 `disagreement`，模块2 传 `h_target=0` 剔除 HAM。
2. **模块1 HAM 信号消融**：A 组=exp409 原版，B 组=同频率同分布高斯噪声替换 `disagreement`（30 随机种子），输出年化/夏普/回撤/Calmar/盈亏比/交易次数 + 显著性。
3. **模块2 组合消融**：T1=exp412 完整组合，T2=剔 HAM 仅基本面+全套风控，计算 HAM 对组合年化/夏普/Calmar 的贡献占比。
4. **归因分解**：区分"信号本身增益"（模块1 A vs B）vs"交易执行规则优化"（模块1 A vs 模块2 T1 的风控回撤收窄）。
5. **exp413 短板复盘**：事后风控、震荡行情拖累两大短板 + exp414 新证据，列出 6 个优化方向。
6. 完整报告 + 交接文档 + 7 个 CSV/JSON + git commit。

**底层零改动承诺已履行**：复现校验 `T1_matches_exp413_baseline = True`（年化偏差 < 1e-5，Calmar 偏差 < 1e-3）。T1 指标与 exp413 报告逐位吻合。

---

## 2. 核心结果速查

### 4 个核心问题回答

| # | 问题 | 结论 |
|---|------|------|
| ① | 超额收益来源？ | **HAM 系统失衡信号**，是唯一独立 Alpha 来源。 |
| ② | HAM 信号是否提供独立 Alpha？ | **是，决定性。** 同框架下真实信号 vs 随机噪声差 +97.5pp 年化，0/30 随机种子击败原版。 |
| ③ | 信号增益 vs 执行规则？ | 信号是收益来源（+0.975 年化）；执行规则不创造收益，只管理风险（回撤 -0.154→-0.116，Calmar +12%）。 |
| ④ | HAM 对组合贡献？ | 年化 89%、夏普 69%、Calmar 84%。基本面是"风险稳定器"非收益引擎。 |

### 关键数值

| 组别 | 年化 | 夏普 | 最大回撤 | Calmar | 盈亏比 | 交易次数 |
|------|:---:|:---:|:---:|:---:|:---:|:---:|
| 模块1 A 组（原版） | **0.910** | **2.598** | -0.154 | **5.902** | 2.989 | 40 |
| 模块1 B 组（噪声均值） | -0.065 | -0.185 | -0.431 | -0.063 | 1.004 | 40.3 |
| 模块1 B 组（最好种子） | 0.345 | 1.222 | -0.207 | 1.159 | 1.223 | 38 |
| 模块2 T1（完整组合） | 0.761 | 2.638 | -0.116 | 6.584 | 2.927 | 40 |
| 模块2 T2（剔 HAM） | 0.083 | 0.816 | -0.080 | 1.037 | 11.202 | 7 |

### 超额与贡献量化

| 维度 | 数值 |
|------|:---:|
| HAM 信号超额年化（A - B均值） | **+0.975（+97.5pp）** |
| HAM 信号超额夏普 | +2.782 |
| HAM 信号超额 Calmar | +5.965 |
| 随机种子击败 A 组年化比例 | **0/30（0%）** |
| HAM 对组合年化贡献 | **89.0%** |
| HAM 对组合夏普贡献 | 69.1% |
| HAM 对组合 Calmar 贡献 | 84.2% |

---

## 3. 关键方法论沉淀

### 3.1 ⚠️ 信号消融的 NaN 陷阱（本轮最重要教训）

`disagreement` 含 NaN（180 日滚动 rank 暖机期，661/720 有效）。**错误做法**：对全列 `np.mean/np.std` 得 NaN → `np.random.normal(loc=NaN)` 生成全 NaN 噪声 → 30 种子退化为**恒等结果**（年化全部 -0.413），假阴性。

**正确做法**（exp414 已采用并验证）：
```python
valid_mask = ~np.isnan(orig_dis)
dis_mean = orig_dis[valid_mask].mean()      # 用有效值统计
dis_std  = orig_dis[valid_mask].std()
for seed in seeds:
    noise = orig_dis.copy()
    noise[valid_mask] = rs.normal(dis_mean, dis_std, size=valid_mask.sum())  # 只随机化有效值
    noise[~valid_mask] = np.nan             # NaN 位置保持 NaN
```
**必做健全性检查**：断言种子间年化值互不相同（本轮验证 30 个唯一值，交易数 37~42 合理波动），否则说明设计退化。

### 3.2 框架 vs 信号的隔离设计

固定框架（双信号共振开仓、4 条平仓规则、动态仓位、最大持仓 12 日）、只换信号输入，才能量化"信号独立 Alpha"。随机噪声基线的收益 ≈ 框架+成本的净效应（此处为负，因 ~40 笔高换手被成本拖成 -6.5%），真实信号相对它的超额 = 纯信号增益。

### 3.3 贡献占比的样本限制

模块2 T2 仅 7 笔交易，样本小。贡献占比结论需注明样本限制——T2 的高盈亏比（11.2）是低样本假象，不能据此认为基本面盈亏结构优于 HAM。基本面真实定位：**低波动、低换手、低贡献的风险稳定器**。

### 3.4 零改动的复现校验

消融实验先验证对照组（T1）与历史基准逐位吻合，才能信任差异归因。本轮 T1 年化 0.761/夏普 2.638/回撤 -0.116/Calmar 6.584/40 笔，与 exp413 报告完全一致，程序化标志 `T1_matches_exp413_baseline=True`。

---

## 4. 文件与路径清单

### 产物（本轮新增）

| 用途 | 路径 |
|------|------|
| 零改动引擎 | `model_ham/exp414_attribution/exp414_engine.py` |
| 运行脚本 | `model_ham/exp414_attribution/exp414_run.py` |
| 主报告 | `reports/exp414_attribution.md` |
| 模块1 A/B 主结果 CSV | `model_ham/exp414_attribution/data/exp414_module1_ham_ablation.csv` |
| 模块1 30 噪声种子明细 | `model_ham/exp414_attribution/data/exp414_module1_noise_seeds.csv` |
| 模块2 T1/T2 结果 | `model_ham/exp414_attribution/data/exp414_module2_portfolio_ablation.csv` |
| T1/T2 净值+仓位 | `model_ham/exp414_attribution/data/exp414_daily_equity.csv` |
| T1 交易日志 | `model_ham/exp414_attribution/data/exp414_T1_trades.csv` |
| T2 交易日志 | `model_ham/exp414_attribution/data/exp414_T2_trades.csv` |
| 汇总 JSON | `model_ham/exp414_attribution/data/exp414_summary.json` |
| 交接文档 | `model_ham/HANDOVER_round15_exp414_attribution.md` |

### 数据源（只读，完全复用未改）

| 数据 | 路径 |
|------|------|
| HAM 引擎 | `model_ham/exp409_ham_dynamic/exp409_ham_dynamic.py`（被 import 复用）|
| 基本面引擎 | `model_ham/exp410_fund_dynamic/exp410_fund_dynamic.py`（被 import 复用）|
| 组合目标构建 | `model_ham/exp411_combined/exp411_combined.py`（build_targets/price_series/extract_trades 复用）|
| 风控引擎 | `model_ham/exp413_robustness/exp413_engine.py`（run_exp412_param 复用，V4 参数固定）|

---

## 5. 复现命令

```bash
cd /home/ubuntu/lithium-engine
# 引擎用 /usr/bin/python3（pandas 3.0.3）
# 信号引擎 import 安全（__main__ 不触发），无需单独运行
# exp414 主实验（约 20s，含 30 噪声种子）
/usr/bin/python3 model_ham/exp414_attribution/exp414_run.py
# 仅引擎自检（快速，约 5s）
/usr/bin/python3 model_ham/exp414_attribution/exp414_engine.py
```

**坑点**：
- exp414 通过 sys.path.insert import exp409/410/411/413 模块，**__main__ 不触发（import 安全）**。
- Pyright 报 pandas/exp4xx import 误报（reportMissingImports / reportAttributeAccessIssue），**全部忽略**——环境实际有 pandas，运行正常。这是 Pyright 无法解析 sys.path.insert 动态导入的已知误报。
- pandas 3.0.3 的 `pd.concat` 对 DatetimeIndex 排序报 Pandas4Warning，无害可忽略。
- **噪声消融的 NaN 陷阱见 §3.1**——这是本轮唯一真实代码 bug，已修复并验证。下一轮若复用 exp414 消融范式到其他信号列，务必先检查该列是否含 NaN。
- git push 网络到 github.com:443 可能不通，需用户手动或有网络时推。

---

## 6. 遗留待办（下一轮优先）

### P0：GitHub push（唯一硬阻塞）

```bash
cd /home/ubuntu/lithium-engine && git pull --no-rebase --no-edit && git push origin main
```

本地 commit 已含全部产物，只欠 push。push 前先 pull 避免 non-fast-forward。

### P1：可继续优化方向（exp414 衍生，详见报告 §5）

1. **HAM 单笔事前止损**：加 -5% 单笔止损，测试削平 -8.58% 尾部（exp413 P1 延续，exp414 提供 A 组回撤 -0.154 的新证据）。
2. **前视化风控**：M2 从"已实现回撤降仓"改为"预期回撤预警"（基于持仓 realized vol 预测未来回撤，事前降仓）。
3. **震荡段换手抑制**：识别 range regime 时跳过弱信号/降低开仓阈值，减少无信号高频换手的成本拖累（exp414 B 组噪声在震荡段纯成本损耗的证据）。
4. **震荡段方向信号 / regime 切换组合**：震荡段用基本面信号（低频长持仓更稳健）、趋势段用 HAM，测试 regime 切换。
5. **HAM 信号强度连续仓位映射**：当前仅 60%/100% 两档，可测试 deviation 连续值平滑映射。
6. **基本面重新定位为风险对冲腿**：按波动率反向配置权重，从"收益子策略"改为"波动稳定器"。
7. **样本外验证**：所有结论基于 2024-02~2026-09，需 2026 Q4+ 样本外验证（exp412/413/414 共同遗留）。

---

## 7. 用户偏好备忘

- 微信/飞书偏好简洁直接，任务路径清晰时一口气执行到底再汇总。
- 数据敏感，网页需反拷贝保护。
- 看板风格：Dark ECharts 高密度独立页；导出报告偏好横版 A4 每页一图。
- 跨 agent 协作给出可复制提示词卡（单个 fenced code block）。
- 看板/前端链接必须给 GitHub Pages .github.io 域名，禁止给 IP。
- 每次回复末尾标注当前估算上下文占用百分比。

---

*生成时间：2026-09-09 | 第十五轮会话交接 | exp414 消融归因 | 底层零改动，复现校验通过，本地 commit 就绪，push 待手动*
