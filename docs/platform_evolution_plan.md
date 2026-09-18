# Plan de evolución de la plataforma

## Alcance y estado de la auditoría

Este documento es el resultado de la Fase 0 de la evolución incremental del
repositorio. La auditoría se realizó sobre la rama `feature/platform-evolution`
partiendo de `master`, sin cambiar el comportamiento de producción. El estado
inicial validado es:

- 100 tests pasan y 1 test marcado `live` queda excluido por la configuración
  habitual de pytest.
- `ruff check .` pasa.
- `mypy src` pasa en modo estricto.
- `git diff --check` pasa.
- SQLite sigue siendo el backend por defecto y el cliente solo expone
  operaciones de lectura hacia Wallapop.

## Arquitectura actual

El sistema está organizado en capas razonables:

```text
CLI (Typer)
  -> runners / services
      -> WallapopClient (httpx, retries, rate limit, raw fixtures)
      -> parsers (Pydantic/domain models)
      -> repositories (SQLAlchemy 2)
      -> SQLite + Alembic
  -> reporting / diff
```

### Cliente y dominio

`WallapopClient` centraliza HTTP asíncrono, reintentos acotados, `Retry-After`,
rate limiting, captura RAW y parsing de perfiles, estadísticas, reseñas,
anuncios y búsqueda. `Listing`, `Profile` y los demás modelos de extracción
son Pydantic y están separados de los modelos ORM.

La búsqueda actual usa `GET /api/v3/search`, paginación por `meta.next_page`,
deduplicación por `id` y filtros locales deterministas para precio, condición,
marca y envío. Este endpoint está cubierto offline por fixtures/respx; no se
considera validado ningún endpoint alternativo.

### Persistencia histórica

Las entidades históricas principales son:

- `profiles`, `listings` y sus snapshots change-based;
- `tracking_runs` y `tracking_run_listings` para auditoría y presencia;
- `tracked_profiles` para configuración de perfiles;
- `tracked_searches`, `search_listing_matches` y `tracking_events` para la
  búsqueda integrada y su deduplicación global.

`TrackingRunRecord.profile_id` es nullable y `tracked_search_id` es la fuente
alternativa. Un `TrackingRun` exige exactamente una de las dos FKs. Las
búsquedas no crean perfiles técnicos `tracked-search:<id>` y los anuncios
observados solo por búsquedas pueden conservar `profile_id = NULL`.

### Servicios

- `ProfileTracker` captura perfil, estadísticas, reseñas y anuncios, y
  persiste una ejecución válida, parcial o fallida.
- `SearchTracker` captura búsquedas, filtra, actualiza coincidencias, guarda
  snapshots y crea eventos idempotentes.
- `DiffService` deriva cambios históricos de perfil/listings.
- `SearchAlertService` y `PriceAlertService` mantienen el sistema legacy
  basado en `saved_searches`, `saved_search_items` y `price_watches`.
- `TrackingScheduler` evalúa perfiles y búsquedas secuencialmente.
- `TrackedListingTracker` evalúa anuncios monitorizados y comparte snapshots,
  eventos y deliveries con los demás trackers.
- `ProfileTrackingRunner` y `SearchTrackingRunner` encapsulan el ciclo de
  vida del cliente y actualizan metadatos de scheduling.

### Migraciones y documentación

Las migraciones `0001` a `0010` evolucionan el esquema histórico, perfiles
monitorizados, alertas legacy y búsquedas integradas. La `0007` añade
`tracking_runs.tracked_search_id` como entero sin FK para evitar una
dependencia circular durante la creación histórica de tablas; el ORM expone
la relación lógica, pero el esquema no la protege con una FK.

La documentación existente cubre arquitectura histórica, semántica de
presencia, esquema, tracking de búsquedas y contratos de API. Parte de ella
describe diseños previstos que ya han sido implementados parcialmente y por
tanto necesitará sincronización en fases posteriores.

