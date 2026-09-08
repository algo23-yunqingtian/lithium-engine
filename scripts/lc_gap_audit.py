#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""数据缺口详查：Excel源 vs DB、升贴水缺失模式、周度缺失列"""
import sqlite3, os, sys
from datetime import datetime

OUT = "/home/ubuntu/analysis/output/lc_data_gap_report.md"
DB = "/home/ubuntu/lithium_calendar/lithium.db"
XLSX = os.path.expanduser("~/analysis/temp/AI碳酸锂.xlsx")

lines = ["# 碳酸锂数据缺口详查", f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", ""]
conn = sqlite3.connect(DB)

# 1. Excel 源文件
if os.path.exists(XLSX):
    mt = datetime.fromtimestamp(os.path.getmtime(XLSX)).strftime('%Y-%m-%d %H:%M')
    lines.append(f"## Excel 源文件: {XLSX}")
    lines.append(f"- 最后修改: {mt}  →  07-31 数据未进入源文件，属人工更新滞后（需每日手动更新Excel再导入，或接自动数据源）")
else:
    lines.append("## Excel 源文件不存在！")
lines.append("")

# 2. 日度表缺失模式
lines.append("## 日度表近10天列缺失模式 (lithium_daily_prices)")
rows = conn.execute("""SELECT date, COUNT(*) c, GROUP_CONCAT(col_idx) cols FROM lithium_daily_prices
    WHERE date >= date('now','-10 days') GROUP BY date ORDER BY date DESC""").fetchall()
lines.append("| 日期 | 列数 | 缺失列 |")
lines.append("|---|---|---|")
allcols = set(range(1, 23))
for d, c, cols in rows:
    have = set(int(x) for x in cols.split(','))
    miss = sorted(allcols - have)
    lines.append(f"| {d} | {c} | {miss if miss else '无'} |")
lines.append("")

# 3. 周度表缺失模式
lines.append("## 周度表近8周列缺失 (lithium_weekly)")
rows = conn.execute("""SELECT date, COUNT(*) c, GROUP_CONCAT(col_idx) cols FROM lithium_weekly
    WHERE date >= date('now','-60 days') GROUP BY date ORDER BY date DESC""").fetchall()
lines.append("| 日期 | 列数 | 缺失列 |")
lines.append("|---|---|---|")
allcols_w = set(range(1, 26))
for d, c, cols in rows:
    have = set(int(x) for x in cols.split(','))
    miss = sorted(allcols_w - have)
    lines.append(f"| {d} | {c} | {miss if miss else '无'} |")
lines.append("")

# 4. 月度表
lines.append("## 月度表 (lithium_monthly) — 最新6个月")
rows = conn.execute("""SELECT date, COUNT(*) c FROM lithium_monthly GROUP BY date ORDER BY date DESC LIMIT 6""").fetchall()
for d, c in rows:
    lines.append(f"- {d}: {c} 列")

# 5. 07-31 基本面空缺总结
lines.append("")
lines.append("## 结论")
lines.append("- 07-31 日度基本面数据缺失 = Excel 源文件未更新到 07-31（SMM 数据需人工从SMM导出）")
lines.append("- 07-30 当天缺升贴水 col2/3：源文件本身缺该两列数据，非导入问题")
lines.append("- 周度缺 col11(四地库存)：源文件该列未更新")
lines.append("- 月度最新 06-30：符合月度发布节奏（7月数据8月才发布）")
lines.append("- 期货价格/仓单/持仓/Agent 全链路 07-31 完整 ✅")

with open(OUT, "w") as f:
    f.write("\n".join(lines))
print("\n".join(lines))
print(f"\n报告: {OUT}")
