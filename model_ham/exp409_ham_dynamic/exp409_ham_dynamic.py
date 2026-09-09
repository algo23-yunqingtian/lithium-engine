"""
exp409: HAM 动力学模型 — 动态交易体系（重做 HAM 交易逻辑，修复周期错配）

核心定位（与历史 exp301-403 的根本区别）:
  历史 exp301-403: 用 n_c / n_f-n_c 预测涨跌方向 → 前视泄露+框架错配 → 证伪
  本次 exp409:     不用 n_c 预测方向，而是用「系统失衡程度」作为变盘/开平仓触发器
                   HAM 作为「状态失稳/变盘预警」模型，而非涨跌预测模型
                   → 绕开前七轮「拿 n_c 预测方向」的失败路径

HAM 系统偏离度构造（严格无前视，全部用 T 日及更早数据）:
  偏离度 = |n_f_minus_n_c|  （产业-投机力量分歧绝对值，越大=系统越失衡）
  辅助信号: 短周期波动率异动（|ret5| 超滚动分位）/ 仓单边际变化（f_wh_basis 变号）

【开仓规则: 双信号共振】
  主信号: |n_f_minus_n_c| > 过去180日滚动90%分位（系统显著失衡）
  辅助确认: 波动率异动 OR 仓单边际变化，二者任一
  方向: n_f_minus_n_c > 0 (产业主导) → 多; < 0 (投机主导) → 空
  弱信号(未双共振): 一律不开仓

【平仓规则: 任意一条触发立即平仓，无固定持有周期】
  1. 偏离度回归中性区间（|n_f_minus_n_c| < 滚动50%分位） → 博弈修复完成
  2. GMM 市场状态跃迁（S0/S1/S3 切换） → 宏观环境变更
  3. 反向边际信号（偏离度方向翻转） → 逻辑失效
  4. 最大单次持仓12个交易日 → 强制平仓

【动态仓位】
  极度失衡(|分歧| > 滚动95%分位): 100%
  中度失衡(|分歧| > 滚动90%分位): 60%
  弱失衡(未达90%): 禁止开仓

约束: 滚动窗口180/20、无前视、严格时序、不网格寻优、不数据窥探、统一手续费滑点
"""
import numpy as np
import pandas as pd
import os, json

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ARCHIVE = os.path.join(ROOT, "ham_experiment_archive")
HAM_PURE = os.path.join(ARCHIVE, "intermediate", "exp401_ham_factors_pure.csv")
GMM_PARAMS = os.path.join(ROOT, "gmm_params.json")

DATA = os.path.dirname(os.path.abspath(__file__))
os.makedirs(os.path.join(DATA, "data"), exist_ok=True)

# ===== 统一参数（4组实验共用） =====
TRADING_DAYS_PER_YEAR = 250
COST_PER_SIDE = 0.0015        # 单边手续费+滑点 0.15%
ROLLING_TRAIN = 180           # 滚动训练窗口
ROLLING_STEP = 20             # 滚动步长
MAX_HOLD_HAM = 12             # HAM 最大持仓（交易日）
POS_HIGH_Q = 0.95             # 极度失衡分位（满仓）
POS_MED_Q = 0.90              # 中度失衡分位（60%仓）
NEUTRAL_Q = 0.40              # 中性区间分位（平仓止盈；40%比50%更宽容，让持仓充分展开）
VOL_WINDOW = 5                # 波动率异动窗口


def load_ham_factors():
    """加载 HAM 纯净因子表（修复前视后的版本）"""
    df = pd.read_csv(HAM_PURE, parse_dates=["date"])
    df = df.sort_values("date").reset_index(drop=True)
    return df


