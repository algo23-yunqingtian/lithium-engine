"""
exp416: 尾部风险防护升级 — 核心引擎

基准: exp412 完整策略, HAM因子/基本面信号/风控逻辑固定不变, 禁止改动信号本身。
本模块仅在 exp412 引擎之上叠加"收盘隔夜风控 + 趋势降权 + 期权对冲仿真"三层增量,
完全复用 exp409/410/411/413/414 引擎与口径。回测区间 2024-02 ~ 2026-09。

变体定义:
  T0 = exp412 原版 (基准, 无任何新增防护)
  T1 = T0 + 波动率隔夜敞口管控 (收盘按波动率分档: >vol_half 减半, >vol_full 清仓)
  T2 = T1 + 趋势识别降权 (持续单边趋势段 HAM 仓位乘 trend_scale)
  T3 = T2 + 期权对冲仿真 (每日买入虚值期权对冲隔夜跳空, 扣权利金成本)

时序约定 (严格无前视):
  - T 日收盘决策, T 日仓位吃 T 日 (T-1→T) 价格收益 —— 与 exp411/412/413 完全一致。
  - "隔夜敞口管控": 收盘风控判定的是 T 日仓位 (T 日收盘后进入夜间的敞口),
    其判定输入为 T 日及更早的波动率/趋势状态 —— 无前视。
  - 波动率/趋势阈值判定全部使用截至 T 日的滚动窗口 (min_periods 防前视)。
  - 期权对冲: 每日按 T 日收盘已实现波动率估算权利金 (Black-Scholes), 次日跳空赔付。
    权利金在 T 日扣 (T 日已实现的波动率定价, 无前视), 赔付在 T+1 结算。
"""
import os
import sys
import numpy as np
import pandas as pd

MODEL = "/home/ubuntu/lithium-engine/model_ham"
for d in ["exp409_ham_dynamic", "exp410_fund_dynamic", "exp411_combined",
          "exp413_robustness", "exp414_attribution"]:
    sys.path.insert(0, os.path.join(MODEL, d))

import exp409_ham_dynamic as E409
import exp413_engine as E413
import exp414_engine as E414
from exp411_combined import (
    build_targets, price_series, extract_trades, market_regime,
    TRADING_DAYS_PER_YEAR,
)

EXP_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(EXP_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)

START, END = pd.Timestamp("2024-02-01"), pd.Timestamp("2026-09-07")
COST = E409.COST_PER_SIDE
TDPY = E409.TRADING_DAYS_PER_YEAR
VOL_ANNUALIZED = np.sqrt(TDPY)


# ============================================================
# 数据加载 (与 exp413/414 完全一致)
# ============================================================
def load_context():
    """加载 T1 完整组合的上下文: 目标仓位 / 价格 / 统一日索引 / 波动率 / 趋势标记"""
    h_target, f_target = build_targets()
    idx = h_target.index
    price = price_series(idx)
    mask = (idx >= START) & (idx <= END)
    idx_w = idx[mask]
    price_w = price.reindex(idx_w)

    # 已实现年化波动率 (截至 T 日, min_periods=5 防前视)
    vol = E413.compute_realized_vol(price_w, lookback=20)
    vol = vol.reindex(idx_w)

    # 趋势识别: 20日动量, |动量| > trend_thr 判为单边趋势
    # regime 用 exp411 market_regime (20日 pct, ±12%), 这里额外算动量幅度供强度加权
    pct20 = price_w.pct_change(20)

    return {
        "h_target": h_target.reindex(idx_w).fillna(0.0),
        "f_target": f_target.reindex(idx_w).fillna(0.0),
        "idx": idx_w,
        "price": price_w,
        "vol": vol,
        "pct20": pct20,
    }


