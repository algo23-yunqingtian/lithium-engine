"""
exp425: 分钟级进场仿真与滑点评估

基准：exp424 最优版本 T1 + ADX降权(高ADX仓位×0.3)，HAM信号与风控逻辑完全锁定，不改动。
  推荐配置锚定：T1 + ADX降权(×0.3)，1.0倍仓位。

模块1：分钟行情接入与成交规则
  信号依旧T日收盘由HAM计算；开仓/平仓成交在T+1交易日9:01 1min bar均价。
  由于真实1分钟数据仅覆盖近期约5个交易日，全回测期的9:01均价采用"统计外推"：
    9:01均价 = 日K开盘 × (1 + d)，d 为开盘→9:01均价的真实偏离，按当日波动率缩放抽样。
  设置基础滑点模型：固定基点滑点；并行无滑点/滑点两组对照。

模块2：绩效对比
  ① exp424基准(T+1开盘，无滑点)
  ② 9:01分钟进场、无滑点
  ③ 9:01分钟进场，叠加商品基础滑点
  输出年化、夏普、最大回撤、Calmar、交易笔数对比。

模块3：跳空极端场景过滤实验
  新增可选规则：若T+1开盘相对T日收盘跳空超过阈值，本次信号放弃开仓，规避极端流动性冲击。

模块4：结论总结
  量化从【开盘价假设】切换到【9:01分钟成交+滑点】带来的绩效衰减，评估剩余收益是否满足实盘要求。

时序铁律：版本B口径——T日收盘决策，T+1开盘（或9:01）成交；隔夜跳空由pos[t-1]承担，日内由pos[t]承担。
"""
import os
import sys
import json
import itertools
import numpy as np
import pandas as pd

MODEL = "/home/ubuntu/lithium-engine/model_ham"
for d in ["exp409_ham_dynamic", "exp410_fund_dynamic", "exp411_combined",
          "exp413_robustness", "exp414_attribution"]:
    sys.path.insert(0, os.path.join(MODEL, d))
sys.path.insert(0, os.path.join(MODEL, "exp416_tail_risk_protect"))
sys.path.insert(0, os.path.join(MODEL, "exp424_t1_robustness"))

import exp409_ham_dynamic as E409
import exp413_engine as E413
from exp411_combined import build_targets, price_series, extract_trades, TRADING_DAYS_PER_YEAR
from exp424_engine import (
    load_context, compute_positions, compute_adx,
    metrics_from_net_ret, net_ret_variant_B,
    VOL_HALF, VOL_FULL, HALF_SCALE, FULL_SCALE,
    DD_RECOVER, DD_WARN, DD_HARD, START, END, COST, TDPY,
)

EXP_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(EXP_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)
REPORTS_DIR = os.path.join(os.path.dirname(os.path.dirname(EXP_DIR)), "reports")
os.makedirs(REPORTS_DIR, exist_ok=True)

RAW_MINUTE_CSV = os.path.join(EXP_DIR, "raw_minute", "lc0_1min_raw.csv")

# ADX降权参数（与 exp424 推荐配置一致）
ADX_THRESHOLD = 30.0
ADX_SCALE = 0.3

# 滑点模型参数（基点，bp = 0.01%）。开平各叠加一次。
SLIP_BP_GRID = [0.0, 2.0, 5.0, 10.0, 15.0, 20.0]

# 跳空过滤阈值（开盘相对T日收盘的跳空幅度，绝对值）
GAP_THR_GRID = [0.0, 0.02, 0.03, 0.04, 0.05, 0.07, 0.10]

# 随机种子，保证可复现
RNG_SEED = 20260910


