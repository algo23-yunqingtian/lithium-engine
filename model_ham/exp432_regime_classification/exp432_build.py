#!/usr/bin/env python3
"""
exp432: HAM 行情边界量化复盘研究 —— 数据构建层

【约束遵守】纯离线研究，只读历史数据，不修改 ham_pipeline.py / 风控阈值 / 实盘定时任务。

数据源（全部本地）:
  - OHLC: model_ham/raw_data/lithium_future.csv (2023-07~2026-09, 763行, 含 volume/position)
  - 现货+基差: lc_spot.db.spot_price (含 spot_price / dom_basis / dom_basis_rate)
  - IC 时序: model_ham/exp429_production/data/exp429_monitor_metrics.csv (IC_roll)
  - 告警序列: model_ham/exp429_production/data/exp429_alert_series.csv (alert_level)

产出: data/exp432_dataset.csv — 全历史逐日宽表 (供后续切片/特征/判别使用)
"""
import os
import sys
import numpy as np
import pandas as pd
import sqlite3

MODEL = "/home/ubuntu/lithium-engine/model_ham"
EXP_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(EXP_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)

OHLC_CSV = os.path.join(MODEL, "raw_data", "lithium_future.csv")
SPOT_DB = "/home/ubuntu/lc_futures_data/data/lc_spot.db"
MON_CSV = os.path.join(MODEL, "exp429_production", "data", "exp429_monitor_metrics.csv")
ALERT_CSV = os.path.join(MODEL, "exp429_production", "data", "exp429_alert_series.csv")

# 研究区间: 与 IC 监控有效期对齐 (IC 有效样本起点)
IC_VALID_START = "2024-02-01"
IC_VALID_END = "2026-09-07"


def build_dataset():
    """构建全历史逐日研究宽表."""
    # ---- OHLC ----
    oh = pd.read_csv(OHLC_CSV)
    oh["date"] = pd.to_datetime(oh["date"])
    oh = oh.sort_values("date").reset_index(drop=True)

    df = oh.copy()

    # ---- 1. 波动率 (已实现波动) ----
    df["ret"] = df["close"].pct_change()
    df["rvol20"] = df["ret"].rolling(20).std() * 100          # 20日已实现波动率(%)
    df["rvol60"] = df["ret"].rolling(60).std() * 100          # 60日已实现波动率(%)
    # 相对波动率: 当前波动 / 历史中位 (剔除品种绝对波动差异)
    df["rvol20_rel"] = df["rvol20"] / df["rvol20"].rolling(200).median()

    # ---- 2. 持仓增减 ----
    df["pos_chg_5d"] = df["position"].diff(5)                 # 5日持仓变动
    df["pos_chg_5d_pct"] = df["pos_chg_5d"] / df["position"].shift(5) * 100
    df["pos_chg_20d"] = df["position"].diff(20)
    df["pos_chg_20d_pct"] = df["pos_chg_20d"] / df["position"].shift(20) * 100
    # 持仓-价格背离: 价格上涨但持仓下降 = 投机撤退
    df["pos_price_diverge"] = np.sign(df["ret"].rolling(5).sum()) * np.sign(df["pos_chg_5d"])
    # 持仓-波动背离: 高波动+持仓下降 = 单边去杠杆

    # ---- 3. 仓单环比 ----
    # 注: lithium_future.csv 无独立仓单列。以 volume(成交量) 5日环比代理"仓单/活跃环比"，
    # 真实仓单需 GFEX 披露数据（不在本仓库）。此代理已在报告中声明局限。
    df["vol_chg_5d_pct"] = df["volume"].pct_change(5) * 100
    df["vol_chg_20d_pct"] = df["volume"].pct_change(20) * 100

    # ---- 4/5. 基差 + 现货-期货价差 (来自 spot_price 表) ----
    conn = sqlite3.connect(SPOT_DB)
    sp = pd.read_sql("SELECT * FROM spot_price", conn)
    conn.close()
    sp["date"] = pd.to_datetime(sp["date"].astype(str), format="%Y%m%d")
    sp = sp.sort_values("date").reset_index(drop=True)
    sp = sp[["date", "spot_price", "near_basis", "dom_basis",
             "near_basis_rate", "dom_basis_rate"]].rename(
        columns={"dom_basis": "basis_abs", "dom_basis_rate": "basis_rate",
                 "near_basis": "near_basis_abs", "near_basis_rate": "near_basis_rate"})
    df = df.merge(sp, on="date", how="left")
    # 基差变动 (结构性信号: 升水转贴水)
    df["basis_rate_chg5"] = df["basis_rate"].diff(5)
    df["basis_turn"] = np.sign(df["basis_rate"])              # 基差符号
    df["spot_close_gap_pct"] = (df["spot_price"] - df["close"]) / df["close"] * 100

    # ---- 6. 成交活跃度 ----
    df["vol_rel"] = df["volume"] / df["volume"].rolling(20).mean()   # 相对活跃度
    df["amt_proxy"] = df["volume"] * df["close"]                      # 成交额代理
    df["amt_rel"] = df["amt_proxy"] / df["amt_proxy"].rolling(20).mean()

    # ---- IC + 告警 (仅 IC 有效期有值) ----
    mon = pd.read_csv(MON_CSV)
    mon["date"] = pd.to_datetime(mon["date"])
    df = df.merge(mon[["date", "IC_roll", "dev_median", "dev_mean", "dev_p90",
                       "alpha_cv_roll", "beta_cv_roll"]], on="date", how="left")
    alr = pd.read_csv(ALERT_CSV)
    alr["date"] = pd.to_datetime(alr["date"])
    df = df.merge(alr[["date", "alert_level", "flags"]], on="date", how="left")

    # ---- 单边行情标注 (事后真值, 用于评估预警) ----
    # 定义: |N日滚动累计收益| > 阈值 且 方向一致(单调性强) = 单边行情
    # 用前瞻 20 日收益方向一致性判定 (仅事后标注, 不进入判别)
    f_ret20 = df["close"].shift(-20) / df["close"] - 1
    f_dir20 = np.sign(f_ret20)
    # 前瞻 20 日路径单调性: 20日中同向日占比
    fwd = df["ret"].shift(-1)
    mono20 = []
    for i in range(len(df)):
        w = fwd.iloc[i:i + 20].dropna().values
        if len(w) < 10:
            mono20.append(np.nan)
        else:
            sgn = np.sign(w)
            mono20.append((np.sum(sgn > 0) + 0.0) / len(w))   # 上涨占比
    df["fwd20_ret"] = f_ret20
    df["fwd20_dir"] = f_dir20
    df["fwd20_up_ratio"] = mono20

    return df


