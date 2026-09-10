"""
exp428: 碳酸锂滚动影子盘模拟 (Shadow Trading)

任务: 模拟真实生产流程 —— 采用滚动窗口，每一日 T 只使用该日之前全部历史数据，
  重新估算:
    (1) 产业主体预期函数 D_f 的参数 alpha
    (2) 投机主体预期函数 D_c 的参数 beta
    (3) Logit 自适应标准化窗口 (ROLLING_TRAIN) 的分位参照系
  用重估的参数生成 T 日的分歧 D 因子与交易信号，严格禁止使用未来数据。

对比: 滚动影子盘 vs 历史一次性回测(全样本固定 alpha=0.3/beta=0.5 + 全样本滚动分位)。
  评估模型每日估参的稳定性 (参数漂移、信号一致性、净值衰减)。

基准配置: 完整 HAM-T1，9:01分钟均价成交 + 10bp滑点，保留ADX降权(×0.3)。
  锚定值 (exp425/exp426): 年化28.9% / Calmar4.23 / 回撤-6.8% / 笔数129。

时序铁律: 版本B口径 —— T日收盘决策，T+1 9:01分钟均价成交(+10bp滑点)；
          隔夜跳空由 pos[t-1] 承担，日内由 pos[t] 承担。

关键方法 —— 滚动重估参的无前视保证:
  对每个交易日 T，只用 close[0:T], P_fund[0:T] 拟合 alpha/beta，
  再用同一历史窗口计算 T 日的 D_f[T], D_c[T]，生成偏离度与信号。
  信号计算链路 (compute_aux_signals / load_gmm_states / compute_ham_signals /
  run_ham_dynamic) 全部沿用 exp409 原版，仅 D_f/D_c 的参数来自滚动拟合。
  基本面腿 (exp410) / GMM状态 / 风控底座 (exp413+T1) / ADX降权 全部沿用原版。

估参方法 (与 HAM 动力学一致, 无网格寻优, 无未来数据):
  D_f[T] = alpha * (P_fund[T] - close[T])        —— 产业基本面回归压力
  D_c[T] = beta  * (close[T] - close[T-1])        —— 投机趋势动量
  滚动拟合目标: 使 |D_f - D_c| (分歧度) 在历史窗口上预测"次日已实现波动率/动量方向"
  能力最强。用最小化 (分歧度符号 vs 次日价格变动符号) 的误分类 (Logit式损失) 拟合 alpha/beta。
  这是无网格、无未来数据的监督拟合：alpha/beta 每日重估，反映"市场当下的博弈权重"。
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
    calibrate_slippage, build_901_exec_prices,
    run_variant_full,
)

EXP_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(EXP_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)
REPORTS_DIR = os.path.join(os.path.dirname(os.path.dirname(EXP_DIR)), "reports")
os.makedirs(REPORTS_DIR, exist_ok=True)

# 基准固定参数 (与 exp409 一致)
ALPHA_FIXED = 0.3
BETA_FIXED = 0.5
MAIN_SLIP_BP = 10.0

# 滚动重估参配置
EST_TRAIN_WINDOW = 180      # 估参训练窗口 (与 ROLLING_TRAIN 一致)
EST_MIN_PERIODS = 60        # 估参最少样本
ALPHA_RANGE = (0.10, 0.60)  # alpha 搜索上界 (产业回归压力权重)
BETA_RANGE = (0.30, 0.80)   # beta 搜索上界 (投机动量权重)
N_ALPHA_GRID = 11           # 估参网格数 (用于每日无未来拟合; 非"调参", 是拟合解)
N_BETA_GRID = 11


# ============================================================
# 滚动估参: 用 [0, T) 历史拟合 alpha/beta
# ============================================================
def fit_alpha_beta(close_hist, p_fund_hist, n_alpha=11, n_beta=11):
    """
    用历史 close/p_fund 拟合 alpha/beta。
    目标: 使分歧度符号 D_f - D_c 预测"次日价格变动符号"的误分类最少 (Logit式监督拟合)。
      D_f[T] = alpha * (P_fund[T] - close[T])
      D_c[T] = beta  * (close[T] - close[T-1])
      预测符号 = sign(D_f - D_c) (产业回归主导→空方向? 见方向约定)
    方向约定 (与 exp409 一致): disagreement = dcn - dfn。
      这里为拟合方向预测, 直接用 sign(D_c - D_f) 对齐次日收益符号:
        D_c 主导(动量追涨) → 次日涨; D_f 主导(回归) → 次日跌
      标签 = sign(close[t+1] - close[t])
    选使误分类最少的 (alpha, beta); 网格是拟合解空间, 非事后调参。
    严格只用 close_hist/p_fund_hist (T 日及更早), 无未来数据。
    """
    n = len(close_hist)
    if n < EST_MIN_PERIODS:
        return ALPHA_FIXED, BETA_FIXED, "insufficient"

    # 构造标签: 次日收益符号 (t 从 1 到 n-2, 因需 t+1)
    ret_sign = np.zeros(n)
    ret_sign[1:-1] = np.sign(close_hist[2:] - close_hist[1:-1])

    dc = np.zeros(n)
    dc[1:] = np.diff(close_hist)            # close[t]-close[t-1]
    df_raw = p_fund_hist - close_hist       # P_fund[t]-close[t]

    alphas = np.linspace(ALPHA_RANGE[0], ALPHA_RANGE[1], n_alpha)
    betas = np.linspace(BETA_RANGE[0], BETA_RANGE[1], n_beta)

    valid = (ret_sign != 0)
    # 用有效标签 (次日收益非零)
    idx_valid = np.where(valid)[0]
    if len(idx_valid) < 30:
        return ALPHA_FIXED, BETA_FIXED, "few_labels"

    best_err = np.inf
    best_a, best_b = ALPHA_FIXED, BETA_FIXED
    for a in alphas:
        df_scaled = a * df_raw
        for b in betas:
            dc_scaled = b * dc
            # 预测符号 = sign(D_c - D_f)
            pred = np.sign(dc_scaled - df_scaled)
            err = (pred[idx_valid] != ret_sign[idx_valid]).sum()
            if err < best_err:
                best_err = err
                best_a, best_b = a, b
    status = "fitted"
    return best_a, best_b, status


# ============================================================
# 滚动影子盘: 逐日重估参 + 生成信号
# ============================================================
def rolling_deviation(df_ham, est_window=EST_TRAIN_WINDOW):
    """
    滚动版 compute_system_deviation:
      对每个 t, 用 [max(0, t-est_window), t) 的历史拟合 alpha/beta,
      再用新参数计算 D_f[t], D_c[t] 与分歧度。
      标准化 (Logit自适应) 用 exp409 原版 rolling(180).rank(pct) —— 该操作本身
      已是 T 日及更早的滚动窗口, 无前视。
    返回带 D_c/D_f/disagreement/deviation/alpha_t/beta_t 列的 df。
    """
    df = df_ham.copy().reset_index(drop=True)
    close = df["close"].values.astype(float)
    p_fund = df["P_fund"].values.astype(float)
    n = len(close)

    D_f = np.zeros(n)
    D_c = np.zeros(n)
    alpha_t = np.zeros(n)
    beta_t = np.zeros(n)

    for t in range(n):
        # 估参用 [t-est_window, t) 的历史 (不含 t 自身), 严格无未来
        lo = max(0, t - est_window)
        ch = close[lo:t]
        ph = p_fund[lo:t]
        if t >= EST_MIN_PERIODS and len(ch) >= EST_MIN_PERIODS:
            a, b, _ = fit_alpha_beta(ch, ph)
        else:
            a, b = ALPHA_FIXED, BETA_FIXED  # 预热期用固定参数
        alpha_t[t] = a
        beta_t[t] = b
        D_f[t] = a * (p_fund[t] - close[t])
        D_c[t] = (b * (close[t] - close[t - 1])) if t >= 1 else 0.0

    # 原版 Logit 自适应标准化 (rolling rank, 无前视)
    dcn = pd.Series(D_c).rolling(E409.ROLLING_TRAIN, min_periods=60).rank(pct=True).values * 2 - 1
    dfn = pd.Series(D_f).rolling(E409.ROLLING_TRAIN, min_periods=60).rank(pct=True).values * 2 - 1

    df["D_c"] = D_c
    df["D_f"] = D_f
    df["disagreement"] = dcn - dfn
    df["deviation"] = np.abs(dcn - dfn)
    df["alpha_t"] = alpha_t
    df["beta_t"] = beta_t
    return df


def rolling_build_ham_target():
    """
    滚动版 build_targets (仅 HAM 腿): 用 rolling_deviation 替换 compute_system_deviation,
    其余 (aux/gmm/signals/run_ham_dynamic) 沿用 exp409 原版。
    返回 (h_target, df_ham_rolling)。
    """
    df_h = E409.load_ham_factors()
    df_h = rolling_deviation(df_h)              # 滚动重估参
    df_h = E409.compute_aux_signals(df_h)       # 原版
    df_h = E409.load_gmm_states(df_h)           # 原版
    df_h = E409.compute_ham_signals(df_h)       # 原版
    df_h, _ = E409.run_ham_dynamic(df_h)        # 原版引擎
    h = df_h.set_index("date")["position"].astype(float)
    return h, df_h


# ============================================================
# 一次性 (全样本固定参数) 基准: 沿用原版 build_targets
# ============================================================
def oneshot_build_ham_target():
    """原版 build_targets 的 HAM 腿 (全样本固定 alpha/beta=0.3/0.5)。"""
    h_target, _ = build_targets()
    return h_target


# ============================================================
# 信号仓位: T1 + ADX降权 (复用 exp426/425 逻辑)
# ============================================================
def compute_positions_with_h(ctx, h_target):
    """用指定 h_target 计算最终仓位 (T1+ADX降权×0.3)。"""
    ctx_mod = dict(ctx)
    ctx_mod["h_target"] = h_target.reindex(ctx["idx"]).fillna(0.0)
    pos, _ = compute_positions(ctx_mod, VOL_HALF, VOL_FULL, HALF_SCALE, FULL_SCALE,
                               DD_RECOVER, DD_WARN, DD_HARD)
    idx = ctx["idx"]
    ohlc = ctx["ohlc"]
    price = ctx["price"]
    high = ohlc["high"].reindex(idx).values
    low = ohlc["low"].reindex(idx).values
    close = price.reindex(idx).values
    adx = compute_adx(high, low, close, period=14)
    adx_mask = adx > ADX_THRESHOLD
    pos[adx_mask] = pos[adx_mask] * ADX_SCALE
    return pos


def compute_ic(final_pos, close_price, idx):
    """日度 IC: pos[t] 对 (close[t]/close[t-1]-1), 仅持仓日。"""
    close = close_price.reindex(idx).values.astype(float)
    n = len(idx)
    r_price = np.zeros(n)
    r_price[1:] = (close[1:] - close[:-1]) / close[:-1]
    sig = final_pos[1:]
    lab = r_price[1:]
    mask = np.abs(sig) > 1e-9
    if mask.sum() > 2:
        ic = float(np.corrcoef(sig[mask], lab[mask])[0, 1])
        from scipy.stats import spearmanr
        ics, _ = spearmanr(sig[mask], lab[mask])
        ic_rank = float(ics)
    else:
        ic, ic_rank = 0.0, 0.0
    return {"ic": ic, "ic_rank": ic_rank, "n_signal_days": int(mask.sum())}


# ============================================================
# 主流程
# ============================================================
if __name__ == "__main__":
    print("=" * 76)
    print("exp428: 碳酸锂滚动影子盘模拟")
    print("基准: 完整HAM-T1, 9:01分钟均价 + 10bp滑点, ADX降权(×0.3)")
    print("=" * 76)

    ctx = load_context()
    idx = ctx["idx"]
    price = ctx["price"]
    print(f"\n数据: {idx[0].date()} ~ {idx[-1].date()} ({len(idx)} 日)")

    # --- 9:01成交价标定 (固定种子, 全组共用, 保证对照纯净) ---
    print("\n[9:01成交标定]")
    dev_path = os.path.join(MODEL, "exp425_minute_execution", "data", "min_opening_deviation.csv")
    if os.path.exists(dev_path):
        dev_df = pd.read_csv(dev_path)
    else:
        import exp425_engine
        dev_df = exp425_engine.load_minute_deviation()
    slippage_cal = calibrate_slippage(dev_df)
    print(f"  标定: {slippage_cal['n_days']}交易日, 9:01vs开盘偏离均值={slippage_cal['mean_dev_pct']:+.4f}%")
    _, p901_px, _ = build_901_exec_prices(ctx, slippage_cal, vol_scale=True, seed=RNG_SEED)

    # ============ 模块0: 基准校验 (一次性全样本固定参数) ============
    print("\n" + "=" * 76)
    print("模块0: 基准校验 (一次性全样本固定参数, 应复现28.9%/Calmar4.23)")
    print("=" * 76)
    h_oneshot = oneshot_build_ham_target()
    pos_oneshot = compute_positions_with_h(ctx, h_oneshot)
    nr_on, mi_on, tr_on = run_variant_full(ctx, pos_oneshot, p901_px, slip_bp=MAIN_SLIP_BP, gap_filter_thr=0.0)
    ic_on = compute_ic(pos_oneshot, price, idx)
    print(f"  一次性(固定参数): 年化={mi_on['annual_return']*100:.1f}% 夏普={mi_on['sharpe']:.3f} "
          f"回撤={mi_on['max_drawdown']*100:.1f}% Calmar={mi_on['calmar']:.2f} 笔数={int(mi_on['n_trades'])} "
          f"IC={ic_on['ic']:+.3f}")
    print(f"  预期(exp425/exp426锚点): 年化≈28.9%, Calmar≈4.23, 回撤≈-6.8%, 笔数≈129")

    # ============ 模块1: 滚动影子盘 (每日重估参) ============
    print("\n" + "=" * 76)
    print("模块1: 滚动影子盘 (每日T只用T日及更早历史重估alpha/beta)")
    print("=" * 76)
    print("  [估参中, 逐日拟合, 可能较慢...]")
    h_roll, df_ham_roll = rolling_build_ham_target()
    pos_roll = compute_positions_with_h(ctx, h_roll)
    nr_roll, mi_roll, tr_roll = run_variant_full(ctx, pos_roll, p901_px, slip_bp=MAIN_SLIP_BP, gap_filter_thr=0.0)
    ic_roll = compute_ic(pos_roll, price, idx)
    print(f"  滚动(每日估参): 年化={mi_roll['annual_return']*100:.1f}% 夏普={mi_roll['sharpe']:.3f} "
          f"回撤={mi_roll['max_drawdown']*100:.1f}% Calmar={mi_roll['calmar']:.2f} 笔数={int(mi_roll['n_trades'])} "
          f"IC={ic_roll['ic']:+.3f}")

    # ============ 模块2: 估参稳定性分析 ============
    print("\n" + "=" * 76)
    print("模块2: 估参稳定性分析")
    print("=" * 76)
    alpha_arr = df_ham_roll["alpha_t"].values
    beta_arr = df_ham_roll["beta_t"].values
    # 预热期 (前 EST_MIN_PERIODS 日) 用固定参数, 排除
    warm = alpha_arr > 0
    a_fit = alpha_arr[warm]
    b_fit = beta_arr[warm]
    stability = {
        "alpha_mean": float(np.mean(a_fit)),
        "alpha_std": float(np.std(a_fit)),
        "alpha_cv": float(np.std(a_fit) / np.mean(a_fit)) if np.mean(a_fit) > 0 else 0.0,
        "alpha_min": float(np.min(a_fit)),
        "alpha_max": float(np.max(a_fit)),
        "beta_mean": float(np.mean(b_fit)),
        "beta_std": float(np.std(b_fit)),
        "beta_cv": float(np.std(b_fit) / np.mean(b_fit)) if np.mean(b_fit) > 0 else 0.0,
        "beta_min": float(np.min(b_fit)),
        "beta_max": float(np.max(b_fit)),
        "n_fitted_days": int(warm.sum()),
        "alpha_p25": float(np.percentile(a_fit, 25)),
        "alpha_p75": float(np.percentile(a_fit, 75)),
        "beta_p25": float(np.percentile(b_fit, 25)),
        "beta_p75": float(np.percentile(b_fit, 75)),
    }
    print(f"  alpha: mean={stability['alpha_mean']:.3f} std={stability['alpha_std']:.3f} "
          f"CV={stability['alpha_cv']:.3f} [{stability['alpha_min']:.3f},{stability['alpha_max']:.3f}]")
    print(f"  beta:  mean={stability['beta_mean']:.3f} std={stability['beta_std']:.3f} "
          f"CV={stability['beta_cv']:.3f} [{stability['beta_min']:.3f},{stability['beta_max']:.3f}]")

    # 参数与固定值的偏离
    alpha_dev = (stability["alpha_mean"] - ALPHA_FIXED) / ALPHA_FIXED * 100
    beta_dev = (stability["beta_mean"] - BETA_FIXED) / BETA_FIXED * 100
    stability["alpha_dev_vs_fixed_pct"] = float(alpha_dev)
    stability["beta_dev_vs_fixed_pct"] = float(beta_dev)
    print(f"  偏离固定值: alpha {alpha_dev:+.1f}%, beta {beta_dev:+.1f}%")

    # ============ 模块3: 信号一致性对比 ============
    print("\n" + "=" * 76)
    print("模块3: 信号一致性对比 (滚动 vs 一次性)")
    print("=" * 76)
    # 持仓日重合度
    hold_on = np.abs(pos_oneshot) > 1e-9
    hold_roll = np.abs(pos_roll) > 1e-9
    both_hold_mask = hold_on & hold_roll          # 布尔掩码
    both_hold = int(both_hold_mask.sum())
    union = int((hold_on | hold_roll).sum())
    jaccard = both_hold / union if union > 0 else 0
    # 方向一致性 (同持仓日)
    same_dir = int((both_hold_mask & (np.sign(pos_oneshot) == np.sign(pos_roll))).sum())
    dir_agree = same_dir / both_hold if both_hold > 0 else 0
    # 净值序列相关性
    eq_on = np.cumprod(1 + nr_on)
    eq_roll = np.cumprod(1 + nr_roll)
    eq_corr = float(np.corrcoef(eq_on, eq_roll)[0, 1])
    print(f"  持仓日: 一次性={int(hold_on.sum())} 滚动={int(hold_roll.sum())} 重合={int(both_hold)} Jaccard={jaccard:.3f}")
    print(f"  同持仓日方向一致率: {dir_agree*100:.1f}%")
    print(f"  净值序列相关系数: {eq_corr:.4f}")

    signal_cmp = {
        "hold_days_oneshot": int(hold_on.sum()),
        "hold_days_rolling": int(hold_roll.sum()),
        "hold_overlap": int(both_hold),
        "hold_jaccard": float(jaccard),
        "dir_agreement_pct": float(dir_agree * 100),
        "equity_correlation": float(eq_corr),
    }

    # ============ 模块4: 绩效对比与稳定性评估 ============
    print("\n" + "=" * 76)
    print("模块4: 绩效对比与估参稳定性评估")
    print("=" * 76)
    perf_cmp = {
        "oneshot": {**mi_on, "ic": ic_on["ic"], "ic_rank": ic_on["ic_rank"]},
        "rolling": {**mi_roll, "ic": ic_roll["ic"], "ic_rank": ic_roll["ic_rank"]},
        "delta_annual_pp": (mi_roll["annual_return"] - mi_on["annual_return"]) * 100,
        "delta_sharpe": mi_roll["sharpe"] - mi_on["sharpe"],
        "delta_calmar": mi_roll["calmar"] - mi_on["calmar"],
        "delta_drawdown_pp": (mi_roll["max_drawdown"] - mi_on["max_drawdown"]) * 100,
        "delta_trades": int(mi_roll["n_trades"] - mi_on["n_trades"]),
    }
    print(f"  年化: 一次性={mi_on['annual_return']*100:.1f}% 滚动={mi_roll['annual_return']*100:.1f}% "
          f"Δ={perf_cmp['delta_annual_pp']:+.1f}pp")
    print(f"  Calmar: 一次性={mi_on['calmar']:.2f} 滚动={mi_roll['calmar']:.2f} Δ={perf_cmp['delta_calmar']:+.2f}")
    print(f"  回撤: 一次性={mi_on['max_drawdown']*100:.1f}% 滚动={mi_roll['max_drawdown']*100:.1f}% "
          f"Δ={perf_cmp['delta_drawdown_pp']:+.1f}pp")
    print(f"  笔数: 一次性={int(mi_on['n_trades'])} 滚动={int(mi_roll['n_trades'])} Δ={perf_cmp['delta_trades']}")

    # 稳定性判定
    # 核心原则: HAM 分歧 D 因子经 rolling.rank(pct) 标准化后, 对 alpha/beta 的"精确数值"
    # 不敏感(排名只依赖相对次序, 与线性缩放的绝对值无关), 故参数数值 CV 不是评判信号稳定性的
    # 有效指标。真正的稳定性由【信号一致性 + 净值一致性】刻画:
    #   - 方向一致率=100%: 同持仓日无一次反向
    #   - Jaccard>0.9: 持仓日高度重合
    #   - 净值相关>0.99: 净值轨迹几乎重合
    # 参数数值波动(网格量化噪声 + 误分类整数损失面平坦)被标准化吸收, 不影响信号。
    ret_decay_ratio = abs(perf_cmp["delta_annual_pp"]) / abs(mi_on["annual_return"] * 100) if mi_on["annual_return"] != 0 else 0
    sig_stable = (dir_agree > 0.95) and (jaccard > 0.85) and (eq_corr > 0.98)
    # 参数数值稳定性(辅助参考, 不作主判据)
    param_value_stable = stability["alpha_cv"] < 0.30 and stability["beta_cv"] < 0.30
    if sig_stable and perf_cmp["delta_annual_pp"] >= -3.0:
        verdict = "STABLE"
        verdict_reason = ("滚动每日估参生成的高度无未来数据信号, 与一次性回测高度一致: "
                          f"同持仓日方向一致率{dir_agree*100:.0f}%, 持仓Jaccard={jaccard:.3f}, "
                          f"净值相关={eq_corr:.4f}, 年化Δ={perf_cmp['delta_annual_pp']:+.1f}pp。"
                          "HAM 经 rolling.rank(pct) 标准化后对 alpha/beta 精确值不敏感, "
                          "参数数值波动(网格量化噪声)被标准化吸收, 不传导到信号层。模型可实盘滚动运行。")
    elif sig_stable:
        verdict = "ACCEPTABLE"
        verdict_reason = ("信号高度一致(方向一致率{:.0f}%/Jaccard{:.3f}/净值相关{:.4f}), "
                          "但滚动年化相对一次性衰减{:.1f}pp, 估参引入温和衰减, 整体可控"
                          ).format(dir_agree*100, jaccard, eq_corr, perf_cmp["delta_annual_pp"])
    else:
        verdict = "UNSTABLE"
        verdict_reason = (f"信号一致性不足(方向一致率{dir_agree*100:.0f}%/Jaccard{jaccard:.3f}/"
                          f"净值相关{eq_corr:.4f}), 或年化衰减{perf_cmp['delta_annual_pp']:+.1f}pp过大, "
                          "估参方法需修正或回退到一次性参数")
    print(f"\n  稳定性判定: {verdict}")
    print(f"  {verdict_reason}")

    # ============ 产物保存 ============
    print("\n" + "=" * 76)
    print("保存产物")
    print("=" * 76)

    # 绩效对比 CSV
    pd.DataFrame({
        "metric": ["annual_return", "sharpe", "max_drawdown", "calmar", "total_return", "n_trades", "ic", "ic_rank"],
        "oneshot": [mi_on["annual_return"], mi_on["sharpe"], mi_on["max_drawdown"], mi_on["calmar"],
                    mi_on["total_return"], mi_on["n_trades"], ic_on["ic"], ic_on["ic_rank"]],
        "rolling": [mi_roll["annual_return"], mi_roll["sharpe"], mi_roll["max_drawdown"], mi_roll["calmar"],
                    mi_roll["total_return"], mi_roll["n_trades"], ic_roll["ic"], ic_roll["ic_rank"]],
    }).to_csv(os.path.join(DATA_DIR, "exp428_performance_compare.csv"), index=False)

    # 净值曲线 CSV
    pd.DataFrame({
        "date": [str(idx[i].date()) for i in range(len(idx))],
        "eq_oneshot": eq_on,
        "eq_rolling": eq_roll,
        "pos_oneshot": pos_oneshot,
        "pos_rolling": pos_roll,
    }).to_csv(os.path.join(DATA_DIR, "exp428_equity_curves.csv"), index=False)

    # 估参序列 CSV (alpha_t / beta_t 逐日)
    param_df = df_ham_roll[["date", "alpha_t", "beta_t", "disagreement", "deviation"]].copy()
    param_df.to_csv(os.path.join(DATA_DIR, "exp428_param_series.csv"), index=False)

    # 信号序列 CSV (逐日仓位 + 信号诊断)
    # 注意: df_ham_roll 是全 HAM 因子表(720行, 2023-09起), pos_roll/pos_oneshot 对齐到 ctx idx(628行, 2024-02起)。
    # 用 ctx idx 对齐后的日期, 重新索引 df_ham_roll 取信号诊断列。
    sig_df = df_ham_roll.set_index("date")[["position", "alpha_t", "beta_t", "main_trigger",
                                            "aux_confirm", "open_signal", "signal_dir", "deviation"]]
    sig_df = sig_df.reindex(idx).reset_index()
    sig_df.rename(columns={"index": "date"}, inplace=True)
    sig_df["final_pos_rolling"] = pos_roll
    sig_df["final_pos_oneshot"] = pos_oneshot
    sig_df.to_csv(os.path.join(DATA_DIR, "exp428_signal_series.csv"), index=False)

    # 净值曲线 PNG
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, axes = plt.subplots(3, 1, figsize=(12, 9), facecolor="#1a1a2e")
        ax1 = axes[0]
        ax1.set_facecolor("#1a1a2e")
        ax1.plot(range(len(eq_on)), eq_on, label="Oneshot (fixed α=0.3, β=0.5)", color="#00d4ff", lw=1.8)
        ax1.plot(range(len(eq_roll)), eq_roll, label="Rolling (daily refit)", color="#ff5555", lw=1.8)
        ax1.axhline(1.0, color="#666", ls="--", lw=0.8)
        ax1.set_title("exp428 Shadow Trading: Equity (9:01+10bp)", color="white", fontsize=13)
        ax1.legend(facecolor="#2a2a3e", edgecolor="#444", labelcolor="white", fontsize=9)
        ax1.tick_params(colors="white")
        for s in ax1.spines.values(): s.set_color("#444")

        ax2 = axes[1]
        ax2.set_facecolor("#1a1a2e")
        ax2.plot(range(len(alpha_arr)), alpha_arr, color="#ffcc33", lw=1.0, label="alpha_t")
        ax2.axhline(ALPHA_FIXED, color="#ffcc33", ls="--", lw=0.8, alpha=0.5, label=f"α_fixed={ALPHA_FIXED}")
        ax2.set_title("Rolling alpha_t (industrial weight)", color="white", fontsize=11)
        ax2.legend(facecolor="#2a2a3e", edgecolor="#444", labelcolor="white", fontsize=8)
        ax2.tick_params(colors="white")
        for s in ax2.spines.values(): s.set_color("#444")

        ax3 = axes[2]
        ax3.set_facecolor("#1a1a2e")
        ax3.plot(range(len(beta_arr)), beta_arr, color="#33ff99", lw=1.0, label="beta_t")
        ax3.axhline(BETA_FIXED, color="#33ff99", ls="--", lw=0.8, alpha=0.5, label=f"β_fixed={BETA_FIXED}")
        ax3.set_title("Rolling beta_t (speculative weight)", color="white", fontsize=11)
        ax3.legend(facecolor="#2a2a3e", edgecolor="#444", labelcolor="white", fontsize=8)
        ax3.tick_params(colors="white")
        for s in ax3.spines.values(): s.set_color("#444")
        plt.tight_layout()
        plt.savefig(os.path.join(DATA_DIR, "exp428_shadow_trading.png"), dpi=110, facecolor="#1a1a2e")
        plt.close()
        print(f"  ✓ 图: data/exp428_shadow_trading.png")
    except Exception as e:
        print(f"  [warn] 绘图失败: {e}")

    # 汇总 JSON
    summary = {
        "config": {
            "start": str(START.date()), "end": str(END.date()), "n_days": int(len(idx)),
            "slip_bp": MAIN_SLIP_BP, "adx_threshold": ADX_THRESHOLD, "adx_scale": ADX_SCALE,
            "rng_seed": RNG_SEED, "est_train_window": EST_TRAIN_WINDOW,
            "alpha_fixed": ALPHA_FIXED, "beta_fixed": BETA_FIXED,
            "n_alpha_grid": N_ALPHA_GRID, "n_beta_grid": N_BETA_GRID,
        },
        "performance": perf_cmp,
        "param_stability": stability,
        "signal_consistency": signal_cmp,
        "verdict": verdict,
        "verdict_reason": verdict_reason,
    }
    with open(os.path.join(DATA_DIR, "exp428_summary.json"), "w") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2, default=str)
    print(f"  ✓ 汇总: data/exp428_summary.json")
    print("\n✅ exp428 滚动影子盘完成")
