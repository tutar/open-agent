# Pre-Merge Checklist

这份清单用于合入 `main` 前的最终验收。

它和 CI 的关系是：

- CI 负责稳定、可重复、无外部依赖的门禁
- 这份清单补充本地人工验收，尤其是 Feishu 真实网络链路

## 1. OpenAgent Python Baseline

在当前项目根目录执行：

```bash
ruff check src tests
ruff format --check src tests
mypy src
pytest -q tests -m 'not feishu_e2e and not feishu_group_e2e'
```

预期：

- 全部通过
- 没有新增 lint / type error
- 非真实网络测试保持稳定

## 2. Terminal TUI Baseline

如果本次改动涉及 `frontend/terminal-tui`，再执行：

```bash
cd frontend/terminal-tui
npm ci
npm run type-check
```

预期：

- 前端依赖可以安装
- type-check 通过

## 3. Feishu Private E2E

准备环境变量：

```bash
export OPENAGENT_RUN_FEISHU_E2E=1
export OPENAGENT_FEISHU_APP_ID=cli_xxx
export OPENAGENT_FEISHU_APP_SECRET=xxx
export OPENAGENT_FEISHU_E2E_P2P_CHAT_ID=oc_xxx
```

执行：

```bash
pytest -q tests/test_feishu_e2e.py -m feishu_e2e
```

预期：

- 私聊普通回复通过
- 私聊审批继续通过
- `/resume` 通过
- 工具进度通知通过

说明：

- “首条就是 `/approve`” 这类真实飞书私聊控制消息不稳定，不作为真实 E2E 门禁
- 这类场景继续由确定性单元测试覆盖

## 4. Feishu Group E2E

在前面的环境变量基础上，再补：

```bash
export OPENAGENT_RUN_FEISHU_GROUP_E2E=1
export OPENAGENT_FEISHU_E2E_GROUP_ID=oc_3b0779b193e78de1052091fae2c272d8
export OPENAGENT_FEISHU_E2E_BOT_NAME=openagent
```

执行：

```bash
pytest -q tests/test_feishu_e2e.py -m feishu_group_e2e
```

预期：

- 群聊 `@openagent ...` 能被接收
- mention 文本会被清洗，不把 `@openagent` 或 `@_user_1` 之类占位符带进模型输入
- 群聊普通文本在 `mention_required_in_group=true` 下会被忽略

## 5. Feishu Runtime Sanity

如果本次改动触及 Feishu host / gateway，额外确认：

- 同一台机器上同一个 `app_id` 只运行一个 Feishu host
- 第二个 host 启动时会明确报错，而不是静默抢占连接
- host 日志中能看到：

```text
feishu-host> starting long connection
feishu-host> received raw event
feishu-host> normalized input
feishu-host> sending outbound
feishu-host> agent send_text
```

## 6. Docs And TODO Hygiene

合入前再检查：

- `README.md` 是否需要更新
- `docs/get-started.md` 是否需要更新
- `docs/developer-guide/` 下相关文档是否同步
- `todo.md` 是否新增或消除待办

## 7. Merge Decision

满足下面条件才建议合入 `main`：

- CI 门禁全部通过
- 本次改动影响到的模块都有测试覆盖
- 如果涉及 Feishu 链路，至少完成对应的私聊或群聊真实 E2E
- 文档和 `todo.md` 已同步
