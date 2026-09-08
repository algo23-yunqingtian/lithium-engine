# -*- coding: utf-8 -*-
"""
锌研究助手 · 对话版
集成到 lithium_calendar 主 Flask 服务，访客通过 http://124.221.113.37:8766/zinc_chat 访问。

隔离原则：
- 只读取主人主数据库 zinc_v1.db，不做任何写入。
- 访客生成的图表、报告统一写入 lithium_calendar/static/guest/，与主人正式报告隔离。
- 不执行 DELETE/UPDATE/INSERT/DROP 等写入 SQL。
"""

from flask import Blueprint, request, jsonify, send_from_directory, current_app
import sqlite3
import json
import os
import re
import requests
import html
from datetime import datetime
from collections import defaultdict

zinc_chat_bp = Blueprint('zinc_chat', __name__)

BASE_DIR = '/home/ubuntu/zinc_assistant'
CONFIG_PATH = os.path.join(BASE_DIR, 'config.json')
DB_PATH = '/home/ubuntu/zinc_v1.db'
GUEST_OUTPUT_DIR = '/home/ubuntu/lithium_calendar/static/guest'
GUEST_URL_PREFIX = '/guest/'
SKILL_PATH = '/home/ubuntu/.hermes/skills/commodity-research/zinc-data-analysis/SKILL.md'


# ---------- Config ----------

def load_config():
    if not os.path.exists(CONFIG_PATH):
        cfg = default_config()
        save_config(cfg)
        return cfg
    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)

def default_config():
    return {
        'access_key': 'zinc2026',
        'llm': {'provider': 'openai', 'base_url': 'https://api.openai.com/v1', 'api_key': '', 'model': 'gpt-4o-mini'}
    }

def save_config(cfg):
    os.makedirs(BASE_DIR, exist_ok=True)
    with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


# ---------- Skill context ----------

def load_skill():
    if not os.path.exists(SKILL_PATH):
        return '本技能文档暂未找到，但仍可以通过数据库工具进行分析。'
    with open(SKILL_PATH, 'r', encoding='utf-8') as f:
        return f.read()


# ---------- Database (read-only) ----------

def get_db():
    return sqlite3.connect(f'file:{DB_PATH}?mode=ro', uri=True)

def db_query(sql, params=(), limit=1000):
    if not sql.strip().upper().startswith('SELECT'):
        raise ValueError('Only SELECT queries are allowed.')
    conn = get_db()
    c = conn.cursor()
    c.execute(sql, params)
    rows = c.fetchmany(limit)
    columns = [d[0] for d in c.description] if c.description else []
    conn.close()
    return {'columns': columns, 'rows': rows}

def resolve_indicator(key):
    conn = get_db()
    c = conn.cursor()
    if re.match(r'^[aAsS]\w+$', key):
        c.execute('SELECT indicator_id, indicator_name, unit, frequency FROM indicator_mapping WHERE indicator_id = ?', (key,))
    else:
        c.execute('SELECT indicator_id, indicator_name, unit, frequency FROM indicator_mapping WHERE indicator_name LIKE ? ORDER BY indicator_name LIMIT 1', (f'%{key}%',))
    row = c.fetchone()
    conn.close()
    return row

def get_series(indicator_key, limit=200):
    row = resolve_indicator(indicator_key)
    if not row:
        return None
    indicator_id, name, unit, freq = row
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT date, value FROM time_series WHERE indicator_id = ? AND value IS NOT NULL ORDER BY date DESC LIMIT ?', (indicator_id, limit))
    rows = c.fetchall()
    conn.close()
    rows = [(d, float(v)) for d, v in reversed(rows)]
    return {'id': indicator_id, 'name': name, 'unit': unit, 'frequency': freq, 'data': rows}

