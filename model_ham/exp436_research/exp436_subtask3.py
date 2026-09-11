#!/usr/bin/env python3
"""
exp436 子任务3: 多主体拓展模型 (学术向)
- 扩展至3-4类交易主体 (贸易中间商、储能采购盘)
- 仅离线试算, 重点输出参数膨胀风险、样本稳定性问题
- 不优先判定有效性
"""
import json, os, sys
import numpy as np
import pandas as pd
from scipy.stats import spearmanr

MODEL = "/home/ubuntu/lithium-engine/model_ham"
OUT_DIR = os.path.join(MODEL, "exp436_research")
DATA_DIR = os.path.join(OUT_DIR, "data")

df = pd.read_csv(os.path.join(MODEL, "exp432_regime_classification/data/exp432_dataset.csv"))
df['date'] = pd.to_datetime(df['date'])
h = pd.read_csv(os.path.join(MODEL, "ham_experiment_archive/intermediate/exp401_ham_factors_pure.csv"))
h['date'] = pd.to_datetime(h['date'])
df = df.merge(h[['date', 'D_f', 'D_c', 'n_f', 'n_c', 'P_fund', 'profit_f', 'profit_c']], on='date', how='left')
df['fwd20_ret'] = df['fwd20_ret'] if 'fwd20_ret' in df.columns else df['ret'].shift(-20)

n = len(df)
split_idx = int(n * 0.8)

print(f"=== exp436 子任务3: 多主体拓展模型 (学术向) ===")
print(f"总样本: {n} 日 | 训练: {split_idx} 日 | 盲测: {n-split_idx} 日")

# === 主体定义 ===
# 原版2主体:
#   F = 产业基本面 Agent (P_fund - close, 回归压力)
#   C = 投机趋势 Agent (close - close_prev, 动量)
# 拓展主体:
#   T = 贸易中间商 (basis_rate 驱动, 贴水看空/升水看多)
#   S = 储能采购盘 (vol_rel + 长期趋势, 逆周期布局)

close = df['close'].values
close_prev = np.r_[np.nan, close[:-1]]
ret_c = close - close_prev
pfund = df['P_fund'].values
basis_rate = df['basis_rate'].fillna(0).values
vol_rel = df['vol_rel'].fillna(1.0).values
# 长期动量 (60日)
long_mom = pd.Series(close).rolling(60, min_periods=20).mean().shift(1).values

# 4主体分歧信号
df['D_T'] = basis_rate  # 贸易中间商: 基差直接驱动
df['D_S'] = (vol_rel - 1) * long_mom  # 储能采购: 活跃度×长期动量 (逆周期)

# 原版2主体合计信号
df['signal_2agent'] = df['D_f'] - df['D_c']  # 原版分歧

# 3主体: 加贸易中间商
df['signal_3agent'] = df['D_f'] - df['D_c'] + 0.5 * df['D_T']

# 4主体: 加储能采购
df['signal_4agent'] = df['D_f'] - df['D_c'] + 0.5 * df['D_T'] + 0.3 * df['D_S']

# === 检验 ===
def _ic(fa, ra):
    m = np.isfinite(fa) & np.isfinite(ra)
    if m.sum() < 30:
        return np.nan, np.nan
    try:
        return float(spearmanr(fa[m], ra[m])[0]), float(spearmanr(fa[m], ra[m])[1])
    except:
        return np.nan, np.nan

def _perm_p(fa, ra):
    m = np.isfinite(fa) & np.isfinite(ra)
    if m.sum() < 30:
        return np.nan
    f = fa[m]; r = ra[m]
    np.random.seed(42)
    actual = abs(spearmanr(f, r)[0])
    perms = [abs(spearmanr(np.random.permutation(f), r)[0]) for _ in range(100)]
    return (np.sum(np.array(perms) >= actual) + 1) / 101

factors = {
    '2agent_baseline': df['signal_2agent'].values,
    '3agent_trade': df['signal_3agent'].values,
    '4agent_energy': df['signal_4agent'].values,
}

# 传统因子
trad_cols = ['basis_rate', 'basis_rate_chg5', 'pos_chg_20d_pct', 'vol_chg_20d_pct', 'rvol20', 'vol_rel', 'basis_turn']
trad_train = df[trad_cols].iloc[:split_idx].fillna(0).values

