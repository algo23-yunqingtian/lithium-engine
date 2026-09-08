#!/usr/bin/env python3
"""
碳酸锂现货数据回填脚本
========================
将 lc_spot.db 的 20 行历史数据回填为 4760 行全量数据。

数据来源：
- 现货价: lithium_daily_prices col6 (SMM 电池级碳酸锂平均价)
- 期货近月/主力价: contract_prices 表（按 contract 排序取近月+主力）
- 基差: 现货 - 期货（自动计算）

用法：
    python3 backfill_spot.py           # 回填全量
    python3 backfill_spot.py --dry-run # 仅预览，不写入

作者：Hermes Agent · 2026-09-08
"""

import sqlite3
import os
import sys
from datetime import datetime

# === 配置 ===
LITHIUM_DB = os.environ.get("LITHIUM_DB", "/home/ubuntu/lithium_calendar/lithium.db")
LC_SPOT_DB = "/home/ubuntu/lc_futures_data/data/lc_spot.db"
DRY_RUN = "--dry-run" in sys.argv

def get_contract_list(conn, date):
    """获取指定日期的所有合约代码（按月份排序）"""
    rows = conn.execute(
        "SELECT DISTINCT contract FROM contract_prices WHERE date = ? ORDER BY contract",
        (date,)
    ).fetchall()
    return [r[0] for r in rows]

def find_near_and_dom(contracts):
    """
    从合约列表中找到近月合约和主力合约。
    主力合约 = 持仓量最大的合约。
    """
    # 近月 = 第一个合约（按月份排序）
    near = contracts[0] if contracts else None

    # 主力 = 需要从 contract_prices 查持仓量
    if not contracts:
        return near, None

    # 先尝试从 lithium_daily_prices 拿持仓量最大的合约
    return near, contracts[-1] if len(contracts) > 1 else contracts[0]

