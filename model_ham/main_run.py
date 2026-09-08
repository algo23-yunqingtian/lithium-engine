"""
HAM 双主体模型 — 主运行脚本
完整流程：预处理 → 模型 → 因子输出 → 回测 → 参数扫描
"""
import os
import sys
import numpy as np
import pandas as pd
from scipy import stats

# 添加 src 到路径
SRC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, SRC_DIR)

from data_preprocess import preprocess_all
from ham_model import run_ham_model, save_factors, PARAM_GRID
from backtest import run_backtest, param_sensitivity_scan

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT = os.path.join(ROOT, "output_factors")
BACKTEST = os.path.join(ROOT, "backtest_result")


def time_split(df, train_pct=0.5, val_pct=0.2):
    """
    时间顺序切分数据集
    训练集：最早 50%
    验证集：中间 20%
    测试集：最后 30%
    """
    n = len(df)
    train_end = int(n * train_pct)
    val_end = int(n * (train_pct + val_pct))
    return {
        "train": df.iloc[:train_end].copy(),
        "val": df.iloc[train_end:val_end].copy(),
        "test": df.iloc[val_end:].copy()
    }


def main():
    print("=" * 60)
    print("碳酸锂 HAM 双主体模型 — 主运行脚本")
    print("=" * 60)
    
    # 1. 数据预处理
    print("\n[1/5] 数据预处理...")
    df = preprocess_all()
    
    # 时间切分
    splits = time_split(df)
    print(f"  训练集: {len(splits['train'])} 天 ({splits['train']['date'].min()} ~ {splits['train']['date'].max()})")
    print(f"  验证集: {len(splits['val'])} 天 ({splits['val']['date'].min()} ~ {splits['val']['date'].max()})")
    print(f"  测试集: {len(splits['test'])} 天 ({splits['test']['date'].min()} ~ {splits['test']['date'].max()})")
    
    # 2. 默认参数运行模型
    print("\n[2/5] 运行 HAM 模型 (默认参数)...")
    default_params = {"alpha": 0.3, "beta": 0.5, "gamma": 2, "W": 20}
    result = run_ham_model(df, **default_params, verbose=True)
    
    # 3. 保存因子
    print("\n[3/5] 保存因子...")
    save_factors(result, **default_params)
    
    # 4. 回测（仅测试集）
    print("\n[4/5] 回测（仅测试集，样本外）...")
    test_mask = result["date"] >= splits["test"]["date"].min()
    test_result = result[test_mask].copy()
    
    for factor_name in ["n_c", "Total_Demand", "n_f_minus_n_c"]:
        print(f"\n--- 因子: {factor_name} ---")
        bt = run_backtest(test_result, factor_name=factor_name, horizon=1, verbose=True)
        
        # 保存回测结果
        os.makedirs(BACKTEST, exist_ok=True)
    
    # 5. 参数敏感性扫描（仅训练集）
    print("\n[5/5] 参数敏感性扫描（仅训练集，避免过拟合）...")
    train_result = run_ham_model(splits["train"], **default_params)
    
    # 简化的参数扫描（全量 256 组合可能耗时，先扫关键组合）
    scan_grid = {
        "alpha": [0.3, 0.5],
        "beta": [0.5, 1.0],
        "gamma": [2, 3],
        "W": [20, 40]
    }
    
    scan_results = []
    for alpha in scan_grid["alpha"]:
        for beta in scan_grid["beta"]:
            for gamma in scan_grid["gamma"]:
                for W in scan_grid["W"]:
                    r = run_ham_model(splits["train"], alpha, beta, gamma, W)
                    valid = r["valid"] == 1
                    for fn in ["n_c", "Total_Demand"]:
                        f = r.loc[valid, fn]
                        c = r.loc[valid, "close"]
                        fwd = c.shift(-1) / c - 1
                        mask = f.notna() & fwd.notna()
                        if mask.sum() > 20:
                            try:
                                ric, _ = stats.spearmanr(f[mask], fwd[mask])
                            except:
                                ric = np.nan
                            scan_results.append({
                                "alpha": alpha, "beta": beta, "gamma": gamma, "W": W,
                                "factor": fn, "rank_ic": ric
                            })
    
    scan_df = pd.DataFrame(scan_results)
    if not scan_df.empty:
        scan_df.to_csv(os.path.join(BACKTEST, "param_sensitivity_scan.csv"), index=False)
        print(f"\n参数扫描结果 ({len(scan_df)} 组合):")
        print(scan_df.groupby("factor")["rank_ic"].describe().to_string())
        
        # 找到最优组合（训练集 RankIC 最大绝对值）
        scan_df["abs_ric"] = scan_df["rank_ic"].abs()
        best = scan_df.sort_values("abs_ric", ascending=False).head(5)
        print(f"\nTop 5 参数组合 (训练集 RankIC):")
        print(best[["alpha","beta","gamma","W","factor","rank_ic"]].to_string())
    
    # 6. 生成报告
    print("\n[6] 生成报告...")
    generate_report(result, splits, default_params, scan_df)
    
    print("\n" + "=" * 60)
    print("运行完成！")
    print(f"因子输出: {OUTPUT}/")
    print(f"回测结果: {BACKTEST}/")
    print("=" * 60)


