# Agent Evaluation Platform

Status: proposed

## Summary

OpenAgent 当前已经提供了 transcript、events、model-io、verifier result 这些 runtime primitives，但还没有完整的 agent evaluation platform。

真正的平台层需要补的是：

- task bank
- multi-trial runner
- grader orchestration
- score aggregation
- capability / regression suite
- replay and reporting workflow

本 proposal 只讨论离线/批量/研发平台面，不重复定义 runtime 自身的 turn loop、tool loop、session store 或 verifier task 内核。

## Evaluation Platform Model

平台需要把评估对象收敛成一套稳定 object model：

- task
  - 一个测试样本或工作流场景
- trial
  - 一次具体运行
- transcript
  - 本次 trial 的 agent trajectory
- outcome
  - 环境最终状态、验证结果、side effects
- grader
  - 评分逻辑
- evaluation run
  - 一次批量执行
- evaluation suite
  - capability / regression / domain-specific benchmark 集合

当前 repo 还没有这套 object model 的统一实现。现有 runtime 只提供其中的证据输入面。

## Platform Capabilities

### 1. Task Bank And Suite Management

平台必须先有 task bank，而不是只靠人工临时挑几个 prompt。

需要落地：

- task definition format
  - input prompt
  - environment preconditions
  - expected outcome shape
  - allowed tools / constraints
- suite grouping
  - capability suite
  - regression suite
  - scenario / domain suite
- versioning
  - task definition changes可追踪
  - baseline score changes可追踪
- dataset curation workflow
  - 新增任务
  - 淘汰失效任务
  - 标记 flaky task

### 2. Trial Runner And Environment Control

平台需要把单次 runtime 执行扩展成 batch harness。

需要落地：

- batch execution harness
- repeated trials per task
- concurrency / queue policy
- timeout / retry / interruption policy
- environment setup and reset
- sandbox / fixture / seeded workspace control
- deterministic replay hooks when full determinism is unavailable

这里的关键不是重写 runtime，而是稳定调用 runtime，并把每次 trial 的 transcript / events / model-io / verifier result 归档到 evaluation run 下。

### 3. Grader Architecture

平台的 grader 必须是可组合的，而不是只剩一个 pass/fail verifier。

至少需要支持三类 grader：

- code-based grader
  - test pass/fail
  - UI/CLI/API/browser assertions
  - file system / artifact assertions
- model-based grader
  - 评估解释质量、策略质量、对话质量
  - 只应用在不能完全代码化的维度
- human grader
  - calibration
  - rubric-based review
  - disputed case adjudication

还需要落地：

- grader input contract
- grader output schema
- grader composition order
- score normalization
- pass/fail 与 scalar score 并存方式

### 4. Evidence Ingestion And Outcome Reconstruction

平台不能只吃最终文本，需要直接消费 runtime 证据。

需要支持的输入面：

- transcript
- session events
- model-io capture
- verifier output
- task events / outputs
- artifact snapshots
- environment result snapshots

平台要定义：

- 哪些输入是 primary evidence
- 哪些输入只是辅助调试
- 如何从 transcript + outcome reconstruct 一次完整 trial
- 如何让 grader 访问这些证据，而不是各自重新拼装

### 5. Scoring, Reporting, And Regression Analysis

平台最终要解决的是“怎么比较、怎么回归、怎么解释退化”。

需要落地：

- per-task result
- per-trial result
- per-suite aggregate
- capability score vs regression pass rate
- baseline compare
- score delta / pass-rate delta
- failure bucket / clustering
- run summary report
- replay links to transcript / events / model-io / artifacts

这里应把“监控”和“评估”继续分开：

- observability 解决运行期 telemetry
- evaluation platform 解决实验结果、质量回归、能力对比

## Current Gaps

当前 repo 明确还没有：

- task bank
- evaluation suite registry
- multi-trial runner
- batch evaluation harness
- grader orchestration
- aggregate scoring
- baseline compare workflow
- regression dashboard or report
- flaky task management
- human review integration

当前已有能力仅能作为平台的输入基础：

- transcript
- events
- model-io
- verifier result
- task lifecycle persistence
- observability primitives

## Interface Decisions

本 proposal 固定以下边界：

- `evaluation`
  - 指离线或批量的平台级评测活动
- `verification`
  - 指 runtime 内的在线单任务验证
- `grader`
  - platform 中的评分逻辑
- `verifier`
  - runtime 中的验证执行体，可为 grader 提供证据，但两者不等同
- `evaluation suite`
  - task bank 的分组与运行单元
- `evaluation run`
  - 一次批量执行及其产物集合

平台不负责：

- 重写 runtime turn execution
- 重写 session persistence
- 把所有 grader 逻辑都塞回主 agent prompt

平台负责：

- 稳定消费 runtime 产物
- 编排 task/trial/grader/run
- 生成可比较的结果与回归结论

## Acceptance Direction

当这篇 proposal 落地后，至少应满足：

- 同一 suite 可批量运行并保留每次 trial 的完整证据
- capability eval 与 regression eval 可以分开建模和汇总
- grader 可组合，且能统一输出结构化结果
- 任一回归结论都能追溯到 transcript / outcome / verifier evidence
