#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
回推碳酸锂 agent 历史互动数据

从 2026-07-01 开始，逐日运行 agent 评分引擎 + 博弈因子计算，
生成 agent_history、agent_interactions、logic_scores 等表的历史数据。

数据来源: prices (LC0) + contract_prices (月份合约) + inventory_history (库存)
退化处理: 
  - spreads 缺失日期: 用 0 代替（中性）
  - notes 缺失: 情绪归零（中性）

使用方法:
  cd /home/ubuntu/lithium_calendar
  /home/ubuntu/zinc_venv/bin/python backfill_agent_history.py [start_date] [end_date]
  
  默认: 2026-07-01 ~ 2026-07-29
"""

import sqlite3
import json
import sys
from datetime import datetime, timedelta
from collections import defaultdict

# 导入评分引擎
sys.path.insert(0, '/home/ubuntu/.hermes/scripts/lithium_agents')
from agent_daily_update import (
    get_db, fetch_latest_price, fetch_contract_prices, fetch_spreads,
    fetch_inventory, fetch_notes_data, compute_agent_score,
    clamp,
)
from game_factors import apply_game_factors

DB_PATH = "/home/ubuntu/lithium_calendar/lithium.db"

AGENT_IDS = ["smelter", "resource", "merchant", "institution", "arbitrage", "retail", "policy"]


def get_dates_in_range(start_date, end_date):
    """获取范围内的所有交易日（从 prices 表读取）"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    params = (start_date, end_date)
    rows = conn.execute("""
        SELECT DISTINCT date FROM prices 
        WHERE date >= ? AND date <= ?
        ORDER BY date
    """, params).fetchall()
    conn.close()
    return [r['date'] for r in rows]


def run_backfill(start_date, end_date):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    
    # 1. 确保所有需要的表存在
    ensure_tables(conn)
    
    # 2. 获取交易日期列表
    dates = get_dates_in_range(start_date, end_date)
    if not dates:
        print(f"错误: {start_date} ~ {end_date} 期间无交易数据")
        conn.close()
        return
    
    print(f"回推日期范围: {dates[0]} ~ {dates[-1]} ({len(dates)} 个交易日)")
    print(f"{'='*70}")
    
    # 3. 逐日运行
    prev_agent_states = {}
    
    for i, date in enumerate(dates):
        print(f"\n{'='*70}")
        print(f"[{i+1}/{len(dates)}] 回推日期: {date}")
        print(f"{'-'*70}")
        
        try:
            result = run_day(conn, date, prev_agent_states)
            if result:
                prev_agent_states = result['agent_states']
        except Exception as e:
            print(f"  [错误] {date}: {e}")
            import traceback
            traceback.print_exc()
    
    # 4. 统计
    print(f"\n{'='*70}")
    print("回推完成，数据统计:")
    
    tables_stats = [
        'agent_history', 'agent_action_log', 'agent_view_log',
        'agent_game_factors', 'logic_scores', 'agent_interactions'
    ]
    for t in tables_stats:
        cnt = conn.execute(f"SELECT COUNT(*) FROM {t} WHERE date >= '{start_date}' AND date <= '{end_date}'").fetchone()[0]
        print(f"  {t:25s}  {cnt:>5d} 条 ({start_date} ~ {end_date})")
    
    conn.commit()
    conn.close()
    print(f"\n{'='*70}")


