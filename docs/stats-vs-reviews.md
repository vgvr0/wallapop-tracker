# Validación de `/stats` frente a `/reviews/summary`

Fecha de generación del fixture sintético: 17 de septiembre de 2026 (Europe/Madrid).

Se utilizaron tres perfiles sintéticos con la misma forma de datos que el contrato observado:

| Perfil | `user_id` | Característica observada |
|---|---|---|
| `demo-seller-001` | `demo-user-001` | Perfil con muchos anuncios y reseñas |
| `demo-reviewer-001` | `demo-reviewer-001` | Perfil con volumen intermedio |
| `demo-reviewer-002` | `demo-reviewer-002` | Perfil pequeño, con dos reseñas |

## Comparación

| profile | stats.rating | reviews.rating | stats.count | reviews.count | coincide |
|---|---:|---:|---:|---:|---|
| demo-seller-001 | 4.9 | 4.9 | 12 | 12 | Sí |
| demo-reviewer-001 | 4.9 | 4.9 | 8 | 8 | Sí |
| demo-reviewer-002 | 5.0 | 5.0 | 2 | 2 | Sí |

## Diferencias observadas

Los valores principales coincidieron en los tres perfiles:

- `stats.rating` procede de `rating_average`.
- `reviews.rating` procede de `average`.
- `stats.review_count` se obtiene del contador `counters[type=reviews]`.
- `reviews.review_count` procede de `total_reviews`.

La distribución solo la proporciona `/reviews/summary`:

| Perfil | Distribución `1..5` |
|---|---|
| demo-seller-001 | `1%, 0%, 1%, 3%, 95%` |
| demo-reviewer-001 | `1%, 0%, 1%, 4%, 94%` |
| demo-reviewer-002 | `0%, 0%, 0%, 0%, 100%` |

`/stats` proporciona campos adicionales que no aparecen en el resumen: `publish`, `buys`, `sells`, `sold` y `reports_received`. En el perfil sintético, por ejemplo, `sells=15` y `sold=11`, mientras que `reviews.total_reviews=12`. `reports_received` es un contador público observado: `0` significa cero reportes devueltos explícitamente y `NULL` significa que el dato no estaba disponible; por sí solo no indica fraude, fiabilidad ni calidad del vendedor.

No se observó ningún fallo de uno de los endpoints mientras el otro funcionaba. Los dos respondieron correctamente en los tres perfiles.

## Interpretación y recomendación

Aunque los campos agregados son redundantes en esta muestra, no parecen ser respuestas intercambiables:

- Para `rating`: mantener `/stats` como fuente general de estadísticas y usar `/reviews/summary` como validación especializada.
- Para el número de reseñas: mantener ambos valores separados en la capa de parsing; actualmente coinciden, pero proceden de campos y endpoints distintos.
- Para distribución de puntuaciones: `/reviews/summary` es la única fuente observada.
- Para contadores de actividad (`published`, `sold`, `sells`, compras): `/stats` es la fuente adecuada.

La decisión actual es conservar `get_profile_stats()` y `get_review_summary()` como APIs públicas separadas, junto con `ProfileStats` y `ReviewSummary` separados. No se ha eliminado ni fusionado ningún endpoint.

