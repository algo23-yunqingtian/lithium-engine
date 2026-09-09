"""
exp417 模块2: 时序逻辑专项审计 — shift-equivariance 校验
原理: 若 HAM 信号链无未来函数, 则在输入序列末尾追加/删除若干天,
      前面所有历史日的信号输出应保持不变 (shift-equivariance: 平移等变性)。
      若存在未来函数, 追加未来数据会改变历史信号。

单元测试设计:
  1. 取全量 HAM 因子表, 计算完整信号链, 记录目标日的 disagreement/deviation/signal_dir。
  2. 截断到目标日及之前 (删除目标日之后所有未来数据), 重新计算信号链。
  3. 若目标日信号完全一致 → 无前视 (shift-equivariant)。
  4. 对多个目标日批量验证, 任一不一致即报告未来函数。

同时区分两类风险:
  ① 代码层面未来函数 bug (本测试直接检验)
  ② 参数历史拟合/因子漂移 (本测试不检验, 属模型理论风险, 见报告§3)
"""
import os
import sys
import numpy as np
import pandas as pd

MODEL = "/home/ubuntu/lithium-engine/model_ham"
for d in ["exp409_ham_dynamic", "exp410_fund_dynamic", "exp411_combined"]:
    sys.path.insert(0, os.path.join(MODEL, d))

import exp409_ham_dynamic as E409

EXP_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(EXP_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)


def compute_full_chain(df):
    """计算完整 HAM 信号链 (compute_system_deviation → aux → ham_signals), 返回信号列"""
    df = E409.compute_system_deviation(df.copy())
    df = E409.compute_aux_signals(df)
    df = E409.load_gmm_states(df)
    df = E409.compute_ham_signals(df)
    return df.reset_index(drop=True)[["date", "disagreement", "deviation", "signal_dir",
                                       "main_trigger", "open_signal", "position_size"]]


def shift_equivariance_test(df_full, test_dates):
    """
    对多个目标日做 shift-equivariance 校验:
    截断到目标日及之前, 重算信号链, 与全量信号对比目标日。
    """
    full_signals = compute_full_chain(df_full)
    full_signals = full_signals.set_index("date")

    rows = []
    all_pass = True
    for td in test_dates:
        # 截断到目标日及之前
        df_trunc = df_full[df_full["date"] <= td].reset_index(drop=True)
        trunc_signals = compute_full_chain(df_trunc).set_index("date")
        if td not in trunc_signals.index:
            rows.append({"date": str(td.date()), "status": "SKIP(无数据)"})
            continue
        fs = full_signals.loc[td]
        ts = trunc_signals.loc[td]
        checks = {}
        for col in ["disagreement", "deviation", "signal_dir"]:
            fv, tv = float(fs[col]), float(ts[col])
            checks[col] = abs(fv - tv) <= 1e-9
        for col in ["main_trigger", "open_signal"]:
            checks[col] = bool(fs[col]) == bool(ts[col])
        checks["position_size"] = abs(float(fs["position_size"]) - float(ts["position_size"])) <= 1e-12
        ok = all(checks.values())
        all_pass = all_pass and ok
        rows.append({
            "date": str(td.date()),
            "disagreement_full": round(float(fs["disagreement"]), 6),
            "disagreement_trunc": round(float(ts["disagreement"]), 6),
            "deviation_full": round(float(fs["deviation"]), 6),
            "deviation_trunc": round(float(ts["deviation"]), 6),
            "signal_dir": int(fs["signal_dir"]),
            "main_trigger": bool(fs["main_trigger"]),
            "open_signal": bool(fs["open_signal"]),
            "position_size": float(fs["position_size"]),
            "PASS": ok,
        })
    return pd.DataFrame(rows), all_pass


def main():
    print("=" * 70)
    print("exp417 模块2: 时序逻辑专项审计 — shift-equivariance 校验")
    print("=" * 70)

    df_full = E409.load_ham_factors()
    df_full = df_full.sort_values("date").reset_index(drop=True)
    print(f"\n[数据] HAM 因子表 {df_full.date.min().date()} ~ {df_full.date.max().date()}, "
          f"{len(df_full)} 行")

    # 选取多个目标日 (覆盖信号活跃期, 跨不同日期, 均为非NaN有效信号)
    test_dates = [
        pd.Timestamp("2024-04-01"), pd.Timestamp("2024-07-01"),
        pd.Timestamp("2024-10-15"), pd.Timestamp("2025-02-01"),
        pd.Timestamp("2025-05-20"), pd.Timestamp("2025-11-20"),
        pd.Timestamp("2026-02-01"), pd.Timestamp("2026-07-01"),
    ]

    df_res, all_pass = shift_equivariance_test(df_full, test_dates)
    df_res.to_csv(os.path.join(DATA_DIR, "exp417_shift_equivariance_test.csv"), index=False)

    print("\n[Shift-equivariance 校验] (截断未来数据后历史信号是否不变)")
    print(f"{'日期':<12}{'disagree(全)':>13}{'disagree(截)':>13}{'deviation':>10}"
          f"{'方向':>6}{'主触发':>7}{'开仓':>6}{'仓位':>6}{'结果':>6}")
    for _, r in df_res.iterrows():
        print(f"{r['date']:<12}{r.get('disagreement_full',''):>13}"
              f"{r.get('disagreement_trunc',''):>13}{r.get('deviation_full',''):>10}"
              f"{r.get('signal_dir',''):>6}{str(r.get('main_trigger','')):>7}"
              f"{str(r.get('open_signal','')):>6}{r.get('position_size',''):>6}"
              f"{'✓' if r.get('PASS') else '✗':>6}")

    print(f"\n[结论] {'全部通过 ✓ — 无未来函数' if all_pass else '存在失败 ✗ — 检测未来函数!'}")

    # shift-equivariance 数学原理说明
    print("\n[Shift-equivariance 原理]")
    print("  定义: 对任意时间序列 x[0..n], 信号函数 S(x) 满足 shift-equivariance 当且仅当")
    print("  S(x[0..k])[k] == S(x[0..n])[k] 对任意 n>k 成立。")
    print("  即: 目标日 k 的信号只依赖 x[0..k], 不受 x[k+1..n] 未来数据影响。")
    print("  测试方法: 截断未来数据(只保留 [0..k]), 重算 S, 与全量 S 对比第 k 项。")
    print("  全部一致 → 信号链严格因果, 无未来函数。")

    # 风险区分
    print("\n[两类风险区分]")
    print("  ① 代码层面未来函数 bug: 本测试直接检验。结论: "
          f"{'未检测到未来函数' if all_pass else '检测到未来函数!'}")
    print("  ② 参数历史拟合/因子漂移: 本测试不检验(属模型理论假设风险)。")
    print("     参数 alpha/beta/gamma/W 在碳酸锂样本内选定, 存在过拟合风险;")
    print("     HAM 因子在碳酸锂外样本衰减(exp416 §5.3), 印证因子漂移风险。")

    return all_pass, df_res


if __name__ == "__main__":
    main()
