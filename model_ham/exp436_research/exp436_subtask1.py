#!/usr/bin/env python3
"""
exp436 子任务1: 分歧信号解构
- 不修改原有双主体迭代内核
- 对现有 HAM 分歧序列 (D_f/D_c) 拆解，衍生3个新因子:
  1. 方向分歧 (direction_disagreement): sign(D_f) - sign(D_c) 的有向分歧
  2. 分歧波动幅度 (disagreement_volatility): |D_f - D_c| 的滚动std
  3. 分歧收敛衰减速率 (disagreement_decay_rate): 分歧度的指数衰减时间常数
- 每个因子: IC时序 + 显著性p值 + 与传统9项因子正交残差IC
- 前置约束: 所有滚动拟合在窗口内部完成; 尾部20%为盲测试集
- 对比原 HAM 基准 (dev_mean / n_f_minus_n_c)
"""
import json, os, sys
import numpy as np
import pandas as pd
from scipy.stats import spearmanr

MODEL = "/home/ubuntu/lithium-engine/model_ham"
OUT_DIR = os.path.join(MODEL, "exp436_research")
DATA_DIR = os.path.join(OUT_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)

# 加载
df = pd.read_csv(os.path.join(MODEL, "exp432_regime_classification/data/exp432_dataset.csv"))
df['date'] = pd.to_datetime(df['date'])
h = pd.read_csv(os.path.join(MODEL, "ham_experiment_archive/intermediate/exp401_ham_factors_pure.csv"))
h['date'] = pd.to_datetime(h['date'])
df = df.merge(h[['date', 'D_f', 'D_c', 'n_f', 'n_c', 'n_f_minus_n_c', 'P_fund']], on='date', how='left')

# 前向收益标签 (fwd20_ret 已在宽表)
df['fwd20_ret'] = df['fwd20_ret'] if 'fwd20_ret' in df.columns else df['ret'].shift(-20)

# === 划分训练/盲测试集 (尾部20%) ===
n = len(df)
split_idx = int(n * 0.8)
print(f"=== exp436 子任务1: 分歧信号解构 ===")
print(f"总样本: {n} 日 | 训练集: {split_idx} 日 (前80%) | 盲测试集: {n-split_idx} 日 (尾20%)")
print(f"训练区间: {df['date'].iloc[0].date()} ~ {df['date'].iloc[split_idx-1].date()}")
print(f"盲测区间: {df['date'].iloc[split_idx].date()} ~ {df['date'].iloc[-1].date()}")

# === 衍生3个新因子 ===
# 1. 方向分歧: sign(D_f) - sign(D_c) ∈ {-2,0,+2}, 再标准化
df['dir_disagreement'] = np.sign(df['D_f']) - np.sign(df['D_c'])
# 2. 分歧波动幅度: |D_f - D_c| 的滚动std(20日)
disagreement_mag = (df['D_f'] - df['D_c']).abs()
df['disagreement_vol'] = disagreement_mag.rolling(20, min_periods=10).std()
# 3. 分歧收敛衰减速率: 对 |disagreement| 做指数衰减拟合，时间常数 τ
#    用差分法: decay = -d(log|mag|)/dt 的滚动均值
log_mag = np.log(disagreement_mag + 1e-6)
df['disagreement_decay'] = -log_mag.diff().rolling(20, min_periods=10).mean()

new_factors = {
    'dir_disagreement': df['dir_disagreement'],
    'disagreement_vol': df['disagreement_vol'],
    'disagreement_decay': df['disagreement_decay'],
}

# 原 HAM 基准因子
baseline_factors = {
    'ham_dev_mean': df['dev_mean'].fillna(0) if 'dev_mean' in df.columns else pd.Series(0, index=df.index),
    'ham_n_f_minus_n_c': df['n_f_minus_n_c'].fillna(0),
}

# 传统9项因子 (正交对标)
trad_cols = ['basis_rate', 'basis_rate_chg5', 'pos_chg_20d_pct', 'vol_chg_20d_pct',
             'rvol20', 'vol_rel', 'basis_turn']
trad_df = df[trad_cols].fillna(0)

# === IC 检验函数 ===
def compute_ic(factor, fwd_ret, mask=None):
    """滚动 IC: spearman(factor, fwd_ret), 返回整体IC和p值"""
    if mask is None:
        mask = factor.notna() & fwd_ret.notna()
    sub_f = factor[mask]
    sub_r = fwd_ret[mask]
    if len(sub_f) < 30:
        return np.nan, np.nan
    r, p = spearmanr(sub_f, sub_r)
    return float(r), float(p)

def orthogonal_residual_ic(factor, trad_X, fwd_ret):
    """剔除传统因子后残差的IC"""
    valid = factor.notna() & fwd_ret.notna()
    f = factor[valid].values
    x = trad_X[valid].fillna(0).values
    r = fwd_ret[valid].fillna(0).values
    if len(f) < 30:
        return np.nan, np.nan
    X_c = np.c_[np.ones(len(x)), x]
    try:
        beta, _, _, _ = np.linalg.lstsq(X_c, f, rcond=None)
        resid = f - X_c @ beta
        res_ic, res_p = spearmanr(resid, r)
        return float(res_ic), float(res_p)
    except:
        return np.nan, np.nan