def list_indicators(keyword=None, limit=50):
    conn = get_db()
    c = conn.cursor()
    if keyword:
        c.execute('SELECT indicator_id, indicator_name, unit, frequency FROM indicator_mapping WHERE indicator_name LIKE ? ORDER BY indicator_name LIMIT ?', (f'%{keyword}%', limit))
    else:
        c.execute('SELECT indicator_id, indicator_name, unit, frequency FROM indicator_mapping ORDER BY indicator_name LIMIT ?', (limit,))
    rows = c.fetchall()
    conn.close()
    return [{'id': r[0], 'name': r[1], 'unit': r[2], 'frequency': r[3]} for r in rows]


def _latest_value(indicator_key):
    s = get_series(indicator_key, 1)
    if not s or not s['data']:
        return None
    return s['name'], s['data'][-1], s['unit']


# ---------- Chart generation ----------

def generate_chart(title, series_list, filename=None):
    """
    生成 Dark ECharts HTML 图表，并存入 guest 目录。
    series_list: [{"name": str, "data": [[date, value], ...], "yAxisIndex": int}, ...]
    日期对齐：取所有序列日期的交集，避免日期错位。
    """
    if not series_list:
        return {'error': 'No series data'}
    if filename is None:
        filename = f'guest_chart_{datetime.now().strftime("%Y%m%d_%H%M%S")}.html'
    if not filename.endswith('.html'):
        filename += '.html'
    safe_name = re.sub(r'[^a-zA-Z0-9_.\-]', '_', filename)
    output_path = os.path.join(GUEST_OUTPUT_DIR, safe_name)

    date_sets = [set(d for d, _ in s['data']) for s in series_list]
    dates = sorted(set.intersection(*date_sets)) if date_sets else []
    if not dates:
        return {'error': 'No common dates among series'}

    series_data = []
    for s in series_list:
        s_dates = {d: v for d, v in s['data']}
        aligned = [[d, s_dates.get(d)] for d in dates]
        series_data.append({
            'name': s['name'],
            'type': 'line',
            'smooth': True,
            'yAxisIndex': s.get('yAxisIndex', 0),
            'data': aligned,
            'symbol': 'none'
        })

    has_second_axis = any(s.get('yAxisIndex', 0) == 1 for s in series_list)

    html_content = f'''<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>{html.escape(title)}</title>
<script src="https://cdn.jsdelivr.net/npm/echarts@5.5.0/dist/echarts.min.js"></script>
<style>
  body {{ margin: 0; background: #0d1117; }}
  #chart {{ width: 100%; height: 100vh; }}
</style>
</head>
<body>
<div id="chart"></div>
<script>
var chart = echarts.init(document.getElementById('chart'), 'dark');
chart.setOption({{
  backgroundColor: '#0d1117',
  title: {{ text: {json.dumps(title, ensure_ascii=False)}, left: 'center', textStyle: {{ color: '#f0c040' }} }},
  tooltip: {{ trigger: 'axis' }},
  legend: {{ top: 40, textStyle: {{ color: '#c9d1d9' }} }},
  grid: {{ left: 60, right: 60, top: 100, bottom: 80 }},
  xAxis: {{ type: 'category', data: {json.dumps(dates)}, axisLabel: {{ rotate: 45, color: '#8b949e' }} }},
  yAxis: [
    {{ type: 'value', axisLabel: {{ color: '#8b949e' }}, splitLine: {{ lineStyle: {{ color: '#30363d' }} }} }},
    {("{ type: 'value', axisLabel: { color: '#8b949e' }, splitLine: { show: false } }," if has_second_axis else '')}
  ],
  dataZoom: [{{ type: 'inside' }}, {{ type: 'slider', bottom: 20 }}],
  series: {json.dumps(series_data)}
}});
window.addEventListener('resize', function() {{ chart.resize(); }});
</script>
</body>
</html>'''
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html_content)
    url = f'http://124.221.113.37:8766{GUEST_URL_PREFIX}{safe_name}'
    return {'url': url, 'filename': safe_name, 'path': output_path}


# ---------- LLM call ----------

