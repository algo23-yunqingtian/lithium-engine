"""
exp404b: 状态时序复盘 + 自动市场分析文本 + 可视化
====================================================
基于 exp404a 滚动状态标签(340行样本外):
  1. 最近90交易日状态 + 切换记录 + 平均持续天数/切换频率
  2. 自动生成市场分析文本(仅客观陈述, 禁止涨跌预测)
  3. 可视化: 行情曲线+状态色块 + 4状态雷达图
产出: reports/exp404b_state_review.md + .png + data/exp404b_switches.csv
硬性约束: 仅解析既有滚动状态, 无新GMM拟合; 禁止涨跌预测文本
"""
import os, sys, warnings
import numpy as np
import pandas as pd
from collections import Counter
warnings.filterwarnings("ignore")

BASE = "model_ham"
EXP = os.path.join(BASE, "exp404_cluster_analysis")
DATA = os.path.join(EXP, "data")
REPS = os.path.join(EXP, "reports")
FUND = ["warehouse_stock","basis_spot_main","spread_near1_near3","hv_20d","oi_main"]
FEAT_CN = {"warehouse_stock":"库存","basis_spot_main":"基差","spread_near1_near3":"近月1-近月3价差",
           "hv_20d":"波动率","oi_main":"持仓"}
STATE_COLOR = {0:"#2196F3", 1:"#4CAF50", 2:"#FF9800", 3:"#9C27B0"}
STATE_NAME = {0:"低持仓中性态", 1:"高库存弱价差态", 2:"高价差高波态", 3:"高波动高基差态"}

# ---------- 1. 读取滚动状态标签 ----------
st = pd.read_csv(os.path.join(DATA,"exp404a_state_labels_rolling.csv"), parse_dates=["date"])
st = st.sort_values("date").reset_index(drop=True)
# 特征统计表(用于状态画像)
stats_df = pd.read_csv(os.path.join(DATA,"exp404a_feature_stats.csv"))

# ---------- 2. 最近90交易日 ----------
recent = st.tail(90).copy()
n_recent = len(recent)

# 状态切换记录
switches = []
prev_state = recent["state_id"].iloc[0]
seg_start = recent["date"].iloc[0]
for i in range(1, n_recent):
    cur = recent["state_id"].iloc[i]
    d = recent["date"].iloc[i]
    if cur != prev_state:
        switches.append({"from":int(prev_state),"to":int(cur),
            "date":d,"prev_days":(d-seg_start).days})
        prev_state = cur
        seg_start = d
# 最后一段
if n_recent>0:
    switches.append({"from":int(prev_state),"to":int(prev_state),
        "date":recent["date"].iloc[-1],"prev_days":(recent["date"].iloc[-1]-seg_start).days})
switch_df = pd.DataFrame(switches)
switch_df.to_csv(os.path.join(DATA,"exp404b_switches.csv"), index=False)

# ---------- 3. 全样本状态持续性统计 ----------
# 段持续天数
all_switches = []
prev = st["state_id"].iloc[0]; seg_start = st["date"].iloc[0]
for i in range(1, len(st)):
    cur = st["state_id"].iloc[i]; d = st["date"].iloc[i]
    if cur != prev:
        all_switches.append({"from":int(prev),"to":int(cur),"duration_days":(d-seg_start).days})
        prev = cur; seg_start = d
all_switches.append({"from":int(prev),"to":int(prev),"duration_days":(st["date"].iloc[-1]-seg_start).days})
# 段长度(按交易日数)
segments = []
prev = st["state_id"].iloc[0]; cnt=1
for i in range(1,len(st)):
    if st["state_id"].iloc[i]==prev: cnt+=1
    else: segments.append({"state":int(prev),"length":cnt}); prev=st["state_id"].iloc[i]; cnt=1
segments.append({"state":int(prev),"length":cnt})
seg_df = pd.DataFrame(segments)
avg_dur = seg_df.groupby("state")["length"].mean().to_dict()
n_switch_total = len(st)-1 - sum(1 for i in range(1,len(st)) if st["state_id"].iloc[i]==st["state_id"].iloc[i-1])

