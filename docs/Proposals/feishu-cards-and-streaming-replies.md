# Feishu Cards And Streaming Replies

Status: partially completed

## Summary

将 Feishu channel 中当前依赖 slash 命令的交互改为飞书卡片按钮，并让 agent 回复通过飞书卡片流式更新。

## Scope

- 仅影响 `feishu` channel
- 不改变 terminal/TUI
- 不要求其他 channel 复用卡片交互模型
- WeChat 等未来 channel 如果不支持卡片，不受此 proposal 约束

## Current State

- Feishu 控制流此前依赖 slash 文本命令
- 当前 `/channel` 与 `/channel-config` 仍保留为暂存的 management 路径
- reply card、card action、reaction、retry 隔离与流式更新链路都已落地
- OpenAI-compatible provider 已接入真实 `stream=true` 与 `assistant_delta`
- Feishu reply card 已能消费 `assistant_delta`，并按短时间窗口聚合后刷新同一张卡

## Implementation Status

### 已完成

- 普通 Feishu turn 默认创建单 turn reply card
- reply card 优先走 CardKit streaming updates；租户权限或平台能力不足时，会自动降级为对同一张消息卡片做 patch 更新
- `approve/reject` 已从文本 slash 命令切到卡片按钮
- 审批卡片按钮只保留 `Approve / Reject`
- `interrupt` 与 `resume` 继续保留为主动控制语义，不承载在审批卡片按钮中
- card action 默认通过飞书长连接事件进入 host
- 卡片发送/更新失败会进入 file-backed retry queue
- pending card 重试已按当前 `conversation_id` 隔离，不会再被其他 chat 触发
- 原消息 reaction 已支持：
  - 处理中 `OneSecond`
  - 完成 `DONE`
- OpenAI-compatible provider 已支持真实 streaming，请求会发送 `stream=true`
- terminal TUI 与 Feishu reply card 都已消费 `assistant_delta`
- Feishu reply card 为避免远程卡片更新过慢，已改为短时间窗口聚合 delta，再刷新同一张卡；终态会强制尾包 flush
- Feishu 卡片中的 markdown 显示仍不稳定，尤其是：
  - `###` 这类标题语法
  - 粗体标题
  - pipe table 表格

### 当前遗留

- 当前已经把 reply 正文拆成独立 markdown 区块，避免和 `Request / Status / Reply` 标签混在同一个大 markdown 串里；但真实飞书客户端中的最终渲染仍未达到预期
- 这说明剩余问题更接近飞书消息卡 markdown 组件本身的支持边界、或当前卡片 schema/组件选型仍不足，而不是“没有使用 markdown tag”
- `/channel` 与 `/channel-config` 仍未迁到独立的 host management page

## Proposed Design

- 在 Feishu 中将控制命令替换为交互卡片按钮
- `/channel` 与 `/channel-config` 不在本次实现范围，后续转到 host management page
- 普通 agent 回复通过单 turn reply card 承载，并通过 Feishu CardKit streaming updates 形成流式体验
- reaction 保留为轻量状态提示，但不承担命令交互职责

## Replaced Feishu Slash Commands

- `/approve`
- `/reject`

## Interaction Model

- 需要人工操作时，Feishu host 发送交互卡片
- 卡片按钮回传 action payload
- host 将 action payload 映射为现有 canonical control intent
- 普通回复开始时创建卡片，生成中持续更新，完成后进入稳定终态

## Required States

- thinking / running
- requires_action
- completed
- failed
- interrupted

## Host / Gateway Boundary

- Gateway 继续处理 canonical control / management intent
- Feishu host 负责 card action ingress
- Feishu host 负责 card create / update / finalize
- 这套 card 交互不提升为跨 channel 通用契约

## Delivered Behavior

- 普通 Feishu turn 现在默认创建单 turn reply card，并优先通过 CardKit streaming updates 更新同一张卡；如果租户权限或平台能力不足，会自动降级为对同一张消息卡片做 patch 更新
- `requires_action` 通过卡片按钮触发 `approve/reject`
- 审批卡片按钮只包含 `Approve` / `Reject`
- `interrupt` 与 `resume` 继续保留为主动控制语义，不承载在审批卡片按钮中
- card action 默认通过飞书长连接事件进入 host
- 卡片发送/更新失败会进入 file-backed retry queue
- pending card 重试按当前 `conversation_id` 隔离
- 在补发成功前，原消息保持 `OneSecond` reaction；成功后切 `DONE`
- reply card 的 CardKit 跟踪标识以 `card_id + uuid + sequence` 维护，不再依赖 `im.v1.message.update`
- OpenAI-compatible provider 已接入真实 `stream=true`
- Feishu reply card 已消费 `assistant_delta`，并按窗口聚合后更新同一张卡

## Known Gaps

- Feishu markdown 组件的最终渲染效果仍未满足预期，尤其是标题、粗体和表格
- 当前 reply card 虽然已经把模型回复作为独立 markdown 区块渲染，但在真实飞书客户端中仍会出现：
  - markdown 特殊字符原样显示
  - 标题未按标题样式显示
  - 表格样式异常
- 这个问题保留在本 proposal 中继续跟踪，后续需要进一步验证：
  - 飞书 markdown 组件的真实支持子集
  - 是否需要改成更细粒度的卡片组件组合，而不是继续依赖单个 markdown 区块承载复杂排版

## 参考
- 卡片按钮：https://open.feishu.cn/document/feishu-cards/card-json-v2-components/interactive-components/button
- 流式更新卡片：https://open.feishu.cn/document/cardkit-v1/streaming-updates-openapi-overview?lang=zh-CN
