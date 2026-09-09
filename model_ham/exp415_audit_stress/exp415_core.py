"""
exp415: 代码执行真实性核验 + HAM 隐藏风险压力测试

模块A: 代码执行真实性核验
  A1: 3组手工预设单元测试（白盒：用 numpy 独立重算整条信号链路，对比代码）
  A2: 完整逐笔交易日志 + 每日净值CSV + 随机抽5笔交易展示
  A3: exp412 基准强制复现校验（先复现全部指标，指标匹配才允许跑实验组）
模块B: HAM 隐藏风险专项压力测试
  B1: 资金容量/冲击成本测试（多档资金→非线性滑点→收益衰减曲线→容量上限）
  B2: 跳空流动性压力测试（隔夜大幅跳空→事前止损失效→单笔亏损上限）
  B3: 因子稳定性检验（滚动分段回测→超额收益稳定性→因子漂移）
  B4: 极端拥挤场景测试（多信号同向同时开仓→集中持仓风险）

底层零改动承诺：完全复用 exp409/410/411/413 引擎，不修改任何信号逻辑/阈值/风控参数。
"""
import os, sys, json
import numpy as np
import pandas as pd

MODEL = "/home/ubuntu/lithium-engine/model_ham"
sys.path.insert(0, os.path.join(MODEL, "exp409_ham_dynamic"))
sys.path.insert(0, os.path.join(MODEL, "exp410_fund_dynamic"))
sys.path.insert(0, os.path.join(MODEL, "exp411_combined"))
sys.path.insert(0, os.path.join(MODEL, "exp413_robustness"))
sys.path.insert(0, os.path.join(MODEL, "exp414_attribution"))

import exp409_ham_dynamic as E409
from exp411_combined import build_targets, price_series, extract_trades
import exp413_engine as E413
import exp414_engine as E414

EXP_DIR = os.path.join(MODEL, "exp415_audit_stress")
DATA_DIR = os.path.join(EXP_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)

START, END = pd.Timestamp("2024-02-01"), pd.Timestamp("2026-09-07")
TDPY = E409.TRADING_DAYS_PER_YEAR
COST = E409.COST_PER_SIDE
ROLLING_TRAIN = E409.ROLLING_TRAIN      # 180
POS_MED_Q, POS_HIGH_Q, NEUTRAL_Q = E409.POS_MED_Q, E409.POS_HIGH_Q, E409.NEUTRAL_Q
VOL_WINDOW = E409.VOL_WINDOW            # 5
MAX_HOLD = E409.MAX_HOLD_HAM            # 12


# ============================================================
# A3: 基准强制复现校验（最先执行，作为硬门槛）
# ============================================================
def a3_baseline_reproduce():
    """
    复现 exp412/exp414 历史指标，逐位比对。全部匹配才返回 True（才允许跑实验组）。
    历史记录（交接文档 exp414）：
      模块1 A组: 年化0.9099 夏普2.5976 回撤-0.1542 Calmar5.9015 盈亏比2.9887 40笔
      模块2 T1 : 年化0.7609 夏普2.6381 回撤-0.1156 Calmar6.5841 40笔
      模块2 T2 : 年化0.0833 夏普0.8158  回撤-0.0803 Calmar1.0374 7笔
    """
    # A组（HAM原版）
    df_orig = E414.build_ham_signal_frame()
    idx_full = df_orig.set_index("date").index.sort_values()
    mask = (idx_full >= START) & (idx_full <= END)
    idx_w = idx_full[mask]
    price_w = price_series(idx_w)
    pos_A, _ = E414.ham_group_positions(df_orig, mask_window=idx_w)
    m_A, _, _, _, _ = E414.unified_metrics(pos_A.values, price_w)

    # T1 / T2
    r2 = E414.run_module2()
    m_T1, m_T2 = r2["T1"], r2["T2"]

    expected = {
        "A":  {"annual_return": 0.9099, "sharpe_full": 2.5976, "max_drawdown": -0.1542,
               "calmar": 5.9015, "pl_ratio": 2.9887, "n_trades": 40},
        "T1": {"annual_return": 0.7609, "sharpe_full": 2.6381, "max_drawdown": -0.1156,
               "calmar": 6.5841, "pl_ratio": 2.9268, "n_trades": 40},
        "T2": {"annual_return": 0.0833, "sharpe_full": 0.8158, "max_drawdown": -0.0803,
               "calmar": 1.0374, "pl_ratio": 11.2019, "n_trades": 7},
    }
    actual = {"A": m_A, "T1": m_T1, "T2": m_T2}

    rows, all_ok = [], True
    for grp in ["A", "T1", "T2"]:
        for k, exp in expected[grp].items():
            act = actual[grp][k]
            if k == "n_trades":
                ok = int(act) == int(exp)
                diff = abs(act - exp)
            else:
                ok = abs(act - exp) < 5e-4   # 容忍末位四舍五入
                diff = abs(act - exp)
            all_ok = all_ok and ok
            rows.append({"group": grp, "metric": k, "expected": round(float(exp), 4),
                         "actual": round(float(act), 4), "abs_diff": round(diff, 6),
                         "PASS": ok})

    df_check = pd.DataFrame(rows)
    df_check.to_csv(os.path.join(DATA_DIR, "exp415_A3_baseline_reproduce.csv"), index=False)
    print("\n[A3] 基准复现校验:", "全部通过 ✓" if all_ok else "存在不匹配 ✗")
    print(df_check.to_string(index=False))
    return all_ok, actual, df_check


