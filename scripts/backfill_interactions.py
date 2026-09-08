#!/usr/bin/env python
"""
Phase 5: 从历史 agent_action_log 数据中推算 agent 间互动关系。
将每天的历史 position/score 变化转化为互动矩阵数据，写入 agent_interactions 表。
"""
import sqlite3, json
from datetime import datetime

DB_PATH = "/home/ubuntu/lithium_calendar/lithium_v2_20260730.db"

conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row

# 读取所有历史动作日志
rows = [dict(r) for r in conn.execute("""
    SELECT date, agent_id, prev_position, new_position, delta,
           prev_view_score, new_view_score, trigger, reasoning, pnl_unreal
    FROM agent_action_log
    ORDER BY date, agent_id
""").fetchall()]

print(f"共 {len(rows)} 条历史动作记录")

if not rows:
    print("没有历史数据可回推")
    conn.close()
    exit(0)

# 按日期分组
from collections import defaultdict
by_date = defaultdict(list)
for r in rows:
    by_date[r['date']].append(r)

# 创建 agent_interactions 表
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

# 对每个日期，推断互动关系
total_interactions = 0
for date in sorted(by_date.keys()):
    day_rows = by_date[date]
    
    # 构建 date 级别的 agent 状态快照
    agents_info = {}
    for r in day_rows:
        aid = r['agent_id']
        agents_info[aid] = {
            'delta': r['delta'],
            'score_delta': r['new_view_score'] - r['prev_view_score'],
            'new_position': r['new_position'],
            'new_score': r['new_view_score'],
        }
    
    agent_ids = list(agents_info.keys())
    n = len(agent_ids)
    
    # 两两比较
    for i in range(n):
        for j in range(n):
            if i == j:
                continue
            ai = agent_ids[i]
            aj = agent_ids[j]
            ai_info = agents_info[ai]
            aj_info = agents_info[aj]
            
            # 信息冲突：一方看多一方看空
            if (ai_info['new_score'] > 0 and aj_info['new_score'] < 0) or \
               (ai_info['new_score'] < 0 and aj_info['new_score'] > 0):
                # ai 和 aj 方向冲突，aj -> ai 是冲突方
                mag = abs(aj_info['new_score'] + ai_info['new_score']) / max(abs(ai_info['new_score']) + 1, 1) * 50
                mag = round(mag, 1)
                if mag > 0:
                    conn.execute("""
                        INSERT OR REPLACE INTO agent_interactions (date, from_agent, to_agent, influence_type, magnitude)
                        VALUES (?,?,?,?,?)
                    """, (date, aj, ai, 'info_conflict', mag))
                    total_interactions += 1
            
            # 行为跟随：同方向变动
            if ai_info['delta'] != 0 and aj_info['delta'] != 0:
                same_dir = (ai_info['delta'] > 0 and aj_info['delta'] > 0) or \
                           (ai_info['delta'] < 0 and aj_info['delta'] < 0)
                if same_dir:
                    # 仓位大的一方影响仓位小的一方
                    ai_pos = abs(ai_info['new_position'])
                    aj_pos = abs(aj_info['new_position'])
                    if ai_pos > 0 and aj_pos > 0:
                        mag = round(min(ai_pos, aj_pos) / max(ai_pos, aj_pos) * 30, 1)
                        if mag > 0:
                            conn.execute("""
                                INSERT OR REPLACE INTO agent_interactions (date, from_agent, to_agent, influence_type, magnitude)
                                VALUES (?,?,?,?,?)
                            """, (date, aj, ai, 'behavior_follow', mag))
                            total_interactions += 1

print(f"回推完成，共 {total_interactions} 条互动记录")
conn.commit()

# 验证
count = conn.execute("SELECT COUNT(*) FROM agent_interactions").fetchone()[0]
dates = conn.execute("SELECT DISTINCT date FROM agent_interactions ORDER BY date").fetchall()
print(f"agent_interactions 表: {count} 条, {len(dates)} 天")
for d in dates:
    dc = conn.execute("SELECT COUNT(*) FROM agent_interactions WHERE date=?", (d[0],)).fetchone()[0]
    print(f"  {d[0]}: {dc} 条")

conn.close()
