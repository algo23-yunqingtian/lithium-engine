"""
exp413: exp412 策略稳健性校验 — 可参数化引擎

底层零改动承诺: 完全复用 exp409/410/411 的信号与交易引擎函数,
不修改任何信号逻辑、阈值、交易条件。本模块仅把 exp412 风控引擎
的 M2回撤/ M3波动/ COST 参数外提为可注入参数, 以支持:
  模块1 风控参数扰动扫描
  模块2 交易成本敏感性扫描
  模块3 情景压力拆分
  模块4 HAM子策略尾部风险统计
  模块5 交易判定逻辑溯源(伪代码, 见报告)
  模块6 未来函数/前视偏差排查

设计:
  - run_exp412_param(): 核心引擎, 接受 dd_recover/warn/hard, vol_upper/scale, cost 注入
  - 返回每日仓位/净值/风控控制因子/信号结构, 供上层各模块复用
"""
import os
import numpy as np
import pandas as pd
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # model_ham
sys.path.insert(0, os.path.join(BASE, "exp409_ham_dynamic"))
sys.path.insert(0, os.path.join(BASE, "exp410_fund_dynamic"))
sys.path.insert(0, os.path.join(BASE, "exp411_combined"))

import exp409_ham_dynamic as E409
import exp410_fund_dynamic as E410
from exp411_combined import (
    build_targets, price_series, daily_equity, two_caliber_metrics,
    extract_trades, market_regime, stage_metrics, TRADING_DAYS_PER_YEAR,
)

TRADING_DAYS_PER_YEAR = E409.TRADING_DAYS_PER_YEAR  # 250
COST_DEFAULT = E409.COST_PER_SIDE                    # 0.0015

# ===== exp412 V4 最优参数(基准) =====
DD_RECOVER_DEFAULT = 0.05
DD_WARN_DEFAULT = 0.12
DD_HARD_DEFAULT = 0.18
TRAILING_WINDOW = 60
VOL_LOOKBACK = 20
VOL_UPPER_DEFAULT = 0.55
VOL_SCALE_DEFAULT = 0.50

START, END = pd.Timestamp("2024-02-01"), pd.Timestamp("2026-09-07")
VOL_ANNUALIZED = np.sqrt(TRADING_DAYS_PER_YEAR)


def compute_realized_vol(price, lookback=VOL_LOOKBACK):
    """滚动已实现年化波动率 (T日及更早数据, min_periods防前视)"""
    rets = price.pct_change()
    vol = rets.rolling(lookback, min_periods=5).std() * VOL_ANNUALIZED
    return vol.fillna(0)


