# 交接文档 — 碳酸锂 Agent 博弈系统 · 信号驱动互动层（Phase 5.5）

> 日期：2026-08-01 · 交接给下一会话
> 本次完成：**升贴水/基本面信号 → Agent 阵营互动** 全链路（建模 + 存储 + API + 前端 + 回填）

## 一、本次做了什么

1. **新建 `signal_interactions.py`**（~/.hermes/scripts/lithium_agents/）
   - 从 lithium.db 提取 5 类信号：升贴水（日度）、库存/产量（周度）、月度产量、价格动量（5日）
   - 归一化为 (direction, strength)；按 agent 主导信号划分多空阵营
   - 生成 2 种新互动：`signal_resonance`（同阵营共振，蓝）、`signal_conflict`（跨阵营背离，橙）
   - 写入 `agent_interactions` 表 + 新增 `market_signals` 表（信号快照）

2. **接入每日更新**：`agent_daily_update.py` Step 3.6（在博弈因子之后、异常事件之前）
   - cron：18:30 周一至五 `lithium_agents/agent_daily_update.sh`

3. **后端 API**（lithium_api.py，已重启生效）
   - 新增 `GET /api/market-signals?days=N`
   - `GET /api/agent-interactions` 增加 `signal_types` 字段

4. **前端**（static/battlefield_agents.html）
   - 新增「信号驱动互动」区块：5 张信号卡片（升贴水/库存/产量/价格动量/综合）+ 信号方向时序图 + 共振/背离强度堆叠图
   - 互动热力图/网络图支持 4 种互动类型颜色与中文名

5. **历史回填**：2026-07 全月 23 个交易日，225 条信号互动

## 二、页面验证（已实测）

- http://124.221.113.37:8766/battlefield_agents.html
- 信号卡片 5 张正常：升贴水利空(-500)、库存利多(10.79万)、产量利多(2.28万)、价格动量利空(13.78万)、综合中性
- 信号趋势图 4 线 × 22 天、互动强度图 2 系列正常；console 零错误
- 最新阵营（7/31）：多方=机构/政策/矿商，空方=套利/贸易/散户/冶炼

## 三、核心文件清单

| 文件 | 说明 |
|---|---|
| `~/.hermes/scripts/lithium_agents/signal_interactions.py` | 信号驱动互动核心（约 510 行） |
| `~/.hermes/scripts/lithium_agents/agent_daily_update.py` | 每日更新（Step 3.6 接入） |
| `/home/ubuntu/lithium_calendar/lithium_api.py` | API（/market-signals） |
| `/home/ubuntu/lithium_calendar/static/battlefield_agents.html` | 前端（信号驱动区块） |
| `/home/ubuntu/lithium_calendar/docs/lithium_signal_driven_interactions.md` | **设计文档（建模细节全在此）** |
| `/home/ubuntu/lithium_calendar/lithium.db` | 主库（agent_interactions + market_signals） |

## 四、常用操作

```bash
# 独立跑一次（最新一天信号互动，写库）
/home/ubuntu/zinc_venv/bin/python ~/.hermes/scripts/lithium_agents/signal_interactions.py

# 回填历史（从某日期起）
/home/ubuntu/zinc_venv/bin/python ~/.hermes/scripts/lithium_agents/signal_interactions.py --backfill 2026-07-01

# 完整跑每日更新（含 Step 3.6；注意会真实更新 agents 表）
/home/ubuntu/zinc_venv/bin/python ~/.hermes/scripts/lithium_agents/agent_daily_update.py

# 服务重启（API 改后）
ps aux | grep "gunicorn.*8766" | grep -v grep | awk '{print $2}' | xargs kill
# supervisord 会自动拉起
```

## 五、遗留事项（Phase 6-8）

- **Phase 6 参数调优与回测**（P2）：需 ≥10 天数据积累（现在已有 7 月 23 天，可以做首轮回测）
  - 候选参数：MAGNITUDE_THRESHOLD、共振/冲突基准 10/14、知行背离放大 1.3
  - 验证：信号互动强度 vs 次日价格方向的相关性
- Phase 7 LLM 批量审核（P4，可跳过）
- Phase 8 主页联动入口（P3，约 0.5h）：把信号驱动互动加进 index 导航
- 潜在增强：把 signal 类型互动纳入博弈因子（game_factors.py 的 Layer 2 行为影响），实现"信号→互动→仓位"闭环

## 六、注意事项

- **两数据库**：写入目标是 `/home/ubuntu/lithium_calendar/lithium.db`（Flask 主库），勿误写 lithium_v2_20260730.db
- 信号方向存在真实矛盾（如 7 月：库存去库+减产=利多 vs 贴水+价格大跌=利空），这是**特性不是 bug**——阵营分歧正是由它驱动的
- 回填用 `agent_game_factors.base_score` 优先，无则回退 `agent_view_log.view_score`
- 前端 ECharts 隔离：所有 render 前 dispose + innerHTML=''
