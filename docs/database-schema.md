# Diseño del esquema histórico

Este documento define el modelo relacional previsto para SQLite con SQLAlchemy 2, manteniendo tipos y relaciones portables a PostgreSQL. No implementa todavía tablas, migraciones ni repositorios.

## Modelo conceptual

```text
Profile
  ├──< ProfileSnapshot
  ├──< Listing ───< ListingSnapshot
  ├──< TrackingRun ───< TrackingRunListing >─── Listing
  └──< TrackingRun
             └──< TrackingEvent ───< NotificationDelivery
```

- Un `Profile` representa la identidad estable de un usuario de Wallapop.
- Un `ProfileSnapshot` representa sus métricas en una observación concreta.
- Un `Listing` representa la identidad estable de un anuncio asociado al perfil.
- Un `ListingSnapshot` representa el contenido observable del anuncio en una observación.
- Un `TrackingRun` representa una ejecución completa o parcial de captura para un perfil.
- Un `TrackingRunListing` representa que un anuncio fue visto explícitamente durante una ejecución, sin duplicar su contenido.
- Un `TrackingEvent` referencia una ejecución y puede tener una `NotificationDelivery`
  independiente por canal y destino.

### `notification_deliveries`

| Campo | Tipo lógico | Reglas |
|---|---|---|
| `id` | integer | PK |
| `event_id` | FK a `tracking_events.id` | NOT NULL, `ON DELETE RESTRICT` |
| `channel` | varchar(50) | NOT NULL |
| `destination` | varchar(2048) | NOT NULL |
| `status` | varchar(20) | `pending`, `delivered` o `failed` |
| `attempts` | integer | NOT NULL, por defecto 0 |
| `last_error` | text | nullable |
| `created_at` | timestamp | NOT NULL |
| `updated_at` | timestamp | NOT NULL |
| `delivered_at` | timestamp | nullable |

Restricciones: `UNIQUE(event_id, channel, destination)` e índice
`(status, created_at)` para seleccionar la cola. La entrega nunca se realiza
dentro de la transacción que confirma un tracking run.

## Tablas

### `profiles`

| Campo | Tipo lógico | Reglas |
|---|---|---|
| `id` | integer/bigint | PK, generado por la base de datos |
| `wallapop_user_id` | varchar | NOT NULL, UNIQUE |
| `slug` | varchar | nullable |
| `name` | varchar | nullable |
| `url` | varchar | nullable |
| `first_seen_at` | timestamp with timezone | NOT NULL |
| `last_seen_at` | timestamp with timezone | NOT NULL |
| `created_at` | timestamp with timezone | NOT NULL |
| `updated_at` | timestamp with timezone | NOT NULL |

No se guardan aquí rating, reseñas ni contadores: todos son temporales y pertenecen a snapshots.

### `profile_snapshots`

| Campo | Tipo lógico | Reglas |
|---|---|---|
| `id` | integer/bigint | PK |
| `profile_id` | FK a `profiles.id` | NOT NULL |
| `tracking_run_id` | FK a `tracking_runs.id` | NOT NULL |
| `observed_at` | timestamp with timezone | NOT NULL |
| `rating` | numeric(4,2) | nullable, rango lógico 0–5 |
| `review_count` | integer | nullable, >= 0 |
| `published_count` | integer | nullable, >= 0 |
| `purchases_count` | integer | nullable, >= 0 |
| `sales_count` | integer | nullable, >= 0 |
| `sold_count` | integer | nullable, >= 0 |
| `reports_count` | integer | nullable, >= 0 |
| `rating_1_count` ... `rating_5_count` | integer | nullable, >= 0 |

Los campos desconocidos se representan como `NULL`, nunca como cero. Los contadores de la API se mapean por significado, no por posición.

Restricción recomendada: `UNIQUE(profile_id, tracking_run_id)`, para que reprocesar una misma ejecución no genere dos snapshots del perfil.

Los snapshots de perfil son change-based: solo se inserta una fila si cambia alguna métrica relevante respecto al último snapshot de perfil. La ejecución sigue existiendo aunque no haya snapshot nuevo.

### `listings`

| Campo | Tipo lógico | Reglas |
|---|---|---|
| `id` | integer/bigint | PK |
| `wallapop_item_id` | varchar | NOT NULL, UNIQUE |
| `profile_id` | FK a `profiles.id` | nullable; NULL para listings observados solo por búsquedas |
| `first_seen_at` | timestamp with timezone | NOT NULL |
| `last_seen_at` | timestamp with timezone | NOT NULL |

No se persiste inicialmente `current_state`. El estado actual debe derivarse del último snapshot válido y de la evidencia disponible. Así se evita que una columna materializada contradiga el histórico.

Si el volumen futuro exige lecturas rápidas, `current_state` puede añadirse como caché derivada, nunca como única fuente de verdad y siempre reconstruible.

### `listing_snapshots`

| Campo | Tipo lógico | Reglas |
|---|---|---|
| `id` | integer/bigint | PK |
| `listing_id` | FK a `listings.id` | NOT NULL |
| `tracking_run_id` | FK a `tracking_runs.id` | NOT NULL |
| `observed_at` | timestamp with timezone | NOT NULL |
| `title` | text | nullable |
| `description` | text | nullable |
| `price` | numeric(12,2) | nullable, >= 0 |
| `currency` | varchar(3) | nullable |
| `category_id` | varchar | nullable |
| `category_name` | varchar | nullable |
| `reserved` | boolean | nullable |
| `status` | varchar | nullable |
| `url` | varchar | nullable |
| `image_url` | varchar | nullable |
| `created_at_source` | timestamp with timezone | nullable |
| `modified_at_source` | timestamp with timezone | nullable |
| `raw_json` | JSON/text portable | nullable |

