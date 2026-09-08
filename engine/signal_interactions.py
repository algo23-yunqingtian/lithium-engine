#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
信号驱动互动层 v1.0 — 用升贴水/基本面数据驱动 agent 间互动建模

核心思想：
    现有 agent_interactions 只记录 agent 内部仓位/评分衍生的互动（info_conflict / behavior_follow）。
    本模块引入外部市场信号（升贴水、周度库存、周度产量、月度供需），计算：
      1. 市场综合信号 S ∈ [-1, +1]（多头环境 S>0，空头环境 S<0）
      2. 每个 agent 的信号暴露敏感度（对升贴水/库存/产量/需求的反应系数）
      3. 互动类型：
         - signal_resonance : agent 评分方向与信号方向一致（顺势抱团）
         - signal_conflict  : agent 评分方向与信号方向相反（逆势背离/对抗）
         - 同类背离 agent 之间也会产生弱共振（少数派抱团）

数据来源（lithium.db）：
    lithium_daily_prices : col_idx=2 升贴水低幅, 3 升贴水高幅 (日度)
    lithium_weekly        : col_idx=2 大样本库存总计, 12 产量总计 (周度)
    lithium_monthly       : col_idx=1 产量总计 (月度, 辅助)
    prices                : 日K（基准日/价格动量）
    agent_view_log        : agent 当日 base_score（方向）

用法：
    在 agent_daily_update.py 的 run_daily_update() 中调用:
        from signal_interactions import apply_signal_interactions
        apply_signal_interactions(conn, today, base_scores_dict, agents_dict, market)
    独立回填:
        python signal_interactions.py --backfill