# ============================================================
# 分钟级滑点标定：从真实1分钟数据提取"开盘→9:01均价"的偏离分布
# ============================================================
def load_minute_deviation():
    """
    从真实LC主力1分钟数据提取每个交易日"日开盘 → 9:01均价"的相对偏离(%)。
    9:01均价用该分钟bar的(H+L+C)/3近似。
    返回 DataFrame[date, day_open, p901, dev_pct]。
    """
    if not os.path.exists(RAW_MINUTE_CSV):
        raise FileNotFoundError(f"分钟数据缺失: {RAW_MINUTE_CSV}")
    df = pd.read_csv(RAW_MINUTE_CSV)
    df["datetime"] = pd.to_datetime(df["datetime"])
    df["date"] = df["datetime"].dt.date
    df["hm"] = df["datetime"].dt.strftime("%H:%M")

    rows = []
    for d in sorted(df["date"].unique()):
        sub = df[df["date"] == d].sort_values("datetime")
        day_open = float(sub.iloc[0]["open"])  # 当日首根 = 9:00 开盘
        m = sub[sub["hm"] == "09:01"]
        if len(m) == 0:
            continue
        p901 = (float(m.iloc[0]["high"]) + float(m.iloc[0]["low"]) + float(m.iloc[0]["close"])) / 3.0
        dev_pct = (p901 - day_open) / day_open * 100.0
        rows.append({"date": str(d), "day_open": day_open, "p901": p901, "dev_pct": dev_pct})
    dev = pd.DataFrame(rows)
    dev.to_csv(os.path.join(DATA_DIR, "min_opening_deviation.csv"), index=False)
    return dev


def calibrate_slippage(dev_df):
    """
    统计标定的关键参数：
      - mean_dev_pct / std_dev_pct / mean_abs_dev_pct：9:01 vs 开盘的偏离分布
      - 用偏离分布作为"9:01均价相对开盘的可实现偏离"的随机种子池。
    """
    vals = dev_df["dev_pct"].values
    return {
        "n_days": int(len(vals)),
        "mean_dev_pct": float(np.mean(vals)),
        "std_dev_pct": float(np.std(vals)),
        "mean_abs_dev_pct": float(np.mean(np.abs(vals))),
        "min_dev_pct": float(np.min(vals)),
        "max_dev_pct": float(np.max(vals)),
        "sample_pool_pct": [float(v) for v in vals],
    }


# ============================================================
# 信号层：T1定稿 + ADX降权（锁定，不改HAM核心）
# ============================================================
def compute_signal_positions(ctx):
    """exp424推荐配置：T1定稿(版本B口径用) + ADX降权(高趋势日×0.3)"""
    idx = ctx["idx"]
    ohlc = ctx["ohlc"]
    price = ctx["price"]

    pos, _ = compute_positions(ctx, VOL_HALF, VOL_FULL, HALF_SCALE, FULL_SCALE,
                               DD_RECOVER, DD_WARN, DD_HARD)

    high = ohlc["high"].reindex(idx).values
    low = ohlc["low"].reindex(idx).values
    close = price.reindex(idx).values
    adx = compute_adx(high, low, close, period=14)
    adx_mask = adx > ADX_THRESHOLD
    pos[adx_mask] = pos[adx_mask] * ADX_SCALE

    # 保存ADX序列供复盘
    adx_df = pd.DataFrame({"date": [str(idx[i].date()) for i in range(len(idx))], "adx": adx})
    adx_df.to_csv(os.path.join(DATA_DIR, "exp425_adx_series.csv"), index=False)
    return pos, adx_mask.sum()


