# HAM 代码与实验逻辑审计报告（exp426 / 427-1 / 428 / 429）

> 审计时间：2026-09-10
> 审计范围：`model_ham/exp429_production/`（3 文件）+ exp426/427-1/428 实验引擎核心 + exp424/425/409 底座
> 审计方法：静态代码审查 + 实际运行流水线核对 + 边界/溢出/前视验证
> **总体判定：✅ 审计通过（无致命 bug，时序铁律成立，绩效与文档一致）。发现 4 项非致命改进项（见 §5）。**

---

## 0. 一句话结论

HAM 实盘工程化代码（exp429）**时序无前视、绩效与报告文档完全一致、滚动/一次性两套逻辑同源、极端场景无数值溢出**。实盘运行 `run_daily.py` 实测配置A 年化34.0%/Calmar4.98/回撤-6.8%/笔数135/IC+0.219/持仓232日、配置B 年化37.4%/Calmar3.55/回撤-10.5%/笔数132/IC+0.241/持仓232日，**与报告 §3 一字不差**，回撤与 exp426 锚点完全一致（验证 ADX 降权是唯一变量）。**可进入实盘。**

---

## 1. 代码层面检查

### 1.1 核心脚本关键片段

#### exp429 生产流水线（3 文件）

**ham_pipeline.py — 分歧 D 因子序列（滚动估参编排）**
```python
def build_disagreement_series():
    """滚动版分歧 D 因子序列。严格无前视: 每日 T 只用 [T-180, T) 历史拟合 alpha/beta。"""
    df_h = E409.load_ham_factors()
    df_h = rolling_deviation(df_h)        # 滚动重估参 (exp428)
    df_h = E409.compute_aux_signals(df_h)
    df_h = E409.load_gmm_states(df_h)
    df_h = E409.compute_ham_signals(df_h)
    df_h, _ = E409.run_ham_dynamic(df_h)
    return df_h
```
> 关键：核心库**只做编排**，滚动估参 `rolling_deviation`、信号链 `compute_aux_signals/load_gmm_states/compute_ham_signals/run_ham_dynamic` 全部 import 复用，**零底层重写**。

**ham_pipeline.py — 两套仓位（可开关 ADX 降权）**
```python
def compute_position_config(ctx, h_target, adx_scale):
    ctx_mod = dict(ctx)
    ctx_mod["h_target"] = h_target.reindex(ctx["idx"]).fillna(0.0)
    pos, _ = compute_positions(ctx_mod, VOL_HALF, VOL_FULL, HALF_SCALE, FULL_SCALE,
                               DD_RECOVER, DD_WARN, DD_HARD)
    high = ohlc["high"].reindex(idx).values; low = ohlc["low"].reindex(idx).values
    close = price.reindex(idx).values
    adx = compute_adx(high, low, close, period=14)
    if adx_scale < 1.0:                              # 配置A: adx_scale=0.3
        adx_mask = adx > ADX_THRESHOLD               # ADX>30
        pos[adx_mask] = pos[adx_mask] * adx_scale    # 强趋势日仓位×0.3
    return pos
```

**ham_pipeline.py — 动作翻译**
```python
def position_to_action(pos, pos_prev):
    for p, pv in zip(pos, pos_prev):
        if abs(p) < 1e-9 and abs(pv) < 1e-9:  actions.append("HOLD_FLAT")
        elif abs(p) < 1e-9:                   actions.append("CLOSE")
        elif abs(pv) < 1e-9:                  actions.append("OPEN_LONG" if p>0 else "OPEN_SHORT")
        elif np.sign(p) != np.sign(pv):        actions.append("REVERSE")
        elif abs(p) > abs(pv) + 1e-9:          actions.append("INCREASE")
        elif abs(p) < abs(pv) - 1e-9:          actions.append("DECREASE")
        else:                                  actions.append("HOLD")
```