# 切换频率: 每状态平均多久切一次
freq = {s: avg_dur.get(s,np.nan) for s in sorted(st["state_id"].unique())}

# ---------- 4. 历史转移矩阵(全样本) ----------
trans = Counter()
for s in all_switches:
    if s["from"]!=s["to"]:
        trans[(s["from"],s["to"])] += 1
# 各状态转出分布
from_ct = Counter()
for s in all_switches:
    if s["from"]!=s["to"]: from_ct[s["from"]]+=1
transition = {}
for s in sorted(st["state_id"].unique()):
    tot = from_ct[s]
    if tot>0:
        outs = {b: trans[(s,b)]/tot for (a,b) in trans if a==s}
        transition[s] = sorted(outs.items(), key=lambda x:x[1], reverse=True)

# ---------- 5. 当前状态(最近一日) ----------
cur_state = int(st["state_id"].iloc[-1])
cur_date = st["date"].iloc[-1]
cur_row = stats_df[stats_df["state"]==cur_state].iloc[0]

# ---------- 6. 自动市场分析文本(仅客观陈述) ----------
def fmt_num(v, dec=1):
    if v>=1000: return f"{v:,.0f}"
    return f"{v:.{dec}f}"

text=[]
text.append("# exp404b 市场状态自动复盘文本\n")
text.append(f"> 生成日期: {cur_date.date()} | 基于滚动窗口K=4 GMM状态标签")
text.append(f"> ⚠️ 本复盘仅客观陈述市场状态、指标特征与历史统计事实, **不含涨跌预测或价格预判**\n")

# ① 当前状态
text.append("## ① 当前市场状态\n")
text.append(f"截至 **{cur_date.date()}**, 碳酸锂市场处于 **State {cur_state}（{STATE_NAME[cur_state]}）**。")
text.append(f"该状态在近期90个交易日中出现 {int((recent['state_id']==cur_state).sum())} 天, 占比 {(recent['state_id']==cur_state).mean()*100:.0f}%。\n")

# ② 当前状态典型特征
text.append("## ② 当前状态典型特征\n")
text.append(f"**State {cur_state}** 的指标画像(滚动样本内统计):\n")
text.append(f"- **库存水平**: 均值 {cur_row['warehouse_stock_mean']:.0f}, 区间 {cur_row['warehouse_stock_p25']:.0f}~{cur_row['warehouse_stock_p75']:.0f}, "
            f"相对全体{'偏高' if cur_row['warehouse_stock_z_mean']>0.2 else '偏低' if cur_row['warehouse_stock_z_mean']<-0.2 else '居中'}(z={cur_row['warehouse_stock_z_mean']:+.2f})")
text.append(f"- **基差(basis_spot_main)**: 均值 {cur_row['basis_spot_main_mean']:.0f}, "
            f"{'负基差(现货贴水期货/近月偏弱)' if cur_row['basis_spot_main_mean']<0 else '正基差(现货升水期货/近月偏强)'}(z={cur_row['basis_spot_main_z_mean']:+.2f})")
text.append(f"- **跨期价差结构(spread_near1_near3)**: 均值 {cur_row['spread_near1_near3_mean']:.0f}, "
            f"{'正向结构(近月强)' if cur_row['spread_near1_near3_mean']>0 else '反向结构(近月弱)' if cur_row['spread_near1_near3_mean']<0 else '近平'}(z={cur_row['spread_near1_near3_z_mean']:+.2f})")
text.append(f"- **波动率(hv_20d)**: 均值 {cur_row['hv_20d_mean']:.3f}, "
            f"{'偏高' if cur_row['hv_20d_z_mean']>0.2 else '偏低' if cur_row['hv_20d_z_mean']<-0.2 else '居中'}(z={cur_row['hv_20d_z_mean']:+.2f})")
