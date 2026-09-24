# CLI reference

The package installs one entry point, `wallapop-track` (module
`wallapop_tracker.cli:app`). Every command reads its database from
`WALLAPOP_TRACKER_DB_URL` (`sqlite:///data/wallapop_tracker.db` by default) and applies
`alembic upgrade head` before the first use. Commands that contact Wallapop are read-only.

```text
wallapop-track [COMMAND]
├── add / list / enable / disable / remove / run / run-all / schedule / notify
├── search        add update list import show explain enable disable delete run run-all alerts
├── listing       add list show enable disable remove run run-all alerts
├── notifications list retry
├── metadata      categories filters brands models
├── relistings    list show
├── analytics     market prices activity sellers brands
├── score         listing search
├── market-value  LISTING_ID [--window-days N] [--json]
├── ai            assess show
    events        list show consume replay
    dlq           list show retry
    worker        events tracking notifications
```

Los comandos `events` y `dlq` están documentados en [event_bus.md](event_bus.md).

## Tracked profiles

| Command | Description |
| --- | --- |
| `add URL --alias NAME [--notes TEXT]` | Resolve the public profile and store a tracked profile. |
| `list` | List tracked profiles with their enabled state and last run. |
| `enable NAME` / `disable NAME` | Toggle participation in scheduled runs. |
| `remove NAME [--yes]` | Delete a tracked profile; asks for confirmation unless `--yes` is given. |
| `run NAME` | Track one profile now. |
| `run-all` | Track every enabled profile once. |
| `profile show ALIAS` | Show the latest observed `reports_received` value (`unknown` when unavailable). |
| `profile history ALIAS` | Show the historical observed profile metrics, including `reports_received`. |
| `profile reputation ALIAS [--json]` | Show descriptive seller reputation metrics and local peer context from stored snapshots. |
| `schedule [--once] [--interval-hours 168] [--poll-seconds 60] [--max-concurrency 4]` | Run due profiles, searches and tracked listings; `--once` evaluates due work and exits. |
| `notify [--dry-run]` | Dispatch persisted notification deliveries through the configured channels. |

## Tracked searches

| Command | Description |
| --- | --- |
| `search add --query TEXT [...]` | Create a tracked search locally; no Wallapop request is made. |
| `search import URL [--name TEXT] [--interval-seconds N] [--disabled] [--notify-on-first-run]` | Create a tracked search from a compatible public search URL, warning about unsupported parameters. |
| `search update ID [...]` | Replace only the text filters and/or the name you pass; `--clear-text-filters` removes all text filters. |
| `search list` / `search show ID` | Inspect stored configuration and baseline state. |
| `search run ID` / `search run-all` | Track one search or every enabled search. |
| `search enable ID` / `search disable ID` / `search delete ID [--yes]` | Toggle or delete a search. |
| `search explain SEARCH_ID LISTING_ID [--json]` | Explain why a stored listing matched, failed or is incomplete for a stored search. |
| `search alerts show ID` / `search alerts set ID [...]` | Read or update alert thresholds. |

`search add` options: `--name`, `--min-price`, `--max-price`, `--interval-seconds`,
`--include` (repeatable, `--include-all` for ALL), `--exclude` (repeatable), `--title-include`,
`--title-include-mode`, `--description-include`, `--description-include-mode`, `--title-exclude`,
`--description-exclude`, `--title-first-word-include`, `--title-first-word-exclude`, `--regex`,
`--regex-target`, `--category-id`, `--brand`, `--model`, `--condition`, `--latitude`, `--longitude`,
`--max-distance-km` and `--notify-on-first-run`.

