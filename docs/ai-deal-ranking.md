# AI deal ranking

`DealRankingService` calculates an on-demand `overall_score` from 0 to 100. It is a
separate aggregate and does not change the existing `DealScore` formula, snapshots,
or alerts.

The default weights are DealScore `0.45`, MarketValue `0.30`, and SemanticScore
`0.25`. Weights are configured in `RankingConfig` and are renormalized across
available signals. Missing signals are therefore not silently treated as zero.

MarketValue is transformed from discount versus median and price percentile and is
excluded when confidence is `insufficient`. Lower-confidence estimates still
participate through their confidence-adjusted contribution. A persisted AI
assessment contributes semantic score and risk score only when its input hash
matches the current listing context. Ranking never executes an LLM.

Risk is an explicit penalty: `max_risk_penalty * risk_score / 100`, with a default
maximum of 25 points. Risk zero has no penalty and increasing risk cannot improve a
result. Ranking confidence is an independent 0..1 value derived from available
signals and their confidence.

Use `wallapop-track rank LISTING_ID [--search-id SEARCH_ID] [--json]` or
`GET /api/v1/listings/{listing_id}/ranking?search_id=...`. The endpoint is read-only;
no ranking table, migration, alert, Wallapop request, or LLM call is involved.

The aggregate should not be confused with any individual signal: DealScore,
MarketValue, and SemanticScore remain independently meaningful inputs.
