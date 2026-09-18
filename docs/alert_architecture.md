# Arquitectura de alertas

La ruta activa de alertas de búsquedas queda consolidada en el ledger
persistente de eventos:

```text
SearchTracker
     ↓
SearchMatch
     ↓
TrackingEvent
     ↓
NotificationDelivery
```

`SearchTracker` obtiene resultados, persiste la identidad global del anuncio,
actualiza la asociación `search_listing_matches` y guarda snapshots dentro de
una única transacción. La entrega externa no forma parte de esta fase.

## Semántica de eventos

- `NEW_LISTING` se identifica globalmente por el anuncio.
- `PRICE_DROP` y `PRICE_INCREASE` se identifican globalmente por anuncio,
  dirección y transición de precio (`old_price` → `new_price`).
- El evento conserva el `tracking_run_id` que primero observó la transición y
  el `tracked_search_id` que la detectó.
- Dos búsquedas solapadas conservan dos filas en `search_listing_matches`,
  pero comparten un único `TrackingEventRecord`.
- Una transición posterior distinta, como `80 → 70`, tiene una clave distinta
  y genera un nuevo evento.

La restricción única `uq_tracking_events_idempotency` es la protección final
contra duplicados después de reinicios y carreras entre procesos. El repositorio
usa una transacción anidada para convertir una colisión de unicidad en una
lectura del evento existente, en lugar de depender de una caché en memoria.

La relación búsqueda-anuncio tiene además la restricción única
`uq_search_listing_match`; `SearchMatchRepository.touch` trata una colisión
concurrente de esa restricción de forma idempotente.

## Compatibilidad legacy

`SavedSearchRecord`, `SavedSearchItemRecord` y `SearchAlertService` se
conservan temporalmente para no invalidar bases existentes ni borrar datos sin
migración. Están fuera del CLI, scheduler y flujo de nuevas alertas, y se
consideran deprecated.

`PriceWatchRecord` y `PriceAlertService` también se conservan como deprecated:
representan una vigilancia directa de un anuncio, que fue sustituida por
`TrackedListing`. No participan en el scheduler ni en el flujo de nuevas
notificaciones; sus tablas no se eliminan para preservar histórico.
