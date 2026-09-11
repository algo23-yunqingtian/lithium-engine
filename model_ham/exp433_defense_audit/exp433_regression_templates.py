#!/usr/bin/env python3
"""
exp433 任务3: 自动化回归脚本 — 新增4项校验模板
约束: 仅新增检测代码，不改动 HAM 主引擎 (ham_pipeline.py / exp409/424/425/428等)

4项校验:
  1. 置换检验模板 (permutation test)
  2. 因子正交校验模板 (orthogonality check)
  3. IC 分段稳定性模板 (IC stability by segment)
  4. Alpha-Beta 收益拆解模板 (return decomposition)

使用方式:
  python exp433_regression_templates.py --check all     # 全部校验
  python exp433_regression_templates.py --check perm     # 仅置换检验
  python exp433_regression_templates.py --check ortho    # 仅正交性
  python exp433_regression_templates.py --check ic      # 仅IC稳定性
  python exp433_regression_templates.py --check alpha    # 仅收益拆解

数据依赖:
  - exp432_dataset.csv (因子宽表)
  - exp429_daily_pipeline.csv (仓位序列)
  不依赖 ham_pipeline.py 或任何主引擎代码。
"""
import json, os, argparse
import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from numpy.linalg import lstsq

MODEL = "/home/ubuntu/lithium-engine/model_ham"
DATA_DIR = os.path.join(MODEL, "exp433_defense_audit", "data")
DATASET = os.path.join(MODEL, "exp432_regime_classification/data/exp432_dataset.csv")
PIPELINE = os.path.join(MODEL, "exp429_production/data/exp429_daily_pipeline.csv")

COST = 0.0015
SLIP = 0.001


def load_data():
    """加载数据（不依赖主引擎）"""
    df = pd.read_csv(DATASET)
    df['date'] = pd.to_datetime(df['date'])
    pipe = pd.read_csv(PIPELINE)
    pipe['date'] = pd.to_datetime(pipe['date'])
    df = df.merge(pipe[['date', 'pos_A']], on='date', how='left')
    df['pos_A'] = df['pos_A'].fillna(0)
    df['ret'] = df['close'].pct_change()
    df['overnight_ret'] = df['pos_A'].shift(1) * (df['open'] - df['close'].shift(1)) / df['close'].shift(1)
    df['intraday_ret'] = df['pos_A'] * (df['close'] - df['open']) / df['open']
    df['cost_ret'] = (2 * COST + 2 * SLIP) * np.abs(df['pos_A'].diff())
    df['net_ret'] = df['overnight_ret'] + df['intraday_ret'] - df['cost_ret']
    df['bh_ret'] = df['ret']
    df['alpha_ret'] = df['net_ret'] - df['bh_ret']
    return df


# ============================================================
# 校验1: 置换检验模板
# ============================================================
def check_permutation(df, n_perms=100, seed=42):
    """
    标签置换检验: 打乱仓位方向符号，验证收益是否显著高于随机。
    与 exp433 原始置换检验一致: 保留仓位大小和交易时机，仅打乱方向符号。
    """
    print("\n" + "=" * 60)
    print("校验1: 置换检验 (Permutation Test)")
    print("=" * 60)

    actual_ret = df['net_ret'].dropna().values
    actual_ann = np.mean(actual_ret) * 250
    pos_arr = df['pos_A'].fillna(0).values
    open_arr = df['open'].values
    close_arr = df['close'].values
    n = len(df)

    np.random.seed(seed)
    perm_stats = []
    for i in range(n_perms):
        signs = np.random.choice([-1, 1], size=n)
        perm_pos = pos_arr * signs
        perm_net = np.zeros(n)
        perm_net[1:] = perm_pos[:-1] * (open_arr[1:] - close_arr[:-1]) / close_arr[:-1]
        perm_net[1:] += perm_pos[1:] * (close_arr[1:] - open_arr[1:]) / open_arr[1:]
        perm_net[1:] -= (2 * COST + 2 * SLIP) * np.abs(np.diff(np.r_[0, perm_pos]))[:n-1]
        perm_stats.append(np.mean(perm_net[1:]) * 250)

    p_value = (np.sum(np.array(perm_stats) >= actual_ann) + 1) / (len(perm_stats) + 1)

    # 基线对比
    baseline = json.load(open(os.path.join(DATA_DIR, 'permutation_test.json')))
    match = abs(p_value - baseline['p_value']) < 0.01

    print(f"  实际年化: {actual_ann*100:.2f}%")
    print(f"  置换均值: {np.mean(perm_stats)*100:.2f}%")
    print(f"  p-value: {p_value:.4f}")
    print(f"  基线 p-value: {baseline['p_value']:.4f}")
    print(f"  ✅ 一致" if match else f"  ⚠️ 不一致")

    return {'actual_ann': actual_ann, 'p_value': p_value, 'baseline_p': baseline['p_value'], 'match': match}


