"""
exp430: 真实行情影子盘 —— 每日数据获取 + 追加 + 流水线重跑

设计目标:
  1. 每日收盘后自动获取最新 LC 期货 OHLC + 现货价
  2. 追加到历史数据文件 (lithium_future.csv + exp401_ham_factors_pure.csv)
  3. 调用 exp429 流水线重跑，得到最新 D 因子 + 信号 + 仓位
  4. 记录当日影子盘交易日志（信号/仓位/模拟成交价/模拟盈亏）

铁律:
  - 仅记录信号、仓位、模拟盈亏，**不发起真实下单**
  - 每日运行后追加一行到 shadow_log.csv
  - 所有数据追加均做幂等检查（同日重复运行只更新不重复）

数据来源:
  - 期货 OHLC: akshare futures_zh_daily_sina(symbol='LC2409') 
    实际合约月份动态选择（近月主力）
  - 现货价: lc_spot.db 或 akshare 备选
"""
import os
import sys
import json
import numpy as np
import pandas as pd
from datetime import datetime, timedelta

MODEL = "/home/ubuntu/lithium-engine/model_ham"
EXP_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(EXP_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)

# exp429 路径
sys.path.insert(0, os.path.join(MODEL, "exp429_production"))
sys.path.insert(0, MODEL)
for d in ["exp409_ham_dynamic", "exp410_fund_dynamic", "exp411_combined",
          "exp413_robustness", "exp414_attribution"]:
    sys.path.insert(0, os.path.join(MODEL, d))
sys.path.insert(0, os.path.join(MODEL, "exp416_tail_risk_protect"))
sys.path.insert(0, os.path.join(MODEL, "exp424_t1_robustness"))
sys.path.insert(0, os.path.join(MODEL, "exp425_minute_execution"))
sys.path.insert(0, os.path.join(MODEL, "exp428_shadow_trading"))

# 数据文件路径
OHLC_CSV = os.path.join(MODEL, "raw_data", "lithium_future.csv")
HAM_PURE_CSV = os.path.join(MODEL, "ham_experiment_archive", "intermediate", "exp401_ham_factors_pure.csv")
SPOT_DB = "/home/ubuntu/lc_futures_data/data/lc_spot.db"

# 影子盘日志路径
SHADOW_LOG = os.path.join(DATA_DIR, "exp430_shadow_log.csv")
SHADOW_PNL = os.path.join(DATA_DIR, "exp430_shadow_pnl.csv")
SHADOW_SLIPPAGE = os.path.join(DATA_DIR, "exp430_slippage_deviation.csv")
SHADOW_IC_MONITOR = os.path.join(DATA_DIR, "exp430_ic_monitor.csv")
SHADOW_ALERTS = os.path.join(DATA_DIR, "exp430_alerts.csv")
SHADOW_RUN_LOG = os.path.join(DATA_DIR, "exp430_run_log.json")

# 回测基准 (配置A)
BENCHMARK_A = {
    "annual_return": 0.340,
    "max_drawdown": -0.068,
    "calmar": 4.98,
    "sharpe": 2.293,
    "n_trades": 135,
    "ic": 0.219,
}

# 滑点预算
SLIP_BP_BUDGET = 10.0  # 10bp 单侧

# 模拟账户初始资金
INIT_CAPITAL = 1_000_000.0  # 100万

# 交易单位 (碳酸锂期货: 1手 = 1吨)
CONTRACT_SIZE = 1


