"""
exp307: 优化实验4 - 增强因子有效性检验
1. 五分位分层回测：依据因子数值分层，统计每档未来收益均值/盈亏
   (弥补RankIC受离散因子干扰的缺陷——分层用分位数，对连续因子有效)
2. 消融实验新增基准对照组：固定多头基准
说明：HAM中n_c/n_f高度离散(3-4唯一值)无法五分位分层，单独处理。
"""
import os
import numpy as np
import pandas as pd
from scipy import stats as sp_stats
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import mean_squared_error, accuracy_score
from lightgbm import LGBMClassifier
import warnings, json
warnings.filterwarnings("ignore")

BASE = "model_ham"
EXP = os.path.join(BASE, "exp3_audit_optimize")
RANDOM_STATE = 42

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
n_train = int(n_total*0.75)

# 未来收益
for h in [1,3,5]:
    combined[f"fwd_{h}d"] = combined["close"].shift(-h)/combined["close"]-1
combined["target"] = (combined["fwd_1d"]>0).astype(int)
combined = combined.dropna(subset=["target"]).reset_index(drop=True)
n_train_bt = int(len(combined)*0.75)

print(f"数据: {len(combined)}行, 训练{n_train_bt}/测试{len(combined)-n_train_bt}")

# ---------- 1. 连续型特征 vs 离散型特征 ----------
print("\n=== 因子离散度检查（五分位可行性）===")
cont_feats = []
disc_feats = []
for f in ALL_FEAT:
    nu = combined[f].nunique()
    tag = "连续(可分层)" if nu >= 10 else "离散(不可分层)"
    print(f"  {f}: {nu}唯一值 → {tag}")
    if nu >= 10:
        cont_feats.append(f)
    else:
        disc_feats.append(f)

# ---------- 2. 五分位分层回测 ----------
print(f"\n=== 五分位分层回测 (连续特征, {len(cont_feats)}个) ===")
print("按测试集分位数分层，统计各档未来收益\n")

quantile_results = []
for f in cont_feats:
    # 测试集分位数分层（仅测试集，防泄露）
    test_df = combined.iloc[n_train_bt:].copy()
    test_train = combined.iloc[:n_train_bt]
    try:
        test_df["quintile"] = pd.qcut(test_df[f], 5, labels=[1,2,3,4,5], duplicates="drop")
    except ValueError:
        print(f"  {f}: 无法5分位（值过少），跳过")
        continue
    for q in sorted(test_df["quintile"].dropna().unique()):
        sub = test_df[test_df["quintile"]==q]
        ret = sub["fwd_1d"].dropna()
        if len(ret)==0: continue
        quantile_results.append({
            "factor": f, "quintile": int(q),
            "range": f"[{sub[f].min():.2f}, {sub[f].max():.2f}]",
            "mean_ret": ret.mean(), "std": ret.std(),
            "win_rate": (ret>0).mean(), "n": len(ret),
            "cum_ret": ret.sum()
        })
    # 单调性检验（第一档 vs 第五档收益差）
    q1 = test_df[test_df["quintile"]==1]["fwd_1d"].dropna()
    q5 = test_df[test_df["quintile"]==5]["fwd_1d"].dropna()
    if len(q1)>0 and len(q5)>0:
        diff = q5.mean() - q1.mean()
        # t检验
        t, p = sp_stats.ttest_ind(q5, q1, equal_var=False)
        quantile_results.append({
            "factor": f, "quintile": "Q5-Q1", "range": "差值",
            "mean_ret": diff, "std": np.nan, "win_rate": np.nan,
            "n": len(q1)+len(q5), "cum_ret": np.nan, "t": t, "p": p
        })

# ---------- 3. 固定多头基准 vs 模型 ----------
print("\n=== 消融 + 固定多头基准对照 ===")
LGBM_PARAMS = {"n_estimators": 100, "learning_rate": 0.05, "num_leaves": 15,
    "max_depth": 5, "min_child_samples": 20, "subsample": 0.8,
    "colsample_bytree": 0.8, "random_state": 42, "verbose": -1}

