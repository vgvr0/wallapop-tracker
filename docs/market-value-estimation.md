# Market value estimation

`MarketValueService` calculates an on-demand, deterministic estimate from the latest stored snapshot per listing. It never calls Wallapop, an LLM, embeddings, or a new data source.

Comparables require a score of at least `0.45`: category (`.25`), brand (`.25`), title-token Jaccard (`.30`), condition (`.10`) and recency (`.10`). The candidate query is limited to 1000 rows and a 30-day window by default. One listing contributes one latest snapshot; the target is excluded and incompatible currencies are ignored.

The estimate uses median and linear-interpolated P25/P75. With at least four prices, observations outside `Q1 - 1.5*IQR` and `Q3 + 1.5*IQR` are excluded and counted. Percentile means the fraction of accepted prices strictly below the target price. `discount_vs_median = (current - median) / median`.

Confidence is bounded to 0–1 and combines sample size (45%), average similarity (35%) and low relative IQR (20%). Fewer than three accepted comparables is `insufficient`; 3–5 `low`; 6–14 `medium`; 15+ `high`.

Use `wallapop-track market-value LISTING_ID [--window-days 30] [--json]` or `GET /api/v1/listings/{listing_id}/market-value?window_days=30`.

These are asking prices, not completed sale prices. Historical observations can be biased; condition and exact model identity may be missing, and relistings can still affect results when entity resolution is uncertain. The result is an estimate, not an official appraisal.
# Ranking integration

Market value contributes a market-attractiveness signal to the overall ranking
only when its confidence is sufficient. The market median itself is not an
overall score; discount, percentile, and confidence are combined deterministically.
