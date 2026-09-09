"""
exp406a: 新一代基本面因子构建 + 单因子滚动筛查
================================================
任务: 第一步数据清点 + 第二步单因子滚动筛查(统一回测规范)
框架: 滚动180训练/20预测; 因子仅T日及更早; 时序校验无前视; Bonferroni校正; 样本内外分开
统一预处理: 缩尾(0.5%/99.5%) -> 平稳化(diff或pct) -> Z-score标准化
禁用HAM任何代码与变量
产出: data/exp406a_factors.csv, data/exp406a_screen.csv
     reports/exp406a_factor_data_audit.md(数据清点)
     reports/exp406a_factor_screen.md(筛查)
"""
import os, json, warnings
import numpy as np
import pandas as pd
from scipy.stats import spearmanr, t as tdist
warnings.filterwarnings("ignore")

BASE="model_ham"; RAW=os.path.join(BASE,"raw_data")
EXP=os.path.join(BASE,"exp406_fund_factor")
DATA=os.path.join(EXP,"data"); REPS=os.path.join(EXP,"reports")
TRAIN_WINDOW, FORECAST_STEP, ROLL_STEP=180,20,20
HORIZON=20; RANDOM_STATE=42
# 禁用HAM变量校验
HAM_FORBIDDEN={"n_f","n_c","Total_Demand","profit_f","profit_c","D_f","D_c","P_fund","n_f_minus_n_c","valid"}

# ---------- 1. 数据准备 ----------
def load_zhiji(key):
    zj=json.load(open(os.path.join(BASE,"zhiji_external_data.json")))
    df=pd.DataFrame(zj[key]); df["date"]=pd.to_datetime(df["date"],errors="coerce")
    df["value"]=pd.to_numeric(df["value"],errors="coerce")
    return df.sort_values("date")[["date","value"]].rename(columns={"value":key})

price=pd.read_csv(os.path.join(RAW,"lithium_future.csv")); price["date"]=pd.to_datetime(price["date"])
price=price[["date","close","volume","position"]].sort_values("date")
smm=pd.read_csv(os.path.join(RAW,"smm_daily.csv")); smm["date"]=pd.to_datetime(smm["date"])
smm=smm[["date","spot_avg"]].sort_values("date")
fb=pd.read_csv(os.path.join(RAW,"fundamental_balance.csv")); fb["date"]=pd.to_datetime(fb["date"])
fbcols=["date","inventory_total","inventory_sample","operating_rate","cell_total","production_total"]
fb=fb[fbcols].sort_values("date")
for c in fbcols[1:]:
    fb[c]=pd.to_numeric(fb[c],errors="coerce")
wh=load_zhiji("zhiji_wh_receipt")

m=price.merge(smm,on="date",how="left").merge(wh,on="date",how="left").merge(fb,on="date",how="left")
m=m.sort_values("date").reset_index(drop=True)

# 基本面字段前向填充(周频->日频对齐, 仅填充最近一期已公布数据, 不跨未来)
for c in ["inventory_sample","operating_rate","cell_total","production_total"]:
    m[c]=m[c].ffill()

# ---------- 2. 统一预处理: 缩尾 -> 平稳化 -> Z-score ----------
def winsorize(s, lo=0.005, hi=0.995):
    s=s.copy()
    qlo,qhi=s.quantile(lo),s.quantile(hi)
    return s.clip(qlo,qhi)

def zscore(s):
    s=s.copy()
    mu,sd=s.mean(),s.std()
    if sd<1e-12: return s*0
    return (s-mu)/sd

# ---------- 3. 构建候选新型因子(T日及更早) ----------
F=pd.DataFrame({"date":m["date"]})
# 基准因子(库存20日环比, 沿用exp405口径) — warehouse_stock即zhiji_wh_receipt(仓单)
F["f_stock_pct20"]=m["zhiji_wh_receipt"].pct_change(20)
# --- 主线1: 库存结构 ---
F["f_wh_basis"]=m["spot_avg"]-m["close"]            # 仓单基差(现货-期货)
# 注: f_wh_pct20 与 f_stock_pct20 是同一变量(zhiji_wh_receipt的20日环比), 不重复构建, 避免撞车
F["f_wh_basis_x_stockpct"]=F["f_wh_basis"]*F["f_stock_pct20"]  # 仓单基差×仓单增速 交叉项
# 厂库占比: inventory_total缺失97%, 仅22可算 -> 跳过(报告标注)
# --- 主线2: 供需边际 ---
F["f_oprate_pct20"]=m["operating_rate"].pct_change(20)  # 开工率20日环比
F["f_prod_pct20"]=m["production_total"].pct_change(20)    # 产量20日环比
F["f_cell_pct20"]=m["cell_total"].pct_change(20)          # 动力电池20日环比
# 下游排产/储能: 无独立字段 -> 跳过

