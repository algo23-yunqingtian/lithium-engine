#!/usr/bin/env python3
"""
纯 HAM 因子消融实验
只用 3 个 HAM 因子 (n_c, Total_Demand, n_f-n_c) 跑三种模型：
1. LGBM 回归（与主线一致，基线对比）
2. LGBM 二分类（预测涨跌方向，非收益率值）
3. 线性回归（验证非线性是否带来增益）

目的：验证 HAM 因子有无独立预测力
"""

import csv
import math
import os
import sys
import time

import numpy as np
import lightgbm as lgb
from sklearn.linear_model import LinearRegression

ROOT = os.path.dirname(os.path.abspath(__file__))

# ============================================================
# 1. 读取 HAM 数据
# ============================================================
print("=" * 60)
print("STEP 1: 读取 HAM 因子数据")
print("=" * 60)

with open(os.path.join(ROOT, "ham_factors_full_800d.csv")) as f:
    reader = csv.DictReader(f)
    ham_data = list(reader)

n_total = len(ham_data)
dates = [r['date'] for r in ham_data]
closes = [float(r['close']) for r in ham_data]

print(f"总数据: {n_total} 行, {dates[0]} ~ {dates[-1]}")

# ============================================================
# 2. Winsorize (与主线一致: 1%-99%)
# ============================================================
print("\n" + "=" * 60)
print("STEP 2: Winsorize 缩尾 (1%-99%)")
print("=" * 60)

def winsorize(values, lower_pct=1, upper_pct=99):
    valid = [v for v in values if not math.isnan(v)]
    if not valid:
        return values
    sorted_v = sorted(valid)
    idx_lo = int(len(sorted_v) * lower_pct / 100)
    idx_hi = int(len(sorted_v) * upper_pct / 100)
    lo, hi = sorted_v[idx_lo], sorted_v[idx_hi]
    result = []
    for v in values:
        if math.isnan(v):
            result.append(v)
        elif v < lo:
            result.append(lo)
        elif v > hi:
            result.append(hi)
        else:
            result.append(v)
    return result

n_c = winsorize([float(r['n_c']) for r in ham_data])
td = winsorize([float(r['Total_Demand']) for r in ham_data])
nfnc = winsorize([float(r['n_f_minus_n_c']) for r in ham_data])

print(f"  n_c: mean={np.mean(n_c):.4f}, std={np.std(n_c):.4f}")
print(f"  Total_Demand: mean={np.mean(td):.4f}, std={np.std(td):.4f}")
print(f"  n_f-n_c: mean={np.mean(nfnc):.4f}, std={np.std(nfnc):.4f}")

# ============================================================
# 3. 构造标签
# ============================================================
print("\n" + "=" * 60)
print("STEP 3: 构造预测标签")
print("=" * 60)

labels = {}
for horizon in [1, 3, 5]:
    vals = []
    for i in range(n_total):
        if i + horizon < n_total:
            vals.append((closes[i + horizon] - closes[i]) / closes[i])
        else:
            vals.append(None)
    labels[f'label_ret_{horizon}d'] = vals
    valid = sum(1 for v in vals if v is not None)
    print(f"  label_ret_{horizon}d: {valid}/{n_total} 有效")

# ============================================================
# 4. 构建特征矩阵（仅 3 个 HAM 因子）
# ============================================================
print("\n" + "=" * 60)
print("STEP 4: 构建特征矩阵（仅 HAM 因子）")
print("=" * 60)

feature_names = ['n_c', 'Total_Demand', 'n_f_minus_n_c']

X = np.array([[n_c[i], td[i], nfnc[i]] for i in range(n_total)], dtype=np.float32)
X = np.nan_to_num(X, nan=0.0)

print(f"  特征矩阵: {X.shape}")
print(f"  特征: {feature_names}")

# ============================================================
# 5. 时间切分: 60% / 20% / 20%
# ============================================================
print("\n" + "=" * 60)
print("STEP 5: 时间切分")
print("=" * 60)

train_end = int(n_total * 0.6)
val_end = int(n_total * 0.8)

X_train, X_val, X_test = X[:train_end], X[train_end:val_end], X[val_end:]
train_dates, val_dates, test_dates = dates[:train_end], dates[train_end:val_end], dates[val_end:]

print(f"  训练集: {len(X_train)} 样本 ({train_dates[0]} ~ {train_dates[-1]})")
print(f"  验证集: {len(X_val)} 样本 ({val_dates[0]} ~ {val_dates[-1]})")
print(f"  测试集: {len(X_test)} 样本 ({test_dates[0]} ~ {test_dates[-1]})")

# ============================================================
# 6. 模型训练 + 评估
# ============================================================
print("\n" + "=" * 60)
print("STEP 6: 模型训练与评估")
print("=" * 60)