`search update` accepts `--name`, the text filters (`--title-include`, `--description-include`,
`--title-exclude`, `--description-exclude`, `--title-first-word-include`,
`--title-first-word-exclude`, plus the two `*-mode` options) and `--clear-text-filters`. It keeps
every stored value you do not pass. Filter semantics are documented in
[`search_tracking_design.md`](search_tracking_design.md#filtros-avanzados-de-texto).

`search alerts set` accepts `--percentage-drop`, `--deal-score-threshold` (0–100, the deal-score
scale), `--notify-30d-low`, `--notify-90d-low`, `--notify-all-time-low` and the matching
`--clear-percentage-drop` / `--clear-deal-score-threshold` options.

## Tracked listings

| Command | Description |
| --- | --- |
| `listing add REFERENCE [--alias NAME] [--interval-seconds 600] [--notes TEXT]` | Track one listing by public URL or item ID. |
| `listing list` / `listing show REFERENCE` | Inspect tracked listings. |
| `listing run REFERENCE` / `listing run-all` | Track one listing or every enabled listing. |
| `listing enable REFERENCE` / `listing disable REFERENCE` / `listing remove REFERENCE [--yes]` | Toggle or delete a tracked listing. |
| `listing alerts show REFERENCE` / `listing alerts set REFERENCE [...]` | Read or update alert thresholds. |

`listing alerts set` accepts `--target-price`, `--percentage-drop`, `--deal-score-threshold` (0–100),
`--notify-30d-low`, `--notify-90d-low`, `--notify-all-time-low` and the matching `--no-...` /
`--clear-...` options. Background: [`tracked_listing_architecture.md`](tracked_listing_architecture.md)
and [`advanced_alerts.md`](advanced_alerts.md).

## Notifications

| Command | Description |
| --- | --- |
| `notifications list` | List persisted deliveries with their state and attempt count. |
| `notifications retry` | Retry pending and failed deliveries. |
| `notify [--dry-run]` | Dispatch deliveries (`--dry-run` only reports pending work). |

Channel configuration is environment based; see [`notification_architecture.md`](notification_architecture.md)
and the configuration section of the README.

## Metadata discovery

| Command | Description |
| --- | --- |
| `metadata categories [--context CONTEXT]` | List the observed public category catalog. |
| `metadata filters [--query TEXT] [--category-id ID]` | List filters exposed for a search context. |
| `metadata brands [--category-id ID]` | List brand options. |
| `metadata models [--category-id ID] [--query TEXT]` | List model options. |

All metadata commands are read-only and limited to endpoints validated by RAW fixtures; see
[`discovery_endpoints.md`](discovery_endpoints.md).

## Relistings, analytics and scoring

| Command | Description |
| --- | --- |
| `relistings list [--min-score 0.0-1.0] [--listing-id ID]` | List possible relistings without changing candidate status. |
| `relistings show ID` | Show one candidate with its explainable reasons. |
| `analytics market SEARCH_ID [--json]` | Read-only market summary for one tracked search. |
| `analytics prices SEARCH_ID [--weekly]` | Price series. |
| `analytics activity SEARCH_ID [--weekly]` | Activity series. |
| `analytics sellers SEARCH_ID` / `analytics brands SEARCH_ID` | Seller and brand aggregations. |
| `score listing LISTING_ID --search-id SEARCH_ID` | Deterministic score of one listing inside one search context. |
| `score search SEARCH_ID [--limit 20]` | Rank active listings of a search by their derived score. |

## AI listing analysis

`ai assess LISTING_ID [--force] [--json]` explicitly runs or reuses a cached semantic assessment. `ai show LISTING_ID [--json]` only reads the latest persisted assessment. AI is disabled by default and is configured through `WALLAPOP_AI_*` variables.

## Market value estimation

`market-value LISTING_ID` estimates the observed market median from selected
stored comparables. It defaults to a 30-day window and performs no external or
LLM request. Use `--json` for machine-readable output:

```bash
wallapop-track market-value 123
wallapop-track market-value 123 --json
```

The output reports the estimated median, observed P25–P75 range, comparable
count and confidence. It estimates asking prices, not completed-sale value.

Analytics are read-only queries over the persisted history: removed listings are never treated as
sold. Scores are contextual market analysis, not purchase advice. See
[`market_analytics.md`](market_analytics.md), [`deal_scoring.md`](deal_scoring.md) and
[`relisting_detection.md`](relisting_detection.md).

## Exit behaviour

`remove` and `search delete` ask for confirmation unless `--yes` is supplied. Commands exit non-zero
when the referenced record does not exist or when the stored configuration is invalid; Wallapop
transport errors surface as tracker errors instead of partial historical state.
# Deal ranking

Use `wallapop-track rank <listing_id>` for a human-readable explainable ranking,
or add `--json` for machine-readable output. AI is optional; missing signals are
reported and remaining weights are renormalized.
## Telegram control bot

The optional polling worker is started with `wallapop-track telegram-bot`. See
[`telegram-control-bot.md`](telegram-control-bot.md) for setup and ownership semantics.
