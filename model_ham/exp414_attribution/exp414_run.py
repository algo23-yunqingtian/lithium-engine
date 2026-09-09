"""
exp414: 消融归因实验 — 运行脚本

模块1 HAM信号消融对照 (A=原版 vs B=同频率随机噪声, 多种子)
模块2 整体组合消融 (T1=完整 vs T2=剔HAM)

底层零改动: 完全复用 exp409/410/411/413 引擎, 仅做信号替换与目标注入。
"""
import os
import json
import numpy as np
import pandas as pd
import sys

EXP = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(EXP, "data")
os.makedirs(DATA, exist_ok=True)

sys.path.insert(0, EXP)
import exp414_engine as ENG

START, END = ENG.START, ENG.END
COST = ENG.COST_PER_SIDE
N_NOISE_SEEDS = 30
print("=" * 76)
print("exp414: 消融归因实验")
print("  区间 %s ~ %s | 成本 %.2f%%/边 | exp412 V4风控参数固定 | 噪声种子 %d"
      % (START.date(), END.date(), COST * 100, N_NOISE_SEEDS))
print("=" * 76)

# ============================================================
# 模块1: HAM 信号消融
# ============================================================
print("\n" + "=" * 76)
print("模块1: HAM 信号消融对照 (A=原版 vs B=随机噪声)")
print("=" * 76)

r1 = ENG.run_module1(seed=42, n_noise_seeds=N_NOISE_SEEDS)
m_A = r1["A"]
b_df = r1["B_df"]

metrics_cols = ["annual_return", "sharpe_full", "max_drawdown", "calmar",
                "pl_ratio", "n_trades", "win_rate", "total_return"]

# 模块1 主结果表
rows_m1 = [{"group": "A_exp409_HAM原版", **{k: m_A[k] for k in metrics_cols}}]
rows_m1.append({
    "group": "B_随机噪声_均值",
    **{k: float(b_df[k].mean()) for k in ["annual_return", "sharpe_full",
                                          "max_drawdown", "calmar", "pl_ratio", "n_trades", "win_rate"]},
    "total_return": float(b_df["total_return"].mean()),
})
rows_m1.append({
    "group": "B_随机噪声_中位",
    **{k: float(b_df[k].median()) for k in ["annual_return", "sharpe_full",
                                            "max_drawdown", "calmar", "pl_ratio", "n_trades", "win_rate"]},
    "total_return": float(b_df["total_return"].median()),
})
rows_m1.append({
    "group": "B_随机噪声_最好",
    **{k: float(b_df[k].max()) for k in ["annual_return", "sharpe_full", "calmar", "pl_ratio"]},
    "max_drawdown": float(b_df["max_drawdown"].max()),
    "n_trades": float(b_df["n_trades"].max()),
    "win_rate": float(b_df["win_rate"].mean()),
    "total_return": float(b_df["total_return"].max()),
})
df_m1 = pd.DataFrame(rows_m1)

# 模块1 多种子噪声分布(明细)
df_m1_detail = b_df.copy().reset_index(drop=True)
df_m1_detail.insert(0, "noise_seed", range(1, len(df_m1_detail) + 1))

# 超额收益量化
excess_ann = m_A["annual_return"] - b_df["annual_return"].mean()
excess_sharpe = m_A["sharpe_full"] - b_df["sharpe_full"].mean()
excess_calmar = m_A["calmar"] - b_df["calmar"].mean()
# B组噪声中有多少种子的年化超过A组(显著性)
n_beat = int((b_df["annual_return"] > m_A["annual_return"]).sum())
n_sharpe_beat = int((b_df["sharpe_full"] > m_A["sharpe_full"]).sum())

print("\n[A组 exp409原版]: 年化%.3f 夏普%.3f 回撤%.3f Calmar%.3f 盈亏比%.3f 交易%d"
      % (m_A["annual_return"], m_A["sharpe_full"], m_A["max_drawdown"],
         m_A["calmar"], m_A["pl_ratio"], m_A["n_trades"]))
print("[B组 噪声均值]:    年化%.3f 夏普%.3f 回撤%.3f Calmar%.3f 盈亏比%.3f 交易%.1f"
      % (b_df["annual_return"].mean(), b_df["sharpe_full"].mean(),
         b_df["max_drawdown"].mean(), b_df["calmar"].mean(),
         b_df["pl_ratio"].mean(), b_df["n_trades"].mean()))
print("[B组 噪声最好]:    年化%.3f 夏普%.3f 回撤%.3f Calmar%.3f"
      % (b_df["annual_return"].max(), b_df["sharpe_full"].max(),
         b_df["max_drawdown"].max(), b_df["calmar"].max()))
print("\n[HAM信号超额]: 年化 +%.3f | 夏普 +%.3f | Calmar +%.3f"
      % (excess_ann, excess_sharpe, excess_calmar))
print("  噪声种子中击败A组年化的比例: %d/%d = %.1f%%" % (n_beat, N_NOISE_SEEDS, 100.0 * n_beat / N_NOISE_SEEDS))
print("  噪声种子中击败A组夏普的比例: %d/%d = %.1f%%" % (n_sharpe_beat, N_NOISE_SEEDS, 100.0 * n_sharpe_beat / N_NOISE_SEEDS))

# ============================================================
# 模块2: 整体组合消融
# ============================================================
print("\n" + "=" * 76)
print("模块2: 整体组合消融 (T1=完整 vs T2=剔HAM)")
print("=" * 76)

r2 = ENG.run_module2()
m_T1 = r2["T1"]
m_T2 = r2["T2"]

