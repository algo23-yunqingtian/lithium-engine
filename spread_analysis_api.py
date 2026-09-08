#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
月差分析增强 API — 正反套信号识别 + 持仓联动视图 + 多合约对支持 v2

新增能力:
  - 支持任意合约对组合查询 (通过 contracts 参数)
  - 自动预定义常见合约对: 08-09, 08-11, 08-12, 09-10, 09-11, 09-12, 09-01, 09-05,
    10-11, 10-12, 11-12, 11-01, 12-01 等
  - 日期格式自动对齐 (价格 DB: YYYY-MM-DD, 持仓 DB: YYYYMMDD)
  - 无价格数据时也能展示持仓+信号
  - 返回各合约的仓单数据 (lc_position.db)
"""

import sqlite3
import datetime
from flask import jsonify, request

LITHIUM_DB = '/home/ubuntu/lithium_calendar/lithium.db'
LC_DB = '/home/ubuntu/lc_futures_data/data/lc_position.db'

# 预定义合约对 (按关注度排序)
DEFAULT_SPREAD_PAIRS = [
    ('LC2609', 'LC2611'),  # 主力换月
    ('LC2609', 'LC2612'),  # 近远月
    ('LC2611', 'LC2612'),  # 次主力
    ('LC2609', 'LC2701'),  # 09-01 (跨年)
    ('LC2609', 'LC2705'),  # 09-05
    ('LC2611', 'LC2701'),  # 11-01
    ('LC2612', 'LC2701'),  # 12-01
    ('LC2608', 'LC2609'),  # 08-09
    ('LC2608', 'LC2611'),  # 08-11
    ('LC2608', 'LC2612'),  # 08-12
    ('LC2609', 'LC2610'),  # 09-10
    ('LC2610', 'LC2611'),  # 10-11
    ('LC2610', 'LC2612'),  # 10-12
    ('LC2611', 'LC2705'),  # 11-05
    ('LC2701', 'LC2705'),  # 01-05
]


def _get_price_db():
    conn = sqlite3.connect(LITHIUM_DB)
    conn.row_factory = sqlite3.Row
    return conn


def _get_pos_db():
    conn = sqlite3.connect(LC_DB)
    conn.row_factory = sqlite3.Row
    return conn


def _normalize_price_date(d):
    """统一为 YYYYMMDD 格式"""
    if '-' in str(d):
        return d.replace('-', '')
    return str(d)


def _normalize_pos_date(d):
    """统一为 YYYYMMDD 格式"""
    s = str(d).strip()
    if len(s) == 10 and s[4] == '-' and s[7] == '-':
        return s.replace('-', '')
    return s


def _fmt_sign(val):
    if val >= 0:
        return "+{}".format(val)
    return "{}".format(val)


def _signal_to_cn(signal):
    mapping = {
        'positive_build': '正套建仓',
        'positive_close': '正套平仓',
        'negative_build': '反套建仓',
        'negative_close': '反套平仓',
        'both_long': '双双增多',
        'both_short': '双双增空',
        'neutral': '观望'
    }
    return mapping.get(signal, signal)


def _month_sort_key(symbol):
    """排序 key: LC2609 -> (2026, 9)"""
    ym = symbol[2:]
    if len(ym) != 4:
        return (999, 99)
    year = int('20' + ym[:2]) if int(ym[:2]) < 50 else int('19' + ym[:2])
    month = int(ym[2:])
    return (year, month)


def api_spread_analysis():
    """月差分析增强 API"""
    
    # 获取请求参数: 自定义合约列表
    contracts_param = request.args.get('contracts', None)
    
    if contracts_param:
        raw = [c.strip() for c in contracts_param.split(',') if c.strip()]
        # 解析为合约对 (相邻配对)
        contracts = sorted(set(raw), key=_month_sort_key)
        custom_pairs = []
        for i in range(len(contracts) - 1):
            custom_pairs.append((contracts[i], contracts[i + 1]))
        pairs_to_process = custom_pairs
    else:
        # 使用默认合约对
        pairs_to_process = DEFAULT_SPREAD_PAIRS
    
    # ========== 1. 批量读取价格数据 ==========
    conn = _get_price_db()
    price_cache = {}
    for sp in pairs_to_process:
        for sym in sp:
            if sym not in price_cache:
                rows = conn.execute(
                    "SELECT date, settle FROM contract_prices WHERE contract=? ORDER BY date",
                    (sym,)
                ).fetchall()
                if rows:
                    price_cache[sym] = {
                        _normalize_price_date(r['date']): float(r['settle'])
                        for r in rows
                    }
    conn.close()
    
    # ========== 2. 批量读取持仓数据 ==========
    lc = _get_pos_db()
    pos_cache = {}
    for sp in pairs_to_process:
        for sym in sp:
            if sym not in pos_cache:
                rows = lc.execute(
                    "SELECT date, SUM(long_oi) as long_oi, SUM(short_oi) as short_oi "
                    "FROM position_rank WHERE symbol=? GROUP BY date ORDER BY date",
                    (sym.upper(),)
                ).fetchall()
                if rows:
                    pos_cache[sym] = {
                        _normalize_pos_date(r['date']): dict(r)
                        for r in rows
                    }
    
    # 读取仓单数据 (仓单按产品汇总, 不分合约, 取 LC 相关符号的总仓单)
    wr_total_cache = {}
    wr_rows = lc.execute(
        "SELECT date, SUM(today_qty) as total FROM warehouse_receipt "
        "GROUP BY date ORDER BY date"
    ).fetchall()
    for r in wr_rows:
        wr_total_cache[_normalize_pos_date(r['date'])] = int(r['total'])
    lc.close()
    
    # ========== 3. 构建各合约对数据 ==========
    spread_pairs = []
    signal_table = {}
    position_cross = {}
    latest_summary = []
    
    for idx, (near, far) in enumerate(pairs_to_process):
        pair_key = "{}-{}".format(near, far)
        near_short = near[-4:]
        far_short = far[-4:]
        pair_label = "{}-{}".format(near_short, far_short)
        
        has_price = near in price_cache and far in price_cache
        has_pos = near in pos_cache and far in pos_cache
        
        # --- 3a. 月差数据 ---
        spread_data = []
        if has_price:
            nd = price_cache[near]
            fd = price_cache[far]
            common = sorted(set(nd.keys()) & set(fd.keys()))
            
            for d in common:
                np_ = nd[d]
                fp_ = fd[d]
                sp_ = np_ - fp_
                sp_pct = (sp_ / fp_) * 10000 if fp_ else 0
                spread_data.append({
                    'date': d,
                    'near_price': np_,
                    'far_price': fp_,
                    'spread': round(sp_, 1),
                    'spread_pct': round(sp_pct, 1),
                    'structure': 'contango' if sp_ > 0 else 'backwardation'
                })
        
        spreads = [s['spread'] for s in spread_data]
        latest_sp = spread_data[-1] if spread_data else {}
        prev_sp = spread_data[-2] if len(spread_data) > 1 else {}
        spread_chg = latest_sp.get('spread', 0) - prev_sp.get('spread', 0) if latest_sp and prev_sp else 0
        
        # --- 3b. 信号识别 + 持仓交叉分析 ---
        daily_signals = []
        daily_cross = []
        threshold = 500
        
        if has_pos:
            nd_pos = pos_cache[near]
            fd_pos = pos_cache[far]
            common_dates = sorted(set(nd_pos.keys()) & set(fd_pos.keys()))
            
            for ci, d in enumerate(common_dates):
                n = nd_pos[d]
                f = fd_pos[d]
                
                if ci > 0:
                    pd_ = common_dates[ci - 1]
                    n_prev = nd_pos[pd_]
                    f_prev = fd_pos[pd_]
                else:
                    n_prev = n
                    f_prev = f
                
                n_long_chg = n['long_oi'] - n_prev['long_oi']
                n_short_chg = n['short_oi'] - n_prev['short_oi']
                f_long_chg = f['long_oi'] - f_prev['long_oi']
                f_short_chg = f['short_oi'] - f_prev['short_oi']
                n_net = n_long_chg - n_short_chg
                f_net = f_long_chg - f_short_chg
                
                # 信号判定
                signal = 'neutral'
                strength = 'weak'
                
                if n_long_chg > threshold and f_short_chg > threshold:
                    signal = 'positive_build'
                    strength = 'strong' if (n_long_chg > 2000 and f_short_chg > 2000) else 'moderate'
                elif n_long_chg < -threshold and f_short_chg < -threshold:
                    signal = 'positive_close'
                    strength = 'strong' if (abs(n_long_chg) > 2000 and abs(f_short_chg) > 2000) else 'moderate'
                elif n_short_chg > threshold and f_long_chg > threshold:
                    signal = 'negative_build'
                    strength = 'strong' if (n_short_chg > 2000 and f_long_chg > 2000) else 'moderate'
                elif n_short_chg < -threshold and f_long_chg < -threshold:
                    signal = 'negative_close'
                    strength = 'strong' if (abs(n_short_chg) > 2000 and abs(f_long_chg) > 2000) else 'moderate'
                elif n_long_chg > threshold and f_long_chg > threshold:
                    signal = 'both_long'
                elif n_short_chg > threshold and f_short_chg > threshold:
                    signal = 'both_short'
                
                # 解释
                if signal.startswith('positive_'):
                    exp = "近月多{:+} 远月空{:+} -> {}".format(n_long_chg, f_short_chg, _signal_to_cn(signal))
                elif signal.startswith('negative_'):
                    exp = "近月空{:+} 远月多{:+} -> {}".format(n_short_chg, f_long_chg, _signal_to_cn(signal))
                elif signal == 'both_long':
                    exp = "近月多{:+} 远月多{:+} -> 双双增多".format(n_long_chg, f_long_chg)
                elif signal == 'both_short':
                    exp = "近月空{:+} 远月空{:+} -> 双双增空".format(n_short_chg, f_short_chg)
                else:
                    exp = "近月净{:+} 远月净{:+}".format(n_net, f_net)
                
                daily_signals.append({
                    'date': d,
                    'signal': signal,
                    'signal_cn': _signal_to_cn(signal),
                    'strength': strength,
                    'near_long_chg': n_long_chg,
                    'near_short_chg': n_short_chg,
                    'far_long_chg': f_long_chg,
                    'far_short_chg': f_short_chg,
                    'near_oi': n['long_oi'],
                    'near_short': n['short_oi'],
                    'far_oi': f['long_oi'],
                    'far_short': f['short_oi'],
                    'near_net': n_long_chg - n_short_chg,
                    'far_net': f_long_chg - f_short_chg,
                    'explanation': exp
                })
                
                # 持仓交叉分析
                near_total = n['long_oi'] + n['short_oi']
                far_total = f['long_oi'] + f['short_oi']
                near_total_prev = n_prev['long_oi'] + n_prev['short_oi']
                far_total_prev = f_prev['long_oi'] + f_prev['short_oi']
                
                cross_sig = 'neutral'
                cross_desc = '持仓变化不显著'
                if near_total - near_total_prev > threshold and far_total - far_total_prev < -threshold:
                    cross_sig = 'near_favor'
                    cross_desc = '资金流向近月'
                elif near_total - near_total_prev < -threshold and far_total - far_total_prev > threshold:
                    cross_sig = 'far_favor'
                    cross_desc = '资金流向远月'
                elif near_total - near_total_prev > threshold and far_total - far_total_prev > threshold:
                    cross_sig = 'both_growing'
                    cross_desc = '双合约均增仓'
                elif near_total - near_total_prev < -threshold and far_total - far_total_prev < -threshold:
                    cross_sig = 'both_shrinking'
                    cross_desc = '双合约均减仓'
                
                daily_cross.append({
                    'date': d,
                    'cross_signal': cross_sig,
                    'cross_desc': cross_desc,
                    'near_oi': near_total,
                    'far_oi': far_total,
                    'near_oi_chg': near_total - near_total_prev,
                    'far_oi_chg': far_total - far_total_prev,
                    'net_position_diff': (n['long_oi'] - n['short_oi']) - (f['long_oi'] - f['short_oi'])
                })
        
        # --- 3c. 各合约详情 (供前端持仓详情区块使用) ---
        near_detail = _build_contract_detail(near, pos_cache, wr_total_cache)
        far_detail = _build_contract_detail(far, pos_cache, wr_total_cache)
        
        pair_info = {
            'near': near,
            'far': far,
            'pair_label': pair_label,
            'has_price': has_price,
            'has_position': has_pos,
            'data': spread_data,
            'latest_spread': latest_sp.get('spread', 0),
            'spread_change': round(spread_chg, 1),
            'structure': latest_sp.get('structure', 'unknown'),
            'near_detail': near_detail,
            'far_detail': far_detail,
            'stats': _calc_stats(spreads)
        }
        spread_pairs.append(pair_info)
        
        signal_table[pair_key] = {
            'dates': [s['date'] for s in daily_signals],
            'signals': daily_signals,
            'near': near,
            'far': far
        }
        position_cross[pair_key] = {
            'dates': [c['date'] for c in daily_cross],
            'data': daily_cross
        }
        
        # --- 3d. 最新摘要 ---
        latest_sig = daily_signals[-1] if daily_signals else None
        latest_xr = daily_cross[-1] if daily_cross else None
        latest_summary.append({
            'pair': pair_key,
            'pair_label': pair_label,
            'spread': latest_sp.get('spread', 0),
            'spread_pct': latest_sp.get('spread_pct', 0),
            'structure': latest_sp.get('structure', 'unknown'),
            'spread_change': round(spread_chg, 1),
            'near_oi': near_detail.get('latest_oi'),
            'far_oi': far_detail.get('latest_oi'),
            'near_wr': near_detail.get('latest_wr'),
            'far_wr': far_detail.get('latest_wr'),
            'latest_signal': latest_sig,
            'latest_cross': latest_xr
        })
    
    return jsonify({
        'spread_pairs': spread_pairs,
        'signal_table': signal_table,
        'position_cross': position_cross,
        'latest_summary': latest_summary,
        'pairs_config': [{'near': p[0], 'far': p[1], 'label': "{}-{}".format(p[0][-4:], p[1][-4:])} for p in pairs_to_process]
    })


def _build_contract_detail(symbol, pos_cache, wr_total_cache):
    """构建单合约详情"""
    detail = {'symbol': symbol}
    
    if symbol in pos_cache:
        dates = sorted(pos_cache[symbol].keys())
        latest = dates[-1] if dates else None
        detail['latest_date'] = latest
        if latest:
            p = pos_cache[symbol][latest]
            detail['latest_oi'] = p['long_oi'] + p['short_oi']
            detail['latest_long_oi'] = p['long_oi']
            detail['latest_short_oi'] = p['short_oi']
            detail['latest_net'] = p['long_oi'] - p['short_oi']
            # 最近10日趋势
            recent = []
            for d in dates[-10:]:
                r = pos_cache[symbol][d]
                recent.append({'date': d, 'long': r['long_oi'], 'short': r['short_oi'], 'net': r['long_oi'] - r['short_oi']})
            detail['recent_10d'] = recent
            # 全量OI序列
            detail['oi_series'] = [{'date': d, 'long': pos_cache[symbol][d]['long_oi'], 'short': pos_cache[symbol][d]['short_oi']} for d in dates]
        else:
            detail['latest_oi'] = 0
            detail['latest_long_oi'] = 0
            detail['latest_short_oi'] = 0
            detail['latest_net'] = 0
            detail['recent_10d'] = []
            detail['oi_series'] = []
    else:
        detail['latest_oi'] = 0
        detail['latest_long_oi'] = 0
        detail['latest_short_oi'] = 0
        detail['latest_net'] = 0
        detail['recent_10d'] = []
        detail['oi_series'] = []
    
    # 仓单数据: 按产品汇总(不分合约), 所有 LC 合约共享同一仓单
    if wr_total_cache:
        wr_dates = sorted(wr_total_cache.keys())
        detail['latest_wr'] = wr_total_cache[wr_dates[-1]]
        detail['recent_wr'] = [{'date': d, 'qty': wr_total_cache[d]} for d in wr_dates[-10:]]
    else:
        detail['latest_wr'] = 0
        detail['recent_wr'] = []
    
    return detail


def _calc_stats(spreads):
    if not spreads:
        return {'avg': 0, 'max': 0, 'min': 0, 'points': 0}
    return {
        'avg': round(sum(spreads) / len(spreads), 1),
        'max': round(max(spreads), 1),
        'min': round(min(spreads), 1),
        'points': len(spreads)
    }


def api_date_spreads():
    """根据日期返回该日所有上市合约的结算价，用于月差曲线分析
    
    参数: date (YYYY-MM-DD), limit_months (默认12个月)
    返回: 该日所有合约的结算价列表 + 月差曲线数据
    """
    from flask import request
    date = request.args.get('date', '')
    limit_months = int(request.args.get('limit_months', 12))
    
    if not date:
        return jsonify({'error': 'date 参数必填'}), 400
    
    # 从 contract_prices 读取该日所有合约
    conn = sqlite3.connect('/home/ubuntu/lithium_calendar/lithium.db')
    rows = conn.execute(
        "SELECT contract, settle FROM contract_prices WHERE date = ? ORDER BY contract",
        (date,)
    ).fetchall()
    conn.close()
    
    if not rows:
        return jsonify({'error': f'日期 {date} 无数据', 'date': date, 'contracts': []}), 404
    
    # Parse: rows are (contract, settle) tuples
    contracts = []
    for r in rows:
        sym = r[0]
        settle = float(r[1])
        if settle <= 0:
            continue
        # 解析: LC2609 -> (2026, 9)
        ym = sym[2:]
        if len(ym) == 4:
            year = int('20' + ym[:2]) if int(ym[:2]) < 50 else int('19' + ym[:2])
            month = int(ym[2:])
            contracts.append({
                'symbol': sym,
                'year': year,
                'month': month,
                'settle': settle,
                'sort_key': (year, month)
            })
    
    # 按月份排序
    contracts.sort(key=lambda x: x['sort_key'])
    
    # 只取最近的 limit_months 个
    contracts = contracts[-limit_months:]
    
    # 构建月差数据：相邻合约价差
    spread_data = []
    if len(contracts) >= 2:
        for i in range(len(contracts) - 1):
            near = contracts[i]
            far = contracts[i + 1]
            spread = near['settle'] - far['settle']
            spread_pct = (spread / far['settle']) * 10000 if far['settle'] else 0
            structure = 'contango' if spread > 0 else 'backwardation'
            spread_data.append({
                'near_symbol': near['symbol'],
                'far_symbol': far['symbol'],
                'near_settle': near['settle'],
                'far_settle': far['settle'],
                'spread': round(spread, 1),
                'spread_pct': round(spread_pct, 1),
                'structure': structure
            })
    
    # 构建曲线数据（按月份排列的结算价）
    curve_data = {
        'dates': [c['symbol'] for c in contracts],
        'settle_prices': [c['settle'] for c in contracts],
        'months': [f"{c['year']}-{c['month']:02d}" for c in contracts],
    }
    
    return jsonify({
        'date': date,
        'contracts': contracts,
        'spread_data': spread_data,
        'curve_data': curve_data,
        'count': len(contracts)
    })


def api_historical_spreads():
    """返回最近N个交易日的月差数据，用于前端叠加显示历史曲线

    参数: n_days (默认5), before_date (可选, YYYY-MM-DD)
    返回: [{date, contracts: [{symbol, settle}]}, ...]
    before_date 传入时，返回该日期之前最近 n_days 个交易日（相对基准日期），
    保证任意历史日期都能取到 T-1~T-N；不传时保持原行为（全局最近 n_days）。
    """
    from flask import request
    n_days = int(request.args.get('n_days', 5))
    before_date = request.args.get('before_date', '').strip()

    conn = sqlite3.connect('/home/ubuntu/lithium_calendar/lithium.db')

    # 取最近N个有数据的日期（支持相对某日期之前）
    if before_date:
        date_rows = conn.execute(
            "SELECT DISTINCT date FROM contract_prices WHERE date < ? ORDER BY date DESC LIMIT ?",
            (before_date, n_days)
        ).fetchall()
    else:
        date_rows = conn.execute(
            "SELECT DISTINCT date FROM contract_prices ORDER BY date DESC LIMIT ?",
            (n_days,)
        ).fetchall()

    dates = [d[0] for d in date_rows]
    # 反过来排序，从远到近
    dates.reverse()

    result = []
    for d in dates:
        rows = conn.execute(
            "SELECT contract, settle FROM contract_prices WHERE date = ? ORDER BY contract",
            (d,)
        ).fetchall()
        contracts = []
        for r in rows:
            sym = r[0]
            settle = float(r[1])
            if settle <= 0:
                continue
            ym = sym[2:]
            if len(ym) == 4:
                year = int('20' + ym[:2]) if int(ym[:2]) < 50 else int('19' + ym[:2])
                month = int(ym[2:])
                contracts.append({
                    'symbol': sym,
                    'year': year,
                    'month': month,
                    'settle': settle,
                    'sort_key': (year, month)
                })
        contracts.sort(key=lambda x: x['sort_key'])
        # 只取最近limit_months个
        contracts = contracts[-12:]
        result.append({
            'date': d,
            'contracts': contracts
        })

    conn.close()
    return jsonify(result)


def api_all_trading_dates():
    """获取所有有数据的交易日列表（用于K线图x轴）"""
    conn = sqlite3.connect('/home/ubuntu/lithium_calendar/lithium.db')
    dates = conn.execute(
        "SELECT DISTINCT date FROM contract_prices WHERE date IS NOT NULL ORDER BY date"
    ).fetchall()
    conn.close()
    return [d[0] for d in dates]

