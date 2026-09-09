"""
step2_gmm_cluster.py — GMM 无监督聚类（完全重新训练）
5 项特征：warehouse_stock, basis_spot_main, spread_near1_near3, hv_20d, oi_main
BIC 选 K，仅训练集拟合，全量预测
"""
import os
import numpy as np
import pandas as pd
from sklearn.mixture import GaussianMixture
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import brier_score_loss

BASE = "model_ham"
FEATURES = ["warehouse_stock", "basis_spot_main", "spread_near1_near3", "hv_20d", "oi_main"]
RANDOM_STATE = 42

# ---------- 1. 读取数据 ----------
df = pd.read_csv(os.path.join(BASE, "cluster_input.csv"))
df["date"] = pd.to_datetime(df["date"])
print(f"原始数据: {len(df)} 行")

# 剔除存在 NaN 的行
df_clean = df.dropna(subset=FEATURES).copy()
print(f"剔除NaN后: {len(df_clean)} 行 (剔除 {len(df) - len(df_clean)} 行)")
print(f"日期范围: {df_clean['date'].min().date()} ~ {df_clean['date'].max().date()}")

# ---------- 2. 时间切分：训练集 / 测试集 ----------
# 严格按日期顺序，前 75% 训练，后 25% 测试
n_train = int(len(df_clean) * 0.75)
df_train = df_clean.iloc[:n_train].copy()
df_test = df_clean.iloc[n_train:].copy()
print(f"\n训练集: {len(df_train)} 行 ({df_train['date'].min().date()} ~ {df_train['date'].max().date()})")
print(f"测试集: {len(df_test)} 行 ({df_test['date'].min().date()} ~ {df_test['date'].max().date()})")

# ---------- 3. Z-score 标准化（仅训练集统计量） ----------
scaler = StandardScaler()
X_train_scaled = scaler.fit_transform(df_train[FEATURES])
X_all_scaled = scaler.transform(df_clean[FEATURES])

# ---------- 4. BIC 选 K ----------
print("\n=== BIC 对比 ===")
bic_results = {}
for k in [2, 3, 4]:
    gmm = GaussianMixture(n_components=k, covariance_type="full",
                          n_init=10, max_iter=1000, random_state=RANDOM_STATE)
    gmm.fit(X_train_scaled)
    bic = gmm.bic(X_train_scaled)
    bic_results[k] = bic
    print(f"  K={k}: BIC={bic:.2f}")

best_k = min(bic_results, key=bic_results.get)
print(f"\n最优 K={best_k} (BIC={bic_results[best_k]:.2f})")

# ---------- 5. 用最优 K 训练 GMM，全量预测 ----------
gmm = GaussianMixture(n_components=best_k, covariance_type="full",
                      n_init=10, max_iter=1000, random_state=RANDOM_STATE)
gmm.fit(X_train_scaled)

labels_all = gmm.predict(X_all_scaled)
probs_all = gmm.predict_proba(X_all_scaled)
proba_max = probs_all.max(axis=1)

# ---------- 6. 输出 market_state_label.csv ----------
out = df_clean[["date"] + FEATURES].copy()
out["state_id"] = labels_all
out["proba_max"] = np.round(proba_max, 4)
out["date"] = out["date"].dt.strftime("%Y-%m-%d")
out_path = os.path.join(BASE, "market_state_label.csv")
out.to_csv(out_path, index=False)
print(f"\n已输出: {out_path}")

# ---------- 7. 状态分布 ----------
print(f"\n=== 状态分布 ===")
dist = out["state_id"].value_counts().sort_index()
for sid, cnt in dist.items():
    pct = cnt / len(out) * 100
    print(f"  State {sid}: {cnt} 天 ({pct:.1f}%)")

# 训练集 vs 测试集状态分布
out_dates = pd.to_datetime(out["date"])
train_dates = out_dates.iloc[:len(df_train)]
test_dates = out_dates.iloc[len(df_train):]
print(f"\n=== 训练集状态分布 ===")
for sid, cnt in pd.Series(labels_all[:len(df_train)]).value_counts().sort_index().items():
    print(f"  State {sid}: {cnt} 天 ({cnt/len(df_train)*100:.1f}%)")
print(f"\n=== 测试集状态分布 ===")
for sid, cnt in pd.Series(labels_all[len(df_train):]).value_counts().sort_index().items():
    print(f"  State {sid}: {cnt} 天 ({cnt/len(df_test)*100:.1f}%)")

# ---------- 8. 各状态特征画像 ----------
print(f"\n=== 各状态特征均值 ===")
for sid in sorted(dist.index):
    mask = out["state_id"] == sid
    means = out.loc[mask, FEATURES].mean()
    print(f"\n  State {sid} (n={mask.sum()}):")
    for f in FEATURES:
        print(f"    {f}: {means[f]:.2f}")

# ---------- 9. 保存训练参数 ----------
params = {
    "best_k": best_k,
    "bic_results": bic_results,
    "n_train": n_train,
    "n_test": len(df_clean) - n_train,
    "n_total_clean": len(df_clean),
    "n_total_raw": len(df),
    "n_nan_removed": len(df) - len(df_clean),
    "features": FEATURES,
    "scaler_mean": scaler.mean_.tolist(),
    "scaler_scale": scaler.scale_.tolist()
}
import json
with open(os.path.join(BASE, "gmm_params.json"), "w") as f:
    json.dump(params, f, indent=2)

print(f"\nstep2 完成")
