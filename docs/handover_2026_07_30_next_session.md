# 交接文档 - 碳酸锂多主体博弈项目 2026-07-30

## 之前完成了什么

### 项目背景
碳酸锂多主体博弈项目（`/home/ubuntu/lithium_calendar/`），已在 `http://124.221.113.37:8766/` 部署运行。

### Phase 1-3（已完成）
- 7个agent：smelter、merchant、institution、arbitrage、retail、resource、policy
- 每日数据自动抓取、agent评分、数据库持久化
- 交互式博弈网页：`/battlefield_agents.html`

### Phase 4（已放弃）
LLM批量复盘，评估为低ROI，决定跳过。

### 2026-07-30 新增

#### 1. 博弈因子体系 v1.0
- `game_factors.py` 核心模块，3层博弈机制
  - Layer 1：信息传递修正（基于评分方向分歧，反向修正）
  - Layer 2：行为影响修正（基于仓位变动同向跟随）
  - Layer 3：极化博弈检测（极端分歧触发反转信号）
- 已集成到 `agent_daily_update.py`，每日更新自动计算
- DB 表 `agent_game_factors` 已就绪

#### 2. 前端博弈可视化（新增 v2.0）
- 博弈因子热力图：5列×7行，展示 base_score/info_transfer/behavioral_impact/total_correction/corrected_score
- 极化检测警示条：三色（红=极化触发、黄=修正较大、蓝=正常）
- 修正幅度趋势图：30日内最大修正|信息|行为/观点分歧的时间序列

#### 3. API 新增
- `/api/battlefield/game-factors` — 返回博弈因子数据（最新+历史+汇总统计）

#### 4. 设计文档
- `lithium_game_factors_design.md`
- `archive_2026_07_30.md`

### 备份文件位置
```
/home/ubuntu/lithium_calendar/lithium_backup_20260730.db        # 第1次备份
/home/ubuntu/lithium_calendar/static/battlefield_agents_backup_20260730.html  # 第1次前端备份
/home/ubuntu/lithium_calendar/app.py.backup_20260730            # 第1次备份
/home/ubuntu/lithium_calendar/lithium_backup2_20260730.db       # 第2次备份(含game_factor数据)
/home/ubuntu/lithium_calendar/static/battlefield_agents_v2_20260730.html  # 第2次前端备份(含博弈可视化)
/home/ubuntu/lithium_calendar/lithium_api_v2_20260730.py        # 第2次API备份
/home/ubuntu/lithium_calendar/lithium_v2_20260730.db            # 第2次DB备份
```

## 当前状态
- 服务运行中：`/home/ubuntu/zinc_venv/bin/supervisorctl -c /home/ubuntu/lithium_calendar/supervisor.conf status`
- 数据库：`lithium.db`，包含表 `agent_game_factors`（7行=1天×7agent）
- 博弈因子已集成到 `agent_daily_update.py`，每日更新自动计算
- 前端新增博弈可视化模块

## 待做任务（优先级排序）

### 任务1：前端博弈可视化（已完成✅）
- [x] 博弈因子热力图
- [x] 极化检测警示条
- [x] 修正幅度趋势图
- [x] 30s自动刷新联动

### 任务2：参数调优（用户指定，下次做）
- 用历史数据观察因子修正分布
- 调整敏感度、动量等参数
- 验证因子效果

### 任务3：7维度逻辑甘特图（之前计划，后续）
- 将7个agent映射到7个交易逻辑维度
- ECharts heatmap渲染
- 时间线展示

### 任务4：数据积累（自动）
- 目前只有1天数据（2026-07-30），需积累至少10个交易日后修正趋势图才有意义
- 极化检测需等多空比>3x的情况

## 关键文件路径
- 主应用：`/home/ubuntu/lithium_calendar/app.py`
- API：`/home/ubuntu/lithium_calendar/lithium_api.py`
- Agent：`/home/ubuntu/lithium_calendar/lithium_agents.py`
- 博弈因子：`/home/ubuntu/.hermes/scripts/lithium_agents/game_factors.py`
- 每日更新：`/home/ubuntu/lithium_calendar/agent_daily_update.py`
- 前端：`/home/ubuntu/lithium_calendar/static/battlefield_agents.html`
- DB：`/home/ubuntu/lithium_calendar/lithium.db`

## 参考文档
- `/home/ubuntu/lithium_calendar/docs/lithium_game_factors_design.md`
- `/home/ubuntu/lithium_calendar/docs/archive_2026_07_30.md`

## 注意事项
- 前端已有防复制保护，新增页面需注意
- ECharts跨页面初始化需要dispose旧实例
- 服务重启用 `/home/ubuntu/zinc_venv/bin/supervisorctl -c /home/ubuntu/lithium_calendar/supervisor.conf restart lithium_calendar`
- 博弈因子只在有新数据时才写入，极化检测条件：多空Agent比>3x 且 平均绝对分>30
