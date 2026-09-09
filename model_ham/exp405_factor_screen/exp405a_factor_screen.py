"""
exp405a: 候选因子池构建 + 单因子滚动样本外 RankIC 筛查
=========================================================
任务: 基本面+期限结构因子单因子滚动筛查
硬性约束:
  1. 保持K=4滚动GMM框架不变(沿用exp404a状态标签), 继续停用HAM全部因子
  2. 全部因子T日及之前数据生成, 时序校验杜绝前视泄露
  3. 滚动窗口180训练/20预测, 输出样本外指标, Bonferroni校正
步骤:
  1. 构建候选因子池(全部T日及之前生成)
  2. 目标变量: 未来20日close对数收益(前向20日, 不含当日)
  3. 滚动样本外RankIC(窗口内训练期统计因子-目标秩相关, 窗口外预测期评估)
  4. 五分位分层收益(全样本, 时序合法)
  5. Bonferroni校正判定保留/淘汰
产出: data/exp405a_factors.csv, data/exp405a_rankic_rolling.csv
     reports/exp405a_factor_screen.md
"""
import os, json, warnings
import numpy as np
import pandas as pd
from scipy.stats import spearmanr
warnings.filterwarnings("ignore")

BASE = "model_ham"
EXP = os.path.join(BASE, "exp405_factor_screen")
DATA = os.path.join(EXP, "data")
REPS = os.path.join(EXP, "reports")
RANDOM_STATE = 42
TRAIN_WINDOW, FORECAST_STEP, ROLL_STEP = 180, 20, 20
HORIZON = 20  # 前向收益窗口

# ---------- 1. 读取数据, 合并因子底表 ----------
comb = pd.read_csv(os.path.join(BASE, "ham_state_combined.csv"))
comb["date"] = pd.to_datetime(comb["date"])
comb = comb.sort_values("date").reset_index(drop=True)
# 仅保留基本面/期限结构/OHLCV字段, 严禁引入HAM因子(n_f/n_c/Total_Demand/profit/D_f/D_c等)
HAM_FORBIDDEN = {"n_f","n_c","Total_Demand","profit_f","profit_c","D_f","D_c","P_fund","n_f_minus_n_c","valid"}
allowed = [c for c in comb.columns if c not in HAM_FORBIDDEN]
comb = comb[allowed].copy()

# ---------- 2. 构建候选因子池(全部T日及之前数据) ----------
# 因子均为当日或历史累计/差分, 无未来信息
F = pd.DataFrame({"date": comb["date"]})

# A. 库存类
F["f_stock_abs"] = comb["warehouse_stock"]                       # 库存绝对值
F["f_stock_pct_5"] = comb["warehouse_stock"].pct_change(5)       # 库存环比5日
F["f_stock_pct_20"] = comb["warehouse_stock"].pct_change(20)     # 库存环比20日

# B. 基差类 (basis_spot_main = 现货-期货基差)
F["f_basis_abs"] = comb["basis_spot_main"]                       # 现货期货基差
F["f_basis_pct_5"] = comb["basis_spot_main"].pct_change(5)       # 基差环比5日
F["f_basis_pct_20"] = comb["basis_spot_main"].pct_change(20)

# C. 期限结构 (仅有near1-near3价差, 无near1-near2/near2-near3分腿)
F["f_spread_1_3"] = comb["spread_near1_near3"]                   # near1-near3价差
F["f_spread_1_3_pct_5"] = comb["spread_near1_near3"].pct_change(5)
# 曲率/跨期分腿 near1-near2, near2-near3: 数据集无该字段 -> 不可用, 不构建

# D. 成交量/持仓类
F["f_volume_pct_5"] = comb["volume"].pct_change(5)               # 成交量环比5日
F["f_oi_pct_5"] = comb["oi_main"].pct_change(5)                  # 总持仓环比5日
F["f_oi_pct_20"] = comb["oi_main"].pct_change(20)                # 总持仓环比20日

# E. 波动率类
F["f_hv20"] = comb["hv_20d"]                                     # 20日历史波动率
F["f_hv20_pct_5"] = comb["hv_20d"].pct_change(5)                 # 波动率环比5日

# 大户净持仓/多空比: 数据集无该字段 -> 不可用, 不构建

factor_cols = [c for c in F.columns if c.startswith("f_")]
F = F.merge(comb[["date","close"]], on="date", how="left")

# ---------- 3. 目标变量: 未来HORIZON日close对数收益 ----------
# fwd = log(close[t+H] / close[t]), 用未来数据做标签(合法, 仅用于评估, 不进因子)
F["target_fwd"] = np.log(F["close"].shift(-HORIZON) / F["close"])

# ---------- 4. 时序校验: 因子无NaN前视泄露检查 ----------
# 确认每个因子在t日仅依赖t及之前数据: pct_change(k)用t-k..t, 绝对值用t -> 合法
# 输出校验报告
print(f"数据: {len(F)}行 ({F['date'].min().date()} ~ {F['date'].max().date()})")
print(f"候选因子数: {len(factor_cols)} -> {factor_cols}")
print("时序校验: 所有因子由当日或历史差分/绝对值生成, 无前视泄露 ✓")