"""

import json
import sqlite3
import sys
import os
from datetime import datetime, timedelta

DB_PATH = os.environ.get("LITHIUM_DB", "/home/ubuntu/lithium_calendar/lithium.db")

AGENT_NAMES = {
    "smelter": "冶炼厂", "merchant": "贸易商", "institution": "机构",
    "arbitrage": "套利商", "retail": "散户", "resource": "矿商", "policy": "政策",
}

# ── Agent 信号暴露矩阵 ─────────────────────────────────────────────
# 每个 agent 对四类信号的敏感度 (0~1) 与方向偏好 (+1 利多敏感, -1 利空敏感)
# direction: 信号走强(升贴水转升/库存去化/产量下降/需求上行) 对该 agent 的含义
SIGNAL_EXPOSURE = {
    #       升贴水    库存      产量      需求      (权重/方向)
    #       敏感度    敏感度    敏感度    敏感度
    "smelter":    {"premium": (0.7, +1), "inventory": (0.6, +1), "output": (0.5, +1), "demand": (0.4, +1)},
    "merchant":   {"premium": (1.0, +1), "inventory": (0.8, +1), "output": (0.3, -1), "demand": (0.6, +1)},
    "institution": {"premium": (0.5, +1), "inventory": (0.9, +1), "output": (0.7, +1), "demand": (0.8, +1)},
    "arbitrage":  {"premium": (1.0, +1), "inventory": (0.5, +1), "output": (0.2, +1), "demand": (0.2, +1)},
    "retail":     {"premium": (0.3, +1), "inventory": (0.2, +1), "output": (0.1, +1), "demand": (0.3, +1),
                   "price_momentum": (1.0, +1)},   # 散户主导信号=价格动量(情绪)
    "resource":   {"premium": (0.4, +1), "inventory": (0.6, +1), "output": (0.9, +1), "demand": (0.3, +1)},
    "policy":     {"premium": (0.1, +1), "inventory": (0.3, +1), "output": (0.2, +1), "demand": (0.2, +1)},
}

# 输出强度阈值：信号强度低于此值不生成互动
MAGNITUDE_THRESHOLD = 3.0

# 每个 agent 的"主导信号"——该 agent 最关注的基本面维度
# (max exposure 的信号; 用于划分阵营)
def dominant_signal(agent_id):
    exp = SIGNAL_EXPOSURE.get(agent_id, {})
    if not exp:
        return "premium"
    best, best_s = None, -1
    for name, (sens, _) in exp.items():
        if sens > best_s:
            best, best_s = name, sens
    return best


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# ── 信号提取 ────────────────────────────────────────────────────────

def fetch_premium_series(conn, days=40):
    """升贴水序列: 取低幅/高幅中间值, 返回 [(date, mid), ...]"""
    rows = conn.execute("""
        SELECT date,
               MAX(CASE WHEN col_idx=2 THEN value END) AS lo,
               MAX(CASE WHEN col_idx=3 THEN value END) AS hi
        FROM lithium_daily_prices
        WHERE col_idx IN (2,3)
        GROUP BY date ORDER BY date DESC LIMIT ?
    """, (days,)).fetchall()
    series = []
    for r in reversed(rows):
        if r["lo"] is not None and r["hi"] is not None:
            series.append((r["date"], (r["lo"] + r["hi"]) / 2.0))
    return series


def fetch_weekly_series(conn, col_idx, days=12):
    """周度序列 (col_idx: 2=库存总计, 12=产量总计)"""
    rows = conn.execute("""
        SELECT date, value FROM lithium_weekly
        WHERE col_idx=? ORDER BY date DESC LIMIT ?
    """, (col_idx, days)).fetchall()
    return list(reversed([(r["date"], r["value"]) for r in rows]))


def fetch_monthly_series(conn, col_idx, months=6):
    """月度序列 (col_idx: 1=产量总计)"""
    rows = conn.execute("""
        SELECT date, value FROM lithium_monthly
        WHERE col_idx=? ORDER BY date DESC LIMIT ?
    """, (col_idx, months)).fetchall()
    return list(reversed([(r["date"], r["value"]) for r in rows]))


def latest_price_info(conn, today):
    """当日价格与5日动量"""
    row = conn.execute(
        "SELECT date, close FROM prices WHERE date<=? ORDER BY date DESC LIMIT 5",
        (today,)
    ).fetchall()
    if not row:
        return None, None
    latest = row[0]["close"]
    base = row[-1]["close"] if len(row) >= 5 else row[0]["close"]
    mom = (latest / base - 1) * 100 if base else 0.0
    return latest, mom


def compute_signals(conn, today):
    """
    计算市场综合信号。
    返回 dict: {signal_name: {value, change, strength, direction}}
    其中 direction=+1 表示信号指向多头(利多), -1 空头(利空), 0 中性
    strength 为 0~1 归一化强度。
    """
    signals = {}

    # ── 1. 升贴水信号 ──
    prem = fetch_premium_series(conn, 40)
    if len(prem) >= 5:
        cur = prem[-1][1]                      # 当前升贴水中值 (元/吨)
        base = prem[-6][1] if len(prem) >= 6 else prem[0][1]
        chg = cur - base                        # 20日变化
        # 归一化: 升贴水范围约 -3000 ~ +2000, 映射到 -1~+1 (贴水-3000→-1, 升水+2000→+1)
        level_norm = max(-1.0, min(1.0, cur / 2500.0))
        chg_norm = max(-1.0, min(1.0, chg / 1500.0))
        strength = min(1.0, abs(level_norm) * 0.6 + abs(chg_norm) * 0.4)
        direction = +1 if (level_norm + chg_norm) > 0.05 else (-1 if (level_norm + chg_norm) < -0.05 else 0)
        signals["premium"] = {
            "name": "升贴水", "value": round(cur, 0), "change": round(chg, 0),
            "strength": round(strength, 3), "direction": direction,
            "detail": f"升贴水中值{cur:+.0f}元/吨, 20日{chg:+.0f}",
        }
    else:
        signals["premium"] = {"name": "升贴水", "value": None, "change": None,
                              "strength": 0.0, "direction": 0, "detail": "数据不足"}

    # ── 2. 周度库存信号 (去库=利多) ──
    inv = fetch_weekly_series(conn, 2, 12)
    if len(inv) >= 2:
        cur_inv = inv[-1][1]
        # 用最近3期平均变化估算周环比 (库存以"周"为单位)
        if len(inv) >= 4:
            recent = inv[-4:]
            avg_chg = (recent[-1][1] - recent[0][1]) / (len(recent) - 1)
        else:
            avg_chg = inv[-1][1] - inv[-2][1]
        chg_pct = avg_chg / cur_inv * 100 if cur_inv else 0.0   # 周环比%
        # 归一化: 周环比 ±3% 映射到 ∓1 (去库-3%→+1 利多, 累库+3%→-1)
        chg_norm = max(-1.0, min(1.0, -chg_pct / 3.0))
        strength = abs(chg_norm)
        direction = +1 if chg_norm > 0.05 else (-1 if chg_norm < -0.05 else 0)
        signals["inventory"] = {
            "name": "库存", "value": round(cur_inv, 0), "change": round(avg_chg, 0),
            "strength": round(strength, 3), "direction": direction,
            "detail": f"库存{cur_inv:,.0f}吨, 周均变化{avg_chg:+,.0f}吨({chg_pct:+.2f}%)",
        }
    else:
        signals["inventory"] = {"name": "库存", "value": None, "change": None,
                                "strength": 0.0, "direction": 0, "detail": "数据不足"}

    # ── 3. 周度产量信号 (减产=利多) ──
    out = fetch_weekly_series(conn, 12, 12)
    if len(out) >= 2:
        cur_out = out[-1][1]
        if len(out) >= 4:
            recent = out[-4:]
            avg_chg = (recent[-1][1] - recent[0][1]) / (len(recent) - 1)
        else:
            avg_chg = out[-1][1] - out[-2][1]
        chg_pct = avg_chg / cur_out * 100 if cur_out else 0.0
        chg_norm = max(-1.0, min(1.0, -chg_pct / 5.0))   # 减产5%→+1, 增产5%→-1
        strength = abs(chg_norm)
        direction = +1 if chg_norm > 0.05 else (-1 if chg_norm < -0.05 else 0)
        signals["output"] = {
            "name": "产量", "value": round(cur_out, 0), "change": round(avg_chg, 0),
            "strength": round(strength, 3), "direction": direction,
            "detail": f"周产{cur_out:,.0f}吨, 周均变化{avg_chg:+,.0f}吨({chg_pct:+.2f}%)",
        }
    else:
        signals["output"] = {"name": "产量", "value": None, "change": None,
                             "strength": 0.0, "direction": 0, "detail": "数据不足"}

    # ── 4. 月度供需信号 (产量同比/环比, 需求用铁锂产量) ──
    m_out = fetch_monthly_series(conn, 1, 6)
    if len(m_out) >= 2:
        m_chg_pct = (m_out[-1][1] / m_out[-2][1] - 1) * 100 if m_out[-2][1] else 0.0
        m_norm = max(-1.0, min(1.0, -m_chg_pct / 8.0))  # 月产-8%→+1
        signals["monthly_output"] = {
            "name": "月度产量", "value": round(m_out[-1][1], 0),
            "change": round(m_chg_pct, 2),
            "strength": round(abs(m_norm), 3), "direction": +1 if m_norm > 0.05 else (-1 if m_norm < -0.05 else 0),
            "detail": f"月产{m_out[-1][1]:,.0f}吨, 环比{m_chg_pct:+.2f}%",
        }
    else:
        signals["monthly_output"] = {"name": "月度产量", "value": None, "change": None,
                                     "strength": 0.0, "direction": 0, "detail": "数据不足"}

    # ── 5. 价格动量信号 ──
    latest, mom = latest_price_info(conn, today)
    if latest:
        mom_norm = max(-1.0, min(1.0, mom / 5.0))  # 5日±5%→±1
        signals["price_momentum"] = {
            "name": "价格动量", "value": round(latest, 0), "change": round(mom, 2),
            "strength": round(abs(mom_norm), 3), "direction": +1 if mom_norm > 0.05 else (-1 if mom_norm < -0.05 else 0),
            "detail": f"收盘{latest:,.0f}, 5日{mom:+.2f}%",
        }
    else:
        signals["price_momentum"] = {"name": "价格动量", "value": None, "change": None,
                                     "strength": 0.0, "direction": 0, "detail": "无价格"}

    # ── 综合信号 ──
    weighted_dir = 0.0
    weight_sum = 0.0
    for name, sig in signals.items():
        w = sig["strength"]
        weighted_dir += sig["direction"] * w
        weight_sum += w
    composite = weighted_dir / weight_sum if weight_sum > 0 else 0.0
    signals["composite"] = {
        "name": "综合", "value": round(composite, 3), "change": None,
        "strength": round(min(1.0, abs(composite)), 3),
        "direction": +1 if composite > 0.1 else (-1 if composite < -0.1 else 0),
        "detail": f"综合信号 {composite:+.3f} (权重和 {weight_sum:.2f})",
    }

    return signals


# ── 信号 → Agent 反应 ──────────────────────────────────────────────

def agent_signal_bias(agent_id, signals):
    """
    计算 agent 对当前市场信号的感知方向与强度。
    返回: (bias, strength)  bias ∈ [-1, +1], strength ∈ [0, 1]
    逻辑: 对每个信号, 用 agent 敏感度加权信号方向; 综合后归一化。
    """
    exposure = SIGNAL_EXPOSURE.get(agent_id, {})
    total = 0.0
    weight = 0.0
    for sig_name, sig in signals.items():
        if sig_name == "composite":
            continue
        if sig_name not in exposure:
            continue
        sens, pref_dir = exposure[sig_name]
        d = sig.get("direction", 0)
        s = sig.get("strength", 0)
        if d == 0 or s == 0:
            continue
        # 信号方向 × agent 偏好方向 × 敏感度 × 强度
        total += d * pref_dir * sens * s
        weight += sens * s
    if weight == 0:
        return 0.0, 0.0
    bias = total / weight  # -1~+1
    return round(bias, 3), round(min(1.0, abs(bias)), 3)


# ── 互动生成与入库 ─────────────────────────────────────────────────

def apply_signal_interactions(conn, today, base_scores_dict, agents_dict, market=None, verbose=True):
    """
    主入口: 基于升贴水/基本面信号 + agent 评分方向生成互动, 写入 agent_interactions。

    参数:
        conn: SQLite 连接
        today: 日期字符串
        base_scores_dict: {agent_id: base_score} (规则引擎原始评分, 未加博弈修正)
        agents_dict: {agent_id: {capital: float, risk_params: dict}} (可空)
        market: 现有 market dict (可空, 仅用于取价格/库存辅助)

    互动规则 (v1.1 阵营模型):
        每个 agent 有:
          - sig_dir : 其"主导信号"(最敏感维度) 当前方向 → 信号立场
          - score_dir: 当日评分方向 (规则引擎观点)
        阵营划分:
          - 看多阵营 (sig_dir=+1): 主导信号指向利多
          - 看空阵营 (sig_dir=-1): 主导信号指向利空
        互动:
          - 同阵营之间   → signal_resonance (基本面共识)
          - 跨阵营之间   → signal_conflict  (基本面维度分歧)
          - 知行背离放大: score_dir != sig_dir 的 agent 是"矛盾体",
            其参与的任何互动幅度 ×1.3 (内部矛盾 → 对外冲突更剧烈)
        magnitude = 信号强度 × 主导敏感度 × 缩放系数
    """
    if not base_scores_dict:
        return []

    signals = compute_signals(conn, today)
    comp_dir = signals["composite"]["direction"]
    comp_strength = signals["composite"]["strength"]

    # 每个 agent: (sig_dir, score_dir, bias_strength, capital)
    agent_info = {}
    total_cap = 0
    for aid, score in base_scores_dict.items():
        if score == 0:
            agent_info[aid] = None
            continue
        dom = dominant_signal(aid)
        sig = signals.get(dom)
        if sig is None or sig.get("direction", 0) == 0 or sig.get("strength", 0) < 0.1:
            # 主导信号中性 → 退化为综合信号
            sig_dir = comp_dir
            dom_str = comp_strength
        else:
            exp = SIGNAL_EXPOSURE[aid][dom]
            sig_dir = sig["direction"] * exp[1]   # 信号方向 × agent 偏好方向
            dom_str = sig["strength"]
        if sig_dir == 0:
            agent_info[aid] = None
            continue
        capital = agents_dict.get(aid, {}).get("capital", 1.0) if agents_dict else 1.0
        total_cap += capital
        agent_info[aid] = {
            "sig_dir": +1 if sig_dir > 0 else -1,
            "score_dir": +1 if score > 0 else -1,
            "dom_signal": dom,
            "dom_strength": dom_str,
            "capital": capital,
        }

    active = {aid: info for aid, info in agent_info.items() if info is not None}
    if len(active) < 2:
        if verbose:
            print(f"  [信号互动] 活跃 agent <2, 跳过")
        return []

    # 阵营划分
    bull = {aid: info for aid, info in active.items() if info["sig_dir"] > 0}
    bear = {aid: info for aid, info in active.items() if info["sig_dir"] < 0}

    interactions = []

    def _mag(info1, info2, base):
        """幅度: base × 双方主导信号强度均值 × 知行背离放大
        (base: 共振=10, 冲突=14; 强信号如去库1.0 → 10~14, 弱信号0.2 → 6~8)"""
        avg_s = (info1["dom_strength"] + info2["dom_strength"]) / 2.0
        m = base * (0.4 + 0.6 * avg_s)
        if info1["score_dir"] != info1["sig_dir"]:
            m *= 1.3
        if info2["score_dir"] != info2["sig_dir"]:
            m *= 1.3
        return round(min(40.0, max(MAGNITUDE_THRESHOLD, m)), 1)

    def _desc(a1, i1, a2, i2):
        d1 = "多" if i1["sig_dir"] > 0 else "空"
        d2 = "多" if i2["sig_dir"] > 0 else "空"
        s1 = signals[i1["dom_signal"]]["name"]
        s2 = signals[i2["dom_signal"]]["name"]
        if i1["sig_dir"] == i2["sig_dir"]:
            return f"{AGENT_NAMES[a1]}与{AGENT_NAMES[a2]}同看{d1}({s1}→{s2})"
        return f"{AGENT_NAMES[a1]}看{d1}({s1}) vs {AGENT_NAMES[a2]}看{d2}({s2})"

    # 1. 同阵营共振 (bull×bull, bear×bear)
    for camp in (bull, bear):
        items = list(camp.items())
        for i, a1 in enumerate(items):
            for a2 in items[i+1:]:
                aid1, info1 = a1
                aid2, info2 = a2
                mag = _mag(info1, info2, 10.0)
                interactions.append((aid1, aid2, "signal_resonance", mag,
                                     _desc(aid1, info1, aid2, info2)))

    # 2. 跨阵营冲突 (bull × bear)
    for aid1, info1 in bull.items():
        for aid2, info2 in bear.items():
            mag = _mag(info1, info2, 14.0)
            interactions.append((aid1, aid2, "signal_conflict", mag,
                                 _desc(aid1, info1, aid2, info2)))

    # 写入 DB
    if interactions:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS agent_interactions (
                date              TEXT NOT NULL,
                from_agent        TEXT NOT NULL,
                to_agent          TEXT NOT NULL,
                influence_type    TEXT NOT NULL,
                magnitude         REAL NOT NULL,
                PRIMARY KEY (date, from_agent, to_agent, influence_type)
            )
        """)
        for from_a, to_a, itype, mag, desc in interactions:
            conn.execute("""
                INSERT OR REPLACE INTO agent_interactions (date, from_agent, to_agent, influence_type, magnitude)
                VALUES (?,?,?,?,?)
            """, (today, from_a, to_a, itype, mag))

        # 记录信号快照
        conn.execute("""
            CREATE TABLE IF NOT EXISTS market_signals (
                date      TEXT NOT NULL,
                signal    TEXT NOT NULL,
                value     REAL,
                change    REAL,
                strength  REAL,
                direction INTEGER,
                detail    TEXT,
                PRIMARY KEY (date, signal)
            )
        """)
        for sig_name, sig in signals.items():
            conn.execute("""
                INSERT OR REPLACE INTO market_signals (date, signal, value, change, strength, direction, detail)
                VALUES (?,?,?,?,?,?,?)
            """, (today, sig_name, sig.get("value"), sig.get("change"),
                  sig.get("strength", 0), sig.get("direction", 0), sig.get("detail", "")))

    if verbose:
        print(f"\n[信号驱动互动] 综合信号 dir={comp_dir:+d} strength={comp_strength:.2f}")
        for s_name in ["premium", "inventory", "output", "price_momentum"]:
            s = signals.get(s_name)
            if s:
                print(f"  {s['name']:5s}: {s['detail']}")
        print(f"  多方阵营: {[AGENT_NAMES.get(a,a) for a in bull]}  空方阵营: {[AGENT_NAMES.get(a,a) for a in bear]}")
        print(f"  生成 {len(interactions)} 条信号互动")

    return interactions