results = []

for horizon in [1, 3, 5]:
    y_all = np.array(labels[f'label_ret_{horizon}d'], dtype=np.float32)
    
    # 有效样本（标签非 None）
    mask_train = ~np.isnan(y_all[:train_end])
    mask_val = ~np.isnan(y_all[train_end:val_end])
    mask_test = ~np.isnan(y_all[val_end:])
    
    y_train = y_all[:train_end][mask_train]
    y_val = y_all[train_end:val_end][mask_val]
    y_test = y_all[val_end:][mask_test]
    
    X_train_m = X_train[mask_train]
    X_val_m = X_val[mask_val]
    X_test_m = X_test[mask_test]
    
    if len(X_train_m) < 50 or len(X_test_m) < 10:
        print(f"  [{horizon}d] 样本不足，跳过")
        continue
    
    print(f"\n{'='*60}")
    print(f"  {horizon}d 标签 — 有效样本: 训练{len(X_train_m)}, 验证{len(X_val_m)}, 测试{len(X_test_m)}")
    print(f"{'='*60}")
    
    # ---- 模型 A: LGBM 回归（基线，与主线一致） ----
    print(f"\n  --- 模型A: LGBM 回归 ---")
    params_a = {
        'objective': 'regression',
        'metric': 'rmse',
        'learning_rate': 0.05,
        'num_leaves': 7,       # 3个特征，树不能太深
        'max_depth': 3,
        'min_child_samples': 10,
        'feature_fraction': 1.0,  # 只有3个特征，全部使用
        'bagging_fraction': 0.8,
        'bagging_freq': 1,
        'verbose': -1,
        'n_estimators': 300,
        'random_state': 42,
        'lambda_l2': 1.0,  # 加强正则化
    }
    model_a = lgb.LGBMRegressor(**params_a)
    model_a.fit(
        X_train_m, y_train,
        eval_set=[(X_val_m, y_val)],
        callbacks=[lgb.early_stopping(30), lgb.log_evaluation(0)],
    )
    
    pred_test_a = model_a.predict(X_test_m)
    rmse_a = math.sqrt(np.mean((y_test - pred_test_a) ** 2))
    dir_acc_a = np.mean(np.sign(pred_test_a) == np.sign(y_test))
    
    print(f"    测试集 RMSE: {rmse_a:.6f}")
    print(f"    方向准确率: {dir_acc_a:.2%}")
    print(f"    实际迭代轮数: {model_a.best_iteration_}")
    
    feat_imp_a = model_a.feature_importances_
    print(f"    特征重要性: {dict(zip(feature_names, feat_imp_a))}")
    
    # ---- 模型 B: LGBM 二分类（涨跌方向） ----
    print(f"\n  --- 模型B: LGBM 二分类 ---")
    y_train_cls = (y_train > 0).astype(int)
    y_val_cls = (y_val > 0).astype(int)
    y_test_cls = (y_test > 0).astype(int)
    
    params_b = {
        'objective': 'binary',
        'metric': 'binary_logloss',
        'learning_rate': 0.05,
        'num_leaves': 7,
        'max_depth': 3,
        'min_child_samples': 10,
        'feature_fraction': 1.0,
        'bagging_fraction': 0.8,
        'bagging_freq': 1,
        'verbose': -1,
        'n_estimators': 300,
        'random_state': 42,
        'lambda_l2': 1.0,
    }
    model_b = lgb.LGBMClassifier(**params_b)
    model_b.fit(
        X_train_m, y_train_cls,
        eval_set=[(X_val_m, y_val_cls)],
        callbacks=[lgb.early_stopping(30), lgb.log_evaluation(0)],
    )
    
    pred_test_b = model_b.predict(X_test_m)
    dir_acc_b = np.mean(pred_test_b == y_test_cls)
    
    # 正例比例（基线：如果全预测多数类）
    pos_ratio = np.mean(y_test_cls)
    majority_acc = max(pos_ratio, 1 - pos_ratio)
    
    print(f"    测试集方向准确率: {dir_acc_b:.2%}")
    print(f"    多数类基线准确率: {majority_acc:.2%}")
    print(f"    实际迭代轮数: {model_b.best_iteration_}")
    
    feat_imp_b = model_b.feature_importances_
    print(f"    特征重要性: {dict(zip(feature_names, feat_imp_b))}")
    
    # ---- 模型 C: 线性回归（验证非线性） ----
    print(f"\n  --- 模型C: 线性回归 ---")
    lr_model = LinearRegression()
    lr_model.fit(X_train_m, y_train)
    
    pred_test_c = lr_model.predict(X_test_m)
    rmse_c = math.sqrt(np.mean((y_test - pred_test_c) ** 2))
    dir_acc_c = np.mean(np.sign(pred_test_c) == np.sign(y_test))
    
    print(f"    测试集 RMSE: {rmse_c:.6f}")
    print(f"    方向准确率: {dir_acc_c:.2%}")
    print(f"    系数: {dict(zip(feature_names, lr_model.coef_))}")
    print(f"    截距: {lr_model.intercept_:.6f}")
    
    # ---- 随机基线 ----
    # 方向准确率 50% = 随机猜测
    random_baseline = 0.5
    
    # ---- 汇总 ----
    print(f"\n  --- {horizon}d 对比 ---")
    print(f"    {'模型':12s} {'RMSE':12s} {'方向准确率':12s} {'vs随机':12s}")
    print(f"    {'-'*48}")
    print(f"    {'LGBM回归':12s} {rmse_a:12.6f} {dir_acc_a:11.2%} {dir_acc_a - random_baseline:+11.2%}")
    print(f"    {'LGBM分类':12s} {'N/A':12s} {dir_acc_b:11.2%} {dir_acc_b - random_baseline:+11.2%}")
    print(f"    {'线性回归':12s} {rmse_c:12.6f} {dir_acc_c:11.2%} {dir_acc_c - random_baseline:+11.2%}")
    print(f"    {'随机猜测':12s} {'N/A':12s} {random_baseline:11.2%} {'':>12s}")
    
    results.append({
        'horizon': f'{horizon}d',
        'lgbm_reg_rmse': rmse_a,
        'lgbm_reg_dir_acc': dir_acc_a,
        'lgbm_reg_iterations': model_a.best_iteration_,
        'lgbm_cls_dir_acc': dir_acc_b,
        'lgbm_cls_iterations': model_b.best_iteration_,
        'linear_rmse': rmse_c,
        'linear_dir_acc': dir_acc_c,
        'majority_baseline': majority_acc,
        'n_test': len(X_test_m),
    })