# ============================================================
# 校验2: 因子正交校验模板
# ============================================================
def check_orthogonality(df):
    """
    HAM 因子与传统因子的正交性校验。
    计算: 相关系数矩阵 + HAM 正交残差 IC + 信息增益保留。
    """
    print("\n" + "=" * 60)
    print("校验2: 因子正交性 (Orthogonality Check)")
    print("=" * 60)

    ham_dev = df['dev_mean'].fillna(0) if 'dev_mean' in df.columns else df['IC_roll'].fillna(0)
    next_ret = df['ret'].shift(-1).fillna(0)

    # HAM 原始 IC
    mask = np.abs(ham_dev.values) > 0
    r_orig, p_orig = spearmanr(ham_dev.values[mask], next_ret.values[mask])

    # 正交残差 IC
    trad_cols = ['basis_rate', 'basis_rate_chg5', 'pos_chg_20d_pct', 'vol_chg_20d_pct',
                 'rvol20', 'vol_rel', 'basis_turn']
    trad_avail = [c for c in trad_cols if c in df.columns]
    X = df[trad_avail].fillna(0).values
    y = ham_dev.values
    X_c = np.c_[np.ones(len(X)), X]
    beta_hat, _, _, _ = lstsq(X_c, y, rcond=None)
    residual = y - X_c @ beta_hat
    r_res, p_res = spearmanr(residual[mask], next_ret.values[mask])
    info_ret = abs(r_res) / abs(r_orig) * 100 if abs(r_orig) > 0.001 else 0

    # 基线对比
    baseline = json.load(open(os.path.join(DATA_DIR, 'orthogonality.json')))
    match_ic = abs(r_orig - baseline['ham_ic_original']) < 0.01
    match_res = abs(r_res - baseline['ham_orthogonal_residual_ic']) < 0.01

    print(f"  HAM 原始 IC: {r_orig:.4f} (基线: {baseline['ham_ic_original']:.4f})")
    print(f"  正交残差 IC: {r_res:.4f} (基线: {baseline['ham_orthogonal_residual_ic']:.4f})")
    print(f"  信息增益保留: {info_ret:.1f}% (基线: {baseline['info_retention_pct']:.1f}%)")
    print(f"  ✅ IC 一致" if match_ic else f"  ⚠️ IC 不一致")
    print(f"  ✅ 残差IC一致" if match_res else f"  ⚠️ 残差IC不一致")

    return {'ham_ic': r_orig, 'residual_ic': r_res, 'info_retention': info_ret,
            'baseline_ic': baseline['ham_ic_original'], 'baseline_res': baseline['ham_orthogonal_residual_ic'],
            'match_ic': match_ic, 'match_res': match_res}


# ============================================================
# 校验3: IC 分段稳定性模板
# ============================================================
def check_ic_stability(df):
    """
    20/40/60 日滚动 IC 统计 + 分行情环境 IC 正占比。
    """
    print("\n" + "=" * 60)
    print("校验3: IC 分段稳定性 (IC Stability by Segment)")
    print("=" * 60)

    df['next_ret'] = df['ret'].shift(-1)
    hold_mask = df['pos_A'].abs() > 1e-9

    # 滚动 IC
    results = {}
    for w in [20, 40, 60]:
        ic_series = []
        for i in range(w, len(df) - 1):
            mask = hold_mask.iloc[i-w:i]
            if mask.sum() < 5:
                ic_series.append(np.nan)
                continue
            r, _ = spearmanr(df['pos_A'].iloc[i-w:i], df['next_ret'].iloc[i-w:i])
            ic_series.append(r)
        ic_arr = np.array([x for x in ic_series if not np.isnan(x)])
        results[f'{w}d'] = {
            'mean': float(np.mean(ic_arr)) if len(ic_arr) > 0 else 0,
            'std': float(np.std(ic_arr)) if len(ic_arr) > 0 else 0,
            'positive_pct': float((ic_arr > 0).mean()) if len(ic_arr) > 0 else 0,
        }

    # 分环境 IC
    rvol_med = df['rvol20'].median()
    crowded = df['vol_rel'] > 1.2
    highvol = df['rvol20'] > rvol_med
    df['state'] = 'UNKNOWN'
    df.loc[crowded & highvol, 'state'] = '拥挤高波动'
    df.loc[crowded & (~highvol), 'state'] = '拥挤低波动'
    df.loc[(~crowded) & highvol, 'state'] = '博弈高波动'
    df.loc[(~crowded) & (~highvol), 'state'] = '博弈低波动'

    env_ic = {}
    ic_valid = df.dropna(subset=['IC_roll']) if 'IC_roll' in df.columns else pd.DataFrame()
    for st in ['博弈低波动', '博弈高波动', '拥挤低波动', '拥挤高波动']:
        if len(ic_valid) > 0:
            m = ic_valid['state'] == st
            if m.sum() > 0:
                ic = ic_valid.loc[m, 'IC_roll']
                env_ic[st] = {'n': int(m.sum()), 'mean': float(ic.mean()), 'positive_pct': float((ic > 0).mean())}

    # 基线对比
    baseline = json.load(open(os.path.join(DATA_DIR, 'stability_by_state.json')))
    match_20 = abs(results['20d']['positive_pct'] - baseline['ic_by_window']['20d']['positive_pct']) < 0.01

    print(f"  20日 IC 正占比: {results['20d']['positive_pct']:.1%} (基线: {baseline['ic_by_window']['20d']['positive_pct']:.1%})")
    print(f"  40日 IC 正占比: {results['40d']['positive_pct']:.1%} (基线: {baseline['ic_by_window']['40d']['positive_pct']:.1%})")
    print(f"  60日 IC 正占比: {results['60d']['positive_pct']:.1%} (基线: {baseline['ic_by_window']['60d']['positive_pct']:.1%})")
    print(f"  ✅ 20日一致" if match_20 else f"  ⚠️ 20日不一致")

    return {'ic_by_window': results, 'env_ic': env_ic, 'match_20d': match_20}


