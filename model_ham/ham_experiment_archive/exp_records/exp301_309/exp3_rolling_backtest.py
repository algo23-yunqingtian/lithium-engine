"""
exp306: 优化实验3 - 滚动窗口时序回测（核心任务）
训练窗口180交易日，预测步长20交易日，窗口逐月向前滚动。
每窗口内: 仅窗口内历史数据训练GMM、标准化、缩尾统计量;
          对未来20天生成状态标签、因子预测。
汇总: 整体RankIC、分状态IC、逐期准确率、时序盈亏曲线。

严格防泄露:
- GMM/scaler/缩尾分位点 仅用窗口内历史(训练窗口) fit
- 禁止全量数据拟合/标准化
- 未来20天只用历史模型 predict/transform
"""
import os
import numpy as np
import pandas as pd
from scipy import stats as sp_stats
from sklearn.mixture import GaussianMixture
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import silhouette_score
from lightgbm import LGBMClassifier
import warnings, json
warnings.filterwarnings("ignore")

BASE = "model_ham"
EXP = os.path.join(BASE, "exp3_audit_optimize")
RANDOM_STATE = 42

# ---------- 参数 ----------
TRAIN_WINDOW = 180   # 训练窗口
FORECAST_STEP = 20   # 预测步长(未来20天)
ROLL_STEP = 20       # 滚动步长(逐月≈20交易日)

FUND = ["warehouse_stock", "basis_spot_main", "spread_near1_near3", "hv_20d", "oi_main"]
HAM = ["n_c", "n_f_minus_n_c", "Total_Demand"]
ALL_FEAT = FUND + HAM
BEST_K = 4

# ---------- 读取数据 ----------
combined = pd.read_csv(os.path.join(BASE, "ham_state_combined.csv"))
combined["date"] = pd.to_datetime(combined["date"])
labels = pd.read_csv(os.path.join(BASE, "market_state_label.csv"))
labels["date"] = pd.to_datetime(labels["date"])
combined = combined.drop(columns=["state_id"], errors="ignore")
combined = combined.merge(labels[["date", "state_id"]], on="date", how="inner")
if "state_id_x" in combined.columns:
    combined = combined.rename(columns={"state_id_x": "state_id"})
    combined = combined.drop(columns=["state_id_y"], errors="ignore")

combined = combined.dropna(subset=ALL_FEAT + ["state_id"])
combined = combined.sort_values("date").reset_index(drop=True)
for col in ALL_FEAT:
    combined[col] = combined[col].astype(float)
# 未来收益标签
for h in [1,3,5]:
    combined[f"fwd_{h}d"] = combined["close"].shift(-h)/combined["close"]-1
combined["target_1d"] = (combined["fwd_1d"]>0).astype(int)
combined = combined.dropna(subset=["target_1d"]).reset_index(drop=True)
n_total = len(combined)
print(f"数据: {n_total}行 ({combined['date'].min().date()} ~ {combined['date'].max().date()})")
print(f"参数: 训练窗口{TRAIN_WINDOW}, 预测步长{FORECAST_STEP}, 滚动步长{ROLL_STEP}")

# ---------- 滚动回测 ----------
# 逐窗口滚动
all_preds = []   # 每行的预测结果
all_dates = []
all_actual = []
all_fwd = {h: [] for h in [1,3,5]}
all_states = []
all_factors = {f: [] for f in ALL_FEAT}
windows_log = []

