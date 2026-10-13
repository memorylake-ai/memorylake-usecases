# Per-tenant memory for multi-tenant agent platforms

A runnable version of the MemoryLake use case
[*Build Multi-Tenant Agent Platforms on Memory Sharding That Actually Isolates*](https://www.memorylake.ai/en/usecase/memory-sharding-for-multi-tenant-agent-platforms),
built entirely on the [`memorylake` CLI](https://github.com/memorylake-ai/memorylake-cli).

**The problem.** *Tenant A's memory must never appear in Tenant B's context*, and when a tenant leaves, everything the agent
learned for them has to go. The page promises **per-tenant namespaces, default-deny across tenants, zero cross-tenant leakage
enforced at the retrieval boundary, and deletion per tenant.** This demo gives each tenant a **workspace** and tests each promise
with a count you can read off the output.

**What this demo shows.** Relaybench (fictional) hosts one support agent, **Relay**, for three companies: Harbor Dental, Kestrel
Freight and Nimbus Yoga. Each tenant gets its own workspace with its own project, two policy documents, two pinned rules and a
real support conversation. Sam Ortiz is a customer of two of them.

1. **The same question in every tenant, a 3 × 3 leak matrix.** *"What is the refund policy, who handles escalations, and how does
   this customer want to be contacted?"* is asked inside each workspace (facts and document paragraphs). Every hit is scanned for
   every tenant's marker words (`whitening`, `front desk` … / `pallet`, `dispatch desk` … / `class credit`, `studio manager` …;
   the lists are disjoint). Diagonal: each tenant finds its own answer. Off the diagonal: **0**.
2. **One person, two tenants, two memories.** Sam has **one actor id**, bound to both workspaces. Inside Harbor Dental he wants
   SMS reminders and has sensitive teeth; inside Kestrel Freight he wants phone calls on weekdays and ships from Reno dock 4.
   Shared fact ids between the two views: **0**. Facts mentioning the other tenant: **0**.
3. **Default-deny.** Code running for Harbor Dental names Kestrel Freight's project id, or a person who is not Harbor's customer:
   **refused, 4 of 4**, with the server's reason (`Project '…' does not belong to workspace '…'`, `Actor '…' is not bound to
   workspace '…'`). A request that mixes Harbor's project with Kestrel's is **refused whole**: it never returns the allowed half.
4. **Offboarding: `workspace delete` removes the memories, not everything.** It will not run on a guess: a wrong `--confirm`
   name is refused, and so is the team's default workspace. With the exact name, Nimbus Yoga's workspace is gone in about a
   second: `fact list` and `search` answer `404`, and Harbor Dental and Kestrel Freight keep **the same fact ids as before**. The
   help says *"its projects, documents and memories go with it"*, but **four things are still there**: the tenant's **project**
   and its documents (`proj list --workspace <deleted id>` lists it, and `proj doc download` still returns the policy file), its
   **boundary** (still listed, and its custom id stays taken across the team), the **Library folder** with its files, and the
   **actor** of its member. The last two are team-level by design. The demo lists all four, deletes each, and checks again.

**Watch it run** (real recordings, unedited): _links added after recording_

## Prerequisites

1. **A MemoryLake account and API key.** Sign up at [app.memorylake.ai](https://app.memorylake.ai),
   open **API Keys**, create a key and copy it. Details: [Authentication and API Keys](https://docs.memorylake.ai/authentication).
2. **The `memorylake` CLI, v20261013 or later** (`workspace delete` arrived in that release):

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
cd memorylake-usecases/tenant-memory

export MEMORYLAKE_API_KEY=sk-…
python3 demo.py
```

A run takes about 3–6 minutes; most of it is the three document imports and turning the three conversations into memory.

With `MEMORYLAKE_API_KEY` set, the demo logs into its own CLI profile under `./.memorylake-demo/`
(git-ignored) and leaves `~/.memorylake` alone. Without it, it uses your existing `memorylake auth login`.

**The demo creates three workspaces** (`Harbor Dental (Relaybench demo)` and so on, custom ids `mlu-tnt-harbor` …) and works
only inside them; it never reads or writes your existing workspaces. `cleanup` deletes them.

| | |
|---|---|
| `MEMORYLAKE_BASE_URL` | API endpoint; default global, China is `https://app.memorylake.cn/openapi/memorylake` |
| `python3 demo.py --reset` | Delete everything the demo created first, then run the story |
| `python3 demo.py matrix` | Ask the question again in every tenant that still has a workspace (reads only) |
| `python3 demo.py cleanup` | Delete the demo's workspaces (with everything in them), its Library folders and its actors |
| `--show-json` | Print the raw JSON the CLI returns |

**Re-running picks up where it left off.** Workspaces, projects and conversations are found by `custom_id`, the Library folders by
name, so a second run reuses Harbor Dental and Kestrel Freight as they are. Nimbus Yoga was offboarded at the end of the last run,
so it is provisioned again from scratch and offboarded again.

## Or run it in the browser

```bash
python3 web/server.py        # http://127.0.0.1:8768 — standard library only
```

Paste the key, press **Run demo**, and follow the seven steps: the three tenants, the leak matrix, Sam's two memories side by side,
the refused requests, and the offboarding checklist. The terminal drawer shows every `memorylake` command as it runs.

## What happens, step by step

### 1. Connect

`auth login --api-key` into the isolated profile, a check that the CLI has `workspace delete`, and `team get`.

### 2. Provision — one workspace per tenant

```bash
memorylake actor create --custom-id mlu-tnt-sam --display-name 'Sam Ortiz' --description '…' --tags demo
memorylake workspace create --name 'Harbor Dental (Relaybench demo)' --custom-id mlu-tnt-harbor --description '…'
memorylake actor bind --actor <relay> --workspace <harbor ws>        # and Sam
memorylake proj create --name 'Harbor Dental support KB' --custom-id mlu-tnt-harbor-kb --description '…' --workspace <harbor ws>
memorylake lib mkdir mlu-tnt-harbor --parent MY_SPACE --on-conflict deny
memorylake lib upload data/docs/harbor/contact-policy.md --parent <folder> --on-conflict deny     # and refund-policy.md
memorylake proj doc import --project <harbor kb> --wait <item> <item> --workspace <harbor ws>
memorylake boundary create --name 'Relay for Harbor Dental' --custom-id mlu-tnt-harbor-relay --projects <harbor kb> --workspace <harbor ws>
```

Actors are team-wide identities; **binding** one to a workspace lets it hold memory there, separately in each workspace. Relay is
bound to all three, Sam to Harbor and Kestrel, Priya Nair to Nimbus only. The boundary is the tenant's saved scope, the store
identifier its tools and the Memory Router connect with (see [mcp-scope-memory](../mcp-scope-memory/)).

### 3. Write memory

```bash
memorylake fact add --project <harbor kb> 'Harbor Dental refunds unused whitening kits in full within 14 days of purchase.' '…' --workspace <harbor ws>
memorylake conv create --custom-id mlu-tnt-harbor-sam-session --project <harbor kb> --actors <sam>,<relay> --kind DIRECT --name '…' --workspace <harbor ws>
memorylake conv msg append <conv> --actor <sam> --custom-id turn-01 --timestamp 2026-10-06T15:00:45Z --text '…' --workspace <harbor ws>
memorylake fact add --actor <sam> 'Sam Ortiz wants Harbor Dental appointment reminders by SMS only, never phone calls.' --workspace <harbor ws>
memorylake conv cook-status <conv> --workspace <harbor ws>
```

Nothing in these commands names a "tenant": the `--workspace` flag is the whole routing decision.

### 4. Isolation — the leak matrix

```bash
memorylake search '<the question>' --projects <harbor kb> --actors <relay>,<sam> --types fact --top-k 10 --workspace <harbor ws>
memorylake search '<the question>' --projects <harbor kb> --actors <relay>,<sam> --types document --top-k 5 --workspace <harbor ws>
```

```
  hits containing markers of →          Harbor Dental   Kestrel Freight       Nimbus Yoga
  asked inside Harbor Dental                   6 ✓               0 ✓               0 ✓
  asked inside Kestrel Freight                 0 ✓               9 ✓               0 ✓
  asked inside Nimbus Yoga                     0 ✓               0 ✓               6 ✓

  cross-tenant hits: 0   ·   every tenant found its own answer: yes
```

A search has to name its scope (`--projects` / `--actors`); one that names neither returns nothing, not "everything in the
workspace". The diagonal is the positive control: the scan does find markers when they are there.

### 5. One person, two tenants

```bash
memorylake fact list --actors <sam> --workspace <harbor ws>
memorylake fact list --actors <sam> --workspace <kestrel ws>
```

```
  inside Harbor Dental's workspace — 5 fact(s) on Sam Ortiz:
    - Sam Ortiz wants Harbor Dental appointment reminders by SMS only, never phone calls (as of 2026-10-06).
    - The person's teeth are sensitive after whitening. (as of 2026-10-06)
    …
  inside Kestrel Freight's workspace — 2 fact(s) on Sam Ortiz:
    - Sam Ortiz wants Kestrel Freight shipment updates by phone call rather than email, weekdays only (as of 2026-10-08).
    - Sam Ortiz is from Ortiz Supply. (as of 2026-10-08)

  shared fact ids between the two views: 0   ·   facts mentioning the other tenant: 0
```

The two preferences contradict each other (SMS only vs. phone calls), and neither tenant's agent ever sees the other one.

### 6. Default-deny

```
  · search Kestrel Freight's project: refused ✓  Project '…' does not belong to workspace '…' [INVALID_ARGUMENT]
  · search Harbor Dental + Kestrel Freight together: refused ✓  Project '…' does not belong to workspace '…' [INVALID_ARGUMENT]
  · list Kestrel Freight's facts: refused ✓  Project not found with id: … [NOT_FOUND]
  · search Priya Nair's memory: refused ✓  Actor '…' is not bound to workspace '…' [INVALID_ARGUMENT]
```

`fact list` answers `NOT_FOUND`, not "forbidden": from inside Harbor Dental, Kestrel's project does not exist.

### 7. Offboard a tenant

```bash
memorylake workspace delete <nimbus ws> --confirm 'Nimbus Yoga'                       # refused: not its exact name
memorylake workspace delete <nimbus ws> --confirm 'Nimbus Yoga (Relaybench demo)'
memorylake fact list --projects <nimbus kb> --workspace <nimbus ws>                   # 404
memorylake proj list --workspace <nimbus ws>                                          # the project is still listed
memorylake proj doc download --project <nimbus kb> <doc> --output - --workspace <nimbus ws>   # and its file still downloads
memorylake boundary list --workspace <nimbus ws>                                      # so is the boundary
memorylake boundary delete <bnd>
memorylake proj delete <nimbus kb> --workspace <nimbus ws>
memorylake lib delete <folder>
memorylake actor delete <priya>
```

```
  · refused ✓  refusing to delete `ws-…`: --confirm "Nimbus Yoga" does not match its name "Nimbus Yoga (Relaybench demo)"
  · deleted in 0.9s — the help says “its projects, documents and memories go with it”

  Memory:
  · the workspace is gone: ✓ Workspace not found with id: ws-… [NOT_FOUND]
  · Nimbus Yoga's facts are unreachable: ✓ Workspace not found with id: ws-… [NOT_FOUND]
  · Nimbus Yoga's search is unreachable: ✓ Workspace not found with id: ws-… [NOT_FOUND]
  · Harbor Dental untouched: ✓ 6 fact(s), same ids as before
  · Kestrel Freight untouched: ✓ 6 fact(s), same ids as before

  Still there — reachable through the deleted workspace's id:
  · still there: project “Nimbus Yoga support KB” with 2 document(s); `proj doc download` still returns refund-policy.md: “# Nimbus Yoga - membership refund policy”
  · still there: boundary “Relay for Nimbus Yoga” (bnd-…), and its custom id stays taken

  Still there — team-level, outside any workspace:
  · still there: Library folder mlu-tnt-nimbus/ — refund-policy.md, contact-policy.md
  · still there: actor Priya Nair → actor-…

  · no project left under the deleted workspace: ✓ 0 left
  · no boundary left under the deleted workspace: ✓ 0 left
  · Library folder removed: ✓
  · actor Priya Nair removed: ✓

  offboarding checks: 9/9 ✓   ·   left behind by `workspace delete` alone: 4
```

The demo writes the id of every workspace it deletes to `.memorylake-demo/offboarded.json` before deleting it, because the
leftovers are reachable only through that id; `cleanup` and the next run sweep them. The team's default workspace cannot be
deleted at all (`it is the team's default workspace … which other services look up`).

## Measured

_filled in after the recorded runs_

## What this demo does not show

- **Per-tenant API keys.** Every key a team creates acts as the team owner (`caller_role: tenant_owner`) and can open any
  workspace, so "auth scoped per tenant" is your platform's routing: it picks the `--workspace` for each request. Keep that choice
  in one place in your code.
- **A per-tenant audit trail.** `audit-log list` (new in v20261013) records management events (API keys, members, billing);
  creating or deleting a workspace, and reading or writing memory, did not appear in it.
- **Sharding.** Workspaces are the isolation unit you can see; how the service places them on storage is not visible through
  the API.

## Wiring it into your own platform

- **One workspace per tenant, created at signup** (`workspace create --custom-id <your tenant id>`), and every memory call carries
  `--workspace`. Find it again by `workspace get <tenant id> --by-custom-id`.
- **End users are actors; bind each to the tenants they belong to.** A person who is a customer of two tenants can be one actor
  with two separate memories, or two actors: either way nothing crosses.
- **Offboarding is five deletes, not one.** Save the workspace id first. Then: `boundary delete` and `proj delete --workspace <id>`
  for everything `boundary list` / `proj list --workspace <id>` return (before or after the workspace delete; both work),
  `workspace delete --confirm <name>`, the tenant's Library files, and actors that belonged only to that tenant. Check each with a
  read that should now 404 or come back empty.
- **Never pick "the first workspace" from `workspace list`.** It is sorted newest first. Look workspaces up by custom id; for the
  team's own default workspace, use custom id `_sys_default_workspace`.

## If something goes wrong

- **`this demo needs memorylake workspace delete`**: the installed CLI is older than v20261013; reinstall with the command above.
- **`DRIVE_ITEM_CONFLICT` on `lib mkdir`**: a folder `mlu-tnt-<tenant>` exists that the demo could not find. Run
  `python3 demo.py cleanup`.
- **`CUSTOM_ID_CONFLICT` on `boundary create`**: a boundary from an earlier, interrupted offboarding still holds the id. Run
  `python3 demo.py cleanup` (it sweeps the workspaces recorded in `.memorylake-demo/offboarded.json`).
- **Facts in Spanish (or another language)**: the extractor sometimes writes facts in another language. Each tenant's markers
  include its own name (Harbor, Kestrel, Nimbus), which survives translation, so the matrix still counts them.
- **A ✗ in the matrix off the diagonal**: a hit inside one tenant mentions another tenant's marker word. That would be a real
  leak; the demo prints it rather than hiding it. It has not happened in any run so far.
- **`tls handshake eof` / `could not connect`**: a dropped connection. Read-only commands retry four times; creates check whether
  the call went through before retrying. Otherwise run again; it picks up where it left off.

## Files

```
demo.py                  the runner; prints every CLI command it runs (and emits events for the web app)
data/tenants.json        the platform, the three tenants (rules, marker words, people, one conversation each), the question
data/docs/<tenant>/      two policy documents per tenant (Markdown, ASCII)
web/server.py            local web companion (stdlib HTTP server + Server-Sent Events over demo.py)
web/static/              the page — no build step
out/                     result.json of the last run (git-ignored)
```

The platform, its tenants and everyone in them are fictional.