# ============================================================
# A1: 手工预设单元测试（白盒 numpy 重算）
# ============================================================
def manual_ham_chain(df_raw, target_date):
    """
    完全独立的白盒重算：仅用原始列 close/P_fund/Total_Demand + numpy/pandas 滚动，
    复刻 compute_system_deviation → compute_aux_signals → compute_ham_signals →
    run_ham_dynamic 的判定，返回目标日的信号/方向/仓位 + 若开仓则其平仓结果。
    不 import 任何 E409 计算函数，避免同源错误。
    """
    d = df_raw.copy().reset_index(drop=True)
    close = d["close"].values.astype(float)
    p_fund = d["P_fund"].values.astype(float)
    n = len(close)
    alpha, beta = 0.3, 0.5

    # --- compute_system_deviation ---
    D_f = alpha * (p_fund - close)
    D_c = np.zeros(n)
    D_c[1:] = beta * np.diff(close)
    dcn = pd.Series(D_c).rolling(ROLLING_TRAIN, min_periods=60).rank(pct=True).values * 2 - 1
    dfn = pd.Series(D_f).rolling(ROLLING_TRAIN, min_periods=60).rank(pct=True).values * 2 - 1
    disagreement = dcn - dfn
    deviation = np.abs(dcn - dfn)
    signal_dir = np.where(disagreement > 0, 1, -1)
    signal_dir = np.where(disagreement == 0, 0, signal_dir)

    # --- compute_aux_signals ---
    ret5 = np.log(close)
    ret5[1:] -= np.log(close[:-1])
    ret5[0] = np.nan
    ret5 = np.concatenate([[np.nan] * VOL_WINDOW, np.log(close[VOL_WINDOW:]) - np.log(close[:-VOL_WINDOW])]) if n > VOL_WINDOW else np.full(n, np.nan)
    vol_abs5 = np.abs(ret5)
    td_sign = np.sign(d["Total_Demand"].values)
    td_flip = (td_sign != np.roll(td_sign, 1)) & (~np.isnan(td_sign))
    td_flip[0] = False

    # --- compute_ham_signals ---
    dev_s = pd.Series(deviation)
    q_med = dev_s.rolling(ROLLING_TRAIN, min_periods=60).quantile(POS_MED_Q).values
    q_high = dev_s.rolling(ROLLING_TRAIN, min_periods=60).quantile(POS_HIGH_Q).values
    q_neutral = dev_s.rolling(ROLLING_TRAIN, min_periods=60).quantile(NEUTRAL_Q).values
    q_vol = pd.Series(vol_abs5).rolling(ROLLING_TRAIN, min_periods=60).quantile(0.90).values
    vol_anomaly = (vol_abs5 > q_vol) & (~np.isnan(q_vol))
    main_trigger = (deviation > q_med) & (~np.isnan(q_med))
    aux_confirm = vol_anomaly | td_flip
    open_signal = main_trigger & aux_confirm
    position_size = np.zeros(n)
    position_size[(main_trigger) & (deviation > q_high)] = 1.0
    position_size[(main_trigger) & (deviation <= q_high)] = 0.6

    t = d.index[d["date"] == target_date][0]
    return {
        "date": target_date, "close": close[t],
        "disagreement": disagreement[t], "deviation": deviation[t],
        "signal_dir": signal_dir[t],
        "main_trigger": bool(main_trigger[t]), "aux_confirm": bool(aux_confirm[t]),
        "open_signal": bool(open_signal[t]), "position_size": position_size[t],
        # 供平仓链用
        "_disagreement": disagreement, "_deviation": deviation, "_signal_dir": signal_dir,
        "_q_neutral": q_neutral, "_q_med": q_med, "_q_high": q_high,
    }