start = TRAIN_WINDOW
n_windows = 0
while start + FORECAST_STEP <= n_total:
    train_end = start  # 训练窗口: [start-TRAIN_WINDOW, start)
    test_end = start + FORECAST_STEP
    
    train_slice = combined.iloc[start-TRAIN_WINDOW:start]
    test_slice = combined.iloc[start:test_end]
    
    # 仅窗口内历史 fit GMM + scaler
    scaler = StandardScaler()
    X_train = scaler.fit_transform(train_slice[FUND])
    X_test = scaler.transform(test_slice[FUND])
    
    # BIC选K（仅训练窗口）
    bic_best = 1e9; gmm_best = None; k_best = BEST_K
    for k in [2,3,4]:
        g = GaussianMixture(n_components=k, covariance_type="full", n_init=10,
                            max_iter=1000, random_state=RANDOM_STATE)
        g.fit(X_train)
        bic = g.bic(X_train)
        if bic < bic_best:
            bic_best = bic; gmm_best = g; k_best = k
    
    # 状态标签（测试期用窗口GMM predict）
    state_pred = gmm_best.predict(X_test)
    # 轮廓系数：需至少2个标签，否则N/A
    if len(np.unique(state_pred)) >= 2:
        sil = silhouette_score(X_test, state_pred)
    else:
        sil = np.nan  # 单状态，无法计算轮廓
    
    # LightGBM 因子预测（基本面+HAM→未来收益）
    feat_cols = ALL_FEAT
    X_train_m = train_slice[feat_cols].values
    y_train_m = train_slice["target_1d"].values
    X_test_m = test_slice[feat_cols].values
    y_test_m = test_slice["target_1d"].values
    
    # 训练集缩尾（仅窗口内）
    ws_train = X_train_m.copy()
    for i in range(X_train_m.shape[1]):
        lo = np.percentile(X_train_m[:,i], 1); hi = np.percentile(X_train_m[:,i], 99)
        ws_train[:,i] = np.clip(ws_train[:,i], lo, hi)
        X_test_m[:,i] = np.clip(X_test_m[:,i], lo, hi)
    
    m = LGBMClassifier(n_estimators=100, learning_rate=0.05, num_leaves=15,
        max_depth=5, min_child_samples=20, subsample=0.8, colsample_bytree=0.8,
        random_state=42, verbose=-1)
    m.fit(ws_train, y_train_m)
    pred = m.predict(X_test_m)
    pred_proba = m.predict_proba(X_test_m)[:,1]
    
    acc = (pred == y_test_m).mean()
    
    n_windows += 1
    windows_log.append({
        "window": n_windows,
        "train_start": train_slice["date"].iloc[0].strftime("%Y-%m-%d"),
        "train_end": train_slice["date"].iloc[-1].strftime("%Y-%m-%d"),
        "test_start": test_slice["date"].iloc[0].strftime("%Y-%m-%d"),
        "test_end": test_slice["date"].iloc[-1].strftime("%Y-%m-%d"),
        "k": k_best, "bic": round(bic_best,2),
        "silhouette": round(float(sil),4) if not np.isnan(sil) else None,
        "accuracy": round(acc,4),
        "n_test": len(test_slice)
    })
    
    # 收集预测
    for i in range(len(test_slice)):
        all_dates.append(test_slice["date"].iloc[i])
        all_actual.append(y_test_m[i])
        all_preds.append(pred[i])
        all_states.append(state_pred[i])
        for h in [1,3,5]:
            v = test_slice[f"fwd_{h}d"].iloc[i]
            all_fwd[h].append(np.nan if pd.isna(v) else v)
        for f in ALL_FEAT:
            all_factors[f].append(test_slice[f].iloc[i])
    
    # 滚动
    start += ROLL_STEP

print(f"\n完成 {n_windows} 个滚动窗口")

# ---------- 汇总 ----------
pred_df = pd.DataFrame({
    "date": all_dates, "pred": all_preds, "actual": all_actual,
    "state": all_states, **{f"fwd_{h}d": all_fwd[h] for h in [1,3,5]},
    **all_factors
})
# 去重（相邻窗口可能重叠——逐月滚动step=20=forecast，无重叠，但保险去重）
pred_df = pred_df.drop_duplicates(subset="date").reset_index(drop=True)
print(f"汇总预测: {len(pred_df)} 行 ({pred_df['date'].min().date()} ~ {pred_df['date'].max().date()})")

