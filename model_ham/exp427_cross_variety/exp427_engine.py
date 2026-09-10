"""
exp427: HAM-T1+ADX 跨品种泛化测试（锌 Zn）

将 exp425/exp426 锚定的完整 HAM 框架（预期函数 + Logit自适应 + ADX降权 + 三层风控 +
T1波动率隔夜敞口管控）迁移至沪锌期货，9:01+10bp滑点，做样本内外回测，
评估 HAM 异质主体框架的跨品种复用性。

迁移方法（纯参数化，零逻辑复制）：
  - 运行时 monkey-patch exp409.load_ham_factors / exp410.load_factors，指向锌因子表
  - 复用 exp411.build_targets / exp413.run_exp412_param / exp424.compute_positions 全套
  - 复用 exp425 的 9:01 分钟成交 + 滑点引擎（统计外推口径，固定种子）
  - HAM 全部信号逻辑、阈值、风控参数、时序口径与碳酸锂完全一致，仅数据源不同

数据（build_zinc_data.py 已构造）：
  - raw_data/zinc_ham_factors.csv        (HAM因子表，对齐exp401 schema)
  - raw_data/zinc_future.csv             (沪锌主力OHLC)
  - exp406_fund_factor/data/zinc_fund_factors.csv (基本面三因子表)

回测区间：数据 2023-07-03 ~ 2026-07-17。
  训练/样本内：2023-07-03 ~ 2025-06-30
  测试/样本外：2025-07-01 ~ 2026-07-17（约250日，对齐LC口径做样本外验证）

输出：reports/exp427_cross_variety.md
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
import exp410_fund_dynamic as E410
import exp413_engine as E413
from exp411_combined import build_targets, price_series, extract_trades, TRADING_DAYS_PER_YEAR
from exp424_engine import (
    compute_positions, compute_adx, metrics_from_net_ret,
    VOL_HALF, VOL_FULL, HALF_SCALE, FULL_SCALE,
    DD_RECOVER, DD_WARN, DD_HARD, COST, TDPY,
)
from exp425_engine import (
    ADX_THRESHOLD, ADX_SCALE, RNG_SEED,
    calibrate_slippage, build_901_exec_prices,
    net_ret_variant_B_exec, run_variant_full,
)

EXP_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(EXP_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)
REPORTS_DIR = os.path.join(os.path.dirname(os.path.dirname(EXP_DIR)), "reports")
os.makedirs(REPORTS_DIR, exist_ok=True)

ZINC_HAM_CSV = os.path.join(MODEL, "raw_data", "zinc_ham_factors.csv")
ZINC_FUND_CSV = os.path.join(MODEL, "exp406_fund_factor", "data", "zinc_fund_factors.csv")
ZINC_FUTURE_CSV = os.path.join(MODEL, "raw_data", "zinc_future.csv")

# 锌回测区间（数据覆盖 2023-07-03 ~ 2026-07-17）
START = pd.Timestamp("2023-07-03")
END = pd.Timestamp("2026-07-17")
# 样本内/外切分
SPLIT = pd.Timestamp("2025-07-01")

MAIN_SLIP_BP = 10.0


# ============================================================
# 数据源 monkey-patch：把 exp409/exp410 的因子表指向锌数据
# ============================================================
_orig_load_ham = E409.load_ham_factors
_orig_load_fund = E410.load_factors


def _zinc_load_ham_factors():
    df = pd.read_csv(ZINC_HAM_CSV, parse_dates=["date"])
    df = df.sort_values("date").reset_index(drop=True)
    return df


def _zinc_load_factors():
    F = pd.read_csv(ZINC_FUND_CSV)
    F["date"] = pd.to_datetime(F["date"])
    F = F.sort_values("date").reset_index(drop=True)
    # 与 exp410.load_factors 一致：清理 inf，校验禁止字段
    HAM_FORBIDDEN = E410.HAM_FORBIDDEN
    assert not (set(F.columns) & HAM_FORBIDDEN), f"禁止HAM字段: {set(F.columns) & HAM_FORBIDDEN}"
    for c in F.columns:
        if F[c].dtype != object:
            F[c] = F[c].replace([np.inf, -np.inf], np.nan)
    return F


E409.load_ham_factors = _zinc_load_ham_factors
E410.load_factors = _zinc_load_factors


# ============================================================
# 锌版 load_context：复用 exp424.load_context 逻辑，但 OHLC 指向锌
# ============================================================
def load_zinc_context():
    h_target, f_target = build_targets()
    idx = h_target.index
    price = price_series(idx)
    mask = (idx >= START) & (idx <= END)
    idx_w = idx[mask]
    price_w = price.reindex(idx_w)

    vol = E413.compute_realized_vol(price_w, lookback=20).reindex(idx_w)
    pct20 = price_w.pct_change(20)

    ohlc = pd.read_csv(ZINC_FUTURE_CSV)
    ohlc["date"] = pd.to_datetime(ohlc["date"])
    ohlc = ohlc.set_index("date").sort_index()

    return {
        "h_target": h_target.reindex(idx_w).fillna(0.0),
        "f_target": f_target.reindex(idx_w).fillna(0.0),
        "idx": idx_w,
        "price": price_w,
        "vol": vol,
        "pct20": pct20,
        "ohlc": ohlc,
    }


def compute_signal_positions_zinc(ctx):
    """T1 + ADX降权(×0.3)，锌版（与exp425.compute_signal_positions同构）"""
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
    return pos, int(adx_mask.sum())


def metrics_full(net_ret, n):
    """扩展指标：年化/夏普/回撤/Calmar"""
    return metrics_from_net_ret(net_ret, n)


def compute_ic(final_pos, close_price, idx):
    close = close_price.reindex(idx).values.astype(float)
    n = len(idx)
    r_price = np.zeros(n)
    r_price[1:] = (close[1:] - close[:-1]) / close[:-1]
    sig = final_pos[1:]
    lab = r_price[1:]
    mask = np.abs(sig) > 1e-9
    if mask.sum() > 2:
        ic = float(np.corrcoef(sig[mask], lab[mask])[0, 1])
    else:
        ic = 0.0
    try:
        from scipy.stats import spearmanr
        ics, _ = spearmanr(sig[mask], lab[mask]) if mask.sum() > 2 else (0, 0)
        ic_rank = float(ics)
    except Exception:
        ic_rank = float("nan")
    return {"ic": ic, "ic_rank": ic_rank, "n_signal_days": int(mask.sum())}


# ============================================================
# 主流程
# ============================================================
if __name__ == "__main__":
    print("=" * 72)
    print("exp427: HAM-T1+ADX 跨品种泛化测试（锌 Zn）")
    print("完整HAM框架迁移，9:01+10bp滑点，样本内外回测")
    print("=" * 72)

    # 1. 锌上下文
    print("\n[1] 锌上下文加载")
    ctx = load_zinc_context()
    idx = ctx["idx"]
    print(f"  数据: {idx[0].date()} ~ {idx[-1].date()} ({len(idx)} 日)")
    n_sig_h = int((ctx["h_target"].abs() > 1e-9).sum())
    n_sig_f = int((ctx["f_target"].abs() > 1e-9).sum())
    print(f"  HAM腿持仓日: {n_sig_h}, 基本面腿持仓日: {n_sig_f}")

    # 2. 锌版9:01成交价标定
    # 锌无本地分钟数据，复用exp425的LC标定偏离分布（商品期货开盘→9:01偏离的通用微观结构）
    print("\n[2] 9:01成交价标定（复用LC偏离分布，固定种子）")
    dev_csv = os.path.join(MODEL, "exp425_minute_execution", "data", "min_opening_deviation.csv")
    dev_df = pd.read_csv(dev_csv)
    slippage_cal = calibrate_slippage(dev_df)
    print(f"  标定: {slippage_cal['n_days']}交易日, 9:01vs开盘偏离均值={slippage_cal['mean_dev_pct']:+.4f}%")
    _, p901_px, _ = build_901_exec_prices(ctx, slippage_cal, vol_scale=True, seed=RNG_SEED)

    # 3. 全样本回测（9:01+10bp）
    print("\n[3] 锌全样本回测（9:01+10bp）")
    final_pos, n_adx_days = compute_signal_positions_zinc(ctx)
    _, mi_full, trades = run_variant_full(ctx, final_pos, p901_px, slip_bp=MAIN_SLIP_BP, gap_filter_thr=0.0)
    ic_full = compute_ic(final_pos, ctx["price"], idx)
    print(f"  全样本: 年化={mi_full['annual_return']*100:.1f}% 夏普={mi_full['sharpe']:.3f} "
          f"回撤={mi_full['max_drawdown']*100:.1f}% Calmar={mi_full['calmar']:.2f} "
          f"IC={ic_full['ic']:+.3f} 笔数={mi_full['n_trades']} ADX降权日={n_adx_days}")

    # 4. 样本内/外切分回测
    print("\n[4] 样本内/外回测（切分点 %s）" % SPLIT.date())
    results = {}
    for label, s, e in [("样本内(训练)", START, SPLIT - pd.Timedelta(days=1)),
                        ("样本外(测试)", SPLIT, END)]:
        mask = (idx >= s) & (idx <= e)
        sub_idx = idx[mask]
        if len(sub_idx) < 30:
            print(f"  {label}: 数据不足({len(sub_idx)}日)"); continue
        ctx_sub = dict(ctx)
        ctx_sub["idx"] = sub_idx
        ctx_sub["h_target"] = ctx["h_target"].reindex(sub_idx).fillna(0.0)
        ctx_sub["f_target"] = ctx["f_target"].reindex(sub_idx).fillna(0.0)
        ctx_sub["price"] = ctx["price"].reindex(sub_idx)
        ctx_sub["vol"] = ctx["vol"].reindex(sub_idx)
        ctx_sub["pct20"] = ctx["pct20"].reindex(sub_idx)

        # 用全样本final_pos的子集（保持时序一致，避免重新开仓的边界效应）
        mask_arr = np.asarray(mask)
        pos_sub = final_pos[mask_arr]
        p901_sub = p901_px[mask_arr]
        nr, mi, _ = run_variant_full(ctx_sub, pos_sub, p901_sub, slip_bp=MAIN_SLIP_BP, gap_filter_thr=0.0)
        ic = compute_ic(pos_sub, ctx_sub["price"], sub_idx)
        results[label] = mi
        results[label]["ic"] = ic["ic"]
        print(f"  {label} ({sub_idx[0].date()}~{sub_idx[-1].date()}, {len(sub_idx)}日): "
              f"年化={mi['annual_return']*100:.1f}% 夏普={mi['sharpe']:.3f} "
              f"回撤={mi['max_drawdown']*100:.1f}% Calmar={mi['calmar']:.2f} "
              f"IC={ic['ic']:+.3f} 笔数={mi['n_trades']}")

    # 5. 与碳酸锂基准对比
    print("\n[5] 跨品种对比（锌 vs 碳酸锂基准）")
    lc_benchmark = {"annual_return": 0.289, "sharpe": 2.092, "max_drawdown": -0.068,
                    "calmar": 4.23, "n_trades": 129, "ic": 0.196}
    compare_rows = []
    compare_rows.append({"品种": "碳酸锂(LC)", "口径": "9:01+10bp", **{
        "年化": lc_benchmark["annual_return"], "夏普": lc_benchmark["sharpe"],
        "回撤": lc_benchmark["max_drawdown"], "Calmar": lc_benchmark["calmar"],
        "IC": lc_benchmark["ic"], "笔数": lc_benchmark["n_trades"]}})
    if "全样本" not in results:
        results["全样本"] = {**mi_full, "ic": ic_full["ic"]}
    compare_rows.append({"品种": "锌(Zn)", "口径": "9:01+10bp 全样本", **{
        "年化": mi_full["annual_return"], "夏普": mi_full["sharpe"],
        "回撤": mi_full["max_drawdown"], "Calmar": mi_full["calmar"],
        "IC": ic_full["ic"], "笔数": mi_full["n_trades"]}})
    for label in ["样本内(训练)", "样本外(测试)"]:
        if label in results:
            compare_rows.append({"品种": "锌(Zn)", "口径": label, **{
                "年化": results[label]["annual_return"], "夏普": results[label]["sharpe"],
                "回撤": results[label]["max_drawdown"], "Calmar": results[label]["calmar"],
                "IC": results[label].get("ic", 0), "笔数": results[label]["n_trades"]}})
    compare_df = pd.DataFrame(compare_rows)
    compare_df.to_csv(os.path.join(DATA_DIR, "exp427_variety_compare.csv"), index=False)
    print(compare_df.to_string(index=False))

    # 6. 净值曲线
    print("\n[6] 净值曲线")
    equity_full = np.cumprod(1 + run_variant_full(ctx, final_pos, p901_px, slip_bp=MAIN_SLIP_BP,
                                                   gap_filter_thr=0.0)[0])
    eq_df = pd.DataFrame({
        "date": [str(idx[i].date()) for i in range(len(idx))],
        "equity_zinc_full": equity_full,
    })
    # 样本内外切分点
    eq_df["split"] = np.where(pd.to_datetime(eq_df["date"]) >= SPLIT, "样本外", "样本内")
    eq_df.to_csv(os.path.join(DATA_DIR, "exp427_equity_curves.csv"), index=False)

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(11, 5.5), facecolor="#1a1a2e")
        ax.set_facecolor("#1a1a2e")
        ax.plot(range(len(equity_full)), equity_full, color="#00d4ff", linewidth=1.8,
                label="Zinc HAM (full)")
        split_i = int(np.searchsorted([str(idx[i].date()) for i in range(len(idx))],
                                       str(SPLIT.date())))
        if 0 < split_i < len(equity_full):
            ax.axvline(split_i, color="#ff5555", linestyle="--", linewidth=1,
                       label="OOS split")
        ax.axhline(1.0, color="#666", linewidth=0.8, linestyle="--")
        ax.set_title("exp427 Zinc HAM-T1+ADX: Equity (9:01+10bp)", color="white", fontsize=13)
        ax.legend(facecolor="#2a2a3e", edgecolor="#444", labelcolor="white", fontsize=9)
        ax.tick_params(colors="white")
        for s in ax.spines.values():
            s.set_color("#444")
        plt.tight_layout()
        plt.savefig(os.path.join(DATA_DIR, "exp427_equity_curves.png"), dpi=110, facecolor="#1a1a2e")
        plt.close()
        print("  ✓ data/exp427_equity_curves.png")
    except Exception as e:
        print(f"  [warn] 绘图失败: {e}")

    # 7. 跨品种复用性判定
    print("\n[7] 跨品种复用性判定")
    zn_full_calmar = mi_full["calmar"]
    zn_oos_calmar = results.get("样本外(测试)", {}).get("calmar", 0)
    zn_oos_annual = results.get("样本外(测试)", {}).get("annual_return", 0)
    if zn_oos_annual > 0 and zn_full_calmar > 1.5:
        verdict = "TRANSFERABLE"
        reason = (f"锌样本外年化{zn_oos_annual*100:.1f}%为正、全样本Calmar{zn_full_calmar:.2f}>1.5，"
                  f"HAM异质主体框架可跨品种复用")
    elif zn_full_calmar > 1.0:
        verdict = "PARTIALLY_TRANSFERABLE"
        reason = (f"锌全样本Calmar{zn_full_calmar:.2f}>1但样本外年化{zn_oos_annual*100:.1f}%，"
                  f"框架部分可迁移但收益衰减明显")
    else:
        verdict = "NOT_TRANSFERABLE"
        reason = (f"锌Calmar{zn_full_calmar:.2f}≤1，HAM框架在锌未有效迁移，"
                  f"可能锌的投机-产业博弈结构与LC不同")
    print(f"  判定: {verdict}")
    print(f"  {reason}")

    # 8. 汇总JSON
    summary = {
        "config": {"variety": "锌(Zn)", "symbol": "zn0", "start": str(START.date()),
                   "end": str(END.date()), "split": str(SPLIT.date()),
                   "main_slip_bp": MAIN_SLIP_BP, "rng_seed": RNG_SEED,
                   "n_days": int(len(idx)), "adx_threshold": ADX_THRESHOLD,
                   "adx_scale": ADX_SCALE, "n_adx_days": n_adx_days},
        "full_sample": {**mi_full, "ic": ic_full["ic"], "ic_rank": ic_full["ic_rank"]},
        "in_out_sample": {k: v for k, v in results.items()},
        "lc_benchmark": lc_benchmark,
        "compare": compare_df.to_dict(orient="records"),
        "verdict": verdict, "verdict_reason": reason,
    }
    with open(os.path.join(DATA_DIR, "exp427_summary.json"), "w") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n  ✓ 汇总JSON: data/exp427_summary.json")
    print("\n✅ exp427 跨品种泛化测试完成")
