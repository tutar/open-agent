#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_FILE="${SCRIPT_DIR}/docker-compose.yml"
MODE="${1:-local}"
SMOKE_ID="smoke-$(date +%s)"
SMOKE_SERVICE_CLIENT="openagent-smoke-client-${SMOKE_ID}"
SMOKE_SERVICE_SERVER="openagent-smoke-server-${SMOKE_ID}"
OPENAGENT_SMOKE_SERVICE="openagent-runtime-smoke"
OPENAGENT_SMOKE_SESSION="sess-monitoring-smoke"
SMOKE_LOG_FILE="${SCRIPT_DIR}/data/host-logs/smoke.log"
SMOKE_LOG_LINE="openagent monitoring smoke ${SMOKE_ID}"

http_get() {
  local url="$1"
  curl --noproxy '*' -fsS "${url}"
}

prom_query() {
  local query="$1"
  curl --noproxy '*' -fsSG \
    "http://127.0.0.1:9090/api/v1/query" \
    --data-urlencode "query=${query}"
}

tempo_search() {
  local tags="$1"
  curl --noproxy '*' -fsSG \
    "http://127.0.0.1:3200/api/search" \
    --data-urlencode "tags=${tags}"
}

wait_prom_query_nonempty() {
  local query="$1"
  local attempts="${2:-30}"
  local sleep_seconds="${3:-2}"
  local response
  local i

  for ((i = 1; i <= attempts; i++)); do
    response="$(prom_query "${query}")"
    if [[ "${response}" != *'"result":[]'* ]]; then
      return 0
    fi
    sleep "${sleep_seconds}"
  done

  echo "prometheus query returned empty result: ${query}" >&2
  return 1
}

wait_loki_query_contains() {
  local query="$1"
  local needle="$2"
  local attempts="${3:-30}"
  local sleep_seconds="${4:-2}"
  local encoded_query
  local response
  local i

  for ((i = 1; i <= attempts; i++)); do
    response="$(
      curl --noproxy '*' -fsSG \
        "http://127.0.0.1:3100/loki/api/v1/query_range" \
        --data-urlencode "query=${query}" \
        --data-urlencode 'limit=20'
    )"
    if [[ "${response}" == *"${needle}"* ]]; then
      return 0
    fi
    sleep "${sleep_seconds}"
  done

  echo "loki query did not contain expected line: ${needle}" >&2
  return 1
}

wait_tempo_search_nonempty() {
  local tags="$1"
  local attempts="${2:-30}"
  local sleep_seconds="${3:-2}"
  local response
  local i

  for ((i = 1; i <= attempts; i++)); do
    response="$(tempo_search "${tags}")"
    if [[ "${response}" != *'"traces":[]'* ]]; then
      return 0
    fi
    sleep "${sleep_seconds}"
  done

  echo "tempo search returned no traces for tags: ${tags}" >&2
  return 1
}

wait_grafana_datasource() {
  local uid="$1"
  local attempts="${2:-30}"
  local sleep_seconds="${3:-2}"
  local response
  local i

  for ((i = 1; i <= attempts; i++)); do
    response="$(
      curl --noproxy '*' -fsS \
        -u admin:admin123 \
        "http://127.0.0.1:3000/api/datasources/uid/${uid}"
    )"
    if [[ "${response}" == *"\"uid\":\"${uid}\""* ]]; then
      return 0
    fi
    sleep "${sleep_seconds}"
  done

  echo "grafana datasource unavailable: ${uid}" >&2
  return 1
}

wait_grafana_dashboard() {
  local uid="$1"
  local attempts="${2:-30}"
  local sleep_seconds="${3:-2}"
  local response
  local i

  for ((i = 1; i <= attempts; i++)); do
    response="$(
      curl --noproxy '*' -fsS \
        -u admin:admin123 \
        "http://127.0.0.1:3000/api/dashboards/uid/${uid}"
    )"
    if [[ "${response}" == *"\"uid\":\"${uid}\""* ]]; then
      return 0
    fi
    sleep "${sleep_seconds}"
  done

  echo "grafana dashboard unavailable: ${uid}" >&2
  return 1
}

write_smoke_metric_payload() {
  local now_ns="$1"
  local payload_path="$2"
  cat >"${payload_path}" <<EOF
{
  "resourceMetrics": [
    {
      "resource": {
        "attributes": [
          {"key": "service.name", "value": {"stringValue": "openagent-monitoring-smoke"}},
          {"key": "service.instance.id", "value": {"stringValue": "${SMOKE_ID}"}},
          {"key": "deployment.environment", "value": {"stringValue": "docker-compose"}}
        ]
      },
      "scopeMetrics": [
        {
          "scope": {"name": "infra.monitoring.verify"},
          "metrics": [
            {
              "name": "openagent_smoke_gauge",
              "description": "Monitoring stack smoke metric",
              "unit": "1",
              "gauge": {
                "dataPoints": [
                  {
                    "timeUnixNano": "${now_ns}",
                    "asDouble": 1,
                    "attributes": [
                      {"key": "scope", "value": {"stringValue": "runtime"}},
                      {"key": "callsite", "value": {"stringValue": "monitoring.verify"}},
                      {"key": "smoke_id", "value": {"stringValue": "${SMOKE_ID}"}}
                    ]
                  }
                ]
              }
            }
          ]
        }
      ]
    }
  ]
}
EOF
}