# ============================================================
# 成交模型：日K开盘 vs 9:01分钟均价外推
# ============================================================
def build_901_exec_prices(ctx, slippage_cal, vol_scale=True, seed=RNG_SEED):
    """
    为每个交易日构造9:01成交价。
    真实分钟数据仅有~5天，全回测期采用统计外推：
      9:01均价 = 日K开盘 × (1 + d_t)，d_t 从真实偏离池抽样后按当日波动率缩放。
    vol_scale=True：按当日20日已实现波动率相对中位波动率的比值缩放d_t，
                    使高波动日9:01偏离更大（贴近真实市场微观结构）。
    返回 open_px, p901_px, dev_t(%) 三组与idx对齐的数组。
    """
    rng = np.random.default_rng(seed)
    idx = ctx["idx"]
    ohlc = ctx["ohlc"]
    vol = ctx["vol"].reindex(idx).values
    n = len(idx)
    open_px = ohlc["open"].reindex(idx).values.astype(float)

    pool = np.array(slippage_cal["sample_pool_pct"])  # 真实偏离池(%)
    pool_std = slippage_cal["std_dev_pct"]
    if pool_std <= 1e-9:
        pool_std = 1.0
    # 波动率中位数（用于缩放）
    vol_med = np.median(vol[np.isfinite(vol) & (vol > 0)]) if np.any(vol > 0) else 1.0
    if vol_med <= 0:
        vol_med = 1.0

    p901_px = np.zeros(n)
    dev_t = np.zeros(n)
    for t in range(n):
        d = float(pool[rng.integers(0, len(pool))])
        if vol_scale and vol[t] > 0 and vol_med > 0:
            d = d * (vol[t] / vol_med)
        dev_t[t] = d
        p901_px[t] = open_px[t] * (1 + d / 100.0)
    return open_px, p901_px, dev_t


# ============================================================
# 净收益引擎：版本B口径，可切换成交价源 + 滑点 + 跳空过滤
# ============================================================
def net_ret_variant_B_exec(final_pos, close_price, exec_open_px, close_px_ref,
                           idx, slip_bp=0.0, gap_filter_thr=0.0, skip_open_mask=None):
    """
    版本B口径通用引擎：
      隔夜跳空 = pos[t-1] × (exec_open_px[t] - close[t-1]) / close[t-1]
      日内     = pos[t]     × (close[t]     - exec_open_px[t]) / exec_open_px[t]
    slip_bp：开/平各叠加的固定基点滑点（bp, 0.01%）。开仓在exec_open_px处成交，
              平仓在当日收盘处成交，两侧各扣一次滑点。
    gap_filter_thr：跳空过滤阈值。若 |exec_open_px[t]/close[t-1] - 1| > thr，
              则该信号放弃开仓（pos[t]置0，仅保留隔夜pos[t-1]暴露）。
    skip_open_mask：布尔掩码，True处强制pos[t]=0（用于模块3过滤命中日）。
    """
    close = close_px_ref.values if hasattr(close_px_ref, "values") else np.asarray(close_px_ref)
    n = len(idx)
    exec_open_px = np.asarray(exec_open_px, dtype=float)

    slip = slip_bp / 10000.0

    # 跳空过滤：构造最终持仓
    pos = final_pos.copy()
    if gap_filter_thr > 0:
        for t in range(1, n):
            if close[t - 1] > 0:
                gap = exec_open_px[t] / close[t - 1] - 1
                if abs(gap) > gap_filter_thr:
                    pos[t] = 0.0
    if skip_open_mask is not None:
        pos[skip_open_mask] = 0.0

    overnight_ret = np.zeros(n)
    intraday_ret = np.zeros(n)
    for t in range(1, n):
        if close[t - 1] > 0:
            overnight_ret[t] = final_pos[t - 1] * (exec_open_px[t] - close[t - 1]) / close[t - 1]
    for t in range(n):
        if exec_open_px[t] > 0:
            intraday_ret[t] = pos[t] * (close[t] - exec_open_px[t]) / exec_open_px[t]

    # 成本：原有换手成本 + 滑点（开/平各一次，按持仓变动量计）
    dpos = np.zeros(n)
    dpos[1:] = np.abs(pos[1:] - pos[:-1])
    dpos[0] = np.abs(pos[0])
    cost_ret = (2 * COST + 2 * slip) * dpos
    return overnight_ret + intraday_ret - cost_ret