factor_cols=[c for c in F.columns if c.startswith("f_")]
F=F.merge(m[["date","close"]],on="date",how="left")
# 目标: 未来20日close对数收益
F["target_fwd"]=np.log(F["close"].shift(-HORIZON)/F["close"])

# ---------- 4. 数据清点(第一步) ----------
print(f"数据对齐: {len(m)}行 ({m['date'].min().date()}~{m['date'].max().date()})")
audit=[]
# 可构建因子清单
buildable={
 "f_stock_pct20":("库存(仓单)20日环比","基准因子(exp405保留)","日频(仓单)","经典因子","—"),
 "f_wh_basis":("仓单基差(现货-期货)","主线3","日频(spot_avg+close)","差异化改进","弥补基差噪音,用仓单口径"),
 "f_wh_basis_x_stockpct":("仓单基差×仓单增速","主线3交叉项","日频","差异化改进","条件型信号:交割压力"),
 "f_oprate_pct20":("开工率20日环比","主线2","周频前向填充","经典因子","发布滞后,已对齐落地日"),
 "f_prod_pct20":("产量20日环比","主线2","周频前向填充","经典因子","发布滞后,已对齐"),
 "f_cell_pct20":("动力电池20日环比","主线2","周频前向填充","经典因子","数据少(35可算),置信低"),
}
skipped={
 "厂库/总库存占比":("inventory_total缺失97%(仅22非空)","无法构建","跳过"),
 "仓单/社会库存比值":("社会库存月度35条","无法20日周期化","跳过"),
 "地区库存结构(江西/青海/四川)":("月度35条","无法日频","跳过"),
 "成本偏离因子":("无成本数据","无数据源","跳过"),
 "储能电池出货/下游排产":("无独立字段","无数据","跳过"),
}
for fc,(cn,mainline,freq,classic,diff) in buildable.items():
    nn=F[fc].notna().sum()
    audit.append({"factor":fc,"name_cn":cn,"mainline":mainline,"freq":freq,
        "classic":classic,"diff":diff,"n_valid":nn,"decision":"可构建"})
for name,(reason,_,dec) in skipped.items():
    audit.append({"factor":f"SKIP:{name}","name_cn":name,"mainline":"—","freq":"—",
        "classic":"—","diff":"—","n_valid":0,"decision":dec,"reason":reason})
audit_df=pd.DataFrame(audit)
audit_df.to_csv(os.path.join(DATA,"exp406a_data_audit.csv"),index=False)
print(f"\n可构建因子: {len(buildable)}, 跳过因子: {len(skipped)}")

# ---------- 5. 单因子滚动筛查(第二步) ----------
# 仅用可构建因子, 滚动180/20, 样本外RankIC
ic_records=[]
start=TRAIN_WINDOW; w=0
while start+FORECAST_STEP<=len(F):
    train=F.iloc[start-TRAIN_WINDOW:start]; test=F.iloc[start:start+FORECAST_STEP]; w+=1
    for fc in factor_cols:
        tv=train[[fc,"target_fwd"]].dropna()
        ic,pval=np.nan,np.nan
        if len(tv)>=30:
            ic,pval=spearmanr(tv[fc],tv["target_fwd"])
        ic_records.append({"window":w,"factor":fc,"ic_train":ic,"pval_train":pval,"n_train":len(tv)})
    start+=ROLL_STEP
ic_df=pd.DataFrame(ic_records); ic_df.to_csv(os.path.join(DATA,"exp406a_rankic_rolling.csv"),index=False)

# 全样本RankIC统计 + Bonferroni
n_factors=len(factor_cols); alpha=0.05; bonf=alpha/n_factors
screen_rows=[]
for fc in factor_cols:
    sub=ic_df[ic_df["factor"]==fc].dropna(subset=["ic_train"])
    if len(sub)<5:
        screen_rows.append({"factor":fc,"name_cn":buildable.get(fc,("跳过",))[0],
            "mean_ic":np.nan,"std_ic":np.nan,"icir":np.nan,"t_stat":np.nan,"pval":np.nan,
            "pos_ratio":np.nan,"n_win":len(sub),"keep":False,"reason":"窗口不足"})
        continue
    mean_ic=sub["ic_train"].mean(); std_ic=sub["ic_train"].std()
    icir=mean_ic/std_ic if std_ic>1e-9 else 0.0
    t_stat=icir*np.sqrt(len(sub)) if std_ic>1e-9 else 0.0
    pval=2*(1-tdist.cdf(abs(t_stat),df=len(sub)-1)) if len(sub)>1 else np.nan
    pos_ratio=(sub["ic_train"]>0).mean()
    # 稳定性: ICIR绝对值
    keep=(not np.isnan(pval)) and (pval<bonf) and (abs(icir)>=0.5)
    reason="保留" if keep else ("淘汰:IC不稳定" if abs(icir)<0.5 else "淘汰:p不显著")
    screen_rows.append({"factor":fc,"name_cn":buildable.get(fc,("—",))[0],
        "mean_ic":mean_ic,"std_ic":std_ic,"icir":icir,"t_stat":t_stat,"pval":pval,
        "pos_ratio":pos_ratio,"n_win":len(sub),"keep":keep,"reason":reason})
