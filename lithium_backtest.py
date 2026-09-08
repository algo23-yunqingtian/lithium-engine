#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
碳酸锂多主体博弈 · 防透视回测引擎 (v0.1 框架)
================================================
设计文档: /home/ubuntu/lithium_calendar/docs/backtest_no_lookahead_design.md

核心原则:
1. 数据时钟模型 - 每个主体在日期T只能看到 ≤ 数据发布日 的信息
2. 信号计算防透视 - 只用 T-1 及之前数据, 禁止全样本统计
3. 事件驱动状态机 - 主体行为规则逐日推进
4. Walk-forward 样本切分 - 训练/验证分离

用法:
    python lithium_backtest.py --test-start 2025-01-01 --test-end 2026-07-31
    python lithium_backtest.py --quick   # 快速冒烟测试

输出: 控制台摘要 + /home/ubuntu/analysis/output/backtest_result.json
"""
import sqlite3
import json
import argparse
import os
from datetime import datetime, timedelta

DB = "/home/ubuntu/lithium_calendar/lithium.db"
OUTPUT = "/home/ubuntu/analysis/output/backtest_result.json"

# 数据发布滞后 (交易日, 用于数据时钟)
PUB_LAG = {
    "daily": 1,       # 日度价格: T日收盘后, 次日可用
    "weekly": 3,      # 周度库存/产量: 约3日后发布
    "monthly": 7,     # 月度产量/需求: 约7日后发布
    "fundamental": 1, # 基本面指数: 基于日度数据, 次日可用
}

# 主体默认状态机 (v0.1 简化版, 后续迭代替换为 agent_psychology 的完整状态机)
DEFAULT_RULES = {
    "smelter":    {"sensitivity": {"profit": 0.6, "supply": -0.3}, "lookback": 20},
    "merchant":   {"sensitivity": {"basis": 0.3, "inventory": 0.2}, "lookback": 20},
    "institution": {"sensitivity": {"composite": 0.5}, "lookback": 20},
    "arbitrage":  {"sensitivity": {"basis": 0.4, "profit": 0.2}, "lookback": 20},
    "retail":     {"sensitivity": {"sentiment": 0.4, "composite": 0.2}, "lookback": 5},
    "resource":   {"sensitivity": {"supply": -0.3, "profit": 0.4}, "lookback": 20},
    "policy":     {"sensitivity": {"composite": 0.3}, "lookback": 60},
}

AGENT_NAMES = {
    "smelter": "冶炼厂", "merchant": "贸易商/正极厂", "institution": "机构",
    "arbitrage": "套利商", "retail": "散户", "resource": "锂矿商", "policy": "政策",
}


class BacktestEngine:
    def __init__(self, train_start="2023-07-01", train_end="2024-12-31",
                 test_start="2025-01-01", test_end="2026-07-31", db=DB):
        self.db = db
        self.train_start, self.train_end = train_start, train_end
        self.test_start, self.test_end = test_start, test_end
        self.prices = {}       # date -> {open, high, low, close, volume}
        self.dates = []        # 排序后的交易日列表
        self.fundamental = {}  # date -> {supply, demand, inventory, profit, sentiment, basis, composite}
        self.positions = {}    # agent -> {date: pos}
        self.conn = None

    # ---------- 数据加载 ----------
    def load_data(self):
        self.conn = sqlite3.connect(self.db)
        cur = self.conn.cursor()
        # 价格
        cur.execute("SELECT date, open, high, low, close, volume FROM prices ORDER BY date")
        for r in cur.fetchall():
            self.prices[r[0]] = {"open": r[1], "high": r[2], "low": r[3], "close": r[4],
                                 "volume": r[5] if r[5] is not None else 0}
        self.dates = sorted(self.prices.keys())
        # 基本面指数 (防透视: 表中每个值只依赖当日及之前数据)
        try:
            cur.execute("SELECT date, supply, demand, inventory, profit, sentiment, basis, composite FROM fundamental_indices")
            for r in cur.fetchall():
                self.fundamental[r[0]] = {"supply": r[1], "demand": r[2], "inventory": r[3],
                                          "profit": r[4], "sentiment": r[5], "basis": r[6], "composite": r[7]}
        except sqlite3.OperationalError:
            print("[WARN] fundamental_indices 表不存在, 回测将只使用价格动量信号")
        self.conn.close()
        print(f"[LOAD] 价格 {len(self.prices)} 日, 基本面指数 {len(self.fundamental)} 日")

    # ---------- 数据时钟: 某日期可用信号 ----------
    def available_signals(self, date_idx, lag_days=1):
        """返回 date_idx 往前 lag_days 个交易日的数据可用集合 (防透视核心)"""
        # 简化: 日度价格/基本面指数用 T-1 (即 index-1), 周度/月度用 PUB_LAG
        available = {"prices": {}, "fundamental": {}}
        if date_idx - lag_days >= 0:
            prev_date = self.dates[date_idx - lag_days]
            available["prices"] = self.prices.get(prev_date, {})
            available["fundamental"] = self.fundamental.get(prev_date, {})
        return available

    # ---------- 信号计算 (只用历史) ----------
    def momentum(self, date_idx, window=5):
        """5日动量: 用 T-1 收盘 vs T-1-window 收盘, 不含当日"""
        if date_idx - window - 1 < 0:
            return 0.0
        c_now = self.prices[self.dates[date_idx - 1]]["close"]
        c_past = self.prices[self.dates[date_idx - 1 - window]]["close"]
        if c_past == 0:
            return 0.0
        return (c_now - c_past) / c_past

    def rolling_zscore(self, date_idx, field, window=250, source="fundamental"):
        """滚动 z-score: 只用 [date_idx-window, date_idx-1] 的样本"""
        if source == "fundamental":
            vals = []
            for i in range(max(0, date_idx - window), date_idx):
                d = self.dates[i]
                if d in self.fundamental:
                    v = self.fundamental[d].get(field)
                    if v is not None:
                        vals.append(v)
            if len(vals) < 20:
                return 0.0
            mean = sum(vals) / len(vals)
            var = sum((v - mean) ** 2 for v in vals) / len(vals)
            std = var ** 0.5
            if std < 1e-9:
                return 0.0
            cur_date = self.dates[date_idx - 1]
            cur_v = self.fundamental.get(cur_date, {}).get(field)
            if cur_v is None:
                return 0.0
            return (cur_v - mean) / std
        return 0.0

    # ---------- 主体状态机 (v0.1 简化) ----------
    def agent_signal(self, agent_id, date_idx):
        """返回 (信号方向, 强度) — 只用历史数据"""
        rules = DEFAULT_RULES[agent_id]
        total = 0.0
        for field, w in rules["sensitivity"].items():
            if field == "composite":
                z = self.rolling_zscore(date_idx, "composite", 250)
            elif field in ("supply", "demand", "inventory", "profit", "sentiment", "basis"):
                z = self.rolling_zscore(date_idx, field, 250)
            else:
                continue
            total += w * z
        # 动量补充 (retail 重动量)
        if agent_id == "retail":
            total += 0.6 * self.momentum(date_idx, 5)
        elif agent_id == "institution":
            total += 0.3 * self.momentum(date_idx, 20)
        direction = 1 if total > 0.05 else (-1 if total < -0.05 else 0)
        strength = min(1.0, abs(total) / 1.0)
        return direction, strength

    # ---------- 回测主循环 ----------
    def run(self):
        self.load_data()
        # 定位测试区间
        try:
            test_start_idx = self.dates.index(self.test_start)
            test_end_idx = self.dates.index(self.test_end)
        except ValueError:
            # 自动对齐到最近日期
            test_start_idx = next(i for i, d in enumerate(self.dates) if d >= self.test_start)
            test_end_idx = next(i for i, d in enumerate(self.dates) if d >= self.test_end)
        print(f"[RUN] 回测区间 {self.dates[test_start_idx]} ~ {self.dates[test_end_idx]} ({test_end_idx - test_start_idx + 1} 个交易日)")

        # 每个 agent 独立账户
        accounts = {a: {"pos": 0, "cash": 1_000_000, "avg_cost": 0.0,
                        "pnl_peak": 0.0, "pnl_history": [], "trades": 0} for a in DEFAULT_RULES}
        equity_curve = []
        state_log = []

        for idx in range(test_start_idx, test_end_idx + 1):
            date = self.dates[idx]
            close = self.prices[date]["close"]
            # 每日决策: 用 idx-1 及之前信号
            for agent_id in DEFAULT_RULES:
                direction, strength = self.agent_signal(agent_id, idx)
                acc = accounts[agent_id]
                target = int(direction * 100 * strength)  # 仓位 -100~100
                # 简单调仓: 每次调 20%
                diff = target - acc["pos"]
                if abs(diff) >= 20:
                    trade = diff / abs(diff) * 20
                    cost = trade * close
                    if cost > acc["cash"] and trade > 0:
                        trade = acc["cash"] / close
                    acc["cash"] -= trade * close
                    acc["pos"] += trade
                    acc["trades"] += 1
                    if acc["pos"] != 0:
                        acc["avg_cost"] = close
            # 每日盯市
            for agent_id, acc in accounts.items():
                pnl = acc["pos"] * (close - acc["avg_cost"]) if acc["pos"] != 0 else 0.0
                acc["pnl_history"].append(pnl)
                acc["pnl_peak"] = max(acc["pnl_peak"], pnl)
                state_log.append({"date": date, "agent": agent_id,
                                  "pos": acc["pos"], "signal_dir": None})
            # 组合净值
            total_pnl = sum(a["pos"] * (close - a["avg_cost"]) for a in accounts.values() if a["pos"] != 0)
            equity_curve.append({"date": date, "pnl": total_pnl})

        metrics = self.calc_metrics(equity_curve, accounts)
        self.report(metrics, equity_curve, accounts)
        return metrics

    # ---------- 绩效 ----------
    def calc_metrics(self, equity_curve, accounts):
        if not equity_curve:
            return {}
        pnls = [e["pnl"] for e in equity_curve]
        final_pnl = pnls[-1]
        peak = max(pnls)
        drawdown = peak - final_pnl if peak > 0 else 0
        # 简单年化 (按 244 交易日)
        n = len(pnls)
        ann_return = (1 + final_pnl / 1_000_000) ** (244 / max(n, 1)) - 1
        # 波动与夏普
        mean_pnl = sum(pnls) / n
        var = sum((p - mean_pnl) ** 2 for p in pnls) / n
        std = var ** 0.5
        sharpe = (mean_pnl / std) * (244 ** 0.5) if std > 0 else 0.0
        return {
            "period_days": n,
            "final_pnl": round(final_pnl, 2),
            "total_return_pct": round(final_pnl / 1_000_000 * 100, 2),
            "max_drawdown": round(drawdown, 2),
            "ann_return_pct": round(ann_return * 100, 2),
            "sharpe": round(sharpe, 2),
            "n_trades": sum(a["trades"] for a in accounts.values()),
            "per_agent": {a: {"pos": round(acc["pos"], 1), "trades": acc["trades"],
                              "final_pnl": round(acc["pnl_history"][-1], 2) if acc["pnl_history"] else 0}
                          for a, acc in accounts.items()}
        }

    def report(self, metrics, equity_curve, accounts):
        print("\n" + "=" * 50)
        print("回测结果摘要")
        print("=" * 50)
        for k, v in metrics.items():
            if k != "per_agent":
                print(f"  {k}: {v}")
        print("  per_agent:")
        for a, d in metrics.get("per_agent", {}).items():
            print(f"    {AGENT_NAMES[a]}: {d}")
        os.makedirs(os.path.dirname(OUTPUT), exist_ok=True)
        with open(OUTPUT, "w", encoding="utf-8") as f:
            json.dump({"metrics": metrics,
                       "last_equity": equity_curve[-5:] if equity_curve else [],
                       "params": {"train": [self.train_start, self.train_end],
                                  "test": [self.test_start, self.test_end],
                                  "pub_lag": PUB_LAG}},
                      f, ensure_ascii=False, indent=2)
        print(f"\n[OUTPUT] {OUTPUT}")


def main():
    ap = argparse.ArgumentParser(description="碳酸锂多主体博弈防透视回测")
    ap.add_argument("--train-start", default="2023-07-01")
    ap.add_argument("--train-end", default="2024-12-31")
    ap.add_argument("--test-start", default="2025-01-01")
    ap.add_argument("--test-end", default="2026-07-31")
    ap.add_argument("--quick", action="store_true", help="快速冒烟测试(最后30日)")
    args = ap.parse_args()

    if args.quick:
        dates = sorted(p["date"] for p in BacktestEngine().prices) if False else None
        # quick 模式: 直接用最近 30 日
        args.test_start = "2026-06-01"
        args.test_end = "2026-07-31"

    eng = BacktestEngine(train_start=args.train_start, train_end=args.train_end,
                         test_start=args.test_start, test_end=args.test_end)
    eng.run()


if __name__ == "__main__":
    main()
