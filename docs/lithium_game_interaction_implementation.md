# 碳酸锂博弈互动 — 实施清单

> 2026-07-30 · 基于 `lithium_game_interaction_design.md` 拆解的具体实施步骤

---

## 任务A：交易逻辑甘特图

### A1. 后端：新增 `/api/logic_scores`

**文件**：`/home/ubuntu/lithium_calendar/app.py`（或 `lithium_api.py`）

```python
@app.route('/api/logic_scores')
def api_logic_scores():
    """返回每日7个交易逻辑的评分时序"""
    days = request.args.get('days', 90, type=int)
    
    # 方案1：从 agent_view_log 的 key_indicators 解析
    # 方案2：从 agent_action_log 的 reasoning 解析
    # 推荐：直接查询 agent_view_log，解析 key_indicators JSON
    
    results = {}
    dates = []
    
    for agent_log in agent_logs:  # agent_view_log 按日期排序
        date = agent_log['date']
        if date not in dates:
            dates.append(date)
            logic_row = {}
            # 汇总当日7个agent的logic_scores
            for log in agent_logs:
                if log['date'] == date:
                    key_ind = json.loads(log.get('key_indicators', '{}'))
                    for logic, score in key_ind.items():
                        if logic in LOGIC_MAP:
                            # 多个agent对同一逻辑打分，取平均
                            if logic not in logic_row:
                                logic_row[logic] = []
                            logic_row[logic].append(score)
            
            # 平均化
            for logic in LOGIC_MAP:
                scores = logic_row.get(logic, [0])
                logic_row[logic] = round(sum(scores) / len(scores))
            
            results[date] = logic_row
    
    return jsonify({
        'dates': dates,
        'logic_map': LOGIC_MAP,
        'scores': results
    })
```

**需要新增**：`LOGIC_MAP` 定义（7个逻辑的中文名称+英文ID+颜色）

```python
LOGIC_MAP = {
    'supply_risk':    {'name': '供给风险', 'color': '#f85149'},
    'demand_drop':    {'name': '需求下滑', 'color': '#f0883e'},
    'inventory':      {'name': '库存变化', 'color': '#58a6ff'},
    'macro_policy':   {'name': '宏观政策', 'color': '#bc8cff'},
    'cost_support':   {'name': '成本支撑', 'color': '#3fb950'},
    'sentiment':      {'name': '情绪小作文', 'color': '#d29922'},
    'basis_trade':    {'name': '基差月差', 'color': '#39d2c0'}
}
```

### A2. 后端：`compute_agent_score()` 增加 logic_scores 输出

**文件**：`/home/ubuntu/.hermes/scripts/lithium_agents/agent_daily_update.py`

修改每个agent的评分函数，返回一个 `logic_scores` dict。

例如冶炼厂：
```python
def compute_agent_score(agent_id, market):
    ...
    # 现有逻辑打分
    score = 0
    if market.get('inventory_decreasing'): score += 30
    if market.get('price_above_cost'): score += 25
    ...
    
    # 【新增】映射到逻辑维度
    logic_scores = {}
    logic_scores['supply_risk'] = score  # 冶炼厂主要看供给风险
    logic_scores['cost_support'] = 25 if market.get('price_above_cost') else -15
    logic_scores['inventory'] = 30 if market.get('inventory_decreasing') else -10
    
    # 其他agent也类似映射
    if agent_id == 'merchant':
        logic_scores['sentiment'] = -25 if market.get('price_high') else 10
        logic_scores['basis_trade'] = compute_basis_score(market)
    elif agent_id == 'retail':
        logic_scores['sentiment'] = 30 if market.get('price_rising') else -20
    elif agent_id == 'policy':
        logic_scores['macro_policy'] = compute_policy_score(market)
    ...
    
    return score, reasons_text, delta_pos, logic_scores
```

### A3. 前端：`lithium_logic_gantt.html`

新建文件，结构：
```
- K线图（顶部，ECharts）
- 7逻辑强度时序图（中间，ECharts line）
- 逻辑热力图（ECharts heatmap）
- 主导逻辑轮动条带（ECharts）
- 交互：点击/悬停显示当日详细
```

---

## 任务B：博弈互动 Layer 1（MVP）

### B1. 修改 `compute_agent_score()` 加入博弈因子

**文件**：`/home/ubuntu/.hermes/scripts/lithium_agents/agent_daily_update.py`

在 `run_daily_update()` 中，先跑完一轮规则评分，再叠加博弈因子：

```python
def run_daily_update():
    ...
    # 第一轮：规则评分
    for aid, agent in agents:
        new_score, reasons, delta, logic_scores = compute_agent_score(aid, market)
        # 保存中间结果
        base_scores[aid] = new_score
        base_logic_scores[aid] = logic_scores
    
    # 第二轮：博弈因子叠加
    for aid, agent in agents:
        game_bonus = compute_game_factor(aid, base_scores, market)
        final_score = base_scores[aid] + game_bonus
        final_logic_scores = {k: v for k, v in base_logic_scores[aid].items()}
        # 博弈因子只影响个别逻辑
        if game_bonus > 10:
            final_logic_scores['sentiment'] = min(100, final_logic_scores.get('sentiment', 0) + game_bonus)
        elif game_bonus < -10:
            final_logic_scores['sentiment'] = max(-100, final_logic_scores.get('sentiment', 0) + game_bonus)
        
        # 更新数据库
        update_agent_in_db(aid, final_score, ...)
```

