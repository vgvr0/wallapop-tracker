# Arquitectura de proveedores de búsqueda

## Problema anterior

`SearchTracker` llamaba directamente a `WallapopClient.search_items`, por lo
que conocía indirectamente la implementación concreta de Wallapop. Esto hacía
que el tracker mezclara la configuración de una búsqueda con detalles de
transporte y dificultaba probarlo con otro origen.

## Contrato

```python
class SearchProvider(Protocol):
    async def search(self, request: SearchRequest) -> list[Listing]: ...
```

`SearchRequest` contiene solo semántica de búsqueda: query, categoría, rango
de precios `Decimal`, condición, marca, envío, coordenadas, distancia y límite
de páginas. No contiene URLs, headers, cursores ni nombres internos de HTTP.

## Flujo y responsabilidades

```text
SearchTracker
    ↓ SearchRequest
SearchProvider
    ↓ list[Listing]
WallapopSearchProvider
    ↓
WallapopClient.search_items
    ↓ HTTP /api/v3/search
```

`SearchTracker` carga configuración, construye la petición semántica, aplica
`FilterEngine`, persiste runs, matches, snapshots y eventos.

`WallapopSearchProvider` adapta el contrato al cliente Wallapop. El cliente
mantiene HTTP, retries, rate limiting, paginación, deduplicación y normalización
de payloads para conservar la API pública existente. La separación evita que
esas decisiones aparezcan en `SearchTracker`.

## Filtros

Los parámetros que el endpoint actual acepta (`category_id`, rango de precio,
condición, marca, envío y localización) se envían como candidatos server-side
a través del provider. `FilterEngine` sigue siendo la fuente de verdad para la
semántica local y determinista de cada `TrackedSearch`, incluyendo texto,
regex y cualquier filtro cuya semántica remota no esté garantizada. La
reaplicación de precio y algunos campos en el cliente se mantiene por
compatibilidad con `WallapopClient.search_items`; no se duplica lógica en el
tracker.

## Cambios futuros de API

Una futura implementación puede traducir el mismo `SearchRequest` a otro
endpoint sin modificar `SearchTracker`. No se añade todavía un
`ComponentsSearchProvider`: el flujo alternativo no tiene fixture RAW ni una
validación reproducible dentro de este repositorio.

## Multi-marketplace

Otro marketplace puede implementar `SearchProvider` y devolver el mismo modelo
normalizado `Listing`, sin introducir un framework DI. La construcción se hace
por constructor injection.

