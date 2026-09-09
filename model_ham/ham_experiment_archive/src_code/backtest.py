"""
HAM 双主体模型 — 回测与因子评价模块
IC / RankIC / 分层回测 / 参数敏感性扫描
"""
import numpy as np
import pandas as pd
from scipy import stats
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKTEST = os.path.join(ROOT, "backtest_result")


def compute_forward_returns(close, horizon):
    """
    计算未来 horizon 日收益率
    fwd_ret = P(t+horizon) / P(t) - 1
    """
    return close.shift(-horizon) / close - 1


def compute_ic(factor, forward_ret):
    """计算 IC（Pearson 相关系数）和 RankIC（Spearman）"""
    mask = factor.notna() & forward_ret.notna()
    if mask.sum() < 10:
        return np.nan, np.nan, np.nan
    f = factor[mask]
    r = forward_ret[mask]
    ic, _ = stats.pearsonr(f, r)
    rank_ic, _ = stats.spearmanr(f, r)
    return ic, rank_ic, mask.sum()


def compute_ic_series(factor, close, horizon=1):
    """
    计算滚动 IC 序列
    每天计算前 20 日的 IC
    """
    n = len(close)
    window = 20
    ics = np.full(n, np.nan)
    rank_ics = np.full(n, np.nan)
    
    for t in range(window, n):
        fwd = close[t+1:t+horizon+1].mean() / close[t] - 1 if t+horizon < n else np.nan
        if np.isnan(fwd):
            continue
        # 过去 20 天的因子与次日收益率的相关性
        f_window = factor[t-window:t]
        r_window = np.array([close[i+1]/close[i]-1 for i in range(t-window, t) if i+1 < n])
        if len(f_window) == len(r_window) and len(f_window) >= 10:
            try:
                ic, _ = stats.pearsonr(f_window, r_window)
                ics[t] = ic
                rank_ic, _ = stats.spearmanr(f_window, r_window)
                rank_ics[t] = rank_ic
            except:
                pass
    
    return ics, rank_ics


def ic_statistics(ics):
    """IC 统计量：均值、标准差、ICIR、p-value"""
    valid = ics[~np.isnan(ics)]
    if len(valid) < 10:
        return {"ic_mean": np.nan, "ic_std": np.nan, "icir": np.nan, "p_value": np.nan, "count": 0}
    mean = np.mean(valid)
    std = np.std(valid, ddof=1)
    icir = mean / std if std > 0 else 0
    # t-test: H0: mean IC = 0
    t_stat, p_value = stats.ttest_1samp(valid, 0)
    return {"ic_mean": mean, "ic_std": std, "icir": icir, "p_value": p_value, "count": len(valid)}


def quintile_backtest(factor, close, horizon=1, n_groups=5):
    """
    5 分组分层回测
    按因子值分 5 组，计算每组未来收益率
    """
    n = len(close)
    group_returns = {g: [] for g in range(n_groups)}
    dates = []
    
    # 滚动分位
    window = 60  # 每 60 天重新分组
    for start in range(window, n - horizon, window):
        f_window = factor[start-window:start]
        mask = f_window.notna()
        if mask.sum() < n_groups * 2:
            continue
        valid_indices = np.where(mask)[0]
        f_valid = f_window[mask]
        
        try:
            quantiles = pd.qcut(f_valid, n_groups, labels=False, duplicates='drop')
        except:
            continue
        
        for g in range(n_groups):
            if g not in quantiles.values:
                continue
            g_indices = valid_indices[quantiles.values == g]
            for idx in g_indices:
                t = start - window + idx
                if t + horizon < n:
                    ret = close[t+horizon] / close[t] - 1
                    group_returns[g].append(ret)
            dates.append(start)
    
    # 汇总
    summary = {}
    for g in range(n_groups):
        rets = np.array(group_returns[g])
        if len(rets) > 0:
            summary[g] = {
                "mean_return": np.mean(rets),
                "std_return": np.std(rets, ddof=1),
                "sharpe": np.mean(rets) / np.std(rets, ddof=1) * np.sqrt(252) if np.std(rets, ddof=1) > 0 else 0,
                "count": len(rets)
            }
        else:
            summary[g] = {"mean_return": 0, "std_return": 0, "sharpe": 0, "count": 0}
    
    # 多空组合：最高组 - 最低组
    long_ret = summary.get(n_groups-1, {}).get("mean_return", 0)
    short_ret = summary.get(0, {}).get("mean_return", 0)
    long_short = long_ret - short_ret
    
    return summary, long_short


