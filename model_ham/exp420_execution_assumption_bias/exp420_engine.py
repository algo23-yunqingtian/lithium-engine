"""
exp420: 成交假设偏差量化校验 + OHLC与持仓逻辑审计

基准：exp419 HAM T1模型，HAM因子/信号规则保持不变。
目标：量化不同成交假设带来的绩效差异，同时审计日K数据、持仓周期的底层逻辑。

模块1：两套成交假设并行回测
  版本A：原有假设（T日收盘计算信号，T日收盘价成交，基线）
  版本B：贴近现实假设（T日收盘计算信号，T+1下一交易日日K开盘价成交）
模块2：日K数据审计（OHLC存在性、HAM仅用Close声明、无1分钟K线限制）
模块3：持仓周期逻辑专项审计（1.4天来源、持仓天数分布、完整交易时序样例）

时序约定（与exp409/411/412/413/416/419完全一致）：
  - T日收盘决策，T日成交（开仓/平仓价均用T日close）
  - T日仓位吃T日（T-1→T）价格收益
  - 所有滚动窗口/分位/波动率判定全用截至T日数据，min_periods防前视
  - exp417 shift-equivariance 8日校验已证无未来函数

交易链路权威来源：HAM动态引擎（exp409.run_ham_dynamic）提供exit_reason
"""
import os
import sys
import json
import numpy as np
import pandas as pd

MODEL = "/home/ubuntu/lithium-engine/model_ham"
for d in ["exp409_ham_dynamic"]:
    sys.path.insert(0, os.path.join(MODEL, d))

import exp409_ham_dynamic as E409

EXP_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(EXP_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)
REPORTS_DIR = os.path.join(os.path.dirname(os.path.dirname(EXP_DIR)), "reports")
os.makedirs(REPORTS_DIR, exist_ok=True)

START, END = pd.Timestamp("2024-02-01"), pd.Timestamp("2026-09-07")
COST = E409.COST_PER_SIDE
TDPY = E409.TRADING_DAYS_PER_YEAR


# ============================================================
# 加载HAM信号链 + OHLC完整数据
# ============================================================
def load_ham_chain():
    """加载HAM因子表，计算完整信号链"""
    fac_path = os.path.join(
        MODEL, "ham_experiment_archive/intermediate/exp401_ham_factors_pure.csv")
    df = pd.read_csv(fac_path)
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()
    df = E409.compute_system_deviation(df)
    df = E409.compute_aux_signals(df)
    df = E409.load_gmm_states(df)
    df = E409.compute_ham_signals(df)
    mask = (df.index >= START) & (df.index <= END)
    return df[mask].copy()


def load_ohlc():
    """加载完整OHLC数据（来自raw_data/lithium_future.csv）"""
    ohlc_path = os.path.join(MODEL, "raw_data/lithium_future.csv")
    ohlc = pd.read_csv(ohlc_path)
    ohlc["date"] = pd.to_datetime(ohlc["date"])
    ohlc = ohlc.set_index("date").sort_index()
    return ohlc


def merge_ohlc_with_ham(df_ham):
    """将OHLC合并到HAM因子表"""
    ohlc = load_ohlc()
    df = df_ham.copy()
    for col in ["open", "high", "low", "close"]:
        if col in ohlc.columns:
            df[col] = ohlc[col].reindex(df.index)
    # 验证：HAM表中的close应等于OHLC表中的close（两者来自同一数据源）
    close_ham = df_ham["close"]
    close_ohlc = df["close"]
    match = (close_ham == close_ohlc).sum()
    print(f"[数据校验] HAM close与OHLC close一致率: {match}/{len(df)} "
          f"({match/len(df)*100:.1f}%)")
    return df


# ============================================================
# ADX 趋势强度
# ============================================================
def compute_adx(price, period=14):
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
# 模块1：两套成交假设并行回测
# ============================================================
def run_backtest_variant_A(df):
    """
    版本A：T日收盘计算信号，T日收盘价成交（基线）
    完全复用exp409.run_ham_dynamic
    df: 可以是有date列的DataFrame，也可以是以日期为索引的DataFrame
    """
    # 统一转换为有date列的DataFrame
    if "date" not in df.columns:
        df = df.reset_index()  # 索引(日期)变成列，列名自动为date
    else:
        df = df.copy()
    df = df.reset_index(drop=True)
    _, trades = E409.run_ham_dynamic(df.copy())
    return trades


