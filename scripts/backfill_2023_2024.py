#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
回填 contract_prices 2023-07-21 ~ 2024-12-31 全合约日度结算价
（碳酸锂 2023-07-21 上市，首批 LC2401~LC2407，逐月加挂 LC2501~LC2512）
数据源: akshare futures_zh_daily_sina（单合约全历史日线，含 settle）
只插入 2024-12-31 之前的行，不触碰已有 2025 年后数据。
"""
import sqlite3
import time
import sys
import akshare as ak

DB_PATH = "/home/ubuntu/lithium_calendar/lithium.db"
START_DATE = "2023-07-21"
END_DATE = "2024-12-31"


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def normalize_date(d):
    d = str(d).strip()
    if len(d) == 8:
        return d[:4] + "-" + d[4:6] + "-" + d[6:8]
    if len(d) == 10:
        return d
    return None


def fetch_contract(contract):
    """拉取单合约全历史，过滤到目标区间，返回 rows 列表"""
    df = ak.futures_zh_daily_sina(symbol=contract)
    if df is None or len(df) == 0:
        return []
    rows = []
    for _, r in df.iterrows():
        ds = normalize_date(r["date"])
        if ds is None:
            continue
        if ds < START_DATE or ds > END_DATE:
            continue
        settle = float(r.get("settle", 0) or 0)
        if settle <= 0:
            continue
        rows.append((
            ds, contract,
            float(r.get("open", 0) or 0),
            float(r.get("high", 0) or 0),
            float(r.get("low", 0) or 0),
            float(r.get("close", 0) or 0),
            int(float(r.get("volume", 0) or 0)),
            int(float(r.get("hold", 0) or 0)),
            settle,
        ))
    return rows


def main():
    # 候选合约：2023-08 ~ 2025-12（不存在的自动跳过），另加 LC2601 验证
    codes = []
    for yy in (23, 24, 25):
        for mm in range(1, 13):
            codes.append("LC%02d%02d" % (yy, mm))
    codes += ["LC2601"]

    conn = get_db()
    print("开始回填 %s ~ %s 全合约数据" % (START_DATE, END_DATE))

    total_new = 0
    fetched_contracts = 0
    errors = []

    for i, c in enumerate(codes):
        try:
            rows = fetch_contract(c)
        except Exception as e:
            errors.append("%s: %s" % (c, e))
            time.sleep(0.3)
            continue

        if not rows:
            # 可能是合约不存在（未上市/未到区间）
            print("[%d/%d] %s: 区间内无数据（跳过）" % (i + 1, len(codes), c))
            time.sleep(0.2)
            continue

        # INSERT OR REPLACE 天然幂等：已存在覆盖修正，不存在插入
        conn.execute("BEGIN")
        for row in rows:
            conn.execute(
                "INSERT OR REPLACE INTO contract_prices (date, contract, open, high, low, close, volume, position, settle) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (row[0], row[1], row[2], row[3], row[4], row[5], row[6], row[7], row[8]),
            )
            conn.execute(
                "INSERT OR REPLACE INTO contract_daily_all (contract, date, open, high, low, close, volume, hold, settle) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (row[1], row[0], row[2], row[3], row[4], row[5], row[6], row[7], row[8]),
            )
        conn.commit()

        total_new += len(rows)
        fetched_contracts += 1
        dmin = min(r[0] for r in rows)
        dmax = max(r[0] for r in rows)
        print("[%d/%d] %s: %d 条 (%s ~ %s)" % (i + 1, len(codes), c, len(rows), dmin, dmax))
        time.sleep(0.4)

    print("\n=== 完成 ===")
    print("写入合约数: %d" % fetched_contracts)
    print("写入/更新记录: %d 条" % total_new)
    if errors:
        print("错误: %d 个" % len(errors))
        for e in errors[:10]:
            print("  ", e)

    # 最终状态
    rng = conn.execute("SELECT MIN(date), MAX(date), COUNT(*) FROM contract_prices").fetchone()
    print("contract_prices 范围: %s ~ %s, 共 %d 条" % (rng[0], rng[1], rng[2]))
    conn.close()


if __name__ == "__main__":
    main()
