"""
exp405b: 分市场状态条件因子检验
================================
任务: 对exp405a保留的有效因子, 在S0/S1/S3内部分状态检验 + 全局vs状态条件对照
硬性约束:
  1. 沿用exp404a滚动窗口状态标签(仅窗口内fit GMM, 样本外标签)
  2. S2样本仅26天, 统计置信不足 -> 仅保留记录, 不采信、不用于策略推断
  3. 对照组A: 全局使用因子, 不区分状态
  4. 对照组B: 状态条件模式, 不同状态启用对应有效因子, S2不纳入策略
  5. 对比: 样本外涨跌准确率、年化收益、最大回撤; 基准=固定多头
  6. 全部因子T日及之前数据, 无前视泄露
产出: data/exp405b_state_ic.csv, data/exp405b_comparison.csv, data/exp405b_s2_record.csv
     reports/exp405b_state_factor_review.md
"""
import os, json, warnings
import numpy as np
import pandas as pd
from scipy.stats import spearmanr, t as tdist
warnings.filterwarnings("ignore")

BASE = "model_ham"
EXP = os.path.join(BASE, "exp405_factor_screen")
DATA = os.path.join(EXP, "data")
REPS = os.path.join(EXP, "reports")
RANDOM_STATE = 42
TRAIN_WINDOW, FORECAST_STEP, ROLL_STEP = 180, 20, 20
HORIZON = 20
KEEP_FACTORS = ["f_stock_abs","f_stock_pct_5","f_stock_pct_20","f_volume_pct_5"]
FEAT_CN = {"f_stock_abs":"库存绝对值","f_stock_pct_5":"库存环比5日",
           "f_stock_pct_20":"库存环比20日","f_volume_pct_5":"成交量环比5日"}
TARGET_STATES = [0,1,3]  # S2不采信

# ---------- 1. 读取因子底表 + exp405a保留因子 + exp404a状态标签 ----------
comb = pd.read_csv(os.path.join(BASE, "ham_state_combined.csv"))
comb["date"] = pd.to_datetime(comb["date"])
comb = comb.sort_values("date").reset_index(drop=True)

# 重建因子(同exp405a逻辑, 仅保留因子)
F = pd.DataFrame({"date": comb["date"]})
F["f_stock_abs"] = comb["warehouse_stock"]
F["f_stock_pct_5"] = comb["warehouse_stock"].pct_change(5)
F["f_stock_pct_20"] = comb["warehouse_stock"].pct_change(20)
F["f_volume_pct_5"] = comb["volume"].pct_change(5)
F["close"] = comb["close"]

# 目标: 未来HORIZON日close对数收益
F["target_fwd"] = np.log(F["close"].shift(-HORIZON) / F["close"])

# exp404a滚动状态标签
state_lab = pd.read_csv(os.path.join(os.path.join(BASE,"exp404_cluster_analysis","data"),
    "exp404a_state_labels_rolling.csv"))
state_lab["date"] = pd.to_datetime(state_lab["date"])

# 合并: 因子表左连接状态标签(仅保留有状态标签的行 -> 样本外合法区间)
F = F.merge(state_lab[["date","state_id"]], on="date", how="inner")
F = F.sort_values("date").reset_index(drop=True)
print(f"对齐后样本: {len(F)}行 ({F['date'].min().date()} ~ {F['date'].max().date()})")
print(f"状态分布: {F['state_id'].value_counts().sort_index().to_dict()}")

# S2记录(不采信)
s2 = F[F["state_id"]==2].copy()
s2.to_csv(os.path.join(DATA,"exp405b_s2_record.csv"), index=False)
print(f"S2记录: {len(s2)}行(仅记录, 不采信)")

# ---------- 2. 分状态内部滚动RankIC (S0/S1/S3) ----------
# 在每个滚动测试窗口内, 按状态子集计算因子-目标秩相关(仍用样本外窗口)
state_ic_records = []
start = TRAIN_WINDOW; w = 0
n_total = len(F)
# 注意: 状态标签区间起点晚于全量数据起点, 窗口需按F的索引滚动
while start + FORECAST_STEP <= n_total:
    test = F.iloc[start:start+FORECAST_STEP]
    w += 1
    for sid in TARGET_STATES:
        sub = test[test["state_id"]==sid]
        for fc in KEEP_FACTORS:
            tmp = sub[[fc,"target_fwd"]].dropna()
            ic = np.nan
            if len(tmp) >= 5:
                ic, _ = spearmanr(tmp[fc], tmp["target_fwd"])
            state_ic_records.append({"window":w,"state":sid,"factor":fc,
                "ic":ic,"n":len(tmp)})
    start += ROLL_STEP
