# Importación de búsquedas desde URL

La Fase 6 permite crear un `TrackedSearch` sin hacer requests ni consultar
catálogos externos:

```text
URL de Wallapop → SearchImportResult → TrackedSearch
```

## Formato soportado

Se aceptan únicamente URLs HTTPS de `es.wallapop.com` o `www.wallapop.com`
con ruta `/app/search` o `/search`:

```text
https://es.wallapop.com/app/search?keywords=iphone+15&min_sale_price=300&max_sale_price=650
```

La ruta `/app/search` es el formato habitual de las URLs copiadas desde la
interfaz. Los nombres alternativos se aceptan únicamente cuando expresan la
misma información observable:

| Semántica | Parámetros aceptados |
| --- | --- |
| query | `keywords`, `query`, `kws` |
| categoría | `category_id`, `catIds` |
| precio mínimo | `min_price`, `minPrice`, `min_sale_price` |
| precio máximo | `max_price`, `maxPrice`, `max_sale_price` |
| coordenadas | `latitude`, `longitude` |
| distancia | `distance`, `distance_in_km`, `dist` |
| envío | `shipping`, `shipping_required` |
| condición/marca | `condition`, `brand` |

Los parámetros de sesión, orden y paginación (`source`, `filters_source`,
`order_by`, `search_id`, `next_page`, `section_type`, `page` y `_p`) se
reconocen como internos y no se guardan en la configuración semántica.

## Contrato y normalización

`parse_search_url()` es una función pura. Devuelve `SearchImportResult` con
`Decimal` para precios, `float` para coordenadas/distancia, booleanos
explícitos y `unknown_params` para parámetros no soportados. Los nombres y
valores desconocidos no se descartan silenciosamente.

La query se decodifica con las reglas URL estándar, se normalizan espacios y
una query vacía se representa como `None`. El parser acepta parámetros
repetidos solo si tienen el mismo valor; valores conflictivos producen un
error. Los precios no pueden ser negativos ni inválidos, la latitud queda en
`[-90, 90]`, la longitud en `[-180, 180]` y la distancia debe ser positiva.

El parser no convierte slugs de categoría o marca a IDs y no intenta
completar datos ausentes. Una URL de categoría sin query se parsea, pero el
CLI la rechaza porque el esquema actual de `TrackedSearch` requiere `query`.

## CLI

```bash
wallapop-track search import \
  "https://es.wallapop.com/app/search?keywords=iphone+15&min_sale_price=300&max_sale_price=650" \
  --name "iPhone 15 barato" \
  --interval-seconds 900
```

`--disabled` crea la búsqueda desactivada. El comando muestra un resumen de
los campos semánticos y advierte de los parámetros desconocidos.

La configuración importada alimenta el mismo `SearchRequest` que una búsqueda
creada manualmente: query, categoría, precios, coordenadas, distancia, envío,
condición y marca. El parser no contiene lógica de filtros ni depende de
SQLAlchemy.

Los ejemplos y nombres de parámetros se basan en URLs reproducibles y en la
documentación pública observada del flujo de búsqueda; el contrato de la UI
puede cambiar, por lo que los parámetros no reconocidos permanecen visibles
para diagnóstico.