**monitoring.py — 因子 IC 滚动时序（无前视关键）**
```python
def compute_ic_series(ctx, pos, window=60):
    ret = np.zeros(n); ret[1:] = (close[1:]-close[:-1])/close[:-1]
    lab = np.zeros(n); lab[:-1] = ret[1:]        # 次日收益前移到 t
    sig_shift = np.zeros(n); sig_shift[:-1] = sig[1:]  # pos[t] 预测 lab[t]=ret[t+1]
    for t in range(window, n):
        s = sig_shift[t-window:t]; l = lab[t-window:t]
        m = np.abs(s) > 1e-9
        if m.sum() >= 5 and np.std(l[m]) > 0:     # std>0 防 corrcoef 除零
            ic.iloc[t] = np.corrcoef(s[m], l[m])[0,1]
    return ic
```
> **时序正确**：`lab[t]=ret[t+1]`（次日收益），`sig_shift[t]=pos[t+1]`——即 pos[t+1]（T+1 已知决策）预测 ret[t+1]，滚动窗口取 `[t-window, t)` 历史，**不含 t 及之后的未来数据**。无 look-ahead。

**monitoring.py — 漂移告警判定（三级）**
```python
IC_WARN, IC_CRIT = 0.05, 0.0
DEV_MED_WARN, DEV_MED_CRIT, DEV_MED_LO = 0.95, 1.05, 0.25
CV_WARN, CV_CRIT = 0.50, 0.80
for _, r in merged.iterrows():
    if pd.notna(r["IC_roll"]):
        if r["IC_roll"] < IC_CRIT: flags.append("IC_CRIT")
        elif r["IC_roll"] < IC_WARN: flags.append("IC_WARN")
    if pd.notna(r["dev_median"]):
        if r["dev_median"] > DEV_MED_CRIT or r["dev_median"] < DEV_MED_LO*0.9: flags.append("DEV_CRIT")
        elif r["dev_median"] > DEV_MED_WARN or r["dev_median"] < DEV_MED_LO: flags.append("DEV_WARN")
    cv = max(acv, bcv)
    if cv > CV_CRIT: flags.append("CV_CRIT")
    elif cv > CV_WARN: flags.append("CV_WARN")
    level = "CRITICAL" if has_crit else ("WARNING" if has_warn else "OK")
```

**run_daily.py — 绩效验证（9:01 + 10bp）**
```python
def run_validation(ctx, df_ham):
    _, p901_px, _ = build_901_exec_prices(ctx, slippage_cal, vol_scale=True, seed=RNG_SEED)
    h_target = HP.ham_target_from(df_ham)
    for ckey, cfg in CONFIGS.items():
        pos = HP.compute_position_config(ctx, h_target, cfg["adx_scale"])
        nr, mi, tr = run_variant_full(ctx, pos, p901_px, slip_bp=MAIN_SLIP_BP, gap_filter_thr=0.0)
        # IC: pos[1:] 对 r[1:] (T日仓位吃T日收益, 无前视)
        sig = pos[1:]; lab = r[1:]; m = np.abs(sig)>1e-9
        ic = float(np.corrcoef(sig[m], lab[m])[0,1]) if m.sum()>2 else 0.0
```

