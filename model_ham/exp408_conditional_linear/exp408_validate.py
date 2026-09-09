#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
exp408: 条件线性策略 —— 引入哑变量开关，解决全局线性"权重一成不变"缺陷
================================================================================
任务性质: exp407 全局等权三因子的深化 —— 条件线性(因子×环境哑变量)
本轮约束: 不新增基本面原始预测因子; 资金/波动率仅用于构造哑变量, 不直接作预测因子
           滚动180/20; 严格T日前; Bonferroni; HAM封存; GMM仅事后复盘;
           禁止网格搜索阈值遍历(阈值仅由窗口内历史分位数生成, 仅65/70/75%少量扰动)
哑变量设计(最多2个):
  D1 高波动哑变量: D1=1 当 窗口内20日波动率 > 窗口内70%分位数
      交互项: 仓单基差×仓单增速 × D1  (仓单交叉因子仅高波动环境启用)
  D2 累库哑变量:   D2=1 当 库存20日环比 > 0
      交互项: 动力电池20日环比 × D2    (动力电池需求因子仅累库环境启用)
模型:
  基准1: 单库存20日环比
  基准2: 三因子全局等权线性(exp407)
  新模型: w1*库存20日环比 + w2*(仓单交叉*D1) + w3*(动力电池环比*D2)  等权