## Problemas y deuda detectados

### 1. Dos sistemas de alertas

El sistema nuevo persiste `TrackingEventRecord` con clave única de idempotencia,
pero el sistema legacy genera alertas en memoria y mantiene tablas paralelas.
`SearchAlertService` solo detecta nuevos IDs por búsqueda y
`PriceAlertService` solo contempla price drops de `PriceWatchRecord`; no
comparten el ledger global ni cubren de forma simétrica subidas, reapariciones
o búsquedas solapadas.

La migración debe conservar la compatibilidad de lectura y de importación de
las tablas legacy mientras los casos de uso se redirigen al modelo nuevo.

### 2. Identidad incorrecta de las búsquedas

El perfil sintético `tracked-search:<id>` no representa un vendedor y puede
contaminar reporting de perfiles, claves foráneas y futuras métricas de seller
activity. Además, un `TrackingRun` tiene una semántica distinta según se
origine en un perfil o en una búsqueda.

### 3. Acoplamiento del tracker al transporte

`SearchTracker` invoca directamente `client.search_items`. El cliente contiene
a la vez transporte HTTP, construcción del request y normalización de resultados
de búsqueda. Esto dificulta probar el tracker contra un contrato estable y
reutilizar la lógica con otro proveedor.

### 4. Entrega de notificaciones desacoplada

La Fase 4 añade `notification_deliveries` como cola persistente, idempotente y
acotada. `alert_delivered` se conserva por compatibilidad histórica, pero ya
no representa por sí solo el resultado de todos los destinos.

### 5. Modelo de anuncios monitorizados incompleto

Existe tracking de perfiles y búsquedas, pero no una configuración persistente
para vigilar un anuncio individual. El cliente tampoco tiene todavía un
`get_item` validado mediante fixture o contrato documentado.

### 6. Scheduler secuencial

Cada job debido se ejecuta uno detrás de otro. Ya existe un rate limiter por
cliente, pero todavía no hay un límite configurable de jobs concurrentes ni
aislamiento explícito de errores por job.

### 7. Reporting limitado a snapshots existentes

El reporting actual cubre métricas de perfil, presencia, cambios y resumen de
búsqueda. Faltan analytics de mercado, duración activa, distribución de
precios, actividad de vendedores y scoring determinista con política explícita
de datos insuficientes.

### 8. Cobertura pendiente

La entrega persistente ya tiene tests offline de persistencia, canales,
reintentos, reinicio, CLI, migración y listing tracking. Sigue sin haber tests
para metadata discovery, scheduler concurrente, analytics de
mercado, relisting heurístico, deal scoring ni FastAPI. Los endpoints externos
no documentados deben seguir bloqueados detrás de fixtures y validación
reproducible.

## Arquitectura objetivo

La evolución propuesta mantiene el núcleo actual y separa progresivamente
origen, detección, eventos y entrega:

```text
TrackedProfile ─────┐
TrackedSearch ──────┼─> Tracking source/run
TrackedListing ─────┘          |
                               v
                       Search/List/Profile provider
                               |
                               v
                    normalized Listing snapshots
                               |
                               v
                 domain changes / TrackingEvent ledger
                               |
                               v
                   NotificationDelivery per destination
                               |
                         channel adapters
```

Decisiones objetivo:

1. `TrackingRun` distingue el origen mediante dos FKs mutuamente exclusivas,
   sin introducir `source_type` ni `source_id`:

   ```text
   TrackingRun
   - profile_id: nullable, para profile runs
   - tracked_search_id: nullable, para search runs
   - CHECK: exactamente una FK no nula
   ```

   En una primera migración, se conservan `profile_id` y los runs históricos;
   los nuevos runs de búsqueda no crean `ProfileRecord`. La tabla de runs se
   migra de forma compatible y se valida en la capa de servicio hasta que todas
   las consultas estén adaptadas.

