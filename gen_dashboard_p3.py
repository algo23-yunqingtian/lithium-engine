#!/usr/bin/env python3
"""Part 3: JavaScript - Core functions, tab switching, API fetch, metrics"""
js = r'''
<script>
// Anti-copy
document.addEventListener('contextmenu',e=>e.preventDefault());
document.addEventListener('keydown',e=>{if((e.ctrlKey&&['c','s','p'].includes(e.key))||e.key==='F12')e.preventDefault()});
document.body.style.userSelect='none';

const API='/api/lithium';
const tabs=document.querySelectorAll('.tab');
const tabContents=document.querySelectorAll('.tc');

function switchTab(i){
  tabs.forEach((t,j)=>{t.classList.toggle('active',j===i);tabContents[j].classList.toggle('active',j===i)});
  if(i===0)loadOverview();if(i===1)loadPipeline();if(i===2)loadAfrica();
  if(i===3)loadSaltLake();if(i===4)loadAnalysis();if(i===5)loadMarket();
}
tabs.forEach((t,i)=>t.addEventListener('click',()=>switchTab(i)));

const charts={};
function initChart(id){
  const el=document.getElementById(id);
  if(!el)return null;
  const c=echarts.init(el,'dark');
  charts[id]=c;
  return c;
}

function showLoading(c){c.showLoading({text:'加载中',color:'#e94560',maskColor:'#16213e'})}
function hideLoading(c){c.hideLoading()}

async function fetchJSON(url){
  try{const r=await fetch(url);return await r.json()}catch(e){console.error(e);return null}
}

function stageBadge(s){
  const m={'exploration':['探索期','b-exp'],'planning':['规划','b-pln'],'feasibility':['可研','b-fea'],
    'construction':['建设','b-con'],'pre-production':['试产','b-pre'],'operating':['运营','b-opr']};
  const[x,c]=m[s]||[s,''];
  return`<span class="badge ${c}">${x}</span>`;
}

function sevClass(s){return s==='high'?'s-h':s==='medium'?'s-m':'s-l'}
function sevLabel(s){return s==='high'?'🔴高':s==='medium'?'🟡中':'🟢低'}

// Metrics cards
async function loadMetrics(){
  const[pipe,af,sl,an,qs]=await Promise.all([
    fetchJSON(`${API}/pipeline`),fetchJSON(`${API}/africa`),fetchJSON(`${API}/salt_lake`),
    fetchJSON(`${API}/analysis`),fetchJSON(`${API}/quotes`)
  ]);
  const saltProjects=new Set((sl&&sl.data||[]).map(d=>d.project_name)).size;
  const highAn=(an&&an.data||[]).filter(d=>d.severity==='high').length;
  const lastQ=qs&&qs.data&&qs.data[0];
  const lastPrice=lastQ?`¥${(lastQ.close/10000).toFixed(2)}w`:'--';
  document.getElementById('metrics').innerHTML=`
    <div class="met"><div class="v">${pipe?pipe.data.length:0}</div><div class="l">硬岩管道项目</div></div>
    <div class="met"><div class="v">${af?af.data.length:0}</div><div class="l">非洲锂矿项目</div></div>
    <div class="met"><div class="v">${saltProjects}</div><div class="l">盐湖项目</div></div>
    <div class="met"><div class="v" style="color:#e94560">${highAn}</div><div class="l">高风险信号</div></div>
    <div class="met"><div class="v">${lastPrice}</div><div class="l">最新期货价</div></div>
  `;
}
</script>
'''
with open('/home/ubuntu/lithium_calendar/static/lithium_global_dashboard.html', 'a') as f:
    f.write(js)
print(f"P3: {len(js)} chars appended")
