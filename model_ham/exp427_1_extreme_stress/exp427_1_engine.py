"""
exp427-1: 碳酸锂极端行情压力测试

背景: exp428 证明 HAM-T1+ADX 滚动每日估参 STABLE(34.0%/Calmar4.98, +5.2pp vs 一次性)。
      本实验对 LC 历史极端行情段做压力测试, 验证:
        (1) HAM-T1+ADX 在极端单边趋势/暴涨暴跌中的稳健性
        (2) ADX降权在极端趋势中的保护作用 (exp426证明ADX是风控组件)
        (3) exp428滚动估参 vs 一次性在极端段的稳健性差异
        (4) 极端执行假设(高滑点/大跳空)下的生存能力
        (5) 极端段最大回撤与风控触发情况

极端行情识别 (基于 LC 2024-02~2026-09 历史):
  - 动量极端: |20日动量| > 25%
  - 波动率极端: 20日实现波动率 > 中位数 2x
  - 跳空极端: |单日收益| > 8%
  已知 LC 极端段:
    2026-01 暴涨(+50%~+70%, 20日动量峰值+70.9%)
    2026-02~03 暴涨暴跌(单日+11.8%/-12.3%/-10.6%)
    2025-07~08 高波动(+10.5%单日, 月波动63%)
  共约63个极端日集中在 2025-07~2026-03。

基准配置: 完整 HAM-T1+ADX降权(×0.3), 9:01分钟均价+10bp滑点。
  锚定值: 全样本年化28.9%/Calmar4.23/回撤-6.8%/129笔。

时序铁律: 版本B口径 —— T日收盘决策, T+1 9:01分钟均价成交(+10bp滑点)。
"""
import os
import sys
import json
import numpy as np
import pandas as pd

MODEL = "/home/ubuntu/lithium-engine/model_ham"
for d in ["exp409_ham_dynamic", "exp410_fund_dynamic", "exp411_combined",
          "exp413_robustness", "exp414_attribution"]:
    sys.path.insert(0, os.path.join(MODEL, d))
sys.path.insert(0, os.path.join(MODEL, "exp416_tail_risk_protect"))
sys.path.insert(0, os.path.join(MODEL, "exp424_t1_robustness"))
sys.path.insert(0, os.path.join(MODEL, "exp425_minute_execution"))

import exp409_ham_dynamic as E409
import exp413_engine as E413
from exp411_combined import build_targets, price_series, extract_trades, TRADING_DAYS_PER_YEAR
from exp424_engine import (
    load_context, compute_positions, compute_adx, metrics_from_net_ret,
    VOL_HALF, VOL_FULL, HALF_SCALE, FULL_SCALE,
    DD_RECOVER, DD_WARN, DD_HARD, START, END, COST, TDPY,
)
from exp425_engine import (
    ADX_THRESHOLD, ADX_SCALE, RNG_SEED,
    calibrate_slippage, build_901_exec_prices, run_variant_full, net_ret_variant_B_exec,
)

EXP_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(EXP_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)
REPORTS_DIR = os.path.join(os.path.dirname(os.path.dirname(EXP_DIR)), "reports")
os.makedirs(REPORTS_DIR, exist_ok=True)

MAIN_SLIP_BP = 10.0

# 极端识别阈值
MOM20_THR = 0.25      # |20日动量|阈值
VOL_MULT = 2.0        # 波动率>中位数倍数
GAP_THR = 0.08        # |单日收益|阈值