def _ortho(fa, tx, ra):
    m = np.isfinite(fa) & np.isfinite(ra)
    if m.sum() < 30:
        return np.nan, np.nan
    f = fa[m]; x = tx[m]; r = ra[m]
    X_c = np.c_[np.ones(len(x)), x]
    try:
        beta, _, _, _ = np.linalg.lstsq(X_c, f, rcond=None)
        resid = f - X_c @ beta
        return float(spearmanr(resid, r)[0]), float(spearmanr(resid, r)[1])
    except:
        return np.nan, np.nan

print("\n=== 多主体信号对比 ===")
results = {}
for fname, fv in factors.items():
    train_f = fv[:split_idx]
    train_r = df['fwd20_ret'].values[:split_idx]
    blind_f = fv[split_idx:]
    blind_r = df['fwd20_ret'].values[split_idx:]
    ic, p = _ic(train_f, train_r)
    res_ic, res_p = _ortho(train_f, trad_train, train_r)
    perm_p = _perm_p(train_f, train_r)
    blind_ic, blind_p = _ic(blind_f, blind_r)
    results[fname] = {
        'train_ic': round(ic, 4) if np.isfinite(ic) else None,
        'train_p': round(p, 4) if np.isfinite(p) else None,
        'orthogonal_ic': round(res_ic, 4) if np.isfinite(res_ic) else None,
        'permutation_p': round(perm_p, 4) if np.isfinite(perm_p) else None,
        'blind_ic': round(blind_ic, 4) if np.isfinite(blind_ic) else None,
    }
    print(f"  {fname:18s}: 训练IC={results[fname]['train_ic']} (p={results[fname]['train_p']}) | "
          f"残差IC={results[fname]['orthogonal_ic']} | 置换p={results[fname]['permutation_p']} | "
          f"盲测IC={results[fname]['blind_ic']}")

# === 参数膨胀分析 ===
# 每增加一个主体, 增加: 1个权重系数 + 1个参数集(至少alpha+beta) = 2参数
# 加上主体间的交互项, 组合爆炸
n_agents = {'2agent': 2, '3agent': 3, '4agent': 4}
params_per_agent = 2  # alpha + beta 最低
interactions = {2: 1, 3: 3, 4: 6}  # C(n,2)
results['parameter_analysis'] = {
    'baseline_2agent': {'agents': 2, 'params': 2 * params_per_agent, 'interactions': interactions[2], 'total': 2*params_per_agent + interactions[2]},
    'expanded_3agent': {'agents': 3, 'params': 3 * params_per_agent, 'interactions': interactions[3], 'total': 3*params_per_agent + interactions[3]},
    'expanded_4agent': {'agents': 4, 'params': 4 * params_per_agent, 'interactions': interactions[4], 'total': 4*params_per_agent + interactions[4]},
}

# === 样本稳定性: 滚动窗口IC离散度 ===
stability = {}
for fname, fv in factors.items():
    ics = []
    valid = np.isfinite(fv) & np.isfinite(df['fwd20_ret'].values)
    for w in [20, 40, 60]:
        w_ics = []
        for i in range(w, split_idx):
            fa = fv[i-w:i]
            ra = df['fwd20_ret'].values[i-w:i]
            m = np.isfinite(fa) & np.isfinite(ra)
            if m.sum() < w // 2:
                continue
            ic, _ = _ic(fa, ra)
            if np.isfinite(ic):
                w_ics.append(ic)
        if w_ics:
            stability[f'{fname}_{w}d'] = {
                'mean': round(np.mean(w_ics), 4),
                'std': round(np.std(w_ics), 4),
                'positive_pct': round((np.array(w_ics) > 0).mean(), 4),
            }

results['stability'] = stability

with open(os.path.join(DATA_DIR, 'subtask3_multi_agent.json'), 'w') as f:
    json.dump(results, f, indent=2)

print(f"\n=== 参数膨胀分析 ===")
for k, v in results['parameter_analysis'].items():
    print(f"  {k}: {v['agents']}主体, {v['total']}参数, {v['interactions']}交互项")

print(f"\n=== 样本稳定性 (滚动IC离散度) ===")
for k, v in stability.items():
    print(f"  {k}: 均值={v['mean']}, std={v['std']}, 正占比={v['positive_pct']:.1%}")

print(f"\n=== 子任务3 完成 ===")
print(f"输出: {DATA_DIR}/subtask3_multi_agent.json")
print(f"约束: 仅离线试算, 不判定有效性, 参数膨胀风险量化完成")
