#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""前端自动刷新修改：30s → 5分钟（改善客户体验）"""
import re, sys, shutil, os

TARGETS = [
    ("/home/ubuntu/lithium_calendar/static/battlefield_agents.html", "setInterval(loadAll, 30000);", "setInterval(loadAll, 300000);"),
    ("/home/ubuntu/lithium_calendar/static/battlefield_agents_v2_20260730.html", "setInterval(loadAll, 30000);", "setInterval(loadAll, 300000);"),
]

for path, old, new in TARGETS:
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        if old in content:
            # 备份一次
            bak = path + ".bak_freq"
            if not os.path.exists(bak):
                shutil.copy(path, bak)
            content = content.replace(old, new)
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            print(f"✅ {path}: {old} → {new}")
        else:
            print(f"ℹ️ {path}: 未找到 {old}（可能已改过）")
            # 检查是否已存在新间隔
            m = re.search(r"setInterval\(loadAll,\s*(\d+)\)", content)
            if m:
                print(f"   当前间隔: {m.group(1)}ms")
    except Exception as e:
        print(f"❌ {path}: {e}")

print("\n完成。已从30秒改为5分钟（300000ms）。")
