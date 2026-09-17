# Validación de `/stats` frente a `/reviews/summary`

Fecha de captura: 17 de septiembre de 2026 (Europe/Madrid).

Se utilizaron tres perfiles públicos cubiertos por la autorización indicada para este proyecto:

| Perfil | `user_id` | Característica observada |
|---|---|---|
| `joseantoniol-64102686` | `v4z4nyeyq8jy` | Perfil con muchos anuncios y reseñas |
| `martag-16085078` | `qnzx5d9g9p62` | Perfil con volumen intermedio |
| `pablos-457067972` | `e65y995po0jo` | Perfil pequeño, con dos reseñas |

## Comparación

| profile | stats.rating | reviews.rating | stats.count | reviews.count | coincide |
|---|---:|---:|---:|---:|---|
| joseantoniol-64102686 | 4.9 | 4.9 | 294 | 294 | Sí |
| martag-16085078 | 4.9 | 4.9 | 119 | 119 | Sí |
| pablos-457067972 | 5.0 | 5.0 | 2 | 2 | Sí |

## Diferencias observadas

Los valores principales coincidieron en los tres perfiles:

- `stats.rating` procede de `rating_average`.
- `reviews.rating` procede de `average`.
- `stats.review_count` se obtiene del contador `counters[type=reviews]`.
- `reviews.review_count` procede de `total_reviews`.

La distribución solo la proporciona `/reviews/summary`:

| Perfil | Distribución `1..5` |
|---|---|
| joseantoniol-64102686 | `1%, 0%, 1%, 3%, 95%` |
| martag-16085078 | `1%, 0%, 1%, 4%, 94%` |
| pablos-457067972 | `0%, 0%, 0%, 0%, 100%` |

`/stats` proporciona campos adicionales que no aparecen en el resumen: `publish`, `buys`, `sells`, `sold` y `reports_received`. En el perfil de ejemplo, por ejemplo, `sells=417` y `sold=415`, mientras que `reviews.total_reviews=294`.

No se observó ningún fallo de uno de los endpoints mientras el otro funcionaba. Los dos respondieron correctamente en los tres perfiles.

## Interpretación y recomendación

Aunque los campos agregados son redundantes en esta muestra, no parecen ser respuestas intercambiables:

- Para `rating`: mantener `/stats` como fuente general de estadísticas y usar `/reviews/summary` como validación especializada.
- Para el número de reseñas: mantener ambos valores separados en la capa de parsing; actualmente coinciden, pero proceden de campos y endpoints distintos.
- Para distribución de puntuaciones: `/reviews/summary` es la única fuente observada.
- Para contadores de actividad (`published`, `sold`, `sells`, compras): `/stats` es la fuente adecuada.

La decisión actual es conservar `get_profile_stats()` y `get_review_summary()` como APIs públicas separadas, junto con `ProfileStats` y `ReviewSummary` separados. No se ha eliminado ni fusionado ningún endpoint.

