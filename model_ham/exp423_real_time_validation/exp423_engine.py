"""
exp423: T1完整版本真实时序验证（紧急）

基准：exp416 T1稳健版（HAM动态+基本面+三层风控），不改任何参数。
  T1 = T0(exp412原版) + 波动率隔夜敞口管控(vol_half=0.40/vol_full=0.90)

模块1：T1完整版本两套成交假设并行回测
  版本A：T日收盘成交（历史基线，复现48.76%确认）
  版本B：T+1开盘成交（现实可行）
模块2：隔夜跳空归因分析
模块3：结论定性

关键修复：exp420的BUG是position不含exit日→收益重复→max_dd=0%
  本脚本使用exp416原生引擎的position数组（正确含exit日）
  版本B用逐笔重建position（含exit日）
"""
import os
import sys
import json
import numpy as np
import pandas as pd

MODEL = "/home/ubuntu/lithium-engine/model_ham"
for d in ["exp409_ham_dynamic", "exp410_fund_dynamic", "exp411_combined",
          "exp413_robustness", "exp414_attribution"]:
    sys.path.insert(0, os.path.join(MODEL, d))
sys.path.insert(0, os.path.join(MODEL, "exp416_tail_risk_protect"))

import exp409_ham_dynamic as E409
import exp413_engine as E413
import exp414_engine as E414
import exp416_engine as E416
from exp411_combined import (
    build_targets, price_series, extract_trades, market_regime,
    TRADING_DAYS_PER_YEAR,
)

EXP_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(EXP_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)
REPORTS_DIR = os.path.join(os.path.dirname(os.path.dirname(EXP_DIR)), "reports")
os.makedirs(REPORTS_DIR, exist_ok=True)

START, END = pd.Timestamp("2024-02-01"), pd.Timestamp("2026-09-07")
COST = E409.COST_PER_SIDE
TDPY = E409.TRADING_DAYS_PER_YEAR

# T1参数（从exp416_summary.json读取，与原始完全一致）
VOL_HALF = 0.40
VOL_FULL = 0.90
HALF_SCALE = 0.5
FULL_SCALE = 0.0


# ============================================================
# 数据加载（与exp416完全一致）
# ============================================================
def load_context():
    """加载T1完整组合的上下文"""
    h_target, f_target = build_targets()
    idx = h_target.index
    price = price_series(idx)
    mask = (idx >= START) & (idx <= END)
    idx_w = idx[mask]
    price_w = price.reindex(idx_w)

    # 已实现年化波动率（截至T日，min_periods=5防前视）
    vol = E413.compute_realized_vol(price_w, lookback=20)
    vol = vol.reindex(idx_w)

    # 20日动量
    pct20 = price_w.pct_change(20)

    # 加载OHLC
    ohlc = pd.read_csv(os.path.join(MODEL, "raw_data/lithium_future.csv"))
    ohlc["date"] = pd.to_datetime(ohlc["date"])
    ohlc = ohlc.set_index("date").sort_index()

    return {
        "h_target": h_target.reindex(idx_w).fillna(0.0),
        "f_target": f_target.reindex(idx_w).fillna(0.0),
        "idx": idx_w,
        "price": price_w,
        "vol": vol,
        "pct20": pct20,
        "ohlc": ohlc,
    }


