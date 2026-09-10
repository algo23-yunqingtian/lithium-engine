"""
exp429 模块2: 实盘监控指标 + 漂移告警体系

三项核心监控 (日度频率, 实盘每日刷新):
  1. 因子 IC 时序: pos[t] 方向 与 次日收益 sign 的滚动相关 (衡量分歧因子有效性)。
     IC 持续走低 = 因子漂移, 核心告警信号。
  2. D 因子分布: |deviation| 滚动分布 (中位数/均值/P90), 衡量市场博弈失衡程度。
     分布突变 = 市场结构变化 (趋势盲区 / 无机会)。
  3. Logit 动态权重时序: alpha_t / beta_t 滚动估参轨迹 + CV, 衡量博弈权重漂移。
     alpha_t/beta_t 剧烈跳变 = 产业/投机主导权切换。

漂移告警条件 (三级):
  - OK: 指标在基准分位内。
  - WARNING (一级): 指标偏离进入警戒区, 提示观察/降仓准备。
  - CRITICAL (二级): 指标突破临界, 提示因子漂移风险, 建议降仓或暂停交易。

阈值基于 exp418 方法论: 全样本滚动窗口的分位统计 + 经济含义校准。
"""
import os
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))
from ham_pipeline import run_pipeline, EXP_DIR, DATA_DIR
from exp424_engine import START, END
from exp425_engine import ADX_THRESHOLD

TDPY = 244  # 交易日/年 (与 HAM 一致)


# ============================================================
# 1. 因子 IC 时序 (滚动 60 日)
# ============================================================
def compute_ic_series(ctx, pos, window=60):
    """
    滚动窗口 IC: pos[t] 与 次日收益 (close[t+1]/close[t]-1) 的 Pearson 相关。
    只在持仓日计算, 衡量"持仓方向 vs 实际收益"一致性。
    """
    idx = ctx["idx"]
    close = ctx["price"].reindex(idx).values.astype(float)
    n = len(idx)
    ret = np.zeros(n)
    ret[1:] = (close[1:] - close[:-1]) / close[:-1]
    sig = pos.copy()
    lab = np.zeros(n)
    lab[:-1] = ret[1:]            # 次日收益前移到 t
    sig_shift = np.zeros(n)
    sig_shift[:-1] = sig[1:]      # pos[t] 预测 lab[t]=ret[t+1]

    ic = pd.Series(np.nan, index=idx)
    for t in range(window, n):
        s = sig_shift[t - window:t]
        l = lab[t - window:t]
        m = np.abs(s) > 1e-9
        if m.sum() >= 5 and np.std(l[m]) > 0:
            ic.iloc[t] = np.corrcoef(s[m], l[m])[0, 1]
    return ic


# ============================================================
# 2. D 因子分布时序 (滚动 60 日)
# ============================================================
def compute_deviation_dist(daily, window=60):
    """滚动窗口 |deviation| 分布统计。"""
    dev = daily["deviation"].values.astype(float)
    idx = pd.to_datetime(daily["date"])
    n = len(dev)
    out = pd.DataFrame({"date": idx})
    out["dev_median"] = pd.Series(dev).rolling(window).median()
    out["dev_mean"] = pd.Series(dev).rolling(window).mean()
    out["dev_p90"] = pd.Series(dev).rolling(window).quantile(0.9)
    return out


# ============================================================
# 3. Logit 动态权重时序 (alpha_t / beta_t + CV)
# ============================================================
def compute_weight_series(daily, window=60):
    """alpha_t/beta_t 滚动均值 + CV 时序。"""
    a = daily["alpha_t"].values.astype(float)
    b = daily["beta_t"].values.astype(float)
    idx = pd.to_datetime(daily["date"])
    sa = pd.Series(a)
    sb = pd.Series(b)
    out = pd.DataFrame({"date": idx})
    out["alpha_t"] = a
    out["beta_t"] = b
    out["alpha_mean_roll"] = sa.rolling(window).mean()
    out["beta_mean_roll"] = sb.rolling(window).mean()
    out["alpha_cv_roll"] = sa.rolling(window).std() / sa.rolling(window).mean()
    out["beta_cv_roll"] = sb.rolling(window).std() / sb.rolling(window).mean()
    return out


