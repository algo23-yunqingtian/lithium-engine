#!/usr/bin/env python3
"""Generate lithium global dashboard - Part 1: Head + CSS + Tab structure"""
html = r'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>🔋 全球锂矿 Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"></script>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#1a1a2e;color:#e0e0e0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;font-size:13px}
.header{background:linear-gradient(90deg,#16213e,#0f3460);padding:14px 24px;display:flex;align-items:center;justify-content:space-between;border-bottom:2px solid #e94560}
.header h1{font-size:20px;color:#fff}
.header .sub{color:#8899aa;font-size:12px;margin-top:2px}
.btn{background:#e94560;color:#fff;border:none;padding:7px 14px;border-radius:4px;cursor:pointer;font-size:12px}
.btn:hover{background:#ff6b6b}
.tabs{display:flex;background:#16213e;border-bottom:1px solid #333}
.tab{padding:10px 18px;cursor:pointer;color:#8899aa;border-bottom:2px solid transparent;font-size:13px;transition:.2s}
.tab:hover{color:#fff;background:#1a1a3e}
.tab.active{color:#e94560;border-bottom-color:#e94560;background:#1a1a3e}
.tc{display:none;padding:16px}.tc.active{display:block}
.g2{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:12px}
.g3{display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;margin-bottom:12px}
.card{background:#16213e;border-radius:6px;padding:14px;border:1px solid #1f2b47}
.ct{font-size:13px;color:#e94560;margin-bottom:10px;font-weight:600}
.ch{width:100%;height:280px}
.ch-lg{height:380px}
.mets{display:grid;grid-template-columns:repeat(5,1fr);gap:10px;margin-bottom:12px}
.met{background:#16213e;border-radius:6px;padding:12px;text-align:center;border:1px solid #1f2b47}
.met .v{font-size:26px;font-weight:700;color:#00b4d8}
.met .l{font-size:11px;color:#8899aa;margin-top:3px}
table{width:100%;border-collapse:collapse;font-size:12px}
th{background:#0f3460;color:#fff;padding:7px 8px;text-align:left;cursor:pointer;position:sticky;top:0}
td{padding:5px 8px;border-bottom:1px solid #1f2b47}
tr:hover{background:#1a1a3e}
.s-h{color:#e94560;font-weight:700}.s-m{color:#f0a500}.s-l{color:#00b4d8}
.ic{background:#1a1a3e;border-radius:4px;padding:10px;margin-bottom:6px;border-left:3px solid #e94560}
.ic .f{font-size:12px;color:#ddd;margin-bottom:4px}
.ic .r{font-size:11px;color:#8899aa}
.tw{max-height:350px;overflow-y:auto}
.badge{display:inline-block;padding:1px 6px;border-radius:8px;font-size:10px;color:#fff}
.b-exp{background:#533483}.b-pln{background:#0f3460}.b-fea{background:#00b4d8}
.b-con{background:#e94560}.b-pre{background:#f0a500}.b-opr{background:#28a745}
.spinner{display:inline-block;width:12px;height:12px;border:2px solid #333;border-top-color:#e94560;border-radius:50%;animation:spin .6s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
</style>
</head>
<body>
<div class="header">
<div><h1>🔋 全球锂矿 Dashboard</h1><div class="sub">硬岩管道 · 非洲锂矿 · 盐湖跟踪 · 分析洞见 · 市场行情</div></div>
<button class="btn" onclick="refreshAll()">⟳ 刷新全部</button>
</div>
<div class="tabs">
<div class="tab active" data-i="0">📊 总览</div>
<div class="tab" data-i="1">⛏️ 硬岩管道</div>
<div class="tab" data-i="2">🌍 非洲锂矿</div>
<div class="tab" data-i="3">🧂 盐湖跟踪</div>
<div class="tab" data-i="4">🔍 分析洞见</div>
<div class="tab" data-i="5">📈 市场行情</div>
</div>
<div id="metrics" class="mets"></div>
'''
with open('/home/ubuntu/lithium_calendar/static/lithium_global_dashboard.html', 'w') as f:
    f.write(html)
print(f"P1: {len(html)} chars written")
