# Monitoring Stack

本目录提供一套基于 `docker compose` 的本地最小可用监控栈，面向 `open-agent` 的指标、日志和链路观测。

## 技术栈

- `Prometheus`
- `Grafana`
- `OpenTelemetry Collector`
- `Loki`
- `Promtail`
- `Tempo`

说明：

- `OpenTelemetry Collector` 统一接收 OTLP traces / metrics / logs
- `Tempo` 是 OpenTelemetry trace 的存储后端，并启用了 `metrics-generator`
- `Prometheus` 同时承接 runtime metrics 与 Tempo 生成的 span metrics / service graph metrics
- `Loki` 是日志存储后端
- `Promtail` 负责采集 [`infra/monitoring/data/host-logs`](/home/tutar/work/agent-sdk/open-agent/infra/monitoring/data/host-logs) 下的本地文件日志

## 目录结构

```text
infra/monitoring/
├── docker-compose.yml
├── README.md
├── verify-monitoring.sh
├── data/
├── prometheus/
├── grafana/
├── loki/
├── promtail/
├── tempo/
└── otel/
```

## 数据目录

所有监控组件的本地持久化数据统一放在：

[`infra/monitoring/data`](/home/tutar/work/agent-sdk/open-agent/infra/monitoring/data)

当前约定如下：

- [`infra/monitoring/data/prometheus`](/home/tutar/work/agent-sdk/open-agent/infra/monitoring/data/prometheus)
- [`infra/monitoring/data/grafana`](/home/tutar/work/agent-sdk/open-agent/infra/monitoring/data/grafana)
- [`infra/monitoring/data/loki`](/home/tutar/work/agent-sdk/open-agent/infra/monitoring/data/loki)
- [`infra/monitoring/data/tempo`](/home/tutar/work/agent-sdk/open-agent/infra/monitoring/data/tempo)
- [`infra/monitoring/data/promtail`](/home/tutar/work/agent-sdk/open-agent/infra/monitoring/data/promtail)
- [`infra/monitoring/data/host-logs`](/home/tutar/work/agent-sdk/open-agent/infra/monitoring/data/host-logs)

Grafana 当前预置两张 dashboard：

- `OpenAgent Runtime Overview`
- `OpenAgent Session Drilldown`

`OpenAgent Runtime Overview` 的 token panel 当前显示：

- `Token Usage In Selected Range By Model And Callsite`
  - 基于 `openagent_token_usage_total`
  - 语义是 Grafana 当前选定时间段内的累计 token 使用量
  - 不是历史总量，也不是单次 request 的 gauge

## 启动方式

在仓库根目录执行：

```bash
docker compose -f infra/monitoring/docker-compose.yml up -d
```

查看状态：

```bash
docker compose -f infra/monitoring/docker-compose.yml ps
```

停止：

```bash
docker compose -f infra/monitoring/docker-compose.yml down
```

如果需要连同数据卷一起清理：

```bash
docker compose -f infra/monitoring/docker-compose.yml down -v
```

## 验证方式

启动完成后执行：

```bash
bash infra/monitoring/verify-monitoring.sh
```

验证脚本现在会同时检查：

- Docker Compose 配置有效
- Grafana / Prometheus / Tempo / Loki / OTel Collector 端点 ready
- Grafana datasource provisioning 正常
- Grafana dashboard provisioning 正常
- Prometheus scrape targets 正常
- OTLP traces / metrics / logs 能写入对应 backend
- Tempo metrics-generator 生成的 span metrics / service graph metrics 能回写到 Prometheus
- Promtail 采集本地文件日志并写入 Loki
- 会运行一次真实 `OpenAgent` smoke：
  - Prometheus 中出现 `openagent_duration_ms_milliseconds` /
    `openagent_token_usage` / `openagent_token_usage_total`
  - Tempo 中出现 `interaction / llm_request / tool` trace
  - Loki 中出现 `conversation / runtime_event / model_io` 三类日志流

`OpenAgent Session Drilldown` 当前同时提供两种 token 视图：

- `Token Usage By Session`
  - 当前窗口内每类 token 的 snapshot
- `Token Usage Cumulative Over Time`
  - 单个 session 内每类 token 随时间累计增长的线图

如果要切换采集模式：

```bash
bash infra/monitoring/switch-monitoring-mode.sh local
```

## 访问地址与登录账密

| 组件 | 地址 | 账密 |
|------|------|------|
| Grafana | `http://localhost:3000` | `admin / admin123` |
| Prometheus | `http://localhost:9090` | 无 |
| Loki API | `http://localhost:3100` | 无 |
| Tempo API | `http://localhost:3200` | 无 |
| OTel Collector OTLP gRPC | `http://localhost:4317` | 无 |
| OTel Collector OTLP HTTP | `http://localhost:4318` | 无 |
