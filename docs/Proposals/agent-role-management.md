# Agent Role Management

Status: implemented

## Summary

把 `role` 从当前只有 `role_id` 和目录前缀的薄概念，收敛成 OpenAgent 的正式长期资产模型。

当一个 agent 被赋予某个 role 时，它就继承这个 role 所拥有的：

- `ROLE.md`
- `USER.md`
- skills
- MCPs
- 建议使用的 models 列表
- role memory

这篇 proposal 只定义 role 本身是什么，以及 agent 如何继承 role 资产；不定义 channel、session、host 生命周期规则。

## Storage Layout

role 数据应收敛到 `OPENAGENT_ROOT/roles/` 下，并保持最小可实现结构：

```text
OPENAGENT_ROOT/
├── roles/
│  ├── <role_id>/
│  │  ├── ROLE.md
│  │  ├── USER.md
│  │  └── memory/
│  │     ├── MEMORY.md
│  │     └── TOPIC.md
│  ├── default/
│  │  ├── ROLE.md
│  │  ├── USER.md
│  │  └── memory/
│  └── ...
```

目录职责固定为：

- `ROLE.md`
  - role 的机器可读资源包装器
  - YAML frontmatter 承载最小机器可读真源
- `USER.md`
  - role 的主自然语言指令入口
  - 进入 runtime instruction assembly
- `memory/`
  - role 级 durable memory
  - 当前先只放：
    - `MEMORY.md`
    - `TOPIC.md`

`ROLE.md` 的 frontmatter 目前只约定最小字段集合：

- `role_id`
- `recommended_models`
- `skills`
- `mcps`

这里的 `skills` / `mcps` 只保存最小定义与引用信息，例如名称、标识、地址；不复制完整资产实现。机器主要读取 frontmatter，不依赖正文 section 解析。

## Current State

当前代码已经支持：

- `src/openagent/role/` 作为正式 role 域
- `OPENAGENT_ROOT/roles/<role_id>/` 的 `ROLE.md + USER.md + memory/`
- default role fallback
- agent 创建时的 role 绑定与 delegated subagent role 继承
- `USER.md` 进入 instruction assembly
- role memory 作为 durable-memory 正式根参与 recall、write-back、dreaming、consolidation
- role frontmatter 指定的最小 skill / MCP 装配
- `recommended_models` 作为 runtime metadata / guidance 暴露

## Proposed Design

### 1. Role As A First-Class Asset Boundary

`role` 是 agent 的长期身份与能力包，不是一次运行时参数。

role 至少应拥有这些资产：

- `ROLE.md`
  - role 的主定义入口
- skills
  - role 默认可用、默认暴露的 skill 集
- MCPs
  - role 默认挂载的 MCP server / adapter / capability 集
- recommended models
  - role 建议使用的 model 列表，而不是单一固定 model
- role memory
  - role 级 durable memory，如 `MEMORY.md`、`TOPIC.md`

这些资产都属于 role 根，而不属于某个 agent instance。
完整 plugin / skill / MCP 实现仍不放在 role 根；role 只定义“应挂载什么”。

### 2. Agent Creation Must Bind A Role

每个 agent 在创建时都必须被赋予一个 role。

这条规则适用于：

- default local agent
- delegated subagent
- future channel-bound agent

若没有显式 role，则绑定 `default` role，而不是让 agent 处于“无 role”状态。

### 3. Agent Inherits Role-Owned Capability Surface

agent 被赋予 role 后，runtime 看到的能力面应来自 role，而不是散落在 host 或 agent instance 目录中。

至少要把下面几类装配统一到 role：

- instruction assembly
  - 从 role 的 `USER.md` 进入 system/context assembly
- skill exposure
  - role 控制默认 skill 集
- MCP exposure
  - role 控制默认 MCP 集
- model guidance
  - role 提供 recommended model list，供 host/runtime 选择或约束
- role memory recall / write-back
  - role memory 作为 agent durable-memory 的正式根目录

这层的关键不是“所有能力都强绑定为单一配置”，而是明确真源在 role。

### 4. Role Root Becomes The Stable Authoring Location

role 根目录应成为长期维护位置：

- author `ROLE.md`
- maintain role memory
- maintain model recommendations

agent instance 目录只保留运行时资产：

- transcript
- workspace
- model-io
- task side-state
- parent/subagent refs

### 5. Role Does Not Own Session Lifecycle

这篇 proposal 明确不把下面内容写进 role contract：

- session creation
- channel binding
- appId / appSecret
- host preload behavior
- agent 与 session 的 1:1 绑定规则

这些运行时规则留给独立的 agent lifecycle proposal。

## Interface Decisions

本 proposal 固定以下边界：

- `role`
  - 长期身份、指令和能力资产边界
- `agent`
  - 运行实例，创建时必须绑定一个 role
- `role assignment`
  - agent 与 role 的一对一绑定
- `recommended models`
  - role 提供建议列表，不等于此 proposal 强制定死最终 model selection policy
- `ROLE.md`
  - role 的最小机器可读真源
- `USER.md`
  - role 的自然语言身份定义入口
- `skills` / `mcps`
  - 在 `ROLE.md` frontmatter 中只保存最小引用信息，不保存完整实现资产

## Acceptance Direction

当前落地后，至少满足：

- 每个 agent 都能明确回答自己属于哪个 role
- `ROLE.md`、`USER.md`、recommended models、skills/mcps 的最小引用信息、role memory 都有清晰的 role 归属
- runtime 会在 agent 创建时装配 role skills / MCPs，并把 `USER.md` 和 role memory 纳入模型输入链路
- role memory 不再是只读旁路，而是 durable-memory 正式写入路径的一部分