# 整体方向准确率
overall_acc = (pred_df["pred"]==pred_df["actual"]).mean()
print(f"整体方向准确率(样本外): {overall_acc*100:.1f}%")

# 逐期准确率
acc_by_window = []
for w in windows_log:
    ws = (w["test_start"], w["test_end"])
    mask = (pred_df["date"]>=ws[0]) & (pred_df["date"]<=ws[1])
    sub = pred_df[mask]
    if len(sub)>0:
        acc_by_window.append((w["window"], sub["date"].iloc[0].strftime("%Y-%m-%d"),
                              (sub["pred"]==sub["actual"]).mean()))

# 分状态RankIC（汇总后按状态分组）
print(f"\n=== 分状态 RankIC (汇总样本外) ===")
ic_results = []
for sid in sorted(pred_df["state"].unique()):
    sub = pred_df[pred_df["state"]==sid]
    for f in ALL_FEAT:
        valid = sub[[f,"fwd_1d"]].dropna()
        if len(valid)>=10 and valid[f].nunique()>=2:
            ic = valid[f].rank().corr(valid["fwd_1d"].rank())
            n = len(valid)
            if abs(ic)<1.0:
                t = ic*np.sqrt((n-2)/(1-ic**2))
                p = 2*(1-sp_stats.t.cdf(abs(t), df=n-2))
                ic_results.append({"state":sid,"factor":f,"ic":ic,"p":p,"n":n})
n_tests = len(ic_results)*3
bonf = 0.05/n_tests if n_tests>0 else 0.05

# 整体RankIC（全特征 vs fwd_1d）
overall_ic_results = []
for f in ALL_FEAT:
    valid = pred_df[[f,"fwd_1d"]].dropna()
    if len(valid)>=10 and valid[f].nunique()>=2:
        ic = valid[f].rank().corr(valid["fwd_1d"].rank())
        n = len(valid)
        t = ic*np.sqrt((n-2)/(1-ic**2)) if abs(ic)<1 else None
        p = 2*(1-sp_stats.t.cdf(abs(t), df=n-2)) if t is not None else None
        overall_ic_results.append({"factor":f,"ic":ic,"p":p,"n":n})

# 盈亏曲线（按预测方向做多/空）
pred_df["position"] = np.where(pred_df["pred"]==1, 1, -1)  # 预测涨做多，跌做空
pred_df["cum_ret"] = (pred_df["position"] * pred_df["fwd_1d"]).cumsum()
final_ret = pred_df["position"].mul(pred_df["fwd_1d"]).sum()
# 基准：全多头
long_ret = pred_df["fwd_1d"].sum()
# 胜率
win_rate = ((pred_df["position"]*pred_df["fwd_1d"])>0).mean()

print(f"\n=== 时序盈亏汇总 ===")
print(f"多空策略累计收益: {final_ret*100:.2f}%")
print(f"固定多头累计收益: {long_ret*100:.2f}%")
print(f"胜率: {win_rate*100:.1f}%")

# 保存中间数据
pred_df.to_csv(os.path.join(EXP, "data", "exp306_rolling_predictions.csv"), index=False)
with open(os.path.join(EXP, "data", "exp306_rolling_windows.json"), "w") as f:
    json.dump(windows_log, f, indent=2)

# ---------- 出图：盈亏曲线 + 逐期准确率 ----------
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
plt.rcParams["font.sans-serif"]=["WenQuanYi Zen Hei","DejaVu Sans"]
plt.rcParams["axes.unicode_minus"]=False

fig, axes = plt.subplots(2,2, figsize=(16,12))
fig.suptitle("exp306 滚动窗口时序回测（样本外）", fontsize=14)