2. `SearchProvider` será un protocolo pequeño inyectado por constructor.
   `WallapopSearchProvider` encapsulará la implementación actual de
   `/api/v3/search`; `SearchTracker` solo consumirá resultados normalizados.

3. Los cambios detectados se persistirán una sola vez en
   `tracking_events`, con una clave canónica que incluya tipo, marketplace,
   anuncio y transición relevante. Una entrega será otra preocupación y tendrá
   su propia idempotencia `(event_id, channel, destination)`.

4. `WallapopClient` seguirá siendo read-only, SQLite seguirá siendo el default,
   el dinero seguirá usando `Decimal` y los timestamps seguirán siendo UTC.

5. Las interfaces multi-marketplace se introducirán solo en los límites que
   aporten valor: provider de búsqueda/listing/profile y referencias internas
   `marketplace` + `external_id`. No se añadirá infraestructura distribuida.

## Plan de migración por fases

Cada fase debe inspeccionar el estado resultante, añadir tests offline,
ejecutar `pytest`, Ruff, mypy y `git diff --check`, documentar deudas y crear
un commit independiente. Si una validación real contra Wallapop no es posible,
se conserva un fixture RAW y se marca la investigación como pendiente.

| Fase | Resultado | Migración/documentación clave |
|---|---|---|
| 1 | Implementado: detección unificada en tracking events; servicios legacy fuera del flujo activo | Deprecation explícita, eventos simétricos, cobertura de restart, solapamiento, subidas y bajadas |
| 2 | Implementado: separar runs de búsqueda de identidad de perfil | Nueva semántica de `TrackingRun`, preservación de histórico y `docs/tracking_run_model.md` |
| 3 | Implementado: `SearchProvider` + `WallapopSearchProvider` | Contrato, provider testeable y documentación del endpoint |
| 4 | Implementado: NotificationDelivery persistente | `0009_notification_deliveries`, canales mockeables y comandos `notifications retry/list` |
| 5 | Implementado: `TrackedListing` y `get_item` validado | `0010_tracked_listings`, snapshots, eventos y comandos listing |
| 6 | Implementado: importación pura de URLs de búsqueda | `parsers/search_url.py`, CLI `search import` y tests de parámetros realmente observables |
| 7 | Implementado: discovery de metadata validado | `docs/discovery_endpoints.md`, fixtures RAW, parsers y CLI read-only |
| 8 | Implementado: scheduler con concurrencia acotada | `TaskGroup`, `Semaphore`, limiter compartido, WAL y orden determinista |
| 9 | Implementado: baseline silencioso configurable | `0011_search_initial_baseline`, `docs/search_baseline.md`, tests de inventario inicial |
| 10 | Implementado: relisting heurístico explicable | `0012_possible_relistings`, score y razones sin identidad automática |
| 11 | Implementado: analytics de mercado | `reporting/market.py`, términos `removed/inactive/not_seen`, sin inferir ventas |
| 12 | Deal scoring determinista | `ListingAnalyzer` como interfaz futura y `insufficient_data` |
| 13 | API FastAPI fina | Schemas Pydantic separados, reutilización de servicios y sin writes remotos |
| 14 | Observabilidad | Logs/contadores de runs, requests, retries, 429, parseos y notificaciones |
| 15 | Preparación multi-marketplace | Providers y claves `marketplace`/`external_id`, sin Vinted |
| 16 | Limpieza y documentación final | README, docs, diagrama, auditoría final y resumen |

El orden es deliberado: primero se estabiliza el ledger de eventos y la
semántica de runs; después se añaden providers y entregas; las features de
producto y la API se construyen sobre esos contratos.

## Cambios de schema previstos

Los nombres son propuestas sujetas a revisión en cada fase; no se deben
aplicar todas de una vez.

- `tracking_runs`: mantener `profile_id` nullable y `tracked_search_id` nullable
  con CHECK de exactamente una fuente; no añadir `source_type/source_id`.