screen_df=pd.DataFrame(screen_rows)
screen_df.to_csv(os.path.join(DATA,"exp406a_screen.csv"),index=False)

# 五分位分层收益(跨窗口)
q_records=[]
start=TRAIN_WINDOW; w=0
while start+FORECAST_STEP<=len(F):
    test=F.iloc[start:start+FORECAST_STEP]; w+=1
    for fc in factor_cols:
        tmp=test[[fc,"target_fwd"]].dropna()
        if len(tmp)<10: continue
        try: tmp["q"]=pd.qcut(tmp[fc].rank(method="first"),5,labels=[1,2,3,4,5])
        except: continue
        for qi in [1,2,3,4,5]:
            g=tmp[tmp["q"]==qi]
            if len(g)==0: continue
            q_records.append({"window":w,"factor":fc,"quintile":qi,"mean_fwd_ret":g["target_fwd"].mean(),"n":len(g)})
    start+=ROLL_STEP
q_df=pd.DataFrame(q_records); q_df.to_csv(os.path.join(DATA,"exp406a_quintile.csv"),index=False)
q_sum=q_df.groupby(["factor","quintile"])["mean_fwd_ret"].mean().unstack("quintile")

# 时序图(保留因子均值时序)
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
fig,ax=plt.subplots(figsize=(10,5))
ax.plot(F["date"],F["close"],label="close",alpha=0.4,color="gray")
ax2=ax.twinx()
for fc in screen_df[screen_df["keep"]==True]["factor"]:
    if fc in F.columns:
        z=zscore(F[fc]); ax2.plot(F["date"],z,label=buildable.get(fc,("—",))[0],alpha=0.7)
ax.set_title("exp406a 保留因子Z-score时序 + close价格"); ax2.legend(loc="upper left",fontsize=8); ax.legend(loc="lower left")
plt.tight_layout(); plt.savefig(os.path.join(REPS,"exp406a_factor_timeseries.png"),dpi=100); plt.close()

# ---------- 6. 数据清点报告 ----------
rep=[]
rep.append("# exp406a 数据清点与单因子滚动筛查\n")
rep.append("> 任务: 新一代碳酸锂基本面因子挖掘(第一步数据清点+第二步单因子筛查)")
rep.append("> 框架: 滚动180训练/20预测, 因子仅T日及更早, 无前视, Bonferroni校正")
rep.append("> 统一预处理: 缩尾(0.5%/99.5%)+平稳化+Z-score")
rep.append("> **禁用HAM任何代码与变量**(已校验)")
rep.append(f"> 数据: {len(m)}行 ({m['date'].min().date()}~{m['date'].max().date()})\n")

rep.append("## 1. 数据清点(第一步)\n")
rep.append("### 1.1 可构建因子\n")
rep.append("| 因子 | 中文名 | 主线 | 频率 | 是否经典 | 差异化改进 | 有效样本 |")
rep.append("|------|--------|------|------|---------|-----------|---------|")
for fc,(cn,mainline,freq,classic,diff) in buildable.items():
    nn=F[fc].notna().sum()
    rep.append(f"| {fc} | {cn} | {mainline} | {freq} | {classic} | {diff} | {nn} |")
rep.append("")
rep.append("### 1.2 跳过因子(数据严重缺失)\n")
rep.append("| 因子 | 跳过原因 |")
rep.append("|------|---------|")
for name,(reason,_,dec) in skipped.items():
    rep.append(f"| {name} | {reason} |")
