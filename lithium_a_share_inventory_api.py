# -*- coding: utf-8 -*-
"""
锂电产业链A股存货库存 API
数据源: /home/ubuntu/analysis/lithium_inventory.db
覆盖 20 家公司 (上/中/下游), 季度财报存货数据
"""

import sqlite3
import os
from flask import Blueprint, request, jsonify

lithium_inv_bp = Blueprint('lithium_inv', __name__, url_prefix='/api/lithium_inv')

INV_DB = '/home/ubuntu/analysis/lithium_inventory.db'


def get_inv_db():
    conn = sqlite3.connect(INV_DB)
    conn.row_factory = sqlite3.Row
    return conn


@lithium_inv_bp.route('/companies')
def api_companies():
    """20家公司基本信息 + 最新报告期存货摘要"""
    try:
        conn = get_inv_db()
        # 先取最新报告期
        rows = conn.execute('''
            SELECT a.ticker, a.name, a.sector, a.industry_chain,
                   b.report_date, b.inventory_yuan, b.total_assets_yuan,
                   b.current_assets_yuan, b.inventory_to_assets_pct, b.inventory_to_current_pct
            FROM companies a
            LEFT JOIN inventory_data b ON a.ticker = b.ticker
                AND b.report_date = (SELECT MAX(report_date) FROM inventory_data WHERE ticker = a.ticker)
            ORDER BY a.sector, b.inventory_yuan DESC
        ''').fetchall()
        result = []
        for r in rows:
            result.append({
                'ticker': r['ticker'],
                'name': r['name'],
                'sector': r['sector'],
                'industry_chain': r['industry_chain'],
                'report_date': r['report_date'],
                'inventory_yuan': r['inventory_yuan'] or 0,
                'total_assets_yuan': r['total_assets_yuan'] or 0,
                'current_assets_yuan': r['current_assets_yuan'] or 0,
                'inventory_to_assets_pct': r['inventory_to_assets_pct'] or 0,
                'inventory_to_current_pct': r['inventory_to_current_pct'] or 0,
            })
        conn.close()
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@lithium_inv_bp.route('/trends')
def api_trends():
    """
    个股存货趋势
    ?ticker=300750 默认全量
    ?tickers=300750,002594,002460 多选
    ?from=20180101 起始日期过滤
    """
    try:
        tickers_param = request.args.get('tickers', '').strip()
        ticker_param = request.args.get('ticker', '').strip()
        from_date = request.args.get('from', '').strip()
        if ticker_param:
            tickers_param = ticker_param

        conn = get_inv_db()
        conditions = []
        params = []
        if tickers_param:
            tickers = [t.strip() for t in tickers_param.split(',') if t.strip()]
            placeholders = ','.join(['?' for _ in tickers])
            conditions.append(f"b.ticker IN ({placeholders})")
            params.extend(tickers)
        if from_date:
            conditions.append("b.report_date >= ?")
            params.append(from_date)
        where = (' WHERE ' + ' AND '.join(conditions)) if conditions else ''
        rows = conn.execute(f'''
            SELECT b.ticker, a.name, a.sector, b.report_date,
                   b.inventory_yuan, b.total_assets_yuan,
                   b.inventory_to_assets_pct, b.inventory_to_current_pct
            FROM inventory_data b
            JOIN companies a ON a.ticker = b.ticker
            {where}
            ORDER BY b.ticker, b.report_date
        ''', params).fetchall()

        # 按 ticker 分组
        data = {}
        for r in rows:
            t = r['ticker']
            if t not in data:
                data[t] = {'ticker': t, 'name': r['name'], 'sector': r['sector'], 'quarters': []}
            data[t]['quarters'].append({
                'date': r['report_date'],
                'inventory_yuan': r['inventory_yuan'] or 0,
                'total_assets_yuan': r['total_assets_yuan'] or 0,
                'inventory_to_assets_pct': r['inventory_to_assets_pct'] or 0,
                'inventory_to_current_pct': r['inventory_to_current_pct'] or 0,
            })
        conn.close()
        return jsonify(list(data.values()))
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@lithium_inv_bp.route('/sector_summary')
def api_sector_summary():
    """按产业链板块汇总 (上/中/下游)，日期升序(左旧→右新)"""
    try:
        from_date = request.args.get('from', '').strip()
        conn = get_inv_db()
        where = ''
        params = []
        if from_date:
            where = ' WHERE report_date >= ?'
            params = [from_date]
        dates = [r[0] for r in conn.execute(
            f'SELECT DISTINCT report_date FROM inventory_data{where} ORDER BY report_date ASC', params
        ).fetchall()]

        result = {'dates': [], 'sectors': {}}
        for d in dates:
            result['dates'].append(d)
            rows = conn.execute('''
                SELECT a.sector,
                       SUM(b.inventory_yuan) as total_inv,
                       AVG(b.inventory_to_assets_pct) as avg_inv_asset,
                       AVG(b.inventory_to_current_pct) as avg_inv_current,
                       COUNT(*) as company_count
                FROM inventory_data b
                JOIN companies a ON a.ticker = b.ticker
                WHERE b.report_date = ?
                GROUP BY a.sector
            ''', (d,)).fetchall()
            for r in rows:
                sec = r['sector']
                if sec not in result['sectors']:
                    result['sectors'][sec] = {'inv': [], 'inv_asset_pct': [], 'inv_current_pct': []}
                result['sectors'][sec]['inv'].append(r['total_inv'] or 0)
                result['sectors'][sec]['inv_asset_pct'].append(r['avg_inv_asset'] or 0)
                result['sectors'][sec]['inv_current_pct'].append(r['avg_inv_current'] or 0)
        conn.close()
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@lithium_inv_bp.route('/latest_compare')
def api_latest_compare():
    """最新报告期所有公司横向对比"""
    try:
        conn = get_inv_db()
        rows = conn.execute('''
            SELECT a.ticker, a.name, a.sector, a.industry_chain,
                   b.report_date, b.inventory_yuan, b.total_assets_yuan,
                   b.current_assets_yuan, b.inventory_to_assets_pct, b.inventory_to_current_pct,
                   c.revenue_yuan, c.inv_to_revenue_ratio
            FROM companies a
            JOIN inventory_data b ON a.ticker = b.ticker
                AND b.report_date = (SELECT MAX(report_date) FROM inventory_data WHERE ticker = a.ticker)
            LEFT JOIN financial_data c ON a.ticker = c.ticker
                AND c.report_date = (SELECT MAX(report_date) FROM financial_data WHERE ticker = a.ticker)
            ORDER BY b.inventory_yuan DESC
        ''').fetchall()
        conn.close()
        result = []
        for r in rows:
            result.append({
                'ticker': r['ticker'],
                'name': r['name'],
                'sector': r['sector'],
                'report_date': r['report_date'],
                'inventory_yuan': r['inventory_yuan'] or 0,
                'total_assets_yuan': r['total_assets_yuan'] or 0,
                'inventory_to_assets_pct': r['inventory_to_assets_pct'] or 0,
                'inventory_to_current_pct': r['inventory_to_current_pct'] or 0,
                'revenue_yuan': r['revenue_yuan'] or 0,
                'inv_to_revenue_ratio': r['inv_to_revenue_ratio'],
            })
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@lithium_inv_bp.route('/inv_sales_ratio')
def api_inv_sales_ratio():
    """
    库销比趋势 (存货/营业收入)
    ?sector=上游 筛选板块 (上游/中游/下游)
    ?tickers=300750,002594 指定公司
    ?from=20180101 起始日期过滤
    """
    try:
        sector_param = request.args.get('sector', '').strip()
        tickers_param = request.args.get('tickers', '').strip()
        from_date = request.args.get('from', '').strip()

        conn = get_inv_db()

        # Build base query joining inventory + financial
        base_sql = '''
            SELECT a.ticker, a.name, a.sector, b.report_date,
                   b.inventory_yuan, c.revenue_yuan, c.inv_to_revenue_ratio
            FROM inventory_data b
            JOIN companies a ON a.ticker = b.ticker
            LEFT JOIN financial_data c ON a.ticker = c.ticker AND b.report_date = c.report_date
        '''
        params = []
        conditions = []

        if sector_param:
            conditions.append("a.industry_chain = ?")
            params.append(sector_param)

        if tickers_param:
            tickers = [t.strip() for t in tickers_param.split(',') if t.strip()]
            placeholders = ','.join(['?' for _ in tickers])
            conditions.append(f"b.ticker IN ({placeholders})")
            params.extend(tickers)

        if from_date:
            conditions.append("b.report_date >= ?")
            params.append(from_date)

        if conditions:
            base_sql += ' WHERE ' + ' AND '.join(conditions)

        base_sql += ' ORDER BY b.ticker, b.report_date'
        
        rows = conn.execute(base_sql, params).fetchall()
        conn.close()
        
        # Group by ticker
        data = {}
        for r in rows:
            t = r['ticker']
            if t not in data:
                data[t] = {'ticker': t, 'name': r['name'], 'sector': r['sector'], 'quarters': []}
            data[t]['quarters'].append({
                'date': r['report_date'],
                'inventory_yuan': r['inventory_yuan'] or 0,
                'revenue_yuan': r['revenue_yuan'] or 0,
                'inv_to_revenue_ratio': r['inv_to_revenue_ratio'],
            })
        
        return jsonify(list(data.values()))
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@lithium_inv_bp.route('/sector_companies')
def api_sector_companies():
    """按板块返回公司列表 (上/中/下游)"""
    try:
        conn = get_inv_db()
        rows = conn.execute('''
            SELECT industry_chain, ticker, name, sector
            FROM companies
            ORDER BY industry_chain, sector, ticker
        ''').fetchall()
        conn.close()
        
        result = {}
        for r in rows:
            chain = r['industry_chain']
            if chain not in result:
                result[chain] = []
            result[chain].append({
                'ticker': r['ticker'],
                'name': r['name'],
                'sector': r['sector'],
            })
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500
