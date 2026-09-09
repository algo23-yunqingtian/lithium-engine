"""
exp406b: 有效因子消融测试 + 稳健性扰动测试
==========================================
任务: 第三步消融(单因子+叠加库存20日环比基准) + 第四步稳健性扰动
统一回测规范: 20日持有周期; 连续时序回测(非重叠近似); 基准=单独库存20日环比
对照: ①单独新因子 ②新因子+库存20日环比线性加权叠加 ③基准(仅库存20日环比) ④固定多头
禁用HAM任何代码与变量; 简单线性加权, 非线性/非线性建模
产出: data/exp406b_ablation.csv, data/exp406b_robustness.csv
     reports/exp406b_ablation_robustness.md
"""
import os, json, warnings
import numpy as np
import pandas as pd
from scipy.stats import spearmanr
warnings.filterwarnings("ignore")

BASE="model_ham"; EXP=os.path.join(BASE,"exp406_fund_factor")
DATA=os.path.join(EXP,"data"); REPS=os.path.join(EXP,"reports")
TRAIN_WINDOW, FORECAST_STEP, ROLL_STEP=180,20,20
HORIZON=20; BASE_FACTOR="f_stock_pct20"  # 库存20日环比基准
NEW_FACTORS=["f_wh_basis_x_stockpct","f_cell_pct20"]  # 通过筛查的新因子
FEAT_CN={"f_stock_pct20":"库存20日环比(基准)","f_wh_basis_x_stockpct":"仓单基差×仓单增速",
         "f_cell_pct20":"动力电池20日环比"}
HAM_FORBIDDEN={"n_f","n_c","Total_Demand","profit_f","profit_c","D_f","D_c","P_fund","n_f_minus_n_c","valid"}

# 读取exp406a因子表
F=pd.read_csv(os.path.join(DATA,"exp406a_factors.csv"))
F["date"]=pd.to_datetime(F["date"])
F=F.sort_values("date").reset_index(drop=True)
# 校验无HAM字段
assert not (set(F.columns)&HAM_FORBIDDEN), "禁止HAM字段!"

# 基准因子全局IC方向(用于方向校准): 库存负IC -> 因子高做空
BASE_IC=-0.257450; NEW_IC={"f_wh_basis_x_stockpct":0.107401,"f_cell_pct20":-0.288753}

# ---------- 信号构造: 因子值相对训练期中位数的方向(+1高/-1低), 乘IC方向 ----------
def factor_signal(Fdf, fc, idx, ic_sign):
    train_start=max(0,idx-TRAIN_WINDOW)
    tv=Fdf[fc].iloc[train_start:idx].dropna()
    if len(tv)<30: return 0.0
    med=tv.median(); cur=Fdf[fc].iloc[idx]
    if pd.isna(cur): return 0.0
    raw=1.0 if cur>med else -1.0
    return raw*(1 if ic_sign>0 else -1)

def make_signals(Fdf):
    n=len(Fdf); base_sig=np.zeros(n); new_sig={fc:np.zeros(n) for fc in NEW_FACTORS}
    combo_sig=np.zeros(n)  # 新因子+基准叠加(等权线性)
    for i in range(n):
        s_base=factor_signal(Fdf,BASE_FACTOR,i,BASE_IC)
        base_sig[i]=s_base
        for fc in NEW_FACTORS:
            new_sig[fc][i]=factor_signal(Fdf,fc,i,NEW_IC[fc])
        # 叠加: 基准 + 新因子等权线性合成
        signals=[s_base]+[new_sig[fc][i] for fc in NEW_FACTORS]
        combo_sig[i]=np.mean(signals)
    return base_sig,new_sig,combo_sig

base_sig,new_sig,combo_sig=make_signals(F)

# ---------- 连续时序回测(20日持有, 逐日信号×20日前向收益, 净值累乘) ----------
def backtest(sig, target_fwd, n_days=250):
    """
    sig: 逐日信号(+1多/-1空/0中性)
    target_fwd: 20日前向对数收益
    连续时序: 逐日持有, 20日收益折算为逐日收益(除20), 净值累乘
    """
    sig=np.asarray(sig); fwd=np.asarray(target_fwd)
    mask=(~np.isnan(fwd))&(np.abs(sig)>1e-9)
    # 20日前向收益 -> 逐日收益(除以20近似, 避免重叠收益累乘重复计)
    daily_ret=sig* fwd/ HORIZON
    daily_ret=np.where(mask,daily_ret,0)
    # 净值
    nav=np.cumprod(1+daily_ret)
    total=nav[-1]-1
    # 准确率: 信号方向与20日前向收益方向一致(仅计有效)
    valid=(~np.isnan(fwd))&(np.abs(sig)>1e-9)&(~np.isnan(sig))
    if valid.sum()>0:
        acc=(np.sign(sig[valid])==np.sign(fwd[valid])).mean()
    else: acc=np.nan
    # 年化(250交易日)
    n_days_obs=len(daily_ret)
    ann=(1+total)**(n_days/n_days_obs)-1 if n_days_obs>0 else 0
    # 夏普(年化)
    ddaily=daily_ret[valid]
    sharpe=ddaily.mean()/ddaily.std()*np.sqrt(250) if ddaily.std()>1e-9 else np.nan
    # 最大回撤
    peak=np.maximum.accumulate(nav)
    mdd=((nav-peak)/peak).min()
    return {"n_valid":int(valid.sum()),"accuracy":acc,"ann_ret":ann,"sharpe":sharpe,"max_dd":mdd}

