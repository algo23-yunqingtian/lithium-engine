#!/usr/bin/env python3
"""
exp430: 影子盘回填验证 —— 用历史日K逐日模拟20交易日影子盘

用途:
  用 akshare 已发生的真实历史日K数据，逐日模拟影子盘运行过程，
  验证影子盘框架的完整性和正确性。不涉及任何真实下单。

机制:
  1. 确保最近20交易日OHLC数据完整
  2. 对每个交易日: 逐日追加 → 更新END → 重跑流水线 → 记录日志
  3. 每日产生 D因子/信号/仓位/模拟盈亏/告警
  4. 最终生成对比报告

运行:
  /usr/bin/python3 model_ham/exp430_shadow_trading/backfill_shadow.py
"""
import os
import sys
import json
import numpy as np
import pandas as pd
from datetime import datetime, timedelta

EXP_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(EXP_DIR, "data")
MODEL = "/home/ubuntu/lithium-engine/model_ham"

sys.path.insert(0, EXP_DIR)
sys.path.insert(0, os.path.join(MODEL, "exp429_production"))
sys.path.insert(0, MODEL)
for d in ["exp409_ham_dynamic", "exp410_fund_dynamic", "exp411_combined",
          "exp413_robustness", "exp414_attribution"]:
    sys.path.insert(0, os.path.join(MODEL, d))
sys.path.insert(0, os.path.join(MODEL, "exp416_tail_risk_protect"))
sys.path.insert(0, os.path.join(MODEL, "exp424_t1_robustness"))
sys.path.insert(0, os.path.join(MODEL, "exp425_minute_execution"))
sys.path.insert(0, os.path.join(MODEL, "exp428_shadow_trading"))

OHLC_CSV = os.path.join(MODEL, "raw_data", "lithium_future.csv")
HAM_PURE_CSV = os.path.join(MODEL, "ham_experiment_archive", "intermediate", "exp401_ham_factors_pure.csv")
SPOT_DB = "/home/ubuntu/lc_futures_data/data/lc_spot.db"
SHADOW_LOG = os.path.join(DATA_DIR, "exp430_shadow_log.csv")
RUN_LOG = os.path.join(DATA_DIR, "exp430_run_log.json")

INIT_CAPITAL = 1_000_000.0
SLIP_BP_BUDGET = 10.0


def ensure_ohlc_complete():
    """确保 OHLC 数据完整 (含最近交易日)."""
    import akshare as ak
    
    ohlc = pd.read_csv(OHLC_CSV)
    ohlc["date"] = pd.to_datetime(ohlc["date"])
    
    # Get latest from akshare
    symbol = "LC2701"  # 当前主力
    df_ak = ak.futures_zh_daily_sina(symbol=symbol)
    df_ak["date"] = pd.to_datetime(df_ak["date"])
    
    # Find missing dates
    missing = df_ak[~df_ak["date"].isin(ohlc["date"])]
    if len(missing) == 0:
        print(f"  [OHLC] 数据已完整，无缺失")
        return ohlc, symbol
    
    print(f"  [OHLC] 补全 {len(missing)} 个缺失交易日")
    for _, row in missing.iterrows():
        new_row = {
            "date": row["date"],
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "volume": int(row["volume"]),
            "position": int(row["hold"]),
            "settle": float(row["settle"]),
        }
        ohlc = pd.concat([ohlc, pd.DataFrame([new_row])], ignore_index=True)
        print(f"    + {row['date'].date()} close={row['close']}")
    
    ohlc = ohlc.sort_values("date").reset_index(drop=True)
    ohlc.to_csv(OHLC_CSV, index=False)
    return ohlc, symbol


def get_spot_for_date(date_str):
    """获取某日现货价 (从 lc_spot.db)."""
    import sqlite3
    if not os.path.exists(SPOT_DB):
        return None
    conn = sqlite3.connect(SPOT_DB)
    try:
        row = conn.execute(
            "SELECT spot_price FROM spot_price WHERE date <= ? ORDER BY date DESC LIMIT 1",
            (date_str,)
        ).fetchone()
        conn.close()
        if row:
            return float(row[0])
    except Exception:
        conn.close()
    return None


def estimate_p_fund(close_price, spot_price=None):
    """估算 P_fund."""
    if spot_price and spot_price > 0:
        return float(spot_price)
    return float(close_price)