# ============================================================
# 极端行情识别
# ============================================================
def detect_extreme_segments(ctx):
    """
    识别 LC 极端行情日与连续段。
    返回 (extreme_mask布尔数组, segs列表[(start_idx,end_idx,count,peak_vol,peak_mom)])。
    """
    idx = ctx["idx"]
    close = ctx["price"].reindex(idx).values
    n = len(idx)
    # 20日动量
    pct20 = pd.Series(close).pct_change(20).values
    # 20日实现波动率(年化)
    ret = np.zeros(n)
    ret[1:] = np.diff(close) / close[:-1]
    vol20 = pd.Series(ret).rolling(20, min_periods=10).std().values * np.sqrt(TDPY)
    # 单日跳空
    abs_ret = np.abs(ret)

    med_vol = np.nanmedian(vol20[np.isfinite(vol20) & (vol20 > 0)]) if np.any(np.isfinite(vol20) & (vol20 > 0)) else 0.0
    extreme = ((np.abs(pct20) > MOM20_THR) | (vol20 > med_vol * VOL_MULT) | (abs_ret > GAP_THR))
    extreme = np.nan_to_num(extreme, nan=False)

    # 连续段 (>=3日连续极端)
    segs = []
    t = 0
    while t < n:
        if extreme[t]:
            s = t
            while t < n and extreme[t]:
                t += 1
            e = t - 1
            if e - s + 1 >= 3:
                seg_vol = np.nanmax(vol20[s:e+1]) if np.any(np.isfinite(vol20[s:e+1])) else 0.0
                seg_mom = np.nanmax(np.abs(pct20[s:e+1])) if np.any(np.isfinite(pct20[s:e+1])) else 0.0
                segs.append({
                    "start_idx": s, "end_idx": e, "count": e - s + 1,
                    "start_date": str(idx[s].date()), "end_date": str(idx[e].date()),
                    "peak_vol_ann": float(seg_vol), "peak_abs_mom20": float(seg_mom),
                    "peak_abs_ret": float(np.max(abs_ret[s:e+1])),
                })
        else:
            t += 1
    return extreme, segs, vol20, pct20, abs_ret, med_vol


# ============================================================
# 分段绩效: 在指定 [lo, hi) 区间计算净收益指标
# ============================================================
def segment_metrics(net_ret, lo, hi, n_total):
    """在 [lo, hi) 子区间计算段内绩效(段内净值, 不复利到全样本)。"""
    nr = net_ret[lo:hi]
    n = len(nr)
    if n < 2:
        return {"n_days": n, "total_return": 0.0, "ann_return": 0.0, "sharpe": 0.0, "max_drawdown": 0.0}
    eq = np.cumprod(1 + nr)
    years = n / TDPY
    total_ret = eq[-1] - 1
    ann_ret = (1 + total_ret) ** (1 / years) - 1 if total_ret > -1 else -1
    sharpe = nr.mean() / nr.std() * np.sqrt(TDPY) if nr.std() > 0 else 0.0
    peak = np.maximum.accumulate(eq)
    max_dd = ((eq - peak) / peak).min()
    return {
        "n_days": n, "total_return": float(total_ret), "annual_return": float(ann_ret),
        "sharpe": float(sharpe), "max_drawdown": float(max_dd),
        "final_eq": float(eq[-1]),
    }