# ============================================================
# 漂移告警判定
# ============================================================
def judge_drift(ic_series, dev_dist, wt_series):
    """
    生成滚动窗口的漂移告警序列。
    三级告警: OK / WARNING / CRITICAL。
    任一维度触发 WARNING -> 整体 WARNING; 任一 CRITICAL -> 整体 CRITICAL。
    """
    ic_df = pd.DataFrame({"date": ic_series.index, "IC_roll": ic_series.values})
    merged = ic_df.merge(dev_dist, on="date", how="left").merge(
        wt_series[["date", "alpha_cv_roll", "beta_cv_roll"]], on="date", how="left")

    # 阈值 (基于全样本分位校准, 告警应覆盖少数极端日而非常态化):
    #   IC:      全样本 P10=0.000 / median=0.223 / min=-0.243 -> 持续失效才 CRITICAL
    #   DEV:     全样本 median=0.561 / P90=0.956 / max=1.117 -> 取 P90/P99 作警戒/临界
    #   CV:      alpha_cv P90=0.334 / max=0.893 -> 取高分位作权重切换告警
    IC_WARN, IC_CRIT = 0.05, 0.0     # IC<0.05警戒, <0漂移(因子失效)
    DEV_MED_WARN, DEV_MED_CRIT = 0.95, 1.05   # deviation中位数 (P90/P99) 过高=趋势盲区
    DEV_MED_LO = 0.25                          # 过低=无机会
    CV_WARN, CV_CRIT = 0.50, 0.80              # alpha/beta 滚动CV (P90/高分位) 权重剧烈切换

    levels = []
    for _, r in merged.iterrows():
        flags = []
        # IC 漂移
        if pd.notna(r["IC_roll"]):
            if r["IC_roll"] < IC_CRIT:
                flags.append("IC_CRIT")
            elif r["IC_roll"] < IC_WARN:
                flags.append("IC_WARN")
        # D 分布
        if pd.notna(r["dev_median"]):
            if r["dev_median"] > DEV_MED_CRIT or r["dev_median"] < DEV_MED_LO * 0.9:
                flags.append("DEV_CRIT")
            elif r["dev_median"] > DEV_MED_WARN or r["dev_median"] < DEV_MED_LO:
                flags.append("DEV_WARN")
        # 权重CV
        acv = r.get("alpha_cv_roll")
        bcv = r.get("beta_cv_roll")
        acv = 0.0 if pd.isna(acv) else float(acv)
        bcv = 0.0 if pd.isna(bcv) else float(bcv)
        cv = max(acv, bcv)
        if cv > CV_CRIT:
            flags.append("CV_CRIT")
        elif cv > CV_WARN:
            flags.append("CV_WARN")

        has_crit = any(f.endswith("CRIT") for f in flags)
        has_warn = any(f.endswith("WARN") for f in flags)
        level = "CRITICAL" if has_crit else ("WARNING" if has_warn else "OK")
        levels.append({"date": r["date"], "alert_level": level,
                       "flags": ",".join(flags) if flags else "-",
                       "IC_roll": r["IC_roll"], "dev_median": r["dev_median"],
                       "alpha_cv": r.get("alpha_cv_roll"), "beta_cv": r.get("beta_cv_roll")})
    return pd.DataFrame(levels), merged


ALERT_THRESHOLDS = {
    "IC_roll": {"warn": 0.05, "crit": 0.0, "direction": "low",
                "note": "因子有效性; 持续<0=分歧信号失效, 必须暂停"},
    "dev_median": {"warn_high": 0.95, "crit_high": 1.05, "warn_low": 0.25,
                   "note": "市场博弈失衡度; 过高=趋势盲区, 过低=无机会"},
    "alpha/beta_cv": {"warn": 0.50, "crit": 0.80, "direction": "high",
                      "note": "Logit动态权重波动; 过高=博弈主导权剧烈切换"},
}


if __name__ == "__main__":
    print("=" * 76)
    print("exp429 模块2: 实盘监控指标 + 漂移告警体系")
    print("=" * 76)
    daily, ctx, df_ham, cr = run_pipeline()
    pos_a = cr["A"]["pos"]

    print("\n[1/3] 因子 IC 时序 (滚动60日)")
    ic = compute_ic_series(ctx, pos_a)
    valid = ic.dropna()
    print(f"  有效日 {len(valid)}, 均值={valid.mean():.4f} 中位={valid.median():.4f} "
          f"P10={valid.quantile(0.1):.4f} 正占比={(valid > 0).mean()*100:.1f}%")

    print("\n[2/3] D因子分布时序 (滚动60日)")
    dd = compute_deviation_dist(daily)
    dm = dd["dev_median"].dropna()
    print(f"  中位数: 均值={dm.mean():.3f} 范围[{dm.min():.3f},{dm.max():.3f}]")

    print("\n[3/3] Logit动态权重时序 (滚动60日)")
    wt = compute_weight_series(daily)
    print(f"  alpha_t 滚动CV: 均值={wt['alpha_cv_roll'].mean():.3f} 峰值={wt['alpha_cv_roll'].max():.3f}")
    print(f"  beta_t  滚动CV: 均值={wt['beta_cv_roll'].mean():.3f} 峰值={wt['beta_cv_roll'].max():.3f}")

    print("\n[4/3] 漂移告警判定")
    alerts, merged = judge_drift(ic, dd, wt)
    valid_alerts = alerts[alerts["date"] >= pd.Timestamp("2024-08-01")]
    vc = valid_alerts["alert_level"].value_counts()
    print("  告警分布 (2024-08起, 排除预热):")
    for lv in ["OK", "WARNING", "CRITICAL"]:
        print(f"    {lv:<10}: {vc.get(lv, 0)} 日")
    trig = valid_alerts[valid_alerts["flags"] != "-"]
    # 统计各触发源出现次数
    flag_counts = {}
    for fl in trig["flags"]:
        for f in str(fl).split(","):
            f = f.strip()
            if f and f != "-":
                flag_counts[f] = flag_counts.get(f, 0) + 1
    print(f"  触发告警日: {len(trig)} 日")
    if flag_counts:
        print("  触发源频次: " + ", ".join(f"{k}={v}" for k, v in sorted(flag_counts.items(), key=lambda x: -x[1])))

    # 保存
    alerts.to_csv(os.path.join(DATA_DIR, "exp429_alert_series.csv"), index=False)
    merged.to_csv(os.path.join(DATA_DIR, "exp429_monitor_metrics.csv"), index=False)
    pd.DataFrame([{"metric": k, **v} for k, v in ALERT_THRESHOLDS.items()]).to_csv(
        os.path.join(DATA_DIR, "exp429_alert_thresholds.csv"), index=False)
    print("\n监控产物已保存 -> data/exp429_*.csv")
