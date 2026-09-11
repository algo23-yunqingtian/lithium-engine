"""
碳酸锂新候选因子构建模块
从本地 DB 读取原始数据，计算6个候选因子值（日频对齐）

因子清单:
  F1: 上游锂矿+盐湖合并开工率 20 日环比 (供给侧)
  F2: 动力电池排产 20 日环比 (需求侧)
  F3: 库存预期差因子 (一致预期库存 - 真实库存) (预期差)
  F4: 现货成交溢价因子 (现货成交均价 - 近月期货) (价差)
  F5: 动态近月-次近月跨期价差 + 跨期曲率 (期限结构)
  F6: 拥挤度因子: 20 日持仓分位 × 20 日成交量分位 (风控状态)
"""
import sqlite3
import pandas as pd
import numpy as np
from typing import Optional

LITHIUM_DB = "/home/ubuntu/lithium_calendar/lithium.db"
SPOT_DB = "/home/ubuntu/lc_futures_data/data/lc_spot.db"


def load_main_prices() -> pd.DataFrame:
    """加载主力合约日频 OHLCV+持仓"""
    conn = sqlite3.connect(LITHIUM_DB)
    df = pd.read_sql("SELECT date, open, high, low, close, volume, position, settle FROM prices ORDER BY date", conn)
    conn.close()
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()
    return df


def load_weekly_data() -> pd.DataFrame:
    """加载周频 SMM 数据，pivot 为宽表"""
    conn = sqlite3.connect(LITHIUM_DB)
    # 先拿 meta 映射
    meta = pd.read_sql("SELECT sheet_name, col_idx, key, value FROM lithium_meta WHERE key='指标名称'", conn)
    # 周频指标的 sheet_name 包含 '周度'
    weekly_meta = meta[meta["value"].str.contains("周度")].copy()
    weekly_meta = weekly_meta[["col_idx", "value"]].drop_duplicates(subset=["col_idx"])
    col_map = dict(zip(weekly_meta["col_idx"], weekly_meta["value"]))

    raw = pd.read_sql("SELECT date, col_idx, value FROM lithium_weekly", conn)
    conn.close()
    raw["date"] = pd.to_datetime(raw["date"])
    raw = raw.pivot(index="date", columns="col_idx", values="value")
    raw = raw.rename(columns=col_map)
    return raw.sort_index()


def load_monthly_data() -> pd.DataFrame:
    """加载月频 SMM 数据，pivot 为宽表"""
    conn = sqlite3.connect(LITHIUM_DB)
    meta = pd.read_sql("SELECT sheet_name, col_idx, key, value FROM lithium_meta WHERE key='指标名称'", conn)
    monthly_meta = meta[meta["value"].str.contains("月度")].copy()
    monthly_meta = monthly_meta[["col_idx", "value"]].drop_duplicates(subset=["col_idx"])
    col_map = dict(zip(monthly_meta["col_idx"], monthly_meta["value"]))

    raw = pd.read_sql("SELECT date, col_idx, value FROM lithium_monthly", conn)
    conn.close()
    raw["date"] = pd.to_datetime(raw["date"])
    raw = raw.pivot(index="date", columns="col_idx", values="value")
    raw = raw.rename(columns=col_map)
    return raw.sort_index()


def load_spot_data() -> pd.DataFrame:
    """加载现货价格+基差"""
    conn = sqlite3.connect(SPOT_DB)
    df = pd.read_sql("SELECT date, spot_price, near_price, near_basis, near_basis_rate FROM spot_price", conn)
    conn.close()
    df["date"] = pd.to_datetime(df["date"], format="%Y%m%d")
    return df.set_index("date").sort_index()


def load_contract_prices() -> pd.DataFrame:
    """加载全合约日行情（用于跨期价差）"""
    conn = sqlite3.connect(LITHIUM_DB)
    df = pd.read_sql("SELECT contract, date, close, volume, hold FROM contract_daily_all", conn)
    conn.close()
    df["date"] = pd.to_datetime(df["date"])
    return df


def forward_fill_to_daily(weekly_df: pd.DataFrame, date_index: pd.DatetimeIndex) -> pd.DataFrame:
    """周频→日频前向填充（严格 ffill，无未来泄漏）"""
    return weekly_df.reindex(date_index, method="ffill")