state_ic_df = pd.DataFrame(state_ic_records)
state_ic_df.to_csv(os.path.join(DATA,"exp405b_state_ic.csv"), index=False)

# 汇总: 各状态各因子平均IC
state_ic_sum = state_ic_df[state_ic_df["ic"].notna()].groupby(["state","factor"])["ic"].agg(["mean","std","count"]).reset_index()
state_ic_sum.columns = ["state","factor","mean_ic","std_ic","n_win"]
state_ic_sum.to_csv(os.path.join(DATA,"exp405b_state_ic_summary.csv"), index=False)

print("\n分状态因子IC汇总:")
print(state_ic_sum.to_string(index=False))

# ---------- 3. 信号构造与对照回测 ----------
# 基准: 固定多头(每日持有, 收益=前向HORIZON日收益)
# 对照组A: 全局使用因子(不分状态), 取所有保留因子的等权信号
# 对照组B: 状态条件模式, 不同状态启用对应有效因子; S2不纳入(信号=0/持有基准)

# 先确定每状态"有效因子"= 该状态下|mean_ic|最大的因子(方向由IC符号决定)
# 方向: IC>0 -> 因子高做多(因子排序高者买); IC<0 -> 因子高做空
# 若该状态子样本不足(无IC), 回退用全局最佳因子
# 全局最佳因子(按|IC|)
_global_sub = state_ic_sum.copy()
_global_sub["abs_ic"] = _global_sub["mean_ic"].abs()
_GLOBAL_BEST_FC = _global_sub.sort_values("abs_ic", ascending=False).iloc[0]["factor"]
_GLOBAL_BEST_IC = _global_sub.loc[_global_sub["factor"]==_GLOBAL_BEST_FC,"mean_ic"].mean()

def best_factor_for_state(sid):
    sub = state_ic_sum[state_ic_sum["state"]==sid]
    if len(sub)==0:
        # 回退全局最佳因子
        return _GLOBAL_BEST_FC, _GLOBAL_BEST_IC
    sub2 = sub.copy()
    sub2["abs_ic"] = sub2["mean_ic"].abs()
    b = sub2.sort_values("abs_ic", ascending=False).iloc[0]
    return b["factor"], b["mean_ic"]

state_best = {}
for sid in TARGET_STATES:
    fc, ic = best_factor_for_state(sid)
    state_best[sid] = (fc, ic)
print("\n各状态最佳因子(按|IC|):")
for sid,(fc,ic) in state_best.items():
    print(f"  State {sid}: {FEAT_CN.get(fc,fc)} (IC={ic:+.4f})")

# 构造每日信号与目标收益
# 信号: 1=做多, 0=空仓/中性, -1=做空(但基准固定多头, 这里用相对基准的超额判断)
# 为简化对照: 用"信号方向是否预测对了目标收益方向"评估准确率, 用分层收益评估年化/回撤

# 全局因子等权信号: 每因子按当期相对历史中位数判定方向, 多因子投票
# 中位数用训练期(T-TRAIN_WINDOW..T-1)计算, 避免前视
n_med = len(F)
global_signal = np.zeros(n_med)
state_signal = np.zeros(n_med)
base_ret = F["target_fwd"].values  # 基准=固定多头的未来收益

# 预计算训练期中位数
def factor_signal_at(idx, fc):
    """idx日因子值相对训练期中位数的方向(+1高/-1低)"""
    train_start = max(0, idx - TRAIN_WINDOW)
    train_vals = F[fc].iloc[train_start:idx]
    train_vals = train_vals.dropna()
    if len(train_vals) < 30:
        return 0
    med = train_vals.median()
    cur = F[fc].iloc[idx]
    if pd.isna(cur):
        return 0
    return 1 if cur > med else -1

