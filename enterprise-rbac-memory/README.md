# Role-based access to AI memory, enforced by the server

A runnable version of the MemoryLake use case
[*Enterprise-Grade AI Memory: Security, Compliance, and Scale*](https://www.memorylake.ai/en/usecase/enterprise-ai-memory-deployment),
built entirely on the [`memorylake` CLI](https://github.com/memorylake-ai/memorylake-cli).

**The problem.** *Access is not binary. Enterprise deployments require different roles to have different permissions across
different memory types.* The page promises **read, write and administrative permissions**, enforced in the memory layer, and an
audit trail. This demo sets that up for three AI assistants and then has each of them try everything, with its own key.

**What this demo shows.** Corvane Mutual (a fictional insurer) gives three AI assistants access to one memory with three parts:

| | Underwriting policy | Claims case notes | HR investigations |
|---|---|---|---|
| **claims-assistant** — drafts replies, keeps claim files | read | **write** | — |
| **underwriting-bot** — answers rule questions | **write** | — | — |
| **audit-reviewer** — the external auditor's assistant | read | read | read |

That table is [`data/policy.json`](data/policy.json). The demo builds it for real and checks it:

1. **A sandbox org team.** A personal team cannot have members; an org team can. `team create --issue-api-key` makes one on
   your account, with an owner key printed once. Everything else happens in that team; `cleanup` deletes it.
2. **One member and one key per assistant.** `member create` makes a *virtual member* (no login; it acts only through API
   keys), `member scope set` limits it to the projects the policy names, with the actions of its level, `access set-level`
   records the level, and `api-key create --member` issues its key.
3. **27 calls, each answered by the server.** Every assistant runs `fact list`, `search` and `fact add` on every project with
   its own key. **27 of 27** answers match the policy, in every run. Refusals are `403 ACCESS_DENIED` in the server's own words:
   *You don't have the "Add Memory" permission on project "Underwriting policy".*
4. **Who can reach the HR file, and why.** `access show` lists the members that reach a project, at what level and through
   what (role, level, scope), and the members that cannot.
5. **A grant changes, and the quick way grants too much.** A ticket gives underwriting-bot *read* on the claim files.
   `access grant` + `access set-level read` makes the next search succeed, but a write succeeds too: `access show` reports
   the effective level as **write**. Levels only add, and a scope entry without actions carries every action the role allows.
   `member scope set` with the read actions makes the write fail again, and `access show` says **read**.
6. **A key leaks.** `member remove` and the same call with that key is refused **within a second** (`401 service account is
   disabled`). What it wrote stays.
7. **The audit log.** `audit-log list` shows the 20 access changes of the run: team created, members created, scopes set,
   levels assigned, keys created, member removed, each with the key that made it and its target.

**What MemoryLake enforces and what the demo adds.** Every allow and every refusal in this demo is the server's answer to a
call made with that assistant's key. The demo only writes the policy down, applies it, and compares. Two things it adds
itself, and says so: facts carry no record of who wrote them, so each assistant stamps `metadata.written_by` on what it
writes (self-reported); and the audit log does not keep the `--reason` text, so the reasons are in `data/policy.json` and in
the report.

**Watch it run** (real recordings, unedited): [CLI demo, 2:28](https://github.com/memorylake-ai/memorylake-usecases/releases/download/enterprise-rbac-memory-v1/enterprise-rbac-memory-cli-demo.mp4) · [web demo, 3:20](https://github.com/memorylake-ai/memorylake-usecases/releases/download/enterprise-rbac-memory-v1/enterprise-rbac-memory-web-demo.mp4)
(GitHub serves release videos as downloads.)

## Prerequisites

1. **A MemoryLake account and API key of your personal team.** Sign up at [app.memorylake.ai](https://app.memorylake.ai),
   open **API Keys**, create a key and copy it. Details: [Authentication and API Keys](https://docs.memorylake.ai/authentication).
   An account may own **one org team** besides its personal team. The demo creates one and deletes it at the end. If your
   account already owns an org team, the run stops at step 2 and says so.
2. **The `memorylake` CLI** (v20261013 or later):

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
cd memorylake-usecases/enterprise-rbac-memory

export MEMORYLAKE_API_KEY=sk-…
python3 demo.py
python3 demo.py cleanup
```

A run takes about 70–90 seconds; nothing in it waits for an LLM. With `MEMORYLAKE_API_KEY` set, the demo logs into its own
CLI profile under `./.memorylake-demo/` (git-ignored) and leaves `~/.memorylake` alone. The sandbox owner key and the three
assistant keys each live in their own profile there (`org/`, `members/<name>/`) and are only ever printed as `sk-` + 4
characters.

| | |
|---|---|
| `MEMORYLAKE_BASE_URL` | API endpoint; default global, China is `https://app.memorylake.cn/openapi/memorylake` |
| `python3 demo.py --reset` | Delete a sandbox team left by an earlier run first, then run (a run does this on its own) |
| `python3 demo.py audit` | Read the sandbox team's audit log again |
| `python3 demo.py report` | Print the last access review (`out/access-review.md`, `.json`) |
| `python3 demo.py cleanup` | Delete the sandbox team (its projects, members and keys go with it) and the local profiles |
| `--show-json` | Print the raw JSON the CLI returns (keys masked) |

**Every run starts from nothing.** Teams, members and keys are one-shot, so a run that finds a sandbox team from an earlier
run deletes it first and says so. The run leaves the sandbox team in place so you can look around; `cleanup` deletes it.

## Or run it in the browser

```bash
python3 web/server.py        # http://127.0.0.1:8765 — standard library only
```

![Web companion — 27 calls, each assistant with its own key: 27/27 as the policy says](https://github.com/memorylake-ai/memorylake-usecases/raw/main/enterprise-rbac-memory/web/screenshot-matrix.png)

![Web companion — the quick grant is effectively write; the scoped fix makes it read](https://github.com/memorylake-ai/memorylake-usecases/raw/main/enterprise-rbac-memory/web/screenshot-grant.png)

Paste the key, press **Run demo**, and follow the nine steps: the sandbox team, the three projects, the members and the
policy, the 27-call matrix filling in as each call returns, who reaches the HR file, the grant that over-reaches and its fix,
the removed member, and the audit log (**Read the audit log again** re-reads it). **Access review** shows the report.
The terminal drawer shows every `memorylake` command as it runs, with the key it ran under.

## What happens, step by step

### 1. Connect

```
$ memorylake auth login --api-key sk-f7b3… --base-url https://app.memorylake.ai/openapi/memorylake --profile memorylake-usecases
$ memorylake team get
  · connected to team “you@example.com” (personal) as tenant_owner
```

### 2. A sandbox org team

```
$ memorylake team create --name 'Corvane Mutual - AI memory sandbox' --issue-api-key --api-key-name mlu-rbc-owner --idempotency-key mlu-rbc-20261013062300
$ memorylake auth login --api-key sk-fb99… --base-url … --profile memorylake-usecases   # as sandbox owner
  · org team 9fa94401… “Corvane Mutual - AI memory sandbox” — owner key mlu-rbc-owner (id 15981) is printed once; it now lives only in .memorylake-demo/org/
$ memorylake ws list --page-size 100   # as sandbox owner
  · sandbox team type “org”, your role there “tenant_owner”, workspace “Default Workspace” (ws-42e69c36…)
```

The owner key is in the `team create` reply only. A retry with the same `--idempotency-key` returns the same team **without
the key**, so the demo writes the team id to `.memorylake-demo/sandbox.json` and logs the key into its profile before doing
anything else.

### 3. Corvane's memory

Three projects, written with the owner key: the underwriting rule book (6 facts), the open claim files (6), and the HR
investigations file (4). `fact add` takes all of a project's facts in one call.

### 4. One member per assistant, limited by the policy, each with its own key

```
$ memorylake member create --name claims-assistant --role tenant_member   # as sandbox owner
  · claims-assistant: virtual member 7f8af5eb… (virtual, role tenant_member)
…
$ memorylake member scope set 7f8af5eb… --reason 'AI-ACC-101: claims assistant keeps claim files, reads rules' --scopes '[{"resource_type": "memorylake:project", "mode": "instances", "instances": [{"id": "proj-1537…", "workspace_id": "ws-…", "actions": ["project:read", "project:mem_read", "project:mem_search", "project:doc_read", "project:doc_search", "project:search", "project:mem_add", "project:doc_add", "project:mem_conflict_read"]}, {"id": "proj-844f…", "workspace_id": "ws-…", "actions": ["project:read", "project:mem_read", "project:mem_search", "project:doc_read", "project:doc_search", "project:search"]}]}]'   # as sandbox owner
$ memorylake access set-level --member 7f8af5eb… --level write --reason 'AI-ACC-101: …' project proj-1537… --workspace ws-…   # as sandbox owner
$ memorylake access set-level --member 7f8af5eb… --level read --reason 'AI-ACC-101: …' project proj-844f… --workspace ws-…   # as sandbox owner
…
$ memorylake api-key create --name mlu-rbc-claims-assistant --member 7f8af5eb…   # as sandbox owner
  · claims-assistant: key mlu-rbc-claims-assistant (id 15978, sk-af67…) → its own profile .memorylake-demo/members/claims-assistant/
```

A `tenant_member` reaches every project by default, with every action its role allows (which includes adding memory). The
scope limits it. Two details the demo learned the hard way:

- **Each scope entry carries `actions`.** Without them, the entry grants every action of the role, and levels only add: a
  member at `read` could still write (4 of 27 cells in the first run, before this was fixed; see step 7).
- **Project entries need their `workspace_id`.** The help says the server fills it in; it answers
  `400 project instances need their workspace_id`.

### 5. 27 calls — every assistant × every project × read / search / write

```
$ memorylake fact list --projects proj-a913… --page-size 50 --workspace ws-…   # as claims-assistant
$ memorylake search 'payout approval limit' --projects proj-a913… --top-k 3 --workspace ws-…   # as claims-assistant
$ memorylake fact add --project proj-a913… 'Claim CL-2291: the customer sent photos of the ceiling on 2026-10-12; the estimate is unchanged.' --workspace ws-…   # as claims-assistant
…

  what the server allowed (R read · S search · W write; ✓ allowed, ✗ refused, ! differs from the policy)
                    Underwriting policy   Claims case notes     HR investigations
  claims-assistant  R✓ S✓ W✗              R✓ S✓ W✓              R✗ S✗ W✗
  underwriting-bot  R✓ S✓ W✓              R✗ S✗ W✗              R✗ S✗ W✗
  audit-reviewer    R✓ S✓ W✗              R✓ S✓ W✗              R✓ S✓ W✗
  · 27/27 calls answered as the policy says; refusals: HTTP 403 ACCESS_DENIED
  · e.g. claims-assistant → write Underwriting policy: “You don't have the "Add Memory" permission on project "Underwriting policy". Ask a team owner or admin to grant it.”
```

`search` needs both **Search Memories** and **Search Documents** on every project it names, even with `--types fact`; a
search that names one project the member cannot reach is refused as a whole. With a member's key, `proj list` returns
only the projects that member can reach.

### 6. Who can reach the HR file — and why

```
$ memorylake access show project proj-74de… --workspace ws-…   # as sandbox owner

  access show project proj-74de…  (HR investigations)
  can reach:
    audit-reviewer     read    role tenant_member  via permission_level, scope_grant
    you                manage  role tenant_owner   via permission_level
  cannot:
    claims-assistant   role tenant_member
    underwriting-bot   role tenant_member
  · assistants that reach the HR file: audit-reviewer — as the policy says
```

### 7. A grant changes — and the quick way grants more than the ticket asked

```
  · ticket AI-ACC-117: underwriting-bot needs read access to Claims case notes
  · before: HTTP 403 “You don't have the "Search Memories" permission on project "Claims case notes". …”
  · the quick way: add the project to its scope, set the level
$ memorylake access grant --member 27c61663… --reason 'AI-ACC-117: …' project proj-1537… --workspace ws-…   # as sandbox owner
$ memorylake access set-level --member 27c61663… --level read --reason 'AI-ACC-117: …' project proj-1537… --workspace ws-…   # as sandbox owner
  · after, same key, next call: rc 0 — #1 Claim CL-2291 (Marta Ilves, water damage, kitchen ceiling):…
  · and a write, which the ticket did not ask for: rc 0 — added fact-904d511a…
  · access show: underwriting-bot reaches Claims case notes at level “write”, from permission_level (read), scope_grant [every action the role allows]
  · levels only add: `access grant` put the project in its scope with every action the role allows, and a tenant_member may add memory. The level says read; the scope says write
  · the exact way: the scope entry carries the actions of the level
$ memorylake member scope set 27c61663… --reason 'AI-ACC-117: …' --scopes '[…"actions": ["project:read", …, "project:search"]…]'   # as sandbox owner
  · write now: HTTP 403 “You don't have the "Add Memory" permission on project "Claims case notes". …” · search: rc 0
  · access show: level “read”, from permission_level (read)
  · the note it was never meant to write is deleted (fact-904d511a…)
```

The change takes effect on the next call with the same key; nothing is restarted. `access levels` reports only the level set
directly (`current_level: read`); **`access show` reports the effective level**, so that is the one to check.

### 8. A key leaks: remove the member, the key is refused

```
  · incident SEC-2026-044: the claims assistant's key was pasted into a public ticket
  · claims-assistant reads the claim files: rc 0, 7 facts
$ memorylake member remove 7f8af5eb…   # as sandbox owner
  · the same call 0.6s later: HTTP 401 “API key was rejected by the server (authentication_error): service account is disabled”
  · what it wrote stays in the claim files: 1 fact(s) with written_by=claims-assistant
```

### 9. The audit log

```
$ memorylake audit-log list --page-size 100   # as sandbox owner

  time (UTC)           action                      by          key     target
  2026-10-13 06:20:15Z tenant:create               you         11511   Corvane Mutual - AI memory sandbox
  2026-10-13 06:20:26Z member:manage_virtual       you         15981   claims-assistant
  2026-10-13 06:20:28Z member:set_scope            you         15981   claims-assistant
  2026-10-13 06:20:29Z project:assign              you         15981   claims-assistant
  …
  2026-10-13 06:20:37Z service_account:create      you         15981   mlu-rbc-claims-assistant
  2026-10-13 06:21:20Z member:set_scope            you         15981   underwriting-bot
  2026-10-13 06:21:21Z project:assign              you         15981   underwriting-bot
  2026-10-13 06:21:27Z member:set_scope            you         15981   underwriting-bot
  2026-10-13 06:21:35Z member:remove               you         15981   claims-assistant
  · 20 event(s) since the sandbox was created: tenant:create ×1, member:manage_virtual ×3, member:set_scope ×5, project:assign ×7, service_account:create ×3, member:remove ×1
  · each names the key that made the change (actor.api_key_id) and its target; the --reason given to scope / level changes is not in the event (fields: action, actor, category, created_at, id, result, target)
  · data-plane events (fact add, search) in the log: 0 — it records who changed access, not who read memory
```

`access grant` is logged as `member:set_scope` and `access set-level` as `project:assign`. The team was created with your
personal key (11511 here); everything after it with the sandbox owner key.

### Cleanup

```
$ memorylake team delete --confirm 9fa94401…   # as sandbox owner
  · deleted the sandbox team 9fa94401… — its projects, members and keys go with it
$ memorylake team get   # as sandbox owner
  · its owner key now: HTTP 401 “service account is disabled”
```

Deleting the team frees the account's org-team slot at once; the next run can create a new one.

## Measured

Seven full runs on a free account (2026-10-13, CLI v20261013), each in a fresh sandbox team. Six after the scope fix:
two from the terminal, one with `--reset`, one from the web companion, and the two recordings (CLI and web):

| | every run after the scope fix |
|---|---|
| matrix | **27/27** as the policy says (6 of 6) |
| refusals | `403 ACCESS_DENIED`, the permission named (*Read Memory*, *Search Memories*, *Add Memory*) |
| quick grant (`access grant` + `set-level read`) | write allowed, `access show` effective level **write** (6 of 6) |
| exact grant (`member scope set` with the read actions) | write `403`, `access show` **read** (6 of 6) |
| removed member's key refused after | 0.5–0.8 s (`401 service account is disabled`) |
| audit events per run | 20 |
| whole run | 77–86 s |

The first run, before the scope entries carried actions, came out **23/27**: the four "write" cells of members at `read`
(claims-assistant on the rule book, audit-reviewer on all three) were allowed. That is the same behaviour step 7 now shows on
purpose.

## Wiring it into your own deployment

- **One member per agent, one key per member.** Virtual members have no login; their keys are their identity. Removing the
  member disables every key it has in the team.
- **Write the scope with actions, and check `access show`.** A level is a floor, not a ceiling: scope entries without actions,
  role defaults and access groups all add to it. `access show project <id>` is the effective answer.
- **Search needs read on every project it names.** Build each agent's search scope from the projects it can reach, or the whole
  call is refused.
- **Keep your own reason log.** The audit log records what changed, by which key, on what; not the `--reason`, and not memory
  reads or writes. Stamp the writer in fact metadata if you need it — the fact itself does not record it.
- **Org teams are limited per account** (one besides the personal team on a free account). Use one org team per environment,
  not one per agent.

## If something goes wrong

- **`tenant creation limit reached: limit=1 current=1`** at step 2: your account already owns an org team. Delete it with its
  owner key (`memorylake team delete --confirm <team-id>`) or use another account.
- **`project instances need their workspace_id`**: a scope entry for a project without `workspace_id`. The demo always sends it.
- **A member at `read` can write**: its scope entry has no `actions`. Run `access show project <id>` and look for `scope_grant`.
- **`.memorylake-demo/org/` was deleted but the sandbox team still exists**: the owner key is gone with it, and it is only
  printed once. Delete the team in the MemoryLake console.
- **`tls handshake eof` / `could not connect`**: a dropped connection. Calls whose refusal is the expected answer, and read-only
  commands, retry four times. Otherwise run again; it starts over.

## Files

```
demo.py                  the runner; prints every CLI command it runs and the key it ran under (and emits events for the web app)
data/policy.json         the company, the three projects and their facts, the three assistants, the access policy, the ticket and the incident
web/server.py            local web companion (stdlib HTTP server + Server-Sent Events over demo.py)
web/static/              the page — no build step
out/                     access-review.md / .json (the matrix, the change, the removal, the audit log), runs.jsonl (git-ignored)
```

The company, its assistants and everyone in it are fictional.
