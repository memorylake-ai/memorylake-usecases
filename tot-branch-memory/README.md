# Branched memory for Tree-of-Thoughts agents

A runnable version of the MemoryLake use case
[*Give Tree-of-Thoughts Agents Branched Memory That Survives Every Exploration*](https://www.memorylake.ai/en/usecase/memory-for-tree-of-thoughts-agents),
built entirely on the [`memorylake` CLI](https://github.com/memorylake-ai/memorylake-cli).

**The problem.** A Tree-of-Thoughts agent explores several reasoning branches in parallel. When the run ends, so does
everything it found: the branch that worked can't be reused, the branches that failed get tried again next time, and the
reasoning that led to either is gone.

**What this demo shows.** Pathfinder, a planning bot at Larkspur Outfitters (fictional), has to get checkout p95 below 300 ms.
It opens three branches at once and keeps them in MemoryLake:

- **every branch is its own project** (branch-local memory) with its own conversation
- the step-by-step reasoning goes into **THINKING blocks**, which MemoryLake stores and replays verbatim but **does not turn into memory**
- the three explorers write to **one shared trunk log**, and the server keeps it linear: an append whose parent is no longer the
  head is refused with **409 "not the current head"**, and the writer retries on the new head
- **checkout** a branch = search the trunk and that branch; **merge** = promote the winner's result and each loser's *reason*
  to the trunk; **rollback** = `proj delete` the dead branch (its memory is gone: `fact list` → 404)
- **a week later** the next task reads the trunk first: it opens **1 branch instead of 3**, and each skipped branch is cited by
  the trunk fact that says why it was pruned

```
                       ┌─ br r1/cache  (project) ── THINKING … ── result: 455 ms, pruned ──┐
Priya's task ─▶ trunk ─┼─ br r1/pool   (project) ── THINKING … ── result: 240 ms, merged ──┼─▶ merge ─▶ trunk: 1 result + 2 lessons
                 log ◀─┴─ br r1/index  (project) ── THINKING … ── result: 520 ms, pruned ──┘     rollback ─▶ proj delete (404)
       (3 writers, 409 on a stale head)
a week later: search the trunk ─▶ skip cache, skip index (cited) ─▶ 1 branch ─▶ merge
```

The planner's choices are scripted in `demo.py` (no LLM call); what the demo shows is the memory around them. It runs in
about 3 minutes on a free personal account. The only credential you need is a MemoryLake API key.

**Watch it run** (real recordings, unedited): recording in progress.

## Prerequisites

1. **A MemoryLake account and API key.** Sign up at [app.memorylake.ai](https://app.memorylake.ai),
   open **API Keys**, create a key and copy it. Details: [Authentication and API Keys](https://docs.memorylake.ai/authentication).
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
cd memorylake-usecases/tot-branch-memory

export MEMORYLAKE_API_KEY=sk-…
python3 demo.py
```

With `MEMORYLAKE_API_KEY` set, the demo logs into its own CLI profile under `./.memorylake-demo/`
(git-ignored) and leaves `~/.memorylake` alone. Without it, it uses your existing `memorylake auth login`.

| | |
|---|---|
| `MEMORYLAKE_BASE_URL` | API endpoint; default global, China is `https://app.memorylake.cn/openapi/memorylake` |
| `MEMORYLAKE_WORKSPACE` | Workspace id; default is the one remembered by `ws use`, else your default workspace |
| `python3 demo.py --reset` | Delete everything the demo created first, then replay the whole story |
| `python3 demo.py tree` | The trunk, every branch and its state (open / merged / pruned / rolled back) |
| `python3 demo.py log` | The trunk log, in the order the server accepted it |
| `python3 demo.py thoughts pool` | Replay a branch's conversation: every THINKING and TEXT block, as stored |
| `python3 demo.py plan "The cart page p95 is 700 ms"` | Pathfinder's plan for any task, read from the trunk |
| `python3 demo.py cleanup` | Delete the conversations, the trunk and branch projects, Priya and Pathfinder |

It is safe to re-run. Everything is found again by `custom_id` (prefix `mlu-tot-`); trunk-log entries carry an `entry`
metadata key and are appended only if missing; a branch already merged into the trunk is not explored again, and its
rollback is re-checked from the project id the merge recorded. A re-run plans the second task from what the trunk held
before that task (its own merge is left out).

## Or run it in the browser

```bash
python3 web/server.py        # http://127.0.0.1:8765 — standard library only
```

![Web companion — a week later: the plan read from the trunk skips the pruned branches, citing the facts](https://github.com/memorylake-ai/memorylake-usecases/raw/main/tot-branch-memory/web/screenshot-next.png)

Paste the key, press **Run demo**, and watch the three branches fill in with their THINKING blocks and the 409s of the race
for the trunk log, the log itself with the refused stale append, the reasoning-vs-memory check, checkout, merge and rollback,
and the second task's plan. **Plan a task** runs the planning step for any task you type. The terminal drawer shows every
`memorylake` command as it runs.

## What happens, step by step

All output below is from real runs on a free account (ids shortened).

### 1–3. Connect, the trunk, the task

`proj create` makes the trunk project; `conv create` makes the **trunk log**, one DIRECT conversation between Priya and
Pathfinder in the trunk. Priya's task is a TEXT message, so MemoryLake extracts it into trunk memory:

```
$ memorylake conv msg append <trunk log> --actor <Priya> --custom-id r1-task --timestamp 2026-10-01T09:00:00Z
    --content-json '[{"block_type": "TEXT", "text": "Priya Nair (staff engineer, …): Checkout p95 latency is 610 ms. …"}]'
    --metadata entry=r1-task --metadata event=task --metadata run=r1 --workspace ws-…
```

### 4. Three branches at once, and a race for the trunk log

Three threads start together. Each one opens its branch in the trunk log, creates the branch **project** and its
**conversation**, appends two messages whose reasoning is in **THINKING** blocks, pins the measured result on the branch
(`fact add` + `fact update --metadata`), and reports back to the trunk log.

```
$ memorylake conv msg append <branch conv> --actor <Pathfinder> --custom-id pool-m1 --timestamp 2026-10-01T09:20:00Z
    --content-json '[{"block_type": "THINKING", "text": "Thought 1: under load the 20-connection pool queues requests; …"},
                     {"block_type": "TEXT", "text": "Pathfinder (…): Exploring branch pool: PgBouncer in transaction mode, …"}]'
```

The trunk-log appends carry no `--parent`: the CLI looks the conversation's latest message up and sends it as the parent.
When two explorers read the same head, the second one to arrive is refused:

```
  409 r1-pool-open: parent conv-entry-a126… is not the current head → head is now conv-entry-6999…, retrying on top of it
  409 r1-cache-open: parent conv-entry-a126… is not the current head → head is now conv-entry-6999…, retrying on top of it
  ✓ r1-pool-open landed on attempt 2
  409 r1-cache-open: parent conv-entry-6999… is not the current head → head is now conv-entry-0455…, retrying on top of it
  ✓ r1-cache-open landed on attempt 3
  …
  r1: 3 branch(es)
    r1/cache         p95  455 ms  pruned  project proj-2c3c… · result fact-af3b…
    r1/pool          p95  240 ms  merged  project proj-6a21… · result fact-6d36…
    r1/index         p95  520 ms  pruned  project proj-fda7… · result fact-bf19…

  trunk log: 6 append(s) landed, 6 refused with 409 and retried
```

The number of 409s depends on timing. Across 6 fresh runs it was 2, 4, 4, 6, 6 and 4.

### 5. One linear log

`conv msg list` returns the log in the order the server accepted it (`sequence_no`). The timestamps are what each writer
claimed. Then a writer with an old view of the log appends on top of message #1, and is refused:

```
    #1   2026-10-01T09:00  task   r1             TEXT          Priya Nair (staff engineer, Larkspur Outfitters): Checkou…
    #2   2026-10-01T09:05  open   r1/index       THINKING      Pathfinder opens branch r1/index: Composite index on orde…
    #3   2026-10-01T09:05  open   r1/pool        THINKING      Pathfinder opens branch r1/pool: PgBouncer in transaction…
    #4   2026-10-01T09:05  open   r1/cache       THINKING      Pathfinder opens branch r1/cache: Cache price lookups in …
    #5   2026-10-01T11:45  result r1/cache       THINKING      Branch r1/cache: p95 455 ms (target 300 ms) → pruned.
    #6   2026-10-01T11:45  result r1/pool        THINKING      Branch r1/pool: p95 240 ms (target 300 ms) → merged.
    #7   2026-10-01T11:45  result r1/index       THINKING      Branch r1/index: p95 520 ms (target 300 ms) → pruned.

  ✓ sequence_no 1..7 with no gaps   ✓ every entry exactly once (7 keys)
  head (`conv get` → current_message_id): conv-entry-93f6…
  ✓ refused (rc=1): `parent_entry_id` 'conv-entry-a126…' is not the current head of conversation '…'. Appends must extend
    the current head; use the value returned in the conversation's `current_entry_id` as the parent.
```

A conversation is a log, not a tree: `--parent` is a compare-and-swap on the head, not a way to fork. That is why each branch
here is its own project and conversation. (The error names an internal conversation id, not the `conv-` id you passed, and
`conv get` calls the field `current_message_id`.)

### 6. Reasoning is stored, not remembered

Each branch's reasoning carries details that appear nowhere else: node names, hit ratios, benchmark numbers. The demo
replays every THINKING block (`conv msg list`, compared by sha256 with what was sent) and checks each detail against every
scope's `fact list`: the trunk, every branch, Priya and Pathfinder.

```
  6/6 THINKING blocks replayed verbatim · 12/12 reasoning-only details kept out of memory (scopes: trunk, every branch, Priya, Pathfinder)

  r1/cache  (conv-47d3…)
    THINKING 1  ✓ replayed verbatim (sha256 a0cf8749fb7a)
               “Thought 1: price lookups are 38 percent of checkout time. Put them behind the Valkey 7.2 cache …”
    THINKING 2  ✓ replayed verbatim (sha256 cae7576fe660)
               “Thought 2: with a 5-second TTL the hit ratio is only 0.17 and p95 barely moves (590 ms). With a…”
    reasoning-only details, checked against every scope's `fact list`:
      ✓ Valkey   ✓ rk-edge-3   ✓ 0.62   ✓ 0.17
    what the branch remembers (1 fact(s)):
      · Branch result (r1/cache, 2026-10-01, checkout): Cache price lookups in Redis — p95 455 ms against a 300…
```

It is the block type that keeps reasoning out of memory, not the bot: when the same reasoning was sent as a **TEXT** block by
the same actor (a control, 3 conversations), the vendor price was extracted 3/3 and the "dead end" verdict 2/3. As THINKING
it was extracted **0 times in 4 probe conversations and 0 of 70 details across 6 fresh demo runs**. If a detail ever leaks,
the line turns ✗ and names the fact.

### 7. Checkout: trunk alone vs trunk + one branch

```
  `search "what did each branch measure for checkout p95 latency" --projects <trunk>` → 3 hit(s), 0 branch result(s)
  `--projects <trunk>,<r1/cache>` → 4 hit(s); 1 from the branch (trunk alone: 0)  ✓
  `--projects <trunk>,<r1/pool>` → 5 hit(s); 2 from the branch (trunk alone: 0)  ✓
  `--projects <trunk>,<r1/index>` → 4 hit(s); 1 from the branch (trunk alone: 0)  ✓
```

### 8. Merge and rollback

Merging is a promotion: one trunk fact per branch, with metadata pointing back at the branch project and its result fact.
For a pruned branch, what is merged is the **reason**, so the next run does not try it again. Rolling back is `proj delete`
(the conversation first: `proj delete` does not delete conversations).

```
  Merged into the trunk:
    + pruned fact-d9c7…  Pruned branch r1/cache (2026-10-01, checkout): Cache price lookups in Redis — do not retry:…
    + merged fact-021d…  Merged from branch r1/pool (2026-10-01, checkout): PgBouncer in transaction mode, connectio…
    + pruned fact-2e20…  Pruned branch r1/index (2026-10-01, checkout): Composite index on order_items (cart_id, sku…

  Rolled back:
    r1/cache   proj delete proj-21ca…  →  fact list --projects proj-21ca…: ✓ 404 (its memory is gone)
    r1/index   proj delete proj-193d…  →  fact list --projects proj-193d…: ✓ 404 (its memory is gone)
```

The winning branch is kept, so `python3 demo.py thoughts pool` can still replay how it got there.

### 9. A week later, the next task reads the trunk first

```
  r2 · 2026-10-08 · search
  “Product search p95 latency is 540 ms. Target below 300 ms before the November 20 freeze. …”

  Pathfinder's plan, read from the trunk before opening anything:
  trunk search: 6 hit(s), 3 merge/prune lesson(s), 3 other hit(s) left out (task notes, not lessons)
    try 1st  pool (PgBouncer in transaction mode, connection pool raised…
             won r1/pool at 240 ms (fact-021d…)
    skip     cache (Cache price lookups in Redis)
             pruned in r1/cache (fact-d9c7…): it only reaches 455 ms, and only with a 60-second cache TTL, which breaks the rule that p…
    skip     index (Composite index on order_items (cart_id, sku))
             pruned in r1/index (fact-2e20…): it saves only 90 ms (520 ms) and building the index locks order_items for 14 minutes
  …
  r1 opened 3 branch(es); r2 opened 1 and skipped 2, each skip citing the trunk fact that says why (fact-d9c7…, fact-2e20…)
```

`search` always returns its top-k, so the plan keeps only the hits that are merge or prune lessons (read from the fact's
`metadata.type`) and prints how many others it left out.

### Measured

6 fresh runs (`--reset` or a new prefix) on a free account: THINKING replayed verbatim 8/8 each; reasoning-only details kept
out of memory 14/14 each (70/70 in total, 0 leaks); the trunk log contiguous with every entry once 6/6; the stale append
refused 6/6; rollback 404 12/12; the second task opened 1 branch and skipped 2 in 6/6. Each run took 2.5–3 minutes.

## Wiring it into your own agent

- **One project per branch, one trunk project.** Branch-local memory is what the branch learned; the trunk is what you keep.
  `search --projects <trunk>,<branch>` is a checkout; `proj delete <branch>` is a rollback.
- **Put chain-of-thought in THINKING blocks.** It is kept verbatim for replay and audit (`conv msg list`), and it does not
  become memory. Put conclusions in TEXT, or pin them with `fact add`.
- **Merge by promotion.** `fact add` the result to the trunk and `fact update --metadata` with where it came from. Merge the
  reasons for pruning too; they are what saves the next run.
- **Shared logs need a retry loop.** Many writers on one conversation: append, and on 409 read `conv get` →
  `current_message_id` and append again with `--parent` set to it. Make every entry idempotent (`--custom-id`).

## If something goes wrong

- **409 "is not the current head" keeps coming back**: another writer is appending to the same conversation. Retry with
  `--parent` set to `conv get`'s `current_message_id`; the demo gives up after 12 tries.
- **`appending after the conversation's latest message needs a workspace`**: without `--parent`, the CLI looks the head up,
  and that needs `--workspace` (or `MEMORYLAKE_WORKSPACE`, or `ws use`).
- **`search` fails with 400 "does not belong to workspace"**: one of the `--projects` ids was deleted (a rolled-back branch).
  The whole query fails; build the scope from `proj list` after a rollback.
- **A reasoning-only detail shows ✗**: extraction read it from somewhere other than a THINKING block (check the TEXT turns).
  The line names the fact and its scope.
- **`tls handshake eof` / `could not connect`** while polling: a dropped connection. Read-only commands retry four times;
  otherwise re-run `python3 demo.py` and it resumes.

## Files

```
demo.py                  the runner; prints every CLI command it runs (and emits events for the web app)
data/tot.json            the team, Priya, Pathfinder, the approaches, both runs with their branches, thoughts and results
web/server.py            local web companion (stdlib HTTP server + Server-Sent Events over demo.py)
web/static/              the page — no build step
out/                     runs.jsonl, one summary line per run (git-ignored)
```

All names, the company and the services are fictional.
