#!/usr/bin/env python3
"""
抓取锂电A股20家公司季度财务数据（营业收入、营业成本、净利润）
数据源: akshare stock_financial_report_sina (利润表)
目标DB: /home/ubuntu/analysis/lithium_inventory.db
"""
import sqlite3
import time
import subprocess
import json

INV_DB = "/home/ubuntu/analysis/lithium_inventory.db"
VENV_PYTHON = "/home/ubuntu/zinc_venv/bin/python3"

# 20 companies with their market identifiers
COMPANIES = [
    # 上游-锂矿/盐湖
    ("002466", "天齐锂业", "SZ"),
    ("002460", "赣锋锂业", "SZ"),
    ("000792", "盐湖股份", "SZ"),
    ("000408", "藏格矿业", "SZ"),
    ("000155", "川能动力", "SZ"),
    ("600338", "西藏珠峰", "SH"),
    # 中游-锂盐/锂化合物
    ("002738", "中矿资源", "SZ"),
    ("002756", "永兴材料", "SZ"),
    ("301152", "盛新锂能", "SZ"),
    ("002497", "雅化集团", "SZ"),
    ("600499", "科达制造", "SH"),
    # 下游-正极材料
    ("688005", "容百科技", "SH"),
    ("300073", "当升科技", "SZ"),
    ("688779", "长远锂科", "SH"),
    ("300890", "翔丰华", "SZ"),
    # 下游-电池
    ("300750", "宁德时代", "SZ"),
    ("002594", "比亚迪", "SZ"),
    ("300014", "亿纬锂能", "SZ"),
    ("002074", "国轩高科", "SZ"),
    ("300438", "鹏辉能源", "SZ"),
]

QUARTER_ENDINGS = ("0331", "0630", "0930", "1231")


def ensure_financial_table(conn):
    """Create financial_data table if not exists"""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS financial_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            report_date TEXT NOT NULL,
            revenue_yuan REAL,
            cost_yuan REAL,
            operating_profit_yuan REAL,
            net_profit_yuan REAL,
            revenue_yoy_pct REAL,
            inv_to_revenue_ratio REAL,
            UNIQUE(ticker, report_date)
        )
    """)
    conn.commit()


def fetch_one_company(ticker, name, market):
    """Fetch quarterly income statement for one company via akshare subprocess"""
    code = market + ticker
    print(f"  Fetching {code} {name}...", end=" ", flush=True)
    
    script = 'import akshare as ak\nimport json\nimport pandas as pd\n\n'
    script += f"stock_code = '{code}'\n"
    script += """
try:
    df = ak.stock_financial_report_sina(stock=stock_code, symbol='利润表')
    df = df[df['类型'] == '合并期末'].copy()
    df = df[df['报告日'].str.endswith(('0331','0630','0930','1231'))].copy()
    df = df.sort_values('报告日').tail(40)
    
    records = []
    for _, row in df.iterrows():
        rd = str(row['报告日']).strip()
        rev = float(row['营业收入']) if pd.notna(row['营业收入']) else None
        cost = float(row['营业成本']) if pd.notna(row['营业成本']) else None
        op_profit = float(row['营业利润']) if pd.notna(row['营业利润']) else None
        net_profit = float(row['归属于母公司所有者的净利润']) if pd.notna(row['归属于母公司所有者的净利润']) else None
        
        if rd and len(rd) == 8 and rev is not None:
            records.append({
                'report_date': rd,
                'revenue_yuan': rev,
                'cost_yuan': cost,
                'operating_profit_yuan': op_profit,
                'net_profit_yuan': net_profit
            })
    
    print(json.dumps(records, ensure_ascii=False))
except Exception as e:
    print('ERROR:' + str(e))
"""
    
    result = subprocess.run(
        [VENV_PYTHON, "-c", script],
        capture_output=True, text=True, timeout=60
    )
    
    output = result.stdout.strip()
    if output.startswith("ERROR:") or not output:
        err_msg = result.stderr[-200:] if result.stderr else "unknown"
        print(f"Failed: {output[:100] or err_msg}")
        return []
    
    try:
        data = json.loads(output)
        print(f"Got {len(data)} quarters")
        return data
    except:
        print(f"JSON parse failed: {output[:100]}")
        return []


def main():
    conn = sqlite3.connect(INV_DB)
    ensure_financial_table(conn)
    
    # Check existing data
    cursor = conn.execute("SELECT ticker, COUNT(*) FROM financial_data GROUP BY ticker")
    existing = {row[0]: row[1] for row in cursor.fetchall()}
    print(f"Existing data: {existing}")
    print()
    
    total_fetched = 0
    for ticker, name, market in COMPANIES:
        data = fetch_one_company(ticker, name, market)
        
        for row in data:
            # Calculate inv_to_revenue_ratio from inventory_data
            inv_row = conn.execute(
                "SELECT inventory_yuan FROM inventory_data WHERE ticker=? AND report_date=? LIMIT 1",
                (ticker, row["report_date"])
            ).fetchone()
            inv_to_rev = None
            if inv_row and inv_row[0] and row["revenue_yuan"] and row["revenue_yuan"] > 0:
                inv_to_rev = inv_row[0] / row["revenue_yuan"]
            
            conn.execute("""
                INSERT OR REPLACE INTO financial_data 
                (ticker, report_date, revenue_yuan, cost_yuan, operating_profit_yuan, 
                 net_profit_yuan, inv_to_revenue_ratio)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                ticker, row["report_date"], row["revenue_yuan"], row["cost_yuan"],
                row["operating_profit_yuan"], row["net_profit_yuan"],
                inv_to_rev
            ))
        
        conn.commit()
        total_fetched += len(data)
        time.sleep(1.5)  # Rate limiting
    
    # Summary
    cursor = conn.execute("SELECT COUNT(*) FROM financial_data")
    total = cursor.fetchone()[0]
    print(f"\nTotal records in financial_data: {total}")
    
    cursor = conn.execute("""
        SELECT a.ticker, a.name, a.sector, 
               MIN(f.report_date), MAX(f.report_date), COUNT(*)
        FROM financial_data f
        JOIN companies a ON a.ticker = f.ticker
        GROUP BY f.ticker
        ORDER BY a.industry_chain, a.sector, f.ticker
    """)
    print("\nPer-company coverage:")
    for row in cursor.fetchall():
        inv_b = None
        rev_b = None
        ratio = None
        # Get latest values
        latest = conn.execute("""
            SELECT f.revenue_yuan, f.inv_to_revenue_ratio
            FROM financial_data f
            WHERE f.ticker=?
            ORDER BY f.report_date DESC LIMIT 1
        """, (row[0],)).fetchone()
        if latest:
            rev_b = latest[0] / 1e8 if latest[0] else 0
            ratio = latest[1]
        print(f"  {row[0]} {row[1]:<6} ({row[2]:<12}) | {row[3]}~{row[4]} | {row[5]}q | 最新营收: {rev_b:.1f}亿 | 库销比: {ratio:.2f}" if ratio else f"  {row[0]} {row[1]:<6} ({row[2]:<12}) | {row[3]}~{row[4]} | {row[5]}q | 最新营收: {rev_b:.1f}亿")
    
    conn.close()
    print(f"\nDone! Fetched {total_fetched} records total.")


if __name__ == "__main__":
    main()
