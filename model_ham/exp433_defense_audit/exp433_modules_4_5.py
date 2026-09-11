#!/usr/bin/env python3
"""exp433 模块4-5: 收益来源拆解 + 稳定性分环境检验"""
import json, os, sys
import numpy as np
import pandas as pd
from scipy.stats import spearmanr

MODEL = "/home/ubuntu/lithium-engine/model_ham"
DATA_DIR = os.path.join(MODEL, "exp433_defense_audit", "data")

df = pd.read_csv(os.path.join(MODEL, "exp432_regime_classification/data/exp432_dataset.csv"))
df['date'] = pd.to_datetime(df['date'])
pipe = pd.read_csv(os.path.join(MODEL, "exp429_production/data/exp429_daily_pipeline.csv"))
pipe['date'] = pd.to_datetime(pipe['date'])
df = df.merge(pipe[['date', 'pos_A']], on='date', how='left')

df['ret'] = df['close'].pct_change()
df['pos_A'] = df['pos_A'].fillna(0)
COST = 0.0015; SLIP = 0.001

# 净收益
df['overnight_ret'] = df['pos_A'].shift(1) * (df['open'] - df['close'].shift(1)) / df['close'].shift(1)
df['intraday_ret'] = df['pos_A'] * (df['close'] - df['open']) / df['open']
df['cost_ret'] = (2 * COST + 2 * SLIP) * np.abs(df['pos_A'].diff())
df['net_ret_A'] = df['overnight_ret'] + df['intraday_ret'] - df['cost_ret']

# Buy&Hold收益 (Beta)
df['bh_ret'] = df['ret']

# === 模块4: 收益来源拆解 ===
print("=== 模块4: 收益来源拆解 ===")

# 1. HAM Alpha = 策略净收益 - Buy&Hold收益 (超额)
df['alpha_ret'] = df['net_ret_A'] - df['bh_ret']
# 2. Logit自适应增益 = 滚动估参收益 - 固定参数收益 (用IC差异代理)
# exp426消融: 固定权重-6.6pp, 即 Logit增益 ≈ 6.6pp
# 直接用 IC 正占比差异代理
# 3. 仓位调整收益 = ADX降权减少的收益 vs 不降权
# exp426: 关ADX +3.2pp收益但回撤恶化
# 4. Beta = Buy&Hold

ann = lambda r: r.mean() * 250 * 100 if len(r) > 0 else 0
components = {
    'HAM_Alpha年化': round(ann(df['alpha_ret'].dropna()), 2),
    'BuyHold_Beta年化': round(ann(df['bh_ret'].dropna()), 2),
    '总策略年化': round(ann(df['net_ret_A'].dropna()), 2),
    'Logit自适应增益_年化(代理)': 6.6,  # exp426消融锚定值
    'ADX降权收益成本_年化(代理)': -3.2,  # exp426消融锚定值
    '仓位调整净值_年化(代理)': 3.2,  # ADX关闭多出的收益
}

# 分环境: 拥挤一致行情 vs 博弈行情
# 用IC_roll符号分类
if 'IC_roll' in df.columns:
    ic_valid = df['IC_roll'].notna()
    ic_pos = ic_valid & (df['IC_roll'] > 0)   # 博弈行情
    ic_neg = ic_valid & (df['IC_roll'] <= 0)  # 拥挤一致

    env_results = {}
    for label, mask in [('博弈行情(IC>0)', ic_pos), ('拥挤一致(IC<=0)', ic_neg), ('全样本', ic_valid)]:
        if mask.sum() < 5:
            continue
        sub = df[mask]
        net = sub['net_ret_A'].dropna()
        bh = sub['bh_ret'].dropna()
        alpha = sub['alpha_ret'].dropna()
        nav = (1 + net.values).cumprod() * 1e6
        peak = np.maximum.accumulate(nav)
        md = ((nav - peak) / peak).min() if len(nav) > 0 else 0
        hold_pct = (sub['pos_A'].abs() > 1e-9).mean()
        
        env_results[label] = {
            'n_days': int(mask.sum()),
            'ann_ret_pct': round(ann(net), 2),
            'bh_ann_ret_pct': round(ann(bh), 2),
            'alpha_ann_pct': round(ann(alpha), 2),
            'mdd_pct': round(md * 100, 2),
            'hold_days_pct': round(hold_pct * 100, 1),
        }
        print(f"\n{label} ({mask.sum()}日):")
        print(f"  策略年化: {ann(net):.2f}%, BuyHold: {ann(bh):.2f}%, Alpha: {ann(alpha):.2f}%")
        print(f"  回撤: {md*100:.2f}%, 持仓占比: {hold_pct:.1%}")

    with open(os.path.join(DATA_DIR, 'return_decomposition.json'), 'w') as f:
        json.dump({'components': components, 'by_environment': env_results}, f, indent=2)

# === 模块5: 稳定性分环境检验 ===
print("\n\n=== 模块5: 稳定性分环境检验 ===")

