# Arquitectura de persistencia — Fase 3

Estado: diseño cerrado para seguimiento semanal, pendiente de implementación.

## Objetivo y frecuencia

El sistema está optimizado para aproximadamente una captura completa por perfil y semana. Se priorizan simplicidad, trazabilidad, histórico consultable y comportamiento conservador ante fallos. No se diseñará una cola de eventos de alta frecuencia ni una estrategia de optimización para millones de registros.

## Flujo de una captura

```text
crear TrackingRun (running)
        ↓
capturar perfil, estadísticas y todas las páginas de anuncios
        ↓
¿todo terminó y es coherente?
   ├── no → TrackingRun partial/failed; no cambiar presencia
   └── sí → TrackingRun valid
                  ↓
          comparar con última captura válida
                  ↓
          actualizar entidades de identidad y snapshots si procede
```

La persistencia y la comparación deben ejecutarse dentro de una única transacción de base de datos para una captura válida. Si la transacción falla, el `TrackingRun` debe quedar auditado como fallido y no debe quedar una captura válida parcialmente aplicada.

## Estados de `TrackingRun`

Valores previstos:

- `running`: intento creado pero todavía no finalizado.
- `valid`: perfil y listado completo recuperados y persistidos correctamente.
- `partial`: se obtuvieron datos, pero faltó alguna parte necesaria para considerar completa la captura.
- `failed`: error que impide obtener o persistir la captura.

Semántica:

| Estado | Auditoría en `tracking_runs` | Actualiza presencia | Crea snapshots | Confirma desapariciones |
|---|---:|---:|---:|---:|
| `valid` | Sí | Sí | Sí, si hay cambios | Sí |
| `partial` | Sí | No | No | No |
| `failed` | Sí | No | No | No |

En particular, una captura `partial` no actualiza `last_seen_at`, ni siquiera para anuncios recuperados antes del fallo. Se prefiere perder una observación semanal antes que registrar presencia basada en un inventario incompleto.

Una captura `valid` con cero anuncios solo será válida si se han completado todas las páginas y la fuente ha confirmado correctamente el final de la paginación. En ese caso sí puede confirmar la desaparición de los anuncios previamente activos.

## Representación de presencia

No se añadirá una tabla específica para desapariciones. La transición histórica se almacenará en `listing_snapshots` mediante un campo separado:

```text
presence_state: ACTIVE | REMOVED
```

El campo `status` del anuncio, si existe, seguirá representando el estado que Wallapop expone para el anuncio —por ejemplo, reservado— y no se utilizará para representar presencia histórica.

Ejemplo conceptual:

| observed_at | item_id | presence_state | price |
|---|---|---|---:|
| Semana 1 | A | `ACTIVE` | 120 |
| Semana 3 | A | `REMOVED` | 120 |
| Semana 5 | A | `ACTIVE` | 110 |

La reaparición se representa como un nuevo snapshot `ACTIVE` posterior a `REMOVED`. Más adelante el diff engine podrá derivar `REAPPEARED`; no se persistirá todavía una entidad `Event`.

`REMOVED` no significa `SOLD`. La desaparición se registra únicamente como ausencia confirmada en una captura completa y válida. Aunque aumente `sold_count`, no se atribuirá una venta a un `item_id` concreto sin una señal explícita de Wallapop.

## Entidades previstas

### `profiles`

Identidad estable del perfil:

```text
id
wallapop_user_id UNIQUE
slug
name
url
first_seen_at
last_seen_at
```

`last_seen_at` se actualiza solo al observar correctamente el perfil en un `TrackingRun` `valid`.

### `tracking_runs`

Auditoría de cada intento, incluidos los que no generan cambios:

```text
id
profile_id                 # nullable; source for profile runs
tracked_search_id          # nullable; source for search runs
started_at
finished_at
status                 # running | valid | partial | failed
error_message
items_fetched
pages_fetched
```

Exactly one of `profile_id` and `tracked_search_id` is required by a database
constraint. Search runs do not create a synthetic `ProfileRecord`; listings
seen only from searches may temporarily have a null `profile_id`.

Se pueden añadir detalles operativos pequeños si resultan útiles para diagnóstico, pero no se almacenarán headers, cookies ni secretos.

### `profile_snapshots`

Snapshot de métricas únicamente cuando cambian respecto a la última captura válida:

```text
id
profile_id
tracking_run_id
observed_at
rating
review_count
published_count
sold_count
raw_stats_json
```

La comparación se hace contra el último `profile_snapshot` de una captura válida. Una ejecución `partial` o `failed` no puede convertirse en referencia.

### `listings`

Identidad estable del anuncio:

```text
id
marketplace
external_id UNIQUE per marketplace
profile_id
first_seen_at
last_seen_at
```

`first_seen_at` se fija en la primera observación `ACTIVE` válida. `last_seen_at` representa la última observación válida en la que el anuncio apareció, no el momento en que se confirmó su desaparición. Por tanto, permite calcular una duración publicada aproximada con precisión semanal.

Una ausencia válida crea el snapshot `REMOVED`, pero no mueve `last_seen_at` hacia delante. Si el mismo anuncio reaparece, vuelve a actualizarse `last_seen_at` y se crea un snapshot `ACTIVE`.

### `listing_snapshots`

Histórico de cambios relevantes y transiciones de presencia:

```text
id
listing_id
tracking_run_id
observed_at
presence_state         # ACTIVE | REMOVED
title
description
price
currency
category_id
status                 # estado del anuncio expuesto por Wallapop
reserved
url
image_url
created_at
modified_at
raw_json
```

Se crea un snapshot cuando:

- aparece un anuncio nuevo (`ACTIVE`);
- cambia un dato relevante de un anuncio activo;
- se confirma su ausencia (`REMOVED`);
- reaparece un anuncio previamente marcado como `REMOVED` (`ACTIVE`).

Si una captura válida vuelve a observar el mismo anuncio sin cambios, solo se actualiza `last_seen_at` de `listings`; no se crea otro snapshot.

## Idempotencia y ejecuciones repetidas

Dos capturas válidas con el mismo contenido no generan snapshots duplicados. Una nueva fila en `tracking_runs` sí se crea para cada intento. La deduplicación de cambios se basa en comparar con el último snapshot válido y no en el número de ejecución.

La aplicación de una captura válida debe ser idempotente frente a un reintento de proceso: las restricciones únicas, la selección de la última captura válida y una transacción única deben impedir duplicar la identidad de perfiles o anuncios.

## Consultas que debe soportar el esquema

Sin introducir una tabla de eventos todavía, el esquema debe permitir derivar:

- anuncios nuevos por semana: primer `ACTIVE` de cada `listing`;
- desaparecidos por semana: snapshots `REMOVED` agrupados por `observed_at`;
- cambios de precio: snapshots activos consecutivos con distinto `price`;
- variación de reseñas y `sold_count`: diferencia entre `profile_snapshots` consecutivos;
- inventario activo: último estado de presencia conocido por anuncio, condicionado a capturas válidas;
- duración aproximada publicado: `last_seen_at - first_seen_at`, con precisión semanal.

## Alcance excluido de esta implementación

La siguiente fase podrá implementar modelos SQLAlchemy 2, SQLite, esquema/migraciones, repositorios y tests de persistencia aislada. Quedan explícitamente fuera:

- diff engine;
- `Event` y eventos históricos materializados;
- scheduler;
- CLI de tracking;
- integración automática con `WallapopClient`.
Las búsquedas pasan por un provider normalizado y llevan identidad explícita
de marketplace (actualmente solo `wallapop`):

```text
SearchTracker -> SearchProvider -> WallapopSearchProvider -> WallapopClient
```

Los listings se identifican por `(marketplace, external_id)` y
`wallapop_item_id` permanece solo como bridge de migración. `TrackingRun` no
duplica marketplace: lo deriva de su source.

Los eventos se entregan mediante una cola persistente desacoplada:

```text
TrackingEvent -> NotificationService -> NotificationDelivery
                                  -> Webhook / Discord / Telegram
```

Las nuevas identidades de anuncio pasan además por un análisis best-effort:

```text
new Listing + removed historical Listing
              ↓
RelistingDetectionService
              ↓
PossibleRelisting + POSSIBLE_RELISTING (si supera el threshold)
```

La relación es heurística y explicable; nunca fusiona listings.

Los analytics de mercado son una capa read-only sobre `SearchMatch`, runs
válidos, presence, snapshots y eventos:

```text
SearchMatch + valid runs + snapshots + TrackingEvent
                         ↓
                 reporting.market
                         ↓
              summary / series / aggregations
```

Una ausencia observada se denomina `removed`; no se interpreta como venta.

### Discovery de metadata

`WallapopClient` expone APIs read-only para categorías, filtros, marcas y
modelos. Cada respuesta pasa por un parser puro y produce modelos pequeños de
`domain/metadata.py`; esta rama no conecta discovery con `SearchTracker`.

### Scheduler concurrente

El scheduler construye primero los jobs due y los ejecuta con un único
`asyncio.Semaphore` y `TaskGroup`. Los runners mantienen sesiones SQLAlchemy
independientes; el limiter de `WallapopClient` es compartido por host y las
notificaciones se procesan después de completar el tracking.

El evento y el run se confirman antes de cualquier POST externo. Cada destino
se procesa independientemente y sus reintentos están limitados por
`WALLAPOP_NOTIFICATION_MAX_ATTEMPTS`.

Las búsquedas nuevas comienzan con un baseline silencioso: la primera
ejecución válida persiste anuncios, snapshots y matches sin emitir
`NEW_LISTING`, salvo que la búsqueda se cree con `notify_on_first_run=true`.
Los cambios de precio con histórico previo siguen generando eventos.

El tercer origen monitorizable reutiliza el mismo listing global:

```text
TrackedListing -> ListingProvider -> WallapopClient.get_item
              -> TrackingRun -> ListingSnapshot -> TrackingEvent
```

## Deal scoring

Read-only `DealScoringService` consumes the persisted search history and the
existing market reporting layer. It returns an explainable score and separate
confidence value without adding a persistence table or coupling tracking to
analysis.

## FastAPI

`wallapop_tracker.api.app.create_app` creates the HTTP transport layer. Each
request gets its own synchronous SQLAlchemy session; routers map validated
input to existing repositories, reporting functions, and services.

Operational observability is centralized in `observability.py` and consumed by
the HTTP client, persistence boundaries, scheduler, notifications and FastAPI
middleware. It does not alter business analytics or tracking semantics.
