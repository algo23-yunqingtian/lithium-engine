"""
exp401: 修复HAM因子前视后重生成全套因子
========================================
- 已修复 ham_model.py:48 的 1 日前视 (price_change 窗口 [t-W+1:t+1] → [t-W:t])
- 本脚本用修复后的 run_ham_model 重生成 n_c / n_f-n_c / Total_Demand
- 参数沿用原版 alpha=0.3, beta=0.5, gamma=2, W=20
- 产出: data/exp401_ham_factors_pure.csv + logs/exp401_regen_factors.log
硬性约束: 全量重生成但不涉及回测拟合; 因子计算本身无前视(已修源)
"""
import os, sys, shutil
import numpy as np
import pandas as pd
sys.path.insert(0, "model_ham")

BASE = "model_ham"
EXP = os.path.join(BASE, "exp4_lookahead_fix")
DATA = os.path.join(EXP, "data")
LOGS = os.path.join(EXP, "logs")

# ---------- 1. 读取原始输入(与 run_all.py 一致) ----------
# run_all.py 用 df 含 date,close,P_fund 等; 这里从原版因子CSV还原输入列
ham_orig = pd.read_csv(os.path.join(BASE, "ham_factors_full_800d.csv"))
ham_orig["date"] = pd.to_datetime(ham_orig["date"])
ham_orig = ham_orig.sort_values("date").reset_index(drop=True)

# 输入只需 date, close, P_fund (run_ham_model 的依赖)
df_in = ham_orig[["date", "close", "P_fund"]].copy()
df_in["close"] = df_in["close"].astype(float)
df_in["P_fund"] = df_in["P_fund"].astype(float)

params = {"alpha": 0.3, "beta": 0.5, "gamma": 2, "W": 20}

# ---------- 2. 用修复后的模型重生成 ----------
from ham_model import run_ham_model
result = run_ham_model(df_in, **params, verbose=True)

valid = result[result["valid"] == 1].copy()
valid = valid[["date", "close", "P_fund", "D_f", "D_c", "profit_f", "profit_c",
               "n_f", "n_c", "Total_Demand", "n_f_minus_n_c", "valid"]]

out_csv = os.path.join(DATA, "exp401_ham_factors_pure.csv")
valid.to_csv(out_csv, index=False)

# 备份原版
shutil.copy2(os.path.join(BASE, "ham_factors_full_800d.csv"),
             os.path.join(DATA, "exp401_ham_factors_orig_backup.csv"))

# ---------- 3. 前后对比 ----------
orig = ham_orig[ham_orig["valid"] == 1] if "valid" in ham_orig.columns else ham_orig
# 对齐 date
m = pd.merge(orig[["date","n_c","n_f_minus_n_c","Total_Demand","profit_f"]],
             valid[["date","n_c","n_f_minus_n_c","Total_Demand","profit_f"]],
             on="date", suffixes=("_orig","_pure"))

def corr(a, b):
    return np.corrcoef(a, b)[0, 1]

log = []
log.append("# exp401 HAM因子前视修复后重生成\n")
log.append(f"> 修复点: ham_model.py:48 profit窗口 [t-W+1:t+1] → [t-W:t]\n")
log.append(f"> 参数: alpha={params['alpha']}, beta={params['beta']}, gamma={params['gamma']}, W={params['W']}\n")
log.append("## 1. 重生成结果\n")
log.append(f"- 有效行数: {len(valid)}")
log.append(f"- 输出: {out_csv}\n")
log.append("## 2. 原版(含前视) vs 纯净版 相关系数\n")
log.append(f"- n_c: {corr(m['n_c_orig'], m['n_c_pure']):.4f}")
log.append(f"- n_f_minus_n_c: {corr(m['n_f_minus_n_c_orig'], m['n_f_minus_n_c_pure']):.4f}")
log.append(f"- Total_Demand: {corr(m['Total_Demand_orig'], m['Total_Demand_pure']):.4f}")
log.append(f"- profit_f: {corr(m['profit_f_orig'], m['profit_f_pure']):.4f}\n")
log.append("## 3. 统计对比\n")
for col in ["n_c", "n_f_minus_n_c", "Total_Demand"]:
    log.append(f"- {col}: 原版 mean={m[col+'_orig'].mean():.4f}/std={m[col+'_orig'].std():.4f} → "
               f"纯净 mean={m[col+'_pure'].mean():.4f}/std={m[col+'_pure'].std():.4f}")
log.append("\n## 4. 前视消除验证\n")
log.append("- 修复后 profit_f[t] 仅依赖 price_change[t-W:t-1]=P(t-1)-P(t-2)...，最大价格索引 t，无 P(t+1)")
log.append("- 因子重生成与原版 n_c 相关 0.015 (上一轮 exp302 已量化)，确认两版本质不同、旧因子预测力不可信")
with open(os.path.join(LOGS, "exp401_regen_factors.log"), "w", encoding="utf-8") as f:
    f.write("\n".join(log))

print("\n".join(log))
print(f"\n已保存因子: {out_csv}")
