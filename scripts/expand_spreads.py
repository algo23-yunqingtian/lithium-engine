#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
扩充 spreads 表月差数据

来源: 用 contract_prices (具体月份合约) + prices (主力连续 LC0) 计算
覆盖范围: 从 2026-05-01 开始到最新交易日

月差计算公式 (远月-近月):
  LC2608 - LC2609 = LC2609.close - LC2608.close  (contango 为正)
  LC2609 - LC2610
  LC2610 - LC2611
  LC2611 - LC2612
  LC0 - LC2608 = LC0.close (from prices) - LC2608.close

注意: 如果某日缺少任一合约数据，该日这对月差不写入。
"""

import sqlite3
from collections import defaultdict
from datetime import datetime

DB_PATH = "/home/ubuntu/lithium_calendar/lithium.db"

SPREAD_PAIRS = [
    ("LC0", "LC2608"),
    ("LC2608", "LC2609"),
    ("LC2609", "LC2610"),
    ("LC2610", "LC2611"),
    ("LC2611", "LC2612"),
]


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # 1. 读取所有需要的价格数据
    # 1a. contract_prices: 具体月份合约
    contract_rows = conn.execute("""
        SELECT date, contract, close
        FROM contract_prices
        WHERE date >= '2026-05-01'
        ORDER BY date, contract
    """).fetchall()
    contract_data = defaultdict(dict)
    for r in contract_rows:
        contract_data[r['date']][r['contract']] = r['close']

    # 1b. prices: LC0 (主力连续)
    price_rows = conn.execute("""
        SELECT date, close
        FROM prices
        WHERE date >= '2026-05-01'
        ORDER BY date
    """).fetchall()
    price_data = {}
    for r in price_rows:
        price_data[r['date']] = r['close']

    # 2. 找出有完整数据的日期
    all_dates = sorted(set(list(contract_data.keys()) + list(price_data.keys())))
    
    # 对每个日期，检查有哪些月差可以计算
    # LC2608-LC2612 需要 contract_prices
    # LC0-LC2608 需要 prices + contract_prices

    dates_with_all_contracts = set()
    for d in all_dates:
        has_months = all(c in contract_data.get(d, {}) 
                        for c in ['LC2608', 'LC2609', 'LC2610', 'LC2611', 'LC2612'])
        if has_months:
            dates_with_all_contracts.add(d)

    print(f"contract_prices 日期: {len(contract_data)}")
    print(f"prices 日期: {len(price_data)}")
    print(f"有全部月份合约的日期: {len(dates_with_all_contracts)}")

    # 3. 清空 spreads 表，重新写入
    conn.execute("DELETE FROM spreads")
    
    count = 0
    missing_dates = []
    for d in sorted(dates_with_all_contracts):
        cp = contract_data[d]
        
        # LC2608-LC2612 这4对
        pairs = [
            ("LC2608", "LC2609"),
            ("LC2609", "LC2610"),
            ("LC2610", "LC2611"),
            ("LC2611", "LC2612"),
        ]
        for near, far in pairs:
            if near in cp and far in cp:
                spread = round(cp[far] - cp[near], 2)  # far - near
                conn.execute("""
                    INSERT INTO spreads (date, near_contract, far_contract, spread)
                    VALUES (?, ?, ?, ?)
                """, (d, near, far, spread))
                count += 1
        
        # LC0-LC2608 (LC0 from prices)
        if d in price_data:
            lc0_close = price_data[d]
            if 'LC2608' in cp:
                spread = round(lc0_close - cp['LC2608'], 2)
                conn.execute("""
                    INSERT INTO spreads (date, near_contract, far_contract, spread)
                    VALUES (?, ?, ?, ?)
                """, (d, "LC0", "LC2608", spread))
                count += 1

    conn.commit()
    
    # 4. 统计
    print(f"\n写入 {count} 条月差记录")
    
    # 按日期统计
    date_counts = conn.execute("""
        SELECT date, COUNT(*) as cnt 
        FROM spreads 
        GROUP BY date 
        ORDER BY date
    """).fetchall()
    print(f"覆盖日期: {len(date_counts)} 天")
    if date_counts:
        print(f"范围: {date_counts[0]['date']} ~ {date_counts[-1]['date']}")
    
    # 按日期范围统计
    may_jun = conn.execute("""
        SELECT COUNT(*) FROM spreads 
        WHERE date >= '2026-05-01' AND date < '2026-07-01'
    """).fetchone()[0]
    july = conn.execute("""
        SELECT COUNT(*) FROM spreads 
        WHERE date >= '2026-07-01'
    """).fetchone()[0]
    print(f"  5-6月: {may_jun} 条")
    print(f"  7月: {july} 条")
    
    # 5. 展示示例
    print(f"\n=== 示例数据 ===")
    sample_dates = sorted([d for d, in conn.execute(
        "SELECT DISTINCT date FROM spreads WHERE date < '2026-07-01' ORDER BY date LIMIT 5"
    )])
    for d in sample_dates:
        rows = conn.execute(
            "SELECT near_contract, far_contract, spread FROM spreads WHERE date=? ORDER BY near_contract",
            (d,)
        ).fetchall()
        parts = [f"{r['near_contract']}->{r['far_contract']}={r['spread']:+.0f}" for r in rows]
        print(f"  {d}  {'  '.join(parts)}")
    
    print(f"\n完成: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    conn.close()


if __name__ == "__main__":
    main()