write_smoke_trace_payload() {
  local start_ns="$1"
  local client_end_ns="$2"
  local server_start_ns="$3"
  local server_end_ns="$4"
  local payload_path="$5"
  local trace_id
  local client_span_id
  local server_span_id

  trace_id="11223344556677889900aabbccddeeff"
  client_span_id="1020304050607080"
  server_span_id="1020304050607081"

  cat >"${payload_path}" <<EOF
{
  "resourceSpans": [
    {
      "resource": {
        "attributes": [
          {"key": "service.name", "value": {"stringValue": "${SMOKE_SERVICE_CLIENT}"}},
          {"key": "deployment.environment", "value": {"stringValue": "docker-compose"}}
        ]
      },
      "scopeSpans": [
        {
          "scope": {"name": "infra.monitoring.verify"},
          "spans": [
            {
              "traceId": "${trace_id}",
              "spanId": "${client_span_id}",
              "name": "monitoring-smoke-client",
              "kind": 3,
              "startTimeUnixNano": "${start_ns}",
              "endTimeUnixNano": "${client_end_ns}"
            }
          ]
        }
      ]
    },
    {
      "resource": {
        "attributes": [
          {"key": "service.name", "value": {"stringValue": "${SMOKE_SERVICE_SERVER}"}},
          {"key": "deployment.environment", "value": {"stringValue": "docker-compose"}}
        ]
      },
      "scopeSpans": [
        {
          "scope": {"name": "infra.monitoring.verify"},
          "spans": [
            {
              "traceId": "${trace_id}",
              "spanId": "${server_span_id}",
              "parentSpanId": "${client_span_id}",
              "name": "monitoring-smoke-server",
              "kind": 2,
              "startTimeUnixNano": "${server_start_ns}",
              "endTimeUnixNano": "${server_end_ns}"
            }
          ]
        }
      ]
    }
  ]
}
EOF
}

write_smoke_log_payload() {
  local now_ns="$1"
  local payload_path="$2"
  cat >"${payload_path}" <<EOF
{
  "resourceLogs": [
    {
      "resource": {
        "attributes": [
          {"key": "service.name", "value": {"stringValue": "openagent-monitoring-smoke"}},
          {"key": "deployment.environment", "value": {"stringValue": "docker-compose"}}
        ]
      },
      "scopeLogs": [
        {
          "scope": {"name": "infra.monitoring.verify"},
          "logRecords": [
            {
              "timeUnixNano": "${now_ns}",
              "severityText": "INFO",
              "body": {"stringValue": "${SMOKE_LOG_LINE}"},
              "attributes": [
                {"key": "smoke_id", "value": {"stringValue": "${SMOKE_ID}"}}
              ]
            }
          ]
        }
      ]
    }
  ]
}
EOF
}

send_smoke_telemetry() {
  local tmp_dir
  local metric_payload
  local trace_payload
  local log_payload
  local now_ns
  local client_end_ns
  local server_start_ns
  local server_end_ns

  tmp_dir="$(mktemp -d)"
  trap 'rm -rf "${tmp_dir}"' RETURN

  metric_payload="${tmp_dir}/metric.json"
  trace_payload="${tmp_dir}/trace.json"
  log_payload="${tmp_dir}/log.json"
  now_ns="$(date +%s%N)"
  client_end_ns="$((now_ns + 20000000))"
  server_start_ns="$((now_ns + 5000000))"
  server_end_ns="$((now_ns + 15000000))"

  write_smoke_metric_payload "${now_ns}" "${metric_payload}"
  write_smoke_trace_payload "${now_ns}" "${client_end_ns}" "${server_start_ns}" "${server_end_ns}" "${trace_payload}"
  write_smoke_log_payload "${now_ns}" "${log_payload}"

  curl --noproxy '*' -fsS \
    -H 'Content-Type: application/json' \
    -X POST \
    --data @"${metric_payload}" \
    http://127.0.0.1:4318/v1/metrics >/dev/null

  curl --noproxy '*' -fsS \
    -H 'Content-Type: application/json' \
    -X POST \
    --data @"${trace_payload}" \
    http://127.0.0.1:4318/v1/traces >/dev/null

  curl --noproxy '*' -fsS \
    -H 'Content-Type: application/json' \
    -X POST \
    --data @"${log_payload}" \
    http://127.0.0.1:4318/v1/logs >/dev/null

  mkdir -p "$(dirname "${SMOKE_LOG_FILE}")"
  printf '%s\n' "${SMOKE_LOG_LINE}" >>"${SMOKE_LOG_FILE}"
}