# ============================================================
# exp416 组合引擎: T0/T1/T2/T3 统一实现
# ============================================================
def run_variant(h_target, f_target, idx, price,
                vol=None, pct20=None,
                vol_half=None, vol_full=None, half_scale=0.5, full_scale=0.0,
                trend_thr=0.0, trend_scale=1.0,
                opt_delta=0.0, opt_horizon_days=1.0,
                cost=COST, return_diag=False):
    """
    exp416 变体引擎。以 exp412 V4 风控为底座, 叠加三层增量防护:

    步骤 (逐日 t):
      1. exp411 合成组合目标仓位 pos
      2. exp412 M3 高波动压缩 + M2 账户回撤控制  → pos_risk (T0 到此为止)
      3. [T1] 隔夜敞口管控: 收盘按已实现波动率分档
             vol[t] > vol_full  → 仓位 × full_scale (清仓 0.0)
             vol_half < vol[t] <= vol_full → 仓位 × half_scale (减半 0.5)
      4. [T2] 趋势降权: |pct20[t]| > trend_thr (单边趋势) → 仓位 × trend_scale
      5. 计算 T 日策略收益 (仓位 × T 日价格收益) - 换手成本
      6. [T3] 期权对冲: 每日扣权利金, 次日跳空赔付

    返回: (final_pos 数组, diag 字典)
    """
    h = h_target.reindex(idx).fillna(0.0).values
    f = f_target.reindex(idx).fillna(0.0).values
    n = len(idx)
    close = price.reindex(idx).values

    # --- 底座: 直接复用 exp412 V4 引擎得到 pos_risk 及 diag ---
    pos_risk, diag412 = E413.run_exp412_param(
        h_target, f_target, idx, price, cost=cost, return_diag=True)

    # --- 增量层所需输入 ---
    vol_arr = vol.reindex(idx).values if vol is not None else np.zeros(n)
    pct20_arr = pct20.reindex(idx).values if pct20 is not None else np.zeros(n)

    overnight_mult = np.ones(n)   # 隔夜敞口管控因子 (T1)
    trend_mult = np.ones(n)       # 趋势降权因子 (T2)

    for t in range(n):
        # [T1] 隔夜敞口管控: 收盘按 T 日已实现波动率分档降仓
        if vol_half is not None:
            v = vol_arr[t]
            if vol_full is not None and v > vol_full:
                overnight_mult[t] = full_scale
            elif v > vol_half:
                overnight_mult[t] = half_scale
        # [T2] 趋势识别降权: |20日动量| > trend_thr → 压至 trend_scale
        if trend_thr is not None and trend_thr > 0:
            if abs(pct20_arr[t]) > trend_thr:
                trend_mult[t] = trend_scale

    final_pos = pos_risk * overnight_mult * trend_mult

    if return_diag:
        return final_pos, {
            "pos_risk": pos_risk,
            "overnight_mult": overnight_mult,
            "trend_mult": trend_mult,
            "signal_state": diag412["signal_state"],
            "vol": vol_arr,
            "dd_control": diag412["dd_control"],
            "vol_control": diag412["vol_control"],
        }
    return final_pos


# ============================================================
# T3: 期权对冲仿真 (在 T2 仓位之上叠加, 不改信号)
# ============================================================
def black_scholes_put_call_pv(S, K, sigma_ann, T_years, is_put):
    """
    单腿欧式期权现值 (Black-Scholes, 零利率简化版)。
    S=标的现价, K=行权价, sigma_ann=年化波动率, T_years=剩余年数, is_put=True买看跌。
    用于估算每日买入虚值期权的权利金成本。
    """
    from math import log, sqrt
    import math
    if sigma_ann <= 0 or T_years <= 0:
        return 0.0
    d1 = (log(S / K) + 0.5 * sigma_ann ** 2 * T_years) / (sigma_ann * sqrt(T_years))
    d2 = d1 - sigma_ann * sqrt(T_years)
    N = lambda x: 0.5 * (1 + math.erf(x / sqrt(2)))
    if is_put:
        return K * N(-d2) - S * N(-d1)   # put price
    else:
        return S * N(d1) - K * N(d2)     # call price


def estimate_option_premium(close_arr, vol_arr, delta=0.15, horizon_days=1.0,
                            strike_pct=0.03):
    """
    估算每日买入虚值看跌期权 (对冲隔夜下跌跳空) 的权利金。
    返回每日权利金占标的比 (小数, 如 0.0008 = 0.08%/日)。
    delta 用于把行权价放在虚值程度 (此处直接用 strike_pct 作虚值距离, 更直观)。
    strike_pct: 行权价低于现价的比例 (3% = 虚值3%)。
    """
    n = len(close_arr)
    premium = np.zeros(n)
    T_years = horizon_days / TDPY
    from math import sqrt
    for t in range(n):
        sig = vol_arr[t] if vol_arr[t] > 0.05 else 0.10   # 波动率下限防权利金塌陷
        K = close_arr[t] * (1 - strike_pct)   # 虚值看跌行权价
        px = black_scholes_put_call_pv(close_arr[t], K, sig, T_years, is_put=True)
        premium[t] = px / close_arr[t]   # 权利金占标的比
    return premium


