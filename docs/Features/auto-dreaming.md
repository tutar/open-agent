# Auto Dreaming

Auto Dreaming 是 open-agent 内置的异步记忆整合机制，灵感来自认知科学中的睡眠记忆巩固理论：Agent 在每轮对话结束后，在后台以三阶段 sweep 将高价值短期记忆自动晋升为持久化的 `MemoryRecord`，不阻塞主流程。

核心实现位于 `durable_memory/dreaming/`，通过 `SimpleHarness.maybe_schedule_dreaming()` 在 post-turn 阶段触发。

## 当前支持

### 触发与调度
- post-turn `MemoryMaintenanceProcessor` 自动调用 `maybe_schedule_dreaming()`
- `DreamingScheduler` 时间门控：`min_interval_seconds`（默认 86400 秒）防止同日重复运行
- `enabled=False` 时完全静默，不产生任何 job
- 异步线程池执行，通过 `MemoryStore.schedule(..., write_path=DREAM)` 提交 job

### 三阶段 Sweep（`DreamingEngine`）
- **Light 阶段**：扫描全部短期条目，提取主题词，记录 phase signals
- **REM 阶段**：按平均相关性排序，生成叙事性 Dream Diary 条目
- **Deep 阶段**：六维加权评分，过滤不合格候选，将通过门槛的条目晋升为 `MemoryRecord`

### 评分模型（`PromotionWeights`）
- 六个维度：`frequency`（0.24）、`relevance`（0.30）、`query_diversity`（0.15）、`recency`（0.15）、`consolidation`（0.10）、`conceptual_richness`（0.06），总权重恒为 1.00
- `phase_boost`：条目在多个阶段累计出现后附加加分，上限 0.10
- 默认晋升门槛：`min_score=0.45`、`min_recall_count=2`、`min_unique_queries=1`

### 晋升过滤
- `score_below_threshold`：评分不足时标记 `eligible=False`
- `recall_count_below_threshold`：召回次数不足时拒绝
- `unique_queries_below_threshold`：查询多样性不足时拒绝
- `duplicate`：与已有 `MemoryRecord`（content 或 title 匹配）重复时跳过，并记录 `skipped_refs`
- `source_not_grounded`：daily 来源文件已被删除或清空时拒绝晋升

### 短期记忆状态（`DreamingStateStore`）
- 条目持久化到 `memory/.dreams/short-term-recall.json`，支持跨 sweep 的 upsert 合并（recall_count、relevance_total、query_hashes、recall_days、concept_tags 均正确累加）
- phase signals 记录到 `memory/.dreams/phase-signals.json`，用于 phase_boost 计算
- 文件锁（`memory/.dreams/dreaming.lock`）防止并发 sweep 互相干扰
- 原子写：所有 JSON 文件先写 `.tmp` 再 replace，保证写入安全

### 数据摄取（Ingestion）
- 从当轮 session transcript 摄取 user/assistant 消息，自动过滤低信号文本（< 8 字符、含 `def /class /stack trace` 等）
- 从 `memory/*.md` 日常记忆文件逐行摄取，跳过 `DREAMS.md` 和 `MEMORY.md`
- PII 脱敏：`sanitize_session_corpus=True` 时自动将邮件地址、卡号替换为 `[redacted-*]`

### Markdown 产物（`DreamingMarkdownWriter`）
- 每个阶段报告写入 `memory/dreaming/<phase>/YYYY-MM-DD.md`，同时以 managed block（`<!-- openagent:dreaming:<phase>:start/end -->`）幂等更新 `DREAMS.md`
- Deep 阶段晋升条目追加到 `MEMORY.md` 的 `## Dream Promotions YYYY-MM-DD` 节
- Dream Diary 追加到 `DREAMS.md` 的 `## Dream Diary` 节，并明确标注"不作为晋升来源"
- 所有 Markdown 写入均幂等：同一内容重复写不产生重复块

### 配置（`DreamingConfig`）

`DreamingConfig` 是 Python 级别的 dataclass，在构造 `SimpleHarness` 时以参数形式传入，当前无独立 JSON 配置文件：

