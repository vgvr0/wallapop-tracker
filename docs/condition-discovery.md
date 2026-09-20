# Product condition discovery

Status: `Search payload condition: PARTIALLY_DEMONSTRATED`; `Global condition catalogue: PARTIALLY_DEMONSTRATED`.

Wallapop exposes physical condition separately from commercial status. Observed detail and
profile-item payloads contain `condition.value` / `condition.text` and, in item attributes,
`type_attributes.condition.value` / `type_attributes.condition.text`. The current live detail
examples confirmed `good` = `Buen estado`, `as_good_as_new` = `Como nuevo`, and
`has_given_it_all` = `Lo ha dado todo`.

Repository fixtures additionally demonstrate `new`, `un_opened`, `in_box`, and `un_worn`.
These are extensible strings, not a closed global enum. The public search client currently
accepts a top-level `condition` value. Chrome DevTools captured the current browser request as
`GET /api/v3/search/section?...&section_type=organic_search_results`, returning HTTP 200 with
the structure `data.section.items`. The observed result items contained identifiers, title,
description, category, price, images, location, shipping and timestamps, but no `condition`,
`condition.value`, `condition.text`, or `type_attributes.condition`. The direct HTTP client
still receives 403 for the old `/api/v3/search` route, and the browser response did not provide
a condition field to fixture. The search conclusion therefore remains partial.

The code stores `condition_code` and `condition_label`; legacy `condition` remains the visible
label-compatible bridge. Missing condition stays null. Commercial fields such as `sold`,
`reserved`, `active`, and `removed` are unrelated.
