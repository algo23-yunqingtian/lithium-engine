"""
exp413: exp412 策略稳健性校验 — 全模块运行脚本

模块1 风控参数扰动扫描 (M2/M3局部小范围)
模块2 交易成本敏感性扫描 (0~0.4% 单边)
模块3 情景压力拆分 (单边上涨/下跌/震荡)
模块4 HAM子策略尾部风险统计
模块5 完整交易判定逻辑溯源 (伪代码, 写入报告)
模块6 未来函数/前视偏差排查 (时序校验)

底层信号引擎完全复用exp409/410/411, 零改动。
"""
import os
import json
import numpy as np
import pandas as pd
import sys
from itertools import product

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # model_ham
sys.path.insert(0, os.path.join(BASE, "exp413_robustness"))
import exp413_engine as ENG

EXP = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(EXP, "data")
os.makedirs(DATA, exist_ok=True)

COST_BASE = ENG.COST_DEFAULT
TRADING_DAYS_PER_YEAR = ENG.TRADING_DAYS_PER_YEAR
print("=" * 72)
print("exp413: exp412 策略稳健性校验 (6模块)")
print("  区间 %s ~ %s | 基准成本 %.2f%%/边" % (ENG.START.date(), ENG.END.date(), COST_BASE*100))
print("=" * 72)

h, f, idx, price = ENG.load_data()
n = len(idx)
print("交易日 n=%d" % n)

# ============================================================
# 模块1: 风控参数扰动扫描
# ============================================================
print("\n" + "=" * 72)
print("模块1: 风控参数扰动扫描 (M2回撤 / M3波动)")
print("=" * 72)

# M2: warn 10/12/14%, hard 16/18/20%, recover 3/5/7% (固定 dd_mult warn=0.70 hard=0.30)
m2_grid = list(product([0.10, 0.12, 0.14],      # dd_warn
                       [0.16, 0.18, 0.20],      # dd_hard
                       [0.03, 0.05, 0.07]))     # dd_recover
# M3: 波动阈值 50/55/60%, 仓位压缩比例 40/50/60%
m3_grid = list(product([0.50, 0.55, 0.60],      # vol_upper
                       [0.40, 0.50, 0.60]))     # vol_scale

def calmar_ratio(m):
    return m["annual_return"] / abs(m["max_drawdown"]) if m["max_drawdown"] < 0 else 0

# 基准(当前最优)
base_pos, base_eq, base_nr, base_m, base_tr, base_diag = ENG.run_with_metrics(h, f, idx, price)
base_calmar = calmar_ratio(base_m)
print("基准V4: 年化%.3f 夏普%.3f 回撤%.3f Calmar%.3f" % (base_m["annual_return"], base_m["sharpe_full"], base_m["max_drawdown"], base_calmar))

# 扫描 M2 (M3固定V4最优)
rows_m2 = []
for dd_warn, dd_hard, dd_recover in m2_grid:
    _, eq, nr, m, tr, diag = ENG.run_with_metrics(
        h, f, idx, price,
        dd_warn=dd_warn, dd_hard=dd_hard, dd_recover=dd_recover)
    rows_m2.append({
        "param_group": "M2_dd_control",
        "dd_recover": dd_recover, "dd_warn": dd_warn, "dd_hard": dd_hard,
        "vol_upper": ENG.VOL_UPPER_DEFAULT, "vol_scale": ENG.VOL_SCALE_DEFAULT,
        "annual_return": m["annual_return"], "sharpe_full": m["sharpe_full"],
        "max_drawdown": m["max_drawdown"], "calmar": calmar_ratio(m),
        "n_trades": m["n_trades"],
        "dd_triggered_days": int((diag["dd_control"] < 1.0).sum()),
    })
m2_df = pd.DataFrame(rows_m2)

# 扫描 M3 (M2固定V4最优)
rows_m3 = []
for vol_upper, vol_scale in m3_grid:
    _, eq, nr, m, tr, diag = ENG.run_with_metrics(
        h, f, idx, price,
        vol_upper=vol_upper, vol_scale=vol_scale)
    rows_m3.append({
        "param_group": "M3_vol_control",
        "dd_recover": ENG.DD_RECOVER_DEFAULT, "dd_warn": ENG.DD_WARN_DEFAULT, "dd_hard": ENG.DD_HARD_DEFAULT,
        "vol_upper": vol_upper, "vol_scale": vol_scale,
        "annual_return": m["annual_return"], "sharpe_full": m["sharpe_full"],
        "max_drawdown": m["max_drawdown"], "calmar": calmar_ratio(m),
        "n_trades": m["n_trades"],
        "vol_triggered_days": int((diag["vol_control"] < 1.0).sum()),
    })
