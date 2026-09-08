# 博弈因子体系设计 (Game Factor System v1.0)

## 一、设计目标

在现有规则引擎基础上，增加 **Agent 间博弈因子**，使每个 Agent 的评分不仅取决于市场数据，还受其他 Agent 行为影响，形成真实的"博弈"效果。

**核心原则**：
1. 因子计算纯数学化，不依赖 LLM（避免延迟和 API 调用）
2. 因子对 base_score 的修正控制在 ±10 以内（保持 base_score 的主导地位）
3. 因子按天运行，每轮 `agent_daily_update` 自动计算
4. 可观测：所有因子输出写 DB 和 stdout

---

## 二、三层次因子体系

### Layer 1: 信息传递因子 (Info Transfer) — 评分修正

**逻辑**：其他 Agent 的评分方向与我相反时，说明信息不对称，我会对我的评分做出修正。

**计算公式**：

```
info_correction(agent_i) = Σ [sign(score_j) == -sign(score_i)] × weight_j / N_opposing

final_score = base_score × (1 - info_correction × 0.15)
```

其中 `weight_j` 是 agent j 的资金权重（资本占比），`N_opposing` 是意见相反的 agent 数量。

**各 Agent 的信息敏感度（不同 agent 受信息影响程度不同）**：

| Agent | 信息敏感度 | 理由 |
|-------|-----------|------|
| smelter | 0.3 | 供给端，信息滞后 |
| resource | 0.3 | 同上 |
| merchant | 0.5 | 现货市场，信息敏感 |
| institution | 0.7 | 纯投，快速反应 |
| arbitrage | 0.6 | 套利，关注跨市场 |
| retail | 0.9 | 散户，易受情绪传染 |
| policy | 0.2 | 政策，独立判断 |

**效果**：如果大多数 agent 看多，而 smelter 看空，smelter 会因信息不对称而减少看空程度（修正 toward neutral）。

### Layer 2: 行为影响因子 (Behavioral Impact) — 仓位修正

**逻辑**：其他 Agent 的仓位变化会影响我的预期仓位。

**公式**：

```
behavioral_impact(agent_i) = Σ [sign(delta_pos_j) == sign(delta_pos_i)] × weight_j × momentum_j

momentum_j = |delta_pos_j| / max_pos_j  (0~1 的动量)

position_correction = behavioral_impact × behavior_sensitivity × 0.05
```

**各 Agent 的行为敏感度**：

| Agent | 行为敏感度 | 理由 |
|-------|-----------|------|
| smelter | 0.2 | 供给刚性强，不轻易变仓位 |
| resource | 0.2 | 同上 |
| merchant | 0.5 | 会根据别人操作调整 |
| institution | 0.6 | 趋势跟随者 |
| arbitrage | 0.4 | 独立套利，不太跟风 |
| retail | 0.9 | 散户，强烈跟风 |
| policy | 0.1 | 政策独立，不跟风 |

### Layer 3: 极化博弈因子 (Polarization Trigger) — 特殊事件触发

**逻辑**：当 agent 间分歧极大时（多空比分接近 50/50），触发市场异常状态。

**触发条件**：
- 看多 agent 数 / 看空 agent 数 < 2/5 或 > 5/2 → 极化
- 且 |avg_score| > 30 → 分歧强烈

**触发效果**：
- 写事件到 `agent_events` 表：type="polarization", severity=3
- 标记所有 agent 的 `needs_review=1`（等待人工或 LLM 审核）
- 在 score 上给一个 ±5 的"反转因子"（极端分歧可能意味着拐点）

---

## 三、实现方案

### 修改 `compute_agent_score` 返回值

```python
def compute_agent_score(agent_id, market):
    # ... existing logic ...
    return {
        "base_score": score,       # 基础评分（不变）
        "base_text": reasons_text,  # 基础原因
        "delta_position": delta_pos,
        "trigger": trigger,
        "logic_scores": {          # 7维度逻辑分数
            "supply_risk": ...,
            "demand_drop": ...,
            "inventory": ...,
            "macro": ...,
            "cost": ...,
            "sentiment": ...,
            "basis": ...,
        }
    }
```

### 新增 `apply_game_factors` 函数

