# 碳酸锂多主体博弈 — 主体心理状态与心理价位带 K 线叠加设计

版本: v1.0 · 2026-08-01 · 短线风格（日线级滚动窗口，只用 T 日及之前数据，防透视）

## 1. 目标

在 battlefield_agents.html 现有 K 线图（kline-chart）上，为 7 个主体叠加「心理状态 + 心理价位带」：
- 每个主体的 buy_zone（采购/交易区间）作为半透明色带（状态色）
- 最新 anchor_price（心理锚点）作为水平虚线
- 状态标记（散点/文字）标注在 K 线上方
- 右上角图例可点击切换各主体显隐
- 状态汇总面板：7 个 agent 最新状态 + 心理价位 + 触发器文本

短线风格：所有指标用短窗口（5/10/20 日）、指数移动平均、动量触发。

## 2. 数据源（全部只用 T 日及之前）

| 表 | 关键列 | 用途 |
|----|--------|------|
| prices | date, open, close, high, low, volume | 价格动量/均线/ATR/极值 |
| agent_history | agent_id, date, position, avg_cost, pnl_unreal, close | 持仓/成本/盈亏 → 被套判定 |
| fundamental_indices | date, supply, demand, inventory, profit, sentiment, basis, composite | 基本面 z-score 偏差（已是 T 日滚动窗口标准化） |

## 3. 通用指标计算（脚本内每日期 T 只用到 T 及之前）

```
close_t      = prices.close at T
ma5/ma10/ma20 = 简单均值（含 T 日，窗口内不足则用可用值）
ema10        = EMA(close, 10)            # 种子=首个值
atr20        = mean(|close[i]-close[i-1]|, 最近20个 i)   # 波动尺度
mom5         = close_t / close_{t-5} - 1  # 5日动量（不足则 None→0）
mom20        = close_t / close_{t-20} - 1 # 20日动量
mom1         = close_t / close_{t-1} - 1  # 单日动量
hi20 / lo20  = 最近20日 high 最大值 / low 最小值
dev_ma20     = close_t / ma20 - 1         # 对20日均线偏离
```

基本面偏差（每主体只取敏感维度，缺 z 值按 0 处理，全部来自 fundamental_indices 当日值）：
```
SENSITIVITY = {
  'smelter':    {'profit': 0.6, 'inventory': 0.4},
  'merchant':   {'demand': 0.6, 'sentiment': 0.4},
  'institution':{'composite': 0.7, 'sentiment': 0.3},
  'arbitrage':  {'basis': 1.0},
  'retail':     {'sentiment': 0.8, 'composite': 0.2},
  'resource':   {'profit': 0.5, 'supply': 0.5},
  'policy':     {'composite': 1.0},
}
bias = Σ w_d * z_d   # z_d 为当日该维度 z-score
```

## 4. 心理锚点与价位带（所有主体通用公式，参数按角色不同）

```
anchor_price = ema10 + bias * ANCHOR_COEF[agent] * atr20

w = ZONE_BASE[agent] * atr20 * (1 + ZONE_MOM_COEF * mom5)   # 动量上行扩张、下行收缩
w = max(w, 0.4 * atr20)                                     # 下限保护
buy_zone_low  = anchor_price - w
buy_zone_high = anchor_price + w
```

参数表：

| agent | ANCHOR_COEF | ZONE_BASE | 说明 |
|-------|------------|-----------|------|
| smelter | 1.2 | 1.5 | 成本锚定，区间宽（产能/库存决策慢） |
| merchant | 1.0 | 1.2 | 采购区间，随动 |
| institution | 0.8 | 1.0 | 趋势交易，区间窄 |
| arbitrage | 0.6 | 0.8 | 升贴水驱动，最窄 |
| retail | 0.6 | 1.3 | 情绪放大，区间宽但锚点漂移快 |
| resource | 1.0 | 1.5 | 出货/囤货切换 |
| policy | 0.4 | 0.6 | 稳定，区间最窄 |

ZONE_MOM_COEF = 2.0（mom5=+5% → 宽度 +10%；mom5=-5% → 宽度 -10%）

## 5. 主体状态机（每主体 2-4 态，判断顺序即优先级）

