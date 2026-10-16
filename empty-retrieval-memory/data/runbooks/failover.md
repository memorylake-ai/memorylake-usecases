# Runbook - Postgres failover (ledger database)

Owner: Data platform, Larkspur Pay. Last reviewed 2026-08-30.

If the primary ledger database in us-east is unhealthy for more than 3 minutes, promote the standby replica named pg-east-2. Promotion needs approval from the on-call DBA.

Expected data loss (RPO) is at most 30 seconds. After promotion, rebuild a new standby from the nightly snapshot.
