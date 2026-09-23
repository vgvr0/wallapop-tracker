# FastAPI application

The project exposes a local/private HTTP API at `/api/v1`. It is a transport
layer over the existing repositories, reporting functions and services; it
does not call Wallapop while serving requests.

Run it with:

```bash
uvicorn wallapop_tracker.api.app:app --reload
```

The default database is `WALLAPOP_TRACKER_DB_URL`. The health endpoint is
outside the versioned API: `GET /health` returns `{"status": "ok"}`.

Implemented endpoint groups:

* profiles and profile history;
* tracked searches, local create/update, and search URL import;
* listings and price history;
* tracked listings and local enable/update;
* events and tracking runs;
* market analytics (summary, prices, activity, sellers, brands);
* contextual deal scores;
* possible relistings;
* notification delivery state.

Collection endpoints accept `limit` and `offset`, defaulting to 50 and capped
at 200. Score search uses `limit` capped at 100. Money is serialized as a
two-decimal string and datetimes are UTC ISO-8601 values. Analytics durations
are serialized as seconds.

`POST /api/v1/searches` and `PATCH /api/v1/searches/{id}` accept the tracked
search `filters` object verbatim. It is the same JSON configuration the CLI
writes, including the advanced text filters `title_include`,
`description_include`, `title_exclude`, `description_exclude`,
`title_first_word_include`, `title_first_word_exclude` and their
`title_include_mode` / `description_include_mode` (`any` or `all`) policies.
See [`../README.md`](../README.md#search-filters) for the exact semantics.

Known missing entities return 404, invalid input returns 422, and local
configuration conflicts such as duplicate tracked-listing aliases return 409.
Notification destinations are redacted. No authentication is implemented in
this phase: the API is intended for local/private deployment and must not be
published as an internet-facing secure API.

Example:

```bash
curl http://localhost:8000/api/v1/searches?limit=20
curl "http://localhost:8000/api/v1/scores/listing/123?search_id=7"
```

Operational endpoints are documented separately in
[`observability.md`](observability.md): `/health`, `/ready` and `/metrics`.
