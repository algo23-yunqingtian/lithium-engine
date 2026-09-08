# -*- coding: utf-8 -*-
"""
碳酸锂期货日历小作文系统 — Flask后端
Lithium Calendar System for LC Futures Notes + K-Line
"""

import os
import sys
import sqlite3
import json
from datetime import datetime, timedelta

# 添加 zinc_venv 到 sys.path 以使用 akshare
sys.path.insert(0, '/home/ubuntu/zinc_venv/lib/python3.11/site-packages')

from flask import Flask, request, jsonify, send_from_directory

app = Flask(__name__, static_folder='static', static_url_path='')


@app.after_request
def add_cache_control_headers(response):
    """强制不缓存静态页面 + CORS跨域支持"""
    # CORS headers for GitHub Pages access
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, DELETE, OPTIONS'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
    # Cache control
    if request.path.endswith('.html'):
        response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
    return response

# 注册锌对话助手蓝图（访客模式，与主数据隔离）
try:
    from zinc_chat import zinc_chat_bp
    app.register_blueprint(zinc_chat_bp)
    print('[启动] 锌对话助手已加载: /zinc_chat')
except Exception as e:
    print(f'[警告] 锌对话助手加载失败: {e}')

# 注册碳酸锂多主体复盘与实时数据 API
try:
    from lithium_api import lithium_api, init_minute_tables
    app.register_blueprint(lithium_api)
    init_minute_tables()
    print('[启动] 碳酸锂实时数据 API 已加载: /api/*')
except Exception as e:
    print(f'[警告] 碳酸锂实时 API 加载失败: {e}')

# 注册碳酸锂多主体博弈 Agent 框架（初始化表 + 注入路由）
try:
    from lithium_agents import init_all
    init_all()
    print('[启动] 多主体博弈 Agent 框架已加载')
except Exception as e:
    print(f'[警告] 多主体博弈 Agent 框架加载失败: {e}')

# 注册席位交叉分析 API
try:
    from seat_cross_analysis import seat_bp
    app.register_blueprint(seat_bp)
    print('[启动] 席位交叉分析 API 已加载: /api/seat_cross/*')
except Exception as e:
    print(f'[警告] 席位交叉分析 API 加载失败: {e}')

# 注册月差分析 API (v6新增)

# 注册锂电A股存货库存 API
try:
    from lithium_a_share_inventory_api import lithium_inv_bp
    app.register_blueprint(lithium_inv_bp)
    print('[启动] 锂电A股存货库存 API 已加载: /api/lithium_inv/*')
except Exception as e:
    print(f'[警告] 锂电A股存货库存 API 加载失败: {e}')

DB_PATH = os.path.join(os.path.dirname(__file__), 'lithium.db')