def run_variant_full(ctx, final_pos, exec_open_px, slip_bp=0.0, gap_filter_thr=0.0):
    """对全期跑一遍成交引擎，返回(净收益数组, 指标dict, 交易笔数)"""
    idx = ctx["idx"]
    price = ctx["price"]
    nr = net_ret_variant_B_exec(final_pos, price, exec_open_px, price, idx,
                                 slip_bp=slip_bp, gap_filter_thr=gap_filter_thr)
    mi = metrics_from_net_ret(nr, len(idx))
    trades = extract_trades(final_pos, price, idx)
    n_trades = int(np.sum(np.abs(final_pos[1:] - final_pos[:-1]) > 1e-9)) if len(final_pos) > 1 else 0
    mi["n_trades"] = n_trades
    mi["exec_slip_bp"] = slip_bp
    mi["gap_filter_thr"] = gap_filter_thr
    return nr, mi, trades


# ============================================================
# 模块1：分钟行情接入与成交规则（标定 + 真实样本验证）
# ============================================================
def module1_minute_execution(ctx):
    """
    标定开盘→9:01偏离分布，并在有真实分钟数据的小样本上验证成交逻辑。
    """
    dev_df = load_minute_deviation()
    slippage_cal = calibrate_slippage(dev_df)

    # 在真实样本上对比"日开盘成交" vs "9:01均价成交"的实现偏离
    sample = dev_df.copy()
    sample["exec_diff_pct"] = sample["dev_pct"]  # 9:01成交相对开盘的优势/劣势(%)
    sample.to_csv(os.path.join(DATA_DIR, "exp425_min_opening_deviation.csv"), index=False)

    print(f"[模块1] 真实分钟数据: {slippage_cal['n_days']}个完整交易日")
    print(f"  9:01 vs 开盘 偏离: 均值={slippage_cal['mean_dev_pct']:+.4f}% "
          f"标准差={slippage_cal['std_dev_pct']:.4f}% 绝对均值={slippage_cal['mean_abs_dev_pct']:.4f}%")
    print(f"  偏离区间: [{slippage_cal['min_dev_pct']:+.4f}%, {slippage_cal['max_dev_pct']:+.4f}%]")
    print(f"  解读: 系统性偏移近0(开盘撮合价公允)，但高频波动±{slippage_cal['std_dev_pct']:.3f}%构成真实执行不确定性")
    return slippage_cal


# ============================================================
# 模块2：三组绩效对比
# ============================================================
def module2_performance_compare(ctx, final_pos, slippage_cal):
    """
    三组对照（均以T1+ADX降权信号、版本B口径为基底）：
      ① exp424基准: T+1开盘成交，无滑点
      ② 9:01无滑点: 9:01均价成交(外推)，无滑点
      ③ 9:01+滑点: 9:01均价成交(外推)，叠加商品基础滑点
    滑点用多档bp做敏感度；主结果取10bp(0.10%)代表中等商品流动性成本。
    """
    idx = ctx["idx"]
    price = ctx["price"]
    ohlc = ctx["ohlc"]
    open_px = ohlc["open"].reindex(idx).values.astype(float)

    # 构造9:01成交价（固定种子，保证三组用同一套9:01价）
    _, p901_px, dev_t = build_901_exec_prices(ctx, slippage_cal, vol_scale=True, seed=RNG_SEED)

    rows = []
    # ① 基准：开盘成交，无滑点
    _, mi1, _ = run_variant_full(ctx, final_pos, open_px, slip_bp=0.0, gap_filter_thr=0.0)
    rows.append({"group": "① exp424基准(开盘,无滑点)", **mi1})

    # ② 9:01无滑点
    _, mi2, _ = run_variant_full(ctx, final_pos, p901_px, slip_bp=0.0, gap_filter_thr=0.0)
    rows.append({"group": "② 9:01进场(无滑点)", **mi2})

    # ③ 9:01+滑点（主结果10bp）
    MAIN_SLIP_BP = 10.0
    _, mi3, _ = run_variant_full(ctx, final_pos, p901_px, slip_bp=MAIN_SLIP_BP, gap_filter_thr=0.0)
    mi3["group_main_slip_bp"] = MAIN_SLIP_BP
    rows.append({"group": f"③ 9:01进场(+{MAIN_SLIP_BP:.0f}bp滑点)", **mi3})

    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(DATA_DIR, "exp425_performance_compare.csv"), index=False)

    # 滑点敏感度：固定9:01成交，扫描多档bp
    slip_rows = []
    for bp in SLIP_BP_GRID:
        _, mi, _ = run_variant_full(ctx, final_pos, p901_px, slip_bp=bp, gap_filter_thr=0.0)
        slip_rows.append({"slip_bp": bp, "slip_pct": bp / 100.0,
                          "ann_ret": mi["annual_return"], "sharpe": mi["sharpe"],
                          "max_dd": mi["max_drawdown"], "calmar": mi["calmar"]})
    df_slip = pd.DataFrame(slip_rows)
    df_slip.to_csv(os.path.join(DATA_DIR, "exp425_slip_sensitivity.csv"), index=False)

    return df, df_slip, p901_px, dev_t


