# -*- coding: utf-8 -*-
"""
碳酸锂多主体博弈 — Agent 框架
Phase 1: 建表 + 初始化7个agent + API路由

设计决策：
- 不使用独立 Blueprint（与 lithium_api 冲突 /api/battlefield）
- 通过 inject_routes(blueprint) 将路由函数注入到已有蓝图中
- 数据流: 规则引擎每日跑 → 写入 agents/history/decisions → 前端 battle
- 用户手动注入 → POST /api/battlefield/custom 写入 custom_data
- LLM批量审核 → POST /api/agents/review (按需触发)
"""

import os
import json
import sqlite3
from datetime import datetime
from flask import request, jsonify

DB_PATH = os.path.join(os.path.dirname(__file__), 'lithium.db')


# ─── 数据库初始化 ─────────────────────────────────────────

def _get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_agent_tables():
    """创建多主体博弈相关表（幂等操作）"""
    conn = _get_db()
    conn.executescript("""
        -- agents: 主体定义 + 实时状态
        CREATE TABLE IF NOT EXISTS agents (
            agent_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            role TEXT NOT NULL,
            capital REAL DEFAULT 10000,
            position INTEGER DEFAULT 0,
            avg_cost REAL DEFAULT 0,
            target_long REAL DEFAULT 0,
            target_short REAL DEFAULT 0,
            stop_loss REAL DEFAULT 0,
            take_profit REAL DEFAULT 0,
            view_score INTEGER DEFAULT 0,
            view_text TEXT,
            needs_review INTEGER DEFAULT 0,
            risk_params TEXT DEFAULT '{}',
            updated_at TEXT DEFAULT (datetime('now','localtime'))
        );

        -- agent_history: 每日快照（甘特图纵轴 + 历史回溯）
        CREATE TABLE IF NOT EXISTS agent_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id TEXT NOT NULL,
            date TEXT NOT NULL,
            position INTEGER,
            avg_cost REAL,
            view_score INTEGER,
            view_text TEXT,
            pnl_unreal REAL,
            close REAL,
            FOREIGN KEY (agent_id) REFERENCES agents(agent_id)
        );
        CREATE INDEX IF NOT EXISTS idx_agent_history_date ON agent_history(agent_id, date);

        -- agent_decisions: LLM/规则决策记录
        CREATE TABLE IF NOT EXISTS agent_decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id TEXT NOT NULL,
            date TEXT NOT NULL,
            trigger TEXT,
            action TEXT,
            delta_position INTEGER,
            reason TEXT,
            model_used TEXT,
            FOREIGN KEY (agent_id) REFERENCES agents(agent_id)
        );
        CREATE INDEX IF NOT EXISTS idx_agent_decisions_date ON agent_decisions(agent_id, date);

        -- agent_events: 市场事件
        CREATE TABLE IF NOT EXISTS agent_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            event_type TEXT,
            summary TEXT,
            severity INTEGER,
            FOREIGN KEY (date, event_type) REFERENCES agent_events(date, event_type)
        );

        -- agent_view_log: 历史观点日志（甘特图数据源）
        CREATE TABLE IF NOT EXISTS agent_view_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id TEXT NOT NULL,
            date TEXT NOT NULL,
            view_score INTEGER,
            view_text TEXT,
            daily_reasoning TEXT,
            key_indicators TEXT,
            daily_actions TEXT,
            FOREIGN KEY (agent_id) REFERENCES agents(agent_id)
        );
        CREATE INDEX IF NOT EXISTS idx_agent_view_log ON agent_view_log(agent_id, date);

        -- agent_custom_data: 用户手动录入非标信息
        CREATE TABLE IF NOT EXISTS agent_custom_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id TEXT,
            date TEXT NOT NULL,
            data_type TEXT NOT NULL,
            source TEXT,
            content TEXT NOT NULL,
            score_impact INTEGER,
            tags TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now','localtime'))
        );
        CREATE INDEX IF NOT EXISTS idx_agent_custom_date ON agent_custom_data(date);

        -- agent_action_log: 完整决策流水（审计 + 回测）
        CREATE TABLE IF NOT EXISTS agent_action_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id TEXT NOT NULL,
            date TEXT NOT NULL,
            prev_position INTEGER,
            new_position INTEGER,
            delta INTEGER,
            prev_view_score INTEGER,
            new_view_score INTEGER,
            trigger TEXT,
            reasoning TEXT,
            close_price REAL,
            pnl_unreal REAL,
            FOREIGN KEY (agent_id) REFERENCES agents(agent_id)
        );
        CREATE INDEX IF NOT EXISTS idx_agent_action_log ON agent_action_log(agent_id, date);
    """)
    conn.commit()
    conn.close()
    print('[agents] 表结构初始化完成')


