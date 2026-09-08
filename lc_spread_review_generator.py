#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
碳酸锂月差复盘页面生成器 V1
=============================
流程：
1. 加载月差/持仓/现货数据
2. 运行评分器计算5大逻辑得分
3. 生成JSON数据注入模板
4. 输出到 static/ 目录

用法：
  python lc_spread_review_generator.py
"""

import json
import os
import sys
import datetime
import sqlite3

sys.path.insert(0, os.path.dirname(__file__))
from lc_spread_review_scorer import (
    load_spread_data, calc_scores, get_latest_summary,
    CORE_PAIRS, LC_POSITION_DB
)

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), 'static')
TEMPLATE_PATH = os.path.join(os.path.dirname(__file__), 'lc_spread_review_template.html')

LOGIC_NAMES_CN = {
    'term_structure': '期限结构',
    'spread_momentum': '月差动量',
    'capital_flow': '资金流向',
    'basis_convergence': '基差收敛',
    'signal_strength': '信号强度',
}

LOGIC_COLORS = {
    'term_structure': '#56d364',
    'spread_momentum': '#f0b232',
    'capital_flow': '#58a6ff',
    'basis_convergence': '#d2a8ff',
    'signal_strength': '#f85149',
}


def build_pair_json(pair_key, scores, data):
    """构建单个合约对的JSON数据"""
    if scores is None or scores.empty:
        return None

    near, far = pair_key.split('-')
    dates = [str(d)[:10] for d in scores.index]

    # 各逻辑得分序列
    logic_series = {}
    for col in scores.columns:
        logic_series[col] = [round(float(v), 2) for v in scores[col].values]

    # 主导逻辑序列
    dominant = scores.abs().idxmax(axis=1)
    dominant_series = [str(d)[:10] for d in dominant.values]

    # 从lithium.db读月差
    conn = sqlite3.connect(os.path.join(os.path.dirname(__file__), '../lithium_calendar/lithium.db'))
    spread_data_rows = conn.execute(
        "SELECT date, spread FROM spreads WHERE near_contract=? AND far_contract=? ORDER BY date",
        (near, far)
    ).fetchall()
    conn.close()

    spread_data = [{'date': str(r[0])[:10], 'spread': round(float(r[1]), 1)} for r in spread_data_rows]

    # 最新摘要
    summary = get_latest_summary(scores, data, pair_key)

    return {
        'key': pair_key,
        'label': near[-4:] + '-' + far[-4:],
        'near': near,
        'far': far,
        'dates': dates,
        'logic_series': logic_series,
        'dominant_series': dominant_series,
        'logic_names_cn': LOGIC_NAMES_CN,
        'logic_colors': LOGIC_COLORS,
        'spread_data': spread_data,
        'latest': summary,
    }


def main():
    print("=== 碳酸锂月差复盘页面生成器 ===")

    # 加载数据
    data = load_spread_data('2026-05-01', '2026-07-31')
    print(f"数据: 月差{len(data['spreads'])}条, 价格{len(data['prices'])}条, 持仓{len(data['positions'])}条, 现货{len(data['spot'])}条")

    # 构建各合约对数据
    pairs_json = []
    summaries = []

    for pair in CORE_PAIRS:
        pair_key = pair[0] + '-' + pair[1]
        print(f"\n计算 {pair_key}...")
        try:
            scores = calc_scores(data, main_pair=pair_key)
            if scores is not None and not scores.empty:
                pj = build_pair_json(pair_key, scores, data)
                if pj:
                    pairs_json.append(pj)
                    summaries.append(pj['latest'])
                    print(f"  OK: {len(scores)}天, 最新={pj['latest']}")
            else:
                print(f"  无数据")
        except Exception as e:
            print(f"  错误: {e}")
            import traceback
            traceback.print_exc()

    if not pairs_json:
        print("没有可生成数据的合约对")
        return

    # 读取模板
    with open(TEMPLATE_PATH, 'r', encoding='utf-8') as f:
        template = f.read()

    # 生成JSON
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M')
    range_str = '2026-05-01 ~ 2026-07-31'
    meta = now + ' | 数据范围: ' + range_str + ' | 共 ' + str(len(summaries)) + ' 个合约对分析'
    range_repl = range_str

    pairs_str = json.dumps(pairs_json, ensure_ascii=False, indent=2)
    summaries_str = json.dumps(summaries, ensure_ascii=False, indent=2)

    # 替换占位符
    html = template
    html = html.replace('{RANGE}', range_str)
    html = html.replace('{META}', meta)
    html = html.replace('{PAIRS_JSON}', pairs_str)
    html = html.replace('{SUMMARIES_JSON}', summaries_str)

    # 写入
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    date_str = datetime.datetime.now().strftime('%Y%m%d')
    output_path = os.path.join(OUTPUT_DIR, 'lc_spread_review_latest.html')
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html)

    print(f"\n生成: {output_path}")
    print(f"文件大小: {os.path.getsize(output_path)} bytes")
    print(f"合约对数量: {len(pairs_json)}")


if __name__ == '__main__':
    main()
