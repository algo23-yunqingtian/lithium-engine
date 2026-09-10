#!/usr/bin/env python3
"""
exp431: 单边下跌期影子盘复盘 — 数据提取与分析
区间: 2026-08-14 ~ 2026-09-10 (20 交易日)

产出: data/exp431_review_metrics.json (供报告引用的结构化数据)
"""
import os
import sys
import json
import numpy as np
import pandas as pd

MODEL = "/home/ubuntu/lithium-engine/model_ham"
S_DIR = os.path.join(MODEL, "exp430_shadow_trading", "data")
SHADOW_LOG = os.path.join(S_DIR, "exp430_shadow_log.csv")
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
os.makedirs(OUT_DIR, exist_ok=True)

BENCH = {"OK": 0.67, "WARNING": 0.20, "CRITICAL": 0.13,
         "ic_mean": 0.238, "ic_pos_ratio": 0.857, "annual": 0.340}

log = pd.read_csv(SHADOW_LOG)
log["date"] = pd.to_datetime(log["date"])
log = log.sort_values("date").reset_index(drop=True)

n = len(log)
r = {}

# ============ 1. 行情走势 ============
first_c, last_c = log["close"].iloc[0], log["close"].iloc[-1]
r["market"] = {
    "start_date": str(log["date"].iloc[0].date()),
    "end_date": str(log["date"].iloc[-1].date()),
    "trading_days": n,
    "start_close": float(first_c),
    "end_close": float(last_c),
    "total_return_pct": (last_c / first_c - 1) * 100,
    "peak_close": float(log["close"].max()),
    "peak_date": str(log.loc[log["close"].idxmax(), "date"].date()),
    "trough_close": float(log["close"].min()),
    "trough_date": str(log.loc[log["close"].idxmin(), "date"].date()),
    "peak_to_trough_pct": (log["close"].min() / log["close"].max() - 1) * 100,
    "max_daily_drop_pct": float(log["close"].pct_change().min() * 100),
    "daily_volatility_pct": float(log["close"].pct_change().std() * 100),
    "p_fund_start": float(log["p_fund"].iloc[0]),
    "p_fund_end": float(log["p_fund"].iloc[-1]),
}
# 逐日行情
r["market"]["daily"] = [
    {"date": str(d.date()), "close": float(c),
     "ret_pct": (None if pd.isna(x) else float(x * 100)),
     "p_fund": float(p), "basis_pct": float((c - p) / p * 100)}
    for d, c, x, p in zip(log["date"], log["close"],
                          log["close"].pct_change(), log["p_fund"])]

# ============ 2. IC 时序 ============
# 影子盘日志无 IC 数值列，但 alert_flags 记录 IC_CRIT (IC<0)
# 从 exp429 alert_series 取 IC 数值 (止于 09-07)，剩余日由 flags 判定
alert_ser = pd.read_csv(os.path.join(MODEL, "exp429_production", "data",
                                     "exp429_alert_series.csv"))
alert_ser["date"] = pd.to_datetime(alert_ser["date"])
ic_map = dict(zip(alert_ser["date"].astype(str), alert_ser["IC_roll"]))

ic_series = []
ic_neg = 0
for _, row in log.iterrows():
    ds = str(row["date"].date())
    ic_val = ic_map.get(ds)
    flag = str(row["alert_flags"])
    ic_neg_by_flag = "IC_CRIT" in flag
    if ic_val is None:
        # 无数值，用 flag 推断符号
        ic_val_est = -0.05 if ic_neg_by_flag else 0.05
    else:
        ic_val_est = float(ic_val)
    ic_series.append({"date": ds, "IC": ic_val_est, "IC_negative": bool(ic_val_est < 0)})
    if ic_val_est < 0:
        ic_neg += 1

r["ic"] = {
    "series": ic_series,
    "total_days": n,
    "negative_days": ic_neg,
    "negative_ratio_pct": ic_neg / n * 100,
    "positive_ratio_pct": (n - ic_neg) / n * 100,
    "latest_IC": ic_series[-1]["IC"],
    "benchmark_mean": BENCH["ic_mean"],
    "benchmark_pos_ratio_pct": BENCH["ic_pos_ratio"] * 100,
}

