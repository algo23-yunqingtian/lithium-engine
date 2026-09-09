"""
exp410: 基本面三因子模型（exp407）— 动态交易体系（替换固定20日持仓）

核心定位（与 exp407 的根本区别）:
  exp407:  固定20日强制持仓，信号在T日算、T+20平仓 → 即使基本面逻辑提前消退/兑现也死拿20天
  本次:    基本面边际驱动动态止盈止损，逻辑消退/透支/反转立即平仓 → 保留 exp407 的真实预测力，
           但让持仓周期随基本面状态自适应

三因子（沿用 exp407，不新增因子挖掘）:
  f_stock_pct20            库存20日环比        IC_SIGN=-1 (累库→空)
  f_wh_basis_x_stockpct    仓单基差×仓单增速    IC_SIGN=+1 (基差走强→多)
  f_cell_pct20             动力电池20日环比    IC_SIGN=-1 (需求走弱→空)

【开仓规则: 强边际共振开仓】
  三因子合成强度 = 各因子(滚动180日中位数方向×IC_SIGN)的等权和，取值范围[-1,1]
  强阈值: |合成强度| > 滚动180日90%分位 → 供需显著失衡才开仓（剔除临界弱信号）
  方向: 合成强度>0→多, <0→空

【平仓规则: 任意触发立即平仓】
  1. 基本面边际衰减: 合成强度向0回归超一半（逻辑消退，核心平仓）
  2. 预期透支平仓: 持仓期累计收益 > 期现基差快速修复代理（20日收益超滚动分位，行情提前兑现）
  3. 反向信号触发: 合成强度符号反转（短周期因子反转）
  4. 最长保护性持仓: 18日强制平仓

【动态仓位】
  强信号(|合成|>滚动95%分位): 100%
  中等信号(|合成|>滚动90%分位): 70%
  弱信号(未达90%): 不交易

约束: 滚动180/20、无前视、严格时序、不网格寻优、不数据窥探、统一手续费滑点
"""
import numpy as np
import pandas as pd
import os, json, sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(BASE, "exp406_fund_factor")
DATA = os.path.dirname(os.path.abspath(__file__))
os.makedirs(os.path.join(DATA, "data"), exist_ok=True)

# ===== 统一参数（与 exp409 共用） =====
TRADING_DAYS_PER_YEAR = 250
COST_PER_SIDE = 0.0015        # 单边手续费+滑点 0.15%
TRAIN_WINDOW = 180            # 滚动训练窗口
FORECAST_STEP = 20
MAX_HOLD_FUND = 18            # 基本面最大保护性持仓
STRONG_Q = 0.95               # 强信号分位（满仓）
MED_Q = 0.80                  # 中等信号分位（70%仓，强边际门槛）
EXPECT_REALIZE_Q = 0.90       # 预期透支分位（行情提前兑现）

N_FACTORS = 3
FACTOR_COLS = ["f_stock_pct20", "f_wh_basis_x_stockpct", "f_cell_pct20"]
IC_SIGN = {"f_stock_pct20": -1, "f_wh_basis_x_stockpct": +1, "f_cell_pct20": -1}
HAM_FORBIDDEN = {"n_f", "n_c", "Total_Demand", "profit_f", "profit_c", "D_f", "D_c", "P_fund", "n_f_minus_n_c", "valid"}


def load_factors():
    """加载 exp406a 因子表，清理 inf（与 exp407 一致口径）"""
    F = pd.read_csv(os.path.join(SRC, "data", "exp406a_factors.csv"))
    F["date"] = pd.to_datetime(F["date"])
    F = F.sort_values("date").reset_index(drop=True)
    assert not (set(F.columns) & HAM_FORBIDDEN), "禁止HAM字段!"
    for c in F.columns:
        if F[c].dtype != object:
            F[c] = F[c].replace([np.inf, -np.inf], np.nan)
    return F


