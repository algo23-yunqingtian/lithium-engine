# 会话交接文档：exp426+exp427 消融实验与跨品种泛化

> 生成时间：2026-09-10
> 交接对象：下一轮对话 agent
> 会话完成度：✅ exp426消融实验 + ✅ exp427跨品种泛化 全部完成，已commit push
> 核心交付：
>   - exp426：定量拆解HAM四组件——投机主体=收益引擎(-21.4pp)/产业主体=方向过滤器(-11.3pp,IC+28%)/Logit自适应=状态增强(-6.6pp,IC-59%)/ADX降权=风控组件(关闭+3.2pp收益但-3.7pp回撤,Calmar-1.18)。四组件缺一不可。
>   - exp427：HAM跨品种泛化(锌)判定NOT_TRANSFERABLE——锌全样本2.0%/Calmar0.24(LC 28.9%/Calmar4.23)，根因aux_confirm仅76次(LC422次)，td_flip依赖需求高频变号而锌镀锌稳定单调。

---

## 0. 一句话状态

**两个交接任务全部完成，已commit push到 main（1924feb..176a127）。**

- **任务一 exp426消融**：五组对照(统一9:01+10bp)定量拆解HAM四组件价值分层，证明完整HAM是唯一推荐配置。
- **任务二 exp427跨品种**：将完整HAM纯参数化迁移到锌，判定不可迁移，根因是锌的辅助确认信号(td_flip)稀缺——品种微观结构差异，非参数可调。

---

## 1. exp426 消融实验（任务一）

### 1.1 五组绩效（基准28.9%/Calmar4.23，9:01+10bp）

| 组别 | 消融内容 | 年化 | Calmar | IC | 笔数 | 年化Δ |
|:---|:---|:---:|:---:|:---:|:---:|:---:|
| 基准组 | 原版全组件 | 28.9% | 4.23 | +0.196 | 129 | - |
| 消融A | 固定权重(关Logit自适应) | 22.3% | 3.87 | +0.080 | 81 | -6.6pp |
| 消融B | 移除产业主体(仅投机动量) | 17.5% | 2.57 | +0.153 | 106 | -11.3pp |
| 消融C | 移除投机主体(仅产业基本面) | 7.5% | 0.99 | +0.112 | 58 | -21.4pp |
| 消融D | 关闭ADX降权(原版T1) | 32.1% | 3.05 | +0.221 | 126 | +3.2pp |

### 1.2 价值分层结论
1. **投机主体D_c = 收益引擎**：移除-21.4pp，Calmar4.23→0.99
2. **产业主体D_f = 方向过滤器**：移除-11.3pp，IC+28%（把单腿动量升级为双向分歧）
3. **Logit自适应 = 状态增强**：固定权重-6.6pp，IC-59%
4. **ADX降权 = 风控组件(非收益组件)**：关闭+3.2pp收益但回撤-6.8%→-10.5%，Calmar反降-1.18

**四组件缺一不可**，完整HAM是唯一推荐配置。

### 1.3 关键方法论
- 消融只改`compute_system_deviation`（HAM核心），基本面腿/GMM/信号/风控全沿用原版
- 消融A：`rolling(180).rank(pct)` → `expanding z-score`（去滚动自适应，保持无前视）
- 所有组共用同一套9:01成交价（固定种子20260910），对照纯净
- D_c须先zeros(n)再赋D_c[1:]，勿直接np.diff（少一元素报719vs720）

---

## 2. exp427 跨品种泛化（任务二）

### 2.1 迁移方法
纯参数化monkey-patch：覆盖exp409.HAM_PURE/load_ham_factors + exp410.load_factors指向锌数据，复用exp411/413/424/425全套引擎，HAM全部逻辑/阈值/风控与LC一致，仅换数据源。

### 2.2 锌数据源
| HAM要素 | 锌数据 | indicator_id |
|:---|:---|:---|
| OHLC | akshare zn0主力(738行) | - |
| 库存 | 七地锌库存 | a10000098 |
| TC(→P_fund) | 国产TC加工费 | s20095859 |
| 需求 | 镀锌产量 | a10097191 |
| 仓单 | SHFE仓单日报 | a10157132 |

P_fund产业锚=TC滚动z-score映射(k=0.03，P_fund=close×(1+0.03×z(TC)))，与close相关0.796。

### 2.3 跨品种结果
| 品种 | 年化 | Calmar | IC | 笔数 |
|:---|:---:|:---:|:---:|:---:|
| 碳酸锂(LC) | 28.9% | 4.23 | +0.196 | 129 |
| 锌(Zn)全样本 | 2.0% | 0.24 | +0.205 | 47 |
| 锌样本内 | 0.1% | 0.01 | +0.157 | 30 |
| 锌样本外 | 5.7% | 1.13 | +0.266 | 17 |

