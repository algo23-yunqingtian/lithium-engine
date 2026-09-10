"""
exp427 数据层：锌(Zn) HAM因子表构造

从两个数据源构造 HAM 迁移所需的两张因子表（对齐 exp401/exp406 的schema）：
  1. HAM因子表 (zinc_ham_factors.csv)：close, P_fund, Total_Demand 等
     - close/ohlc: akshare 沪锌主力 zn0 日K
     - P_fund(产业回归压力锚): 用锌冶炼成本代理 = TC(加工费)标准化后的"基本面价格"
       锌的D_f = alpha*(P_fund - P_t)。P_fund用"冶炼成本线"作产业定价锚：
       P_fund = f(TC) —— TC高→矿端紧张→成本抬升→P_fund高→回归压力反向
       为对齐LC口径，用TC的滚动z-score映射到一个"成本价格"水平。
     - Total_Demand: 镀锌产量(锌最主要需求，约占70%+)
  2. 基本面因子表 (zinc_fund_factors.csv)：对齐exp406a三因子schema
     - f_stock_pct20: 精炼锌库存20日环比 (库存因子)
     - f_wh_basis_x_stockpct: 仓单基差×仓单增速 (仓单基差因子)
     - f_cell_pct20: 镀锌产量20日环比 (需求因子，替换LC的动力电池)

数据源：
  - zinc_v1.db (time_series表): 库存/TC/镀锌/仓单/现货价
  - akshare futures_main_sina('zn0'): 期货OHLC日K

输出：
  - raw_data/zinc_future.csv            (date,open,high,low,close,volume,position,settle)
  - raw_data/zinc_ham_factors.csv        (HAM因子表，对齐exp401 schema)
  - exp406_fund_factor/data/zinc_fund_factors.csv (基本面三因子表)
"""
import os
import sys
import numpy as np
import pandas as pd
import sqlite3
import akshare as ak

MODEL = "/home/ubuntu/lithium-engine/model_ham"
RAW_DIR = os.path.join(MODEL, "raw_data")
os.makedirs(RAW_DIR, exist_ok=True)
FUND_DIR = os.path.join(MODEL, "exp406_fund_factor", "data")
os.makedirs(FUND_DIR, exist_ok=True)

ZINC_DB = "/home/ubuntu/zinc_v1.db"

# 锌指标ID（已从zinc_v1.db调研确认）
ID_FUTURES_SETTLE = "S0068135"   # 期货结算价(连续):锌
ID_SEVEN_INV = "a10000098"       # SMM七地锌锭库存
ID_SMELT_INV = "a10099948"       # SHFE精炼锌库存总计
ID_TC_DOMESTIC = "s20095859"     # SMM Zn50国产TC 平均价
ID_GALV = "a10097191"            # SMM镀锌产量
ID_WARRANT = "a10157132"         # SHFE锌仓单日报
ID_SPOT = "s22771582"            # 中国市场价:锌锭(0#) 候选


def load_indicator(conn, ind_id):
    """加载单个指标的日频序列，返回按日期索引的Series（ffill到全样本由调用方处理）"""
    df = pd.read_sql(
        "SELECT date, value FROM time_series WHERE indicator_id=? ORDER BY date",
        conn, params=(ind_id,)
    )
    if df.empty:
        return pd.Series(dtype=float)
    df["date"] = pd.to_datetime(df["date"])
    return df.set_index("date")["value"].astype(float)


