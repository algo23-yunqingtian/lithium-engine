# HAM因子 + 外部特征 LightGBM 回测报告

> 生成时间: 2026-09-08 19:05:30
> 模型: LightGBM Regressor
> 特征数: 17
> 有效样本: 624

## 1. 特征列表

共 17 个特征:

| # | 特征名 | 类别 |
|---|--------|------|
| 1 | n_c | HAM因子 |
| 2 | Total_Demand | HAM因子 |
| 3 | n_f_minus_n_c | HAM因子 |
| 4 | basis | 量价 |
| 5 | basis_rate | 量价 |
| 6 | spot_avg | 量价 |
| 7 | volume | 量价 |
| 8 | position | 量价 |
| 9 | ret_5d | 动量 |
| 10 | ret_10d | 动量 |
| 11 | ret_20d | 动量 |
| 12 | zhiji_wh_receipt | 仓单库存 |
| 13 | zhiji_social_inv | 社会库存 |
| 14 | zhiji_inv_jx | 库存-分地区 |
| 15 | zhiji_inv_qh | 库存-分地区 |
| 16 | zhiji_inv_sc | 库存-分地区 |
| 17 | zhiji_recycle_inv | 回收料库存 |

## 2. 时间切分

| 集合 | 样本数 | 时间范围 |
|------|--------|----------|
| 训练集 | 374 (60%) | 2024-01-31 ~ 2025-08-19 |
| 验证集 | 125 (20%) | 2025-08-20 ~ 2026-03-02 |
| 测试集 | 125 (20%) | 2026-03-03 ~ 2026-08-31 |

**原则**: 严格时间顺序，不shuffle，无重叠

## 3. 回测结果

| 标签 | 训练样本 | 测试样本 | 验证RMSE | 验证MAE | 测试RMSE | 测试MAE | 方向准确率 |
|------|----------|----------|----------|---------|----------|---------|-----------|
| 1d | 374 | 125 | 0.042439 | 0.029622 | 0.029076 | 0.023142 | 44.00% |
| 3d | 374 | 125 | 0.073005 | 0.056256 | 0.049654 | 0.041335 | 48.80% |
| 5d | 374 | 125 | 0.095157 | 0.073212 | 0.063091 | 0.051124 | 49.60% |

## 4. 特征重要性 (5d模型)

| # | 特征 | 重要性 |
|---|------|--------|
| 1 | zhiji_wh_receipt | 7 |
| 2 | volume | 4 |
| 3 | basis | 3 |
| 4 | spot_avg | 3 |
| 5 | ret_20d | 2 |
| 6 | basis_rate | 1 |
| 7 | position | 1 |
| 8 | ret_10d | 1 |
| 9 | n_c | 0 |
| 10 | Total_Demand | 0 |
| 11 | n_f_minus_n_c | 0 |
| 12 | ret_5d | 0 |
| 13 | zhiji_social_inv | 0 |
| 14 | zhiji_inv_jx | 0 |
| 15 | zhiji_inv_qh | 0 |
| 16 | zhiji_inv_sc | 0 |
| 17 | zhiji_recycle_inv | 0 |

## 5. 模型参数

```json
{
  "objective": "regression",
  "learning_rate": 0.05,
  "num_leaves": 31,
  "max_depth": 6,
  "min_child_samples": 20,
  "feature_fraction": 0.8,
  "bagging_fraction": 0.8,
  "bagging_freq": 1,
  "n_estimators": 200,
  "early_stopping_rounds": 20
}
```

## 6. 结论

- 最佳方向准确率: 5d 标签，49.60%
- 测试集RMSE范围: 0.029076 ~ 0.063091
- **结论: 模型未显著优于随机猜测**

## 7. 数据说明

### 外部特征覆盖率

| 特征 | 有效样本 | 覆盖率 |
|------|----------|--------|
| n_c | 624 | 100% |
| Total_Demand | 624 | 100% |
| n_f_minus_n_c | 624 | 100% |
| basis | 624 | 100% |
| basis_rate | 624 | 100% |
| spot_avg | 624 | 100% |
| volume | 624 | 100% |
| position | 624 | 100% |
| ret_5d | 624 | 100% |
| ret_10d | 624 | 100% |
| ret_20d | 624 | 100% |
| zhiji_wh_receipt | 622 | 99.7% |
| zhiji_social_inv | 624 | 100.0% |
| zhiji_inv_jx | 624 | 100.0% |
| zhiji_inv_qh | 624 | 100.0% |
| zhiji_inv_sc | 624 | 100.0% |
| zhiji_recycle_inv | 624 | 100.0% |
