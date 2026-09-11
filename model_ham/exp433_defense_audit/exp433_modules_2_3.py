#!/usr/bin/env python3
"""exp433 模块2-3: 过拟合检验 + 因子正交性"""
import json, os, sys
import numpy as np
import pandas as pd
from scipy import stats
from scipy.stats import spearmanr
from numpy.linalg import lstsq

MODEL = "/home/ubuntu/lithium-engine/model_ham"
OUT_DIR = os.path.join(MODEL, "exp433_defense_audit")
DATA_DIR = os.path.join(OUT_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)

# === 加载数据 ===
df = pd.read_csv(os.path.join(MODEL, "exp432_regime_classification/data/exp432_dataset.csv"))
df['date'] = pd.to_datetime(df['date'])
pipe = pd.read_csv(os.path.join(MODEL, "exp429_production/data/exp429_daily_pipeline.csv"))
pipe['date'] = pd.to_datetime(pipe['date'])
mon = pd.read_csv(os.path.join(MODEL, "exp429_production/data/exp429_monitor_metrics.csv"))
mon['date'] = pd.to_datetime(mon['date'])

df = df.merge(pipe[['date', 'pos_A', 'action_A']], on='date', how='left')
if 'IC_roll' not in df.columns or df['IC_roll'].isna().all():
    df = df.merge(mon[['date', 'IC_roll']], on='date', how='left', suffixes=('', '_mon'))

# 基准绩效
df['ret'] = df['close'].pct_change()
df['pos_A'] = df['pos_A'].fillna(0)
df['overnight_ret'] = df['pos_A'].shift(1) * (df['open'] - df['close'].shift(1)) / df['close'].shift(1)
df['intraday_ret'] = df['pos_A'] * (df['close'] - df['open']) / df['open']
COST = 0.0015; SLIP = 0.001
df['cost_ret'] = (2 * COST + 2 * SLIP) * np.abs(df['pos_A'].diff())
df['net_ret_A'] = df['overnight_ret'] + df['intraday_ret'] - df['cost_ret']

valid = df.dropna(subset=['net_ret_A'])
ann_ret = valid['net_ret_A'].mean() * 250
nav = (1 + valid['net_ret_A'].values).cumprod() * 1e6
peak = np.maximum.accumulate(nav)
mdd = ((nav - peak) / peak).min()
calmar = ann_ret / abs(mdd) if mdd != 0 else 0
n_trades = int((np.abs(df['pos_A'].diff()) > 1e-9).sum())

print(f"=== 基准绩效 ===")
print(f"年化: {ann_ret*100:.2f}%, 回撤: {mdd*100:.2f}%, Calmar: {calmar:.2f}, 交易笔数: {n_trades}")

# === 模块2: 过拟合检验 ===
print("\n=== 模块2: 过拟合检验 ===")

# 2.1 参数敏感性扫描
print("\n--- 2.1 参数敏感性扫描 ---")
sensitivity = []
for adx_s in [0.1, 0.2, 0.3, 0.4, 0.5, 0.7, 1.0]:
    for slip in [0, 0.001, 0.002, 0.005, 0.01, 0.015, 0.02]:
        adx_proxy = (df['rvol20_rel'] > 1.5).values
        pos_adj = df['pos_A'].values.copy()
        pos_adj[adx_proxy] *= adx_s
        cost_adj = (2 * COST + 2 * slip) * np.abs(np.diff(np.r_[0, pos_adj]))
        net = np.zeros(len(df))
        net[1:] = pos_adj[:-1] * (df['open'].values[1:] - df['close'].values[:-1]) / df['close'].values[:-1]
        net[1:] += pos_adj[1:] * (df['close'].values[1:] - df['open'].values[1:]) / df['open'].values[1:]
        net[1:] -= cost_adj[:len(net)-1]
        ann = np.mean(net[1:]) * 250
        nav_ = np.cumprod(1 + net) * 1e6
        pk = np.maximum.accumulate(nav_)
        md = ((nav_ - pk) / pk).min()
        cl = ann / abs(md) if md != 0 else 0
        sensitivity.append({
            'adx_scale': adx_s, 'slip_bp': int(slip * 10000),
            'ann_ret': round(ann * 100, 2), 'mdd': round(md * 100, 2), 'calmar': round(cl, 2),
        })

