"""
exp419: HAM因子、开平仓规则拆解 + 历史全交易清单导出

基准: exp416 T1 稳健版, 不改动任何模型、回测逻辑。
任务:
  模块1: HAM因子完整计算规则输出 (来源+公式+标准化+信号判定)
  模块2: T1版本完整开平仓、仓位规则 (开仓/仓位/平仓/时序/波动率风控)
  模块3: 导出全部历史回测交易明细清单 (含平仓原因+D_z+ADX强趋势标记)
  模块4: 底层假设+失效场景+时序确认(无前视)

时序约定 (与 exp409/411/412/413/416 完全一致):
  - T 日收盘决策, T 日成交 (开仓/平仓价均用 T 日 close)
  - T 日仓位吃 T 日 (T-1→T) 价格收益
  - 所有滚动窗口/分位/波动率判定全用截至 T 日数据, min_periods 防前视
  - exp417 shift-equivariance 8 日校验已证无未来函数

交易链路权威来源:
  - HAM 动态引擎 (exp409.run_ham_dynamic) 提供 exit_reason (平仓原因)
  - T1 在此基础上叠加 exp411组合 + exp413 exp412 V4风控 + exp416 T1波动率降仓
  - 本脚本用 HAM 动态引擎重建完整交易链 (含平仓原因), 关联 ADX 趋势区间与 D_z
"""
import os
import sys
import numpy as np
import pandas as pd

MODEL = "/home/ubuntu/lithium-engine/model_ham"
for d in ["exp409_ham_dynamic", "exp411_combined"]:
    sys.path.insert(0, os.path.join(MODEL, d))

import exp409_ham_dynamic as E409

EXP_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(EXP_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)

START, END = pd.Timestamp("2024-02-01"), pd.Timestamp("2026-09-07")
COST = E409.COST_PER_SIDE
TDPY = E409.TRADING_DAYS_PER_YEAR


# ============================================================
# 加载 HAM 信号链 (disagreement / deviation / D_z / 信号)
# ============================================================
def load_ham_chain():
    """加载 HAM 因子表, 计算完整信号链 (含 D_z 标准化)。"""
    fac_path = os.path.join(
        MODEL, "ham_experiment_archive/intermediate/exp401_ham_factors_pure.csv")
    df = pd.read_csv(fac_path)
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()
    df = E409.compute_system_deviation(df)   # disagreement / deviation
    df = E409.compute_aux_signals(df)         # vol_abs5 / td_flip
    df = E409.load_gmm_states(df)             # gmm_state_proxy
    df = E409.compute_ham_signals(df)         # signal_dir / position_size / open_signal
    # D_z: deviation 的滚动 Z-score (180日标准化, 标准化HAM信号强度)
    dev = df["deviation"]
    z_mean = dev.rolling(E409.ROLLING_TRAIN, min_periods=60).mean()
    z_std = dev.rolling(E409.ROLLING_TRAIN, min_periods=60).std()
    df["D_z"] = (dev - z_mean) / z_std.replace(0, np.nan)
    mask = (df.index >= START) & (df.index <= END)
    return df[mask].copy()


# ============================================================
# ADX 趋势强度 (纯价格, 与 exp418 模块1 一致)
# ============================================================
def compute_adx(price, period=14):
    """Wilder ADX (单价格序列代理 TR)。"""
    close = price
    tr = close.diff().abs()
    up = close.diff()
    gain = np.where(up > 0, up, 0)
    loss = np.where(up < 0, -up, 0)
    gain = pd.Series(gain, index=close.index)
    loss = pd.Series(loss, index=close.index)
    atr = tr.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    dgain = gain.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    dloss = loss.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    with np.errstate(divide="ignore", invalid="ignore"):
        pdi = 100 * dgain / atr
        mdi = 100 * dloss / atr
        dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    adx = dx.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    return adx