# 固定多头基准：测试集全部买入持有（dropna末尾NaN）
test_df_full = combined.iloc[n_train_bt:].copy()
valid_long = test_df_full["fwd_1d"].dropna()
long_ret = valid_long.sum()
long_winrate = (valid_long>0).mean()
long_annualized = (1+long_ret)**(242/len(valid_long))-1

print(f"固定多头基准:")
print(f"  累计收益: {long_ret*100:.2f}%")
print(f"  胜率: {long_winrate*100:.1f}%")
print(f"  年化收益: {long_annualized*100:.2f}%")

# 模型多空策略
def run_ablation(features, state_col=None):
    cols = features + ([state_col] if state_col else [])
    X = combined[cols].values
    y = combined["target"].values
    Xtr,Xte=X[:n_train_bt],X[n_train_bt:]
    ytr,yte=y[:n_train_bt],y[n_train_bt:]
    sc=StandardScaler();Xtr=sc.fit_transform(Xtr);Xte=sc.transform(Xte)
    m=LGBMClassifier(**LGBM_PARAMS);m.fit(Xtr,ytr)
    pred=m.predict(Xte)
    proba=m.predict_proba(Xte)[:,1]
    acc=accuracy_score(yte,pred)
    rmse=np.sqrt(mean_squared_error(yte,proba))
    # 多空收益（fwd_1d末尾有NaN，需dropna配对）
    pos=np.where(pred==1,1,-1)
    te_returns = combined.iloc[n_train_bt:]["fwd_1d"].values
    valid_mask = ~np.isnan(te_returns)
    te_returns_v = te_returns[valid_mask]
    pos_v = pos[valid_mask]
    ls_ret=(pos_v*te_returns_v).sum()
    ls_win=((pos_v*te_returns_v)>0).mean()
    return {"acc":acc,"rmse":rmse,"ls_ret":ls_ret,"ls_win":ls_win,
            "long_ret":te_returns_v.sum()}

# state_id 已在开头 merge，直接用于消融
groups = [
    ("A 基本面", FUND, None),
    ("B HAM", HAM, None),
    ("C 基本面+HAM", ALL_FEAT, None),
    ("D 全+state", ALL_FEAT, "state_id"),
]
ablation_results = []
for nm, feats, sc in groups:
    r = run_ablation(feats, sc)
    ablation_results.append({**r, "name": nm})
    print(f"\n{nm}:")
    print(f"  方向准确率: {r['acc']*100:.1f}%  RMSE: {r['rmse']:.4f}")
    print(f"  多空累计收益: {r['ls_ret']*100:.2f}%  胜率: {r['ls_win']*100:.1f}%")
    print(f"  vs 固定多头({r['long_ret']*100:.2f}%): {(r['ls_ret']-r['long_ret'])*100:+.2f}pp")

# ---------- 4. 报告 ----------
rep = []
rep.append("# exp307 优化实验4：增强因子有效性检验（五分位分层 + 固定多头基准）\n")
rep.append(f"> 数据: {len(combined)}行, 测试集{len(combined)-n_train_bt}行\n")

rep.append("## 1. 因子离散度（五分位可行性）\n")
rep.append(f"- **连续型(可五分位分层)**: {', '.join(cont_feats)}")
rep.append(f"- **离散型(无法五分位)**: {', '.join(disc_feats)}")
rep.append("- 离散因子(n_c/n_f_minus_n_c)唯一值过少，五分位分层不适用，需分组均值法（见专章）")