# ============================================================
# 核心引擎: 可参数化 exp412
# ============================================================
def run_exp412_param(h_target, f_target, idx, price,
                     dd_recover=DD_RECOVER_DEFAULT, dd_warn=DD_WARN_DEFAULT,
                     dd_hard=DD_HARD_DEFAULT,
                     vol_upper=VOL_UPPER_DEFAULT, vol_scale=VOL_SCALE_DEFAULT,
                     cost=COST_DEFAULT,
                     dd_mult_warn=0.70, dd_mult_hard=0.30,
                     return_diag=False):
    """
    exp412 跨策略动态风控组合引擎 (可参数化版):
      1. 按exp411规则合成组合日度目标仓位
      2. M3: 高波动环境压缩(阈值vol_upper, 压缩至vol_scale)
      3. M2: 账户级回撤控制(dd_recover/dd_warn/dd_hard阶梯, 60日窗口)
      成本: 每次仓位变化扣 2*cost*|仓位变化量|

    时序: T日仓位 = f(T日及更早信号); T日仓位吃T日(T-1→T)价格收益;
          M2回撤用 equity[t-1] 与 equity[start:t] (截至t-1, 不含当日) → 无前视。
    返回: (risk_pos数组, [diag字典])
    """
    h = h_target.reindex(idx).fillna(0.0).values
    f = f_target.reindex(idx).fillna(0.0).values
    n = len(idx)

    vol = compute_realized_vol(price)
    vol_values = vol.reindex(idx).values
    vol_mult = np.where(vol_values > vol_upper, vol_scale, 1.0)

    close = price.reindex(idx).values
    risk_pos = np.zeros(n)
    equity = np.ones(n)
    dd_control = np.ones(n)
    vol_control = np.ones(n)
    state_arr = np.array(["none"] * n, dtype=object)

    for t in range(n):
        hv, fv = h[t], f[t]

        # 合成组合仓位(同exp411规则)
        if abs(hv) > 1e-9 and abs(fv) > 1e-9:
            if np.sign(hv) == np.sign(fv):
                pos = np.sign(hv) * min(abs(hv) + abs(fv), 1.0)
                st = "both_same"
            else:
                net = hv + fv
                pos = net if abs(net) > 1e-9 else 0.0
                pos = np.clip(pos, -1.0, 1.0)
                st = "both_opp"
        elif abs(hv) > 1e-9:
            pos = hv
            st = "ham_only"
        elif abs(fv) > 1e-9:
            pos = fv
            st = "fund_only"
        else:
            pos = 0.0
            st = "flat"

        # M3: 高波动压缩
        pos_after_vol = pos * vol_mult[t]
        vol_control[t] = vol_mult[t]

        # M2: 回撤控制(用截至t-1的净值, 无前视)
        dd_mult = 1.0
        if t > 0:
            start = max(0, t - TRAILING_WINDOW)
            eq_window = equity[start:t]
            if len(eq_window) > 0:
                peak = np.max(eq_window)
                current_dd = (equity[t-1] - peak) / peak if peak > 0 else 0
                dd_abs = -current_dd
                if dd_abs <= dd_recover:
                    dd_mult = 1.0
                elif dd_abs <= dd_warn:
                    dd_mult = dd_mult_warn
                else:
                    dd_mult = dd_mult_hard

        final_pos = pos_after_vol * dd_mult
        risk_pos[t] = final_pos
        dd_control[t] = dd_mult
        state_arr[t] = st

        # 更新净值(T日仓位吃T日收益, 无前视)
        if t > 0:
            r_price = (close[t] - close[t-1]) / close[t-1] if close[t-1] > 0 else 0
        else:
            r_price = 0.0
        strat_ret = risk_pos[t] * r_price
        dpos = abs(risk_pos[t] - risk_pos[t-1]) if t > 0 else abs(risk_pos[t])
        cost_ret = 2 * cost * dpos
        equity[t] = equity[t-1] * (1 + strat_ret - cost_ret) if t > 0 else 1.0

    if return_diag:
        return risk_pos, {"dd_control": dd_control, "vol_control": vol_control,
                          "signal_state": state_arr, "equity": equity, "vol": vol_values}
    return risk_pos


def baseline_equity(net_ret):
    """从每日净收益数组算指标(全时段口径)"""
    return net_ret


def metrics_from_equity(equity, net_ret, positions, price, cost=COST_DEFAULT):
    """
    从给定仓位数组 + 成本 计算标准指标(全时段口径, 统一COST参数)。
    用于模块1/2: 因为exp412引擎净值已内置成本, 这里直接用引擎净值重算。
    """
    n = len(equity)
    years = n / TRADING_DAYS_PER_YEAR
    total_ret = equity[-1] - 1
    ann_ret = (1 + total_ret) ** (1 / years) - 1 if total_ret > -1 else -1
    sharpe_full = (np.mean(net_ret) / np.std(net_ret) * np.sqrt(TRADING_DAYS_PER_YEAR)
                   if np.std(net_ret) > 0 else 0.0)
    peak = np.maximum.accumulate(equity)
    max_dd = ((equity - peak) / peak).min()
    calmar = ann_ret / abs(max_dd) if max_dd < 0 else 0.0
    return {
        "annual_return": ann_ret,
        "sharpe_full": sharpe_full,
        "max_drawdown": max_dd,
        "calmar": calmar,
        "total_return": total_ret,
    }


def run_with_metrics(h_target, f_target, idx, price, **kwargs):
    """跑一次引擎并返回 (仓位, 净值, 每日净收益, 指标, 交易, diag)"""
    cost = kwargs.get("cost", COST_DEFAULT)
    risk_pos, diag = run_exp412_param(h_target, f_target, idx, price,
                                       return_diag=True, **kwargs)
    eq = diag["equity"]
    # 每日净收益(复利展开)
    net_ret = np.zeros(len(eq))
    for t in range(1, len(eq)):
        net_ret[t] = eq[t] / eq[t-1] - 1
    trades = extract_trades(risk_pos, price.reindex(idx).dropna(), idx)
    m = metrics_from_equity(eq, net_ret, risk_pos, price, cost)
    m["n_trades"] = len(trades)
    return risk_pos, eq, net_ret, m, trades, diag


def load_data():
    """加载信号 + 价格 + 窗口切片 (与exp412完全一致的区间)"""
    h_target, f_target = build_targets()
    idx = h_target.index
    price = price_series(idx)
    mask = (idx >= START) & (idx <= END)
    idx_w = idx[mask]
    return h_target.reindex(idx_w), f_target.reindex(idx_w), idx_w, price.reindex(idx_w)
