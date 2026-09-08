#!/usr/bin/env python3
"""
Fetch quarterly financial data (revenue, cost, profit) for 20 lithium A-share companies.
Source: akshare stock_financial_report_sina
Target: /home/ubuntu/analysis/lithium_inventory.db
"""
import sqlite3
import sys
import os
import time

sys.path.insert(0, "/home/ubuntu/zinc_venv/lib/python3.11/site-packages")
import akshare as ak
import pandas as pd

INV_DB = "/home/ubuntu/analysis/lithium_inventory.db"

COMPANIES = [
    ("002466", "天齐锂业", "sz"),
    ("002460", "赣锋锂业", "sz"),
    ("000792", "盐湖股份", "sz"),
    ("000408", "藏格矿业", "sz"),
    ("000155", "川能动力", "sz"),
    ("600338", "西藏珠峰", "sh"),
    ("002738", "中矿资源", "sz"),
    ("002756", "永兴材料", "sz"),
    ("301152", "盛新锂能", "sz"),
    ("002497", "雅化集团", "sz"),
    ("600499", "科达制造", "sh"),
    ("688005", "容百科技", "sh"),
    ("300073", "当升科技", "sz"),
    ("688779", "长远锂科", "sh"),
    ("300890", "翔丰华", "sz"),
    ("300750", "宁德时代", "sz"),
    ("002594", "比亚迪", "sz"),
    ("300014", "亿纬锂能", "sz"),
    ("002074", "国轩高科", "sz"),
    ("300438", "鹏辉能源", "sz"),
]

def safe_float(val):
    try:
        if pd.isna(val):
            return None
        return float(val)
    except:
        return None

def main():
    conn = sqlite3.connect(INV_DB)
    
    # Create table
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
    
    total_new = 0
    errors = []
    
    for ticker, name, market in COMPANIES:
        stock_id = f"{market}{ticker}"
        
        existing = conn.execute(
            "SELECT COUNT(*) FROM financial_data WHERE ticker=?", (ticker,)
        ).fetchone()[0]
        if existing > 10:
            print(f"Skip {ticker} {name} ({existing} records)")
            continue
        
        try:
            print(f"Fetching {ticker} {name} ({stock_id})...", end=" ")
            df = ak.stock_financial_report_sina(stock=stock_id, symbol="利润表")
            
            if df is None or df.empty:
                print("No data")
                errors.append(f"{ticker} {name}: no data")
                time.sleep(1)
                continue
            
            # Filter quarterly only
            quarterly = []
            for _, row in df.iterrows():
                report_date = str(row["报告日"]).replace("-", "").replace("/", "")
                if (len(report_date) == 8 and report_date.isdigit() 
                    and report_date.endswith(("0331", "0630", "0930", "1231"))):
                    revenue = safe_float(row["营业收入"])
                    cost = safe_float(row["营业成本"])
                    op_profit = safe_float(row["营业利润"])
                    net_profit = safe_float(row["归属于母公司所有者的净利润"])
                    
                    if revenue and revenue > 0:
                        quarterly.append({
                            "report_date": report_date,
                            "revenue": revenue,
                            "cost": cost,
                            "op_profit": op_profit,
                            "net_profit": net_profit,
                        })
            
            quarterly.sort(key=lambda x: x["report_date"])
            
            for q in quarterly:
                inv = conn.execute(
                    "SELECT inventory_yuan FROM inventory_data WHERE ticker=? AND report_date=? LIMIT 1",
                    (ticker, q["report_date"])
                ).fetchone()
                inv_to_rev = None
                if inv and inv[0] and q["revenue"] and q["revenue"] > 0:
                    inv_to_rev = round(inv[0] / q["revenue"], 4)
                
                conn.execute("""
                    INSERT OR REPLACE INTO financial_data 
                    (ticker, report_date, revenue_yuan, cost_yuan, operating_profit_yuan, 
                     net_profit_yuan, inv_to_revenue_ratio)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (ticker, q["report_date"], q["revenue"], q["cost"], 
                      q["op_profit"], q["net_profit"], inv_to_rev))
            
            conn.commit()
            total_new += len(quarterly)
            print(f"{len(quarterly)} quarters")
            time.sleep(1.5)
            
        except Exception as e:
            msg = f"{ticker} {name}: {e}"
            print(f"Error: {msg}")
            errors.append(msg)
            time.sleep(2)
    
    print(f"\n=== Summary ===")
    print(f"Total new records: {total_new}")
    
    cursor = conn.execute(
        "SELECT ticker, MIN(report_date), MAX(report_date), COUNT(*) FROM financial_data GROUP BY ticker ORDER BY ticker"
    )
    for row in cursor.fetchall():
        print(f"  {row[0]}: {row[1]} ~ {row[2]} ({row[3]} records)")
    
    if errors:
        print(f"\nErrors ({len(errors)}):")
        for e in errors:
            print(f"  - {e}")
    
    conn.close()

if __name__ == "__main__":
    main()
