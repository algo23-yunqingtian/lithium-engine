import pandas as pd
import numpy as np

nav = pd.read_csv('/home/ubuntu/lithium-engine/model_ham/exp412_risk_control/data/exp412_daily_equity.csv', index_col=0)
nav.index = pd.to_datetime(nav.index)

exp411_pos = nav['exp411_pos'].values
exp412_pos = nav['exp412_pos'].values
close = nav['close'].values
fund_state = nav['fund_state'].values
ham_mult = nav['ham_mult'].values

# 被压制HAM天数的收益
suppressed_idx = np.where(ham_mult < 1.0)[0]
exp411_ret_on_suppressed = 0
exp412_ret_on_suppressed = 0
n_ham_active_suppressed = 0
n_win = 0
n_loss = 0

for i in suppressed_idx:
    if i > 0:
        r_p = (close[i] - close[i-1]) / close[i-1] if close[i-1] > 0 else 0
        r411 = exp411_pos[i] * r_p
        r412 = exp412_pos[i] * r_p
        exp411_ret_on_suppressed += r411
        exp412_ret_on_suppressed += r412
        if abs(exp411_pos[i]) > 1e-9 and abs(exp412_pos[i]) < abs(exp411_pos[i]) * 0.9:
            n_ham_active_suppressed += 1
            if r411 > 0:
                n_win += 1
            else:
                n_loss += 1

print("=== 被压制HAM天数的收益 ===")
print(f"  exp411基线收益: {exp411_ret_on_suppressed:.4f}")
print(f"  exp412风控收益: {exp412_ret_on_suppressed:.4f}")
print(f"  压制节省: {exp411_ret_on_suppressed - exp412_ret_on_suppressed:.4f}")
print(f"  HAM仓位实际减少的天数: {n_ham_active_suppressed}")
print(f"    基线盈利: {n_win}, 亏损: {n_loss}")

print()
print("=== 按基本面状态分: HAM持仓日的收益 ===")
ham_active = np.abs(exp411_pos) > 1e-9
for state in ['strong_bull','strong_bear','weak_bull','weak_bear','neutral']:
    mask_state = ham_active & (fund_state == state)
    if mask_state.sum() == 0:
        continue
    rets = []
    for i in np.where(mask_state)[0]:
        if i > 0:
            r_p = (close[i] - close[i-1]) / close[i-1] if close[i-1] > 0 else 0
            rets.append(exp411_pos[i] * r_p)
    rets = np.array(rets)
    wins = (rets > 0).sum()
    losses = (rets <= 0).sum()
    print(f"  {state:<14}: {len(rets):>4}天, 总收益={rets.sum():.4f}, 胜率={wins/(wins+losses):.1%} (盈{wins}/亏{losses})")

print()
print("=== 回撤控制效果 ===")
dd_control = nav['dd_control'].values
dd_triggered_idx = np.where(dd_control < 1.0)[0]
print(f"  回撤触发天数: {len(dd_triggered_idx)}")
dd_ret_411 = 0
dd_ret_412 = 0
for i in dd_triggered_idx:
    if i > 0:
        r_p = (close[i] - close[i-1]) / close[i-1] if close[i-1] > 0 else 0
        dd_ret_411 += exp411_pos[i] * r_p
        dd_ret_412 += exp412_pos[i] * r_p
print(f"  触发日基线收益: {dd_ret_411:.4f}")
print(f"  触发日风控收益: {dd_ret_412:.4f}")
print(f"  节省: {dd_ret_411 - dd_ret_412:.4f}")

print()
print("=== 波动控制效果 ===")
vol_control = nav['vol_control'].values
vol_triggered_idx = np.where(vol_control < 1.0)[0]
print(f"  波动触发天数: {len(vol_triggered_idx)}")
vol_ret_411 = 0
vol_ret_412 = 0
for i in vol_triggered_idx:
    if i > 0:
        r_p = (close[i] - close[i-1]) / close[i-1] if close[i-1] > 0 else 0
        vol_ret_411 += exp411_pos[i] * r_p
        vol_ret_412 += exp412_pos[i] * r_p
print(f"  触发日基线收益: {vol_ret_411:.4f}")
print(f"  触发日风控收益: {vol_ret_412:.4f}")
print(f"  节省: {vol_ret_411 - vol_ret_412:.4f}")

print()
print("=== 关键诊断: 基线回撤来自哪里? ===")
# 找基线净值最低点
base_eq = nav['exp411_equity'].values
peak = np.maximum.accumulate(base_eq)
dd = (base_eq - peak) / peak
min_dd_idx = np.argmin(dd)
print(f"  基线最大回撤点: {nav.index[min_dd_idx].date()}, 回撤={dd[min_dd_idx]:.3f}")
# 回撤前后的基金状态
start_dd = max(0, min_dd_idx - 10)
for i in range(start_dd, min_dd_idx+5):
    print(f"  {nav.index[i].date()}: 基线仓位={exp411_pos[i]:.2f}, 基本面状态={fund_state[i]}, 波动={nav['vol_control'][i]:.2f}")