- `tracking_events`: conservar el ledger actual, normalizar su clave y
  considerar `marketplace` y `external_id` cuando se introduzca el límite
  multi-marketplace.
- `notification_deliveries`: `id`, `event_id`, `channel`, `destination`,
  `status`, `attempts`, `last_error`, `created_at`, `delivered_at`, con unique
  sobre evento/canal/destino.
- `tracked_listings`: configuración de anuncio y estado de última ejecución.
- `listings`: solo añadir identidad de marketplace cuando exista una migración
  completa y consultas adaptadas; no duplicar la tabla por marketplace.
- `tracked_searches`: añadir `notify_on_first_run` y, si procede, parámetros
  estructurados sin romper `filters_json` durante la transición.
- tablas analíticas o snapshots adicionales solo si una consulta reproducible
  demuestra que las tablas existentes no son suficientes.

Cada migración debe ser reversible cuando SQLite lo permita, tolerar bases
creadas con versiones previas y tener al menos una prueba de upgrade/downgrade
o de compatibilidad del schema.

## Riesgos y mitigaciones

| Riesgo | Mitigación |
|---|---|
| Perder histórico al retirar perfiles sintéticos | Migrar runs y asociaciones antes de eliminar nada; mantener compatibilidad de lectura |
| Duplicar alertas al reiniciar | Unique persistente en eventos y deliveries; transacciones explícitas |
| Cambios no observados en Wallapop | No cambiar endpoints sin fixture RAW y evidencia reproducible |
| Rate limiting o flood | Rate limiter compartido, concurrencia conservadora y retries limitados |
| Confundir desaparición con venta | Usar `removed`, `inactive` o `not_seen`; solo `sold` con señal explícita |
| Fallo de una notificación bloqueando tracking | Persistir delivery y aislar el boundary de canal del commit del run |
| Compatibilidad CLI/API | Conservar comandos actuales, añadir aliases/deprecations y probar rutas públicas |
| Complejidad prematura | Sin Airflow, Kafka, Kubernetes, Celery o Redis; solo componentes necesarios |
| Dependencias circulares en ORM/migraciones | Mantener migraciones por etapas y FK solo cuando el orden sea seguro |

## Estrategia de backward compatibility

- No se borrarán tablas legacy en la Fase 1. Se marcarán las clases y servicios
  como deprecated cuando la ruta nueva cubra su comportamiento.
- Los comandos actuales de perfiles y búsquedas seguirán funcionando durante la
  migración. Los nuevos comandos se añadirán sin cambiar la semántica de los
  existentes sin una nota explícita.
- Las filas históricas se conservarán; los runs de búsqueda históricos se
  reasocian a `tracked_search_id` y los perfiles sintéticos se eliminan solo
  cuando ya no tienen referencias reales.
- Los modelos Pydantic públicos y la firma de `WallapopClient` se modificarán
  solo de forma compatible. Las nuevas abstracciones se introducirán mediante
  constructor injection, sin framework DI.
- Las migraciones nuevas serán incrementales y no dependerán de recrear toda la
  base de datos en cada arranque.
- Las notificaciones externas estarán desactivadas en tests/CI mediante
  transports falsos; ningún token se almacenará en el repositorio.

## Decisiones aún pendientes de validación

1. Endpoint y payload actuales para `get_item`, categorías, filtros, marcas,
   modelos y sugerencias.
2. Campos de detalle suficientes para detectar cambios de descripción, envío,
   estado y desaparición de un anuncio.
3. Compatibilidad de URLs de búsquedas y nombres de filtros visibles en la
   interfaz.
4. Política de backoff futura por canal de notificación; la Fase 4 usa un
   límite simple de intentos sin backoff distribuido.
5. Necesidad real de índices/materializaciones adicionales tras medir queries.

Hasta que esas preguntas tengan evidencia offline o validación autorizada, no
se debe inventar un contrato ni cambiar automáticamente a endpoints modernos.
