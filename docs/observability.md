# Observabilidad

La Fase 14 añade observabilidad operativa pequeña y local, sin Prometheus
Server, Grafana, Loki, Sentry ni tracing distribuido.

## Logging

The event bus exposes `event_bus_published_total`, `event_bus_processed_total`,
`event_bus_processing_failures_total`, `event_bus_retries_total`, `event_bus_dlq_total`,
`event_bus_processing_duration_seconds`, `event_bus_pending`, `event_bus_leased` and
`event_bus_dlq_size`. Labels are limited to event type, consumer and status. Event IDs and
correlation IDs are structured log fields, never Prometheus labels.

Se usa `logging` estándar con `WALLAPOP_LOG_LEVEL=INFO`,
`WALLAPOP_LOG_FORMAT=human|json` y `WALLAPOP_METRICS_ENABLED=true|false`.
El formato JSON produce una entrada válida
por línea. Los eventos pueden incorporar IDs de correlación, estado, duración,
intento, items fetched y eventos creados. Destinos, URLs de webhook, tokens y
valores tipo bearer se redactan.

## Health and readiness

* `GET /health` solo indica que el proceso HTTP está vivo y no consulta la DB.
* `GET /ready` comprueba conexión, tablas utilizables y que
  `alembic_version` coincide con el head de Alembic. Nunca ejecuta migraciones
  ni llamadas externas y devuelve 503 si no está listo.
* `GET /metrics` expone el registro Prometheus local. La API es privada y no
  debe publicarse sin protección.

## Métricas

Se registran runs y duración, fallos, listings fetched, eventos creados,
requests HTTP a Wallapop, retries, 429, errores de parsing, entregas y fallos
de notificación, además de polls/jobs/active jobs del scheduler y requests de
la API. Los labels se limitan a valores de baja cardinalidad como
`source_type`, `status`, `channel`, `method`, `route`, `operation` y
`status_class`; nunca contienen IDs, destinos ni URLs completas.

`Metrics` acepta un `CollectorRegistry` independiente, evitando duplicados y
contaminación entre tests. El cliente HTTP conserva intactos sus retries,
`Retry-After`, rate limiter y límites existentes.

## Uso y limitaciones

```bash
WALLAPOP_LOG_FORMAT=json uvicorn wallapop_tracker.api.app:app
curl http://localhost:8000/metrics
```

La capa queda preparada para que en el futuro un Prometheus externo scrapee el
endpoint, pero no despliega ese sistema ni ofrece alertas, dashboards o
retención histórica de métricas.
The stats schema observation covers `counters[].type=reports_received`. A missing field is
kept distinct from an explicit zero: the parser stores `NULL` and the existing schema-drift
monitor can report the missing path without inventing a value.
