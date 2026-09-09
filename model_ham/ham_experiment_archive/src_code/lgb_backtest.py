#!/usr/bin/env python3
"""
HAM因子 + 外部特征 → LightGBM 回测
严格时间切分: 训练60% / 验证20% / 测试20%
"""

import csv
import json
import math
import os
import subprocess
import time

import lightgbm as lgb
import numpy as np

ROOT = os.path.dirname(os.path.abspath(__file__))
ZHIJI = '~/.hermes/scripts/zhiji_api.py'

# ============================================================
# 1. 读取 HAM 因子数据
# ============================================================
print("=" * 60)
print("STEP 1: 读取 HAM 因子数据")
print("=" * 60)

with open(os.path.join(ROOT, "ham_factors_full_800d.csv")) as f:
    reader = csv.DictReader(f)
    ham_data = list(reader)

for r in ham_data:
    for col in ['close', 'open', 'high', 'low', 'volume', 'position',
                'P_fund', 'basis', 'spot_avg', 'D_f', 'D_c',
                'profit_f', 'profit_c', 'n_f', 'n_c',
                'Total_Demand', 'n_f_minus_n_c']:
        r[col] = float(r[col])
    r['valid'] = int(r['valid'])

n = len(ham_data)
dates = [r['date'] for r in ham_data]
closes = [r['close'] for r in ham_data]

print(f"HAM 数据: {n} 行, {dates[0]} ~ {dates[-1]}")

# ============================================================
# 2. Winsorize 缩尾
# ============================================================
print("\n" + "=" * 60)
print("STEP 2: Winsorize 缩尾 (1%-99%)")
print("=" * 60)

def winsorize(values, lower_pct=1, upper_pct=99):
    valid = [v for v in values if v is not None and not math.isnan(v)]
    if not valid:
        return values
    sorted_v = sorted(valid)
    idx_lo = int(len(sorted_v) * lower_pct / 100)
    idx_hi = int(len(sorted_v) * upper_pct / 100)
    lo, hi = sorted_v[idx_lo], sorted_v[idx_hi]
    result = []
    clipped = 0
    for v in values:
        if v is None or math.isnan(v):
            result.append(v)
        elif v < lo:
            result.append(lo)
            clipped += 1
        elif v > hi:
            result.append(hi)
            clipped += 1
        else:
            result.append(v)
    return result

n_c = winsorize([r['n_c'] for r in ham_data])
td = winsorize([r['Total_Demand'] for r in ham_data])
nfnc = winsorize([r['n_f_minus_n_c'] for r in ham_data])

print(f"  n_c: 缩尾完成")
print(f"  Total_Demand: 缩尾完成")
print(f"  n_f-n_c: 缩尾完成")

# ============================================================
# 3. 拉取知几外部数据
# ============================================================
print("\n" + "=" * 60)
print("STEP 3: 拉取知几外部数据")
print("=" * 60)

zhiji_indicators = [
    ('FU00058102', 'GFEX仓单(日)', 'zhiji_wh_receipt', 'daily'),
    ('ID01865189', 'Mysteel社会库存(月)', 'zhiji_social_inv', 'monthly'),
    ('ID01865185', 'Mysteel库存-江西(月)', 'zhiji_inv_jx', 'monthly'),
    ('ID01865196', 'Mysteel库存-青海(月)', 'zhiji_inv_qh', 'monthly'),
    ('ID01865203', 'Mysteel库存-四川(月)', 'zhiji_inv_sc', 'monthly'),
    ('ID02226359', 'Mysteel回收料库存(月)', 'zhiji_recycle_inv', 'monthly'),
]

# 尝试从缓存加载
cache_path = os.path.join(ROOT, "zhiji_external_data.json")
zhiji_raw = {}
if os.path.exists(cache_path):
    with open(cache_path) as f:
        cached = json.load(f)
    for _, _, field, _ in zhiji_indicators:
        if field in cached:
            zhiji_raw[field] = cached[field]
    if zhiji_raw:
        print(f"  从缓存加载: {len(zhiji_raw)} 个指标")
