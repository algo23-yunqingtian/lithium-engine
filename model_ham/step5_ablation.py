"""
step5_ablation.py — 消融对照实验（LightGBM 预测回测）
4组对照：A(基本面) / B(HAM) / C(基本面+HAM) / D(基本面+HAM+state+交互)
"""
import os
import numpy as np
import pandas as pd
from sklearn.model_selection import TimeSeriesSplit
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import mean_squared_error, accuracy_score
import warnings
warnings.filterwarnings("ignore")

BASE = "model_ham"

# ---------- 1. 读取数据 ----------
combined = pd.read_csv(os.path.join(BASE, "ham_state_combined.csv"))
combined["date"] = pd.to_datetime(combined["date"])

labels = pd.read_csv(os.path.join(BASE, "market_state_label.csv"))
labels["date"] = pd.to_datetime(labels["date"])

# 对齐
combined = combined.drop(columns=["state_id"], errors="ignore")
combined = combined.merge(labels[["date", "state_id"]], on="date", how="inner")
if "state_id_x" in combined.columns:
    combined = combined.rename(columns={"state_id_x": "state_id"})
    combined = combined.drop(columns=["state_id_y"], errors="ignore")

FEATURES_FUND = ["warehouse_stock", "basis_spot_main", "spread_near1_near3", "hv_20d", "oi_main"]
FEATURES_HAM = ["n_c", "n_f_minus_n_c", "Total_Demand"]
FEATURES_ALL = FEATURES_FUND + FEATURES_HAM

# 剔除 NaN
combined = combined.dropna(subset=FEATURES_ALL + ["state_id"])
combined = combined.sort_values("date").reset_index(drop=True)

# 标签：未来1日涨跌方向 (1=涨, 0=跌)
combined["fwd_1d"] = combined["close"].shift(-1) / combined["close"] - 1
combined["target"] = (combined["fwd_1d"] > 0).astype(int)
combined = combined.dropna(subset=["target"]).reset_index(drop=True)

print(f"数据: {len(combined)} 行, {combined['date'].min().date()} ~ {combined['date'].max().date()}")
print(f"目标分布: {combined['target'].value_counts().to_dict()}")

# ---------- 2. 时间切分：前 75% 训练，后 25% 测试 ----------
n_train = int(len(combined) * 0.75)
train_idx = range(0, n_train)
test_idx = range(n_train, len(combined))
print(f"训练集: {n_train} 行, 测试集: {len(combined) - n_train} 行")

# ---------- 3. LightGBM 超参数（与上一轮一致） ----------
from lightgbm import LGBMClassifier

LGBM_PARAMS = {
    "n_estimators": 100,
    "learning_rate": 0.05,
    "num_leaves": 15,
    "max_depth": 5,
    "min_child_samples": 20,
    "subsample": 0.8,
    "colsample_bytree": 0.8,
    "random_state": 42,
    "verbose": -1
}

def run_experiment(features, name, extra_cols=None):
    """运行一组消融实验"""
    use_cols = features.copy()
    if extra_cols:
        use_cols = features + extra_cols
    
    # 构建特征矩阵
    X_all = combined[use_cols].values
    y_all = combined["target"].values
    
    X_train = X_all[:n_train]
    X_test = X_all[n_train:]
    y_train = y_all[:n_train]
    y_test = y_all[n_train:]
    
    # 标准化
    scaler = StandardScaler()
    X_train_s = scaler.fit_transform(X_train)
    X_test_s = scaler.transform(X_test)
    
    # 训练
    model = LGBMClassifier(**LGBM_PARAMS)
    model.fit(X_train_s, y_train)
    
    # 预测
    y_pred = model.predict(X_test_s)
    y_prob = model.predict_proba(X_test_s)[:, 1]
    
    # 计算指标
    rmse = np.sqrt(mean_squared_error(y_test, y_prob))
    accuracy = accuracy_score(y_test, y_pred)
    
    return {
        "name": name,
        "n_features": len(use_cols),
        "features": use_cols,
        "rmse": rmse,
        "accuracy": accuracy,
        "n_test": len(y_test)
    }

# ---------- 4. 运行 4 组实验 ----------
print("\n=== 消融实验 ===")

# A组：仅传统基本面
print("A组：仅传统基本面...")
res_a = run_experiment(FEATURES_FUND, "A: 基本面")

