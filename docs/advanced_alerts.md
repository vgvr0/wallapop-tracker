# Advanced alerts

Tracked listings and searches can configure `target_price`,
`percentage_drop_threshold` (1–100), and `deal_score_threshold` (0–100), plus
the `notify_on_30d_low`, `notify_on_90d_low`, and `notify_on_all_time_low`
flags. Existing `PRICE_DROP` remains the raw price transition; the advanced
events add threshold and historical context.

`TARGET_PRICE_REACHED` means the previous valid price was above the target and
the current price is at or below it. `PERCENTAGE_DROP` uses
`(previous-current)/previous * 100`, only for valid positive previous prices.
The low events compare against observations strictly before the current
snapshot, so the current value cannot make its own baseline. A window with no
prior observations produces no event.

Events carry explanatory JSON metadata and use a unique deterministic key
containing the global listing and snapshot transition. Notification delivery
keeps its existing event/channel/destination uniqueness, so retries and
process restarts remain safe. `SOLD` is emitted only for an explicit status
transition; disappearance remains `REMOVED`. Possible relistings retain their
existing conservative candidate model and event.

The scorer is contextual and returns a 0–100 score. Valid observations are
stored per global listing and tracked-search context. The first score is a
silent baseline; an event is emitted only when `previous_score < threshold`
and `current_score >= threshold`. Remaining above is silent, dropping below
arms the next crossing, and a later crossing emits again.

CLI examples:

```text
wallapop-track listing alerts set camera --target-price 500 --percentage-drop 10 --deal-score-threshold 80 --notify-30d-low
wallapop-track search alerts set 3 --percentage-drop 10 --deal-score-threshold 80
wallapop-track listing alerts show camera
```