```python
def apply_game_factors(all_agent_results, market):
    """
    对所有 agent 应用博弈因子修正。
    
    all_agent_results: list of dicts (from compute_agent_score)
    Returns: dict of {agent_id: {corrected_score, factors_applied}}
    """
    # 1. 计算信息传递因子
    info_factors = compute_info_transfer(all_agent_results)
    
    # 2. 计算行为影响因子  
    behavioral_factors = compute_behavioral_impact(all_agent_results, market)
    
    # 3. 检查极化博弈
    polarization = check_polarization(all_agent_results)
    
    # 4. 综合修正
    results = {}
    for agent_result in all_agent_results:
        aid = agent_result["agent_id"]
        base = agent_result["base_score"]
        
        # Layer 1: 信息传递
        info_corr = info_factors.get(aid, 0)
        
        # Layer 2: 行为影响
        beh_corr = behavioral_factors.get(aid, 0)
        
        # Layer 3: 极化反转
        polar_corr = polarization.get(aid, 0)
        
        # 综合修正（不超过 ±10）
        total_correction = info_corr + beh_corr + polar_corr
        total_correction = clamp(total_correction, -10, 10)
        
        corrected = base + total_correction
        factors_applied = {
            "info_transfer": round(info_corr, 2),
            "behavioral_impact": round(beh_corr, 2),
            "polarization": round(polar_corr, 2),
            "total_correction": round(total_correction, 2),
        }
        results[aid] = {
            "corrected_score": corrected,
            "factors": factors_applied,
        }
    
    return results
```

### DB 变更

新增表 `agent_game_factors`：
```sql
CREATE TABLE IF NOT EXISTS agent_game_factors (
    date TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    base_score INTEGER,
    info_transfer REAL,
    behavioral_impact REAL,
    polarization REAL,
    total_correction REAL,
    corrected_score INTEGER,
    factors_detail TEXT,  -- JSON
    PRIMARY KEY (date, agent_id)
);
```

---

## 四、7 维度逻辑分数体系

每个 agent 的 score 可以映射到 7 个交易逻辑维度：

| 维度 | 来源 | 说明 |
|------|------|------|
| supply_risk | smelter + resource | 供给端风险（产能、减产、矿端扰动） |
| demand_drop | merchant + institution | 需求端（消费、库存消化） |
| inventory | merchant + arbitrage | 库存压力 |
| macro | institution + policy | 宏观情绪（政策、经济周期） |
| cost | resource + smelter | 成本支撑 |
| sentiment | retail + institution | 市场情绪（散户+机构） |
| basis | merchant + arbitrage | 基差/月差结构 |

映射方式：
```python
# 例如 supply_risk = smelter.score * 0.6 + resource.score * 0.4
# 权重基于 agent 对该维度的敏感度
logic_scores = {
    "supply_risk": round(agent_scores["smelter"] * 0.6 + agent_scores["resource"] * 0.4),
    "demand_drop": round(agent_scores["merchant"] * 0.5 + agent_scores["institution"] * 0.5),
    # ...
}
```

这样后续可以做甘特图：日期 × 7维度 × 分数（热力图）。

---

## 五、执行流程

```
run_daily_update():
    1. 读取市场数据 (不变)
    2. FIRST PASS: 对所有 agent 调用 compute_agent_score → 得到 base_score + logic_scores
    3. 调用 apply_game_factors → 得到 corrected_score + factors_detail
    4. 检查止损止盈 (使用 corrected_score)
    5. 写入 DB:
       - agents 表: view_score = corrected_score (覆盖原来的 base_score)
       - agent_history: 写入 corrected_score 和 base_score 两个字段
       - agent_game_factors: 写入因子详情
       - agent_view_log: factors_detail 写入 JSON
    6. 检查极化博弈 → 写事件
    7. 输出摘要（含因子信息）
```

---

## 六、参数调优建议

初期参数：
- 信息敏感度：基于上表，可调
- 行为敏感度：基于上表，可调
- 因子上限：±10，可逐步放宽到 ±15
- 极化阈值：3:1，可调整

调优方法：
1. 先用历史数据跑一周，观察因子修正分布
2. 对比 corrected_score 和 base_score 的差异
3. 看哪些 agent 受博弈影响最大
4. 根据实际效果调整敏感度参数
