# Semántica de seguimiento histórico

## Estados de un anuncio

Los estados de dominio posibles son:

- `active`: aparece en una captura completa y válida.
- `reserved`: la API indica explícitamente que está reservado.
- `removed`: no aparece en una captura completa y válida posterior, sin evidencia explícita de venta.
- `sold`: solo cuando Wallapop proporciona una señal explícita y asociable al anuncio.
- `unknown`: no hay evidencia suficiente para decidir.

`removed` nunca significa automáticamente `sold`. Un aumento de `sold_count` del perfil tampoco permite asignar una venta a un anuncio concreto.

## Tracking runs

Cada ejecución se registra antes de comenzar como `running`.

### `valid`

Se usa cuando:

- el perfil se obtuvo correctamente;
- las estadísticas y el resumen de reseñas se obtuvieron correctamente, si forman parte de la captura;
- se recorrieron todas las páginas de anuncios hasta `next_since = NULL`;
- no hubo errores que invaliden la colección de anuncios.

Una ejecución `valid` puede confirmar que un anuncio previamente visible ya no aparece.

### `partial`

Se usa cuando parte de la información se obtuvo, pero al menos un componente falló o la paginación no terminó. Por ejemplo, perfil y primera página correctos, pero segunda página fallida.

Una ejecución `partial` no puede confirmar desapariciones ni marcar anuncios como `removed`.

### `failed`

Se usa cuando no se obtuvo una captura utilizable o el fallo impide confiar en sus resultados. Puede existir un registro de ejecución con cero elementos.

Una ejecución `failed` no modifica presencia ni estado histórico de anuncios.

### `running`

Solo representa una ejecución en curso. Un proceso de recuperación podrá marcarla como `failed` por timeout o abandono, sin usar sus datos para diffs.

## Invariante crítica de desaparición

```text
Solo una ejecución valid con items_ok=True y paginación completa
puede confirmar la ausencia de un anuncio.
```

La secuencia correcta es:

1. localizar la última captura `valid` completa;
2. comparar sus `listing_id` con la nueva captura `valid` completa;
3. para los ausentes, generar posteriormente `REMOVED` o `REAPPEARED` según el histórico;
4. no convertir la ausencia en `SOLD` sin señal explícita.

## Presencia sin snapshot idéntico

`Listing.last_seen_at` solo contiene el último momento conocido. Por tanto, la expresión “el anuncio estaba presente en la semana 2” no puede reconstruirse para una semana histórica usando únicamente `TrackingRun` y el valor actual de `last_seen_at`.

Para resolverlo sin guardar snapshots repetidos se necesita una marca ligera por ejecución:

```text
tracking_run_listings(tracking_run_id, listing_id, observed_at)
```

Cada fila indica que el anuncio fue visto durante esa ejecución. No contiene título, precio ni descripción. La combinación de esta relación con el estado del `TrackingRun` permite distinguir presencia positiva, ejecución parcial y ejecución completa.

Se puede seguir actualizando `listings.last_seen_at` cuando el anuncio sea observado explícitamente. En una ejecución `partial`, esto es evidencia positiva de presencia, pero nunca evidencia de ausencia para los anuncios no vistos.

## Snapshots change-based

La política elegida es guardar snapshots únicamente cuando cambia el contenido relevante.

### `ProfileSnapshot`

Se compara el conjunto de métricas relevantes (`rating`, contadores y distribución disponible) con el último snapshot de perfil. Si no cambia, no se inserta snapshot. El `TrackingRun` siempre queda registrado.

### `ListingSnapshot`

Se compara una representación canónica de `title`, `description`, `price`, `currency`, `category_id`, `category_name`, `reserved`, `status`, `url`, `image_url`, `created_at_source` y `modified_at_source`.

Si la representación no cambia, no se inserta snapshot. Si cambia cualquier campo, se inserta uno nuevo. Los campos ausentes o `NULL` deben permanecer diferenciados de valores reales y el orden de claves del JSON no debe afectar a la comparación.

El contenido canónico o su hash puede calcularse en la capa de aplicación; no debe depender de un trigger específico de SQLite.

## Impacto de la política change-based

### Reconstrucción histórica

Para reconstruir el contenido en una fecha se toma el último snapshot change-based anterior o igual a la fecha. La presencia se consulta mediante `tracking_run_listings` y el estado de la ejecución.

Sin la relación de presencia, solo se podría responder cuándo se vio por última vez el anuncio, no si estuvo presente en cada ejecución intermedia.

### Consultas

- Precio y contenido: último `ListingSnapshot` con `observed_at <= fecha`.
- Métricas de perfil: último `ProfileSnapshot` con `observed_at <= fecha`.
- Presencia: existencia de `tracking_run_listings` para el anuncio y la ejecución consultada.
- Anuncios actualmente observados: última ejecución `valid` completa y sus filas de presencia.

Las consultas son algo más complejas que con snapshots completos, pero el volumen es menor y se separan contenido histórico y presencia observada.

### Idempotencia

La idempotencia se apoya en tres restricciones:

