"""
exp418 模块2: 实盘监控指标体系

目标: 设计月度监控指标清单, 设定一级/二级告警阈值, 指标跌破阈值提示因子漂移风险。

六项监控指标 (月度频率):
  1. IC (Information Coefficient): disagreement 与未来 5 日收益的相关系数。
     核心因子有效性度量。IC 持续走低 = 因子漂移。
  2. 盈亏比 (Profit/Loss Ratio): 平均盈利交易 / 平均亏损交易。
     反映策略的赔率结构。
  3. 平均持仓周期 (Avg Hold Days): 持仓交易的天数均值。
     反映策略节奏是否变化。
  4. 盈利交易占比 (Win Rate): 盈利交易 / 总交易。
  5. 分歧指标均值 (Avg Deviation): 持仓期 deviation 均值。
     反映市场博弈失衡程度。
  6. 波动率 (Realized Vol): 月度已实现年化波动率。
     反映市场状态。

两级告警阈值:
  - 一级告警 (Warning): 指标偏离基准进入警戒区, 提示观察。
  - 二级告警 (Critical): 指标突破临界, 提示因子漂移风险, 建议降仓/暂停。

阈值设定方法: 基于 2024-02~2026-09 全样本各月度值的统计分布 (中位数±分位),
再结合策略经济含义人工校准。

输出: 月度监控指标 CSV + 告警阈值表 CSV + 告警判定。
"""
import os
import sys
import numpy as np
import pandas as pd

MODEL = "/home/ubuntu/lithium-engine/model_ham"
for d in ["exp409_ham_dynamic", "exp411_combined"]:
    sys.path.insert(0, os.path.join(MODEL, d))

import exp409_ham_dynamic as E409
from exp411_combined import build_targets, price_series, TRADING_DAYS_PER_YEAR

EXP_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(EXP_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)

START, END = pd.Timestamp("2024-02-01"), pd.Timestamp("2026-09-07")
TDPY = E409.TRADING_DAYS_PER_YEAR
COST = E409.COST_PER_SIDE


# ============================================================
# 加载 HAM 信号链 (disagreement / deviation) + 交易数据 + 净值
# ============================================================
def load_ham_signals():
    """加载 HAM 原生因子表, 计算 disagreement/deviation 信号链。"""
    fac_path = os.path.join(
        MODEL, "ham_experiment_archive/intermediate/exp401_ham_factors_pure.csv")
    df = pd.read_csv(fac_path)
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()
    # 计算系统偏离度 (disagreement/deviation)
    df = E409.compute_system_deviation(df)
    df = E409.compute_aux_signals(df)
    df = E409.compute_ham_signals(df)
    mask = (df.index >= START) & (df.index <= END)
    return df[mask].copy()


def load_equity_and_trades():
    """加载 T0 每日净值 + 交易日志。"""
    eq = pd.read_csv(os.path.join(
        MODEL, "exp416_tail_risk_protect/data/exp416_daily_equity.csv"))
    eq["date"] = pd.to_datetime(eq["date"])
    eq = eq.set_index("date").sort_index()
    eq["T0_ret"] = eq["T0_equity"].pct_change()
    eq["market_ret"] = eq["close"].pct_change()
    trades = pd.read_csv(os.path.join(
        MODEL, "exp416_tail_risk_protect/data/exp416_T0_trades.csv"))
    trades["entry_date"] = pd.to_datetime(trades["entry_date"])
    trades["exit_date"] = pd.to_datetime(trades["exit_date"])
    return eq, trades