else:
    print("  无缓存，开始拉取...")

# 拉取缺失的
for ind_id, name, field, freq in zhiji_indicators:
    if field in zhiji_raw:
        continue
    print(f"  拉取: {name} ({ind_id})...", end=' ', flush=True)
    cmd = f'{ZHIJI} series {ind_id} 2023-08-01 2026-09-08'
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=60)
    time.sleep(1.2)
    if r.returncode == 0:
        try:
            d = json.loads(r.stdout)
            pts = d.get('points', [])
            zhiji_raw[field] = pts
            print(f"成功: {len(pts)} 点")
        except:
            zhiji_raw[field] = None
            print("解析失败")
    else:
        zhiji_raw[field] = None
        print("失败")

# 保存缓存
with open(cache_path, 'w') as f:
    json.dump(zhiji_raw, f, ensure_ascii=False)

# 构建日期索引
def build_date_index(points):
    """从知几时序数据构建 {date: value} 字典"""
    if not points:
        return {}
    d = {}
    for p in points:
        date = p['date']  # 格式: '2023-12-06'
        try:
            val = float(p['value'])
        except:
            val = 0.0
        d[date] = val
    return d

# 构建特征数组
external_features = {}

# 仓单库存 (日度, 直接映射)
if zhiji_raw.get('zhiji_wh_receipt'):
    wh_dict = build_date_index(zhiji_raw['zhiji_wh_receipt'])
    wh_vals = [wh_dict.get(d, None) for d in dates]
    valid = sum(1 for v in wh_vals if v is not None)
    external_features['zhiji_wh_receipt'] = wh_vals
    print(f"  仓单库存: {valid}/{n} 有效")

# 社会库存 (月度, 前向填充)
def forward_fill_monthly(ham_dates, monthly_dict):
    """将月度数据前向填充到日频"""
    result = []
    last_val = None
    for d in ham_dates:
        # 月度数据的日期格式: '2023-10-31' (月末)
        # 判断当前日期是否在该月
        month_key = None
        for md in sorted(monthly_dict.keys()):
            if d >= md:
                month_key = md
                break
        if month_key and d[:7] == month_key[:7]:
            last_val = monthly_dict[month_key]
        result.append(last_val)
    return result

if zhiji_raw.get('zhiji_social_inv'):
    soc_dict = build_date_index(zhiji_raw['zhiji_social_inv'])
    soc_vals = forward_fill_monthly(dates, soc_dict)
    valid = sum(1 for v in soc_vals if v is not None)
    external_features['zhiji_social_inv'] = soc_vals
    print(f"  社会库存(月度FF): {valid}/{n} 有效")

# 其他月度指标
for field, label in [('zhiji_inv_jx', '库存-江西'), ('zhiji_inv_qh', '库存-青海'),
                     ('zhiji_inv_sc', '库存-四川'), ('zhiji_recycle_inv', '回收料库存')]:
    if zhiji_raw.get(field):
        d_dict = build_date_index(zhiji_raw[field])
        vals = forward_fill_monthly(dates, d_dict)
        valid = sum(1 for v in vals if v is not None)
        external_features[field] = vals
        print(f"  {label}(月度FF): {valid}/{n} 有效")

# ============================================================
# 4. 构建动量指标
# ============================================================
print("\n" + "=" * 60)
print("STEP 4: 构建动量指标")
print("=" * 60)

for lookback in [5, 10, 20]:
    feat_name = f'ret_{lookback}d'
    vals = []
    for i in range(len(closes)):
        if i < lookback:
            vals.append(None)
        else:
            vals.append((closes[i] - closes[i - lookback]) / closes[i - lookback])
    external_features[feat_name] = vals
    valid = sum(1 for v in vals if v is not None)
    print(f"  {feat_name}: {valid}/{n} 有效")