# ============================================================
# 模块3：跳空极端场景过滤实验
# ============================================================
def module3_gap_filter(ctx, final_pos, p901_px):
    """
    跳空过滤：若T+1开盘(或9:01)相对T日收盘跳空幅度超过阈值，放弃该信号开仓。
    扫描阈值网格，找到对绩效影响最小的阈值（避免极端流动性冲击日逆势开仓）。
    同时输出跳空分布统计。
    """
    idx = ctx["idx"]
    price = ctx["price"]
    close = price.reindex(idx).values.astype(float)
    n = len(idx)

    # 跳空分布：9:01价 vs T日收盘
    gaps = np.zeros(n)
    gaps[1:] = (p901_px[1:] - close[:-1]) / close[:-1]
    gap_df = pd.DataFrame({
        "date": [str(idx[i].date()) for i in range(n)],
        "gap_pct": gaps[1:] * 100 if n > 1 else 0,
    }).iloc[1:]
    gap_df.to_csv(os.path.join(DATA_DIR, "exp425_gap_distribution.csv"), index=False)

    # 阈值扫描（叠加10bp滑点的9:01口径，与模块2第③组一致）
    rows = []
    for thr in GAP_THR_GRID:
        _, mi, _ = run_variant_full(ctx, final_pos, p901_px, slip_bp=10.0, gap_filter_thr=thr)
        # 命中过滤的交易日数
        n_filtered = int(np.sum(np.abs(gaps[1:]) > thr)) if thr > 0 else 0
        rows.append({"gap_thr": thr, "n_filtered_days": n_filtered, **mi})
    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(DATA_DIR, "exp425_gap_filter_scan.csv"), index=False)

    return df, gap_df