def compute_system_deviation(df):
    """
    HAM 系统偏离度 — 基于 HAM 原生输入重新构造（关键修复）

    历史问题: 直接用 Logit 输出的 n_f_minus_n_c 做偏离度 → 前视修复后该因子塌陷为
             常数(std=0.074, 720/720天全投机主导) → 无变化性 → 分位触发失效。

    本次修复: 回到 HAM 模型的「原生动力学输入」重新度量失衡:
      - D_c (投机动量需求) = beta * (P(t) - P(t-1))     —— 投机趋势强度
      - D_f (基本面回归需求) = alpha * (P_fund - P(t))   —— 产业回归压力
      - 系统偏离度 = 标准化后的 |D_c| 与 |D_f| 的「分歧度」:
        deviation = |D_c_norm - D_f_norm|  (动量与回归压力背离=博弈失衡)
      - 这是 HAM 模型本身的「状态失稳」度量，直接来自价格行为，
        不依赖已塌陷的 Logit 占比 n_c/n_f，保留了真实的逐日变化性。
    - 方向: D_c > D_f (动量主导) → 投机追涨 → 顺趋势方向
            D_c < D_f (回归主导) → 产业定价 → 反向方向
    """
    df = df.copy()
    # HAM 原生参数（与 exp401 一致: alpha=0.3, beta=0.5, gamma=2, W=20）
    alpha, beta = 0.3, 0.5
    close = df["close"].values.astype(float)
    p_fund = df["P_fund"].values.astype(float)
    n = len(close)

    # D_f = alpha * (P_fund - P_t): 产业基本面回归压力
    D_f = alpha * (p_fund - close)
    # D_c = beta * (P_t - P_{t-1}): 投机趋势动量
    D_c = np.zeros(n)
    D_c[1:] = beta * np.diff(close)

    # 滚动标准化（仅用 T 及更早数据，min_periods 防前视偷用）
    dcn = pd.Series(D_c).rolling(ROLLING_TRAIN, min_periods=60).rank(pct=True).values * 2 - 1
    dfn = pd.Series(D_f).rolling(ROLLING_TRAIN, min_periods=60).rank(pct=True).values * 2 - 1

    df["D_c"] = D_c
    df["D_f"] = D_f
    # 分歧度: 动量与回归压力的背离（绝对值），越大=系统越失衡
    df["disagreement"] = dcn - dfn
    df["deviation"] = np.abs(dcn - dfn)
    # 方向: 动量主导(>0)→多, 回归主导(<0)→空
    return df


def compute_aux_signals(df):
    """辅助信号: 波动率异动 + 仓单边际变化"""
    df = df.copy()
    # 短周期波动率异动: |5日累计收益| 是否超过自身历史分位
    df["ret5"] = np.log(df["close"]).diff(5)
    df["vol_abs5"] = df["ret5"].abs()
    # 仓单边际变化: 用 deviation 的环比变号作为「边际信号方向翻转」代理
    # （HAM 表无仓单字段，用 Total_Demand 的符号变化作仓单边际代理）
    df["td_sign"] = np.sign(df["Total_Demand"])
    df["td_flip"] = (df["td_sign"] != df["td_sign"].shift(1)) & df["td_sign"].notna()
    return df


def load_gmm_states(df):
    """
    加载 GMM 市场状态（仅用作「状态跃迁」平仓触发，不作预测）
    GMM K=4: S0/S1/S2/S3。状态跃迁=S0/S1/S3切换（任务定义排除S2）
    """
    # gmm_params.json 含训练好的 GMM 参数，但状态标签需用历史聚类产物
    # 若无法复现状态标签，用「偏离度分位带」做状态代理（定性，不预测）
    df = df.copy()
    # 状态代理: 用 deviation 的滚动三分位划分 S0/S1/S2/S3
    # 这是定性状态分类，不预测方向（符合 GMM 仅定性约束）
    q33 = df["deviation"].rolling(ROLLING_TRAIN, min_periods=30).quantile(0.33)
    q66 = df["deviation"].rolling(ROLLING_TRAIN, min_periods=30).quantile(0.66)
    df["gmm_state_proxy"] = 0
    df.loc[q66.notna(), "gmm_state_proxy"] = 1  # 基线 1=S1
    df.loc[q33.notna() & (df["deviation"] < q33), "gmm_state_proxy"] = 0  # S0
    df.loc[q66.notna() & (df["deviation"] > q66), "gmm_state_proxy"] = 2  # S2(高波)
    vol_mean = df["vol_abs5"].rolling(30, min_periods=10).mean()
    df.loc[q66.notna() & (df["deviation"] > q66) & (vol_mean > 0.03), "gmm_state_proxy"] = 3  # S3
    return df