### B2. `compute_game_factor()` 函数

```python
GAME_FACTORS = {
    # (target_agent_id, source_agent_id): (weight, direction)
    # direction: 'same'=正相关, 'opp'=负相关
    ('merchant', 'retail'):     (-15, 'opp'),  # 散户追多→贸易商反向
    ('institution', 'retail'):  (-20, 'opp'),  # 散户过热→机构反向
    ('merchant', 'smelter'):    (10, 'same'),  # 大户增仓→贸易商跟风
    ('institution', 'smelter'): (10, 'same'),  # 大户增仓→机构跟多
    ('retail', 'policy'):       (10, 'same'),  # 散户跟风政策
    ('smelter', 'resource'):    (10, 'same'),  # 矿商成本→冶炼厂
    ('resource', 'policy'):     (5, 'same'),
}

def compute_game_factor(target_id, base_scores, market):
    bonus = 0
    for (tgt, src), (weight, direction) in GAME_FACTORS.items():
        if tgt == target_id:
            src_score = base_scores.get(src, 0)
            if direction == 'same':
                bonus += weight * (src_score / 100)
            else:  # opp
                bonus -= weight * (src_score / 100)
    
    return round(bonus)
```

### B3. 前端：在 `battlefield_agents.html` 加博弈因子展示

在 agent card 中显示博弈因子贡献：
```html
<div class="agent-card">
  ...
  <div class="game-factors">
    <span style="color:var(--text2);font-size:9px">博弈因子:</span>
    <span style="color:#3fb950">+5</span>
  </div>
</div>
```

---

## 任务C：博弈矩阵 Layer 2

### C1. 新增数据库表

```sql
CREATE TABLE IF NOT EXISTS agent_interactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    source_agent TEXT NOT NULL,
    target_agent TEXT NOT NULL,
    interaction_type TEXT NOT NULL DEFAULT 'position_change',
    source_action REAL NOT NULL,
    score_impact INTEGER NOT NULL,
    direction TEXT DEFAULT 'neutral',
    description TEXT
);
```

### C2. 后端：计算互动并写入

```python
def compute_interactions(agents_dict, today):
    """计算当日agent之间的互动"""
    interactions = []
    
    for src_id in agents_dict:
        src_pos = agents_dict[src_id]['position']
        prev_pos = agents_dict[src_id].get('prev_position', src_pos)
        pos_change = abs(src_pos - prev_pos)
        
        if pos_change <= 10:
            continue
        
        direction = 'bull' if src_pos > 0 else 'bear'
        
        for tgt_id in agents_dict:
            if src_id == tgt_id:
                continue
            
            # 查互动规则
            for (tgt, src), (weight, d) in GAME_FACTORS.items():
                if tgt == tgt_id and src == src_id:
                    impact = round(weight * pos_change / 50)  # 缩放
                    if abs(impact) >= 5:
                        interactions.append({
                            'date': today,
                            'source_agent': src_id,
                            'target_agent': tgt_id,
                            'source_action': pos_change,
                            'score_impact': impact,
                            'direction': direction,
                            'description': f"{AGENT_NAME[src_id]}{direction}持仓+{pos_change}→{AGENT_NAME[tgt_id]}修正{impact}"
                        })
    
    return interactions
```

### C3. 后端：新增 `/api/interactions` API

```python
@app.route('/api/interactions')
def api_interactions():
    days = request.args.get('days', 7, type=int)
    # 查询 agent_interactions 表
    # 返回 JSON 格式
```

### C4. 前端：博弈矩阵热力图

在 `battlefield_agents.html` 新增：
```html
<div class="section">
  <div class="section-title"><span class="dot" style="background:var(--purple)"></span> 博弈矩阵</div>
  <div class="chart-box">
    <div class="chart-container" id="game-matrix-chart"></div>
  </div>
</div>
```

ECharts heatmap 渲染：
- X轴：source_agent（7个）
- Y轴：target_agent（7个）
- 值：score_impact
- 颜色：红→绿

---

## 实施顺序

1. **A1+A2**：甘特图后端（1小时）
2. **A3**：甘特图前端（2小时）
3. **B1+B2+B3**：博弈因子MVP（2小时）
4. **C1+C2+C3+C4**：博弈矩阵（3小时）

**总计约8小时**

---

## 验证清单

- [ ] `/api/logic_scores` 返回正确的7逻辑评分
- [ ] `lithium_logic_gantt.html` 甘特图正常渲染
- [ ] `compute_game_factor()` 返回合理值（-20~20）
- [ ] agent card 显示博弈因子贡献
- [ ] `/api/interactions` 返回互动记录
- [ ] 博弈矩阵热力图正常渲染
- [ ] 页面无JS报错
- [ ] 服务重启后正常

**估算百分比：~35%**
