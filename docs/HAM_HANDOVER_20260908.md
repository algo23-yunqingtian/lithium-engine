# 碳酸锂 HAM 双主体模型 — 交接文档

> 日期：2026-09-08
> 状态：✅ 全部完成，已推送 GitHub，本地可清理

---

## 一、项目概述

基于 **Brock-Hommes** 框架的碳酸锂期货两主体异质 Agent 模型（Heterogeneous-Agent Model），输出可回测量化因子。

- **观测模式**：外部输入真实行情，Agent 不闭环内生生成价格
- **因子定位**：作为 LightGBM 等 ML 模型输入特征，禁止单独做买卖信号
- **不改动数学内核**：D_f、D_c、Logit 策略切换公式保持不变

### 两个主体

| 主体 | 现实角色 | 需求函数 | 核心逻辑 |
|------|----------|----------|----------|
| 产业基本面 Agent | 锂矿商、冶炼厂、贸易商、正极厂 | D_f(t) = α × (P_fund − P) | 价格偏离公允价→回归 |
| 投机趋势 Agent | CTA、游资、散户 | D_c(t) = β × (P − P_{t-1}) | 动量追涨杀跌 |

### 三个输出因子

| 因子 | 含义 | 方向 |
|------|------|------|
| n_c | 投机 Agent 市场占比 | 越高→波动率越大→脱离基本面 |
| Total_Demand | 聚合超额需求 | 正→多头压力，负→空头压力 |
| n_f − n_c | 产业-投机力量分歧 | 正→产业主导，负→投机主导 |

---

## 二、GitHub 仓库（全部代码和数据都在这里）

**主仓库**：<https://github.com/algo23-yunqingtian/lithium-engine>

### model_ham/ 目录结构

```
lithium-engine/model_ham/
├── README.md                        # 项目说明
├── requirements.txt                 # 依赖
├── run_all.py                       # 完整流程编排脚本
├── src/（源码，4个模块）
│   ├── data_preprocess.py           # 数据预处理 + P_fund 计算
│   ├── ham_model.py                 # HAM 核心模型
│   ├── backtest.py                  # 回测与因子评价
│   └── main_run.py                  # 主运行脚本
├── raw_data/                        # 原始输入数据
│   ├── lithium_future.csv           # LC0 日频价格（760天）
│   ├── fundamental_balance.csv      # SMM 周频基本面（305周）
│   ├── spot_price.csv               # 现货价格+基差（732天）
│   ├── smm_daily.csv                # SMM 日频现货（4760天）
│   └── readme.md                    # 数据来源说明
├── output_factors/
│   └── ham_factors_full_800d.csv    # 完整740天全量因子
├── backtest_result/
│   ├── check_result.md              # 5项正确性校验报告
│   ├── report.md                    # 回测报告（样本外）
│   ├── param_sensitivity_scan.csv   # 192组合参数扫描
│   └── model_compare.md             # 双主体 vs 7主体对比
├── backtest_ic/                     # IC 时间序列
│   ├── ic_Total_Demand.csv
│   ├── ic_n_c.csv
│   └── ic_n_f_minus_n_c.csv
└── sample_factor.csv                # 样例因子输出
```

**总计 21 个文件，547 KB**

### 前端可视化

**页面**：<https://algo23-yunqingtian.github.io/lithium-dashboard/pages/ham_model.html>

**仓库**：<https://github.com/algo23-yunqingtian/lithium-dashboard>

---

## 三、回测结果

### 数据范围
- LC0 期货：2023-07-21 ~ 2026-09-07（760 交易日，有效 740 天）
- SMM 基本面：2023-06-01 起（周频→日频前向填充）
- 已接近 800 天上限，受限于原始数据源无法进一步扩展

### 样本外测试集（228 天）

| 因子 | IC均值 | ICIR | p-value | RankIC均值 | RankICIR |
|------|--------|------|---------|------------|----------|
| Total_Demand | -0.0816 | -0.448 | <0.0001 | -0.1185 | -0.531 |
| n_c | -0.0134 | -0.082 | 0.2609 | -0.0273 | -0.146 |
| n_f-n_c | 0.0134 | 0.082 | 0.2609 | 0.0273 | 0.146 |

### 参数稳健性（192 组合全网格扫描）
- n_c: **192/192 全部 RankIC 为负**（方向完全稳定）
- n_f-n_c: **192/192 全部 RankIC 为正**（方向完全稳定）
- Total_Demand: 128 负 / 64 正（样本外 ICIR 仍显著）

### 正确性校验（5 项全部通过）
1. ✅ 收益率数据源：LC0 主力连续合约
2. ✅ P_fund 预处理：前向填充，无未来函数
3. ✅ Logit exp 数值稳定：截断 + log-sum-exp
4. ✅ 数据集切分：时间顺序，测试集不参与调参
5. ✅ 参数扫描：方向一致

---

## 四、双主体 vs 7主体对比

| 维度 | 双主体 HAM | 7主体 ABM |
|------|-----------|----------|
| 参数数量 | 4（可调） | 50+（难调） |
| 因子可复现性 | 高（确定性公式） | 中（依赖DB状态） |
| 过拟合风险 | 低 | 中 |
| ML 特征输入 | ✅ 首选 | 备选 |
| 博弈结构分析 | 备选 | ✅ 首选 |

**结论**：两者互补不替代。

---

## 五、运行方式

```bash
# Clone 仓库
git clone https://github.com/algo23-yunqingtian/lithium-engine.git
cd lithium-engine/model_ham

# 安装依赖
pip install -r requirements.txt

# 运行完整流程（校验 + 因子 + 回测 + 对比）
python run_all.py

# 或仅运行主流程
python src/main_run.py
```

---

## 六、关键网址汇总

| 用途 | 地址 |
|------|------|
| HAM 源码 | <https://github.com/algo23-yunqingtian/lithium-engine/tree/main/model_ham> |
| HAM 前端可视化 | <https://algo23-yunqingtian.github.io/lithium-dashboard/pages/ham_model.html> |
| 完整因子 CSV | <https://github.com/algo23-yunqingtian/lithium-engine/blob/main/model_ham/output_factors/ham_factors_full_800d.csv> |
| 校验报告 | <https://github.com/algo23-yunqingtian/lithium-engine/blob/main/model_ham/check_result.md> |
| 回测报告 | <https://github.com/algo23-yunqingtian/lithium-engine/blob/main/model_ham/report.md> |
| 对比报告 | <https://github.com/algo23-yunqingtian/lithium-engine/blob/main/model_ham/model_compare.md> |
| 前端仓库 | <https://github.com/algo23-yunqingtian/lithium-dashboard> |
| 导航主页 | <https://algo23-yunqingtian.github.io/lithium-dashboard/> |

---

## 七、腾讯云本地文件

以下内容**已完整同步到 GitHub**，腾讯云上的副本可以安全删除：

| 本地路径 | 说明 | GitHub 对应位置 |
|----------|------|----------------|
| `/home/ubuntu/lithium_ham_two_agents/` | 完整项目（608KB） | `model_ham/`（547KB） |

GitHub 上比本地多了一个 `backtest_ic/` 子目录和 `run_all.py`，内容更全。
本地独有的 `intermediate/processed_input.csv`（64KB）是中间产物，不需要保留。

**可以安全删除**：`rm -rf /home/ubuntu/lithium_ham_two_agents/`

---

*文档路径：`/home/ubuntu/lithium_calendar/docs/HAM_HANDOVER_20260908.md`*