### smelter 冶炼厂 — 成本锚定（3 态）
- 惜售(blue #58a6ff): close > cost*1.05 且 inventory_z < 0.5 → 价高于成本，囤货待涨
- 降价抛售(red #f85149): close < cost*0.97 或 (inventory_z > 1.0 且 dev_ma20 < 0) → 低于成本/高库存被迫出货
- 正常销售(green #3fb950): 其余
- cost = avg_cost(>0 时) 否则 ma20 代理

### merchant 贸易商/正极厂 — 区间采购（4 态）
- 恐慌撤退(orange #f0883e): mom5 < -0.06 → 近5日急跌，推迟采购
- 被套冻结(red #f85149): position > 0 且 close < ma20*0.98 → 持仓成本高于现价，暂停采购
- 积极采购(green #3fb950): close <= buy_zone_high 且 mom5 > -0.02 → 价格回到心理区间且跌势企稳，集中补库
- 观望(yellow #d29922): 其余 → 减缓采购、心理价位下调

### institution 机构 — 动量/趋势（3 态）
- 强趋势做多(green #3fb950): mom20 > 0.06 → 趋势跟单
- 强趋势做空(red #f85149): mom20 < -0.06 → 趋势跟空
- 震荡观望(yellow #d29922): 其余 → 震荡减仓

### arbitrage 套利商 — 升贴水（3 态）
- 正套做多(green #3fb950): basis_z > 0.5 → contango 买近抛远（净多近月）
- 反套做空(red #f85149): basis_z < -0.5 → backwardation 买远抛近（净空近月）
- 观望(yellow #d29922): 其余

### retail 散户 — 追涨杀跌+情绪放大（3 态）
- 追涨(green #3fb950): mom5 > 0.04 且 sentiment_z > -0.5
- 恐慌追跌(red #f85149): mom5 < -0.04 且 sentiment_z < 0.5
- 观望(yellow #d29922): 其余

### resource 矿商 — 高位出货/低位囤货（3 态）
- 加速出货(red #f85149): dev_ma20 > 0.05 或 close > hi20*0.98 → 高位加速出货
- 囤货惜售(blue #58a6ff): mom20 < -0.05 且 dev_ma20 < -0.03 → 低位囤货
- 正常销售(green #3fb950): 其余

### policy 政策 — 稳定，事件驱动（3 态）
- 利好释放(green #3fb950): mom1 > 0.04 且 composite_z > 0.5 → 政策利好事件
- 利空压制(red #f85149): mom1 < -0.04 且 composite_z < -0.5 → 政策利空事件
- 稳定观望(yellow #d29922): 其余（常态）

## 6. score（行为强度 0-100，控制散点大小/状态面板强调）

```
score = 50
score += clip(mom5 * 300, -20, 20)        # 动量贡献
score += 状态加成: 恐慌撤退/强趋势做空/加速出货 +15; 被套冻结 +10;
                  积极采购/强趋势做多/正套做多 +10; 囤货惜售/惜售 +8;
                  观望/稳定观望/正常销售 -5
score = clip(score, 10, 95)
```

## 7. trigger_desc（简短中文，格式 "事实→状态→动作"）

事实片段（按触发条件生成，只取命中的）：
- 动量: "近5日跌{:.0%}" / "近5日涨{:.0%}" / "近20日跌{:.0%}" / "近20日涨{:.0%}" / "单日{:.0%}"
- 成本: "价低于成本{:.0%}" / "价高于成本{:.0%}"
- 基差: "升水{basis_z:.1f}σ" / "贴水{basis_z:.1f}σ"
- 库存: "库存{basis_z:.1f}σ"（smelter 用 inventory_z）
- 偏离: "20日偏离{dev_ma20:.0%}"

动作片段（按状态与动量）：
- mom5 < 0: "下调锚点/区间收缩"；mom5 > 0: "上调锚点/区间扩张"
- merchant: 恐慌撤退→"暂停采购"；积极采购→"集中补库"；被套冻结→"解套前不动"；观望→"减缓采购"
- institution: 做多→"跟随做多"；做空→"跟随做空"；观望→"震荡减仓"
- arbitrage: 正套→"买近抛远"；反套→"买远抛近"
- resource: 加速出货→"高位出货"；囤货→"低位囤货"
- smelter: 惜售→"囤货待涨"；降价抛售→"被迫出货"；正常→"按需销售"

示例: "近5日跌12%→观望→下调锚点/减缓采购"；"升水1.2σ→正套做多→买近抛远/区间扩张"

## 8. 数据库表 agent_psychology

```sql
CREATE TABLE IF NOT EXISTS agent_psychology (
    date           TEXT    NOT NULL,
    agent_id       TEXT    NOT NULL,
    state          TEXT    NOT NULL,
    anchor_price   REAL,
    buy_zone_low   REAL,
    buy_zone_high  REAL,
    trigger_desc   TEXT,
    score          REAL,
    state_color    TEXT,
    PRIMARY KEY (date, agent_id)
);
```

## 9. API: GET /api/agent-psychology?days=N（默认30）

返回（lithium_api.py 内新增路由，模式参照 /api/logic-scores）：

```json
{
  "dates": ["2026-07-01", "..."],
  "agents": {
    "smelter": {
      "name": "冶炼厂",
      "color": "#58a6ff",
      "data": [
        {"date": "2026-07-31", "state": "惜售", "state_color": "#58a6ff",
         "anchor": 143000, "zone_low": 140000, "zone_high": 146000,
         "score": 72, "trigger": "价高于成本5%→惜售→囤货待涨"}
      ]
    }
  },
  "state_colors": {"积极采购": "#3fb950", "观望": "#d29922", "恐慌撤退": "#f0883e",
                   "被套冻结": "#f85149", "惜售": "#58a6ff", "降价抛售": "#f85149",
                   "正常销售": "#3fb950", "强趋势做多": "#3fb950", "强趋势做空": "#f85149",
                   "震荡观望": "#d29922", "正套做多": "#3fb950", "反套做空": "#f85149",
                   "追涨": "#3fb950", "恐慌追跌": "#f85149", "加速出货": "#f85149",
                   "囤货惜售": "#58a6ff", "利好释放": "#3fb950", "利空压制": "#f85149",
                   "稳定观望": "#d29922"},
  "latest": {"smelter": {"date": "2026-07-31", "state": "惜售", "state_color": "#58a6ff",
             "anchor": 143000, "zone_low": 140000, "zone_high": 146000,
             "score": 72, "trigger": "..."}, "...7个主体..."},
  "latest_date": "2026-07-31"
}
```

color 定义：agents 主题色 — smelter #58a6ff, merchant #3fb950, institution #f0883e, arbitrage #d29922, retail #f85149, resource #bc8cff, policy #79c0ff（暗色系，供色带/虚线/图例区分主体）

## 10. 计算脚本

路径: /home/ubuntu/.hermes/scripts/lithium_agents/agent_psychology_calc.py
- 读 /home/ubuntu/lithium_calendar/lithium.db（prices + agent_history + fundamental_indices）
- 日期范围: agent_history 的最早日期 → 最新日期（prices 对齐）
- 幂等: INSERT OR REPLACE INTO agent_psychology ...（按 (date, agent_id) 主键）
- 支持参数 --days N（默认全量）与 --date YYYY-MM-DD（单日增量，供 cron 使用）
- 输出: 写入行数 + 最新一行示例

## 11. 前端规格（battlefield_agents.html，kline-chart 叠加）

ECharts 约束（用户明确要求 + 项目已知陷阱）:
- **不要 dispose 已有实例**：kline 渲染函数用 `const inst = echarts.getInstanceByDom(dom) || echarts.init(dom)` 持久复用
- **setOption(option, true)** 第二参数传 true（notMerge），每次构建完整 option（原 K 线 series + 叠加 series）
- 颜色暗色系匹配现有页面；背景透明

叠加内容（同一 kline-chart，新增 series）：
1. 每个主体的 buy_zone 色带: `type:'custom'` 或两条 line 围成的 stacked area（推荐 custom renderItem 画矩形带，或 markArea 按点对）；半透明 fill（状态色 + alpha 0.15~0.2）。建议用 `type:'custom'` series，renderItem 返回 rect（x 由 category index 映射，y0/y1 由 zone_low/zone_high 映射到像素）— 与 K 线共 x 轴（category 日期）与 y 轴（value）。
2. anchor_price 虚线: 每主体一条 `type:'line'` data=anchor 序列, `lineStyle:{type:'dashed', width:1, opacity:0.8, color:agentColor}`, `symbol:'none'`, `z:3`
3. 状态散点: 每主体 `type:'scatter'` data=[date, close_t]（画在 K 线上方），`symbolSize: score/100*14+6`，`itemStyle.color = state_color`，label 显示 state 中文（短，如"惜售"），`z:5`
4. 图例显隐: 右上角图例。用 ECharts legend（data 为主体名, selected 默认全开）或自定义 HTML checkbox 面板（推荐自定义面板，放 kline-chart 容器右上角 absolute 定位），点击切换对应 series group 的显隐（通过 `chart.setOption({series:[...], legend:...})` 或直接 `chart.dispatchAction({type:'legendToggleSelect'})`）。
5. 状态汇总面板: 在 kline 图下方或右侧新增一个 HTML 面板（7 行），每行: 主体名 + 状态色块 + 状态 + anchor + 区间 + trigger 文本。数据来自 /api/agent-psychology 的 latest。

实现步骤（前端子任务）:
1. loadPsychologyData(): fetch('/api/agent-psychology?days=30') → 存全局变量
2. renderKline() 改造: 在现有 K 线 option 基础上，追加上述叠加 series（注意 category axis 与 K 线共用）
3. 新增状态汇总面板 HTML + render 函数
4. loadAll() 末尾调用 loadPsychologyData
5. 图例 checkbox 事件 → 控制各主体 series 显隐（用 legendToggleSelect 或维护 hiddenSet 重 setOption）
6. 浏览器验证: 打开页面, console 无 JS 错误, 截图确认色带/虚线/散点/面板渲染

## 12. 部署与验证

```bash
cd /home/ubuntu/.hermes/scripts/lithium_agents && /home/ubuntu/zinc_venv/bin/python agent_daily_update.py   # D1 确保 agent_history 最新
/home/ubuntu/zinc_venv/bin/python /home/ubuntu/.hermes/scripts/lithium_agents/agent_psychology_calc.py      # D2 计算写库
/home/ubuntu/zinc_venv/bin/supervisorctl -c /home/ubuntu/lithium_calendar/supervisor.conf restart lithium_calendar  # D3
curl -s 'http://127.0.0.1:8766/api/agent-psychology?days=30' | python -m json.tool | head -60                  # D4
```
