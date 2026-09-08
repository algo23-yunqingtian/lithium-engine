#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
博弈因子体系 v1.0 — 三层次 Agent 间影响计算

Layer 1: 信息传递修正 — 评分方向分歧导致的不确定性修正
Layer 2: 行为影响修正 — 其他 Agent 仓位变动方向对我的影响
Layer 3: 极化博弈检测 — 多空极度分歧时的反转信号

修正幅度控制在 ±10 以内，保持 base_score 的主导地位。
"""

import json


def apply_game_factors(base_scores_dict, deltas_dict, texts_dict, agents_dict, capital_dict, market, today, conn, agent_ids):
    """
    对 run_daily_update 收集的数据应用博弈因子修正。
    
    参数:
        base_scores_dict: {agent_id: raw_score} 规则引擎计算的基础评分
        deltas_dict: {agent_id: position_delta} 仓位变动
        texts_dict: {agent_id: reasons_text} 原因文本
        agents_dict: {agent_id: agent_row_dict} agent 元数据（含 risk_params）
        capital_dict: {agent_id: capital} 资金量
        market: 市场数据字典
        today: 日期字符串
        conn: SQLite 连接（已开启事务）
        agent_ids:  agent 列表
    """
    n_bull = sum(1 for s in base_scores_dict.values() if s > 0)
    n_bear = sum(1 for s in base_scores_dict.values() if s < 0)
    total_agents = len(base_scores_dict)
    total_cap = max(sum(capital_dict.values()), 1)
    
    # Agent 敏感度配置
    info_sensitivities = {
        "smelter": 0.3, "resource": 0.3, "merchant": 0.5,
        "institution": 0.7, "arbitrage": 0.6, "retail": 0.9, "policy": 0.2,
    }
    beh_sensitivities = {
        "smelter": 0.2, "resource": 0.2, "merchant": 0.5,
        "institution": 0.6, "arbitrage": 0.4, "retail": 0.9, "policy": 0.1,
    }
    
    # ── Layer 1: 信息传递修正 ──
    info_factors = {}
    for aid in agent_ids:
        own_score = base_scores_dict.get(aid, 0)
        if own_score == 0:
            info_factors[aid] = 0.0
            continue
        opposing_cap = 0.0
        for other_id, other_score in base_scores_dict.items():
            if other_id != aid and other_score != 0:
                if (own_score > 0 and other_score < 0) or (own_score < 0 and other_score > 0):
                    opposing_cap += capital_dict.get(other_id, 0)
        ratio = opposing_cap / total_cap
        # 与自身方向相反 → 修正 toward zero (减少绝对值)
        info_factors[aid] = round(-ratio * info_sensitivities[aid] * 8, 2)
    
    # ── Layer 2: 行为影响修正 ──
    # 同向跟随效应: 其他 agent 与自身同向的仓位变动 → 轻微增强
    beh_factors = {}
    for aid in agent_ids:
        own_delta = deltas_dict.get(aid, 0)
        if own_delta == 0:
            beh_factors[aid] = 0.0
            continue
        influence = 0.0
        for other_id in agent_ids:
            other_delta = deltas_dict.get(other_id, 0)
            if other_id != aid and other_delta != 0:
                if (own_delta > 0 and other_delta > 0) or (own_delta < 0 and other_delta < 0):
                    risk_p = agents_dict.get(other_id, {}).get("risk_params", "{}")
                    try:
                        max_pos = json.loads(risk_p)["max_position"]
                    except Exception:
                        max_pos = 100
                    momentum = abs(other_delta) / max_pos if max_pos > 0 else 0
                    influence += (capital_dict.get(other_id, 0) / total_cap) * momentum
        beh_factors[aid] = round(influence * beh_sensitivities[aid] * 5, 2)
    
    # ── Layer 3: 极化博弈检测 ──
    polarization_event = None
    if n_bull > 0 and n_bear > 0:
        ratio = max(n_bull, n_bear) / min(n_bull, n_bear)
        avg_abs = sum(abs(s) for s in base_scores_dict.values()) / total_agents
        if ratio > 3 and avg_abs > 30:
            polarization_event = {
                "bull": n_bull, "bear": n_bear,
                "ratio": round(ratio, 2), "avg_score": round(avg_abs, 1),
            }
            # 极化 → 反转因子: 多数派减分，少数派加分
            for aid in agent_ids:
                s = base_scores_dict.get(aid, 0)
                if s > 0:
                    info_factors[aid] -= 5  # 看多的减分
                elif s < 0:
                    info_factors[aid] += 5  # 看空的加分
    
    # ── 输出 Layer 1&2 ──
    print(f"\n[博弈因子 Layer 1(信息传递) & Layer 2(行为影响)]")
    for aid in agent_ids:
        base = base_scores_dict.get(aid, 0)
        info_f = info_factors.get(aid, 0)
        beh_f = beh_factors.get(aid, 0)
        total_f = round(info_f + beh_f, 2)
        corrected = _clamp(base + total_f, -100, 100)
        print(f"  {aid:12s} base={int(base):4d} info={info_f:+6.2f} beh={beh_f:+6.2f} "
              f"total={total_f:+6.2f} → corrected={int(corrected):4d}")
    
    if polarization_event:
        print(f"\n[⚠️ 极化博弈检测] 多空比 {n_bull}:{n_bear} ({polarization_event['ratio']:.1f}x)")
    
    # ── 写入 agent_game_factors 表 ──
    print(f"\n[写入博弈因子到 DB...]")
    
    # ── 记录 agent 间互动关系 ──
    interactions = []  # (from_agent, to_agent, influence_type, magnitude, direction)
    for aid in agent_ids:
        own_score = base_scores_dict.get(aid, 0)
        if own_score == 0:
            continue
        for other_id in agent_ids:
            if other_id == aid:
                continue
            other_score = base_scores_dict.get(other_id, 0)
            if other_score == 0:
                continue
            # Layer 1: 信息传递 — 方向冲突度
            if (own_score > 0 and other_score < 0) or (own_score < 0 and other_score > 0):
                opp_cap = capital_dict.get(other_id, 0)
                total_cap = max(sum(capital_dict.values()), 1)
                mag = round(opp_cap / total_cap * 100, 1)
                if mag > 0:
                    interactions.append((other_id, aid, 'info_conflict', mag))
            # Layer 2: 行为影响 — 同向跟随
            own_delta = deltas_dict.get(aid, 0)
            other_delta = deltas_dict.get(other_id, 0)
            if own_delta != 0 and other_delta != 0:
                same_dir = (own_delta > 0 and other_delta > 0) or (own_delta < 0 and other_delta < 0)
                if same_dir:
                    influence = beh_factors.get(aid, 0)
                    if influence > 0:
                        interactions.append((other_id, aid, 'behavior_follow', round(influence * 10, 1)))

    if interactions:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS agent_interactions (
                date              TEXT NOT NULL,
                from_agent        TEXT NOT NULL,
                to_agent          TEXT NOT NULL,
                influence_type    TEXT NOT NULL,
                magnitude         REAL NOT NULL,
                PRIMARY KEY (date, from_agent, to_agent, influence_type)
            )
        """)
        for from_a, to_a, itype, mag in interactions:
            conn.execute("""
                INSERT OR REPLACE INTO agent_interactions (date, from_agent, to_agent, influence_type, magnitude)
                VALUES (?,?,?,?,?)
            """, (today, from_a, to_a, itype, mag))
        print(f"\n[互动关系] 记录 {len(interactions)} 条 agent 间影响")
    
    review_count_global = 0
    for aid in agent_ids:
        base = base_scores_dict.get(aid, 0)
        info_f = info_factors.get(aid, 0)
        beh_f = beh_factors.get(aid, 0)
        total_f = round(info_f + beh_f, 2)
        corrected = _clamp(base + total_f, -100, 100)
        conn.execute("""
            INSERT OR REPLACE INTO agent_game_factors 
            (date, agent_id, base_score, info_transfer, behavioral_impact, 
             total_correction, corrected_score, factors_detail)
            VALUES (?,?,?,?,?,?,?,?)
        """, (today, aid, int(base), info_f, beh_f, total_f, int(corrected),
              json.dumps({
                  "info_transfer": info_f,
                  "behavioral_impact": beh_f,
                  "total_correction": total_f,
                  "polarization": polarization_event is not None,
                  "n_bull": n_bull, "n_bear": n_bear,
              }, ensure_ascii=False)))
    
    # ── 覆盖 agents.view_score 为 corrected_score ──
    for aid in agent_ids:
        base = base_scores_dict.get(aid, 0)
        info_f = info_factors.get(aid, 0)
        beh_f = beh_factors.get(aid, 0)
        corrected = _clamp(base + info_f + beh_f, -100, 100)
        needs_review = polarization_event is not None or abs(corrected - base) > 10
        
        conn.execute("""
            UPDATE agents SET view_score=?, needs_review=?, updated_at=datetime('now','localtime')
            WHERE agent_id=?
        """, (corrected, 1 if needs_review else 0, aid))
        
        old_text = texts_dict.get(aid, "")
        factor_text = f"[博弈] info={info_f:+.1f} beh={beh_f:+.1f}"
        if polarization_event:
            factor_text += " [极化⚠️]"
        
        conn.execute("""
            UPDATE agent_view_log SET daily_reasoning=?
            WHERE agent_id=? AND date=?
        """, (json.dumps({
            "base_score": base, "base_text": old_text,
            "info_transfer": info_f, "behavioral_impact": beh_f,
            "corrected_score": corrected,
            "polarization": polarization_event is not None,
            "polarization_event": polarization_event,
        }, ensure_ascii=False), aid, today))
        
        if needs_review:
            review_count_global += 1
    
    # ── 汇总 ──
    print(f"\n{'='*60}")
    print(f"博弈因子计算完成，{review_count_global} 个 agent 标记待审核")
    if polarization_event:
        print(f"⚠️ 极化博弈触发: 多空{n_bull}:{n_bear}，建议人工审核")
    print(f"{'='*60}")


def _clamp(val, lo=-100, hi=100):
    return max(lo, min(hi, val))
