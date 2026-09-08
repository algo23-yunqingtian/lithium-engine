# -*- coding: utf-8 -*-
"""
碳酸锂多主体复盘与实时数据 API
- 从 akshare 获取免费实时行情、K线、库存
- 从lithium.db获取已有主体画像、行为模型、小作文等
"""

import os
import sys
import json
import sqlite3
import math
from datetime import datetime, timedelta

# 确保 akshare 可用
sys.path.insert(0, '/home/ubuntu/zinc_venv/lib/python3.11/site-packages')

from flask import Blueprint, jsonify, request

DB_PATH = os.path.join(os.path.dirname(__file__), 'lithium.db')

lithium_api = Blueprint('lithium_api', __name__, url_prefix='/api')


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_minute_tables():
    """初始化分钟级数据表"""
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS minute_prices (
            datetime    TEXT    NOT NULL,
            contract    TEXT    NOT NULL,
            open        REAL,
            high        REAL,
            low         REAL,
            close       REAL,
            volume      INTEGER DEFAULT 0,
            position    INTEGER DEFAULT 0,
            PRIMARY KEY (datetime, contract)
        );
        CREATE INDEX IF NOT EXISTS idx_minute_contract_dt ON minute_prices(contract, datetime);

        CREATE TABLE IF NOT EXISTS inventory_history (
            date        TEXT    PRIMARY KEY,
            inventory   INTEGER DEFAULT 0,
            change      INTEGER DEFAULT 0,
            source      TEXT    DEFAULT 'em'
        );
    """)
    conn.commit()
    conn.close()


# ─── 实时行情读取 ───

def fetch_lc_realtime():
    """获取碳酸锂各合约实时行情"""
    try:
        import akshare as ak
        df = ak.futures_zh_realtime(symbol='碳酸锂')
        if df is None or df.empty:
            return []
        cols = list(df.columns)
        rows = []
        for _, r in df.iterrows():
            d = {}
            for c in cols:
                v = r[c]
                if isinstance(v, (float, int)):
                    d[c] = v
                else:
                    d[c] = str(v) if v is not None else ''
            rows.append(d)
        return rows
    except Exception as e:
        return {'error': str(e)}


def fetch_lc_contracts():
    """获取 LC 主力 + 合约的日K线数据"""
    try:
        import akshare as ak
        out = {}
        # LC0 主力
        df0 = ak.futures_main_sina(symbol='LC0')
        if df0 is not None and not df0.empty:
            out['LC0'] = {
                'dates': [str(x)[:10] for x in df0['日期']],
                'data': [[float(x) for x in [r['开盘价'], r['收盘价'], r['最低价'], r['最高价']]] for _, r in df0.iterrows()],
                'volumes': [int(r['成交量']) for _, r in df0.iterrows()],
                'positions': [int(r['持仓量']) for _, r in df0.iterrows()],
            }
        # 各合约
        for contract in ['LC2608', 'LC2609', 'LC2610', 'LC2611', 'LC2701', 'LC2703', 'LC2705']:
            try:
                df = ak.futures_zh_daily_sina(symbol=contract)
                if df is not None and not df.empty:
                    out[contract] = {
                        'dates': [str(x)[:10] for x in df['日期']],
                        'data': [[float(r['开盘价']), float(r['收盘价']), float(r['最低价']), float(r['最高价'])] for _, r in df.iterrows()],
                        'volumes': [int(r['成交量']) for _, r in df.iterrows()],
                        'positions': [int(r['持仓量']) for _, r in df.iterrows()],
                    }
            except Exception as e:
                out[contract] = {'error': str(e)}
        return out
    except Exception as e:
        return {'error': str(e)}


def fetch_minute_prices(contract='LC0', period='1'):
    """从 akshare 拉取分钟数据，并存入数据库"""
    try:
        import akshare as ak
        df = ak.futures_zh_minute_sina(symbol=contract, period=period)
        if df is None or df.empty:
            return 0, "空数据"
        conn = get_db()
        rows = []
        for _, r in df.iterrows():
            rows.append((
                str(r['datetime']),
                contract,
                float(r['open']), float(r['high']), float(r['low']), float(r['close']),
                int(r['volume']), int(r['hold'])
            ))
        conn.executemany(
            "INSERT OR REPLACE INTO minute_prices (datetime, contract, open, high, low, close, volume, position) "
            "VALUES (?,?,?,?,?,?,?,?)", rows
        )
        conn.commit()
        conn.close()
        return len(rows), f"更新 {len(rows)} 条分钟数据"
    except Exception as e:
        return 0, str(e)


def fetch_inventory():
    """获取碳酸锂库存并存入数据库"""
    try:
        import akshare as ak
        df = ak.futures_inventory_em(symbol='碳酸锂')
        if df is None or df.empty:
            return 0, "空数据"
        conn = get_db()
        rows = []
        for _, r in df.iterrows():
            rows.append((
                str(r['日期'])[:10],
                int(r['库存']),
                float(r['增减']) if pd_notna(r['增减']) else 0,
                'em'
            ))
        conn.executemany(
            "INSERT OR REPLACE INTO inventory_history (date, inventory, change, source) VALUES (?,?,?,?)", rows
        )
        conn.commit()
        # 返回最近数据
        latest = [dict(x) for x in conn.execute(
            "SELECT * FROM inventory_history ORDER BY date DESC LIMIT 60"
        ).fetchall()]
        conn.close()
        return latest
    except Exception as e:
        return {'error': str(e)}


def pd_notna(v):
    try:
        import math
        return not (v is None or (isinstance(v, float) and math.isnan(v)))
    except Exception:
        return v is not None


# ─── API 路由 ───

@lithium_api.route('/participants', methods=['GET'])
def api_participants():
    """返回市场主体、行为模型、信息获取能力"""
    conn = get_db()
    participants = [dict(r) for r in conn.execute(
        "SELECT * FROM participants ORDER BY id"
    ).fetchall()]
    models = {}
    for r in conn.execute("SELECT * FROM behavior_models").fetchall():
        d = dict(r)
        # 尝试解析 JSON 字段
        for k in ['entry_rules', 'exit_rules', 'risk_rules', 'known_info', 'blind_info']:
            try:
                d[k] = json.loads(d[k]) if d[k] else []
            except Exception:
                d[k] = []
        models[d['participant_id']] = d
    info_access = {}
    for r in conn.execute("SELECT * FROM info_access").fetchall():
        d = dict(r)
        info_access.setdefault(d['participant_id'], []).append(d)
    conn.close()
    return jsonify({
        'participants': participants,
        'behavior_models': models,
        'info_access': info_access
    })


@lithium_api.route('/battlefield', methods=['GET'])
def api_battlefield():
    """返回战场地图数据：旧版主体(participants) + 新版Agent框架(agents) + 其他数据"""
    conn = get_db()
    # 旧版主体
    participants = [dict(r) for r in conn.execute(
        "SELECT * FROM participants ORDER BY id"
    ).fetchall()]
    # 行为模型
    models = {}
    for r in conn.execute("SELECT * FROM behavior_models").fetchall():
        d = dict(r)
        for k in ['entry_rules', 'exit_rules', 'risk_rules', 'known_info', 'blind_info']:
            try:
                d[k] = json.loads(d[k]) if d[k] else []
            except Exception:
                d[k] = []
        models[d['participant_id']] = d
    # 位置
    positions = [dict(r) for r in conn.execute(
        "SELECT * FROM participant_positions ORDER BY participant_id, price_level"
    ).fetchall()]
    # 近期小作文
    notes = [dict(r) for r in conn.execute(
        "SELECT * FROM notes ORDER BY date DESC LIMIT 20"
    ).fetchall()]
    # 解读
    interpretations = [dict(r) for r in conn.execute(
        "SELECT i.*, n.title as note_title, n.date as note_date, n.sentiment as note_sentiment "
        "FROM interpretations i JOIN notes n ON i.note_id = n.id ORDER BY i.created_at DESC LIMIT 50"
    ).fetchall()]
    # 卡片
    cards = [dict(r) for r in conn.execute(
        "SELECT * FROM cards ORDER BY entry_date DESC LIMIT 20"
    ).fetchall()]
    # 新版Agent框架数据
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
    # 最新价格
    price_row = conn.execute(
        "SELECT * FROM prices ORDER BY date DESC LIMIT 1"
    ).fetchone()
    latest_price = dict(price_row) if price_row else None
    conn.close()
    return jsonify({
        # 旧版（兼容现有前端）
        'participants': participants,
        'behavior_models': models,
        'positions': positions,
        'notes': notes,
        'interpretations': interpretations,
        'cards': cards,
        # 新版Agent框架（Phase 1）
        'agents': agents,
        'history': history,
        'decisions': decisions,
        'custom_data': custom,
        'latest_price': latest_price
    })


@lithium_api.route('/timeline', methods=['GET'])
def api_timeline():
    """返回市场时间线数据：按日期聚合小作文和各主体行为"""
    raw_days = request.args.get('days', '180')
    days = 0 if raw_days.lower() in ('all', '0', 'inf', 'infinity') else int(raw_days)
    conn = get_db()
    if days > 0:
        start = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
        notes = [dict(r) for r in conn.execute(
            "SELECT * FROM notes WHERE date >= ? ORDER BY date DESC, id DESC",
            (start,)
        ).fetchall()]
        interpretations = [dict(r) for r in conn.execute(
            "SELECT i.*, n.title as note_title, n.date as note_date, p.name as participant_name, p.short_name as participant_short "
            "FROM interpretations i "
            "JOIN notes n ON i.note_id = n.id "
            "JOIN participants p ON i.participant_id = p.id "
            "WHERE n.date >= ? ORDER BY n.date DESC",
            (start,)
        ).fetchall()]
        # 最近 prices
        prices = [dict(r) for r in conn.execute(
            "SELECT * FROM prices WHERE date >= ? ORDER BY date",
            (start,)
        ).fetchall()]
    else:
        notes = [dict(r) for r in conn.execute(
            "SELECT * FROM notes ORDER BY date DESC, id DESC"
        ).fetchall()]
        interpretations = [dict(r) for r in conn.execute(
            "SELECT i.*, n.title as note_title, n.date as note_date, p.name as participant_name, p.short_name as participant_short "
            "FROM interpretations i "
            "JOIN notes n ON i.note_id = n.id "
            "JOIN participants p ON i.participant_id = p.id "
            "ORDER BY n.date DESC"
        ).fetchall()]
        # 所有 prices
        prices = [dict(r) for r in conn.execute(
            "SELECT * FROM prices ORDER BY date"
        ).fetchall()]
    conn.close()
    return jsonify({
        'days': days,
        'notes': notes,
        'interpretations': interpretations,
        'prices': prices
    })


@lithium_api.route('/sentiment', methods=['GET'])
def api_sentiment():
    """返回情绪仪表盘数据：小作文情绪统计、价格走势、库存等"""
    days = int(request.args.get('days', 60))
    conn = get_db()
    start = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
    # 情绪统计
    stats = [dict(r) for r in conn.execute(
        "SELECT date, COUNT(*) as total, "
        "SUM(CASE WHEN sentiment='利多' THEN 1 ELSE 0 END) as bullish, "
        "SUM(CASE WHEN sentiment='利空' THEN 1 ELSE 0 END) as bearish, "
        "SUM(CASE WHEN sentiment='中性' THEN 1 ELSE 0 END) as neutral "
        "FROM notes WHERE date >= ? GROUP BY date ORDER BY date",
        (start,)
    ).fetchall()]
    # 价格
    prices = [dict(r) for r in conn.execute(
        "SELECT * FROM prices WHERE date >= ? ORDER BY date",
        (start,)
    ).fetchall()]
    # 库存
    inventory = [dict(r) for r in conn.execute(
        "SELECT * FROM inventory_history WHERE date >= ? ORDER BY date",
        (start,)
    ).fetchall()]
    # 最新实时
    realtime = fetch_lc_realtime()
    conn.close()
    return jsonify({
        'stats': stats,
        'prices': prices,
        'inventory': inventory,
        'realtime': realtime,
        'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    })


@lithium_api.route('/market-scan', methods=['GET'])
def api_market_scan():
    """简化版市场扫描：从实时行情获取各板块涨跌幅"""
    try:
        conn = get_db()
        # 从lithium.db 计算 LC 近日涨跌（如果有数据）
        rows = [dict(r) for r in conn.execute(
            "SELECT contract, date, close FROM contract_prices WHERE contract='LC0' ORDER BY date DESC LIMIT 6"
        ).fetchall()]
        chg, chg5 = 0, 0
        if len(rows) >= 2:
            chg = (rows[0]['close'] / rows[1]['close'] - 1) * 100
            idx = min(5, len(rows)-1)
            if idx > 0:
                chg5 = (rows[0]['close'] / rows[idx]['close'] - 1) * 100

        # 主要 LC 合约 5日涨跌
        symbols_list = []
        for contract in ['LC0', 'LC2609', 'LC2611', 'LC2701']:
            c_rows = [dict(r) for r in conn.execute(
                "SELECT date, close, volume, position FROM contract_prices WHERE contract=? ORDER BY date DESC LIMIT 6",
                (contract,)
            ).fetchall()]
            if len(c_rows) >= 2:
                c_chg = (c_rows[0]['close'] / c_rows[1]['close'] - 1) * 100
                idx = min(5, len(c_rows)-1)
                c_chg5 = (c_rows[0]['close'] / c_rows[idx]['close'] - 1) * 100 if idx > 0 else c_chg
                symbols_list.append({
                    'sym': contract,
                    'close': c_rows[0]['close'],
                    'chg': round(c_chg, 2),
                    'chg5': round(c_chg5, 2),
                    'volume': c_rows[0]['volume'],
                    'position': c_rows[0]['position']
                })
        conn.close()

        if symbols_list:
            avg_chg = round(sum(s['chg'] for s in symbols_list) / len(symbols_list), 2)
            avg_chg5 = round(sum(s['chg5'] for s in symbols_list) / len(symbols_list), 2)
        else:
            avg_chg, avg_chg5 = 0.0, 0.0
        sectors = {
            '新能源': {
                'direction': 'up' if avg_chg5 > 0 else 'down' if avg_chg5 < 0 else 'flat',
                'avg_chg': avg_chg,
                'avg_chg5': avg_chg5,
                'heat': min(100, max(0, 50 + int(abs(avg_chg5) * 5))),
                'symbols': symbols_list
            }
        }

        # 新闻关键词：从 notes 标签提取
        conn = get_db()
        tags = []
        for r in conn.execute("SELECT tags FROM notes WHERE tags IS NOT NULL AND tags != ''").fetchall():
            tags.extend([t.strip() for t in r[0].split(',') if t.strip()])
        from collections import Counter
        news = dict(Counter(tags).most_common(10))
        conn.close()

        return jsonify({
            'sectors': sectors,
            'news_keywords': news,
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        })
    except Exception as e:
        import traceback
        return jsonify({'error': str(e), 'traceback': traceback.format_exc()})


@lithium_api.route('/lc-contracts', methods=['GET'])
def api_lc_contracts():
    """返回 LC 各合约价格"""
    # 优先从 contract_prices 表读取
    conn = get_db()
    contracts = {}
    for contract in ['LC0', 'LC2608', 'LC2609', 'LC2610', 'LC2611', 'LC2701', 'LC2703', 'LC2705']:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM contract_prices WHERE contract=? ORDER BY date",
            (contract,)
        ).fetchall()]
        if rows:
            contracts[contract] = {
                'data': rows,
                'last': rows[-1]
            }
    conn.close()
    # 如果没有某个合约数据，尝试实时补充
    if not contracts:
        contracts = fetch_lc_contracts()
    return jsonify(contracts)


@lithium_api.route('/minute-prices', methods=['GET'])
def api_minute_prices():
    """返回分钟级价格"""
    contract = request.args.get('contract', 'LC0')
    limit = int(request.args.get('limit', 1000))
    conn = get_db()
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM minute_prices WHERE contract=? ORDER BY datetime DESC LIMIT ?",
        (contract, limit)
    ).fetchall()]
    conn.close()
    rows.reverse()
    return jsonify({'contract': contract, 'rows': rows})


@lithium_api.route('/minute-prices/refresh', methods=['POST'])
def api_minute_refresh():
    """刷新分钟数据"""
    contract = request.get_json(force=True).get('contract', 'LC0')
    period = request.get_json(force=True).get('period', '1')
    n, msg = fetch_minute_prices(contract, period)
    return jsonify({'ok': n > 0, 'count': n, 'message': msg})


@lithium_api.route('/realtime', methods=['GET'])
def api_realtime():
    """返回实时行情"""
    return jsonify(fetch_lc_realtime())


@lithium_api.route('/battlefield/game-factors', methods=['GET'])
def api_game_factors():
    """返回博弈因子数据：热力图 + 极化检测 + 历史趋势"""
    conn = get_db()
    # 最新日期
    latest_date = conn.execute(
        "SELECT DISTINCT date FROM agent_game_factors ORDER BY date DESC LIMIT 1"
    ).fetchone()
    latest_date = latest_date['date'] if latest_date else None

    # 最新数据
    rows = conn.execute(
        "SELECT * FROM agent_game_factors WHERE date=? ORDER BY agent_id",
        (latest_date,)
    ).fetchall() if latest_date else []
    latest = [dict(r) for r in rows]

    # 历史趋势（最近30天）
    hist_rows = conn.execute(
        """SELECT date, agent_id, base_score, info_transfer, behavioral_impact,
                  total_correction, corrected_score, factors_detail
           FROM agent_game_factors
           WHERE date >= date('now', '-30 days')
           ORDER BY date, agent_id"""
    ).fetchall()
    history = [dict(r) for r in hist_rows]

    # 聚合统计
    n_bull = sum(1 for r in latest if r.get('base_score', 0) > 0)
    n_bear = sum(1 for r in latest if r.get('base_score', 0) < 0)
    max_abs_correction = max((abs(r.get('total_correction', 0)) for r in latest), default=0)
    has_polarization = any(
        json.loads(r.get('factors_detail', '{}')).get('polarization', False)
        for r in latest
    ) if latest else False

    conn.close()
    return jsonify({
        'latest_date': latest_date,
        'latest': latest,
        'history': history,
        'summary': {
            'n_bull': n_bull,
            'n_bear': n_bear,
            'max_correction': max_abs_correction,
            'polarization': has_polarization,
        }
    })


@lithium_api.route('/inventory', methods=['GET'])
def api_inventory():
    """返回库存数据（优先从数据库，没有则拉取）"""
    conn = get_db()
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM inventory_history ORDER BY date DESC LIMIT 90"
    ).fetchall()]
    conn.close()
    if not rows:
        rows = fetch_inventory()
    return jsonify(rows)


@lithium_api.route('/inventory/refresh', methods=['POST'])
def api_inventory_refresh():
    """刷新库存数据"""
    rows = fetch_inventory()
    return jsonify({'ok': 'error' not in str(rows), 'count': len(rows) if isinstance(rows, list) else 0, 'data': rows})


    """获取agent历史观点序列（甘特图纵轴数据）"""
    agent_id = request.args.get('agent_id', '')
    days = int(request.args.get('days', '90'))
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
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


@lithium_api.route('/agents/init', methods=['POST'])
def api_init_agents():
    """重新初始化agent配置（幂等，只插入不存在的）"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    agents_data = [
        ('smelter', '冶炼厂', 'smelter', 5000, 0, 0, 0, 0, 0, 0, 0, '', 0,
         json.dumps({'max_position': 200, 'stop_loss_pct': 0.08, 'take_profit_pct': 0.15, 'profit_threshold_reduce': -500, 'profit_threshold_add': 1000})),
        ('merchant', '贸易商/加工商', 'merchant', 3000, 0, 0, 0, 0, 0, 0, 0, '', 0,
         json.dumps({'max_position': 150, 'stop_loss_pct': 0.06, 'take_profit_pct': 0.12, 'basis_sensitivity': 0.35, 'term_structure_sensitivity': 0.25})),
        ('institution', '纯投机构', 'institution', 8000, 0, 0, 0, 0, 0, 0, 0, '', 0,
         json.dumps({'max_position': 300, 'stop_loss_pct': 0.10, 'take_profit_pct': 0.20, 'momentum_window': 20, 'trend_follow': True})),
        ('arbitrage', '套利商', 'arbitrage', 4000, 0, 0, 0, 0, 0, 0, 0, '', 0,
         json.dumps({'max_position': 100, 'stop_loss_pct': 0.03, 'take_profit_pct': 0.08, 'mean_reversion': True, 'spread_threshold': 0.02})),
        ('retail', '散户/游资', 'retail', 1000, 0, 0, 0, 0, 0, 0, 0, '', 0,
         json.dumps({'max_position': 50, 'stop_loss_pct': 0.15, 'take_profit_pct': 0.25, 'chase_trend': True, 'news_sensitivity': 0.30})),
        ('resource', '锂矿/资源商', 'resource', 6000, 0, 0, 0, 0, 0, 0, 0, '', 0,
         json.dumps({'max_position': 250, 'stop_loss_pct': 0.05, 'take_profit_pct': 0.10, 'cost_support_weight': 0.40, 'capacity_util_weight': 0.20})),
        ('policy', '政策/宏观博弈者', 'policy', 10000, 0, 0, 0, 0, 0, 0, 0, '', 0,
         json.dumps({'max_position': 500, 'stop_loss_pct': 0.12, 'take_profit_pct': 0.20, 'macro_weight': 0.35, 'demand_weight': 0.25, 'policy_trigger_events': ['两会', '发改委', '工信部', '新能源车', '储能']})),
    ]
    for a in agents_data:
        conn.execute("SELECT 1 FROM agents WHERE agent_id=?", (a[0],)).fetchone() or \
            conn.execute("""INSERT INTO agents (agent_id, name, role, capital, position, avg_cost,
                target_long, target_short, stop_loss, take_profit, view_score, view_text, needs_review, risk_params)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", a)
    conn.commit()
    count = conn.execute("SELECT COUNT(*) FROM agents").fetchone()[0]
    conn.close()
    return jsonify({'ok': True, 'message': f'agent配置已更新，共 {count} 个'})


@lithium_api.route('/agents/<agent_id>', methods=['GET'])
def api_get_agent(agent_id):
    """获取单个agent详细信息"""
    days = int(request.args.get('days', '30'))
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
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


@lithium_api.route('/agents/<agent_id>/update', methods=['POST'])
def api_update_agent(agent_id):
    """手动更新agent状态（规则引擎或用户手动调整）"""
    data = request.get_json(force=True)
    if not data:
        return jsonify({'error': 'no data'}), 400
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
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
                prev_view_score, new_view_score, trigger, reasoning, close_price, pnl_unreal)
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


@lithium_api.route('/battlefield/history', methods=['GET'])
def api_battlefield_history():
    """获取agent历史观点序列（甘特图纵轴数据）"""
    agent_id = request.args.get('agent_id', '')
    days = int(request.args.get('days', '90'))
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
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


@lithium_api.route('/battlefield/custom', methods=['POST'])
def api_custom_data():
    """用户手动注入非标信息"""
    data = request.get_json(force=True)
    if 'date' not in data or 'content' not in data:
        return jsonify({'error': 'missing date or content'}), 400
    conn = sqlite3.connect(DB_PATH)
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


@lithium_api.route('/agents/review', methods=['POST'])
def api_trigger_review():
    """手动触发LLM批量审核"""
    data = request.get_json(force=True) if request.data else {}
    agent_ids = data.get('agent_ids', None)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
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