def param_sensitivity_scan(df, param_grid, horizon=1):
    """
    参数敏感性扫描
    遍历参数组合，计算每个组合的 RankIC
    """
    results = []
    
    for alpha in param_grid["alpha"]:
        for beta in param_grid["beta"]:
            for gamma in param_grid["gamma"]:
                for W in param_grid["W"]:
                    from ham_model import run_ham_model
                    result = run_ham_model(df, alpha, beta, gamma, W)
                    
                    # 只用 valid 数据
                    valid_mask = result["valid"] == 1
                    if valid_mask.sum() < 50:
                        continue
                    
                    for factor_name in ["n_c", "Total_Demand", "n_f_minus_n_c"]:
                        factor = result.loc[valid_mask, factor_name]
                        close_valid = result.loc[valid_mask, "close"]
                        
                        fwd_ret = close_valid.shift(-horizon) / close_valid - 1
                        mask = factor.notna() & fwd_ret.notna()
                        if mask.sum() < 20:
                            continue
                        
                        try:
                            rank_ic, _ = stats.spearmanr(factor[mask], fwd_ret[mask])
                            ic, _ = stats.pearsonr(factor[mask], fwd_ret[mask])
                        except:
                            rank_ic, ic = np.nan, np.nan
                        
                        results.append({
                            "alpha": alpha, "beta": beta, "gamma": gamma, "W": W,
                            "factor": factor_name, "rank_ic": rank_ic, "ic": ic,
                            "abs_rank_ic": abs(rank_ic) if not np.isnan(rank_ic) else 0
                        })
    
    return pd.DataFrame(results)


def run_backtest(df, factor_name="n_c", horizon=1, verbose=True):
    """完整回测流程"""
    close = df["close"].values.astype(float)
    factor = df[factor_name].values.astype(float)
    
    os.makedirs(BACKTEST, exist_ok=True)
    
    # 1. 滚动 IC
    ics, rank_ics = compute_ic_series(df[factor_name], close, horizon)
    
    # 2. IC 统计
    ic_stats = ic_statistics(ics)
    rank_ic_stats = ic_statistics(rank_ics)
    
    # 3. 分层回测
    group_summary, long_short = quintile_backtest(
        df[factor_name], close, horizon
    )
    
    # 4. 保存结果
    ic_df = pd.DataFrame({
        "date": df["date"].values,
        "ic": ics,
        "rank_ic": rank_ics
    })
    ic_df.to_csv(os.path.join(BACKTEST, f"ic_{factor_name}.csv"), index=False)
    
    group_df = pd.DataFrame(group_summary).T
    group_df.index.name = "group"
    group_df.to_csv(os.path.join(BACKTEST, f"group_{factor_name}.csv"))
    
    # 5. 汇总报告
    if verbose:
        print(f"\n=== Backtest: {factor_name} (horizon={horizon}d) ===")
        print(f"IC: mean={ic_stats['ic_mean']:.4f}, std={ic_stats['ic_std']:.4f}, "
              f"ICIR={ic_stats['icir']:.3f}, p={ic_stats['p_value']:.4f}")
        print(f"RankIC: mean={rank_ic_stats['ic_mean']:.4f}, std={rank_ic_stats['ic_std']:.4f}, "
              f"ICIR={rank_ic_stats['icir']:.3f}, p={rank_ic_stats['p_value']:.4f}")
        print(f"Long-Short: {long_short:.6f} per day")
        print(f"Group returns:")
        for g in sorted(group_summary.keys()):
            s = group_summary[g]
            print(f"  Group {g}: mean={s['mean_return']:.6f}, sharpe={s['sharpe']:.2f}, n={s['count']}")
    
    return {"ic_stats": ic_stats, "rank_ic_stats": rank_ic_stats, "group_summary": group_summary, "long_short": long_short}


if __name__ == "__main__":
    # 快速测试
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from data_preprocess import preprocess_all
    from ham_model import run_ham_model
    
    df = preprocess_all()
    result = run_ham_model(df, alpha=0.3, beta=0.5, gamma=2, W=20, verbose=True)
    print("\n--- Backtest ---")
    run_backtest(result, factor_name="n_c", horizon=1)