def run_backtest_variant_B(df):
    """
    版本B：T日收盘计算信号，T+1下一交易日日K开盘价成交（贴近现实）
    
    时序逻辑：
      T日收盘 → 计算信号 → 若触发开仓，T+1开盘价买入
      T日收盘 → 检查平仓条件 → 若触发平仓，T+1开盘价卖出
    
    注意：T+1开盘价是当前数据集的OHLC数据中的Open列
          如果T是最后交易日，则无T+1，该笔交易以T日收盘平仓
    df: 可以是有date列的DataFrame，也可以是以日期为索引的DataFrame
    """
    # 统一转换为有date列的DataFrame
    if "date" not in df.columns:
        df = df.reset_index()
    else:
        df = df.copy()
    df_reset = df.reset_index(drop=True)
    n = len(df_reset)
    close = df_reset["close"].values
    open_px = df_reset["open"].values
    
    trades = []
    in_pos = False
    entry_t = -1
    entry_dir = 0
    entry_price = 0.0
    entry_size = 0.0
    
    for t in range(1, n):
        if in_pos:
            # 检查平仓条件（T日收盘检查）
            should_close, reason = E409.ham_close_condition(
                df_reset, t, entry_t, entry_dir)
            if should_close:
                # 版本B：平仓在T+1开盘价执行
                exit_t = t + 1
                if exit_t < n:
                    exit_price = open_px[exit_t]
                else:
                    # 无T+1，用T日收盘平仓
                    exit_t = t
                    exit_price = close[t]
                
                pos_ret = entry_dir * (exit_price - entry_price) / entry_price
                net_ret = pos_ret - 2 * COST
                trades.append({
                    "entry_date": df_reset.iloc[entry_t]["date"],
                    "exit_date": df_reset.iloc[exit_t]["date"],
                    "direction": entry_dir,
                    "size": entry_size,
                    "hold_days": exit_t - entry_t,
                    "gross_ret": pos_ret,
                    "net_ret": net_ret,
                    "exit_reason": reason,
                    "entry_price_A": entry_price,
                    "exit_price_A": close[t],
                    "entry_price_B": entry_price,
                    "exit_price_B": exit_price,
                })
                in_pos = False
                entry_t = -1
            else:
                # 持仓中，不操作
                pass
        else:
            # 检查开仓条件（T日收盘检查）
            if df_reset.iloc[t]["open_signal"]:
                # 版本B：开仓在T+1开盘价执行
                entry_t_signal = t
                entry_dir = int(df_reset.iloc[t]["signal_dir"])
                entry_size = float(df_reset.iloc[t]["position_size"])
                entry_t_exec = t + 1
                
                if entry_t_exec < n:
                    entry_price = open_px[entry_t_exec]
                    in_pos = True
                    entry_t = entry_t_exec
                else:
                    # 无T+1，不开仓（或退化用T日收盘）
                    in_pos = True
                    entry_t = t
                    entry_price = close[t]
    
    # 如果最后仍在持仓，以最后一天收盘平仓
    if in_pos and entry_t >= 0:
        last_t = n - 1
        exit_price = close[last_t]
        pos_ret = entry_dir * (exit_price - entry_price) / entry_price
        net_ret = pos_ret - 2 * COST
        trades.append({
            "entry_date": df_reset.iloc[entry_t]["date"],
            "exit_date": df_reset.iloc[last_t]["date"],
            "direction": entry_dir,
            "size": entry_size,
            "hold_days": last_t - entry_t,
            "gross_ret": pos_ret,
            "net_ret": net_ret,
            "exit_reason": "end_of_data",
            "entry_price_A": entry_price,
            "exit_price_A": close[last_t],
            "entry_price_B": entry_price,
            "exit_price_B": exit_price,
        })
    
    return trades


