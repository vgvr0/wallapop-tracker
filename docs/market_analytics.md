# Market Analytics

Los analytics son consultas read-only sobre el histórico persistido. Para una
búsqueda, el universo se limita siempre a `SearchMatch`; que un listing exista
en la tabla global no lo incluye automáticamente en otra búsqueda.

## Métricas

`MarketSummary` calcula listings activos en el último run válido, IDs únicos
observados, nuevos, removidos, cambios de precio y posibles relistings. Los
precios usan el snapshot más reciente hasta el instante consultado y devuelven
`Decimal`.

Los percentiles P25, mediana y P75 usan interpolación lineal determinista sobre
los precios ordenados. Los precios ausentes se excluyen sin romper el resto de
las métricas. `get_price_distribution` genera bins deterministas entre mínimo
y máximo.

## Tiempo y presencia

Un listing activo es aquel presente en el último `TrackingRun` válido de la
búsqueda hasta el final del periodo. Los nuevos se basan en
`SearchMatch.first_seen_at`, que significa primera observación del tracker, no
fecha de creación en Wallapop.

Un removed es una diferencia entre dos runs válidos consecutivos: estaba
presente en el anterior y ausente en el siguiente. `REMOVED` no implica
`SOLD`.

La duración es `observed_active_duration`: desde `first_seen_at` hasta la
primera ausencia observada o `last_seen_at` si continúa presente. Es una
duración observada, no la vida real exacta del anuncio.

## Series y agregaciones

`get_price_time_series` produce precio mediano, medio y activos por día o
semana. `get_activity_time_series` produce nuevos, removidos y bajadas.
También existen agregaciones objetivas por vendedor, marca y categoría, con
listing count, activos, mediana y bajadas cuando aplica. El modelo no se
infiere desde el título; el análisis de modelos queda pendiente porque no hay
un campo robusto normalizado.

Los filtros `start_at` y `end_at` son opcionales y se interpretan en UTC.
Los datasets vacíos devuelven cero o `None`, nunca divisiones por cero.

Los `PossibleRelisting` no fusionan IDs: un relisting posible sigue contando
como dos listings únicos, y se expone por separado mediante
`possible_relisting_count`.
