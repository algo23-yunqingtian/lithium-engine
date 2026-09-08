#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
碳酸锂基本面综合指数计算 (防透视版)
========================================
设计原则：
1. 标准化用「滚动窗口 z-score」（默认250交易日，只含当日及之前数据），禁止全样本标准化 → 无未来函数
2. 周度/月度数据用 forward-fill 对齐到日度（只填充过去已发布值，不填充未来）
3. 六大维度 → 综合指数，写入 lithium.db 新表 fundamental_indices

维度映射（col_idx → lithium_meta）：
- supply    供应: 月产总量(月1)、进口总量(月1)、周产量(周)、开工率(周)
- demand    需求: 磷酸铁锂产量(月1)、三元产量(月1)、动力电芯(月1)、储能电芯(月1)、周电芯
- inventory 库存: 大样本库存(周)、样本库存(周)、四地社会库存(周)
- profit    利润: 锂辉石生产利润(日)、锂云母生产利润(日)
- sentiment 情绪: 成交情绪(日)、出货情绪上游(日)、购货情绪下游(日)
- basis     基差: 期现价差(日)、升贴水低幅(日)、升贴水高幅(日)

方向：供应↑/库存↑ = 利空(-)；需求/利润/情绪/基差↑ = 利多(+)
"""
import sqlite3, os, sys, json, math
import numpy as np

DB = "/home/ubuntu/lithium_calendar/lithium.db"
OUT_JSON = "/home/ubuntu/analysis/output/lc_fundamental_indices.json"
os.makedirs(os.path.dirname(OUT_JSON), exist_ok=True)

# (sheet, col_idx, 方向) 方向 +1=利多, -1=利空
DIMENSIONS = {
    "supply":    [("月度1", 1, -1), ("月度1", 6, -1), ("周度", 12, -1), ("周度", 17, -1)],
    "demand":    [("月度1", 10, 1), ("月度1", 9, 1), ("月度1", 14, 1), ("月度1", 15, 1), ("周度", 25, 1)],
    "inventory": [("周度", 2, -1), ("周度", 6, -1), ("周度", 11, -1)],
    "profit":    [("日度价", 15, 1), ("日度价", 16, 1)],
    "sentiment": [("日度价", 10, 1), ("日度价", 11, 1), ("日度价", 12, 1)],
    "basis":     [("日度价", 1, 1), ("日度价", 2, 1), ("日度价", 3, 1)],
}

def load_series(conn, sheet, col_idx):
    rows = conn.execute(
        "SELECT date, value FROM lithium_daily_prices WHERE col_idx=? AND value IS NOT NULL ORDER BY date",
        (col_idx,)) if sheet == "日度价" else conn.execute(
        "SELECT date, value FROM lithium_weekly WHERE col_idx=? AND value IS NOT NULL ORDER BY date", (col_idx,)) if sheet == "周度" else conn.execute(
        "SELECT date, value FROM lithium_monthly WHERE col_idx=? AND value IS NOT NULL ORDER BY date", (col_idx,))
    return [(r[0], float(r[1])) for r in rows]

def roll_zscore(series, window=250):
    """滚动窗口 z-score，只使用当日及之前 window 天数据。返回 {date: z}"""
    vals = np.array([v for _, v in series])
    dates = [d for d, _ in series]
    out = {}
    for i, d in enumerate(dates):
        lo = max(0, i - window + 1)
        seg = vals[lo:i+1]
        if len(seg) < 20 or np.std(seg) < 1e-9:
            continue
        z = (vals[i] - np.mean(seg)) / np.std(seg)
        out[d] = float(z)
    return out

def ffill_align(series_map, all_dates):
    """周度/月度 → 日度 forward-fill。series_map: {date: z}"""
    out = {}
    last = None
    for d in all_dates:
        if d in series_map:
            last = series_map[d]
        out[d] = last
    return out

def main():
    conn = sqlite3.connect(DB)
    # 交易日历：用 prices 表日期
    trade_dates = [r[0] for r in conn.execute("SELECT date FROM prices ORDER BY date").fetchall()]
    if not trade_dates:
        print("prices 表为空，无法对齐"); sys.exit(1)
    # 每个维度：取指标滚动 z-score → ffill → 平均 → 汇总
    dim_z = {}
    for dim, cols in DIMENSIONS.items():
        aligned = {}
        cnt = {}
        for sheet, col, sign in cols:
            s = load_series(conn, sheet, col)
            if len(s) < 30:
                continue
            zmap = roll_zscore(s)
            f = ffill_align(zmap, trade_dates)
            for d, z in f.items():
                if z is None:
                    continue
                val = z * sign
                aligned[d] = aligned.get(d, 0.0) + val
                cnt[d] = cnt.get(d, 0) + 1
        dim_z[dim] = {d: aligned[d] / cnt[d] for d in aligned if cnt.get(d, 0) > 0}
    # 综合指数 = 各维度均值（仅当 ≥3 维度有值）
    all_dates = [d for d in trade_dates if sum(1 for dim in dim_z if d in dim_z[dim]) >= 3]
    rows_out = []
    for d in all_dates:
        vals = [dim_z[dim][d] for dim in dim_z if d in dim_z[dim]]
        composite = float(np.mean(vals))
        rows_out.append({
            "date": d,
            **{dim: round(dim_z[dim].get(d), 4) if d in dim_z[dim] else None for dim in DIMENSIONS},
            "composite": round(composite, 4),
            "n_dims": len(vals),
        })
    # 写库
    conn.execute("DROP TABLE IF EXISTS fundamental_indices")
    conn.execute("""CREATE TABLE fundamental_indices (
        date TEXT PRIMARY KEY, supply REAL, demand REAL, inventory REAL,
        profit REAL, sentiment REAL, basis REAL, composite REAL, n_dims INTEGER)""")
    conn.executemany("INSERT OR REPLACE INTO fundamental_indices VALUES (?,?,?,?,?,?,?,?,?)",
                     [(r["date"], r.get("supply"), r.get("demand"), r.get("inventory"),
                       r.get("profit"), r.get("sentiment"), r.get("basis"), r["composite"], r["n_dims"]) for r in rows_out])
    conn.commit()
    conn.close()
    # 输出 JSON 摘要
    with open(OUT_JSON, "w") as f:
        json.dump(rows_out[-180:], f, ensure_ascii=False)
    # 打印摘要
    latest = rows_out[-5:] if rows_out else []
    print(f"指数总天数: {len(rows_out)}  ({rows_out[0]['date']} ~ {rows_out[-1]['date'] if rows_out else 'N/A'})")
    print("最新5天:")
    for r in latest:
        print(f"  {r['date']} supply={r.get('supply')} demand={r.get('demand')} inv={r.get('inventory')} profit={r.get('profit')} senti={r.get('sentiment')} basis={r.get('basis')} composite={r['composite']} (n={r['n_dims']})")
    print(f"JSON: {OUT_JSON}")

if __name__ == "__main__":
    main()