def classify_regime(df, vol_thr_pct, trend_thr_pct):
    """
    事后标注两类区间 (基于前瞻真值, 用于评估判别器):
      - VALID  : HAM 有效博弈区间 (震荡/双向, 分歧信号有预测力)
      - TREND  : 单边一致预期失效区间 (强趋势, 分歧信号失效)

    判定 (前瞻 20 日):
      TREND 条件: |fwd20_ret| > trend_thr_pct (小数形式, 如 0.08=8%) 且 单边性强
                 (fwd20_up_ratio >= 0.65 或 <= 0.35, 即 20 日中 >=13 日同向)
    """
    regime = pd.Series("UNKNOWN", index=df.index)
    trend = (df["fwd20_ret"].abs() > trend_thr_pct) & \
            ((df["fwd20_up_ratio"] >= 0.65) | (df["fwd20_up_ratio"] <= 0.35))
    regime[trend] = "TREND"
    regime[~trend & df["fwd20_ret"].notna()] = "VALID"
    return regime


if __name__ == "__main__":
    print("=" * 72)
    print("exp432 数据构建层")
    print("=" * 72)
    df = build_dataset()
    df = df[(df["date"] >= IC_VALID_START) & (df["date"] <= IC_VALID_END)].reset_index(drop=True)
    print(f"研究区间: {IC_VALID_START} ~ {IC_VALID_END} | {len(df)} 交易日")

    # 字段可用性
    print("\n[关键字段非空率]")
    for c in ["rvol20", "pos_chg_20d_pct", "vol_chg_20d_pct",
              "basis_rate", "spot_price", "vol_rel", "IC_roll", "alert_level",
              "fwd20_ret", "fwd20_up_ratio"]:
        nn = df[c].notna().mean() * 100
        print(f"  {c:<20}: {nn:5.1f}%")

    # 区间划分 (默认阈值: 20日 |累计|>8% 且单边性>65%)
    print("\n[区间划分] 阈值: |fwd20_ret|>8% 且 单边占比>65%/<35%")
    reg = classify_regime(df, vol_thr_pct=15, trend_thr_pct=0.08)
    vc = reg.value_counts()
    for k in ["VALID", "TREND", "UNKNOWN"]:
        if k in vc.index:
            print(f"  {k:<10}: {vc[k]:4d} 日 ({vc[k]/len(df)*100:5.1f}%)")

    df["regime_gt"] = reg   # ground truth
    out = os.path.join(DATA_DIR, "exp432_dataset.csv")
    df.to_csv(out, index=False)
    print(f"\n产出 -> {out}")
    print(f"总行数: {len(df)}")