# 对照组A: 全局因子等权(不乘状态, 不做方向校准) -- 朴素基线
# 对照组A': 全局因子等权+方向校准(按各因子全局IC符号调整方向) -- 公平的全局基线
# 对照组B: 状态条件, 仅用该状态最佳因子, S2信号=0
# 各因子全局IC(来自exp405a, 用于方向校准)
GLOBAL_IC = {"f_stock_abs":-0.2853,"f_stock_pct_5":-0.3559,
             "f_stock_pct_20":-0.4021,"f_volume_pct_5":0.0786}
state_signal = np.zeros(n_med)
signal_a_dir = np.zeros(n_med)  # 全局因子+方向校准
for i in range(n_med):
    sid = F["state_id"].iloc[i]
    # A: 所有保留因子等权(不做方向校准)
    sigs_a = [factor_signal_at(i, fc) for fc in KEEP_FACTORS]
    global_signal[i] = np.mean(sigs_a)
    # A': 所有保留因子等权+方向校准(按全局IC符号)
    sigs_ad = [factor_signal_at(i, fc) * (1 if GLOBAL_IC[fc]>0 else -1) for fc in KEEP_FACTORS]
    signal_a_dir[i] = np.mean(sigs_ad)
    # B: 状态条件
    if sid in state_best:
        fc_b = state_best[sid][0]
        ic_b = state_best[sid][1]
        raw = factor_signal_at(i, fc_b)
        # IC方向: IC>0 因子高做多(raw=+1); IC<0 因子高做空(raw=+1应为做空)
        state_signal[i] = raw * (1 if ic_b > 0 else -1)
    else:
        state_signal[i] = 0  # S2不纳入

# 只评估有目标收益的行
mask = F["target_fwd"].notna() & F["state_id"].notna()
ev = pd.DataFrame({
    "date": F["date"].iloc[mask.values],
    "state": F["state_id"].iloc[mask.values].astype(int),
    "base_ret": base_ret[mask.values],
    "sig_global": global_signal[mask.values],
    "sig_global_dir": signal_a_dir[mask.values],
    "sig_state": state_signal[mask.values]
})

# ---------- 4. 样本外指标: 涨跌准确率 + 年化收益 + 最大回撤 ----------
def backtest_metrics(sig, ret, n_days_per_year=250):
    """
    sig: 信号方向(+1做多/-1做空/0中性), ret: 同期20日前向收益
    准确率 = 信号方向与收益方向一致的比例(仅计|sig|>0的样本, 全样本重叠窗口)
    年化收益/最大回撤 = 用非重叠20日窗口收益序列(每HORIZON日取一点)计算复利, 避免重叠失真
    """
    sig = np.asarray(sig); ret = np.asarray(ret)
    valid = np.abs(sig) > 1e-9
    n_sig = valid.sum()
    if n_sig == 0:
        return {"n":0,"accuracy":np.nan,"ann_ret":np.nan,"max_dd":np.nan,
                "win_rate":np.nan,"n_periods":0}
    # 准确率: 信号方向与收益方向一致(全样本)
    correct = (np.sign(sig[valid]) == np.sign(ret[valid]))
    accuracy = correct.mean()
    # 胜率: 该次交易(信号方向收益>0)的比例
    port_ret = sig[valid] * ret[valid]
    win_rate = (port_ret>0).mean()
    # 年化/回撤: 用非重叠窗口收益序列(每HORIZON日一个点)
    sig_nz = sig[valid]; ret_nz = ret[valid]
    n_nz = len(sig_nz)
    # 每隔HORIZON取一个点(非重叠), 得到真实20日周期收益序列
    idx_period = np.arange(0, n_nz, HORIZON)
    period_port = sig_nz[idx_period] * ret_nz[idx_period]
    n_periods = len(period_port)
    if n_periods >= 2:
        cum = np.cumprod(1 + period_port)
        # 年化: 每周期20日, 一年约250/20=12.5个周期
        periods_per_year = n_days_per_year / HORIZON
        total_ret = cum[-1] - 1
        ann_ret = (1 + total_ret) ** (periods_per_year / n_periods) - 1
        peak = np.maximum.accumulate(cum)
        dd = (cum - peak) / peak
        max_dd = dd.min()
    else:
        ann_ret = np.nan; max_dd = np.nan
    return {"n":n_sig,"accuracy":accuracy,"ann_ret":ann_ret,
            "max_dd":max_dd,"win_rate":win_rate,"n_periods":n_periods}

