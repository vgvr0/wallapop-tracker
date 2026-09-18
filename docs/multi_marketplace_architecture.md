# Preparación multi-marketplace

La Fase 15 añade identidad explícita de marketplace sin implementar un
framework universal ni un segundo adapter. El único valor soportado hoy es
`wallapop`.

## Identity model

Los anuncios se identifican por `(marketplace, external_id)`. `ListingRecord`
conserva `wallapop_item_id` como puente físico de compatibilidad con las
migraciones históricas, mientras que el código nuevo lee y escribe
`marketplace` y `external_id`. `0013_marketplace_identity` rellena históricos
con `wallapop` y conserva snapshots, eventos, matches y relistings.

`TrackedSearchRecord` también tiene `marketplace`, con default `wallapop`.
`ProfileRecord` continúa usando `wallapop_user_id`: una identidad de perfil
multi-marketplace se pospone porque aún no aporta un segundo consumidor.

## Providers y adapter Wallapop

```text
SearchProvider  -> WallapopSearchProvider  -> WallapopClient
ListingProvider -> WallapopListingProvider -> WallapopClient
```

No se creó `MarketplaceClient` ni `ProfileProvider`. URLs y discovery de
categorías, marcas y modelos siguen dentro del adapter Wallapop.

## Tracking y derivados

`TrackingRun` deriva marketplace de su source y no duplica ese dato. Eventos,
notificaciones, analytics y scoring usan listing y contexto de búsqueda.
Relisting exige que ambos listings pertenezcan al mismo marketplace.

Las respuestas de listings y búsquedas incluyen `marketplace`; las creaciones
usan `wallapop` por defecto y rechazan valores no soportados con `422`. Se
mantienen aliases Python legacy durante la transición.

## Futuro

Un futuro adapter Vinted podría componerse como:

```text
VintedSearchProvider
VintedListingProvider
VintedProfileProvider
```

Sin cambiar servicios de tracking. Vinted, eBay, Milanuncios, matching
cross-market y frontend quedan fuera de esta fase.
