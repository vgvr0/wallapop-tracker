# Baseline inicial de búsquedas

Cada `TrackedSearch` tiene `notify_on_first_run`. Su valor por defecto es
`false`: la primera ejecución válida construye el inventario persistente
(`Listing`, snapshot y `SearchMatch`) sin emitir `NEW_LISTING`. Una ejecución
fallida no establece el baseline.

El baseline se determina por la existencia de un `TrackingRun` válido para esa
búsqueda, no por `last_run_at`. En ejecuciones posteriores, un anuncio que no
tenga aún `SearchMatch` para la búsqueda genera `NEW_LISTING`; el evento es
globalmente idempotente para que búsquedas solapadas no dupliquen alertas.

Los cambios de precio se calculan con el histórico global del anuncio y no se
silencian durante el baseline. Por tanto, un precio distinto observado en la
primera ejecución puede producir `PRICE_DROP` o `PRICE_INCREASE` si ya existía
histórico.

Las búsquedas existentes al aplicar `0011_search_initial_baseline` reciben
`notify_on_first_run = true`, preservando el comportamiento anterior. Las
búsquedas nuevas creadas por ORM o CLI reciben `false`, salvo que se use
`--notify-on-first-run` en `search add` o `search import`.

La configuración es visible en `search list` y `search show`. No requiere
cambios en el scheduler: el tracker persiste primero el run y sus datos, y el
servicio de notificaciones solo observa los eventos que realmente se crean.
