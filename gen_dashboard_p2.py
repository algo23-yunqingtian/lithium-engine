#!/usr/bin/env python3
"""Part 2: Tab content HTML for all 6 tabs"""
html = r'''
<!-- Tab 0: Overview -->
<div class="tc active" id="tab0">
<div class="g2">
<div class="card"><div class="ct">📈 碳酸锂价格走势 (近90天)</div><div id="c_price" class="ch"></div></div>
<div class="card"><div class="ct">🏗️ 项目阶段分布</div><div id="c_stages" class="ch"></div></div>
</div>
<div class="g2">
<div class="card"><div class="ct">🌏 项目国家分布</div><div id="c_countries" class="ch"></div></div>
<div class="card"><div class="ct">📊 盐湖产量排名 (Top 10)</div><div id="c_salt_top" class="ch"></div></div>
</div>
</div>

<!-- Tab 1: Hard Rock Pipeline -->
<div class="tc" id="tab1">
<div class="g2">
<div class="card"><div class="ct">🔄 开发阶段漏斗</div><div id="c_funnel" class="ch"></div></div>
<div class="card"><div class="ct">🌏 各国项目数量</div><div id="c_hr_countries" class="ch"></div></div>
</div>
<div class="card"><div class="ct">📋 硬岩管道项目明细</div><div class="tw" id="t_pipeline"></div></div>
</div>

<!-- Tab 2: African Lithium -->
<div class="tc" id="tab2">
<div class="g2">
<div class="card"><div class="ct">🌍 非洲各国项目数</div><div id="c_africa" class="ch"></div></div>
<div class="card"><div class="ct">🎯 开发阶段分布</div><div id="c_africa_stage" class="ch"></div></div>
</div>
<div class="card"><div class="ct">📋 非洲锂矿项目明细</div><div class="tw" id="t_africa"></div></div>
</div>

<!-- Tab 3: Salt Lake -->
<div class="tc" id="tab3">
<div class="g2">
<div class="card"><div class="ct">📊 盐湖产能排名</div><div id="c_salt_cap" class="ch-lg"></div></div>
<div class="card"><div class="ct">💰 C1 成本曲线</div><div id="c_cost" class="ch-lg"></div></div>
</div>
<div class="g2">
<div class="card"><div class="ct">🏦 CAPEX 投入 (Top 10)</div><div id="c_capex" class="ch"></div></div>
<div class="card"><div class="ct">📋 盐湖明细数据</div><div class="tw" id="t_salt"></div></div>
</div>
</div>

<!-- Tab 4: Analysis -->
<div class="tc" id="tab4">
<div class="g2">
<div class="card"><div class="ct">⚠️ 信号严重程度</div><div id="c_severity" class="ch"></div></div>
<div class="card"><div class="ct">📋 分析类型分布</div><div id="c_atypes" class="ch"></div></div>
</div>
<div class="card"><div class="ct">💡 分析洞见</div><div class="tw" id="t_analysis"></div></div>
</div>

<!-- Tab 5: Market -->
<div class="tc" id="tab5">
<div class="g2">
<div class="card"><div class="ct">📈 期货 K 线 (近180天)</div><div id="c_kline" class="ch-lg"></div></div>
<div class="card"><div class="ct">📊 持仓量走势</div><div id="c_hold" class="ch-lg"></div></div>
</div>
<div class="card"><div class="ct">📋 近期行情数据</div><div class="tw" id="t_market"></div></div>
</div>
'''
with open('/home/ubuntu/lithium_calendar/static/lithium_global_dashboard.html', 'a') as f:
    f.write(html)
print(f"P2: {len(html)} chars appended")
