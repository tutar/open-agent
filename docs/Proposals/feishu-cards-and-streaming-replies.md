# Feishu Cards And Streaming Replies

Status: proposed

## Summary

将 Feishu channel 中当前依赖 slash 命令的交互改为飞书卡片按钮，并让 agent 回复通过飞书卡片流式更新。

## Scope

- 仅影响 `feishu` channel
- 不改变 terminal/TUI
- 不要求其他 channel 复用卡片交互模型
- WeChat 等未来 channel 如果不支持卡片，不受此 proposal 约束

## Current State

- Feishu 当前仍使用 slash 文本命令：
  - `/approve`
  - `/reject`
  - `/interrupt`
  - `/resume`
  - `/channel`
  - `/channel-config`
- 当前回复仍以文本消息为主
- reaction 已支持轻量状态提示
- 当前没有任何 CardKit / card action / streaming updates 实现基础

## Proposed Design

- 在 Feishu 中完全替换 slash 命令，不保留 slash 兼容路径
- 对审批、恢复、host/channel 管理使用交互卡片按钮
- 普通 agent 回复优先通过卡片承载，并用飞书卡片 streaming updates 做增量更新
- reaction 保留为轻量状态提示，但不承担命令交互职责

## Replaced Feishu Slash Commands

- `/approve`
- `/reject`
- `/interrupt`
- `/resume`
- `/channel`
- `/channel-config`

## Interaction Model

- 需要人工操作时，Feishu host 发送交互卡片
- 卡片按钮回传 action payload
- host 将 action payload 映射为现有 canonical control / management intent
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

## 参考
- 卡片按钮：https://open.feishu.cn/document/feishu-cards/card-json-v2-components/interactive-components/button
- 流式更新卡片：https://open.feishu.cn/document/cardkit-v1/streaming-updates-openapi-overview?lang=zh-CN