# ============================================================
# 1. 每日数据获取
# ============================================================
def get_main_contract():
    """获取当前主力合约代码 (LC + 4位数字)."""
    import akshare as ak
    # 尝试最近几个月的合约
    candidates = []
    now = datetime.now()
    for m in range(6):
        d = now + timedelta(days=30 * m)
        sym = f"LC{d.year % 100:02d}{d.month:02d}"
        try:
            df = ak.futures_zh_daily_sina(symbol=sym)
            if len(df) > 0 and df['volume'].iloc[-1] > 100:
                candidates.append((sym, df['volume'].iloc[-1], len(df)))
        except Exception:
            pass
    if not candidates:
        # fallback: 用 lc_spot.db 的近月合约
        import sqlite3
        if os.path.exists(SPOT_DB):
            conn = sqlite3.connect(SPOT_DB)
            row = conn.execute("SELECT near_symbol FROM spot_price ORDER BY date DESC LIMIT 1").fetchone()
            conn.close()
            if row:
                return row[0]
        return "LC2609"
    # 选成交量最大的
    candidates.sort(key=lambda x: x[1], reverse=True)
    return candidates[0][0]


def fetch_daily_ohlc(symbol="LC2609"):
    """从 akshare 获取期货日 OHLC 数据."""
    import akshare as ak
    df = ak.futures_zh_daily_sina(symbol=symbol)
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    return df


def fetch_spot_price():
    """获取现货价 (从 lc_spot.db)."""
    import sqlite3
    if not os.path.exists(SPOT_DB):
        return None
    conn = sqlite3.connect(SPOT_DB)
    try:
        row = conn.execute(
            "SELECT date, spot_price FROM spot_price ORDER BY date DESC LIMIT 1"
        ).fetchone()
        conn.close()
        if row:
            return {"date": row[0], "price": float(row[1])}
    except Exception:
        conn.close()
    return None


def fetch_realtime_ohlc():
    """获取今日实时 OHLC (盘中时用)."""
    import akshare as ak
    try:
        df = ak.futures_zh_realtime()
        # GFEX 合约不在 realtime 里，尝试从日K线拿最新
        symbol = get_main_contract()
        df2 = ak.futures_zh_daily_sina(symbol=symbol)
        if len(df2) > 0:
            row = df2.iloc[-1]
            return {
                "date": pd.Timestamp(row["date"]),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": int(row["volume"]),
                "position": int(row["hold"]),
                "settle": float(row["settle"]),
                "symbol": symbol,
            }
    except Exception as e:
        print(f"[WARN] fetch_realtime_ohlc: {e}")
    return None


# ============================================================
# 2. 数据追加 (幂等)
# ============================================================
def append_ohlc(new_row):
    """追加一行 OHLC 到 lithium_future.csv (幂等: 同日只更新)."""
    if not os.path.exists(OHLC_CSV):
        return False
    df = pd.read_csv(OHLC_CSV)
    df["date"] = pd.to_datetime(df["date"])
    new_date = pd.Timestamp(new_row["date"])
    if new_date in df["date"].values:
        # 更新已有行
        idx = df.index[df["date"] == new_date][0]
        for col in ["open", "high", "low", "close", "volume", "position", "settle"]:
            if col in new_row:
                df.loc[idx, col] = new_row[col]
        print(f"  [OHLC] 更新 {new_date.date()} 已有行")
    else:
        new_data = pd.DataFrame([new_row])
        df = pd.concat([df, new_data], ignore_index=True)
        df = df.sort_values("date").reset_index(drop=True)
        print(f"  [OHLC] 追加 {new_date.date()} 新行")
    df.to_csv(OHLC_CSV, index=False)
    return True