# ============================================================
# 7. 全因子组合消融：逐一加入特征
# ============================================================
print("\n" + "=" * 60)
print("STEP 7: 单因子消融（逐一测试每个 HAM 因子的独立预测力）")
print("=" * 60)

single_factor_results = []

for factor_idx, factor_name in enumerate(feature_names):
    X_single = X[:, factor_idx].reshape(-1, 1)
    
    print(f"\n  --- 单因子: {factor_name} ---")
    
    for horizon in [1, 3, 5]:
        y_all = np.array(labels[f'label_ret_{horizon}d'], dtype=np.float32)
        
        y_train = y_all[:train_end][~np.isnan(y_all[:train_end])]
        y_val = y_all[train_end:val_end][~np.isnan(y_all[train_end:val_end])]
        y_test = y_all[val_end:][~np.isnan(y_all[val_end:])]
        
        X_train_s = X_single[:train_end][~np.isnan(y_all[:train_end])]
        X_val_s = X_single[train_end:val_end][~np.isnan(y_all[train_end:val_end])]
        X_test_s = X_single[val_end:][~np.isnan(y_all[val_end:])]
        
        if len(X_train_s) < 50 or len(X_test_s) < 10:
            continue
        
        # 线性回归（单因子，线性关系检验）
        lr_single = LinearRegression()
        lr_single.fit(X_train_s, y_train)
        pred_single = lr_single.predict(X_test_s)
        dir_acc_single = np.mean(np.sign(pred_single) == np.sign(y_test))
        coef = lr_single.coef_[0]
        
        print(f"    {horizon}d 线性回归: 方向准确率={dir_acc_single:.2%}, 系数={coef:.6f}")
        
        single_factor_results.append({
            'factor': factor_name,
            'horizon': f'{horizon}d',
            'dir_acc': dir_acc_single,
            'coef': coef,
            'n_test': len(X_test_s),
        })

# ============================================================
# 8. 生成报告
# ============================================================
print("\n" + "=" * 60)
print("STEP 8: 生成消融实验报告")
print("=" * 60)

from datetime import datetime