- `UNIQUE(profile_id, tracking_run_id)` y `UNIQUE(listing_id, tracking_run_id)` para snapshots.
- `PRIMARY KEY(tracking_run_id, listing_id)` para presencia.
- hash o contenido canónico para no insertar un snapshot cuyo estado no cambió.

Reprocesar la misma ejecución debe producir el mismo conjunto de snapshots y marcas de presencia.

### Detección futura de eventos

La política change-based es adecuada para eventos: una diferencia entre dos snapshots consecutivos produce como máximo un evento de cambio por transición. La presencia por ejecución permite detectar `NEW_LISTING`, `REMOVED` y `REAPPEARED` sin snapshots idénticos.

Los eventos de desaparición deben comparar únicamente ejecuciones `valid` completas. Una ejecución `partial` solo queda en auditoría y no crea presencia ni `REMOVED`.

### Tiempo aproximado activo

Con capturas semanales no se conoce la hora exacta de alta o baja. Se puede calcular un intervalo:

- `active_from`: primera ejecución en la que aparece el anuncio;
- `active_until`: última ejecución completa en la que aparece, o la primera ejecución completa posterior en la que falta;
- duración aproximada: `active_until - active_from`.

El resultado debe etiquetarse como aproximado. Si el anuncio aparece en la semana 1 y falta en la semana 4, la baja ocurrió entre ambas observaciones, no necesariamente al inicio de la semana 4.

## Alternativas de almacenamiento

### Opción A: snapshot en cada ejecución

Ejemplo: 120 €, 120 €, 120 €, 100 €.

Ventajas:

- reconstrucción exacta del estado observado en cada ejecución;
- auditoría sencilla;
- consultas temporales directas;
- implementación simple y uniforme;
- permite distinguir “sin cambio observado” de “no se capturó”.

Inconvenientes:

- mayor volumen de filas, especialmente para anuncios estables;
- más coste de almacenamiento de texto y JSON.

### Opción B: solo cuando cambia el contenido

Ejemplo: 120 €, 100 €, actualizando `last_seen_at` en `listings`.

Ventajas:

- menor volumen;
- histórico de cambios de precio más compacto.

Inconvenientes:

- no reconstruye directamente el estado observado en cada día;
- obliga a distinguir “sin cambios” de “sin captura”;
- requiere hash o comparación de contenido;
- complica auditoría y re-procesado;
- puede perder cambios de campos que inicialmente no se compararon.

### Recomendación actual

Para la frecuencia aproximada de una captura semanal se adopta una variante de la Opción B: snapshots change-based más `tracking_run_listings` para presencia por ejecución. Así se evita almacenar contenido idéntico, se conserva la auditoría de cada ejecución y se mantiene una base sólida para reconstrucción, eventos y estimación de actividad.

## Idempotencia

La unidad de idempotencia debe ser una ejecución, no el timestamp del anuncio.

Un `TrackingRun` tiene exactamente una fuente: `profile_id` para capturas de
perfil o `tracked_search_id` para capturas de búsqueda. Las búsquedas no crean
perfiles sintéticos; su presencia se registra directamente en el run y en las
tablas de matches.

- Cada `tracking_run` recibe un `idempotency_key` determinista cuando el invocador puede proporcionarlo.
- `profile_snapshots` debe ser único por `(profile_id, tracking_run_id)`.
- `listing_snapshots` debe ser único por `(listing_id, tracking_run_id)`.
- Reprocesar una ejecución existente debe hacer upsert o devolver el resultado ya persistido, nunca insertar una segunda copia.
- Los futuros eventos deben usar una clave única derivada de `tracking_run_id`, `event_type`, `listing_id`, `old_value` y `new_value`.

Así, volver a procesar la misma captura no produce tres veces `PRICE_CHANGED 100 -> 90`. Dos capturas distintas que observen el mismo cambio sí pueden tener snapshots distintos, pero el evento debe generarse según la política de deduplicación elegida.

## Eventos futuros

No se implementan todavía. El diseño previsto es:

| Campo | Significado |
|---|---|
| `id` | PK |
| `profile_id` | perfil afectado |
| `listing_id` | anuncio afectado, nullable para eventos de perfil |
| `tracking_run_id` | ejecución que aporta la evidencia |
| `event_type` | `NEW_LISTING`, `PRICE_CHANGED`, `TITLE_CHANGED`, `RESERVED`, `UNRESERVED`, `REMOVED`, `REAPPEARED`, `REVIEW_COUNT_CHANGED`, `RATING_CHANGED`, `SOLD_COUNT_CHANGED` |
| `observed_at` | momento de observación |
| `old_value` | valor anterior serializado, nullable |
| `new_value` | valor nuevo serializado, nullable |
| `metadata_json` | diferencias adicionales y evidencia |

Reglas previstas:

- `REMOVED` solo con una ejecución completa válida;
- `SOLD` solo con evidencia explícita de Wallapop;
- eventos de perfil no requieren `listing_id`;
- cada evento debe poder rastrearse hasta snapshots y `tracking_run`.
- los eventos de presencia deben poder rastrearse también hasta `tracking_run_listings`.