with open(os.path.join(DATA_DIR, 'param_sensitivity.json'), 'w') as f:
    json.dump(sensitivity, f, indent=2)

profitable = [s for s in sensitivity if s['ann_ret'] > 0 and s['calmar'] > 1]
print(f"参数组合: {len(sensitivity)}, 盈利且Calmar>1: {len(profitable)} ({len(profitable)/len(sensitivity)*100:.0f}%)")

# 检查参数孤岛: 看高绩效区域是否连续
grid = {}
for s in sensitivity:
    key = s['adx_scale']
    if key not in grid:
        grid[key] = []
    grid[key].append(s['calmar'])
print(f"各ADX_scale下Calmar范围: " + ", ".join(f"{k}:{min(v):.1f}~{max(v):.1f}" for k, v in sorted(grid.items())))

# 2.2 标签置换检验 (打乱仓位方向标签，保留收益时间序列)
print("\n--- 2.2 标签置换检验 (100次, 打乱仓位符号) ---")
np.random.seed(42)
actual_ret = df['net_ret_A'].dropna().values
actual_ann = np.mean(actual_ret) * 250
pos_arr = df['pos_A'].fillna(0).values
ret_arr = df['ret'].fillna(0).values
open_arr = df['open'].values
close_arr = df['close'].values
n = len(df)
perm_stats = []
for i in range(100):
    # 打乱仓位符号（+1/-1 方向随机化），保持仓位大小不变
    signs = np.random.choice([-1, 1], size=n)
    perm_pos = pos_arr * signs
    perm_net = np.zeros(n)
    perm_net[1:] = perm_pos[:-1] * (open_arr[1:] - close_arr[:-1]) / close_arr[:-1]
    perm_net[1:] += perm_pos[1:] * (close_arr[1:] - open_arr[1:]) / open_arr[1:]
    perm_net[1:] -= (2 * COST + 2 * SLIP) * np.abs(np.diff(np.r_[0, perm_pos]))[:n-1]
    perm_ann = np.mean(perm_net[1:]) * 250
    perm_stats.append(perm_ann)

p_value = (np.sum(np.array(perm_stats) >= actual_ann) + 1) / (len(perm_stats) + 1)
print(f"实际年化: {actual_ann*100:.2f}%, 置换均值: {np.mean(perm_stats)*100:.2f}%, p-value: {p_value:.4f}")
print(f"置换分布: 5%={np.percentile(perm_stats, 5)*100:.2f}%, 95%={np.percentile(perm_stats, 95)*100:.2f}%")
print(f"置换中盈利比例: {sum(1 for s in perm_stats if s > 0)/len(perm_stats):.0%}")

# 2.3 随机时间切片 IC
print("\n--- 2.3 随机时间切片 IC ---")
ic_series = df['IC_roll'].dropna()
if len(ic_series) > 60:
    slice_ics = []
    np.random.seed(123)
    for _ in range(100):
        start = np.random.randint(0, len(ic_series) - 60)
        chunk = ic_series.iloc[start:start+60]
        slice_ics.append({
            'mean': chunk.mean(),
            'positive_pct': (chunk > 0).mean(),
        })
    ic_means = [s['mean'] for s in slice_ics]
    ic_pos = [s['positive_pct'] for s in slice_ics]
    print(f"随机60日切片IC: 均值={np.mean(ic_means):.4f}, std={np.std(ic_means):.4f}")
    print(f"IC正占比: 均值={np.mean(ic_pos):.2%}, 负切片数: {sum(1 for m in ic_means if m < 0)}/100")
else:
    ic_means = [0]
    ic_pos = [0]

perm_results = {
    'actual_ann_ret_pct': round(actual_ann * 100, 2),
    'perm_mean_pct': round(np.mean(perm_stats) * 100, 2),
    'perm_std_pct': round(np.std(perm_stats) * 100, 2),
    'perm_p5_pct': round(np.percentile(perm_stats, 5) * 100, 2),
    'perm_p95_pct': round(np.percentile(perm_stats, 95) * 100, 2),
    'p_value': round(p_value, 4),
    'significant_5pct': bool(p_value < 0.05),
    'n_perms': 100,
    'random_slice_ic_mean': round(float(np.mean(ic_means)), 4),
    'random_slice_ic_std': round(float(np.std(ic_means)), 4),
    'random_slice_negative_count': int(sum(1 for m in ic_means if m < 0)),
    'random_slice_positive_pct_mean': round(float(np.mean(ic_pos)), 4),
}
with open(os.path.join(DATA_DIR, 'permutation_test.json'), 'w') as f:
    json.dump(perm_results, f, indent=2)

