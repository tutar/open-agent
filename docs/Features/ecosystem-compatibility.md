# Ecosystem Compatibility

当前 OpenAgent 已有三类兼容层：

- Commands
- Skills
- MCP baseline

## 当前组件

- `StaticCommandRegistry`
- `FileSkillRegistry`
- `SkillActivator`
- `SkillInvocationBridge`
- `SkillCatalogEntry`
- `SkillActivationResult`
- `InMemoryMcpClient`
- `TransportBackedMcpClient`
- `InMemoryMcpTransport`
- `StdioMcpTransport`
- `StreamableHttpMcpTransport`
- `McpProtocolClient`
- `McpAuthorizationAdapter`
- `McpRootsProvider`
- `McpSamplingBridge`
- `McpElicitationBridge`
- `McpToolAdapter`
- `McpPromptAdapter`
- `McpResourceAdapter`
- `McpSkillAdapter`
- MCP tool/prompt/skill conformance baseline

## MCP

当前 MCP 已经拆到 `src/openagent/tools/mcp/`，并按四层组织：

- protocol client
- transport + auth
- runtime adaptation
- host extension

当前已经支持：

- `initialize -> initialized`
- protocol version / capability negotiation
- deterministic in-memory transport
- real `stdio` transport
- real `Streamable HTTP` transport with JSON / SSE parity
- auth discovery + token acquire + `WWW-Authenticate` scope upgrade
- tools/prompts/resources pagination
- roots list + `list_changed`
- resource subscribe + change notification observation
- sampling / elicitation host bridge seams

`mcp skill` 继续保留，但明确属于 host extension，不属于 MCP core。

## Skills

当前 skills 还支持：

- deterministic discovery precedence across scopes
- shadow diagnostics for conflicting skills
- `SKILL.md` frontmatter import with lenient parsing and diagnostics
- source / scope / trust metadata on discovered skills
- catalog disclosure vs activation disclosure vs resource disclosure separation
- structured activation wrapper with activation identity and resource listing
- activation dedupe, compaction protection, and bound-resource allowlisting baseline
- catalog disclosure distinct from activation disclosure
- wrapped activation result for dedupe / replay / compaction-friendly semantics

