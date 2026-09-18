# TrackedListing

`TrackedListing` añade una tercera configuración monitorizable sin duplicar la
identidad global de un anuncio:

```text
TrackedListing -> TrackingRun -> ListingSnapshot -> TrackingEvent
                                             -> NotificationDelivery
```

## Modelo y lifecycle

`tracked_listings` contiene `listing_id`, `alias`, `enabled`,
`interval_seconds`, `last_run_at`, `last_run_status`, `last_tracking_run_id`,
`notes`, `created_at` y `updated_at`. `listing_id` es FK a `listings.id` y
cada anuncio global puede tener como máximo una configuración.

El tracker obtiene el detalle, crea un run con `tracked_listing_id`, persiste
un snapshot y actualiza el scheduling. Timeout, 429, 5xx o parseo producen
`FAILED` y no modifican la presencia. Un 404 explícito, o un estado explícito
`removed`/`inactive`/`deleted`, permite confirmar `REMOVED`.

## Runs y estados

`TrackingRun` exige exactamente una fuente entre `profile_id`,
`tracked_search_id` y `tracked_listing_id`. Los runs históricos de perfiles y
búsquedas no se modifican.

La secuencia visible → 404 → visible conserva el mismo `ListingRecord`, crea
snapshots `ACTIVE`, `REMOVED`, `ACTIVE` y eventos `REMOVED`, `REAPPEARED`.
La desaparición no se interpreta como `SOLD`.

## Eventos soportados

Se generan eventos idempotentes para `PRICE_DROP`, `PRICE_INCREASE`,
`TITLE_CHANGE`, `RESERVATION_CHANGE`, `SHIPPING_CHANGE`, `STATUS_CHANGE`,
`REMOVED` y `REAPPEARED`. El primer snapshot es baseline silencioso.

## Listing global

Si el anuncio fue descubierto por una búsqueda, un perfil o por la orden de
crear el `TrackedListing`, se reutiliza la fila de `listings` localizada por
`wallapop_item_id`. No se crean copias.

## PriceWatch legacy

`PriceWatchRecord` y `PriceAlertService` se conservan deprecated para leer y
probar histórico legacy. No participan en el scheduler ni en el nuevo flujo y
no se migran automáticamente: no contienen alias, intervalo ni un contrato
de desaparición equivalente. La detección nueva de precio vive únicamente en
`TrackedListingTracker` y `TrackingEventRecord`.

## Provider y endpoint

El contrato es:

```python
class ListingProvider(Protocol):
    async def get(self, item_id: str) -> Listing: ...
```

`WallapopListingProvider` delega en `WallapopClient.get_item`, que usa
`GET /api/v3/items/{id}`. Se añadió una fixture HTTP offline; no se hacen
llamadas externas durante tests o CI.

## Scheduler, CLI y delivery

El scheduler evalúa perfiles, búsquedas y listings según `enabled`,
`last_run_at` e `interval_seconds`, secuencialmente. La CLI ofrece `listing
add/list/show/run/run-all/enable/disable/remove`; `add` acepta ID o URL de
Wallapop y separa el parser de referencia.

Los eventos de listing se encolan en la misma `NotificationService` de Fase 4.
La entrega ocurre después del commit del run y no puede invalidar su estado.