# ─── Database Init ────────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS notes (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            date        TEXT    NOT NULL,
            title       TEXT    NOT NULL,
            content     TEXT    DEFAULT '',
            sentiment   TEXT    DEFAULT '中性'
                CHECK(sentiment IN ('利多','利空','中性')),
            tags        TEXT    DEFAULT '',
            created_at  TEXT    DEFAULT (datetime('now','localtime')),
            updated_at  TEXT    DEFAULT (datetime('now','localtime'))
        );
        CREATE INDEX IF NOT EXISTS idx_notes_date ON notes(date);

        CREATE TABLE IF NOT EXISTS periods (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            start_date  TEXT    NOT NULL,
            end_date    TEXT    NOT NULL,
            theme       TEXT    NOT NULL,
            color       TEXT    DEFAULT '#ff6b6b',
            description TEXT    DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS prices (
            date        TEXT    PRIMARY KEY,
            open        REAL,
            high        REAL,
            low         REAL,
            close       REAL,
            volume      INTEGER DEFAULT 0,
            position    INTEGER DEFAULT 0,
            settle      REAL    DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS spot_price (
            date              TEXT    NOT NULL,
            variety           TEXT    NOT NULL DEFAULT 'LC',
            spot_price        REAL,
            near_symbol       TEXT,
            near_price        REAL,
            dom_symbol        TEXT,
            dom_price         REAL,
            near_basis        REAL,
            dom_basis         REAL,
            near_basis_rate   REAL,
            dom_basis_rate    REAL,
            PRIMARY KEY (date, variety)
        );
        CREATE INDEX IF NOT EXISTS idx_spot_price_date ON spot_price(date);
    """)
    conn.commit()
    conn.close()


# ─── K-Line Data (AKShare) ────────────────────────────────────────

def fetch_lc_kline():
    """从AKShare拉取碳酸锂LC0日K线，写入prices表"""
    try:
        import akshare as ak
        df = ak.futures_main_sina(symbol="LC0")
        if df is None or df.empty:
            return False, "AKShare返回空数据"

        required = {'日期', '开盘价', '最高价', '最低价', '收盘价'}
        if not required.issubset(df.columns):
            return False, f"缺少必要列，已有: {list(df.columns)}"

        conn = get_db()
        rows = []
        for _, row in df.iterrows():
            rows.append((
                str(row['日期'])[:10],
                float(row.get('开盘价', 0)),
                float(row.get('最高价', 0)),
                float(row.get('最低价', 0)),
                float(row.get('收盘价', 0)),
                int(row.get('成交量', 0) or 0),
                int(row.get('持仓量', 0) or 0),
                float(row.get('动态结算价', 0) or 0),
            ))

        conn.executemany(
            "INSERT OR REPLACE INTO prices (date, open, high, low, close, volume, position, settle) "
            "VALUES (?,?,?,?,?,?,?,?)", rows
        )
        conn.commit()
        conn.close()
        return True, f"更新了 {len(rows)} 条K线数据"
    except Exception as e:
        return False, str(e)


# ─── Routes ────────────────────────────────────────────────────────

@app.route('/')
def index():
    return send_from_directory(app.static_folder, 'index.html')


# --- Notes API ---

@app.route('/api/notes', methods=['GET'])
def get_notes():
    year = request.args.get('year')
    month = request.args.get('month')
    date = request.args.get('date')
    conn = get_db()

    if date:
        rows = conn.execute(
            "SELECT * FROM notes WHERE date=? ORDER BY id DESC", (date,)
        ).fetchall()
    elif year and month:
        start = f"{year}-{int(month):02d}-01"
        # 计算下个月1号
        from calendar import monthrange
        ym = int(year), int(month)
        last_day = monthrange(*ym)[1]
        end = f"{year}-{int(month):02d}-{last_day}"
        rows = conn.execute(
            "SELECT * FROM notes WHERE date BETWEEN ? AND ? ORDER BY date DESC, id DESC",
            (start, end)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM notes ORDER BY date DESC, id DESC LIMIT 100"
        ).fetchall()

    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route('/api/notes', methods=['POST'])
def create_note():
    data = request.get_json(force=True)
    if not data.get('date') or not data.get('title'):
        return jsonify({'error': 'date and title required'}), 400
    sentiment = data.get('sentiment', '中性')
    if sentiment not in ('利多', '利空', '中性'):
        sentiment = '中性'
    conn = get_db()
    conn.execute(
        "INSERT INTO notes (date, title, content, sentiment, tags) VALUES (?,?,?,?,?)",
        (data['date'], data['title'], data.get('content', ''),
         sentiment, data.get('tags', ''))
    )
    conn.commit()
    note_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    row = conn.execute("SELECT * FROM notes WHERE id=?", (note_id,)).fetchone()
    conn.close()
    return jsonify(dict(row)), 201


@app.route('/api/notes/<int:note_id>', methods=['PUT'])
def update_note(note_id):
    data = request.get_json(force=True)
    conn = get_db()
    fields = []
    values = []
    for key in ('date', 'title', 'content', 'sentiment', 'tags'):
        if key in data:
            fields.append(f"{key}=?")
            values.append(data[key])
    if not fields:
        conn.close()
        return jsonify({'error': 'no fields to update'}), 400
    fields.append("updated_at=datetime('now','localtime')")
    values.append(note_id)
    conn.execute(
        f"UPDATE notes SET {', '.join(fields)} WHERE id=?", values
    )
    conn.commit()
    row = conn.execute("SELECT * FROM notes WHERE id=?", (note_id,)).fetchone()
    conn.close()
    return jsonify(dict(row) if row else {'error': 'not found'})


@app.route('/api/notes/<int:note_id>', methods=['DELETE'])
def delete_note(note_id):
    conn = get_db()
    conn.execute("DELETE FROM notes WHERE id=?", (note_id,))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})


# --- Monthly Stats API ---

@app.route('/api/stats', methods=['GET'])
def get_stats():
    year = request.args.get('year', datetime.now().year)
    conn = get_db()
    rows = conn.execute("""
        SELECT date,
               COUNT(*) as total,
               SUM(CASE WHEN sentiment='利多' THEN 1 ELSE 0 END) as bullish,
               SUM(CASE WHEN sentiment='利空' THEN 1 ELSE 0 END) as bearish,
               SUM(CASE WHEN sentiment='中性' THEN 1 ELSE 0 END) as neutral
        FROM notes
        WHERE date LIKE ?
        GROUP BY date
        ORDER BY date
    """, (f"{year}-%",)).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


# --- Periods API (K-line annotations) ---

@app.route('/api/periods', methods=['GET'])
def get_periods():
    conn = get_db()
    rows = conn.execute("SELECT * FROM periods ORDER BY start_date").fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route('/api/periods', methods=['POST'])
def create_period():
    data = request.get_json(force=True)
    if not data.get('start_date') or not data.get('end_date') or not data.get('theme'):
        return jsonify({'error': 'start_date, end_date, theme required'}), 400
    conn = get_db()
    conn.execute(
        "INSERT INTO periods (start_date, end_date, theme, color, description) VALUES (?,?,?,?,?)",
        (data['start_date'], data['end_date'], data['theme'],
         data.get('color', '#ff6b6b'), data.get('description', ''))
    )
    conn.commit()
    pid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    row = conn.execute("SELECT * FROM periods WHERE id=?", (pid,)).fetchone()
    conn.close()
    return jsonify(dict(row)), 201


@app.route('/api/periods/<int:pid>', methods=['DELETE'])
def delete_period(pid):
    conn = get_db()
    conn.execute("DELETE FROM periods WHERE id=?", (pid,))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})


# --- Prices API (K-line data) ---

@app.route('/api/prices', methods=['GET'])
def get_prices():
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM prices ORDER BY date"
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route('/api/prices/refresh', methods=['POST'])
def refresh_prices():
    ok, msg = fetch_lc_kline()
    return jsonify({'ok': ok, 'message': msg})


# ─── Paichan Visit Tracking ───────────────────────────────────────

def init_paichan_visits():
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS paichan_visits (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            ip          TEXT    NOT NULL,
            user_agent  TEXT,
            referrer    TEXT,
            visited_at  TEXT    DEFAULT (datetime('now','localtime')),
            notified    INTEGER DEFAULT 0
        )
    """)
    conn.commit()
    conn.close()


def log_paichan_visit(request):
    try:
        conn = get_db()
        conn.execute(
            "INSERT INTO paichan_visits (ip, user_agent, referrer) VALUES (?,?,?)",
            (request.remote_addr or request.headers.get('X-Forwarded-For', 'unknown'),
             request.headers.get('User-Agent', ''),
             request.headers.get('Referer', ''))
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[PaichanVisit] log error: {e}")


@app.route('/paichan.html')
def paichan_page():
    log_paichan_visit(request)
    # 排产系统已转为维护状态，底层数据与代码安全保留
    return "<html><head><title>维护中</title></head><body style='font-family:sans-serif;padding:40px;text-align:center;color:#555;'><h1>🔒 维护中</h1><p>排产系统暂时关闭，如需访问请联系管理员。</p><p><small>底层数据与代码已安全保留。</small></p></body></html>", 403


# ─── SMM News API (for zinc review page) ─────────────────────────

# 重要矿企/事件关键词（用于自动筛选高价值新闻）
IMPORTANT_MINES = [
    'Red Dog', 'Antamina', 'Dugald River', 'Gamsberg', 'Mount Isa',
    'McArthur River', 'Teck', 'Glencore', 'Vedanta', 'BHP', 'South32',
    'Nexa', 'Volcan', 'Buenavista', 'Peñasquito', 'Saucito', 'Fresnillo',
    'Kidd', 'Neves-Corvo', 'Zinkgruvan', 'Garpenberg', 'Rosebery',
    'Tara', 'Cerro Lindo', 'Vazante', 'New Century', 'Woodlawn',
    'Cannington', 'Kazzinc', 'Trevali', 'Grupo Mexico',
    'Red Dog矿', 'Antamina矿', 'Dugald River矿', 'Gamsberg矿',
]
IMPORTANT_SMELTERS = [
    'Nyrstar', 'Boliden', 'Teck Trail', 'Korea Zinc', 'Hindustan Zinc',
    '豫光', '中金岭南', '驰宏锌锗', '株冶', '葫芦岛锌业',
    '南方有色', '汉中锌业', '祥云飞龙', '陕西锌业',
]
IMPORTANT_EVENTS = [
    '停产', '减产', '事故', '关闭', '关停', '罢工', '洪水', '地震',
    '矿难', '爆炸', '坍塌', 'force majeure', '不可抗力',
    '产量指引', 'guidance', '产量下调', '产量上调',
    '资本开支', '扩产', '新项目', '投产', '达产',
    '品位下降', '资源枯竭', '采矿权', '环保限产',
    '加工费', 'TC', 'benchmark', '年度谈判',
]
# 需要排除的低价值噪音
NOISE_KEYWORDS = [
    '荣誉', '表彰', '获奖', '培训', '演练', '检查',
    '党建', '学习', '会议', '参观', '调研', '慰问',
    '小型', '检修完成', '复产', '达标',
]


@app.route('/api/important_news')
def important_news_api():
    """自动筛选高价值锌资讯（读取预评分数据库）"""
    limit = int(request.args.get('limit', 50))
    tier_filter = request.args.get('tier', '')  # 可选过滤tier
    sort_by = request.args.get('sort', 'impact')  # date/score/impact
    
    try:
        import sqlite3
        db_path = '/home/ubuntu/analysis/zinc_v1.db'
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # 检查表是否存在
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='news_zinc_scored'")
        if not cursor.fetchone():
            conn.close()
            return jsonify({'items': [], 'total': 0, 'message': '评分数据库尚未初始化'})
        
        # 构建查询 - 支持排序
        order_map = {
            'date': 'date DESC',
            'score': 'score DESC, date DESC',
            'impact': '''CASE tier WHEN 'A' THEN 0 WHEN 'B' THEN 1 ELSE 2 END,
                         score DESC, date DESC'''
        }
        order_clause = order_map.get(sort_by, order_map['impact'])
        
        if tier_filter in ('A', 'B'):
            query = f'''
                SELECT date, score, tier, matched_terms, content, source_url
                FROM news_zinc_scored
                WHERE tier = ?
                ORDER BY {order_clause}
                LIMIT ?
            '''
            cursor.execute(query, (tier_filter, limit))
        else:
            query = f'''
                SELECT date, score, tier, matched_terms, content, source_url
                FROM news_zinc_scored
                WHERE tier != 'C'
                ORDER BY {order_clause}
                LIMIT ?
            '''
            cursor.execute(query, (limit,))
        
        rows = cursor.fetchall()
        
        # 统计
        cursor.execute('SELECT tier, COUNT(*) FROM news_zinc_scored GROUP BY tier')
        tier_counts = dict(cursor.fetchall())
        
        conn.close()
        
        items = []
        for date, score, tier, matched, content, source_url in rows:
            tags = [t.strip() for t in (matched or '').split(',') if t.strip()]
            tier_emoji = '🔴' if tier == 'A' else '🟡' if tier == 'B' else ''
            items.append({
                'date': date,
                'title': content[:300],
                'score': score,
                'tier': tier,
                'tier_emoji': tier_emoji,
                'tags': tags[:5],
                'source_url': source_url or ''
            })
        
        return jsonify({
            'items': items, 
            'total': len(items),
            'stats': {
                'tier_a': tier_counts.get('A', 0),
                'tier_b': tier_counts.get('B', 0),
            }
        })
        
    except Exception as e:
        return jsonify({'items': [], 'error': f'读取评分数据库失败: {str(e)}'})


@app.route('/api/smm_news')
def smm_news_api():
    """搜索SMM锌相关新闻，直接调用akshare"""
    keyword = request.args.get('keyword', '').strip()
    if not keyword:
        return jsonify({'items': [], 'error': '请输入关键词'})
    
    try:
        import akshare as ak
        news_df = ak.futures_news_shmet(symbol="锌")
        
        # 按关键词过滤
        mask = news_df['内容'].str.contains(keyword, case=False, na=False)
        filtered_df = news_df[mask].head(50)
        
        # 转换为JSON格式
        items = []
        for _, row in filtered_df.iterrows():
            items.append({
                'date': str(row['发布时间'])[:16],
                'title': str(row['内容'])[:200]
            })
        
        return jsonify({'items': items})
        
    except Exception as e:
        return jsonify({'items': [], 'error': f'搜索新闻失败: {str(e)}'})

@app.route('/api/status')
def api_status():
    """返回数据 freshness 状态"""
    import os
    conn = get_db()
    last_price = conn.execute("SELECT MAX(date) FROM prices").fetchone()[0]
    last_spread = conn.execute("SELECT MAX(date) FROM spreads").fetchone()[0]
    last_contract = conn.execute("SELECT MAX(date) FROM contract_prices").fetchone()[0]
    note_count = conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
    conn.close()
    db_mtime = datetime.fromtimestamp(os.path.getmtime(DB_PATH)).strftime('%Y-%m-%d %H:%M:%S')
    return jsonify({
        'last_price_date': last_price,
        'last_spread_date': last_spread,
        'last_contract_date': last_contract,
        'note_count': note_count,
        'db_mtime': db_mtime,
        'server_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    })


# ─── LC 现货价格 + 基差 API ────────────────────────────────

@app.route('/api/lc_spot')
def api_lc_spot():
    """碳酸锂现货价格 + 基差 API — 读取 lc_spot.db 数据"""
    import os as _os
    try:
        spot_db = "/home/ubuntu/lc_futures_data/data/lc_spot.db"
        if not _os.path.exists(spot_db):
            return jsonify({
                'error': '现货数据数据库尚未创建',
                'items': [],
                'latest': None,
                'series': []
            })

        conn = sqlite3.connect(spot_db)
        conn.row_factory = sqlite3.Row

        # 读取现货+基差时间序列
        rows = conn.execute(
            "SELECT * FROM spot_price WHERE variety='LC' ORDER BY date"
        ).fetchall()
        series = [dict(r) for r in rows]

        # 读取元数据
        meta_rows = conn.execute("SELECT * FROM meta").fetchall()
        meta = {r['key']: r['value'] for r in meta_rows}

        # 最新一条
        latest = dict(rows[-1]) if rows else None
        if latest:
            # 格式化日期为 YYYY-MM-DD
            latest['date_fmt'] = latest['date'][:4] + '-' + latest['date'][4:6] + '-' + latest['date'][6:8]

        conn.close()

        return jsonify({
            'latest': latest,
            'series': series,
            'meta': meta,
            'total': len(series)
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e), 'items': [], 'latest': None, 'series': []}), 500


@app.route('/api/lc_spot/refresh', methods=['POST'])
def api_lc_spot_refresh():
    """手动刷新 LC 现货数据 — 调用 akshare 实时拉取"""
    import subprocess
    try:
        result = subprocess.run(
            ['/home/ubuntu/zinc_venv/bin/python3', '/home/ubuntu/lc_futures_data/fetch_spot.py'],
            capture_output=True, text=True, timeout=60,
            env={**os.environ, 'HOME': '/home/ubuntu'}
        )
        if result.returncode == 0:
            return jsonify({'ok': True, 'message': '现货数据刷新成功'})
        else:
            return jsonify({'ok': False, 'message': result.stderr or '刷新失败'}), 500
    except Exception as e:
        return jsonify({'ok': False, 'message': str(e)}), 500


@app.route('/api/term-structure')
def api_term_structure():
    """返回最新 N 天期限结构、月差变动、合约量仓变动"""
    days = request.args.get('days', '5')
    try:
        days = max(1, min(30, int(days)))
    except ValueError:
        days = 5

    conn = get_db()
    # 取最近 N 个有合约数据的交易日
    date_rows = conn.execute(
        "SELECT DISTINCT date FROM contract_prices ORDER BY date DESC LIMIT ?",
        (days,)
    ).fetchall()
    dates = [r[0] for r in reversed(date_rows)]

    history = []
    for d in dates:
        rows = conn.execute(
            "SELECT contract, close, volume, position FROM contract_prices WHERE date=? ORDER BY contract",
            (d,)
        ).fetchall()
        history.append({
            'date': d,
            'contracts': [dict(r) for r in rows]
        })

    # 计算相邻合约月差（远月 - 近月）
    spreads = {}
    for h in history:
        cs = h['contracts']
        for i in range(len(cs) - 1):
            key = f"{cs[i]['contract']}-{cs[i+1]['contract']}"
            spreads.setdefault(key, {})[h['date']] = round(cs[i+1]['close'] - cs[i]['close'], 2)

    # 计算最新一日相对前一日的量仓变化
    changes = {}
    if len(history) >= 2:
        prev = {c['contract']: c for c in history[-2]['contracts']}
        curr = {c['contract']: c for c in history[-1]['contracts']}
        for contract, c in curr.items():
            p = prev.get(contract)
            if p:
                changes[contract] = {
                    'close_change': round(c['close'] - p['close'], 2),
                    'volume_change': int(c['volume'] - p['volume']),
                    'position_change': int(c['position'] - p['position']),
                    'volume_change_pct': round((c['volume'] - p['volume']) / max(p['volume'], 1), 4),
                    'position_change_pct': round((c['position'] - p['position']) / max(p['position'], 1), 4),
                }

    conn.close()
    return jsonify({
        'history': history,
        'spreads': spreads,
        'changes': changes,
        'latest_date': dates[-1] if dates else None
    })


# ─── Startup ──────────────────────────────────────────────────────


# ─── LME 有色日报 ────────────────────────────────────────────────

@app.route('/lme')
@app.route('/lme/')
@app.route('/lme_daily_report.html')
def lme_daily_report():
    from flask import send_from_directory
    import os
    report_path = os.path.join(os.path.expanduser('~'), 'lme_daily_report.html')
    if os.path.exists(report_path):
        return send_from_directory(os.path.dirname(report_path), os.path.basename(report_path))
    return "LME 日报文件不存在", 404

# ─── Spread Calendar Page ───────────────────────────────────────────
@app.route("/spread_calendar.html")
def spread_calendar_page():
    from flask import send_from_directory
    return send_from_directory("static", "spread_calendar.html")


@app.route("/lithium_inventory.html")
def lithium_inventory_page():
    from flask import send_from_directory
    return send_from_directory("static", "lithium_inventory_page.html")


# ─── 注册锂矿综合看板 API (Blueprint) ───────────────────────────────
try:
    from lithium_dashboard_api import lithium_bp
    app.register_blueprint(lithium_bp)
    print('[启动] 锂矿综合看板 API 已加载: /api/lithium/*, /lithium_global_dashboard.html')
except Exception as e:
    import traceback
    print(f'[警告] 锂矿综合看板 API 加载失败: {e}')
    traceback.print_exc()

# ─── 注册锂电产业链A股存货库存 API ─────────────────────────────────
try:
    from lithium_a_share_inventory_api import lithium_inv_bp
    app.register_blueprint(lithium_inv_bp)
    print('[启动] 锂电A股存货库存 API 已加载: /api/lithium_inv/*')
except Exception as e:
    import traceback
    print(f'[警告] 锂电A股存货库存 API 加载失败: {e}')
    traceback.print_exc()


# ─── 碳酸锂持仓分析 API ────────────────────────────────────────────

LC_DB_PATH = "/home/ubuntu/lc_futures_data/data/lc_position.db"

@app.route("/api/lc_analysis")
def api_lc_analysis():
    """持仓分析数据 API — 返回所有图表所需数据"""
    import sqlite3, pandas as pd
    from datetime import datetime
    try:
        conn = sqlite3.connect(LC_DB_PATH)
        
        # 最新日期
        latest_date = pd.read_sql("SELECT DISTINCT date FROM position_rank ORDER BY date DESC LIMIT 1", conn).iloc[0, 0]
        
        # 统计卡片
        wr_total = pd.read_sql(f"SELECT SUM(today_qty) FROM warehouse_receipt WHERE date='{latest_date}'", conn).iloc[0, 0] or 0
        prev_date_sql = f"SELECT DISTINCT date FROM warehouse_receipt WHERE date < '{latest_date}' ORDER BY date DESC LIMIT 1"
        prev_wr_date = pd.read_sql(prev_date_sql, conn).iloc[0, 0] if pd.read_sql(prev_date_sql, conn).shape[0] > 0 else latest_date
        wr_prev = pd.read_sql(f"SELECT SUM(today_qty) FROM warehouse_receipt WHERE date='{prev_wr_date}'", conn).iloc[0, 0] or 0
        wr_delta = int(wr_total) - int(wr_prev)
        wr_pct = (wr_delta / wr_prev * 100) if wr_prev else 0
        
        # LC2609 最新
        lc = pd.read_sql(f"SELECT SUM(long_oi), SUM(short_oi), SUM(long_oi)-SUM(short_oi) FROM position_rank WHERE symbol='LC2609' AND date='{latest_date}'", conn).iloc[0]
        long_oi = int(lc.iloc[0]) or 0
        short_oi = int(lc.iloc[1]) or 0
        net_oi = long_oi - short_oi
        
        # LC2609 首尾对比
        first_l = pd.read_sql("SELECT SUM(long_oi) FROM position_rank WHERE symbol='LC2609' ORDER BY date ASC LIMIT 1", conn).iloc[0, 0] or 0
        first_s = pd.read_sql("SELECT SUM(short_oi) FROM position_rank WHERE symbol='LC2609' ORDER BY date ASC LIMIT 1", conn).iloc[0, 0] or 0
        lc2609_l_pct = (long_oi - int(first_l)) / int(first_l) * 100 if first_l else 0
        lc2609_s_pct = (short_oi - int(first_s)) / int(first_s) * 100 if first_s else 0
        
        # LC2609 趋势
        df = pd.read_sql("SELECT date, SUM(long_oi) as L, SUM(short_oi) as S, SUM(long_oi)-SUM(short_oi) as N FROM position_rank WHERE symbol='LC2609' GROUP BY date ORDER BY date", conn)
        lc2609 = {
            "dates": [str(d) for d in df["date"].values],
            "longs": [int(v) for v in df["L"].values],
            "shorts": [int(v) for v in df["S"].values],
            "nets": [int(v) for v in df["N"].values]
        }
        
        # 仓单趋势
        wr_df = pd.read_sql("SELECT date, SUM(today_qty) as T FROM warehouse_receipt WHERE product='碳酸锂' GROUP BY date ORDER BY date", conn)
        wr = {
            "dates": [str(d) for d in wr_df["date"].values],
            "totals": [int(v) for v in wr_df["T"].values],
            "deltas": [0] + [int(wr_df["T"].values[i]) - int(wr_df["T"].values[i-1]) for i in range(1, len(wr_df))]
        }
        
        # 各合约最新持仓
        contracts_df = pd.read_sql(f"SELECT symbol, SUM(long_oi) as L, SUM(short_oi) as S FROM position_rank WHERE date='{latest_date}' AND variety='LC' GROUP BY symbol ORDER BY symbol", conn)
        contracts = {
            "names": [str(r["symbol"]) for _, r in contracts_df.iterrows()],
            "longs": [int(r["L"]) for _, r in contracts_df.iterrows()],
            "shorts": [int(r["S"]) for _, r in contracts_df.iterrows()],
            "nets": [int(r["L"]) - int(r["S"]) for _, r in contracts_df.iterrows()]
        }
        
        # 合约迁移 - 各合约总持仓趋势
        symbols = ["LC2606", "LC2607", "LC2609", "LC2611", "LC2612", "LC2701", "LC2705"]
        all_dates = sorted(pd.read_sql("SELECT DISTINCT date FROM position_rank", conn)["date"].values)
        migration = {"dates": [str(d) for d in all_dates], "symbols": {}, "maxOi": []}
        for sym in symbols:
            sdf = pd.read_sql(f"SELECT date, SUM(long_oi)+SUM(short_oi) as total FROM position_rank WHERE symbol='{sym}' GROUP BY date ORDER BY date", conn)
            if len(sdf) > 0:
                # 填充缺失日期为 0
                full = []
                si = 0
                for d in all_dates:
                    if si < len(sdf) and str(sdf.iloc[si]["date"]) == str(d):
                        full.append(int(sdf.iloc[si]["total"]))
                        si += 1
                    else:
                        full.append(0)
                migration["symbols"][sym] = full
            else:
                migration["symbols"][sym] = [0] * len(all_dates)
        
        # 找出每日持仓最大的合约（主力）
        max_oi = []
        max_sym = []
        for i in range(len(all_dates)):
            vals = [(sym, migration["symbols"][sym][i]) for sym in symbols]
            best = max(vals, key=lambda x: x[1])
            max_oi.append(best[1])
            max_sym.append(best[0])
        migration["maxOi"] = max_sym
        
        # 近10日快照
        snap = pd.read_sql(f"SELECT date, SUM(long_oi) as L, SUM(short_oi) as S, SUM(long_oi)-SUM(short_oi) as N FROM position_rank WHERE symbol='LC2609' GROUP BY date ORDER BY date DESC LIMIT 10", conn)
        snapshot = {
            "dates": [str(d) for d in snap["date"].values],
            "longs": [int(v) for v in snap["L"].values],
            "shorts": [int(v) for v in snap["S"].values],
            "nets": [int(v) for v in snap["N"].values]
        }
        # 计算变化量
        snapshot["lChg"] = [0] + [snapshot["longs"][i] - snapshot["longs"][i-1] for i in range(1, len(snapshot["longs"]))]
        snapshot["sChg"] = [0] + [snapshot["shorts"][i] - snapshot["shorts"][i-1] for i in range(1, len(snapshot["shorts"]))]
        snapshot["nChg"] = [0] + [snapshot["nets"][i] - snapshot["nets"][i-1] for i in range(1, len(snapshot["nets"]))]
        
        result = {
            "latest_date": latest_date,
            "stats": {
                "wr_total": int(wr_total),
                "wr_delta": wr_delta,
                "wr_trend": round(wr_pct, 1),
                "long_oi": long_oi,
                "short_oi": short_oi,
                "net_oi": net_oi,
                "net_ratio": round(net_oi / (long_oi + short_oi) * 100, 1) if (long_oi + short_oi) else 0,
            },
            "lc2609": lc2609,
            "wr": wr,
            "contracts": contracts,
            "migration": migration,
            "snapshot": snapshot
        }
        
        conn.close()
        return jsonify(result)
    
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/api/lc_data")
def api_lc_data():
    """持仓数据原始 API — 返回仓单+持仓排名"""
    import sqlite3, pandas as pd
    try:
        conn = sqlite3.connect(LC_DB_PATH)
        wr = pd.read_sql("SELECT * FROM warehouse_receipt WHERE product='碳酸锂' ORDER BY date DESC, warehouse", conn)
        pos = pd.read_sql("SELECT * FROM position_rank WHERE variety='LC' ORDER BY date DESC, symbol, rank", conn)
        conn.close()
        # 转 JSON 序列化
        def serialize(df):
            return df.to_dict(orient="records")
        return jsonify({
            "warehouse_receipt": serialize(wr),
            "position_rank": serialize(pos)
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/lc_seat_flow")
def api_lc_seat_flow():
    """席位流向分析 API — 追踪主力席位持仓变化"""
    import sqlite3, pandas as pd
    try:
        conn = sqlite3.connect(LC_DB_PATH)
        
        # 获取所有日期
        all_dates = sorted(pd.read_sql("SELECT DISTINCT date FROM position_rank", conn)["date"].values)
        
        # 按合约分组，取前10席位
        symbols = ["LC2609", "LC2611", "LC2612", "LC2701", "LC2705"]
        results = {}
        
        for sym in symbols:
            df = pd.read_sql(f"""
                SELECT date, rank, long_party, long_oi, long_oi_chg,
                       short_party, short_oi, short_oi_chg
                FROM position_rank 
                WHERE symbol='{sym}'
                ORDER BY date DESC, rank ASC
            """, conn)
            
            # 按日期和rank分组
            daily_top = {}
            for date in df['date'].unique():
                day_df = df[df['date'] == date].head(20)
                top = {}
                for _, row in day_df.iterrows():
                    seat = row['long_party']
                    top[seat] = {
                        'rank': int(row['rank']),
                        'long_oi': int(row['long_oi']) if pd.notna(row['long_oi']) else 0,
                        'long_oi_chg': int(row['long_oi_chg']) if pd.notna(row['long_oi_chg']) else 0,
                        'short_oi': int(row['short_oi']) if pd.notna(row['short_oi']) else 0,
                        'short_oi_chg': int(row['short_oi_chg']) if pd.notna(row['short_oi_chg']) else 0,
                    }
                daily_top[str(date)] = top
            results[sym] = daily_top
        
        # 计算每个席位的长期排名和平均持仓
        seat_stats = pd.read_sql("""
            SELECT long_party,
                   AVG(long_oi) as avg_long,
                   MAX(long_oi) as max_long,
                   COUNT(DISTINCT date) as active_days,
                   AVG(long_oi_chg) as avg_daily_chg
            FROM position_rank 
            WHERE variety='LC'
            GROUP BY long_party
            HAVING active_days >= 5
            ORDER BY avg_long DESC
            LIMIT 20
        """, conn)
        
        seat_stats_list = [{
            'seat': str(r['long_party']),
            'avg_long': round(float(r['avg_long']), 0),
            'max_long': int(r['max_long']),
            'active_days': int(r['active_days']),
            'avg_daily_chg': round(float(r['avg_daily_chg']), 0)
        } for _, r in seat_stats.iterrows()]
        
        # 近10日头部席位变化（LC2609为主）
        latest_10 = pd.read_sql("""
            SELECT date, long_party, long_oi, long_oi_chg
            FROM position_rank 
            WHERE symbol='LC2609' AND rank <= 10
            ORDER BY date DESC, rank ASC
        """, conn)
        
        recent_changes = {}
        seats_10 = set(latest_10['long_party'].tolist()[:10])
        for seat in seats_10:
            seat_data = latest_10[latest_10['long_party'] == seat]
            recent_changes[seat] = []
            for _, r in seat_data.iterrows():
                recent_changes[seat].append({
                    'date': str(r['date']),
                    'long_oi': int(r['long_oi']) if pd.notna(r['long_oi']) else 0,
                    'daily_chg': int(r['long_oi_chg']) if pd.notna(r['long_oi_chg']) else 0
                })
            recent_changes[seat].reverse()  # 时间正序
        
        # 多空对比：前10席位总多 vs 总空
        multi_seat = pd.read_sql(f"""
            SELECT date,
                   SUM(long_oi) as top10_long,
                   SUM(short_oi) as top10_short,
                   SUM(long_oi_chg) as top10_long_chg,
                   SUM(short_oi_chg) as top10_short_chg
            FROM position_rank 
            WHERE symbol='LC2609' AND rank <= 10
            GROUP BY date ORDER BY date
        """, conn)
        
        multi = {
            'dates': [str(d) for d in multi_seat['date']],
            'top10_long': [int(v) for v in multi_seat['top10_long']],
            'top10_short': [int(v) for v in multi_seat['top10_short']],
            'top10_long_chg': [int(v) for v in multi_seat['top10_long_chg']],
            'top10_short_chg': [int(v) for v in multi_seat['top10_short_chg']],
        }
        
        result = {
            'all_dates': [str(d) for d in all_dates],
            'results': results,
            'seat_stats': seat_stats_list,
            'recent_changes': recent_changes,
            'multi_seat': multi
        }
        
        conn.close()
        return jsonify(result)
    
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

# ========== 月差分析 API ==========
@app.route("/api/spread_analysis")
def api_spread_analysis():
    from spread_analysis_api import api_spread_analysis as _fn
    return _fn()


# ========== 页面路由 ==========
@app.route("/spread_analysis.html")
def spread_analysis_page():
    from flask import send_from_directory
    return send_from_directory('static', 'spread_analysis.html')

@app.route("/lithium_spreads_calendar.html")
def spreads_calendar_page():
    from flask import send_from_directory
    return send_from_directory('static', 'lithium_spreads_calendar.html')

@app.route("/api/date_spreads")
def api_date_spreads():
    from spread_analysis_api import api_date_spreads as _fn
    return _fn()

@app.route("/api/all_dates")
def api_all_trading_dates():
    from spread_analysis_api import api_all_trading_dates as _fn
    return jsonify(_fn())

@app.route("/api/historical_spreads")
def api_historical_spreads():
    from spread_analysis_api import api_historical_spreads as _fn
    return _fn()

@app.route("/seat_cross_analysis.html")
def seat_cross_analysis_page():
    from flask import send_from_directory
    return send_from_directory('static', 'seat_cross_analysis.html')


# ========== 月差复盘页面 ==========
@app.route("/lc_spread_review.html")
def spread_review_page():
    from flask import send_from_directory
    return send_from_directory('static', 'lc_spread_review_latest.html')


# ========== 全合约日度价格 API（月差曲线回测用） ==========
@app.route("/api/contract_prices_by_date")
def contract_prices_by_date():
    """返回指定日期所有上市合约的结算价, 用于月差曲线"""
    date = request.args.get("date", "")
    if not date:
        return jsonify({"error": "需指定 date 参数, 格式: 2026-04-01"}), 400
    
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    
    rows = conn.execute(
        "SELECT contract, date, open, high, low, close, volume, hold, settle "
        "FROM contract_daily_all WHERE date=? ORDER BY contract",
        (date,)
    ).fetchall()
    
    if not rows:
        # 日期可能不存在或不是交易日, 返回最近交易日
        last_row = conn.execute(
            "SELECT MAX(date) as last_date FROM contract_daily_all WHERE date<=?",
            (date,)
        ).fetchone()
        if last_row and last_row["last_date"]:
            rows = conn.execute(
                "SELECT contract, date, open, high, low, close, volume, hold, settle "
                "FROM contract_daily_all WHERE date=? ORDER BY contract",
                (last_row["last_date"],)
            ).fetchall()
            if rows:
                return jsonify({
                    "date": date,
                    "actual_date": last_row["last_date"],
                    "contracts": [dict(r) for r in rows],
                    "note": f"{date} 非交易日, 已返回最近交易日 {last_row['last_date']} 数据"
                })
        conn.close()
        return jsonify({"error": f"无 {date} 数据, 请尝试其他日期"}), 404
    
    # 计算月差结构: 以近月(主力)为基准, 计算各合约相对近月的价差
    contracts_data = [dict(r) for r in rows]
    # 按持仓排序, 持仓最大的为近月(主力)
    contracts_with_oi = [(c, int(c.get("hold", 0) or 0)) for c in contracts_data]
    contracts_with_oi.sort(key=lambda x: -x[1])
    main_contract = contracts_with_oi[0][0] if contracts_with_oi else None
    main_settle = float(main_contract["settle"]) if main_contract else 0
    
    # 计算月差
    for c in contracts_data:
        settle = float(c.get("settle", 0))
        c["spread_from_main"] = round(settle - main_settle, 1) if main_settle else 0
        c["spread_pct"] = round((settle - main_settle) / main_settle * 10000, 1) if main_settle else 0
    
    conn.close()
    
    return jsonify({
        "date": date,
        "contracts": contracts_data,
        "main_contract": main_contract["contract"] if main_contract else None,
        "main_settle": main_settle,
        "contract_count": len(contracts_data)
    })


# ========== 获取所有有数据的日期列表 ==========
@app.route("/api/trading_dates")
def trading_dates():
    """返回 contract_daily_all 中所有有数据的日期"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    dates = conn.execute("SELECT DISTINCT date FROM contract_daily_all ORDER BY date").fetchall()
    conn.close()
    return jsonify([{"date": d["date"]} for d in dates])
    conn.close()
    return jsonify([{"date": d["date"]} for d in dates])


# ========== 获取某日 K 线数据 (主力量) ==========
@app.route("/api/kline_by_date")
def kline_by_date():
    """返回指定日期的 K 线数据 (主力量, 从 contract_daily_all 中按持仓取主力)"""
    date = request.args.get("date", "")
    if not date:
        return jsonify({"error": "需指定 date 参数"}), 400
    
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    
    # 找到当日持仓最大的合约作为主力
    main = conn.execute(
        "SELECT contract, MAX(hold) as max_hold FROM contract_daily_all WHERE date=? GROUP BY contract ORDER BY max_hold DESC LIMIT 1",
        (date,)
    ).fetchone()
    
    if not main:
        conn.close()
        return jsonify({"error": f"无 {date} 数据"}), 404
    
    row = conn.execute(
        "SELECT contract, date, open, high, low, close, volume, hold, settle "
        "FROM contract_daily_all WHERE date=? AND contract=?",
        (date, main["contract"])
    ).fetchone()
    
    result = dict(row) if row else {}
    result["main_contract"] = main["contract"]
    conn.close()
    return jsonify(result)


# 页面路由


@app.route("/api/kline_daily_range")
def kline_daily_range():
    """返回日期范围内的K线数据"""
    start = request.args.get("start", "2026-04-01")
    end = request.args.get("end", "2026-07-30")
    
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    
    # Find the main contract for each date in range
    rows = conn.execute("""
        SELECT cd.date, cd.contract, cd.open, cd.high, cd.low, cd.close, cd.volume, cd.hold, cd.settle,
               (SELECT COUNT(*) FROM contract_daily_all cd2 
                WHERE cd2.date = cd.date AND cd2.hold > cd.hold) as oi_rank
        FROM contract_daily_all cd
        WHERE cd.date >= ? AND cd.date <= ?
        ORDER BY cd.date, cd.hold DESC
    """, (start, end)).fetchall()
    
    # Group by date, pick the top OI contract per day
    from collections import OrderedDict
    date_main = OrderedDict()
    for r in rows:
        d = r["date"]
        if d not in date_main:
            date_main[d] = dict(r)
    
    result = {"start": start, "end": end, "kline": list(date_main.values())}
    conn.close()
    return jsonify(result)


if __name__ == '__main__':
    init_db()
    init_paichan_visits()
    # 启动时尝试拉取最新K线
    ok, msg = fetch_lc_kline()
    print(f"[启动] K线数据: {msg}")
    print(f"[启动] 监听 http://0.0.0.0:8766")
    app.run(host='0.0.0.0', port=8801, debug=False)
