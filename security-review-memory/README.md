# Answering an enterprise security review with live evidence

A runnable version of the MemoryLake use case
[*Pass Enterprise Security Reviews on Agent Memory With Compliance Built In*](https://www.memorylake.ai/en/usecase/memory-compliance-for-agent-saas-selling-to-enterprise),
built entirely on the [`memorylake` CLI](https://github.com/memorylake-ai/memorylake-cli).

**The problem.** When an agent SaaS sells to an enterprise, the security review asks the same questions about the memory
layer every time: who holds credentials, can they be rotated, scoped, revoked, made temporary, is every change audited, and
can a departing customer's memory be deleted. Answers in a spreadsheet are claims. Reviewers want evidence.

**What this demo shows.** Quillstack (fictional) sells an agent that drafts procurement memos and keeps each customer's
memory in MemoryLake. Halden Mutual (a fictional bank) sends seven questions. The demo answers each one by **running the
check against your own MemoryLake team**, and writes an evidence pack where every answer carries the commands and what
they returned:

| | question | how it is checked | answer |
|---|---|---|---|
| Q1 | one credential per integration, shown once? | `api-key create --name` ×3; retry with the same `--idempotency-key`; `api-key list` / `get` | ✓ yes |
| Q2 | rotation without losing data? | `api-key rotate` → old secret 401 on the next call, new secret reads the same fact ids | ✓ yes |
| Q3 | a credential limited to one tenant? | the ingest key: `team get` → `caller_role: tenant_owner`; reads another customer's memory; lists the team's keys | **✗ no** |
| Q4 | immediate revocation? | the key revokes itself → 409; the owner revokes → 401, `api-key get` 404; what it wrote stays | ✓ yes |
| Q5 | temporary access that expires? | `api-key create --expires-at`: works before, 401 "has expired" right after | ✓ yes |
| Q6 | every change audited, incl. which credential? | `fact trace`: ADD / UPDATE, old and new text, time, MANUAL vs COOK + source messages; **no key field** | **◐ partly** |
| Q7 | offboarding: delete and prove it? | `conv delete`, `actor delete`, `proj delete` → `fact list` 404, `search` 400; Halden's keys revoked | ✓ yes |

The two answers that are not "yes" are part of the demo on purpose: an evidence pack that can only say yes is not evidence.
For each, the pack states what is true and what Quillstack can do about it.

```
        your key (owner)                                       integration keys (each in its own CLI profile)
 ┌────────────────────────┐   api-key create ×3   ┌──────────────────────────────────────────────────────────────┐
 │ team · tenant_owner    │──────────────────────▶│ ingest-worker   ──writes Halden's memory──▶ rotate (Q2) ─▶ Q3 │
 │                        │                       │ support-console ──last note──▶ revoke itself 409 · revoked (Q4)│
 │ fact trace (Q6)        │                       │ contractor-export (--expires-at +150 s) ──▶ 401 expired (Q5)  │
 │ proj delete (Q7)       │                       └──────────────────────────────────────────────────────────────┘
 └────────────────────────┘          ─▶ out/security-review-halden.md / .json  (7 answers, verdict + evidence each)
```

It runs in about 3 minutes on a free personal account (2½ of them waiting for a key to expire). The only credential you
need is a MemoryLake API key.

> **What the demo does to your keys.** It creates three keys named `mlu-sec-…`, rotates one, revokes them all, and never
> touches any other key — including the one you run it with (the server would refuse to revoke that one anyway). Keys are
> printed as `sk-` + 4 characters; each integration key lives only in its own profile under `./.memorylake-demo/keys/`
> (git-ignored), which is deleted when the key is revoked.

**Watch it run** (real recordings, unedited):
[CLI demo, 3:39](https://github.com/memorylake-ai/memorylake-usecases/releases/download/security-review-memory-v1/security-review-memory-cli-demo.mp4) · [Web companion demo, 4:58](https://github.com/memorylake-ai/memorylake-usecases/releases/download/security-review-memory-v1/security-review-memory-web-demo.mp4)

## Prerequisites

1. **A MemoryLake account and API key.** Sign up at [app.memorylake.ai](https://app.memorylake.ai),
   open **API Keys**, create a key and copy it. Details: [Authentication and API Keys](https://docs.memorylake.ai/authentication).
   The key must be able to manage API keys (a personal account's own key can).
2. **The `memorylake` CLI:**

   ```bash
   curl -fsSL https://raw.githubusercontent.com/memorylake-ai/memorylake-cli/main/scripts/install.sh | sh
   ```

   On Windows (PowerShell):

   ```powershell
   irm https://raw.githubusercontent.com/memorylake-ai/memorylake-cli/main/scripts/install.ps1 | iex
   ```
3. **Python 3.9+** (standard library only).

## Run it

```bash
git clone https://github.com/memorylake-ai/memorylake-usecases.git
cd memorylake-usecases/security-review-memory

export MEMORYLAKE_API_KEY=sk-…
python3 demo.py
```

With `MEMORYLAKE_API_KEY` set, the demo logs into its own CLI profile under `./.memorylake-demo/`
(git-ignored) and leaves `~/.memorylake` alone. Without it, it uses your existing `memorylake auth login`.

| | |
|---|---|
| `MEMORYLAKE_BASE_URL` | API endpoint; default global, China is `https://app.memorylake.cn/openapi/memorylake` |
| `MEMORYLAKE_WORKSPACE` | Workspace id; default is the one remembered by `ws use`, else your default workspace |
| `python3 demo.py pack` | Print the last evidence pack again (from `out/`, no API calls) |
| `python3 demo.py keys` | The demo's keys on your team right now, prefixes only (normally none: a run revokes them all) |
| `python3 demo.py cleanup` | Delete the projects, the conversation and the people, revoke every `mlu-sec-…` key, delete the key profiles |

It is safe to re-run. Keys are one-shot (a revoked key cannot be brought back), so every run starts from nothing: if it
finds keys, projects or key profiles from an earlier run, it cleans them up first and says so. A finished run leaves only
the second customer's project (`mlu-sec-orrin`); `cleanup` removes it.

## Or run it in the browser

```bash
python3 web/server.py        # http://127.0.0.1:8765 — standard library only
```

![Web companion — the evidence pack: rotation answered yes, least privilege answered no, each with its evidence](https://github.com/memorylake-ai/memorylake-usecases/raw/main/security-review-memory/web/screenshot-pack.png)

[Watch the web companion demo (mp4, 4:58)](https://github.com/memorylake-ai/memorylake-usecases/releases/download/security-review-memory-v1/security-review-memory-web-demo.mp4).

Paste the key, press **Run demo**, and watch each question get its answer: the three keys with their masked secrets and
the prefix-only key list, Halden's memory written by the ingest key, the rotation (old secret refused, same facts), what
the ingest key can reach, the self-revoke refusal, the expiry countdown, the audit trail, and the offboarding 404 / 400.
**Evidence pack** shows all seven answers with their evidence. The terminal drawer shows every `memorylake` command as it
runs; a command run with an integration key ends in `# as mlu-sec-…`. No key is ever sent to the page.

## What happens, step by step

All output below is from real runs on a free account (ids shortened).

### 2. Q1 — one key per integration, the secret shown once

```
$ memorylake api-key create --name mlu-sec-ingest-worker --idempotency-key mlu-sec-ingest-worker-20261010103531
$ memorylake auth login --api-key sk-f66b… --base-url https://app.memorylake.ai/openapi/memorylake --profile memorylake-usecases   # as mlu-sec-ingest-worker
  · mlu-sec-ingest-worker → id 15419 · sk-f66b… (67 chars), stored in its own profile .memorylake-demo/keys/mlu-sec-ingest-worker/ and nowhere else
  …
$ memorylake api-key create --name mlu-sec-contractor-export --idempotency-key … --expires-at 1791628689
  · retry the first create with the same idempotency key (what a client does after a timeout)
$ memorylake api-key create --name mlu-sec-ingest-worker --idempotency-key mlu-sec-ingest-worker-20261010103531
  · replayed: id 15419 (same key), the secret field is empty — it is shown exactly once
$ memorylake api-key list
  id     name                        prefix    status   expires_at
  15423  mlu-sec-contractor-export   466e4fc4  enabled  2026-10-10 10:38:09Z
  15422  mlu-sec-support-console     5faac23d  enabled  —
  15419  mlu-sec-ingest-worker       f66bab00  enabled  —
  · `api-key list` / `get` return no secret — only `key_prefix`
```

### 3. Halden Mutual's memory, written through the ingest worker's key

Every command in this step runs with the ingest key's profile: the project, Ines (Halden's procurement lead) and Quill (the
memo assistant), a five-message setup call (`msg append --wait`, read into memory in ~40 s), and two pinned settings, each
stamped with `fact update --metadata '{"written_by": "mlu-sec-ingest-worker", …}'`. A second customer, Orrin Freight, gets
its own project — written with your key.

### 4. Q2 — rotation

```
  · 8 fact(s) in Halden's memory before the rotation
$ memorylake api-key rotate 15419 --idempotency-key mlu-sec-ingest-worker-rotate-20261010103531
  · same id 15419, new prefix 97d333ac (was f66bab00), new secret sk-97d3…
$ memorylake fact list --projects proj-4ab0… --workspace ws-…   # as mlu-sec-ingest-worker
  · old secret: refused after 0.8s — HTTP 401 “API key was rejected by the server (authentication_error): service account not found”
$ memorylake fact list --projects proj-4ab0… --page-size 100 --workspace ws-…   # as mlu-sec-ingest-worker-v2
  · new secret: rc 0, 8 fact(s) — the same ids
```

Memory belongs to the team, not to the key, so nothing is re-ingested.

### 5. Q3 — what one integration key can reach (the answer is no)

```
$ memorylake team get   # as mlu-sec-ingest-worker-v2
  · caller_role: tenant_owner
$ memorylake fact list --projects proj-d934… --workspace ws-…   # as mlu-sec-ingest-worker-v2
  · Orrin Freight's memory: readable — Orrin Freight: purchase memos above 40,000 dollars need a second sign-off from the CFO, T…
$ memorylake api-key list   # as mlu-sec-ingest-worker-v2
  · `api-key list` with the ingest key: rc 0, 5 keys of the team (prefixes)

  Q3 Least privilege: ✗ no
    No — not per key. A MemoryLake API key acts for the whole team: the ingest worker's key reports `caller_role: tenant_owner`,
    reads another customer's memory (Orrin Freight) and lists the team's keys. The isolation boundary is the team, not the key.
    note: Mitigation Quillstack can state: one MemoryLake team per enterprise customer, with its own keys; inside a team,
    scoping is enforced by Quillstack's own service, which holds the key.
```

### 6. Q4 — revocation

```
$ memorylake fact add --project proj-4ab0… 'Support note: Halden Mutual asked that Quillstack staff sign in to the admin console with SSO only.'   # as mlu-sec-support-console
$ memorylake api-key revoke 15422   # as mlu-sec-support-console
  · the key tries to revoke itself → HTTP 409 “cannot revoke the key used for this request [STATE_NOT_READY]”
$ memorylake api-key revoke 15422
$ memorylake fact list --projects proj-4ab0… --workspace ws-…   # as mlu-sec-support-console
  · its secret now: refused after 0.6s — HTTP 401 “API key was rejected by the server (authentication_error): service account not found”
$ memorylake api-key get 15422
  · api-key get 15422 → HTTP 404 api key not found [NOT_FOUND]
  · the note it wrote is still in Halden’s memory (fact-dfbd…)
```

### 7. Q5 — the contractor's key expires on its own

```
$ memorylake proj list --workspace ws-…   # as mlu-sec-contractor-export
  · 70s before its expiry (2026-10-10 10:38:09Z): rc 0
  · waiting for the expiry time — nobody revokes this key
$ memorylake proj list --workspace ws-…   # as mlu-sec-contractor-export
  · 3s after expiry: refused — HTTP 401 “API key was rejected by the server (authentication_error): service account has expired”
$ memorylake api-key get 15423
  · api-key get 15423 → status “enabled”, expires_at 2026-10-10 10:38:09Z (the status field does not change at expiry; compare expires_at with the clock)
```

### 8. Q6 — audit trail (partly)

Halden signs amendment 1 (drafts kept 60 days, not 90) and the owner updates the setting in place:

```
$ memorylake fact trace fact-1b49… --project proj-951c…
  UPDATE MANUAL  2026-10-10T10:31:37  Halden Mutual: memo drafts are kept in memory for 60 days, then delet…
  ADD    MANUAL  2026-10-10T10:29:51  Halden Mutual: memo drafts are kept in memory for 90 days, then delet…

  extracted fact fact-881d…: Any Halden Mutual purchase memo above 25,000 euros goes to Marek Holm in treasu…
  ADD COOK, read from 4 message(s):
    #1 2026-09-29T09:00  Ines Varga (procurement lead, Halden Mutual): For every purchase memo, put the total cost…
    …
  · trace entry fields: event, history_id, new_fact, old_fact, source_entry_ids, source_event_id, source_kind, timestamp — none names the key that made the change
  · the fact's own metadata says written_by=mlu-sec-ingest-worker — set by the writer, so self-reported
```

The trace answers *what*, *when* and *from where*; it does not record *which key*. The `trace` timestamp is server time
(when the change was made), and a COOK entry's `source_entry_ids` point at the conversation messages it was read from.

### 9. Q7 — offboarding

```
$ memorylake conv delete conv-390b…
$ memorylake actor delete actor-6b79…
$ memorylake actor delete actor-efe9…
$ memorylake proj delete proj-951c…
$ memorylake fact list --projects proj-951c…
  · fact list on the deleted project → HTTP 404 Project not found with id: proj-951c… [NOT_FOUND]
$ memorylake search 'memo retention' --projects proj-951c…
  · search over it → HTTP 400 Project 'proj-951c…' does not belong to workspace 'ws-83f0…' [INVALID_ARGUMENT]
$ memorylake api-key revoke 15408
$ memorylake api-key revoke 15406
  · demo keys left on the team: 0
```

### The evidence pack

```
  Quillstack — security review answers for Halden Mutual  (2026-10-10T10:38:24+00:00)
  5 yes · 1 partly · 1 no

  Q1  ✓ yes     Credentials       Yes. Each integration gets its own named key (`api-key create --name`). The secret …
  Q2  ✓ yes     Rotation          Yes. `api-key rotate` keeps the key id and issues a new secret; the old secret was …
  Q3  ✗ no      Least privilege   No — not per key. A MemoryLake API key acts for the whole team: the ingest worker's…
  Q4  ✓ yes     Revocation        Yes. `api-key revoke` cut the support console off on the next call (0.6 s later) an…
  Q5  ✓ yes     Temporary access  Yes. A key created with `--expires-at` worked until that moment and was refused rig…
  Q6  ◐ partly  Audit trail       Partly. `fact trace` records every change to a fact — ADD / UPDATE / FORGET, the ol…
  Q7  ✓ yes     Offboarding       Yes. Deleting the customer's conversation, people and project removed its memory (8…

  full pack: out/security-review-halden.md (evidence for every answer) · .json
```

`out/security-review-halden.md` has the question, the answer, the note and every piece of evidence (command → result,
✓/✗) for each of the seven. A check that fails is written as "Not confirmed by this run" — the pack never reuses an
expected answer the run did not show.

### Measured

6 runs on a free account after the last fix (a re-run, `--reset`, 3 parallel copies with their own prefixes, the web
companion), each starting from nothing: the same seven verdicts every time (5 yes · 1 partly · 1 no). The old secret after `rotate` and the revoked
secret were refused on the first call each time (0.5–0.9 s after the change); the expired key on the first call 2–3 s after
`expires_at`. `caller_role` of an integration key: `tenant_owner` 6/6. Each run took 2 min 50 s – 3 min.

## Using this in your own review

- **One key per integration, named for it.** Revocation and rotation then retire exactly one integration.
- **Rotate with an idempotency key.** A retried `rotate` or `create` replays the first result instead of minting another key;
  the secret is only in the first reply, so store it before anything else.
- **Give temporary access an expiry.** `--expires-at` is enforced by the server; your key inventory should compare
  `expires_at` with the clock, because `status` stays `enabled`.
- **Say "no" where the answer is no.** Keys are team-wide. If a customer needs credential-level isolation, give that customer
  its own MemoryLake team. Within a team, the scoping is your service's job.
- **Stamp the writer.** The trace does not say which key made a change; put the integration's name in the fact's
  `metadata`, and say in the answer that it is self-reported.
- **Offboarding is three deletes and two checks.** `conv delete` (a project delete does not delete conversations),
  `actor delete`, `proj delete`; then `fact list` → 404 and `search` → 400.

## If something goes wrong

- **`api-key create` is refused (403 / permission)**: the key you run with cannot manage keys. Use a key of the team owner.
- **"STILL ACCEPTED" after a rotate / revoke / expiry**: the demo polls for 30 s and prints how long it took; if it is still
  accepted after that, the answer is written as not confirmed. Re-run to see whether it repeats.
- **The run stopped half-way**: run `python3 demo.py` again — it finds the leftover `mlu-sec-…` keys, projects and key
  profiles, cleans them up and starts over. `python3 demo.py keys` shows what is left on your team.
- **`tls handshake eof` / `could not connect`**: a dropped connection. Read-only commands retry four times; otherwise
  re-run (it cleans up first).

## Files

```
demo.py                  the runner; prints every CLI command it runs (and emits events for the web app)
data/review.json         Quillstack, Halden Mutual, the three integration keys, the setup call, pinned settings, the 7 questions
web/server.py            local web companion (stdlib HTTP server + Server-Sent Events over demo.py)
web/static/              the page — no build step
out/                     security-review-halden.md / .json (the evidence pack) and runs.jsonl (git-ignored)
.memorylake-demo/keys/   one CLI profile per integration key while it exists (git-ignored, deleted on revoke)
```

All names and companies are fictional.