m3_df = pd.DataFrame(rows_m3)

scan_df = pd.concat([m2_df, m3_df], ignore_index=True)
scan_df.to_csv(os.path.join(DATA, "exp413_param_scan.csv"), index=False)

# 稳健性判定: 单点过拟合 = 最优参数附近扰动后指标断崖恶化
def assess_scan(df, name):
    best_calmar = df["calmar"].max()
    best_ann = df.loc[df["calmar"].idxmax(), "annual_return"]
    calmar_min = df["calmar"].min()
    calmar_mean = df["calmar"].mean()
    ann_min = df["annual_return"].min()
    sharpe_min = df["sharpe_full"].min()
    dd_max = df["max_drawdown"].max()  # 最浅回撤(数值最大, 接近0)
    # 断崖判定: 最差组Calmar < 最优组50% 视为断崖
    cliff = calmar_min < 0.5 * best_calmar
    print("\n[%s 扫描统计]" % name)
    print("  Calmar: min=%.3f mean=%.3f max=%.3f" % (calmar_min, calmar_mean, best_calmar))
    print("  年化:   min=%.3f max=%.3f" % (ann_min, best_ann))
    print("  夏普:   min=%.3f" % sharpe_min)
    print("  回撤:   最浅=%.3f 最深=%.3f" % (dd_max, df['max_drawdown'].min()))
    print("  断崖恶化(最差Calmar<最优50%%): %s" % ("是 ⚠️" if cliff else "否 ✓"))
    return {"best_calmar": best_calmar, "calmar_min": calmar_min, "calmar_mean": calmar_mean,
            "calmar_max": best_calmar, "ann_min": ann_min, "ann_max": best_ann,
            "sharpe_min": sharpe_min, "dd_shallowest": dd_max, "dd_deepest": df["max_drawdown"].min(),
            "cliff_overfit": bool(cliff), "n_groups": len(df)}

m2_stat = assess_scan(m2_df, "M2回撤控制")
m3_stat = assess_scan(m3_df, "M3波动控仓")

print("\n[M2扫描明细]")
print(m2_df.sort_values("calmar", ascending=False)[
    ["dd_recover","dd_warn","dd_hard","annual_return","sharpe_full","max_drawdown","calmar"]].to_string(index=False, float_format="%.3f"))
print("\n[M3扫描明细]")
print(m3_df.sort_values("calmar", ascending=False)[
    ["vol_upper","vol_scale","annual_return","sharpe_full","max_drawdown","calmar"]].to_string(index=False, float_format="%.3f"))

# ============================================================
# 模块2: 交易成本敏感性扫描
# ============================================================
print("\n" + "=" * 72)
print("模块2: 交易成本敏感性扫描 (单边 0~0.4%)")
print("=" * 72)

# 基准HAM单独子策略(用于观察HAM对滑点耐受度)
import exp409_ham_dynamic as E409
import exp410_fund_dynamic as E410
from exp411_combined import build_targets, price_series

h_target_all, f_target_all = build_targets()
idx_all = h_target_all.index
price_all = price_series(idx_all)
mask_all = (idx_all >= ENG.START) & (idx_all <= ENG.END)
idx_w = idx_all[mask_all]
h_w = h_target_all.reindex(idx_w).fillna(0.0)
f_w = f_target_all.reindex(idx_w).fillna(0.0)
price_w = price_all.reindex(idx_w)