def append_ham_factors(date_str, close, p_fund):
    """追加一行到 exp401_ham_factors_pure.csv (幂等)."""
    if not os.path.exists(HAM_PURE_CSV):
        return False
    df = pd.read_csv(HAM_PURE_CSV)
    df["date"] = pd.to_datetime(df["date"])
    new_date = pd.Timestamp(date_str)
    
    # 计算 D_f, D_c (用简单代理: P_fund - close)
    # 注意: 实盘影子盘阶段，alpha/beta 由 rolling_deviation 内部估参，
    # 这里只追加 close 和 P_fund，D_f/D_c 会被 rolling_deviation 重算
    d_f = p_fund - close  # 产业回归压力 (未乘alpha)
    
    if new_date in df["date"].values:
        idx = df.index[df["date"] == new_date][0]
        df.loc[idx, "close"] = close
        df.loc[idx, "P_fund"] = p_fund
        df.loc[idx, "D_f"] = d_f
        print(f"  [HAM] 更新 {new_date.date()} 已有行 (close={close}, P_fund={p_fund:.0f})")
    else:
        new_row = {
            "date": new_date,
            "close": close,
            "P_fund": p_fund,
            "D_f": d_f,
            "D_c": 0.0,  # 会被 compute_aux_signals 重算
            "profit_f": 0.0, "profit_c": 0.0,
            "n_f": 0.0, "n_c": 1.0,
            "Total_Demand": 0.0,
            "n_f_minus_n_c": 0.0,
            "valid": 1,
        }
        df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
        df = df.sort_values("date").reset_index(drop=True)
        print(f"  [HAM] 追加 {new_date.date()} 新行 (close={close}, P_fund={p_fund:.0f})")
    df.to_csv(HAM_PURE_CSV, index=False)
    return True


# ============================================================
# 3. P_fund 估算 (现货价代理)
# ============================================================
def estimate_p_fund(close_price, spot_price=None):
    """
    估算 P_fund (产业基本面锚定价).
    
    策略:
    1. 如果有现货价 → 用现货价作为 P_fund 基准
    2. 如果没有现货价 → 用 close 的 20 日均线 + 调整
    
    注: 这是代理估算，实盘手册 §8.3 已记录此局限。
    """
    if spot_price and spot_price > 0:
        # P_fund ≈ 现货价 (产业成本锚)
        return float(spot_price)
    # fallback: close 的 5 日均线 (短期基本面锚代理)
    return float(close_price)  # 最简代理: P_fund = close


# ============================================================
# 4. 影子盘交易日志 (核心)
# ============================================================
def init_shadow_log():
    """初始化影子盘日志 (如果不存在)."""
    if not os.path.exists(SHADOW_LOG):
        cols = [
            "date", "close", "p_fund", "deviation", "disagreement",
            "D_c", "D_f", "alpha_t", "beta_t",
            "open_signal", "signal_dir",
            "pos_A", "pos_B", "action_A", "action_B",
            "shadow_active_A", "shadow_active_B",
            # 模拟成交
            "exec_price_901_A", "exec_price_901_B",
            "slippage_actual_A_bp", "slippage_actual_B_bp",
            "slippage_budget_bp",
            # 模拟盈亏
            "daily_pnl_A", "daily_pnl_B",
            "cum_pnl_A", "cum_pnl_B",
            "cum_return_A", "cum_return_B",
            "nav_A", "nav_B",
            # 持仓状态
            "holding_dir_A", "holding_dir_B",
            "holding_days_A", "holding_days_B",
            # 告警
            "alert_level", "alert_flags",
            # 数据来源
            "data_source", "contract_symbol",
        ]
        df = pd.DataFrame(columns=cols)
        df.to_csv(SHADOW_LOG, index=False)


