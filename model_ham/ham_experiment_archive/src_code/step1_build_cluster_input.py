"""
step1_build_cluster_input.py — 重建聚类输入数据集
补入 spread_near1_near3（滚动近月1-近月3跨期价差）
5项特征：warehouse_stock, basis_spot_main, spread_near1_near3, hv_20d, oi_main
"""
import os, sys, json
import numpy as np
import pandas as pd
import sqlite3

BASE = "model_ham"
DB = "/home/ubuntu/lithium_calendar/lithium.db"

# ---------- 1. 计算 spread_near1_near3 ----------
print("=== 1. 计算 spread_near1_near3 ===")
con = sqlite3.connect(DB)
cp = pd.read_sql("SELECT date, contract, close, volume FROM contract_prices", con, parse_dates=["date"])
con.close()

spread_records = []
for date, grp in cp.groupby("date"):
    active = grp[grp["volume"] > 0].sort_values("contract")
    if len(active) >= 3:
        near1 = active.iloc[0]
        near3 = active.iloc[2]
        spread_records.append({
            "date": date,
            "spread_near1_near3": near1["close"] - near3["close"],
            "near1": near1["contract"],
            "near3": near3["contract"]
        })
    else:
        spread_records.append({
            "date": date,
            "spread_near1_near3": np.nan,
            "near1": active.iloc[0]["contract"] if len(active) > 0 else None,
            "near3": None
        })

spread_df = pd.DataFrame(spread_records)
n_total = len(spread_df)
n_valid = spread_df["spread_near1_near3"].notna().sum()
print(f"  总日期: {n_total}, 有值: {n_valid}, 缺失: {n_total - n_valid} ({(n_total-n_valid)/n_total*100:.1f}%)")

# ---------- 2. 读取其他特征 ----------
print("\n=== 2. 读取其他特征 ===")

# 2a. 主力合约日线 (close, position)
ham = pd.read_csv(os.path.join(BASE, "raw_data", "lithium_future.csv"))
ham["date"] = pd.to_datetime(ham["date"])
print(f"  lithium_future.csv: {len(ham)} 行, {ham['date'].min().date()} ~ {ham['date'].max().date()}")

# 2b. 现货均价 (basis_spot_main = spot_avg - close)
spot = pd.read_csv(os.path.join(BASE, "raw_data", "spot_price.csv"))
spot["date"] = pd.to_datetime(spot["date"], format="%Y%m%d")
print(f"  spot_price.csv: {len(spot)} 行")

# 2c. 仓单库存 (zhiji_external_data.json)
with open(os.path.join(BASE, "zhiji_external_data.json")) as f:
    zhiji = json.load(f)

wh = pd.DataFrame(zhiji["zhiji_wh_receipt"])
wh["value"] = pd.to_numeric(wh["value"])
wh["date"] = pd.to_datetime(wh["date"])
wh = wh.sort_values("date").set_index("date")[["value"]].rename(columns={"value": "warehouse_stock"})
wh.index.name = "date"
print(f"  zhiji_wh_receipt: {len(wh)} 行, {wh.index.min().date()} ~ {wh.index.max().date()}")

# ---------- 3. 合并特征 ----------
print("\n=== 3. 合并 5 项特征 ===")

# 以主力合约日线为主表
df = ham[["date", "close", "position"]].copy()

# basis_spot_main = spot_avg - close
df = df.merge(spot[["date", "spot_price"]].rename(columns={"spot_price": "spot_avg"}), on="date", how="left")
df["basis_spot_main"] = df["spot_avg"] - df["close"]

# hv_20d = log(close).rolling(20).std() * sqrt(242)
log_close = np.log(df["close"])
df["hv_20d"] = log_close.rolling(20).std() * np.sqrt(242)

# oi_main = position
df["oi_main"] = df["position"]

# spread_near1_near3
df = df.merge(spread_df[["date", "spread_near1_near3"]], on="date", how="left")

# warehouse_stock
df = df.join(wh, on="date", how="left")

# 输出
out = df[["date", "warehouse_stock", "basis_spot_main", "spread_near1_near3", "hv_20d", "oi_main"]].copy()
out["date"] = out["date"].dt.strftime("%Y-%m-%d")

print(f"\n=== 4. 特征覆盖度 ===")
for col in ["warehouse_stock", "basis_spot_main", "spread_near1_near3", "hv_20d", "oi_main"]:
    valid = out[col].notna().sum()
    total = len(out)
    print(f"  {col}: {valid}/{total} ({valid/total*100:.1f}%)")

out_path = os.path.join(BASE, "cluster_input.csv")
out.to_csv(out_path, index=False)
print(f"\n已输出: {out_path} ({len(out)} 行)")
print("step1 完成")
