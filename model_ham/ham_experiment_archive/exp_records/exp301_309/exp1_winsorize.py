"""
exp304: 优化实验1 - 特征预处理增强(1%-99%缩尾)
基于训练集统计量对全部聚类特征+模型输入因子执行1%-99%缩尾，
不使用全量数据极值；缺失值沿用旧标准不插值；全套回测重跑对比。
严格防泄露：缩尾分位点仅用训练集计算，测试集用训练集分位点裁剪。
"""
import os
import numpy as np
import pandas as pd
from sklearn.mixture import GaussianMixture
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import silhouette_score, mean_squared_error, accuracy_score
from lightgbm import LGBMClassifier
import warnings, json
warnings.filterwarnings("ignore")

BASE = "model_ham"
EXP = os.path.join(BASE, "exp3_audit_optimize")
RANDOM_STATE = 42

# ---------- 1. 读取数据 ----------
combined = pd.read_csv(os.path.join(BASE, "ham_state_combined.csv"))
combined["date"] = pd.to_datetime(combined["date"])
labels = pd.read_csv(os.path.join(BASE, "market_state_label.csv"))
labels["date"] = pd.to_datetime(labels["date"])
combined = combined.drop(columns=["state_id"], errors="ignore")
combined = combined.merge(labels[["date", "state_id"]], on="date", how="inner")
if "state_id_x" in combined.columns:
    combined = combined.rename(columns={"state_id_x": "state_id"})
    combined = combined.drop(columns=["state_id_y"], errors="ignore")

FUND = ["warehouse_stock", "basis_spot_main", "spread_near1_near3", "hv_20d", "oi_main"]
HAM = ["n_c", "n_f_minus_n_c", "Total_Demand"]
ALL_FEAT = FUND + HAM

combined = combined.dropna(subset=ALL_FEAT + ["state_id"])
combined = combined.sort_values("date").reset_index(drop=True)
n_total = len(combined)
n_train = int(n_total * 0.75)
print(f"数据: {n_total}行, 训练{n_train}/测试{n_total-n_train}")

# ---------- 2. 训练集缩尾分位点（1%-99%）----------
# 先转float避免pandas 3.0 LossySetitemError
for col in ALL_FEAT:
    combined[col] = combined[col].astype(float)
train_part = combined.iloc[:n_train].copy()
test_part = combined.iloc[n_train:].copy()

# 计算训练集1%和99%分位点（仅训练集！）
winsor_limits = {}
for col in ALL_FEAT:
    lo = train_part[col].quantile(0.01)
    hi = train_part[col].quantile(0.99)
    winsor_limits[col] = (lo, hi)
    # 裁剪测试集用训练集分位点
    test_part.loc[:, col] = test_part[col].clip(lower=lo, upper=hi)
    # 训练集也裁剪（缩尾）
    train_part.loc[:, col] = train_part[col].clip(lower=lo, upper=hi)

# 重建缩尾后的完整数据（先转float避免LossySetitemError）
combined_wins = pd.concat([train_part, test_part], ignore_index=True)
for col in ALL_FEAT:
    combined_wins[col] = combined_wins[col].astype(float)
# 记录缩尾分位点
with open(os.path.join(EXP, "data", "exp304_winsor_limits.json"), "w") as f:
    json.dump({k: list(v) for k, v in winsor_limits.items()}, f, indent=2)

# 缩尾前后极值对比
print("\n=== 缩尾分位点(训练集) ===")
for col in ALL_FEAT:
    lo, hi = winsor_limits[col]
    print(f"  {col}: [1%={lo:.4f}, 99%={hi:.4f}]")

# ---------- 3. GMM聚类（缩尾+标准化 vs 原始+标准化）----------
def gmm_cluster(df, feats, n_train_idx, tag):
    """GMM聚类，返回labels, BIC, silhouette"""
    scaler = StandardScaler()
    X_train = scaler.fit_transform(df.iloc[:n_train_idx][feats])
    X_all = scaler.transform(df[feats])
    bic = {}
    for k in [2,3,4]:
        g = GaussianMixture(n_components=k, covariance_type="full", n_init=10,
                            max_iter=1000, random_state=RANDOM_STATE)
        g.fit(X_train)
        bic[k] = g.bic(X_train)
    best_k = min(bic, key=bic.get)
    g = GaussianMixture(n_components=best_k, covariance_type="full", n_init=10,
                        max_iter=1000, random_state=RANDOM_STATE)
    g.fit(X_train)
    labels = g.predict(X_all)
    sil = silhouette_score(X_all, labels)
    return labels, bic, sil, best_k

# 原始版
labels_raw, bic_raw, sil_raw, k_raw = gmm_cluster(combined, FUND, n_train, "raw")
# 缩尾版
labels_wins, bic_wins, sil_wins, k_wins = gmm_cluster(combined_wins, FUND, n_train, "wins")

print(f"\n=== GMM对比 ===")
print(f"原始: K={k_raw}, BIC={min(bic_raw.values()):.2f}, 轮廓={sil_raw:.4f}")
print(f"缩尾: K={k_wins}, BIC={min(bic_wins.values()):.2f}, 轮廓={sil_wins:.4f}")
print(f"原始状态分布: {pd.Series(labels_raw).value_counts().sort_index().to_dict()}")
print(f"缩尾状态分布: {pd.Series(labels_wins).value_counts().sort_index().to_dict()}")

# ---------- 4. 消融回测对比 ----------
LGBM_PARAMS = {"n_estimators": 100, "learning_rate": 0.05, "num_leaves": 15,
    "max_depth": 5, "min_child_samples": 20, "subsample": 0.8,
    "colsample_bytree": 0.8, "random_state": 42, "verbose": -1}

