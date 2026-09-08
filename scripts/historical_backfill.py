#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
回拉碳酸锂期货历史数据：从2025-01-01起补全所有合约（LC2501 ~ LC2706+）
"""
import sqlite3
import time
import akshare as ak
from datetime import datetime

DB_PATH = "/home/ubuntu/lithium_calendar/lithium.db"
START_DATE = "2025-01-01"

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def month_sort_key(symbol):
    if symbol == "LC0":
        return (9999, 99)
    ym = symbol[2:]
    if len(ym) != 4:
        return (9999, 99)
    yy = int(ym[:2])
    year = 2000 + yy if yy < 50 else 1900 + yy
    return (year, int(ym[2:]))

def normalize_date(date_raw):
    date_raw = str(date_raw).strip()
    if len(date_raw) == 8:
        return date_raw[:4] + "-" + date_raw[4:6] + "-" + date_raw[6:8]
    elif len(date_raw) == 10:
        return date_raw
    return None

def fetch_and_parse(contract, start_date):
    try:
        df = ak.futures_zh_daily_sina(symbol=contract)
        if df is None or len(df) == 0:
            return None, "无数据"
        
        start_dt = datetime.strptime(start_date, "%Y-%m-%d")
        new_rows = []
        
        for _, r in df.iterrows():
            date_str = normalize_date(r["date"])
            if date_str is None:
                continue
            dt = datetime.strptime(date_str, "%Y-%m-%d")
            if dt < start_dt:
                continue
            settle = float(r.get("settle", 0) or 0)
            if settle <= 0:
                continue
            row = (
                date_str, contract,
                float(r.get("open", 0) or 0),
                float(r.get("high", 0) or 0),
                float(r.get("low", 0) or 0),
                float(r.get("close", 0) or 0),
                int(float(r.get("volume", 0) or 0)),
                int(float(r.get("hold", 0) or 0)),
                settle,
            )
            new_rows.append(row)
        
        if not new_rows:
            return None, "无数据"
        
        last_date = max(new_rows, key=lambda x: x[0])[0]
        return new_rows, last_date
    except Exception as e:
        return None, "错误: " + str(e)

def main():
    conn = get_db()
    
    # 1. 当前数据库已有合约
    print("=== 当前数据库状态 ===")
    rows = conn.execute(
        "SELECT contract, MIN(date), MAX(date), COUNT(*) FROM contract_prices GROUP BY contract ORDER BY contract"
    ).fetchall()
    
    existing_contracts = {}
    for r in rows:
        existing_contracts[r["contract"]] = {
            "start": r["MIN(date)"],
            "end": r["MAX(date)"],
            "count": r["COUNT(*)"]
        }
        print("  %s: %s ~ %s, %s 条" % (r["contract"], r["MIN(date)"], r["MAX(date)"], r["COUNT(*)"]))
    
    print("现有合约数: %d" % len(existing_contracts))
    print()
    
    # 2. 生成合约列表
    print("=== 生成合约编码列表 ===")
    all_contract_codes = []
    for year_short in range(25, 30):
        for month in range(1, 13):
            sym = "LC%02d%02d" % (year_short, month)
            all_contract_codes.append(sym)
    print("生成 %d 个合约编码" % len(all_contract_codes))
    
    # 3. 去重：跳过数据库中已有的
    contracts_to_fetch = [c for c in all_contract_codes if c not in existing_contracts]
    print("需要回拉的合约数: %d" % len(contracts_to_fetch))
    
    if len(contracts_to_fetch) == 0:
        print("所有合约已有数据，无需回拉")
        conn.close()
        return
    
    # 4. 逐个回拉
    print("\n=== 开始回拉历史数据 ===")
    print("起始日期: %s" % START_DATE)
    print()
    
    total_new = 0
    total_skipped = 0
    new_contracts = []
    
    for i, contract in enumerate(contracts_to_fetch):
        status_str = ""
        try:
            rows, status_str = fetch_and_parse(contract, START_DATE)
        except Exception as e:
            print("[%d/%d] %s... 错误: %s" % (i+1, len(contracts_to_fetch), contract, e))
            total_skipped += 1
            time.sleep(0.3)
            continue
        
        if rows is None or len(rows) == 0:
            print("[%d/%d] %s... 跳过 (%s)" % (i+1, len(contracts_to_fetch), contract, status_str))
            total_skipped += 1
            time.sleep(0.3)
            continue
        
        # 检查是否已有
        existing_count = 0
        for row in rows:
            count = conn.execute(
                "SELECT COUNT(*) FROM contract_prices WHERE date=? AND contract=?",
                (row[0], row[1])
            ).fetchone()[0]
            if count > 0:
                existing_count += 1
        
        if existing_count == len(rows):
            print("[%d/%d] %s... 已有 %d 条，跳过" % (i+1, len(contracts_to_fetch), contract, len(rows)))
            total_skipped += 1
            time.sleep(0.3)
            continue
        
        # 写入
        conn.execute("BEGIN")
        for row in rows:
            conn.execute(
                "INSERT OR REPLACE INTO contract_prices (date, contract, settle) VALUES (?, ?, ?)",
                (row[0], row[1], row[8])
            )
            conn.execute(
                "INSERT OR REPLACE INTO contract_daily_all (date, contract, open, high, low, close, volume, hold, settle) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (row[0], row[1], row[2], row[3], row[4], row[5], row[6], row[7], row[8])
            )
        conn.commit()
        
        total_new += len(rows)
        if not new_contracts or new_contracts[-1] != contract:
            new_contracts.append(contract)
        
        print("[%d/%d] %s... ✅ %d 条" % (i+1, len(contracts_to_fetch), contract, len(rows)))
        time.sleep(0.5)
    
    # 5. 不更新spread（该表没有spread列，spread分析走API实时计算）
    print("\n=== 跳过spread更新（该表无spread列，API实时计算） ===")
    
    # 6. 最终统计
    print("\n=== 回拉完成 ===")
    print("新增合约: %d 个" % len(new_contracts))
    print("新写入记录: %d 条" % total_new)
    print("跳过合约: %d 个" % total_skipped)
    
    rows_final = conn.execute(
        "SELECT contract, MIN(date), MAX(date), COUNT(*) FROM contract_prices GROUP BY contract ORDER BY contract"
    ).fetchall()
    for r in rows_final:
        print("  %s: %s ~ %s, %s 条" % (r["contract"], r["MIN(date)"], r["MAX(date)"], r["COUNT(*)"]))
    
    print("\n总合约数: %d" % len(rows_final))
    print("数据范围: %s ~ %s" % (rows_final[0]["MIN(date)"], rows_final[-1]["MAX(date)"]))
    print("总记录数: %d" % conn.execute("SELECT COUNT(*) FROM contract_prices").fetchone()[0])
    
    conn.close()

if __name__ == "__main__":
    main()
