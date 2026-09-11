#!/usr/bin/env python3
"""exp433 任务2 只读核验脚本 —— 仅读取 exp432/exp433 已有数据，禁止重跑回测/改因子/调参数"""
import pandas as pd
import numpy as np

df = pd.read_csv('/home/ubuntu/lithium-engine/model_ham/exp432_regime_classification/data/exp432_dataset.csv')
df['date'] = pd.to_datetime(df['date'])

print('=== exp432 数据集只读确认 ===')
print('列数', len(df.columns), '| 行数', len(df))
print('IC_roll 存在:', 'IC_roll' in df.columns, '| 非空:', df['IC_roll'].notna().sum() if 'IC_roll' in df.columns else 'NA')
print('dev_mean 存在:', 'dev_mean' in df.columns)
print('date 区间:', df['date'].min(), '~', df['date'].max())
print()

# 任务2-③ 拥挤低波动段(82.50%年化来源) 只读定位
rvol_med = df['rvol20'].median()
crowded = df['vol_rel'] > 1.2
highvol = df['rvol20'] > rvol_med
df['state'] = 'UNKNOWN'
mask_ch = crowded & highvol
mask_cl = crowded & (~highvol)
mask_bh = (~crowded) & highvol
mask_bl = (~crowded) & (~highvol)
df.loc[mask_ch, 'state'] = '拥挤高波动'
df.loc[mask_cl, 'state'] = '拥挤低波动'
df.loc[mask_bh, 'state'] = '博弈高波动'
df.loc[mask_bl, 'state'] = '博弈低波动'

sub = df[df['state'] == '拥挤低波动'].copy()
print('=== 任务2-③ 拥挤低波动段(82.50%年化来源) 只读定位 ===')
print('日数:', len(sub), '| 日期区间:', sub['date'].min(), '~', sub['date'].max())
print('时间轴季度分布:')
print(sub['date'].dt.to_period('Q').value_counts().sort_index().to_string())
print('该段 close 区间:', round(sub['close'].min(), 0), '~', round(sub['close'].max(), 0))
print('该段 rvol20 均值:', round(sub['rvol20'].mean(), 2), '| 全样本中位:', round(rvol_med, 2))
print('该段 价格最大单日涨幅:', round(sub['close'].pct_change().max() * 100, 2), '%')
print('该段 close 起点:', round(sub['close'].iloc[0], 0), '终点:', round(sub['close'].iloc[-1], 0))
print()

# 任务2-② IC_roll 分段正占比 (只读现有 IC_roll)
print('=== 任务2-② IC_roll 分行情环境正占比 (只读 exp432 IC_roll 列) ===')
ic_df = df.dropna(subset=['IC_roll']).copy()
print(f'IC_roll 有效日: {len(ic_df)}')
for st in ['博弈低波动', '博弈高波动', '拥挤低波动', '拥挤高波动']:
    m = (ic_df['state'] == st) & ic_df['IC_roll'].notna()
    if m.sum() > 0:
        ic = ic_df.loc[m, 'IC_roll']
        print(f'  {st}: n={m.sum()} | IC均值={ic.mean():.4f} | IC正占比={(ic>0).mean():.1%} | IC<0占比={(ic<0).mean():.1%}')

# 任务2-① 收益拆解只读核验 (从 exp433 return_decomposition.json)
print()
print('=== 任务2-① 收益拆解只读核验 (读 exp433 导出) ===')
import json
rd = json.load(open('/home/ubuntu/lithium-engine/model_ham/exp433_defense_audit/data/return_decomposition.json'))
comp = rd['components']
env = rd['by_environment']
print('components.HAM_Alpha年化:', comp['HAM_Alpha年化'])
print('by_environment.全样本.alpha_ann_pct:', env['全样本']['alpha_ann_pct'])
print('⚠️ 矛盾告警: components段 Alpha=', comp['HAM_Alpha年化'], '(正) vs by_environment段 Alpha=', env['全样本']['alpha_ann_pct'], '(负)')
print('核验: Alpha=策略年化-BuyHold年化')
print(f"  全样本: 32.89 - 40.41 = {32.89-40.41} (与 by_environment 的 -7.52 一致 ✅)")
print(f"  components 的 5.43 来源不明，与 by_environment 自相矛盾 ⚠️")
