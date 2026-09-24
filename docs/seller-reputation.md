# Seller Reputation Intelligence

Seller Reputation Intelligence is a read-only, descriptive view over public seller
metrics already persisted by the tracker. It does not make live Wallapop requests,
publish events, create notifications, or alter deal scoring.

## Signals and sources

| Signal | Source | Type | Historical availability |
| --- | --- | --- | --- |
| `rating` | `profile_snapshots.rating` | decimal, nullable | snapshot history |
| `reviews` | `profile_snapshots.review_count` | integer, nullable | snapshot history |
| `sales` | `profile_snapshots.sales_count` | integer, nullable | snapshot history |
| `published` | `profile_snapshots.published_count` | integer, nullable | snapshot history |
| `sold` | `profile_snapshots.sold_count` | integer, nullable | snapshot history |
| `reports_received` | `profile_snapshots.reports_received` | integer, nullable | snapshot history |
| star distribution | `profile_snapshots.rating_1_pct` … `rating_5_pct` | integer percentages, nullable | snapshot history |
| seller type | `profiles.seller_type` | string, nullable | current profile record |
| account age | `profiles.registered_at` | datetime, nullable | not used unless reliably observed |

The tracker currently persists star percentages rather than review-count buckets.
When both a review count and a percentage are present, the implementation derives
star counts by rounding `reviews * percentage / 100`. Missing inputs remain missing.

## Derived metrics

The service derives low-rating count (`1`–`3` stars), low-rating ratio, reports per
100 sales, reports per 100 reviews, and 30/90-day report deltas. It also reports
30-day rating, review and sales changes. Division by zero and unavailable inputs
produce `null`, never zero by assumption.

Historical baselines are the latest snapshot at or before the requested 30-day or
90-day cutoff. No second time series is stored.

## Peer comparison

Peers are sellers with a current snapshot and observed reports, matching seller type
when the target has one and, when sales are known and positive, having sales between
half and twice the target's observed sales. At least 20 peers are required. The
output is the peer median, the percentage of peers whose observed report count is at
or below the target, and the sample size.

The percentile is a relative position among sellers observed by this tracker. It is
not a probability, risk score, trust score, or fraud score. Interpretation labels
(`below_peer_range`, `within_peer_range`, `above_peer_range`, and
`very_high_relative_to_peers`) describe only that relative position.

## Interfaces and limitations

Use `wallapop-track profile reputation <alias>` or add `--json`. The API endpoint is:

```text
GET /api/v1/profiles/{id}/reputation
```

The response includes `data_quality`, `confidence`, and warnings for unavailable
reports or rating, missing historical windows, and insufficient peers. A higher
`reports_received` count does not establish misconduct, fraud, danger, or seller
quality; it is only a count observed from public marketplace data. Peer percentile
likewise describes relative position among the local observed sample.

No automatic alerts are generated and no reputation metric is mixed into
`deal_score`.