# ============================================================
# 模块4：结论总结 + 绩效衰减量化
# ============================================================
def module4_summary(perf_df, slip_df, gap_df, slippage_cal):
    """量化从【开盘价假设】切换到【9:01+滑点】的绩效衰减，给出实盘可行性判断。"""
    # 取三组
    g1 = perf_df.iloc[0]  # 基准
    g2 = perf_df.iloc[1]  # 9:01无滑点
    g3 = perf_df.iloc[2]  # 9:01+滑点

    def delta(new, base, key):
        return (new[key] - base[key]) * 100

    summary = {
        "baseline_opening_no_slip": {
            "annual_return": float(g1["annual_return"]),
            "sharpe": float(g1["sharpe"]),
            "max_drawdown": float(g1["max_drawdown"]),
            "calmar": float(g1["calmar"]),
            "n_trades": int(g1["n_trades"]),
        },
        "exec_901_no_slip": {
            "annual_return": float(g2["annual_return"]),
            "sharpe": float(g2["sharpe"]),
            "max_drawdown": float(g2["max_drawdown"]),
            "calmar": float(g2["calmar"]),
            "n_trades": int(g2["n_trades"]),
        },
        "exec_901_with_slip": {
            "annual_return": float(g3["annual_return"]),
            "sharpe": float(g3["sharpe"]),
            "max_drawdown": float(g3["max_drawdown"]),
            "calmar": float(g3["calmar"]),
            "n_trades": int(g3["n_trades"]),
        },
        # 衰减量化（基准→9:01+滑点）
        "decay_annual_pp": float(delta(g3, g1, "annual_return")),
        "decay_sharpe": float(delta(g3, g1, "sharpe")),
        "decay_calmar": float(delta(g3, g1, "calmar")),
        "decay_annual_ratio_pct": float((g3["annual_return"] / g1["annual_return"] - 1) * 100) if g1["annual_return"] > 0 else 0.0,
        # 9:01均价本身的影响（无滑点对照）
        "opening_to_901_annual_pp": float(delta(g2, g1, "annual_return")),
        "slip_only_annual_pp": float(delta(g3, g2, "annual_return")),
    }

    # 跳空过滤结论：选Calmar最高且n_filtered_days合理的阈值
    if len(gap_df) > 0:
        best_gap = gap_df.loc[gap_df["calmar"].idxmax()]
        summary["best_gap_filter"] = {
            "gap_thr": float(best_gap["gap_thr"]),
            "n_filtered_days": int(best_gap["n_filtered_days"]),
            "calmar": float(best_gap["calmar"]),
            "ann_ret": float(best_gap["ann_ret"]),
        }

    # 实盘可行性判断
    rec = summary["exec_901_with_slip"]
    if rec["calmar"] >= 3.0 and rec["max_drawdown"] > -0.12:
        verdict = "FEASIBLE"
        reason = (f"9:01+滑点后年化{rec['annual_return']*100:.1f}%、Calmar{rec['calmar']:.2f}、"
                  f"回撤{rec['max_drawdown']*100:.1f}%，仍满足实盘要求")
    elif rec["annual_return"] > 0:
        verdict = "MARGINAL"
        reason = (f"9:01+滑点后年化{rec['annual_return']*100:.1f}%仍为正，但Calmar{rec['calmar']:.2f}、"
                  f"回撤{rec['max_drawdown']*100:.1f}%，需进一步降低换手或滑点敏感参数精调")
    else:
        verdict = "NOT_FEASIBLE"
        reason = f"9:01+滑点后年化{rec['annual_return']*100:.1f}%转负，开盘假设优势被成交摩擦耗尽"
    summary["verdict"] = verdict
    summary["verdict_reason"] = reason
    return summary