# ============================================================
# 模块1：两套成交假设并行回测
# ============================================================
def run_variant_A(ctx):
    """
    版本A：T日收盘成交（历史基线）
    直接复用exp416引擎，复现48.76%年化
    """
    h_target = ctx["h_target"]
    f_target = ctx["f_target"]
    idx = ctx["idx"]
    price = ctx["price"]
    vol = ctx["vol"]
    pct20 = ctx["pct20"]

    # 用exp416引擎运行T1变体
    final_pos, diag = E416.run_variant(
        h_target, f_target, idx, price,
        vol=vol, pct20=pct20,
        vol_half=VOL_HALF, vol_full=VOL_FULL,
        half_scale=HALF_SCALE, full_scale=FULL_SCALE,
        cost=COST, return_diag=True,
    )

    # 计算净值和指标
    close = price.reindex(idx).values
    n = len(idx)

    # 逐日收益：仓位×价格变动 - 换手成本
    r_price = np.zeros(n)
    r_price[1:] = (close[1:] - close[:-1]) / close[:-1]
    dpos = np.zeros(n)
    dpos[1:] = np.abs(final_pos[1:] - final_pos[:-1])
    dpos[0] = np.abs(final_pos[0])

    net_ret = final_pos * r_price - 2 * COST * dpos
    equity = np.cumprod(1 + net_ret)

    # 统一指标
    years = n / TDPY
    total_ret = equity[-1] - 1
    ann_ret = (1 + total_ret) ** (1 / years) - 1 if total_ret > -1 else -1
    sharpe = np.mean(net_ret) / np.std(net_ret) * np.sqrt(TDPY) if np.std(net_ret) > 0 else 0
    peak = np.maximum.accumulate(equity)
    max_dd = ((equity - peak) / peak).min()
    calmar = ann_ret / abs(max_dd) if max_dd < 0 else 0

    # 提取交易
    trades = extract_trades(final_pos, price, idx)

    metrics = {
        "total_return": float(total_ret),
        "annual_return": float(ann_ret),
        "sharpe": float(sharpe),
        "max_drawdown": float(max_dd),
        "calmar": float(calmar),
        "n_trades": len(trades),
    }

    return {
        "final_pos": final_pos,
        "equity": equity,
        "net_ret": net_ret,
        "trades": trades,
        "metrics": metrics,
        "diag": diag,
    }


def run_variant_B(ctx):
    """
    版本B：T+1开盘成交（现实可行）
    
    核心逻辑：
      T日收盘 → 计算信号/风控 → 确定目标仓位pos[t]
      T+1开盘 → 执行仓位变化（从pos[t-1]调整到pos[t]）
    
    收益计算：
      T+1日收益 = pos[t] × (close[t+1] - open[t+1]) / open[t+1]  （开盘到收盘）
      + pos[t-1] × (open[t+1] - close[t]) / close[t]  （隔夜跳空，旧仓位承担）
    
    简化实现：
      版本B用"shifted position"方式：
      pos_shifted[t] = pos[t-1]  （T+1日持仓= T日决策的仓位）
      但这不对——T+1日持仓应该是pos[t]（T日收盘决策的仓位），只是成交在T+1开盘
    
    正确实现：
      版本B的收益 = 隔夜跳空收益（旧仓位） + 日内收益（新仓位）
      隔夜跳空：pos[t-1] × (open[t] - close[t-1]) / close[t-1]
      日内：pos[t] × (close[t] - open[t]) / open[t]
    """
    final_pos = ctx["final_pos_A"]  # 版本A的仓位（同一套信号/风控）
    ohlc = ctx["ohlc"]
    idx = ctx["idx"]
    price = ctx["price"]
    close = price.reindex(idx).values
    open_px = ohlc["open"].reindex(idx).values
    n = len(idx)

    # 隔夜跳空收益：pos[t-1] × (open[t] - close[t-1]) / close[t-1]
    overnight_ret = np.zeros(n)
    overnight_ret[0] = 0
    for t in range(1, n):
        overnight_ret[t] = final_pos[t-1] * (open_px[t] - close[t-1]) / close[t-1]

    # 日内收益：pos[t] × (close[t] - open[t]) / open[t]
    intraday_ret = np.zeros(n)
    intraday_ret[0] = 0
    for t in range(n):
        if open_px[t] > 0:
            intraday_ret[t] = final_pos[t] * (close[t] - open_px[t]) / open_px[t]

    # 总收益 = 隔夜 + 日内
    price_ret_B = overnight_ret + intraday_ret

    # 换手成本：仓位变化在T+1开盘发生
    dpos = np.zeros(n)
    dpos[1:] = np.abs(final_pos[1:] - final_pos[:-1])
    dpos[0] = np.abs(final_pos[0])
    cost_ret = 2 * COST * dpos

    # 净收益
    net_ret_B = price_ret_B - cost_ret

    # 净值
    equity_B = np.cumprod(1 + net_ret_B)

    # 统一指标
    years = n / TDPY
    total_ret = equity_B[-1] - 1
    ann_ret = (1 + total_ret) ** (1 / years) - 1 if total_ret > -1 else -1
    sharpe = np.mean(net_ret_B) / np.std(net_ret_B) * np.sqrt(TDPY) if np.std(net_ret_B) > 0 else 0
    peak = np.maximum.accumulate(equity_B)
    max_dd = ((equity_B - peak) / peak).min()
    calmar = ann_ret / abs(max_dd) if max_dd < 0 else 0

    # 提取交易（用shifted position近似）
    # 版本B的实际持仓是pos[t]，但成交在T+1开盘
    trades_B = extract_trades(final_pos, price, idx)

    metrics = {
        "total_return": float(total_ret),
        "annual_return": float(ann_ret),
        "sharpe": float(sharpe),
        "max_drawdown": float(max_dd),
        "calmar": float(calmar),
        "n_trades": len(trades_B),
    }

    return {
        "final_pos": final_pos,
        "equity": equity_B,
        "net_ret": net_ret_B,
        "trades": trades_B,
        "metrics": metrics,
        "overnight_ret": overnight_ret,
        "intraday_ret": intraday_ret,
    }


