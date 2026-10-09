# checkout-web conventions

## Talking to the backend

- The browser never calls ledger-service or any other internal service directly. Every payment
  call goes through the `checkout-api` backend-for-frontend (BFF), which adds auth and the
  `Idempotency-Key` header.
- Retries of a payment request must reuse the same idempotency key (see INC-2291).

## Feature flags

- New checkout steps ship behind a flag in `src/flags.ts`, default off, and are removed within two
  releases of reaching 100 %.

## Money on the page

- Amounts arrive from the BFF as integer minor units plus a currency code. Format them with
  `Intl.NumberFormat`; never divide by 100 in the browser (JPY has no minor unit).