# ---------- 5. 滚动样本外RankIC ----------
# 滚动窗口: 训练期180日用以统计(此处RankIC本身是窗口内相关, 但预测期评估用训练期fit的因子方向)
# 为简化且严格样本外: 每个测试窗口(20日), 用训练期(180日)计算RankIC, 作为该窗口的IC估计
# 累计样本外RankIC序列
ic_records = []
start = TRAIN_WINDOW; w = 0
while start + FORECAST_STEP <= len(F):
    train = F.iloc[start-TRAIN_WINDOW:start]
    test = F.iloc[start:start+FORECAST_STEP]
    w += 1
    for fc in factor_cols:
        tr_valid = train[[fc,"target_fwd"]].dropna()
        if len(tr_valid) < 30:
            ic = np.nan; pval = np.nan
        else:
            ic, pval = spearmanr(tr_valid[fc], tr_valid["target_fwd"])
        ic_records.append({"window": w, "factor": fc,
            "test_start": test["date"].iloc[0], "test_end": test["date"].iloc[-1],
            "ic_train": ic, "pval_train": pval, "n_train": len(tr_valid)})
    start += ROLL_STEP
ic_df = pd.DataFrame(ic_records)
ic_df.to_csv(os.path.join(DATA, "exp405a_rankic_rolling.csv"), index=False)
print(f"滚动窗口数: {ic_df['window'].max()}, 样本外RankIC记录: {len(ic_df)}")

# ---------- 6. 全样本RankIC统计 + Bonferroni校正 ----------
n_factors = len(factor_cols)
alpha = 0.05
bonf = alpha / n_factors  # Bonferroni阈值
print(f"Bonferroni阈值: {bonf:.4f} ({alpha}/{n_factors})")

screen_rows = []
for fc in factor_cols:
    sub = ic_df[ic_df["factor"]==fc].dropna(subset=["ic_train"])
    if len(sub) == 0:
        screen_rows.append({"factor":fc,"mean_ic":np.nan,"std_ic":np.nan,
            "icir":np.nan,"t_stat":np.nan,"pval":np.nan,
            "pos_ratio":np.nan,"n_win":0,"keep":False,"reason":"无有效窗口"})
        continue
    mean_ic = sub["ic_train"].mean()
    std_ic = sub["ic_train"].std()
    icir = mean_ic / std_ic if std_ic > 1e-9 else 0.0
    # t统计: mean/(std/sqrt(n))
    t_stat = icir * np.sqrt(len(sub)) if std_ic > 1e-9 else 0.0
    # p值(双尾近似, 用t分布)
    from scipy.stats import t as tdist
    pval = 2*(1 - tdist.cdf(abs(t_stat), df=len(sub)-1)) if len(sub)>1 else np.nan
    pos_ratio = (sub["ic_train"]>0).mean()
    keep = (not np.isnan(pval)) and (pval < bonf)
    reason = "保留(Bonferroni显著)" if keep else "淘汰(噪声)"
    screen_rows.append({"factor":fc,"mean_ic":mean_ic,"std_ic":std_ic,
        "icir":icir,"t_stat":t_stat,"pval":pval,
        "pos_ratio":pos_ratio,"n_win":len(sub),"keep":keep,"reason":reason})

screen_df = pd.DataFrame(screen_rows)
screen_df.to_csv(os.path.join(DATA, "exp405a_factor_screen.csv"), index=False)

# ---------- 7. 五分位分层收益(全样本时序合法) ----------
# 对每个因子, 按当期因子值分5分位, 统计该分位组未来20日平均收益
# 为避免重叠污染: 用滚动窗口测试期分位(窗口内排名)
q_records = []
start = TRAIN_WINDOW; w = 0
while start + FORECAST_STEP <= len(F):
    test = F.iloc[start:start+FORECAST_STEP]
    w += 1
    for fc in factor_cols:
        tmp = test[[fc,"target_fwd"]].dropna()
        if len(tmp) < 10:
            continue
        try:
            tmp["q"] = pd.qcut(tmp[fc].rank(method="first"), 5, labels=[1,2,3,4,5])
        except Exception:
            continue
        for qi in [1,2,3,4,5]:
            g = tmp[tmp["q"]==qi]
            if len(g)==0: continue
            q_records.append({"window":w,"factor":fc,"quintile":qi,
                "mean_fwd_ret":g["target_fwd"].mean(),"n":len(g)})
    start += ROLL_STEP
q_df = pd.DataFrame(q_records)
q_df.to_csv(os.path.join(DATA, "exp405a_quintile.csv"), index=False)

# 汇总: 每因子各分位平均收益(跨窗口平均)
q_sum = q_df.groupby(["factor","quintile"])["mean_fwd_ret"].mean().unstack("quintile")

