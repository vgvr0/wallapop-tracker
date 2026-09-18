# Scheduler con concurrencia acotada

La Fase 8 mantiene un único ciclo de scheduler, pero ejecuta en paralelo los
jobs due de perfiles, búsquedas y anuncios monitorizados:

```text
poll → construir due → TaskGroup + Semaphore → resultados ordenados
                                      ↓
                              NotificationService
```

## Límite global

`TrackingScheduler.max_concurrency` tiene valor por defecto `4` y debe ser
mayor o igual que uno. Un único `asyncio.Semaphore` cubre perfiles, búsquedas
y listings; no existe un límite separado por tipo que pueda superar el total.

```bash
wallapop-track schedule --once --max-concurrency 4
```

`max_concurrency=1` conserva el comportamiento secuencial.

## Aislamiento y orden

Cada job captura sus propias excepciones y las convierte en un resultado
`FAILED`; una excepción no cancela los siblings del `TaskGroup`. La lista
`SchedulerResult.results` se rellena por índice lógico: perfiles, búsquedas y
listings en el orden estable devuelto por sus repositories, independientemente
del orden de finalización.

La lista `due` se construye antes de crear las tasks. Por tanto, un mismo job
no se duplica dentro de un poll y se mantienen las reglas existentes de
`enabled`, `last_run_at` e intervalo.

## Rate limiting

Los runners siguen usando `WallapopClient`. Sus clientes comparten un limiter
asíncrono por `base_url`, con lock, último request y ventana bloqueada. La
concurrencia de jobs no equivale a una ráfaga HTTP: cada request espera el
`min_interval` compartido. Un `Retry-After` actualiza la ventana compartida y
bloquea también a los demás clientes del mismo host.

## Sessions y SQLite

Cada runner abre sus propias `Session`; ninguna se comparte entre tasks. El
HTTP ocurre antes de las transacciones de persistencia, que siguen siendo
cortas. Para SQLite en disco se activa `busy_timeout=5000` y WAL; las bases
`:memory:` mantienen su configuración compatible con `StaticPool` y no activan
WAL. Las restricciones y commits siguen dependiendo de cada transacción
individual.

## Notifications

El scheduler espera a que terminen todos los tracking jobs antes de encolar y
procesar deliveries. Las notificaciones no consumen el semáforo de requests de
Wallapop y un fallo de delivery sigue aislado del resultado del tracking.

## Procesos múltiples y timeouts

No hay distributed lock. Dos procesos scheduler pueden ejecutar el mismo job;
esa coordinación queda para una fase posterior. El timeout HTTP existente del
cliente sigue siendo el límite de red; no se añadió una cancelación global que
pudiera interrumpir una transacción.