def update_shadow_log(daily, ctx, config_results, alerts_df, exec_prices, 
                      prev_state, run_meta):
    """
    追加当日影子盘日志行。
    
    daily: exp429 流水线输出
    ctx: exp429 context
    config_results: {"A": {"pos": ...}, "B": {"pos": ...}}
    alerts_df: exp429 告警序列
    exec_prices: {"A": price, "B": price} 模拟成交价
    prev_state: 前一日状态 (nav, holding, cum_pnl 等)
    run_meta: 运行元数据 (data_source, contract_symbol)
    """
    init_shadow_log()
    
    log = pd.read_csv(SHADOW_LOG)
    log["date"] = pd.to_datetime(log["date"]) if len(log) > 0 else log["date"]
    
    # 取最新日的数据
    last_idx = len(daily) - 1
    row = daily.iloc[last_idx]
    today = pd.Timestamp(row["date"])
    
    close = float(ctx["price"].iloc[-1])
    
    # 模拟成交价 (9:01 分钟均价 + 10bp 滑点)
    # 影子盘: 用 close 作为代理成交价 + 滑点模拟
    pos_a = float(row["pos_A"])
    pos_b = float(row["pos_B"])
    
    # 如果流水线有 exec_prices, 用它; 否则用 close
    exec_price_a = exec_prices.get("A", close)
    exec_price_b = exec_prices.get("B", close)
    
    # 实际滑点 = (exec_price - close) / close * 10000 (bp)
    slip_a = (exec_price_a - close) / close * 10000 if close > 0 else 0
    slip_b = (exec_price_b - close) / close * 10000 if close > 0 else 0
    
    # 模拟盈亏
    # 前一日持仓方向 × 当日收益
    prev_dir_a = prev_state.get("holding_dir_A", 0)
    prev_dir_b = prev_state.get("holding_dir_B", 0)
    
    prev_close = prev_state.get("prev_close", close)
    daily_ret = (close - prev_close) / prev_close if prev_close > 0 else 0.0
    
    # PnL = 持仓方向 × 日收益 × 仓位 × 账户规模
    nav_a = prev_state.get("nav_A", INIT_CAPITAL)
    nav_b = prev_state.get("nav_B", INIT_CAPITAL)
    
    pnl_a = prev_dir_a * abs(pos_a) * daily_ret * nav_a if prev_dir_a != 0 else 0
    pnl_b = prev_dir_b * abs(pos_b) * daily_ret * nav_b if prev_dir_b != 0 else 0
    
    cum_pnl_a = prev_state.get("cum_pnl_A", 0) + pnl_a
    cum_pnl_b = prev_state.get("cum_pnl_B", 0) + pnl_b
    
    cum_ret_a = cum_pnl_a / INIT_CAPITAL
    cum_ret_b = cum_pnl_b / INIT_CAPITAL
    
    new_nav_a = nav_a + pnl_a
    new_nav_b = nav_b + pnl_b
    
    # 持仓状态更新
    new_dir_a = int(np.sign(pos_a)) if abs(pos_a) > 1e-9 else 0
    new_dir_b = int(np.sign(pos_b)) if abs(pos_b) > 1e-9 else 0
    
    # 持仓天数
    hold_days_a = prev_state.get("holding_days_A", 0)
    if new_dir_a != 0:
        hold_days_a = (hold_days_a + 1) if prev_dir_a == new_dir_a else 1
    else:
        hold_days_a = 0
    
    hold_days_b = prev_state.get("holding_days_B", 0)
    if new_dir_b != 0:
        hold_days_b = (hold_days_b + 1) if prev_dir_b == new_dir_b else 1
    else:
        hold_days_b = 0
    
    # 告警
    today_alert = alerts_df[alerts_df["date"] == today] if len(alerts_df) > 0 else pd.DataFrame()
    alert_level = today_alert["alert_level"].iloc[0] if len(today_alert) > 0 else "OK"
    alert_flags = today_alert["flags"].iloc[0] if len(today_alert) > 0 else "-"
    
    # 幂等: 同日只更新
    new_row = {
        "date": today.strftime("%Y-%m-%d"),
        "close": close,
        "p_fund": float(row.get("D_f", 0)) + close if "D_f" in row else 0,
        "deviation": float(row.get("deviation", 0)),
        "disagreement": float(row.get("disagreement", 0)),
        "D_c": float(row.get("D_c", 0)),
        "D_f": float(row.get("D_f", 0)),
        "alpha_t": float(row.get("alpha_t", 0)),
        "beta_t": float(row.get("beta_t", 0)),
        "open_signal": bool(row.get("open_signal", False)),
        "signal_dir": int(row.get("signal_dir", 0)),
        "pos_A": pos_a,
        "pos_B": pos_b,
        "action_A": row.get("action_A", "HOLD_FLAT"),
        "action_B": row.get("action_B", "HOLD_FLAT"),
        "shadow_active_A": int(abs(pos_a) > 1e-9),
        "shadow_active_B": int(abs(pos_b) > 1e-9),
        "exec_price_901_A": exec_price_a,
        "exec_price_901_B": exec_price_b,
        "slippage_actual_A_bp": slip_a,
        "slippage_actual_B_bp": slip_b,
        "slippage_budget_bp": SLIP_BP_BUDGET,
        "daily_pnl_A": pnl_a,
        "daily_pnl_B": pnl_b,
        "cum_pnl_A": cum_pnl_a,
        "cum_pnl_B": cum_pnl_b,
        "cum_return_A": cum_ret_a,
        "cum_return_B": cum_ret_b,
        "nav_A": new_nav_a,
        "nav_B": new_nav_b,
        "holding_dir_A": new_dir_a,
        "holding_dir_B": new_dir_b,
        "holding_days_A": hold_days_a,
        "holding_days_B": hold_days_b,
        "alert_level": alert_level,
        "alert_flags": alert_flags,
        "data_source": run_meta.get("data_source", "akshare_sina"),
        "contract_symbol": run_meta.get("contract_symbol", "unknown"),
    }
    
    if os.path.exists(SHADOW_LOG) and len(log) > 0:
        td_str = today.strftime("%Y-%m-%d")
        log_dates_str = log["date"].astype(str).str[:10]
        if td_str in log_dates_str.values:
            idx = log.index[log_dates_str == td_str][0]
            for col, val in new_row.items():
                log.loc[idx, col] = val
            print(f"  [Shadow] 更新 {today.date()} 已有行")
        else:
            log = pd.concat([log, pd.DataFrame([new_row])], ignore_index=True)
            print(f"  [Shadow] 追加 {today.date()} 新行")
    
    log.to_csv(SHADOW_LOG, index=False)
    
    # 返回更新后的状态
    return {
        "prev_close": close,
        "nav_A": new_nav_a, "nav_B": new_nav_b,
        "cum_pnl_A": cum_pnl_a, "cum_pnl_B": cum_pnl_b,
        "holding_dir_A": new_dir_a, "holding_dir_B": new_dir_b,
        "holding_days_A": hold_days_a, "holding_days_B": hold_days_b,
    }


