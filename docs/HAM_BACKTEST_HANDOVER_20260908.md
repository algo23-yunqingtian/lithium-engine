# HAM因子 ML回测 — 交接文档

> 日期: 2026-09-08
> 状态: ✅ 回测完成，模型未显著优于随机猜测
> 仓库: github.com/algo23-yunqingtian/lithium-engine → model_ham/

---

## 一、已完成的工作

### 1. HAM 本地副本清理
- ✅ 对比本地 vs GitHub，核心因子文件完全一致（diff=0）
- ✅ 备份本地独有产物（3个 group_*.csv）到 GitHub
- ✅ 删除本地副本 `/home/ubuntu/lithium_ham_two_agents/`

### 2. ML 预处理流水线
- ✅ `ml_preprocess.py` — 预处理脚本
- ✅ `ml_input_features.csv` — ML输入数据集（740行×26列）
- ✅ `feature_analysis.md` — 分析文档

处理内容:
- 3个标签: label_ret_1d / 3d / 5d（严格无未来函数）
- 3个HAM因子: n_c, Total_Demand, n_f-n_c（1%-99% winsorize）
- Total_Demand 过滤规则: W=20参数全负稳定，无需过滤
- 外部特征: 13个（本地DB + 知几Mysteel）

### 3. 知几API数据拉取
- ✅ 6个指标全部成功（Mysteel源，SMM故障）
- ✅ 限频: >=1秒/次
- 仓单库存(日): FU00058102, 669点, 2023-12-06 ~ 2026-09-08
- 社会库存(月): ID01865189, 35点, 2023-10-31 ~ 2026-08-31
- 库存-江西: ID01865185, 35点
- 库存-青海: ID01865196, 35点
- 库存-四川: ID01865203, 35点
- 回收料库存: ID02226359, 31点

### 4. LightGBM 回测
- ✅ `lgb_backtest.py` — 回测脚本（需 python3.12 + lightgbm + sklearn）
- ✅ `backtest_report.md` — 回测报告

**结果:**
| 标签 | 训练 | 测试 | 方向准确率 | RMSE |
|------|------|------|-----------|------|
| 1d | 374 | 125 | 44.0% | 0.029 |
| 3d | 374 | 125 | 48.8% | 0.050 |
| 5d | 374 | 125 | 49.6% | 0.063 |

**结论: 模型未显著优于随机猜测（50%）**

**特征重要性 Top 5:**
1. zhiji_wh_receipt (仓单库存) — 7
2. volume (成交量) — 4
3. basis (基差) — 3
4. spot_avg (现货均价) — 3
5. ret_20d (20日动量) — 2

**HAM因子 (n_c, Total_Demand, n_f-n_c) 在LGBM中重要性为0**

---

## 二、关键发现

1. **HAM因子单独预测力弱**: IC回测显示Total_Demand显著（ICIR=-0.448, p<0.0001），但LGBM方向准确率仅44-50%。IC衡量的是排序能力，LGBM方向准确率衡量的是绝对方向预测——两者不同。
2. **仓单库存是最重要特征**: 知几Mysteel的日度仓单数据（668天覆盖）在LGBM中排名第一。
3. **HAM因子在LGBM中被淹没**: 17个特征中，HAM因子重要性为0，说明传统量价特征对短期价格预测更有用。
4. **SMM源故障**: 开工率等关键SMM指标无法获取（HTTP 500，登录密码错误次数超限）。
5. **模型参数过早停止**: Early stopping在7/1/2轮就触发，说明模型学到了很少的模式就过拟合了。

---

## 三、下一步建议

### 方向1: 改进模型（推荐）
- **更多树/更浅深度**: 当前early stopping太早（1-7轮），可尝试减少特征数或增加正则化
- **只用HAM因子**: 做单独消融实验，验证HAM因子是否有独立预测力
- **分类而非回归**: 预测涨跌方向（二分类）而非收益率值，方向准确率可能更高
- **更长horizon**: 1d/3d/5d都试了，可试10d/20d（噪声更小）
- **特征工程**: 基差变化率、成交量变化率、持仓变化率等衍生特征

### 方向2: 获取更多数据
- **SMM开工率**: 等SMM源恢复后拉取（已找到ID: a12754399, 周度）
- **A股锂矿板块**: 赣锋锂业/天齐锂业股价可作为情绪指标
- **USD/CNY汇率**: 影响进口成本
- **宏观指标**: 社融/PMI/美元指数

### 方向3: 模型对比
- **纯HAM因子模型 vs 纯传统特征模型**: 消融实验
- **线性模型 vs LGBM**: 验证非线性是否带来增益
- **集成模型**: HAM因子 + LGBM集成

---

## 四、运行环境

```bash
# 回测脚本需要 python3.12 + lightgbm + scikit-learn
python3.12 -c "import lightgbm; print(lightgbm.__version__)"  # 4.7.0
python3.12 -c "import sklearn; print(sklearn.__version__)"     # 1.9.0

# 运行回测
cd /home/ubuntu/lithium-engine/model_ham
python3.12 lgb_backtest.py
```

注意: Hermes venv (python3.11) 的 scipy 被覆盖损坏，必须用 python3.12 运行。

---

## 五、文件清单

| 文件 | 说明 |
|------|------|
| `ml_preprocess.py` | ML预处理脚本（生成特征CSV） |
| `lgb_backtest.py` | LightGBM回测脚本 |
| `ml_input_features.csv` | ML输入数据集（740行×26列） |
| `feature_analysis.md` | 特征分析文档 |
| `backtest_report.md` | 回测报告 |
| `zhiji_external_data.json` | 知几API缓存（6个指标） |
| `ham_factors_full_800d.csv` | HAM原始因子（740天，未修改） |

---

## 六、知几API状态

| 数据源 | 状态 | 说明 |
|--------|------|------|
| Mysteel | ✅ 可用 | search + series 正常 |
| SMM | ❌ 故障 | HTTP 500，登录错误次数超限 |
| 观(行情) | 未测 | 配额独立 |

**碳酸锂关键指标ID:**
- 仓单数量(日): FU00058102
- 社会库存(月): ID01865189
- 开工率总计(周): a12754399 (SMM, 暂不可用)
- 开工率盐湖(月): a12715874 (SMM, 暂不可用)

---

## 七、GitHub提交记录

```
1e10ec0 feat(model_ham): LightGBM backtest with zhiji external data
69311b8 feat(model_ham): add ML preprocessing pipeline
1faf9cf feat(model_ham): add quintile group backtest results
d9cf2f6 docs: add HAM_HANDOVER_20260908.md handover document
```