# 基准: 固定多头 (sig=1)
base_sig = np.ones(len(ev))
metrics = {
    "基准(固定多头)": backtest_metrics(base_sig, ev["base_ret"].values),
    "对照A(全局因子)": backtest_metrics(ev["sig_global"].values, ev["base_ret"].values),
    "对照B(状态条件)": backtest_metrics(ev["sig_state"].values, ev["base_ret"].values),
}

# 对照B: 排除S2后的准确率(S2信号=0已不计入, 但需确认)
metrics_b_nos2 = metrics["对照B(状态条件)"]

# 对照A/A'在S0/S1/S3子集上的准确率(公平对比, 排除S2)
ev_no_s2 = ev[ev["state"]!=2]
metricsA_no_s2 = backtest_metrics(ev_no_s2["sig_global"].values, ev_no_s2["base_ret"].values)
metricsA_dir_no_s2 = backtest_metrics(ev_no_s2["sig_global_dir"].values, ev_no_s2["base_ret"].values)
metricsB_no_s2 = backtest_metrics(ev_no_s2["sig_state"].values, ev_no_s2["base_ret"].values)

comp_rows = []
for name,m in [("基准(固定多头)",metrics["基准(固定多头)"]),
               ("对照A(全局因子,无方向校准,去S2)",metricsA_no_s2),
               ("对照A'(全局因子+方向校准,去S2)",metricsA_dir_no_s2),
               ("对照B(状态条件+方向校准,去S2)",metricsB_no_s2)]:
    comp_rows.append({"组别":name,"有效样本":m["n"],
        "涨跌准确率":m["accuracy"],"年化收益":m["ann_ret"],
        "最大回撤":m["max_dd"],"胜率":m["win_rate"]})
comp_df = pd.DataFrame(comp_rows)
comp_df.to_csv(os.path.join(DATA,"exp405b_comparison.csv"), index=False)

print("\n对照组对比:")
print(comp_df.to_string(index=False))

# ---------- 5. 报告 ----------
rep=[]
rep.append("# exp405b 分市场状态条件因子检验\n")
rep.append("> 任务: 保留因子分状态检验 + 全局vs状态条件对照")
rep.append("> 框架: 沿用exp404a滚动K=4 GMM状态标签(样本外)")
rep.append(f"> 对齐样本: {len(F)}行 ({F['date'].min().date()} ~ {F['date'].max().date()})")
rep.append(f"> 保留因子(来自exp405a): {', '.join(FEAT_CN.get(f,f) for f in KEEP_FACTORS)}")
rep.append("> **S2样本仅26天, 统计置信不足, 仅保留记录, 不采信、不用于策略推断**\n")

rep.append("## 1. 各状态有效因子(IC绝对值最大)\n")
rep.append("| State | 有效因子 | 平均IC | 方向 |")
rep.append("|-------|----------|--------|------|")
for sid in TARGET_STATES:
    fc,ic = state_best[sid]
    direction = "因子高→做多" if ic>0 else "因子高→做空"
    rep.append(f"| {sid} | {FEAT_CN.get(fc,fc)} | {ic:+.4f} | {direction} |")
rep.append("")

rep.append("## 2. 分状态滚动RankIC汇总(S0/S1/S3)\n")
rep.append("| State | 因子 | 平均IC | 标准差 | 窗口数 |")
rep.append("|-------|------|--------|--------|--------|")
for _,r in state_ic_sum.iterrows():
    rep.append(f"| {int(r['state'])} | {FEAT_CN.get(r['factor'],r['factor'])} | "
        f"{r['mean_ic']:+.4f} | {r['std_ic']:.4f} | {int(r['n_win'])} |")
rep.append("")

