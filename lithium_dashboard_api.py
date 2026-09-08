#!/usr/bin/env python3
"""
Lithium Global Dashboard API - Flask Blueprint for lithium data overview.
Serves company master, quarterly data, data sources, futures summary, SMM indicators.
Mounted as blueprint on lithium_calendar app.py (port 8766).
"""
from flask import Blueprint, jsonify, send_from_directory
import sqlite3
import json
import os
import traceback

lithium_bp = Blueprint("lithium_dashboard", __name__, url_prefix="")

DB1 = "/home/ubuntu/analysis/lithium_global/lithium_global.db"
DB2 = "/home/ubuntu/lithium_calendar/lithium.db"
DB3 = "/home/ubuntu/lc_futures_data/data/lc_position.db"

def query_all(db, sql):
    try:
        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(sql).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        return []

def safe_query(db, sql):
    try:
        return query_all(db, sql)
    except Exception as e:
        print(f"Query error: {e}\n{traceback.format_exc()}")
        return []

@lithium_bp.route("/api/lithium/overview")
def overview():
    """Complete lithium data overview - all tables, companies, counts"""
    try:
        # DB1 tables
        db1_tables = query_all(DB1, "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        db1_counts = {}
        for t in db1_tables:
            c = query_all(DB1, f"SELECT COUNT(*) as cnt FROM [{t['name']}]")
            db1_counts[t['name']] = c[0]['cnt'] if c else 0

        # DB2 tables
        db2_tables = query_all(DB2, "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        db2_counts = {}
        for t in db2_tables:
            c = query_all(DB2, f"SELECT COUNT(*) as cnt FROM [{t['name']}]")
            db2_counts[t['name']] = c[0]['cnt'] if c else 0

        # DB3 tables
        db3_tables = query_all(DB3, "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        db3_counts = {}
        for t in db3_tables:
            c = query_all(DB3, f"SELECT COUNT(*) as cnt FROM [{t['name']}]")
            db3_counts[t['name']] = c[0]['cnt'] if c else 0

        # Companies
        companies = query_all(DB1, "SELECT * FROM company_master ORDER BY region, company_name")

        # Quarterly data
        qdata = query_all(DB1, "SELECT * FROM quarterly_data ORDER BY ticker, fiscal_year DESC")

        # Source map
        sources = query_all(DB1, "SELECT * FROM data_source_map ORDER BY ticker, data_field")

        # Latest futures quotes
        quotes = query_all(DB2, "SELECT * FROM prices ORDER BY date DESC LIMIT 10")

        # Agents
        agents = query_all(DB2, "SELECT * FROM agents")

        # SMM meta groups
        smm_groups = []
        sheet_names = query_all(DB2, "SELECT DISTINCT sheet_name FROM lithium_meta ORDER BY sheet_name")
        for s in sheet_names:
            sn = s['sheet_name']
            keys = query_all(DB2, f"SELECT key, col_idx FROM lithium_meta WHERE sheet_name='{sn}' ORDER BY col_idx")
            cnt = query_all(DB2, f"SELECT COUNT(*) as cnt FROM lithium_meta WHERE sheet_name='{sn}'")
            idx_list = ','.join([str(k['col_idx']) for k in keys]) if keys else '0'
            data_cnt = {
                "daily": query_all(DB2, f"SELECT MIN(date) as start_date, MAX(date) as end_date, COUNT(*) as cnt FROM lithium_daily_prices WHERE col_idx IN ({idx_list})")[0] if keys else None,
                "weekly": query_all(DB2, f"SELECT MIN(date) as start_date, MAX(date) as end_date, COUNT(*) as cnt FROM lithium_weekly WHERE col_idx IN ({idx_list})")[0] if keys else None,
                "monthly": query_all(DB2, f"SELECT MIN(date) as start_date, MAX(date) as end_date, COUNT(*) as cnt FROM lithium_monthly WHERE col_idx IN ({idx_list})")[0] if keys else None
            }
            smm_groups.append({"sheet": sn, "key_count": cnt[0]['cnt'] if cnt else 0, "keys": keys, "data_count": data_cnt})

        # Latest fundamentals
        fundamentals = query_all(DB2, "SELECT * FROM fundamental_indices ORDER BY date DESC LIMIT 30")

        # Inventory
        inventory = query_all(DB2, "SELECT * FROM inventory_history ORDER BY date DESC LIMIT 30")

        # Market signals (replaces news)
        signals = query_all(DB2, "SELECT * FROM market_signals ORDER BY date DESC LIMIT 20")

        # Position data
        positions = query_all(DB3, "SELECT * FROM position_rank WHERE variety='LC' ORDER BY date DESC LIMIT 10")
        receipts = query_all(DB3, "SELECT * FROM warehouse_receipt WHERE product='碳酸锂' ORDER BY date DESC LIMIT 10")

        return jsonify({
            "status": "ok",
            "databases": {
                "lithium_global": {"path": DB1, "description": "全球锂矿财报数据库", "tables": db1_counts},
                "lithium_calendar": {"path": DB2, "description": "碳酸锂日历看板数据库", "tables": db2_counts},
                "lc_position": {"path": DB3, "description": "期货持仓数据库", "tables": db3_counts}
            },
            "companies": companies,
            "quarterly_data": qdata,
            "sources": sources,
            "quotes": quotes,
            "agents": agents,
            "smm_groups": smm_groups,
            "fundamentals": fundamentals,
            "inventory": inventory,
            "signals": signals,
            "positions": positions,
            "receipts": receipts
        })
    except Exception as e:
        print(f"Overview error: {e}\n{traceback.format_exc()}")
        return jsonify({"status": "error", "message": str(e)}), 500

@lithium_bp.route("/api/lithium/companies")
def companies():
    """Company master data with sources"""
    companies = query_all(DB1, "SELECT * FROM company_master ORDER BY region, company_name")
    for c in companies:
        c['sources'] = query_all(DB1, f"SELECT * FROM data_source_map WHERE ticker='{c['ticker']}'")
        c['quarterly_data'] = query_all(DB1, f"SELECT * FROM quarterly_data WHERE ticker='{c['ticker']}' ORDER BY fiscal_year DESC")
    return jsonify({"status": "ok", "companies": companies})

@lithium_bp.route("/api/lithium/company/<ticker>")
def company_detail(ticker):
    """Single company detail"""
    company = query_all(DB1, f"SELECT * FROM company_master WHERE ticker='{ticker}'")
    if not company:
        return jsonify({"status": "error", "message": "Company not found"}), 404
    c = company[0]
    c['sources'] = query_all(DB1, f"SELECT * FROM data_source_map WHERE ticker='{ticker}'")
    c['quarterly_data'] = query_all(DB1, f"SELECT * FROM quarterly_data WHERE ticker='{ticker}' ORDER BY fiscal_year DESC, quarter")
    return jsonify({"status": "ok", "company": c})

@lithium_bp.route("/api/lithium/qdata")
def quarterly_data():
    """Quarterly data with company names"""
    qdata = query_all(DB1, "SELECT q.*, cm.company_name, cm.region, cm.resource_type FROM quarterly_data q LEFT JOIN company_master cm ON q.ticker=cm.ticker ORDER BY q.ticker, q.fiscal_year DESC")
    return jsonify({"status": "ok", "data": qdata})

@lithium_bp.route("/api/lithium/fundamentals")
def fundamentals():
    fundamentals = query_all(DB2, "SELECT * FROM fundamental_indices ORDER BY date DESC LIMIT 100")
    return jsonify({"status": "ok", "data": fundamentals})

@lithium_bp.route("/api/lithium/inventory")
def inventory():
    inv = query_all(DB2, "SELECT * FROM inventory_history ORDER BY date DESC LIMIT 100")
    return jsonify({"status": "ok", "data": inv})

@lithium_bp.route("/api/lithium/quotes")
def quotes():
    qs = query_all(DB2, "SELECT * FROM prices ORDER BY date DESC LIMIT 200")
    return jsonify({"status": "ok", "data": qs})

@lithium_bp.route("/api/lithium/smm")
def smm():
    """SMM indicators"""
    sheets = []
    sheet_names = query_all(DB2, "SELECT DISTINCT sheet_name FROM lithium_meta ORDER BY sheet_name")
    for s in sheet_names:
        sn = s['sheet_name']
        keys = query_all(DB2, f"SELECT key, col_idx FROM lithium_meta WHERE sheet_name='{sn}' ORDER BY col_idx")
        sheets.append({"sheet": sn, "keys": keys})
    return jsonify({"status": "ok", "sheets": sheets})

@lithium_bp.route("/api/lithium/agents")
def agents():
    ags = query_all(DB2, "SELECT * FROM agents")
    return jsonify({"status": "ok", "data": ags})

@lithium_bp.route("/api/lithium/signals")
def signals():
    ss = query_all(DB2, "SELECT * FROM market_signals ORDER BY date DESC LIMIT 50")
    return jsonify({"status": "ok", "data": ss})

@lithium_bp.route("/api/lithium/positions")
def positions():
    ps = query_all(DB3, "SELECT * FROM position_rank WHERE variety='LC' ORDER BY date DESC LIMIT 50")
    return jsonify({"status": "ok", "data": ps})

@lithium_bp.route("/api/lithium/receipts")
def receipts():
    rs = query_all(DB3, "SELECT * FROM warehouse_receipt WHERE product='碳酸锂' ORDER BY date DESC LIMIT 50")
    return jsonify({"status": "ok", "data": rs})

@lithium_bp.route("/api/lithium/spreads")
def spreads():
    ss = query_all(DB2, "SELECT * FROM spreads ORDER BY date DESC LIMIT 100")
    return jsonify({"status": "ok", "data": ss})

@lithium_bp.route("/api/lithium/pipeline")
def pipeline():
    """Hard rock pipeline projects (companies_pipeline table)"""
    projects = query_all(DB1, "SELECT * FROM companies_pipeline ORDER BY country, development_stage")
    return jsonify({"status": "ok", "data": projects})

@lithium_bp.route("/api/lithium/africa")
def africa():
    """African lithium projects (african_lithium table)"""
    projects = query_all(DB1, "SELECT * FROM african_lithium ORDER BY country, development_stage")
    return jsonify({"status": "ok", "data": projects})

@lithium_bp.route("/api/lithium/salt_lake")
def salt_lake():
    """Salt lake detailed tracking (salt_lake_detail table)"""
    projects = query_all(DB1, "SELECT * FROM salt_lake_detail ORDER BY country, project_name, quarter")
    return jsonify({"status": "ok", "data": projects})

@lithium_bp.route("/api/lithium/analysis")
def analysis():
    """Analysis results (rampup + capex insights)"""
    results = query_all(DB1, "SELECT * FROM analysis_results ORDER BY analysis_type, severity DESC")
    return jsonify({"status": "ok", "data": results})

@lithium_bp.route("/api/lithium/crawler_paths")
def crawler_paths():
    """Crawler paths (crawler_paths table)"""
    paths = query_all(DB1, "SELECT * FROM crawler_paths ORDER BY exchange, ticker")
    return jsonify({"status": "ok", "data": paths})


@lithium_bp.route("/api/lithium/quarterly_time")
def quarterly_time():
    """Quarterly time-series data: financials + production by ticker"""
    # Financial data (revenue, net_profit) - annual
    financials = safe_query(DB1, """
        SELECT q.ticker, q.quarter, q.fiscal_year, 
               q.revenue_m, q.net_profit_m,
               cm.company_name, cm.resource_type, cm.region, cm.exchange
        FROM quarterly_data q
        LEFT JOIN company_master cm ON q.ticker = cm.ticker
        WHERE q.revenue_m IS NOT NULL OR q.net_profit_m IS NOT NULL
        ORDER BY q.ticker, q.quarter
    """)
    # Production data (hard rock + salt lake)
    production = safe_query(DB1, """
        SELECT q.ticker, q.quarter, q.fiscal_year,
               q.production_kt, q.sales_kt, q.production_lce_kt,
               cm.company_name, cm.resource_type, cm.region, cm.exchange
        FROM quarterly_data q
        LEFT JOIN company_master cm ON q.ticker = cm.ticker
        WHERE q.production_kt IS NOT NULL OR q.production_lce_kt IS NOT NULL
        ORDER BY q.ticker, q.quarter
    """)
    # Salt lake quarterly production
    salt_quarterly = safe_query(DB1, """
        SELECT company_name, ticker, project_name, country, quarter,
               lce_production_kt, lce_annualized_kt,
               c1_cost_usd_t, aisc_usd_t, production_guidance
        FROM salt_lake_detail
        ORDER BY company_name, quarter
    """)
    # Company master
    companies = safe_query(DB1, "SELECT * FROM company_master ORDER BY region, company_name")
    return jsonify({
        "status": "ok",
        "financials": financials,
        "production": production,
        "salt_quarterly": salt_quarterly,
        "companies": companies
    })


@lithium_bp.route("/lithium_time_dashboard.html")
def lithium_time_page():
    return send_from_directory("static", "lithium_time_dashboard.html")


@lithium_bp.route("/lithium_global_dashboard.html")
def lithium_global_page():
    return send_from_directory("static", "lithium_global_dashboard.html")
