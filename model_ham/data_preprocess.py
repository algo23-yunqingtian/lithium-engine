"""
HAM 双主体模型 — 数据预处理模块
读取原始数据，对齐日频，计算基本面公允价格 P_fund(t)
"""
import pandas as pd
import numpy as np
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(ROOT, "raw_data")
INTERMEDIATE = os.path.join(ROOT, "intermediate")


def load_future_prices():
    """加载期货日频价格"""
    df = pd.read_csv(os.path.join(RAW, "lithium_future.csv"), parse_dates=["date"])
    return df


def load_fundamental_weekly():
    """加载 SMM 周频基本面数据"""
    df = pd.read_csv(os.path.join(RAW, "fundamental_balance.csv"), parse_dates=["date"])
    return df


def load_smm_daily():
    """加载 SMM 日频现货数据"""
    df = pd.read_csv(os.path.join(RAW, "smm_daily.csv"), parse_dates=["date"])
    return df


def forward_fill_weekly_to_daily(weekly_df, date_range):
    """
    周频数据前向填充为日频
    严格使用前向填充，禁止未来值
    """
    all_dates = pd.date_range(date_range[0], date_range[1], freq='B')  # 工作日
    idx = pd.DatetimeIndex(all_dates)
    weekly_df = weekly_df.set_index("date")
    daily = weekly_df.reindex(idx, method='ffill')
    daily.index.name = "date"
    return daily.reset_index()


def compute_p_fund(future_df, fundamental_daily, smm_daily):
    """
    计算基本面公允价格 P_fund(t)
    
    方法：基于供需平衡的综合评分
    - 库存低 → P_fund 高（利多）
    - 产量低 → P_fund 高
    - 开工率低 → P_fund 高
    - 电芯需求高 → P_fund 高
    - 基差大（现货紧张）→ P_fund 高
    
    输出：P_fund(t) = 现货均价 + 供需调整因子
    """
    merged = future_df.merge(fundamental_daily[["date","inventory_total","production_total","operating_rate","cell_total","inventory_days"]], 
                              on="date", how="left")
    merged = merged.merge(smm_daily[["date","basis","spot_avg"]], on="date", how="left")
    
    # 前向填充缺失值
    fill_cols = ["inventory_total","production_total","operating_rate","cell_total","inventory_days","basis","spot_avg"]
    merged[fill_cols] = merged[fill_cols].ffill()
    
    # 标准化各因子到 [-1, 1] 区间
    def zscore_clip(series, clip_range=(-1, 1)):
        s = (series - series.mean()) / series.std()
        return s.clip(*clip_range)
    
    # 供需调整因子（各维度 zscore 加权）
    inv_factor = -zscore_clip(merged["inventory_total"])  # 库存低=利多=正
    prod_factor = -zscore_clip(merged["production_total"])  # 产量低=利多=正
    op_factor = -zscore_clip(merged["operating_rate"])  # 开工率低=利多=正
    demand_factor = zscore_clip(merged["cell_total"])  # 电芯需求高=利多=正
    basis_factor = zscore_clip(merged["basis"])  # 升水=紧张=利多=正
    
    # 加权综合
    weights = {"inv": 0.30, "prod": 0.20, "op": 0.15, "demand": 0.25, "basis": 0.10}
    supply_demand_score = (inv_factor * weights["inv"] + 
                           prod_factor * weights["prod"] + 
                           op_factor * weights["op"] + 
                           demand_factor * weights["demand"] + 
                           basis_factor * weights["basis"])
    
    # P_fund = 现货均价 + 供需分 × 价格调整幅度
    # 供需分 [-1,1] 对应 ±15% 价格调整
    price_adjust = supply_demand_score * merged["close"] * 0.15
    merged["P_fund"] = merged["spot_avg"].fillna(merged["close"]) + price_adjust
    
    # 回退：如果 spot_avg 不可用，用 close 作为基础
    merged["P_fund"] = merged["P_fund"].fillna(merged["close"])
    
    return merged[["date","close","open","high","low","volume","position","P_fund","basis","spot_avg"]].copy()


def preprocess_all():
    """完整预处理流程"""
    future_df = load_future_prices()
    fundamental_weekly = load_fundamental_weekly()
    smm_daily = load_smm_daily()
    
    # 周频转日频（前向填充）
    date_range = (future_df["date"].min(), future_df["date"].max())
    fundamental_daily = forward_fill_weekly_to_daily(fundamental_weekly, date_range)
    
    # 计算 P_fund
    result = compute_p_fund(future_df, fundamental_daily, smm_daily)
    
    # 保存到 intermediate/
    os.makedirs(INTERMEDIATE, exist_ok=True)
    result.to_csv(os.path.join(INTERMEDIATE, "processed_input.csv"), index=False)
    
    print(f"Preprocessing complete: {len(result)} rows")
    print(f"Date range: {result['date'].min()} ~ {result['date'].max()}")
    print(f"P_fund NaN: {result['P_fund'].isna().sum()}")
    print(f"close NaN: {result['close'].isna().sum()}")
    
    return result


if __name__ == "__main__":
    result = preprocess_all()
    print("\nSample output:")
    print(result.head(3).to_string())
    print(result.tail(3).to_string())
