# ADR-0003: date-fns for dates in checkout-web

- Status: Accepted
- Date: 2026-03-11
- Deciders: Tomás Ortega, checkout-web maintainers

## Decision

checkout-web formats and parses dates with `date-fns` (tree-shaken imports only, e.g.
`import { format } from "date-fns"`) and `Intl.DateTimeFormat` for locale-aware display.
moment.js was removed from the bundle in March 2026 and must not be added back.

## Why

moment.js was 67 KB gzipped of the checkout bundle and is in maintenance mode. Removing it cut
time-to-interactive on the payment page by 380 ms on a mid-range Android phone.
