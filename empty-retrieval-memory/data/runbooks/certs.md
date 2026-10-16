# Runbook - TLS certificate rotation

Owner: Security engineering, Larkspur Pay. Last reviewed 2026-07-11.

Service certificates are issued by the internal certificate authority harbor-ca and rotated every 60 days. Rotations run in the Tuesday maintenance window, 14:00 to 15:00 UTC.

An expired certificate is a Sev2 incident. The rotation script is certctl rotate --service <name>.