```python
from openagent.durable_memory.dreaming import DreamingConfig
from openagent.harness.runtime import SimpleHarness

harness = SimpleHarness(
    ...,
    dreaming_config=DreamingConfig(
        enabled=True,
        min_score=0.45,
        min_recall_count=2,
        min_interval_seconds=86400,
    ),
)
```

主要配置项：

| 字段 | 默认值 | 说明 |
|---|---|---|
| `enabled` | `false` | 必须显式开启，默认静默 |
| `frequency` | `"0 3 * * *"` | cron 表达式（调度参考，非 daemon 触发） |
| `min_interval_seconds` | `86400` | 两次 dreaming 之间的最短间隔（秒） |
| `lookback_days` | `7` | 短期记忆摄取窗口（天） |
| `max_candidates` | `20` | 每阶段最多处理候选数 |
| `min_score` | `0.45` | 晋升最低综合评分 |
| `min_recall_count` | `2` | 晋升要求的最少召回次数 |
| `min_unique_queries` | `1` | 晋升要求的最少不同查询数 |
| `write_markdown` | `true` | 是否生成阶段 Markdown 报告 |
| `write_memory_markdown` | `true` | 是否写入 `MEMORY.md` |
| `dream_diary_enabled` | `true` | 是否生成 Dream Diary 叙事条目 |
| `sanitize_session_corpus` | `true` | 摄取前是否 PII 脱敏 |
| `weights` | `PromotionWeights()` | 六维权重，可完整自定义 |

### 文件路径布局

所有路径均相对于 `FileMemoryStore` 的 `_dreaming_memory_root()`，实际为 `<memory_root>/../`（即 `FileMemoryStore(root)` 中 `root` 的父目录）。

在默认 `.openagent` 工作区布局下（`root = .openagent/agent_default/memory`）：

```
.openagent/
└── agent_default/
    ├── DREAMS.md                          # 各阶段 managed block 汇总 + Dream Diary
    ├── MEMORY.md                          # Deep 阶段晋升条目追加记录
    └── memory/
        ├── dream_<key>.json               # 晋升产出的 MemoryRecord 文件
        ├── .dreams/
        │   ├── short-term-recall.json     # 短期记忆条目（跨 sweep 持久化）
        │   ├── phase-signals.json         # 阶段信号计数（用于 phase_boost）
        │   ├── dreaming.lock              # 并发防护文件锁
        │   └── <checkpoint>.json          # 自定义 checkpoint（如 daily-ingestion）
        └── dreaming/
            ├── light/
            │   └── YYYY-MM-DD.md          # Light 阶段每日归档报告
            ├── rem/
            │   └── YYYY-MM-DD.md          # REM 阶段每日归档报告
            └── deep/
                └── YYYY-MM-DD.md          # Deep 阶段每日归档报告
```

`DreamingConfig` 中 `short_term_store_relative_path`、`phase_signal_relative_path`、`session_corpus_relative_path` 三个字段记录了机器状态文件相对于 `_dreaming_memory_root()` 的路径，通常无需修改。

### 晋升记录格式
- 晋升产出的 `MemoryRecord` id 前缀为 `dream_`，scope 为 `project`，type 为 `note`
- metadata 携带 `write_path=dream`、`dreaming_score`、`dreaming_components`、`dreaming_phase_boost`、`promotion_source=dreaming`

## 当前不支持

- 外部 cron/daemon 集成：当前调度完全依赖 runtime-local scheduler，无独立后台进程
- 跨 Agent 的 dreaming 共享：dreaming 状态目前是单 Agent workspace 隔离的
- 向量相似度去重：重复检测依赖文本归一化精确匹配，不支持语义近似去重
- 自定义摄取策略：transcript 过滤规则和 daily 文件摄取逻辑目前固定，不可插拔替换
- 晋升回调/事件：dreaming 完成后无 hook 通知外部系统
- 增量 phase signal 衰减：phase signals 只累加，无随时间衰减的遗忘机制
