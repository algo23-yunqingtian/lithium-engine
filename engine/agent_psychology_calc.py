#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
主体心理状态与心理价位带计算脚本 (v1.0 2026-08-01)
====================================================
设计文档: /home/ubuntu/lithium_calendar/docs/agent_psychology_kline_design.md (第3-7节)
- 只用 T 日及之前数据 (防透视)
- 幂等: INSERT OR REPLACE (date, agent_id 主键)
- 用法:
    python agent_psychology_calc.py                      # 全量 (agent_history 最早→最新, prices 对齐)
    python agent_psychology_calc.py --days 30            # 最近30个交易日
    python agent_psychology_calc.py --date 2026-07-31    # 单日增量 (供 cron)
"""
import sqlite3, sys, os, argparse

DB = "/home/ubuntu/lithium_calendar/lithium.db"

AGENTS = ['smelter', 'merchant', 'institution', 'arbitrage', 'retail', 'resource', 'policy']

# ── 参数表 (设计文档第4节) ──
ANCHOR_COEF = {
    'smelter': 1.2, 'merchant': 1.0, 'institution': 0.8, 'arbitrage': 0.6,
    'retail': 0.6, 'resource': 1.0, 'policy': 0.4,
}
ZONE_BASE = {
    'smelter': 1.5, 'merchant': 1.2, 'institution': 1.0, 'arbitrage': 0.8,
    'retail': 1.3, 'resource': 1.5, 'policy': 0.6,
}
ZONE_MOM_COEF = 2.0
W_MIN_FACTOR = 0.4  # w 下限保护: 0.4 * atr20

# ── 基本面敏感维度 (设计文档第3节) ──
SENSITIVITY = {
    'smelter':     {'profit': 0.6, 'inventory': 0.4},
    'merchant':    {'demand': 0.6, 'sentiment': 0.4},
    'institution': {'composite': 0.7, 'sentiment': 0.3},
    'arbitrage':   {'basis': 1.0},
    'retail':      {'sentiment': 0.8, 'composite': 0.2},
    'resource':    {'profit': 0.5, 'supply': 0.5},
    'policy':      {'composite': 1.0},
}

# ── 状态色 (设计文档第5节) ──
STATE_COLORS = {
    '惜售': '#58a6ff', '降价抛售': '#f85149', '正常销售': '#3fb950',
    '恐慌撤退': '#f0883e', '被套冻结': '#f85149', '积极采购': '#3fb950', '观望': '#d29922',
    '强趋势做多': '#3fb950', '强趋势做空': '#f85149', '震荡观望': '#d29922',
    '正套做多': '#3fb950', '反套做空': '#f85149',
    '追涨': '#3fb950', '恐慌追跌': '#f85149',
    '加速出货': '#f85149', '囤货惜售': '#58a6ff',
    '利好释放': '#3fb950', '利空压制': '#f85149', '稳定观望': '#d29922',
}

# ── score 状态加成 (设计文档第6节) ──
SCORE_BONUS = {
    '恐慌撤退': 15, '强趋势做空': 15, '加速出货': 15,
    '被套冻结': 10, '积极采购': 10, '强趋势做多': 10, '正套做多': 10,
    '囤货惜售': 8, '惜售': 8,
    '观望': -5, '稳定观望': -5, '正常销售': -5,
}

# ── 动作片段 (设计文档第7节) ──
ACTION_MAP = {
    'smelter':     {'惜售': '囤货待涨', '降价抛售': '被迫出货', '正常销售': '按需销售'},
    'merchant':    {'恐慌撤退': '暂停采购', '被套冻结': '解套前不动', '积极采购': '集中补库', '观望': '减缓采购'},
    'institution': {'强趋势做多': '跟随做多', '强趋势做空': '跟随做空', '震荡观望': '震荡减仓'},
    'arbitrage':   {'正套做多': '买近抛远', '反套做空': '买远抛近'},
    'resource':    {'加速出货': '高位出货', '囤货惜售': '低位囤货'},
}

CREATE_SQL = """
CREATE TABLE IF NOT EXISTS agent_psychology (
    date           TEXT    NOT NULL,
    agent_id       TEXT    NOT NULL,
    state          TEXT    NOT NULL,
    anchor_price   REAL,
    buy_zone_low   REAL,
    buy_zone_high  REAL,
    trigger_desc   TEXT,
    score          REAL,
    state_color    TEXT,
    PRIMARY KEY (date, agent_id)
);
CREATE INDEX IF NOT EXISTS idx_agent_psych_date ON agent_psychology(date);
CREATE INDEX IF NOT EXISTS idx_agent_psych_agent ON agent_psychology(agent_id);
"""


def clip(v, lo, hi):
    return max(lo, min(hi, v))


def ema_series(closes, period=10):
    """EMA(close, period)，种子=首个值"""
    if not closes:
        return []
    alpha = 2.0 / (period + 1.0)
    out = [closes[0]]
    for c in closes[1:]:
        out.append(alpha * c + (1 - alpha) * out[-1])
    return out


def compute_indicators(prows):
    """对全部价格行计算逐日指标（只用当日及之前数据）。prows: [{date, open, high, low, close, volume}]"""
    n = len(prows)
    closes = [p['close'] for p in prows]
    emas = ema_series(closes, 10)
    inds = []
    for i, p in enumerate(prows):
        c = p['close']

        def sma(win):
            lo = max(0, i - win + 1)
            seg = closes[lo:i + 1]
            return sum(seg) / len(seg) if seg else None

        ma5, ma10, ma20 = sma(5), sma(10), sma(20)

        # atr20: mean(|close[j]-close[j-1]|, 最近20个 j)
        j0 = max(1, i - 19)
        diffs = [abs(closes[j] - closes[j - 1]) for j in range(j0, i + 1)]
        atr20 = sum(diffs) / len(diffs) if diffs else 0.0

        mom5 = (c / closes[i - 5] - 1) if i >= 5 and closes[i - 5] else 0.0
        mom20 = (c / closes[i - 20] - 1) if i >= 20 and closes[i - 20] else 0.0
        mom1 = (c / closes[i - 1] - 1) if i >= 1 and closes[i - 1] else 0.0

        lo_i = max(0, i - 19)
        hi20 = max(prows[j]['high'] for j in range(lo_i, i + 1))
        lo20 = min(prows[j]['low'] for j in range(lo_i, i + 1))

        dev_ma20 = (c / ma20 - 1) if ma20 else 0.0

        inds.append({
            'date': p['date'], 'close': c, 'ma5': ma5, 'ma10': ma10, 'ma20': ma20,
            'ema10': emas[i], 'atr20': atr20,
            'mom5': mom5, 'mom20': mom20, 'mom1': mom1,
            'hi20': hi20, 'lo20': lo20, 'dev_ma20': dev_ma20,
        })
    return inds


def state_machine(agent, ind, fz, cost, position):
    """按设计文档第5节状态机判定（判断顺序即优先级），返回 (state, state_color, fact_fragment)"""
    c = ind['close']
    ma20 = ind['ma20'] or c
    mom5, mom20, mom1 = ind['mom5'], ind['mom20'], ind['mom1']
    dev = ind['dev_ma20']
    hi20 = ind['hi20']

    if agent == 'smelter':
        inv_z = fz.get('inventory', 0.0)
        if c > cost * 1.05 and inv_z < 0.5:
            return '惜售', STATE_COLORS['惜售'], '价高于成本{:.0%}'.format(c / cost - 1)
        if c < cost * 0.97 or (inv_z > 1.0 and dev < 0):
            if c < cost * 0.97:
                fact = '价低于成本{:.0%}'.format(1 - c / cost)
            else:
                fact = '库存{:.1f}σ'.format(inv_z)
            return '降价抛售', STATE_COLORS['降价抛售'], fact
        return '正常销售', STATE_COLORS['正常销售'], '20日偏离{:.0%}'.format(dev)

    if agent == 'merchant':
        if mom5 < -0.06:
            return '恐慌撤退', STATE_COLORS['恐慌撤退'], '近5日跌{:.0%}'.format(-mom5)
        if position > 0 and c < ma20 * 0.98:
            return '被套冻结', STATE_COLORS['被套冻结'], '20日偏离{:.0%}'.format(dev)
        if c <= ind['buy_zone_high'] and mom5 > -0.02:
            fact = '近5日涨{:.0%}'.format(mom5) if mom5 > 0 else '20日偏离{:.0%}'.format(dev)
            return '积极采购', STATE_COLORS['积极采购'], fact
        fact = '近5日跌{:.0%}'.format(-mom5) if mom5 < 0 else '20日偏离{:.0%}'.format(dev)
        return '观望', STATE_COLORS['观望'], fact

    if agent == 'institution':
        if mom20 > 0.06:
            return '强趋势做多', STATE_COLORS['强趋势做多'], '近20日涨{:.0%}'.format(mom20)
        if mom20 < -0.06:
            return '强趋势做空', STATE_COLORS['强趋势做空'], '近20日跌{:.0%}'.format(-mom20)
        fact = '近20日涨{:.0%}'.format(mom20) if mom20 > 0 else '近20日跌{:.0%}'.format(-mom20)
        return '震荡观望', STATE_COLORS['震荡观望'], fact

    if agent == 'arbitrage':
        basis_z = fz.get('basis', 0.0)
        if basis_z > 0.5:
            return '正套做多', STATE_COLORS['正套做多'], '升水{basis_z:.1f}σ'.format(basis_z=basis_z)
        if basis_z < -0.5:
            return '反套做空', STATE_COLORS['反套做空'], '贴水{basis_z:.1f}σ'.format(basis_z=basis_z)
        return '观望', STATE_COLORS['观望'], '基差{basis_z:.1f}σ'.format(basis_z=basis_z)

    if agent == 'retail':
        sent_z = fz.get('sentiment', 0.0)
        if mom5 > 0.04 and sent_z > -0.5:
            return '追涨', STATE_COLORS['追涨'], '近5日涨{:.0%}'.format(mom5)
        if mom5 < -0.04 and sent_z < 0.5:
            return '恐慌追跌', STATE_COLORS['恐慌追跌'], '近5日跌{:.0%}'.format(-mom5)
        fact = '近5日跌{:.0%}'.format(-mom5) if mom5 < 0 else '近5日涨{:.0%}'.format(mom5)
        return '观望', STATE_COLORS['观望'], fact

    if agent == 'resource':
        if dev > 0.05 or c > hi20 * 0.98:
            return '加速出货', STATE_COLORS['加速出货'], '20日偏离{:.0%}'.format(dev)
        if mom20 < -0.05 and dev < -0.03:
            return '囤货惜售', STATE_COLORS['囤货惜售'], '近20日跌{:.0%}'.format(-mom20)
        return '正常销售', STATE_COLORS['正常销售'], '20日偏离{:.0%}'.format(dev)

    if agent == 'policy':
        comp_z = fz.get('composite', 0.0)
        if mom1 > 0.04 and comp_z > 0.5:
            return '利好释放', STATE_COLORS['利好释放'], '单日涨{:.0%}'.format(mom1)
        if mom1 < -0.04 and comp_z < -0.5:
            return '利空压制', STATE_COLORS['利空压制'], '单日跌{:.0%}'.format(-mom1)
        return '稳定观望', STATE_COLORS['稳定观望'], '20日偏离{:.0%}'.format(dev)

    raise ValueError('unknown agent: ' + agent)


def build_action(agent, state, mom5):
    """设计文档第7节动作片段: 动量动作 + 主体状态动作"""
    mom_action = '下调锚点/区间收缩' if mom5 < 0 else '上调锚点/区间扩张'
    agent_action = ACTION_MAP.get(agent, {}).get(state)
    if agent_action:
        # merchant 观望按示例 "下调锚点/减缓采购"（动量在前）
        if agent == 'merchant' and state == '观望':
            return f"{mom_action}/{agent_action}"
        return f"{agent_action}/{mom_action}"
    return mom_action


def compute_score(state, mom5):
    """设计文档第6节 score (0-100)"""
    s = 50.0 + clip(mom5 * 300.0, -20.0, 20.0)
    s += SCORE_BONUS.get(state, 0)
    return round(clip(s, 10.0, 95.0), 1)


def main():
    ap = argparse.ArgumentParser(description='主体心理状态与心理价位带计算')
    ap.add_argument('--days', type=int, default=None, help='只计算最近N个交易日')
    ap.add_argument('--date', type=str, default=None, help='只计算单日 YYYY-MM-DD（增量）')
    args = ap.parse_args()

    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    conn.executescript(CREATE_SQL)
    conn.commit()

    prows = [dict(r) for r in conn.execute(
        "SELECT date, open, high, low, close, volume FROM prices ORDER BY date").fetchall()]
    if not prows:
        print("prices 表为空，无法计算"); sys.exit(1)

    inds = compute_indicators(prows)
    ind_by_date = {d['date']: d for d in inds}

    # 基本面 z-score: {date: {dim: z}}，缺失维度按 0 处理
    fund_map = {}
    for r in conn.execute(
            "SELECT date, supply, demand, inventory, profit, sentiment, basis, composite FROM fundamental_indices"):
        fund_map[r[0]] = {
            'supply': r[1] or 0.0, 'demand': r[2] or 0.0, 'inventory': r[3] or 0.0,
            'profit': r[4] or 0.0, 'sentiment': r[5] or 0.0, 'basis': r[6] or 0.0,
            'composite': r[7] or 0.0,
        }

    # agent_history: {date: {agent_id: {position, avg_cost}}}
    hist = {}
    for r in conn.execute("SELECT agent_id, date, position, avg_cost FROM agent_history"):
        hist.setdefault(r[1], {})[r[0]] = {'position': r[2] or 0, 'avg_cost': r[3] or 0}

    # 目标日期: agent_history 最早→最新，与 prices 对齐
    target = sorted(d for d in hist if d in ind_by_date)
    if args.date:
        if args.date in ind_by_date:
            target = [args.date]
        else:
            print(f"[warn] {args.date} 不在 prices 交易日中，跳过")
            target = []
    elif args.days:
        target = target[-args.days:]

    rows_to_write = []
    for d in target:
        ind = ind_by_date[d]
        fz = fund_map.get(d, {})
        agent_rows = hist.get(d, {})
        for agent in AGENTS:
            ar = agent_rows.get(agent) or {}
            avg_cost = ar.get('avg_cost') or 0
            cost = avg_cost if avg_cost > 0 else (ind['ma20'] or ind['close'])  # cost=avg_cost>0 否则 ma20 代理
            position = ar.get('position') or 0

            # bias: 敏感维度加权 z-score
            bias = 0.0
            for dim, w in SENSITIVITY[agent].items():
                bias += w * fz.get(dim, 0.0)

            # 锚点与价位带（设计文档第4节）
            anchor = ind['ema10'] + bias * ANCHOR_COEF[agent] * ind['atr20']
            w = ZONE_BASE[agent] * ind['atr20'] * (1 + ZONE_MOM_COEF * ind['mom5'])
            w = max(w, W_MIN_FACTOR * ind['atr20'])
            zone_low = anchor - w
            zone_high = anchor + w
            ind['buy_zone_high'] = zone_high  # merchant 状态机用到

            state, color, fact = state_machine(agent, ind, fz, cost, position)
            score = compute_score(state, ind['mom5'])
            trigger = f"{fact}→{state}→{build_action(agent, state, ind['mom5'])}"

            rows_to_write.append((d, agent, state, anchor, zone_low, zone_high, trigger, score, color))

    conn.executemany("""
        INSERT OR REPLACE INTO agent_psychology
            (date, agent_id, state, anchor_price, buy_zone_low, buy_zone_high, trigger_desc, score, state_color)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, rows_to_write)
    conn.commit()

    print(f"写入 {len(rows_to_write)} 行 (日期 {target[0] if target else '-'} ~ {target[-1] if target else '-'})")
    if rows_to_write:
        last = rows_to_write[-1]
        print(f"最新一行示例: date={last[0]} agent={last[1]} state={last[2]} "
              f"anchor={last[3]:.0f} zone=[{last[4]:.0f},{last[5]:.0f}] "
              f"score={last[7]} trigger={last[6]}")
    conn.close()


if __name__ == '__main__':
    main()