# 基差率
basis_rate = []
for r in ham_data:
    if r['spot_avg'] != 0:
        basis_rate.append((r['basis'] / r['spot_avg']) * 100)
    else:
        basis_rate.append(0.0)
external_features['basis_rate'] = basis_rate

# ============================================================
# 5. 构造标签
# ============================================================
print("\n" + "=" * 60)
print("STEP 5: 构造预测标签")
print("=" * 60)

labels = {}
for horizon in [1, 3, 5]:
    feat_name = f'label_ret_{horizon}d'
    vals = []
    for i in range(len(closes)):
        if i + horizon < len(closes):
            vals.append((closes[i + horizon] - closes[i]) / closes[i])
        else:
            vals.append(None)
    labels[feat_name] = vals
    valid = sum(1 for v in vals if v is not None)
    print(f"  {feat_name}: {valid}/{n} 有效")

# ============================================================
# 6. 组装数据集
# ============================================================
print("\n" + "=" * 60)
print("STEP 6: 组装 ML 输入数据集")
print("=" * 60)

# 定义特征列
feature_names = [
    'n_c', 'Total_Demand', 'n_f_minus_n_c',  # HAM因子
    'basis', 'basis_rate', 'spot_avg', 'volume', 'position',  # 量价
    'ret_5d', 'ret_10d', 'ret_20d',  # 动量
]

# 外部特征（按可用情况）
if 'zhiji_wh_receipt' in external_features:
    feature_names.append('zhiji_wh_receipt')
if 'zhiji_social_inv' in external_features:
    feature_names.append('zhiji_social_inv')
if 'zhiji_inv_jx' in external_features:
    feature_names.append('zhiji_inv_jx')
if 'zhiji_inv_qh' in external_features:
    feature_names.append('zhiji_inv_qh')
if 'zhiji_inv_sc' in external_features:
    feature_names.append('zhiji_inv_sc')
if 'zhiji_recycle_inv' in external_features:
    feature_names.append('zhiji_recycle_inv')

print(f"  特征总数: {len(feature_names)}")
print(f"  特征列表: {feature_names}")

# 构建 numpy 数组
X = []
Y_1d, Y_3d, Y_5d = [], [], []
valid_mask = []

for i in range(n):
    row = []
    # HAM因子
    row.append(n_c[i])
    row.append(td[i])
    row.append(nfnc[i])
    # 量价
    row.append(ham_data[i]['basis'])
    row.append(external_features['basis_rate'][i])
    row.append(ham_data[i]['spot_avg'])
    row.append(ham_data[i]['volume'])
    row.append(ham_data[i]['position'])
    # 动量
    row.append(external_features['ret_5d'][i])
    row.append(external_features['ret_10d'][i])
    row.append(external_features['ret_20d'][i])
    # 外部
    if 'zhiji_wh_receipt' in external_features:
        row.append(external_features['zhiji_wh_receipt'][i])
    if 'zhiji_social_inv' in external_features:
        row.append(external_features['zhiji_social_inv'][i])
    if 'zhiji_inv_jx' in external_features:
        row.append(external_features['zhiji_inv_jx'][i])
    if 'zhiji_inv_qh' in external_features:
        row.append(external_features['zhiji_inv_qh'][i])
    if 'zhiji_inv_sc' in external_features:
        row.append(external_features['zhiji_inv_sc'][i])
    if 'zhiji_recycle_inv' in external_features:
        row.append(external_features['zhiji_recycle_inv'][i])
    
    X.append(row)
    Y_1d.append(labels['label_ret_1d'][i])
    Y_3d.append(labels['label_ret_3d'][i])
    Y_5d.append(labels['label_ret_5d'][i])
    
    # 有效: 所有特征非None + 标签非None
    has_nan = any(v is None for v in row)
    has_label = all(l is not None for l in [labels['label_ret_5d'][i]])
    valid_mask.append(not has_nan and has_label)

X = np.array(X, dtype=np.float32)
Y_1d = np.array(Y_1d, dtype=np.float32)
Y_3d = np.array(Y_3d, dtype=np.float32)
Y_5d = np.array(Y_5d, dtype=np.float32)
valid_mask = np.array(valid_mask)