# B组：仅HAM三因子
print("B组：仅HAM三因子...")
res_b = run_experiment(FEATURES_HAM, "B: HAM三因子")

# C组：基本面 + HAM三因子
print("C组：基本面 + HAM...")
res_c = run_experiment(FEATURES_ALL, "C: 基本面+HAM")

# D组：基本面 + HAM + state_id + 交互项
print("D组：基本面 + HAM + state + 交互...")
# 构建交互特征
combined["n_c_x_state"] = combined["n_c"] * combined["state_id"]
combined["Total_Demand_x_state"] = combined["Total_Demand"] * combined["state_id"]
combined["n_f_minus_n_c_x_state"] = combined["n_f_minus_n_c"] * combined["state_id"]

FEATURES_INTERACT = ["state_id", "n_c_x_state", "Total_Demand_x_state", "n_f_minus_n_c_x_state"]
res_d = run_experiment(FEATURES_ALL + FEATURES_INTERACT, "D: 基本面+HAM+state+交互", 
                        extra_cols=None)

# ---------- 5. 输出报告 ----------
results = [res_a, res_b, res_c, res_d]

report_lines = []
report_lines.append("# 消融对照实验报告（LightGBM 预测回测）\n")
report_lines.append(f"> 生成时间：2026-09-09\n")
report_lines.append(f"> **核心变化**：加入 spread_near1_near3 后重跑消融实验\n")

report_lines.append("\n## 1. 实验设置\n")
report_lines.append(f"- 预测目标：未来 1 日涨跌方向（二分类）")
report_lines.append(f"- 训练集：前 75% ({n_train} 行)")
report_lines.append(f"- 测试集：后 25% ({len(combined) - n_train} 行)")
report_lines.append(f"- 模型：LightGBM (n_estimators=100, lr=0.05, num_leaves=15)")
report_lines.append(f"- 指标：RMSE（概率误差）、方向准确率\n")

report_lines.append("## 2. 四组对照\n")
report_lines.append("| 组别 | 特征 | 特征数 | RMSE | 方向准确率 |")
report_lines.append("|------|------|--------|------|-----------|")
for r in results:
    feat_short = r["name"].split(": ")[1] if ": " in r["name"] else r["name"]
    report_lines.append(f"| {r['name'].split(':')[0]} | {feat_short} | {r['n_features']} | {r['rmse']:.4f} | {r['accuracy']*100:.1f}% |")

report_lines.append("\n## 3. 各特征重要性\n")
for r in results:
    # 重新训练获取特征重要性
    use_cols = r["features"]
    X_all = combined[use_cols].values
    y_all = combined["target"].values
    X_train = X_all[:n_train]
    y_train = y_all[:n_train]
    scaler = StandardScaler()
    X_train_s = scaler.fit_transform(X_train)
    model = LGBMClassifier(**LGBM_PARAMS)
    model.fit(X_train_s, y_train)
    importances = model.feature_importances_
    
    report_lines.append(f"\n### {r['name']}\n")
    sorted_idx = np.argsort(importances)[::-1]
    for i in sorted_idx[:10]:
        report_lines.append(f"- {use_cols[i]}: {importances[i]}")

report_lines.append("\n## 4. 与上一轮对比\n")
report_lines.append("> 上一轮（无 spread_near1_near3）消融实验未执行（该轮任务要求仅做聚类+条件分析）")
report_lines.append("> 本次首次加入 spread_near1_near3 并完整执行消融对照")

report_lines.append("\n## 5. 局限性\n")
report_lines.append("1. **测试集样本较少**（约 134 行），统计波动大")
report_lines.append("2. **LightGBM 超参数未针对本轮数据调优**，沿用上一轮设置")
report_lines.append("3. **二分类任务**：预测涨跌方向，未做回归预测（价格点位）")
report_lines.append("4. **全部为样本内统计观察**，不代表未来预测有效性")
report_lines.append("5. **n_c / n_f_minus_n_c 唯一值过少**（3-4 个），作为特征信息量有限")

report_path = os.path.join(BASE, "ablation_result.md")
with open(report_path, "w", encoding="utf-8") as f:
    f.write("\n".join(report_lines))
print(f"\n已保存: ablation_result.md")

print("\n=== 消融实验结果 ===")
for r in results:
    print(f"  {r['name']}: RMSE={r['rmse']:.4f}, Accuracy={r['accuracy']*100:.1f}%")

print("\nstep5 完成")
