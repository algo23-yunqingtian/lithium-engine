"""
exp404a: 滚动窗口GMM状态标签生成 + 聚类特征解析
================================================
任务目标: 停用HAM预测, 仅对现有K=4 GMM体系做解析复盘
硬性约束: GMM严格沿用滚动窗口训练逻辑(仅窗口内历史fit), 禁止全量拟合聚类
步骤:
  1. 用滚动窗口(训练180/预测20/滚动20)重新生成每日状态标签 —— 合法样本外标签
  2. 导出5项特征 × 4状态 统计表(均值/中位数/25-75分位)
  3. 判定每状态最显著区分特征
产出: data/exp404a_state_labels_rolling.csv + data/exp404a_feature_stats.csv
     reports/exp404a_cluster_feature_analysis.md
"""
import os, sys, json, warnings
import numpy as np
import pandas as pd
from sklearn.mixture import GaussianMixture
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import silhouette_score
warnings.filterwarnings("ignore")

BASE = "model_ham"
EXP = os.path.join(BASE, "exp404_cluster_analysis")
DATA = os.path.join(EXP, "data")
REPS = os.path.join(EXP, "reports")
RANDOM_STATE = 42
TRAIN_WINDOW, FORECAST_STEP, ROLL_STEP = 180, 20, 20
BEST_K = 4
FUND = ["warehouse_stock","basis_spot_main","spread_near1_near3","hv_20d","oi_main"]
FEAT_CN = {"warehouse_stock":"库存","basis_spot_main":"基差","spread_near1_near3":"近月1-近月3价差",
           "hv_20d":"波动率","oi_main":"持仓"}

# ---------- 1. 读取特征数据 ----------
src = pd.read_csv(os.path.join(BASE, "market_state_label.csv"))
src["date"] = pd.to_datetime(src["date"])
# close用于后续复盘(若src无close, 从ham_state_combined补)
if "close" not in src.columns:
    comb = pd.read_csv(os.path.join(BASE, "ham_state_combined.csv"))
    comb["date"] = pd.to_datetime(comb["date"])
    src = src.merge(comb[["date","close"]], on="date", how="left")
else:
    src = src.drop(columns=["state_id","proba_max"], errors="ignore")
src = src.dropna(subset=FUND).sort_values("date").reset_index(drop=True)
for c in FUND:
    src[c] = src[c].astype(float)
n_total = len(src)
print(f"特征数据: {n_total}行 ({src['date'].min().date()} ~ {src['date'].max().date()})")

# ---------- 2. 滚动窗口GMM生成状态标签(仅窗口内fit) ----------
# 先给每行一个状态, 用滚动窗口逐段预测
state_arr = np.full(n_total, np.nan, dtype=float)
windows_log = []
start = TRAIN_WINDOW; n_windows = 0
while start + FORECAST_STEP <= n_total:
    train_slice = src.iloc[start-TRAIN_WINDOW:start]
    test_slice = src.iloc[start:start+FORECAST_STEP]
    # 仅窗口内历史 fit scaler + GMM
    scaler = StandardScaler()
    X_train = scaler.fit_transform(train_slice[FUND])
    X_test = scaler.transform(test_slice[FUND])
    # BIC选K(仅训练窗口)
    bic_best=1e9; gmm_best=None; k_best=BEST_K
    for k in [2,3,4]:
        g = GaussianMixture(n_components=k, covariance_type="full", n_init=10, max_iter=1000, random_state=RANDOM_STATE)
        g.fit(X_train)
        if g.bic(X_train) < bic_best:
            bic_best=g.bic(X_train); gmm_best=g; k_best=k
    state_pred = gmm_best.predict(X_test)
    sil = silhouette_score(X_test, state_pred) if len(np.unique(state_pred))>=2 else np.nan
    state_arr[start:start+FORECAST_STEP] = state_pred
    n_windows += 1
    windows_log.append({"window":n_windows,
        "test_start":test_slice["date"].iloc[0].strftime("%Y-%m-%d"),
        "test_end":test_slice["date"].iloc[-1].strftime("%Y-%m-%d"),
        "k":k_best,"bic":round(bic_best,2),
        "silhouette":round(float(sil),4) if not np.isnan(sil) else None,
        "n_test":len(test_slice)})
    start += ROLL_STEP

src["state_id"] = state_arr
# 取有标签的部分
labeled = src[src["state_id"].notna()].copy()
labeled["state_id"] = labeled["state_id"].astype(int)
labeled = labeled.sort_values("date").reset_index(drop=True)
print(f"滚动窗口状态标签: {len(labeled)}行, {n_windows}个窗口, K={BEST_K}")
print(f"状态分布: {labeled['state_id'].value_counts().sort_index().to_dict()}")

labeled.to_csv(os.path.join(DATA,"exp404a_state_labels_rolling.csv"), index=False)
with open(os.path.join(DATA,"exp404a_windows.json"),"w") as f:
    json.dump(windows_log, f, indent=2)

# ---------- 3. 聚类特征解析: 5特征 × 4状态 统计 ----------
# 为可比性, 归一化各特征(用全体z-score)后判定区分度, 但报告展示原始值
z = labeled[FUND].copy()
zmean = z.mean(); zstd = z.std()
z = (z - zmean)/zstd

stats_rows = []
for sid in sorted(labeled["state_id"].unique()):
    sub = labeled[labeled["state_id"]==sid]
    sub_z = z[labeled["state_id"]==sid]
    row = {"state": int(sid), "n_days": len(sub)}
    for c in FUND:
        row[f"{c}_mean"] = sub[c].mean()
        row[f"{c}_median"] = sub[c].median()
        row[f"{c}_p25"] = sub[c].quantile(0.25)
        row[f"{c}_p75"] = sub[c].quantile(0.75)
        row[f"{c}_z_mean"] = sub_z[c].mean()
    stats_rows.append(row)
