#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
席位交叉分析 API — 席位净持仓排名 + 席位持仓时间序列趋势

核心功能:
  - 按席位名称聚合所有合约上的总多/总空/净持仓
  - 席位持仓时间序列趋势（支持多合约切换）
  - 席位多空对比雷达图（净持仓排名）
  - 投机 vs 套保识别提示（长期单边持仓占比）
"""

import sqlite3
from flask import Blueprint, jsonify, request

seat_bp = Blueprint('seat_cross', __name__, url_prefix='/api/seat_cross')

LC_DB = '/home/ubuntu/lc_futures_data/data/lc_position.db'

# 预定义要分析的合约组合（全品种聚合 vs 单合约）
DEFAULT_CONTRACTS = ['LC2609', 'LC2611', 'LC2612', 'LC2701', 'LC2705']


def _get_conn():
    conn = sqlite3.connect(LC_DB)
    conn.row_factory = sqlite3.Row
    return conn


def _aggregate_seats(conn, symbols, aggregate_all=False):
    """聚合席位持仓数据"""
    if aggregate_all:
        # 跨所有合约聚合
        query = """
            SELECT
                long_party as party_name,
                SUM(long_oi) as total_long,
                COUNT(DISTINCT date) as long_days
            FROM (SELECT DISTINCT date, symbol, long_party, long_oi
                  FROM position_rank WHERE variety='LC')
            GROUP BY long_party
        """
        query2 = """
            SELECT
                short_party as party_name,
                SUM(short_oi) as total_short,
                COUNT(DISTINCT date) as short_days
            FROM (SELECT DISTINCT date, symbol, short_party, short_oi
                  FROM position_rank WHERE variety='LC')
            GROUP BY short_party
        """
    else:
        placeholders = ','.join(['?' for _ in symbols])
        query = f"""
            SELECT
                long_party as party_name,
                SUM(long_oi) as total_long,
                COUNT(DISTINCT date) as long_days
            FROM (SELECT DISTINCT date, symbol, long_party, long_oi
                  FROM position_rank WHERE symbol IN ({placeholders}))
            GROUP BY long_party
        """
        query2 = f"""
            SELECT
                short_party as party_name,
                SUM(short_oi) as total_short,
                COUNT(DISTINCT date) as short_days
            FROM (SELECT DISTINCT date, symbol, short_party, short_oi
                  FROM position_rank WHERE symbol IN ({placeholders}))
            GROUP BY short_party
        """

    longs = {r['party_name']: r['total_long'] for r in conn.execute(query, symbols).fetchall()}
    shorts = {r['party_name']: r['total_short'] for r in conn.execute(query2, symbols).fetchall()}

    all_names = sorted(set(list(longs.keys()) + list(shorts.keys())),
                       key=lambda x: (longs.get(x, 0) + shorts.get(x, 0)), reverse=True)

    result = []
    for name in all_names:
        l = longs.get(name, 0)
        s = shorts.get(name, 0)
        result.append({
            'party_name': name,
            'total_long': l,
            'total_short': s,
            'net': l - s,
            'long_days': conn.execute(
                "SELECT COUNT(DISTINCT date) FROM (SELECT DISTINCT date, long_party FROM position_rank WHERE symbol IN ({}) AND long_party=?)".format(
                    ','.join(['?' for _ in symbols]), name), symbols + [name]).fetchone()[0] if not aggregate_all else conn.execute(
                "SELECT COUNT(DISTINCT date) FROM (SELECT DISTINCT date, long_party FROM position_rank WHERE long_party=?)", [name]).fetchone()[0],
            'short_days': conn.execute(
                "SELECT COUNT(DISTINCT date) FROM (SELECT DISTINCT date, short_party FROM position_rank WHERE symbol IN ({}) AND short_party=?)".format(
                    ','.join(['?' for _ in symbols]), name), symbols + [name]).fetchone()[0] if not aggregate_all else conn.execute(
                "SELECT COUNT(DISTINCT date) FROM (SELECT DISTINCT date, short_party FROM position_rank WHERE short_party=?)", [name]).fetchone()[0],
        })

    return result


def _get_seat_trend(conn, party_name, symbols):
    """获取席位持仓时间序列"""
    placeholders = ','.join(['?' for _ in symbols])
    query = f"""
        SELECT date, long_party, long_oi, short_party, short_oi
        FROM position_rank
        WHERE symbol IN ({placeholders})
    """
    rows = conn.execute(query, symbols).fetchall()

    dates = sorted(set(r['date'] for r in rows))
    long_trend = []
    short_trend = []
    for date in dates:
        l_total = 0
        s_total = 0
        for r in rows:
            if r['date'] == date:
                if r['long_party'] == party_name:
                    l_total += r['long_oi']
                if r['short_party'] == party_name:
                    s_total += r['short_oi']
        long_trend.append(l_total)
        short_trend.append(s_total)

    return {
        'dates': dates,
        'long_trend': long_trend,
        'short_trend': short_trend,
        'net_trend': [l - s for l, s in zip(long_trend, short_trend)]
    }


@seat_bp.route('')
def seat_cross_analysis():
    """席位交叉分析主API"""
    symbols = request.args.get('contracts', ','.join(DEFAULT_CONTRACTS)).split(',')
    symbols = [s.strip() for s in symbols if s.strip()]
    if not symbols:
        symbols = DEFAULT_CONTRACTS

    conn = _get_conn()
    try:
        # 聚合席位数据
        seat_agg = _aggregate_seats(conn, symbols)

        # 最新日期
        latest = conn.execute(
            "SELECT date FROM position_rank WHERE variety='LC' ORDER BY date DESC LIMIT 1"
        ).fetchone()
        latest_date = latest['date'] if latest else 'N/A'

        # 获取前15席位的趋势
        top_seats = [s['party_name'] for s in seat_agg[:15]]
        trends = {}
        for seat in top_seats:
            trends[seat] = _get_seat_trend(conn, seat, symbols)

        # 投机/套保识别提示（根据持仓模式判断）
        hints = []
        for s in seat_agg[:10]:
            total = s['total_long'] + s['total_short']
            if total == 0:
                continue
            long_ratio = s['total_long'] / total
            if s['total_short'] == 0 and s['long_days'] > 30:
                hints.append({
                    'party_name': s['party_name'],
                    'type': '单边看多',
                    'reason': f'仅持有{long_ratio*100:.0f}%多头，无空单，{s["long_days"]}天持续存在',
                    'net': s['net']
                })
            elif s['total_long'] == 0 and s['short_days'] > 30:
                hints.append({
                    'party_name': s['party_name'],
                    'type': '单边看空',
                    'reason': f'仅持有{100-long_ratio*100:.0f}%空头，无多单，{s["short_days"]}天持续存在',
                    'net': s['net']
                })
            elif abs(s['net']) / total > 0.3:
                hints.append({
                    'party_name': s['party_name'],
                    'type': '偏方向性',
                    'reason': f'净持仓占比{(abs(s["net"])/total)*100:.0f}%，方向性较强' if s['net'] > 0 else f'净空占比{(abs(s["net"])/total)*100:.0f}%，偏空',
                    'net': s['net']
                })

        return jsonify({
            'symbols': symbols,
            'latest_date': latest_date,
            'seat_agg': seat_agg,
            'trends': trends,
            'hints': hints,
            'total_seats': len(seat_agg)
        })
    finally:
        conn.close()


@seat_bp.route('/trend')
def seat_trend():
    """单个席位的时间序列趋势"""
    party = request.args.get('party', '')
    symbols = request.args.get('contracts', ','.join(DEFAULT_CONTRACTS)).split(',')
    symbols = [s.strip() for s in symbols if s.strip()]
    if not symbols:
        symbols = DEFAULT_CONTRACTS

    if not party:
        return jsonify({'error': 'missing party parameter'})

    conn = _get_conn()
    try:
        trend = _get_seat_trend(conn, party, symbols)
        return jsonify({
            'party': party,
            'symbols': symbols,
            'dates': trend['dates'],
            'long_trend': trend['long_trend'],
            'short_trend': trend['short_trend'],
            'net_trend': trend['net_trend']
        })
    finally:
        conn.close()


@seat_bp.route('/all_parties')
def all_party_names():
    """获取所有席位的名单"""
    conn = _get_conn()
    try:
        longs = {r[0] for r in conn.execute(
            "SELECT DISTINCT long_party FROM position_rank WHERE long_party IS NOT NULL AND variety='LC'"
        ).fetchall()}
        shorts = {r[0] for r in conn.execute(
            "SELECT DISTINCT short_party FROM position_rank WHERE short_party IS NOT NULL AND variety='LC'"
        ).fetchall()}
        return jsonify({'parties': sorted(list(longs | shorts))})
    finally:
        conn.close()