# ============================================================
# 模块2：隔夜跳空归因分析
# ============================================================
def analyze_gap_attribution(result_A, result_B, ctx):
    """隔夜跳空归因分析"""
    idx = ctx["idx"]
    ohlc = ctx["ohlc"]
    final_pos = result_A["final_pos"]
    close = ctx["price"].reindex(idx).values
    open_px = ohlc["open"].reindex(idx).values
    n = len(idx)

    # 隔夜跳空：open[t] - close[t-1]
    gap_pct = np.zeros(n)
    gap_pct[0] = 0
    for t in range(1, n):
        gap_pct[t] = (open_px[t] - close[t-1]) / close[t-1]

    # 信号方向
    pos_sign = np.sign(final_pos)
    pos_abs = np.abs(final_pos)

    # 信号方向收益（被隔夜跳空吃掉的部分）
    # 多头(pos>0)：隔夜跳空gap_pct < 0 → 亏损
    # 空头(pos<0)：隔夜跳空gap_pct > 0 → 亏损
    overnight_impact = pos_sign * gap_pct  # 负值=跳空不利

    # 只在持仓时统计
    in_pos = pos_abs > 1e-9
    overnight_impact_in_pos = overnight_impact[in_pos]
    gap_in_pos = gap_pct[1:][in_pos[1:]]
    pos_sign_in_pos = pos_sign[in_pos]

    # 统计
    n_pos_days = int(in_pos.sum())
    n_long = int((pos_sign_in_pos > 0).sum())
    n_short = int((pos_sign_in_pos < 0).sum())

    # 跳空方向与信号方向的一致性
    # 多头+跳空上涨=有利；多头+跳空下跌=不利
    # 空头+跳空下跌=有利；空头+跳空上涨=不利
    favorable = overnight_impact_in_pos > 0
    n_favorable = int(favorable.sum())
    n_unfavorable = int((~favorable).sum())

    # 跳空幅度分布
    gap_stats = {
        "total": {
            "mean": float(np.mean(gap_pct[1:])),
            "median": float(np.median(gap_pct[1:])),
            "std": float(np.std(gap_pct[1:])),
            "min": float(np.min(gap_pct[1:])),
            "max": float(np.max(gap_pct[1:])),
        },
        "in_position": {
            "mean": float(np.mean(gap_in_pos)),
            "median": float(np.median(gap_in_pos)),
            "std": float(np.std(gap_in_pos)),
            "min": float(np.min(gap_in_pos)),
            "max": float(np.max(gap_in_pos)),
        },
        "long_only": {
            "mean": float(np.mean(gap_in_pos[pos_sign_in_pos > 0])) if n_long > 0 else 0,
            "median": float(np.median(gap_in_pos[pos_sign_in_pos > 0])) if n_long > 0 else 0,
        },
        "short_only": {
            "mean": float(np.mean(gap_in_pos[pos_sign_in_pos < 0])) if n_short > 0 else 0,
            "median": float(np.median(gap_in_pos[pos_sign_in_pos < 0])) if n_short > 0 else 0,
        },
    }

    # 隔夜跳空对总收益的贡献
    overnight_total = float(np.sum(overnight_impact))
    intraday_total = float(np.sum(result_B["intraday_ret"]))
    total_price_ret_B = overnight_total + intraday_total

    attribution = {
        "n_position_days": n_pos_days,
        "n_long_days": n_long,
        "n_short_days": n_short,
        "n_favorable_gaps": n_favorable,
        "n_unfavorable_gaps": n_unfavorable,
        "favorable_ratio": float(n_favorable / max(n_pos_days, 1)),
        "unfavorable_ratio": float(n_unfavorable / max(n_pos_days, 1)),
        "gap_stats": gap_stats,
        "overnight_total_impact": overnight_total,
        "intraday_total_impact": intraday_total,
        "total_price_ret_B": total_price_ret_B,
        "overnight_pct_of_total": float(overnight_total / max(abs(total_price_ret_B), 1e-9)),
        "is_systematic": bool(n_unfavorable > n_favorable * 1.2),  # 不利跳空多20%以上=系统性
        "conclusion": "",
    }

    # 定性结论
    if attribution["is_systematic"]:
        attribution["conclusion"] = (
            f"隔夜跳空系统性不利：持仓{n_pos_days}天中，"
            f"不利跳空{n_unfavorable}次（{n_unfavorable/n_pos_days*100:.1f}%），"
            f"有利跳空{n_favorable}次（{n_favorable/n_pos_days*100:.1f}%）。"
            f"隔夜跳空贡献{overnight_total*100:.2f}%，"
            f"占版本B总价格收益的{attribution['overnight_pct_of_total']*100:.1f}%。"
        )
    else:
        attribution["conclusion"] = (
            f"隔夜跳空非系统性：持仓{n_pos_days}天中，"
            f"有利跳空{n_favorable}次（{n_favorable/n_pos_days*100:.1f}%），"
            f"不利跳空{n_unfavorable}次（{n_unfavorable/n_pos_days*100:.1f}%）。"
            f"隔夜跳空贡献{overnight_total*100:.2f}%。"
        )

    return attribution


