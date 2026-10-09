# ADR-0004: Publish ledger events through a transactional outbox

- Status: Accepted
- Date: 2025-11-20
- Deciders: Priya Raman, ledger-service owners
- Context: Architecture review of 2025-11-18 (see `docs/reviews/2025-11-ledger-architecture-review.pptx`)

## Decision

Ledger events are written to the `ledger_outbox` table in the same Postgres transaction as the
`ledger_entries` row. A relay worker publishes outbox rows every 2 seconds and marks them sent.
Postgres stays the source of truth; the message bus is only a delivery channel.

## Why not Kafka as the source of truth

Rejected in the 2025-11-18 review: the on-call rotation cannot operate a Kafka cluster, and
exactly-once delivery into the ledger would need transactions we do not want to own.

## Consequences

- Consumers must be idempotent: the relay can publish the same row twice after a crash.
- Never publish an event outside the transaction that writes the entry.
