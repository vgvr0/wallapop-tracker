# Arquitectura de notificaciones

La detección y la entrega son estados distintos:

```text
TrackingRun válido -> TrackingEvent persistido -> NotificationDelivery pendiente
                                                    -> canal HTTP
```

## Event vs delivery

`TrackingEventRecord` es el ledger idempotente de cambios detectados. No
contiene el resultado de una llamada externa. Cada destino configurado genera
una fila independiente en `notification_deliveries`, por lo que un fallo de
Telegram no altera el evento ni el `TrackingRun`.

## Modelo DB e idempotencia

`notification_deliveries` contiene `event_id`, `channel`, `destination`,
`status`, `attempts`, `last_error`, timestamps y `delivered_at`. La FK a
`tracking_events.id` usa `RESTRICT`. La restricción única
`(event_id, channel, destination)` y `create_once` hacen que reinicios o
reintentos no creen una segunda entrega para el mismo destino.

## Retries

Los estados son `pending`, `delivered` y `failed`. El límite por defecto es
tres intentos, configurable con `WALLAPOP_NOTIFICATION_MAX_ATTEMPTS`.
`deliver_pending` procesa pendientes una vez; `retry_failed` procesa fallos
que aún no han alcanzado el límite. Timeouts, errores de red, 429 y 5xx se
marcan como retryables en los adaptadores; los demás errores HTTP se conservan
como fallos para diagnóstico. No hay backoff distribuido.

## Canales y configuración

Se implementan `WebhookNotificationChannel`, `DiscordWebhookChannel` y
`TelegramNotificationChannel`, todos detrás de `NotificationChannel`. Las
URLs/destinos se configuran con `WALLAPOP_WEBHOOK_URL`,
`WALLAPOP_DISCORD_WEBHOOK_URL`, `TELEGRAM_BOT_TOKEN` y `TELEGRAM_CHAT_ID`.
El token de Telegram solo vive en configuración de proceso y nunca en la DB.

## Plantilla de compra de Telegram

Telegram usa HTML con escaping centralizado. El renderizado es específico del canal;
el payload JSON del webhook y el contenido de Discord no cambian. La plantilla
prioriza título, precio, contexto de mercado y evidencia de matching, e incluye
solo datos opcionales presentes en snapshots o metadatos ya persistidos.

Ejemplo: `🔥 iPhone 15 Pro 256GB · 💶 575 € · 📊 Mercado: ~690 € · 🎯 Deal score: 86/100`,
seguido de ubicación, envíos, métricas descriptivas del vendedor, hasta cinco
razones y un enlace `Ver anuncio`. `PRICE_DROP` muestra precio anterior/nuevo y
variación; los mínimos solo se muestran si el evento ya trae esa señal.

## Integración y fallos

El runner persiste primero el run y sus eventos; después crea las deliveries.
El scheduler procesa deliveries en una iteración separada, con timeout HTTP,
sin mantener abierta la transacción del tracking. Una excepción de enqueue o
de envío se registra y no invalida un run válido.

## Seguridad

Los tokens no se imprimen ni se incluyen en errores. El comando
`notifications list` redacta rutas de URLs HTTP. Los tests usan canales falsos
o `httpx.MockTransport`; no realizan llamadas externas.
