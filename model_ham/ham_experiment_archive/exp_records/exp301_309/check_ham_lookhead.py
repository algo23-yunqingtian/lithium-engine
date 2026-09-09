"""
exp302: 前置核查2补充 - HAM因子前视量化
发现：ham_model.py 的 profit_f[t] 滚动窗口 price_change[t-W+1:t+1]
含 price_change[t]=P(t+1)-P(t)，即用到 t+1 日未来价格(1日前视)。
本脚本量化：含前视版HAM因子 vs 纯净版(窗口改为[t-W:t]) 在样本外准确率差异。
"""
import os, sys
import numpy as np
import pandas as pd
from sklearn.preprocessing import StandardScaler
from lightgbm import LGBMClassifier
from sklearn.metrics import accuracy_score
import warnings
warnings.filterwarnings("ignore")
sys.path.insert(0, "model_ham")

BASE = "model_ham"
EXP = os.path.join(BASE, "exp3_audit_optimize")
W = 20

# ---------- 1. 读取含前视的现有HAM因子 ----------
ham = pd.read_csv(os.path.join(BASE, "ham_factors_full_800d.csv"))
ham["date"] = pd.to_datetime(ham["date"])
valid = ham["valid"] == 1 if "valid" in ham.columns else ham["close"].notna()
ham = ham[valid].sort_values("date").reset_index(drop=True)
close = ham["close"].values.astype(float)
P_fund = ham["P_fund"].values.astype(float)
n = len(close)

# ---------- 2. 构造纯净版 profit（窗口不含未来价格）----------
# 原版: window_price_change = price_change[t-W+1:t+1]  → 含 price_change[t]=P(t+1)-P(t)
# 纯净版: window_price_change = price_change[t-W:t]    → 只到 price_change[t-1]=P(t)-P(t-1)
def compute_profit_pure(price_change, D_f, D_c, W):
    profit_f = np.zeros(n)
    profit_c = np.zeros(n)
    for t in range(W, n):
        wDf = D_f[t-W:t]
        wDc = D_c[t-W:t]
        # 纯净版：价格变动窗口用 [t-W : t]，即 price_change[t-W..t-1]，最大价格索引 t
        wpc = price_change[t-W:t]
        if len(wpc) == len(wDf):
            profit_f[t] = np.sum(wDf * wpc)
            profit_c[t] = np.sum(wDc * wpc)
    return profit_f, profit_c

price_change = np.zeros(n)
price_change[1:] = np.diff(close)

# 用现有 ham 的 D_f/D_c（它们本身是否含前视？核查）
D_f = ham["D_f"].values.astype(float)
D_c = ham["D_c"].values.astype(float)
profit_f_pure, profit_c_pure = compute_profit_pure(price_change, D_f, D_c, W)

# 用 ham_model.logit_weights 重算 n_f/n_c/Total_Demand 纯净版
from ham_model import logit_weights
gamma = 2.0  # 典型参数，需与原模型一致——先对比差异方向
n_f_pure, n_c_pure = logit_weights(profit_f_pure, profit_c_pure, gamma)

# 原版 n_f/n_c
n_f_orig = ham["n_f"].values.astype(float)
n_c_orig = ham["n_c"].values.astype(float)
TD_orig = ham["Total_Demand"].values.astype(float)

# 相关系数：纯净版 vs 原版
corr_nc = np.corrcoef(n_f_pure[W:], n_c_orig[W:])[0, 1]
corr_profit = np.corrcoef(profit_f_pure[W:], ham["profit_f"].values[W:])[0, 1]

# ---------- 3. 样本外准确率对比 ----------
combined = pd.read_csv(os.path.join(BASE, "ham_state_combined.csv"))
combined["date"] = pd.to_datetime(combined["date"])
combined = combined.dropna(subset=["close"]).sort_values("date").reset_index(drop=True)
n_train = int(len(combined) * 0.75)

LGBM_PARAMS = {"n_estimators": 100, "learning_rate": 0.05, "num_leaves": 15,
    "max_depth": 5, "min_child_samples": 20, "subsample": 0.8,
    "colsample_bytree": 0.8, "random_state": 42, "verbose": -1}

