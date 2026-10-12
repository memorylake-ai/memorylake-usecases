# Memory scopes for MCP tools

A runnable version of the MemoryLake use case
[*Add Persistent Memory to Any MCP-Compatible AI Tool*](https://www.memorylake.ai/en/usecase/persistent-memory-via-mcp-for-ai-tools),
built entirely on the [`memorylake` CLI](https://github.com/memorylake-ai/memorylake-cli).

**The problem.** An MCP tool connects to MemoryLake with an API key and a *memory store identifier*.
That identifier decides what the tool remembers and what it can never see. Give every tool on a team one,
and the identifiers outlive the things they point at: projects end, people move teams, bots get replaced.

**What this demo shows.** In MemoryLake that identifier is a **boundary**: a saved scope of projects, one
person and one agent. MCP clients pick one on the OAuth consent screen; Memory Router takes it as
`?boundary_id=` ([concepts](https://docs.memorylake.ai/concepts)). Fernhill Robotics (fictional) gives three
tools one boundary each: Priya's editor assistant, Tom's editor assistant and the CI review bot. Then:

- **one question, three tools, three answers.** *What should I check before changing how payouts are
  retried?* goes through each boundary. Priya's tool answers from payments and the runbooks plus her own
  preferences; Tom's from the web app and the runbooks plus his; the bot from three projects plus its
  review lessons. Every hit is labelled with the scope it came from: **0 from outside a tool's boundary**;
- **a boundary is checked when it is written.** One for Kai, a contractor not yet on the workspace, is refused;
- **the team changes, the boundaries do not.** The ledger-migration project is deleted, Tom's actor is
  sealed (`actor unbind`), the review bot is replaced. `boundary get` still lists all three. The bot's
  scope now makes every search fail (`HTTP 400 … does not belong to workspace`), and Tom's and the bot's
  boundaries refuse any edit, even a rename;
- **audit and repair.** Every reference is checked against what exists today (3 stale), each boundary is
  repaired with one `boundary update` (same id, so the tools keep their identifier): Ana takes Tom's
  seat, review-bot-v2 takes the bot's. The same question works again, and Tom's preferences appear in no answer.

```
             boundary (memory store identifier)                       search through it
Priya's IDE ─ priya-ide : payments-service, platform-runbooks + Priya  → payments · runbooks · Priya
Tom's IDE   ─ tom-ide   : web-app, platform-runbooks + Tom             → web-app · runbooks · Tom
CI bot      ─ review-bot: payments, web-app, ledger-migration + bot    → three projects · review-bot
                                    │
 proj delete ledger-migration · actor unbind Tom · agent delete review-bot
 boundary get → still lists all three · bot's search → HTTP 400 · rename Tom's / bot's → refused
                                    │
 audit: 3 stale references → boundary update (Ana, review-bot-v2, live projects) → 0 stale, all three answer
```

Runs in about 2 minutes on a free personal account. The only credential you need is a MemoryLake API key.
Nothing here depends on extraction: every memory is pinned with `fact add`, so the output is the same every run.

**Watch it run** (real recordings, unedited):
[CLI demo, 3:41](https://github.com/memorylake-ai/memorylake-usecases/releases/download/mcp-scope-memory-v1/mcp-scope-memory-cli-demo.mp4) · [Web companion demo, 5:14](https://github.com/memorylake-ai/memorylake-usecases/releases/download/mcp-scope-memory-v1/mcp-scope-memory-web-demo.mp4).

## Prerequisites

1. **A MemoryLake account and API key.** Sign up at [app.memorylake.ai](https://app.memorylake.ai),
   open **API Keys**, create a key and copy it. Details: [Authentication and API Keys](https://docs.memorylake.ai/authentication).
2. **The `memorylake` CLI:**

   ```bash
   curl -fsSL https://raw.githubusercontent.com/memorylake-ai/memorylake-cli/main/scripts/install.sh | sh
   ```

   ```powershell
   irm https://raw.githubusercontent.com/memorylake-ai/memorylake-cli/main/scripts/install.ps1 | iex
   ```
3. **Python 3.9+** (standard library only).

## Run it

```bash
git clone https://github.com/memorylake-ai/memorylake-usecases.git
cd memorylake-usecases/mcp-scope-memory

export MEMORYLAKE_API_KEY=sk-…
python3 demo.py
```

With `MEMORYLAKE_API_KEY` set, the demo logs into its own CLI profile under `./.memorylake-demo/`
(git-ignored) and leaves `~/.memorylake` alone. Without it, it uses your existing `memorylake auth login`.

| | |
|---|---|
| `MEMORYLAKE_BASE_URL` | API endpoint; default global, China is `https://app.memorylake.cn/openapi/memorylake` |
| `MEMORYLAKE_WORKSPACE` | Workspace id; default is the one remembered by `ws use`, else your default workspace |
| `python3 demo.py scopes` | Audit every demo boundary against what exists today |
| `python3 demo.py --reset` | Delete everything the demo created, then start over (a plain re-run does the same) |
| `python3 demo.py cleanup` | Delete the boundaries, both agents' memories and the agents, the projects, the people's facts and the people |

The story changes its boundaries for good (step 5 breaks them, step 6 repairs them), so a re-run does not
resume half-way: it finds what the last run left (everything is prefixed `mlu-msc-`), says so, cleans it
up and starts over.

## Or run it in the browser

```bash
python3 web/server.py        # http://127.0.0.1:8765 — standard library only
```

![Web companion — one question, three tools, every hit labelled by scope](https://github.com/memorylake-ai/memorylake-usecases/raw/main/mcp-scope-memory/web/screenshot-answers.png)

![Web companion — the audit after the team changes: 3 stale references](https://github.com/memorylake-ai/memorylake-usecases/raw/main/mcp-scope-memory/web/screenshot-audit.png)

[Watch the web companion demo (mp4, 5:14)](https://github.com/memorylake-ai/memorylake-usecases/releases/download/mcp-scope-memory-v1/mcp-scope-memory-web-demo.mp4).

Paste the key and press **Run demo**. The page shows the three boundaries, the three answers side by side
with every hit labelled by scope, the boundaries after the team changes and what each tool gets back, the
audit with every reference marked ✓/✗, the repair, and the answers again. **Ask** runs any search through
any of the three boundaries; **Run audit** checks them again. The terminal drawer shows every `memorylake`
command as it runs.

## What happens, step by step

All output below is from one real `--reset` run. Ids are shortened; commands that repeat are left out.

### 1. Connect

```
$ memorylake auth login --api-key sk-… --base-url https://app.memorylake.ai/openapi/memorylake --profile memorylake-usecases
$ memorylake team get
  · connected to team “niuniu@…” as tenant_owner
  · using workspace “Default Workspace” (ws-83f0…)
```

### 2. Four projects, four people, one review bot

Four projects (`payments-service`, `web-app`, `platform-runbooks`, `ledger-migration`), four people
(Priya, Tom and Ana bound to the workspace; Kai, a contractor, not yet), and the `review-bot` agent.
Every memory is pinned with `fact add`: project notes on the projects, preferences on each person
(`--actor`), review lessons on the bot (`--agent`). 18 facts in all; the texts are in `data/story.json`.

```
$ memorylake proj create --name payments-service --custom-id mlu-msc-payments --description '…' --workspace ws-83f0…
$ memorylake actor create --custom-id mlu-msc-priya --display-name 'Priya Raman' --description 'Backend engineer, payments' --tags demo,platform
$ memorylake actor bind --actor actor-b1c8… --workspace ws-83f0…
$ memorylake agent create --name review-bot --custom-id mlu-msc-bot --description '…'
$ memorylake fact add --project proj-696b… 'payments-service: every payout call carries an idempotency key built from the payout id, so a retried call can never pay twice.' --workspace ws-83f0…
$ memorylake fact add --actor actor-b1c8… 'Priya prefers retry changes to land behind a feature flag with a dashboard first.' --workspace ws-83f0…
$ memorylake fact add --agent agent-4bae… 'review-bot lesson: flag any retry loop without an upper bound on attempts.' --workspace ws-83f0…
  · pinned 18 facts with `fact add`: project notes on the projects, preferences on each person, lessons on the bot
```

### 3. One boundary per tool

```
$ memorylake boundary create --name "Priya's editor assistant" --custom-id mlu-msc-priya-ide --projects proj-696b…,proj-d0a3… --human-actor actor-b1c8… --workspace ws-83f0…
$ memorylake boundary create --name "Tom's editor assistant" --custom-id mlu-msc-tom-ide --projects proj-af04…,proj-d0a3… --human-actor actor-fd15… --workspace ws-83f0…
$ memorylake boundary create --name 'CI review bot' --custom-id mlu-msc-review-bot --projects proj-696b…,proj-af04…,proj-0fc5… --agent agent-4bae… --workspace ws-83f0…

  One memory store identifier per tool:
   bnd-ebfa…  Priya's editor assistant  ← Priya's IDE (MCP client)
     projects: payments-service, platform-runbooks · person: Priya Raman · agent: —
   bnd-e81b…  Tom's editor assistant  ← Tom's IDE (MCP client)
     projects: web-app, platform-runbooks · person: Tom Okafor · agent: —
   bnd-ab2f…  CI review bot  ← CI pipeline (Memory Router, ?boundary_id=)
     projects: payments-service, web-app, ledger-migration · person: — · agent: review-bot
     assistant_actor_id actor-896d… (added by the server: the agent's own actor)

  A boundary is checked when it is written. Kai Brandt is not on the workspace yet:
$ memorylake boundary create --name "Kai's editor assistant" --custom-id mlu-msc-kai-ide --projects proj-af04… --human-actor actor-84db… --workspace ws-83f0…
   ✓ refused: HTTP 400 INVALID_ARGUMENT — human_actor_id: Actor 'actor-84db…' is not bound to workspace 'ws-83f0…'.
```

A project id that does not exist is refused the same way (`Some project_ids do not exist in this workspace`).
For an agent the server fills in `assistant_actor_id`, the agent's own actor, which is where the agent's memory is searched.

### 4. One question, three tools

Each tool's boundary is read with `boundary get` and expanded into a search: its projects (`--projects`),
its person and its agent's actor (`--actors`). Search hits carry no owner, so the demo maps each hit back
with `fact list` on every scope it wrote to, and marks it ✓ if that scope is inside the boundary.

> On a free account the demo does this expansion itself. Memory Router (`?boundary_id=`) and MCP clients
> (consent screen) hand the boundary id to the server instead; Memory Router needs a paid quota and MCP
> needs an interactive OAuth sign-in, so neither runs here. The expansion uses only the boundary's own fields.

```
$ memorylake boundary get bnd-ebfa…
$ memorylake search 'What should I check before changing how payouts are retried?' --projects proj-696b…,proj-d0a3… --actors actor-b1c8… --types fact --top-k 8 --workspace ws-83f0…

  through “Priya's editor assistant”:
   ✓ [payments-service] payments-service: every payout call carries an idempotency key built from the payout id, so a retried call can never pay twice.
   ✓ [payments-service] payments-service: retries on the bank API stop after 3 attempts with jittered backoff starting at 2 seconds; after that the payout is parked for a human.
   ✓ [platform-runbooks] platform-runbooks: before changing retry behaviour anywhere, check the incident log for the bank API's current rate limit.
   ✓ [platform-runbooks] platform-runbooks: a payout stuck in parked state for more than an hour pages the payments on-call.
   ✓ [payments-service] payments-service: changes to the retry logic need a second reviewer from the payments on-call rota.
   ✓ [Priya Raman] Priya prefers retry changes to land behind a feature flag with a dashboard first.
   ✓ [Priya Raman] Priya reviews in the morning and wants small, single-purpose pull requests.
   7 hit(s) · 0 from outside this boundary

  through “Tom's editor assistant”:
   ✓ [platform-runbooks] platform-runbooks: before changing retry behaviour anywhere, check the incident log for the bank API's current rate limit.
   ✓ [web-app] web-app: the dashboard never retries a payout itself; it polls the payout status endpoint every 5 seconds and shows a pending badge.
   ✓ [web-app] web-app: network errors in the dashboard are retried by the fetch wrapper in src/lib/http.ts, never in components.
   ✓ [Tom Okafor] Tom prefers Playwright tests over unit tests for anything that touches the payout screen.
   …
   7 hit(s) · 0 from outside this boundary

  through “CI review bot”:
   ✓ [payments-service] payments-service: every payout call carries an idempotency key built from the payout id, so a retried call can never pay twice.
   ✓ [review-bot] review-bot lesson: a retry that re-sends a payout without the idempotency key is a blocking comment.
   ✓ [review-bot] review-bot lesson: flag any retry loop without an upper bound on attempts.
   ✓ [ledger-migration] ledger-migration: during the migration, payout retries were disabled behind the flag ledger_dual_write.
   ✓ [web-app] web-app: the dashboard never retries a payout itself; it polls the payout status endpoint every 5 seconds and shows a pending badge.
   …
   8 hit(s) · 0 from outside this boundary

  What each tool saw:
   Priya's editor assistant     Priya Raman, payments-service, platform-runbooks
   Tom's editor assistant       Tom Okafor, platform-runbooks, web-app
   CI review bot                ledger-migration, payments-service, review-bot, web-app

  3 of 3 tools answered · 0 hit(s) from outside a tool's boundary
```

The ✓/✗ check is not a formality: widening Priya's search by one project (`web-app`) while judging it
against her boundary marks the web-app hit ✗ (tested while building this demo).

### 5. The team changes

```
  · The ledger migration is finished: its project is deleted.
$ memorylake proj delete proj-0fc5… --workspace ws-83f0…
  · Tom moves to another team: his actor is sealed (unbound from the workspace).
$ memorylake actor unbind --actor actor-fd15… --workspace ws-83f0…
  · review-bot is replaced by review-bot-v2: its lessons are copied over, then it is deleted.
$ memorylake agent create --name review-bot-v2 --custom-id mlu-msc-bot2 --description '…'
$ memorylake fact list --agents agent-4bae… --page-size 50 --workspace ws-83f0…
$ memorylake fact add --agent agent-156c… 'review-bot lesson: a retry that re-sends a payout without the idempotency key is a blocking comment.' --workspace ws-83f0…
$ memorylake agent delete agent-4bae…
  · copied 2 lesson(s) to review-bot-v2, deleted 2 from review-bot, then review-bot

  What the boundaries say now (`boundary get`):
   Priya's editor assistant: projects: payments-service, platform-runbooks · person: Priya Raman · agent: —
   Tom's editor assistant: projects: web-app, platform-runbooks · person: Tom Okafor · agent: —
   CI review bot: projects: payments-service, web-app, ledger-migration · person: — · agent: review-bot
  Nothing was cascaded: the deleted project, the sealed person and the deleted agent are all still referenced.

  What that does to the tools:
   CI review bot, searching through its boundary → ✗ HTTP 400 INVALID_ARGUMENT — Project 'proj-0fc5…' does not belong to workspace 'ws-83f0…'
   rename Tom's boundary → ✗ HTTP 400 INVALID_ARGUMENT — human_actor_id: Actor 'actor-fd15…' is not bound to workspace 'ws-83f0…'.
   rename the CI bot's boundary → ✗ HTTP 400 ACTOR_INACTIVE — agent_id: Agent 'agent-4bae…' is inactive because it has been deleted. Its existing memories stay readable, but it can no longer take part in writes.
```

Two different rules are at work. **Projects** are only checked when you pass `--projects`: a deleted one
stays in the list without a warning, a rename still goes through, and any search that uses the list is
rejected as a whole (one unknown project fails the query, it is not skipped). **The person and the agent**
are checked on every update: once either is gone, nothing about the boundary can be changed until that
reference is cleared (`--clear-human-actor`, `--clear-agent`) or replaced.

A new agent starts with an empty memory, so the bot's lessons are copied with `fact add` before it is
deleted. They are deleted from the old agent first: a deleted agent's facts stay readable and can no longer be deleted.

### 6. Audit and repair

Every reference is checked against what exists today: projects with `proj get`, the person with
`fact list --actors` (a sealed actor answers 404), the agent with `agent get`.

```
   ✗ CI review bot (bnd-ab2f…)
      ✓ project payments-service
      ✓ project web-app
      ✗ project ledger-migration  — project deleted
      ✗ agent   review-bot  — agent deleted

   ✗ Tom's editor assistant (bnd-e81b…)
      ✓ project web-app
      ✓ project platform-runbooks
      ✗ person  Tom Okafor  — not bound to the workspace

   ✓ Priya's editor assistant (bnd-ebfa…)
      ✓ project payments-service
      ✓ project platform-runbooks
      ✓ person  Priya Raman

   3 boundaries · 3 stale reference(s)
$ memorylake boundary update bnd-ab2f… --projects proj-696b…,proj-af04… --agent agent-156c…
$ memorylake boundary update bnd-e81b… --human-actor actor-0974… --name "Ana's editor assistant"

  Repaired (one `boundary update` per boundary, same id — the tools keep their identifier):
   CI review bot → CI review bot
     projects: payments-service, web-app · person: — · agent: review-bot-v2
   Tom's editor assistant → Ana's editor assistant
     projects: web-app, platform-runbooks · person: Ana Sousa · agent: —
```

`--projects` replaces the list, so it names every project to keep.

### 7. After the repair

```
  through “Ana's editor assistant”:
   ✓ [platform-runbooks] platform-runbooks: before changing retry behaviour anywhere, check the incident log for the bank API's current rate limit.
   ✓ [web-app] web-app: network errors in the dashboard are retried by the fetch wrapper in src/lib/http.ts, never in components.
   ✓ [Ana Sousa] Ana prefers component tests with mocked fetch over end-to-end tests for the payout screen.
   ✓ [Ana Sousa] Ana wants accessibility checks run on every dashboard change.
   …
   7 hit(s) · 0 from outside this boundary

  through “CI review bot”:
   ✓ [review-bot-v2] review-bot lesson: a retry that re-sends a payout without the idempotency key is a blocking comment.
   ✓ [review-bot-v2] review-bot lesson: flag any retry loop without an upper bound on attempts.
   …
   8 hit(s) · 0 from outside this boundary

  What each tool saw:
   Priya's editor assistant     Priya Raman, payments-service, platform-runbooks
   Ana's editor assistant       Ana Sousa, platform-runbooks, web-app
   CI review bot                payments-service, review-bot-v2, web-app

  3 of 3 tools answered · 0 hit(s) from outside a tool's boundary

  Tom Okafor's preferences in any answer: 0 — his seat went to Ana Sousa, his memory did not

  Audit again:
   3 boundaries · 0 stale reference(s)
  · the boundary ids are in out/boundaries.json — Memory Router takes one as `?boundary_id=`; an MCP client picks one on the consent screen
```

## Wiring it into your own team

- One boundary per tool and per person, not one shared by everybody. The boundary is the tool's whole
  view of memory: what is outside it does not exist for that tool.
- Put the person in the boundary (`--human-actor`) only when the tool works for that person; a CI bot gets
  projects and its own agent (`--agent`), no person.
- Make the audit part of offboarding and of closing a project: after `proj delete`, `actor unbind` or
  `agent delete`, run it (`python3 demo.py scopes` shows the checks) and repair before the tools notice.
  Clear or replace the person or agent *first*: until then the boundary accepts no update at all.
- Repair in place with `boundary update`. The id stays the same, so no tool has to be reconnected.
- Before you delete an agent or an actor, delete its facts: afterwards they stay readable and can no
  longer be deleted.

## If something goes wrong

- **`tls handshake eof` / `could not connect`**: a dropped connection. Read-only commands retry four
  times; otherwise re-run `python3 demo.py` (it cleans up and starts over).
- **`401 service account not found`**: the key was deleted or belongs to the other deployment.
- **`HTTP 400 … does not belong to workspace` on a search**: one of the boundary's projects was deleted.
  `python3 demo.py scopes` names it.
- **`not bound to workspace` / `ACTOR_INACTIVE` on `boundary update`**: the boundary's person was unbound
  or its agent deleted. Pass `--clear-human-actor` / `--clear-agent`, or a replacement, in the same update.
- **`cleanup` says facts are still on an actor or agent**: it stops before deleting that actor or agent,
  because its facts would become undeletable. Run `python3 demo.py cleanup` again.

## Files

```
demo.py            the runner; prints every CLI command it runs (and emits events for the web app)
data/story.json    the projects, people, agents and every pinned fact; the three boundaries, the one that
                   is refused, the question, the team changes and how each boundary is repaired
web/server.py      local web companion (stdlib HTTP server + Server-Sent Events over demo.py)
web/static/        the page — no build step
out/               boundaries.json (git-ignored)
```

All names and companies are fictional.
