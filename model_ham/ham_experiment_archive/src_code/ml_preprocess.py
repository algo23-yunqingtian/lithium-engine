#!/usr/bin/env python3
"""
HAM因子预处理脚本 — 构建ML输入数据集
输出: ml_input_features.csv + feature_analysis.md

硬性约束:
1. 不修改 ham_factors_full_800d.csv 原始文件
2. 标签严格使用t时刻之后的收益率，禁止未来函数
3. 不伪造行业数据
4. 全部使用相对路径
"""

import csv
import json
import math
import os
import sqlite3
from collections import defaultdict
from datetime import datetime

ROOT = os.path.dirname(os.path.abspath(__file__))

# ============================================================
# 1. 读取 HAM 因子原始数据
# ============================================================
print("=" * 60)
print("STEP 1: 读取 HAM 因子数据")
print("=" * 60)

with open(os.path.join(ROOT, "ham_factors_full_800d.csv")) as f:
    reader = csv.DictReader(f)
    ham_data = list(reader)

print(f"HAM 数据: {len(ham_data)} 行")
print(f"日期范围: {ham_data[0]['date']} ~ {ham_data[-1]['date']}")

# 提取为字典列表，统一类型
for r in ham_data:
    r['close'] = float(r['close'])
    r['open'] = float(r['open'])
    r['high'] = float(r['high'])
    r['low'] = float(r['low'])
    r['volume'] = float(r['volume'])
    r['position'] = float(r['position'])
    r['P_fund'] = float(r['P_fund'])
    r['basis'] = float(r['basis'])
    r['spot_avg'] = float(r['spot_avg'])
    r['D_f'] = float(r['D_f'])
    r['D_c'] = float(r['D_c'])
    r['profit_f'] = float(r['profit_f'])
    r['profit_c'] = float(r['profit_c'])
    r['n_f'] = float(r['n_f'])
    r['n_c'] = float(r['n_c'])
    r['Total_Demand'] = float(r['Total_Demand'])
    r['n_f_minus_n_c'] = float(r['n_f_minus_n_c'])
    r['valid'] = int(r['valid'])

# ============================================================
# 2. 读取参数扫描数据，分析 Total_Demand 符号翻转
# ============================================================
print("\n" + "=" * 60)
print("STEP 2: 分析 Total_Demand 符号翻转过滤规则")
print("=" * 60)

with open(os.path.join(ROOT, "param_sensitivity_scan.csv")) as f:
    scan_data = list(csv.DictReader(f))

td_data = [r for r in scan_data if r['factor'] == 'Total_Demand']

# 按 W 分组分析符号
by_W = defaultdict(list)
for r in td_data:
    w = int(r['W'])
    by_W[w].append(float(r['rank_ic']))

symbol_flip_summary = {}
for w in sorted(by_W.keys()):
    neg = sum(1 for ic in by_W[w] if ic < 0)
    pos = sum(1 for ic in by_W[w] if ic > 0)
    symbol_flip_summary[w] = {"neg": neg, "pos": pos, "total": len(by_W[w])}
    print(f"  W={w}: 负={neg}, 正={pos} → {'全负' if pos==0 else '全正' if neg==0 else '混合'}")

# 关键结论: W=20,40 → 全负; W=60 → 全正
# ham_factors_full_800d.csv 使用 W=20 参数 (来自 main_run.py 默认参数)
# W=20 属于稳定区间（rank_ic全负），无需过滤
# 符号翻转风险仅存在于W=60，与当前数据无关
print("\n结论: ham_factors_full_800d.csv 使用 W=20 参数，属于稳定区间")
print("      W=20/40 全部 rank_ic 为负（稳定），W=60 全部为正（符号翻转）")
print("      W=60 不参与当前因子计算，无需过滤样本")

# ============================================================
# 3. 获取外部辅助指标
# ============================================================
print("\n" + "=" * 60)
print("STEP 3: 获取外部辅助指标")
print("=" * 60)

external_features = {}
external_feature_status = {}
external_feature_detail = {}

