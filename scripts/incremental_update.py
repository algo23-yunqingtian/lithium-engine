#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
碳酸锂期货数据增量更新脚本（cron 用）

核心逻辑:
  - 每个合约只对比数据库最后日期 vs akshare 最后日期
  - 有新数据则只拉取"最后日期+1"到最新之间的增量数据
  - 幂等写入 contract_prices + contract_daily_all
  - 自动更新 spread 字段

用法:
  /home/ubuntu/zinc_venv/bin/python incremental_update.py [--dry-run] [--force]
"""

import sqlite3
import sys
import time
from datetime import datetime

DB_PATH = "/home/ubuntu/lithium_calendar/lithium.db"


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def get_last_date_per_contract(conn):
    """获取每个合约的最后一个交易日"""
    rows = conn.execute(
        "SELECT contract, MAX(date) as last_date FROM contract_prices GROUP BY contract"
    ).fetchall()
    return {r["contract"]: r["last_date"] for r in rows}


def fetch_incremental_data(contract, after_date):
    """
    拉取单个合约全部历史，然后过滤出 after_date 之后的数据
    因为 akshare 不支持按日期过滤，只能全量拉后筛选
    """
    import akshare as ak
    try:
        df = ak.futures_zh_daily_sina(symbol=contract)
        if df is None or len(df) == 0:
            return []
        
        after_dt = datetime.strptime(after_date, "%Y-%m-%d")
        rows = []
        for _, r in df.iterrows():
            date_raw = str(r["date"]).strip()
            if len(date_raw) == 8:
                date_str = date_raw[:4] + "-" + date_raw[4:6] + "-" + date_raw[6:8]
            elif len(date_raw) == 10:
                date_str = date_raw
            else:
                continue
            
            dt = datetime.strptime(date_str, "%Y-%m-%d")
            if dt <= after_dt:
                continue
            
            settle = float(r.get("settle", 0) or 0)
            if settle <= 0:
                continue
            
            rows.append((
                date_str, contract,
                float(r.get("open", 0) or 0),
                float(r.get("high", 0) or 0),
                float(r.get("low", 0) or 0),
                float(r.get("close", 0) or 0),
                int(float(r.get("volume", 0) or 0)),
                int(float(r.get("hold", 0) or 0)),
                settle,
            ))
        return rows
    except Exception as e:
        print(f"    [错误] {e}")
        return []


def _month_sort_key(symbol):
    if symbol == "LC0":
        return (9999, 99)
    ym = symbol[2:]
    if len(ym) != 4:
        return (9999, 99)
    yy = int(ym[:2])
    year = 2000 + yy if yy < 50 else 1900 + yy
    return (year, int(ym[2:]))


def run_incremental(force=False):
    conn = get_db()
    
    last_dates = get_last_date_per_contract(conn)
    if not last_dates:
        print("数据库为空，请手动拉取全量数据")
        conn.close()
        return
    
    # 找出整体最新日期
    overall_latest = max(last_dates.values())
    today = datetime.now().strftime("%Y-%m-%d")
    
    print(f"当前整体最新日期: {overall_latest}")
    print(f"今天: {today}")
    
    if overall_latest >= today:
        print("数据已是最新，无需更新")
        conn.close()
        return
    
    # 确定需要更新的合约列表
    all_contracts = sorted(last_dates.keys(), key=_month_sort_key)
    
    # 获取所有上市合约的完整列表（包括尚未上市的）
    # 从 akshare 拉取 LC0 来确认当前上市的合约
    try:
        import akshare as ak
        lc0_df = ak.futures_zh_daily_sina(symbol="LC0")
        if lc0_df is not None and len(lc0_df) > 0:
            # 主力合约每天可能不同，但所有在 LC0 数据中出现过的合约都是有效合约
            # 我们使用数据库中已有的合约列表即可
            pass
    except:
        pass
    
    # 增量拉取
    total_new = 0
    total_skipped = 0
    errors = 0
    all_new_rows = []
    
    for i, contract in enumerate(all_contracts):
        db_last = last_dates[contract]
        
        # 跳过还没上市的合约（数据库中没有数据说明该合约还未上市）
        if db_last is None:
            continue
        
        print(f"  [{i+1}/{len(all_contracts)}] {contract}: DB最新={db_last}...", end=" ")
        
        # 拉取增量数据
        rows = fetch_incremental_data(contract, db_last)
        
        if not rows:
            # 可能还没到该合约的新交易日（周末/节假日）
            # 尝试直接拉取全部确认一下最新日期
            try:
                import akshare as ak
                df = ak.futures_zh_daily_sina(symbol=contract)
                if df is not None and len(df) > 0:
                    last_df_date = str(df["date"].iloc[-1]).replace("-", "")
                    if len(last_df_date) == 8:
                        last_df_date = last_df_date[:4] + "-" + last_df_date[4:6] + "-" + last_df_date[6:8]
                    if last_df_date > db_last:
                        # 有数据但没被增量拉取到（可能日期格式问题），拉全量
                        print(f"akshare最新={last_df_date}，增量未覆盖，改用全量拉取")
                        rows = fetch_incremental_data(contract, "2020-01-01")
                    else:
                        print("无新数据")
                        continue
                else:
                    print("akshare无数据")
                    continue
            except Exception as e:
                print(f"[错误] {e}")
                errors += 1
                continue
        
        print(f"新数据 {len(rows)} 条")
        total_new += len(rows)
        all_new_rows.extend(rows)
        time.sleep(0.3)
    
    if errors > 0:
        print(f"\n⚠️  {errors} 个合约拉取失败")
    
    if not all_new_rows:
        print("没有新数据需要写入")
        conn.close()
        return
    
    # 写入数据库
    print(f"\n写入 {len(all_new_rows)} 条新记录...")
    
    conn.execute("BEGIN")
    for row in all_new_rows:
        conn.execute("""
            INSERT OR REPLACE INTO contract_prices (date, contract, settle, spread)
            VALUES (?, ?, ?, 0)
        """, (row[0], row[1], row[8]))
        conn.execute("""
            INSERT OR REPLACE INTO contract_daily_all 
            (date, contract, open, high, low, close, volume, hold, settle)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (row[0], row[1], row[2], row[3], row[4], row[5], row[6], row[7], row[8]))
    
    conn.commit()
    
    # 更新 spread 字段
    new_dates = sorted(set(r[0] for r in all_new_rows))
    _update_spreads(conn, new_dates)
    
    new_max = max(new_dates)
    print(f"\n✅ 更新完成!")
    print(f"   新写入: {len(all_new_rows)} 条记录")
    print(f"   覆盖天数: {len(new_dates)} 天 ({new_dates[0]} ~ {new_max})")
    print(f"   更新后最新日期: {new_max}")
    
    conn.close()


