# Contributing

## Scope

This project is a read-only monitoring tool for authorized public Wallapop data. Changes that add
write actions against Wallapop, mass scraping, authentication servers, new databases or new external
services are out of scope unless they are discussed first.

## Development setup

```bash
python -m venv .venv
pip install -e ".[dev]"
export WALLAPOP_TRACKER_DB_URL=sqlite:///data/wallapop_tracker.db
alembic upgrade head
```

## Checks before opening a change

```bash
ruff check .
mypy src
pytest
git diff --check
```

`ruff format --check .` currently reports pre-existing formatting debt across legacy files. Do not
reformat unrelated files in the same change, but keep your own additions formatted.

## Guidelines

- Keep the client read-only and conservative: rate limiting, retries and `Retry-After` handling are
  part of the contract, not optional extras.
- Prefer extending existing services, repositories and filters over adding parallel abstractions.
- A new runtime dependency needs a reason; the shipped set is deliberately small.
- Parser changes must keep the checked-in RAW contract fixtures in `tests/fixtures/raw/` meaningful.
  Fixtures come from authorized captures; never commit credentials, cookies, tokens, full response
  headers or new personal data.
- Update the documentation that owns the behaviour you changed: `README.md` for scope and quick start,
  `docs/` for design detail, `docs/cli.md` for CLI commands, `CHANGELOG.md` for user-visible changes.
- State clearly which commands you executed when documenting a workflow. Do not describe Docker, the
  API or the CLI as verified when they were not run.

## License

This repository is distributed under the MIT License; see [`LICENSE`](LICENSE). Contributions are
accepted under the same project license.