# 盈亏曲线
ax = axes[0,0]
ax.plot(pred_df["date"], pred_df["cum_ret"]*100, label="多空策略", color="#2196F3")
ax.plot(pred_df["date"], pred_df["fwd_1d"].cumsum()*100, label="固定多头", color="#FF9800", ls="--")
ax.set_title("累计收益曲线"); ax.set_ylabel("累计收益(%)")
ax.legend(); ax.grid(alpha=0.3)

# 逐期准确率
ax = axes[0,1]
w_dates = [a[1] for a in acc_by_window]
w_accs = [a[2]*100 for a in acc_by_window]
ax.bar(range(len(w_dates)), w_accs, color="#4CAF50")
ax.axhline(50, color="red", ls="--", label="50%基准")
ax.set_title("逐期方向准确率"); ax.set_ylabel("准确率(%)")
ax.set_xticks(range(0,len(w_dates),3)); ax.set_xticklabels([w_dates[i][:7] for i in range(0,len(w_dates),3)], rotation=45)
ax.legend(); ax.grid(alpha=0.3)

# 整体RankIC
ax = axes[1,0]
oic = sorted(overall_ic_results, key=lambda x: abs(x["ic"]), reverse=True)
ax.barh([r["factor"] for r in oic], [r["ic"] for r in oic], color="#9C27B0")
ax.set_title("整体RankIC（样本外）"); ax.set_xlabel("RankIC")
ax.axvline(0, color="gray"); ax.grid(alpha=0.3, axis="y")

# 分状态准确率
ax = axes[1,1]
state_acc = []
for sid in sorted(pred_df["state"].unique()):
    sub = pred_df[pred_df["state"]==sid]
    state_acc.append((f"State{sid}", (sub["pred"]==sub["actual"]).mean()*100, len(sub)))
ax.bar([s[0] for s in state_acc], [s[1] for s in state_acc], color="#00BCD4")
ax.axhline(50, color="red", ls="--")
ax.set_title("分状态准确率"); ax.set_ylabel("准确率(%)")
ax.grid(alpha=0.3)

plt.tight_layout()
plot_path = os.path.join(EXP, "reports", "exp306_rolling_backtest.png")
plt.savefig(plot_path, dpi=100, bbox_inches="tight")
plt.close()
print(f"\n已保存图: {plot_path}")

# ---------- 报告 ----------
rep = []
rep.append("# exp306 优化实验3：滚动窗口时序回测（核心任务）\n")
rep.append(f"> 训练窗口{TRAIN_WINDOW}交易日, 预测步长{FORECAST_STEP}, 滚动步长{ROLL_STEP}")
rep.append(f"> 共 {n_windows} 个滚动窗口, 汇总样本外预测 {len(pred_df)} 行")
rep.append(f"> **严格防泄露**: 每窗口仅用窗口内历史fit GMM/scaler/缩尾, 未来20天仅predict\n")

rep.append("## 1. 整体样本外表现\n")
rep.append(f"- **整体方向准确率: {overall_acc*100:.1f}%**")
rep.append(f"- 多空策略累计收益: {final_ret*100:.2f}%")
rep.append(f"- 固定多头累计收益: {long_ret*100:.2f}%")
rep.append(f"- 胜率: {win_rate*100:.1f}%")
rep.append(f"- 多空 vs 多头: {'多空更优' if final_ret>long_ret else '多头更优'} (差{(final_ret-long_ret)*100:.2f}pp)")

rep.append("\n## 2. 逐期准确率（每窗口）\n")
rep.append("| 窗口 | 测试期起 | 准确率 |")
rep.append("|------|----------|--------|")
for i,(w,d,a) in enumerate(acc_by_window):
    rep.append(f"| {w} | {d} | {a*100:.1f}% |")
acc_vals = [a[2] for a in acc_by_window]
rep.append(f"\n- 逐期准确率均值: {np.mean(acc_vals)*100:.1f}%, 中位数: {np.median(acc_vals)*100:.1f}%")
rep.append(f"- 准确率>50%的窗口: {sum(1 for a in acc_vals if a>0.5)}/{len(acc_vals)}")
rep.append(f"- 准确率波动: {min(acc_vals)*100:.1f}% ~ {max(acc_vals)*100:.1f}%")