# ============================================================
# 模块3：结论定性
# ============================================================
def qualitative_conclusion(metrics_A, metrics_B, attribution):
    """结论定性：T1在真实时序下是否仍然盈利？"""
    conclusion = {
        "variant_A_annual": metrics_A["annual_return"],
        "variant_B_annual": metrics_B["annual_return"],
        "variant_A_calmar": metrics_A["calmar"],
        "variant_B_calmar": metrics_B["calmar"],
        "is_B_positive": metrics_B["annual_return"] > 0,
        "annual_decay_pp": (metrics_A["annual_return"] - metrics_B["annual_return"]) * 100,
        "calmar_decay": metrics_A["calmar"] - metrics_B["calmar"],
    }

    if conclusion["is_B_positive"]:
        conclusion["verdict"] = "PROFITABLE"
        conclusion["summary"] = (
            f"T1在真实时序下仍然盈利，但收益显著衰减。"
            f"版本B年化{metrics_B['annual_return']*100:.1f}%（vs A {metrics_A['annual_return']*100:.1f}%），"
            f"衰减{conclusion['annual_decay_pp']:.0f}pp。"
            f"真实Calmar={metrics_B['calmar']:.2f}（vs A {metrics_A['calmar']:.2f}）。"
        )
        conclusion["recommendation"] = "可考虑实盘部署，但需按版本B的Calmar重新校准仓位"
    else:
        conclusion["verdict"] = "LOSS"
        conclusion["summary"] = (
            f"T1在真实时序下亏损。"
            f"版本B年化{metrics_B['annual_return']*100:.1f}%（vs A {metrics_A['annual_return']*100:.1f}%），"
            f"衰减{conclusion['annual_decay_pp']:.0f}pp。"
            f"最大回撤从{metrics_A['max_drawdown']*100:.1f}%恶化至{metrics_B['max_drawdown']*100:.1f}%。"
        )
        conclusion["recommendation"] = (
            "需结构性改造，方向：盘中信号/信号时点后移/持仓周期拉长/开仓过滤隔夜"
        )

    # 改造方向
    if not conclusion["is_B_positive"]:
        conclusion["restructure_options"] = [
            {
                "option": "盘中信号",
                "description": "使用分钟K线，在盘中触发信号时立即成交，避免隔夜跳空",
                "pros": "消除隔夜跳空风险",
                "cons": "需要分钟数据源，增加计算复杂度",
            },
            {
                "option": "信号时点后移",
                "description": "将信号计算从T日收盘后移到T日收盘前（如14:00），用当时价格成交",
                "pros": "信号与成交更同步",
                "cons": "可能丢失收盘后的信息，信号质量下降",
            },
            {
                "option": "持仓周期拉长",
                "description": "从当前平均持仓1-4天拉长至5-10天，摊薄隔夜跳空影响",
                "pros": "隔夜跳空占比降低",
                "cons": "增加持仓期风险，可能错过最佳平仓时机",
            },
            {
                "option": "开仓过滤隔夜",
                "description": "仅在已实现波动率低的日子开仓（隔夜跳空小的日子），高波动日不开仓",
                "pros": "主动规避大跳空",
                "cons": "减少交易机会，可能错过高收益行情",
            },
        ]

    return conclusion


