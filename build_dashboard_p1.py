#!/usr/bin/env python3
"""Generate lithium global dashboard HTML - Part 1: HTML head, CSS, tabs structure"""
import json

html = '''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>🔋 全球锂矿 Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/echarts@5.5.0/dist/echarts.min.js"></script>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#1a1a2e;color:#e0e0e0;font-family:-apple-system,'Segoe UI',sans-serif;overflow-x:hidden}
.header{background:linear-gradient(135deg,#16213e,#0f3460);padding:16px 24px;display:flex;align-items:center;justify-content:space-between;border-bottom:2px solid #e94560}
.header h1{font-size:22px;color:#fff}
.header .subtitle{color:#8899aa;font-size:13px}
.refresh-btn{background:#e94560;color:#fff;border:none;padding:8px 16px;border-radius:4px;cursor:pointer;font-size:13px}
.refresh-btn:hover{background:#ff6b6b}
.tabs{display:flex;background:#16213e;border-bottom:1px solid #333}
.tab{padding:12px 20px;cursor:pointer;color:#8899aa;border-bottom:2px solid transparent;transition:all .2s;font-size:14px}
.tab:hover{color:#fff;background:#1a1a3e}
.tab.active{color:#e94560;border-bottom-color:#e94560;background:#1a1a3e}
.tab-content{display:none;padding:16px}
.tab-content.active{display:block}
.grid{display:grid;gap:12px;margin-bottom:12px}
.grid-2{grid-template-columns:1fr 1fr}
.grid-3{grid-template-columns:1fr 1fr 1fr}
.card{background:#16213e;border-radius:8px;padding:16px;border:1px solid #1f2b47}
.card-title{font-size:14px;color:#e94560;margin-bottom:12px;font-weight:600}
.chart{width:100%;height:300px}
.chart-lg{height:400px}
.metrics{display:grid;grid-template-columns:repeat(5,1fr);gap:12px;margin-bottom:12px}
.metric{background:#16213e;border-radius:8px;padding:16px;text-align:center;border:1px solid #1f2b47}
.metric .val{font-size:28px;font-weight:700;color:#00b4d8}
.metric .label{font-size:12px;color:#8899aa;margin-top:4px}
table{width:100%;border-collapse:collapse;font-size:12px}
th{background:#0f3460;color:#fff;padding:8px;text-align:left;cursor:pointer;position:sticky;top:0}
td{padding:6px 8px;border-bottom:1px solid #1f2b47}
tr:hover{background:#1a1a3e}
.severity-high{color:#e94560;font-weight:700}
.severity-medium{color:#f0a500}
.severity-low{color:#00b4d8}
.insight-card{background:#1a1a3e;border-radius:6px;padding:12px;margin-bottom:8px;border-left:3px solid #e94560}
.insight-card .finding{font-size:13px;color:#ddd;margin-bottom:6px}
.insight-card .rec{font-size:12px;color:#8899aa}
.table-wrap{max-height:400px;overflow-y:auto}
.badge{display:inline-block;padding:2px 8px;border-radius:10px;font-size:11px}
.badge-explore{background:#533483;color:#fff}
.badge-plan{background:#0f3460;color:#fff}
.badge-feas{background:#00b4d8;color:#fff}
.badge-const{background:#e94560;color:#fff}
.badge-oper{background:#28a745;color:#fff}
.loading{text-align:center;padding:40px;color:#8899aa}
</style>
</head>
<body>
<div class="header">
<div>
<h1>🔋 全球锂矿 Dashboard</h1>
<div class="subtitle">硬岩管道 · 非洲锂矿 · 盐湖跟踪 · 分析洞见 · 市场行情</div>
</div>
<button class="refresh-btn" onclick="refreshAll()">⟳ 刷新全部</button>
</div>

<div class="tabs">
<div class="tab active" onclick="switchTab(0)">📊 总览</div>
<div class="tab" onclick="switchTab(1)">⛏️ 硬岩管道</div>
<div class="tab" onclick="switchTab(2)">🌍 非洲锂矿</div>
<div class="tab" onclick="switchTab(3)">🧂 盐湖跟踪</div>
<div class="tab" onclick="switchTab(4)">🔍 分析洞见</div>
<div class="tab" onclick="switchTab(5)">📈 市场行情</div>
</div>

<div id="metrics" class="metrics"></div>

'''

with open('/home/ubuntu/lithium_calendar/static/lithium_global_dashboard.html', 'w') as f:
    f.write(html)

print(f"Part 1 written: {len(html)} chars")
