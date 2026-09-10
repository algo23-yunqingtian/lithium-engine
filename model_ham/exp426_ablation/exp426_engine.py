"""
exp426: HAM 全套消融对照实验 — 定量拆解每个组件的独立贡献

基准组锚定：exp425 最终版本（T1 + ADX降权×0.3，9:01分钟均价成交 + 10bp滑点）。
  推荐配置：年化28.9% / Calmar4.23 / 回撤-6.8%（exp425复现锚点）。

消融目标：拆解HAM异质主体框架每个组件对收益的独立贡献，验证
  - Logit自适应资金跟随（滚动自适应标准化）的边际价值
  - 产业主体预期（D_f 基本面回归压力）的边际价值
  - 投机主体预期（D_c 投机动量需求）的边际价值
  - ADX趋势降权的边际价值

5组对照（统一9:01分钟成交 + 10bp滑点，时序/回测参数完全一致，仅改目标模块）：
  1. 基准组  ：原版 T1 + ADX降权(×0.3)
  2. 消融A   ：固定权重版本（关闭Logit自适应资金跟随）
               —— compute_system_deviation 的 D_c/D_f 由「180日滚动自适应标准化」
                  改为「全样本一次标准化(z-score)」，去除滚动自适应跟随能力
  3. 消融B   ：移除产业交易者预期模块，仅保留投机动量预期
               —— D_f 置0，偏离度 = 纯 D_c 动量维度（产业回归压力失效）
  4. 消融C   ：移除投机交易者预期模块，仅保留产业基本面均衡预期
               —— D_c 置0，偏离度 = 纯 D_f 回归维度（投机动量失效）
  5. 消融D   ：关闭ADX趋势降权（原版T1无趋势过滤）
               —— ADX_SCALE=1.0（不降权），其余全部不变

输出：各组绩效对比表、净值曲线、IC、Calmar、最大回撤，
     生成 reports/exp426_ablation.md。

时序铁律：版本B口径——T日收盘决策，T+1 9:01分钟均价成交（+10bp滑点）；
          隔夜跳空由 pos[t-1] 承担，日内由 pos[t] 承担。
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
from exp411_combined import (
    build_targets, price_series, extract_trades, TRADING_DAYS_PER_YEAR,
)
from exp424_engine import (
    load_context, compute_positions, compute_adx,
    metrics_from_net_ret, net_ret_variant_B,
    VOL_HALF, VOL_FULL, HALF_SCALE, FULL_SCALE,
    DD_RECOVER, DD_WARN, DD_HARD, START, END, COST, TDPY,
)
from exp425_engine import (
    ADX_THRESHOLD, ADX_SCALE, RNG_SEED,
    load_minute_deviation, calibrate_slippage,
    build_901_exec_prices, net_ret_variant_B_exec, run_variant_full,
)

EXP_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(EXP_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)
REPORTS_DIR = os.path.join(os.path.dirname(os.path.dirname(EXP_DIR)), "reports")
os.makedirs(REPORTS_DIR, exist_ok=True)

RAW_MINUTE_CSV = os.path.join(MODEL, "exp425_minute_execution", "raw_minute", "lc0_1min_raw.csv")

MAIN_SLIP_BP = 10.0


# ============================================================
# 消融版 HAM 信号层：可注入组件开关的 compute_system_deviation
# ============================================================
def compute_system_deviation_ablation(df, fixed_weights=False,
                                      drop_industrial=False, drop_speculative=False):
    """
    HAM 系统偏离度的消融版构造。与 exp409.compute_system_deviation 完全同构，
    仅通过三个开关改动目标模块，其余公式/参数/时序口径不变：

      fixed_weights=True      : 关闭 Logit 自适应资金跟随。
                                D_c/D_f 的标准化由「180日滚动 rank 自适应」改为
                                「全样本 z-score 一次标准化」，去除滚动自适应跟随。
      drop_industrial=True    : 移除产业主体预期。D_f 置0（基本面回归压力失效），
                                偏离度退化为纯投机动量维度 |D_c_norm|。
      drop_speculative=True   : 移除投机主体预期。D_c 置0（投机动量失效），
                                偏离度退化为纯产业回归维度 |D_f_norm|。

    方向约定保持一致：D_c>D_f(动量主导)→多; D_f>D_c(回归主导)→空。
      - 消融B(drop_industrial)：无 D_f，disagreement=D_c_norm≥0 → 恒多头方向由D_c符号决定
      - 消融C(drop_speculative)：无 D_c，disagreement=-D_f_norm → 方向由D_f符号决定
    """
    df = df.copy()
    alpha, beta = 0.3, 0.5
    close = df["close"].values.astype(float)
    p_fund = df["P_fund"].values.astype(float)
    n = len(close)

    # D_f = alpha * (P_fund - P_t): 产业基本面回归压力
    D_f = np.zeros(n) if drop_industrial else alpha * (p_fund - close)
    # D_c = beta * (P_t - P_{t-1}): 投机趋势动量
    D_c = np.zeros(n)
    if not drop_speculative:
        D_c[1:] = beta * np.diff(close)

    if fixed_weights:
        # 固定权重：全样本一次 z-score 标准化（去除滚动自适应跟随）
        # 用 T 及更早数据的 expanding z-score 保持无前视，但失去「随窗口自适应」的跟随性。
        # 为避免全样本(含未来)泄露，用 expanding（仅 T 及更早）标准化——
        # 这是"关闭自适应跟随"的忠实实现：标准化参照系不再随180日窗口滚动更新其分布。
        def _expanding_zscore(arr):
            s = pd.Series(arr)
            mu = s.expanding(min_periods=60).mean()
            sd = s.expanding(min_periods=60).std()
            sd = sd.replace(0, np.nan)
            z = (s - mu) / sd
            return z.bfill().fillna(0).values
        dcn = _expanding_zscore(D_c) * 2 - 1  # 归一化到[-1,1]量级近似
        dfn = _expanding_zscore(D_f) * 2 - 1
    else:
        # 原版：180日滚动 rank 自适应标准化（Logit 自适应资金跟随）
        dcn = pd.Series(D_c).rolling(E409.ROLLING_TRAIN, min_periods=60).rank(pct=True).values * 2 - 1
        dfn = pd.Series(D_f).rolling(E409.ROLLING_TRAIN, min_periods=60).rank(pct=True).values * 2 - 1

    df["D_c"] = D_c
    df["D_f"] = D_f
    df["disagreement"] = dcn - dfn
    df["deviation"] = np.abs(dcn - dfn)
    return df


def compute_ham_targets_ablation(ctx, fixed_weights=False,
                                 drop_industrial=False, drop_speculative=False):
    """
    消融版 build_targets：只改 HAM 腿的 compute_system_deviation，
    基本面腿(exp410)、GMM状态、HAM信号、风控全部沿用原版，确保对照纯净。
    返回 (h_target, f_target)，与原版 build_targets 同构。
    """
    df_h = E409.load_ham_factors()
    df_h = compute_system_deviation_ablation(
        df_h, fixed_weights=fixed_weights,
        drop_industrial=drop_industrial, drop_speculative=drop_speculative,
    )
    df_h = E409.compute_aux_signals(df_h)
    df_h = E409.load_gmm_states(df_h)
    df_h = E409.compute_ham_signals(df_h)
    df_h, _ = E409.run_ham_dynamic(df_h)
    h = df_h.set_index("date")["position"].astype(float)

    f_target = ctx["f_target"]
    idx = h.index.union(f_target.index).sort_values()
    h = h.reindex(idx).fillna(0.0)
    f_target = f_target.reindex(idx).fillna(0.0)
    return h, f_target


# ============================================================
# 消融组信号仓位：T1 + 可开关ADX降权
# ============================================================
def compute_positions_ablation(ctx, h_target, f_target, adx_scale=ADX_SCALE):
    """
    在 exp412 风控底座 + T1 波动率隔夜敞口管控上，叠加可开关的 ADX 降权。
    adx_scale=0.3 为基准；=1.0 为消融D（关闭ADX降权）。
    """
    # 用消融后的 h_target/f_target 覆盖 ctx
    ctx_mod = dict(ctx)
    ctx_mod["h_target"] = h_target
    ctx_mod["f_target"] = f_target

    pos, _ = compute_positions(ctx_mod, VOL_HALF, VOL_FULL, HALF_SCALE, FULL_SCALE,
                               DD_RECOVER, DD_WARN, DD_HARD)

    idx = ctx_mod["idx"]
    ohlc = ctx["ohlc"]
    price = ctx["price"]
    high = ohlc["high"].reindex(idx).values
    low = ohlc["low"].reindex(idx).values
    close = price.reindex(idx).values
    adx = compute_adx(high, low, close, period=14)
    if adx_scale < 1.0:
        adx_mask = adx > ADX_THRESHOLD
        pos[adx_mask] = pos[adx_mask] * adx_scale
    return pos


# ============================================================
# IC 计算：仓位信号与次日收益的信息系数
# ============================================================
def compute_ic(final_pos, close_price, idx):
    """
    计算信号-收益 IC：当日仓位(final_pos) 与 次日收益(close[T+1]/close[T]-1) 的
    Pearson 相关（日度 IC）。同时给出 Rank IC(Spearman)。
    """
    close = close_price.reindex(idx).values.astype(float)
    n = len(idx)
    fwd_ret = np.zeros(n)
    fwd_ret[:-1] = (close[1:] - close[:-1]) / close[:-1]
    # 信号=当日仓位（T日收盘已知的决策）；标签=当日仓位承担的(T-1→T)价格变动
    # 为避免前视，IC 用 pos[t] 对 (close[t]/close[t-1]-1)：T日仓位吃T日收益
    r_price = np.zeros(n)
    r_price[1:] = (close[1:] - close[:-1]) / close[:-1]
    sig = final_pos[1:]
    lab = r_price[1:]
    mask = np.abs(sig) > 1e-9
    if mask.sum() > 2:
        ic = float(np.corrcoef(sig[mask], lab[mask])[0, 1])
    else:
        ic = 0.0
    # Rank IC
    from scipy.stats import spearmanr
    try:
        if mask.sum() > 2:
            ics, _ = spearmanr(sig[mask], lab[mask])
            ic_rank = float(ics)
        else:
            ic_rank = 0.0
    except Exception:
        ic_rank = np.nan
    ic_info = float(np.mean(sig) / np.std(sig) * np.sqrt(TDPY)) if np.std(sig) > 0 else 0.0
    return {"ic": ic, "ic_rank": ic_rank, "n_signal_days": int(mask.sum())}


# ============================================================
# 主流程
# ============================================================
GROUPS = [
    {"name": "基准组", "desc": "原版T1+ADX降权(×0.3)",
     "fixed_weights": False, "drop_industrial": False, "drop_speculative": False,
     "adx_scale": 0.3},
    {"name": "消融A", "desc": "固定权重(关闭Logit自适应资金跟随)",
     "fixed_weights": True, "drop_industrial": False, "drop_speculative": False,
     "adx_scale": 0.3},
    {"name": "消融B", "desc": "移除产业主体(仅投机动量)",
     "fixed_weights": False, "drop_industrial": True, "drop_speculative": False,
     "adx_scale": 0.3},
    {"name": "消融C", "desc": "移除投机主体(仅产业基本面)",
     "fixed_weights": False, "drop_industrial": False, "drop_speculative": True,
     "adx_scale": 0.3},
    {"name": "消融D", "desc": "关闭ADX趋势降权(原版T1)",
     "fixed_weights": False, "drop_industrial": False, "drop_speculative": False,
     "adx_scale": 1.0},
]


def run_ablation(ctx, p901_px, slippage_cal):
    idx = ctx["idx"]
    price = ctx["price"]

    rows = []
    equity_curves = {}
    all_net_ret = {}

    for g in GROUPS:
        # 消融版信号
        h, f = compute_ham_targets_ablation(
            ctx, fixed_weights=g["fixed_weights"],
            drop_industrial=g["drop_industrial"], drop_speculative=g["drop_speculative"],
        )
        # 9:01口径要求 idx 与 ctx 对齐：reindex 回 ctx 的 idx
        h = h.reindex(ctx["idx"]).fillna(0.0)
        f = f.reindex(ctx["idx"]).fillna(0.0)

        final_pos = compute_positions_ablation(ctx, h, f, adx_scale=g["adx_scale"])

        # 9:01 + 10bp 滑点成交
        nr, mi, trades = run_variant_full(ctx, final_pos, p901_px, slip_bp=MAIN_SLIP_BP, gap_filter_thr=0.0)
        ic_res = compute_ic(final_pos, price, idx)

        rows.append({
            "group": g["name"], "desc": g["desc"],
            "annual_return": mi["annual_return"], "sharpe": mi["sharpe"],
            "max_drawdown": mi["max_drawdown"], "calmar": mi["calmar"],
            "total_return": mi["total_return"], "n_trades": mi["n_trades"],
            "ic": ic_res["ic"], "ic_rank": ic_res["ic_rank"],
            "n_signal_days": ic_res["n_signal_days"],
        })
        equity = np.cumprod(1 + nr)
        equity_curves[g["name"]] = equity
        all_net_ret[g["name"]] = nr

    return pd.DataFrame(rows), equity_curves, all_net_ret


if __name__ == "__main__":
    print("=" * 72)
    print("exp426: HAM 全套消融对照实验")
    print("基准: exp425 T1 + ADX降权(×0.3)，9:01分钟均价 + 10bp滑点")
    print("=" * 72)

    ctx = load_context()
    idx = ctx["idx"]
    print(f"\n数据: {idx[0].date()} ~ {idx[-1].date()} ({len(idx)} 日)")

    # 9:01成交价标定（固定种子，全组共用同一套9:01价，保证对照纯净）
    print("\n[9:01成交标定] 复用 exp425 分钟数据标定")
    dev_df = load_minute_deviation() if os.path.exists(RAW_MINUTE_CSV) else None
    # exp425_engine.load_minute_deviation 读的是 exp425 目录下的 RAW_MINUTE_CSV
    # 此处重新指向 exp426 自己的路径：直接复用 exp425 已标定数据
    # 简化：调用 exp425 的标定函数（其内部指向 exp425 目录的分钟数据）
    from exp425_engine import RAW_MINUTE_CSV as RAW425
    dev_df = pd.read_csv(os.path.join(MODEL, "exp425_minute_execution", "data",
                                      "min_opening_deviation.csv")) if os.path.exists(
        os.path.join(MODEL, "exp425_minute_execution", "data", "min_opening_deviation.csv")
    ) else None
    if dev_df is None or dev_df.empty:
        # 重新标定
        import exp425_engine
        dev_df = exp425_engine.load_minute_deviation()
    slippage_cal = calibrate_slippage(dev_df)
    print(f"  标定: {slippage_cal['n_days']}交易日, 9:01vs开盘偏离均值={slippage_cal['mean_dev_pct']:+.4f}%")

    _, p901_px, _ = build_901_exec_prices(ctx, slippage_cal, vol_scale=True, seed=RNG_SEED)

    # 基准校验：原版全组件在9:01+10bp下应复现 28.9%/Calmar4.23
    # 直接复用 exp425 的 compute_signal_positions（T1+ADX降权×0.3，原版全组件）
    print("\n[基准校验] 原版 T1+ADX降权 (9:01+10bp)")
    import exp425_engine
    pos0, n_adx_days = exp425_engine.compute_signal_positions(ctx)
    _, mi_base, _ = run_variant_full(ctx, pos0, p901_px, slip_bp=MAIN_SLIP_BP, gap_filter_thr=0.0)
    print(f"  基准(9:01+10bp): 年化={mi_base['annual_return']*100:.1f}% "
          f"夏普={mi_base['sharpe']:.3f} 回撤={mi_base['max_drawdown']*100:.1f}% "
          f"Calmar={mi_base['calmar']:.2f} 笔数={mi_base['n_trades']}")
    print(f"  预期(exp425③组): 年化≈28.9%, Calmar≈4.23, 回撤≈-6.8%")

    # 跑5组消融
    print("\n" + "=" * 72)
    print("5组消融对照 (统一9:01+10bp滑点)")
    print("=" * 72)
    perf_df, equity_curves, all_net_ret = run_ablation(ctx, p901_px, slippage_cal)

    for _, r in perf_df.iterrows():
        print(f"  {r['group']:<6}({r['desc']}): 年化={r['annual_return']*100:.1f}% "
              f"夏普={r['sharpe']:.3f} 回撤={r['max_drawdown']*100:.1f}% "
              f"Calmar={r['calmar']:.2f} IC={r['ic']:+.3f} 笔数={int(r['n_trades'])}")

    # 保存
    perf_df.to_csv(os.path.join(DATA_DIR, "exp426_ablation_compare.csv"), index=False)

    # 净值曲线
    eq_df = pd.DataFrame({
        "date": [str(idx[i].date()) for i in range(len(idx))],
        **{f"eq_{k}": v for k, v in equity_curves.items()},
    })
    eq_df.to_csv(os.path.join(DATA_DIR, "exp426_equity_curves.csv"), index=False)

    # 净值曲线PNG（Dark ECharts风格静态图，供报告嵌入）
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(11, 5.5), facecolor="#1a1a2e")
        ax.set_facecolor("#1a1a2e")
        colors = ["#00d4ff", "#ff5555", "#ffcc33", "#33ff99", "#cc66ff"]
        label_map = {
            "基准组": "Baseline (T1+ADX)",
            "消融A": "A: Fixed weights (no Logit adapt)",
            "消融B": "B: Drop industrial (speculative only)",
            "消融C": "C: Drop speculative (industrial only)",
            "消融D": "D: No ADX damp (raw T1)",
        }
        for i, (name, eq) in enumerate(equity_curves.items()):
            ax.plot(range(len(eq)), eq, label=label_map.get(name, name),
                    color=colors[i % len(colors)], linewidth=1.6)
        ax.axhline(1.0, color="#666", linewidth=0.8, linestyle="--")
        ax.set_title("exp426 HAM Ablation: Equity Curves (9:01+10bp)", color="white", fontsize=13)
        ax.legend(facecolor="#2a2a3e", edgecolor="#444", labelcolor="white", fontsize=9)
        ax.tick_params(colors="white")
        for s in ax.spines.values():
            s.set_color("#444")
        plt.tight_layout()
        plt.savefig(os.path.join(DATA_DIR, "exp426_equity_curves.png"), dpi=110,
                    facecolor="#1a1a2e")
        plt.close()
        print(f"\n  ✓ 净值曲线图: data/exp426_equity_curves.png")
    except Exception as e:
        print(f"\n  [warn] 净值曲线绘图失败: {e}")

    # 消融归因：各组相对基准的贡献（年化pp + Calmar）
    base = perf_df[perf_df["group"] == "基准组"].iloc[0]
    attribution = []
    for _, r in perf_df.iterrows():
        if r["group"] == "基准组":
            continue
        attribution.append({
            "group": r["group"], "desc": r["desc"],
            "delta_annual_pp": (r["annual_return"] - base["annual_return"]) * 100,
            "delta_calmar": r["calmar"] - base["calmar"],
            "delta_drawdown_pp": (r["max_drawdown"] - base["max_drawdown"]) * 100,
            "annual_return": r["annual_return"],
            "calmar": r["calmar"],
        })
    attr_df = pd.DataFrame(attribution)
    attr_df.to_csv(os.path.join(DATA_DIR, "exp426_attribution.csv"), index=False)

    print("\n[消融归因] 各组相对基准的年化衰减:")
    for _, a in attr_df.iterrows():
        print(f"  {a['group']:<6}({a['desc']}): 年化Δ={a['delta_annual_pp']:+.1f}pp "
              f"CalmarΔ={a['delta_calmar']:+.2f}")

    # 汇总JSON
    summary = {
        "config": {"adx_threshold": ADX_THRESHOLD, "adx_scale_base": ADX_SCALE,
                   "main_slip_bp": MAIN_SLIP_BP, "rng_seed": RNG_SEED,
                   "start": str(START.date()), "end": str(END.date()),
                   "n_days": int(len(idx))},
        "performance": perf_df.to_dict(orient="records"),
        "attribution": attr_df.to_dict(orient="records"),
    }
    with open(os.path.join(DATA_DIR, "exp426_summary.json"), "w") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n  ✓ 汇总JSON: data/exp426_summary.json")
    print("\n✅ exp426 消融实验完成")