target_fwd=F["target_fwd"].values

# ---------- 第三步: 消融测试 ----------
ablation=[]
# ④固定多头基准
ablation.append({"组别":"固定多头","策略":"始终做多","mean_ic":np.nan,**backtest(np.ones(len(F)),target_fwd)})
# ③基准: 单独库存20日环比
ablation.append({"组别":"基准:库存20日环比","策略":"单独基准因子","mean_ic":BASE_IC,**backtest(base_sig,target_fwd)})
# ①单独新因子
for fc in NEW_FACTORS:
    ablation.append({"组别":f"单因子:{FEAT_CN[fc]}","策略":"单独新因子","mean_ic":NEW_IC[fc],
        **backtest(new_sig[fc],target_fwd)})
# ②新因子+基准叠加
ablation.append({"组别":"叠加:新因子+基准","策略":"基准+新因子等权线性","mean_ic":np.nan,
    **backtest(combo_sig,target_fwd)})

abl_df=pd.DataFrame(ablation)
abl_df.to_csv(os.path.join(DATA,"exp406b_ablation.csv"),index=False)
print("消融测试:")
print(abl_df[["组别","策略","accuracy","ann_ret","sharpe","max_dd"]].to_string(index=False))

# ---------- 第四步: 稳健性扰动测试 ----------
# 对每个保留因子(基准+新因子), 测试不同预测周期T+5/T+10/T+20 和 窗口扰动
def make_signals_horizon(Fdf, horizon, factor_list):
    n=len(Fdf); sigs={fc:np.zeros(n) for fc in factor_list}
    return sigs  # 信号不依赖horizon, 仅目标收益变

def screen_horizon(Fdf, fc, horizon, ic_sign, n_factors=3):
    """测试不同horizon下的滚动RankIC"""
    fwd=np.log(Fdf["close"].shift(-horizon)/Fdf["close"])
    ics=[]
    start=TRAIN_WINDOW; w=0
    while start+FORECAST_STEP<=len(Fdf):
        tr=Fdf.iloc[start-TRAIN_WINDOW:start][[fc]].copy()
        tr["tgt"]=fwd.iloc[start-TRAIN_WINDOW:start]
        tv=tr.dropna()
        if len(tv)>=30:
            ic,_=spearmanr(tv[fc],tv["tgt"]); ics.append(ic)
        start+=ROLL_STEP; w+=1
    if len(ics)<5: return np.nan,np.nan
    return np.mean(ics), np.std(ics)

robust_rows=[]
for fc in [BASE_FACTOR]+NEW_FACTORS:
    for horizon in [5,10,20]:
        ic_mean,ic_std=screen_horizon(F,fc,horizon,NEW_IC.get(fc,BASE_IC))
        robust_rows.append({"factor":fc,"name_cn":FEAT_CN[fc],"horizon":f"T+{horizon}",
            "mean_ic":ic_mean,"std_ic":ic_std,"stable":abs(ic_mean)>=0.05})
robust_df=pd.DataFrame(robust_rows)
robust_df.to_csv(os.path.join(DATA,"exp406b_robustness.csv"),index=False)
print("\n稳健性扰动(T+5/10/20):")
print(robust_df.to_string(index=False))

# 窗口扰动: 训练窗口160/180/200对基准因子IC影响
win_rows=[]
for tw in [160,180,200]:
    fwd=np.log(F["close"].shift(-20)/F["close"])
    ics=[]; start=tw; w=0
    while start+FORECAST_STEP<=len(F):
        tr=F.iloc[start-tw:start][[BASE_FACTOR]].copy()
        tr["tgt"]=fwd.iloc[start-tw:start]; tv=tr.dropna()
        if len(tv)>=30:
            ic,_=spearmanr(tv[BASE_FACTOR],tv["tgt"]); ics.append(ic)
        start+=ROLL_STEP; w+=1
    win_rows.append({"factor":BASE_FACTOR,"train_window":tw,"mean_ic":np.mean(ics),"n_win":len(ics)})
win_df=pd.DataFrame(win_rows); win_df.to_csv(os.path.join(DATA,"exp406b_window_perturb.csv"),index=False)

# ---------- 报告 ----------
rep=[]
rep.append("# exp406b 因子消融测试 + 稳健性扰动\n")
rep.append("> 任务: 第三步消融(单因子+叠加基准) + 第四步稳健性扰动")
rep.append("> 统一回测: 20日持有周期, 连续时序(逐日信号×20日前向收益/20折算, 净值累乘)")
rep.append("> 基准: 单独库存20日环比策略; 合成用简单线性等权, 无非线性模型")
rep.append("> **禁用HAM任何代码与变量**(已校验)\n")