text.append(f"- **持仓(oi_main)**: 均值 {cur_row['oi_main_mean']:.0f}, "
            f"{'偏高' if cur_row['oi_main_z_mean']>0.2 else '偏低' if cur_row['oi_main_z_mean']<-0.2 else '居中'}(z={cur_row['oi_main_z_mean']:+.2f})\n")

# ③ 近三个月状态演变路径
text.append("## ③ 近三个月状态演变路径\n")
# 把recent分段
path=[]; prev=recent["state_id"].iloc[0]; path=[(int(prev),recent["date"].iloc[0].date())]
for i in range(1,n_recent):
    if recent["state_id"].iloc[i]!=prev:
        path.append((int(recent["state_id"].iloc[i]), recent["date"].iloc[i].date()))
        prev=recent["state_id"].iloc[i]
text.append(f"近{n_recent}个交易日共经历 {len(path)} 个状态段:")
text.append("")
for j,(s,d) in enumerate(path):
    text.append(f"  {j+1}. **{d}** 起 → State {s}（{STATE_NAME[s]}）")
text.append("")
text.append(f"演变主线: {' → '.join([f'S{p[0]}' for p in path])}\n")

# ④ 历史参考
text.append("## ④ 历史参考(全滚动样本统计)\n")
text.append("### 各状态平均持续天数\n")
text.append("| State | 平均持续(交易日) | 段数 |")
text.append("|-------|-----------------|------|")
for s in sorted(st["state_id"].unique()):
    sub=seg_df[seg_df["state"]==s]
    text.append(f"| {s}（{STATE_NAME[s]}） | {avg_dur.get(s,0):.1f} | {len(sub)} |")
text.append(f"\n- 全样本状态切换总次数: {n_switch_total}")
text.append(f"- 状态切换频率(平均): 每 {(len(st)-1)/max(1,n_switch_total):.1f} 个交易日切换一次\n")
text.append(f"### State {cur_state} 的历史转出方向\n")
if transition.get(cur_state):
    for to_state, prob in transition[cur_state]:
        text.append(f"- 切换至 State {to_state}（{STATE_NAME[to_state]}）: {prob*100:.1f}%")
else:
    text.append(f"- State {cur_state} 在样本末, 无后续切换记录")
text.append("")
text.append("### 各状态历史转出全景\n")
text.append("| 起始状态 | 最高频去向 | 占比 |")
text.append("|---------|-----------|------|")
for s in sorted(transition.keys()):
    if transition[s]:
        to,p=transition[s][0]
        text.append(f"| State {s} | State {to}（{STATE_NAME[to]}） | {p*100:.1f}% |")

# 客观声明
text.append("\n## 客观性声明\n")
text.append("- 本文本仅基于 GMM 聚类的指标统计与历史转移频率, 描述市场**当前所处阶段**与**历史平均规律**")
text.append("- **不包含任何涨跌预测、价格预判或投资建议**")
text.append("- 历史转移频率描述的是**统计上的高频去向**, 非对未来的确定性判断")
text.append("- 状态标签为滚动窗口样本外预测, 每窗口仅用窗口内历史fit GMM, 无全量拟合")

with open(os.path.join(REPS,"exp404b_state_review.md"),"w",encoding="utf-8") as f:
    f.write("\n".join(text))

# ---------- 7. 可视化 ----------
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.colors import ListedColormap
plt.rcParams["font.sans-serif"]=["WenQuanYi Zen Hei","DejaVu Sans"]
plt.rcParams["axes.unicode_minus"]=False

fig=plt.figure(figsize=(18,14))
gs=fig.add_gridspec(2,2,width_ratios=[2,1],height_ratios=[1,1])

# 图1: 行情曲线+状态色块(按连续状态段铺色)
ax=fig.add_subplot(gs[0,:])
# 先确定每个连续状态段的索引范围
idx=0
segs=[]
while idx < len(st):
    s=st["state_id"].iloc[idx]; j=idx
    while j+1 < len(st) and st["state_id"].iloc[j+1]==s: j+=1
    segs.append((s,idx,j)); idx=j+1