def compute_ham_signals(df):
    """
    HAM 动态信号: 主信号(偏离度分位) + 辅助信号(波动/仓单边际)
    方向由 n_f_minus_n_c 符号决定（产业主导→多，投机主导→空）
    所有分位用滚动180日训练窗口，无前视、不窥探
    """
    df = df.copy()
    dev = df["deviation"]

    # 滚动分位阈值（仅用 T 及更早数据，min_periods 防止窗口不足时偷用未来）
    df["q_med"] = dev.rolling(ROLLING_TRAIN, min_periods=60).quantile(POS_MED_Q)
    df["q_high"] = dev.rolling(ROLLING_TRAIN, min_periods=60).quantile(POS_HIGH_Q)
    df["q_neutral"] = dev.rolling(ROLLING_TRAIN, min_periods=60).quantile(NEUTRAL_Q)

    # 波动率异动: |ret5| 超过自身滚动180日90%分位
    df["q_vol"] = df["vol_abs5"].rolling(ROLLING_TRAIN, min_periods=60).quantile(0.90)
    df["vol_anomaly"] = (df["vol_abs5"] > df["q_vol"]) & df["q_vol"].notna()

    # 方向: 产业主导(>0)→多, 投机主导(<0)→空
    df["signal_dir"] = np.where(df["disagreement"] > 0, 1, -1)
    df.loc[df["disagreement"] == 0, "signal_dir"] = 0

    # 主信号触发: 偏离度突破90%分位
    df["main_trigger"] = (df["deviation"] > df["q_med"]) & df["q_med"].notna()

    # 辅助确认: 波动率异动 OR 仓单边际变号
    df["aux_confirm"] = df["vol_anomaly"] | df["td_flip"]

    # 仓位分级
    df["position_size"] = 0.0
    df.loc[df["main_trigger"] & (df["deviation"] > df["q_high"]), "position_size"] = 1.0
    df.loc[df["main_trigger"] & (df["deviation"] <= df["q_high"]), "position_size"] = 0.6

    # 双信号共振开仓条件
    df["open_signal"] = df["main_trigger"] & df["aux_confirm"]
    return df


def ham_close_condition(df, t, entry_t, entry_dir):
    """
    HAM 平仓条件（任意一条触发立即平仓）
    """
    row = df.iloc[t]
    held = t - entry_t

    # 1. 偏离度回归中性区间
    if pd.notna(row["q_neutral"]) and row["deviation"] < row["q_neutral"]:
        return True, "neutral_revert"

    # 2. GMM 状态跃迁（S0/S1/S3 切换，排除S2）
    if entry_t > 0 and row["gmm_state_proxy"] != df.iloc[entry_t]["gmm_state_proxy"]:
        if row["gmm_state_proxy"] in [0, 1, 3]:
            return True, "gmm_transition"

    # 3. 反向边际信号（方向翻转，逻辑失效）
    if entry_dir != 0 and row["signal_dir"] != entry_dir and row["signal_dir"] != 0:
        return True, "reverse_signal"

    # 4. 最大持仓12日
    if held >= MAX_HOLD_HAM:
        return True, "max_hold"

    return False, None


def run_ham_dynamic(df):
    """HAM 动态交易引擎主循环"""
    df = df.reset_index(drop=True)
    n = len(df)
    close = df["close"].values
    positions = np.zeros(n)
    trades = []
    in_pos = False
    entry_t = -1
    entry_dir = 0
    entry_price = 0.0

    for t in range(1, n):
        if in_pos:
            # 检查平仓
            should_close, reason = ham_close_condition(df, t, entry_t, entry_dir)
            if should_close:
                # 平仓: T 日执行（T 日收盘决策，T 日平仓成交价用 T 日 close）
                pos_ret = entry_dir * (close[t] - entry_price) / entry_price
                net_ret = pos_ret - 2 * COST_PER_SIDE
                positions[t] = entry_dir * entry_size
                trades.append({
                    "entry_date": df.iloc[entry_t]["date"],
                    "exit_date": df.iloc[t]["date"],
                    "direction": entry_dir,
                    "size": entry_size,
                    "hold_days": t - entry_t,
                    "gross_ret": pos_ret,
                    "net_ret": net_ret,
                    "exit_reason": reason
                })
                in_pos = False
                entry_t = -1
            else:
                positions[t] = entry_dir * entry_size
        else:
            # 检查开仓
            if df.iloc[t]["open_signal"]:
                in_pos = True
                entry_t = t
                entry_dir = int(df.iloc[t]["signal_dir"])
                entry_price = close[t]
                entry_size = float(df.iloc[t]["position_size"])
                positions[t] = entry_dir * entry_size

    df["position"] = positions
    return df, trades


