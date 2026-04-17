# Object Model

`object_model` 是整个 OpenAgent 的底层公共语言层。

它不负责执行逻辑，而负责把跨模块共享的数据结构固定下来，让 `harness`、`session`、`tools`、
`gateway` 和测试层可以围绕同一套事件和记录工作。

## Why It Exists

这个模块存在的原因有三个：

- 统一运行时事件格式
- 统一终态和任务记录格式
- 为 golden replay / conformance 测试提供稳定对象形状

## Main Types

当前最关键的对象有：

- `RuntimeEvent`
- `TerminalState`
- `RequiresAction`
- `ToolResult`
- `TaskRecord`
- `SchemaEnvelope`

配套枚举包括：

- `RuntimeEventType`
- `TerminalStatus`

## Design Principle

当前对象模型遵循这些原则：

- 保持扁平
- 保持 JSON-like
- 减少跨模块隐式约定
- 优先服务 replay 和测试稳定性

例如 `RuntimeEvent` 使用统一 envelope：

- `event_type`
- `event_id`
- `timestamp`
- `session_id`
- `payload`

这样 `gateway` 和 `session store` 不需要理解每一种事件的业务细节，只需要保存和投影它。

## Serialization Strategy

对象模型当前大量使用：

- `dataclass`
- `SerializableModel`
- `to_dict()`
- `from_dict()`

这样做的目的不是追求 ORM 风格，而是为了：

- 文件存储方便
- 测试断言简单
- bridge / gateway 输出稳定

## Current Limitation

当前 object model 还没有做更强的 schema evolution 策略。

例如：

- 没有显式版本迁移器
- 没有更严格的 event payload schema 注册
- 没有更细的 cursor / restore marker object

所以它现在更偏向“稳定 baseline”，还不是最终成熟模型层。