rows_m2 = [
    {"group": "T1_完整组合_HAM+基本面+风控", **{k: m_T1[k] for k in metrics_cols}},
    {"group": "T2_剔HAM_仅基本面+风控", **{k: m_T2[k] for k in metrics_cols}},
]
df_m2 = pd.DataFrame(rows_m2)

# HAM 贡献占比
contrib_ann = (m_T1["annual_return"] - m_T2["annual_return"]) / m_T1["annual_return"] if m_T1["annual_return"] > 0 else 0
contrib_sharpe = (m_T1["sharpe_full"] - m_T2["sharpe_full"]) / m_T1["sharpe_full"] if m_T1["sharpe_full"] > 0 else 0
contrib_calmar = (m_T1["calmar"] - m_T2["calmar"]) / m_T1["calmar"] if m_T1["calmar"] > 0 else 0

print("\n[T1 完整组合]: 年化%.3f 夏普%.3f 回撤%.3f Calmar%.3f 盈亏比%.3f 交易%d"
      % (m_T1["annual_return"], m_T1["sharpe_full"], m_T1["max_drawdown"],
         m_T1["calmar"], m_T1["pl_ratio"], m_T1["n_trades"]))
print("[T2 剔HAM]:    年化%.3f 夏普%.3f 回撤%.3f Calmar%.3f 盈亏比%.3f 交易%d"
      % (m_T2["annual_return"], m_T2["sharpe_full"], m_T2["max_drawdown"],
         m_T2["calmar"], m_T2["pl_ratio"], m_T2["n_trades"]))
print("\n[HAM对组合贡献占比]: 年化 %.1f%% | 夏普 %.1f%% | Calmar %.1f%%"
      % (100 * contrib_ann, 100 * contrib_sharpe, 100 * contrib_calmar))

# ============================================================
# 模块2 补充: T1/T2 净值序列 + 交易日志
# ============================================================
idx_w = r2["idx_w"]
df_eq = pd.DataFrame({"date": idx_w.date,
                      "T1_equity": r2["eq_T1"],
                      "T2_equity": r2["eq_T2"],
                      "T1_pos": r2["pos_T1"],
                      "T2_pos": r2["pos_T2"]})
df_eq.to_csv(os.path.join(DATA, "exp414_daily_equity.csv"), index=False)

pd.DataFrame(r2["tr_T1"]).to_csv(os.path.join(DATA, "exp414_T1_trades.csv"), index=False)
pd.DataFrame(r2["tr_T2"]).to_csv(os.path.join(DATA, "exp414_T2_trades.csv"), index=False)

# ============================================================
# 保存 CSV
# ============================================================
df_m1.to_csv(os.path.join(DATA, "exp414_module1_ham_ablation.csv"), index=False)
df_m1_detail.to_csv(os.path.join(DATA, "exp414_module1_noise_seeds.csv"), index=False)
df_m2.to_csv(os.path.join(DATA, "exp414_module2_portfolio_ablation.csv"), index=False)

# 汇总 JSON
summary = {
    "backtest_window": {"start": str(START.date()), "end": str(END.date()),
                        "n_trading_days": int(len(idx_w))},
    "cost_per_side_pct": COST * 100,
    "risk_control": "exp412 V4固定 (dd_recover=0.05, dd_warn=0.12, dd_hard=0.18, "
                    "vol_upper=0.55, vol_scale=0.50)",
    "module1_ham_ablation": {
        "A_original": {k: round(float(m_A[k]), 4) for k in metrics_cols},
        "B_noise_mean": {k: round(float(b_df[k].mean()), 4)
                         for k in ["annual_return", "sharpe_full", "max_drawdown",
                                   "calmar", "pl_ratio", "n_trades", "win_rate"]},
        "B_noise_best": {k: round(float(b_df[k].max()), 4)
                         for k in ["annual_return", "sharpe_full", "calmar"]},
        "n_noise_seeds": N_NOISE_SEEDS,
        "excess_annual": round(float(excess_ann), 4),
        "excess_sharpe": round(float(excess_sharpe), 4),
        "excess_calmar": round(float(excess_calmar), 4),
        "noise_beat_A_ann_pct": round(float(n_beat / N_NOISE_SEEDS), 4),
        "noise_beat_A_sharpe_pct": round(float(n_sharpe_beat / N_NOISE_SEEDS), 4),
    },
    "module2_portfolio_ablation": {
        "T1_full": {k: round(float(m_T1[k]), 4) for k in metrics_cols},
        "T2_no_ham": {k: round(float(m_T2[k]), 4) for k in metrics_cols},
        "ham_contrib_annual_pct": round(float(100 * contrib_ann), 2),
        "ham_contrib_sharpe_pct": round(float(100 * contrib_sharpe), 2),
        "ham_contrib_calmar_pct": round(float(100 * contrib_calmar), 2),
    },
    "validation": {
        "T1_matches_exp413_baseline": bool(
            abs(m_T1["annual_return"] - 0.7608605903) < 1e-5 and
            abs(m_T1["calmar"] - 6.5841166832) < 1e-3),
    },
}
with open(os.path.join(DATA, "exp414_summary.json"), "w") as fp:
    json.dump(summary, fp, indent=2, ensure_ascii=False)

print("\n" + "=" * 76)
print("✓ 产物已保存到 %s" % DATA)
print("  exp414_module1_ham_ablation.csv  (A/B组主结果)")
print("  exp414_module1_noise_seeds.csv   (%d个噪声种子明细)" % N_NOISE_SEEDS)
print("  exp414_module2_portfolio_ablation.csv (T1/T2)")
print("  exp414_daily_equity.csv          (T1/T2净值+仓位)")
print("  exp414_T1_trades.csv / exp414_T2_trades.csv")
print("  exp414_summary.json")
print("=" * 76)