send_openagent_smoke() {
  OPENAGENT_OTLP_HTTP_ENDPOINT="http://127.0.0.1:4318" \
  OPENAGENT_OTLP_SERVICE_NAME="${OPENAGENT_SMOKE_SERVICE}" \
  OPENAGENT_OBSERVABILITY_STDOUT=false \
  PYTHONPATH="${SCRIPT_DIR}/../../src" \
  python "${SCRIPT_DIR}/smoke_openagent_observability.py"
}

wait_http_ok() {
  local url="$1"
  local attempts="${2:-30}"
  local sleep_seconds="${3:-2}"
  local i

  for ((i = 1; i <= attempts; i++)); do
    if curl --noproxy '*' -fsS "${url}" >/dev/null; then
      return 0
    fi
    sleep "${sleep_seconds}"
  done

  echo "endpoint not ready: ${url}" >&2
  return 1
}

echo "[1/6] validating compose file"
docker compose -f "${COMPOSE_FILE}" config >/dev/null

echo "[2/6] checking required endpoints"
wait_http_ok http://127.0.0.1:9090/-/ready
wait_http_ok http://127.0.0.1:3100/ready
wait_http_ok http://127.0.0.1:3200/ready 45 2
wait_http_ok http://127.0.0.1:13133
wait_http_ok http://127.0.0.1:3000/api/health

echo "[3/6] checking Grafana datasources"
wait_grafana_datasource prometheus
wait_grafana_datasource loki
wait_grafana_datasource tempo
wait_grafana_dashboard openagent-runtime-overview
wait_grafana_dashboard openagent-session-drilldown

echo "[4/6] checking Prometheus scrape targets"
wait_prom_query_nonempty 'up{job="prometheus"} == 1'
wait_prom_query_nonempty 'up{job="otel-collector"} == 1'
wait_prom_query_nonempty 'up{job="otel-collector-otlp-metrics"} == 1'
wait_prom_query_nonempty 'up{job="loki"} == 1'
wait_prom_query_nonempty 'up{job="tempo"} == 1'
wait_prom_query_nonempty 'up{job="promtail"} == 1'

echo "[5/6] sending smoke telemetry"
send_smoke_telemetry
send_openagent_smoke

echo "[6/6] verifying telemetry paths"
wait_prom_query_nonempty "openagent_smoke_gauge_ratio{smoke_id=\"${SMOKE_ID}\"}"
wait_prom_query_nonempty "traces_spanmetrics_calls_total{service=\"${SMOKE_SERVICE_SERVER}\"}"
wait_prom_query_nonempty "traces_service_graph_request_total{client=\"${SMOKE_SERVICE_CLIENT}\",server=\"${SMOKE_SERVICE_SERVER}\"}"
wait_tempo_search_nonempty "service.name=${SMOKE_SERVICE_SERVER}"
wait_loki_query_contains "{job=\"openagent-local\"}" "${SMOKE_LOG_LINE}"
wait_prom_query_nonempty "openagent_duration_ms_milliseconds{scope=\"turn\",metric_kind=\"total_duration_ms\",session_id=\"${OPENAGENT_SMOKE_SESSION}\"}"
wait_prom_query_nonempty "openagent_token_usage{metric_kind=\"token_usage\",session_id=\"${OPENAGENT_SMOKE_SESSION}\"}"
wait_prom_query_nonempty "openagent_token_usage_total{metric_kind=\"token_usage\",session_id=\"${OPENAGENT_SMOKE_SESSION}\"}"
wait_prom_query_nonempty "increase(openagent_token_usage_total{token_type=\"total_tokens\",session_id=\"${OPENAGENT_SMOKE_SESSION}\"}[30m])"
wait_tempo_search_nonempty "service.name=${OPENAGENT_SMOKE_SERVICE}"
wait_loki_query_contains "{exporter=\"OTLP\",service_name=\"${OPENAGENT_SMOKE_SERVICE}\"}" "\\\"role\\\":\\\"assistant\\\""
wait_loki_query_contains "{exporter=\"OTLP\",service_name=\"${OPENAGENT_SMOKE_SERVICE}\"}" "\\\"event_type\\\":\\\"tool_started\\\""
wait_loki_query_contains "{exporter=\"OTLP\",service_name=\"${OPENAGENT_SMOKE_SERVICE}\"}" "\\\"provider_adapter\\\":\\\"ToolThenReplyExchangeModel\\\""

if [[ "${MODE}" == "k8s" || "${MODE}" == "dual" ]]; then
  echo "[7/8] checking k8s monitoring path"
  kubectl get deploy/prometheus-k8s-agent -n apps >/dev/null
  kubectl get daemonset/promtail-k8s -n apps >/dev/null
  wait_http_ok "http://127.0.0.1:9090/api/v1/query?query=sum%20by%20(service)%20(up%7Bruntime_mode%3D%22k8s%22%7D)"
  echo "[8/8] monitoring stack is ready"
else
  echo "monitoring stack is ready"
fi
