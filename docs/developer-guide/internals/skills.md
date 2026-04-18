# Skills

`skills` 属于 `tools` 域，但 `Skill` 不是 `Tool`。

当前 OpenAgent 里的默认承载语义是：

- `SkillDefinition` 表示可发现、可激活的 skill 本体
- `SkillInvocationBridge` 把 model-invocable skill 投影成 `Command(kind=prompt)`
- `SkillTool` 只是 bridge tool，不等于 skill 本体

## Discovery And Import

`FileSkillRegistry` 现在承担 discovery/import 的本地 baseline：

- 扫描多个 `SkillDiscoveryRoot`
- 只把目录中存在 `SKILL.md` 的路径视为标准 skill
- 按 `project -> managed -> user -> bundled -> plugin -> mcp` 做 deterministic precedence
- 对被覆盖的 skill 产生 shadow diagnostics，而不是静默丢弃

发现阶段保留这些语义：

- `source`
- `scope`
- `trust_level`
- `skill_root`
- `skill_file`

当前 untrusted skill 不会默认进入 model-invocable catalog，但仍可以保留在 user-facing catalog 里。

## SKILL.md Definition

OpenAgent 现在会把 `SKILL.md` 拆成：

- frontmatter manifest
- markdown body

标准 frontmatter baseline：

- `name`
- `description`
- `license`
- `compatibility`
- `metadata`
- `allowed-tools`

同时支持当前 host extensions：

- `argument-hint`
- `arguments`
- `when_to_use`
- `version`
- `user-invocable`
- `disable-model-invocation`
- `hooks`
- `context`
- `agent`
- `effort`
- `paths`
- `shell`

解析策略是 lenient parsing：

- 第一次 frontmatter 解析失败后允许一次 normalization retry
- cosmetic 问题记 `warning`
- 结构不可用或关键字段缺失记 `error` 并 skip

## Disclosure And Activation

当前实现按 progressive disclosure 分三层：

1. Catalog disclosure
   - `SkillCatalogEntry`
   - 只暴露 `name`、`description`、`location?`、`source`、`invocable_by_model`
2. Activation disclosure
   - `SkillActivationResult`
   - 返回渲染后的 body、activation identity、wrapper metadata
3. Resource disclosure
   - 激活时只列出 `scripts/`、`references/`、`assets/`
   - 不会 eager read 全部资源

`SkillActivationResult` 当前固定包含：

- `skill_name`
- `body`
- `frontmatter_mode`
- `skill_root`
- `listed_resources`
- `wrapped`
- `activation_mode`
- `metadata`

## Context Management

`SkillContextManager` 负责 skills 激活后的长期语义：

- `mark_activated(...)`
- `is_already_active(...)`
- `protect_from_compaction(...)`
- `list_bound_resources(...)`

当前 baseline 已支持：

- activation dedupe
- compaction protection 标记
- skill root / listed resources 的 bound-resource allowlisting

这层语义让 skill activation 不再只是“一段 prompt 文本”，而是一个可追踪、可保护的上下文绑定。

## Capability Surface

skills 进入 capability surface 后会继续保留：

- `source`
- `scope`
- `trust_level`
- `disclosure`
- `activation_mode`
- `frontmatter_mode`
- `listed_resources`
- `diagnostics`
- `host_extensions`

这样 host 或 frontend 在做 catalog / picker / filtering 时，不需要重新理解技能导入细节。

## MCP Extension Boundary

`mcp skill` 继续存在，但明确属于 host extension。

它通过 `McpSkillAdapter` 适配到同一 skills surface，同时保留：

- `source = "mcp"`
- `server_id`
- `host_extension = "mcp_skill"`

这和本地 `SKILL.md` skills 共用 skill surface，但不属于 Agent Skills core definition。