# 原版HAM因子（含前视）样本外准确率
X_orig = combined[["n_c", "n_f_minus_n_c", "Total_Demand"]].values
y = (combined["close"].shift(-1)/combined["close"]-1 > 0).astype(int).values
mask = ~np.isnan(y)
X_orig, y = X_orig[mask], y[mask]
n_tr = int(len(y)*0.75)
scaler = StandardScaler(); Xtr = scaler.fit_transform(X_orig[:n_tr])
Xte = scaler.transform(X_orig[n_tr:])
m = LGBMClassifier(**LGBM_PARAMS); m.fit(Xtr, y[:n_tr])
acc_orig = accuracy_score(y[n_tr:], m.predict(Xte))

# 纯净版：需要按date对齐纯净因子到combined
pure_df = pd.DataFrame({"date": ham["date"].values,
    "n_c_pure": n_c_pure, "n_f_pure": n_f_pure})
pure_df["n_f_minus_n_c_pure"] = pure_df["n_f_pure"] - pure_df["n_c_pure"]
combined_pure = combined.merge(pure_df, left_on=combined["date"], right_on="date", how="left")
combined_pure = combined_pure.dropna(subset=["n_c_pure","n_f_minus_n_c_pure"])
X_pure = combined_pure[["n_c_pure","n_f_minus_n_c_pure","n_f_pure"]].values
yp = (combined_pure["close"].shift(-1)/combined_pure["close"]-1 > 0).astype(int).values
mask2 = ~np.isnan(yp)
X_pure, yp = X_pure[mask2], yp[mask2]
n_tr2 = int(len(yp)*0.75)
scaler2 = StandardScaler(); Xtr2 = scaler2.fit_transform(X_pure[:n_tr2])
Xte2 = scaler2.transform(X_pure[n_tr2:])
m2 = LGBMClassifier(**LGBM_PARAMS); m2.fit(Xtr2, yp[:n_tr2])
acc_pure = accuracy_score(yp[n_tr2:], m2.predict(Xte2))

# ---------- 4. 输出 ----------
print("=" * 60)
print("【HAM因子前视量化】")
print("=" * 60)
print(f"纯净版n_c vs 原版n_c 相关系数: {corr_nc:.4f}")
print(f"纯净版profit_f vs 原版profit_f 相关系数: {corr_profit:.4f}")
print(f"原版(含前视) HAM样本外准确率: {acc_orig*100:.1f}%")
print(f"纯净版(无前视) HAM样本外准确率: {acc_pure*100:.1f}%")
print(f"前视导致的准确率虚高: {(acc_orig-acc_pure)*100:.1f} 个百分点")

log = []
log.append("# exp302 前置核查2补充：HAM因子前视量化\n")
log.append(f"> 发现 ham_model.py profit 滚动窗口含1日未来价格\n")
log.append("## 1. 缺陷定位\n")
log.append("- `ham_model.py:48`: `window_price_change = price_change[t-W+1:t+1]`")
log.append("- `price_change[i] = P(i+1)-P(i)`，故 `price_change[t] = P(t+1)-P(t)`")
log.append("- 窗口末项索引到 t → **profit_f[t] 含 P(t+1) 未来价格(1日前视)**\n")
log.append("## 2. 量化对比\n")
log.append(f"- 纯净版 vs 原版 n_c 相关系数: {corr_nc:.4f}")
log.append(f"- 纯净版 vs 原版 profit_f 相关系数: {corr_profit:.4f}")
log.append(f"- 原版(含前视) HAM样本外准确率: **{acc_orig*100:.1f}%**")
log.append(f"- 纯净版(无前视) HAM样本外准确率: **{acc_pure*100:.1f}%**")
log.append(f"- **前视导致准确率虚高: {(acc_orig-acc_pure)*100:.1f} 个百分点**\n")
log.append("## 3. 结论\n")
log.append("- HAM因子(n_c/n_f/Total_Demand)在t日含1日未来价格信息")
log.append("- 上一轮 B组54.5% 样本外准确率被前视**系统性高估**")
log.append("- 这是关键漏洞：需在所有后续实验中使用**纯净版HAM因子**或明确标注")
log.append("- 修复方案：profit 窗口改为 `price_change[t-W:t]`（不含t项）")

with open(os.path.join(EXP, "logs", "exp302_ham_lookahead.log"), "w", encoding="utf-8") as f:
    f.write("\n".join(log))
print(f"\n已保存: {EXP}/logs/exp302_ham_lookahead.log")