rep.append("\n## 3. 整体 RankIC（样本外汇总）\n")
rep.append(f"> Bonferroni校正阈值: p<{bonf:.4f} (共{n_tests}次分状态检验)\n")
rep.append("| 特征 | RankIC | p | n | 显著(p<0.05)? |")
rep.append("|------|--------|---|---|---------------|")
for r in sorted(overall_ic_results, key=lambda x: abs(x["ic"]), reverse=True):
    sig = "✅" if r["p"]<0.05 else "❌"
    rep.append(f"| {r['factor']} | {r['ic']:.4f} | {r['p']:.4f} | {r['n']} | {sig} |")

rep.append("\n## 4. 分状态 RankIC（样本外汇总）\n")
rep.append("| 状态 | 特征 | RankIC | p | n | 显著? |")
rep.append("|------|------|--------|---|---|-------|")
for r in ic_results:
    sig = "✅" if r["p"]<0.05 else "❌"
    rep.append(f"| {r['state']} | {r['factor']} | {r['ic']:.4f} | {r['p']:.4f} | {r['n']} | {sig} |")
n_sig = sum(1 for r in ic_results if r["p"]<0.05)
rep.append(f"\n- 分状态显著项: {n_sig} (Bonferroni校正后: {sum(1 for r in ic_results if r['p']<bonf)})")

rep.append("\n## 5. 分状态准确率\n")
rep.append("| 状态 | 准确率 | 样本数 |")
rep.append("|------|--------|--------|")
for s in state_acc:
    rep.append(f"| {s[0]} | {s[1]:.1f}% | {s[2]} |")

rep.append("\n## 6. 与上一轮对比（关键）\n")
rep.append("| 口径 | 上一轮(75/25单次切分) | 本轮(滚动窗口) |")
rep.append("|------|---------------------|---------------|")
rep.append(f"| 整体方向准确率 | B组54.5%(样本外134行) | **{overall_acc*100:.1f}%** |")
rep.append(f"| 样本外覆盖 | 134行(25%) | {len(pred_df)}行(多窗口) |")
rep.append("- 滚动窗口是**更严格的样本外检验**: 每个窗口都真正训练→预测，无事后拟合")

rep.append("\n## 7. 结论\n")
if overall_acc < 0.50:
    rep.append(f"- **滚动样本外准确率 {overall_acc*100:.1f}% 低于50%**")
    rep.append("  - 说明上一轮B组54.5%在严格滚动窗口下**未能稳定复现**")
    rep.append("  - 上一轮的单次75/25切分存在**幸运切点**，准确率被高估")
    rep.append("  - ⚠️ HAM因子的预测力在滚动样本外检验下不稳健")
elif overall_acc >= 0.55:
    rep.append(f"- **滚动样本外准确率 {overall_acc*100:.1f}% 稳定优于50%**")
    rep.append("  - HAM因子预测力在严格滚动检验下**得到验证**")
else:
    rep.append(f"- **滚动样本外准确率 {overall_acc*100:.1f}% 在50%附近**")
    rep.append("  - 预测力微弱，不显著优于随机")

rep.append("\n## 8. 局限性\n")
rep.append("1. 533行数据仅支持约17个滚动窗口，统计效力有限")
rep.append("2. 训练窗口180日约9个月，GMM小样本拟合可能不稳定")
rep.append("3. 预测步长20日重叠度低，部分窗口样本数偏少")
rep.append("4. 多空策略未计交易成本，实际收益会低于累计收益")

with open(os.path.join(EXP, "reports", "exp306_exp3_rolling_backtest.md"), "w", encoding="utf-8") as f:
    f.write("\n".join(rep))
print(f"\n已保存报告: {EXP}/reports/exp306_exp3_rolling_backtest.md")
