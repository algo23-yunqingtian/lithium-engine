#!/usr/bin/env python3
"""
exp436 子任务4: HAM 特征集多因子合成研究
- 提取原版+改造版HAM全部派生指标 + 基本面/量价指标 → 候选特征池
- 正交筛选 + 二阶交互项构造 → 合成复合因子
- 检验复合因子独立 Alpha
- 前置约束: 滚动窗口内拟合, 尾部20%盲测试集
"""
import json, os, sys
import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from itertools import combinations

MODEL = "/home/ubuntu/lithium-engine/model_ham"
OUT_DIR = os.path.join(MODEL, "exp436_research")
DATA_DIR = os.path.join(OUT_DIR, "data")

df = pd.read_csv(os.path.join(MODEL, "exp432_regime_classification/data/exp432_dataset.csv"))
df['date'] = pd.to_datetime(df['date'])
h = pd.read_csv(os.path.join(MODEL, "ham_experiment_archive/intermediate/exp401_ham_factors_pure.csv"))
h['date'] = pd.to_datetime(h['date'])
df = df.merge(h[['date', 'D_f', 'D_c', 'n_f', 'n_c', 'n_f_minus_n_c', 'P_fund', 'profit_f', 'profit_c']], on='date', how='left')
df['fwd20_ret'] = df['fwd20_ret'] if 'fwd20_ret' in df.columns else df['ret'].shift(-20)

n = len(df)
split_idx = int(n * 0.8)

print(f"=== exp436 子任务4: HAM 特征集多因子合成 ===")
print(f"总样本: {n} 日 | 训练: {split_idx} 日 | 盲测: {n-split_idx} 日")

# === 构建候选特征池 ===
# HAM 派生指标
close = df['close'].values
close_prev = np.r_[np.nan, close[:-1]]
ret_c = close - close_prev
pfund = df['P_fund'].values

features = {}
# 原版 HAM
features['ham_dev_mean'] = df['dev_mean'].fillna(0) if 'dev_mean' in df.columns else pd.Series(0, index=df.index)
features['ham_dev_median'] = df['dev_median'].fillna(0) if 'dev_median' in df.columns else pd.Series(0, index=df.index)
features['ham_D_f_minus_D_c'] = df['D_f'] - df['D_c']
features['ham_n_f_minus_n_c'] = df['n_f_minus_n_c'].fillna(0)

# 子任务1衍生因子
features['dir_disagreement'] = np.sign(df['D_f']) - np.sign(df['D_c'])
disagreement_mag = (df['D_f'] - df['D_c']).abs()
features['disagreement_vol'] = disagreement_mag.rolling(20, min_periods=10).std()
log_mag = np.log(disagreement_mag + 1e-6)
features['disagreement_decay'] = -log_mag.diff().rolling(20, min_periods=10).mean()

# 子任务2自适应因子
pos_pct = df['position'].rolling(200, min_periods=60).rank(pct=True).fillna(0.5)
basis_pct = df['basis_rate'].rolling(200, min_periods=60).rank(pct=True).fillna(0.5)
alpha_v1 = (0.5 * (1 - pos_pct) + 0.5 * (1 - basis_pct)).clip(0.1, 1.0)
beta_v1 = (0.5 * pos_pct + 0.5 * basis_pct).clip(0.1, 1.0)
features['adaptive_disagree_v1'] = alpha_v1 * (pfund - close) - beta_v1 * ret_c

# 子任务3多主体
features['multiagent_3'] = df['D_f'] - df['D_c'] + 0.5 * df['basis_rate'].fillna(0)

# 基本面/量价指标
features['basis_rate'] = df['basis_rate'].fillna(0)
features['basis_rate_chg5'] = df['basis_rate_chg5'].fillna(0)
features['pos_chg_20d_pct'] = df['pos_chg_20d_pct'].fillna(0)
features['vol_chg_20d_pct'] = df['vol_chg_20d_pct'].fillna(0)
features['rvol20'] = df['rvol20'].fillna(0)
features['vol_rel'] = df['vol_rel'].fillna(1.0)
features['basis_turn'] = df['basis_turn'].fillna(0)
features['momentum_20d'] = df['close'].pct_change(20).fillna(0) * 100
features['bias_rate'] = (df['close'] / df['close'].rolling(20).mean() - 1).fillna(0) * 100
features['position_pct_chg'] = df['position'].pct_change().fillna(0) * 100

