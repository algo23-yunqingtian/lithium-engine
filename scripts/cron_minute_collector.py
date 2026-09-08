#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
碳酸锂 1 分钟 K 线定时采集
定时从 akshare 拉取 LC0 / LC2609 / LC2611 等 1 分钟数据，存入 lithium.db
使用: python3 /home/ubuntu/lithium_calendar/cron_minute_collector.py
"""

import os
import sys
import sqlite3
import logging
from datetime import datetime

sys.path.insert(0, '/home/ubuntu/zinc_venv/lib/python3.11/site-packages')
import akshare as ak

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(__file__), 'lithium.db')
CONTRACTS = ['LC0', 'LC2609', 'LC2611', 'LC2701']


def init_minute_tables():
    conn = sqlite3.connect(DB_PATH)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS minute_prices (
            datetime    TEXT    NOT NULL,
            contract    TEXT    NOT NULL,
            open        REAL,
            high        REAL,
            low         REAL,
            close       REAL,
            volume      INTEGER DEFAULT 0,
            position    INTEGER DEFAULT 0,
            PRIMARY KEY (datetime, contract)
        );
        CREATE INDEX IF NOT EXISTS idx_minute_contract_dt ON minute_prices(contract, datetime);
    """)
    conn.commit()
    conn.close()


def collect(contract='LC0', period='1'):
    try:
        df = ak.futures_zh_minute_sina(symbol=contract, period=period)
        if df is None or df.empty:
            logger.warning(f'[{contract}] 未拉取到数据')
            return 0

        conn = sqlite3.connect(DB_PATH)
        rows = []
        for _, r in df.iterrows():
            rows.append((
                str(r['datetime']),
                contract,
                float(r['open']), float(r['high']), float(r['low']), float(r['close']),
                int(r['volume']), int(r['hold'])
            ))
        conn.executemany(
            "INSERT OR REPLACE INTO minute_prices (datetime, contract, open, high, low, close, volume, position) "
            "VALUES (?,?,?,?,?,?,?,?)", rows
        )
        conn.commit()
        conn.close()
        logger.info(f'[{contract}] 采集 {len(rows)} 条分钟数据')
        return len(rows)
    except Exception as e:
        logger.error(f'[{contract}] 采集失败: {e}')
        return 0


def main():
    init_minute_tables()
    total = 0
    for c in CONTRACTS:
        n = collect(c, '1')
        total += n
    logger.info(f'总共采集 {total} 条分钟数据: {CONTRACTS}')
    return total


if __name__ == '__main__':
    main()