#### exp428 — 滚动估参核心（前视关键）
```python
def fit_alpha_beta(close_hist, p_fund_hist, n_alpha=11, n_beta=11):
    """目标: 使 sign(D_c-D_f) 预测"次日收益符号" 误分类最少。严格只用 close_hist/p_fund_hist(T日及更早)。"""
    ret_sign = np.zeros(n); ret_sign[1:-1] = np.sign(close_hist[2:] - close_hist[1:-1])  # 次日收益符号
    dc = np.zeros(n); dc[1:] = np.diff(close_hist)
    df_raw = p_fund_hist - close_hist
    valid = (ret_sign != 0); idx_valid = np.where(valid)[0]
    for a in alphas:
        df_scaled = a * df_raw
        for b in betas:
            dc_scaled = b * dc
            pred = np.sign(dc_scaled - df_scaled)
            err = (pred[idx_valid] != ret_sign[idx_valid]).sum()
            if err < best_err: best_err = err; best_a, best_b = a, b
    return best_a, best_b, status

def rolling_deviation(df_ham, est_window=EST_TRAIN_WINDOW):  # EST_TRAIN_WINDOW=180
    for t in range(n):
        lo = max(0, t - est_window)          # 估参窗口 [t-180, t) 严格不含 t 自身
        ch = close[lo:t]; ph = p_fund[lo:t]
        if t >= EST_MIN_PERIODS and len(ch) >= EST_MIN_PERIODS:  # 60日预热
            a, b, _ = fit_alpha_beta(ch, ph)
        else:
            a, b = ALPHA_FIXED, BETA_FIXED   # 预热期固定参数
        D_f[t] = a * (p_fund[t] - close[t])
        D_c[t] = (b * (close[t] - close[t-1])) if t >= 1 else 0.0
    # 原版 Logit 自适应标准化 (rolling rank, 无前视)
    dcn = pd.Series(D_c).rolling(ROLLING_TRAIN, min_periods=60).rank(pct=True).values * 2 - 1
    dfn = pd.Series(D_f).rolling(ROLLING_TRAIN, min_periods=60).rank(pct=True).values * 2 - 1
    df["disagreement"] = dcn - dfn; df["deviation"] = np.abs(dcn - dfn)
```

#### exp426 — 消融核心（只改偏离度，其余沿用原版）
```python
def compute_system_deviation_ablation(df, fixed_weights=False, drop_industrial=False, drop_speculative=False):
    D_f = np.zeros(n) if drop_industrial else alpha * (p_fund - close)   # 消融B: D_f置0
    D_c = np.zeros(n)
    if not drop_speculative: D_c[1:] = beta * np.diff(close)              # 消融C: D_c置0
    if fixed_weights:   # 消融A: 关Logit自适应, expanding z-score(仅T及更早, 无前视)
        def _expanding_zscore(arr):
            s = pd.Series(arr); mu = s.expanding(min_periods=60).mean(); sd = s.expanding(min_periods=60).std()
            z = (s - mu) / sd.replace(0, np.nan); return z.bfill().fillna(0).values
        dcn = _expanding_zscore(D_c) * 2 - 1; dfn = _expanding_zscore(D_f) * 2 - 1
    else:   # 原版: 180日滚动 rank 自适应
        dcn = pd.Series(D_c).rolling(ROLLING_TRAIN, min_periods=60).rank(pct=True).values * 2 - 1
        dfn = pd.Series(D_f).rolling(ROLLING_TRAIN, min_periods=60).rank(pct=True).values * 2 - 1
```

#### exp427-1 — 极端识别
```python
def detect_extreme_segments(ctx):
    pct20 = pd.Series(close).pct_change(20).values
    vol20 = pd.Series(ret).rolling(20, min_periods=10).std().values * np.sqrt(TDPY)
    extreme = ((np.abs(pct20) > MOM20_THR) | (vol20 > med_vol * VOL_MULT) | (abs_ret > GAP_THR))
    # 连续段 >=3日成段
```

#### exp424 — 风控底座 + 版本B净收益（口径关键）
```python
def compute_positions(ctx, ...):
    pos_risk, diag = E413.run_exp412_param(h_target, f_target, idx, price, dd_recover, dd_warn, dd_hard, cost=COST, return_diag=True)
    for t in range(n):
        v = vol_arr[t]
        if v > vol_full: overnight_mult[t] = full_scale    # 波动率>0.90 -> 0.0
        elif v > vol_half: overnight_mult[t] = half_scale  # 波动率>0.40 -> 0.5
    final_pos = pos_risk * overnight_mult * trend_mult
    return final_pos, diag

def net_ret_variant_B(final_pos, price, ohlc, idx):
    """版本B: T+1开盘成交。隔夜跳空由pos[t-1]承担, 日内由pos[t]承担"""
    overnight_ret[t] = final_pos[t-1] * (open_px[t] - close[t-1]) / close[t-1]
    intraday_ret[t]  = final_pos[t]   * (close[t]     - open_px[t]) / open_px[t]
    cost_ret = 2 * COST * dpos
    return overnight_ret + intraday_ret - cost_ret

def compute_adx(high, low, close, period=14):
    """Wilder ADX。预热期(前2*period=28日)ADX=0。"""
```

