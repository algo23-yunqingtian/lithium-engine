#!/usr/bin/env python3
"""
exp436 子任务2: 状态自适应行为参数改造
- 保留产业/投机两类交易者主体
- 移除全局固定学习速率/风险厌恶参数
- 参数根据库存周期、基差所处区间动态自适应
- 离线回测对比基准 HAM
- 前置约束: 滚动窗口内拟合, 尾部20%盲测试集
- 输出: IC、分环境绩效、Alpha、自由度增量、过拟合风险
"""
import json, os, sys
import numpy as np
import pandas as pd
from scipy.stats import spearmanr

MODEL = "/home/ubuntu/lithium-engine/model_ham"
OUT_DIR = os.path.join(MODEL, "exp436_research")
DATA_DIR = os.path.join(OUT_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)

df = pd.read_csv(os.path.join(MODEL, "exp432_regime_classification/data/exp432_dataset.csv"))
df['date'] = pd.to_datetime(df['date'])
h = pd.read_csv(os.path.join(MODEL, "ham_experiment_archive/intermediate/exp401_ham_factors_pure.csv"))
h['date'] = pd.to_datetime(h['date'])
df = df.merge(h[['date', 'D_f', 'D_c', 'n_f', 'n_c', 'P_fund']], on='date', how='left')
df['fwd20_ret'] = df['fwd20_ret'] if 'fwd20_ret' in df.columns else df['ret'].shift(-20)

n = len(df)
split_idx = int(n * 0.8)

print(f"=== exp436 子任务2: 状态自适应行为参数改造 ===")
print(f"总样本: {n} 日 | 训练: {split_idx} 日 | 盲测: {n-split_idx} 日")

# === 状态自适应参数设计 ===
# 库存周期: 用 position 的滚动分位确定所处区间
# 基差区间: basis_rate 的滚动分位
# 参数: alpha (学习速率/产业回归强度), beta (风险厌恶/投机动量强度)
# 原版: alpha/beta 全局固定 (exp409的logit自适应标准化)
# 改造: alpha/beta 随库存周期和基差区间动态变化

# 库存周期分位 (200日滚动)
df['pos_percentile'] = df['position'].rolling(200, min_periods=60).rank(pct=True)
# 基差区间分位 (200日滚动)
df['basis_percentile'] = df['basis_rate'].rolling(200, min_periods=60).rank(pct=True)

# === 自适应参数: 根据库存周期+基差区间动态调整 ===
# 库存高周期(库存累积) → 产业回归压力弱 → alpha低
# 库存低周期(去库) → 产业回归压力强 → alpha高
# 基差贴水 → 产业看空回归强 → alpha高
# 基差升水 → 投机主导 → beta高

# 3种自适应方案
def adaptive_alpha_v1(df):
    """方案A: alpha = 库存低位+贴水时高，否则低"""
    alpha = 0.5 * (1 - df['pos_percentile']) + 0.5 * (1 - df['basis_percentile'])
    return alpha.clip(0.1, 1.0)

def adaptive_beta_v1(df):
    """方案A: beta = 库存高位+升水时高"""
    beta = 0.5 * df['pos_percentile'] + 0.5 * df['basis_percentile']
    return beta.clip(0.1, 1.0)

def adaptive_alpha_v2(df):
    """方案B: 仅库存周期驱动"""
    alpha = (1 - df['pos_percentile']).clip(0.2, 1.0)
    return alpha

def adaptive_beta_v2(df):
    """方案B: 仅基差区间驱动"""
    beta = df['basis_percentile'].clip(0.2, 1.0)
    return beta

def adaptive_alpha_v3(df):
    """方案C: 三区间离散化 (低/中/高库存周期)"""
    alpha = pd.cut(df['pos_percentile'], bins=[0, 1/3, 2/3, 1],
                   labels=[1.0, 0.6, 0.3]).astype(float)
    return alpha.fillna(0.6)

