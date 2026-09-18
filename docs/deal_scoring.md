# Deterministic deal scoring

Deal scoring is a read-only, contextual calculation for a listing inside one
tracked search. It measures relative price anomaly and observed context; it is
not a recommendation to buy and does not perform any automatic action.

## Signals and weights

The score starts at 50 and is clamped to 0–100. The relative price signal is
the dominant signal:

* relative price: 55 points, scaled linearly to the market median at ±30%;
* price at or below P25: 12 points;
* freshness: up to 15 points (`<15 min`, `<1 h`, `<24 h` thresholds);
* observed price drops: up to 10 points, three points per drop;
* objective seller metrics: up to 10 points from review count and rating.

Possible relisting is reported as context and contributes zero points. The
listing being scored is excluded from the comparable set. Market median and
quartiles come from `reporting.market`, using the latest valid search state.

## Confidence and insufficient data

`confidence` is separate from opportunity score. It is derived from priced
comparables and valid historical runs, capped at 1.0. A score requires at
least three priced comparables, a market median, a search match, and a current
listing price. Otherwise the result is `insufficient_data` with `score=None`.
Missing seller, drop, or relisting data does not manufacture a signal or make a
valid score fail.

## Context and persistence

The same listing can receive different scores for different searches because
each result is evaluated against that search's market. Results are derived and
not persisted; recalculation therefore always reflects the latest valid market
state. Historical backtesting and calibration are intentionally deferred.

## CLI

```text
wallapop-track score listing <listing-id> --search-id <id>
wallapop-track score search <search-id> --limit 20
```

The batch command ranks the latest active listings in that search and does not
persist a ranking. No ML, LLM, embeddings, forecasting, or purchase labels are
used.