# ─── Agent 默认配置 ─────────────────────────────────────

DEFAULT_AGENTS = [
    {'agent_id': 'smelter',     'name': '冶炼厂',         'role': 'smelter',      'capital': 5000,
     'risk_params': json.dumps({'max_position': 200, 'stop_loss_pct': 0.08, 'take_profit_pct': 0.15, 'profit_threshold_reduce': -500, 'profit_threshold_add': 1000})},
    {'agent_id': 'merchant',    'name': '贸易商/加工商',   'role': 'merchant',     'capital': 3000,
     'risk_params': json.dumps({'max_position': 150, 'stop_loss_pct': 0.06, 'take_profit_pct': 0.12, 'basis_sensitivity': 0.35, 'term_structure_sensitivity': 0.25})},
    {'agent_id': 'institution', 'name': '纯投机构',        'role': 'institution',  'capital': 8000,
     'risk_params': json.dumps({'max_position': 300, 'stop_loss_pct': 0.10, 'take_profit_pct': 0.20, 'momentum_window': 20, 'trend_follow': True})},
    {'agent_id': 'arbitrage',   'name': '套利商',          'role': 'arbitrage',    'capital': 4000,
     'risk_params': json.dumps({'max_position': 100, 'stop_loss_pct': 0.03, 'take_profit_pct': 0.08, 'mean_reversion': True, 'spread_threshold': 0.02})},
    {'agent_id': 'retail',      'name': '散户/游资',       'role': 'retail',       'capital': 1000,
     'risk_params': json.dumps({'max_position': 50, 'stop_loss_pct': 0.15, 'take_profit_pct': 0.25, 'chase_trend': True, 'news_sensitivity': 0.30})},
    {'agent_id': 'resource',    'name': '锂矿/资源商',     'role': 'resource',     'capital': 6000,
     'risk_params': json.dumps({'max_position': 250, 'stop_loss_pct': 0.05, 'take_profit_pct': 0.10, 'cost_support_weight': 0.40, 'capacity_util_weight': 0.20})},
    {'agent_id': 'policy',      'name': '政策/宏观博弈者', 'role': 'policy',       'capital': 10000,
     'risk_params': json.dumps({'max_position': 500, 'stop_loss_pct': 0.12, 'take_profit_pct': 0.20, 'macro_weight': 0.35, 'demand_weight': 0.25, 'policy_trigger_events': ['两会', '发改委', '工信部', '新能源车', '储能']})},
]