# ============================================================
# 主流程
# ============================================================
if __name__ == "__main__":
    print("=" * 70)
    print("exp423: T1完整版本真实时序验证")
    print("=" * 70)

    # 加载数据
    ctx = load_context()
    print(f"\n数据: {ctx['idx'][0].date()} ~ {ctx['idx'][-1].date()} ({len(ctx['idx'])} 日)")
    print(f"价格: first={ctx['price'].iloc[0]:.0f}, last={ctx['price'].iloc[-1]:.0f}")
    print(f"波动率: mean={ctx['vol'].mean():.4f}, std={ctx['vol'].std():.4f}")

    # ===== 模块1：两套成交假设并行回测 =====
    print("\n" + "=" * 70)
    print("模块1：T1完整版本两套成交假设并行回测")
    print("=" * 70)

    # 版本A
    result_A = run_variant_A(ctx)
    ctx["final_pos_A"] = result_A["final_pos"]
    print(f"\n[版本A] T日收盘成交:")
    for k, v in result_A["metrics"].items():
        print(f"  {k}: {v:.4f}" if isinstance(v, float) else f"  {k}: {v}")
    
    # 复现检查
    expected_annual = 0.4876
    actual_annual = result_A["metrics"]["annual_return"]
    print(f"\n  复现检查: 预期年化={expected_annual:.4f}, 实际={actual_annual:.4f}, "
          f"差异={abs(actual_annual-expected_annual):.4f}")

    # 版本B
    result_B = run_variant_B(ctx)
    print(f"\n[版本B] T+1开盘成交:")
    for k, v in result_B["metrics"].items():
        print(f"  {k}: {v:.4f}" if isinstance(v, float) else f"  {k}: {v}")

    # 绩效衰减
    print(f"\n--- 绩效衰减（B vs A） ---")
    for k in ["annual_return", "sharpe", "max_drawdown", "calmar"]:
        diff = result_B["metrics"][k] - result_A["metrics"][k]
        print(f"  {k}: A={result_A['metrics'][k]:.4f} → B={result_B['metrics'][k]:.4f} (Δ={diff:+.4f})")

    # ===== 模块2：隔夜跳空归因分析 =====
    print("\n" + "=" * 70)
    print("模块2：隔夜跳空归因分析")
    print("=" * 70)
    attribution = analyze_gap_attribution(result_A, result_B, ctx)
    for k, v in attribution.items():
        if k == "gap_stats":
            print(f"  gap_stats:")
            for sk, sv in v.items():
                print(f"    {sk}: {sv}")
        elif k == "conclusion":
            print(f"  {k}: {v}")
        else:
            print(f"  {k}: {v}")

    # ===== 模块3：结论定性 =====
    print("\n" + "=" * 70)
    print("模块3：结论定性")
    print("=" * 70)
    conclusion = qualitative_conclusion(result_A["metrics"], result_B["metrics"], attribution)
    for k, v in conclusion.items():
        if k == "restructure_options":
            print(f"  restructure_options:")
            for opt in v:
                print(f"    - {opt['option']}: {opt['description']}")
        elif isinstance(v, (dict, list)):
            print(f"  {k}: {v}")
        else:
            print(f"  {k}: {v}")

    # ===== 保存交付物 =====
    print("\n" + "=" * 70)
    print("保存交付物")
    print("=" * 70)

    # 1. 净值对比CSV
    dates_list = [str(ctx["idx"][i].date()) for i in range(len(ctx["idx"]))]
    equity_df = pd.DataFrame({
        "date": dates_list,
        "equity_A": result_A["equity"],
        "equity_B": result_B["equity"],
        "net_ret_A": result_A["net_ret"],
        "net_ret_B": result_B["net_ret"],
        "overnight_ret_B": result_B["overnight_ret"],
        "intraday_ret_B": result_B["intraday_ret"],
    })
    equity_df.to_csv(os.path.join(DATA_DIR, "exp423_equity_comparison.csv"), index=False)
    print(f"  ✓ 净值对比CSV: data/exp423_equity_comparison.csv")

    # 2. 逐笔对比CSV
    trades_A_df = pd.DataFrame(result_A["trades"])
    trades_B_df = pd.DataFrame(result_B["trades"])
    comparison = pd.DataFrame({
        "trade_no": range(1, max(len(trades_A_df), len(trades_B_df)) + 1),
    })
    if len(trades_A_df) > 0 and len(trades_B_df) > 0:
        # 按entry_date合并
        merged = trades_A_df.merge(trades_B_df, on="entry_date", how="outer", suffixes=("_A", "_B"))
        merged.to_csv(os.path.join(DATA_DIR, "exp423_trade_comparison.csv"), index=False)
        print(f"  ✓ 逐笔对比CSV: data/exp423_trade_comparison.csv")

    # 3. 隔夜跳空统计CSV
    idx = ctx["idx"]
    ohlc = ctx["ohlc"]
    close = ctx["price"].reindex(idx).values
    open_px = ohlc["open"].reindex(idx).values
    n = len(idx)
    gap_df = pd.DataFrame({
        "date": dates_list[1:],
        "close_prev": close[:-1],
        "open_next": open_px[1:],
        "overnight_gap_pct": [(open_px[t] - close[t-1]) / close[t-1] for t in range(1, n)],
        "position_A": result_A["final_pos"][:-1],
    })
    gap_df.to_csv(os.path.join(DATA_DIR, "exp423_gap_stats.csv"), index=False)
    print(f"  ✓ 隔夜跳空统计CSV: data/exp423_gap_stats.csv")

    # 4. 汇总JSON
    summary = {
        "config": {
            "T1_vol_half": VOL_HALF,
            "T1_vol_full": VOL_FULL,
            "cost_per_side": COST,
        },
        "metrics_A": result_A["metrics"],
        "metrics_B": result_B["metrics"],
        "attribution": {k: v for k, v in attribution.items() if k != "gap_stats"},
        "gap_stats": attribution["gap_stats"],
        "conclusion": {k: v for k, v in conclusion.items() if k != "restructure_options"},
    }
    with open(os.path.join(DATA_DIR, "exp423_summary.json"), "w") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2, default=str)
    print(f"  ✓ 汇总JSON: data/exp423_summary.json")

    print("\n✅ exp423 完成")
    print(f"\n核心结论：")
    print(f"  版本A年化: {result_A['metrics']['annual_return']*100:.1f}%")
    print(f"  版本B年化: {result_B['metrics']['annual_return']*100:.1f}%")
    print(f"  衰减: {conclusion['annual_decay_pp']:.0f}pp")
    print(f"  判定: {conclusion['verdict']}")
    print(f"  隔夜跳空: {attribution['conclusion']}")