def load_prev_state():
    """从影子盘日志加载前一日状态."""
    if not os.path.exists(SHADOW_LOG):
        return {}
    log = pd.read_csv(SHADOW_LOG)
    if len(log) == 0:
        return {}
    last = log.iloc[-1]
    return {
        "prev_close": float(last["close"]),
        "nav_A": float(last["nav_A"]),
        "nav_B": float(last["nav_B"]),
        "cum_pnl_A": float(last["cum_pnl_A"]),
        "cum_pnl_B": float(last["cum_pnl_B"]),
        "holding_dir_A": int(last["holding_dir_A"]),
        "holding_dir_B": int(last["holding_dir_B"]),
        "holding_days_A": int(last["holding_days_A"]),
        "holding_days_B": int(last["holding_days_B"]),
    }


# ============================================================
# 5. 运行元数据日志
# ============================================================
def log_run(status, message, **extra):
    """记录运行日志."""
    entry = {
        "timestamp": datetime.now().isoformat(),
        "status": status,
        "message": message,
        **extra,
    }
    
    history = []
    if os.path.exists(SHADOW_RUN_LOG):
        try:
            with open(SHADOW_RUN_LOG, "r") as f:
                history = json.load(f)
        except Exception:
            history = []
    
    history.append(entry)
    # 只保留最近 500 条
    if len(history) > 500:
        history = history[-500:]
    
    with open(SHADOW_RUN_LOG, "w") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)
    
    return entry


