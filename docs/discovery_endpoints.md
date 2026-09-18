# Marketplace metadata discovery

La Fase 7 añade discovery read-only respaldado por capturas reproducibles.
Los endpoints son internos del frontend de Wallapop y no constituyen una API
oficial estable; por eso cada parser se prueba contra una fixture RAW
sanitizada y no se mezclan con `SearchTracker`.

## Estado

| Capability | Status | Endpoint | Fixture RAW | Notes |
| --- | --- | --- | --- | --- |
| Categories | validated | `GET /api/v3/categories` | `categories.json` | `context` opcional |
| Filters | validated | `GET /api/v3/search/filters/regular-filters` | `filters_regular.json` | depende de query/categoría |
| Brands | validated | `GET /api/v3/search/filters/brand` | `filters_brand.json` | opciones contextuales |
| Models | validated | `GET /api/v3/search/filters/model` | `filters_model.json` | opciones contextuales |
| Suggestions | investigated, not implemented | `GET /api/v3/suggesters/nonlocated` | ninguna | variantes probadas devolvieron `400` |

Las fixtures están en `tests/fixtures/raw/2026-09/` y contienen solo una
muestra estructural sanitizada: no incluyen cookies, tokens, identificadores
personales ni headers sensibles.

## Requests y respuestas

Todas las llamadas reutilizan `WallapopClient`, su timeout, rate limiting,
reintentos, `Retry-After` y captura RAW opcional.

### Categories

```text
GET https://api.wallapop.com/api/v3/categories[?context=...]
```

La respuesta observada es `{ "categories": [...] }`. Cada entrada expone
`id`, `name`, `attributes` y `subcategories`; el parser conserva IDs como
texto, nombres, IDs de atributos y una jerarquía de hijos cuando aparece.

### Filters

```text
GET /api/v3/search/filters/regular-filters
    ?keywords=iphone&category_id=24200&order_by=most_relevance&source=search_box
```

La respuesta observada contiene `filter_sections[].filters[]`. Se normalizan
`id`, `title`, `type`, `selection`, parámetros (`search_params`) y opciones
cuando el payload las expone como objetos. Tipos observados en la captura:
`tree`, `location`, `toggle`, `slider` y `list`.

### Brands y models

```text
GET /api/v3/search/filters/brand
GET /api/v3/search/filters/model
```

Ambos aceptan el contexto `keywords`, `category_id`, `order_by` y `source` y
devuelven `{ "type": "list", "id": ..., "options": [...] }`. Cada opción
se normaliza a ID y nombre. La paginación observada expone un cursor en
`pagination.options_page_key`, pero esta fase no la sigue porque el cliente
actual no necesita una API de paginación de metadata.

### Suggestions

Se investigó `GET /api/v3/suggesters/nonlocated` con `keywords`, `query`,
`text`, `prefix`, `q`, `search` y `source=search_box`. Todas las variantes
read-only probadas devolvieron `400`, sin payload estable que normalizar.
Queda documentado como pendiente y no existe método activo `suggest()`.

## Modelos y arquitectura

Los modelos pequeños viven en `domain/metadata.py`: `Category`, `Brand`,
`ProductModel`, `AvailableFilter` y `MetadataOption`. Los parsers separados
son puros y estrictos ante errores estructurales. No se añadió
`MetadataProvider`: en esta fase `WallapopClient` ya es la frontera de
transporte y añadir otro protocolo no aportaría desacoplamiento real.

No se implementó category-only search. Aunque el endpoint de categorías
permite seleccionar una categoría sin texto, el `TrackedSearch` actual exige
`query` y `SearchProvider` construye `SearchRequest.query` como obligatorio.
Cambiar esa semántica requeriría una decisión de dominio separada.
