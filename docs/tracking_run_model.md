# Modelo de `TrackingRun`

## Modelo anterior

`tracking_runs.profile_id` era obligatorio. Las búsquedas reutilizaban ese
campo creando un `ProfileRecord` artificial con un identificador como
`tracked-search:7`, además de guardar `tracked_search_id`. Eso mezclaba una
fuente de tracking con la identidad de un vendedor.

## Alternativas consideradas

- **A: `source_type` + `source_id`**: flexible, pero polimórfico y sin
  integridad referencial real.
- **B: FKs nullable explícitas**: permite expresar el origen con FKs reales,
  facilita SQL y puede extenderse con `tracked_listing_id`.
- **C: runs ORM especializados**: introduce jerarquía y tablas adicionales sin
  aportar valor al volumen actual.

## Decisión

Se elige B:

```text
TrackingRun
- profile_id: nullable FK a profiles.id
- tracked_search_id: nullable FK a tracked_searches.id
- ...resultado y timestamps...
```

La base de datos exige exactamente una fuente:

```text
(profile_id IS NOT NULL AND tracked_search_id IS NULL)
OR
(profile_id IS NULL AND tracked_search_id IS NOT NULL)
```

`TrackingRunRepository` expone `start_profile_run` y `start_search_run`. El
antiguo `start_tracking_run` queda como alias compatible únicamente para runs
de perfil; ya no acepta una búsqueda y no permite construir la combinación
ambigua.

## Migración de datos

La migración `0008_separate_tracking_run_sources`:

1. hace nullable `tracking_runs.profile_id` y `listings.profile_id`;
2. pone a `NULL` el perfil de todos los runs que ya tienen
   `tracked_search_id`;
3. desancla listings asociados a perfiles `tracked-search:*`;
4. limpia referencias opcionales de `tracked_profiles`;
5. elimina perfiles sintéticos solo cuando no quedan referencias;
6. añade la FK a `tracked_searches` y el `CHECK` de exactamente una fuente.

La migración no elimina runs, snapshots, matches ni eventos. Un downgrade se
rechaza si existen runs de búsqueda o listings sin perfil, porque el esquema
antiguo no puede representarlos sin inventar nuevamente una identidad.

## Semántica por origen

Un `ProfileTracker` crea un run con `profile_id` y conserva su reporting y
diff. Un `SearchTracker` crea un run con `tracked_search_id`, no crea ningún
`ProfileRecord` y puede guardar listings con `profile_id=NULL`. Una captura de
perfil posterior puede asociar un listing sin perfil a su vendedor real.

Los eventos siguen referenciando `tracking_run_id` y, para búsquedas,
`tracked_search_id`; no dependen de perfiles sintéticos.

## Evolución hacia `TrackedListing`

La evolución posterior añadió `tracked_listing_id` como tercera FK nullable y
amplió el `CHECK` para exigir exactamente una de las tres fuentes. La entidad
persistente se migró en `0010_tracked_listings` y reutiliza la identidad global
de `listings`.

## Multi-marketplace

El modelo mantiene FKs a entidades internas, no strings polimórficos. La
migración `0013_marketplace_identity` introduce `marketplace` y `external_id`
para listings y búsquedas, sin duplicar marketplace en `TrackingRun` ni cambiar
la semántica común de ejecución.
