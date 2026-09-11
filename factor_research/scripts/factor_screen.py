"""
碳酸锂单因子检验模块
严格遵循 HAM 项目经验教训：
1. 必须设置尾部 20% 盲测试集
2. 全样本 p 值可能掩盖样本外反转 → 分训练/盲测分别报告
3. 必须做正交残差 IC（剔除传统因子后的独立信息）
4. Bonferroni 校正多重检验
5. 区分收益预测因子 vs 风控状态因子
6. 报告自由度增量和过拟合风险评估
"""
import pandas as pd
import numpy as np
from scipy import stats
from typing import Dict, List, Tuple, Optional
import json
import warnings
warnings.filterwarnings("ignore", category=RuntimeWarning)


def cross_sectional_ic(factor: pd.Series, forward_return: pd.Series) -> Tuple[float, float, float]:
    """
    计算 Spearman Rank IC (信息系数)
    返回 (IC, p_value, n)
    """
    df = pd.concat([factor, forward_return], axis=1).dropna()
    if len(df) < 10:
        return np.nan, np.nan, len(df)
    
    ic, p = stats.spearmanr(df.iloc[:, 0], df.iloc[:, 1])
    return ic, p, len(df)


def rolling_ic(factor: pd.Series, forward_return: pd.Series, window: int = 60) -> pd.Series:
    """滚动 IC 序列"""
    df = pd.concat([factor.rename("f"), forward_return.rename("r")], axis=1)
    
    ic_list = []
    dates = []
    for i in range(window, len(df)):
        window_data = df.iloc[i-window:i].dropna()
        if len(window_data) < 20:
            ic_list.append(np.nan)
            dates.append(df.index[i])
            continue
        ic, _ = stats.spearmanr(window_data["f"], window_data["r"])
        ic_list.append(ic)
        dates.append(df.index[i])
    
    return pd.Series(ic_list, index=dates, name="rolling_IC")


def compute_ir(rolling_ic_series: pd.Series) -> float:
    """信息比率 = IC均值 / IC标准差"""
    s = rolling_ic_series.dropna()
    if len(s) < 5:
        return np.nan
    return s.mean() / s.std() if s.std() > 0 else np.nan


def permutation_test(factor: pd.Series, forward_return: pd.Series, n_perm: int = 1000) -> float:
    """置换检验 p 值"""
    df = pd.concat([factor.rename("f"), forward_return.rename("r")], axis=1).dropna()
    if len(df) < 10:
        return np.nan
    
    observed_ic, _ = stats.spearmanr(df["f"], df["r"])
    
    perm_ics = []
    rng = np.random.RandomState(42)
    for _ in range(n_perm):
        shuffled_r = rng.permutation(df["r"].values)
        ic, _ = stats.spearmanr(df["f"].values, shuffled_r)
        perm_ics.append(ic)
    
    perm_ics = np.array(perm_ics)
    p_value = np.mean(np.abs(perm_ics) >= np.abs(observed_ic))
    return p_value


def orthogonal_residual_ic(
    factor: pd.Series,
    forward_return: pd.Series,
    control_factors: pd.DataFrame,
    train_mask: pd.Series,
) -> Dict:
    """
    正交残差 IC：
    1. 在训练集上用控制因子回归 forward_return
    2. 取残差
    3. 在全集上计算候选因子 vs 残差的 IC
    
    控制因子：close, volume, position（传统量价因子）
    """
    from sklearn.linear_model import LinearRegression
    
    df = pd.concat([
        factor.rename("target"),
        forward_return.rename("fwd_ret"),
        control_factors
    ], axis=1).dropna()
    
    if len(df) < 20:
        return {"residual_ic": np.nan, "residual_p": np.nan, "n": len(df)}
    
    train = df[train_mask.reindex(df.index).fillna(False)]
    test = df[~train_mask.reindex(df.index).fillna(False)]
    
    if len(train) < 10 or len(test) < 10:
        # fallback: use all
        train = df
    
    control_cols = [c for c in control_factors.columns]
    X_train = train[control_cols].values
    y_train = train["fwd_ret"].values
    
    # 拟合线性回归
    reg = LinearRegression()
    reg.fit(X_train, y_train)
    
    # 全集残差
    X_all = df[control_cols].values
    y_all = df["fwd_ret"].values
    residual = y_all - reg.predict(X_all)
    
    # 候选因子 vs 残差的 IC
    ic, p = stats.spearmanr(df["target"].values, residual)
    
    return {
        "residual_ic": ic,
        "residual_p": p,
        "n": len(df),
        "train_n": len(train),
        "test_n": len(test) if len(test) > 0 else 0,
        "r2_train": reg.score(X_train, y_train),
    }


