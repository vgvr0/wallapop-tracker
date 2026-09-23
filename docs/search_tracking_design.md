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
* `services/search_tracker.py`: captura, filtrado, snapshots, asociaciones y
  eventos de búsquedas.
* Repositorios para búsquedas, asociaciones y eventos, más una migración
  Alembic posterior a `0006`.
* Comandos `search add/update/list/show/enable/disable/delete/run/run-all`.

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