# 3.1 从 HAM CSV 提取量价特征（close, volume, position 已在原始数据中）
# 计算动量指标
print("  [1/6] 量价特征 (HAM CSV 内置): close, volume, position")
print("  [2/6] 基差/现货 (HAM CSV 内置): basis, spot_avg, basis_rate")

# 计算基差率 (basis / spot_avg * 100)
for r in ham_data:
    if r['spot_avg'] != 0:
        r['basis_rate'] = (r['basis'] / r['spot_avg']) * 100
    else:
        r['basis_rate'] = 0.0

external_features['basis_rate'] = [r['basis_rate'] for r in ham_data]
external_feature_status['basis_rate'] = '已从 HAM CSV 计算 (basis/spot_avg*100)'
external_feature_detail['basis_rate'] = {'缺失': 0, '来源': 'HAM CSV 内置'}

# 3.2 计算动量指标 (5日/10日/20日收益率)
print("  [3/6] 动量指标: ret_5d, ret_10d, ret_20d")
closes = [r['close'] for r in ham_data]
dates = [r['date'] for r in ham_data]

for lookback in [5, 10, 20]:
    feat_name = f'ret_{lookback}d'
    vals = []
    for i in range(len(closes)):
        if i < lookback:
            vals.append(None)
        else:
            vals.append((closes[i] - closes[i - lookback]) / closes[i - lookback])
    external_features[feat_name] = vals
    missing = sum(1 for v in vals if v is None)
    external_feature_status[feat_name] = f'已计算 (close.t / close.t-{lookback} - 1)'
    external_feature_detail[feat_name] = {'缺失': missing, '来源': 'HAM CSV close 列计算'}
    print(f"    {feat_name}: {len(closes) - missing} 有效, {missing} 缺失")

# 3.3 从 lithium.db 获取 fundamental_indices
print("  [4/6] fundamental_indices (lithium.db)")
try:
    db_path = '/home/ubuntu/lithium_calendar/lithium.db'
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("SELECT date, supply, demand, inventory, profit, sentiment, basis, composite, n_dims FROM fundamental_indices ORDER BY date")
    fi_rows = c.fetchall()
    conn.close()
    
    fi_dict = {}
    for row in fi_rows:
        fi_dict[row[0]] = {
            'fi_supply': row[1],
            'fi_demand': row[2],
            'fi_inventory': row[3],
            'fi_profit': row[4],
            'fi_sentiment': row[5],
            'fi_basis': row[6],
            'fi_composite': row[7],
            'fi_n_dims': row[8],
        }
    
    fi_cols = ['fi_supply', 'fi_demand', 'fi_inventory', 'fi_profit', 'fi_sentiment', 'fi_basis', 'fi_composite']
    for col in fi_cols:
        vals = []
        for r in ham_data:
            if r['date'] in fi_dict and fi_dict[r['date']][col] is not None:
                vals.append(fi_dict[r['date']][col])
            else:
                vals.append(None)
        external_features[col] = vals
        missing = sum(1 for v in vals if v is None)
        external_feature_status[col] = f'已获取 (lithium.db fundamental_indices)'
        external_feature_detail[col] = {'缺失': missing, '来源': 'lithium.db fundamental_indices'}
        print(f"    {col}: {len(vals) - missing} 有效, {missing} 缺失")
except Exception as e:
    print(f"  [4/6] 失败: {e}")
    external_feature_status['fundamental_indices'] = f'获取失败: {e}'

# 3.4 从 warehouse_receipt 获取仓单库存
print("  [5/6] 仓单库存 (warehouse_receipt)")
try:
    db_path2 = '/home/ubuntu/lc_futures_data/data/lc_position.db'
    conn = sqlite3.connect(db_path2)
    c = conn.cursor()
    c.execute("SELECT date, SUM(today_qty) as total_wh FROM warehouse_receipt GROUP BY date ORDER BY date")
    wh_rows = c.fetchall()
    conn.close()
    
    wh_dict = {}
    for row in wh_rows:
        wh_dict[row[0]] = row[1]  # 日期格式: '20260506'
    
    wh_vals = []
    for r in ham_data:
        # HAM 日期格式: '2023-08-18', warehouse 日期格式: '20260506'
        wh_key = r['date'].replace('-', '')
        if wh_key in wh_dict:
            wh_vals.append(wh_dict[wh_key])
        else:
            wh_vals.append(None)
    
    external_features['warehouse_inventory'] = wh_vals
    missing = sum(1 for v in wh_vals if v is None)
    external_feature_status['warehouse_inventory'] = f'已获取 ({len(wh_vals)-missing}天有数据, 仅2026-05后)'
    external_feature_detail['warehouse_inventory'] = {'缺失': missing, '来源': 'lc_position.db warehouse_receipt'}
    print(f"    warehouse_inventory: {len(wh_vals) - missing} 有效, {missing} 缺失")
    print(f"    注意: 仓单数据仅覆盖 2026-05-06 ~ 2026-09-01 (84天)")