print(f"\n候选特征池: {len(features)} 个")

# === 检验函数 ===
def _ic(fa, ra):
    m = np.isfinite(fa) & np.isfinite(ra)
    if m.sum() < 30:
        return np.nan, np.nan
    try:
        return float(spearmanr(fa[m], ra[m])[0]), float(spearmanr(fa[m], ra[m])[1])
    except:
        return np.nan, np.nan

def _perm_p(fa, ra, n_perms=100):
    m = np.isfinite(fa) & np.isfinite(ra)
    if m.sum() < 30:
        return np.nan
    f = fa[m]; r = ra[m]
    np.random.seed(42)
    actual = abs(spearmanr(f, r)[0])
    perms = [abs(spearmanr(np.random.permutation(f), r)[0]) for _ in range(n_perms)]
    return (np.sum(np.array(perms) >= actual) + 1) / (n_perms + 1)

def _ortho_resid(fa, tx, ra):
    m = np.isfinite(fa) & np.isfinite(ra)
    if m.sum() < 30:
        return np.nan, np.nan, None
    f = fa[m]; x = tx[m]; r = ra[m]
    X_c = np.c_[np.ones(len(x)), x]
    try:
        beta, _, _, _ = np.linalg.lstsq(X_c, f, rcond=None)
        resid = f - X_c @ beta
        ic, p = spearmanr(resid, r)
        return float(ic), float(p), resid
    except:
        return np.nan, np.nan, None

# === 步骤1: 单因子IC排名 (训练集) ===
print("\n=== 步骤1: 单因子IC排名 (训练集) ===")
single_ic = {}
fwd_train = df['fwd20_ret'].values[:split_idx]
for fname, fv in features.items():
    fv_arr = fv.values if hasattr(fv, 'values') else np.asarray(fv)
    ic, p = _ic(fv_arr[:split_idx], fwd_train)
    single_ic[fname] = {'ic': round(ic, 4) if np.isfinite(ic) else None,
                         'p': round(p, 4) if np.isfinite(p) else None,
                         'significant': bool(p < 0.05) if np.isfinite(p) else False}

# 排序
sorted_ic = sorted(single_ic.items(), key=lambda x: abs(x[1]['ic']) if x[1]['ic'] else 0, reverse=True)
for fname, stats in sorted_ic[:15]:
    sig = "✅" if stats['significant'] else "❌"
    print(f"  {sig} {fname:28s}: IC={stats['ic']} (p={stats['p']})")

# === 步骤2: 正交筛选 (贪心: 逐步剔除冗余) ===
print("\n=== 步骤2: 正交筛选 (贪心) ===")
# 候选: 取|IC|>0.05且显著的因子
candidates = [fn for fn, st in single_ic.items() if st['ic'] is not None and abs(st['ic']) > 0.05 and st['significant']]
print(f"IC显著且|IC|>0.05的因子: {candidates}")

# 用相关性矩阵剔除冗余 (阈值0.7)
selected = []
remaining = candidates.copy()
while remaining:
    # 选与已选集相关性最低的
    best = None
    best_corr = 1.0
    for fn in remaining:
        if not selected:
            best = fn
            break
        fv = features[fn].values if hasattr(features[fn], 'values') else np.asarray(features[fn])
        max_corr_with_selected = 0
        for sf in selected:
            svf = features[sf].values if hasattr(features[sf], 'values') else np.asarray(features[sf])
            m = np.isfinite(fv[:split_idx]) & np.isfinite(svf[:split_idx])
            if m.sum() > 10:
                c = abs(np.corrcoef(fv[:split_idx][m], svf[:split_idx][m])[0, 1])
                if c > max_corr_with_selected:
                    max_corr_with_selected = c
        if max_corr_with_selected < best_corr:
            best_corr = max_corr_with_selected
            best = fn
    if best is None:
        break
    selected.append(best)
    remaining.remove(best)
    if best_corr > 0.7:
        break

print(f"正交筛选后保留: {selected}")