# 5.1 20/40/60日滚动IC
df['next_ret'] = df['ret'].shift(-1)
hold_mask = df['pos_A'].abs() > 1e-9
ic_by_window = {}
for w in [20, 40, 60]:
    ic_series = []
    for i in range(w, len(df) - 1):
        mask = hold_mask.iloc[i-w:i]
        if mask.sum() < 5:
            ic_series.append(np.nan)
            continue
        r, p = spearmanr(df['pos_A'].iloc[i-w:i], df['next_ret'].iloc[i-w:i])
        ic_series.append(r)
    ic_arr = np.array(ic_series)
    ic_arr = ic_arr[~np.isnan(ic_arr)]
    ic_by_window[f'{w}d'] = {
        'mean': round(float(np.mean(ic_arr)), 4) if len(ic_arr) > 0 else 0,
        'std': round(float(np.std(ic_arr)), 4) if len(ic_arr) > 0 else 0,
        'positive_pct': round(float((ic_arr > 0).mean()), 4) if len(ic_arr) > 0 else 0,
        'median': round(float(np.median(ic_arr)), 4) if len(ic_arr) > 0 else 0,
        'p5': round(float(np.percentile(ic_arr, 5)), 4) if len(ic_arr) > 0 else 0,
        'p95': round(float(np.percentile(ic_arr, 95)), 4) if len(ic_arr) > 0 else 0,
    }
    print(f"{w}日IC: 均值={ic_by_window[f'{w}d']['mean']:.4f}, std={ic_by_window[f'{w}d']['std']:.4f}, 正占比={ic_by_window[f'{w}d']['positive_pct']:.1%}")

# 5.2 4类市场状态分类
# 拥挤度: vol_rel > 1.2 = 拥挤; 波动率: rvol20 > median = 高波动
rvol_median = df['rvol20'].median()
vol_rel_median = df['vol_rel'].median()

df['state'] = 'UNKNOWN'
for i in range(len(df)):
    crowded = df['vol_rel'].iloc[i] > 1.2
    high_vol = df['rvol20'].iloc[i] > rvol_median
    if crowded and high_vol:
        df.iloc[i, df.columns.get_loc('state')] = '拥挤高波动'
    elif crowded and not high_vol:
        df.iloc[i, df.columns.get_loc('state')] = '拥挤低波动'
    elif not crowded and high_vol:
        df.iloc[i, df.columns.get_loc('state')] = '博弈高波动'
    else:
        df.iloc[i, df.columns.get_loc('state')] = '博弈低波动'

state_results = {}
for state in ['博弈低波动', '博弈高波动', '拥挤低波动', '拥挤高波动']:
    mask = df['state'] == state
    if mask.sum() < 10:
        continue
    sub = df[mask]
    net = sub['net_ret_A'].dropna()
    nav = (1 + net.values).cumprod() * 1e6
    peak = np.maximum.accumulate(nav)
    md = ((nav - peak) / peak).min() if len(nav) > 0 else 0
    
    # 胜率
    wins = (net > 0).sum()
    total = (net != 0).sum()
    win_rate = wins / total if total > 0 else 0
    
    # 盈亏比
    gains = net[net > 0]
    losses = net[net < 0]
    avg_gain = gains.mean() if len(gains) > 0 else 0
    avg_loss = abs(losses.mean()) if len(losses) > 0 else 0.001
    pf = avg_gain / avg_loss if avg_loss > 0 else 0
    
    # IC
    hold = sub['pos_A'].abs() > 1e-9
    if hold.sum() > 5 and sub['next_ret'].notna().sum() > 5:
        r, _ = spearmanr(sub.loc[hold, 'pos_A'], sub.loc[hold, 'next_ret'])
    else:
        r = 0
    
    state_results[state] = {
        'n_days': int(mask.sum()),
        'ann_ret_pct': round(ann(net), 2),
        'mdd_pct': round(md * 100, 2),
        'ic_mean': round(float(r), 4),
        'win_rate': round(float(win_rate), 4),
        'profit_factor': round(float(pf), 2),
        'hold_pct': round(float((sub['pos_A'].abs() > 1e-9).mean()), 4),
    }
    print(f"\n{state} ({mask.sum()}日):")
    print(f"  年化: {ann(net):.2f}%, 回撤: {md*100:.2f}%, IC: {r:.4f}")
    print(f"  胜率: {win_rate:.1%}, 盈亏比: {pf:.2f}, 持仓占比: {(sub['pos_A'].abs() > 1e-9).mean():.1%}")

with open(os.path.join(DATA_DIR, 'stability_by_state.json'), 'w') as f:
    json.dump({'ic_by_window': ic_by_window, 'state_results': state_results}, f, indent=2)

print("\n=== 模块4-5 完成 ===")
print(f"输出: {DATA_DIR}")
for f in os.listdir(DATA_DIR):
    print(f"  {f}: {os.path.getsize(os.path.join(DATA_DIR, f))} bytes")