def a1_unit_tests():
    """
    3组手工预设样例。每组给定某交易日原始行情（该日之前完整历史），
    人工独立计算 HAM 信号/方向/仓位，对比代码输出。
    """
    df_raw = E409.load_ham_factors()
    # 跑代码（黑盒）得到信号表
    df_code = E409.compute_system_deviation(df_raw.copy())
    df_code = E409.compute_aux_signals(df_code)
    df_code = E409.load_gmm_states(df_code)
    df_code = E409.compute_ham_signals(df_code)
    df_code = df_code.set_index("date")

    # 3 个预设样例：多头满仓 / 空头60%仓 / 不开仓日
    samples = [
        pd.Timestamp("2024-04-01"),   # 多头 满仓 size=1.0
        pd.Timestamp("2024-08-14"),   # 空头 60%仓 size=0.6
        pd.Timestamp("2024-03-15"),   # 不开仓（main_trigger 或 aux 为假）
    ]

    results = []
    all_pass = True
    for sd in samples:
        manual = manual_ham_chain(df_raw, sd)
        code = df_code.loc[sd]
        checks = [
            ("disagreement", round(float(manual["disagreement"]), 6), round(float(code["disagreement"]), 6), 1e-5),
            ("deviation", round(float(manual["deviation"]), 6), round(float(code["deviation"]), 6), 1e-5),
            ("signal_dir", int(manual["signal_dir"]), int(code["signal_dir"]), 0),
            ("main_trigger", bool(manual["main_trigger"]), bool(code["main_trigger"]), 0),
            ("aux_confirm", bool(manual["aux_confirm"]), bool(code["aux_confirm"]), 0),
            ("open_signal", bool(manual["open_signal"]), bool(code["open_signal"]), 0),
            ("position_size", round(float(manual["position_size"]), 4), round(float(code["position_size"]), 4), 1e-6),
        ]
        grp_pass = True
        for name, mv, cv, tol in checks:
            if tol == 0:
                ok = (mv == cv)
            else:
                ok = (abs(float(mv) - float(cv)) <= tol)
            grp_pass = grp_pass and ok
            results.append({"sample_date": str(sd.date()), "field": name,
                            "manual": mv, "code": cv, "tol": tol, "PASS": ok})
        all_pass = all_pass and grp_pass

    df_res = pd.DataFrame(results)
    df_res.to_csv(os.path.join(DATA_DIR, "exp415_A1_unit_tests.csv"), index=False)
    print("\n[A1] 手工预设单元测试（白盒 numpy 重算 vs 代码）:",
          "全部通过 ✓" if all_pass else "存在偏差 ✗")
    print(df_res.to_string(index=False))
    return all_pass, df_res


if __name__ == "__main__":
    print("=" * 72)
    print("exp415: 代码真实性核验 + HAM 隐藏风险压力测试")
    print("=" * 72)
    ok3, actual, df3 = a3_baseline_reproduce()
    if not ok3:
        print("\n[FATAL] 基准复现未通过，按指令实验作废，终止。")
        sys.exit(1)
    ok1, df1 = a1_unit_tests()
    print("\n=== 模块A 汇总: 基准复现=%s | 单元测试=%s ===" % (ok3, ok1))