# ============================================================
# 计算月度监控指标
# ============================================================
def compute_monthly_metrics(sigs, eq, trades):
    """按月聚合六项监控指标。"""
    # 1. IC: disagreement 与未来 5 日收益的相关性 (按月计算)
    fwd5 = eq["close"].pct_change().shift(-5).cumsum().shift(0)
    # 用未来5日累计收益
    close = eq["close"]
    fwd5_ret = close.shift(-5) / close - 1

    monthly = []
    months = pd.period_range(sigs.index[0], sigs.index[-1], freq="M")
    for m in months:
        m_start = m.start_time
        m_end = m.end_time
        sig_m = sigs[(sigs.index >= m_start) & (sigs.index <= m_end)]
        eq_m = eq[(eq.index >= m_start) & (eq.index <= m_end)]
        if len(sig_m) < 5:
            continue

        # 1. IC: 因子有效性 = 持仓方向与实际未来收益的一致性。
        #    只在持仓日 (T0_pos != 0) 计算, 衡量策略实际持仓方向与未来收益的相关。
        #    IC>0 说明持仓方向正确 (因子有效); IC<0 说明持仓方向与市场相反 (漂移)。
        #    用持仓日 IC 而非全日 IC, 因为 HAM 只在极端分歧开仓, 全日 signal_dir 含噪声。
        pos_col = eq_m["T0_pos"] if "T0_pos" in eq_m.columns else None
        if pos_col is not None:
            held_days = pos_col[pos_col.abs() > 1e-9].index
            fwd_ok = fwd5_ret.dropna().index
            valid = held_days.intersection(fwd_ok)
            if len(valid) >= 3:
                pos_dir = np.sign(pos_col.loc[valid].values)
                f = fwd5_ret.loc[valid].values
                ic = np.corrcoef(pos_dir, f)[0, 1] if (np.std(f) > 0
                                                        and np.std(pos_dir) > 0) else np.nan
            else:
                ic = np.nan
        else:
            ic = np.nan

        # 2-4. 交易指标 (月内平仓交易)
        trades_m = trades[trades["exit_date"] >= m_start]
        trades_m = trades_m[trades_m["exit_date"] <= m_end]
        n_trades = len(trades_m)
        if n_trades >= 2:
            wins = trades_m[trades_m["net_ret"] > 0]
            losses = trades_m[trades_m["net_ret"] < 0]
            avg_win = wins["net_ret"].mean() if len(wins) > 0 else 0.0
            avg_loss = abs(losses["net_ret"].mean()) if len(losses) > 0 else np.nan
            # PL_Ratio 仅在有亏损样本时有意义; 全赢月记 NaN (避免爆炸)
            pl_ratio = avg_win / avg_loss if (avg_loss and avg_loss > 1e-9) else np.nan
            win_rate = len(wins) / n_trades
            avg_hold = trades_m["hold_days"].mean()
        else:
            pl_ratio = np.nan
            win_rate = np.nan
            avg_hold = np.nan

        # 5. 分歧指标均值 (持仓期 deviation 均值)
        avg_dev = sig_m["deviation"].mean()

        # 6. 波动率 (月度已实现年化)
        vol_m = eq_m["market_ret"].std() * np.sqrt(TDPY)

        monthly.append({
            "month": str(m),
            "IC_5d": ic,
            "PL_Ratio": pl_ratio,
            "Avg_Hold_Days": avg_hold,
            "Win_Rate": win_rate,
            "n_trades": n_trades,
            "Avg_Deviation": avg_dev,
            "Realized_Vol": vol_m,
            "month_ret": eq_m["T0_equity"].iloc[-1] / eq_m["T0_equity"].iloc[0] - 1
                         if len(eq_m) > 0 else np.nan,
        })
    return pd.DataFrame(monthly)


