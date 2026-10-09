# Runbook: ledger-service on-call

## Outbox relay lag alert (`LedgerOutboxLagHigh`)

Fires when the oldest unsent row in `ledger_outbox` is older than 60 seconds.

1. Check the relay deployment: `kubectl -n ledger get pods -l app=outbox-relay`.
2. If the relay is crash-looping, roll back to the previous image tag; the outbox keeps every
   unsent row, so nothing is lost while it is down.
3. Do NOT delete rows from `ledger_outbox` by hand. Duplicate delivery is safe (consumers are
   idempotent); a missing event is not.

## Double charge reported by support

1. Look up both payment attempts by `Idempotency-Key` in `ledger_requests` (kept 72 hours).
2. If the two attempts have different keys, the client retried without reusing its key — file it
   against the client, not the ledger (see INC-2291).