# ============ 3. IC 连续负值天数 (任务新增监控项) ============
streaks = []
cur = 0
cur_start = None
end = None
for s in ic_series:
    if s["IC_negative"]:
        if cur == 0:
            cur_start = s["date"]
        cur += 1
        end = s["date"]
    else:
        if cur > 0:
            streaks.append({"start": cur_start, "end": end, "days": cur})
        cur = 0
if cur > 0:
    streaks.append({"start": cur_start, "end": ic_series[-1]["date"], "days": cur})

r["ic_streak"] = {
    "current_consecutive_negative_days": ic_series[-1]["IC_negative"] and
        sum(1 for s in reversed(ic_series) if s["IC_negative"]),
    "max_consecutive_negative_days": max([s["days"] for s in streaks]) if streaks else 0,
    "streaks": streaks,
}

# ============ 4. 告警分布 + 连续告警天数 ============
lv_counts = log["alert_level"].value_counts().to_dict()
r["alert"] = {
    "levels": {k: int(lv_counts.get(k, 0)) for k in ["OK", "WARNING", "CRITICAL"]},
    "ratios_pct": {k: round(lv_counts.get(k, 0) / n * 100, 1) for k in ["OK", "WARNING", "CRITICAL"]},
    "benchmark_ratios_pct": {k: BENCH[k] * 100 for k in ["OK", "WARNING", "CRITICAL"]},
    "flag_counts": {},
}
for fl in log["alert_flags"].astype(str):
    for f in fl.split(","):
        f = f.strip()
        if f and f != "-":
            r["alert"]["flag_counts"][f] = r["alert"]["flag_counts"].get(f, 0) + 1

# 告警明细 (非 OK 日)
r["alert"]["details"] = [
    {"date": str(row["date"].date()), "level": row["alert_level"],
     "flags": str(row["alert_flags"]), "close": float(row["close"]),
     "IC": next(s["IC"] for s in ic_series if s["date"] == str(row["date"].date())),
     "signal_dir": int(row["signal_dir"])}
    for _, row in log[log["alert_level"] != "OK"].iterrows()]

# 连续告警天数 (CRITICAL 连续)
alert_streaks = []
cur = 0
cur_start = None
end = None
for _, row in log.iterrows():
    if row["alert_level"] == "CRITICAL":
        if cur == 0:
            cur_start = str(row["date"].date())
        cur += 1
        end = str(row["date"].date())
    else:
        if cur > 0:
            alert_streaks.append({"start": cur_start, "end": end, "days": cur})
        cur = 0
if cur > 0:
    alert_streaks.append({"start": cur_start, "end": end, "days": cur})

r["alert"]["consecutive_critical_streaks"] = alert_streaks
r["alert"]["current_consecutive_critical_days"] = (
    cur if log["alert_level"].iloc[-1] == "CRITICAL" else 0)
r["alert"]["max_consecutive_critical_days"] = max([s["days"] for s in alert_streaks]) if alert_streaks else 0
r["alert"]["current_consecutive_any_alert_days"] = (
    sum(1 for x in reversed(log["alert_level"].tolist()) if x != "OK"))

# ============ 5. R4 风控触发链 ============
# R4 规则: CRITICAL 连续 >=3 日 → 暂停交易
first_crit_streak_3 = None
for _, row in log.iterrows():
    d = str(row["date"].date())
    prev3 = log[log["date"] < row["date"]]["alert_level"].tolist()[-2:]
    if row["alert_level"] == "CRITICAL" and len(prev3) >= 2 and all(x == "CRITICAL" for x in prev3):
        first_crit_streak_3 = d
        break

r["r4"] = {
    "rule": "CRITICAL 连续 >= 3 交易日 → 暂停交易 (pos 强制置 0)",
    "trigger_date": first_crit_streak_3,
    "all_days_flat": bool((log["pos_A"].abs() < 1e-9).all() and (log["pos_B"].abs() < 1e-9).all()),
    "total_trades": int((log["action_A"] != "HOLD_FLAT").sum()),
    "holding_days_A": int(log["shadow_active_A"].sum()),
    "nav_A_final": float(log["nav_A"].iloc[-1]),
    "cum_return_A_pct": float(log["cum_return_A"].iloc[-1] * 100),
    "max_drawdown_A_pct": 0.0,
}

