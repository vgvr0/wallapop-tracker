# Declarative search configuration

La base de datos sigue siendo la fuente de verdad. Estos archivos son una
representación versionada para backups, revisión en Git, migraciones y
bootstrap manual de una instalación.

## Schema

El documento contiene `version: 1` y una lista `searches`. Cada búsqueda usa
`name` como identificador estable, además de `query`, `enabled` e
`interval_seconds`. Los filtros van dentro de `filters`, la geolocalización
dentro de `location` y las alertas dentro de `alerts`. Los campos desconocidos,
versiones no soportadas, coordenadas inválidas y filtros mal formados fallan
antes de tocar la base de datos.

El ejemplo completo está en [`examples/searches.example.yaml`](../examples/searches.example.yaml).

## Exportar e importar

```bash
wallapop-track search export-config searches.yaml
wallapop-track search export-config searches.json --format json --enabled-only
wallapop-track search import-config searches.yaml --dry-run
wallapop-track search import-config searches.yaml
wallapop-track search import-config searches.yaml --update-existing
```

El formato se infiere de `.yaml`, `.yml` o `.json`; también se puede indicar
`--format yaml` o `--format json`. La exportación es determinista y omite IDs
internos, timestamps, leases, eventos, estados de notificación y secretos.

Por defecto, una colisión de `name` es un error. `--update-existing` actualiza
por nombre y es idempotente. `--dry-run` valida todo y muestra el plan sin
escribir. El import real valida primero el documento completo y escribe todas
las búsquedas en una única transacción; un fallo hace rollback completo.
`--replace-existing` no se ejecuta porque sería destructivo; usa
`--update-existing` para reconciliar búsquedas concretas.

## Backups, GitOps y Docker

Se puede versionar el YAML exportado y restaurarlo con el comando de import.
En Docker se puede montar un archivo en `/config/searches.yaml` e importarlo
explícitamente después de aplicar las migraciones. No se importa
automáticamente al arrancar, para evitar sobrescribir datos existentes.

## Compatibilidad futura

Las versiones nuevas deben aceptar la versión 1 mientras sea posible. Si el
schema cambia de forma incompatible, se añadirá una nueva versión y el error
de import indicará que la versión no es compatible.
