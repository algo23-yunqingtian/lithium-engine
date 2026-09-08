#!/usr/bin/env python3
"""Lithium Global Dashboard Generator"""
import sqlite3, json, os

# Gather data
DB1='/home/ubuntu/analysis/lithium_global/lithium_global.db'
DB2='/home/ubuntu/lithium_calendar/lithium.db'

def q(db, sql):
    c=sqlite3.connect(db); c.row_factory=sqlite3.Row; r=c.execute(sql).fetchall(); c.close()
    return [dict(x) for x in r]

# Get data
prices = q(DB2, "SELECT * FROM prices ORDER BY date DESC LIMIT 180")
prices.reverse()
pipeline = q(DB1, "SELECT * FROM companies_pipeline ORDER BY development_stage, country")
africa = q(DB1, "SELECT * FROM african_lithium ORDER BY country, development_stage")
salt = q(DB1, "SELECT * FROM salt_lake_detail ORDER BY project_name, quarter")
analysis = q(DB1, "SELECT * FROM analysis_results ORDER BY CASE severity WHEN 'high' THEN 1 WHEN 'medium' THEN 2 WHEN 'low' THEN 3 END")

# Compute aggregates
stage_counts={}
for p in pipeline: stage_counts[p.get('development_stage','unknown')] = stage_counts.get(p.get('development_stage','unknown'),0)+1

country_counts={}
for p in pipeline: country_counts[p.get('country','unknown')] = country_counts.get(p.get('country','unknown'),0)+1

salt_caps={}
for s in salt:
    k=s.get('project_name','') or f"{s.get('company_name','')}-{s.get('project_name','')}"
    v=s.get('lce_annualized_kt')
    if v: salt_caps[k]=max(salt_caps.get(k,0),v)
salt_top=sorted(salt_caps.items(),key=lambda x:-x[1])[:10]

africa_country={}
for a in africa: africa_country[a.get('country','unknown')]=africa_country.get(a.get('country','unknown'),0)+1

africa_stage={}
for a in africa: africa_stage[a.get('development_stage','unknown')]=africa_stage.get(a.get('development_stage','unknown'),0)+1

sev_counts={}
for a in analysis: sev_counts[a.get('severity','unknown')]=sev_counts.get(a.get('severity','unknown'),0)+1

atype_counts={}
for a in analysis: atype_counts[a.get('analysis_type','unknown')]=atype_counts.get(a.get('analysis_type','unknown'),0)+1

capex={}
for s in salt:
    k=s.get('project_name','')
    v=s.get('quarterly_capex_usd_m')
    if v: capex[k]=(capex.get(k,0)+v)
capex_top=sorted(capex.items(),key=lambda x:-x[1])[:10]

cost_curve={}
for s in salt:
    k=s.get('project_name','')
    v=s.get('c1_cost_usd_t')
    if v and k:
        if k not in cost_curve: cost_curve[k]=[]
        cost_curve[k].append(v)
cost_avg=[(k,sum(v)/len(v)) for k,v in cost_curve.items()]
cost_avg.sort(key=lambda x:x[1])

high_signals=[a for a in analysis if a.get('severity')=='high']

# Encode as JSON for embedding
data_js=json.dumps({
    'prices':prices,'pipeline':pipeline,'africa':africa,'salt':salt,'analysis':analysis,
    'stage_counts':stage_counts,'country_counts':country_counts,'salt_top':salt_top,
    'africa_country':africa_country,'africa_stage':africa_stage,
    'sev_counts':sev_counts,'atype_counts':atype_counts,
    'capex_top':capex_top,'cost_avg':cost_avg,'high_count':len(high_signals)
},ensure_ascii=False,default=str)

# Write to temp file
with open('/home/ubuntu/lithium_calendar/_dashboard_data.json','w') as f:
    json.dump({
        'prices':prices,'pipeline':pipeline,'africa':africa,'salt':salt,'analysis':analysis,
        'stage_counts':stage_counts,'country_counts':country_counts,'salt_top':salt_top,
        'africa_country':africa_country,'africa_stage':africa_stage,
        'sev_counts':sev_counts,'atype_counts':atype_counts,
        'capex_top':capex_top,'cost_avg':cost_avg,'high_count':len(high_signals)
    },f,ensure_ascii=False,default=str)

print(f"Data generated: {len(data_js)} chars")
print(f"  prices: {len(prices)} rows")
print(f"  pipeline: {len(pipeline)} rows")
print(f"  africa: {len(africa)} rows")
print(f"  salt: {len(salt)} rows")
print(f"  analysis: {len(analysis)} rows")