#### exp425 — 9:01 统计外推成交 + 滑点
```python
def build_901_exec_prices(ctx, slippage_cal, vol_scale=True, seed=RNG_SEED):
    """9:01均价 = 日K开盘 × (1 + d_t), d_t 从真实偏离池抽样后按当日20日波动率缩放。"""
    rng = np.random.default_rng(seed)      # 固定种子 20260910
    vol_med = np.median(vol[...])
    for t in range(n):
        d = float(pool[rng.integers(0, len(pool))])
        if vol_scale: d = d * (vol[t] / vol_med)
        p901_px[t] = open_px[t] * (1 + d / 100.0)

def net_ret_variant_B_exec(final_pos, close_price, exec_open_px, close_px_ref, idx, slip_bp=0.0, gap_filter_thr=0.0):
    slip = slip_bp / 10000.0               # bp -> 小数
    overnight_ret[t] = final_pos[t-1] * (exec_open_px[t] - close[t-1]) / close[t-1]
    intraday_ret[t]  = pos[t] * (close[t] - exec_open_px[t]) / exec_open_px[t]
    cost_ret = (2 * COST + 2 * slip) * dpos  # 换手成本 + 滑点(开/平各一次)
```

### 1.2 风险点逐项排查

| # | 风险点 | 排查结论 | 证据 |
|:---:|:---|:---|:---|
| 1 | **时间对齐（look-ahead）** | ✅ **无前视** | 滚动估参用 `[t-180, t)` 严格不含 t；`fit_alpha_beta` 标签 `ret_sign[1:-1]=sign(close[2:]-close[1:-1])` 用 T-1 日已知信息；IC 用 `pos[t+1]` 预测 `ret[t+1]` 滚动取历史窗口；净收益版本B 隔夜由 pos[t-1] 承担。全程 T 日决策→T+1 成交。 |
| 2 | **滚动重估窗口边界** | ✅ 正确 | `lo=max(0, t-est_window)`；`ch=close[lo:t]`（切片右开，不含 t）；预热期 `t<60` 用固定参数。实测原始 df_ham 720行前60日 alpha=0.3/beta=0.5，第60日起拟合。 |
| 3 | **rolling.rank(pct) 标准化** | ✅ 正确 | `pd.Series(D).rolling(180, min_periods=60).rank(pct=True)*2-1`——窗口右闭（含 T 日及更早），min_periods=60 防窗口不足偷用未来。rank 只依赖相对次序、与线性缩放绝对值无关，α/β 数值噪声被吸收不传导信号层（exp428 核心洞察，已验证）。 |
| 4 | **Logit 动态权重更新** | ✅ 正确 | 每日用 `[T-180,T)` 网格搜索 α∈[0.10,0.60]/β∈[0.30,0.80]（11×11）最小化误分类数。实测 α∈[0.10,0.55]、β∈[0.30,0.50] 落在网格内，无越界。拟合解空间非事后调参。 |
| 5 | **9:01 分钟均价成交** | ⚠️ **统计外推非真实值** | 真实分钟数据仅 4 交易日（标定 mean_dev=+0.039%/std=0.317%），全期统计外推（固定种子20260910，按波动率缩放）。实测外推偏离范围[-1.50%,+1.19%]，均值+0.04%，无极端日（|偏离|>3%为0日）。**文档已标记为诚实局限 §6.1**。 |
| 6 | **滑点实现** | ✅ 正确 | `cost_ret=(2*COST+2*slip)*dpos`，滑点开/平各叠加一次按持仓变动量计。COST=0.0015（单边0.15%），slip=10bp=0.001。公式与 skill 沉淀一致。 |
| 7 | **ADX 降权仓位修改** | ✅ 正确 | `if adx_scale<1.0: pos[adx>30]*=0.3`。实测 ADX 预热期(前28日)=0 故不降权（正确）；全样本 ADX∈[0,40.09]，>30日93天、>45日0天（配置B禁止红线 R6 不触发）。配置A/B 唯一差异即 ADX_SCALE，回撤-6.8%/-10.5% 与 exp426 锚点完全一致，**验证 ADX 是唯一变量**。 |
| 8 | **多空信号生成** | ✅ 正确 | `signal_dir=where(disagreement>0, +1, -1)`（D_c动量主导→多，D_f回归主导→空）；`main_trigger=(deviation>q_med)&q_med.notna()`；`open_signal=main_trigger & aux_confirm`（双信号共振）；`aux_confirm=vol_anomaly | td_flip`。所有分位用滚动180日窗口无前视。 |

