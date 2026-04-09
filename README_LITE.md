# 轻量版本地计量 Agent

这个版本不是复刻原仓库的 MetaGPT + Web UI，而是把它最有价值的能力压缩成一个可控、可解释、可在本地 CLI 直接运行的小 agent。

现在它已经从“最小可运行版”升级成“知识驱动版”，不只会跑模型，还会显式输出方法知识卡、选模依据、协方差设定、RDD 敏感性检查和识别风险提示。

## 原项目中保留了什么

- 任务分解：把问题拆成数据校验、模型选择、估计、反思四步
- 模型选择：根据用户请求和数据结构，在 `OLS / FE / IV / DID / Event Study / PSM / IPW / AIPW / IPWRA / Sharp RDD / Fuzzy RDD` 间做规则驱动选择；`RDD` 还支持 `local-linear / global-poly` 两种形态
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
- `propensity_score_double_robust_estimator_augmented_IPW`
- `propensity_score_double_robust_estimator_IPW_regression_adjustment`
- `Sharp_Regression_Discontinuity_Design_regression`
- `Fuzzy_Regression_Discontinuity_Design_regression`

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

### 10. 跑一个 AIPW

```bash
python lite_econometrics_agent.py run \
  --data selection_sample.csv \
  --query "estimate the treatment effect with doubly robust augmented IPW" \
  --outcome y \
  --treatment treat \
  --controls x1 x2 \
  --model aipw \
  --estimand ATT
```

### 11. 跑一个 IPWRA

```bash
python lite_econometrics_agent.py run \
  --data selection_sample.csv \
  --query "estimate the treatment effect with IPW regression adjustment" \
  --outcome y \
  --treatment treat \
  --controls x1 x2 \
  --model ipwra \
  --estimand ATT
```

### 12. 跑一个 Fuzzy RDD

```bash
python lite_econometrics_agent.py run \
  --data cutoff_sample.csv \
  --query "run a fuzzy RDD around the score cutoff" \
  --outcome y \
  --treatment treat \
  --controls x1 \
  --running-variable score \
  --cutoff 0 \
  --kernel triangle \
  --model fuzzy-rdd
```

### 13. 跑一个 Global Polynomial RDD

```bash
python lite_econometrics_agent.py run \
  --data cutoff_sample.csv \
  --query "run a global polynomial sharp RDD around the score cutoff" \
  --outcome y \
  --treatment treat \
  --controls x1 \
  --running-variable score \
  --cutoff 0 \
  --rdd-mode global-poly \
  --poly-order 3 \
  --model rdd
```

### 14. 使用 HAC 标准误

```bash
python lite_econometrics_agent.py run \
  --data my_data.csv \
  --query "baseline ols with HAC standard errors" \
  --outcome y \
  --treatment treat \
  --controls x1 x2 \
  --cov-type hac \
  --hac-maxlags 2
```

### 15. 使用双向聚类

```bash
python lite_econometrics_agent.py run \
  --data panel.csv \
  --query "two-way fixed effects with two-way clustering" \
  --outcome y \
  --treatment treat \
  --controls x1 \
  --entity-id firm_id \
  --time-id year \
  --cov-type cluster-both
```

### 16. 导出 balance table

```bash
python lite_econometrics_agent.py run \
  --data selection_sample.csv \
  --query "estimate the treatment effect with inverse probability weighting" \
  --outcome y \
  --treatment treat \
  --controls x1 x2 \
  --model ipw \
  --export-balance balance_table.csv
```

### 17. 导出系数表

```bash
python lite_econometrics_agent.py run \
  --data my_data.csv \
  --query "baseline ols" \
  --outcome y \
  --treatment treat \
  --controls x1 x2 \
  --export-terms regression_terms.csv
```

或者导出成 LaTeX：

```bash
python lite_econometrics_agent.py run \
  --data my_data.csv \
  --query "baseline ols" \
  --outcome y \
  --treatment treat \
  --controls x1 x2 \
  --export-terms regression_terms.tex
```

### 18. 导出论文式方法叙述

```bash
python lite_econometrics_agent.py run \
  --data my_data.csv \
  --query "baseline ols" \
  --outcome y \
  --treatment treat \
  --controls x1 x2 \
  --export-narrative methods_and_results.md
```

### 19. 使用 label map 美化表格变量名