def compute_return(prices: pd.DataFrame, fwd: int = 1) -> pd.Series:
    """计算前向收益率 (t+1 ~ t+fwd)"""
    ret = prices["close"].shift(-fwd) / prices["close"] - 1
    return ret


# ============================================================
# 因子计算函数
# ============================================================

def factor_F1(weekly_daily: pd.DataFrame) -> pd.Series:
    """
    F1: 上游锂矿+盐湖合并开工率 20 日环比 (供给侧)
    取开工率:锂辉石产 + 开工率:盐湖产 的均值，计算 20 日环比
    """
    col_lvs = [c for c in weekly_daily.columns if "开工率" in c and "锂辉石" in c]
    col_salt = [c for c in weekly_daily.columns if "开工率" in c and "盐湖" in c]
    
    if not col_lvs or not col_salt:
        # fallback: 用总开工率
        col_total = [c for c in weekly_daily.columns if "开工率" in c and "总计" in c]
        if col_total:
            kaigong = weekly_daily[col_total[0]]
        else:
            return pd.Series(dtype=float)
    else:
        kaigong = (weekly_daily[col_lvs[0]] + weekly_daily[col_salt[0]]) / 2
    
    # 20 日环比
    f1 = kaigong / kaigong.shift(20) - 1
    return f1.rename("F1_kaigong_20d")


def factor_F2(weekly_daily: pd.DataFrame) -> pd.Series:
    """
    F2: 动力电池排产 20 日环比 (需求侧)
    取动力电芯产量:总:周度，计算 20 日环比
    """
    col = [c for c in weekly_daily.columns if "动力电芯产量" in c and "总" in c and "周度" in c]
    if not col:
        col = [c for c in weekly_daily.columns if "动力电芯" in c and "总" in c]
    if not col:
        return pd.Series(dtype=float)
    
    paichan = weekly_daily[col[0]]
    f2 = paichan / paichan.shift(20) - 1
    return f2.rename("F2_paichan_20d")


def factor_F3(weekly_daily: pd.DataFrame, prices: pd.DataFrame) -> pd.Series:
    """
    F3: 库存预期差因子 (一致预期库存 - 真实库存)
    一致预期库存 = 20 日移动平均库存 (代理变量)
    预期差 = MA20(库存) - 真实库存
    """
    col_inv = [c for c in weekly_daily.columns if "大样本库存" in c and "总计" in c]
    if not col_inv:
        col_inv = [c for c in weekly_daily.columns if "样本库存" in c and "总计" in c]
    if not col_inv:
        col_inv = [c for c in weekly_daily.columns if "库存" in c and "总计" in c and "周度" in c]
    if not col_inv:
        return pd.Series(dtype=float)
    
    inv = weekly_daily[col_inv[0]]
    # 一致预期 = 20 日移动平均
    expected = inv.rolling(20, min_periods=10).mean()
    # 预期差 = 预期 - 实际（正值=库存超预期累积→利空）
    f3 = expected - inv
    return f3.rename("F3_inventory_surprise")


def factor_F4(spot: pd.DataFrame, prices: pd.DataFrame) -> pd.Series:
    """
    F4: 现货成交溢价因子 (现货均价 - 近月期货)
    spot 表中有 spot_price 和 near_price
    """
    # 对齐日期
    spot_aligned = spot.reindex(prices.index, method="ffill")
    if "spot_price" not in spot_aligned.columns or "near_price" not in spot_aligned.columns:
        return pd.Series(dtype=float)
    
    # 现货 - 期货近月
    premium = spot_aligned["spot_price"] - spot_aligned["near_price"]
    # 标准化：除以近月价格得到百分比
    f4 = premium / spot_aligned["near_price"]
    return f4.rename("F4_spot_premium")


