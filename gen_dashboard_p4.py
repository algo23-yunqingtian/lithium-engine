#!/usr/bin/env python3
"""Part 4: Tab 0 Overview chart functions"""
js = r'''
// ── Tab 0: Overview ──
async function loadOverview(){
  const[qs,pipe,sl]=await Promise.all([
    fetchJSON(`${API}/quotes`),fetchJSON(`${API}/pipeline`),fetchJSON(`${API}/salt_lake`)
  ]);

  // Price chart
  const c1=initChart('c_price');showLoading(c1);
  if(qs&&qs.data){
    const d=qs.data.slice(-90).reverse();
    c1.setOption({
      tooltip:{trigger:'axis',backgroundColor:'#16213e',borderColor:'#333',textStyle:{color:'#fff'}},
      grid:{left:50,right:20,top:30,bottom:30},
      xAxis:{type:'category',data:d.map(r=>r.date),axisLine:{lineStyle:{color:'#333'}},axisLabel:{color:'#8899aa',interval:Math.floor(d.length/10)}},
      yAxis:{type:'value',name:'元/吨',nameTextStyle:{color:'#8899aa'},axisLine:{lineStyle:{color:'#333'}},splitLine:{lineStyle:{color:'#222'}},axisLabel:{color:'#8899aa'}},
      series:[{name:'收盘价',type:'line',data:d.map(r=>r.close),smooth:true,lineStyle:{color:'#00b4d8',width:2},areaStyle:{color:new echarts.graphic.LinearGradient(0,0,0,1,[{offset:0,color:'rgba(0,180,216,0.3)'},{offset:1,color:'rgba(0,180,216,0)'}])},itemStyle:{color:'#00b4d8'}}]
    });
  }
  hideLoading(c1);

  // Stages chart
  const c2=initChart('c_stages');showLoading(c2);
  if(pipe&&pipe.data){
    const stages={};
    pipe.data.forEach(p=>{stages[p.development_stage]=(stages[p.development_stage]||0)+1});
    c2.setOption({
      tooltip:{trigger:'axis',backgroundColor:'#16213e',borderColor:'#333',textStyle:{color:'#fff'}},
      grid:{left:100,right:40,top:10,bottom:10},
      xAxis:{type:'value',axisLine:{lineStyle:{color:'#333'}},splitLine:{lineStyle:{color:'#222'}},axisLabel:{color:'#8899aa'}},
      yAxis:{type:'category',data:Object.keys(stages),axisLine:{lineStyle:{color:'#333'}},axisLabel:{color:'#8899aa'}},
      series:[{name:'项目数',type:'bar',data:Object.values(stages),barWidth:20,itemStyle:{color:(p)=>{
        const v=['#533483','#0f3460','#00b4d8','#e94560','#f0a500','#28a745'];return v[p.dataIndex%v.length]
      }}}]
    });
  }
  hideLoading(c2);

  // Countries chart
  const c3=initChart('c_countries');showLoading(c3);
  if(pipe&&pipe.data){
    const countrys={};
    pipe.data.forEach(p=>{countrys[p.country]=(countrys[p.country]||0)+1});
    const sorted=Object.entries(countrys).sort((a,b)=>b[1]-a[1]);
    c3.setOption({
      tooltip:{trigger:'item',backgroundColor:'#16213e',borderColor:'#333',textStyle:{color:'#fff'}},
      series:[{type:'pie',radius:['40%','70%'],center:['50%','55%'],
        data:sorted.map(([n,v])=>({name:n,value:v})),
        label:{color:'#ccc',formatter:'{b}: {c}'}
      }]
    });
  }
  hideLoading(c3);

  // Salt top
  const c4=initChart('c_salt_top');showLoading(c4);
  if(sl&&sl.data){
    const caps={};
    sl.data.forEach(s=>{const k=s.project_name||`${s.company_name}-${s.project_name}`;if(s.lce_annualized_kt)caps[k]=Math.max(caps[k]||0,s.lce_annualized_kt)});
    const top=Object.entries(caps).sort((a,b)=>b[1]-a[1]).slice(0,10);
    c4.setOption({
      tooltip:{trigger:'axis',backgroundColor:'#16213e',borderColor:'#333',textStyle:{color:'#fff'}},
      grid:{left:180,right:60,top:10,bottom:10},
      xAxis:{type:'value',name:'kt/yr',nameTextStyle:{color:'#8899aa'},axisLine:{lineStyle:{color:'#333'}},splitLine:{lineStyle:{color:'#222'}},axisLabel:{color:'#8899aa'}},
      yAxis:{type:'category',data:top.map(x=>x[0].substring(0,25)),inverse:true,axisLine:{lineStyle:{color:'#333'}},axisLabel:{color:'#8899aa',fontSize:11}},
      series:[{name:'产能',type:'bar',data:top.map(x=>x[1]),barWidth:16,itemStyle:{color:new echarts.graphic.LinearGradient(0,0,1,0,[{offset:0,color:'#e94560'},{offset:1,color:'#00b4d8'}])}}]
    });
  }
  hideLoading(c4);
}
'''
with open('/home/ubuntu/lithium_calendar/static/lithium_global_dashboard.html', 'a') as f:
    f.write(js)
print(f"P4: {len(js)} chars appended")