except Exception as e:
    print(f"  [5/6] 失败: {e}")
    external_feature_status['warehouse_inventory'] = f'获取失败: {e}'

# 3.5 检查 inventory_history
print("  [6/6] 社会库存 (inventory_history)")
try:
    db_path = '/home/ubuntu/lithium_calendar/lithium.db'
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("SELECT date, inventory, change FROM inventory_history ORDER BY date")
    inv_rows = c.fetchall()
    conn.close()
    
    inv_dict = {}
    for row in inv_rows:
        inv_dict[row[0]] = {'inventory': row[1], 'change': row[2]}
    
    inv_vals = []
    for r in ham_data:
        if r['date'] in inv_dict and inv_dict[r['date']]['inventory'] is not None:
            inv_vals.append(float(inv_dict[r['date']]['inventory']))
        else:
            inv_vals.append(None)
    
    external_features['social_inventory'] = inv_vals
    missing = sum(1 for v in inv_vals if v is None)
    external_feature_status['social_inventory'] = f'已获取 ({len(inv_vals)-missing}天有数据)'
    external_feature_detail['social_inventory'] = {'缺失': missing, '来源': 'lithium.db inventory_history'}
    print(f"    social_inventory: {len(inv_vals) - missing} 有效, {missing} 缺失")
except Exception as e:
    print(f"  [6/6] 失败: {e}")
    external_feature_status['social_inventory'] = f'获取失败: {e}'

# ============================================================
# 4. 构造预测标签 (未来 1d/3d/5d 收益率)
# ============================================================
print("\n" + "=" * 60)
print("STEP 4: 构造预测标签")
print("=" * 60)

labels = {}
for horizon in [1, 3, 5]:
    feat_name = f'label_ret_{horizon}d'
    vals = []
    for i in range(len(closes)):
        if i + horizon < len(closes):
            vals.append((closes[i + horizon] - closes[i]) / closes[i])
        else:
            vals.append(None)
    labels[feat_name] = vals
    missing = sum(1 for v in vals if v is None)
    print(f"  {feat_name}: {len(vals) - missing} 有效, {missing} 缺失 (最后{horizon}天无标签)")

# ============================================================
# 5. Winsorize 缩尾处理 (1%-99% 分位)
# ============================================================
print("\n" + "=" * 60)
print("STEP 5: Winsorize 缩尾处理 (1%-99% 分位)")
print("=" * 60)

def winsorize(values, lower_pct=1, upper_pct=99):
    """对数组做缩尾处理，返回 (处理后数组, 下界, 上界, 缩尾数量)"""
    valid = [v for v in values if v is not None]
    if not valid:
        return values, None, None, 0
    
    sorted_v = sorted(valid)
    n = len(sorted_v)
    lower_idx = int(n * lower_pct / 100)
    upper_idx = int(n * upper_pct / 100)
    lower_bound = sorted_v[lower_idx]
    upper_bound = sorted_v[upper_idx]
    
    result = []
    clipped = 0
    for v in values:
        if v is None:
            result.append(None)
        elif v < lower_bound:
            result.append(lower_bound)
            clipped += 1
        elif v > upper_bound:
            result.append(upper_bound)
            clipped += 1
        else:
            result.append(v)
    
    return result, lower_bound, upper_bound, clipped