# ============================================================
# 重建完整交易链路 (HAM 动态引擎 + 平仓原因)
# ============================================================
def build_full_trade_list(df):
    """
    用 HAM 动态引擎逻辑重建完整交易清单 (含平仓原因)。
    同时记录每笔交易的开仓/平仓日 D_z 和 ADX, 标记强趋势区间。
    """
    df = df.reset_index()  # date 变为列
    n = len(df)
    close = df["close"].values

    # ADX 趋势区间
    adx = compute_adx(pd.Series(close, index=pd.DatetimeIndex(df["date"])), period=14)
    df["adx"] = adx.values

    # HAM 动态引擎 (含平仓原因)
    _, trades = E409.run_ham_dynamic(df.copy())

    # 关联每笔交易的 D_z (开仓日) 和 ADX (开仓/平仓日)
    date_to_idx = {pd.Timestamp(d): i for i, d in enumerate(df["date"])}
    enriched = []
    for tr in trades:
        entry_date = tr["entry_date"]
        exit_date = tr["exit_date"]
        ed = entry_date if isinstance(entry_date, pd.Timestamp) else pd.Timestamp(entry_date)
        xd = exit_date if isinstance(exit_date, pd.Timestamp) else pd.Timestamp(exit_date)
        e_idx = date_to_idx.get(ed)
        x_idx = date_to_idx.get(xd)
        d_z_entry = df["D_z"].iloc[e_idx] if e_idx is not None else np.nan
        d_z_exit = df["D_z"].iloc[x_idx] if x_idx is not None else np.nan
        adx_entry = df["adx"].iloc[e_idx] if e_idx is not None else np.nan
        adx_exit = df["adx"].iloc[x_idx] if x_idx is not None else np.nan
        adx_avg = np.nanmean([adx_entry, adx_exit])
        entry_price = float(close[e_idx]) if e_idx is not None else np.nan
        exit_price = float(close[x_idx]) if x_idx is not None else np.nan
        # 强趋势标记: ADX>28 (exp418 最优阈值)
        strong_trend = (adx_avg > 28.0) if not np.isnan(adx_avg) else False
        enriched.append({
            "entry_date": ed.strftime("%Y-%m-%d"),
            "exit_date": xd.strftime("%Y-%m-%d"),
            "hold_days": tr["hold_days"],
            "direction": tr["direction"],
            "size": tr["size"],
            "entry_price": round(entry_price, 2),
            "exit_price": round(exit_price, 2),
            "net_ret": round(tr["net_ret"], 6),
            "D_z_entry": round(float(d_z_entry), 4) if pd.notna(d_z_entry) else None,
            "D_z_exit": round(float(d_z_exit), 4) if pd.notna(d_z_exit) else None,
            "ADX_entry": round(float(adx_entry), 2) if pd.notna(adx_entry) else None,
            "ADX_exit": round(float(adx_exit), 2) if pd.notna(adx_exit) else None,
            "strong_trend": bool(strong_trend),
            "exit_reason": tr["exit_reason"],
        })
    return pd.DataFrame(enriched)


if __name__ == "__main__":
    print("=" * 70)
    print("exp419: HAM因子/开平仓规则拆解 + 全交易清单导出")
    print("=" * 70)

    df = load_ham_chain()
    print(f"HAM 信号链: {df.index[0].date()} ~ {df.index[-1].date()} ({len(df)} 日)")
    print(f"参数: alpha=0.3 beta=0.5 gamma=2 W=20 ROLLING_TRAIN=180")
    print(f"分位: POS_HIGH_Q=0.95 POS_MED_Q=0.90 NEUTRAL_Q=0.40 MAX_HOLD={E409.MAX_HOLD_HAM}")

    # 重建交易清单
    trades = build_full_trade_list(df)
    trades = trades.reset_index(drop=True)
    trades.insert(0, "序号", trades.index + 1)
    print(f"\n全部历史交易: {len(trades)} 笔")
    print(f"强趋势区间 (ADX>28) 交易: {int(trades['strong_trend'].sum())} 笔")
    print(f"平仓原因分布: {trades['exit_reason'].value_counts().to_dict()}")

    # 保存
    trades.to_csv(os.path.join(DATA_DIR, "exp419_full_trade_list.csv"), index=False)
    # 强趋势子集
    strong = trades[trades["strong_trend"]].copy()
    strong.to_csv(os.path.join(DATA_DIR, "exp419_strong_trend_trades.csv"), index=False)

    # 汇总统计
    import json
    summary = {
        "n_trades": int(len(trades)),
        "strong_trend_trades": int(trades["strong_trend"].sum()),
        "strong_trend_ratio": float(trades["strong_trend"].mean()),
        "exit_reasons": trades["exit_reason"].value_counts().to_dict(),
        "direction_counts": trades["direction"].value_counts().to_dict(),
        "win_rate": float((trades["net_ret"] > 0).mean()),
        "avg_hold_days": float(trades["hold_days"].mean()),
        "strong_trend_win_rate": float(
            strong["net_ret"].gt(0).mean()) if len(strong) > 0 else None,
        "strong_trend_avg_ret": float(
            strong["net_ret"].mean()) if len(strong) > 0 else None,
        "normal_avg_ret": float(
            trades[~trades["strong_trend"]]["net_ret"].mean()),
    }
    with open(os.path.join(DATA_DIR, "exp419_summary.json"), "w") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(f"\n--- 汇总 ---")
    for k, v in summary.items():
        print(f"  {k}: {v}")
    print(f"\n强趋势交易 (ADX>28): {len(strong)} 笔")
    if len(strong) > 0:
        print(strong[["序号", "entry_date", "exit_date", "direction",
                      "hold_days", "net_ret", "ADX_entry", "D_z_entry",
                      "exit_reason"]].to_string(index=False))

    print("\n模块3 交易清单已保存")