md = []
md.append("# 纯 HAM 因子消融实验报告\n")
md.append(f"> 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
md.append(f"> 目的: 验证 HAM 因子 (n_c, Total_Demand, n_f-n_c) 是否有独立预测力")
md.append(f"> 样本: {n_total} 天, {dates[0]} ~ {dates[-1]}")
md.append(f"> 切分: 60%训练 / 20%验证 / 20%测试")
md.append(f"> 基线: 随机猜测方向准确率 = 50%\n")
md.append("---\n")

md.append("## 1. 三模型对比（HAM 因子全量）\n")
md.append("| 标签 | 模型 | RMSE | 方向准确率 | vs随机 | 迭代轮数 |")
md.append("|------|------|------|-----------|--------|---------|")
for r in results:
    md.append(f"| {r['horizon']} | LGBM回归 | {r['lgbm_reg_rmse']:.6f} | {r['lgbm_reg_dir_acc']:.2%} | {r['lgbm_reg_dir_acc']-0.5:+.2%} | {r['lgbm_reg_iterations']} |")
    md.append(f"| {r['horizon']} | LGBM分类 | N/A | {r['lgbm_cls_dir_acc']:.2%} | {r['lgbm_cls_dir_acc']-0.5:+.2%} | {r['lgbm_cls_iterations']} |")
    md.append(f"| {r['horizon']} | 线性回归 | {r['linear_rmse']:.6f} | {r['linear_dir_acc']:.2%} | {r['linear_dir_acc']-0.5:+.2%} | N/A |")
    md.append(f"| {r['horizon']} | 随机猜测 | N/A | 50.00% | 0.00% | N/A |")
    md.append(f"| {r['horizon']} | 多数类基线 | N/A | {r['majority_baseline']:.2%} | {r['majority_baseline']-0.5:+.2%} | N/A |")

md.append("")
md.append("## 2. 单因子消融（逐一测试）\n")
md.append("| 因子 | 标签 | 方向准确率 | 系数 | vs随机 |")
md.append("|------|------|-----------|------|--------|")
for r in single_factor_results:
    md.append(f"| {r['factor']} | {r['horizon']} | {r['dir_acc']:.2%} | {r['coef']:.6f} | {r['dir_acc']-0.5:+.2%} |")

# 判断结论
md.append("")
md.append("## 3. 结论\n")

# 检查所有结果是否显著优于随机
all_above_random = all(
    r['lgbm_reg_dir_acc'] > 0.55 or r['lgbm_cls_dir_acc'] > 0.55 or r['linear_dir_acc'] > 0.55
    for r in results
)

if all_above_random:
    md.append("### ✅ HAM 因子有独立预测力\n")
    md.append("至少一个模型在测试集上方向准确率显著优于随机猜测（>55%）。")
else:
    md.append("### ❌ HAM 因子无独立预测力\n")
    md.append("所有模型在所有标签上方向准确率均未显著优于随机猜测（≤55%）。")
    md.append("HAM 因子 (n_c, Total_Demand, n_f-n_c) 无法独立预测碳酸锂价格方向。")

# 检查非线性增益
md.append("")
md.append("### 非线性增益检验\n")
nonlinear_gains = []
for r in results:
    gain = r['lgbm_reg_dir_acc'] - r['linear_dir_acc']
    nonlinear_gains.append(gain)
    md.append(f"- {r['horizon']}: LGBM {r['lgbm_reg_dir_acc']:.2%} vs 线性 {r['linear_dir_acc']:.2%} → 增益 {gain:+.2%}")

avg_gain = np.mean(nonlinear_gains)
if avg_gain > 0.02:
    md.append(f"\n**平均非线性增益: {avg_gain:+.2%}** — 非线性模型有显著增益")
elif avg_gain < -0.02:
    md.append(f"\n**平均非线性增益: {avg_gain:+.2%}** — 线性模型反而更好（过拟合）")
else:
    md.append(f"\n**平均非线性增益: {avg_gain:+.2%}** — 非线性无明显增益")

md.append("")
md.append("### 与全特征模型对比\n")
md.append("| 模型 | 1d方向准确率 | 3d方向准确率 | 5d方向准确率 |")
md.append("|------|------------|------------|------------|")
md.append("| 全特征 (17特征) | 44.0% | 48.8% | 49.6% |")
for r in results:
    h = r['horizon']
    md.append(f"| 纯HAM (3因子) | {r['lgbm_reg_dir_acc']:.2%} | {r['lgbm_cls_dir_acc']:.2%} | {r['linear_dir_acc']:.2%} |")

# 找对应horizon的
for r in results:
    h = int(r['horizon'][:-1])
    if h == 1:
        md.append(f"| 纯HAM LGBM回归 {r['horizon']} | {r['lgbm_reg_dir_acc']:.2%} | - | - |")
    if h == 3:
        md.append(f"| 纯HAM LGBM回归 {r['horizon']} | - | {r['lgbm_reg_dir_acc']:.2%} | - |")
    if h == 5:
        md.append(f"| 纯HAM LGBM回归 {r['horizon']} | - | - | {r['lgbm_reg_dir_acc']:.2%} |")

report_path = os.path.join(ROOT, "ham_ablation_report.md")
with open(report_path, 'w') as f:
    f.write('\n'.join(md))

print(f"报告已生成: {report_path}")
print("\n" + "=" * 60)
print("消融实验完成")
print("=" * 60)
