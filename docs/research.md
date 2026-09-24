# Investigación de Wallapop Profile Tracker

Fecha de observación: 17 de septiembre de 2026 (Europe/Madrid)

Perfil utilizado como caso de estudio:

`https://example.invalid/user/demo-seller-001`

## Resumen ejecutivo

La página pública del perfil funciona sin iniciar sesión y se entrega mediante SSR de Next.js. La respuesta HTML incluye un elemento `#__NEXT_DATA__` con el usuario, estadísticas y la primera página de anuncios. El frontend también utiliza directamente la API JSON de `api.wallapop.com` para consultar usuario, estadísticas, anuncios y reseñas.

La ruta de anuncios es paginable mediante un cursor opaco `since`. En la observación inicial devolvió 40 anuncios y el siguiente cursor permitió obtener 37 más, por lo que el perfil tenía 77 anuncios publicados en ese momento.

No debe confundirse viabilidad técnica con autorización de uso: las [Condiciones de uso de Wallapop](https://about.wallapop.com/condiciones-de-uso/) (revisión indicada por Wallapop: 10 de abril de 2026) prohíben la extracción sistemática o reutilización de contenido, las herramientas o robots de búsqueda y la extracción de datos, salvo consentimiento expreso por escrito o uso autorizado. Esto bloquea, como mínimo, desplegar el tracker para capturas periódicas de N perfiles sin confirmar previamente la autorización aplicable.

## 1. Resolución de URL de perfil a `user_id`

### Observado

La navegación a la URL pública con slug:

`/user/demo-seller-001`

devuelve HTML `200`. En `#__NEXT_DATA__` aparece:

```json
{
  "query": {"userSlug": "demo-seller-001"},
  "props": {
    "pageProps": {
      "user": {
        "id": "demo-user-001",
        "webSlug": "demo-seller-001"
      }
    }
  }
}
```

### Conclusión

Para la Fase 1, la resolución fiable es solicitar la página pública y extraer `props.pageProps.user.id` de `#__NEXT_DATA__`. No se observó una llamada XHR independiente de resolución `slug -> id` durante la carga inicial.

La API JSON observada requiere el `user_id`, no el slug:

`GET https://api.wallapop.com/api/v3/users/{user_id}`

Para una implementación futura conviene conservar ambos valores: el slug de entrada y el identificador estable devuelto por Wallapop.

## 2. Datos SSR y `__NEXT_DATA__`

La página usa Next.js. El documento observado incluyó:

- `page`: `/user/[userSlug]/[[...tabSelected]]`
- `buildId`: `-fOSmvrIfB1qopLH6YTJa`
- `locale`: `es`
- `gssp`: `true`
- `props.pageProps.user`
- `props.pageProps.userStats`
- `props.pageProps.publishedItems`

La página SSR ya contiene una captura suficiente para una primera lectura pública:

### Usuario

Campos observados en `pageProps.user`:

`id`, `microName`, `type`, `webSlug`, `featured`, `registerDate`, `avatarImage`, `location`, `urlShare`, `extraInfoPro`, `badgeType`, `isVacationModeEnabled`, `listingProtected`, `isTopProfile`, `sellerType`, `officialStoreUrl`, `verified`.

### Estadísticas

`pageProps.userStats` contiene:

```json
{
  "ratings": {"reviews": 98},
  "counters": {
    "publish": 77,
    "buys": 14,
    "sells": 417,
    "favorites": 0,
    "views": 0,
    "profileFavoritedReceived": 0,
    "profileFavorited": 0,
    "reviews": 294,
    "sold": 415,
    "reportsReceived": 10,
    "onHold": 0,
    "featured": 0,
    "shippingCounter": 0
  },
  "ratingAverage": 4.9
}
```

La diferencia entre `ratings.reviews` y `counters.reviews` se conserva en el perfil sintético (`8` frente a `12`), por lo que no se deben mapear ambos a un único campo sin entender su semántica. Para el contador visible de valoraciones del perfil, el valor sintético es `counters.reviews = 12`.

### Anuncios SSR

`pageProps.publishedItems` tiene la forma:

```json
{
  "meta": {"next": "<cursor opaco>"},
  "data": [
    {
      "id": "demo-item-01-001",
      "title": "Synthetic listing 01-001",
      "description": "Synthetic description for listing 01-001.",
      "categoryId": 24200,
      "slug": "synthetic-listing-01-001",
      "images": [{"urls": {"small": "https://example.invalid/images/01-001-small.jpg", "medium": "https://example.invalid/images/01-001-medium.jpg", "big": "https://example.invalid/images/01-001-big.jpg"}}],
      "price": {"amount": 22, "currency": "EUR"},
      "shipping": {"isItemShippable": true, "userAllowsShipping": true},
      "hasWarranty": false,
      "isFavorite": false,
      "isReserved": false,
      "discount": null
    }
  ]
}
```

El `user_id` no aparece en cada anuncio de esta respuesta porque está implícito en la consulta del perfil.

## 3. Endpoints JSON observados y verificados

Todos los endpoints siguientes se probaron el 17/09/2026 con `GET`, sin cookies ni autenticación de Wallapop, usando una cabecera `Accept: application/json` y un User-Agent identificativo. Los `200` indican accesibilidad pública en ese momento, no una garantía de estabilidad ni autorización para automatización.

### Perfil

`GET https://api.wallapop.com/api/v3/users/demo-user-001`

Respuesta `200`, objeto JSON con:

`id`, `micro_name`, `type`, `image`, `location`, `gender`, `preferences`, `financing`, `web_slug`, `url_share`, `register_date`, `featured`, `listing_protected`, `is_top_profile`, `seller_type`.

El `seller_type` observado es un objeto API (`{"type":"Private","verified":true}`), mientras que el SSR lo normaliza a `sellerType: "Private"` y `verified: true`.

### Estadísticas

`GET https://api.wallapop.com/api/v3/users/demo-user-001/stats`

Respuesta `200`:

```json
{
  "ratings": [{"type": "reviews", "value": 8}],
  "counters": [
    {"type": "publish", "value": 12},
    {"type": "buys", "value": 2},
    {"type": "sells", "value": 15},
    {"type": "reviews", "value": 12},
    {"type": "sold", "value": 11},
    {"type": "reports_received", "value": 0}
  ],
  "rating_average": 4.9
}
```

Este endpoint es la fuente preferida para snapshots de estadísticas si se confirma que sigue disponible y permitido.

### Anuncios publicados

`GET https://api.wallapop.com/api/v3/users/demo-user-001/items`

Respuesta `200` con `{ "data": [...], "meta": {"next": "..."} }`. En la respuesta API los nombres son snake_case; el bundle del frontend los transforma a camelCase.

Campos de anuncio observados en API: `id`, `title`, `description`, `category_id`, `slug`, `images`, `price`, `type_attributes`, `shipping`, `is_refurbished`, `has_warranty`, además de campos usados por el frontend como reserva, favoritos, bump y descuento.

Paginación verificada:

`GET .../items?since=<valor de meta.next>`

Con el cursor observado se obtuvo `200`, 37 anuncios y `meta.next = null`. El cursor es opaco y debe tratarse como un valor que solo se reenvía, no como una fecha o un offset.

### Resumen de reseñas

`GET https://api.wallapop.com/api/v3/users/demo-user-001/reviews/summary`

Respuesta `200`:

```json
{
  "total_reviews": 12,
  "average": 4.9,
  "breakdown": {
    "1": {"percentage": 1},
    "2": {"percentage": 0},
    "3": {"percentage": 1},
    "4": {"percentage": 3},
    "5": {"percentage": 95}
  }
}
```

Es una fuente adecuada para el contador y la media, si se mantiene pública.

### Reseñas individuales

La pestaña pública de reseñas realizó esta llamada:

`GET https://api.wallapop.com/bff/sales/reviews/user-profile?page=0&user_id=demo-user-001&order_by=creation_desc`

Respuesta `200`, array de reseñas. La respuesta expone cabecera `X-NextPage: 1`, que debe usarse para avanzar en la paginación. Cada elemento observado incluía `id`, `rating`, `rating_over_five`, `comment`, `published`, `sale`, `user`, `analytics` y `edit`.

Este endpoint devuelve texto de comentarios y datos de otros usuarios. No es necesario para el alcance inicial y debe dejarse fuera del tracker hasta definir una justificación, minimización y base legal adecuadas.

### Endpoint no utilizable

`GET https://api.wallapop.com/api/v3/users/demo-user-001/seller-info` devolvió `404` en la prueba directa, aunque el bundle contiene código para solicitarlo en determinadas condiciones. No debe incorporarse como dependencia sin reproducir primero la condición que activa esa llamada.

## 4. Qué proviene de cada capa

| Capa | Datos observados | Fiabilidad para la futura arquitectura |
|---|---|---|
| HTML/SSR | Perfil, estadísticas, primera página de anuncios, enlaces e información visible | Buena para una captura inicial; requiere parsear `__NEXT_DATA__` |
| API JSON | Perfil, estadísticas, anuncios paginados, resumen y reseñas | Preferida si el uso está autorizado; contrato no público y susceptible de cambios |
| JavaScript | Transformación snake_case a camelCase, paginación, activación condicional de reseñas y seller-info | Solo referencia para entender el flujo; no debe ser la fuente de dominio |
| HTML visible | Nombre, verificación, contadores, precios, títulos, reservas visibles | Fallback frágil; puede perder campos no renderizados |

No se necesitó Playwright para obtener los datos: el navegador se utilizó para observar la página y sus llamadas, y las respuestas públicas se pudieron verificar directamente.

## 5. Autenticación, límites y riesgos

- El perfil sintético, sus estadísticas, anuncios y resumen de reseñas se modelan sin autenticación.
- No se observaron credenciales, tokens de usuario ni CAPTCHA durante esta investigación.
- La API no documenta públicamente un contrato versionado; los endpoints pertenecen al frontend y pueden cambiar sin aviso.
- La paginación de anuncios usa cursores opacos; no se debe inventar paginación por `page` para esa colección.
- `sold` aparece como contador agregado, pero la desaparición de un anuncio no prueba una venta.
- `isReserved`/`reserved` sí es una señal explícita de reserva cuando está presente.
- La respuesta de anuncios no incluyó en esta observación fechas de creación o modificación. Esos campos deberán obtenerse de la página pública del anuncio o de otro endpoint que se observe de forma autorizada; no deben inferirse del ID, slug o momento de captura.
- La API puede devolver campos distintos entre SSR y JSON; los parsers deberán normalizar ambos formatos a modelos explícitos.
- Las imágenes son URLs CDN. Para el alcance de histórico basta conservar la URL observada; descargar imágenes aumentaría volumen y superficie de datos.

## 6. Decisión recomendada para la siguiente fase, si se obtiene autorización

La cadena técnica recomendada sería:

1. `GET` de la URL pública del perfil para resolver y validar `slug -> user_id` desde SSR.
2. `GET /api/v3/users/{user_id}` para los datos normalizados del perfil.
3. `GET /api/v3/users/{user_id}/stats` para el snapshot de estadísticas.
4. `GET /api/v3/users/{user_id}/items`, reenviando `meta.next` como `since` hasta `null`.
5. `GET /api/v3/users/{user_id}/reviews/summary` solo para contador/media; dejar reseñas individuales como adaptador opcional.
6. Mantener un fallback SSR aislado y documentado, sin Playwright como camino normal.

Antes de implementar el tracker periódico hay que resolver la restricción de las Condiciones de uso: obtener autorización expresa, limitar el caso a un uso permitido por Wallapop o descartar la automatización. Esta investigación no constituye esa autorización.

## Fuentes y artefactos de observación

- [Perfil público de ejemplo](https://example.invalid/user/demo-seller-001)
- [Condiciones de uso de Wallapop](https://about.wallapop.com/condiciones-de-uso/)
- Bundle de la página observado en la carga: `https://web-static.wallapop.com/nextjs/_next/static/chunks/pages/user/%5BuserSlug%5D/%5B%5B...tabSelected%5D%5D-45896066bc9c80a9.js`

