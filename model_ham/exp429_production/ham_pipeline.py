"""
exp429: HAM 碳酸锂实盘工程化 —— 标准化每日运行流水线 (核心库)

设计目标:
  把 exp428 (STABLE 滚动影子盘) 的结论固化成可每日复用的生产流水线。
  每日收盘后运行本模块, 输出三件核心交付:
    (1) 分歧 D 因子序列  (disagreement / deviation / D_c / D_f / alpha_t / beta_t)
    (2) 交易信号        (position 方向 + open_signal + main_trigger)
    (3) 建议仓位        (配置A 稳健 / 配置B 激进 两套最终仓位)

时序铁律 (版本B口径, 与 exp425/426/428 一致):
  T 日收盘决策 -> T+1 9:01 分钟均价成交 (+10bp 滑点)。
  每日估参严格只用 [T-180, T) 历史, 无前视。

两套配置 (与 exp426 消融实验一一对应, 数值经核实):
  配置A (稳健, 推荐实盘主力): 保留 ADX 降权 (ADX_SCALE=0.3)
      年化 28.9% / Calmar 4.23 / 最大回撤 -6.8% / 129 笔   (exp426 基准组)
  配置B (高风险高收益): 关闭 ADX 降权 (ADX_SCALE=1.0)
      年化 32.1% / Calmar 3.05 / 最大回撤 -10.5% / 126 笔   (exp426 消融D)
  差异: ADX 降权牺牲约 3.2pp 年化, 换取约 3.7pp 回撤压缩 (风险调整更优)。

复用: 全部信号链路 (compute_aux_signals / load_gmm_states / compute_ham_signals /
  run_ham_dynamic / compute_positions / compute_adx) 沿用 exp409/exp413/exp424/exp425/exp426 原版,
  滚动估参沿用 exp428。本模块只做"编排 + 封装", 不重复实现底层逻辑。
"""
import os
import sys
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
from exp411_combined import build_targets
from exp424_engine import (
    load_context, compute_positions, compute_adx,
    VOL_HALF, VOL_FULL, HALF_SCALE, FULL_SCALE,
    DD_RECOVER, DD_WARN, DD_HARD,
)
from exp425_engine import ADX_THRESHOLD, ADX_SCALE, RNG_SEED

# ---- 从 exp428 复用滚动估参核心 ----
sys.path.insert(0, os.path.join(MODEL, "exp428_shadow_trading"))
from exp428_engine import (
    fit_alpha_beta, rolling_deviation,
    ALPHA_FIXED, BETA_FIXED, EST_TRAIN_WINDOW, EST_MIN_PERIODS,
)

EXP_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(EXP_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)

MAIN_SLIP_BP = 10.0

# 两套配置定义 (与 exp426 消融实验对齐)
CONFIGS = {
    "A": {
        "name": "配置A_稳健",
        "adx_scale": 0.3,
        "desc": "保留ADX趋势降权(×0.3), 风险调整最优, 推荐实盘主力",
        "ref_annual": 0.289, "ref_calmar": 4.23, "ref_mdd": -0.068, "ref_trades": 129,
    },
    "B": {
        "name": "配置B_激进",
        "adx_scale": 1.0,
        "desc": "关闭ADX趋势降权, 年化更高但回撤放大, 高风险高收益",
        "ref_annual": 0.321, "ref_calmar": 3.05, "ref_mdd": -0.105, "ref_trades": 126,
    },
}


# ============================================================
# 1. 分歧 D 因子序列 (滚动每日估参)
# ============================================================
def build_disagreement_series():
    """
    构建滚动版分歧 D 因子序列。
    返回 df_ham (含 D_c/D_f/disagreement/deviation/alpha_t/beta_t + 原 HAM 信号链)。
    严格无前视: 每日 T 只用 [T-180, T) 历史拟合 alpha/beta。
    """
    df_h = E409.load_ham_factors()
    df_h = rolling_deviation(df_h)        # 滚动重估参 (exp428)
    df_h = E409.compute_aux_signals(df_h)
    df_h = E409.load_gmm_states(df_h)
    df_h = E409.compute_ham_signals(df_h)
    df_h, _ = E409.run_ham_dynamic(df_h)
    return df_h