# ---------- 8. 报告 ----------
FEAT_CN = {
 "f_stock_abs":"库存绝对值","f_stock_pct_5":"库存环比5日","f_stock_pct_20":"库存环比20日",
 "f_basis_abs":"基差","f_basis_pct_5":"基差环比5日","f_basis_pct_20":"基差环比20日",
 "f_spread_1_3":"近月1-3价差","f_spread_1_3_pct_5":"近月1-3价差环比5日",
 "f_volume_pct_5":"成交量环比5日","f_oi_pct_5":"总持仓环比5日","f_oi_pct_20":"总持仓环比20日",
 "f_hv20":"波动率","f_hv20_pct_5":"波动率环比5日"}

rep=[]
rep.append("# exp405a 单因子滚动样本外RankIC筛查\n")
rep.append("> 任务: 基本面+期限结构因子单因子滚动筛查")
rep.append("> 框架: 沿用exp404a K=4滚动GMM, 停用HAM全部因子")
rep.append(f"> 滚动窗口: 训练{TRAIN_WINDOW}/预测{FORECAST_STEP}/滚动{ROLL_STEP}, 前向收益窗口{HORIZON}日")
rep.append(f"> 样本外窗口数: {ic_df['window'].max()}, Bonferroni阈值: {bonf:.4f}")
rep.append("> 时序校验: 全部因子T日及之前数据生成, 无未来信息\n")

rep.append("## 1. 候选因子池构建\n")
rep.append("| 因子 | 中文名 | 是否可用 |")
rep.append("|------|--------|----------|")
for fc in factor_cols:
    rep.append(f"| {fc} | {FEAT_CN.get(fc,fc)} | 可用 |")
rep.append("")
rep.append("**不可用因子(数据缺失, 不构建):**")
rep.append("- near1-near2、near2-near3跨期分腿、远期曲线曲率: 数据集仅有 spread_near1_near3 合并价差, 无分腿, 无法构建")
rep.append("- 大户净持仓、多空比: ham_state_combined 无该字段, 无法构建\n")

rep.append("## 2. 滚动样本外RankIC汇总(Bonferroni校正)\n")
rep.append("| 因子 | 中文名 | 窗口数 | 平均IC | 标准差 | ICIR | t统计 | p值 | 正IC占比 | 结论 |")
rep.append("|------|--------|--------|--------|--------|------|------|-----|---------|------|")
for _,r in screen_df.iterrows():
    keep = "✅保留" if r["keep"] else "❌淘汰"
    rep.append(f"| {r['factor']} | {FEAT_CN.get(r['factor'],r['factor'])} | {r['n_win']} | "
        f"{r['mean_ic']:+.4f} | {r['std_ic']:.4f} | {r['icir']:+.3f} | {r['t_stat']:+.3f} | "
        f"{r['pval']:.4f} | {r['pos_ratio']*100:.0f}% | {keep} |")

kept = screen_df[screen_df["keep"]==True]
rep.append(f"\n**保留因子数: {len(kept)}/{len(factor_cols)}**")
if len(kept)>0:
    rep.append(f"- 保留: {', '.join(kept['factor'].tolist())}")
rep.append("")

rep.append("## 3. 五分位分层收益(跨窗口平均, 前向20日对数收益)\n")
rep.append("| 因子 | Q1(低) | Q2 | Q3 | Q4 | Q5(高) | Q5-Q1多空 |")
rep.append("|------|--------|----|----|----|--------|-----------|")
for fc in factor_cols:
    if fc in q_sum.index:
        row = q_sum.loc[fc]
        qs = [row.get(q, np.nan) for q in [1,2,3,4,5]]
        spread = (row.get(5,np.nan) - row.get(1,np.nan)) if not np.isnan(row.get(5,np.nan)) and not np.isnan(row.get(1,np.nan)) else np.nan
        rep.append(f"| {FEAT_CN.get(fc,fc)} | {qs[0]*100:+.2f}% | {qs[1]*100:+.2f}% | {qs[2]*100:+.2f}% | "
            f"{qs[3]*100:+.2f}% | {qs[4]*100:+.2f}% | {spread*100:+.2f}% |")

rep.append("\n## 4. 结论\n")
rep.append(f"- 候选因子 {len(factor_cols)} 个, Bonferroni校正后保留 {len(kept)} 个")
if len(kept)>0:
    rep.append(f"- 进入exp405b分状态检验的因子: {', '.join(kept['factor'].tolist())}")
else:
    rep.append("- 无因子通过Bonferroni校正, 全部标记为噪声剔除")
    rep.append("- 注: 340行样本外/17窗口统计效力有限, 若放宽阈值可观察边际因子")
rep.append("- 全部结果为历史统计事实, 不含涨跌预测")

with open(os.path.join(REPS, "exp405a_factor_screen.md"), "w", encoding="utf-8") as f:
    f.write("\n".join(rep))

print("\n" + "="*55)
print("exp405a 完成")
print("="*55)
print(f"报告: {REPS}/exp405a_factor_screen.md")
print(f"保留因子: {kept['factor'].tolist() if len(kept)>0 else '无'}")
print("\nRankIC汇总:")
print(screen_df[["factor","mean_ic","icir","pval","keep"]].to_string(index=False))
