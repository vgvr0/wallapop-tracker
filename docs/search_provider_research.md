# Investigación del endpoint de búsqueda

## Decisión activa

La implementación continúa usando:

```text
GET https://api.wallapop.com/api/v3/search
```

La decisión se apoya en el contrato ya reproducible del repositorio: fixtures
offline, paginación mediante `meta.next_page`, deduplicación por `id` y
normalización cubierta por `tests/test_client.py` y
`tests/test_search_provider.py`.

## Alternativa observada

Una referencia externa no oficial publicada en 2026 describe un flujo de dos
pasos con:

```text
GET /api/v3/search/components
GET /api/v3/search/section
```

El primer paso devolvería un `search_id` y el segundo usaría
`data.section.items` y `meta.next_page`. Esta observación procede de análisis
de frontend de terceros, no de documentación oficial ni de una captura RAW
validada por este proyecto:
<https://github.com/inlanger/wallaparser/blob/main/skill/references/wallapop-api-reference.md>

## Requests y estabilidad

No se incorpora el flujo alternativo porque no existe todavía en el repositorio
un fixture RAW reproducible que demuestre sus requests, payload completo,
compatibilidad de filtros y estabilidad temporal. Cambiar ahora rompería el
contrato probado sin una ganancia validada.

La alternativa queda documentada como experimento pendiente. Cuando haya una
captura autorizada y reproducible, podrá implementarse como otro provider sin
modificar `SearchTracker`.