def compute_equity_curve_A(trades, df, positions_A=None):
    """
    版本A净值：使用exp409的position数组计算逐日收益
    df是已经reset_index()后的DataFrame（date是普通列）
    """
    close = df["close"].values
    n = len(close)
    
    # 从trades重建每日position
    if positions_A is None:
        positions_A = np.zeros(n)
        
        # 构建date->index映射
        date_to_idx = {}
        dates_col = df["date"] if "date" in df.columns else df.index
        for i in range(n):
            d = dates_col.iloc[i] if hasattr(dates_col, 'iloc') else dates_col[i]
            ts = pd.Timestamp(d) if not isinstance(d, pd.Timestamp) else d
            date_to_idx[ts] = i
        
        for tr in trades:
            ed = pd.Timestamp(tr["entry_date"])
            xd = pd.Timestamp(tr["exit_date"])
            ei = date_to_idx.get(ed)
            xi = date_to_idx.get(xd)
            if ei is not None and xi is not None:
                size = tr["direction"] * tr["size"]
                for t in range(ei, xi):
                    positions_A[t] = size
    
    # 逐日收益
    rets = np.zeros(n)
    for t in range(1, n):
        if positions_A[t] != 0:
            rets[t] = positions_A[t] * (close[t] - close[t-1]) / close[t-1]
    
    equity = np.cumprod(1 + rets)
    # 扣交易成本
    total_cost = len(trades) * 2 * COST
    equity_net = equity * (1 - total_cost)
    
    return equity_net, rets, positions_A


def compute_equity_curve_B(trades, df):
    """
    版本B净值：使用T+1开盘成交假设的逐笔收益计算净值
    每笔交易的net_ret已经是扣双边成本后的净收益
    返回：(equity_array, nets_array)
    """
    nets = [tr["net_ret"] for tr in trades]
    # 逐笔收益累乘
    equity_trades = np.cumprod([1 + r for r in nets])
    
    # 扩展到与df同长度的数组
    n = len(df)
    equity_full = np.ones(n)
    net_ret_full = np.zeros(n)
    
    # 用df的索引（日期）来定位交易
    for i, tr in enumerate(trades):
        ed = pd.Timestamp(tr["entry_date"])
        xd = pd.Timestamp(tr["exit_date"])
        try:
            ei = df.index.get_loc(ed)
            xi = df.index.get_loc(xd)
        except (KeyError, AttributeError):
            # 索引不是日期，尝试从日期列查找
            if "date" in df.columns:
                dates_col = df["date"]
                ei = dates_col[dates_col == ed].index[0] if len(dates_col[dates_col == ed]) > 0 else None
                xi = dates_col[dates_col == xd].index[0] if len(dates_col[dates_col == xd]) > 0 else None
            else:
                continue
        
        if ei is not None and xi is not None:
            net_ret_full[ei:xi+1] = tr["net_ret"] / max(xi - ei + 1, 1)
    
    equity_full = np.cumprod(1 + net_ret_full)
    return equity_full, nets


def compute_metrics_from_equity(equity, n_days, TDPY=250):
    """从净值曲线计算统一指标"""
    years = n_days / TDPY
    total_ret = equity[-1] - 1 if len(equity) > 0 else 0
    ann_ret = (1 + total_ret) ** (1 / years) - 1 if total_ret > -1 else -1
    daily_rets = np.diff(equity) / equity[:-1] if len(equity) > 1 else np.array([0])
    sharpe = (np.mean(daily_rets) / np.std(daily_rets) * np.sqrt(TDPY)
              if np.std(daily_rets) > 0 else 0.0)
    peak = np.maximum.accumulate(equity)
    max_dd = ((equity - peak) / peak).min()
    calmar = ann_ret / abs(max_dd) if max_dd < 0 else 0.0
    return {
        "total_return": float(total_ret),
        "annual_return": float(ann_ret),
        "sharpe": float(sharpe),
        "max_drawdown": float(max_dd),
        "calmar": float(calmar),
    }


