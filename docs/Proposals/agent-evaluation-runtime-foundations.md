# Agent Evaluation Runtime Foundations

Status: proposed

## Summary

为了让 OpenAgent 具备“可评估的 agent”基础，首先要把 runtime 自身做成：

- 可留下完整轨迹
- 可复核真实结果
- 可把验证变成收尾闸门
- 可按项目扩展 verifier

当前代码已经具备一批 baseline：

- agent-owned transcript
- session event log
- model-io evidence capture
- verifier task handle
- local verification runtime
- tool policy / permission evaluation
- task / tool / llm / interaction observability

但这些能力还没有收敛成一套明确的 agent evaluation runtime contract。本 proposal 负责定义这套 contract 还缺什么，以及应该怎样补齐。

## Runtime Foundations

### 1. Trajectory And Evidence Capture

agent 的评估对象必须是完整 trajectory，不是最终回复文本。

runtime 需要稳定产出三份事实源：

- transcript
  - `user / assistant / tool-result` 视图
  - 用于 conversation-level grader 和 verifier 复核
- runtime events
  - turn / tool lifecycle
  - streaming delta
  - requires_action / completion / failure
- model-io evidence
  - assembled request
  - provider payload
  - provider raw response
  - provider usage / reasoning evidence when available

当前 baseline 已经存在，但还缺正式约束：

- transcript / events / model-io 三份事实源各自用于什么评估语义
- verifier 可以依赖哪些事实源
- 哪些信息必须保留到足以支撑离线 replay / audit
- 哪些 tool result / environment result 必须被证据化，而不是只留在最终自然语言总结里

### 2. Independent Verification Runtime

评估不应由主 agent 自评。runtime 需要独立 verifier 执行面。

当前已落地：

- verifier task
- `VerificationResult`
- local verification runtime
- verification command baseline

还需要继续落地的 contract：

- verifier identity
  - verifier 只负责验证，不负责实现
- verifier input contract
  - target session / task / workspace / acceptance target / evidence scope
- verifier output contract
  - verdict
  - summary
  - executed checks
  - raw command/output evidence references
  - limitations / unresolved risks
- verifier execution rules
  - 强制真实执行，不接受纯文本臆测
  - 默认要求 build/test/lint/typecheck/CLI/browser/API 这类可证据化检查优先
  - 对无法自动验证的场景显式降级为 partial，而不是伪装成 pass

### 3. Runtime Gate And Enforcement

只存在 verifier 还不够，评估必须进入主链路。

runtime 需要落地：

- completion gate
  - 非 trivial implementation 默认需要 verification evidence 才能算真正完成
- verification reminder
  - plan/turn/post-task 阶段对缺失验证进行显式提醒
- verifier retry / resume loop
  - 主 agent 根据 verifier 失败结果修复后，能再次进入 verifier
- evidence completeness checks
  - 防止“只写已验证”但没有命令/输出/结果证据

当前仓库已经有 verifier task 和部分 workflow nudge 基线，但还没有统一的 completion gate policy，也没有稳定的 verification completeness contract。

### 4. Risk Evaluation And Correctness Evaluation

agent evaluation 不能只看“做得对不对”，还要分清“该不该放行”。

runtime 至少要分成两类评估：

- risk evaluation
  - tool policy
  - permissions
  - sandbox boundary
  - approval requirement
- correctness evaluation
  - verifier
  - outcome checks
  - transcript / evidence review

当前 risk evaluation baseline 已存在于 tool policy / permission evaluation 中，但还缺统一术语和 runtime-facing contract，避免后续把 risk gate 和 correctness gate 混成一层。

### 5. Project-Specific Verifier Extension

评估必须贴近项目真实交付场景。

runtime 需要允许项目专用 verifier 扩展：

- API verifier
- CLI verifier
- frontend verifier
- workflow-specific verifier skill / command

要补齐的不是“通用 benchmark task bank”，而是：

- verifier prompt / command template
- capability exposure for verifier
- project-level verification policy injection
- result normalization so that different verifier specializations still produce the same verdict shape

## Current Gaps

当前仍未形成稳定能力的缺口主要是：

- 正式的 verifier input/output schema
- 正式的 verification evidence contract
- runtime completion gate policy
- verifier retry / resume / re-check contract
- adversarial verification baseline
- transcript / outcome / model-io 的统一评估口径
- risk evaluation 与 correctness evaluation 的统一术语和 observability attributes

这些缺口补齐后，OpenAgent 才能说“runtime 本身具备了可评估 agent 的最小闭环”。

## Interface Decisions

本 proposal 固定以下术语边界：

- `verification`
  - 在线、单任务、运行时内的验证动作
- `verifier`
  - 独立验证执行体
- `verdict`
  - verifier 给出的结构化结果
- `transcript`
  - conversation trajectory 视图
- `outcome`
  - 环境最终结果与验证结果，不等同于 transcript
- `risk evaluation`
  - 权限/放行边界评估
- `correctness evaluation`
  - 结果正确性评估

## Acceptance Direction

当这篇 proposal 落地后，至少应满足：

- 每个非 trivial implementation 都能留下可 replay 的评估证据
- verifier 可以独立运行并产出统一 verdict
- runtime 能把 verification 作为 completion gate 而不是可选补充
- 项目可以扩展专用 verifier，而不破坏统一 verdict / evidence contract
