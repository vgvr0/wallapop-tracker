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
* filter explanations for a stored listing of a stored search
  (`GET /api/v1/searches/{id}/listings/{listing_id}/explain`);
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
See [`search_tracking_design.md`](search_tracking_design.md#filtros-avanzados-de-texto) for the exact
semantics, and the README filter table for the summary.

`GET /api/v1/searches/{id}/listings/{listing_id}/explain` evaluates a stored
listing against a stored search and returns `matched`, the per-filter `traces`
and any `warnings`. It is read-only and runtime only: nothing is persisted, no
extra Wallapop request is issued, and the listing does not need to be a stored
match of the search, so a rejection can be explained too. See
[`search_tracking_design.md`](search_tracking_design.md#explicación-de-matching-traces).

Both `matched` and each trace `passed` are tri-state. `passed` is `true` (PASS),
`false` (FAIL) or `null` (UNKNOWN: persisted data is not enough to evaluate that
condition). Globally, `matched` is `true` when every condition passed, `false`
when at least one condition failed, and `null` when there is no known failure
but some condition is unknown; `complete` is `false` as soon as one condition is
unknown, and `warnings` summarizes those conditions:

```json
{
  "matched": null,
  "complete": false,
  "traces": [
    {
      "filter_name": "model",
      "passed": null,
      "actual_value": null,
      "expected_value": ["iphone 15"],
      "matched_values": [],
      "reason": "model is not persisted in listing snapshots"
    }
  ],
  "warnings": ["model could not be evaluated from persisted data"]
}
```

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