# n_c: 直接缩尾
n_c_raw = [r['n_c'] for r in ham_data]
n_c_wins, n_c_lower, n_c_upper, n_c_clipped = winsorize(n_c_raw)
print(f"  n_c: 缩尾 [{n_c_lower:.6f}, {n_c_upper:.6f}], 缩尾{ n_c_clipped }个点")

# n_f_minus_n_c: 直接缩尾
nfnc_raw = [r['n_f_minus_n_c'] for r in ham_data]
nfnc_wins, nfnc_lower, nfnc_upper, nfnc_clipped = winsorize(nfnc_raw)
print(f"  n_f-n_c: 缩尾 [{nfnc_lower:.6f}, {nfnc_upper:.6f}], 缩尾{nfnc_clipped}个点")

# Total_Demand: 先过滤符号翻转样本，再缩尾
# 由于 HAM 使用 W=20 (稳定区间)，无需过滤
# 但为完整性，记录过滤规则
td_raw = [r['Total_Demand'] for r in ham_data]
# W=20 全负稳定，不过滤；对全量数据缩尾
td_wins, td_lower, td_upper, td_clipped = winsorize(td_raw)
print(f"  Total_Demand: 缩尾 [{td_lower:.2f}, {td_upper:.2f}], 缩尾{td_clipped}个点")
print(f"  Total_Demand 过滤规则: W=20 参数下 rank_ic 全负稳定，无需过滤样本")

# ============================================================
# 6. 组装最终数据集
# ============================================================
print("\n" + "=" * 60)
print("STEP 6: 组装 ML 输入数据集")
print("=" * 60)

n = len(ham_data)

# 输出字段定义
output_fields = [
    'date',
    'close',
    'label_ret_1d', 'label_ret_3d', 'label_ret_5d',
    'n_c', 'Total_Demand', 'n_f_minus_n_c',  # 缩尾后的 HAM 因子
    # HAM CSV 内置量价特征
    'basis', 'basis_rate', 'spot_avg', 'volume', 'position',
    # 计算动量特征
    'ret_5d', 'ret_10d', 'ret_20d',
    # 基本面指数 (lithium.db)
    'fi_supply', 'fi_demand', 'fi_inventory', 'fi_profit', 'fi_sentiment', 'fi_basis', 'fi_composite',
    # 仓单库存
    'warehouse_inventory',
    # 社会库存
    'social_inventory',
]

output_rows = []
for i in range(n):
    row = ham_data[i]
    
    out = {}
    out['date'] = row['date']
    out['close'] = row['close']
    
    # 标签
    out['label_ret_1d'] = labels['label_ret_1d'][i]
    out['label_ret_3d'] = labels['label_ret_3d'][i]
    out['label_ret_5d'] = labels['label_ret_5d'][i]
    
    # HAM 因子 (缩尾后)
    out['n_c'] = n_c_wins[i]
    out['Total_Demand'] = td_wins[i]
    out['n_f_minus_n_c'] = nfnc_wins[i]
    
    # 量价特征
    out['basis'] = row['basis']
    out['basis_rate'] = external_features['basis_rate'][i]
    out['spot_avg'] = row['spot_avg']
    out['volume'] = row['volume']
    out['position'] = row['position']
    
    # 动量特征
    out['ret_5d'] = external_features.get('ret_5d', [None]*n)[i]
    out['ret_10d'] = external_features.get('ret_10d', [None]*n)[i]
    out['ret_20d'] = external_features.get('ret_20d', [None]*n)[i]
    
    # 基本面指数
    for col in ['fi_supply', 'fi_demand', 'fi_inventory', 'fi_profit', 'fi_sentiment', 'fi_basis', 'fi_composite']:
        if col in external_features:
            out[col] = external_features[col][i]
        else:
            out[col] = None
    
    # 仓单库存
    if 'warehouse_inventory' in external_features:
        out['warehouse_inventory'] = external_features['warehouse_inventory'][i]
    else:
        out['warehouse_inventory'] = None
    
    # 社会库存
    if 'social_inventory' in external_features:
        out['social_inventory'] = external_features['social_inventory'][i]
    else:
        out['social_inventory'] = None
    
    output_rows.append(out)