---

## 2. 实验假设与文档一致性检查

### 2.1 HAM 核心假设清单 → 代码实现位置

| # | 核心假设 | 代码位置 | 实现 |
|:---:|:---|:---|:---|
| H1 | 投机主体追涨（动量） | exp409/exp428 `D_c=beta*(P_t-P_{t-1})` | 投机动量需求 |
| H2 | 产业主体回归（基本面压力） | exp409/exp428 `D_f=alpha*(P_fund-P_t)` | 产业回归压力 |
| H3 | 分歧=市场失衡 | `disagreement=dcn-dfn`, `deviation=\|dcn-dfn\|` | 动量与回归背离=失衡 |
| H4 | Logit 自适应资金跟随 | exp428 `rolling(180).rank(pct)` | 滚动排名标准化 |
| H5 | 双信号共振开仓 | exp409 `open_signal=main_trigger & aux_confirm` | 主触发+辅助确认 |
| H6 | GMM 状态跃迁平仓（定性） | exp409 `load_gmm_states`(分位带代理) | 仅定性约束不预测 |
| H7 | ADX 高趋势降权 | exp424/exp426/429 `pos[adx>30]*=0.3` | 强趋势日仓位×0.3 |
| H8 | 版本B时序（隔夜pos[t-1]承担） | exp424/425 `net_ret_variant_B*` | T日决策→T+1成交 |
| H9 | 9:01 分钟均价成交 | exp425 `build_901_exec_prices` | 统计外推(诚实局限) |
| H10 | 滚动每日估参无前视 | exp428 `rolling_deviation` | `[T-180,T)`历史拟合 |

### 2.2 报告绩效指标 vs 代码计算逻辑核对

实盘运行 `run_daily.py` 实测输出（exit=0）：

| 指标 | 配置A 报告 | 配置A 实测 | 配置B 报告 | 配置B 实测 | 一致 |
|:---|:---:|:---:|:---:|:---:|:---:|
| 年化收益 | 34.0% | 34.0% | 37.4% | 37.4% | ✅ |
| 夏普 | 2.293 | 2.293 | 2.212 | 2.212 | ✅ |
| 最大回撤 | -6.8% | -6.8% | -10.5% | -10.5% | ✅ |
| Calmar | 4.98 | 4.98 | 3.55 | 3.55 | ✅ |
| 笔数 | 135 | 135 | 132 | 132 | ✅ |
| 日度IC | +0.219 | +0.219 | +0.241 | +0.241 | ✅ |
| 持仓日 | 232 | 232 | 232 | 232 | ✅ |

**回撤与 exp426 一次性锚点完全一致**（配置A -6.8% / 配置B -10.5%），验证 ADX 降权是唯一变量、滚动估参只提升收益不放大风险。年化高于 exp426 一次性锚点（28.9%/32.1%）源于 exp428 滚动估参优势（+5.2pp）。**全部匹配。**

> 备注：`CONFIGS` 字典内 `ref_annual=0.289/ref_calmar=4.23/ref_trades=129` 等是 **exp426 一次性锚点**（用于对齐验证），非滚动实测值；代码运行输出明确区分「实测」与「锚定(exp426)」两行，逻辑清晰无混淆。

### 2.3 一次性回测 vs 滚动影子盘：是否同源？

**✅ 同源。两套逻辑共用同一信号链，仅分歧 D 因子构造方式不同：**