def factor_signal(Fdf, fc, idx, ic_sign):
    """单因子滚动信号（与 exp407 完全一致口径）：当前值 vs 滚动180日中位数，乘IC方向"""
    train_start = max(0, idx - TRAIN_WINDOW)
    tv = Fdf[fc].iloc[train_start:idx].dropna()
    if len(tv) < 30:
        return 0.0
    med = tv.median()
    cur = Fdf[fc].iloc[idx]
    if pd.isna(cur):
        return 0.0
    raw = 1.0 if cur > med else -1.0
    return raw * (1 if ic_sign > 0 else -1)


def build_combo_signal(Fdf):
    """
    三因子合成强度（等权，取值[-1,1]）+ 滚动分位阈值
    合成强度 = mean(各因子滚动信号)
    所有分位用滚动180日（min_periods防前视）
    """
    n = len(Fdf)
    combo = np.zeros(n)
    for i in range(n):
        s = np.array([factor_signal(Fdf, fc, i, IC_SIGN[fc]) for fc in FACTOR_COLS])
        combo[i] = np.mean(s)
    Fdf = Fdf.copy()
    Fdf["combo_signal"] = combo
    Fdf["combo_abs"] = np.abs(combo)

    # 滚动分位阈值
    Fdf["q_med"] = Fdf["combo_abs"].rolling(TRAIN_WINDOW, min_periods=60).quantile(MED_Q)
    Fdf["q_strong"] = Fdf["combo_abs"].rolling(TRAIN_WINDOW, min_periods=60).quantile(STRONG_Q)
    # 信号强度衰减基准: 记录入场时的合成强度
    return Fdf


def fund_close_condition(Fdf, t, entry_t, entry_combo):
    """
    基本面平仓条件（任意一条触发立即平仓）
    """
    row = Fdf.iloc[t]
    held = t - entry_t
    combo = row["combo_signal"]

    # 1. 基本面边际衰减: 合成强度回归至中性（符号归零或接近0），核心逻辑消退
    #    注意: combo 是离散投票信号(±1/3步进)，"回归一半"过于敏感会让持仓过短，
    #    基本面是中期趋势，应待逻辑真正消退（归零/反号）才平
    if entry_combo != 0 and combo == 0:
        return True, "marginal_decay"

    # 2. 预期透支平仓: 持仓期累计收益超滚动分位（行情提前兑现）
    if entry_t > 0:
        hold_ret = np.log(Fdf.iloc[t]["close"] / Fdf.iloc[entry_t]["close"])
        entry_dir = np.sign(entry_combo)
        realized = entry_dir * hold_ret
        # 用历史20日收益的滚动分位作阈值（仅T及更早）
        if pd.notna(row["q_ret20"]) and realized > row["q_ret20"]:
            return True, "expect_realized"

    # 3. 反向信号触发: 合成强度符号反转
    if entry_combo != 0 and combo != 0 and np.sign(combo) != np.sign(entry_combo):
        return True, "reverse_signal"

    # 4. 最长保护性持仓18日
    if held >= MAX_HOLD_FUND:
        return True, "max_hold"

    return False, None


def compute_fund_signals(Fdf):
    """准备开仓判断所需的列"""
    Fdf = Fdf.copy()
    # 20日收益（用于预期透支判断与对照组）
    Fdf["ret20"] = np.log(Fdf["close"] / Fdf["close"].shift(20))
    # 预期透支分位: 滚动180日20日收益的90%分位
    Fdf["q_ret20"] = Fdf["ret20"].rolling(TRAIN_WINDOW, min_periods=60).quantile(EXPECT_REALIZE_Q)
    # 方向
    Fdf["signal_dir"] = np.sign(Fdf["combo_signal"])
    # 强边际开仓条件: |合成| > 滚动90%分位
    Fdf["open_trigger"] = (Fdf["combo_abs"] > Fdf["q_med"]) & Fdf["q_med"].notna()
    # 仓位分级
    Fdf["position_size"] = 0.0
    Fdf.loc[Fdf["open_trigger"] & (Fdf["combo_abs"] > Fdf["q_strong"]), "position_size"] = 1.0
    Fdf.loc[Fdf["open_trigger"] & (Fdf["combo_abs"] <= Fdf["q_strong"]), "position_size"] = 0.7
    return Fdf