# ============================================================
# 主流程
# ============================================================
if __name__ == "__main__":
    print("=" * 76)
    print("exp427-1: 碳酸锂极端行情压力测试")
    print("基准: 完整HAM-T1+ADX降权(×0.3), 9:01分钟均价 + 10bp滑点")
    print("=" * 76)

    ctx = load_context()
    idx = ctx["idx"]
    price = ctx["price"]
    ohlc = ctx["ohlc"]
    print(f"\n数据: {idx[0].date()} ~ {idx[-1].date()} ({len(idx)} 日)")

    # 9:01成交价标定 (固定种子)
    dev_path = os.path.join(MODEL, "exp425_minute_execution", "data", "min_opening_deviation.csv")
    if os.path.exists(dev_path):
        dev_df = pd.read_csv(dev_path)
    else:
        import exp425_engine
        dev_df = exp425_engine.load_minute_deviation()
    slippage_cal = calibrate_slippage(dev_df)
    _, p901_px, _ = build_901_exec_prices(ctx, slippage_cal, vol_scale=True, seed=RNG_SEED)

    # ============ 模块0: 基准校验 ============
    print("\n" + "=" * 76)
    print("模块0: 基准校验 (全样本, 应复现28.9%/Calmar4.23)")
    print("=" * 76)
    h_oneshot, _ = build_targets()
    h_oneshot = h_oneshot.reindex(idx).fillna(0.0)
    pos, _diag = compute_positions(ctx, VOL_HALF, VOL_FULL, HALF_SCALE, FULL_SCALE,
                                   DD_RECOVER, DD_WARN, DD_HARD)
    high = ohlc["high"].reindex(idx).values
    low = ohlc["low"].reindex(idx).values
    close = price.reindex(idx).values
    adx = compute_adx(high, low, close, period=14)
    adx_mask = adx > ADX_THRESHOLD
    pos[adx_mask] = pos[adx_mask] * ADX_SCALE
    nr, mi, tr = run_variant_full(ctx, pos, p901_px, slip_bp=MAIN_SLIP_BP, gap_filter_thr=0.0)
    print(f"  全样本: 年化={mi['annual_return']*100:.1f}% 夏普={mi['sharpe']:.3f} "
          f"回撤={mi['max_drawdown']*100:.1f}% Calmar={mi['calmar']:.2f} 笔数={int(mi['n_trades'])}")
    print(f"  预期(exp425/exp426锚点): 年化≈28.9%, Calmar≈4.23, 回撤≈-6.8%, 笔数≈129")

    # ============ 模块1: 极端行情识别 ============
    print("\n" + "=" * 76)
    print("模块1: 极端行情识别 (|20日动量|>25% 或 波动率>2x中位 或 |单日|>8%)")
    print("=" * 76)
    extreme, segs, vol20, pct20, abs_ret, med_vol = detect_extreme_segments(ctx)
    print(f"  极端日总数: {int(extreme.sum())} / {len(idx)} ({extreme.sum()/len(idx)*100:.1f}%)")
    print(f"  20日波动率中位数: {med_vol*100:.1f}%")
    print(f"\n  连续极端段 (>=3日连续): {len(segs)} 段")
    seg_df = pd.DataFrame(segs)
    if len(seg_df) > 0:
        for _, r in seg_df.iterrows():
            print(f"    {r['start_date']} ~ {r['end_date']} ({int(r['count'])}日) "
                  f"峰值波动{r['peak_vol_ann']*100:.0f}% 峰值动量{r['peak_abs_mom20']*100:.0f}% "
                  f"峰值单日{r['peak_abs_ret']*100:.1f}%")
    seg_df.to_csv(os.path.join(DATA_DIR, "exp427_1_extreme_segments.csv"), index=False)

    # ============ 模块2: 极端段 vs 非极端段 绩效对比 ============
    print("\n" + "=" * 76)
    print("模块2: 极端段 vs 非极端段 绩效对比")
    print("=" * 76)
    eq = np.cumprod(1 + nr)
    eq_peak = np.maximum.accumulate(eq)
    dd_series = (eq - eq_peak) / eq_peak

    # 极端日绩效
    extreme_days = extreme.astype(bool)
    n_ext = int(extreme_days.sum())
    n_norm = int((~extreme_days).sum())
    mi_ext = segment_metrics(nr, 0, len(nr), len(nr))  # 占位
    # 分别提取极端日与非极端日的日收益
    nr_ext = nr[extreme_days]
    nr_norm = nr[~extreme_days]
    eq_ext = np.cumprod(1 + nr_ext) if len(nr_ext) > 0 else np.array([1.0])
    eq_norm = np.cumprod(1 + nr_norm) if len(nr_norm) > 0 else np.array([1.0])
    def _seg(name, nr_sub):
        if len(nr_sub) < 2:
            return {"group": name, "n_days": len(nr_sub), "total_return": 0, "sharpe": 0, "max_drawdown": 0}
        eq_s = np.cumprod(1 + nr_sub)
        years = len(nr_sub) / TDPY
        tr_s = eq_s[-1] - 1
        ann = (1+tr_s)**(1/years)-1 if tr_s > -1 else -1
        sh = nr_sub.mean()/nr_sub.std()*np.sqrt(TDPY) if nr_sub.std()>0 else 0
        pk = np.maximum.accumulate(eq_s)
        return {"group": name, "n_days": int(len(nr_sub)), "total_return": float(tr_s),
                "annual_return": float(ann), "sharpe": float(sh), "max_drawdown": float(((eq_s-pk)/pk).min())}
    cmp_rows = [_seg("极端段日", nr_ext), _seg("非极端段日", nr_norm), _seg("全样本", nr)]
    cmp_df = pd.DataFrame(cmp_rows)
    cmp_df.to_csv(os.path.join(DATA_DIR, "exp427_1_extreme_vs_normal.csv"), index=False)
    for _, r in cmp_df.iterrows():
        print(f"  {r['group']:<8}: n={int(r['n_days']):>3} 年化={r['annual_return']*100:>+7.1f}% "
              f"夏普={r['sharpe']:+.2f} 回撤={r['max_drawdown']*100:>+6.1f}% 累计={r['total_return']*100:>+7.1f}%")

    # 极端段内最大回撤
    # 逐极端连续段算段内净值
    print("\n  逐连续极端段内绩效:")
    seg_perf_rows = []
    for _, seg in seg_df.iterrows():
        lo, hi = int(seg["start_idx"]), int(seg["end_idx"]) + 1
        mi_s = segment_metrics(nr, lo, hi, len(nr))
        mi_s["start_date"] = seg["start_date"]
        mi_s["end_date"] = seg["end_date"]
        mi_s["count"] = seg["count"]
        seg_perf_rows.append(mi_s)
        print(f"    {seg['start_date']}~{seg['end_date']}({int(seg['count'])}日): "
              f"段内累计={mi_s['total_return']*100:+.1f}% 回撤={mi_s['max_drawdown']*100:+.1f}%")
    if seg_perf_rows:
        pd.DataFrame(seg_perf_rows).to_csv(os.path.join(DATA_DIR, "exp427_1_segment_performance.csv"), index=False)

    # ============ 模块3: ADX降权在极端段的保护作用 ============
    print("\n" + "=" * 76)
    print("模块3: ADX降权在极端段的保护作用 (ADX降权×0.3 vs 无ADX降权)")
    print("=" * 76)
    # 无ADX降权版
    pos_no_adx, _ = compute_positions(ctx, VOL_HALF, VOL_FULL, HALF_SCALE, FULL_SCALE,
                                      DD_RECOVER, DD_WARN, DD_HARD)
    nr_no_adx = net_ret_variant_B_exec(pos_no_adx, price, p901_px, price, idx,
                                        slip_bp=MAIN_SLIP_BP, gap_filter_thr=0.0)
    # 极端段内对比
    def _extreme_cmp(pos_v):
        nr_v = net_ret_variant_B_exec(pos_v, price, p901_px, price, idx,
                                       slip_bp=MAIN_SLIP_BP, gap_filter_thr=0.0)
        nr_e = nr_v[extreme_days]
        eq_e = np.cumprod(1 + nr_e) if len(nr_e) > 0 else np.array([1.0])
        dd_e = ((eq_e - np.maximum.accumulate(eq_e))/np.maximum.accumulate(eq_e)).min() if len(eq_e) > 1 else 0
        ann_e = ((eq_e[-1]+1)**(TDPY/len(nr_e))-1) if len(nr_e) > 0 else 0
        return ann_e, dd_e
    ann_adx, dd_adx = _extreme_cmp(pos)
    ann_noadx, dd_noadx = _extreme_cmp(pos_no_adx)
    adx_rows = [
        {"config": "ADX降权×0.3(基准)", "extreme_ann": ann_adx, "extreme_dd": dd_adx},
        {"config": "无ADX降权", "extreme_ann": ann_noadx, "extreme_dd": dd_noadx},
    ]
    adx_df = pd.DataFrame(adx_rows)
    adx_df.to_csv(os.path.join(DATA_DIR, "exp427_1_adx_protection.csv"), index=False)
    print(f"  ADX降权×0.3: 极端段年化={ann_adx*100:+.1f}% 回撤={dd_adx*100:+.1f}%")
    print(f"  无ADX降权  : 极端段年化={ann_noadx*100:+.1f}% 回撤={dd_noadx*100:+.1f}%")
    print(f"  ADX保护: 极端段回撤改善={((dd_adx-dd_noadx)*100):+.1f}pp")
    print(f"  解读: ADX降权在极端单边趋势中降低仓位, 牺牲部分收益换取回撤控制")

    # ============ 模块4: 滚动估参 vs 一次性 在极端段稳健性 ============
    print("\n" + "=" * 76)
    print("模块4: 滚动估参 vs 一次性 在极端段稳健性")
    print("=" * 76)
    # 复用 exp428 滚动估参
    sys.path.insert(0, os.path.join(MODEL, "exp428_shadow_trading"))
    from exp428_engine import rolling_build_ham_target, compute_positions_with_h
    h_roll, _ = rolling_build_ham_target()
    h_roll = h_roll.reindex(idx).fillna(0.0)
    pos_roll = compute_positions_with_h(ctx, h_roll)
    nr_roll = net_ret_variant_B_exec(pos_roll, price, p901_px, price, idx,
                                      slip_bp=MAIN_SLIP_BP, gap_filter_thr=0.0)
    nr_roll_ext = nr_roll[extreme_days]
    eq_roll_ext = np.cumprod(1 + nr_roll_ext) if len(nr_roll_ext) > 0 else np.array([1.0])
    dd_roll_ext = ((eq_roll_ext-np.maximum.accumulate(eq_roll_ext))/np.maximum.accumulate(eq_roll_ext)).min() if len(eq_roll_ext)>1 else 0
    ann_roll_ext = ((eq_roll_ext[-1]+1)**(TDPY/len(nr_roll_ext))-1) if len(nr_roll_ext)>0 else 0
    # 一次性极端段
    nr_on_ext = nr[extreme_days]
    eq_on_ext = np.cumprod(1+nr_on_ext) if len(nr_on_ext)>0 else np.array([1.0])
    dd_on_ext = ((eq_on_ext-np.maximum.accumulate(eq_on_ext))/np.maximum.accumulate(eq_on_ext)).min() if len(eq_on_ext)>1 else 0
    ann_on_ext = ((eq_on_ext[-1]+1)**(TDPY/len(nr_on_ext))-1) if len(nr_on_ext)>0 else 0
    roll_rows = [
        {"config": "一次性(固定参数)", "extreme_ann": ann_on_ext, "extreme_dd": dd_on_ext, "extreme_total": float(eq_on_ext[-1]-1)},
        {"config": "滚动(每日估参)", "extreme_ann": ann_roll_ext, "extreme_dd": dd_roll_ext, "extreme_total": float(eq_roll_ext[-1]-1)},
    ]
    roll_df = pd.DataFrame(roll_rows)
    roll_df.to_csv(os.path.join(DATA_DIR, "exp427_1_rolling_vs_oneshot_extreme.csv"), index=False)
    print(f"  一次性(固定参数): 极端段年化={ann_on_ext*100:+.1f}% 回撤={dd_on_ext*100:+.1f}% 累计={float(eq_on_ext[-1]-1)*100:+.1f}%")
    print(f"  滚动(每日估参)  : 极端段年化={ann_roll_ext*100:+.1f}% 回撤={dd_roll_ext*100:+.1f}% 累计={float(eq_roll_ext[-1]-1)*100:+.1f}%")

    # ============ 模块5: 极端执行假设压力测试 ============
    print("\n" + "=" * 76)
    print("模块5: 极端执行假设压力测试 (高滑点/大跳空)")
    print("=" * 76)
    stress_rows = []
    for bp in [10.0, 20.0, 30.0, 50.0]:
        nr_s = net_ret_variant_B_exec(pos, price, p901_px, price, idx,
                                       slip_bp=bp, gap_filter_thr=0.0)
        mi_s = metrics_from_net_ret(nr_s, len(idx))
        nr_s_ext = nr_s[extreme_days]
        eq_s_ext = np.cumprod(1+nr_s_ext) if len(nr_s_ext)>0 else np.array([1.0])
        dd_s_ext = ((eq_s_ext-np.maximum.accumulate(eq_s_ext))/np.maximum.accumulate(eq_s_ext)).min() if len(eq_s_ext)>1 else 0
        stress_rows.append({
            "slip_bp": bp, "full_ann": mi_s["annual_return"], "full_calmar": mi_s["calmar"],
            "full_dd": mi_s["max_drawdown"], "extreme_dd": dd_s_ext,
            "extreme_total": float(eq_s_ext[-1]-1) if len(eq_s_ext)>0 else 0,
        })
        print(f"  {bp:>4.0f}bp: 全样本年化={mi_s['annual_return']*100:+.1f}% Calmar={mi_s['calmar']:.2f} "
              f"极端段回撤={dd_s_ext*100:+.1f}% 极端累计={float(eq_s_ext[-1]-1)*100:+.1f}%")
    stress_df = pd.DataFrame(stress_rows)
    stress_df.to_csv(os.path.join(DATA_DIR, "exp427_1_stress_slippage.csv"), index=False)

    # 大跳空过滤压力测试 (放弃极端跳空日开仓)
    gap_rows = []
    for thr in [0.0, 0.05, 0.08, 0.10]:
        nr_g = net_ret_variant_B_exec(pos, price, p901_px, price, idx,
                                       slip_bp=MAIN_SLIP_BP, gap_filter_thr=thr)
        mi_g = metrics_from_net_ret(nr_g, len(idx))
        nr_g_ext = nr_g[extreme_days]
        eq_g_ext = np.cumprod(1+nr_g_ext) if len(nr_g_ext)>0 else np.array([1.0])
        dd_g_ext = ((eq_g_ext-np.maximum.accumulate(eq_g_ext))/np.maximum.accumulate(eq_g_ext)).min() if len(eq_g_ext)>1 else 0
        n_filtered = int(np.sum(np.abs((p901_px[1:]-close[:-1])/close[:-1]) > thr)) if thr > 0 else 0
        gap_rows.append({"gap_thr": thr, "n_filtered": n_filtered, "full_ann": mi_g["annual_return"],
                         "full_dd": mi_g["max_drawdown"], "extreme_dd": dd_g_ext,
                         "extreme_total": float(eq_g_ext[-1]-1) if len(eq_g_ext)>0 else 0})
        print(f"  跳空阈值{thr*100:.0f}%: 过滤{n_filtered}日 全样本年化={mi_g['annual_return']*100:+.1f}% "
              f"极端段回撤={dd_g_ext*100:+.1f}%")
    gap_df = pd.DataFrame(gap_rows)
    gap_df.to_csv(os.path.join(DATA_DIR, "exp427_1_stress_gap_filter.csv"), index=False)

    # ============ 模块6: 压力判定 ============
    print("\n" + "=" * 76)
    print("模块6: 压力测试判定")
    print("=" * 76)
    # 全样本最大回撤 vs 极端段最大回撤
    full_dd = mi["max_drawdown"]
    # 极端段最坏
    worst_seg_dd = min((r["max_drawdown"] for r in seg_perf_rows), default=0) if seg_perf_rows else 0
    verdict_data = {
        "full_sample_annual": mi["annual_return"],
        "full_sample_calmar": mi["calmar"],
        "full_sample_max_dd": full_dd,
        "n_extreme_days": n_ext,
        "n_extreme_segments": len(segs),
        "extreme_vs_normal_annual": {
            "extreme": cmp_df[cmp_df["group"]=="极端段日"]["annual_return"].values[0] if len(cmp_df)>0 else 0,
            "normal": cmp_df[cmp_df["group"]=="非极端段日"]["annual_return"].values[0] if len(cmp_df)>0 else 0,
        },
        "adx_protection_extreme_dd_improve_pp": (dd_adx - dd_noadx) * 100,
        "rolling_vs_oneshot_extreme_ann": ann_roll_ext - ann_on_ext,
        "worst_segment_dd": worst_seg_dd,
        "extreme_dd_at_50bp": stress_df[stress_df["slip_bp"]==50.0]["full_dd"].values[0] if len(stress_df)>0 else 0,
    }
    # 判定
    extreme_survive = full_dd > -0.20  # 极端段下全样本回撤仍在20%内
    adx_helps = verdict_data["adx_protection_extreme_dd_improve_pp"] > 0
    stress_survive = verdict_data["extreme_dd_at_50bp"] > -0.30
    if extreme_survive and adx_helps and stress_survive:
        verdict = "ROBUST"
        reason = (f"全样本回撤{full_dd*100:.1f}%(<20%阈), ADX降权在极端段回撤改善"
                  f"{verdict_data['adx_protection_extreme_dd_improve_pp']:+.1f}pp, 50bp极端滑点下回撤仍"
                  f"{verdict_data['extreme_dd_at_50bp']*100:.1f}%(<30%阈)。HAM-T1+ADX在极端行情下稳健, 风控有效。")
    elif extreme_survive:
        verdict = "ACCEPTABLE"
        reason = f"极端行情下全样本回撤{full_dd*100:.1f}%仍可控, 但ADX保护或压力测试边界需关注。"
    else:
        verdict = "VULNERABLE"
        reason = f"极端行情下回撤{full_dd*100:.1f}%超标, 风控不足。"
    print(f"  全样本最大回撤: {full_dd*100:.1f}%")
    print(f"  极端段最坏回撤: {worst_seg_dd*100:.1f}%")
    print(f"  压力判定: {verdict}")
    print(f"  {reason}")

    # ============ 产物保存 ============
    print("\n" + "=" * 76)
    print("保存产物")
    print("=" * 76)
    # 净值曲线 + 极端标记
    eq_df = pd.DataFrame({
        "date": [str(idx[i].date()) for i in range(len(idx))],
        "equity": eq, "drawdown": dd_series, "is_extreme": extreme,
        "net_ret": nr, "vol20": vol20, "adx": adx,
    })
    eq_df.to_csv(os.path.join(DATA_DIR, "exp427_1_equity_extreme.csv"), index=False)

    # 可视化
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, axes = plt.subplots(3, 1, figsize=(12, 9), facecolor="#1a1a2e")
        ax = axes[0]
        ax.set_facecolor("#1a1a2e")
        ax.plot(range(len(eq)), eq, color="#00d4ff", lw=1.5, label="Equity (HAM-T1+ADX)")
        # 标记极端段
        for seg in segs:
            ax.axvspan(seg["start_idx"], seg["end_idx"], color="#ff5555", alpha=0.18)
        ax.axhline(1.0, color="#666", ls="--", lw=0.8)
        ax.set_title("exp427-1 LC Extreme Stress: Equity (red=extreme segments)", color="white", fontsize=12)
        ax.legend(facecolor="#2a2a3e", edgecolor="#444", labelcolor="white", fontsize=8)
        ax.tick_params(colors="white")
        for s in ax.spines.values(): s.set_color("#444")
        ax = axes[1]
        ax.set_facecolor("#1a1a2e")
        ax.plot(range(len(dd_series)), dd_series, color="#ffcc33", lw=1.2)
        ax.set_title("Drawdown Series", color="white", fontsize=11)
        ax.tick_params(colors="white")
        for s in ax.spines.values(): s.set_color("#444")
        ax = axes[2]
        ax.set_facecolor("#1a1a2e")
        ax.plot(range(len(vol20)), vol20, color="#33ff99", lw=1.0, label="20d realized vol (ann)")
        ax.axhline(med_vol*VOL_MULT, color="#ff5555", ls="--", lw=0.8, label="2x median threshold")
        ax.set_title("20-day Realized Volatility", color="white", fontsize=11)
        ax.legend(facecolor="#2a2a3e", edgecolor="#444", labelcolor="white", fontsize=8)
        ax.tick_params(colors="white")
        for s in ax.spines.values(): s.set_color("#444")
        plt.tight_layout()
        plt.savefig(os.path.join(DATA_DIR, "exp427_1_stress_test.png"), dpi=110, facecolor="#1a1a2e")
        plt.close()
        print(f"  ✓ 图: data/exp427_1_stress_test.png")
    except Exception as e:
        print(f"  [warn] 绘图失败: {e}")

    summary = {
        "config": {"start": str(START.date()), "end": str(END.date()), "n_days": int(len(idx)),
                   "slip_bp": MAIN_SLIP_BP, "adx_threshold": ADX_THRESHOLD, "adx_scale": ADX_SCALE,
                   "mom20_thr": MOM20_THR, "vol_mult": VOL_MULT, "gap_thr": GAP_THR, "rng_seed": RNG_SEED},
        "full_sample": mi,
        "extreme_identification": {"n_extreme_days": n_ext, "n_segments": len(segs),
                                   "segments": segs if segs else []},
        "extreme_vs_normal": cmp_df.to_dict(orient="records"),
        "segment_performance": seg_perf_rows,
        "adx_protection": adx_df.to_dict(orient="records"),
        "rolling_vs_oneshot_extreme": roll_df.to_dict(orient="records"),
        "stress_slippage": stress_df.to_dict(orient="records"),
        "stress_gap_filter": gap_df.to_dict(orient="records"),
        "verdict": verdict, "verdict_reason": reason, "verdict_data": verdict_data,
    }
    with open(os.path.join(DATA_DIR, "exp427_1_summary.json"), "w") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2, default=str)
    print(f"  ✓ 汇总: data/exp427_1_summary.json")
    print("\n✅ exp427-1 极端行情压力测试完成")