# 填充缺失值为0（LightGBM 支持 NaN，但先填0更稳定）
X = np.nan_to_num(X, nan=0.0)

# 有效样本
valid_idx = np.where(valid_mask)[0]
X_valid = X[valid_idx]
dates_valid = [dates[i] for i in valid_idx]

print(f"  总样本: {n}")
print(f"  有效样本(无缺失+有标签): {len(valid_idx)}")

# 写 CSV
out_csv = os.path.join(ROOT, "ml_input_features.csv")
out_fields = ['date', 'close'] + [f'label_ret_{h}d' for h in [1,3,5]] + feature_names
with open(out_csv, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(out_fields)
    for i in range(n):
        row = [dates[i], closes[i]]
        row.append(f'{Y_1d[i]:.6f}' if Y_1d[i] is not None else '')
        row.append(f'{Y_3d[i]:.6f}' if Y_3d[i] is not None else '')
        row.append(f'{Y_5d[i]:.6f}' if Y_5d[i] is not None else '')
        for j, fn in enumerate(feature_names):
            row.append(f'{X[i][j]:.6f}')
        w.writerow(row)

print(f"  已输出: {out_csv}")

# ============================================================
# 7. 时间切分 + LightGBM 训练
# ============================================================
print("\n" + "=" * 60)
print("STEP 7: LightGBM 训练与回测")
print("=" * 60)

train_end = int(len(valid_idx) * 0.6)
val_end = int(len(valid_idx) * 0.8)

X_train = X_valid[:train_end]
X_val = X_valid[train_end:val_end]
X_test = X_valid[val_end:]

train_dates = dates_valid[:train_end]
val_dates = dates_valid[train_end:val_end]
test_dates = dates_valid[val_end:]

print(f"  训练集: {len(X_train)} 样本 ({train_dates[0]} ~ {train_dates[-1]})")
print(f"  验证集: {len(X_val)} 样本 ({val_dates[0]} ~ {val_dates[-1]})")
print(f"  测试集: {len(X_test)} 样本 ({test_dates[0]} ~ {test_dates[-1]})")

# 标签处理
def preprocess_y(y_arr, idx_arr):
    """提取有效标签，处理NaN"""
    y = y_arr[idx_arr].copy()
    mask = ~np.isnan(y)
    return y[mask], mask

# 对每个 horizon 单独训练
results_summary = []
for horizon, y_full in [(1, Y_1d), (3, Y_3d), (5, Y_5d)]:
    y_all = y_full[valid_idx]
    y_train_raw = y_all[:train_end]
    y_val_raw = y_all[train_end:val_end]
    y_test_raw = y_all[val_end:]
    
    # 去掉 NaN
    mask_train = ~np.isnan(y_train_raw)
    mask_val = ~np.isnan(y_val_raw)
    mask_test = ~np.isnan(y_test_raw)
    
    X_tr = X_train[mask_train]
    y_tr = y_train_raw[mask_train]
    X_vl = X_val[mask_val]
    y_vl = y_val_raw[mask_val]
    X_te = X_test[mask_test]
    y_te = y_test_raw[mask_test]
    
    if len(X_tr) < 50 or len(X_te) < 10:
        print(f"  [{horizon}d] 样本不足，跳过")
        continue
    
    print(f"\n  --- {horizon}d 标签 ---")
    print(f"    训练: {len(X_tr)}, 验证: {len(X_vl)}, 测试: {len(X_te)}")
    
    # 训练
    params = {
        'objective': 'regression',
        'metric': 'rmse',
        'learning_rate': 0.05,
        'num_leaves': 31,
        'max_depth': 6,
        'min_child_samples': 20,
        'feature_fraction': 0.8,
        'bagging_fraction': 0.8,
        'bagging_freq': 1,
        'verbose': -1,
        'n_estimators': 200,
        'random_state': 42,
    }
    
    model = lgb.LGBMRegressor(**params)
    model.fit(
        X_tr, y_tr,
        eval_set=[(X_vl, y_vl)],
        callbacks=[lgb.early_stopping(20), lgb.log_evaluation(0)],
    )
    
    # 预测
    pred_val = model.predict(X_vl)
    pred_test = model.predict(X_te)
    
    # 评估
    def eval_metrics(y_true, y_pred):
        rmse = math.sqrt(np.mean((y_true - y_pred) ** 2))
        mae = np.mean(np.abs(y_true - y_pred))
        return rmse, mae
    
    rmse_val, mae_val = eval_metrics(y_vl, pred_val)
    rmse_test, mae_test = eval_metrics(y_te, pred_test)
    
    # 方向准确率 (方向预测)
    dir_accuracy_test = np.mean(np.sign(pred_test) == np.sign(y_te))
    
    print(f"    验证集: RMSE={rmse_val:.6f}, MAE={mae_val:.6f}")
    print(f"    测试集: RMSE={rmse_test:.6f}, MAE={mae_test:.6f}")
    print(f"    方向准确率(测试): {dir_accuracy_test:.2%}")
    
    results_summary.append({
        'horizon': f'{horizon}d',
        'n_train': len(X_tr),
        'n_test': len(X_te),
        'rmse_val': rmse_val,
        'mae_val': mae_val,
        'rmse_test': rmse_test,
        'mae_test': mae_test,
        'dir_acc_test': dir_accuracy_test,
    })

# ============================================================
# 8. 特征重要性
# ============================================================
print("\n" + "=" * 60)
print("STEP 8: 特征重要性")
print("=" * 60)

# 用5d模型（样本最多）展示特征重要性
model_5d = model  # 最后一次训练的模型
importances = model_5d.feature_importances_
feat_imp = sorted(zip(feature_names, importances), key=lambda x: -x[1])

print(f"  5d 模型特征重要性:")
for fname, imp in feat_imp:
    bar = '#' * int(imp / max(imp for _, imp in feat_imp) * 40) if max(imp for _, imp in feat_imp) > 0 else ''
    print(f"    {fname:25s} {imp:6.0f} {bar}")

# ============================================================
# 9. 生成回测报告
# ============================================================
print("\n" + "=" * 60)
print("STEP 9: 生成回测报告")
print("=" * 60)

from datetime import datetime

md = []
md.append("# HAM因子 + 外部特征 LightGBM 回测报告\n")
md.append(f"> 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
md.append(f"> 模型: LightGBM Regressor")
md.append(f"> 特征数: {len(feature_names)}")
md.append(f"> 有效样本: {len(valid_idx)}")
md.append("")

# 特征列表
md.append("## 1. 特征列表\n")
md.append(f"共 {len(feature_names)} 个特征:\n")
md.append("| # | 特征名 | 类别 |")
md.append("|---|--------|------|")
for i, fn in enumerate(feature_names, 1):
    if fn in ['n_c', 'Total_Demand', 'n_f_minus_n_c']:
        cat = 'HAM因子'
    elif fn in ['basis', 'basis_rate', 'spot_avg', 'volume', 'position']:
        cat = '量价'
    elif fn.startswith('ret_'):
        cat = '动量'
    elif 'zhiji_wh' in fn:
        cat = '仓单库存'
    elif 'zhiji_social' in fn:
        cat = '社会库存'
    elif 'zhiji_inv_' in fn:
        cat = '库存-分地区'
    elif 'zhiji_recycle' in fn:
        cat = '回收料库存'
    else:
        cat = '其他'
    md.append(f"| {i} | {fn} | {cat} |")
md.append("")

# 时间切分
md.append("## 2. 时间切分\n")
md.append("| 集合 | 样本数 | 时间范围 |")
md.append("|------|--------|----------|")
md.append(f"| 训练集 | {len(X_train)} (60%) | {train_dates[0]} ~ {train_dates[-1]} |")
md.append(f"| 验证集 | {len(X_val)} (20%) | {val_dates[0]} ~ {val_dates[-1]} |")
md.append(f"| 测试集 | {len(X_test)} (20%) | {test_dates[0]} ~ {test_dates[-1]} |")
md.append("")
md.append("**原则**: 严格时间顺序，不shuffle，无重叠\n")

# 回测结果
md.append("## 3. 回测结果\n")
md.append("| 标签 | 训练样本 | 测试样本 | 验证RMSE | 验证MAE | 测试RMSE | 测试MAE | 方向准确率 |")
md.append("|------|----------|----------|----------|---------|----------|---------|-----------|")
for r in results_summary:
    md.append(f"| {r['horizon']} | {r['n_train']} | {r['n_test']} | {r['rmse_val']:.6f} | {r['mae_val']:.6f} | {r['rmse_test']:.6f} | {r['mae_test']:.6f} | {r['dir_acc_test']:.2%} |")
md.append("")

# 特征重要性
md.append("## 4. 特征重要性 (5d模型)\n")
md.append("| # | 特征 | 重要性 |")
md.append("|---|------|--------|")
for i, (fname, imp) in enumerate(feat_imp, 1):
    md.append(f"| {i} | {fname} | {imp:.0f} |")
md.append("")

# 模型参数
md.append("## 5. 模型参数\n")
md.append("```json")
md.append(json.dumps({
    'objective': 'regression',
    'learning_rate': 0.05,
    'num_leaves': 31,
    'max_depth': 6,
    'min_child_samples': 20,
    'feature_fraction': 0.8,
    'bagging_fraction': 0.8,
    'bagging_freq': 1,
    'n_estimators': 200,
    'early_stopping_rounds': 20,
}, indent=2))
md.append("```\n")

# 结论
md.append("## 6. 结论\n")
if results_summary:
    best = max(results_summary, key=lambda r: r['dir_acc_test'])
    md.append(f"- 最佳方向准确率: {best['horizon']} 标签，{best['dir_acc_test']:.2%}")
    md.append(f"- 测试集RMSE范围: {min(r['rmse_test'] for r in results_summary):.6f} ~ {max(r['rmse_test'] for r in results_summary):.6f}")
    
    # 判断效果
    if best['dir_acc_test'] > 0.55:
        md.append(f"- **结论: 模型有一定预测能力**，方向准确率超过随机猜测(50%)")
    elif best['dir_acc_test'] > 0.50:
        md.append(f"- **结论: 模型边际有效**，方向准确率略高于随机猜测")
    else:
        md.append(f"- **结论: 模型未显著优于随机猜测**")
md.append("")

# 数据说明
md.append("## 7. 数据说明\n")
md.append("### 外部特征覆盖率\n")
md.append("| 特征 | 有效样本 | 覆盖率 |")
md.append("|------|----------|--------|")
for fn in feature_names:
    if fn in ['n_c', 'Total_Demand', 'n_f_minus_n_c']:
        valid = len(valid_idx)
        md.append(f"| {fn} | {valid} | 100% |")
    elif 'zhiji' in fn:
        valid = sum(1 for i in valid_idx if X[i][feature_names.index(fn)] != 0)
        md.append(f"| {fn} | {valid} | {valid/len(valid_idx)*100:.1f}% |")
    else:
        valid = len(valid_idx)
        md.append(f"| {fn} | {valid} | 100% |")
md.append("")

md_path = os.path.join(ROOT, "backtest_report.md")
with open(md_path, 'w', encoding='utf-8') as f:
    f.write('\n'.join(md))
print(f"已输出: {md_path}")

# 最终摘要
print("\n" + "=" * 60)
print("完成摘要")
print("=" * 60)
print(f"  数据集: {out_csv}")
print(f"  报告: {md_path}")
for r in results_summary:
    print(f"  {r['horizon']}: 方向准确率={r['dir_acc_test']:.2%}, RMSE={r['rmse_test']:.6f}")
