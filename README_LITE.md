# 轻量版本地计量 Agent

这个版本不是复刻原仓库的 MetaGPT + Web UI，而是把它最有价值的能力压缩成一个可控、可解释、可在本地 CLI 直接运行的小 agent。

现在它已经从“最小可运行版”升级成“知识驱动版”，不只会跑模型，还会显式输出方法知识卡、选模依据和识别风险提示。

## 原项目中保留了什么

- 任务分解：把问题拆成数据校验、模型选择、估计、反思四步
- 模型选择：根据用户请求和数据结构，在 `OLS / FE / IV / DID / Event Study / PSM / IPW / RDD` 间做规则驱动选择
- 工具思想：不让 LLM 自由发挥“发明方法”，而是显式调用本地计量函数
- reflection：对报错、缺失、常数列、弱工具变量和估计精度做基础反思
- 方法知识卡：把原项目里分散在 tool docstring 和 prompt guidance 里的识别逻辑，压成结构化知识库

## 故意删掉了什么

- MetaGPT 角色系统
- notebook 持久执行内核
- Web UI、流式前端和多轮会话存储
- 大而全的 tool registry

## 文件

- `lite_econometrics_agent.py`：完整 CLI agent
- `requirements-lite.txt`：最小依赖

## 轻量映射关系

| 原仓库组件 | 轻量替代 |
| --- | --- |
| `DataInterpreter` | `LiteEconometricsAgent` |
| `WritePlan` | `RulePlanner.build_plan()` |
| `TaskType + guidance` | 显式模型选择规则 + 方法知识卡 |
| `tool_registry + tool_recommend` | `EconometricTools` 本地函数库 |
| `WriteAnalysisCode + ExecuteNbCode` | 直接 Python 函数执行 |
| `reflection prompt` | 错误修复 + 结果诊断日志 |

## 当前已经吸收的原项目方法

- `ordinary_least_square_regression`
- `IV_2SLS_regression`
- `Static_Diff_in_Diff_regression`
- `Staggered_Diff_in_Diff_regression`
- `Staggered_Diff_in_Diff_Event_Study_regression`
- `propensity_score_construction`
- `propensity_score_matching`
- `propensity_score_inverse_probability_weighting`
- `Sharp_Regression_Discontinuity_Design_regression`

## 用法

### 1. 跑一个 OLS

```bash
python lite_econometrics_agent.py run \
  --data my_data.csv \
  --query "estimate the baseline effect" \
  --outcome y \
  --treatment treat \
  --controls x1 x2
```

### 2. 跑一个双向固定效应 FE

```bash
python lite_econometrics_agent.py run \
  --data panel.csv \
  --query "estimate the policy effect with firm and year fixed effects" \
  --outcome y \
  --treatment post_treat \
  --controls size leverage \
  --entity-id firm_id \
  --time-id year
```

### 3. 跑一个 IV

```bash
python lite_econometrics_agent.py run \
  --data iv_sample.csv \
  --query "estimate the endogenous treatment effect with IV-2SLS" \
  --outcome y \
  --treatment treat \
  --controls x1 x2 \
  --instrument z
```

### 4. 跑一个 DID

```bash
python lite_econometrics_agent.py run \
  --data did_panel.csv \
  --query "estimate the policy effect with difference in differences" \
  --outcome y \
  --treatment treated_indicator \
  --controls x1 x2 \
  --entity-id firm_id \
  --time-id year \
  --treat-group treated_firm \
  --post post_policy
```

### 5. 跑一个 Event Study

```bash
python lite_econometrics_agent.py run \
  --data staggered_panel.csv \
  --query "run an event study and inspect pre-trends" \
  --outcome y \
  --treatment treated \
  --controls x1 x2 \
  --entity-id firm_id \
  --time-id year \
  --lead-window 4 \
  --lag-window 3
```

### 6. 查看知识库

```bash
python lite_econometrics_agent.py knowledge --model all
```

### 7. 跑一个 PSM

```bash
python lite_econometrics_agent.py run \
  --data selection_sample.csv \
  --query "estimate the treatment effect with propensity score matching" \
  --outcome y \
  --treatment treat \
  --controls x1 x2 \
  --model psm \
  --estimand ATT
```

### 8. 跑一个 IPW

```bash
python lite_econometrics_agent.py run \
  --data selection_sample.csv \
  --query "estimate the treatment effect with inverse probability weighting" \
  --outcome y \
  --treatment treat \
  --controls x1 x2 \
  --model ipw
```

### 9. 跑一个 Sharp RDD

```bash
python lite_econometrics_agent.py run \
  --data cutoff_sample.csv \
  --query "run a sharp RDD around the score cutoff" \
  --outcome y \
  --treatment treat \
  --controls x1 \
  --running-variable score \
  --cutoff 0 \
  --kernel triangle \
  --model rdd
```

### 10. 一键演示 OLS / FE / DID / Event Study / PSM / IPW / RDD / IV

```bash
python lite_econometrics_agent.py demo
```

## 可解释性原则

- 自动选模只用明确规则，不做黑箱决策
- 输出里会给出 `selected_model` 和 `selection_reasons`
- 输出里会给出 `knowledge_card`，包括适用场景、识别逻辑、诊断项和常见失败模式
- 反思日志会记录自动清洗、丢弃行、删除常数列、弱工具变量提示等
- 支持 `weights` 和 `cluster` 作为显式输入，而不是藏在内部假设里
- 所有结果都以结构化 JSON + 系数表打印

## 当前边界

- 默认支持 `csv / dta / parquet / xlsx`
- FE、Staggered DID、Event Study 要求同时提供 `entity-id` 和 `time-id`
- IV 版本当前只支持单个工具变量
- Event Study 当前假设处理状态是单调开启的二元变量
- 当前 RDD 只实现了 sharp local-linear 版本，还没有接 fuzzy RDD
- PSM / IPW 当前面向二元处理变量，且默认依赖“selection on observables”
- reflection 是规则型，不是开放式 LLM 自修复