rep.append("\n## 2. 五分位分层回测（连续因子，测试集）\n")
# 按因子分组
for f in cont_feats:
    fr = [r for r in quantile_results if r["factor"]==f and r["quintile"]!="Q5-Q1"]
    if not fr: continue
    rep.append(f"\n### {f}\n")
    rep.append("| 档位 | 取值范围 | 均值收益 | 胜率 | 累计收益 | 样本数 |")
    rep.append("|------|----------|----------|------|----------|--------|")
    for r in sorted(fr, key=lambda x: x["quintile"]):
        rep.append(f"| Q{r['quintile']} | {r['range']} | {r['mean_ret']*100:.3f}% | {r['win_rate']*100:.1f}% | {r['cum_ret']*100:.2f}% | {r['n']} |")
    diff_r = [r for r in quantile_results if r["factor"]==f and r["quintile"]=="Q5-Q1"]
    if diff_r:
        d = diff_r[0]
        sig = "✅" if d.get("p",1)<0.05 else "❌"
        rep.append(f"\n- **Q5-Q1收益差: {d['mean_ret']*100:.3f}%** (t={d['t']:.3f}, p={d['p']:.4f}, {sig})")
        if d['mean_ret']>0:
            rep.append("  - 因子越高未来收益越高（正单调）")
        else:
            rep.append("  - 因子越低未来收益越高（负单调）")

rep.append("\n## 3. 五分位分层结论\n")
# 统计哪些因子有单调性
sig_factors = [r for r in quantile_results if r.get("quintile")=="Q5-Q1" and r.get("p",1)<0.05]
rep.append(f"- Q5-Q1收益差显著的因子: {len(sig_factors)} 个")
for r in sig_factors:
    rep.append(f"  - {r['factor']}: Q5-Q1={r['mean_ret']*100:.3f}%, p={r['p']:.4f}")
if len(sig_factors)==0:
    rep.append("- **无因子在测试集呈现显著的分层单调性**")
    rep.append("  - 与RankIC结论一致：连续因子预测力微弱")

rep.append("\n## 4. 固定多头基准对照\n")
rep.append(f"- **固定多头基准**: 累计收益{long_ret*100:.2f}%, 胜率{long_winrate*100:.1f}%, 年化{long_annualized*100:.2f}%")
rep.append("\n| 组别 | 方向准确率 | 多空累计收益 | vs 固定多头 |")
rep.append("|------|-----------|-------------|------------|")
for r in ablation_results:
    rep.append(f"| {r['name']} | {r['acc']*100:.1f}% | {r['ls_ret']*100:.2f}% | {(r['ls_ret']-r['long_ret'])*100:+.2f}pp |")
rep.append(f"| **固定多头基准** | {long_winrate*100:.1f}%(胜率) | {long_ret*100:.2f}% | 基准 |")

# 结论
best_ls = max(ablation_results, key=lambda x: x["ls_ret"])
rep.append(f"\n- 最优多空组: {best_ls['name']} ({best_ls['ls_ret']*100:.2f}%)")
rep.append(f"- {'多空优于固定多头' if best_ls['ls_ret']>long_ret else '**多空未优于固定多头**'}")
if best_ls['ls_ret'] <= long_ret:
    rep.append("  - ⚠️ **模型多空策略未能显著优于简单多头基准**")
    rep.append("  - 这与RankIC/滚动回测结论一致：HAM因子预测力不足以产生超额收益")

rep.append("\n## 5. 局限性与离散因子说明\n")
rep.append("1. 五分位分层仅适用于连续因子，离散因子(n_c/n_f)无法分层")
rep.append("2. 离散因子需分组均值+t检验（见HAM离散因子专章）")
rep.append("3. 固定多头基准未计交易成本；多空策略换手率高，实际成本更高")
rep.append("4. 测试集仅134行，分层后每档约26-27行，统计效力有限")

with open(os.path.join(EXP, "reports", "exp307_exp4_quantile_baseline.md"), "w", encoding="utf-8") as f:
    f.write("\n".join(rep))
print(f"\n已保存: {EXP}/reports/exp307_exp4_quantile_baseline.md")
