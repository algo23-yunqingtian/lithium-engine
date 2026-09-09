"""
exp417: HAM 模型底层建模原理、数学形式与时序逻辑审计
模块4: 演示样例 — 选取1个历史交易日, 带入原始行情, 手工完整演算全套 HAM 公式。

本脚本从 HAM 因子表提取目标日及历史窗口的原始数据, 输出逐步演算所需的中间量,
供报告 §模块4 手工演算使用。演算不依赖代码黑盒, 用 numpy 逐步重算展示每步中间结果。
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

EXP_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(EXP_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)

# 目标演算日: 2024-04-01 (exp415 单元测试的多头满仓样例日)
TARGET = pd.Timestamp("2024-04-01")
ALPHA, BETA, GAMMA, W = 0.3, 0.5, 2, 20   # HAM 原生参数 (与 exp401/409 一致)
ROLLING_TRAIN = E409.ROLLING_TRAIN        # 180


def manual_full_chain(df_raw, target):
    """
    完全独立白盒重算 HAM 全套公式, 返回目标日每一步中间量。
    仅用 numpy/pandas, 不调用 E409 计算函数, 便于报告展示。
    """
    d = df_raw.copy().reset_index(drop=True)
    close = d["close"].values.astype(float)
    p_fund = d["P_fund"].values.astype(float)
    n = len(close)
    t = d.index[d["date"] == target][0]

    steps = {"target_date": str(target.date()), "t_index": int(t)}

    # ===== 步骤1: 需求函数 =====
    # D_f(t) = alpha * (P_fund(t) - P(t))   产业基本面回归压力
    D_f = ALPHA * (p_fund - close)
    # D_c(t) = beta * (P(t) - P(t-1))      投机趋势动量
    P_t_minus_1 = np.zeros(n)
    P_t_minus_1[1:] = close[:-1]
    P_t_minus_1[0] = close[0]
    D_c = BETA * (close - P_t_minus_1)
    steps["P_t"] = float(close[t])
    steps["P_fund_t"] = float(p_fund[t])
    steps["P_t_minus_1"] = float(close[t-1])
    steps["D_f_t"] = float(D_f[t])
    steps["D_c_t"] = float(D_c[t])

    # ===== 步骤2: 滚动收益 (窗口 [t-W:t]) =====
    price_change = np.zeros(n)
    price_change[1:] = np.diff(close)
    profit_f = np.zeros(n)
    profit_c = np.zeros(n)
    for i in range(W, n):
        w_Df = D_f[i-W:i]
        w_Dc = D_c[i-W:i]
        w_pc = price_change[i-W:i]   # [i-W:i] = P(i-W)..P(i-1), 不含 price_change[i]
        profit_f[i] = np.sum(w_Df * w_pc)
        profit_c[i] = np.sum(w_Dc * w_pc)
    steps["profit_f_t"] = float(profit_f[t])
    steps["profit_c_t"] = float(profit_c[t])
    # 展示窗口内的 price_change 范围
    steps["price_change_window"] = [float(price_change[t-W]), float(price_change[t-1])]

    # ===== 步骤3: Logit 策略切换权重 =====
    max_exp = 500
    sf = np.clip(GAMMA * profit_f, -max_exp, max_exp)
    sc = np.clip(GAMMA * profit_c, -max_exp, max_exp)
    mv = np.maximum(sf, sc)
    ef = np.exp(sf - mv)
    ec = np.exp(sc - mv)
    denom = np.where(ef + ec == 0, 1e-10, ef + ec)
    n_f = ef / denom
    n_c = ec / denom
    steps["gamma_profit_f"] = float(sf[t])
    steps["gamma_profit_c"] = float(sc[t])
    steps["exp_f"] = float(ef[t])
    steps["exp_c"] = float(ec[t])
    steps["n_f_t"] = float(n_f[t])
    steps["n_c_t"] = float(n_c[t])

    # ===== 步骤4: 系统偏离度 (disagreement) =====
    dcn = pd.Series(D_c).rolling(ROLLING_TRAIN, min_periods=60).rank(pct=True).values * 2 - 1
    dfn = pd.Series(D_f).rolling(ROLLING_TRAIN, min_periods=60).rank(pct=True).values * 2 - 1
    disagreement = dcn - dfn
    deviation = np.abs(dcn - dfn)
    steps["D_c_norm_t"] = float(dcn[t])
    steps["D_f_norm_t"] = float(dfn[t])
    steps["disagreement_t"] = float(disagreement[t])
    steps["deviation_t"] = float(deviation[t])

    # ===== 步骤5: 辅助信号 =====
    ret5 = pd.Series(np.log(close)).diff(5)
    vol_abs5 = ret5.abs()
    td_sign = np.sign((n_f * D_f + n_c * D_c))
    td_flip = (td_sign != np.roll(td_sign, 1)) & (~np.isnan(td_sign))
    td_flip[0] = False
    steps["ret5_t"] = float(ret5.iloc[t]) if pd.notna(ret5.iloc[t]) else 0.0
    steps["vol_abs5_t"] = float(vol_abs5.iloc[t]) if pd.notna(vol_abs5.iloc[t]) else 0.0
    steps["td_sign_t"] = float(td_sign[t]) if not np.isnan(td_sign[t]) else 0.0
    steps["td_flip_t"] = bool(td_flip[t])

    # ===== 步骤6: HAM 动态信号 + 仓位 =====
    dev = pd.Series(deviation)
    q_med = dev.rolling(ROLLING_TRAIN, min_periods=60).quantile(0.90).values
    q_high = dev.rolling(ROLLING_TRAIN, min_periods=60).quantile(0.95).values
    q_neutral = dev.rolling(ROLLING_TRAIN, min_periods=60).quantile(0.40).values
    q_vol = pd.Series(vol_abs5).rolling(ROLLING_TRAIN, min_periods=60).quantile(0.90).values
    vol_anomaly = (vol_abs5 > q_vol) & (~np.isnan(q_vol))
    signal_dir = np.where(disagreement > 0, 1, -1)
    signal_dir = np.where(disagreement == 0, 0, signal_dir)
    main_trigger = (deviation > q_med) & (~np.isnan(q_med))
    aux_confirm = vol_anomaly | td_flip
    position_size = np.zeros(n)
    position_size[(main_trigger) & (deviation > q_high)] = 1.0
    position_size[(main_trigger) & (deviation <= q_high)] = 0.6
    open_signal = main_trigger & aux_confirm
    steps["q_med_t"] = float(q_med[t]) if pd.notna(q_med[t]) else None
    steps["q_high_t"] = float(q_high[t]) if pd.notna(q_high[t]) else None
    steps["q_neutral_t"] = float(q_neutral[t]) if pd.notna(q_neutral[t]) else None
    steps["q_vol_t"] = float(q_vol[t]) if pd.notna(q_vol[t]) else None
    steps["signal_dir_t"] = int(signal_dir[t])
    steps["main_trigger_t"] = bool(main_trigger[t])
    steps["vol_anomaly_t"] = bool(vol_anomaly[t])
    steps["aux_confirm_t"] = bool(aux_confirm[t])
    steps["position_size_t"] = float(position_size[t])
    steps["open_signal_t"] = bool(open_signal[t])

    return steps


def main():
    print("=" * 70)
    print("exp417 模块4: HAM 全套公式手工演算")
    print(f"  目标日: {TARGET.date()} (exp415 单元测试的多头满仓样例日)")
    print("=" * 70)

    df_raw = E409.load_ham_factors()
    steps = manual_full_chain(df_raw, TARGET)

    # 输出逐步演算
    print(f"\n目标日 T = {steps['target_date']}, 数组索引 t = {steps['t_index']}")
    print(f"HAM 原生参数: alpha={ALPHA} beta={BETA} gamma={GAMMA} W={W} | 滚动窗口={ROLLING_TRAIN}")

    print("\n[步骤1] 需求函数")
    print(f"  P(t)={steps['P_t']:.2f}  P_fund(t)={steps['P_fund_t']:.2f}  P(t-1)={steps['P_t_minus_1']:.2f}")
    print(f"  D_f(t) = alpha*(P_fund-P) = {ALPHA}*({steps['P_fund_t']:.2f}-{steps['P_t']:.2f}) = {steps['D_f_t']:.4f}")
    print(f"  D_c(t) = beta*(P-P_{{t-1}}) = {BETA}*({steps['P_t']:.2f}-{steps['P_t_minus_1']:.2f}) = {steps['D_c_t']:.4f}")

    print("\n[步骤2] 滚动收益 (窗口 [t-W:t], 即 t-20..t-1)")
    print(f"  profit_f(t) = Σ D_f*ΔP over window = {steps['profit_f_t']:.4f}")
    print(f"  profit_c(t) = Σ D_c*ΔP over window = {steps['profit_c_t']:.4f}")
    print(f"  窗口ΔP范围: [{steps['price_change_window'][0]:.2f}, {steps['price_change_window'][1]:.2f}]")

    print("\n[步骤3] Logit 策略切换权重")
    print(f"  gamma*profit_f = {steps['gamma_profit_f']:.4f}  gamma*profit_c = {steps['gamma_profit_c']:.4f}")
    print(f"  exp_f={steps['exp_f']:.4f}  exp_c={steps['exp_c']:.4f}")
    print(f"  n_f(t) = exp_f/(exp_f+exp_c) = {steps['n_f_t']:.6f}")
    print(f"  n_c(t) = exp_c/(exp_f+exp_c) = {steps['n_c_t']:.6f}")

    print("\n[步骤4] 系统偏离度")
    print(f"  D_c_norm = {steps['D_c_norm_t']:.6f}  D_f_norm = {steps['D_f_norm_t']:.6f}")
    print(f"  disagreement = D_c_norm - D_f_norm = {steps['disagreement_t']:.6f}")
    print(f"  deviation = |disagreement| = {steps['deviation_t']:.6f}")

    print("\n[步骤5] 辅助信号")
    print(f"  vol_abs5(t) = {steps['vol_abs5_t']:.6f}  td_flip(t) = {steps['td_flip_t']}")

    print("\n[步骤6] HAM 动态信号 + 仓位分级")
    print(f"  q_med(90%)={steps['q_med_t']:.6f}  q_high(95%)={steps['q_high_t']:.6f}  q_neutral(40%)={steps['q_neutral_t']:.6f}")
    print(f"  main_trigger (deviation>{0.90}分位) = {steps['main_trigger_t']}  (deviation={steps['deviation_t']:.6f} vs q_med={steps['q_med_t']:.6f})")
    print(f"  vol_anomaly = {steps['vol_anomaly_t']}  aux_confirm = {steps['aux_confirm_t']}")
    print(f"  signal_dir = {steps['signal_dir_t']} ({'多头' if steps['signal_dir_t']>0 else '空头'})")
    print(f"  position_size = {steps['position_size_t']}  open_signal = {steps['open_signal_t']}")

    # 保存演算步骤数据
    steps_df = pd.DataFrame([steps]).T.reset_index()
    steps_df.columns = ["step", "value"]
    steps_df.to_csv(os.path.join(DATA_DIR, "exp417_manual_calculation_steps.csv"), index=False)

    # 保存目标日前后窗口的原始行情 (供报告展示输入数据)
    t_idx = steps["t_index"]
    window = df_raw.iloc[max(0, t_idx-5):t_idx+2].copy()
    window.to_csv(os.path.join(DATA_DIR, "exp417_manual_calc_input_window.csv"), index=False)

    print(f"\n✓ 演算步骤已保存: exp417_manual_calculation_steps.csv")
    print(f"✓ 输入窗口已保存: exp417_manual_calc_input_window.csv")

    return steps


if __name__ == "__main__":
    main()