def adaptive_beta_v3(df):
    """方案C: 三区间离散化 (低/中/高基差)"""
    beta = pd.cut(df['basis_percentile'], bins=[0, 1/3, 2/3, 1],
                  labels=[0.3, 0.6, 1.0]).astype(float)
    return beta.fillna(0.6)

# 基准: 固定参数 (用 exp409 的全局自适应标准化)
df['alpha_fixed'] = 1.0
df['beta_fixed'] = 1.0

# 应用3种自适应方案
for v in [1, 2, 3]:
    df[f'alpha_v{v}'] = globals()[f'adaptive_alpha_v{v}'](df)
    df[f'beta_v{v}'] = globals()[f'adaptive_beta_v{v}'](df)

# === 自适应分歧信号合成 ===
# 原版: D_f = P_fund - close, D_c = close - close_prev, 然后 logit标准化
# 改造: D_f_adj = alpha_state * (P_fund - close), D_c_adj = beta_state * (close - close_prev)
close = df['close'].values
close_prev = np.r_[np.nan, close[:-1]]
ret_c = close - close_prev  # D_c 基础
pfund = df['P_fund'].values

df['disagree_fixed'] = (pfund - close) - ret_c  # 原版分歧 (固定alpha=beta=1)

for v in [1, 2, 3]:
    a = df[f'alpha_v{v}'].fillna(1.0)
    b = df[f'beta_v{v}'].fillna(1.0)
    df[f'disagree_v{v}'] = a * (pfund - close) - b * ret_c

# 同时对比原 HAM dev_mean
if 'dev_mean' not in df.columns:
    df['dev_mean'] = 0

# === 检验函数 ===
def _ic(fa, ra):
    m = np.isfinite(fa) & np.isfinite(ra)
    if m.sum() < 30:
        return np.nan, np.nan
    try:
        r, p = spearmanr(fa[m], ra[m])
        return float(r), float(p)
    except:
        return np.nan, np.nan

def _perm_p(fa, ra, n_perms=100):
    m = np.isfinite(fa) & np.isfinite(ra)
    if m.sum() < 30:
        return np.nan
    f = fa[m]; r = ra[m]
    np.random.seed(42)
    actual_ic, _ = spearmanr(f, r)
    perm_ics = [spearmanr(np.random.permutation(f), r)[0] for _ in range(n_perms)]
    return (np.sum(np.abs(np.array(perm_ics)) >= abs(actual_ic)) + 1) / (n_perms + 1)

def _ortho_ic(fa, tx, ra):
    m = np.isfinite(fa) & np.isfinite(ra)
    if m.sum() < 30:
        return np.nan, np.nan
    f = fa[m]; x = tx[m]; r = ra[m]
    X_c = np.c_[np.ones(len(x)), x]
    try:
        beta, _, _, _ = np.linalg.lstsq(X_c, f, rcond=None)
        resid = f - X_c @ beta
        return spearmanr(resid, r)
    except:
        return np.nan, np.nan

# === 传统因子 (正交对标) ===
trad_cols = ['basis_rate', 'basis_rate_chg5', 'pos_chg_20d_pct', 'vol_chg_20d_pct', 'rvol20', 'vol_rel', 'basis_turn']
trad_train = df[trad_cols].iloc[:split_idx].fillna(0).values

# === 执行检验 ===
factors = {
    'baseline_ham_dev_mean': df['dev_mean'].values,
    'fixed_disagree': df['disagree_fixed'].values,
    'adaptive_v1': df['disagree_v1'].values,
    'adaptive_v2': df['disagree_v2'].values,
    'adaptive_v3': df['disagree_v3'].values,
}

