# 碳酸锂 HAM 双主体异质 Agent 模型

> 版本：v1.0 | 创建：2026-09-08

## 模型概述

基于 **Brock-Hommes** 框架的两主体异质 Agent 模型（Heterogeneous-Agent Model），用于碳酸锂期货：

- **产业基本面 Agent**（Agent F）：锚定供需公允价格 P_fund，向公允价值回归
- **投机趋势 Agent**（Agent C）：基于价格动量追涨杀跌

策略切换机制：两类 Agent 根据历史滚动收益动态调整市场占比（Logit 权重）。

## 输出因子

| 因子 | 说明 | 核心用法 |
|------|------|----------|
| `n_c` | 投机 Agent 市场占比 | 越高 → 波动率越大 → 行情脱离基本面 |
| `Total_Demand` | 聚合超额需求 | 正→多头压力，负→空头压力 |
| `n_f - n_c` | 产业-投机力量分歧 | 正→产业主导，负→投机主导 |

## 目录结构

```
lithium_ham_two_agents/
├── raw_data/              # 原始输入数据（不修改）
│   ├── lithium_future.csv     # LC0 日频价格
│   ├── fundamental_balance.csv # SMM 周频基本面（需前向填充）
│   ├── spot_price.csv         # 现货价格+基差
│   ├── smm_daily.csv          # SMM 日频现货
│   └── readme.md              # 数据来源说明
├── intermediate/          # 中间计算产物
│   └── processed_input.csv
├── output_factors/        # 模型输出因子
│   └── factor_α0.3_β0.5_γ2_W20.csv
├── backtest_result/       # 回测输出
│   ├── ic_*.csv             # IC 序列
│   ├── group_*.csv          # 分层回测
│   ├── param_sensitivity_scan.csv
│   └── report.md            # 回测报告
├── src/                   # 源代码
│   ├── data_preprocess.py   # 数据预处理
│   ├── ham_model.py         # HAM 核心模型
│   ├── backtest.py          # 回测与因子评价
│   └── main_run.py          # 主运行脚本
├── requirements.txt
└── readme.md              # 本文件
```

## 参数

| 参数 | 含义 | 范围 | 默认 |
|------|------|------|------|
| α | 基本面回归强度 | [0.1-0.8] | 0.3 |
| β | 趋势外推强度 | [0.2-1.5] | 0.5 |
| γ | 策略切换灵敏度 | [1-5] | 2 |
| W | 滚动收益窗口 | [20,40,60] | 20 |

## 运行

```bash
cd lithium_ham_two_agents
python src/main_run.py
```

## 时间切分

- 训练集：最早 50%（参数扫描用）
- 验证集：中间 20%（初步筛选）
- 测试集：最后 30%（样本外评价，不参与参数选择）

## 模型边界

- ✅ 识别投机力量占比变化、市场博弈结构
- ❌ 精准预测每日价格涨跌点位
- ⚠️ 禁止单独用因子做买卖信号，应作为 ML 模型输入特征
