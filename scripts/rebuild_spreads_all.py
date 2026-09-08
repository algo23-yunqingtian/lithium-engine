#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
重建 spreads 表（全日期通用版）
逻辑: 逐交易日取 contract_prices 实际上市合约 → sort_key=(year,month) 排序 →
      相邻合约价差 spread = near.settle - far.settle（与 spread_analysis_api 一致）→
      写入 spreads(date, near_contract, far_contract, spread)
"""
import sqlite3

DB_PATH = "/home/ubuntu/lithium_calendar/lithium.db"


def month_sort_key(symbol):
    ym = symbol[2:]
    if len(ym) != 4:
        return (9999, 99)
    yy = int(ym[:2])
    year = 2000 + yy if yy < 50 else 1900 + yy
    return (year, int(ym[2:]))


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # 1. 读取全部 (date, contract, settle)
    rows = conn.execute(
        "SELECT date, contract, settle FROM contract_prices ORDER BY date"
    ).fetchall()
    print("contract_prices 总记录: %d" % len(rows))

    by_date = {}
    for r in rows:
        if r["settle"] is None or float(r["settle"]) <= 0:
            continue
        by_date.setdefault(r["date"], {})[r["contract"]] = float(r["settle"])

    dates = sorted(by_date.keys())
    print("覆盖交易日: %d (%s ~ %s)" % (len(dates), dates[0], dates[-1]))

    # 2. 逐日构建相邻月差
    conn.execute("BEGIN")
    conn.execute("DELETE FROM spreads")
    count = 0
    for d in dates:
        contracts = sorted(by_date[d].keys(), key=month_sort_key)
        for i in range(len(contracts) - 1):
            near = contracts[i]
            far = contracts[i + 1]
            spread = round(by_date[d][near] - by_date[d][far], 2)
            conn.execute(
                "INSERT OR REPLACE INTO spreads (date, near_contract, far_contract, spread) VALUES (?, ?, ?, ?)",
                (d, near, far, spread),
            )
            count += 1
    conn.commit()

    # 3. 统计
    print("写入月差记录: %d 条" % count)
    rng = conn.execute("SELECT MIN(date), MAX(date), COUNT(*) FROM spreads").fetchone()
    print("spreads 范围: %s ~ %s, 共 %d 条" % (rng[0], rng[1], rng[2]))

    # 按年份统计
    print("\n按年份:")
    for r in conn.execute(
        "SELECT substr(date,1,4) y, COUNT(*) c, COUNT(DISTINCT date) d FROM spreads GROUP BY y ORDER BY y"
    ).fetchall():
        print("  %s: %d 条 / %d 个交易日" % (r["y"], r["c"], r["d"]))

    # 抽查 2023-07-21（上市首日）
    sample = conn.execute(
        "SELECT near_contract, far_contract, spread FROM spreads WHERE date='2023-07-21' ORDER BY near_contract"
    ).fetchall()
    print("\n2023-07-21 上市首日月差:")
    if sample:
        for s in sample[:6]:
            print("  %s -> %s = %+.0f" % (s["near_contract"], s["far_contract"], s["spread"]))
    else:
        print("  （无数据）")

    conn.close()


if __name__ == "__main__":
    main()
