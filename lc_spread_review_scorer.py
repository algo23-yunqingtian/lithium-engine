#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
碳酸锂月差复盘评分器 V1
========================
对标锌逻辑评分器（zinc_logic_scorer_v3），但针对月差结构特征设计。

核心逻辑：
1. 月差结构类型判断（C/B/Flat）→ 趋势信号
2. 月差变化速率 → 加速/减速信号
3. 持仓联动 → 资金流向信号
4. 现货基差 → 基差收敛/发散信号
5. 正套/反套建仓平仓信号

输出：每日各逻辑得分（-10到+10），主导逻辑判定

数据源：
- lithium.db: contract_prices（合约价格）, spreads（月价差）
- lc_position.db: position_rank（持仓排名）, warehouse_receipt（仓单）
- lc_spot.db: spot_price（现货+基差）
"""

import sqlite3
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

LITHIUM_DB = '/home/ubuntu/lithium_calendar/lithium.db'
LC_POSITION_DB = '/home/ubuntu/lc_futures_data/data/lc_position.db'
LC_SPOT_DB = '/home/ubuntu/lc_futures_data/data/lc_spot.db'

# 核心合约对定义（按关注度排序）
CORE_PAIRS = [
    ('LC2608', 'LC2609'),  # 08-09: 近月换月
    ('LC2609', 'LC2610'),  # 09-10: 主力月间
    ('LC2609', 'LC2611'),  # 09-11: 主力跨季
    ('LC2609', 'LC2612'),  # 09-12: 主力远月
    ('LC2611', 'LC2612'),  # 11-12
]

# 主力合约（用于持仓聚合）
MAIN_CONTRACT = 'LC2609'


def load_spread_data(start_date, end_date):
    """加载月差数据（价格+持仓+现货）"""
    # 1. 月差表 + 合约价格 (同一连接)
    conn = sqlite3.connect(LITHIUM_DB)
    spread_df = pd.read_sql_query(
        f"SELECT date, near_contract, far_contract, spread FROM spreads "
        f"WHERE date BETWEEN '{start_date}' AND '{end_date}' ORDER BY date",
        conn
    )
    price_df = pd.read_sql_query(
        f"SELECT date, contract, settle FROM contract_prices "
        f"WHERE date BETWEEN '{start_date}' AND '{end_date}' ORDER BY date, contract",
        conn
    )
    conn.close()
    
    # 2. 持仓数据 (单独连接, 日期格式YYYYMMDD)
    sd_fmt = start_date.replace('-', '')
    ed_fmt = end_date.replace('-', '')
    pos_conn = sqlite3.connect(LC_POSITION_DB)
    pos_df = pd.read_sql_query(
        f"SELECT date, symbol, long_oi, short_oi FROM position_rank "
        f"WHERE date BETWEEN '{sd_fmt}' AND '{ed_fmt}' AND symbol='{MAIN_CONTRACT}' ORDER BY date",
        pos_conn
    )
    pos_conn.close()
    
    # 3. 现货+基差 (单独连接)
    spot_conn = sqlite3.connect(LC_SPOT_DB)
    spot_df = pd.read_sql_query(
        f"SELECT date, spot_price, near_price, near_basis, near_basis_rate FROM spot_price "
        f"WHERE date BETWEEN '{start_date.replace('-', '')}' AND '{end_date.replace('-', '')}' ORDER BY date",
        spot_conn
    )
    spot_conn.close()
    
    return {
        'spreads': spread_df,
        'prices': price_df,
        'positions': pos_df,
        'spot': spot_df,
    }


def build_spread_series(spread_df, near, far):
    """从月差表构建指定合约对的时间序列"""
    mask = (spread_df['near_contract'] == near) & (spread_df['far_contract'] == far)
    subset = spread_df[mask].copy()
    subset['date'] = pd.to_datetime(subset['date'])
    subset = subset.set_index('date')['spread']
    return subset


def build_custom_spread(price_df, near, far):
    """从合约价格计算自定义月差"""
    near_p = price_df[price_df['contract'] == near].set_index('date')['settle']
    far_p = price_df[price_df['contract'] == far].set_index('date')['settle']
    spread = near_p - far_p
    spread = spread.dropna()
    return spread


def build_position_series(pos_df):
    """构建持仓序列（聚合同一日期各席位数据）"""
    pos_df['date'] = pd.to_datetime(pos_df['date'], format='%Y%m%d')
    # 按日期聚合：long_oi总和作为总持仓
    pos_df = pos_df.groupby('date')['long_oi'].sum().sort_index()
    return pos_df


def build_spot_series(spot_df):
    """构建现货序列"""
    spot_df['date'] = pd.to_datetime(pd.to_datetime(spot_df['date']).dt.strftime('%Y-%m-%d'))
    spot_df = spot_df.set_index('date')
    return spot_df


def rolling_trend(series, window=4):
    """计算滚动趋势（连续上升/下降天数）"""
    diff = series.diff()
    return diff.rolling(window).apply(
        lambda x: (x > 0).sum() - (x < 0).sum(), raw=True
    )


def sigmoid(value, center, scale=10):
    """Sigmoid平滑映射"""
    return 2 / (1 + np.exp(-(value - center) / scale)) - 1


def calc_daily_change(series):
    """计算日变化率"""
    return series.pct_change().fillna(0)



def calc_scores(spread_data, main_pair='LC2609-LC2612'):
    """
    计算月差相关逻辑得分
    
    逻辑清单（5个核心逻辑）：
    1. term_structure — 期限结构信号（C/B判断+幅度）
    2. spread_momentum — 月差动量（变化加速/减速）
    3. capital_flow — 资金流向（持仓变化方向）
    4. basis_convergence — 基差收敛/发散
    5. signal_strength — 正反套信号强度
    """
    near, far = main_pair.split('-')
    spreads = spread_data['spreads']
    prices = spread_data['prices']
    positions = spread_data['positions']
    spot = spread_data['spot']
    
    # 构建月差序列
    spread_series = build_spread_series(spreads, near, far)
    if len(spread_series) < 5:
        spread_series = build_custom_spread(prices, near, far)
    if len(spread_series) < 3:
        return None
    
    # 构建持仓序列
    oi_series = build_position_series(positions)
    
    # 构建现货序列
    spot_series = None
    basis_rate_series = None
    if not spot.empty and 'near_basis_rate' in spot.columns:
        spot_df = spot.copy()
        spot_df['date'] = pd.to_datetime(spot_df['date'], format='%Y%m%d')
        spot_df = spot_df.set_index('date')
        basis_rate_series = spot_df.get('near_basis_rate')
    
    # 统一日期索引为datetime
    all_dates = pd.to_datetime(spread_series.index).sort_values()
    spread_series = spread_series.reindex(all_dates)
    n = len(all_dates)
    
    # 转为numpy数组避免pandas比较问题
    spread_vals = spread_series.values.astype(float)
    oi_vals = oi_series.reindex(all_dates).values.astype(float) if not oi_series.empty else np.zeros(n)
    
    # ===== 逻辑1: 期限结构 =====
    s = np.zeros(n)
    for i in range(n):
        val = spread_vals[i]
        if np.isnan(val):
            continue
        if val > 0:  # Contango
            s[i] = -abs(val) / 100.0 * 2
        elif val < 0:  # Backwardation
            s[i] = abs(val) / 100.0 * 2
        if i > 0:
            prev = spread_vals[i - 1]
            if not np.isnan(prev):
                if prev > 0 and val < 0:  # C→B
                    s[i] += 3
                elif prev < 0 and val > 0:  # B→C
                    s[i] -= 3
    scores_df = pd.DataFrame({'term_structure': np.clip(s, -10, 10)}, index=all_dates)
    
    # ===== 逻辑2: 月差动量 =====
    spread_chg = np.zeros(n)
    spread_acc = np.zeros(n)
    for i in range(1, n):
        if not np.isnan(spread_vals[i]) and not np.isnan(spread_vals[i-1]):
            spread_chg[i] = spread_vals[i] - spread_vals[i-1]
    for i in range(2, n):
        if not np.isnan(spread_chg[i]) and not np.isnan(spread_chg[i-1]):
            spread_acc[i] = spread_chg[i] - spread_chg[i-1]
    s2 = np.zeros(n)
    for i in range(n):
        val = spread_vals[i]
        chg = spread_chg[i]
        acc = spread_acc[i]
        if np.isnan(val):
            continue
        if val > 0:
            if acc > 50: s2[i] = -2
            elif acc < -50: s2[i] = 1.5
        elif val < 0:
            if acc < -50: s2[i] = -2
            elif acc > 50: s2[i] = 1.5
        if abs(chg) > 100:
            s2[i] += np.sign(chg) * 1.5
    scores_df['spread_momentum'] = np.clip(s2, -10, 10)
    
    # ===== 逻辑3: 资金流向 =====
    s3 = np.zeros(n)
    price_chg_vals = np.zeros(n)
    for i in range(1, n):
        if not np.isnan(spread_vals[i]) and not np.isnan(spread_vals[i-1]):
            price_chg_vals[i] = spread_vals[i] - spread_vals[i-1]
    for i in range(1, n):
        oi_chg = oi_vals[i] - oi_vals[i-1] if not np.isnan(oi_vals[i]) and not np.isnan(oi_vals[i-1]) else 0
        pc = price_chg_vals[i]
        if np.isnan(oi_vals[i]) or np.isnan(oi_vals[i-1]):
            continue
        if oi_chg > 500 and pc > 0:
            s3[i] = 3
        elif oi_chg > 500 and pc < 0:
            s3[i] = -3
        elif oi_chg < -500 and pc > 0:
            s3[i] = -2
        elif oi_chg < -500 and pc < 0:
            s3[i] = 2
        if i > 20 and oi_vals[i] > 0:
            recent_oi = oi_vals[max(0,i-20):i]
            future_oi = oi_vals[i:]
            if len(recent_oi) > 0 and len(future_oi) > 0 and np.all(~np.isnan(recent_oi)) and np.all(~np.isnan(future_oi)):
                oi_trend = np.mean(future_oi) / np.mean(recent_oi) - 1
                if oi_trend > 0.05:
                    s3[i] += 1
    scores_df['capital_flow'] = np.clip(s3, -10, 10)
    
    # ===== 逻辑4: 基差收敛/发散 =====
    s4 = np.zeros(n)
    if basis_rate_series is not None and not basis_rate_series.empty:
        br = basis_rate_series.reindex(all_dates).values.astype(float)
        for i in range(1, n):
            if not np.isnan(br[i]) and not np.isnan(br[i-1]) and not np.isnan(spread_vals[i]) and not np.isnan(spread_vals[i-1]):
                if abs(br[i]) < abs(br[i-1]) * 0.9:
                    s4[i] += 2
                elif abs(br[i]) > abs(br[i-1]) * 1.1:
                    s4[i] -= 2
                if abs(br[i]) > 0.03:
                    s4[i] += np.sign(br[i]) * 2
    scores_df['basis_convergence'] = np.clip(s4, -10, 10)
    
    # ===== 逻辑5: 信号强度 =====
    # 读取持仓数据
    near_pos = pd.read_sql_query(
        f"SELECT date, SUM(long_oi) as long_oi, SUM(short_oi) as short_oi "
        f"FROM position_rank WHERE symbol='{near}' GROUP BY date ORDER BY date",
        sqlite3.connect(LC_POSITION_DB)
    )
    far_pos = pd.read_sql_query(
        f"SELECT date, SUM(long_oi) as long_oi, SUM(short_oi) as short_oi "
        f"FROM position_rank WHERE symbol='{far}' GROUP BY date ORDER BY date",
        sqlite3.connect(LC_POSITION_DB)
    )
    sqlite3.connect(LC_POSITION_DB).close()
    
    near_pos['date'] = pd.to_datetime(near_pos['date'], format='%Y%m%d')
    far_pos['date'] = pd.to_datetime(far_pos['date'], format='%Y%m%d')
    near_pos = near_pos.set_index('date')
    far_pos = far_pos.set_index('date')
    
    s5 = np.zeros(n)
    THRESHOLD = 500
    for i in range(1, n):
        d = all_dates[i]
        d_prev = all_dates[i-1]
        n_curr = near_pos.loc[d] if d in near_pos.index else None
        n_prev = near_pos.loc[d_prev] if d_prev in near_pos.index else None
        f_curr = far_pos.loc[d] if d in far_pos.index else None
        f_prev = far_pos.loc[d_prev] if d_prev in far_pos.index else None
        
        if n_curr is None or n_prev is None or f_curr is None or f_prev is None:
            continue
        n_long_chg = float(n_curr['long_oi']) - float(n_prev['long_oi'])
        n_short_chg = float(n_curr['short_oi']) - float(n_prev['short_oi'])
        f_long_chg = float(f_curr['long_oi']) - float(f_prev['long_oi'])
        f_short_chg = float(f_curr['short_oi']) - float(f_prev['short_oi'])
        
        if n_long_chg > THRESHOLD and f_short_chg > THRESHOLD:
            s5[i] = 4
        elif n_long_chg < -THRESHOLD and f_short_chg < -THRESHOLD:
            s5[i] = -3
        elif n_short_chg > THRESHOLD and f_long_chg > THRESHOLD:
            s5[i] = -4
        elif n_short_chg < -THRESHOLD and f_long_chg < -THRESHOLD:
            s5[i] = 3
        elif abs(n_long_chg + n_short_chg) > THRESHOLD and abs(f_long_chg + f_short_chg) > THRESHOLD:
            total_chg = (n_long_chg + n_short_chg) + (f_long_chg + f_short_chg)
            s5[i] = np.sign(total_chg) * 2 if total_chg != 0 else 0
    scores_df['signal_strength'] = np.clip(s5, -10, 10)
    
    # EMA平滑
    for col in scores_df.columns:
        scores_df[col] = scores_df[col].ewm(span=2, adjust=False).mean()
    
    return scores_df


def calc_dominant(scores):
    """计算主导逻辑（绝对值最大者）"""
    if scores is None or scores.empty:
        return None
    dominant = scores.abs().idxmax(axis=1)
    return dominant
def calc_dominant(scores):
    """计算主导逻辑（绝对值最大者）"""
    if scores is None or scores.empty:
        return None
    dominant = scores.abs().idxmax(axis=1)
    return dominant


def get_latest_summary(scores, spread_data, main_pair='LC2609-LC2612'):
    """生成最新日摘要"""
    if scores is None or scores.empty:
        return {}
    
    near, far = main_pair.split('-')
    latest_date = scores.index[-1]
    latest_row = scores.iloc[-1]
    
    # 最新月差
    spread_series = build_spread_series(spread_data['spreads'], near, far)
    if len(spread_series) < 1:
        spread_series = build_custom_spread(spread_data['prices'], near, far)
    latest_spread = spread_series.iloc[-1] if len(spread_series) > 0 else 0
    
    summary = {
        'date': str(latest_date)[:10],
        'pair': main_pair,
        'spread': round(float(latest_spread), 1),
        'dominant_logic': str(scores.abs().iloc[-1].idxmax()),
        'logic_scores': {col: round(float(val), 2) for col, val in latest_row.items()},
    }
    
    # 结构类型
    if latest_spread > 0:
        summary['structure'] = 'Contango（远月升水）'
    elif latest_spread < 0:
        summary['structure'] = 'Backwardation（近月升水）'
    else:
        summary['structure'] = 'Flat（平水）'
    
    return summary


if __name__ == '__main__':
    print("=== 碳酸锂月差复盘评分器测试 ===")
    data = load_spread_data('2026-05-01', '2026-07-31')
    print(f"数据: 月差{len(data['spreads'])}条, 价格{len(data['prices'])}条, 持仓{len(data['positions'])}条, 现货{len(data['spot'])}条")
    
    for pair in [('LC2609', 'LC2612')]:
        pair_key = f"{pair[0]}-{pair[1]}"
        scores = calc_scores(data, main_pair=pair_key)
        if scores is not None and not scores.empty:
            print(f"\n=== {pair_key} 评分结果 ===")
            print(f"日期范围: {scores.index[0].date()} ~ {scores.index[-1].date()}")
            print(f"共 {len(scores)} 天")
            print(f"\n最近5天评分:")
            print(scores.tail(5).round(2))
            print(f"\n主导逻辑:")
            dominant = calc_dominant(scores)
            print(dominant.tail(10))
            
            summary = get_latest_summary(scores, data, pair_key)
            print(f"\n最新摘要: {summary}")
        else:
            print(f"\n{pair_key}: 无数据")