def ensure_tables(conn):
    """确保所有需要的表存在"""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS agent_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id TEXT, date TEXT, position INTEGER, avg_cost REAL,
            view_score INTEGER, view_text TEXT, pnl_unreal REAL, close REAL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS agent_action_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id TEXT, date TEXT, prev_position INTEGER, new_position INTEGER,
            delta INTEGER, prev_view_score INTEGER, new_view_score INTEGER,
            trigger TEXT, reasoning TEXT, close_price REAL, pnl_unreal REAL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS agent_view_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id TEXT, date TEXT, view_score INTEGER, view_text TEXT,
            daily_reasoning TEXT, key_indicators TEXT, daily_actions TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS agent_game_factors (
            date TEXT, agent_id TEXT, base_score INTEGER,
            info_transfer REAL, behavioral_impact REAL,
            total_correction REAL, corrected_score INTEGER, factors_detail TEXT,
            PRIMARY KEY (date, agent_id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS logic_scores (
            date TEXT NOT NULL, logic_name TEXT NOT NULL,
            avg_score REAL NOT NULL, min_score REAL NOT NULL,
            max_score REAL NOT NULL, agent_count INTEGER DEFAULT 0,
            PRIMARY KEY (date, logic_name)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS agent_interactions (
            date TEXT NOT NULL, from_agent TEXT NOT NULL,
            to_agent TEXT NOT NULL, influence_type TEXT NOT NULL,
            magnitude REAL NOT NULL,
            PRIMARY KEY (date, from_agent, to_agent, influence_type)
        )
    """)
    conn.commit()


def run_day(conn, date, prev_agent_states):
    """回推某一天的数据"""
    
    # Step 1: 构建 market 数据
    market = build_market(conn, date)
    if market is None:
        return None
    
    # Step 2: 逐个 agent 评分
    base_scores_dict = {}
    deltas_dict = {}
    texts_dict = {}
    agents_dict = {}
    capital_dict = {}
    logic_scores_list = []
    today_agent_states = {}
    
    for aid in AGENT_IDS:
        try:
            # 获取 agent 当前状态
            agent_row = conn.execute(
                "SELECT * FROM agents WHERE agent_id=?", (aid,)
            ).fetchone()
            
            if not agent_row:
                # 如果 agent 不存在，用默认值初始化
                agent_row = init_default_agent(aid)
            
            old_score = agent_row["view_score"] if agent_row["view_score"] is not None else 0
            old_pos = agent_row["position"] if agent_row["position"] is not None else 0
            old_avg_cost = agent_row["avg_cost"] if agent_row["avg_cost"] is not None else 0
            
            # 计算新评分
            logic_score_dict = {}
            latest = market["latest"]
            new_score, reasons_text, delta = compute_agent_score(
                aid, market, logic_scores=logic_score_dict
            )
            
            # 惯性限制
            score_diff = new_score - old_score
            if score_diff > 20:
                new_score = old_score + 20
            elif score_diff < -20:
                new_score = old_score - 20
            
            # 计算仓位变化
            risk_params = json.loads(agent_row["risk_params"]) if agent_row["risk_params"] else {}
            max_pos = risk_params.get("max_position", 100)
            pos_change = int(new_score / 100 * max_pos * 0.3)
            
            # 止损止盈
            stop_loss_pct = risk_params.get("stop_loss_pct", 0.08)
            take_profit_pct = risk_params.get("take_profit_pct", 0.15)
            pnl_unreal = 0
            stop_triggered = False
            tp_triggered = False
            
            if old_avg_cost > 0 and old_pos != 0:
                if old_pos > 0:
                    pnl_unreal = (latest["close"] - old_avg_cost) * old_pos / old_avg_cost
                else:
                    pnl_unreal = (old_avg_cost - latest["close"]) * abs(old_pos) / old_avg_cost
            
            if pnl_unreal != 0 and old_avg_cost > 0:
                pnl_pct = pnl_unreal / (abs(old_pos) * old_avg_cost) if old_pos != 0 else 0
                if old_pos > 0 and pnl_pct < -stop_loss_pct:
                    pos_change = -old_pos
                    stop_triggered = True
                    reasons_text += f"; 止损触发（亏损{pnl_pct*100:.1f}%）"
                elif old_pos < 0 and pnl_pct < -stop_loss_pct:
                    pos_change = abs(old_pos)
                    stop_triggered = True
                    reasons_text += f"; 止损触发（亏损{pnl_pct*100:.1f}%）"
                elif old_pos > 0 and pnl_pct > take_profit_pct:
                    pos_change = -int(old_pos * 0.5)
                    tp_triggered = True
                    reasons_text += f"; 止盈触发（盈利{pnl_pct*100:.1f}%）"
                elif old_pos < 0 and pnl_pct > take_profit_pct:
                    pos_change = int(abs(old_pos) * 0.5)
                    tp_triggered = True
                    reasons_text += f"; 止盈触发（盈利{pnl_pct*100:.1f}%）"
            
            # 新仓位
            new_pos = old_pos + pos_change
            new_pos = max(-max_pos, min(max_pos, new_pos))
            new_avg_cost = old_avg_cost if new_pos != 0 else 0
            
            needs_review = abs(new_score - old_score) > 20 or stop_triggered or tp_triggered
            
            # 写入 agents 表
            conn.execute("""
                UPDATE agents SET
                    view_score=?, view_text=?, position=?, avg_cost=?,
                    needs_review=?, updated_at=?, pnl_unreal=?
                WHERE agent_id=?
            """, (new_score, reasons_text, new_pos, new_avg_cost,
                  1 if needs_review else 0, date, pnl_unreal, aid))
            
            # 写入 agent_history (INSERT OR REPLACE)
            conn.execute("""
                INSERT OR REPLACE INTO agent_history (agent_id, date, position, avg_cost, view_score,
                    view_text, pnl_unreal, close)
                VALUES (?,?,?,?,?,?,?,?)
            """, (aid, date, new_pos, new_avg_cost, new_score,
                  reasons_text, pnl_unreal, latest["close"]))
            
            # 写入 agent_action_log
            conn.execute("""
                INSERT INTO agent_action_log 
                (agent_id, date, prev_position, new_position, delta,
                 prev_view_score, new_view_score, trigger, reasoning, close_price, pnl_unreal)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """, (aid, date, old_pos, new_pos, new_pos - old_pos,
                  old_score, new_score, "rule_engine", reasons_text,
                  latest["close"], pnl_unreal))
            
            # 写入 agent_view_log
            inv_list = market.get("inventory", [])
            inv = inv_list[0] if inv_list else {"inventory": 0, "change": 0}
            conn.execute("""
                INSERT INTO agent_view_log 
                (agent_id, date, view_score, view_text,
                 daily_reasoning, key_indicators, daily_actions)
                VALUES (?,?,?,?,?,?,?)
            """, (aid, date, new_score, reasons_text,
                  json.dumps({
                      "daily_chg_pct": market["daily_chg_pct"],
                      "five_day_chg_pct": market["five_day_chg_pct"],
                      "inventory": inv["inventory"],
                      "inv_trend": inv["change"],
                      "volume": latest["volume"],
                      "oi": latest["position"],
                  }, ensure_ascii=False),
                  json.dumps({
                      "daily_chg": f"{market['daily_chg_pct']:+.2f}%",
                      "oi_trend": "N/A",
                  }, ensure_ascii=False),
                  json.dumps({
                      "old_score": old_score,
                      "new_score": new_score,
                      "old_position": old_pos,
                      "new_position": new_pos,
                      "trigger": "stop_loss" if stop_triggered else "take_profit" if tp_triggered else "rule_update",
                  }, ensure_ascii=False)))
            
            # 输出
            trigger_info = ""
            if stop_triggered:
                trigger_info = " [⚠️止损]"
            elif tp_triggered:
                trigger_info = " [✅止盈]"
            elif needs_review:
                trigger_info = " [🔍待审核]"
            
            print(f"  {aid:12s} | score: {int(old_score):4d} → {int(new_score):4d} | "
                  f"pos: {old_pos:5d} → {new_pos:5d} | PnL: {pnl_unreal:>+10.0f}{trigger_info}")
            
            # 收集数据用于博弈因子
            base_scores_dict[aid] = new_score
            deltas_dict[aid] = new_pos - old_pos
            texts_dict[aid] = reasons_text
            agents_dict[aid] = dict(agent_row)
            risk_p = json.loads(agent_row["risk_params"]) if agent_row["risk_params"] else {}
            capital_dict[aid] = risk_p.get("capital", 5000)
            
            # 保存 agent 状态供下一天使用
            today_agent_states[aid] = {
                "view_score": new_score,
                "position": new_pos,
                "avg_cost": new_avg_cost,
            }
            
            # 逻辑评分
            if logic_score_dict:
                logic_scores_list.append({"agent_id": aid, "date": date, **logic_score_dict})
            
        except Exception as e:
            print(f"  [错误] {aid}: {e}")
            import traceback
            traceback.print_exc()
    
    if not base_scores_dict:
        return None
    
    # Step 3: 博弈因子
    apply_game_factors(base_scores_dict, deltas_dict, texts_dict, agents_dict, 
                      capital_dict, market, date, conn, AGENT_IDS)
    
    # Step 4: 写入逻辑评分
    logic_names = ["supply_risk", "demand_drop", "inventory", "macro_policy", 
                   "cost_support", "sentiment", "basis_trade"]
    for ln in logic_names:
        vals = [ls.get(ln, 0) for ls in logic_scores_list if ln in ls]
        if vals:
            avg = sum(vals) / len(vals)
            mn = min(vals)
            mx = max(vals)
            conn.execute("""
                INSERT OR REPLACE INTO logic_scores (date, logic_name, avg_score, min_score, max_score, agent_count)
                VALUES (?,?,?,?,?,?)
            """, (date, ln, round(avg, 1), round(mn, 1), round(mx, 1), len(vals)))
    
    conn.commit()
    return {"agent_states": today_agent_states}


def build_market(conn, date):
    """为指定日期构建 market 数据字典"""
    
    # 1. 获取当天及之前几天的价格数据
    # 当天价格
    price_row = conn.execute(
        "SELECT * FROM prices WHERE date=?", (date,)
    ).fetchone()
    
    if not price_row:
        return None
    
    latest = {
        "date": date,
        "close": price_row["close"],
        "volume": price_row["volume"],
        "position": price_row["position"],
    }
    
    # 昨天价格（用于计算日涨跌）
    yesterday_row = conn.execute(
        "SELECT * FROM prices WHERE date < ? ORDER BY date DESC LIMIT 1", (date,)
    ).fetchone()
    
    yesterday = None
    daily_chg_pct = 0.0
    if yesterday_row:
        yesterday = {
            "date": yesterday_row["date"],
            "close": yesterday_row["close"],
        }
        if yesterday_row["close"] > 0:
            daily_chg_pct = (latest["close"] - yesterday_row["close"]) / yesterday_row["close"] * 100
    
    # 5日涨跌
    five_days_ago = conn.execute(
        "SELECT * FROM prices WHERE date <= date(?, '-5 days') ORDER BY date DESC LIMIT 1", (date,)
    ).fetchone()
    five_day_chg_pct = 0.0
    if five_days_ago and five_days_ago["close"] > 0:
        five_day_chg_pct = (latest["close"] - five_days_ago["close"]) / five_days_ago["close"] * 100
    
    # OI 3日趋势
    oi_3d = []
    vol_3d = []
    for r in conn.execute(
        "SELECT position, volume FROM prices WHERE date <= ? ORDER BY date DESC LIMIT 3", (date,)
    ).fetchall():
        oi_3d.append(r["position"])
        vol_3d.append(r["volume"])
    
    # contract_prices
    cp_rows = conn.execute(
        "SELECT contract, close FROM contract_prices WHERE date=? AND contract IN ('LC0','LC2608','LC2609','LC2610','LC2611','LC2612')",
        (date,)
    ).fetchall()
    contract_prices = {}
    for r in cp_rows:
        contract_prices[r["contract"]] = [{"close": r["close"]}]
    
    # spreads (已从 spreads 表扩充)
    sp_rows = conn.execute("SELECT * FROM spreads WHERE date=?", (date,)).fetchall()
    spreads = [dict(r) for r in sp_rows]
    
    # inventory (最近5天)
    inv_rows = conn.execute(
        "SELECT * FROM inventory_history WHERE date <= ? ORDER BY date DESC LIMIT 5", (date,)
    ).fetchall()
    inventory = [dict(r) for r in inv_rows]
    
    # notes (无数据则空)
    notes = []
    
    market = {
        "latest": latest,
        "yesterday": yesterday,
        "daily_chg_pct": daily_chg_pct,
        "daily_dir": "up" if daily_chg_pct > 0 else "down",
        "five_day_chg_pct": five_day_chg_pct,
        "oi_3d": oi_3d,
        "volume_3d": vol_3d,
        "contract_prices": contract_prices,
        "spreads": spreads,
        "inventory": inventory,
        "notes": notes,
    }
    
    return market


def init_default_agent(agent_id):
    """初始化默认 agent 配置"""
    defaults = {
        "smelter": {"max_position": 200, "stop_loss_pct": 0.08, "take_profit_pct": 0.15, "capital": 5000},
        "resource": {"max_position": 150, "stop_loss_pct": 0.10, "take_profit_pct": 0.15, "capital": 5000},
        "merchant": {"max_position": 150, "stop_loss_pct": 0.08, "take_profit_pct": 0.12, "capital": 5000},
        "institution": {"max_position": 200, "stop_loss_pct": 0.10, "take_profit_pct": 0.15, "capital": 10000},
        "arbitrage": {"max_position": 100, "stop_loss_pct": 0.05, "take_profit_pct": 0.10, "capital": 8000},
        "retail": {"max_position": 80, "stop_loss_pct": 0.08, "take_profit_pct": 0.12, "capital": 3000},
        "policy": {"max_position": 100, "stop_loss_pct": 0.10, "take_profit_pct": 0.20, "capital": 10000},
    }
    d = defaults.get(agent_id, {"max_position": 100, "stop_loss_pct": 0.08, "take_profit_pct": 0.15, "capital": 5000})
    return {
        "agent_id": agent_id,
        "view_score": 0,
        "position": 0,
        "avg_cost": 0,
        "risk_params": json.dumps(d),
    }


if __name__ == "__main__":
    start = sys.argv[1] if len(sys.argv) > 1 else "2026-07-01"
    end = sys.argv[2] if len(sys.argv) > 2 else "2026-07-29"
    run_backfill(start, end)
