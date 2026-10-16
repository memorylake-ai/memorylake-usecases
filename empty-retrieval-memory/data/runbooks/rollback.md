# Runbook - Release rollback (payments-api)

Owner: Platform team, Larkspur Pay. Last reviewed 2026-09-14.

A rollback of payments-api to an earlier release needs sign-off from two people: the on-call incident commander and one engineer from the payments team. Record both names in the incident channel before running the command.

Command: deployctl rollback payments-api --to <release tag>. A rollback must finish within 15 minutes; if it has not, page the secondary on-call.