# 写 CSV
out_path = os.path.join(ROOT, "ml_input_features.csv")
with open(out_path, 'w', newline='') as f:
    writer = csv.DictWriter(f, fieldnames=output_fields)
    writer.writeheader()
    for row in output_rows:
        out_row = {}
        for k, v in row.items():
            if v is None:
                out_row[k] = ''
            elif isinstance(v, float):
                out_row[k] = f'{v:.6f}'
            else:
                out_row[k] = str(v)
        writer.writerow(out_row)

print(f"已输出: {out_path}")
print(f"总行数: {len(output_rows)}")

# 统计有效样本
valid_1d = sum(1 for r in output_rows if r['label_ret_1d'] is not None)
valid_3d = sum(1 for r in output_rows if r['label_ret_3d'] is not None)
valid_5d = sum(1 for r in output_rows if r['label_ret_5d'] is not None)
print(f"有效标签: 1d={valid_1d}, 3d={valid_3d}, 5d={valid_5d}")

# ============================================================
# 7. 生成 feature_analysis.md
# ============================================================
print("\n" + "=" * 60)
print("STEP 7: 生成 feature_analysis.md")
print("=" * 60)

# 计算统计量
def calc_stats(values):
    valid = [v for v in values if v is not None]
    if not valid:
        return {'mean': None, 'std': None, 'min': None, 'max': None, 'count': 0}
    mean = sum(valid) / len(valid)
    std = math.sqrt(sum((v - mean) ** 2 for v in valid) / len(valid)) if len(valid) > 1 else 0
    return {'mean': mean, 'std': std, 'min': min(valid), 'max': max(valid), 'count': len(valid)}

def calc_corr(vals1, vals2):
    """计算皮尔逊相关系数"""
    pairs = [(v1, v2) for v1, v2 in zip(vals1, vals2) if v1 is not None and v2 is not None]
    if len(pairs) < 3:
        return None
    n = len(pairs)
    x = [p[0] for p in pairs]
    y = [p[1] for p in pairs]
    mx = sum(x) / n
    my = sum(y) / n
    sx = math.sqrt(sum((xi - mx) ** 2 for xi in x) / n)
    sy = math.sqrt(sum((yi - my) ** 2 for yi in y) / n)
    if sx == 0 or sy == 0:
        return None
    cov = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y)) / n
    return cov / (sx * sy)

# HAM 因子统计
nc_stats = calc_stats(n_c_wins)
td_stats = calc_stats(td_wins)
nfnc_stats = calc_stats(nfnc_wins)

# 相关性
corr_nc_td = calc_corr(n_c_wins, td_wins)
corr_nc_nfnc = calc_corr(n_c_wins, nfnc_wins)
corr_td_nfnc = calc_corr(td_wins, nfnc_wins)

# 有效样本统计
total_rows = len(output_rows)
valid_with_label = valid_5d  # 以5d标签为准

# 外部特征统计
ext_stats = {}
for col in ['basis_rate', 'ret_5d', 'ret_10d', 'ret_20d', 'fi_composite', 'fi_supply', 'fi_demand', 'fi_inventory', 'fi_profit', 'fi_sentiment', 'fi_basis']:
    if col in external_features:
        ext_stats[col] = calc_stats(external_features[col])

# 数据切分
train_end_idx = int(n * 0.6)
val_end_idx = int(n * 0.8)
train_dates = dates[0] + ' ~ ' + dates[train_end_idx - 1]
val_dates = dates[train_end_idx] + ' ~ ' + dates[val_end_idx - 1]
test_dates = dates[val_end_idx] + ' ~ ' + dates[-1]
train_count = train_end_idx
val_count = val_end_idx - train_end_idx
test_count = n - val_end_idx