for s,i0,i1 in segs:
    ax.axvspan(i0, i1, color=STATE_COLOR[s], alpha=0.12)
    # 段中央标状态号(仅较宽段)
    if i1-i0>=5:
        ax.text((i0+i1)/2, st["close"].max()*0.99, f"S{s}", ha="center", va="top",
                fontsize=8, color=STATE_COLOR[s], fontweight="bold")
ax.plot(range(len(st)), st["close"], color="black", lw=1.2, label="收盘价", zorder=5)
step=max(1,len(st)//12)
ax.set_xticks(range(0,len(st),step))
ax.set_xticklabels([d.strftime("%Y-%m") for d in st["date"][::step]], rotation=45, ha="right")
ax.set_title(f"碳酸锂行情 + 聚类状态色块 (滚动K=4, {len(st)}日, 17个滚动窗口样本外)", fontsize=14)
ax.set_ylabel("收盘价")
handles=[mpatches.Patch(color=STATE_COLOR[s], alpha=0.3, label=f"State {s} {STATE_NAME[s]}") for s in sorted(st["state_id"].unique())]
handles.append(plt.Line2D([0],[0],color="black",lw=1.2,label="收盘价"))
ax.legend(handles=handles, loc="upper left", fontsize=9, ncol=3, framealpha=0.9)
ax.grid(alpha=0.2)

# 图2: 雷达图
ax2=fig.add_subplot(gs[1,1],polar=True)
# 用z均值归一化到雷达
labels=[FEAT_CN[c] for c in FUND]
angle=np.linspace(0,2*np.pi,len(FUND),endpoint=False).tolist()
angle+=angle[:1]
for sid in sorted(st["state_id"].unique()):
    r=stats_df[stats_df["state"]==sid].iloc[0]
    vals=[r[f"{c}_z_mean"] for c in FUND]
    vals+=vals[:1]
    ax2.plot(angle, vals, label=f"State {sid}", color=STATE_COLOR[sid], lw=2)
    ax2.fill(angle, vals, color=STATE_COLOR[sid], alpha=0.1)
ax2.set_xticks(angle[:-1]); ax2.set_xticklabels(labels, fontsize=9)
ax2.set_title("4状态指标对比雷达图(z标准化)", fontsize=12, pad=20)
ax2.legend(loc="upper right", bbox_to_anchor=(1.3,1.1), fontsize=8)
ax2.grid(alpha=0.3)

# 图3: 最近90日状态时序(小图)
ax3=fig.add_subplot(gs[1,0])
for sid in sorted(recent["state_id"].unique()):
    mask=(recent["state_id"]==sid).values
    ax3.fill_between(range(n_recent), 0, 1, where=mask, alpha=0.5, color=STATE_COLOR[sid],
        step="mid")
step3=max(1,n_recent//9)
ax3.set_xticks(range(0,n_recent,step3))
ax3.set_xticklabels([d.strftime("%m-%d") for d in recent["date"][::step3]], rotation=45, ha="right", fontsize=8)
ax3.set_yticks([])
ax3.set_title(f"最近{n_recent}交易日状态时序", fontsize=11)
ax3.grid(alpha=0.2, axis="x")

plt.tight_layout()
plot_path=os.path.join(REPS,"exp404b_state_review.png")
plt.savefig(plot_path, dpi=110, bbox_inches="tight")
plt.close()

print("="*55)
print("exp404b 完成")
print("="*55)
print(f"当前状态: State {cur_state}({STATE_NAME[cur_state]}), 截至{cur_date.date()}")
print(f"近{n_recent}日状态段数: {len(path)}")
print(f"状态切换总次数: {n_switch_total}")
print("各状态平均持续(交易日):", {k:round(v,1) for k,v in avg_dur.items()})
print(f"报告: {REPS}/exp404b_state_review.md")
print(f"图: {plot_path}")