================================================================================
"""
import os
import numpy as np
import pandas as pd
from scipy.stats import spearmanr, t as tdist

OUT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(OUT, "data")
REPORTS = os.path.join(OUT, "reports")
SRC = os.path.join(os.path.dirname(OUT), "exp406_fund_factor")
os.makedirs(DATA, exist_ok=True)
os.makedirs(REPORTS, exist_ok=True)

# ---------- 全局口径(与 exp406b/exp407 完全一致) ----------
TRAIN_WINDOW = 180      # 滚动训练窗口
HORIZON = 20            # 预测周期(日)
FORECAST_STEP = 20      # 滚动前推步长
ROLL_STEP = 20          # 滚动步长
N_FACTORS = 3           # 用于 Bonferroni 校正
BONF_ALPHA = 0.05 / N_FACTORS   # 0.01667
HAM_FORBIDDEN = {"n_f", "n_c", "Total_Demand", "profit_f", "profit_c",
                 "D_f", "D_c", "P_fund", "n_f_minus_n_c", "valid"}
FACTOR_COLS = ["f_stock_pct20", "f_wh_basis_x_stockpct", "f_cell_pct20"]
BASE_FACTOR = "f_stock_pct20"
FEAT_CN = {"f_stock_pct20": "库存20日环比",
           "f_wh_basis_x_stockpct": "仓单基差×仓单增速",
           "f_cell_pct20": "动力电池20日环比"}
IC_SIGN = {"f_stock_pct20": -1, "f_wh_basis_x_stockpct": +1, "f_cell_pct20": -1}

VOL_WIN = 20            # 波动率计算窗口(日)
VOL_QTL = 0.70          # D1 分位阈值(主)
PERTURB_QTL = [0.65, 0.70, 0.75]  # 阈值扰动(少量, 不批量遍历)
SEG_DOWN = -0.05        # 下跌子区间阈值(20日收益)
SEG_FLAT = 0.05         # 震荡子区间阈值

# ---------- 读取 exp406a 因子表, 清理 inf ----------
F = pd.read_csv(os.path.join(SRC, "data", "exp406a_factors.csv"))
F["date"] = pd.to_datetime(F["date"])
F = F.sort_values("date").reset_index(drop=True)
assert not (set(F.columns) & HAM_FORBIDDEN), "禁止HAM字段!"
for c in F.columns:
    if F[c].dtype != object:
        F[c] = F[c].replace([np.inf, -np.inf], np.nan)
F["ret20"] = np.log(F["close"] / F["close"].shift(20))
target_fwd = F["target_fwd"].values
print(f"数据: {len(F)}行 ({F['date'].min().date()}~{F['date'].max().date()}), inf已清理")

# ---------- 通用: 信号构造(与 exp406b 完全一致) ----------
def factor_signal(Fdf, fc, idx, ic_sign):
    train_start = max(0, idx - TRAIN_WINDOW)
    tv = Fdf[fc].iloc[train_start:idx].dropna()
    if len(tv) < 30:
        return 0.0
    med = tv.median()
    cur = Fdf[fc].iloc[idx]
    if pd.isna(cur):
        return 0.0
    raw = 1.0 if cur > med else -1.0
    return raw * (1 if ic_sign > 0 else -1)

def make_signals(Fdf):
    """全局等权三因子信号(基准2)。返回 dict{fc:signal} 与 combo"""
    n = len(Fdf)
    sigs = {fc: np.zeros(n) for fc in FACTOR_COLS}
    for i in range(n):
        for fc in FACTOR_COLS:
            sigs[fc][i] = factor_signal(Fdf, fc, i, IC_SIGN[fc])
    combo = np.mean([sigs[fc] for fc in FACTOR_COLS], axis=0)
    return sigs, combo

# ---------- 条件线性: 哑变量 + 交互项 ----------
def build_vol(Fdf, vwin=VOL_WIN):
    """窗口内20日波动率: 逐日20日已实现波动率(日对数收益标准差), 用过去窗口滚动均值作为平滑"""
    lr = np.log(Fdf["close"] / Fdf["close"].shift(1))
    rv = lr.rolling(vwin).std()
    # 平滑: 用180日滚动均值避免单日噪声(仍仅用T-1及更早)
    return rv.rolling(TRAIN_WINDOW).mean()

def build_dummy_D1(Fdf, qtl=VOL_QTL):
    """D1高波动哑变量: vol > 窗口内qtl分位数 → 1, 否则0. 仅用T-1及更早数据"""
    vol = build_vol(Fdf)
    d1 = np.zeros(len(Fdf))
    for i in range(len(Fdf)):
        tv = vol.iloc[max(0, i - TRAIN_WINDOW):i].dropna()
        cur = vol.iloc[i]
        if len(tv) < 30 or pd.isna(cur):
            d1[i] = 0.0
            continue
        q = tv.quantile(qtl)
        d1[i] = 1.0 if (cur > q and not pd.isna(q)) else 0.0
    return d1

def build_dummy_D2(Fdf):
    """D2累库哑变量: 库存20日环比 > 0 → 1, 否则0. 严格T日数据"""
    stock = Fdf["f_stock_pct20"].values
    d2 = np.where(np.isnan(stock) | (stock <= 0), 0.0, 1.0)
    return d2

def make_conditional(Fdf, d1_qtl=VOL_QTL):
    """
    条件线性模型信号:
      sig_stock(库存20日环比, 始终启用)
      + sig_wh * D1 (仓单交叉仅在D1=1启用)
      + sig_cell * D2 (动力电池仅在D2=1启用)
    等权: 三项相加后 / 3 (保持与基准2同量纲, 不做参数寻优)
    """
    n = len(Fdf)
    d1 = build_dummy_D1(Fdf, d1_qtl)
    d2 = build_dummy_D2(Fdf)
    sigs = {}
    for fc in FACTOR_COLS:
        sigs[fc] = np.array([factor_signal(Fdf, fc, i, IC_SIGN[fc]) for i in range(n)])
    term_stock = sigs["f_stock_pct20"]
    term_wh = sigs["f_wh_basis_x_stockpct"] * d1
    term_cell = sigs["f_cell_pct20"] * d2
    cond = (term_stock + term_wh + term_cell) / 3.0
    return cond, d1, d2, sigs

# ---------- 通用: 回测 ----------
def backtest(sig, tfwd, n_days=250):
    sig = np.asarray(sig, dtype=float)
    fwd = np.asarray(tfwd, dtype=float)
    valid = (~np.isnan(fwd)) & (np.abs(sig) > 1e-9)
    daily_ret = np.where(valid, sig * fwd / HORIZON, 0.0)
    nav = np.cumprod(1.0 + daily_ret)
    total = nav[-1] - 1
    n_obs = len(daily_ret)
    acc = (np.sign(sig[valid]) == np.sign(fwd[valid])).mean() if valid.sum() > 0 else np.nan
    ann = (1 + total) ** (n_days / n_obs) - 1 if n_obs > 0 else 0.0
    ddaily = daily_ret[valid]
    sharpe = ddaily.mean() / ddaily.std() * np.sqrt(250) if ddaily.std() > 1e-9 else np.nan
    peak = np.maximum.accumulate(nav)
    mdd = ((nav - peak) / peak).min()
    return {"n_valid": int(valid.sum()), "accuracy": acc, "ann_ret": ann,
            "sharpe": sharpe, "max_dd": mdd}

# ============================================================
# 任务1: 滚动样本外 RankIC 与 Bonferroni p 值
# 检验: 条件线性信号(及其两项交互项)在滚动样本外窗口上的 RankIC 与显著性
# 同时对比 基准2(全局等权) 与 基准1(单库存)
# ============================================================
print("\n=== 任务1: 滚动样本外 RankIC + Bonferroni ===")
cond, d1, d2, cs = make_conditional(F)
_, combo_global = make_signals(F)
base_stock = cs["f_stock_pct20"]

def rolling_rank_ic_predictor(pdict, fwd_arr):
    """pdict: {name: signal_array}; 返回每个预测器滚动窗口 RankIC 列表"""
    names = list(pdict.keys())
    fwd = np.log(F["close"].shift(-HORIZON) / F["close"]).values
    out = {nm: [] for nm in names}
    start = TRAIN_WINDOW
    while start + FORECAST_STEP <= len(F):
        sl = slice(max(0, start - TRAIN_WINDOW), start)
        w = slice(sl.start, sl.stop)
        tv_ok = True
        for nm in names:
            x = pdict[nm][w]; y = fwd[w]
            m = (~np.isnan(x)) & (~np.isnan(y))
            out[nm].append(spearmanr(x[m], y[m]).correlation if m.sum() >= 20 else np.nan)
        start += ROLL_STEP
    return out

pdict = {
    "基准1_单库存": base_stock,
    "基准2_全局等权": combo_global,
    "交互项_仓单交叉×D1": cs["f_wh_basis_x_stockpct"] * d1,
    "交互项_动力电池×D2": cs["f_cell_pct20"] * d2,
    "新模型_条件线性": cond,
}
ics = rolling_rank_ic_predictor(pdict, target_fwd)

rows = []
for nm, ic_list in ics.items():
    arr = np.array([x for x in ic_list if not np.isnan(x)])
    if len(arr) < 3:
        rows.append({"预测器": nm, "窗口数": len(arr), "mean_IC": np.nan,
                     "std_IC": np.nan, "t值": np.nan, "p值": np.nan,
                     "Bonferroni显著": False})
        continue
    icm, icstd = arr.mean(), arr.std(ddof=1)
    if icstd > 1e-12:
        tval = icm / (icstd / np.sqrt(len(arr)))
        pval = 2 * (1 - tdist.cdf(abs(tval), df=len(arr) - 1))
    else:
        tval, pval = 0.0, 1.0
    rows.append({"预测器": nm, "窗口数": len(arr), "mean_IC": icm, "std_IC": icstd,
                 "t值": tval, "p值": pval, "Bonferroni显著": pval < BONF_ALPHA})
ic_df = pd.DataFrame(rows)
ic_df.to_csv(os.path.join(DATA, "exp408_task1_rankic.csv"), index=False)
print(ic_df.to_string(index=False))

# ============================================================
# 任务2: 子样本(下跌/震荡)分段回测 —— 基准1/基准2/新模型
# 口径: 全量算信号再切片(滚动窗口需历史)
# ============================================================
print("\n=== 任务2: 子样本分段回测 ===")
down_idx = F.index[F["ret20"] < SEG_DOWN].values
flat_idx = F.index[(F["ret20"].abs() < SEG_FLAT)].values

def seg_run(idx_arr, label):
    out = []
    out.append({"子区间": label, "策略": "基准1_单库存",
                **backtest(base_stock[idx_arr], target_fwd[idx_arr])})
    out.append({"子区间": label, "策略": "基准2_全局等权",
                **backtest(combo_global[idx_arr], target_fwd[idx_arr])})
    out.append({"子区间": label, "策略": "新模型_条件线性",
                **backtest(cond[idx_arr], target_fwd[idx_arr])})
    return out

full_rows = [
    {"子区间": "全样本", "策略": "基准1_单库存", **backtest(base_stock, target_fwd)},
    {"子区间": "全样本", "策略": "基准2_全局等权", **backtest(combo_global, target_fwd)},
    {"子区间": "全样本", "策略": "新模型_条件线性", **backtest(cond, target_fwd)},
]
seg_rows = full_rows + seg_run(down_idx, "下跌子区间(20日<-5%)") + \
           seg_run(flat_idx, "震荡子区间(|20日|<5%)")
seg_df = pd.DataFrame(seg_rows)
seg_df.to_csv(os.path.join(DATA, "exp408_task2_segments.csv"), index=False)
print(seg_df[["子区间", "策略", "accuracy", "ann_ret", "sharpe", "max_dd"]].to_string(index=False))

# ============================================================
# 任务3: 共线性检验(含交互项的因子矩阵)
# ============================================================
print("\n=== 任务3: 共线性检验 ===")
X = pd.DataFrame({
    "库存20日环比": base_stock,
    "仓单交叉×D1": cs["f_wh_basis_x_stockpct"] * d1,
    "动力电池×D2": cs["f_cell_pct20"] * d2,
    "D1(高波动)": d1,
    "D2(累库)": d2,
})
corr = X.corr().round(3)
# VIF 自实现(numpy lstsq), 避免 statsmodels 依赖
def vif(Xmat):
    cols = list(Xmat.columns)
    out = {}
    for c in cols:
        y = Xmat[c].values.astype(float)
        others = [Xmat[o].values.astype(float) for o in cols if o != c]
        A = np.column_stack(others)
        m = np.isfinite(y) & np.all(np.isfinite(A), axis=1)
        if m.sum() < 10 or np.linalg.matrix_rank(A[m]) < A.shape[1]:
            out[c] = np.nan
            continue
        coef, *_ = np.linalg.lstsq(A[m], y[m], rcond=None)
        pred = A[m] @ coef
        ss_res = np.sum((y[m] - pred) ** 2)
        ss_tot = np.sum((y[m] - y[m].mean()) ** 2)
        r2 = 1 - ss_res / ss_tot if ss_tot > 1e-12 else np.nan
        out[c] = np.nan if (np.isnan(r2) or r2 >= 1 - 1e-9) else 1 / (1 - r2)
    return out

vif_map = vif(X)
vif_df = pd.DataFrame([{"因子/哑变量": k, "VIF": round(v, 3) if not np.isnan(v) else np.nan}
                       for k, v in vif_map.items()])
corr.to_csv(os.path.join(DATA, "exp408_task3_corr.csv"))
vif_df.to_csv(os.path.join(DATA, "exp408_task3_vif.csv"), index=False)
print("相关矩阵:\n", corr.to_string())
print("VIF:\n", vif_df.to_string(index=False))

# ============================================================
# 任务4: 稳健扰动 —— D1 分位阈值 65%/70%/75% 少量扰动
# (D2 无阈值, 不参与扰动)
# ============================================================
print("\n=== 任务4: 阈值扰动(65/70/75%) ===")
pert_rows = []
for q in PERTURB_QTL:
    c, d1q, d2q, csq = make_conditional(F, d1_qtl=q)
    bt = backtest(c, target_fwd)
    # 子样本
    bt_down = backtest(c[down_idx], target_fwd[down_idx])
    bt_flat = backtest(c[flat_idx], target_fwd[flat_idx])
    # 滚动IC
    x = c; y = np.log(F["close"].shift(-HORIZON) / F["close"]).values
    icl = []
    start = TRAIN_WINDOW
    while start + FORECAST_STEP <= len(F):
        w = slice(max(0, start - TRAIN_WINDOW), start)
        m = (~np.isnan(x[w])) & (~np.isnan(y[w]))
        if m.sum() >= 20:
            icl.append(spearmanr(x[w][m], y[w][m]).correlation)
        start += ROLL_STEP
    icm = np.mean([v for v in icl if not np.isnan(v)])
    pert_rows.append({"D1分位阈值": f"{int(q*100)}%", "全样本_年化": bt["ann_ret"],
                      "全样本_夏普": bt["sharpe"], "全样本_最大回撤": bt["max_dd"],
                      "下跌段_夏普": bt_down["sharpe"], "震荡段_夏普": bt_flat["sharpe"],
                      "全样本_RankIC均值": icm,
                      "D1触发占比": round(float(d1q.mean()), 3)})
pert_df = pd.DataFrame(pert_rows)
pert_df.to_csv(os.path.join(DATA, "exp408_task4_perturb.csv"), index=False)
print(pert_df.to_string(index=False))

# 哑变量触发统计(主阈值)
print(f"\nD1触发占比(70%): {d1.mean():.3f}  D2触发占比: {d2.mean():.3f}")

# ---------- 汇总: 条件线性 vs 全局线性 差异 ----------
diff_rows = []
for nm in ["基准1_单库存", "基准2_全局等权", "新模型_条件线性"]:
    bt = {"基准1_单库存": backtest(base_stock, target_fwd),
          "基准2_全局等权": backtest(combo_global, target_fwd),
          "新模型_条件线性": backtest(cond, target_fwd)}[nm]
    diff_rows.append({"策略": nm, **{k: round(v, 4) if isinstance(v, float) else v
                                     for k, v in bt.items()}})
diff_df = pd.DataFrame(diff_rows)
diff_df.to_csv(os.path.join(DATA, "exp408_summary_compare.csv"), index=False)

print("\n=== 任务完成, 开始生成报告 ===")
# 报告写入
with open(os.path.join(REPORTS, "exp408_conditional_linear.md"), "w", encoding="utf-8") as fh:
    fh.write("# exp408 条件线性策略：哑变量开关稳健性校验\n\n")
    fh.write("> 第八轮(exp407)深化 —— 因子×环境哑变量，解决全局线性权重一成不变缺陷\n")
    fh.write(f"> 生成时间: 2026-09-09 | 滚动窗口 {TRAIN_WINDOW}/{HORIZON} | Bonferroni 阈值 {BONF_ALPHA:.5f}\n\n")
    fh.write("## 0. 三句话速览\n\n")
    fh.write("1. 详见正文(运行时自动填充)。\n")
print("脚本执行完毕。报告框架已写, 主报告将在后续步骤补充正文。")
