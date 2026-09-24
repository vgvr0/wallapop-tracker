# Diseño de seguimiento de búsquedas

## Auditoría de la arquitectura existente

El proyecto separa correctamente un cliente HTTP asíncrono (`WallapopClient`),
modelos de dominio Pydantic (`Listing`), modelos SQLAlchemy y repositorios,
servicios de aplicación (`ProfileTracker`, `DiffService`), alertas, reporting,
scheduler y CLI. `ProfileTracker` captura un perfil completo, crea un
`TrackingRun` válido y guarda la identidad del anuncio en `listings`, su estado
en `listing_snapshots` y la presencia por ejecución en
`tracking_run_listings`. El diff existente deriva altas, bajas y cambios de
precio desde esas tablas.

El cliente ya contiene una implementación inicial de `search_items`, basada en
`GET /api/v3/search`, paginación mediante `meta.next_page`, deduplicación por
`id` dentro de la respuesta y el mismo mecanismo de reintentos, rate limiting y
captura RAW que el resto del cliente. Se conservará la API compatible y se
completará su normalización y cobertura offline.

La rama también contiene `saved_searches`, `saved_search_items` y
`price_watches` de una fase anterior. Esas tablas sólo recuerdan coincidencias
por búsqueda y generan alertas en memoria; no se relacionan con el histórico
global ni garantizan idempotencia entre búsquedas. Se mantienen sus nombres y
la compatibilidad básica, pero el nuevo tracker usará las tablas históricas y
las entidades de evento/deduplicación que faltaban.

## Decisiones

* `Listing` seguirá siendo el único modelo de dominio para anuncios.
* Los filtros vivirán en `domain/filters.py` y sólo conocerán el protocolo de
  `Listing`; no harán I/O ni dependerán de Wallapop o SQLAlchemy.
* Cada búsqueda tendrá un `TrackedSearchRecord` persistente. Sus filtros se
  guardarán como JSON estructurado y validado, lo que permite extenderlos sin
  concatenar una DSL frágil.
* La localización explícita de una búsqueda se persiste en `latitude`,
  `longitude` y `max_distance_km`. Los tres son opcionales, pero las
  coordenadas deben aparecer juntas y la distancia requiere coordenadas.
  El formato histórico en `filters_json` se mantiene como fallback para no
  romper búsquedas existentes.
* Las búsquedas reutilizarán `TrackingRun`, `listings`, `listing_snapshots` y
  `tracking_run_listings` siempre que exista un perfil vendedor resoluble. Para
  anuncios observados sólo desde una búsqueda se usará un perfil técnico
  estable por vendedor, conservando la identidad global del anuncio.
* `search_run_listings` conservará la asociación explícita entre ejecución y
  búsqueda. `search_listing_matches` conservará la relación histórica
  búsqueda-anuncio con primera/última observación y contador.
* `tracking_events` será el registro idempotente de cambios relevantes. Una
  restricción única sobre `idempotency_key` evita una segunda alerta para el
  mismo anuncio y transición, incluso después de reiniciar el proceso.
* Las alertas se representan como eventos persistidos y se devuelven como
  objetos de dominio. La entrega externa de notificaciones sigue fuera del
  alcance; el registro es la fuente auditable.
* El scheduler se amplía con un runner compuesto que ejecuta perfiles y
  búsquedas en el mismo ciclo. Se conserva `TrackingScheduler` y su API para
  no romper a los consumidores actuales.

## Flujo de datos

```text
TrackedSearch -> SearchTracker -> SearchProvider
              -> WallapopSearchProvider -> WallapopClient.search_items
              -> FilterEngine
              -> search runs + global listings/snapshots
              -> DiffService / event idempotency
              -> persisted alerts and reporting
```

Una primera ejecución establece el baseline y no crea un `NEW_LISTING` masivo.
Las ejecuciones posteriores comparan la presencia completa anterior. Si dos
búsquedas ven el mismo `wallapop_item_id`, ambas asociaciones se guardan, pero
el evento global se inserta una sola vez. Los cambios de precio usan la clave
estructurada equivalente a `(event_type, item_id, old_price, new_price)`.

## Componentes nuevos

* `domain/filters.py`: `PriceFilter`, inclusión/exclusión de texto, regex y
  composición determinista.
* `domain/filters.py`: filtros de texto por campo (`FieldIncludeFilter`,
  `FieldExcludeFilter`) y filtros exactos de primera palabra del título
  (`TitleFirstWordFilter`).
* `domain/filters.py`: `FilterTrace`, `FilterEvaluation` y
  `FilterEngine.evaluate()` para explicar por qué un anuncio pasó o falló.
* `services/search_tracker.py`: captura, filtrado, snapshots, asociaciones y
  eventos de búsquedas.
* `services/filter_explanation.py`: orquestación mínima que reconstruye el
  `Listing` desde `listings` + el último snapshot y evalúa la búsqueda guardada.
* Repositorios para búsquedas, asociaciones y eventos, más una migración
  Alembic posterior a `0006`.
* Comandos `search add/update/list/show/explain/enable/disable/delete/run/run-all`.

La deduplicación es persistente, transaccional y protegida por una restricción
única. No se realizan acciones de escritura en Wallapop.

## Filtros avanzados de texto

Los filtros estructurados se guardan en `tracked_searches.filters_json`, el
mismo límite JSON compatible que ya existía. Por eso no hace falta ninguna
migración: las búsquedas antiguas siguen funcionando sin los campos nuevos y
los campos nuevos se validan en el repositorio antes de persistirse.

Claves admitidas:

```text
include / include_mode            (heredado: título + descripción)
exclude                           (heredado: título + descripción)
title_include
title_include_mode = any | all
description_include
description_include_mode = any | all
title_exclude
description_exclude
title_first_word_include
title_first_word_exclude
```

Alias aceptados por compatibilidad/conveniencia (son los mismos filtros, no
filtros adicionales):

```text
title_must_include -> title_include
description_must_include -> description_include
```

Normalización única: `normalize_text` pasa a minúsculas (`casefold`) y colapsa
espacios; `normalize_first_word` toma el primer token y recorta la puntuación
de los extremos. Los filtros comparan siempre sobre una copia normalizada, por
lo que el texto almacenado en `listings` y `listing_snapshots` nunca se
reescribe. Los acentos no se pliegan porque la normalización actual tampoco lo
hacía.

Orden lógico explícito (todos los términos se combinan con AND):

```text
price
→ structured filters (condición, categoría, marca, modelo, distancia)
→ include / include_mode (heredado, título + descripción)
→ NOT exclude (heredado, título + descripción)
→ title_include
→ description_include
→ NOT title_exclude
→ NOT description_exclude
→ title_first_word_include
→ NOT title_first_word_exclude
→ regex
```

La primera palabra es exacta, no substring: `iph` no coincide con `iphone`.
Un título `None`, vacío o solo con símbolos normaliza a cadena vacía, de modo
que nunca satisface una lista de inclusión y nunca es rechazado por una lista
de exclusión.

La descripción se toma del propio payload de búsqueda (`description` ya está
presente en las respuestas observadas y `Listing` la conserva). No se añaden
peticiones de detalle por anuncio: si el payload omite o acorta la
descripción, los términos de descripción simplemente no coinciden.

## Explicación de matching (traces)

`FilterEngine.matches(listing)` conserva su contrato y su cortocircuito: sigue
devolviendo `bool`. La explicación vive en el mismo módulo de dominio y no
duplica lógica, porque cada filtro implementa `explain()` y `matches()` se
deriva de él:

```text
FilterEngine.matches(listing)  -> bool                    (compatibilidad)
FilterEngine.evaluate(listing) -> FilterEvaluation
FilterEvaluation.matched        -> bool | None
FilterEvaluation.complete       -> bool
FilterEvaluation.traces        -> tuple[FilterTrace, ...]
```

`FilterTrace` es un dataclass inmutable con `filter_name`, `passed`,
`actual_value`, `expected_value`, `matched_values` y `reason`. `passed` es
tri-estado:

```text
passed=True   PASS     el filtro se pudo evaluar y se cumple
passed=False  FAIL     el filtro se pudo evaluar y no se cumple
passed=None   UNKNOWN  no hay datos suficientes para evaluarlo
```

Los términos de texto que coincidieron se exponen en `matched_values`, la
primera palabra exacta en `actual_value`, y el regex guarda el target evaluado
(`title`, `description` o `both`), el patrón y si hubo match (sólo el texto
coincidente, nunca grupos de captura).

Reglas:

* `evaluate()` no cortocircuita: recorre todos los filtros configurados aunque
  uno falle, porque el objetivo es diagnóstico.
* La agregación vive en un único sitio, `FilterEvaluation.from_traces`:
  `matched=True` si todo pasa; `matched=False` en cuanto hay un FAIL conocido;
  `matched=None` si no hay FAIL pero sí algún UNKNOWN. Es decir, un FAIL
  conocido gana sobre UNKNOWN y un UNKNOWN sólo impide afirmar MATCHED.
  `complete=False` en cuanto existe al menos un UNKNOWN.
* `FilterEngine.evaluate()` sobre un `Listing` real sigue siendo determinista y
  de dos estados: el motor nunca inventa UNKNOWN y para esos casos sigue
  cumpliéndose `matches(listing) == evaluate(listing).matched` con
  `complete=True`. El estado UNKNOWN lo introducen los llamadores que saben que
  su entrada está incompleta, como el servicio de explicación desde storage.
* Los filtros no configurados no generan trace y el orden es el documentado
  arriba; un `PriceFilter` con ambos límites produce `min_price` y `max_price`.
* Los traces son datos de runtime: no se persisten, no se añaden columnas ni
  migraciones y no se guardan en `filters_json`.

`services/filter_explanation.py` es la única orquestación añadida: lee
`tracked_searches`, reconstruye el `Listing` de dominio desde `listings` y el
último `listing_snapshots`, y evalúa la búsqueda con la misma configuración
que usó el tracking (las columnas de precio mandan sobre el JSON). Se expone
en `wallapop-track search explain <search_id> <listing_id>` y en
`GET /api/v1/searches/{id}/listings/{listing_id}/explain`.

Ese servicio es el único que conoce el límite de reconstrucción: los snapshots
no guardan `model` ni coordenadas, así que reemplaza las trazas de `model` y
`distance` por UNKNOWN (`passed=None` con
`reason="model is not persisted in listing snapshots"` y
`reason="distance cannot be evaluated because coordinates are not persisted"`),
recalcula `matched`/`complete` con `FilterEvaluation.from_traces` y resume los
filtros afectados en `warnings`. El dominio no sabe nada de `listing_snapshots`
ni de qué columnas existen. Durante el tracking, en cambio, un anuncio real sin
coordenadas sigue siendo rechazado por el filtro de distancia (`FAIL`), porque
ahí el dato ausente es un hecho del anuncio y no un artefacto del almacenamiento.

La CLI traduce los tres estados a `✓ PASS`, `✗ FAIL` y `? UNKNOWN`, con títulos
`MATCHED`, `NOT MATCHED`, `NOT MATCHED (INCOMPLETE)` o `INCOMPLETE`.