rep.append("## 1. 消融测试(第三步)\n")
rep.append("| 组别 | 策略 | 有效样本 | 准确率 | 年化收益 | 夏普 | 最大回撤 |")
rep.append("|------|------|----------|--------|----------|------|----------|")
for _,r in abl_df.iterrows():
    acc=f"{r['accuracy']*100:.1f}%" if not pd.isna(r['accuracy']) else "NA"
    ann=f"{r['ann_ret']*100:.1f}%" if not pd.isna(r['ann_ret']) else "NA"
    sh=f"{r['sharpe']:.2f}" if not pd.isna(r['sharpe']) else "NA"
    mdd=f"{r['max_dd']*100:.1f}%" if not pd.isna(r['max_dd']) else "NA"
    rep.append(f"| {r['组别']} | {r['策略']} | {r['n_valid']} | {acc} | {ann} | {sh} | {mdd} |")
rep.append("")

# 增量判断
base_row=abl_df[abl_df["组别"]=="基准:库存20日环比"].iloc[0]
combo_row=abl_df[abl_df["组别"]=="叠加:新因子+基准"].iloc[0]
d_acc=combo_row["accuracy"]-base_row["accuracy"]
d_ann=combo_row["ann_ret"]-base_row["ann_ret"]
rep.append("### 1.1 增量阿尔法判断\n")
rep.append(f"- 基准(库存20日环比)单独: 准确率{base_row['accuracy']*100:.1f}%, 年化{base_row['ann_ret']*100:.1f}%, 夏普{base_row['sharpe']:.2f}")
rep.append(f"- 叠加(新因子+基准): 准确率{combo_row['accuracy']*100:.1f}%, 年化{combo_row['ann_ret']*100:.1f}%, 夏普{combo_row['sharpe']:.2f}")
rep.append(f"- **增量**: 准确率{d_acc*100:+.1f}pp, 年化{d_ann*100:+.1f}pp")
if d_acc>=0.02 and d_ann>0:
    rep.append("- **结论: 新因子叠加带来正向增量阿尔法**")
elif d_acc>=-0.02:
    rep.append("- **结论: 新因子叠加无显著增量**(增益<2pp或负), 库存20日环比已捕捉主要信息")
else:
    rep.append("- **结论: 新因子叠加反而降低效果**, 不建议纳入")

rep.append("\n### 1.2 单独新因子表现\n")
for fc in NEW_FACTORS:
    row=abl_df[abl_df["组别"]==f"单因子:{FEAT_CN[fc]}"].iloc[0]
    rep.append(f"- {FEAT_CN[fc]}: 准确率{row['accuracy']*100:.1f}%, 年化{row['ann_ret']*100:.1f}%, 夏普{row['sharpe']:.2f}")
    if fc=="f_cell_pct20":
        rep.append(f"  - ⚠️ 仅35样本, 置信低, 结论仅供参考")

rep.append("\n## 2. 稳健性扰动(第四步)\n")
rep.append("### 2.1 预测周期扰动(T+5/T+10/T+20)\n")
rep.append("| 因子 | T+5 | T+10 | T+20 | 是否稳定 |")
rep.append("|------|-----|------|------|---------|")
for fc in [BASE_FACTOR]+NEW_FACTORS:
    sub=robust_df[robust_df["factor"]==fc]
    ics=[sub[sub["horizon"]==f"T+{h}"]["mean_ic"].values[0] for h in [5,10,20]]
    stable=all(np.abs(ic)>=0.05 and np.sign(ic)==np.sign(ics[2]) for ic in ics if not pd.isna(ic))
    rep.append(f"| {FEAT_CN[fc]} | {ics[0]:+.4f} | {ics[1]:+.4f} | {ics[2]:+.4f} | {'✅稳定' if stable else '❌不稳定'} |")
rep.append("")
rep.append("### 2.2 训练窗口扰动(160/180/200, 基准因子)\n")
rep.append("| 训练窗口 | 平均IC | 窗口数 |")
rep.append("|----------|--------|--------|")
for _,r in win_df.iterrows():
    rep.append(f"| {r['train_window']} | {r['mean_ic']:+.4f} | {r['n_win']} |")

rep.append("\n## 3. 局限性声明\n")
rep.append(f"- 样本量{len(F)}行, 约17滚动窗口, 统计效力有限")
rep.append("- f_cell_pct20仅35样本, 置信低")
rep.append("- 年化/夏普基于连续时序回测(20日收益折算逐日), 为近似")
rep.append("- 合成因子用简单线性等权, 未做非线性优化(小样本避免过拟合)")
rep.append("- **全部为历史统计事实, 不含涨跌预测**")

with open(os.path.join(REPS,"exp406b_ablation_robustness.md"),"w",encoding="utf-8") as f:
    f.write("\n".join(rep))

print("\n"+"="*55); print("exp406b 完成"); print("="*55)
print(f"报告: {REPS}/exp406b_ablation_robustness.md")
print(f"增量: 准确率{d_acc*100:+.1f}pp, 年化{d_ann*100:+.1f}pp")