| 维度 | 一次性（exp426/exp409 原版） | 滚动影子盘（exp428/429） | 是否同源 |
|:---|:---|:---|:---:|
| D 因子构造 | `E409.compute_system_deviation`（固定 α=0.3/β=0.5） | `rolling_deviation`（每日 `[T-180,T)` 拟合） | 同一公式，参数来源不同 |
| 标准化 | `rolling(180).rank(pct)` | 同 `rolling(180).rank(pct)` | ✅ 完全同源 |
| aux/gmm/signals | `compute_aux_signals/load_gmm_states/compute_ham_signals` | **同 import 原版** | ✅ 完全同源 |
| 持仓引擎 | `run_ham_dynamic` | **同 import 原版** | ✅ 完全同源 |
| 风控底座 | `compute_positions`(exp412) | **同 import 原版** | ✅ 完全同源 |
| 成交口径 | `run_variant_full`(版本B+9:01+10bp) | **同 import 原版** | ✅ 完全同源 |
| ADX 降权 | `compute_positions_ablation` | `compute_position_config`(同公式) | ✅ 同公式 |

**结论：一次性回测与滚动影子盘是「同一套实现，仅 α/β 参数来源不同（固定 vs 滚动拟合）」，不存在两套不同实现。** exp428 已用方向一致率100%/Jaccard0.931/净值相关0.995 证明两者信号高度一致。

### 2.4 已知模型局限（文档 + 代码双向标记核对）

| # | 局限 | 文档标记 | 代码标记 |
|:---:|:---|:---|:---|
| 1 | 9:01 成交价为统计外推（非真实逐日值） | 报告 §6.1 ✅ | `build_901_exec_prices` docstring「全回测期采用统计外推」✅ |
| 2 | 滚动估参年化优势未样本外独立验证 | 报告 §6.2 ✅ | — （exp428 影子盘已部分验证，但仍是离线） |
| 3 | 告警阈值为分位校准（非统计检验） | 报告 §6.3 ✅ | `judge_drift` 注释「全样本分位校准」✅ |
| 4 | P_fund 产业锚沿用代理 | 报告 §6.4 ✅ | — （沿用 exp410 代理逻辑） |
| 5 | 持续影子盘为离线模拟（未接真实撮合） | 报告 §6.5 ✅ | — |

**全部 5 项局限文档与代码均有标记，诚实性充分。**

---

## 3. 潜在 bug / 风险枚举

### 3.1 潜在 bug / 边界异常枚举

| # | 项 | 严重度 | 说明 | 状态 |
|:---:|:---|:---:|:---|:---|
| B1 | `monitoring.compute_ic_series` 运行时 `RuntimeWarning: invalid value in divide`（corrcoef 分母为0） | 低 | 滚动60日窗口内当持仓日收益全为0时 std=0，`np.corrcoef` 触发除零。代码已用 `np.std(l[m])>0` 前置过滤（实测被跳过窗口=0），但警告仍打印。**不影响结果**（IC 有效均值0.238正确）。 | ⚠️ 改进：用 `np.errstate` 屏蔽或过滤前置于 corrcoef |
| B2 | 滚动 IC 预热后有 79 个 NaN 日 | 低 | `compute_ic_series` 要求 `m.sum()>=5`（窗口内≥5持仓日），HAM 低频换手（232日持仓/628日=37%）导致部分60日窗口持仓不足5日→IC 跳过→NaN。**合理**（数据不足不强算），告警分布统计已排除预热段。 | ⚠️ 文档可注明「IC 覆盖非全样本」 |
| B3 | `run_pipeline` 中 `position_to_action` 被调用两次 | 低 | 第187行先用 `pos_prev_zero` 算 actions，第189-190行立即用 `prev=r_[0.0,pos[:-1]]` 重算覆盖。第187行是冗余死代码，结果正确但浪费计算。 | ⚠️ 改进：删除第187行死代码 |
| B4 | `run_daily.run_validation` 的 IC 与 `monitoring.compute_ic_series` 口径不同 | 低 | `run_validation` IC 用 `pos[1:]` 对 `r[1:]`（全样本一次性 Pearson，非滚动）；`monitoring` 用滚动60日窗口。两者都是无前视，但**报告 §3 的「日度IC +0.219」是全样本一次性口径**，与 §4.1 的「IC均值0.238」是滚动口径——两个数字来源不同，报告已分节标注。 | ⚠️ 文档可统一口径说明 |
| B5 | ADX 预热期（前28日）ADX=0 | 已处理 | 预热期 `adx>30` 为 False 不降权。配置A 在前28日维持满仓——但此段也是 HAM 信号预热段（信号本身可能弱），实际影响极小。逻辑正确。 | ✅ 无问题 |
| B6 | `rolling_deviation` 预热期（前60日）用固定参数 | 已处理 | 前60日 α=0.3/β=0.5。输出表（2024-02起）已切片到预热段之后，故流水线输出无固定参数段——**正确**（实测原始 df_ham 720行前60日确为固定参数，输出628行全是拟合值）。 | ✅ 无问题 |