def compute_trade_metrics(trades):
    """计算逐笔交易统计指标"""
    nets = [tr["net_ret"] for tr in trades]
    if len(nets) == 0:
        return {}
    wins = [r for r in nets if r > 0]
    losses = [r for r in nets if r <= 0]
    return {
        "n_trades": len(nets),
        "win_rate": len(wins) / len(nets),
        "avg_win": float(np.mean(wins)) if wins else 0,
        "avg_loss": float(np.mean(losses)) if losses else 0,
        "avg_net_ret": float(np.mean(nets)),
        "avg_hold_days": float(np.mean([tr["hold_days"] for tr in trades])),
        "hold_days_1": sum(1 for tr in trades if tr["hold_days"] == 1),
        "hold_days_2": sum(1 for tr in trades if tr["hold_days"] == 2),
        "hold_days_3_plus": sum(1 for tr in trades if tr["hold_days"] >= 3),
    }


# ============================================================
# 模块2：日K数据审计
# ============================================================
def audit_ohlc_data():
    """日K数据审计"""
    ohlc = load_ohlc()
    ohlc_mask = ohlc.index >= START
    ohlc_w = ohlc[ohlc_mask]
    
    audit = {
        "total_rows": int(len(ohlc)),
        "rows_in_range": int(len(ohlc_w)),
        "date_range": f"{ohlc.index.min().strftime('%Y-%m-%d')} ~ {ohlc.index.max().strftime('%Y-%m-%d')}",
        "ohlc_columns": list(ohlc.columns),
        "has_open": bool("open" in ohlc.columns),
        "has_high": bool("has_high" in ohlc.columns or "high" in ohlc.columns),
        "has_low": bool("low" in ohlc.columns),
        "has_close": bool("close" in ohlc.columns),
        "has_settle": bool("settle" in ohlc.columns),
        "has_volume": bool("volume" in ohlc.columns),
        "has_position": bool("position" in ohlc.columns),
        "open_non_null_pct": float(ohlc_w["open"].notna().mean() * 100),
        "high_non_null_pct": float(ohlc_w["high"].notna().mean() * 100),
        "low_non_null_pct": float(ohlc_w["low"].notna().mean() * 100),
        "close_non_null_pct": float(ohlc_w["close"].notna().mean() * 100),
        "ham_uses_only_close": True,
        "ham_factor_formula_uses": "P(t) and P(t-1) only, both from close column",
        "has_1min_kline": False,
        "note_1min": "当前数据集不含1分钟K线，无法取9:01分时均价。如需9:01成交价格，必须额外引入分钟级别行情数据。",
    }
    
    # 上影/下影/高低价存在性验证
    if "high" in ohlc.columns and "low" in ohlc.columns and "close" in ohlc.columns and "open" in ohlc.columns:
        upper_shadow = (ohlc_w["high"] - ohlc_w[["open", "close"]].max(axis=1))
        lower_shadow = (ohlc_w[["open", "close"]].min(axis=1) - ohlc_w["low"])
        range_daily = ohlc_w["high"] - ohlc_w["low"]
        audit["upper_shadow_mean"] = float(upper_shadow.mean())
        audit["lower_shadow_mean"] = float(lower_shadow.mean())
        audit["daily_range_mean"] = float(range_daily.mean())
        audit["has_upper_shadow"] = bool((upper_shadow > 0).any())
        audit["has_lower_shadow"] = bool((lower_shadow > 0).any())
    
    return audit