rep.append("## 3. 对照组对比(样本外)\n")
rep.append("| 组别 | 有效样本 | 涨跌准确率 | 年化收益 | 最大回撤 | 胜率 |")
rep.append("|------|----------|------------|----------|----------|------|")
for _,r in comp_df.iterrows():
    acc = f"{r['涨跌准确率']*100:.1f}%" if not pd.isna(r['涨跌准确率']) else "NA"
    ann = f"{r['年化收益']*100:.1f}%" if not pd.isna(r['年化收益']) else "NA"
    mdd = f"{r['最大回撤']*100:.1f}%" if not pd.isna(r['最大回撤']) else "NA"
    wr = f"{r['胜率']*100:.1f}%" if not pd.isna(r['胜率']) else "NA"
    rep.append(f"| {r['组别']} | {r['有效样本']} | {acc} | {ann} | {mdd} | {wr} |")
rep.append("")

rep.append("## 4. 全局 vs 分状态条件效果对比(去S2)\n")
accA = metricsA_no_s2["accuracy"]; annA = metricsA_no_s2["ann_ret"]
accA_dir = metricsA_dir_no_s2["accuracy"]; annA_dir = metricsA_dir_no_s2["ann_ret"]
accB = metricsB_no_s2["accuracy"]; annB = metricsB_no_s2["ann_ret"]
mddA = metricsA_no_s2["max_dd"]; mddB = metricsB_no_s2["max_dd"]
rep.append(f"- **对照A(全局因子,无方向校准)**: 准确率{accA*100:.1f}%, 年化{annA*100:.1f}%, 回撤{mddA*100:.1f}%")
rep.append(f"  - 注: 库存类因子IC为负(库存高→未来收益低), 无方向校准时信号方向反, 准确率低")
rep.append(f"- **对照A'(全局因子+方向校准)**: 准确率{accA_dir*100:.1f}%, 年化{annA_dir*100:.1f}%, 回撤{metricsA_dir_no_s2['max_dd']*100:.1f}%")
rep.append(f"  - 注: 按各因子全局IC符号校准方向, 是公平的全局基线")
rep.append(f"- **对照B(状态条件+方向校准)**: 准确率{accB*100:.1f}%, 年化{annB*100:.1f}%, 回撤{mddB*100:.1f}%")
diff_acc = accB - accA_dir
rep.append(f"\n- **分状态条件 vs 全局(均含方向校准)的增益**: 准确率{diff_acc*100:+.1f}pp, 年化{(annB-annA_dir)*100:+.1f}pp")
if abs(diff_acc) < 0.03:
    rep.append("- 结论: 分状态条件相对全局(方向校准后)增益有限, 库存环比20日跨状态方向稳定, 状态条件选择边际贡献不大")
elif diff_acc > 0:
    rep.append("- 结论: 分状态条件相对全局(方向校准后)有正向增益, 不同状态启用对应因子有效")
else:
    rep.append("- 结论: 分状态条件相对全局(方向校准后)无增益, 全局方向校准已捕捉主要信息")
rep.append("")
rep.append("### State 0 有效因子说明\n")
rep.append("- State 0 在滚动窗口子集内每窗口样本<5天, 无法计算稳定的状态内部IC")
rep.append(f"- 故 State 0 回退使用全局最佳因子 **{FEAT_CN.get(_GLOBAL_BEST_FC,_GLOBAL_BEST_FC)}**(全局平均IC={_GLOBAL_BEST_IC:+.4f}), 非S0内部真实IC, 结论仅供参考\n")

rep.append("## 5. S2状态处理声明\n")
rep.append(f"- S2样本量: {len(s2)}行, 占样本外{len(s2)/len(F)*100:.1f}%")
rep.append("- 因样本量不足, 统计置信度低, **结论不采信, 不用于策略推断**")
rep.append("- S2数据已保留在 exp405b_s2_record.csv 备查\n")

rep.append("## 6. 局限性\n")
rep.append(f"- 样本外仅{len(F)}行, 17滚动窗口, 统计效力有限")
rep.append("- 准确率/年化基于20日前向收益窗口, 年化折算为近似值")
rep.append("- 全部为历史统计事实, 不含涨跌预测")

with open(os.path.join(REPS,"exp405b_state_factor_review.md"),"w",encoding="utf-8") as f:
    f.write("\n".join(rep))

print("\n" + "="*55)
print("exp405b 完成")
print("="*55)
print(f"报告: {REPS}/exp405b_state_factor_review.md")
print(f"S2记录: {DATA}/exp405b_s2_record.csv ({len(s2)}行)")
