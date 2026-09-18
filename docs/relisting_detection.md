# Detección heurística de republicaciones

Una republicación posible relaciona dos `Listing` distintos cuando el anuncio
anterior quedó `REMOVED` y un anuncio nuevo presenta señales compatibles. Es
una relación analítica, no una afirmación de identidad: los IDs históricos no
se fusionan ni se modifican.

## Señales y score

El score determinista se normaliza entre `0` y `1` usando solo señales
disponibles:

| Señal | Peso |
|---|---:|
| mismo vendedor | 0.35 |
| similitud de título | 0.35 |
| precio | 0.15 |
| misma categoría | 0.10 |
| misma marca | 0.05 |

El título se normaliza a minúsculas, sin diacríticos ni puntuación y combina
`SequenceMatcher` (60 %) con Jaccard de tokens (40 %). La señal de precio usa
`abs(nuevo-anterior) / anterior`; su similitud cae linealmente hasta cero al
alcanzar una diferencia del 50 %. Los campos ausentes no cuentan como
desacuerdo: el score se renormaliza entre señales disponibles.

El threshold heurístico es `0.75`. Además, se descartan títulos demasiado
distintos y diferencias de precio sin similitud. No es una probabilidad
estadística calibrada.

## Candidatos y persistencia

Solo se buscan listings del mismo `seller_user_id`, con `last_seen_at` dentro de
una ventana configurable de 30 días y cuyo snapshot más reciente sea
`REMOVED`. Esta preselección evita comparar todo el histórico contra todo el
histórico. La tabla `possible_relistings` tiene una unique pair para que un
reinicio no duplique candidatos.

El análisis se ejecuta después de persistir el nuevo listing y snapshot. Si
falla, el tracking run permanece válido. Un candidato puede generar el evento
`POSSIBLE_RELISTING`, que sigue el flujo normal de `NotificationDelivery`.

## Datos ausentes y límites

Si no se conoce el vendedor, no se genera un candidato porque no existe una
preselección segura; la ausencia no se interpreta como vendedor distinto.
Marca, categoría, precio o título ausentes se omiten de la puntuación. Las
imágenes no se descargan y no se calcula perceptual hashing.

Los falsos positivos son posibles cuando un vendedor republica productos
parecidos; los falsos negativos pueden producirse por cambios grandes de
título, precio o vendedor no identificado. Futuras extensiones podrían
incorporar hashing perceptual, embeddings o ML supervisado, pero no forman
parte de esta fase.