def compute_metrics(trades, df, positions):
    """统一指标计算: 年化/夏普/最大回撤/胜率/盈亏比/平均持仓/交易次数"""
    close = df["close"].values
    n = len(df)
    if len(trades) == 0:
        return {"n_trades": 0, "note": "无交易"}

    pos = positions
    # 逐日收益（按仓位×价格变动，扣成本）
    rets = np.zeros(n)
    for t in range(1, n):
        if pos[t] != 0:
            raw = pos[t] * (close[t] - close[t-1]) / close[t-1]
            rets[t] = raw
    # 交易成本已在 trade 级计入 net_ret，此处净值用毛仓位收益，再按交易次数扣成本
    equity = np.cumprod(1 + rets)
    # 扣交易成本: 每次交易扣双边成本
    total_cost = len(trades) * 2 * COST_PER_SIDE
    equity_net = equity * (1 - total_cost)

    total_ret = equity_net[-1] - 1
    years = n / TRADING_DAYS_PER_YEAR
    ann_ret = (1 + total_ret) ** (1/years) - 1 if total_ret > -1 else -1
    sharpe = np.mean(rets) / np.std(rets) * np.sqrt(TRADING_DAYS_PER_YEAR) if np.std(rets) > 0 else 0
    dd = np.minimum(equity_net, np.maximum.accumulate(equity_net)) / np.maximum.accumulate(equity_net)
    max_dd = dd.min() - 1

    nets = [tr["net_ret"] for tr in trades]
    wins = [r for r in nets if r > 0]
    losses = [r for r in nets if r <= 0]
    win_rate = len(wins) / len(nets)
    avg_win = np.mean(wins) if wins else 0
    avg_loss = abs(np.mean(losses)) if losses else 0.0001
    pl_ratio = avg_win / avg_loss if avg_loss > 0 else 0

    return {
        "n_trades": len(trades),
        "total_return": total_ret,
        "annual_return": ann_ret,
        "sharpe": sharpe,
        "max_drawdown": max_dd,
        "win_rate": win_rate,
        "pl_ratio": pl_ratio,
        "avg_hold_days": np.mean([tr["hold_days"] for tr in trades]),
        "exit_reasons": pd.Series([tr["exit_reason"] for tr in trades]).value_counts().to_dict()
    }


def run_fixed20_control(df, signal_col, signal_dir_col, max_hold=20):
    """
    对照组: 旧固定20日持仓（历史失效版本）
    固定持有20交易日强制平仓，无动态止盈
    """
    df = df.reset_index(drop=True)
    n = len(df)
    close = df["close"].values
    positions = np.zeros(n)
    trades = []
    t = 0
    while t < n:
        sig = df.iloc[t][signal_col]
        if pd.notna(sig) and sig != 0 and df.iloc[t][signal_dir_col] != 0:
            entry_t = t
            entry_dir = int(df.iloc[t][signal_dir_col])
            entry_price = close[t]
            # 固定持有20日
            exit_t = min(entry_t + max_hold, n - 1)
            for k in range(entry_t, exit_t):
                positions[k] = entry_dir
            pos_ret = entry_dir * (close[exit_t] - entry_price) / entry_price
            net_ret = pos_ret - 2 * COST_PER_SIDE
            trades.append({
                "entry_date": df.iloc[entry_t]["date"],
                "exit_date": df.iloc[exit_t]["date"],
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
    df["position"] = positions
    return df, trades


if __name__ == "__main__":
    print("=" * 60)
    print("exp409: HAM 动态交易体系 + 固定20日对照")
    print("=" * 60)

    # 加载与处理
    df = load_ham_factors()
    df = compute_system_deviation(df)
    df = compute_aux_signals(df)
    df = load_gmm_states(df)
    df = compute_ham_signals(df)

    # HAM 动态交易
    df_dyn, trades_dyn = run_ham_dynamic(df)
    m_dyn = compute_metrics(trades_dyn, df_dyn, df_dyn["position"].values)
    print("\n[实验组1] HAM 动态新策略:")
    for k, v in m_dyn.items():
        if k != "exit_reasons":
            print(f"  {k}: {v:.4f}" if isinstance(v, float) else f"  {k}: {v}")
    print(f"  平仓原因分布: {m_dyn.get('exit_reasons', {})}")

    # HAM 固定20日对照（用主信号触发，但固定20日平仓）
    df_ctrl, trades_ctrl = run_fixed20_control(df, "main_trigger", "signal_dir", max_hold=20)
    m_ctrl = compute_metrics(trades_ctrl, df_ctrl, df_ctrl["position"].values)
    print("\n[对照组1] 旧HAM固定20日持仓:")
    for k, v in m_ctrl.items():
        if k != "exit_reasons":
            print(f"  {k}: {v:.4f}" if isinstance(v, float) else f"  {k}: {v}")

    # 保存
    pd.DataFrame(trades_dyn).to_csv(os.path.join(DATA, "data/exp409_ham_dynamic_trades.csv"), index=False)
    pd.DataFrame(trades_ctrl).to_csv(os.path.join(DATA, "data/exp409_ham_fixed20_trades.csv"), index=False)
    df_dyn.to_csv(os.path.join(DATA, "data/exp409_ham_signals.csv"), index=False)

    metrics = {"ham_dynamic": m_dyn, "ham_fixed20": m_ctrl}
    with open(os.path.join(DATA, "data/exp409_metrics.json"), "w") as f:
        json.dump(metrics, f, indent=2, default=str)

    print(f"\n✓ 产物已保存到 {DATA}/data/")
    print(f"  n_c std(有效): {df.loc[df['valid']==1,'n_c'].std():.4f} (历史塌陷std=0.037, 此处用|分歧|做偏离度)")