def _update_spreads(conn, date_range):
    """为指定日期范围重新计算 contract_prices 的 spread 字段"""
    for date in date_range:
        rows = conn.execute(
            "SELECT contract, settle FROM contract_prices WHERE date = ? ORDER BY contract",
            (date,)
        ).fetchall()
        
        if len(rows) < 2:
            continue
        
        rows_sorted = sorted(rows, key=lambda c: _month_sort_key(c["contract"]))
        
        for i in range(1, len(rows_sorted)):
            spread = rows_sorted[i]["settle"] - rows_sorted[i - 1]["settle"]
            conn.execute(
                "UPDATE contract_prices SET spread = ? WHERE date = ? AND contract = ?",
                (spread, date, rows_sorted[i]["contract"])
            )
    
    conn.commit()


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    force = "--force" in sys.argv
    
    if dry_run:
        conn = get_db()
        last_dates = get_last_date_per_contract(conn)
        overall_latest = max(last_dates.values()) if last_dates else None
        print(f"当前最新日期: {overall_latest}")
        for c in sorted(last_dates.keys(), key=_month_sort_key):
            print(f"  {c}: {last_dates[c]}")
        conn.close()
        print("\n【DRY-RUN】请去掉 --dry-run 执行实际更新")
    else:
        run_incremental(force=force)