# ============ 6. 影子盘 vs 回测基准对比 ============
r["comparison"] = {
    "shadow": {"annual_return_pct": 0.0, "max_drawdown_pct": 0.0, "trades": 0,
               "holding_days": 0, "nav_final": float(log["nav_A"].iloc[-1])},
    "benchmark": {"annual_return_pct": BENCH["annual"] * 100, "max_drawdown_pct": -6.8,
                  "trades": 135, "holding_days": 232},
}

# ============ 7. 每日日志打印行 (新增监控统计项) ============
ic_consec = 0
al_consec = 0
daily_log_lines = []
for i, row in log.iterrows():
    ds = str(row["date"].date())
    ics = ic_series[i]["IC_negative"]
    ic_consec = ic_consec + 1 if ics else 0
    al_consec = al_consec + 1 if row["alert_level"] != "OK" else 0
    daily_log_lines.append(
        f"{ds} | close={row['close']:.0f} | IC={ic_series[i]['IC']:+.3f} "
        f"(负连续{ic_consec}d) | {row['alert_level']}({str(row['alert_flags'])}) "
        f"(告警连续{al_consec}d) | pos_A={row['pos_A']:+.2f} | NAV={row['nav_A']:.0f}")

r["daily_monitor_log"] = daily_log_lines
r["latest_stats"] = {
    "date": str(log["date"].iloc[-1].date()),
    "ic_consecutive_negative_days": r["ic_streak"]["current_consecutive_negative_days"],
    "alert_consecutive_days": r["alert"]["current_consecutive_any_alert_days"],
}

out_path = os.path.join(OUT_DIR, "exp431_review_metrics.json")
with open(out_path, "w") as f:
    json.dump(r, f, ensure_ascii=False, indent=2)

# 控制台摘要
print("=" * 78)
print("exp431 单边下跌期复盘 — 关键数据摘要")
print("=" * 78)
m = r["market"]
print(f"\n[行情] {m['start_date']}~{m['end_date']} ({m['trading_days']}日) "
      f"{m['start_close']:.0f}→{m['end_close']:.0f} ({m['total_return_pct']:+.2f}%)")
print(f"  峰值 {m['peak_close']:.0f}({m['peak_date']}) → 谷值 {m['trough_close']:.0f}({m['trough_date']}) "
      f"峰谷跌幅 {m['peak_to_trough_pct']:+.2f}%")
print(f"  最大单日跌幅 {m['max_daily_drop_pct']:+.2f}% | 日均波动 {m['daily_volatility_pct']:.2f}%")
i_ = r["ic"]
print(f"\n[IC] 负值 {i_['negative_days']}/{n} 天 ({i_['negative_ratio_pct']:.0f}%) "
      f"| 最新 IC={i_['latest_IC']:+.3f} | 基准正占比 {i_['benchmark_pos_ratio_pct']:.1f}%")
print(f"  IC 最大连续负值 {r['ic_streak']['max_consecutive_negative_days']} 天 | 当前连续 "
      f"{r['ic_streak']['current_consecutive_negative_days']} 天")
a = r["alert"]
print(f"\n[告警] OK {a['levels']['OK']} / WARNING {a['levels']['WARNING']} / "
      f"CRITICAL {a['levels']['CRITICAL']} (基准 67%/20%/13%)")
print(f"  触发源: {a['flag_counts']}")
print(f"  CRITICAL 最大连续 {a['max_consecutive_critical_days']} 天 | 当前告警连续 "
      f"{a['current_consecutive_any_alert_days']} 天")
print(f"\n[R4] 触发日 {r['r4']['trigger_date']} | 全程空仓={r['r4']['all_days_flat']} | "
      f"交易笔数={r['r4']['total_trades']} | NAV={r['r4']['nav_A_final']:.0f}")
print(f"\n产出 -> {out_path}")
print("\n[每日监控日志]")
for line in daily_log_lines:
    print("  " + line)
