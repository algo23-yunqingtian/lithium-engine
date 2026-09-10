#!/usr/bin/env python3
"""
exp430: 真实行情影子盘 —— 主入口

用法:
  # 手动运行一日
  /usr/bin/python3 model_ham/exp430_shadow_trading/run_exp430.py

  # 仅生成报告 (不运行流水线)
  /usr/bin/python3 model_ham/exp430_shadow_trading/run_exp430.py --report-only

  # 查看状态
  /usr/bin/python3 model_ham/exp430_shadow_trading/run_exp430.py --status

cron 每日 15:30 运行:
  30 15 * * 1-5 /usr/bin/python3 /home/ubuntu/lithium-engine/model_ham/exp430_shadow_trading/run_exp430.py >> /home/ubuntu/lithium-engine/model_ham/exp430_shadow_trading/data/cron_log.txt 2>&1
"""
import os
import sys
import json
import argparse
from datetime import datetime

EXP_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(EXP_DIR, "data")
SHADOW_LOG = os.path.join(DATA_DIR, "exp430_shadow_log.csv")
RUN_LOG = os.path.join(DATA_DIR, "exp430_run_log.json")

sys.path.insert(0, EXP_DIR)


def show_status():
    """显示影子盘当前状态."""
    print("=" * 60)
    print("exp430 影子盘状态")
    print("=" * 60)
    
    # 运行日志
    if os.path.exists(RUN_LOG):
        with open(RUN_LOG, "r") as f:
            history = json.load(f)
        success = [r for r in history if r.get("status") == "SUCCESS"]
        fail = [r for r in history if r.get("status") == "FAIL"]
        print(f"\n运行历史: {len(success)} 成功, {len(fail)} 失败")
        if success:
            print(f"首次: {success[0]['timestamp'][:19]}")
            print(f"最近: {success[-1]['timestamp'][:19]}")
    else:
        print("\n无运行历史")
    
    # 影子盘日志
    import pandas as pd
    if os.path.exists(SHADOW_LOG):
        log = pd.read_csv(SHADOW_LOG)
        print(f"\n影子盘日志: {len(log)} 日")
        if len(log) > 0:
            last = log.iloc[-1]
            print(f"最近日: {last['date']}")
            print(f"  close={last['close']:.0f} action_A={last['action_A']}")
            print(f"  NAV_A={last['nav_A']:.0f} NAV_B={last['nav_B']:.0f}")
            print(f"  累计A={last['cum_return_A']*100:.2f}% 累计B={last['cum_return_B']*100:.2f}%")
            print(f"  告警={last['alert_level']}")
    else:
        print("\n无影子盘日志")
    
    # 进度
    if os.path.exists(SHADOW_LOG):
        log = pd.read_csv(SHADOW_LOG)
        n = len(log)
        pct = n / 20 * 100
        bar = "█" * int(pct / 5) + "░" * (20 - int(pct / 5))
        print(f"\n影子盘进度: [{bar}] {n}/20 日 ({pct:.0f}%)")


def main():
    parser = argparse.ArgumentParser(description="exp430 真实行情影子盘")
    parser.add_argument("--report-only", action="store_true",
                        help="仅生成报告，不运行流水线")
    parser.add_argument("--status", action="store_true",
                        help="显示影子盘状态")
    parser.add_argument("--symbol", type=str, default=None,
                        help="指定合约代码 (如 LC2609)")
    args = parser.parse_args()
    
    if args.status:
        show_status()
        return
    
    if args.report_only:
        from report_generator import generate_report
        generate_report()
        return
    
    # 运行影子盘
    from shadow_runner import run_shadow_trading
    success, msg, state = run_shadow_trading(force_symbol=args.symbol)
    
    if success:
        # 同时生成报告
        from report_generator import generate_report
        generate_report()
        print("\n影子盘运行 + 报告生成完成。")
    else:
        print(f"\n影子盘运行失败: {msg}")
        sys.exit(1)


if __name__ == "__main__":
    main()
