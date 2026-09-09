# HAM 因子实验归档索引

> 归档时间：2026-09-09
> **状态：HAM 预测分支正式终止，后续实验不再调用 HAM 任何函数、变量**
> 根目录 README 已加备注指向本目录

## 必读：完整失败复盘

**[`ham_failure_review.md`](ham_failure_review.md)** — 完整复盘报告，包含：
1. 原始研究思路（HAM 设计初衷、理论假设、捕捉信号）
2. 工程层面问题（前视泄露、修复前后 n_c 对比）
3. 稳健性检验（静态vs滚动、样本内外反转、多重检验校正）
4. 经济学层面失效分析（HAM 适用边界、为何难落地）
5. 明确区分（学术价值 vs 预测适用性）
6. 最终结论（HAM 终止、GMM 仅定性）

## 目录结构

```
ham_experiment_archive/
├── ham_failure_review.md      # 完整失败复盘报告（必读）
├── README.md                  # 本文件
├── src_code/                  # 原始实验代码（13个）
│   ├── ham_model.py           # HAM 核心模型（前视漏洞在 line ~48）
│   ├── data_preprocess.py     # 数据预处理
│   ├── backtest.py            # 旧版回测脚本
│   ├── lgb_backtest.py        # LightGBM 回测
│   ├── main_run.py / run_all.py  # 主运行
│   ├── ml_preprocess.py / ham_ablation.py
│   └── step1-5_*.py           # GMM 聚类步骤
├── exp_records/               # 历史实验记录
│   ├── exp401_403/            # 第四轮：前视修复+滚动回测+方法论专章
│   │   ├── exp401_regen_factors.py / exp402_rolling_backtest.py / exp403_summary_archive.py
│   │   ├── exp402_rolling_backtest_pure.md / .png
│   │   ├── exp403_overview_table.md / exp403_methodology_lesson.md
│   │   └── HANDOVER_round4_lookahead_fix.md
│   └── exp301_309/            # 第三轮：反向校验+稳健性增强
│       ├── exp1-6_*.py + check_*.py
│       ├── reports/exp301-309_*.md + exp306_rolling_backtest.png
│       ├── EXECUTION_SUMMARY.md
│       └── HANDOVER_round3_audit_optimize.md
└── intermediate/              # 关键中间产物
    ├── exp401_ham_factors_orig_backup.csv  # 修复前因子（含前视）
    ├── exp401_ham_factors_pure.csv         # 修复后因子（纯净）
    ├── exp402_g1/g2_predictions.csv        # 两组对照预测
    ├── exp402_windows_g1/g2.json           # 滚动窗口配置
    ├── exp306_rolling_predictions.csv      # 第三轮滚动预测
    └── *.md（旧版回测报告、消融报告、模型对比、特征分析、readme）
```

## 核心结论速查

| 项目 | 结论 |
|------|------|
| HAM 预测力 | ❌ 无（三轮验证均证伪） |
| 失败根因 | 前视泄露伪造因子变化性 + 框架-品种错配 |
| n_c 修复后 | 塌陷为常数（std=0.037），信息归零 |
| RankIC 显著项 | 0（Bonferroni 校正后） |
| 样本外准确率 | 50-52%（随机区间） |
| GMM 聚类 | ✅ 保留定性（K=4 稳健），不做预测 |

## 归档约束遵守

- ✅ 只归档历史产物，无新计算/调参/迭代
- ✅ 未删除 exp404/exp405 原有目录
- ✅ 剔除冗余缓存（__pycache__、.log、原始大CSV），总大小 1.1M
- ✅ 根目录 README 已加归档备注

## 后续实验规则

**后续实验不再调用 HAM 任何函数、变量**，包括：
`n_c` / `n_f` / `Total_Demand` / `profit_f` / `profit_c` / `D_f` / `D_c` / `P_fund` / `n_f_minus_n_c` / `valid`

工作重心转向基本面因子探索（库存/基差/期限结构/成交量持仓）。