def generate_report(result, splits, params, scan_df):
    """生成 report.md"""
    n_total = len(result)
    n_valid = result["valid"].sum()
    
    report = f"""# HAM 双主体模型 — 回测报告

> 生成时间：{pd.Timestamp.now().strftime('%Y-%m-%d %H:%M')}
> 模型版本：v1.0

## 数据集

| 数据集 | 天数 | 时间范围 |
|--------|------|----------|
| 全样本 | {n_total} | {result['date'].min()} ~ {result['date'].max()} |
| 训练集 | {len(splits['train'])} | {splits['train']['date'].min()} ~ {splits['train']['date'].max()} |
| 验证集 | {len(splits['val'])} | {splits['val']['date'].min()} ~ {splits['val']['date'].max()} |
| 测试集 | {len(splits['test'])} | {splits['test']['date'].min()} ~ {splits['test']['date'].max()} |

有效数据（W={params['W']} 之后）：{n_valid} 天

## 默认参数

| 参数 | 值 | 含义 |
|------|-----|------|
| α | {params['alpha']} | 基本面回归强度 |
| β | {params['beta']} | 趋势外推强度 |
| γ | {params['gamma']} | 策略切换灵敏度 |
| W | {params['W']} | 滚动收益窗口 |

## 因子说明

| 因子 | 说明 | 用法 |
|------|------|------|
| n_c | 投机 Agent 市场占比 | 越高→波动率越大→行情脱离基本面 |
| Total_Demand | 聚合超额需求 | 正→多头压力，负→空头压力 |
| n_f-n_c | 产业-投机力量分歧 | 正→产业主导，负→投机主导 |

## 输出文件

- `output_factors/factor_*.csv` — 各参数组合因子
- `backtest_result/ic_*.csv` — IC 序列
- `backtest_result/group_*.csv` — 分层回测结果
- `backtest_result/param_sensitivity_scan.csv` — 参数敏感性扫描
- `backtest_result/report.md` — 本报告

## 模型边界

- ✅ 擅长：识别投机力量占比变化、市场博弈结构
- ❌ 不擅长：精准预测每日价格涨跌点位
- ⚠️ 禁止：单独用因子做买卖信号，应作为 ML 模型输入特征
"""
    
    if not scan_df.empty:
        report += "\n## 参数敏感性扫描结果\n\n"
        best = scan_df.copy()
        best["abs_ric"] = best["rank_ic"].abs()
        report += best.sort_values("abs_ric", ascending=False).head(10).to_markdown(index=False)
        report += "\n"
    
    with open(os.path.join(BACKTEST, "report.md"), "w") as f:
        f.write(report)
    print(f"Report saved: {BACKTEST}/report.md")


if __name__ == "__main__":
    main()
