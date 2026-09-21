# Sold listing detection

The parser accepts explicit Wallapop signals in an item response: boolean
`sold`, `sold_out`, or `is_sold`, or a normalized status of `sold`, `sold_out`,
`soldout`, or `completed_sold`. A present item with one of these signals gets
`sale_status=sold`; `reserved` remains distinct.

An item absent from a complete valid collection only gets `presence_state=removed`.
It is never inferred to be sold. If Wallapop changes its contract, parsing
falls back to `unknown` until a new explicit signal is validated. No captured
IDs, cookies, tokens, or other sensitive data belong in this document.