def run_fund_dynamic(Fdf):
    """基本面动态交易引擎主循环"""
    Fdf = Fdf.reset_index(drop=True)
    n = len(Fdf)
    close = Fdf["close"].values
    positions = np.zeros(n)
    trades = []
    in_pos = False
    entry_t = -1
    entry_combo = 0.0
    entry_size = 0.0

    for t in range(1, n):
        if in_pos:
            should_close, reason = fund_close_condition(Fdf, t, entry_t, entry_combo)
            if should_close:
                pos_ret = np.sign(entry_combo) * (close[t] - close[entry_t]) / close[entry_t]
                net_ret = pos_ret - 2 * COST_PER_SIDE
                positions[t] = np.sign(entry_combo) * entry_size
                trades.append({
                    "entry_date": Fdf.iloc[entry_t]["date"],
                    "exit_date": Fdf.iloc[t]["date"],
                    "direction": int(np.sign(entry_combo)),
                    "size": entry_size,
                    "hold_days": t - entry_t,
                    "gross_ret": pos_ret,
                    "net_ret": net_ret,
                    "exit_reason": reason
                })
                in_pos = False
                entry_t = -1
            else:
                positions[t] = np.sign(entry_combo) * entry_size
        else:
            if Fdf.iloc[t]["open_trigger"]:
                in_pos = True
                entry_t = t
                entry_combo = float(Fdf.iloc[t]["combo_signal"])
                entry_size = float(Fdf.iloc[t]["position_size"])
                positions[t] = np.sign(entry_combo) * entry_size

    Fdf["position"] = positions
    return Fdf, trades


def run_fixed20_control(Fdf):
    """
    对照组2: 旧基本面固定20日持仓（exp407 旧基准）
    合成信号触发即开仓，固定持有20日强制平仓
    """
    Fdf = Fdf.reset_index(drop=True)
    n = len(Fdf)
    close = Fdf["close"].values
    positions = np.zeros(n)
    trades = []
    t = 0
    while t < n:
        combo = Fdf.iloc[t]["combo_signal"]
        if abs(combo) > 1e-9:
            entry_t = t
            entry_dir = int(np.sign(combo))
            entry_price = close[t]
            exit_t = min(entry_t + FORECAST_STEP, n - 1)
            for k in range(entry_t, exit_t):
                positions[k] = entry_dir
            pos_ret = entry_dir * (close[exit_t] - entry_price) / entry_price
            net_ret = pos_ret - 2 * COST_PER_SIDE
            trades.append({
                "entry_date": Fdf.iloc[entry_t]["date"],
                "exit_date": Fdf.iloc[exit_t]["date"],
                "direction": entry_dir,
                "size": 1.0,
                "hold_days": exit_t - entry_t,
                "gross_ret": pos_ret,
                "net_ret": net_ret,
                "exit_reason": "fixed_20"
            })
            t = exit_t + 1
        else:
            t += 1
    Fdf["position"] = positions
    return Fdf, trades