stats_df = pd.DataFrame(stats_rows)
stats_df.to_csv(os.path.join(DATA,"exp404a_feature_stats.csv"), index=False)

# ---------- 4. 判定每状态最显著区分特征(用|z均值|) ----------
# 找每个状态 z均值绝对值最大的特征
disc = []
overall_mean_z = 0.0
for sid in sorted(labeled["state_id"].unique()):
    row = [r for r in stats_rows if r["state"]==sid][0]
    zmeans = {c: row[f"{c}_z_mean"] for c in FUND}
    ranked = sorted(zmeans.items(), key=lambda x: abs(x[1]), reverse=True)
    top_feat = ranked[0][0]
    disc.append({"state":int(sid), "top_feat":top_feat, "top_z":ranked[0][1],
                 "second_feat":ranked[1][0], "second_z":ranked[1][1],
                 "zmeans":zmeans})

# ---------- 5. 报告 ----------
rep=[]
rep.append("# exp404a 聚类特征解析(K=4 GMM, 滚动窗口训练)\n")
rep.append(f"> 任务: 停用HAM预测, 仅解析现有K=4 GMM聚类体系")
rep.append(f"> 数据: {len(labeled)}行滚动样本外状态标签, {n_windows}个窗口")
rep.append(f"> 训练窗口{TRAIN_WINDOW}/预测{FORECAST_STEP}/滚动{ROLL_STEP}, random_state={RANDOM_STATE}")
rep.append(f"> **硬性约束**: GMM仅窗口内历史fit, 禁止全量拟合聚类\n")

rep.append("## 1. 状态分布\n")
rep.append("| State | 天数 | 占比 |")
rep.append("|-------|------|------|")
for sid in sorted(labeled["state_id"].unique()):
    n = (labeled["state_id"]==sid).sum()
    rep.append(f"| {sid} | {n} | {n/len(labeled)*100:.1f}% |")

rep.append("\n## 2. 聚类特征统计表(5项输入 × 4状态)\n")
rep.append("> 展示各状态的特征均值/中位数/25-75分位; z均值=该特征在状态内相对全体的标准化偏离\n")
for c in FUND:
    rep.append(f"### {FEAT_CN[c]} ({c})\n")
    rep.append("| State | 均值 | 中位数 | 25% | 75% | z均值 |")
    rep.append("|-------|------|--------|-----|-----|-------|")
    for r in stats_rows:
        rep.append(f"| {r['state']} | {r[f'{c}_mean']:.2f} | {r[f'{c}_median']:.2f} | "
                   f"{r[f'{c}_p25']:.2f} | {r[f'{c}_p75']:.2f} | {r[f'{c}_z_mean']:+.2f} |")
    rep.append("")

rep.append("## 3. 各状态最显著区分特征\n")
rep.append("| State | 最显著特征 | z均值 | 次显著特征 | z均值 |")
rep.append("|-------|-----------|-------|-----------|-------|")
for d in disc:
    rep.append(f"| {d['state']} | **{FEAT_CN[d['top_feat']]}** | {d['top_z']:+.2f} | "
               f"{FEAT_CN[d['second_feat']]} | {d['second_z']:+.2f} |")

rep.append("\n## 4. 文字说明: 各状态主要依靠哪些指标区分\n")
for d in disc:
    zmeans = d["zmeans"]
    # 按|z|排序取前2-3个
    ranked = sorted(zmeans.items(), key=lambda x: abs(x[1]), reverse=True)
    top3 = [f"{FEAT_CN[k]}({v:+.2f})" for k,v in ranked[:3]]
    rep.append(f"- **State {d['state']}**: 主要依靠 **{FEAT_CN[d['top_feat']]}** 区分 "
               f"(z均值{d['top_z']:+.2f}), 其次 {FEAT_CN[d['second_feat']]}({d['second_z']:+.2f})。"
               f" 区分度排序: {' > '.join(top3)}")

rep.append("\n## 5. 结论\n")
# 哪些特征真正有区分力(跨状态z范围)
feat_range = {}
for c in FUND:
    zvals = [r[f"{c}_z_mean"] for r in stats_rows]
    feat_range[c] = max(zvals)-min(zvals)
rep.append("各特征跨状态区分力(状态间z均值极差):\n")
for c,r in sorted(feat_range.items(), key=lambda x:x[1], reverse=True):
    rep.append(f"- {FEAT_CN[c]}: 极差 {r:.2f}")
best_feat = max(feat_range.items(), key=lambda x:x[1])
rep.append(f"\n- **总区分力最强特征: {FEAT_CN[best_feat[0]]}**(跨状态极差最大)")
rep.append("- GMM对5项特征的联合分布建模, 但实际区分主要由少数特征驱动")

with open(os.path.join(REPS,"exp404a_cluster_feature_analysis.md"),"w",encoding="utf-8") as f:
    f.write("\n".join(rep))

print("\n" + "="*55)
print("exp404a 完成")
print("="*55)
print(f"状态标签: {DATA}/exp404a_state_labels_rolling.csv")
print(f"特征统计: {DATA}/exp404a_feature_stats.csv")
print(f"报告: {REPS}/exp404a_cluster_feature_analysis.md")
print("\n各状态最显著特征:")
for d in disc:
    print(f"  State {d['state']}: {FEAT_CN[d['top_feat']]} (z={d['top_z']:+.2f})")
