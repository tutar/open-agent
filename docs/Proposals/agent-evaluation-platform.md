# Agent Evaluation Platform

Status: proposed

## Summary

OpenAgent 当前已经提供了 transcript、events、model-io、verifier result 这些 runtime primitives，但还没有完整的 agent evaluation platform。

真正的平台层不应只补一个 task bank，而应定义一套能稳定支持这些事情的平台模型：

- 以 `Role` 组织 agent 的评测资产
- 从可编辑资产中冻结出一次不可变测试输入
- 在同一 role 下做多模型、多 `USER.md`、最小 skills 有/无、system prompt 版本的对比实验
- 产出可发布的推荐 agent 配置
- 持续推动“弱模型厚、强模型薄、质量提升、token 成本下降”

本 proposal 只讨论离线/批量/研发平台面，不重复定义 runtime 自身的 turn loop、tool loop、session store 或 verifier task 内核。

同时，本 proposal 与 [Agent Evaluation Runtime Foundations](./agent-evaluation-runtime-foundations.md) 分工明确：

- runtime foundations
  - 负责 evidence、verifier、completion gate、risk/correctness 运行时契约
- evaluation platform
  - 负责 task/suite、snapshot、trial/run、compare、report、promotion

## Evaluation Platform Model

平台需要把评估对象收敛成一套稳定 object model。核心对象应围绕以下 4 个主线展开：

- `RoleEvalConfig`
  - role 维度的可编辑评测资产
- `EvaluationSnapshot`
  - 一次 evaluation run 使用的不可变测试输入
- `EvaluationExperiment`
  - 同一 role 下受控的对比实验
- `AgentProfile`
  - 平台产出的推荐发布配置

平台还需要保留更基础的运行对象：

- `task`
  - 一个测试样本或工作流场景
- `trial`
  - 一次具体尝试
- `grader`
  - 评分逻辑
- `evaluation suite`
  - capability / regression / scenario 分组
- `evaluation run`
  - 一次批量执行及其产物集合

## Platform Capabilities

### 1. Role-Scoped Eval Authoring

平台首先需要 role 维度的评测资产定义面。

`RoleEvalConfig` 至少应组合：

- role-scoped task sets
- `USER.md` versions
- minimal skills / MCP selections
- candidate models
- default experiment axes
- suite grouping metadata

这里要明确：

- role 资产平时可编辑、扩展、维护
- task set 在测试前可以演进
- role 资产负责定义“这个 agent 可以怎么被测”
- role 资产不是一次 execution run 的直接输入

这意味着：

- `Role` 是评测资产的主组合单元
- evaluation platform 负责从这些可编辑资产中挑出一组版本，用于测试

### 2. Frozen Evaluation Snapshot

平台不能直接测试“当前 role 文件状态”，而必须在开跑前冻结一次正式 snapshot。

`EvaluationSnapshot` 至少应冻结：

- `role_id`
- task set version 或 content hash
- `USER.md` version 或 content hash
- selected skills / MCPs
- selected model spec
- selected system prompt bundle version
- verifier/grader config version
- runtime/harness version
- repo commit / fixture version / environment version

规则应固定为：

- evaluation run 只能引用 snapshot
- snapshot 不再读取“当前 role 文件”
- role/task/`USER.md`/system prompt 在 run 启动后继续变化，也不影响本次 run
- 重跑、复核、回归、对比都基于 snapshot
- provenance 必须完整可追溯

“开始测试后不能再修改”在平台里不应只是一条 UI 规则，而应是 snapshot contract。

### 3. System Prompt Bundle Versioning

agent 里的通用方法论 prompt 不应混在 role 下，而应作为独立资产域管理。

平台需要引入 `SystemPromptBundle`：

- 不属于 role
- 独立版本管理
- 可被多个 role 复用
- 可作为 comparison axis
- 可进入 publishable profile

这里应明确分层：

- `ROLE.md`
  - 机器可读 role 资源包装器
- `USER.md`
  - role-specific instruction
- `SystemPromptBundle`
  - generic methodology/system prompts
- `EvaluationSnapshot`
  - 绑定某个 role 资产版本与某个 system prompt bundle 版本

### 4. Controlled Comparison Experiments

平台不应把 compare 当成运行后的临时分析，而应把它定义为一等能力。

`EvaluationExperiment` 应固定：

- same role
- same frozen task set
- same verifier/grader rules
- same environment class

最小变更轴应支持：

- `model`
- `USER.md` version
- minimal skills `on/off`
- `SystemPromptBundle` version

其中 skills 这轮先支持最小验证：

- no extra support
- with selected minimal support

不应在 v1 一开始就做任意大规模组合搜索。平台先支持 small controlled matrix，使结论可解释、可复核。

### 5. Capability Suites And Regression Suites

平台需要明确区分两类 suite：

- capability eval
  - 看“这个 role/agent 还能在哪些任务上继续提升”
  - 通过率可以低
  - 用于 hill-climbing
- regression eval
  - 看“原来已经会的能力是否退化”
  - 通过率应高
  - 用于 gate 和 release confidence