**无致命 bug。B1/B2/B3/B4 均为低严重度改进项，不影响实盘正确性。**

### 3.2 关键校验结果：信号方向一致率 / Jaccard / 净值相关性计算代码

来源：`exp428_engine.py` 模块3（滚动 vs 一次性信号一致性对比）：
```python
# 持仓日重合度
hold_on = np.abs(pos_oneshot) > 1e-9
hold_roll = np.abs(pos_roll) > 1e-9
both_hold_mask = hold_on & hold_roll          # 布尔掩码 (非标量!)
both_hold = int(both_hold_mask.sum())
union = int((hold_on | hold_roll).sum())
jaccard = both_hold / union if union > 0 else 0

# 方向一致性 (同持仓日)
same_dir = int((both_hold_mask & (np.sign(pos_oneshot) == np.sign(pos_roll))).sum())
dir_agree = same_dir / both_hold if both_hold > 0 else 0

# 净值序列相关性
eq_on = np.cumprod(1 + nr_on); eq_roll = np.cumprod(1 + nr_roll)
eq_corr = float(np.corrcoef(eq_on, eq_roll)[0, 1])
```
> **关键正确性**：`both_hold_mask = hold_on & hold_roll` 用**布尔数组**（非 `.sum()` 后标量整数），避免整数 `&` 得错误结果（skill 记录曾因此误报方向一致率0%实为100%）。**代码正确。**

**exp428 实测结果**（skill 沉淀 + 报告）：方向一致率 **100%**、Jaccard **0.931**、净值相关 **0.995**。稳定性判定 `sig_stable = (dir_agree>0.95) and (jaccard>0.85) and (eq_corr>0.98)` → **STABLE**。

### 3.3 极端行情场景下权重/仓位数值溢出检查

实测验证结果：

| 检查项 | 结果 | 判定 |
|:---|:---|:---:|
| 配置A 仓位范围 | [-1.0000, 1.0000]，nan=0，inf=0 | ✅ 无溢出 |
| 配置B 仓位范围 | [-1.0000, 1.0000]，nan=0，inf=0 | ✅ 无溢出 |
| alpha_t 范围 | [0.1000, 0.5500]，落在网格(0.10,0.60)内 | ✅ 无越界 |
| beta_t 范围 | [0.3000, 0.5000]，落在网格(0.30,0.80)内 | ✅ 无越界 |
| deviation 范围 | [0.0000, 1.9889]，rank标准化后应在[0,2] | ✅ 正常 |
| ADX 范围 | [0.00, 40.09]，>30日93天、>45日0天 | ✅ 无极端 |
| 9:01 外推偏离 | [-1.50%, +1.19%]，均值+0.04%，\|偏离\|>3%日=0 | ✅ 无极端放大 |
| metrics 年化计算 | `ann=(1+total)^(1/years)-1`，total>-1 时保护；max_dd<0 时 calmar 保护 | ✅ 有除零/负数保护 |

