#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
碳酸锂多主体博弈 — Agent 每日更新脚本 (Phase 2)
no_agent cron 脚本，每日 18:30 自动运行，驱动 7 个 agent 按规则更新状态。

数据源（全部来自本地 SQLite）：
  - prices 表：LC0 主力日K（close/volume/position/settle）
  - contract_prices 表：各合约收盘价
  - spreads 表：月差结构
  - inventory_history 表：库存数据
  - notes 表：小作文情绪

执行流程：
  1. 读取最新市场数据
  2. 逐个 agent 运行规则引擎
  3. 检查止损止盈
  4. 写入 agent_history + agent_view_log
  5. 记录 agent_action_log
  6. 标记 needs_review（score 变化 > 20 时）

输出：简洁摘要（stdout），供 cron 投递
"""

import os
import sys
import json
import sqlite3
import math
from datetime import datetime, timedelta

# ─── 博弈因子模块 ─────────────────────────────────────────────
from game_factors import apply_game_factors

# ─── 数据库路径 ───────────────────────────────────────────────
DB_PATH = os.environ.get("LITHIUM_DB", "/home/ubuntu/lithium_calendar/lithium.db")

# ─── 基本面六维 z-score 融入参数 (Phase 6, 2026-08-01) ─────────────
# fundamental_indices 表：滚动250日 z-score，方向已归一化（正值=利多，负值=利空）
# 权重公式: 贡献 = z × weight × FUNDAMENTAL_SCALE
FUNDAMENTAL_SCALE = 30   # z-score → 分数贡献放大系数（可调）
FUNDAMENTAL_WEIGHTS = {
    "smelter":     {"supply": -0.3, "profit": 0.4, "inventory": -0.2},
    "resource":    {"supply": -0.3, "profit": 0.4, "inventory": -0.2},
    "merchant":    {"basis": 0.3, "inventory": 0.2, "profit": 0.2},
    "institution": {"composite": 0.5},
    "arbitrage":   {"basis": 0.4, "profit": 0.2},
    "retail":      {"sentiment": 0.4, "composite": 0.2},
    # policy: 保持原逻辑，不参与基本面加权
}
AGENT_CLAMP = {
    "smelter": (-60, 80), "merchant": (-50, 60), "institution": (-70, 70),
    "arbitrage": (-20, 20), "retail": (-60, 60), "resource": (-50, 70),
    "policy": (-60, 60),
}


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=OFF")  # 脚本内表无外部FK依赖，OFF避免 WAL 模式下误报 FK mismatch
    return conn


# ─── 数据读取 ─────────────────────────────────────────────────
def fetch_latest_price(conn):
    """读取最新价格数据"""
    row = conn.execute(
        "SELECT * FROM prices ORDER BY date DESC LIMIT 3"
    ).fetchall()
    if not row:
        return None
    prices = row  # Row 对象，通过键访问
    # 计算近期统计
    closes = [p["close"] for p in prices]
    volumes = [p["volume"] for p in prices]
    positions_list = [p["position"] for p in prices]

    # 1日涨跌
    if len(prices) >= 2 and prices[1]["settle"] > 0:
        daily_chg_pct = (prices[0]["close"] / prices[1]["settle"] - 1) * 100
        daily_dir = "up" if prices[0]["close"] > prices[1]["settle"] else "down"
    else:
        daily_chg_pct = 0
        daily_dir = "flat"

    # 5日涨跌（需要更多数据）
    recent = conn.execute(
        "SELECT close FROM prices ORDER BY date DESC LIMIT 6"
    ).fetchall()
    if len(recent) >= 6:
        five_day_chg_pct = (recent[0]["close"] / recent[5]["close"] - 1) * 100
    else:
        five_day_chg_pct = daily_chg_pct * 2  # 粗略

    # 返回可直接使用的结构
    return {
        "latest": prices[0],
        "yesterday": prices[1] if len(prices) > 1 else prices[0],
        "prev_day": prices[2] if len(prices) > 2 else prices[0],
        "close_3d": closes,
        "volume_3d": volumes,
        "oi_3d": positions_list,
        "daily_chg_pct": daily_chg_pct,
        "daily_dir": daily_dir,
        "five_day_chg_pct": five_day_chg_pct,
    }


def fetch_contract_prices(conn):
    """读取各合约最新价格"""
    contracts = ["LC0", "LC2608", "LC2609", "LC2610", "LC2611", "LC2612", "LC2701"]
    result = {}
    for c in contracts:
        row = conn.execute(
            "SELECT * FROM contract_prices WHERE contract=? ORDER BY date DESC LIMIT 3",
            (c,)
        ).fetchall()
        if row:
            result[c] = [dict(r) for r in row]  # Row -> dict
    return result


def fetch_spreads(conn):
    """读取月差结构"""
    rows = conn.execute(
        "SELECT * FROM spreads ORDER BY date DESC LIMIT 1"
    ).fetchall()
    if not rows:
        return []
    return [dict(r) for r in rows]


def fetch_inventory(conn):
    """读取最新库存"""
    rows = conn.execute(
        "SELECT * FROM inventory_history ORDER BY date DESC LIMIT 5"
    ).fetchall()
    if not rows:
        return []
    return [dict(r) for r in rows]  # Row -> dict


def fetch_notes_data(conn):
    """读取近期小作文数据"""
    rows = conn.execute(
        "SELECT * FROM notes ORDER BY date DESC LIMIT 10"
    ).fetchall()
    if not rows:
        return []
    return [dict(r) for r in rows]


# ─── 评分工具函数 ─────────────────────────────────────────────
def clamp(val, lo=-100, hi=100):
    """限制评分范围"""
    return max(lo, min(hi, val))


def sigmoid(x, center=0, slope=1):
    """sigmoid 映射到 -100 ~ 100"""
    return clamp(200 / (1 + math.exp(-slope * (x - center))) - 100)


def piecewise(x, thresholds, values):
    """分段线性映射"""
    if x <= thresholds[0]:
        return values[0]
    if x >= thresholds[-1]:
        return values[-1]
    for i in range(len(thresholds) - 1):
        if thresholds[i] <= x <= thresholds[i + 1]:
            t = (x - thresholds[i]) / (thresholds[i + 1] - thresholds[i] + 0.001)
            return values[i] + t * (values[i + 1] - values[i])
    return values[-1]


# ─── 通用 Agent 规则引擎 ──────────────────────────────────────
LOGIC_DIMENSIONS = {
    "supply_risk": "供给风险",
    "demand_drop": "需求下滑",
    "inventory": "库存变化",
    "macro_policy": "宏观政策",
    "cost_support": "成本支撑",
    "sentiment": "情绪/小作文",
    "basis_trade": "基差月差",
}

# Agent → Logic 映射（哪些agent参与每个逻辑维度的计算）
AGENT_LOGIC_MAP = {
    "supply_risk": ["smelter", "resource"],
    "demand_drop": ["merchant", "institution"],
    "inventory": ["merchant", "arbitrage"],
    "macro_policy": ["policy"],
    "cost_support": ["smelter", "resource"],
    "sentiment": ["retail"],
    "basis_trade": ["arbitrage", "merchant"],
}

def compute_agent_score(agent_id, market, logic_scores=None):
    """
    通用评分框架：根据 agent_id 返回新的 view_score, view_text,
    delta_position, trigger, reason
    
    同时计算7维逻辑评分（如传入 logic_scores dict）
    market = {latest, spreads, inventory, notes, contract_prices, daily_chg_pct, five_day_chg_pct}
    """
    # ── 读取最新基本面六维 z-score（防透视滚动标准化，见 fundamental_indices.py）──
    # 方向已归一化：正值=利多，负值=利空。表不存在/读取失败 → fund=None → 回退原逻辑
    fund = None
    try:
        _conn = get_db()
        _row = _conn.execute(
            "SELECT supply, demand, inventory, profit, sentiment, basis, composite "
            "FROM fundamental_indices ORDER BY date DESC LIMIT 1"
        ).fetchone()
        _conn.close()
        if _row:
            fund = dict(_row)
    except Exception:
        fund = None

    score = 0
    reasons = []
    delta_pos = 0
    trigger = "rule_engine"

    latest = market["latest"]
    daily_chg = market["daily_chg_pct"]
    five_day_chg = market["five_day_chg_pct"]
    volume_today = latest["volume"]
    oi_today = latest["position"]
    close_price = latest["close"]

    spreads = market.get("spreads", [])
    inventory = market.get("inventory", [])
    notes = market.get("notes", [])
    contract_prices = market.get("contract_prices", {})

    # ── 获取月差数据 ──
    lc0_close = None
    lc2609_close = None
    lc2611_close = None
    lc2612_close = None
    spread_lc0_2608 = 0
    spread_2608_2609 = 0
    spread_2609_2610 = 0
    spread_2610_2611 = 0
    spread_2611_2612 = 0

    # 从 spreads 表读取
    for s in spreads:
        nc = s.get("near_contract", "")
        fc = s.get("far_contract", "")
        sp = s.get("spread", 0)
        if nc == "LC0" and fc == "LC2608": spread_lc0_2608 = sp
        elif nc == "LC2608" and fc == "LC2609": spread_2608_2609 = sp
        elif nc == "LC2609" and fc == "LC2610": spread_2609_2610 = sp
        elif nc == "LC2610" and fc == "LC2611": spread_2610_2611 = sp
        elif nc == "LC2611" and fc == "LC2612": spread_2611_2612 = sp

    # 从 contract_prices 读取具体合约价格
    for c in ["LC0", "LC2608", "LC2609", "LC2610", "LC2611", "LC2612"]:
        if c in contract_prices and len(contract_prices[c]) > 0:
            cp = contract_prices[c][0]
            if c == "LC0": lc0_close = cp["close"]
            elif c == "LC2608": lc0_close = cp["close"]
            elif c == "LC2609": lc2609_close = cp["close"]
            elif c == "LC2611": lc2611_close = cp["close"]
            elif c == "LC2612": lc2612_close = cp["close"]

    # 从 prices 表获取 LC0
    if lc0_close is None and latest:
        lc0_close = latest["close"]

    # 库存数据
    latest_inv = inventory[0]["inventory"] if inventory else 0
    inv_change = inventory[0]["change"] if len(inventory) > 0 else 0
    # 近5天库存趋势
    inv_trend = 0
    if len(inventory) >= 5:
        inv_old = sum(i["inventory"] for i in inventory[:5]) / 5
        inv_old2 = sum(i["inventory"] for i in inventory[3:5]) / 2 if len(inventory) >= 3 else inv_old
        if inv_old2 > 0:
            inv_trend = (latest_inv / inv_old2 - 1) * 100
    elif len(inventory) >= 2:
        inv_trend = (inventory[0]["inventory"] / inventory[1]["inventory"] - 1) * 100 if inventory[1]["inventory"] > 0 else 0

    # 小作文情绪统计
    bullish_count = 0
    bearish_count = 0
    total_notes = 0
    for n in notes:
        sent = n.get("sentiment", "")
        if sent == "利多": bullish_count += 1
        elif sent == "利空": bearish_count += 1
        total_notes += 1
    news_sentiment = (bullish_count - bearish_count) / max(total_notes, 1)  # -1 ~ 1

    # 持仓量变化趋势
    oi_trend = 0
    if latest and market.get("oi_3d") and len(market["oi_3d"]) >= 3:
        oi_old = market["oi_3d"][1] if market["oi_3d"][1] > 0 else market["oi_3d"][2]
        oi_trend = (oi_today / oi_old - 1) * 100 if oi_old > 0 else 0

    # ── 7维逻辑评分计算 ──
    if logic_scores is not None:
        _compute_logic_scores(agent_id, market, logic_scores, latest, daily_chg, inv_trend,
                              news_sentiment, spread_2609_2610, spread_2611_2612,
                              spread_2608_2609, oi_trend, volume_today, lc0_close, notes, fund)

    # ──────────────────────────────────────────────────
    # Agent 评分逻辑
    # ──────────────────────────────────────────────────

    if agent_id == "smelter":
        # 冶炼厂：利润改善→增产信号（看空），亏损→减产预期（看多）
        # 价格趋势 + 库存趋势
        if daily_chg > 0 and oi_trend > 0:
            # 价格上涨 + 增仓 → 多头强势 → 冶炼厂增产意愿强 → 看空
            score += 30
            reasons.append("量价齐升，多头强势")
        elif daily_chg < 0 and oi_trend < 0:
            # 价格下跌 + 减仓 → 多头出逃 → 冶炼厂减产意愿强 → 看多
            score -= 25
            reasons.append("减仓下跌，看空情绪蔓延")
        elif inv_trend > 2 and daily_chg < 0:
            # 累库 + 跌 → 基本面恶化 → 看空
            score -= 20
            reasons.append("累库叠加下跌，基本面承压")
        elif inv_trend < -2 and daily_chg > 0:
            # 去库 + 涨 → 基本面改善 → 看多
            score += 20
            reasons.append("去库叠加上涨，基本面改善")
        elif abs(daily_chg) < 1:
            score += 5  # 窄幅震荡，中性偏多（供给刚性强）
            reasons.append("窄幅震荡，供给刚性支撑")

        score = clamp(score, -60, 80)  # 冶炼厂长期偏多头（供给端）

    elif agent_id == "merchant":
        # 贸易商：基差/月差结构 → 采购/抛售决策
        # 向后市场（contango）→ 持有现货有利 → 做多
        # 向前市场（backwardation）→ 现货溢价高 → 抛售
        if spread_2609_2610 < -500:
            # 远月贴水较大 → 现货相对强 → 持有现货 → 偏多
            score += 25
            reasons.append("远月贴水，现货偏强")
        elif spread_2609_2610 > 300:
            # 远月升水 → 持有成本高 → 抛售 → 偏空
            score -= 25
            reasons.append("远月升水，持货成本高")

        if news_sentiment > 0.3:
            score += 15
            reasons.append("小作文偏利多")
        elif news_sentiment < -0.3:
            score -= 15
            reasons.append("小作文偏利空")

        if daily_chg > 2:
            score -= 10  # 大涨后贸易商倾向于获利了结
            reasons.append("大涨后获利了结")
        elif daily_chg < -2:
            score += 10  # 大跌后贸易商逢低补货
            reasons.append("大跌后逢低补货")

        score = clamp(score, -50, 60)

    elif agent_id == "institution":
        # 纯投机构：动量 + 量仓 + 趋势跟随
        if daily_chg > 1.5 and oi_trend > 0:
            # 量价齐升 → 趋势明确 → 追多
            score += 40
            reasons.append("量价齐升，趋势多头")
        elif daily_chg < -1.5 and oi_trend > 0:
            # 放量下跌 → 空头强势 → 追空
            score -= 40
            reasons.append("放量下跌，空头主导")
        elif daily_chg > 1 and oi_trend < 0:
            # 价格上涨但减仓 → 空头回补 → 趋势不确定
            score += 5
            reasons.append("增涨减仓，空头回补")
        elif daily_chg < -1 and oi_trend < 0:
            # 价格下跌且减仓 → 多头止损 → 可能见底
            score += 10
            reasons.append("减仓下跌，可能接近底部")

        # 5日动量
        if five_day_chg > 5:
            score += 15
            reasons.append("5日动量正向")
        elif five_day_chg < -5:
            score -= 15
            reasons.append("5日动量负向")

        score = clamp(score, -70, 70)

    elif agent_id == "arbitrage":
        # 套利商：月差结构均值回归
        # 检查远月升水/贴水是否偏离历史均值
        if spread_2609_2610 < -800:
            # 远月大幅贴水 → 买入远月/卖出近月
            score += 10  # 套利行为带来中性评分
            reasons.append("远月深度贴水，正向套利空间")
        elif spread_2609_2610 > 500:
            score -= 10
            reasons.append("远月大幅升水，反向套利空间")

        if abs(daily_chg) > 3:
            # 单日大幅波动 → 套利空间增大 → 中性
            score += 5
            reasons.append("波动率放大，套利窗口打开")

        score = clamp(score, -20, 20)  # 套利商中性倾向

    elif agent_id == "retail":
        # 散户：追涨杀跌 + 情绪驱动
        if daily_chg > 2:
            score += 25
            reasons.append("大涨刺激追多")
        elif daily_chg < -2:
            score -= 25
            reasons.append("大跌刺激追空")

        if news_sentiment > 0.4:
            score += 15
            reasons.append("市场情绪偏多")
        elif news_sentiment < -0.4:
            score -= 15
            reasons.append("市场情绪偏空")

        # 换手率/成交量判断
        if volume_today > 250000 and daily_chg > 0:
            score += 10
            reasons.append("放量上涨，散户活跃")
        elif volume_today > 250000 and daily_chg < 0:
            score -= 10
            reasons.append("放量下跌，恐慌蔓延")

        score = clamp(score, -60, 60)

    elif agent_id == "resource":
        # 矿商：成本支撑 + 产能利用
        # 价格接近成本线 → 看多（减产预期）
        # 目前碳酸锂成本线约在 120000-130000 区间
        cost_line = 125000
        if close_price > 0 and cost_line > 0:
            dist_from_cost = (close_price / cost_line - 1) * 100
            if dist_from_cost < 5:
                score += 30
                reasons.append(f"价格接近成本线 ({close_price:.0f} vs {cost_line:.0f})")
            elif dist_from_cost > 20:
                score -= 20
                reasons.append(f"价格远超成本线 ({close_price:.0f} vs {cost_line:.0f})")
            elif dist_from_cost < 10:
                score += 10
                reasons.append("价格在成本线附近，有支撑")

        if inv_trend > 3:
            score -= 10
            reasons.append("累库压力，矿山出货")
        elif inv_trend < -3:
            score += 10
            reasons.append("去库，矿山挺价")

        score = clamp(score, -50, 70)  # 矿商长期偏多（成本支撑）

    elif agent_id == "policy":
        # 政策博弈者：关注政策事件 + 宏观
        policy_words = ["两会", "发改委", "工信部", "新能源", "储能", "监管", "限价",
                        "收储", "抛储", "出口", "碳中和", "双碳"]
        policy_hits = 0
        policy_sentiment = 0
        for n in notes:
            content = (n.get("content", "") + n.get("title", "")).lower()
            for pw in policy_words:
                if pw in content:
                    policy_hits += 1
                    if n.get("sentiment") == "利多":
                        policy_sentiment += 1
                    elif n.get("sentiment") == "利空":
                        policy_sentiment -= 1

        if policy_hits > 0:
            if policy_sentiment > 0:
                score += 15
                reasons.append(f"政策面偏多（{policy_hits}条相关）")
            elif policy_sentiment < 0:
                score -= 15
                reasons.append(f"政策面偏空（{policy_hits}条相关）")
            else:
                score += 5
                reasons.append(f"政策面中性（{policy_hits}条相关）")

        # 价格过高 → 政策干预风险
        if close_price > 160000:
            score -= 20
            reasons.append("价格偏高，政策干预风险")
        elif close_price < 130000:
            score += 10
            reasons.append("价格偏低，政策托底预期")

        score = clamp(score, -60, 60)

    else:
        score = 0
        reasons.append("未知 agent")

    # ── 基本面六维 z-score 加权融入 view_score（policy 保持原逻辑）──
    if fund is not None and agent_id != "policy":
        w = FUNDAMENTAL_WEIGHTS.get(agent_id)
        if w:
            contrib = 0.0
            for dim, wt in w.items():
                z = fund.get(dim)
                if z is not None:
                    contrib += z * wt
            contrib *= FUNDAMENTAL_SCALE
            score += contrib
            if abs(contrib) >= 0.5:
                reasons.append(f"基本面加权{contrib:+.1f}")
            lo, hi = AGENT_CLAMP.get(agent_id, (-100, 100))
            score = clamp(score, lo, hi)

    return score, "; ".join(reasons) if reasons else "无明显信号", delta_pos


def _compute_logic_scores(agent_id, market, logic_scores, latest, daily_chg, inv_trend,
                          news_sentiment, spread_2609_2610, spread_2611_2612,
                          spread_2608_2609, oi_trend, volume_today, lc0_close, notes,
                          fund=None):
    """为7维逻辑计算评分（-100~100）"""
    close_price = latest["close"]
    cost_line = 125000  # 碳酸锂成本线参考
    dist_from_cost = (close_price / cost_line - 1) * 100 if close_price > 0 else 0

    # 1. supply_risk: 供给风险 — 价格↑+增仓 → 冶炼增产 → 风险高 → 评分高
    #    smelter + resource 参与
    # 2. demand_drop: 需求下滑 — 跌+减仓 → 采购弱 → 需求风险高
    #    merchant + institution 参与
    # 3. inventory: 库存变化 — 累库 → 风险高
    #    merchant + arbitrage 参与
    # 4. macro_policy: 宏观政策 — 政策利多 → 评分高
    #    policy 参与
    # 5. cost_support: 成本支撑 — 接近成本线 → 支撑强 → 评分高
    #    smelter + resource 参与
    # 6. sentiment: 情绪 — 新闻利多 → 评分高
    #    retail 参与
    # 7. basis_trade: 基差月差 — contango → 风险高
    #    arbitrage + merchant 参与

    logic_score = 0  # 当前agent对这个逻辑维度的贡献

    # supply_risk: 价格涨+增仓 → 供给风险上升
    if agent_id in ("smelter", "resource"):
        if daily_chg > 1 and oi_trend > 0:
            logic_score += 40
        elif daily_chg > 0:
            logic_score += 15
        if inv_trend < -2:
            logic_score -= 15  # 去库 → 供给风险低
        # 基本面引用 supply + profit（z>0=利多）：
        #   供给偏紧(supply z>0)→供给风险低(减分)；利润高(profit z>0)→增产预期→风险高(加分)
        if fund:
            logic_score += (-fund.get("supply", 0) + fund.get("profit", 0)) * 10
        logic_scores["supply_risk"] = logic_scores.get("supply_risk", 0) + logic_score

    # demand_drop: 价格跌+减仓 → 需求风险上升
    if agent_id in ("merchant", "institution"):
        if daily_chg < -1 and oi_trend < 0:
            logic_score = 40
        elif daily_chg < 0:
            logic_score = 15
        if news_sentiment < -0.3:
            logic_score += 15
        # 基本面引用 demand + sentiment（z>0=利多）：需求/情绪好 → 需求风险低(减分)
        if fund:
            logic_score += (-fund.get("demand", 0) * 10 - fund.get("sentiment", 0) * 5)
        logic_scores["demand_drop"] = logic_scores.get("demand_drop", 0) + logic_score

    # inventory: 累库 → 库存风险上升
    if agent_id in ("merchant", "arbitrage"):
        if inv_trend > 2:
            logic_score = 40
        elif inv_trend > 0:
            logic_score = 15
        # 基本面引用 inventory（z>0=利多=库存偏低）→ 累库风险低(减分)
        if fund:
            logic_score -= fund.get("inventory", 0) * 10
        logic_scores["inventory"] = logic_scores.get("inventory", 0) + logic_score

    # macro_policy: 政策面
    if agent_id == "policy":
        policy_words = ["两会", "发改委", "工信部", "新能源", "储能", "监管", "限价",
                        "收储", "抛储", "出口", "碳中和", "双碳"]
        policy_hits = 0
        policy_pos = 0
        for n in notes:
            content = (n.get("content", "") + n.get("title", "")).lower()
            for pw in policy_words:
                if pw in content:
                    policy_hits += 1
                    if n.get("sentiment") == "利多":
                        policy_pos += 1
                    elif n.get("sentiment") == "利空":
                        policy_pos -= 1
        logic_score = int(policy_pos * 10 + policy_hits * 5)
        if close_price > 160000:
            logic_score -= 20
        elif close_price < 130000:
            logic_score += 10
        logic_scores["macro_policy"] = logic_scores.get("macro_policy", 0) + logic_score

    # cost_support: 接近成本线 → 支撑强
    if agent_id in ("smelter", "resource"):
        if -5 < dist_from_cost < 5:
            logic_score = 40
        elif -10 < dist_from_cost < 10:
            logic_score = 20
        elif dist_from_cost > 20:
            logic_score = -20  # 远离成本 → 支撑弱
        # 基本面引用 profit：利润高(z>0)→离成本远→成本支撑弱(减分)
        if fund:
            logic_score -= fund.get("profit", 0) * 10
        logic_scores["cost_support"] = logic_scores.get("cost_support", 0) + logic_score

    # sentiment: 情绪面
    if agent_id == "retail":
        logic_score = int(news_sentiment * 50)
        if volume_today > 250000 and daily_chg > 0:
            logic_score += 10
        elif volume_today > 250000 and daily_chg < 0:
            logic_score -= 10
        # 基本面引用 sentiment：情绪 z>0(利多) → 情绪分高(加分)
        if fund:
            logic_score += int(fund.get("sentiment", 0) * 10)
        logic_scores["sentiment"] = logic_scores.get("sentiment", 0) + logic_score

    # basis_trade: 基差/月差
    if agent_id in ("arbitrage", "merchant"):
        if spread_2609_2610 < -500:
            logic_score = 20  # 远月贴水 → contango → 持有现货有利 → 供给风险高
        elif spread_2609_2610 > 300:
            logic_score = -20  # 远月升水 → 持有成本高
        if spread_2611_2612 < -300:
            logic_score += 10
        # 基本面引用 basis：基差强(basis z>0=现货升水/近强远弱)→套利风险上升(加分)
        if fund:
            logic_score += int(fund.get("basis", 0) * 10)
        logic_scores["basis_trade"] = logic_scores.get("basis_trade", 0) + logic_score


def run_daily_update():
    """主执行流程"""
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Agent 每日更新脚本启动")
    print(f"{'='*60}")

    conn = get_db()

    # Step 1: 读取市场数据
    print("\n[Step 1] 读取市场数据...")
    
    # 获取价格数据（这是核心 market 数据源）
    price_data = fetch_latest_price(conn)
    if price_data is None:
        print("[错误] 无法读取价格数据，退出")
        conn.close()
        return
    
    # 构建 market 字典（扁平结构，供 compute_agent_score 使用）
    market = {}
    market["latest"] = price_data["latest"]
    market["yesterday"] = price_data["yesterday"]
    market["daily_chg_pct"] = price_data["daily_chg_pct"]
    market["daily_dir"] = price_data["daily_dir"]
    market["five_day_chg_pct"] = price_data["five_day_chg_pct"]
    market["oi_3d"] = price_data["oi_3d"]
    market["volume_3d"] = price_data["volume_3d"]
    
    market["contract_prices"] = fetch_contract_prices(conn)
    market["spreads"] = fetch_spreads(conn)
    market["inventory"] = fetch_inventory(conn)
    market["notes"] = fetch_notes_data(conn)

    # 调试输出
    latest = price_data["latest"]
    print(f"  最新价格: {latest['close']:.0f} ({latest['date']})")
    print(f"  日涨跌: {price_data['daily_chg_pct']:+.2f}%")
    print(f"  5日涨跌: {price_data['five_day_chg_pct']:+.2f}%")
    print(f"  成交量: {latest['volume']:,}")
    print(f"  持仓量: {latest['position']:,}")

    inv_list = market.get("inventory", [])
    if inv_list:
        inv = inv_list[0]
        print(f"  库存: {inv['inventory']:,} ({inv['change']:+d})")

    # Step 2: 逐个 agent 运行规则引擎
    print(f"\n[Step 2] 运行规则引擎...")
    agent_ids = ["smelter", "merchant", "institution", "arbitrage", "retail", "resource", "policy"]

    today = datetime.now().strftime("%Y-%m-%d")
    update_count = 0
    review_count = 0
    logic_scores_list = []  # 每日各agent的logic_scores汇总

    # 收集所有 agent 基础分数用于博弈因子计算
    base_scores_dict = {}
    deltas_dict = {}
    texts_dict = {}
    agents_dict = {}
    capital_dict = {}

    for aid in agent_ids:
        try:
            # 读取当前 agent 状态
            agent_row = conn.execute(
                "SELECT * FROM agents WHERE agent_id=?", (aid,)
            ).fetchone()
            if not agent_row:
                print(f"  [跳过] {aid} 不存在")
                continue

            old_score = agent_row["view_score"] or 0
            old_pos = agent_row["position"] or 0
            old_avg_cost = agent_row["avg_cost"] or 0

            # 计算新评分 + 逻辑分
            logic_score_dict = {}
            new_score, reasons_text, delta = compute_agent_score(aid, market, logic_scores=logic_score_dict)

            # 惯性：不轻易大幅跳变（最多跳 ±20）
            score_diff = new_score - old_score
            if score_diff > 20:
                new_score = old_score + 20
            elif score_diff < -20:
                new_score = old_score - 20

            # 计算仓位变化（基于评分和 risk_params）
            risk_params = json.loads(agent_row["risk_params"]) if agent_row["risk_params"] else {}
            max_pos = risk_params.get("max_position", 100)
            pos_change = int(new_score / 100 * max_pos * 0.3)  # 每次最大变动 30% 仓位

            # 检查止损止盈
            stop_loss_pct = risk_params.get("stop_loss_pct", 0.08)
            take_profit_pct = risk_params.get("take_profit_pct", 0.15)
            pnl_unreal = 0

            # 估算未实现盈亏
            if old_avg_cost > 0 and old_pos != 0:
                if old_pos > 0:  # 多头
                    pnl_unreal = (latest["close"] - old_avg_cost) * old_pos / old_avg_cost
                else:  # 空头
                    pnl_unreal = (old_avg_cost - latest["close"]) * abs(old_pos) / old_avg_cost

            # 止损止盈检查
            stop_triggered = False
            tp_triggered = False
            if pnl_unreal != 0 and old_avg_cost > 0:
                pnl_pct = pnl_unreal / (abs(old_pos) * old_avg_cost) if old_pos != 0 else 0

                if old_pos > 0 and pnl_pct < -stop_loss_pct:
                    pos_change = -old_pos  # 全平
                    stop_triggered = True
                    reasons_text += f"; 止损触发（亏损{pnl_pct*100:.1f}%）"
                elif old_pos < 0 and pnl_pct < -stop_loss_pct:
                    pos_change = abs(old_pos)  # 全平
                    stop_triggered = True
                    reasons_text += f"; 止损触发（亏损{pnl_pct*100:.1f}%）"
                elif old_pos > 0 and pnl_pct > take_profit_pct:
                    pos_change = -int(old_pos * 0.5)  # 减半
                    tp_triggered = True
                    reasons_text += f"; 止盈触发（盈利{pnl_pct*100:.1f}%）"
                elif old_pos < 0 and pnl_pct > take_profit_pct:
                    pos_change = int(abs(old_pos) * 0.5)  # 减半
                    tp_triggered = True
                    reasons_text += f"; 止盈触发（盈利{pnl_pct*100:.1f}%）"

            # 计算新仓位（考虑上限）
            new_pos = old_pos + pos_change
            new_pos = max(-max_pos, min(max_pos, new_pos))
            # 如果持仓为 0，重置均价
            new_avg_cost = old_avg_cost if new_pos != 0 else 0

            # 检查是否需要 LLM 审核（score 变化 > 20 或触发止损止盈）
            needs_review = abs(new_score - old_score) > 20 or stop_triggered or tp_triggered

            # 写入 agents 表
            conn.execute("""
                UPDATE agents SET
                    view_score=?, view_text=?, position=?, avg_cost=?,
                    needs_review=?, updated_at=datetime('now','localtime'),
                    pnl_unreal=?
                WHERE agent_id=?
            """, (new_score, reasons_text, new_pos, new_avg_cost,
                  1 if needs_review else 0, pnl_unreal, aid))

            # 写入 agent_history
            conn.execute("""
                INSERT INTO agent_history (agent_id, date, position, avg_cost, view_score,
                    view_text, pnl_unreal, close)
                VALUES (?,?,?,?,?,?,?,?)
            """, (aid, today, new_pos, new_avg_cost, new_score,
                  reasons_text, pnl_unreal, latest["close"]))

            # 写入 agent_action_log
            conn.execute("""
                INSERT INTO agent_action_log (agent_id, date, prev_position, new_position, delta,
                    prev_view_score, new_view_score, trigger, reasoning, close_price, pnl_unreal)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """, (aid, today, old_pos, new_pos, new_pos - old_pos,
                  old_score, new_score, "rule_engine", reasons_text,
                  latest["close"], pnl_unreal))

            # 写入 agent_view_log
            conn.execute("""
                INSERT INTO agent_view_log (agent_id, date, view_score, view_text,
                    daily_reasoning, key_indicators, daily_actions)
                VALUES (?,?,?,?,?,?,?)
            """, (aid, today, new_score, reasons_text,
                  json.dumps({
                      "daily_chg_pct": market["daily_chg_pct"],
                      "five_day_chg_pct": market["five_day_chg_pct"],
                      "inventory": inv["inventory"] if inv_list else 0,
                      "inv_trend": inv["change"] if inv_list else 0,
                      "volume": latest["volume"],
                      "oi": latest["position"],
                  }, ensure_ascii=False),
                  json.dumps({
                      "daily_chg": f"{market['daily_chg_pct']:+.2f}%",
                      "oi_trend": f"{(latest['position'] / market['oi_3d'][1] - 1)*100:+.1f}%" if len(market['oi_3d']) > 1 and market['oi_3d'][1] > 0 else "N/A",
                  }, ensure_ascii=False),
                  json.dumps({
                      "old_score": old_score,
                      "new_score": new_score,
                      "old_position": old_pos,
                      "new_position": new_pos,
                      "trigger": "stop_loss" if stop_triggered else "take_profit" if tp_triggered else "rule_update",
                  }, ensure_ascii=False)))

            # 输出结果
            trigger_info = ""
            if stop_triggered:
                trigger_info = " [⚠️止损]"
            elif tp_triggered:
                trigger_info = " [✅止盈]"
            elif needs_review:
                trigger_info = " [🔍待审核]"

            print(f"  {aid:12s} | score: {int(old_score):4d} → {int(new_score):4d} | "
                  f"pos: {old_pos:5d} → {new_pos:5d} | PnL: {pnl_unreal:>+10.0f}{trigger_info}")

            update_count += 1
            if needs_review:
                review_count += 1
            
            # 收集基础数据用于博弈因子
            base_scores_dict[aid] = new_score
            deltas_dict[aid] = new_pos - old_pos
            texts_dict[aid] = reasons_text
            agents_dict[aid] = dict(agent_row)
            risk_p = json.loads(agent_row["risk_params"]) if agent_row["risk_params"] else {}
            capital_dict[aid] = risk_p.get("capital", 5000)
            
            # 收集逻辑评分（汇总所有agent对同一逻辑的贡献）
            if logic_score_dict:
                logic_scores_list.append({"agent_id": aid, "date": today, **logic_score_dict})

        except Exception as e:
            print(f"  [错误] {aid}: {e}")
            import traceback
            traceback.print_exc()

    # ── Step 3: 博弈因子 Layer 1&2&3 ──
    print(f"\n[Step 3] 计算博弈因子...")
    apply_game_factors(base_scores_dict, deltas_dict, texts_dict, agents_dict, capital_dict, market, today, conn, agent_ids)

    # ── Step 3.5: 写入逻辑评分到 DB ──
    print(f"\n[Step 3.5] 写入逻辑评分...")
    if logic_scores_list:
        # 创建 logic_scores 表（如果不存在）
        conn.execute("""
            CREATE TABLE IF NOT EXISTS logic_scores (
                date        TEXT    NOT NULL,
                logic_name  TEXT    NOT NULL,
                avg_score   REAL    NOT NULL,
                min_score   REAL    NOT NULL,
                max_score   REAL    NOT NULL,
                agent_count INTEGER DEFAULT 0,
                PRIMARY KEY (date, logic_name)
            )
        """)
        conn.commit()
        # 汇总当日各逻辑维度的均值
        logic_names = ["supply_risk", "demand_drop", "inventory", "macro_policy", "cost_support", "sentiment", "basis_trade"]
        for ln in logic_names:
            vals = [ls.get(ln, 0) for ls in logic_scores_list if ln in ls]
            if vals:
                avg = sum(vals) / len(vals)
                mn = min(vals)
                mx = max(vals)
                conn.execute("""
                    INSERT OR REPLACE INTO logic_scores (date, logic_name, avg_score, min_score, max_score, agent_count)
                    VALUES (?,?,?,?,?,?)
                """, (today, ln, round(avg, 1), round(mn, 1), round(mx, 1), len(vals)))
                print(f"    {ln}: avg={avg:+.1f}  min={mn:+.1f}  max={mx:+.1f}  (n={len(vals)})")
        print(f"  写入完成，共 {len(logic_scores_list)} 条逻辑评分记录")

    # ── Step 3.6: 信号驱动互动 (升贴水/库存/产量 → agent 阵营互动) ──
    print(f"\n[Step 3.6] 信号驱动互动...")
    try:
        from signal_interactions import apply_signal_interactions
        sig_ints = apply_signal_interactions(conn, today, base_scores_dict, agents_dict, market)
    except Exception as e:
        print(f"  [错误] 信号驱动互动: {e}")
        import traceback
        traceback.print_exc()

    # Step 4: 检查市场异常事件
    print(f"\n[Step 4] 检查异常事件...")
    daily_chg = market["daily_chg_pct"]
    if abs(daily_chg) > 3:
        event_type = "大波动" if daily_chg > 0 else "大波动"
        severity = 3 if abs(daily_chg) > 5 else 2
        summary = f"LC0 单日涨跌 {daily_chg:+.2f}%"
        conn.execute("""
            INSERT INTO agent_events (date, event_type, summary, severity)
            VALUES (?,?,?,?)
        """, (today, event_type, summary, severity))
        print(f"  事件: {summary} (severity={severity})")

    conn.commit()
    conn.close()

    # 汇总输出
    print(f"\n{'='*60}")
    print(f"更新完成: {update_count} 个 agent 已更新，{review_count} 个待审核")
    print(f"{'='*60}")


if __name__ == "__main__":
    run_daily_update()