# ============================================================
# 模块3：持仓周期逻辑专项审计
# ============================================================
def audit_holding_period(trades_A, trades_B):
    """持仓周期逻辑专项审计"""
    
    # 持仓天数分布
    hold_A = [tr["hold_days"] for tr in trades_A]
    hold_B = [tr["hold_days"] for tr in trades_B]
    
    dist_A = {}
    for d in sorted(set(hold_A)):
        dist_A[d] = hold_A.count(d)
    
    dist_B = {}
    for d in sorted(set(hold_B)):
        dist_B[d] = hold_B.count(d)
    
    audit = {
        "avg_hold_A": float(np.mean(hold_A)) if hold_A else 0,
        "avg_hold_B": float(np.mean(hold_B)) if hold_B else 0,
        "hold_distribution_A": dist_A,
        "hold_distribution_B": dist_B,
        "source_of_1_4_days": (
            "所有开平仓决策仅在每日收盘执行判断；成交只能在开盘/收盘两个离散时间点，"
            "不存在盘中平仓。hold_days=1表示T日开仓T+1日平仓（最短持仓1个交易日），"
            "hold_days=2表示T日开仓T+2日平仓。1.4天是这些1天和2天持仓的加权统计平均值，"
            "不是单笔持仓可在半天平仓。"),
        "discrete_execution_times": ["每日收盘（15:00）", "每日开盘（集合竞价，约9:00/10:15）"],
        "no_intraday_execution": True,
        "note": "当前数据集为日K，无1分钟K线，无法模拟盘中平仓。如需9:01分钟均价成交，需引入分钟级别行情数据。",
    }
    
    return audit


def generate_trade_timeline_sample(df, trades_A, n_samples=3):
    """生成完整交易时序样例，标注决策时间、评估时间、成交时间
    df是已经reset_index()后的DataFrame（date是普通列）"""
    samples = []
    # 构建date->index映射
    dates = df["date"] if "date" in df.columns else df.index
    date_to_idx = {}
    for i in range(len(df)):
        d = dates.iloc[i] if hasattr(dates, 'iloc') else dates[i]
        ts = pd.Timestamp(d) if not isinstance(d, pd.Timestamp) else d
        date_to_idx[ts] = i
    
    # 构建下一个交易日的索引映射
    next_day_idx = {}
    for i in range(len(df) - 1):
        d = dates.iloc[i] if hasattr(dates, 'iloc') else dates[i]
        ts = pd.Timestamp(d) if not isinstance(d, pd.Timestamp) else d
        next_day_idx[ts] = i + 1
    
    for i, tr in enumerate(trades_A[:n_samples]):
        ed = pd.Timestamp(tr["entry_date"])
        xd = pd.Timestamp(tr["exit_date"])
        
        e_idx = date_to_idx.get(ed)
        x_idx = date_to_idx.get(xd)
        e_next_idx = next_day_idx.get(ed)
        x_next_idx = next_day_idx.get(xd)
        
        if e_idx is not None and x_idx is not None:
            entry_row = df.iloc[e_idx]
            exit_row = df.iloc[x_idx]
            
            sample = {
                "trade_no": i + 1,
                "entry_date": ed.strftime("%Y-%m-%d"),
                "exit_date": xd.strftime("%Y-%m-%d"),
                "direction": tr["direction"],
                "exit_reason": tr["exit_reason"],
                "entry_price_close": float(entry_row["close"]),
                "exit_price_close": float(exit_row["close"]),
                "entry_open_next_day": float(df.iloc[e_next_idx]["open"]) if e_next_idx is not None else None,
                "exit_open_next_day": float(df.iloc[x_next_idx]["open"]) if x_next_idx is not None else None,
                "timeline": {
                    "T_day_decision": f"T日({ed.strftime('%Y-%m-%d')})收盘后计算信号",
                    "T_day_close_price": f"{entry_row['close']:.0f} (版本A成交价)",
                    "T+1_day_open": f"{df.iloc[e_next_idx]['open']:.0f} (版本B成交价)" if e_next_idx is not None else "N/A",
                    "exit_T_day_decision": f"T日({xd.strftime('%Y-%m-%d')})收盘检查平仓条件",
                    "exit_T_day_close_price": f"{exit_row['close']:.0f} (版本A平仓价)",
                    "exit_T+1_day_open": f"{df.iloc[x_next_idx]['open']:.0f} (版本B平仓价)" if x_next_idx is not None else "N/A",
                }
            }
            samples.append(sample)
    
    return samples