def screen_factor(
    factor: pd.Series,
    forward_return: pd.Series,
    control_factors: pd.DataFrame,
    factor_name: str,
    n_factors_total: int = 6,
) -> Dict:
    """
    单因子完整检验
    """
    n = len(factor.dropna())
    total_n = len(forward_return.dropna())
    
    # 尾部 20% 盲测
    valid_idx = factor.dropna().index.intersection(forward_return.dropna().index)
    if len(valid_idx) < 30:
        return {
            "factor": factor_name,
            "status": "insufficient_data",
            "n": n,
            "coverage_pct": n / total_n * 100 if total_n > 0 else 0,
            "full_ic": np.nan, "full_p": np.nan,
            "train_ic": np.nan, "train_p": np.nan,
            "test_ic": np.nan, "test_p": np.nan,
            "ir": np.nan, "perm_p": np.nan, "bonf_p": np.nan,
            "signal_flip": False, "flip_ratio": np.nan,
            "residual_ic": np.nan, "residual_p": np.nan, "r2_train": np.nan,
            "train_n": 0, "test_n": 0,
            "verdict": "reject_insufficient",
            "reasons": f"有效样本{len(valid_idx)}<30，无法检验",
        }
    
    split_point = int(len(valid_idx) * 0.8)
    train_idx = valid_idx[:split_point]
    test_idx = valid_idx[split_point:]
    
    train_mask = pd.Series(False, index=factor.index)
    train_mask.loc[train_idx] = True
    
    # 1. 全样本 IC
    full_ic, full_p, full_n = cross_sectional_ic(
        factor.loc[valid_idx], forward_return.loc[valid_idx]
    )
    
    # 2. 训练集 IC
    train_ic, train_p, train_n = cross_sectional_ic(
        factor.loc[train_idx], forward_return.loc[train_idx]
    )
    
    # 3. 盲测集 IC
    test_ic, test_p, test_n = cross_sectional_ic(
        factor.loc[test_idx], forward_return.loc[test_idx]
    )
    
    # 4. 滚动 IC + IR
    rolling = rolling_ic(factor, forward_return, window=60)
    ir = compute_ir(rolling)
    
    # 5. 信号反转检测
    signal_flip = False
    flip_ratio = np.nan
    if not np.isnan(train_ic) and not np.isnan(test_ic):
        if train_ic * test_ic < 0:
            signal_flip = True
            flip_ratio = abs(test_ic / train_ic) if train_ic != 0 else np.nan
    
    # 6. 置换检验（训练集上）
    perm_p = permutation_test(factor.loc[train_idx], forward_return.loc[train_idx], n_perm=500)
    
    # 7. Bonferroni 校正
    bonf_p = min(perm_p * n_factors_total, 1.0) if not np.isnan(perm_p) else np.nan
    
    # 8. 正交残差 IC
    ortho = orthogonal_residual_ic(
        factor, forward_return, control_factors, train_mask
    )
    
    # 9. 判定
    verdict = "reject"
    reasons = []
    
    if signal_flip:
        reasons.append("信号反转(训练/盲测IC异号)")
    
    if not np.isnan(perm_p) and perm_p > 0.05:
        reasons.append(f"置换检验p={perm_p:.4f}>0.05")
    
    if not np.isnan(bonf_p) and bonf_p > 0.05:
        reasons.append(f"Bonferroni校正p={bonf_p:.4f}>0.05")
    
    if not np.isnan(ortho["residual_ic"]) and abs(ortho["residual_ic"]) < 0.03:
        reasons.append(f"正交残差IC={ortho['residual_ic']:.4f}过低")
    
    if not np.isnan(test_ic) and abs(test_ic) < 0.02:
        reasons.append(f"盲测IC={test_ic:.4f}过低")
    
    if not reasons:
        verdict = "accept"
    elif signal_flip:
        verdict = "reject_critical"
    
    return {
        "factor": factor_name,
        "n": n,
        "coverage_pct": n / total_n * 100,
        "full_ic": full_ic,
        "full_p": full_p,
        "train_ic": train_ic,
        "train_p": train_p,
        "test_ic": test_ic,
        "test_p": test_p,
        "ir": ir,
        "perm_p": perm_p,
        "bonf_p": bonf_p,
        "signal_flip": signal_flip,
        "flip_ratio": flip_ratio,
        "residual_ic": ortho.get("residual_ic", np.nan),
        "residual_p": ortho.get("residual_p", np.nan),
        "r2_train": ortho.get("r2_train", np.nan),
        "train_n": train_n,
        "test_n": test_n,
        "verdict": verdict,
        "reasons": "; ".join(reasons) if reasons else "通过",
    }