**极端场景无数值溢出、无极端不合理值。** exp427-1 极端压力测试已独立验证（极端段日年化50.8%、50bp滑点仍+11.7%、最坏回撤-1.3%），与本次实测一致。

---

## 4. 实盘数值核对汇总

实盘运行 `run_daily.py`（exit=0，2.8秒）实测输出，与报告完全一致：
```
[流水线] 2024-02-01 ~ 2026-09-07 (628 日), 输出 628 行
配置A_稳健: 年化=34.0% 夏普=2.293 回撤=-6.8% Calmar=4.98 笔数=135 IC=+0.219 持仓日=232
配置B_激进: 年化=37.4% 夏普=2.212 回撤=-10.5% Calmar=3.55 笔数=132 IC=+0.241 持仓日=232
漂移告警(2024-08起): OK=342 WARNING=101 CRITICAL=67 (占比 67%/20%/13%)
IC滚动: 均值=0.2376 P10=-0.0365 正占比=85.7%
影子盘: 628 日, 配置A活跃232日 配置B活跃232日
```

---

## 5. 审计结论与改进建议

### 5.1 总体判定

**✅ 审计通过。HAM 实盘工程化代码时序无前视、绩效与文档一致、两套逻辑同源、极端场景无溢出。可进入实盘（PRODUCTION_READY）。**

- 时序铁律（H8/H10）成立：全程 T 日决策→T+1 成交，滚动估参严格用历史。
- 绩效一致性 100%：7 项指标实测与报告一字不差。
- 诚实局限充分标记：5 项局限文档+代码双向标注。

### 5.2 改进建议（非阻塞，P2/P3）

1. **B1（P3）**：`monitoring.compute_ic_series` 用 `np.errstate(divide='ignore')` 屏蔽 corrcoef 除零警告，或前置过滤更严格。
2. **B3（P3）**：删除 `run_pipeline` 第187行冗余死代码（`position_to_action` 首次调用被覆盖）。
3. **B4（P2）**：报告 §3「日度IC +0.219」标注为「全样本一次性 Pearson」口径，与 §4.1「IC滚动均值0.238」区分，避免读者误认为同一指标。
4. **B2（P3）**：报告 §4.1 注明「滚动IC覆盖非全样本（低频换手致部分窗口持仓不足5日跳过）」。

---

## 6. 审计文件清单

| 文件 | 审计动作 |
|:---|:---|
| `model_ham/exp429_production/ham_pipeline.py` | 静态审查 + 实盘运行 ✅ |
| `model_ham/exp429_production/monitoring.py` | 静态审查 + IC口径核查 ✅ |
| `model_ham/exp429_production/run_daily.py` | 静态审查 + 实盘运行核对绩效 ✅ |
| `model_ham/exp428_shadow_trading/exp428_engine.py` | fit_alpha_beta/rolling_deviation 前视核查 + 一致性校验代码审查 ✅ |
| `model_ham/exp426_ablation/exp426_engine.py` | 消融开关点审查 ✅ |
| `model_ham/exp427_1_extreme_stress/exp427_1_engine.py` | 极端识别审查 ✅ |
| `model_ham/exp424_t1_robustness/exp424_engine.py` | compute_positions/compute_adx/版本B/ADX审查 ✅ |
| `model_ham/exp425_minute_execution/exp425_engine.py` | 9:01成交/滑点/run_variant_full审查 ✅ |
| `model_ham/exp409_ham_dynamic/exp409_ham_dynamic.py` | compute_system_deviation/compute_ham_signals/run_ham_dynamic审查 ✅ |
| `reports/exp429_production_ready.md` | 绩效指标核对 ✅ |
| `model_ham/HANDOVER_exp429.md` | 实盘手册一致性核对 ✅ |

---

*审计时间：2026-09-10 | HAM 代码与实验逻辑审计（exp426/427-1/428/429）| 总体判定：审计通过(无致命bug) | 时序无前视/绩效100%一致/两套逻辑同源/极端无溢出 | 发现4项非致命改进(B1-B4) | 实盘可运行(PRODUCTION_READY)*