`created_at_source` y `modified_at_source` solo almacenan fechas proporcionadas explícitamente por Wallapop. No se rellenan con `observed_at`.

Restricción recomendada: `UNIQUE(listing_id, tracking_run_id)`.

Los snapshots de anuncio son change-based: solo se inserta una fila si cambia algún campo relevante respecto al último snapshot del mismo anuncio. Ver el anuncio de nuevo actualiza `listings.last_seen_at`, pero no crea una fila idéntica.

### `tracking_run_listings`

Esta tabla de presencia es necesaria para reconstruir si un anuncio apareció en una ejecución concreta sin almacenar un snapshot idéntico cada semana.

| Campo | Tipo lógico | Reglas |
|---|---|---|
| `tracking_run_id` | FK a `tracking_runs.id` | NOT NULL, parte de PK |
| `listing_id` | FK a `listings.id` | NOT NULL, parte de PK |
| `observed_at` | timestamp with timezone | NOT NULL |

PK compuesta: `PRIMARY KEY(tracking_run_id, listing_id)`.

La fila significa únicamente “fue visto explícitamente”. No significa que el anuncio fuese vendido, ni que una ejecución parcial sea suficiente para confirmar ausencias. Para confirmar una desaparición se sigue necesitando una ejecución completa con paginación válida.

### `tracking_runs`

| Campo | Tipo lógico | Reglas |
|---|---|---|
| `id` | integer/bigint | PK |
| `profile_id` | FK a `profiles.id` | nullable; mutuamente excluyente con `tracked_search_id` |
| `tracked_search_id` | FK a `tracked_searches.id` | nullable; mutuamente excluyente con `profile_id` |
| `started_at` | timestamp with timezone | NOT NULL |
| `finished_at` | timestamp with timezone | nullable mientras está `running` |
| `status` | varchar/enum controlado | `running`, `valid`, `partial`, `failed` |
| `items_fetched` | integer | nullable o 0 según fase, >= 0 |
| `pages_fetched` | integer | nullable o 0 según fase, >= 0 |
| `profile_ok` | boolean | NOT NULL |
| `stats_ok` | boolean | NOT NULL |
| `reviews_ok` | boolean | NOT NULL |
| `items_ok` | boolean | NOT NULL |
| `error_type` | varchar | nullable |
| `error_message` | text | nullable |
| `idempotency_key` | varchar | nullable, UNIQUE si se usa |

`TrackingRun` exige exactamente una fuente mediante un `CHECK`: un run de
perfil tiene `profile_id` y un run de búsqueda tiene `tracked_search_id`.
Los anuncios observados exclusivamente desde búsquedas pueden tener
`listings.profile_id = NULL`; no se crea un perfil sintético.

## Índices

Índices iniciales, evitando indexar cada campo:

- `profiles(wallapop_user_id)` mediante UNIQUE.
- `profile_snapshots(profile_id, observed_at DESC)` para histórico y último snapshot.
- `listings(wallapop_item_id)` mediante UNIQUE.
- `listings(profile_id, last_seen_at)` para anuncios de un perfil.
- `listing_snapshots(listing_id, observed_at DESC)` para histórico y precio actual.
- `tracking_run_listings(listing_id, tracking_run_id)` para presencia de un anuncio por ejecución.
- `tracking_run_listings(tracking_run_id, listing_id)` mediante su PK para listar anuncios vistos en una ejecución.
- `tracking_runs(profile_id, started_at DESC)` para ejecuciones recientes.
- `tracking_runs(tracked_search_id, started_at DESC)` para ejecuciones de búsquedas.
- `tracking_runs(status, finished_at)` para fallos y ejecuciones incompletas.
- `listing_snapshots(price)` solo si las consultas de precio demuestran que lo necesitan; no es obligatorio inicialmente.

Las expresiones `DESC` deben declararse mediante SQLAlchemy de forma portable; no se depende de índices parciales ni de sintaxis exclusiva de SQLite.

## Constraints y validación

En base de datos:

- unicidad de `wallapop_user_id` y `wallapop_item_id`;
- foreign keys con borrado restrictivo por defecto;
- valores no negativos para contadores y precios;
- `finished_at IS NULL` mientras `status = running`;
- `finished_at IS NOT NULL` para `valid`, `partial` y `failed`;
- `status` limitado a los valores documentados.
- exactamente una de `tracking_runs.profile_id` y `tracking_runs.tracked_search_id` debe estar informada.
- `tracking_run_listings` no se usa para inferir ausencias si el `tracking_run` no es completo y válido.

En Pydantic o capa de dominio:

- normalización de `Decimal`, timestamps y campos opcionales;
- rangos de rating;
- semántica de `NULL` frente a cero;
- validación de que una ejecución `valid` tiene `items_ok=True` si se pretende usar para desapariciones.

La base de datos protege invariantes estructurales; la lógica de negocio protege invariantes que dependen de varias tablas.