cost_grid = [0.0, 0.001, 0.0015, 0.002, 0.003, 0.004]  # 0/0.1/0.15/0.2/0.3/0.4%
rows_cost = []
for c in cost_grid:
    # 组合策略(exp412 V4 参数)
    _, eq, nr, m, tr, diag = ENG.run_with_metrics(h, f, idx, price, cost=c)
    # HAM单独子策略
    ham_pos = h_w.values
    # 用引擎净值法重算HAM(独立成本)
    from exp411_combined import daily_equity as de_func
    ham_nr, ham_eq, ham_in = de_func(ham_pos, price_w)
    # 但de_func用了固定COST, 需手动重算HAM成本
    close_w = price_w.values
    r_price = np.zeros(n); r_price[1:] = (close_w[1:] - close_w[:-1])/close_w[:-1]
    dpos = np.zeros(n); dpos[1:] = np.abs(ham_pos[1:] - ham_pos[:-1]); dpos[0] = np.abs(ham_pos[0])
    ham_net = ham_pos * r_price - 2*c*dpos
    ham_eq2 = np.cumprod(1+ham_net)
    ham_ann = (ham_eq2[-1])**(TRADING_DAYS_PER_YEAR/n) - 1
    ham_sharpe = ham_net.mean()/ham_net.std()*np.sqrt(TRADING_DAYS_PER_YEAR) if ham_net.std()>0 else 0
    ham_peak = np.maximum.accumulate(ham_eq2)
    ham_dd = ((ham_eq2-ham_peak)/ham_peak).min()
    rows_cost.append({
        "cost_per_side_pct": c*100,
        "combo_annual": m["annual_return"], "combo_sharpe": m["sharpe_full"],
        "combo_maxdd": m["max_drawdown"], "combo_calmar": calmar_ratio(m),
        "ham_annual": ham_ann, "ham_sharpe": ham_sharpe, "ham_maxdd": ham_dd,
    })
cost_df = pd.DataFrame(rows_cost)
cost_df.to_csv(os.path.join(DATA, "exp413_cost_sensitivity.csv"), index=False)

# 失效阈值: 组合年化首次转负 / HAM年化首次转负
def find_threshold(df, col):
    for _, r in df.iterrows():
        if r[col] <= 0:
            return r["cost_per_side_pct"]
    return None
combo_fail = find_threshold(cost_df, "combo_annual")
ham_fail = find_threshold(cost_df, "ham_annual")
print("成本敏感性表:")
print(cost_df.to_string(index=False, float_format="%.4f"))
print("\n组合年化转负成本阈值: %s" % ("%.2f%%" % combo_fail if combo_fail else ">0.4%(未触达)"))
print("HAM年化转负成本阈值: %s" % ("%.2f%%" % ham_fail if ham_fail else ">0.4%(未触达)"))

# ============================================================
# 模块3: 情景压力拆分
# ============================================================
print("\n" + "=" * 72)
print("模块3: 情景压力拆分 (单边上涨/下跌/震荡)")
print("=" * 72)
from exp411_combined import market_regime, stage_metrics
regime = market_regime(price)
stage_base = stage_metrics(base_pos, price, idx, regime)
# 同时给基线exp411(无风控)对照
def run_exp411_pos(h, f, idx):
    hv = h.reindex(idx).fillna(0.0).values; fv = f.reindex(idx).fillna(0.0).values
    pos = np.zeros(len(idx))
    for t in range(len(idx)):
        if abs(hv[t])>1e-9 and abs(fv[t])>1e-9:
            if np.sign(hv[t])==np.sign(fv[t]): pos[t]=np.sign(hv[t])*min(abs(hv[t])+abs(fv[t]),1.0)
            else: pos[t]=np.clip(hv[t]+fv[t],-1,1)
        elif abs(hv[t])>1e-9: pos[t]=hv[t]
        elif abs(fv[t])>1e-9: pos[t]=fv[t]
    return pos
exp411_pos = run_exp411_pos(h, f, idx)
stage_411 = stage_metrics(exp411_pos, price, idx, regime)

# 交易次数按阶段
def trades_in_regime(trades, regime, idx):
    reg_map = dict(zip(idx, regime.values))
    cnt = {"uptrend":0,"downtrend":0,"range":0}
    for tr in trades:
        ed = pd.Timestamp(tr["entry_date"])
        if ed in reg_map:
            cnt[reg_map[ed]] = cnt.get(reg_map[ed],0)+1
    return cnt
tr_412 = trades_in_regime(base_tr, regime, idx)
tr_411 = trades_in_regime(ENG.extract_trades(exp411_pos, price, idx), regime, idx)