def build_zn0_ohlc(start="20230701", end="20260717"):
    """akshare 沪锌主力日K，转成LC同schema"""
    df = ak.futures_main_sina(symbol="zn0", start_date=start, end_date=end)
    df = df.rename(columns={
        "日期": "date", "开盘价": "open", "最高价": "high", "最低价": "low",
        "收盘价": "close", "成交量": "volume", "持仓量": "position",
        "动态结算价": "settle",
    })
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    for c in ["open", "high", "low", "close", "volume", "position", "settle"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df[["date", "open", "high", "low", "close", "volume", "position", "settle"]]


def resample_to_daily(s, freq):
    """
    将周频/月频/季度频指标重采样到日频。
    freq='W'/'M'/'Q'。先ffill到自然日，再在调用方按OHLC日索引reindex+ffill。
    """
    if s.empty:
        return s
    s = s.sort_index()
    if freq == "W":
        s_daily = s.resample("W-FRI").last()
    elif freq == "M":
        s_daily = s.resample("ME").last()
    elif freq == "Q":
        s_daily = s.resample("QE").last()
    else:
        s_daily = s
    return s_daily


if __name__ == "__main__":
    print("=" * 70)
    print("exp427 数据层：锌(Zn) HAM因子表构造")
    print("=" * 70)

    # 1. 沪锌主力OHLC
    print("\n[1] 沪锌主力 zn0 日K (akshare)")
    ohlc = build_zn0_ohlc()
    ohlc.to_csv(os.path.join(RAW_DIR, "zinc_future.csv"), index=False)
    print(f"  {len(ohlc)}行, {ohlc['date'].min().date()}~{ohlc['date'].max().date()}")
    print(f"  close范围: [{ohlc['close'].min():.0f}, {ohlc['close'].max():.0f}]")

    # 2. 从zinc_v1.db加载基本面指标
    print("\n[2] 锌基本面指标 (zinc_v1.db)")
    conn = sqlite3.connect(ZINC_DB)

    inv_seven = load_indicator(conn, ID_SEVEN_INV)        # 七地库存(周频)
    tc = load_indicator(conn, ID_TC_DOMESTIC)             # 国产TC(周频)
    galv = load_indicator(conn, ID_GALV)                  # 镀锌产量(周频)
    warrant = load_indicator(conn, ID_WARRANT)            # 仓单(日频)

    for name, s in [("七地库存", inv_seven), ("国产TC", tc), ("镀锌产量", galv), ("仓单", warrant)]:
        print(f"  {name}: {len(s)}行, {s.index.min().date() if len(s) else 'NA'}~{s.index.max().date() if len(s) else 'NA'}")
    conn.close()

    # 3. 统一到OHLC日索引（全部ffill到日频）
    print("\n[3] 统一到日频（ffill）")
    date_idx = ohlc.set_index("date")
    n = len(date_idx)
    print(f"  OHLC日索引: {n}日")

    inv_d = inv_seven.reindex(date_idx.index, method="ffill")
    tc_d = tc.reindex(date_idx.index, method="ffill")
    galv_d = galv.reindex(date_idx.index, method="ffill")
    warrant_d = warrant.reindex(date_idx.index, method="ffill")

    for name, s in [("库存(日)", inv_d), ("TC(日)", tc_d), ("镀锌(日)", galv_d), ("仓单(日)", warrant_d)]:
        print(f"  {name}: 非空{s.notna().sum()}日")

    # 4. 构造 P_fund 产业回归压力锚
    # 锌的D_f = alpha*(P_fund - P_t)。P_fund是"产业基本面价格锚"。
    # 锌冶炼成本线随TC（加工费）波动：TC高=矿端紧张=冶炼成本抬升。
    # 用TC的滚动z-score映射到close水平的"成本价格"：
    #   P_fund_t = close_t + k * z(TC_t) * close_t
    # 当TC远高于历史(高z)时，P_fund > close，D_f = alpha*(P_fund-close)>0 → 多(产业回归)
    # 这捕捉"矿端成本抬升推升价格"的产业定价逻辑，与LC的成本曲线锚同构。
    tc_z = (tc_d - tc_d.rolling(180, min_periods=60).mean()) / (tc_d.rolling(180, min_periods=60).std() + 1e-9)
    tc_z = tc_z.fillna(0)
    close_s = date_idx["close"]
    k_pfund = 0.03   # TC每1个标准差映射到价格的3%偏离
    P_fund = close_s * (1 + k_pfund * tc_z)

    print(f"\n[4] P_fund产业锚: 均值{P_fund.mean():.0f}, 与close相关{close_s.corr(P_fund):.3f}")

    # 5. 构造HAM因子表（对齐exp401 schema: date,close,P_fund,...,Total_Demand,...）
    print("\n[5] 构造HAM因子表")
    ham = pd.DataFrame({
        "date": date_idx.index,
        "close": close_s.values,
        "P_fund": P_fund.values,
        "Total_Demand": galv_d.values,   # 镀锌产量作需求代理
        # n_c/n_f等Logit占比字段置0（exp409修复后用D_c/D_f原生动力学，不用占比）
        "n_f": 0.0, "n_c": 0.0,
        "n_f_minus_n_c": 0.0,
        "D_f": 0.0, "D_c": 0.0, "profit_f": 0.0, "profit_c": 0.0,
        "valid": 1,
    })
    # 丢弃P_fund/Total_Demand全NaN的早期（TC/镀锌预热不足）
    ham = ham.dropna(subset=["P_fund", "Total_Demand"]).reset_index(drop=True)
    ham.to_csv(os.path.join(RAW_DIR, "zinc_ham_factors.csv"), index=False)
    print(f"  有效行: {len(ham)}, {ham['date'].min().date()}~{ham['date'].max().date()}")

    # 6. 构造基本面三因子表（对齐exp406a schema）
    print("\n[6] 构造基本面三因子表")
    stock_pct20 = inv_d.pct_change(20)
    wh_basis = close_s - warrant_d   # 仓单基差 = 期货 - 仓单结算
    wh_basis_x_stockpct = wh_basis * (warrant_d.pct_change(20).fillna(0))
    cell_pct20 = galv_d.pct_change(20)  # 镀锌产量环比(需求)

    fund = pd.DataFrame({
        "date": date_idx.index,
        "f_stock_pct20": stock_pct20.values,
        "f_wh_basis": wh_basis.values,
        "f_wh_basis_x_stockpct": wh_basis_x_stockpct.values,
        "f_oprate_pct20": np.nan,
        "f_prod_pct20": np.nan,
        "f_cell_pct20": cell_pct20.values,
        "close": close_s.values,
        "target_fwd": np.nan,
    })
    # 丢弃三因子全NaN的预热期
    fund = fund.dropna(subset=["f_stock_pct20", "f_wh_basis_x_stockpct", "f_cell_pct20"]).reset_index(drop=True)
    fund.to_csv(os.path.join(FUND_DIR, "zinc_fund_factors.csv"), index=False)
    print(f"  有效行: {len(fund)}, {fund['date'].min().date()}~{fund['date'].max().date()}")

    # 7. 校验
    print("\n[7] 数据校验")
    print(f"  HAM因子表: {os.path.join(RAW_DIR,'zinc_ham_factors.csv')}")
    print(f"  基本面因子表: {os.path.join(FUND_DIR,'zinc_fund_factors.csv')}")
    print(f"  OHLC表: {os.path.join(RAW_DIR,'zinc_future.csv')}")
    print("\n✅ exp427 数据层构造完成")
