# 消融对照实验报告（LightGBM 预测回测）

> 生成时间：2026-09-09

> **核心变化**：加入 spread_near1_near3 后重跑消融实验


## 1. 实验设置

- 预测目标：未来 1 日涨跌方向（二分类）
- 训练集：前 75% (399 行)
- 测试集：后 25% (134 行)
- 模型：LightGBM (n_estimators=100, lr=0.05, num_leaves=15)
- 指标：RMSE（概率误差）、方向准确率

## 2. 四组对照

| 组别 | 特征 | 特征数 | RMSE | 方向准确率 |
|------|------|--------|------|-----------|
| A | 基本面 | 5 | 0.5419 | 45.5% |
| B | HAM三因子 | 3 | 0.5053 | 54.5% |
| C | 基本面+HAM | 8 | 0.5380 | 47.8% |
| D | 基本面+HAM+state+交互 | 12 | 0.5374 | 48.5% |

## 3. 各特征重要性


### A: 基本面

- basis_spot_main: 222
- hv_20d: 180
- warehouse_stock: 156
- oi_main: 134
- spread_near1_near3: 102

### B: HAM三因子

- Total_Demand: 384
- n_f_minus_n_c: 27
- n_c: 25

### C: 基本面+HAM

- basis_spot_main: 219
- hv_20d: 190
- warehouse_stock: 148
- oi_main: 111
- spread_near1_near3: 84
- Total_Demand: 46
- n_f_minus_n_c: 6
- n_c: 2

### D: 基本面+HAM+state+交互

- basis_spot_main: 198
- hv_20d: 192
- warehouse_stock: 128
- oi_main: 125
- spread_near1_near3: 85
- Total_Demand: 30
- Total_Demand_x_state: 22
- n_f_minus_n_c_x_state: 6
- n_c: 3
- state_id: 3

## 4. 与上一轮对比

> 上一轮（无 spread_near1_near3）消融实验未执行（该轮任务要求仅做聚类+条件分析）
> 本次首次加入 spread_near1_near3 并完整执行消融对照

## 5. 局限性

1. **测试集样本较少**（约 134 行），统计波动大
2. **LightGBM 超参数未针对本轮数据调优**，沿用上一轮设置
3. **二分类任务**：预测涨跌方向，未做回归预测（价格点位）
4. **全部为样本内统计观察**，不代表未来预测有效性
5. **n_c / n_f_minus_n_c 唯一值过少**（3-4 个），作为特征信息量有限