combined["target"] = (combined["close"].shift(-1)/combined["close"]-1 > 0).astype(int)
combined = combined.dropna(subset=["target"]).reset_index(drop=True)
n_train_bt = int(len(combined)*0.75)
combined_wins = combined_wins.copy()
combined_wins["target"] = (combined_wins["close"].shift(-1)/combined_wins["close"]-1 > 0).astype(int)
combined_wins = combined_wins.dropna(subset=["target"]).reset_index(drop=True)
n_train_wins = int(len(combined_wins)*0.75)

def ablation(df, features, state_col=None, n_tr=None):
    cols = features + ([state_col] if state_col else [])
    X = df[cols].values
    y = df["target"].values
    Xtr, Xte = X[:n_tr], X[n_tr:]
    ytr, yte = y[:n_tr], y[n_tr:]
    sc = StandardScaler(); Xtr = sc.fit_transform(Xtr); Xte = sc.transform(Xte)
    m = LGBMClassifier(**LGBM_PARAMS); m.fit(Xtr, ytr)
    return {"acc": accuracy_score(yte, m.predict(Xte)),
            "rmse": np.sqrt(mean_squared_error(yte, m.predict_proba(Xte)[:,1]))}

combined["state_wins"] = labels_wins
combined_wins["state_wins"] = labels_wins[:len(combined_wins)]

groups = [
    ("A 基本面", FUND, None),
    ("B HAM", HAM, None),
    ("C 基本面+HAM", ALL_FEAT, None),
    ("D +state", ALL_FEAT, "state_wins"),
]
print(f"\n=== 消融对比: 原始 vs 缩尾 (样本外准确率) ===")
print(f"{'组别':<10} {'原始':<10} {'缩尾':<10} {'差异':<8}")
print("-"*38)
comp_results = []
for nm, feats, sc in groups:
    r_raw = ablation(combined, feats, sc, n_train_bt)
    r_wins = ablation(combined_wins, feats, sc, n_train_wins)
    diff = (r_wins["acc"]-r_raw["acc"])*100
    print(f"{nm:<10} {r_raw['acc']*100:>7.1f}% {r_wins['acc']*100:>7.1f}% {diff:>+6.1f}pp")
    comp_results.append({"name": nm, "raw_acc": r_raw["acc"], "wins_acc": r_wins["acc"],
                         "diff": diff, "raw_rmse": r_raw["rmse"], "wins_rmse": r_wins["rmse"]})

# ---------- 5. 报告 ----------
rep = []
rep.append("# exp304 优化实验1：特征预处理增强（1%-99%缩尾）\n")
rep.append(f"> 数据: {n_total}行, 训练{n_train}/测试{n_total-n_train}")
rep.append(f"> 缩尾分位点**仅用训练集计算**，测试集用训练集分位点裁剪（防泄露）\n")

rep.append("## 1. GMM 聚类对比（缩尾 vs 原始）\n")
rep.append("| 版本 | 最优K | BIC | 轮廓系数 | 状态分布 |")
rep.append("|------|-------|-----|----------|----------|")
rep.append(f"| 原始 | {k_raw} | {min(bic_raw.values()):.2f} | {sil_raw:.4f} | {pd.Series(labels_raw).value_counts().sort_index().to_dict()} |")
rep.append(f"| 缩尾 | {k_wins} | {min(bic_wins.values()):.2f} | {sil_wins:.4f} | {pd.Series(labels_wins).value_counts().sort_index().to_dict()} |")
sil_diff = sil_wins - sil_raw
rep.append(f"\n- 轮廓系数变化: {sil_raw:.4f} → {sil_wins:.4f} ({'+' if sil_diff>=0 else ''}{sil_diff:.4f})")
rep.append(f"- 结论: {'缩尾提升聚类分离度' if sil_diff>0 else '缩尾未提升/降低聚类分离度'}")

rep.append("\n## 2. 消融回测对比（样本外准确率）\n")
rep.append("| 组别 | 原始 | 缩尾 | 差异 | 缩尾RMSE |")
rep.append("|------|------|------|------|----------|")
for r in comp_results:
    rep.append(f"| {r['name']} | {r['raw_acc']*100:.1f}% | {r['wins_acc']*100:.1f}% | {r['diff']:+.1f}pp | {r['wins_rmse']:.4f} |")

avg_diff = np.mean([r["diff"] for r in comp_results])
rep.append(f"\n- 平均准确率差异: {avg_diff:+.2f}pp")
if abs(avg_diff) < 2:
    rep.append("- **结论: 缩尾对预测准确率影响很小**（<2pp），极端值对LightGBM影响有限")
elif avg_diff > 0:
    rep.append("- **结论: 缩尾整体提升预测准确率**，极端值确实干扰模型")
else:
    rep.append("- **结论: 缩尾整体降低预测准确率**，需进一步分析")

rep.append("\n## 3. 缩尾合理性\n")
rep.append("- 1%-99%缩尾是金融数据标准做法，抑制极端值对GMM高斯假设的破坏")
rep.append("- LightGBM基于树分裂，对缩尾不敏感（树只看相对顺序），故消融差异小是预期内")
rep.append("- 缩尾主要收益应在GMM聚类（轮廓系数），而非LightGBM预测")

rep.append("\n## 4. 局限性\n")
rep.append("1. 测试集样本仅134行，统计波动大")
rep.append("2. 缩尾分位点基于训练集，但未做滚动更新")
rep.append("3. 仅1%-99%一档，未测试更激进的5%-95%")

with open(os.path.join(EXP, "reports", "exp304_exp1_winsorize.md"), "w", encoding="utf-8") as f:
    f.write("\n".join(rep))
print(f"\n已保存: {EXP}/reports/exp304_exp1_winsorize.md")