# ============================================================
# 校验4: Alpha-Beta 收益拆解模板
# ============================================================
def check_alpha_beta(df):
    """
    收益拆解: HAM Alpha vs Buy&Hold Beta + 分环境。
    """
    print("\n" + "=" * 60)
    print("校验4: Alpha-Beta 收益拆解 (Return Decomposition)")
    print("=" * 60)

    ann = lambda r: r.mean() * 250 * 100 if len(r) > 0 else 0
    strategy_ann = ann(df['net_ret'].dropna())
    bh_ann = ann(df['bh_ret'].dropna())
    alpha_ann = strategy_ann - bh_ann

    # Calmar
    net = df['net_ret'].dropna().values
    nav = np.cumprod(1 + net) * 1e6
    peak = np.maximum.accumulate(nav)
    mdd = ((nav - peak) / peak).min()
    calmar = strategy_ann / abs(mdd) if mdd != 0 else 0

    # 基线对比
    baseline = json.load(open(os.path.join(DATA_DIR, 'return_decomposition.json')))
    bl = baseline['by_environment']['全样本']
    match_alpha = abs(alpha_ann - bl['alpha_ann_pct']) < 0.5
    match_strategy = abs(strategy_ann - bl['ann_ret_pct']) < 0.5

    print(f"  策略年化: {strategy_ann:.2f}% (基线: {bl['ann_ret_pct']:.2f}%)")
    print(f"  Buy&Hold: {bh_ann:.2f}% (基线: {bl['bh_ann_ret_pct']:.2f}%)")
    print(f"  Alpha: {alpha_ann:.2f}% (基线: {bl['alpha_ann_pct']:.2f}%)")
    print(f"  Calmar: {calmar:.2f} | 回撤: {mdd*100:.2f}%")
    print(f"  ✅ Alpha一致" if match_alpha else f"  ⚠️ Alpha不一致")
    print(f"  ✅ 策略一致" if match_strategy else f"  ⚠️ 策略不一致")

    # components 段矛盾告警
    comp_alpha = baseline['components'].get('HAM_Alpha年化', None)
    if comp_alpha is not None:
        conflict = abs(comp_alpha - alpha_ann) > 1.0
        if conflict:
            print(f"  ⚠️ 矛盾告警: components.HAM_Alpha={comp_alpha} vs by_environment.alpha={alpha_ann}")

    return {'strategy_ann': strategy_ann, 'bh_ann': bh_ann, 'alpha_ann': alpha_ann,
            'calmar': calmar, 'mdd': mdd, 'match_alpha': match_alpha, 'match_strategy': match_strategy}


# ============================================================
# 主入口
# ============================================================
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="exp433 回归校验模板")
    parser.add_argument('--check', type=str, default='all', choices=['all', 'perm', 'ortho', 'ic', 'alpha'])
    args = parser.parse_args()

    df = load_data()

    results = {}
    if args.check in ('all', 'perm'):
        results['permutation'] = check_permutation(df)
    if args.check in ('all', 'ortho'):
        results['orthogonality'] = check_orthogonality(df)
    if args.check in ('all', 'ic'):
        results['ic_stability'] = check_ic_stability(df)
    if args.check in ('all', 'alpha'):
        results['alpha_beta'] = check_alpha_beta(df)

    print("\n" + "=" * 60)
    print("回归校验汇总")
    print("=" * 60)
    all_match = True
    for name, res in results.items():
        match_keys = [k for k in res if 'match' in k]
        matches = {k: res[k] for k in match_keys}
        status = "✅" if all(matches.values()) else "⚠️"
        if not all(matches.values()):
            all_match = False
        print(f"  {name}: {status} {matches}")

    print(f"\n  总体: {'✅ 全部一致' if all_match else '⚠️ 存在不一致'}")
    print(f"\n  约束遵守: 未修改 ham_pipeline.py / 主引擎 / 风控 / 参数")