rows_regime = []
for st in ["uptrend","downtrend","range"]:
    b = stage_base.get(st,{}); b411 = stage_411.get(st,{})
    rows_regime.append({
        "regime": st, "n_days": b.get("n_days",0),
        "exp412_ann": b.get("ann_return",0), "exp412_sharpe": b.get("sharpe",0),
        "exp412_maxdd": b.get("max_drawdown",0), "exp412_trades": tr_412.get(st,0),
        "exp411_ann": b411.get("ann_return",0), "exp411_sharpe": b411.get("sharpe",0),
        "exp411_maxdd": b411.get("max_drawdown",0), "exp411_trades": tr_411.get(st,0),
    })
regime_df = pd.DataFrame(rows_regime)
regime_df.to_csv(os.path.join(DATA, "exp413_regime_stress.csv"), index=False)
print(regime_df.to_string(index=False, float_format="%.4f"))

# ============================================================
# 模块4: HAM子策略尾部风险统计
# ============================================================
print("\n" + "=" * 72)
print("模块4: HAM子策略尾部风险统计")
print("=" * 72)
ham_trades = ENG.extract_trades(h_w.values, price_w, idx_w)
nets = np.array([tr["net_ret"] for tr in ham_trades])
wins = nets[nets>0]; losses = nets[nets<=0]
ham_tail = {
    "n_ham_trades": len(nets),
    "win_rate": float((nets>0).mean()),
    "avg_win": float(wins.mean()) if len(wins) else float("nan"),
    "avg_loss": float(abs(losses.mean())) if len(losses) else float("nan"),
    "pl_ratio": float(wins.mean()/abs(losses.mean())) if len(losses) and losses.mean()!=0 else 0,
    "max_single_win": float(nets.max()),
    "max_single_loss": float(nets.min()),
    "p5_loss": float(np.percentile(nets,5)),
    "p10_loss": float(np.percentile(nets,10)),
    "p95_win": float(np.percentile(nets,95)),
    "std_net": float(nets.std()),
}
# 全局账户风控能否约束HAM单笔极端尾部: 比较HAM单笔最大亏损 vs 组合账户M2硬止损回撤
print("HAM单笔盈亏分布:")
for k,v in ham_tail.items():
    print("  %s: %s" % (k, "%.4f"%v if isinstance(v,float) else v))
print("对比: 组合账户最大回撤 %.3f (M2硬止损回撤阈值0.18)" % base_m["max_drawdown"])
ham_tail_df = pd.DataFrame([ham_tail])
ham_tail_df.to_csv(os.path.join(DATA, "exp413_ham_tail_risk.csv"), index=False)

# 保存净值/交易日志
nav = pd.DataFrame({"close": price_w.values, "exp412_equity": base_eq,
                    "exp412_pos": base_pos, "dd_control": base_diag["dd_control"],
                    "vol_control": base_diag["vol_control"],
                    "signal_state": base_diag["signal_state"], "regime": regime.values})
nav.index = idx_w
nav.to_csv(os.path.join(DATA, "exp413_daily_equity.csv"))
pd.DataFrame(base_tr).to_csv(os.path.join(DATA, "exp413_risk_trades.csv"), index=False)
pd.DataFrame(ham_trades).to_csv(os.path.join(DATA, "exp413_ham_trades.csv"), index=False)

# 汇总JSON
summary = {
    "baseline_v4": {"annual": base_m["annual_return"], "sharpe": base_m["sharpe_full"],
                    "maxdd": base_m["max_drawdown"], "calmar": base_calmar, "n_trades": m["n_trades"]},
    "module1_scan": {"M2": m2_stat, "M3": m3_stat},
    "module2_cost": {"cost_grid_pct": [c*100 for c in cost_grid],
                     "combo_fail_cost_pct": combo_fail, "ham_fail_cost_pct": ham_fail},
    "module4_ham_tail": ham_tail,
}
with open(os.path.join(DATA, "exp413_summary.json"),"w") as fp:
    json.dump(summary, fp, indent=2, default=str, ensure_ascii=False)

print("\n✓ 模块1-4 产物已保存到 %s/" % DATA)
print("  exp413_param_scan.csv / exp413_cost_sensitivity.csv / exp413_regime_stress.csv")
print("  exp413_ham_tail_risk.csv / exp413_daily_equity.csv / exp413_risk_trades.csv / exp413_ham_trades.csv")