# ============================================================
# 主流程: 每日影子盘运行
# ============================================================
def update_end_date():
    """动态更新 exp424_engine 的 END 为 HAM 因子最新日期."""
    df = pd.read_csv(HAM_PURE_CSV)
    df["date"] = pd.to_datetime(df["date"])
    latest = df["date"].iloc[-1]
    
    # Patch exp424_engine START/END
    sys.path.insert(0, os.path.join(MODEL, "exp424_t1_robustness"))
    import exp424_engine as E424
    old_end = E424.END
    E424.END = latest
    print(f"  [END] 更新 {old_end.date()} -> {latest.date()}")
    
    # Also patch exp425_engine if it has END
    try:
        sys.path.insert(0, os.path.join(MODEL, "exp425_minute_execution"))
        import exp425_engine as E425
        if hasattr(E425, "END"):
            E425.END = latest
    except Exception:
        pass
    
    return latest


def run_shadow_trading(force_symbol=None):
    """
    每日影子盘主流程:
      1. 获取最新行情
      2. 追加数据
      3. 动态更新 END 日期
      4. 重跑 exp429 流水线
      5. 记录影子盘日志
    
    返回: (success, message, shadow_state)
    """
    print("=" * 78)
    print("exp430: 真实行情影子盘 —— 每日运行")
    print("=" * 78)
    
    try:
        # Step 1: 获取主力合约
        symbol = force_symbol or get_main_contract()
        print(f"\n[1] 主力合约: {symbol}")
        
        # Step 2: 获取最新 OHLC
        ohlc_df = fetch_daily_ohlc(symbol)
        latest = ohlc_df.iloc[-1]
        today_date = pd.Timestamp(latest["date"])
        
        # 检查是否已有今天的数据
        existing_ohlc = pd.read_csv(OHLC_CSV)
        existing_ohlc["date"] = pd.to_datetime(existing_ohlc["date"])
        last_existing = existing_ohlc["date"].iloc[-1]
        
        print(f"  最新行情日: {today_date.date()}, close={latest['close']}")
        print(f"  数据库最新日: {last_existing.date()}")
        
        if today_date <= last_existing:
            # 数据已是最新，但仍需重跑流水线（可能 OHLC 有更新）
            print(f"  [INFO] 行情数据已是最新，重跑流水线更新信号")
        
        # Step 3: 追加 OHLC
        new_ohlc = {
            "date": today_date,
            "open": float(latest["open"]),
            "high": float(latest["high"]),
            "low": float(latest["low"]),
            "close": float(latest["close"]),
            "volume": int(latest["volume"]),
            "position": int(latest["hold"]),
            "settle": float(latest["settle"]),
        }
        append_ohlc(new_ohlc)
        
        # Step 4: 获取现货价 → 估算 P_fund
        spot = fetch_spot_price()
        spot_price = spot["price"] if spot else None
        
        close_price = float(latest["close"])
        p_fund = estimate_p_fund(close_price, spot_price)
        print(f"\n[2] 现货价: {spot_price}, P_fund估算: {p_fund:.0f}")
        
        # 追加 HAM 因子
        append_ham_factors(today_date, close_price, p_fund)
        
        # Step 5: 动态更新 END 日期
        latest_date = update_end_date()
        
        # Step 6: 重跑 exp429 流水线
        print(f"\n[3] 重跑 exp429 流水线...")
        import ham_pipeline as HP
        daily, ctx, df_ham, cr = HP.run_pipeline()
        idx = ctx["idx"]
        print(f"  流水线: {idx[0].date()} ~ {idx[-1].date()} ({len(idx)} 日)")
        
        # Step 6: 绩效验证
        print(f"\n[4] 绩效验证...")
        import run_daily as RD
        results, slip_cal = RD.run_validation(ctx, df_ham)
        for ckey, r in results.items():
            print(f"  {r['name']}: 年化={r['annual_return']*100:.1f}% "
                  f"回撤={r['max_drawdown']*100:.1f}% Calmar={r['calmar']:.2f}")
        
        # Step 7: 监控告警
        print(f"\n[5] 监控告警...")
        import monitoring as MON
        ic = MON.compute_ic_series(ctx, cr["A"]["pos"])
        dd = MON.compute_deviation_dist(daily)
        wt = MON.compute_weight_series(daily)
        alerts, merged = MON.judge_drift(ic, dd, wt)
        
        today_alert = alerts[alerts["date"] == today_date]
        if len(today_alert) > 0:
            al = today_alert.iloc[0]
            print(f"  当日告警: {al['alert_level']} ({al['flags']})")
        else:
            print(f"  当日告警: 无 (可能仍在滚动预热窗口)")
        
        # Step 8: 模拟成交价 (9:01 外推 + 10bp 滑点)
        # 影子盘: 用 close + 预算滑点作为模拟成交价
        exec_prices = {
            "A": close_price * (1 + SLIP_BP_BUDGET / 10000 * np.sign(cr["A"]["pos"][-1]) if abs(cr["A"]["pos"][-1]) > 1e-9 else 1),
            "B": close_price * (1 + SLIP_BP_BUDGET / 10000 * np.sign(cr["B"]["pos"][-1]) if abs(cr["B"]["pos"][-1]) > 1e-9 else 1),
        }
        # 修正: 滑点方向应跟随交易方向
        # 买入: 成交价 = close * (1 + slip), 卖出: 成交价 = close * (1 - slip)
        action_a = daily.iloc[-1].get("action_A", "HOLD_FLAT")
        action_b = daily.iloc[-1].get("action_B", "HOLD_FLAT")
        
        def apply_slip(action, close, slip_bp):
            if action in ("OPEN_LONG", "INCREASE", "REVERSE"):
                return close * (1 + slip_bp / 10000)
            elif action in ("OPEN_SHORT",):
                return close * (1 - slip_bp / 10000)
            elif action == "CLOSE":
                # 平多: 卖出; 平空: 买入
                return close * (1 - slip_bp / 10000)  # 简化
            else:
                return close  # HOLD / HOLD_FLAT
        
        exec_prices = {
            "A": apply_slip(action_a, close_price, SLIP_BP_BUDGET),
            "B": apply_slip(action_b, close_price, SLIP_BP_BUDGET),
        }
        
        # Step 9: 更新影子盘日志
        print(f"\n[6] 更新影子盘日志...")
        prev_state = load_prev_state()
        # 如果是首次运行，初始化
        if not prev_state:
            prev_state = {
                "prev_close": close_price,
                "nav_A": INIT_CAPITAL, "nav_B": INIT_CAPITAL,
                "cum_pnl_A": 0, "cum_pnl_B": 0,
                "holding_dir_A": 0, "holding_dir_B": 0,
                "holding_days_A": 0, "holding_days_B": 0,
            }
        
        run_meta = {
            "data_source": f"akshare_sina_{symbol}",
            "contract_symbol": symbol,
        }
        
        new_state = update_shadow_log(
            daily, ctx, cr, alerts, exec_prices, prev_state, run_meta
        )
        
        # Step 10: 记录运行日志
        log_run("SUCCESS", f"影子盘运行成功 {today_date.date()}", 
                symbol=symbol, close=close_price,
                action_A=action_a, action_B=action_b,
                nav_A=new_state["nav_A"], nav_B=new_state["nav_B"])
        
        print(f"\n[7] 完成: nav_A={new_state['nav_A']:.0f} nav_B={new_state['nav_B']:.0f}")
        print(f"  累计收益 A: {new_state['cum_pnl_A']:.0f} ({new_state['cum_pnl_A']/INIT_CAPITAL*100:.2f}%)")
        print(f"  累计收益 B: {new_state['cum_pnl_B']:.0f} ({new_state['cum_pnl_B']/INIT_CAPITAL*100:.2f}%)")
        
        return True, "成功", new_state
        
    except Exception as e:
        import traceback
        err_msg = f"{e}\n{traceback.format_exc()}"
        log_run("FAIL", err_msg)
        print(f"\n[ERROR] {err_msg}")
        return False, err_msg, {}


if __name__ == "__main__":
    success, msg, state = run_shadow_trading()
    if success:
        print("\n影子盘运行完成。")
    else:
        print(f"\n影子盘运行失败: {msg}")