def call_llm(messages, config):
    llm_cfg = config.get('llm', {})
    api_key = llm_cfg.get('api_key', '').strip()
    if not api_key:
        return None
    base_url = llm_cfg.get('base_url', 'https://api.openai.com/v1').rstrip('/')
    model = llm_cfg.get('model', 'gpt-4o-mini')
    headers = {'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'}
    payload = {'model': model, 'messages': messages, 'temperature': 0.3}
    try:
        resp = requests.post(f'{base_url}/chat/completions', headers=headers, json=payload, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        return data['choices'][0]['message']['content']
    except Exception as e:
        return f'[LLM调用失败: {str(e)}]'


# ---------- Tool execution ----------

def execute_tool(tool_call):
    name = tool_call.get('name')
    args = tool_call.get('arguments', {})
    if name == 'list_indicators':
        return list_indicators(args.get('keyword'), args.get('limit', 50))
    elif name == 'get_series':
        return get_series(args.get('indicator'), args.get('limit', 200))
    elif name == 'query_db':
        return db_query(args.get('sql'), tuple(args.get('params', [])), args.get('limit', 1000))
    elif name == 'generate_chart':
        return generate_chart(args.get('title'), args.get('series'), args.get('filename'))
    elif name == 'get_latest':
        result = {}
        for key in ['a10000098', 'a10018157', 's20095859', 'S5808573', 'a10099948']:
            s = get_series(key, 1)
            if s:
                result[s['name']] = s['data'][-1] if s['data'] else None
        return result
    else:
        return {'error': f'Unknown tool: {name}'}


# ---------- Direct / rule-based response (no LLM needed) ----------

def _sum_by_date(ids):
    conn = get_db()
    c = conn.cursor()
    placeholders = ','.join('?' * len(ids))
    c.execute(f'SELECT date, value FROM time_series WHERE indicator_id IN ({placeholders}) AND value IS NOT NULL', ids)
    rows = c.fetchall()
    conn.close()
    totals = defaultdict(float)
    for d, v in rows:
        totals[d] += float(v)
    return sorted(totals.items())


def _detect_intent(msg):
    m = msg.lower()
    if 'tc' in m or '加工' in msg or '加工费' in msg:
        return 'tc'
    if '七地' in msg or '库存' in msg or '社会库存' in msg:
        return 'inventory'
    if 'lme' in m or '锌价' in msg or '价格' in msg or '行情' in msg:
        return 'price'
    if '海外冶炼' in msg or '海外精炼' in msg or '冶炼厂' in msg:
        return 'overseas_smelter'
    if '国内产量' in msg or '精炼锌' in msg or '冶炼产量' in msg:
        return 'domestic_smelter'
    if '矿山' in msg or '矿产' in msg or '锌精矿' in msg:
        return 'mine'
    if '图' in msg or 'chart' in m or '画' in msg or '趋势' in msg:
        return 'chart'
    return 'help'


def _series_to_dict(name, data, y_axis=0):
    return {'name': name, 'data': data, 'yAxisIndex': y_axis}


def direct_response(user_msg):
    intent = _detect_intent(user_msg)
    if intent == 'tc':
        dom = get_series('s20095859', 100)
        imp = get_series('a10018157', 100)
        if not dom and not imp:
            return {'text': '暂未找到 TC 数据，请检查数据库是否存在相关指标。', 'charts': []}
        series = []
        latest_texts = []
        if dom:
            series.append(_series_to_dict(dom['name'], dom['data'], 0))
            d, v = dom['data'][-1]
            latest_texts.append(f'{dom["name"]}: 最新{d} 为 {v:.2f} {dom["unit"]}')
        if imp:
            series.append(_series_to_dict(imp['name'], imp['data'], 1))
            d, v = imp['data'][-1]
            latest_texts.append(f'{imp["name"]}: 最新{d} 为 {v:.2f} {imp["unit"]}')
        chart = generate_chart('锌加工费走势', series, 'tc_trend.html')
        return {'text': '\n'.join(latest_texts), 'charts': [chart]}

    if intent == 'inventory':
        inv7 = get_series('a10000098', 100)
        lme = get_series('a10017992', 100)
        series = []
        text_lines = []
        if inv7:
            series.append(_series_to_dict(inv7['name'], inv7['data'], 0))
            d, v = inv7['data'][-1]
            text_lines.append(f'{inv7["name"]}: 最新{d} 为 {v:.2f} {inv7["unit"]}')
        if lme:
            series.append(_series_to_dict(lme['name'], lme['data'], 1))
            d, v = lme['data'][-1]
            text_lines.append(f'{lme["name"]}: 最新{d} 为 {v:.2f} {lme["unit"]}')
        chart = generate_chart('锌库存走势', series, 'inventory_trend.html')
        return {'text': '\n'.join(text_lines), 'charts': [chart]}

    if intent == 'price':
        lme = get_series('S5808573', 100)
        series = []
        text_lines = []
        if lme:
            series.append(_series_to_dict(lme['name'], lme['data'], 0))
            d, v = lme['data'][-1]
            text_lines.append(f'{lme["name"]}: 最新{d} 为 {v:.2f} {lme["unit"]}')
        chart = generate_chart('LME 锌价走势', series, 'lme_price.html')
        return {'text': '\n'.join(text_lines), 'charts': [chart]}

    if intent == 'overseas_smelter':
        # 获取所有海外精炼锌产量指标，按季度加总
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT indicator_id FROM indicator_mapping WHERE indicator_name LIKE '%海外精炼锌产量%'")
        ids = [r[0] for r in c.fetchall()]
        conn.close()
        if not ids:
            return {'text': '暂未找到海外冶炼产量数据。', 'charts': []}
        total = _sum_by_date(ids)
        series = [_series_to_dict('海外精炼锌产量合计（合并计算）', total, 0)]
        d, v = total[-1]
        chart = generate_chart('海外精炼锌产量', series, 'overseas_smelter_total.html')
        return {'text': f'海外精炼锌产量合计（按季度加总）：最新{d} 为 {v:.2f} 万吨', 'charts': [chart]}

    if intent == 'domestic_smelter':
        # NBS 中国精炼锌产量
        s = get_series('a10001922', 100)
        if not s:
            return {'text': '暂未找到国内精炼锌产量数据。', 'charts': []}
        series = [_series_to_dict(s['name'], s['data'], 0)]
        d, v = s['data'][-1]
        chart = generate_chart(s['name'], series, 'domestic_zinc_output.html')
        return {'text': f'{s["name"]}: 最新{d} 为 {v:.2f} {s["unit"]}', 'charts': [chart]}

    if intent == 'mine':
        # 按季度加总海外锌精矿产量
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT indicator_id FROM indicator_mapping WHERE indicator_name LIKE '%海外锌精矿产量%'")
        ids = [r[0] for r in c.fetchall()]
        conn.close()
        if not ids:
            return {'text': '暂未找到海外锌精矿产量数据。', 'charts': []}
        total = _sum_by_date(ids)
        series = [_series_to_dict('海外锌精矿产量合计（合并计算）', total, 0)]
        d, v = total[-1]
        chart = generate_chart('海外锌精矿产量', series, 'overseas_mine_total.html')
        return {'text': f'海外锌精矿产量合计（按季度加总）：最新{d} 为 {v:.2f} 万金属吨', 'charts': [chart]}

    if intent == 'chart':
        return {'text': '演示模式：你可以直接说“画一下 TC”“画一下库存”，我会自动识别并生成图表。如果已配置 LLM API，可以直接用自然语言描述需要的图表。', 'charts': []}

    # help
    return {
        'text': '你好，我是锌研究助手！目前处于演示模式，可以直接回答以下几类常见问题：\n\n1. 加工费：“最近锌加工费是多少？”\n2. 库存：“七地锌库存走势”\n3. 价格：“LME 锌价最新价格”\n4. 冶炼：“海外冶炼厂产量”\n5. 矿山：“海外锌精矿产量”\n\n配置 LLM API 后，还可以用自然语言做更复杂的分析。\n\n注意：我只读取主人数据库，生成的图表都会放在独立的 guest 目录，不会影响主人的分析和数据。',
        'charts': []
    }


# ---------- Routes ----------

@zinc_chat_bp.route('/zinc_chat')
def chat_index():
    return send_from_directory(current_app.static_folder, 'zinc_chat.html')


@zinc_chat_bp.route('/api/zinc_chat/chat', methods=['POST'])
def chat():
    cfg = load_config()
    data = request.get_json() or {}
    user_key = data.get('access_key', '').strip()
    if user_key != cfg.get('access_key', ''):
        return jsonify({'error': 'Access key incorrect'}), 403

    user_msg = data.get('message', '').strip()
    if not user_msg:
        return jsonify({'error': 'Message is empty'}), 400

    # If no LLM key configured, use direct/rule-based response
    if not cfg.get('llm', {}).get('api_key', '').strip():
        return jsonify(direct_response(user_msg))

    skill = load_skill()
    tool_desc = '''
你是一个锌行业研究助手。以下工具供你使用，你必须通过输出 JSON 工具调用来获取数据或生成图表。如果不需要工具，直接回答即可。

工具调用格式：
{
  "tool_calls": [
    {"name": "<tool_name>", "arguments": {...}}
  ]
}

可用工具：
1. list_indicators(keyword, limit=50) - 查询指标名称，返回 indicator_id
2. get_series(indicator, limit=200) - 读取某个指标的时间序列，indicator可以是名称关键词或indicator_id
3. query_db(sql, params=[], limit=1000) - 执行只读SELECT SQL，返回行列数据
4. generate_chart(title, series, filename) - 生成Dark ECharts图表。series是数组，每个元素包含 name、data（[[date,value],...）、可选 yAxisIndex(0=左,1=右)
5. get_latest() - 获取核心指标最新值

注意事项：
- 所有数据库操作只读，不会修改主人数据
- 生成的图表会保存到独立的guest目录，URL会返回给用户
- 多序列图表时必须按日期交集对齐
- 回答时结论前置，分点清晰，不说空话
'''

    system_prompt = f'''
{tool_desc}

以下是锌行业分析技能文档，供你参考：

{skill}
'''

    messages = [
        {'role': 'system', 'content': system_prompt},
        {'role': 'user', 'content': user_msg}
    ]

    assistant_msg = call_llm(messages, cfg)
    if assistant_msg is None:
        return jsonify(direct_response(user_msg))

    charts = []
    tool_pattern = re.search(r'```(?:json)?\s*(\{.*\})\s*```', assistant_msg, re.DOTALL)
    if tool_pattern:
        try:
            tool_payload = json.loads(tool_pattern.group(1))
            tool_calls = tool_payload.get('tool_calls', [])
            if tool_calls:
                tool_results = []
                for tc in tool_calls:
                    result = execute_tool(tc)
                    tool_results.append({'tool': tc['name'], 'result': result})
                    if tc['name'] == 'generate_chart':
                        charts.append(result)
                messages.append({'role': 'assistant', 'content': assistant_msg})
                messages.append({'role': 'user', 'content': f'工具执行结果：\n{json.dumps(tool_results, ensure_ascii=False, indent=2)}\n\n请根据上述结果给出最终回答，如果生成了图表请在回答中列出图表URL。'})
                assistant_msg = call_llm(messages, cfg)
                if assistant_msg is None:
                    assistant_msg = '[LLM调用失败，请检查API配置]'
        except Exception as e:
            assistant_msg += f'\n\n[工具解析异常: {str(e)}]'

    return jsonify({'text': assistant_msg, 'charts': charts})


@zinc_chat_bp.route('/api/zinc_chat/config_status', methods=['GET'])
def config_status():
    cfg = load_config()
    has_key = bool(cfg.get('llm', {}).get('api_key', '').strip())
    return jsonify({'llm_configured': has_key, 'model': cfg.get('llm', {}).get('model', '')})