**判定：NOT_TRANSFERABLE**

### 2.4 迁移失败根因（诊断）
| HAM信号 | 锌 | LC |
|:---|:---:|:---:|
| open_signal(双共振) | 13 | 66 |
| aux_confirm | 76 | 422 |

HAM双共振需`main_trigger AND aux_confirm`。锌aux_confirm仅76次(LC422次)是根因。aux_confirm依赖td_flip(仓单边际变号)，锌需求侧(镀锌)周频变化缓慢、符号极少翻转→td_flip几乎不触发。**品种微观结构差异，非参数可调。**

### 2.5 诚实披露（局限性）
- P_fund是TC代理（非真实成本曲线），可能与真实锌成本有偏差
- 9:01偏离分布复用LC（非锌原生标定）
- 镀锌代理Total_Demand（未含压铸/氧化锌）
- 样本外仅255日，结论需谨慎
- 迁移失败可能是数据/代理缺陷或结构差异，彻底排除需真实锌成本曲线

---

## 3. 文件清单

### exp426
| 用途 | 路径 |
|:---|:---|
| 引擎 | `model_ham/exp426_ablation/exp426_engine.py` |
| 报告 | `reports/exp426_ablation.md` |
| 数据 | `model_ham/exp426_ablation/data/` (compare/attribution/equity/summary) |

### exp427
| 用途 | 路径 |
|:---|:---|
| 数据层 | `model_ham/exp427_cross_variety/build_zinc_data.py` |
| 引擎 | `model_ham/exp427_cross_variety/exp427_engine.py` |
| 报告 | `reports/exp427_cross_variety.md` |
| 数据 | `model_ham/exp427_cross_variety/data/` (compare/equity/summary) |
| 锌因子表 | `raw_data/zinc_ham_factors.csv`, `raw_data/zinc_future.csv`, `exp406_fund_factor/data/zinc_fund_factors.csv` |

### 总文档
| 用途 | 路径 |
|:---|:---|
| 结题文档(新增4.7/4.8节) | `model_ham/HAM_Project_Final_Document.md` |

---

## 4. 复现命令
```bash
cd /home/ubuntu/lithium-engine
/usr/bin/python3 model_ham/exp426_ablation/exp426_engine.py              # 消融
/usr/bin/python3 model_ham/exp427_cross_variety/build_zinc_data.py        # 锌数据(需akshare)
/usr/bin/python3 model_ham/exp427_cross_variety/exp427_engine.py          # 跨品种
```

**通用坑点**：
- 必须`/usr/bin/python3`（有akshare/pandas/scipy，venv的python3.11缺akshare）
- Pyright报pandas/exp4xx import误报（sys.path动态导入），全忽略
- akshare列名中文，build_zinc_data已rename
- exp427样本内外用全样本final_pos子集，np.asarray(mask)勿用mask.values

---

## 5. 遗留待办

### P1：可继续方向
1. **🟡 挽救锌迁移**：替换辅助信号(TC变号/LME比价替代td_flip)、真实锌成本曲线重构P_fund、放宽为单信号开仓。
2. **🟡 改测铅(Pb)**：铅需求结构(汽车/电池)可能更接近LC，作第二迁移候选。lead_v2.db仅有time_series表(无indicator_mapping)，需重新探查指标ID。
3. **🟢 ADX降权实盘取舍**：消融D证明关闭ADX降权多赚3.2pp但回撤+3.7pp。若实盘可承受10.5%回撤，可考虑关闭换收益。
4. **🟡 投机主体增强**：D_c是收益引擎，可探索多周期动量/动量加速。

---

## 6. 用户偏好备忘
- 微信/飞书简洁直接，任务路径清晰时一口气执行到底再汇总。
- HAM系列每轮完成必发交接文档路径给用户开新对话。
- 每次回复末尾标注上下文占用百分比。
- 信息源透明：区分"外部数据"vs"自己推理"。
- 跨品种迁移数据量大可考虑delegate_task。

---

*生成时间：2026-09-10 | exp426消融+exp427跨品种 全部完成已push(1924feb..176a127) | exp426:投机主体=收益引擎-21.4pp/产业主体=方向过滤器-11.3pp IC+28%/Logit=状态增强-6.6pp IC-59%/ADX=风控组件+3.2pp收益-3.7pp回撤Calmar-1.18/四组件缺一不可 | exp427:锌NOT_TRANSFERABLE 2.0%/Calmar0.24(aux_confirm仅76次LC422次,td_flip依赖需求高频变号锌镀锌稳定单调)| 锌可挽救:换辅助信号/真实成本曲线/改测铅*