# ── 独立运行: 回填历史 ─────────────────────────────────────────────

def backfill_history(conn, start_date="2026-07-01", end_date=None):
    """用历史价格日期逐日回填信号互动 (需 agent_view_log 有历史评分)"""
    if end_date is None:
        end_date = datetime.now().strftime("%Y-%m-%d")

    # 取历史价格日期序列
    dates = [r[0] for r in conn.execute(
        "SELECT DISTINCT date FROM prices WHERE date BETWEEN ? AND ? ORDER BY date",
        (start_date, end_date)
    ).fetchall()]
    print(f"回填日期范围: {start_date} ~ {end_date}, 共 {len(dates)} 个交易日")

    created = 0
    for d in dates:
        # 当日各 agent 的评分 (用 base_score; 若 agent_game_factors 有则用 base_score 列)
        rows = conn.execute(
            "SELECT agent_id, base_score FROM agent_game_factors WHERE date=? AND base_score != 0",
            (d,)
        ).fetchall()
        if not rows:
            rows = conn.execute(
                "SELECT agent_id, view_score FROM agent_view_log WHERE date=? AND view_score != 0",
                (d,)
            ).fetchall()
        if not rows:
            continue
        scores = {r[0]: r[1] for r in rows}
        ints = apply_signal_interactions(conn, d, scores, {}, None, verbose=False)
        created += len(ints)
    conn.commit()
    print(f"回填完成, 共生成 {created} 条信号互动")
    return created


def main():
    conn = get_db()
    if "--backfill" in sys.argv:
        start = sys.argv[sys.argv.index("--backfill") + 1] if len(sys.argv) > sys.argv.index("--backfill") + 1 else "2026-07-01"
        backfill_history(conn, start)
    else:
        # 演示: 用最新一天跑一次
        last = conn.execute("SELECT MAX(date) FROM prices").fetchone()[0]
        rows = conn.execute(
            "SELECT agent_id, base_score FROM agent_game_factors WHERE date=? AND base_score != 0",
            (last,)
        ).fetchall()
        if not rows:
            rows = conn.execute(
                "SELECT agent_id, view_score FROM agent_view_log WHERE date=? AND view_score != 0",
                (last,)
            ).fetchall()
        scores = {r[0]: r[1] for r in rows}
        print(f"最新日期: {last}, 活跃 agent: {list(scores.keys())}")
        apply_signal_interactions(conn, last, scores, {}, None, verbose=True)
        conn.commit()
    conn.close()


if __name__ == "__main__":
    main()
