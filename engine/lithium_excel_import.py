#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
碳酸锂 Excel 数据导入脚本 (no_agent cron 脚本)
================================================
读取 AI碳酸锂.xlsx 的三个 sheet，导入到 lithium.db。

Sheet 结构:
  月度1   → lithium_monthly  表（月度宏观+产量+需求指标）
  日度价  → lithium_daily_prices 表（日度价格/价差/利润/情绪）
  周度    → lithium_weekly 表（周度库存+产量+开工率）

每 sheet 前 4 行为元数据（行0=指标名称, 行1=指标Id, 行2=单位, 行3=频率）

用法:
  /home/ubuntu/zinc_venv/bin/python3 /home/ubuntu/.hermes/scripts/lithium_agents/lithium_excel_import.py [xlsx_path]

参数:
  xlsx_path  Excel 文件路径，默认 ~/analysis/temp/AI碳酸锂.xlsx

导入规则:
  1. 默认增量模式：只追加新数据（日期比数据库中最新日期还新的行）
  2. 每列独立追溯最早数据日期：每列只存该列有数据后的行，前面的空行不存
  3. 全量模式：设置环境变量 LITHIUM_IMPORT_MODE=full，则先清空再全量导入
  4. 元数据自动同步：每次导入都会更新 lithium_meta 表
  5. 表结构自动检测：如果旧表结构不匹配，自动删除重建
