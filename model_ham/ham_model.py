"""
HAM 双主体模型 — 核心模型模块
实现 Brock-Hommes 框架下的异质 Agent 模型
"""
import numpy as np
import pandas as pd
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INTERMEDIATE = os.path.join(ROOT, "intermediate")
OUTPUT = os.path.join(ROOT, "output_factors")


def compute_fundamental_demand(P_fund, P_t, alpha):
    """
    产业基本面 Agent 需求函数
    D_f(t) = alpha * (P_fund(t) - P(t))
    """
    return alpha * (P_fund - P_t)


def compute_speculative_demand(P_t, P_t_minus_1, beta):
    """
    投机趋势 Agent 需求函数
    D_c(t) = beta * (P(t) - P(t-1))
    """
    return beta * (P_t - P_t_minus_1)


def compute_profits(D_f, D_c, P_t, W):
    """
    滚动窗口收益计算
    Profit_f(t) = sum_{i=t-W}^{t-1} [ D_f(i) * (P(i+1) - P(i)) ]
    Profit_c(t) = sum_{i=t-W}^{t-1} [ D_c(i) * (P(i+1) - P(i)) ]
    
    严格使用 t 之前数据，不含未来信息
    """
    n = len(P_t)
    price_change = np.zeros(n)
    price_change[1:] = np.diff(P_t)  # P(i+1) - P(i)
    
    profit_f = np.zeros(n)
    profit_c = np.zeros(n)
    
    for t in range(W, n):
        window_D_f = D_f[t-W:t]
        window_D_c = D_c[t-W:t]
        window_price_change = price_change[t-W+1:t+1]  # shift: D_f(i) * (P(i+1)-P(i))
        profit_f[t] = np.sum(window_D_f * window_price_change)
        profit_c[t] = np.sum(window_D_c * window_price_change)
    
    return profit_f, profit_c


def logit_weights(profit_f, profit_c, gamma, max_exp=500):
    """
    Logit 策略切换权重
    n_f(t) = exp(gamma * Profit_f) / (exp(gamma * Profit_f) + exp(gamma * Profit_c))
    n_c(t) = exp(gamma * Profit_c) / (exp(gamma * Profit_f) + exp(gamma * Profit_c))
    
    数值截断防止 exp 溢出
    """
    scaled_f = np.clip(gamma * profit_f, -max_exp, max_exp)
    scaled_c = np.clip(gamma * profit_c, -max_exp, max_exp)
    
    # 使用 log-sum-exp trick 防溢出
    max_val = np.maximum(scaled_f, scaled_c)
    exp_f = np.exp(scaled_f - max_val)
    exp_c = np.exp(scaled_c - max_val)
    
    denom = exp_f + exp_c
    # 避免除零
    denom = np.where(denom == 0, 1e-10, denom)
    
    n_f = exp_f / denom
    n_c = exp_c / denom
    
    return n_f, n_c


def run_ham_model(df, alpha, beta, gamma, W, verbose=False):
    """
    运行 HAM 双主体模型，输出三个因子
    
    参数:
        df: 包含 date, close, P_fund 列的 DataFrame
        alpha: 基本面回归强度 [0.1-0.8]
        beta: 趋势外推强度 [0.2-1.5]
        gamma: 策略切换灵敏度 [1-5]
        W: 滚动收益窗口 [20,40,60]
    
    输出因子:
        n_c: 投机交易者群体占比
        Total_Demand: 市场聚合模拟超额需求
        n_f_minus_n_c: 产业-投机力量分歧
    """
    close = df["close"].values.astype(float)
    p_fund = df["P_fund"].values.astype(float)
    n = len(close)
    
    # 1. 计算需求函数
    D_f = compute_fundamental_demand(p_fund, close, alpha)
    
    # P(t-1): 滞后一期价格
    P_t_minus_1 = np.zeros(n)
    P_t_minus_1[1:] = close[:-1]
    P_t_minus_1[0] = close[0]  # 第一天无前值，用当前价
    
    D_c = compute_speculative_demand(close, P_t_minus_1, beta)
    
    # 2. 滚动窗口收益
    profit_f, profit_c = compute_profits(D_f, D_c, close, W)
    
    # 3. Logit 权重
    n_f, n_c = logit_weights(profit_f, profit_c, gamma)
    
    # 4. 聚合因子
    total_demand = n_f * D_f + n_c * D_c
    n_f_minus_n_c = n_f - n_c
    
    # 5. 构建输出
    result = df.copy()
    result["D_f"] = D_f
    result["D_c"] = D_c
    result["profit_f"] = profit_f
    result["profit_c"] = profit_c
    result["n_f"] = n_f
    result["n_c"] = n_c
    result["Total_Demand"] = total_demand
    result["n_f_minus_n_c"] = n_f_minus_n_c
    
    # 标记有效数据区间（W 之后的数据才有效）
    result["valid"] = 0
    result.loc[W:, "valid"] = 1
    
    if verbose:
        valid_mask = result["valid"] == 1
        print(f"=== HAM Model Results (alpha={alpha}, beta={beta}, gamma={gamma}, W={W}) ===")
        print(f"Valid days: {valid_mask.sum()}/{n}")
        print(f"n_c: mean={result.loc[valid_mask,'n_c'].mean():.4f}, std={result.loc[valid_mask,'n_c'].std():.4f}")
        print(f"Total_Demand: mean={result.loc[valid_mask,'Total_Demand'].mean():.2f}")
        print(f"n_f-n_c: mean={result.loc[valid_mask,'n_f_minus_n_c'].mean():.4f}")
        print(f"  n_f-n_c > 0 (产业主导): {(result.loc[valid_mask,'n_f_minus_n_c']>0).sum()} days")
        print(f"  n_f-n_c < 0 (投机主导): {(result.loc[valid_mask,'n_f_minus_n_c']<0).sum()} days")
    
    return result


def save_factors(result, alpha, beta, gamma, W):
    """保存因子到 output_factors/"""
    os.makedirs(OUTPUT, exist_ok=True)
    filename = f"factor_a{alpha}_b{beta}_g{gamma}_W{W}.csv"
    filepath = os.path.join(OUTPUT, filename)
    result.to_csv(filepath, index=False)
    print(f"Factors saved: {filepath}")
    return filepath


# 参数网格
PARAM_GRID = {
    "alpha": [0.1, 0.3, 0.5, 0.8],
    "beta": [0.2, 0.5, 1.0, 1.5],
    "gamma": [1, 2, 3, 5],
    "W": [20, 40, 60]
}