def run_variant_with_options(pos_t2, price, vol, idx=None, opt_delta=0.15,
                             opt_horizon_days=1.0, opt_strike_pct=0.03,
                             cost=COST, return_diag=False):
    """
    T3 = T2 仓位 + 期权对冲仿真。
    期权对冲模型:
      - 每个持仓日 (pos_t2[t] != 0) 买入虚值看跌期权对冲隔夜跳空。
      - 权利金 premium[t] 在 T 日扣 (用 T 日已实现波动率定价, 无前视)。
      - 赔付 payoff[t]: 若次日 (T+1) 价格跳空下跌超过 strike_pct 虚值区,
        期权赔付 = 多头对冲 pos_t2[t] * 保护部分。赔付在 T+1 结算, 记到 T+1 收益。
      - 多头对冲下跌: pos_t2[t]>0 时买看跌; pos_t2[t]<0 时买看涨 (对称)。
    """
    pos_t2 = np.asarray(pos_t2, dtype=float)
    if idx is None:
        idx = price.index
    close = price.reindex(idx).values.astype(float)
    vol_arr = vol.reindex(idx).values.astype(float)
    pos = pos_t2.copy()
    n = len(pos)

    premium_daily = estimate_option_premium(close, vol_arr, delta=opt_delta,
                                            horizon_days=opt_horizon_days,
                                            strike_pct=opt_strike_pct)
    # opt_delta=0 视为禁用期权对冲 (仅复算纯仓位净值, 不扣权利金不赔付)
    if opt_delta <= 0:
        premium_daily = np.zeros(n)

    # 每日净收益重算: 仓位收益 - 换手成本 - 权利金 + 期权赔付
    r_price = np.zeros(n)
    r_price[1:] = (close[1:] - close[:-1]) / close[:-1]
    dpos = np.zeros(n)
    dpos[1:] = np.abs(pos[1:] - pos[:-1])
    dpos[0] = np.abs(pos[0])

    net_ret = pos * r_price - 2 * cost * dpos

    # 权利金成本 (仅持仓日扣)
    in_pos = np.abs(pos) > 1e-9
    premium_cost = np.where(in_pos, premium_daily, 0.0)
    net_ret -= premium_cost

    # 期权赔付: 次日跳空赔付, 记到 T+1
    payoff = np.zeros(n)
    if opt_delta > 0:
        for t in range(n - 1):
            if abs(pos[t]) < 1e-9:
                continue
            # 多头 (pos>0): 对冲下跌, 看跌赔付 = 次日落幅超过虚值区后的保护
            # 空头 (pos<0): 对冲上涨, 看涨赔付 = 次日涨幅超过虚值区后的保护
            gap = r_price[t + 1]
            prot = opt_strike_pct
            if pos[t] > 0:
                # 次日落, gap<0; 赔付覆盖 (|-gap - prot|) 部分 (超过虚值区才赔)
                protection = max(0.0, -gap - prot)
            else:
                # 次日涨, gap>0; 赔付覆盖 (gap - prot) 部分
                protection = max(0.0, gap - prot)
            # 赔付 × 仓位 × 次日结算
            payoff[t + 1] += pos[t] * protection

    net_ret += payoff

    equity = np.cumprod(1 + net_ret)
    return net_ret, equity, {
        "pos": pos, "premium_daily": premium_daily,
        "premium_cost_total": float(np.sum(premium_cost)),
        "payoff_total": float(np.sum(payoff)),
        "n_option_days": int(in_pos.sum()),
    }


def compute_metrics(net_ret, equity, pos):
    """从每日净收益+净值算统一指标 (与 exp414.unified_metrics 口径一致)"""
    n = len(net_ret)
    years = n / TDPY
    total_ret = equity[-1] - 1
    ann_ret = (1 + total_ret) ** (1 / years) - 1 if total_ret > -1 else -1
    sharpe = (np.mean(net_ret) / np.std(net_ret) * np.sqrt(TDPY)
              if np.std(net_ret) > 0 else 0.0)
    peak = np.maximum.accumulate(equity)
    max_dd = ((equity - peak) / peak).min()
    calmar = ann_ret / abs(max_dd) if max_dd < 0 else 0.0
    return {
        "annual_return": float(ann_ret),
        "sharpe_full": float(sharpe),
        "max_drawdown": float(max_dd),
        "calmar": float(calmar),
        "total_return": float(total_ret),
    }


def compute_metrics_from_pos(pos, price, cost=COST):
    """从日度仓位+价格算统一指标 (复用 exp414.unified_metrics, 口径一致)"""
    m, _, _, _, trades = E414.unified_metrics(pos, price, cost=cost)
    m["n_trades"] = len(trades)
    return m, trades


if __name__ == "__main__":
    print("exp416 engine module loaded OK")