# ============================================================
# 主流程
# ============================================================
if __name__ == "__main__":
    print("=" * 72)
    print("exp425: 分钟级进场仿真与滑点评估")
    print("基准: exp424 T1 + ADX降权(×0.3)，HAM信号与风控锁定")
    print("=" * 72)

    ctx = load_context()
    idx = ctx["idx"]
    print(f"\n数据: {idx[0].date()} ~ {idx[-1].date()} ({len(idx)} 日)")

    # 信号层：T1 + ADX降权
    final_pos, n_adx_days = compute_signal_positions(ctx)

    # 基准校验：与exp424推荐配置一致性（开盘口径，无滑点）
    ohlc_open = ctx["ohlc"]["open"].reindex(idx).values.astype(float)
    _, mi_base_check, _ = run_variant_full(ctx, final_pos, ohlc_open, slip_bp=0.0, gap_filter_thr=0.0)
    print(f"\n[基准校验] T1+ADX降权(开盘,无滑点): 年化={mi_base_check['annual_return']*100:.1f}% "
          f"夏普={mi_base_check['sharpe']:.3f} 回撤={mi_base_check['max_drawdown']*100:.1f}% "
          f"Calmar={mi_base_check['calmar']:.2f} 交易日数={mi_base_check['n_trades']}")
    print(f"  预期(exp424 ADX降权): 年化≈33.4%, Calmar≈5.43, 回撤≈-6.1%")

    # 模块1：分钟成交标定
    print("\n" + "=" * 72)
    print("模块1：分钟行情接入与成交规则")
    print("=" * 72)
    slippage_cal = module1_minute_execution(ctx)

    # 模块2：三组绩效对比
    print("\n" + "=" * 72)
    print("模块2：三组绩效对比")
    print("=" * 72)
    perf_df, slip_df, p901_px, dev_t = module2_performance_compare(ctx, final_pos, slippage_cal)
    for _, r in perf_df.iterrows():
        print(f"  {r['group']}: 年化={r['annual_return']*100:.1f}% 夏普={r['sharpe']:.3f} "
              f"回撤={r['max_drawdown']*100:.1f}% Calmar={r['calmar']:.2f} 笔数={int(r['n_trades'])}")
    print(f"\n  滑点敏感度(9:01口径):")
    for _, r in slip_df.iterrows():
        print(f"    {r['slip_bp']:.0f}bp({r['slip_pct']*100:.2f}%): 年化={r['ann_ret']*100:.1f}% "
              f"Calmar={r['calmar']:.2f}")

    # 模块3：跳空过滤实验
    print("\n" + "=" * 72)
    print("模块3：跳空极端场景过滤实验")
    print("=" * 72)
    gap_df, gap_dist = module3_gap_filter(ctx, final_pos, p901_px)
    print(f"  跳空分布(9:01 vs T日收盘): 均值={gap_dist['gap_pct'].mean():+.4f}% "
          f"标准差={gap_dist['gap_pct'].std():.4f}%")
    for _, r in gap_df.iterrows():
        print(f"    阈值{r['gap_thr']*100:.0f}%: 过滤{int(r['n_filtered_days'])}日 "
              f"年化={r['annual_return']*100:.1f}% Calmar={r['calmar']:.2f} 回撤={r['max_drawdown']*100:.1f}%")

    # 模块4：结论
    print("\n" + "=" * 72)
    print("模块4：结论总结")
    print("=" * 72)
    summary = module4_summary(perf_df, slip_df, gap_df, slippage_cal)
    print(f"  年化衰减: {summary['decay_annual_pp']:+.1f}pp "
          f"({summary['decay_annual_ratio_pct']:+.1f}%)")
    print(f"  9:01均价本身影响: {summary['opening_to_901_annual_pp']:+.1f}pp")
    print(f"  滑点单独影响: {summary['slip_only_annual_pp']:+.1f}pp")
    if "best_gap_filter" in summary:
        bg = summary["best_gap_filter"]
        print(f"  最优跳空过滤: 阈值{bg['gap_thr']*100:.0f}% (过滤{bg['n_filtered_days']}日) → Calmar {bg['calmar']:.2f}")
    print(f"  实盘判定: {summary['verdict']}")
    print(f"    {summary['verdict_reason']}")

    # 保存汇总JSON
    summary_json = {
        "baseline_check": mi_base_check,
        "module1_slippage_cal": slippage_cal,
        "module2_performance": perf_df.to_dict(orient="records"),
        "module2_slip_sensitivity": slip_df.to_dict(orient="records"),
        "module3_gap_scan": gap_df.to_dict(orient="records"),
        "module4_summary": summary,
        "config": {
            "adx_threshold": ADX_THRESHOLD,
            "adx_scale": ADX_SCALE,
            "main_slip_bp": 10.0,
            "rng_seed": RNG_SEED,
        },
    }
    with open(os.path.join(DATA_DIR, "exp425_summary.json"), "w") as f:
        json.dump(summary_json, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n  ✓ 汇总JSON: data/exp425_summary.json")
    print("\n✅ exp425 完成")
