# 会话交接文档：exp417 HAM 模型底层建模原理、数学形式与时序逻辑审计（第十八轮）

> 生成时间：2026-09-10
> 交接对象：下一轮对话 agent
> 会话完成度：✅ 模块1/2/3/4 全部完成，主报告 + 演算数据表已就绪，commit 已就绪，**仅 GitHub push 待手动**
> 核心交付：HAM 数学公式完整说明 + shift-equivariance 时序审计 + 假设局限清单 + 手工演算样例

---

## 0. 一句话状态

**任务已完成**。exp417 剥离底层代码，用自然语言+数学公式解释 HAM，并再次审计时序逻辑。
**审计结论**：① 代码层面**无未来函数**（shift-equivariance 8 日校验全部逐位一致，5 个非 NaN 日偏差 0）；② 模型层面存在**参数历史拟合与因子漂移风险**（exp416 跨品种已印证：锌上年化从 91% 衰减到 14%）。
**关键区分**：代码无未来函数 ≠ 模型无过拟合。两类风险严格区分，不混淆。
本地 commit 就绪，唯一遗留：**GitHub push 待用户手动**（与 exp416 同一 commit）。

---

## 1. 本轮做了什么

1. **模块1 HAM 主体建模原理**：两类主体（产业基本面 Agent F + 投机趋势 Agent C）现实对应 + 参数经济含义（alpha/beta/gamma/W）+ 完整迭代数学方程组（6 步 LaTeX）。
2. **模块2 时序逻辑专项审计**：逐行时序梳理 + shift-equivariance 校验原理与结果（8 日全通过）+ 两类风险区分。
3. **模块3 模型假设局限清单**：5 类假设（两类划分/参数静态/核心分歧假设/忽略要素/失效场景）。
4. **模块4 演示样例**：2024-04-01 手工完整演算全套 HAM 公式，从输入到多头满仓开仓信号。

**底层零改动承诺已履行**：不改动任何代码与回测逻辑，仅读取现有 HAM 因子表做白盒重算与审计。

---

## 2. 核心结果速查

### 报告必答三问

| # | 问题 | 结论 |
|---|------|------|
| ① | 两类主体是否真实存在？简化抽象还是真实复刻？ | **真实存在但模型是简化抽象**。产业资本/CTA 对应两类 Agent，但严格数学函数是理想化，非 1:1 复刻。 |
| ② | 区分代码 bug vs 模型理论风险 | **代码无未来函数**（shift-equivariance 全通过）；**模型有参数历史拟合风险**（exp416 跨品种印证）。两者严格区分。 |
| ③ | 模型成立前提与失效场景 | 前提见报告Q3；失效场景：强趋势段/因子漂移段/跨品种/极端跳空/流动性枯竭。 |

### 关键数值

| 维度 | 数值 |
|------|:---:|
| Shift-equivariance 校验 | 8/8 通过（5 非NaN日逐位偏差 0） |
| 手工演算目标日 | 2024-04-01 |
| 演算最终信号 | 多头满仓开仓（position_size=1.0, open_signal=True） |
| P(t) / P_fund(t) / P(t-1) | 112600 / 112600 / 107050 |
| D_f(t) / D_c(t) | 0.0 / 2775.0 |
| profit_f / profit_c | 0.0 / 97,887,500 |
| n_f / n_c | 0.0 / 1.0（纯投机主导） |
| disagreement / deviation | 0.899225 / 0.899225 |
| q_90 / q_95 | 0.818526 / 0.892339 |
| signal_dir / position_size | +1（多头）/ 1.0（满仓） |
| 与 exp415 单元测试一致 | ✓ 完全一致 |

---

## 3. 关键方法论沉淀

### 3.1 Shift-equivariance 校验设计（本审计核心）

**关键**：shift-equivariance（平移等变性）是验证无未来函数的标准方法。
- 原理：若 S(x[0..k])[k] == S(x[0..n])[k] 对任意 n>k 成立，则目标日 k 信号只依赖 x[0..k]，无前视。
- 测试：截断到目标日及之前（删除未来数据），重算完整信号链，与全量信号对比目标日。
- 8 个目标日跨 2024-2026，覆盖多头/空头/无信号/NaN 多种状态。
- 5 个非 NaN 日逐位精确一致（偏差 0），3 个 NaN 也一致。**全部通过 = 信号链严格因果，无未来函数**。
- 不能只测 1 天（可能巧合），必须多日跨区间批量验证。

### 3.2 两类风险的严格区分（报告核心，不混淆）

| 风险 | 性质 | 检验方法 | 结论 |
|------|------|----------|------|
| ① 代码未来函数 bug | 实现错误：T日偷用T+1数据 | shift-equivariance | **未检测到** |
| ② 参数历史拟合/因子漂移 | 模型理论：参数样本内选定 | 外样本验证 | **存在风险**（exp416跨品种印证） |

**关键**：代码无未来函数 ≠ 模型无过拟合。代码 bug 是确定性"算错"（shift-equivariance 能捕捉），模型风险是概率性"算对但参数不泛化"（shift-equivariance 无法捕捉，需外样本）。exp416 锌上年化衰减 84% 正是参数历史拟合表现，但这是模型风险而非代码 bug。报告须严格区分，不能让读者误以为"代码无bug=模型安全"。