# === 步骤3: 二阶交互项构造 ===
print("\n=== 步骤3: 二阶交互项 ===")
interactions = {}
# 只对 top 5 因子做交互
top5 = [fn for fn, _ in sorted_ic[:5]]
print(f"Top5 因子: {top5}")
for i, j in combinations(top5, 2):
    fi = features[i].values if hasattr(features[i], 'values') else np.asarray(features[i])
    fj = features[j].values if hasattr(features[j], 'values') else np.asarray(features[j])
    interaction = fi * fj
    ic, p = _ic(interaction[:split_idx], fwd_train)
    key = f"{i}_x_{j}"
    interactions[key] = {'ic': round(ic, 4) if np.isfinite(ic) else None,
                          'p': round(p, 4) if np.isfinite(p) else None}
    print(f"  {key:50s}: IC={interactions[key]['ic']} (p={interactions[key]['p']})")

# === 步骤4: 复合因子合成 ===
print("\n=== 步骤4: 复合因子合成 ===")
# 方案A: 正交筛选后的因子等权合成
orthogonal_factor = sum(features[fn] for fn in selected) / len(selected) if selected else features['ham_dev_mean']
# 方案B: 用IC加权合成
ic_weights = {}
for fn in selected:
    fv = features[fn].values if hasattr(features[fn], 'values') else np.asarray(features[fn])
    ic, _ = _ic(fv[:split_idx], fwd_train)
    ic_weights[fn] = abs(ic) if np.isfinite(ic) else 0

total_w = sum(ic_weights.values())
weighted_factor = sum(ic_weights[fn] / total_w * features[fn] for fn in selected) if total_w > 0 else orthogonal_factor

# 方案C: 加入最佳交互项
best_interaction_key = max(interactions.items(), key=lambda x: abs(x[1]['ic']) if x[1]['ic'] else 0)[0]
best_interaction = interactions[best_interaction_key]

composite_A = orthogonal_factor.values if hasattr(orthogonal_factor, 'values') else np.asarray(orthogonal_factor)
composite_B = weighted_factor.values if hasattr(weighted_factor, 'values') else np.asarray(weighted_factor)

# 检验复合因子
print(f"\n复合因子检验 (训练集):")
results = {}
for label, comp in [('A_orthogonal_equal', composite_A), ('B_ic_weighted', composite_B)]:
    ic, p = _ic(comp[:split_idx], fwd_train)
    blind_ic, blind_p = _ic(comp[split_idx:], df['fwd20_ret'].values[split_idx:])
    perm_p = _perm_p(comp[:split_idx], fwd_train)
    # 正交残差IC
    trad_cols = ['basis_rate', 'basis_rate_chg5', 'pos_chg_20d_pct', 'vol_chg_20d_pct', 'rvol20', 'vol_rel', 'basis_turn']
    trad_train = df[trad_cols].iloc[:split_idx].fillna(0).values
    res_ic, res_p, _ = _ortho_resid(comp[:split_idx], trad_train, fwd_train)
    
    results[label] = {
        'n_factors': len(selected),
        'train_ic': round(ic, 4) if np.isfinite(ic) else None,
        'train_p': round(p, 4) if np.isfinite(p) else None,
        'orthogonal_ic': round(res_ic, 4) if np.isfinite(res_ic) else None,
        'permutation_p': round(perm_p, 4) if np.isfinite(perm_p) else None,
        'blind_ic': round(blind_ic, 4) if np.isfinite(blind_ic) else None,
        'blind_p': round(blind_p, 4) if np.isfinite(blind_p) else None,
    }
    print(f"  {label}: 训练IC={results[label]['train_ic']} (p={results[label]['train_p']}) | "
          f"残差IC={results[label]['orthogonal_ic']} | 置换p={results[label]['permutation_p']} | "
          f"盲测IC={results[label]['blind_ic']}")

# === 输出 ===
output = {
    'n_features_pool': len(features),
    'single_factor_ic': {k: v for k, v in sorted_ic},
    'orthogonal_selection': selected,
    'interactions': interactions,
    'composite_factors': results,
    'ic_weights': ic_weights,
}
with open(os.path.join(DATA_DIR, 'subtask4_composite.json'), 'w') as f:
    json.dump(output, f, indent=2, default=str)

# 保存特征矩阵
feature_df = pd.DataFrame(features)
feature_df.index = df['date']
feature_df.to_csv(os.path.join(DATA_DIR, 'subtask4_feature_pool.csv'))

print(f"\n=== 子任务4 完成 ===")
print(f"输出: {DATA_DIR}/subtask4_composite.json, subtask4_feature_pool.csv")