def update_end_date(target_date):
    """动态更新 exp424_engine 的 END."""
    df = pd.read_csv(HAM_PURE_CSV)
    df["date"] = pd.to_datetime(df["date"])
    
    # 确保 target_date 在 HAM factors 中存在
    if target_date not in df["date"].values:
        return None
    
    import exp424_engine as E424
    E424.END = target_date
    
    try:
        import exp425_engine as E425
        if hasattr(E425, "END"):
            E425.END = target_date
    except Exception:
        pass
    
    return target_date


def run_one_day(target_date, close_price, p_fund, prev_state, symbol, spot_price):
    """模拟单个交易日的影子盘运行."""
    
    # 1. 追加 HAM 因子 (如果不存在)
    df_ham = pd.read_csv(HAM_PURE_CSV)
    df_ham["date"] = pd.to_datetime(df_ham["date"])
    
    d_f = p_fund - close_price
    
    if target_date in df_ham["date"].values:
        idx = df_ham.index[df_ham["date"] == target_date][0]
        df_ham.loc[idx, "close"] = close_price
        df_ham.loc[idx, "P_fund"] = p_fund
        df_ham.loc[idx, "D_f"] = d_f
    else:
        new_row = {
            "date": target_date,
            "close": close_price,
            "P_fund": p_fund,
            "D_f": d_f,
            "D_c": 0.0,
            "profit_f": 0.0, "profit_c": 0.0,
            "n_f": 0.0, "n_c": 1.0,
            "Total_Demand": 0.0,
            "n_f_minus_n_c": 0.0,
            "valid": 1,
        }
        df_ham = pd.concat([df_ham, pd.DataFrame([new_row])], ignore_index=True)
        df_ham = df_ham.sort_values("date").reset_index(drop=True)
    df_ham.to_csv(HAM_PURE_CSV, index=False)
    
    # 2. 更新 END 日期
    update_end_date(target_date)
    
    # 3. 重跑流水线
    import ham_pipeline as HP
    daily, ctx, df_ham_out, cr = HP.run_pipeline()
    
    # 4. 绩效验证
    import run_daily as RD
    results, slip_cal = RD.run_validation(ctx, df_ham_out)
    
    # 5. 监控告警
    import monitoring as MON
    ic = MON.compute_ic_series(ctx, cr["A"]["pos"])
    dd = MON.compute_deviation_dist(daily)
    wt = MON.compute_weight_series(daily)
    alerts, merged = MON.judge_drift(ic, dd, wt)
    
    # 6. 提取当日数据
    last_row = daily.iloc[-1]
    pos_a = float(last_row["pos_A"])
    pos_b = float(last_row["pos_B"])
    action_a = last_row.get("action_A", "HOLD_FLAT")
    action_b = last_row.get("action_B", "HOLD_FLAT")
    
    # 7. 模拟成交价 (close + 滑点)
    def apply_slip(action, close, slip_bp):
        if action in ("OPEN_LONG", "INCREASE", "REVERSE"):
            return close * (1 + slip_bp / 10000)
        elif action in ("OPEN_SHORT",):
            return close * (1 - slip_bp / 10000)
        elif action == "CLOSE":
            return close * (1 - slip_bp / 10000)
        else:
            return close
    
    exec_price_a = apply_slip(action_a, close_price, SLIP_BP_BUDGET)
    exec_price_b = apply_slip(action_b, close_price, SLIP_BP_BUDGET)
    
    slip_a = (exec_price_a - close_price) / close_price * 10000 if close_price > 0 else 0
    slip_b = (exec_price_b - close_price) / close_price * 10000 if close_price > 0 else 0
    
    # 8. 模拟盈亏
    prev_dir_a = prev_state.get("holding_dir_A", 0)
    prev_dir_b = prev_state.get("holding_dir_B", 0)
    prev_close = prev_state.get("prev_close", close_price)
    
    daily_ret = (close_price - prev_close) / prev_close if prev_close > 0 else 0.0
    
    nav_a = prev_state.get("nav_A", INIT_CAPITAL)
    nav_b = prev_state.get("nav_B", INIT_CAPITAL)
    
    pnl_a = prev_dir_a * abs(pos_a) * daily_ret * nav_a if prev_dir_a != 0 else 0
    pnl_b = prev_dir_b * abs(pos_b) * daily_ret * nav_b if prev_dir_b != 0 else 0
    
    cum_pnl_a = prev_state.get("cum_pnl_A", 0) + pnl_a
    cum_pnl_b = prev_state.get("cum_pnl_B", 0) + pnl_b
    
    new_nav_a = nav_a + pnl_a
    new_nav_b = nav_b + pnl_b
    
    # 9. 持仓状态
    new_dir_a = int(np.sign(pos_a)) if abs(pos_a) > 1e-9 else 0
    new_dir_b = int(np.sign(pos_b)) if abs(pos_b) > 1e-9 else 0
    
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
    
    # 10. 告警
    today_alert = alerts[alerts["date"] == target_date]
    alert_level = today_alert["alert_level"].iloc[0] if len(today_alert) > 0 else "OK"
    alert_flags = today_alert["flags"].iloc[0] if len(today_alert) > 0 else "-"
    
    # 11. 写影子盘日志
    new_row = {
        "date": target_date.strftime("%Y-%m-%d"),
        "close": close_price,
        "p_fund": p_fund,
        "deviation": float(last_row.get("deviation", 0)),
        "disagreement": float(last_row.get("disagreement", 0)),
        "D_c": float(last_row.get("D_c", 0)),
        "D_f": float(last_row.get("D_f", 0)),
        "alpha_t": float(last_row.get("alpha_t", 0)),
        "beta_t": float(last_row.get("beta_t", 0)),
        "open_signal": bool(last_row.get("open_signal", False)),
        "signal_dir": int(last_row.get("signal_dir", 0)),
        "pos_A": pos_a,
        "pos_B": pos_b,
        "action_A": action_a,
        "action_B": action_b,
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
        "cum_return_A": cum_pnl_a / INIT_CAPITAL,
        "cum_return_B": cum_pnl_b / INIT_CAPITAL,
        "nav_A": new_nav_a,
        "nav_B": new_nav_b,
        "holding_dir_A": new_dir_a,
        "holding_dir_B": new_dir_b,
        "holding_days_A": hold_days_a,
        "holding_days_B": hold_days_b,
        "alert_level": alert_level,
        "alert_flags": alert_flags,
        "data_source": f"akshare_sina_{symbol}",
        "contract_symbol": symbol,
    }
    
    # 幂等写入
    if os.path.exists(SHADOW_LOG):
        log = pd.read_csv(SHADOW_LOG)
        td = pd.Timestamp(target_date)
        # 用字符串比较避免日期格式不一致
        td_str = td.strftime("%Y-%m-%d")
        log_dates_str = log["date"].astype(str).str[:10]
        if td_str in log_dates_str.values:
            idx = log.index[log_dates_str == td_str][0]
            for col, val in new_row.items():
                log.loc[idx, col] = val
        else:
            log = pd.concat([log, pd.DataFrame([new_row])], ignore_index=True)
    else:
        log = pd.DataFrame([new_row])
    log.to_csv(SHADOW_LOG, index=False)
    
    return {
        "prev_close": close_price,
        "nav_A": new_nav_a, "nav_B": new_nav_b,
        "cum_pnl_A": cum_pnl_a, "cum_pnl_B": cum_pnl_b,
        "holding_dir_A": new_dir_a, "holding_dir_B": new_dir_b,
        "holding_days_A": hold_days_a, "holding_days_B": hold_days_b,
    }