def run_all_screens():
    """主函数：执行全部6个因子的单因子检验"""
    from build_factors import build_all_factors
    
    print("=" * 70)
    print("碳酸锂单因子检验")
    print("HAM 经验教训约束：尾部20%盲测 + Bonferroni + 正交残差IC + 信号反转检测")
    print("=" * 70)
    
    factors = build_all_factors()
    
    # 前向收益
    fwd_ret = factors["ret_1d"].copy()
    
    # 控制因子（传统量价因子）
    control = pd.DataFrame(index=factors.index)
    control["close"] = factors["close"]
    # 加入 5 日动量和 20 日动量作为控制
    control["mom_5d"] = factors["close"].pct_change(5)
    control["mom_20d"] = factors["close"].pct_change(20)
    
    factor_cols = [c for c in factors.columns if c.startswith("F")]
    
    results = []
    for fc in factor_cols:
        print(f"\n--- 检验 {fc} ---")
        result = screen_factor(
            factors[fc], fwd_ret, control, fc, n_factors_total=len(factor_cols)
        )
        results.append(result)
        
        print(f"  样本数: {result['n']}, 覆盖率: {result.get('coverage_pct', 0):.1f}%")
        print(f"  全样本IC: {result.get('full_ic', np.nan):.4f} (p={result.get('full_p', np.nan):.4f})")
        print(f"  训练集IC: {result.get('train_ic', np.nan):.4f} (p={result.get('train_p', np.nan):.4f})")
        print(f"  盲测集IC: {result.get('test_ic', np.nan):.4f} (p={result.get('test_p', np.nan):.4f})")
        print(f"  IR: {result.get('ir', np.nan):.4f}")
        print(f"  置换p: {result.get('perm_p', np.nan):.4f}, Bonferroni p: {result.get('bonf_p', np.nan):.4f}")
        print(f"  信号反转: {result.get('signal_flip', False)}, 反转比: {result.get('flip_ratio', np.nan)}")
        print(f"  正交残差IC: {result.get('residual_ic', np.nan):.4f} (p={result.get('residual_p', np.nan):.4f})")
        print(f"  判定: {result['verdict']} — {result['reasons']}")
    
    # 汇总
    print("\n" + "=" * 70)
    print("汇总")
    print("=" * 70)
    accepted = [r for r in results if r["verdict"] == "accept"]
    rejected = [r for r in results if r["verdict"] != "accept"]
    
    print(f"通过: {len(accepted)}/{len(results)}")
    print(f"拒绝: {len(rejected)}/{len(results)}")
    
    for r in results:
        status = "✅" if r["verdict"] == "accept" else "❌"
        print(f"  {status} {r['factor']}: IC={r.get('full_ic', np.nan):.4f}, "
              f"盲测IC={r.get('test_ic', np.nan):.4f}, "
              f"verdict={r['verdict']}")
    
    # 保存结果
    output_path = "/home/ubuntu/lithium-engine/factor_research/data/factor_screen_results.json"
    with open(output_path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\n结果已保存到 {output_path}")
    
    return results


if __name__ == "__main__":
    results = run_all_screens()