"""

import os
import sys
import sqlite3
from datetime import datetime
from collections import defaultdict

import pandas as pd

# ─── 路径配置 ────────────────────────────────────────────────────────
DEFAULT_XLSX = os.path.expanduser("~/analysis/temp/AI碳酸锂.xlsx")
DB_PATH = os.environ.get("LITHIUM_DB", "/home/ubuntu/lithium_calendar/lithium.db")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


# ═══════════════════════════════════════════════════════════════════════
# 元数据提取
# ═══════════════════════════════════════════════════════════════════════
def extract_sheet_metadata(df):
    """
    从 Excel sheet 的前 4 行提取元数据。
    返回 dict: {col_idx: {"指标名称": str, "指标Id": str, "单位": str, "频率": str}}
    """
    metadata = {}
    for col_idx in range(1, len(df.columns)):
        metadata[col_idx] = {
            "指标名称": str(df.iloc[0, col_idx]),
            "指标Id": str(df.iloc[1, col_idx]),
            "单位": str(df.iloc[2, col_idx]),
            "频率": str(df.iloc[3, col_idx]),
        }
    return metadata


# ═══════════════════════════════════════════════════════════════════════
# 数据库 schema 创建
# ═══════════════════════════════════════════════════════════════════════
def create_tables(conn):
    # 检查旧表结构，如果不匹配则删除重建
    for table in ['lithium_monthly', 'lithium_daily_prices', 'lithium_weekly', 'lithium_meta']:
        try:
            cursor = conn.execute(f"PRAGMA table_info({table})")
            cols = [c[1] for c in cursor.fetchall()]
            if 'col_name' in cols or 'source' in cols:
                print(f"  ⚠️ 检测到旧表结构 ({table})，删除重建")
                conn.execute(f"DROP TABLE IF EXISTS {table}")
                conn.commit()
        except sqlite3.OperationalError:
            pass  # 表不存在，正常

    conn.execute("""
        CREATE TABLE IF NOT EXISTS lithium_monthly (
            date         TEXT NOT NULL,
            col_idx      INTEGER NOT NULL,
            value        REAL,
            PRIMARY KEY (date, col_idx)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS lithium_daily_prices (
            date         TEXT NOT NULL,
            col_idx      INTEGER NOT NULL,
            value        REAL,
            PRIMARY KEY (date, col_idx)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS lithium_weekly (
            date         TEXT NOT NULL,
            col_idx      INTEGER NOT NULL,
            value        REAL,
            PRIMARY KEY (date, col_idx)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS lithium_meta (
            sheet_name   TEXT NOT NULL,
            col_idx      INTEGER NOT NULL,
            key          TEXT NOT NULL,
            value        TEXT NOT NULL,
            PRIMARY KEY (sheet_name, col_idx, key)
        )
    """)
    conn.commit()


# ═══════════════════════════════════════════════════════════════════════
# 通用导入函数
# ═══════════════════════════════════════════════════════════════════════
def import_sheet(conn, sheet_name, table_name, df, data_start_row=4):
    """
    通用导入函数：
    - 提取每列最早有数据的日期
    - 只从该日期开始插入数据
    - 默认增量模式：只插入比数据库中最新日期还新的行
    - 同时写入元数据表

    参数:
        conn: 数据库连接
        sheet_name: sheet 名称（用于元数据表）
        table_name: 目标表名
        df: 读取的 DataFrame（header=None）
        data_start_row: 数据开始行（通常是第4行，前4行为元数据）

    返回:
        inserted_count: 本次插入的行数
    """
    mode = os.environ.get("LITHIUM_IMPORT_MODE", "append")
    print(f"\n--- 导入 [{sheet_name}] -> {table_name} ---")
    print(f"  Sheet 大小: {len(df)} 行 x {len(df.columns)} 列")
    print(f"  模式: {mode}")

    date_col_idx = 0  # 第0列是日期

    # 1. 提取元数据
    meta = extract_sheet_metadata(df)
    meta_count = len(meta)
    if meta:
        print(f"  元数据: {meta_count} 列的指标名称/Id/单位/频率")
    else:
        print(f"  元数据: 无")

    # 2. 按列找出每列最早有数据的日期
    col_earliest_date = {}  # col_idx -> earliest date string
    for col_idx in range(1, len(df.columns)):
        col_data = df.iloc[data_start_row:, col_idx]
        non_null_mask = col_data.notna()
        if non_null_mask.sum() == 0:
            continue  # 全空的列，跳过
        # 找到第一个非空的日期
        first_valid_idx = non_null_mask.idxmax()
        date_val = df.iloc[data_start_row:, date_col_idx].iloc[first_valid_idx - data_start_row] if first_valid_idx >= data_start_row else df.iloc[first_valid_idx, date_col_idx]
        try:
            dt = pd.Timestamp(date_val)
            col_earliest_date[col_idx] = dt.isoformat()[:10]
        except Exception:
            continue

    print(f"  有数据列数: {len(col_earliest_date)} / {len(df.columns)-1}")

    # 3. 确定要插入的数据范围
    all_dates = df.iloc[data_start_row:, date_col_idx].dropna()
    if len(all_dates) == 0:
        print(f"  ⚠️ 没有有效数据行，跳过")
        return 0

    all_dates_parsed = pd.to_datetime(all_dates)
    # Ensure DatetimeIndex for reliable strftime and boolean indexing
    if not isinstance(all_dates_parsed, pd.DatetimeIndex):
        all_dates_parsed = pd.DatetimeIndex(all_dates_parsed)
    all_dates_str = all_dates_parsed.strftime("%Y-%m-%d")

    # 4. 增量模式：找出比数据库中最新日期还新的日期
    new_dates_parsed = all_dates_parsed  # DatetimeIndex
    new_dates_str = all_dates_str       # ndarray

    if mode == "append":
        try:
            existing = conn.execute(f"SELECT MAX(date) FROM {table_name}").fetchone()[0]
            if existing:
                cutoff = pd.Timestamp(existing)
                mask = all_dates_parsed > cutoff
                if mask.sum() == 0:
                    print(f"  [增量模式] 已有最新日期 {existing}，Excel 中没有更新的数据，跳过")
                    return 0
                new_dates_parsed = all_dates_parsed[mask]
                new_dates_str = all_dates_str[mask]
            else:
                print(f"  [增量模式] 数据库为空，首次导入全量数据")
        except Exception as e:
            print(f"  [增量模式] 查询失败，回退全量: {e}")

    # 5. 全量模式：清空表
    if mode == "full":
        conn.execute(f"DELETE FROM {table_name}")
        conn.execute(f"DELETE FROM lithium_meta WHERE sheet_name = ?", (sheet_name,))
        conn.commit()
        print(f"  [全量模式] 已清空 {table_name}")

    # 6. 写入元数据
    for col_idx, info in meta.items():
        for key, val in info.items():
            if val and val != "nan" and val != "":
                conn.execute(
                    "INSERT OR REPLACE INTO lithium_meta (sheet_name, col_idx, key, value) VALUES (?, ?, ?, ?)",
                    (sheet_name, col_idx, key, val)
                )
    conn.commit()

    # 7. 插入数据
    inserted = 0
    earliest_dates_map = col_earliest_date  # col_idx -> date_str

    for i, date_str in enumerate(new_dates_str):
        for col_idx, earliest in earliest_dates_map.items():
            if date_str < earliest:
                continue  # 该列在该日期之前无数据，跳过
            val = df.iloc[data_start_row + i, col_idx]
            if pd.isna(val):
                continue
            try:
                val = float(val)
            except Exception:
                continue
            conn.execute(
                f"INSERT OR REPLACE INTO {table_name} (date, col_idx, value) VALUES (?, ?, ?)",
                (date_str, col_idx, val)
            )
            inserted += 1

    conn.commit()

    # 8. 统计
    latest = conn.execute(f"SELECT MAX(date) FROM {table_name}").fetchone()[0]
    count = conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
    print(f"  {table_name}: 总 {count} 条, 最新日期 {latest}, 本次插入 {inserted} 条")

    return inserted


# ═══════════════════════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════════════════════
def main():
    xlsx_path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_XLSX

    if not os.path.exists(xlsx_path):
        print(f"❌ Excel 文件不存在: {xlsx_path}")
        sys.exit(1)

    print(f"Excel 文件: {xlsx_path}")
    print(f"数据库: {DB_PATH}")
    print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

    conn = get_db()

    # 创建表
    create_tables(conn)

    # 读取并导入每个 sheet
    sheet_names = ["月度1", "日度价", "周度"]
    tables = ["lithium_monthly", "lithium_daily_prices", "lithium_weekly"]

    total_inserted = 0
    for sheet_name, table_name in zip(sheet_names, tables):
        try:
            df = pd.read_excel(xlsx_path, sheet_name=sheet_name, header=None)
            inserted = import_sheet(conn, sheet_name, table_name, df)
            total_inserted += inserted
        except Exception as e:
            print(f"  ❌ [{sheet_name}] 导入失败: {e}")
            continue

    # 汇总
    print(f"\n{'='*60}")
    print(f"=== 导入汇总 ===")
    print(f"  总插入记录: {total_inserted}")

    for table in ["lithium_monthly", "lithium_daily_prices", "lithium_weekly"]:
        try:
            count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            latest = conn.execute(f"SELECT MAX(date) FROM {table}").fetchone()[0]
            distinct_cols = conn.execute(f"SELECT COUNT(DISTINCT col_idx) FROM {table}").fetchone()[0]
            print(f"  {table}: {count} 条记录, {distinct_cols} 列, 最新日期 {latest}")
        except Exception as e:
            print(f"  {table}: 查询失败 - {e}")

    # 展示元数据表
    try:
        meta_rows = conn.execute("SELECT COUNT(*) FROM lithium_meta").fetchone()[0]
        print(f"  lithium_meta: {meta_rows} 条元数据记录")
    except Exception as e:
        print(f"  lithium_meta: 查询失败 - {e}")

    conn.close()
    print(f"\n✅ 导入完成: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")


if __name__ == "__main__":
    main()