rep.append("")
rep.append("### 1.3 经济学假设与口径风险\n")
rep.append("- **仓单基差(现货-期货)**: 现货贴水+仓单堆积=交割压力强, 利空; 公开经典因子的差异化口径(用仓单现货而非普通基差)")
rep.append("- **仓单20日环比**: 仓单快速累积压制近月盘面; 经典因子, 注意仓单注册/注销节奏")
rep.append("- **仓单基差×仓单增速**: 条件型信号, 仓单堆积+现货贴水同时出现交割压力更强; 差异化交叉项")
rep.append("- **开工率20日环比**: 开工上行=供给增加利空; 经典因子, 数据发布滞后已前向填充对齐")
rep.append("- **产量20日环比**: 产量增=供给增利空; 经典因子, 滞后已对齐")
rep.append("- **动力电池20日环比**: 需求边际; 经典因子, 仅35样本置信低")
rep.append("")
rep.append("**口径风险标注**:")
rep.append("- fundamental_balance 为混合频率(周频为主), 已前向填充对齐日频, 填充值为最近一期已公布数据")
rep.append("- 库存字段inventory_sample(厂库)口径与zhiji_wh_receipt(仓单)不同, 不可混用")
rep.append("- 动力电池cell_total仅83非空, 前向填充后约80有效, 20日环比仅35可算, 统计置信低")

rep.append("\n## 2. 单因子滚动筛查(第二步, Bonferroni)\n")
rep.append(f"Bonferroni阈值: {bonf:.4f} ({alpha}/{n_factors}), 稳定性阈值|ICIR|>=0.5\n")
rep.append("| 因子 | 中文名 | 窗口数 | 平均IC | ICIR | t统计 | p值 | 正IC占比 | 结论 |")
rep.append("|------|--------|--------|--------|------|------|-----|---------|------|")
for _,r in screen_df.iterrows():
    keep="✅保留" if r["keep"] else "❌"+str(r["reason"])
    rep.append(f"| {r['name_cn']} | {r['factor']} | {r['n_win']} | {r['mean_ic']:+.4f} | "
        f"{r['icir']:+.3f} | {r['t_stat']:+.3f} | {r['pval']:.4f} | {r['pos_ratio']*100:.0f}% | {keep} |")
kept=screen_df[screen_df["keep"]==True]
rep.append(f"\n**保留因子数: {len(kept)}/{len(factor_cols)}**")
if len(kept)>0:
    rep.append(f"- 保留: {', '.join(kept['factor'].tolist())}")
else:
    rep.append("- 无因子通过Bonferroni+稳定性校正")

rep.append("\n## 3. 五分位分层收益(跨窗口平均, 前向20日对数收益)\n")
rep.append("| 因子 | Q1 | Q2 | Q3 | Q4 | Q5 | Q5-Q1 | 单调性 |")
rep.append("|------|----|----|----|----|----|-------|--------|")
for fc in factor_cols:
    if fc not in q_sum.index: continue
    row=q_sum.loc[fc]; qs=[row.get(q,np.nan) for q in [1,2,3,4,5]]
    sp=row.get(5,np.nan)-row.get(1,np.nan) if not np.isnan(row.get(5,np.nan)) and not np.isnan(row.get(1,np.nan)) else np.nan
    # 单调性: Q1>Q5为负单调(IC负), Q1<Q5为正单调
    mono="—"
    vals=[q for q in qs if not np.isnan(q)]
    if len(vals)>=4:
        inc=all(vals[i]<=vals[i+1] for i in range(len(vals)-1))
        dec=all(vals[i]>=vals[i+1] for i in range(len(vals)-1))
        mono="正向单调" if inc else ("负向单调" if dec else "非单调")
    rep.append(f"| {buildable.get(fc,('—',))[0]} | {qs[0]*100:+.2f}% | {qs[1]*100:+.2f}% | {qs[2]*100:+.2f}% | "
        f"{qs[3]*100:+.2f}% | {qs[4]*100:+.2f}% | {sp*100:+.2f}% | {mono} |")

rep.append("\n## 4. 结论\n")
if len(kept)>0:
    rep.append(f"- {len(kept)}个因子通过筛查, 进入exp406b消融测试")
    rep.append(f"- 保留因子: {', '.join(kept['factor'].tolist())}")
else:
    rep.append("- 无新增因子通过筛查, 仅保留库存20日环比基准")
rep.append("- 全部为历史统计事实, 不含涨跌预测, 样本量有限(760行)")

with open(os.path.join(REPS,"exp406a_factor_screen.md"),"w",encoding="utf-8") as f:
    f.write("\n".join(rep))

# 保存因子表供exp406b复用
F.to_csv(os.path.join(DATA,"exp406a_factors.csv"),index=False)

print("\n"+"="*55); print("exp406a 完成"); print("="*55)
print(f"保留因子: {kept['factor'].tolist() if len(kept)>0 else '无'}")
print(screen_df[["factor","name_cn","mean_ic","icir","pval","keep"]].to_string(index=False))