### 3.3 手工演算的白盒独立性

手工演算不 import 任何 E409 计算函数，用 numpy 独立重写整条信号链（compute_system_deviation → aux → ham_signals），逐步展示中间量。这样：
- 验证信号链逻辑可被手工重现（非黑盒）。
- 与 exp415 单元测试结果一致（2024-04-01 多头满仓），交叉印证代码正确。
- 报告用真实数据（P=112600 等），非编造，可验证。

### 3.4 参数经济含义的解读

HAM 参数不是任意调优，有明确经济含义：
- alpha=0.3（产业回归强度，碳酸锂温和）< beta=0.5（投机趋势强度，动量活跃）——反映碳酸锂市场动量主导特征。
- gamma=2（Logit 灵敏度，中等）——赢家策略不迅速垄断，市场占比稳定。
- W=20（滚动窗口，约1月）——近一个月表现决定未来权重。
- ROLLING_TRAIN=180（约9月）——分位标准化的训练窗口，保证统计意义。
报告须说明这些经济含义，让读者理解模型设计逻辑而非黑盒参数。

---

## 4. 文件与路径清单

### 产物（本轮新增）

| 用途 | 路径 |
|------|------|
| 手工演算脚本 | `model_ham/exp417_ham_model_theory/exp417_manual_calc.py` |
| 时序审计脚本 | `model_ham/exp417_ham_model_theory/exp417_temporal_audit.py` |
| 主报告 | `reports/exp417_ham_model_theory_audit.md` |
| 演算步骤数据 | `model_ham/exp417_ham_model_theory/data/exp417_manual_calculation_steps.csv` |
| 演算输入窗口 | `model_ham/exp417_ham_model_theory/data/exp417_manual_calc_input_window.csv` |
| Shift-equivariance 校验 | `model_ham/exp417_ham_model_theory/data/exp417_shift_equivariance_test.csv` |
| 交接文档 | `model_ham/HANDOVER_round18_exp417_ham_model_theory.md` |

### 数据源（只读，完全复用未改）

| 数据 | 路径 |
|------|------|
| HAM引擎 | `model_ham/exp409_ham_dynamic/exp409_ham_dynamic.py` |
| HAM因子表 | `model_ham/ham_experiment_archive/intermediate/exp401_ham_factors_pure.csv` |

---

## 5. 复现命令

```bash
cd /home/ubuntu/lithium-engine
# 模块4 手工演算 (2024-04-01)
/usr/bin/python3 model_ham/exp417_ham_model_theory/exp417_manual_calc.py
# 模块2 时序审计 (shift-equivariance 8日)
/usr/bin/python3 model_ham/exp417_ham_model_theory/exp417_temporal_audit.py
```

**坑点**：
- Pyright 报 exp4xx import 误报（sys.path 动态导入），全部忽略，运行正常。
- compute_system_deviation 返回时 date 列被 reset_index，compute_full_chain 需 reset_index(drop=True) 保留 date 列。
- np.sign 包装成数组后用 np.roll，td_sign 是数组非 Series，索引用 td_sign[t] 非 .iloc[t]。
- shift-equivariance 测试日期有些是 NaN（暖机期/无信号），已选非 NaN 有效日期为主（2024-04-01 等 5 个），3 个 NaN 也校验一致。
- git push 网络到 github.com:443 可能不通，需用户手动或有网络时推。

---

## 6. 遗留待办（下一轮优先）

### P0：GitHub push（唯一硬阻塞，与 exp416 同一 commit）

```bash
cd /home/ubuntu/lithium-engine && git pull --no-rebase --no-edit && git push origin main
```

本地 commit 已含 exp416+exp417 全部产物，只欠 push。push 前先 pull 避免 non-fast-forward。

### P1：可继续优化方向（exp417 衍生）

1. **🟡 参数动态化探索**：exp417 已证参数静态假设在长周期/跨品种下失效。可探索参数滚动重估（如 gamma 按波动率动态调整），与 exp416 趋势逆向敞口管理互补。
2. **🟡 因子漂移监控纳入风控**：分段超额 + 夏普 CV（exp415 已证 0.895）做漂移监控，漂移超阈值降权。
3. **模型鲁棒性增强**：探索第三类主体（量化高频/被动盘）或引入流动性调整，缓解"两类划分"假设局限。
4. **外样本独立验证**：exp417 印证参数历史拟合风险，需在 2026Q4+ 外样本验证 HAM 仍有效（exp412~417 共同遗留）。

---

## 7. 用户偏好备忘

- 微信/飞书偏好简洁直接，任务路径清晰时一口气执行到底再汇总。
- 数据敏感，网页需反拷贝保护。
- 看板风格：Dark ECharts 高密度独立页；导出报告偏好横版A4每页一图。
- 看板/前端链接必须给 GitHub Pages .github.io 域名，禁止给 IP。
- 每次回复末尾标注当前估算上下文占用百分比。

---

*生成时间：2026-09-10 | 第十八轮会话交接 | exp417 HAM 模型底层建模原理审计 | 底层零改动，代码无未来函数(shift-equivariance 8日全通过)，模型有参数历史拟合风险(exp416跨品种印证)，手工演算 2024-04-01 多头满仓验证完整，两类风险严格区分，本地commit就绪push待手动*
