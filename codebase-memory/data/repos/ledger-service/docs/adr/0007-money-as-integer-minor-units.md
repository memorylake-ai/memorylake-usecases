# ADR-0007: Store money as integer minor units

- Status: Accepted
- Date: 2026-04-03
- Deciders: Priya Raman

## Decision

Every amount in ledger-service is stored as `BIGINT` minor units (cents for USD, yen for JPY)
with a separate `currency` column holding the ISO 4217 code. Never store an amount as `FLOAT`,
`DOUBLE PRECISION` or `NUMERIC` with a scale; never do arithmetic on amounts in floating point.

## Why

A rounding bug in the 2026-03 refund batch drifted 0.01 USD on 1 in 4,000 refunds because amounts
were summed as floats in the reporting job.