def ham_target_from(df_ham):
    """从 HAM 因子表提取 h_target (持仓目标方向序列, 按 date 索引)。"""
    return df_ham.set_index("date")["position"].astype(float)


# ============================================================
# 2. 建议仓位 (两套配置)
# ============================================================
def compute_position_config(ctx, h_target, adx_scale):
    """
    T1 风控底座 + 可开关 ADX 降权, 输出最终仓位序列 (对齐 ctx idx)。
    adx_scale=0.3 (配置A) / 1.0 (配置B 关闭降权)。
    """
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
    if adx_scale < 1.0:
        adx_mask = adx > ADX_THRESHOLD
        pos[adx_mask] = pos[adx_mask] * adx_scale
    return pos


def position_to_action(pos, pos_prev):
    """
    把仓位序列翻译为可读交易动作。
    返回 action 字符串序列: 空仓/加仓做多/减仓/反手做空 等。
    """
    actions = []
    for p, pv in zip(pos, pos_prev):
        if abs(p) < 1e-9 and abs(pv) < 1e-9:
            actions.append("HOLD_FLAT")
        elif abs(p) < 1e-9:
            actions.append("CLOSE")
        elif abs(pv) < 1e-9:
            actions.append("OPEN_LONG" if p > 0 else "OPEN_SHORT")
        elif np.sign(p) != np.sign(pv):
            actions.append("REVERSE")
        elif abs(p) > abs(pv) + 1e-9:
            actions.append("INCREASE")
        elif abs(p) < abs(pv) - 1e-9:
            actions.append("DECREASE")
        else:
            actions.append("HOLD")
    return actions


# ============================================================
# 3. 信号链路汇总
# ============================================================
def extract_signal_diagnostics(df_ham, ctx_idx):
    """提取信号诊断列 (对齐 ctx idx, 用于每日输出)。"""
    cols = ["position", "alpha_t", "beta_t", "main_trigger",
            "aux_confirm", "open_signal", "signal_dir", "deviation",
            "disagreement", "D_c", "D_f"]
    avail = [c for c in cols if c in df_ham.columns]
    sig = df_ham.set_index("date")[avail]
    sig = sig.reindex(ctx_idx)
    sig.index.name = "date"
    return sig.reset_index()


# ============================================================
# 主流程: 生成完整每日输出表
# ============================================================
def run_pipeline():
    """
    运行完整流水线, 返回 (daily_out, ctx, df_ham, config_results)。
    daily_out: 逐日 D因子+信号+两套仓位+动作 的宽表。
    """
    ctx = load_context()
    idx = ctx["idx"]

    df_ham = build_disagreement_series()
    h_target = ham_target_from(df_ham)

    # 两套配置仓位
    pos_prev_zero = np.zeros(len(idx))
    config_results = {}
    daily = extract_signal_diagnostics(df_ham, idx).copy()

    for ckey, cfg in CONFIGS.items():
        pos = compute_position_config(ctx, h_target, cfg["adx_scale"])
        daily[f"pos_{ckey}"] = pos
        actions = position_to_action(pos, pos_prev_zero[:len(idx)])
        # action 用相邻日对比, 重算 (prev = pos shift 1)
        prev = np.r_[0.0, pos[:-1]]
        actions = position_to_action(pos, prev)
        daily[f"action_{ckey}"] = actions
        config_results[ckey] = {"pos": pos, "config": cfg}

    daily.to_csv(os.path.join(DATA_DIR, "exp429_daily_pipeline.csv"), index=False)
    return daily, ctx, df_ham, config_results


if __name__ == "__main__":
    print("=" * 76)
    print("exp429: HAM 碳酸锂每日运行流水线 (核心库自检)")
    print("=" * 76)
    daily, ctx, df_ham, cr = run_pipeline()
    print(f"\n数据区间: {ctx['idx'][0].date()} ~ {ctx['idx'][-1].date()} ({len(ctx['idx'])} 日)")
    print(f"流水线输出: {len(daily)} 行 -> data/exp429_daily_pipeline.csv")
    for ck, res in cr.items():
        pos = res["pos"]
        hold = int((np.abs(pos) > 1e-9).sum())
        print(f"  {res['config']['name']}: 持仓日 {hold} 天, "
              f"多 {(pos > 1e-9).sum()} 空 {(pos < -1e-9).sum()}")
    print("\n流水线自检完成。")