def main():
    print("=" * 78)
    print("exp430: 影子盘回填验证 —— 逐日模拟20交易日")
    print("=" * 78)
    
    # Step 1: 确保 OHLC 数据完整
    print("\n[1] 确保 OHLC 数据完整...")
    ohlc, symbol = ensure_ohlc_complete()
    ohlc["date"] = pd.to_datetime(ohlc["date"])
    
    # Step 2: 确定回填的20交易日范围
    # 从最新日往回数20个交易日
    target_dates = ohlc["date"].tail(20).tolist()
    print(f"\n[2] 回填范围: {target_dates[0].date()} ~ {target_dates[-1].date()} ({len(target_dates)} 日)")
    
    # Step 3: 确保每个交易日有 HAM 因子数据
    print(f"\n[3] 确保每个交易日有 HAM 因子数据...")
    df_ham = pd.read_csv(HAM_PURE_CSV)
    df_ham["date"] = pd.to_datetime(df_ham["date"])
    
    for td in target_dates:
        if td not in df_ham["date"].values:
            # 需要追加
            ohlc_row = ohlc[ohlc["date"] == td].iloc[0]
            close = float(ohlc_row["close"])
            spot = get_spot_for_date(td.strftime("%Y%m%d"))
            p_fund = estimate_p_fund(close, spot)
            
            d_f = p_fund - close
            new_row = {
                "date": td,
                "close": close,
                "P_fund": p_fund,
                "D_f": d_f,
                "D_c": 0.0,
                "profit_f": 0.0, "profit_c": 0.0,
                "n_f": 0.0, "n_c": 1.0,
                "Total_Demand": 0.0,
                "n_f_minus_n_c": 0.0,
                "valid": 1,
            }
            df_ham = pd.concat([df_ham, pd.DataFrame([new_row])], ignore_index=True)
            df_ham = df_ham.sort_values("date").reset_index(drop=True)
            print(f"  + HAM factor {td.date()} close={close} P_fund={p_fund:.0f}")
        else:
            # 更新 close/P_fund 以确保一致
            idx = df_ham.index[df_ham["date"] == td][0]
            ohlc_row = ohlc[ohlc["date"] == td].iloc[0]
            close = float(ohlc_row["close"])
            spot = get_spot_for_date(td.strftime("%Y%m%d"))
            p_fund = estimate_p_fund(close, spot)
            
            df_ham.loc[idx, "close"] = close
            df_ham.loc[idx, "P_fund"] = p_fund
            df_ham.loc[idx, "D_f"] = p_fund - close
    
    df_ham.to_csv(HAM_PURE_CSV, index=False)
    print(f"  HAM 因子数据已就绪 ({len(df_ham)} 行)")
    
    # Step 4: 清空旧影子盘日志
    if os.path.exists(SHADOW_LOG):
        os.remove(SHADOW_LOG)
    if os.path.exists(RUN_LOG):
        with open(RUN_LOG, "w") as f:
            json.dump([], f)
    
    # Step 5: 逐日模拟
    print(f"\n[4] 逐日模拟影子盘...")
    prev_state = {
        "prev_close": float(ohlc[ohlc["date"] == target_dates[0] - pd.Timedelta(days=1)].iloc[0]["close"])
        if target_dates[0] - pd.Timedelta(days=1) in ohlc["date"].values
        else float(df_ham[df_ham["date"] < target_dates[0]].iloc[-1]["close"]),
        "nav_A": INIT_CAPITAL, "nav_B": INIT_CAPITAL,
        "cum_pnl_A": 0, "cum_pnl_B": 0,
        "holding_dir_A": 0, "holding_dir_B": 0,
        "holding_days_A": 0, "holding_days_B": 0,
    }
    
    for i, td in enumerate(target_dates):
        ohlc_row = ohlc[ohlc["date"] == td].iloc[0]
        close = float(ohlc_row["close"])
        spot = get_spot_for_date(td.strftime("%Y%m%d"))
        p_fund = estimate_p_fund(close, spot)
        
        print(f"\n  Day {i+1}/{len(target_dates)}: {td.date()} close={close}")
        
        try:
            prev_state = run_one_day(td, close, p_fund, prev_state, symbol, spot)
            print(f"    action_A={prev_state['holding_dir_A']} pos_A → nav={prev_state['nav_A']:.0f}")
            
            # 记录运行日志
            entry = {
                "timestamp": datetime.now().isoformat(),
                "status": "SUCCESS",
                "message": f"回填 {td.date()}",
                "symbol": symbol,
                "close": close,
            }
            history = []
            if os.path.exists(RUN_LOG):
                with open(RUN_LOG, "r") as f:
                    history = json.load(f)
            history.append(entry)
            with open(RUN_LOG, "w") as f:
                json.dump(history, f, ensure_ascii=False, indent=2)
            
        except Exception as e:
            import traceback
            print(f"    ERROR: {e}")
            traceback.print_exc()
    
    # Step 6: 生成报告
    print(f"\n\n[5] 生成对比报告...")
    from report_generator import generate_report
    report_path = generate_report()
    
    print(f"\n{'='*78}")
    print(f"回填验证完成: {len(target_dates)} 个交易日")
    print(f"报告: {report_path}")
    print(f"影子盘日志: {SHADOW_LOG}")
    print(f"{'='*78}")


if __name__ == "__main__":
    main()