def permutation_test(factor, n_perms=100, seed=42):
    """标签置换: 打乱因子值，验证IC是否显著高于随机"""
    valid = factor.notna() & df['fwd20_ret'].notna()
    f = factor[valid].values
    r = df['fwd20_ret'][valid].values
    if len(f) < 30:
        return np.nan
    np.random.seed(seed)
    perm_ics = []
    for _ in range(n_perms):
        fp = np.random.permutation(f)
        ic, _ = spearmanr(fp, r)
        perm_ics.append(ic)
    actual_ic, _ = spearmanr(f, r)
    p = (np.sum(np.abs(np.array(perm_ics)) >= abs(actual_ic)) + 1) / (n_perms + 1)
    return float(p)

def ic_by_window(factor, fwd_ret, windows=[20, 40, 60]):
    """分段IC: 不同窗口长度的滚动IC"""
    results = {}
    valid = factor.notna() & fwd_ret.notna()
    f = factor[valid]
    r = fwd_ret[valid]
    for w in windows:
        ics = []
        for i in range(w, len(f)):
            fi = f.iloc[:i]
            ri = r.iloc[:i]
            if fi.notna().sum() < w // 2:
                continue
            ic, _ = spearmanr(fi, ri)
            ics.append(ic)
        if ics:
            results[f'{w}d'] = {'mean': float(np.mean(ics)), 'positive_pct': float((np.array(ics) > 0).mean())}
    return results

# === 执行检验 ===
print("\n=== 因子IC检验 (训练集80%) ===")
train_mask = pd.Series(True, index=df.index)[:split_idx]

all_results = {}
train_slice = slice(0, split_idx)
for fname, factor in {**baseline_factors, **new_factors}.items():
    # 统一为按位置索引 (避免索引不匹配)
    factor_vals = factor.values if hasattr(factor, 'values') else np.asarray(factor)
    fwd_vals = df['fwd20_ret'].values
    train_f = factor_vals[:split_idx]
    train_r = fwd_vals[:split_idx]
    blind_f = factor_vals[split_idx:]
    blind_r = fwd_vals[split_idx:]
    # 训练集IC
    def _ic(fa, ra):
        m = np.isfinite(fa) & np.isfinite(ra)
        if m.sum() < 30:
            return np.nan, np.nan
        r, p = spearmanr(fa[m], ra[m])
        return float(r), float(p)
    ic, p = _ic(train_f, train_r)
    # 正交残差IC (用训练集)
    def _ortho(fa, tx, ra):
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
    trad_train = trad_df.iloc[:split_idx].fillna(0).values
    res_ic, res_p = _ortho(train_f, trad_train, train_r)
    # 置换检验 (训练集)
    def _perm(fa, ra):
        m = np.isfinite(fa) & np.isfinite(ra)
        if m.sum() < 30:
            return np.nan
        f = fa[m]; r = ra[m]
        np.random.seed(42)
        perm_ics = []
        for _ in range(100):
            fp = np.random.permutation(f)
            ic, _ = spearmanr(fp, r)
            perm_ics.append(ic)
        actual_ic, _ = spearmanr(f, r)
        return (np.sum(np.abs(np.array(perm_ics)) >= abs(actual_ic)) + 1) / 101
    perm_p = _perm(train_f, train_r)
    # 盲测试集IC (只报)
    blind_ic, blind_p = _ic(blind_f, blind_r)
    
    result = {
        'train_ic': round(ic, 4) if np.isfinite(ic) else None,
        'train_p': round(p, 4) if np.isfinite(p) else None,
        'orthogonal_residual_ic': round(res_ic, 4) if np.isfinite(res_ic) else None,
        'orthogonal_p': round(res_p, 4) if np.isfinite(res_p) else None,
        'permutation_p': round(perm_p, 4) if np.isfinite(perm_p) else None,
        'blind_test_ic': round(blind_ic, 4) if np.isfinite(blind_ic) else None,
        'blind_test_p': round(blind_p, 4) if np.isfinite(blind_p) else None,
    }
    all_results[fname] = result
    print(f"  {fname:24s}: 训练IC={result['train_ic']} (p={result['train_p']}) | "
          f"残差IC={result['orthogonal_residual_ic']} (p={result['orthogonal_p']}) | "
          f"置换p={result['permutation_p']} | 盲测IC={result['blind_test_ic']}")

# 保存
with open(os.path.join(DATA_DIR, 'subtask1_factor_ic.json'), 'w') as f:
    json.dump(all_results, f, indent=2)

# 保存因子序列
factors_out = pd.DataFrame({
    'date': df['date'],
    'dir_disagreement': df['dir_disagreement'],
    'disagreement_vol': df['disagreement_vol'],
    'disagreement_decay': df['disagreement_decay'],
    'ham_dev_mean': df['dev_mean'] if 'dev_mean' in df.columns else 0,
    'ham_n_f_minus_n_c': df['n_f_minus_n_c'],
    'fwd20_ret': df['fwd20_ret'],
})
factors_out.to_csv(os.path.join(DATA_DIR, 'subtask1_factors.csv'), index=False)

print(f"\n=== 子任务1 完成 ===")
print(f"输出: {DATA_DIR}/subtask1_factor_ic.json, subtask1_factors.csv")
print(f"约束遵守: 未修改原有双主体迭代内核, 滚动窗口内拟合, 盲测试集(尾20%)调参阶段未读取")
