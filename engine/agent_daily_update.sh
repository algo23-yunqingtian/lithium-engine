#!/bin/bash
# 碳酸锂多主体博弈 — Agent 每日更新链（18:30 cron）
# 顺序: 1) 重算基本面六维指数(防透视滚动z-score) 2) 重算主体心理状态/价位带 3) 运行agent规则引擎
set -e
cd /home/ubuntu
echo "[1/3] 重算基本面六维指数..."
/home/ubuntu/zinc_venv/bin/python /home/ubuntu/.hermes/scripts/lithium_agents/fundamental_indices.py >/dev/null 2>&1 || echo "[WARN] fundamental_indices 重算失败(可能源Excel未更新，使用旧数据)"
echo "[2/3] 重算主体心理状态与心理价位带..."
/home/ubuntu/zinc_venv/bin/python /home/ubuntu/.hermes/scripts/lithium_agents/agent_psychology_calc.py --days 60 >/dev/null 2>&1 || echo "[WARN] agent_psychology 重算失败"
echo "[3/3] 运行 Agent 规则引擎..."
exec /home/ubuntu/zinc_venv/bin/python /home/ubuntu/.hermes/scripts/lithium_agents/agent_daily_update.py "$@"
