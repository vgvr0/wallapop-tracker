# Changelog

All notable changes to this project are documented in this file.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - 2026-09-23

First stable release of what the repository already implements. No new tracking capability, backend or
integration was added in this release; the work focused on making the existing project releasable and
honestly documented.

### Added

- `CHANGELOG.md`, `CONTRIBUTING.md` and `.env.example` (real variables only, with the defaults the
  code already uses).
- `docs/cli.md` with the complete `wallapop-track` command reference, moved out of the README.
- Distribution metadata: long description from the README, project URLs and `uvicorn` as a runtime
  dependency so the local/private API can be started from a normal installation.

### Changed

- `pyproject.toml`: version `1.0.0` and a description covering the current scope (marketplace
  monitoring and deal intelligence) instead of profile parsing only.
- `README.md` rewritten as a landing page: verified Quick Start, scope and security limits, real
  configuration table, documentation map and concise feature overview. Deep filter, API, alert,
  analytics and observability details now live in `docs/`.
- `alembic/env.py` honours `WALLAPOP_TRACKER_DB_URL` when `alembic.ini` still holds the default URL, so
  migrations always target the same database as the application.
- HTTP `User-Agent` reports version `1.0`.
- Documented that a database created by the application needs `alembic stamp head` before
  `GET /ready` returns `200`.

### Fixed

- Docker: the image had no `CMD`, so `docker compose up` did not start any useful process. The image
  now runs `uvicorn wallapop_tracker.api.app:app --host 0.0.0.0 --port 8000` (still overridable with
  `docker compose run --rm tracker wallapop-track ...`) and declares `EXPOSE 8000`.
- Docker: `uvicorn` was only a development extra even though the README and `docs/api.md` document
  starting the API with it.

### Release notes

- Verified in the release environment: `pytest` (347 passed, 1 live test deselected), `ruff check .`,
  `mypy src` and `git diff --check`. `ruff format --check .` keeps failing on pre-existing legacy
  formatting debt; this release adds none.
- Verified locally: `alembic upgrade head` on an empty database, the CLI on a migrated database, and
  the API serving `/health`, `/ready` and `/metrics` with `200` from that database.
- Not verified: `docker build` and `docker compose up` could not be executed in the release
  environment (no Docker runtime available). The Docker flow is documented from inspection only.
- Known limitations kept as-is: no authentication (local/private API only), no license file (owner
  decision pending), and third-party public profile data present in authorized RAW fixtures, which
  should be reviewed before the repository is made public.

[1.0.0]: https://github.com/vgvr0/wallapop-tracker/releases/tag/v1.0.0
