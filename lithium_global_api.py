#!/usr/bin/env python3
"""Lithium Global Database Query Helper"""
import sqlite3
import json
import os

DB1 = "/home/ubuntu/analysis/lithium_global/lithium_global.db"
DB2 = "/home/ubuntu/lithium_calendar/lithium.db"
DB3 = "/home/ubuntu/lc_futures_data/data/lc_position.db"

def query_all(db, sql):
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(sql).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_companies():
    return query_all(DB1, "SELECT * FROM company_master ORDER BY region, company_name")

def get_quarterly_data():
    return query_all(DB1, "SELECT * FROM quarterly_data ORDER BY ticker, fiscal_year DESC, quarter")

def get_source_map():
    return query_all(DB1, "SELECT * FROM data_source_map ORDER BY ticker, data_field")

def get_futures_summary():
    stats = []
    stats.append({"table": "prices", **query_all(DB2, "SELECT MIN(date) as start_date, MAX(date) as end_date, COUNT(*) as cnt FROM prices")[0]})
    stats.append({"table": "spreads", **query_all(DB2, "SELECT MIN(date) as start_date, MAX(date) as end_date, COUNT(*) as cnt FROM spreads")[0]})
    stats.append({"table": "contract_daily_all", **query_all(DB2, "SELECT MIN(date) as start_date, MAX(date) as end_date, COUNT(*) as cnt FROM contract_daily_all")[0]})
    stats.append({"table": "inventory_history", **query_all(DB2, "SELECT MIN(date) as start_date, MAX(date) as end_date, COUNT(*) as cnt FROM inventory_history")[0]})
    stats.append({"table": "fundamental_indices", **query_all(DB2, "SELECT MIN(date) as start_date, MAX(date) as end_date, COUNT(*) as cnt FROM fundamental_indices")[0]})
    stats.append({"table": "lithium_daily_prices", **query_all(DB2, "SELECT MIN(date) as start_date, MAX(date) as end_date, COUNT(*) as cnt FROM lithium_daily_prices")[0]})
    stats.append({"table": "lithium_weekly", **query_all(DB2, "SELECT MIN(date) as start_date, MAX(date) as end_date, COUNT(*) as cnt FROM lithium_weekly")[0]})
    stats.append({"table": "lithium_monthly", **query_all(DB2, "SELECT MIN(date) as start_date, MAX(date) as end_date, COUNT(*) as cnt FROM lithium_monthly")[0]})
    
    # Position data from DB3
    pos = query_all(DB3, "SELECT MIN(date) as start_date, MAX(date) as end_date, COUNT(*) as cnt FROM position_rank WHERE variety='碳酸锂'")
    if pos: stats.append({"table": "position_rank", **pos[0]})
    wr = query_all(DB3, "SELECT MIN(date) as start_date, MAX(date) as end_date, COUNT(*) as cnt FROM warehouse_receipt WHERE product='碳酸锂'")
    if wr: stats.append({"table": "warehouse_receipt", **wr[0]})
    return stats

def get_latest_quotes():
    return query_all(DB2, "SELECT * FROM prices ORDER BY date DESC LIMIT 10")

def get_agents():
    return query_all(DB2, "SELECT * FROM agents")

def get_latest_fundamentals():
    return query_all(DB2, "SELECT * FROM fundamental_indices ORDER BY date DESC LIMIT 30")

def get_smm_meta_groups():
    rows = query_all(DB2, "SELECT DISTINCT sheet_name FROM lithium_meta ORDER BY sheet_name")
    result = []
    for r in rows:
        sn = r['sheet_name']
        keys = query_all(DB2, f"SELECT key, col_idx FROM lithium_meta WHERE sheet_name='{sn}' ORDER BY col_idx")
        result.append({"sheet": sn, "keys": keys})
    return result


def get_salt_lake_detail():
    """Get salt lake quarterly detail data"""
    return query_all(DB1, "SELECT company_name, project_name, country, quarter, lce_production_kt, lce_annualized_kt, ramp_up_pct, quarterly_capex_usd_m, annual_capex_usd_m, c1_cost_usd_t, aisc_usd_t, production_guidance, guidance_change, major_events FROM salt_lake_detail ORDER BY country, project_name, quarter")

def get_hardrock_pipeline():
    """Get hard rock pipeline projects"""
    return query_all(DB1, "SELECT * FROM hardrock_pipeline_projects ORDER BY country, development_stage")

def get_african_lithium():
    """Get African lithium projects"""
    return query_all(DB1, "SELECT * FROM african_lithium ORDER BY country, project_name")

def get_analysis_results():
    """Get analysis insights"""
    return query_all(DB1, "SELECT company_name, project_name, analysis_type, finding, severity, recommendation, data_points, confidence FROM analysis_results ORDER BY severity, analysis_type")

def get_crawler_paths():
    """Get crawler configuration"""
    return query_all(DB1, "SELECT company_name, ticker, exchange, source_type, base_url, pattern, crawl_frequency, last_crawled, last_success, status FROM crawler_paths WHERE status='active'")

def get_dashboard_summary():
    """Get summary statistics for dashboard"""
    results = {}
    
    # Salt lake summary
    sl = query_all(DB1, "SELECT country, COUNT(DISTINCT project_name) as project_count, MAX(lce_annualized_kt) as max_annual_kt FROM salt_lake_detail GROUP BY country")
    results['salt_lake_by_country'] = sl
    
    # Hardrock pipeline summary
    hr = query_all(DB1, "SELECT country, development_stage, COUNT(*) as count, COALESCE(SUM(expected_capacity_ktpa), 0) as total_capacity FROM hardrock_pipeline_projects GROUP BY country, development_stage")
    results['hardrock_by_country_stage'] = hr
    
    # African lithium summary
    af = query_all(DB1, "SELECT country, COUNT(*) as count FROM african_lithium GROUP BY country")
    results['african_by_country'] = af
    
    # Analysis signal summary
    sig = query_all(DB1, "SELECT severity, analysis_type, COUNT(*) as count FROM analysis_results GROUP BY severity, analysis_type")
    results['analysis_signals'] = sig
    
    # Total counts
    results['total_salt_lake_projects'] = query_all(DB1, "SELECT COUNT(DISTINCT project_name) as cnt FROM salt_lake_detail")[0]
    results['total_hardrock_projects'] = query_all(DB1, "SELECT COUNT(*) as cnt FROM hardrock_pipeline_projects")[0]
    results['total_african_projects'] = query_all(DB1, "SELECT COUNT(*) as cnt FROM african_lithium")[0]
    results['total_crawler_paths'] = query_all(DB1, "SELECT COUNT(*) as cnt FROM crawler_paths WHERE status='active'")[0]
    
    return results


if __name__ == "__main__":
    result = {
        "companies": get_companies(),
        "quarterly_data": get_quarterly_data(),
        "source_map": get_source_map(),
        "futures_summary": get_futures_summary(),
        "latest_quotes": get_latest_quotes(),
        "agents": get_agents(),
        "latest_fundamentals": get_latest_fundamentals(),
        "smm_meta_groups": get_smm_meta_groups(),
        "dashboard_summary": get_dashboard_summary(),
        "salt_lake_detail": get_salt_lake_detail(),
        "hardrock_pipeline": get_hardrock_pipeline(),
        "african_lithium": get_african_lithium(),
        "analysis_results": get_analysis_results(),
        "crawler_paths": get_crawler_paths()
    }
    print(json.dumps(result, ensure_ascii=False, default=str))