def factor_F5(contracts: pd.DataFrame, prices: pd.DataFrame) -> pd.Series:
    """
    F5: 动态近月-次近月跨期价差 + 跨期曲率
    找到每日最近两个有成交的合约，算价差和曲率
    """
    # 对每个交易日，找近月和次近月合约
    records = []
    for dt in prices.index:
        day_data = contracts[(contracts["date"] == dt) & (contracts["volume"] > 0)].copy()
        if len(day_data) < 2:
            continue
        day_data = day_data.sort_values("contract")
        # 取最近的两个合约
        near = day_data.iloc[0]
        far = day_data.iloc[1]
        spread = near["close"] - far["close"]
        # 曲率：如果有第三个合约
        curvature = np.nan
        if len(day_data) >= 3:
            third = day_data.iloc[2]
            curvature = (near["close"] - 2*far["close"] + third["close"]) / far["close"]
        records.append({
            "date": dt,
            "spread": spread,
            "curvature": curvature,
        })
    
    if not records:
        return pd.Series(dtype=float)
    
    df_spreads = pd.DataFrame(records).set_index("date").sort_index()
    # 合并 spread + curvature
    f5 = df_spreads["spread"] + df_spreads["curvature"].fillna(0)
    return f5.rename("F5_term_structure")


def factor_F6(prices: pd.DataFrame) -> pd.Series:
    """
    F6: 拥挤度因子 = 20 日持仓分位 × 20 日成交量分位
    用 rolling percentile rank
    """
    pos = prices["position"]
    vol = prices["volume"]
    
    pos_rank = pos.rolling(20, min_periods=10).rank(pct=True)
    vol_rank = vol.rolling(20, min_periods=10).rank(pct=True)
    
    f6 = pos_rank * vol_rank
    return f6.rename("F6_crowding")


def build_all_factors() -> pd.DataFrame:
    """主函数：构建所有因子，返回日频 DataFrame"""
    print("[1/5] 加载主力合约价格...")
    prices = load_main_prices()
    print(f"  -> {len(prices)} rows, {prices.index.min().date()} ~ {prices.index.max().date()}")
    
    print("[2/5] 加载周频基本面数据...")
    weekly = load_weekly_data()
    print(f"  -> {len(weekly)} rows, {len(weekly.columns)} indicators")
    
    print("[3/5] 加载现货数据...")
    spot = load_spot_data()
    print(f"  -> {len(spot)} rows")
    
    print("[4/5] 加载全合约数据...")
    contracts = load_contract_prices()
    print(f"  -> {len(contracts)} rows")
    
    print("[5/5] 计算因子...")
    # 周频→日频前向填充
    weekly_daily = forward_fill_to_daily(weekly, prices.index)
    
    factors = pd.DataFrame(index=prices.index)
    
    f1 = factor_F1(weekly_daily)
    if f1 is not None and len(f1) > 0:
        factors[f1.name] = f1
    
    f2 = factor_F2(weekly_daily)
    if f2 is not None and len(f2) > 0:
        factors[f2.name] = f2
    
    f3 = factor_F3(weekly_daily, prices)
    if f3 is not None and len(f3) > 0:
        factors[f3.name] = f3
    
    f4 = factor_F4(spot, prices)
    if f4 is not None and len(f4) > 0:
        factors[f4.name] = f4
    
    f5 = factor_F5(contracts, prices)
    if f5 is not None and len(f5) > 0:
        factors[f5.name] = f5
    
    f6 = factor_F6(prices)
    if f6 is not None and len(f6) > 0:
        factors[f6.name] = f6
    
    # 加入收益率
    factors["ret_1d"] = compute_return(prices, fwd=1)
    factors["ret_5d"] = compute_return(prices, fwd=5)
    factors["close"] = prices["close"]
    
    # 去掉全 NaN 的因子
    factors = factors.dropna(axis=1, how="all")
    
    print(f"\n最终因子表: {factors.shape}")
    print(f"因子列: {[c for c in factors.columns if c.startswith('F')]}")
    print(f"日期范围: {factors.index.min().date()} ~ {factors.index.max().date()}")
    
    return factors


if __name__ == "__main__":
    factors = build_all_factors()
    output_path = "/home/ubuntu/lithium-engine/factor_research/data/factors_daily.csv"
    factors.to_csv(output_path)
    print(f"\n已保存到 {output_path}")
    
    # 打印摘要
    print("\n=== 因子摘要 ===")
    factor_cols = [c for c in factors.columns if c.startswith("F")]
    for col in factor_cols:
        s = factors[col].dropna()
        if len(s) > 0:
            print(f"{col}: n={len(s)}, mean={s.mean():.6f}, std={s.std():.6f}, "
                  f"min={s.min():.6f}, max={s.max():.6f}, "
                  f"coverage={len(s)/len(factors)*100:.1f}%")