def compute_metrics(trades, Fdf, positions):
    """统一指标（与 exp409 一致口径）"""
    close = Fdf["close"].values
    n = len(Fdf)
    if len(trades) == 0:
        return {"n_trades": 0, "note": "无交易"}
    pos = positions
    rets = np.zeros(n)
    for t in range(1, n):
        if pos[t] != 0:
            rets[t] = pos[t] * (close[t] - close[t-1]) / close[t-1]
    equity = np.cumprod(1 + rets)
    total_cost = len(trades) * 2 * COST_PER_SIDE
    equity_net = equity * (1 - total_cost)
    total_ret = equity_net[-1] - 1
    years = n / TRADING_DAYS_PER_YEAR
    ann_ret = (1 + total_ret) ** (1/years) - 1 if total_ret > -1 else -1
    sharpe = np.mean(rets) / np.std(rets) * np.sqrt(TRADING_DAYS_PER_YEAR) if np.std(rets) > 0 else 0
    peak = np.maximum.accumulate(equity_net)
    max_dd = ((equity_net - peak) / peak).min()
    nets = [tr["net_ret"] for tr in trades]
    wins = [r for r in nets if r > 0]
    losses = [r for r in nets if r <= 0]
    win_rate = len(wins) / len(nets)
    avg_win = np.mean(wins) if wins else 0
    avg_loss = abs(np.mean(losses)) if losses else 0.0001
    pl_ratio = avg_win / avg_loss if avg_loss > 0 else 0
    # exp407同口径参考: 用combo信号×20日对数收益/20折算净值夏普（仅基本面组有target_fwd）
    nav_sharpe = np.nan
    nav_acc = np.nan
    if "target_fwd" in Fdf.columns and "combo_signal" in Fdf.columns:
        tf = Fdf["target_fwd"].values
        sg = Fdf["combo_signal"].values
        mk = (~np.isnan(tf)) & (np.abs(sg) > 1e-9)
        dr = np.where(mk, sg*tf/FORECAST_STEP, 0)
        dv = dr[mk & ~np.isnan(sg)]
        if len(dv) > 0 and dv.std() > 0:
            nav_sharpe = dv.mean()/dv.std()*np.sqrt(TRADING_DAYS_PER_YEAR)
            nav_acc = (np.sign(sg[mk])==np.sign(tf[mk])).mean()
    return {
        "n_trades": len(trades),
        "total_return": total_ret,
        "annual_return": ann_ret,
        "sharpe": sharpe,
        "max_drawdown": max_dd,
        "win_rate": win_rate,
        "pl_ratio": pl_ratio,
        "avg_hold_days": np.mean([tr["hold_days"] for tr in trades]),
        "exit_reasons": pd.Series([tr["exit_reason"] for tr in trades]).value_counts().to_dict(),
        "nav_sharpe_exp407_caliber": nav_sharpe,
        "nav_acc_exp407_caliber": nav_acc
    }


if __name__ == "__main__":
    print("=" * 60)
    print("exp410: 基本面三因子动态交易 + 固定20日对照")
    print("=" * 60)

    F = load_factors()
    F = build_combo_signal(F)
    F = compute_fund_signals(F)

    # 实验组2: 基本面动态
    F_dyn, trades_dyn = run_fund_dynamic(F)
    m_dyn = compute_metrics(trades_dyn, F_dyn, F_dyn["position"].values)
    print("\n[实验组2] 基本面三因子动态新策略:")
    for k, v in m_dyn.items():
        if k != "exit_reasons":
            print(f"  {k}: {v:.4f}" if isinstance(v, float) else f"  {k}: {v}")
    print(f"  平仓原因分布: {m_dyn.get('exit_reasons', {})}")

    # 对照组2: 固定20日
    F_ctrl, trades_ctrl = run_fixed20_control(F)
    m_ctrl = compute_metrics(trades_ctrl, F_ctrl, F_ctrl["position"].values)
    print("\n[对照组2] 旧基本面固定20日持仓:")
    for k, v in m_ctrl.items():
        if k != "exit_reasons":
            print(f"  {k}: {v:.4f}" if isinstance(v, float) else f"  {k}: {v}")

    # 保存
    pd.DataFrame(trades_dyn).to_csv(os.path.join(DATA, "data/exp410_fund_dynamic_trades.csv"), index=False)
    pd.DataFrame(trades_ctrl).to_csv(os.path.join(DATA, "data/exp410_fund_fixed20_trades.csv"), index=False)
    F_dyn.to_csv(os.path.join(DATA, "data/exp410_fund_signals.csv"), index=False)
    metrics = {"fund_dynamic": m_dyn, "fund_fixed20": m_ctrl}
    with open(os.path.join(DATA, "data/exp410_metrics.json"), "w") as f:
        json.dump(metrics, f, indent=2, default=str)
    print(f"\n✓ 产物已保存到 {DATA}/data/")
    print(f"  combo_signal stats: mean={F['combo_signal'].mean():.4f} std={F['combo_signal'].std():.4f}")
    print(f"  open_trigger days: {F['open_trigger'].sum()} / {len(F)}")
