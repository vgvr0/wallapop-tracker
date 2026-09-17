# Arquitectura de persistencia histórica

## Límites de responsabilidad

La persistencia no conocerá `WallapopClient` ni hará peticiones HTTP.

```text
WallapopClient / parsers
        ↓ modelos de dominio
servicio de aplicación futuro
        ↓ transacción
repositories
        ↓ SQLAlchemy 2
SQLite inicialmente / PostgreSQL posteriormente
```

Los repositories reciben modelos normalizados y metadatos de ejecución. No reciben respuestas JSON ni URLs de API.

La política de persistencia es change-based: `TrackingRun` siempre se inserta, pero los snapshots solo se insertan cuando cambia el contenido relevante. La presencia de anuncios se registra separadamente en `tracking_run_listings`, que no es un snapshot de contenido.

## Interfaces previstas

Las siguientes interfaces son diseño, no implementación actual:

```python
class ProfileRepository:
    async def get_or_create_profile(self, profile: Profile) -> ProfileRecord: ...
    async def get_profile_by_wallapop_id(self, user_id: str) -> ProfileRecord | None: ...


class ListingRepository:
    async def get_or_create_listing(self, listing: Listing) -> ListingRecord: ...
    async def get_listing_by_wallapop_id(self, item_id: str) -> ListingRecord | None: ...


class SnapshotRepository:
    async def save_profile_snapshot(self, snapshot: ProfileSnapshot) -> ProfileSnapshotRecord: ...
    async def save_listing_snapshot(self, snapshot: ListingSnapshot) -> ListingSnapshotRecord: ...
    async def mark_listing_seen(self, run_id: int, listing_id: int, observed_at: datetime) -> None: ...
    async def get_latest_profile_snapshot(self, profile_id: int) -> ProfileSnapshotRecord | None: ...
    async def get_latest_listing_snapshot(self, listing_id: int) -> ListingSnapshotRecord | None: ...
    async def was_listing_seen_in_run(self, run_id: int, listing_id: int) -> bool: ...


class TrackingRunRepository:
    async def start_tracking_run(self, profile_id: int, started_at: datetime) -> TrackingRunRecord: ...
    async def finish_tracking_run(self, run_id: int, result: TrackingRunResult) -> TrackingRunRecord: ...
```

Los tipos `ProfileRecord`, `ListingRecord`, snapshots y resultados de ejecución serán modelos de persistencia separados de los modelos Pydantic actuales si la implementación lo necesita. Esto evita contaminar la capa de extracción con columnas o estados de base de datos.

## Transacciones

Una captura futura debe persistirse en una transacción delimitada por ejecución:

1. crear `tracking_run` en estado `running`;
2. obtener y validar datos fuera de la transacción larga;
3. abrir una transacción breve;
4. hacer upsert de `profile` y `listing` por identificadores externos;
5. marcar cada anuncio explícitamente observado en `tracking_run_listings`;
6. insertar solo snapshots cuyo contenido canónico difiera del último snapshot;
7. actualizar `tracking_run` a `valid`, `partial` o `failed`;
8. confirmar.

No se deben borrar snapshots anteriores si falla una ejecución posterior.

## Portabilidad SQLite/PostgreSQL

- usar `Numeric`/`Decimal` para precios;
- usar timestamps timezone-aware normalizados a UTC;
- usar `JSON` de SQLAlchemy solo para datos auxiliares, aceptando que SQLite lo almacena como JSON/texto;
- evitar triggers para lógica de estados y eventos;
- evitar SQL específico del motor en repositories;
- dejar las migraciones a Alembic;
- mantener las claves externas y restricciones declaradas en el modelo SQLAlchemy.

La ausencia no se materializa durante el procesamiento de una ejecución parcial. Solo después de cerrar una ejecución `valid` con paginación completa se compara el conjunto de `tracking_run_listings` con la ejecución completa anterior.

## Decisiones abiertas

1. Política de retención o compresión del `raw_json`.
2. Si los snapshots RAW se guardarán en base de datos, filesystem o ambos.
3. Si añadir una caché materializada `current_state` cuando existan mediciones reales de rendimiento.
4. Política exacta para `unknown` cuando la API omita `reserved` o `status`.
5. Si un `Listing` puede cambiar de `profile_id`; por defecto se tratará como una inconsistencia que requiere revisión, no como actualización silenciosa.
6. Si los eventos se generan al insertar snapshots o en un proceso explícito posterior. La opción preferida es un servicio explícito e idempotente, no un trigger.
7. Retención de ejecuciones `failed` y mensajes de error potencialmente voluminosos.

La introducción de `tracking_run_listings` es necesaria: sin ella, los snapshots change-based no permiten reconstruir presencia por semana.

## Riesgos

- Una respuesta parcial puede parecer válida si no se conserva el estado de paginación en `tracking_runs`.
- `sold_count` es agregado y no identifica anuncios vendidos.
- Los endpoints de Wallapop son internos y pueden cambiar nombres, tipos o paginación.
- Una reaparición del mismo `wallapop_item_id` debe conservar la identidad histórica y crear nuevos snapshots, no crear otro `Listing`.
- La hora de observación y las fechas fuente del anuncio tienen semánticas diferentes y nunca deben mezclarse.