# === 模块3: 因子正交性 ===
print("\n=== 模块3: 因子正交性 ===")

trad_factors = pd.DataFrame({
    'basis_rate': df['basis_rate'],
    'basis_chg5': df['basis_rate_chg5'],
    'pos_chg_20d': df['pos_chg_20d_pct'],
    'vol_chg_20d': df['vol_chg_20d_pct'],
    'rvol20': df['rvol20'],
    'momentum_20d': df['close'].pct_change(20) * 100,
    'bias_rate': (df['close'] / df['close'].rolling(20).mean() - 1) * 100,
    'position_level': df['position'].pct_change() * 100,
    'volume_level': df['vol_rel'],
    'term_structure': df['basis_turn'],
})

# HAM deviation as proxy
ham_dev = df['dev_mean'].fillna(0) if 'dev_mean' in df.columns else df['IC_roll'].fillna(0)
trad_factors['ham_deviation'] = ham_dev
trad_factors['next_ret'] = df['ret'].shift(-1)

corr = trad_factors.drop(columns=['next_ret']).corr()
corr.to_csv(os.path.join(DATA_DIR, 'correlation_matrix.csv'))

# VIF
vif_data = trad_factors.drop(columns=['next_ret', 'term_structure']).dropna()
vif_results = {}
for i, col in enumerate(vif_data.columns):
    try:
        from statsmodels.stats.outliers_influence import variance_inflation_factor
        vif = variance_inflation_factor(vif_data.values, i)
        vif_results[col] = round(float(vif), 2)
    except:
        vif_results[col] = float('inf')

high_vif = {k: v for k, v in vif_results.items() if v > 10}
print(f"VIF>10: {high_vif if high_vif else '无'}")

# HAM正交残差IC
X = trad_factors.drop(columns=['next_ret', 'term_structure', 'ham_deviation']).fillna(0).values
y = ham_dev.values
next_ret = trad_factors['next_ret'].fillna(0).values
X_c = np.c_[np.ones(len(X)), X]
beta_hat, _, _, _ = lstsq(X_c, y, rcond=None)
residual = y - X_c @ beta_hat

mask = (~np.isnan(y)) & (~np.isnan(next_ret)) & (np.abs(y) > 0)
if mask.sum() > 10:
    r_orig, p_orig = spearmanr(y[mask], next_ret[mask])
    r_res, p_res = spearmanr(residual[mask], next_ret[mask])
else:
    r_orig = p_orig = r_res = p_res = 0

info_ret = abs(r_res) / abs(r_orig) * 100 if abs(r_orig) > 0.001 else 0

print(f"HAM原始IC: {r_orig:.4f} (p={p_orig:.4f})")
print(f"正交残差IC: {r_res:.4f} (p={p_res:.4f})")
print(f"信息增益保留: {info_ret:.1f}%")

ortho_results = {
    'ham_ic_original': round(float(r_orig), 4),
    'ham_ic_pvalue': round(float(p_orig), 4),
    'ham_orthogonal_residual_ic': round(float(r_res), 4),
    'residual_pvalue': round(float(p_res), 4),
    'info_retention_pct': round(float(info_ret), 1),
    'vif': vif_results,
    'high_vif_factors': high_vif,
    'corr_ham_vs_trad': {col: round(float(corr.loc['ham_deviation', col]), 3) for col in corr.columns if col != 'ham_deviation'},
}
with open(os.path.join(DATA_DIR, 'orthogonality.json'), 'w') as f:
    json.dump(ortho_results, f, indent=2, default=str)

print("\n=== 模块2-3 完成 ===")
print(f"输出: {DATA_DIR}")
for f in os.listdir(DATA_DIR):
    print(f"  {f}: {os.path.getsize(os.path.join(DATA_DIR, f))} bytes")