同一 role 至少应支持：

- capability suite
- regression suite

成熟的 capability tasks 可以 graduate 成 regression suite。汇总与 compare 时，两类 suite 应分别统计，不应混成一个总分。

### 6. Trials, Graders, And Cost-Aware Results

平台不能只停留在“task 跑一遍”，而需要更稳的评估语义。

需要落地：

- multi-trial execution
  - 同一 task 可多次尝试，降低随机性噪声
- grader mix
  - code-based graders
  - model-based graders
  - optional human calibration / review
- unified result schema
  - correctness
  - risk
  - token usage / cost
  - latency
  - evidence references
  - provenance

平台输出不应只有 pass/fail，而应支持多目标评估。

### 7. Publishable Agent Profiles

平台最终不应只产出分数，而应产出可发布配置。

`AgentProfile` 至少应描述：

- target role
- preferred model tier mapping
- preferred `USER.md` version
- preferred `SystemPromptBundle` version
- minimal support skills policy
- expected quality/risk/cost envelope

平台需要支持回答：

- 弱模型下需要打开哪些 support switches
- 强模型下哪些 support 可以关闭
- 哪个更薄的配置已经达到足够质量
- 哪个配置应该成为新的发布 baseline

这正是平台承接的最终目标：

- 发布通用 agent
- 支持不同 LLM
- 当模型能力增强时，agent 可以变薄
- 在保证质量的前提下降 token 成本

### 8. Task Lifecycle And Promotion Workflow

为了让平台真正可运营，还需要补 task 生命周期与 profile 提升流程。

需要落地：

- task lifecycle
  - add
  - revise
  - deprecate
  - flaky mark
  - graduate
- promotion workflow
  - 新 model / 新 `USER.md` / 新 `SystemPromptBundle` / 新 `AgentProfile` 何时替换 baseline
- compare outcome policy
  - regression 退化不放行
  - capability 提升但成本显著上升时显式记录 tradeoff
  - profile promotion 必须基于对比证据

## Evidence And Runtime Inputs

平台不应重新定义 runtime 证据面，而应稳定消费已有产物。

平台需要直接消费：

- transcript
- session events
- model-io capture
- verifier output
- task events / outputs
- artifact snapshots
- environment result snapshots

这些输入来自 runtime foundations。平台要继续定义：

- 哪些输入是 primary evidence
- grader 如何访问这些证据
- 如何从 transcript + outcome reconstruct 一次完整 trial
- 报告和 compare 如何回链到这些证据

## Current Gaps

当前 repo 明确还没有：

- role-scoped eval config registry
- frozen evaluation snapshot contract
- system prompt bundle versioning
- controlled experiment matrix
- capability / regression suite lifecycle
- multi-trial runner
- aggregate scoring and compare workflow
- publishable agent profile model
- promotion workflow
- human review integration

当前已有能力仅能作为平台输入基础：

- transcript
- events
- model-io
- verifier result
- task lifecycle persistence
- observability primitives
- role / `USER.md` / role memory / role capability assembly

## Interface Decisions

本 proposal 固定以下术语边界：

- `RoleEvalConfig`
  - role 维度的可编辑评测资产
- `EvaluationSnapshot`
  - 一次 run 的不可变测试输入
- `EvaluationExperiment`
  - 同一 role 下的受控对比实验
- `AgentProfile`
  - 平台产出的推荐发布配置
- `evaluation suite`
  - task bank 的分组与运行单元
- `evaluation run`
  - 一次批量执行及其产物集合
- `trial`
  - 同一 task 的一次尝试
- `grader`
  - platform 中的评分逻辑
- `verifier`
  - runtime 中的验证执行体，可为 grader 提供证据，但两者不等同

平台不负责：

- 重写 runtime turn execution
- 重写 session persistence
- 重写 verifier runtime
- 把所有 grader 逻辑都塞回主 agent prompt

平台负责：

- 组织 role-scoped eval 资产
- 生成 frozen snapshot
- 编排 task / trial / grader / run / experiment
- 汇总 capability / regression 结果
- 生成 compare 结论与推荐 `AgentProfile`

## Alignment With Industry Practice

本 proposal 的术语与平台分层可参考 Anthropic 对 agent eval 的定义与实践经验，尤其是：

- task
- trial
- grader
- transcript
- outcome
- evaluation harness
- evaluation suite
- capability eval vs regression eval

参考：

- Anthropic, *Demystifying evals for AI agents*  
  https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents

## Acceptance Direction

当这篇 proposal 落地后，至少应满足：

- 同一 role 可维护 capability suite 与 regression suite 两类任务集
- evaluation run 启动后，会基于 role 资产生成 frozen snapshot
- 同一 role 下可对不同 model、不同 `USER.md`、minimal skills 有/无、不同 `SystemPromptBundle` 做 compare
- compare 结果可同时展示 quality、risk、token cost、latency
- 任一结论都能回链到 transcript / outcome / verifier / model-io evidence
- 平台可从 experiment 产出一个推荐 `AgentProfile`