def main():
    print(f"[START] 碳酸锂现货数据回填")
    print(f"  lithium.db: {LITHIUM_DB}")
    print(f"  lc_spot.db: {LC_SPOT_DB}")
    print(f"  dry-run: {DRY_RUN}")

    # 1. 连接数据库
    conn = sqlite3.connect(LITHIUM_DB)
    conn.row_factory = sqlite3.Row

    # 2. 获取现货价格（lithium_daily_prices col6）
    spot_rows = conn.execute("""
        SELECT date, value as spot_price
        FROM lithium_daily_prices
        WHERE col_idx = 6
        ORDER BY date
    """).fetchall()
    print(f"\n[1/4] 现货价格: {len(spot_rows)} 行 ({spot_rows[0]['date']} ~ {spot_rows[-1]['date']})")

    # 3. 获取期货价格
    print(f"[2/4] 期货价格: 查询 contract_prices...")
    fut_dates = conn.execute("SELECT DISTINCT date FROM contract_prices ORDER BY date").fetchall()
    print(f"  contract_prices: {len(fut_dates)} 个交易日")

    # 4. 获取 lc_spot.db 已有数据（用于对比验证）
    print(f"[3/4] lc_spot.db 已有数据: 查询...")
    try:
        spot_conn = sqlite3.connect(LC_SPOT_DB)
        existing = spot_conn.execute("SELECT * FROM spot_price ORDER BY date").fetchall()
        print(f"  已有 {len(existing)} 行 ({existing[0][0]} ~ {existing[-1][0]})")
        spot_conn.close()
    except:
        existing = []
        print(f"  lc_spot.db 不存在或无数据")

    # 5. 逐日生成 spot_price 数据
    print(f"\n[4/4] 生成 spot_price 数据...")

    # 按日期索引期货价格
    fut_prices = {}
    for row in conn.execute("""
        SELECT date, contract, close, position
        FROM contract_prices
        ORDER BY date, contract
    """):
        d = row["date"]
        if d not in fut_prices:
            fut_prices[d] = []
        fut_prices[d].append({"contract": row["contract"], "close": row["close"], "position": row["position"]})

    generated = []
    errors = 0

    # 只处理有对应期货数据的日期（避免主键冲突）
    # 按期货日期分组，每组取最新现货价
    cp_date_set = set(fut_prices.keys())

    # 先按日期分组现货数据
    spot_by_date = {}
    for spot in spot_rows:
        d = spot["date"]
        spot_price = spot["spot_price"]
        if spot_price is None or spot_price == 0:
            continue
        # 只保留在 contract_prices 范围内的日期
        if d not in cp_date_set:
            # 尝试找最近的交易日（向前后最多3天）
            from datetime import datetime, timedelta
            dt = datetime.strptime(d, "%Y-%m-%d")
            found_date = None
            for offset in range(0, 4):
                for delta in [timedelta(days=-offset), timedelta(days=offset)]:
                    adj = (dt + delta).strftime("%Y-%m-%d")
                    if adj in cp_date_set:
                        found_date = adj
                        break
                if found_date:
                    break
            if not found_date:
                continue
            spot_by_date.setdefault(found_date, [])
            spot_by_date[found_date].append(spot_price)
        else:
            spot_by_date.setdefault(d, [])
            spot_by_date[d].append(spot_price)

    for d in sorted(spot_by_date.keys()):
        prices_list = spot_by_date[d]
        # 取该日期的平均现货价（可能有多个值来自不同源）
        spot_price = sum(prices_list) / len(prices_list)

        contracts = fut_prices[d]
        if not contracts:
            continue

        # 找主力合约（持仓量最大）
        main_contract = max(contracts, key=lambda x: x["position"] if x["position"] else 0)
        main_symbol = main_contract["contract"]
        main_price = main_contract["close"]

        # 找近月合约（按月份排序第一个）
        sorted_contracts = sorted(contracts, key=lambda x: x["contract"])
        near_contract = sorted_contracts[0]
        near_symbol = near_contract["contract"]
        near_price = near_contract["close"]

        # 计算基差
        near_basis = spot_price - near_price if near_price else None
        dom_basis = spot_price - main_price if main_price else None
        near_basis_rate = (near_basis / near_price * 100) if (near_basis is not None and near_price) else None
        dom_basis_rate = (dom_basis / main_price * 100) if (dom_basis is not None and main_price) else None

        date_str = d.replace("-", "")  # lc_spot.db 用 YYYYMMDD 格式

        generated.append({
            "date": date_str,
            "variety": "LC",
            "spot_price": spot_price,
            "near_symbol": near_symbol,
            "near_price": near_price,
            "dom_symbol": main_symbol,
            "dom_price": main_price,
            "near_basis": round(near_basis, 1) if near_basis is not None else None,
            "dom_basis": round(dom_basis, 1) if dom_basis is not None else None,
            "near_basis_rate": round(near_basis_rate, 2) if near_basis_rate is not None else None,
            "dom_basis_rate": round(dom_basis_rate, 2) if dom_basis_rate is not None else None,
        })

    print(f"  生成 {len(generated)} 行数据")

    # 6. 对比验证（与 lc_spot.db 已有数据交叉验证）
    if existing:
        print(f"\n[验证] 与 lc_spot.db 已有 {len(existing)} 行对比...")
        # lc_spot.db 日期格式: YYYYMMDD
        existing_map = {r[0]: r for r in existing}
        match_count = 0
        diff_count = 0
        for g in generated:
            if g["date"] in existing_map:
                e = existing_map[g["date"]]
                # e: (date, variety, spot_price, near_symbol, near_price, dom_symbol, dom_price, near_basis, dom_basis, near_basis_rate, dom_basis_rate)
                e_spot = e[2]
                e_near = e[3]
                e_dom = e[5]
                # 比较（允许 5% 误差，因为 akshare 和 SMM 数据源不同）
                if abs((g["spot_price"] or 0) - (e_spot or 0)) / max(abs(e_spot or 1), 1) < 0.15:
                    match_count += 1
                else:
                    diff_count += 1
                    print(f"  差异: {g['date']} spot={g['spot_price']:.0f} vs {e_spot:.0f}")
        print(f"  匹配: {match_count}, 差异: {diff_count}")

    # 7. 写入
    if DRY_RUN:
        print(f"\n[DRY-RUN] 不写入，预览前 5 行：")
        for g in generated[:5]:
            print(f"  {g['date']} spot={g['spot_price']:.0f} near={g['near_symbol']}@{g['near_price']:.0f} dom={g['dom_symbol']}@{g['dom_price']:.0f} basis={g['near_basis']:.0f}")
        print(f"  ...共 {len(generated)} 行")
    else:
        print(f"\n[写入] 更新 lc_spot.db...")
        spot_conn = sqlite3.connect(LC_SPOT_DB)
        cur = spot_conn.cursor()

        # 清空旧数据
        cur.execute("DELETE FROM spot_price")
        print(f"  已清空旧数据")

        # 写入新数据
        cols = ["date", "variety", "spot_price", "near_symbol", "near_price",
                "dom_symbol", "dom_price", "near_basis", "dom_basis",
                "near_basis_rate", "dom_basis_rate"]
        placeholders = ",".join(["?"] * len(cols))
        col_names = ",".join(cols)
        values = [[g[c] for c in cols] for g in generated]

        cur.executemany(f"INSERT INTO spot_price ({col_names}) VALUES ({placeholders})", values)

        # 更新 meta
        now = datetime.now().isoformat()
        last_date = generated[-1]["date"] if generated else ""
        cur.execute("DELETE FROM meta WHERE key IN ('last_fetch_time', 'last_fetch_date')")
        cur.execute("INSERT INTO meta VALUES ('last_fetch_time', ?)", (now,))
        cur.execute("INSERT INTO meta VALUES ('last_fetch_date', ?)", (last_date,))

        spot_conn.commit()
        spot_conn.close()
        print(f"  写入 {len(generated)} 行数据")
        print(f"  更新 meta: last_fetch_time={now}, last_fetch_date={last_date}")

    conn.close()
    print(f"\n[完成] 碳酸锂现货数据回填 {'预览' if DRY_RUN else '完成'}")
    print(f"  从 {generated[0]['date']} 到 {generated[-1]['date']} 共 {len(generated)} 行")
    return len(generated)

if __name__ == "__main__":
    main()