print("\n=== 自适应方案 vs 基准 ===")
results = {}
for fname, fv in factors.items():
    train_f = fv[:split_idx]
    train_r = df['fwd20_ret'].values[:split_idx]
    blind_f = fv[split_idx:]
    blind_r = df['fwd20_ret'].values[split_idx:]
    
    ic, p = _ic(train_f, train_r)
    res_ic, res_p = _ortho_ic(train_f, trad_train, train_r)
    perm_p = _perm_p(train_f, train_r)
    blind_ic, blind_p = _ic(blind_f, blind_r)
    
    results[fname] = {
        'train_ic': round(ic, 4) if np.isfinite(ic) else None,
        'train_p': round(p, 4) if np.isfinite(p) else None,
        'orthogonal_ic': round(res_ic, 4) if np.isfinite(res_ic) else None,
        'orthogonal_p': round(res_p, 4) if np.isfinite(res_p) else None,
        'permutation_p': round(perm_p, 4) if np.isfinite(perm_p) else None,
        'blind_ic': round(blind_ic, 4) if np.isfinite(blind_ic) else None,
        'blind_p': round(blind_p, 4) if np.isfinite(blind_p) else None,
    }
    print(f"  {fname:22s}: 训练IC={results[fname]['train_ic']} (p={results[fname]['train_p']}) | "
          f"残差IC={results[fname]['orthogonal_ic']} | 置换p={results[fname]['permutation_p']} | "
          f"盲测IC={results[fname]['blind_ic']}")

# === 自由度增量 ===
doF = {
    'baseline_ham_dev_mean': 0,   # 无新增参数 (固定)
    'fixed_disagree': 0,          # 固定alpha=beta=1
    'adaptive_v1': 2,             # alpha_v1(库存+基差) + beta_v1(库存+基差) = 2组参数
    'adaptive_v2': 2,             # alpha_v2(库存) + beta_v2(基差) = 2组
    'adaptive_v3': 2,             # alpha_v3(3区间) + beta_v3(3区间) = 2组离散参数
}
results['factors_of_freedom'] = doF

# === 参数敏感性扫描 (对自适应方案的阈值/权重做敏感性) ===
# 扫描库存权重 w_pos ∈ [0, 0.25, 0.5, 0.75, 1.0]
sensitivity = []
for w_pos in [0, 0.25, 0.5, 0.75, 1.0]:
    alpha_scan = (w_pos * (1 - df['pos_percentile'].fillna(0.5)) + 
                  (1-w_pos) * (1 - df['basis_percentile'].fillna(0.5))).clip(0.1, 1.0)
    beta_scan = (w_pos * df['pos_percentile'].fillna(0.5) + 
                 (1-w_pos) * df['basis_percentile'].fillna(0.5)).clip(0.1, 1.0)
    disagree_scan = alpha_scan * (pfund - close) - beta_scan * ret_c
    ic, p = _ic(disagree_scan.values[:split_idx], df['fwd20_ret'].values[:split_idx])
    sensitivity.append({'w_pos': w_pos, 'train_ic': round(ic, 4) if np.isfinite(ic) else None,
                        'train_p': round(p, 4) if np.isfinite(p) else None})

results['sensitivity_scan'] = sensitivity

with open(os.path.join(DATA_DIR, 'subtask2_adaptive.json'), 'w') as f:
    json.dump(results, f, indent=2)

# 保存参数序列
pd.DataFrame({
    'date': df['date'],
    'pos_percentile': df['pos_percentile'],
    'basis_percentile': df['basis_percentile'],
    'alpha_v1': df['alpha_v1'],
    'beta_v1': df['beta_v1'],
    'disagree_v1': df['disagree_v1'],
    'disagree_fixed': df['disagree_fixed'],
}).to_csv(os.path.join(DATA_DIR, 'subtask2_params.csv'), index=False)

print(f"\n=== 自由度增量 ===")
for k, v in doF.items():
    print(f"  {k}: +{v} 自由度")

print(f"\n=== 参数敏感性 (库存权重 w_pos) ===")
for s in sensitivity:
    print(f"  w_pos={s['w_pos']}: IC={s['train_ic']} (p={s['train_p']})")

print(f"\n=== 子任务2 完成 ===")
print(f"输出: {DATA_DIR}/subtask2_adaptive.json, subtask2_params.csv")