先准备一个 JSON：

```json
{
  "const": "Intercept",
  "treat": "Treatment",
  "x1": "Firm Size",
  "x2": "Leverage"
}
```

然后：

```bash
python lite_econometrics_agent.py run \
  --data my_data.csv \
  --query "baseline ols" \
  --outcome y \
  --treatment treat \
  --controls x1 x2 \
  --label-map labels.json \
  --export-terms regression_terms.csv
```

### 20. 一键演示 OLS / FE / DID / Event Study / PSM / IPW / AIPW / IPWRA / Sharp RDD / Fuzzy RDD / IV

```bash
python lite_econometrics_agent.py demo
```

### 21. 跑一个 specification sweep

先准备一个 JSON：

```json
{
  "base_spec": {
    "data": "ols_demo.csv",
    "outcome": "y",
    "treatment": "x",
    "controls": ["c"]
  },
  "specs": [
    {"name": "baseline", "query": "baseline ols", "model": "ols"},
    {"name": "hac2", "query": "ols with HAC", "model": "ols", "cov_type": "hac", "hac_maxlags": 2}
  ]
}
```

然后：

```bash
python lite_econometrics_agent.py sweep \
  --config sweep.json \
  --export-models-table sweep_table.csv \
  --export-results-paragraph sweep_results.md
```

`sweep.json` 还支持 `expand` 自动展开稳健性矩阵，以及 `table` 自定义导出格式。例如：

```json
{
  "base_spec": {
    "data": "ols_demo.csv",
    "outcome": "y",
    "treatment": "x",
    "controls": ["c"]
  },
  "expand": {
    "cov_type": [
      {"value": "auto", "label": "robust"},
      {"value": "hac", "label": "hac2"}
    ],
    "hac_maxlags": [1, 2]
  },
  "table": {
    "drop_terms": ["const"],
    "row_order": ["x", "c"],
    "group_headers": [
      {
        "label": "OLS Variants",
        "models": ["cov_type=robust__hac_maxlags=1", "cov_type=hac2__hac_maxlags=2"]
      }
    ],
    "notes": [
      "Standard errors in parentheses.",
      "Stars denote 10%, 5%, and 1% significance."
    ]
  }
}
```

## 可解释性原则

- 自动选模只用明确规则，不做黑箱决策
- 输出里会给出 `selected_model` 和 `selection_reasons`
- 输出里会给出 `knowledge_card`，包括适用场景、识别逻辑、诊断项和常见失败模式
- 反思日志会记录自动清洗、丢弃行、删除常数列、弱工具变量提示等
- 支持 `weights`、`cluster`、`cov-type`、`hac-maxlags` 作为显式输入，而不是藏在内部假设里
- 对 `PSM / IPW / AIPW` 会输出 balance 改善前后的 standardized mean difference 摘要
- 可以把 propensity-score 方法的 balance 诊断导出为 CSV 表
- 可以把系数表导出为 `csv` 或 `tex`
- 可以导出论文式 narrative，总结方法选择、识别逻辑、主结果和风险
- 可以通过 `label-map` 把变量名映射成更可读的表格标签
- RDD / fuzzy RDD 会输出带宽或多项式阶数的敏感性对比
- 可以通过 `sweep` 子命令把多个规格合并成并列表和结果段落
- `sweep` 支持 `expand` 自动展开规格矩阵，支持 `group_headers / notes / row_order / drop_terms`
- 所有结果都以结构化 JSON + 系数表打印

## 当前边界

- 默认支持 `csv / dta / parquet / xlsx`
- FE、Staggered DID、Event Study 要求同时提供 `entity-id` 和 `time-id`
- IV 版本当前只支持单个工具变量
- Event Study 当前假设处理状态是单调开启的二元变量
- 当前 RDD 层已经支持 `sharp/fuzzy` 的 `local-linear` 和 `global-poly` 两种模式
- PSM / IPW 当前面向二元处理变量，且默认依赖“selection on observables”
- AIPW 当前支持 `ATE` 和 `ATT`
- IPWRA 当前支持 `ATE` 和 `ATT`
- `cluster-both` 目前主要面向面板型模型；`HAC` 目前优先用于非面板或 kernel-based 协方差场景
- reflection 是规则型，不是开放式 LLM 自修复