# ============================================================
# 告警阈值设定 (基于全样本统计 + 经济含义校准)
# ============================================================
def set_alert_thresholds(monthly):
    """
    为每项指标设定一级(Warning)/二级(Critical)告警阈值。
    方向: 部分指标"越低越危险"(IC/盈亏比/胜率), 部分"越高越危险"(波动率)。

    阈值基于全样本分布:
      - 一级 = 偏离基准进入警戒区 (约 P10 或 P90)
      - 二级 = 突破临界 (约 P5 或 P95), 因子漂移高风险
    """
    thr = pd.DataFrame()
    rows = []

    def add(metric, warn_val, crit_val, direction, baseline, note):
        rows.append({
            "metric": metric,
            "baseline_median": baseline,
            "warn_threshold": warn_val,
            "crit_threshold": crit_val,
            "direction": direction,  # "low" = 低于阈值告警, "high" = 高于告警
            "note": note,
        })

    ic = monthly["IC_5d"].dropna()
    if len(ic) > 0:
        add("IC_5d", round(ic.quantile(0.25), 4), round(ic.quantile(0.10), 4),
            "low", round(ic.median(), 4),
            "disagreement与未来5日收益相关性; 越低因子失效越严重")

    pl = monthly["PL_Ratio"].dropna()
    if len(pl) > 0:
        add("PL_Ratio", round(pl.quantile(0.25), 2), round(pl.quantile(0.10), 2),
            "low", round(pl.median(), 2),
            "平均盈利/平均亏损; 跌破1=赔率转负, 策略结构性恶化")

    hold = monthly["Avg_Hold_Days"].dropna()
    if len(hold) > 0:
        # 持仓周期偏离基准过远(过长或过短)都可能是漂移
        add("Avg_Hold_Days", round(hold.quantile(0.25), 1), round(hold.quantile(0.10), 1),
            "low", round(hold.median(), 1),
            "平均持仓天数; 过低=追涨杀跌过频, 过高=信号迟钝")

    wr = monthly["Win_Rate"].dropna()
    if len(wr) > 0:
        add("Win_Rate", round(wr.quantile(0.25), 3), round(wr.quantile(0.10), 3),
            "low", round(wr.median(), 3),
            "盈利交易占比; 跌破基准=策略盈利能力衰减")

    dev = monthly["Avg_Deviation"].dropna()
    if len(dev) > 0:
        # 分歧均值过高=市场持续极端失衡(可能是趋势盲区); 过低=无机会
        add("Avg_Deviation", round(dev.quantile(0.75), 4), round(dev.quantile(0.90), 4),
            "high", round(dev.median(), 4),
            "持仓期分歧均值; 过高=市场极端失衡(HAM趋势盲区风险)")

    vol = monthly["Realized_Vol"].dropna()
    if len(vol) > 0:
        add("Realized_Vol", round(vol.quantile(0.75), 4), round(vol.quantile(0.90), 4),
            "high", round(vol.median(), 4),
            "月度年化波动率; 过高=市场极端波动, 持仓风险放大")

    return pd.DataFrame(rows)


def judge_alerts(monthly, thresholds):
    """对每月每指标做告警判定 (正常/一级/二级)。"""
    thr_dict = thresholds.set_index("metric")
    rows = []
    for _, r in monthly.iterrows():
        for metric, t in thr_dict.iterrows():
            val = r.get(metric, np.nan)
            if pd.isna(val):
                level = "N/A"
            elif t["direction"] == "low":
                if val <= t["crit_threshold"]:
                    level = "CRITICAL"
                elif val <= t["warn_threshold"]:
                    level = "WARNING"
                else:
                    level = "OK"
            else:  # high
                if val >= t["crit_threshold"]:
                    level = "CRITICAL"
                elif val >= t["warn_threshold"]:
                    level = "WARNING"
                else:
                    level = "OK"
            rows.append({"month": r["month"], "metric": metric,
                         "value": round(val, 4) if pd.notna(val) else None,
                         "level": level})
    return pd.DataFrame(rows)


if __name__ == "__main__":
    print("=" * 70)
    print("exp418 模块2: 实盘监控指标体系")
    print("=" * 70)

    sigs = load_ham_signals()
    print(f"HAM 信号: {sigs.index[0].date()} ~ {sigs.index[-1].date()} "
          f"({len(sigs)} 日)")
    eq, trades = load_equity_and_trades()
    print(f"净值 {len(eq)} 日, 交易 {len(trades)} 笔")

    # 月度指标
    monthly = compute_monthly_metrics(sigs, eq, trades)
    print(f"\n月度指标: {len(monthly)} 个月")
    print(monthly.dropna(how="all").to_string(index=False))

    # 阈值
    thresholds = set_alert_thresholds(monthly)
    print(f"\n--- 告警阈值表 ---")
    print(thresholds.to_string(index=False))

    # 告警判定
    alerts = judge_alerts(monthly, thresholds)
    print(f"\n--- 告警判定统计 ---")
    print(alerts["level"].value_counts().to_string())

    # 保存
    monthly.to_csv(os.path.join(DATA_DIR, "exp418_M2_monthly_metrics.csv"),
                   index=False)
    thresholds.to_csv(os.path.join(DATA_DIR, "exp418_M2_alert_thresholds.csv"),
                      index=False)
    alerts.to_csv(os.path.join(DATA_DIR, "exp418_M2_alert_judgement.csv"),
                  index=False)
    print("\n模块2 数据已保存")