def init_default_agents():
    """幂等：只插入不存在的agent"""
    conn = _get_db()
    for agent in DEFAULT_AGENTS:
        existing = conn.execute("SELECT 1 FROM agents WHERE agent_id=?", (agent['agent_id'],)).fetchone()
        if not existing:
            conn.execute("""
                INSERT INTO agents (agent_id, name, role, capital, position, avg_cost,
                    target_long, target_short, stop_loss, take_profit,
                    view_score, view_text, needs_review, risk_params)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (agent['agent_id'], agent['name'], agent['role'],
                  agent['capital'], 0, 0, 0, 0, 0, 0, 0, '', 0, agent['risk_params']))
    conn.commit()
    count = conn.execute("SELECT COUNT(*) FROM agents").fetchone()[0]
    conn.close()
    print(f'[agents] 默认agent已初始化，共 {count} 个')


# ─── API 路由函数（通过 inject_routes 注入） ────────────

def _inject_route(bp, rule, endpoint=None, methods=None):
    """把路由函数注册到蓝图"""
    def decorator(func):
        bp.add_url_rule(rule, endpoint=endpoint or func.__name__, view_func=func, methods=methods or ['GET'])
        return func
    return decorator


# ---- 注入的 route 列表，inject_routes 会批量注册 ----
_injected_routes = []

def _route(rule, methods=None):
    """装饰器：收集路由函数"""
    def decorator(func):
        _injected_routes.append((rule, func, methods or ['GET']))
        return func
    return decorator


@_route('/api/battlefield', ['GET'])
def api_battlefield():
    conn = _get_db()
    agent_rows = conn.execute("SELECT * FROM agents ORDER BY agent_id").fetchall()
    agents = [dict(r) for r in agent_rows]

    history_rows = conn.execute("""
        SELECT agent_id, date, position, view_score, view_text, pnl_unreal, close
        FROM agent_history ORDER BY date DESC, agent_id LIMIT 100
    """).fetchall()
    history = [dict(r) for r in history_rows]

    decision_rows = conn.execute("""
        SELECT agent_id, date, trigger, action, delta_position, reason
        FROM agent_decisions ORDER BY date DESC, id DESC LIMIT 20
    """).fetchall()
    decisions = [dict(r) for r in decision_rows]

    custom_rows = conn.execute("""
        SELECT agent_id, date, data_type, source, content, score_impact, tags
        FROM agent_custom_data ORDER BY date DESC, id DESC LIMIT 20
    """).fetchall()
    custom = [dict(r) for r in custom_rows]

    price_row = conn.execute("SELECT date, close FROM prices ORDER BY date DESC LIMIT 1").fetchone()
    conn.close()

    return jsonify({
        'agents': agents,
        'history': history,
        'decisions': decisions,
        'custom_data': custom,
        'latest_price': dict(price_row) if price_row else None,
        'updated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    })


@_route('/api/battlefield/history', ['GET'])
def api_battlefield_history():
    agent_id = request.args.get('agent_id', '')
    days = int(request.args.get('days', '90'))
    conn = _get_db()
    if agent_id:
        rows = conn.execute("""
            SELECT agent_id, date, position, view_score, view_text, pnl_unreal, close
            FROM agent_history WHERE agent_id=? AND date >= date('now', ?) ORDER BY date
        """, (agent_id, f'-{days} days')).fetchall()
    else:
        rows = conn.execute("""
            SELECT agent_id, date, position, view_score, view_text, pnl_unreal, close
            FROM agent_history WHERE date >= date('now', ?) ORDER BY date, agent_id
        """, (f'-{days} days',)).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@_route('/api/agents/init', ['POST'])
def api_init_agents():
    init_default_agents()
    return jsonify({'ok': True, 'message': 'agent配置已更新'})


@_route('/api/agents/<agent_id>', ['GET'])
def api_get_agent(agent_id):
    agent = request.args.get('agent_id')
    days = int(request.args.get('days', '30'))
    conn = _get_db()
    agent_row = conn.execute("SELECT * FROM agents WHERE agent_id=?", (agent_id,)).fetchone()
    history = conn.execute("""
        SELECT date, position, avg_cost, view_score, view_text, pnl_unreal, close
        FROM agent_history WHERE agent_id=? AND date >= date('now', ?) ORDER BY date
    """, (agent_id, f'-{days} days')).fetchall()
    decisions = conn.execute("""
        SELECT date, trigger, action, delta_position, reason
        FROM agent_decisions WHERE agent_id=? AND date >= date('now', ?) ORDER BY date DESC LIMIT 10
    """, (agent_id, f'-{days} days')).fetchall()
    conn.close()
    return jsonify({
        'agent': dict(agent_row) if agent_row else None,
        'history': [dict(r) for r in history],
        'decisions': [dict(r) for r in decisions]
    })


@_route('/api/agents/<agent_id>/update', ['POST'])
def api_update_agent(agent_id):
    data = request.get_json(force=True)
    if not data:
        return jsonify({'error': 'no data'}), 400
    conn = _get_db()
    update_fields = []
    update_vals = []
    for key in ('position', 'avg_cost', 'target_long', 'target_short',
                'stop_loss', 'take_profit', 'view_score', 'view_text', 'needs_review'):
        if key in data:
            update_fields.append(f"{key}=?")
            update_vals.append(data[key])
    if update_fields:
        update_fields.append("updated_at=datetime('now','localtime')")
        update_vals.append(agent_id)
        conn.execute(f"UPDATE agents SET {', '.join(update_fields)} WHERE agent_id=?", update_vals)

    prev_row = conn.execute("SELECT position, view_score FROM agents WHERE agent_id=?", (agent_id,)).fetchone()
    if prev_row:
        trigger = data.get('trigger', 'manual')
        conn.execute("""
            INSERT INTO agent_action_log (agent_id, date, prev_position, new_position, delta,
                prev_view_score, new_view_score, trigger, reason, close_price, pnl_unreal)
            VALUES (?, date('now'), ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (agent_id, prev_row['position'], data.get('position', prev_row['position']),
              data.get('position', prev_row['position']) - prev_row['position'],
              prev_row['view_score'], data.get('view_score', prev_row['view_score']),
              trigger, data.get('reason', ''), data.get('close_price'), data.get('pnl_unreal', 0)))

    if 'view_score' in data or 'position' in data:
        conn.execute("""
            INSERT INTO agent_history (agent_id, date, position, avg_cost,
                view_score, view_text, pnl_unreal, close)
            VALUES (?, date('now'), ?, ?, ?, ?, ?, ?)
        """, (agent_id, data.get('position', prev_row['position'] if prev_row else 0),
              data.get('avg_cost', 0), data.get('view_score', 0), data.get('view_text', ''),
              data.get('pnl_unreal', 0), data.get('close_price', 0)))

    conn.commit()
    conn.close()
    return jsonify({'ok': True, 'agent_id': agent_id})


@_route('/api/battlefield/custom', ['POST'])
def api_custom_data():
    data = request.get_json(force=True)
    required = ('date', 'content')
    for f in required:
        if f not in data:
            return jsonify({'error': f'missing {f}'}), 400
    conn = _get_db()
    conn.execute("""
        INSERT INTO agent_custom_data (agent_id, date, data_type, source, content, score_impact, tags)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (data.get('agent_id'), data.get('date', datetime.now().strftime('%Y-%m-%d')),
          data.get('data_type', 'manual_signal'), data.get('source', ''),
          data.get('content', ''), data.get('score_impact', 0), data.get('tags', '')))
    cid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()
    conn.close()
    return jsonify({'ok': True, 'id': cid})


@_route('/api/agents/review', ['POST'])
def api_trigger_review():
    data = request.get_json(force=True) if request.data else {}
    agent_ids = data.get('agent_ids', None)
    conn = _get_db()
    if agent_ids:
        placeholders = ','.join(['?' for _ in agent_ids])
        rows = conn.execute(f"SELECT * FROM agents WHERE agent_id IN ({placeholders}) AND needs_review=1", agent_ids).fetchall()
    else:
        rows = conn.execute("SELECT * FROM agents WHERE needs_review=1").fetchall()
    pending = [dict(r) for r in rows]
    conn.close()
    if not pending:
        return jsonify({'ok': True, 'pending': 0, 'message': '没有需要审核的agent'})
    return jsonify({'ok': True, 'pending': len(pending), 'agents': pending,
                    'message': f'{len(pending)} 个agent待LLM审核（Phase 4实现）'})


def inject_routes(bp):
    """把所有 @_route 装饰的路由函数注入到指定蓝图"""
    for rule, func, methods in _injected_routes:
        bp.add_url_rule(rule, endpoint=func.__name__, view_func=func, methods=methods)
    print(f'[agents] 注入了 {len(_injected_routes)} 个路由到蓝图')


# ─── 全局初始化入口 ─────────────────────────────────────

def init_all():
    init_agent_tables()
    init_default_agents()
    print('[agents] 多主体博弈框架初始化完成')