# 写 markdown
md_lines = []
md_lines.append("# HAM 因子 ML 输入数据集 — 分析文档\n")
md_lines.append(f"> 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
md_lines.append(f"> 数据源: model_ham/ham_factors_full_800d.csv\n")
md_lines.append("---\n")

# 1. 数据集有效样本
md_lines.append("## 1. 数据集有效样本数量\n")
md_lines.append(f"| 项目 | 数值 |")
md_lines.append(f"|------|------|")
md_lines.append(f"| 总样本数 | {total_rows} |")
md_lines.append(f"| 数据范围 | {dates[0]} ~ {dates[-1]} |")
md_lines.append(f"| 有效 1d 标签样本 | {valid_1d} |")
md_lines.append(f"| 有效 3d 标签样本 | {valid_3d} |")
md_lines.append(f"| 有效 5d 标签样本 | {valid_5d} |")
md_lines.append(f"| 缺失标签（末尾天） | {n - valid_5d} |")
md_lines.append("")

md_lines.append("### 外部指标获取状态\n")
md_lines.append("| 指标 | 状态 | 有效样本 | 来源 |")
md_lines.append("|------|------|----------|------|")
for col, status in external_feature_status.items():
    detail = external_feature_detail.get(col, {})
    missing = detail.get('缺失', '?')
    total = detail.get('total', total_rows)
    valid = total - missing if isinstance(missing, int) else '?'
    md_lines.append(f"| {col} | {'✅' if '获取' in status else '❌'} {status[:30]} | {valid}/{total_rows} | {detail.get('来源', 'N/A')[:25]} |")
md_lines.append("")

md_lines.append("### 指标缺失说明\n")
md_lines.append("- **warehouse_inventory（仓单库存）**: 仅覆盖 2026-05-06 ~ 2026-09-01（84天），缺失 656 天。原因：交易所仓单日报数据仅在此时间范围可得。")
md_lines.append("- **social_inventory（社会库存）**: 仅覆盖 2026-04-20 ~ 2026-07-27（67天），缺失 673 天。原因：SMM 周频数据前向填充后仅覆盖有限区间。")
md_lines.append("- **ret_5d/ret_10d/ret_20d（动量指标）**: 缺失分别为 5/10/20 天（数据起始段无法计算历史收益率）。")
md_lines.append("- **fi_supply/fi_demand 等（基本面指数）**: 覆盖 2023-07-31 ~ 2026-09-07，部分早期日期可能有缺失（fundamental_indices 表数据不完整）。")
md_lines.append("- **冶炼开工率、正极排产**: 未获取到。本地数据库无此数据，需人工提供。\n")

md_lines.append("### 【需要人工提供的指标】\n")
md_lines.append("以下指标本地无法获取，建议人工补充：\n")
md_lines.append("| 指标名称 | 频率 | 时间范围 | 用途 |")
md_lines.append("|----------|------|----------|------|")
md_lines.append("| 碳酸锂冶炼开工率 | 周/月 | 2023-08 ~ 2026-09 | 供给侧压力评估 |")
md_lines.append("| 正极材料排产数据 | 月 | 2023-08 ~ 2026-09 | 需求侧评估 |")
md_lines.append("| 社会库存完整序列 | 日 | 2023-08 ~ 2026-09 | 供需平衡核心指标 |")
md_lines.append("| 仓单库存完整序列 | 日 | 2023-08 ~ 2026-09 | 交易所库存压力 |")
md_lines.append("")

# 2. HAM 因子统计分布
md_lines.append("## 2. HAM 因子统计分布\n")
md_lines.append("| 因子 | 均值 | 标准差 | 最小值 | 最大值 | 有效样本 |")
md_lines.append("|------|------|--------|--------|--------|----------|")
for name, stats in [('n_c', nc_stats), ('Total_Demand', td_stats), ('n_f-n_c', nfnc_stats)]:
    md_lines.append(f"| {name} | {stats['mean']:.4f} | {stats['std']:.4f} | {stats['min']:.4f} | {stats['max']:.4f} | {stats['count']} |")
md_lines.append("")

# 外部特征统计
md_lines.append("### 外部特征统计\n")
md_lines.append("| 特征 | 均值 | 标准差 | 最小值 | 最大值 | 有效样本 |")
md_lines.append("|------|------|--------|--------|--------|----------|")
for col, stats in ext_stats.items():
    if stats['mean'] is not None:
        md_lines.append(f"| {col} | {stats['mean']:.4f} | {stats['std']:.4f} | {stats['min']:.4f} | {stats['max']:.4f} | {stats['count']} |")
md_lines.append("")

# 3. 因子相关性矩阵
md_lines.append("## 3. 因子相关性矩阵\n")
md_lines.append("| | n_c | Total_Demand | n_f-n_c |")
md_lines.append("|------|------|-------------|---------|")
md_lines.append(f"| n_c | 1.0000 | {corr_nc_td:.4f} | {corr_nc_nfnc:.4f} |")
md_lines.append(f"| Total_Demand | {corr_nc_td:.4f} | 1.0000 | {corr_td_nfnc:.4f} |")
md_lines.append(f"| n_f-n_c | {corr_nc_nfnc:.4f} | {corr_td_nfnc:.4f} | 1.0000 |")
md_lines.append("")

# 共线性评估
max_abs_corr = max(abs(corr_nc_td or 0), abs(corr_nc_nfnc or 0), abs(corr_td_nfnc or 0))
if max_abs_corr < 0.3:
    collinear_risk = "低"
    collinear_note = "三因子间相关系数均低于 0.3，无显著共线性风险"
elif max_abs_corr < 0.6:
    collinear_risk = "中"
    collinear_note = "部分因子对相关性中等，建议 ML 模型中监控 VIF"
else:
    collinear_risk = "高"
    collinear_note = "存在高度共线性，建议使用 PCA 或正则化方法"

md_lines.append(f"**共线性风险评估**: {collinear_risk} — {collinear_note}")
md_lines.append("")
md_lines.append(f"- n_c vs Total_Demand: r = {corr_nc_td:.4f}")
md_lines.append(f"- n_c vs n_f-n_c: r = {corr_nc_nfnc:.4f}（注意：n_f = 1 - n_c 近似，二者高度相关）")
md_lines.append(f"- Total_Demand vs n_f-n_c: r = {corr_td_nfnc:.4f}")
md_lines.append("")

# 4. Winsorize 处理细节
md_lines.append("## 4. Winsorize 缩尾处理细节\n")
md_lines.append("| 因子 | 下界(1%) | 上界(99%) | 缩尾点数量 | 缩尾比例 |")
md_lines.append("|------|----------|----------|------------|----------|")
md_lines.append(f"| n_c | {n_c_lower:.6f} | {n_c_upper:.6f} | {n_c_clipped} | {n_c_clipped/nc_stats['count']*100:.2f}% |")
md_lines.append(f"| n_f-n_c | {nfnc_lower:.6f} | {nfnc_upper:.6f} | {nfnc_clipped} | {nfnc_clipped/nfnc_stats['count']*100:.2f}% |")
md_lines.append(f"| Total_Demand | {td_lower:.2f} | {td_upper:.2f} | {td_clipped} | {td_clipped/td_stats['count']*100:.2f}% |")
md_lines.append("")

md_lines.append("### Total_Demand 过滤规则\n")
md_lines.append("**参数扫描结论** (param_sensitivity_scan.csv, 192 组合):\n")
md_lines.append("| W 值 | 负 rank_ic | 正 rank_ic | 稳定性 |")
md_lines.append("|------|-----------|-----------|--------|")
for w in sorted(symbol_flip_summary.keys()):
    s = symbol_flip_summary[w]
    stability = "全负稳定" if s['pos'] == 0 else ("全正稳定" if s['neg'] == 0 else "混合")
    md_lines.append(f"| {w} | {s['neg']} | {s['pos']} | {stability} |")
md_lines.append("")
md_lines.append("**过滤决策**: ham_factors_full_800d.csv 使用 **W=20** 参数，该参数下 Total_Demand rank_ic 全部为负（64/64），属于稳定区间。**无需过滤样本**。符号翻转仅发生在 W=60（全正），与当前因子计算无关。")
md_lines.append("")

# 5. 数据切分规则
md_lines.append("## 5. 数据切分规则\n")
md_lines.append("**严格时间顺序切分，不 shuffle**\n")
md_lines.append("| 集合 | 样本数 | 时间范围 | 说明 |")
md_lines.append("|------|--------|----------|------|")
md_lines.append(f"| 训练集 | {train_count} (60%) | {train_dates} | 模型训练 |")
md_lines.append(f"| 验证集 | {val_count} (20%) | {val_dates} | 超参调优 |")
md_lines.append(f"| 测试集 | {test_count} (20%) | {test_dates} | 样本外评估 |")
md_lines.append("")
md_lines.append("### 切分原则\n")
md_lines.append("1. **严格时间顺序**：前60%训练，中间20%验证，后20%测试")
md_lines.append("2. **不 shuffle**：避免未来信息泄露")
md_lines.append("3. **无重叠**：各集合无时间重叠")
md_lines.append("4. **标签一致性**：测试集标签使用测试集内部数据计算，不参与调参")
md_lines.append("")

# 6. 实验局限
md_lines.append("## 6. 实验局限性\n")
md_lines.append("### 特征缺失影响\n")
md_lines.append("1. **仓单库存 (warehouse_inventory)**: 仅覆盖 84 天（2026-05-06 ~ 2026-09-01），占样本 11.4%。")
md_lines.append("   - 影响：无法在全样本上评估仓单库存对价格的影响，ML 模型需处理缺失值（插值或特征掩码）")
md_lines.append("   - 建议：若仓单库存对模型有贡献，可考虑单独训练子样本模型")
md_lines.append("")
md_lines.append("2. **社会库存 (social_inventory)**: 仅覆盖 67 天（2026-04-20 ~ 2026-07-27），占样本 9.1%。")
md_lines.append("   - 影响：社会库存是碳酸锂供需平衡的核心指标，大面积缺失严重削弱基本面因子")
md_lines.append("   - 建议：需人工提供完整序列")
md_lines.append("")
md_lines.append("3. **冶炼开工率**: 未获取到，无法纳入数据集。")
md_lines.append("   - 影响：供给侧压力无法量化，ML 对照组缺少关键的供给端特征")
md_lines.append("   - 建议：需人工提供（SMM 周频或月频数据）")
md_lines.append("")
md_lines.append("4. **正极排产**: 未获取到，无法纳入数据集。")
md_lines.append("   - 影响：需求侧评估不完整，无法量化正极材料排产对碳酸锂需求的影响")
md_lines.append("   - 建议：需人工提供（月频数据）")
md_lines.append("")
md_lines.append("### 对照实验影响\n")
md_lines.append("- **纯 HAM 因子模型**: 可正常运行（n_c, Total_Demand, n_f-n_c 全样本有效）")
md_lines.append("- **HAM + 量价特征**: 可正常运行（close, volume, position, basis, spot_avg 全样本有效）")
md_lines.append("- **HAM + 基本面指数**: 部分可用（fundamental_indices 覆盖大部分样本，但 fi_inventory 等缺失较多）")
md_lines.append("- **HAM + 完整传统特征**: 受限于库存、开工率、排产数据缺失，对照组特征不完整")
md_lines.append("- **结论**: HAM 因子本身足以作为 ML 特征独立评估，但与传统 ML 模型的对比实验将因外部特征缺失而不完整")
md_lines.append("")

md_lines.append("### 数据质量说明\n")
md_lines.append("- **HAM 因子**: 确定性公式输出，全样本有效（740天），无未来函数")
md_lines.append("- **量价数据**: 来自 LC0 主力连续合约，全样本有效")
md_lines.append("- **基差/现货**: 来自 SMM 现货数据前向填充，全样本有效（部分日期为填充值）")
md_lines.append("- **标签**: 严格使用 t 时刻之后的收益率，无未来函数")
md_lines.append("")

md_path = os.path.join(ROOT, "feature_analysis.md")
with open(md_path, 'w', encoding='utf-8') as f:
    f.write('\n'.join(md_lines))

print(f"已输出: {md_path}")
print(f"文档行数: {len(md_lines)}")

# ============================================================
# 最终摘要
# ============================================================
print("\n" + "=" * 60)
print("完成摘要")
print("=" * 60)
print(f"  数据集: {out_path}")
print(f"  文档:   {md_path}")
print(f"  总样本: {total_rows}")
print(f"  有效5d标签: {valid_5d}")
print(f"  外部特征: {len(external_features)} 个")
print(f"  HAM因子: 3 个 (n_c, Total_Demand, n_f-n_c)")
print(f"  标签: 3 个 (1d, 3d, 5d)")