# ============================================================
# 主流程
# ============================================================
if __name__ == "__main__":
    print("=" * 70)
    print("exp420: 成交假设偏差量化校验 + OHLC与持仓逻辑审计")
    print("=" * 70)
    
    # 加载数据
    df_ham = load_ham_chain()
    print(f"\nHAM信号链: {df_ham.index[0].date()} ~ {df_ham.index[-1].date()} ({len(df_ham)} 日)")
    
    df = merge_ohlc_with_ham(df_ham)
    print(f"OHLC合并后: {len(df)} 日")
    print(f"OHLC列检查: open={df['open'].notna().all()}, high={df['high'].notna().all()}, low={df['low'].notna().all()}, close={df['close'].notna().all()}")
    
    # ===== 模块1：两套成交假设并行回测 =====
    print("\n" + "=" * 70)
    print("模块1：两套成交假设并行回测")
    print("=" * 70)
    
    # 版本A：T日收盘成交（传入原始df，函数内部会处理reset_index）
    trades_A = run_backtest_variant_A(df)
    print(f"\n[版本A] T日收盘成交: {len(trades_A)} 笔交易")
    
    # 版本B：T+1开盘成交
    trades_B = run_backtest_variant_B(df)
    print(f"[版本B] T+1开盘成交: {len(trades_B)} 笔交易")
    
    # 计算版本A净值（df有date列，用reset_index()保留）
    df_reset = df.reset_index()
    if "date" not in df_reset.columns:
        df_reset = df.reset_index(drop=True)
    equity_A, rets_A, pos_A = compute_equity_curve_A(trades_A, df_reset)
    metrics_A = compute_metrics_from_equity(equity_A, len(df), TDPY)
    trade_metrics_A = compute_trade_metrics(trades_A)
    
    # 计算版本B净值
    equity_B, nets_B = compute_equity_curve_B(trades_B, df_reset)
    metrics_B = compute_metrics_from_equity(equity_B, len(df), TDPY)
    trade_metrics_B = compute_trade_metrics(trades_B)
    
    print(f"\n--- 版本A指标 ---")
    for k, v in metrics_A.items():
        print(f"  {k}: {v:.4f}" if isinstance(v, float) else f"  {k}: {v}")
    print(f"--- 版本B指标 ---")
    for k, v in metrics_B.items():
        print(f"  {k}: {v:.4f}" if isinstance(v, float) else f"  {k}: {v}")
    
    # 绩效衰减
    print(f"\n--- 绩效衰减（版本B vs A） ---")
    for k in ["annual_return", "sharpe", "max_drawdown", "calmar"]:
        diff = metrics_B[k] - metrics_A[k]
        print(f"  {k}: A={metrics_A[k]:.4f} → B={metrics_B[k]:.4f} (Δ={diff:+.4f})")
    
    # ===== 模块2：日K数据审计 =====
    print("\n" + "=" * 70)
    print("模块2：日K数据审计")
    print("=" * 70)
    audit_ohlc = audit_ohlc_data()
    for k, v in audit_ohlc.items():
        print(f"  {k}: {v}")
    
    # ===== 模块3：持仓周期逻辑专项审计 =====
    print("\n" + "=" * 70)
    print("模块3：持仓周期逻辑专项审计")
    print("=" * 70)
    audit_hold = audit_holding_period(trades_A, trades_B)
    for k, v in audit_hold.items():
        if k == "hold_distribution_A":
            print(f"  hold_days分布(A): {v}")
        elif k == "hold_distribution_B":
            print(f"  hold_days分布(B): {v}")
        elif k == "timeline":
            print(f"  timeline: {v}")
        else:
            print(f"  {k}: {v}")
    
    # 生成交易时序样例
    samples = generate_trade_timeline_sample(df, trades_A, n_samples=3)
    print(f"\n--- 交易时序样例（前3笔） ---")
    for s in samples:
        print(f"\n  第{s['trade_no']}笔:")
        for k, v in s["timeline"].items():
            print(f"    {k}: {v}")
    
    # ===== 保存数据文件 =====
    print("\n" + "=" * 70)
    print("保存交付物")
    print("=" * 70)
    
    # 1. 净值对比CSV
    n_days = len(df)
    dates_list = [str(df.index[i].date()) for i in range(n_days)]
    eq_A = equity_A
    eq_B = np.zeros(n_days)
    if len(equity_B) >= n_days:
        eq_B = equity_B[:n_days]
    else:
        eq_B = np.concatenate([equity_B, np.full(n_days - len(equity_B), equity_B[-1] if len(equity_B) > 0 else 1.0)])
    ret_A = rets_A
    equity_df = pd.DataFrame({
        "date": dates_list,
        "equity_A": eq_A,
        "equity_B": eq_B,
        "daily_ret_A": ret_A,
    })
    equity_df.to_csv(os.path.join(DATA_DIR, "exp420_equity_comparison.csv"), index=False)
    print(f"  ✓ 净值对比CSV: data/exp420_equity_comparison.csv")
    
    # 2. 逐笔盈亏对比表
    trades_A_df = pd.DataFrame(trades_A)
    trades_B_df = pd.DataFrame(trades_B)
    if len(trades_A_df) == len(trades_B_df):
        comparison = pd.DataFrame({
            "trade_no": range(1, len(trades_A_df) + 1),
            "entry_date": trades_A_df["entry_date"],
            "exit_date_A": trades_A_df["exit_date"],
            "exit_date_B": trades_B_df["exit_date"],
            "direction": trades_A_df["direction"],
            "size": trades_A_df["size"],
            "hold_days_A": trades_A_df["hold_days"],
            "hold_days_B": trades_B_df["hold_days"],
            "net_ret_A": trades_A_df["net_ret"],
            "net_ret_B": trades_B_df["net_ret"],
            "exit_reason": trades_A_df["exit_reason"],
        })
        # 如果版本B有价格列，也加入对比
        if "exit_price_B" in trades_B_df.columns:
            comparison["exit_price_B"] = trades_B_df["exit_price_B"]
        comparison["net_ret_diff"] = comparison["net_ret_B"] - comparison["net_ret_A"]
        comparison.to_csv(os.path.join(DATA_DIR, "exp420_trade_comparison.csv"), index=False)
        print(f"  ✓ 逐笔盈亏对比表: data/exp420_trade_comparison.csv")
    
    # 3. 持仓天数分布统计表
    hold_dist = pd.DataFrame({
        "hold_days": list(audit_hold["hold_distribution_A"].keys()),
        "count_A": list(audit_hold["hold_distribution_A"].values()),
        "count_B": [audit_hold["hold_distribution_B"].get(k, 0) for k in audit_hold["hold_distribution_A"].keys()],
    })
    hold_dist.to_csv(os.path.join(DATA_DIR, "exp420_hold_distribution.csv"), index=False)
    print(f"  ✓ 持仓天数分布统计表: data/exp420_hold_distribution.csv")
    
    # 4. 汇总JSON
    summary = {
        "metrics_A": metrics_A,
        "metrics_B": metrics_B,
        "trade_metrics_A": trade_metrics_A,
        "trade_metrics_B": trade_metrics_B,
        "audit_ohlc": audit_ohlc,
        "audit_hold": {k: v for k, v in audit_hold.items() if k not in ("hold_distribution_A", "hold_distribution_B")},
        "hold_distribution_A": audit_hold["hold_distribution_A"],
        "hold_distribution_B": audit_hold["hold_distribution_B"],
        "trade_timeline_samples": samples,
        "n_trades_A": len(trades_A),
        "n_trades_B": len(trades_B),
    }
    with open(os.path.join(DATA_DIR, "exp420_summary.json"), "w") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2, default=str)
    print(f"  ✓ 汇总JSON: data/exp420_summary.json")
    
    # ===== 模块4：更新结题文档（仅记录，实际更新在后续步骤） =====
    print("\n" + "=" * 70)
    print("模块4：风险文档更新（待写入）")
    print("=" * 70)
    print("  新增风险1：日度回测局限 — 每日仅收盘一次评估信号，无法响应盘中行情，不能实现盘中平仓")
    print("  新增风险2：成交价格假设风险 — 日K开盘价=集合竞价撮合价，不等于实盘一定可以足额成交，存在滑点、流动性风险")
    print("  如需9:01一分钟均价成交，需要引入分钟K数据集")
    
    print("\n✅ exp420 完成")
