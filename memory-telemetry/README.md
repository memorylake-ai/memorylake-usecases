# Memory telemetry for agent operations

A runnable version of the MemoryLake use case
[*Run Agent Ops on Memory Telemetry That Matches Your Compute Stack*](https://www.memorylake.ai/en/usecase/memory-telemetry-for-agent-operations),
built entirely on the [`memorylake` CLI](https://github.com/memorylake-ai/memorylake-cli).

**The problem.** *The compute graph shows healthy GPU usage. The network graph shows normal traffic. Agent quality is degrading
and nobody knows why.* The page asks for memory metrics that sit next to the rest of the stack: **hit rate, latency
distributions, growth and eviction, per-retrieval cost, alerting**. This demo produces each of them from a real workload, and
uses them to find out why an agent got worse.

**What this demo shows.** Bramblecrest Outfitters (fictional) runs four jobs against **one shared support memory** (a project):

| job | what it does to memory | commands |
|---|---|---|
| intake-agent | imports three support runbooks | `lib upload`, `proj doc import --wait`, `proj stats` |
| scribe-agent | pins six notes from yesterday's tickets, each with a kind, a date and a **time-to-live** | `fact add` (six at once), `fact update --metadata` |
| answer-agent | answers six customer questions, twice | `search --projects … --top-k 5` |
| retention-job | evicts every note whose `noted + ttl_days` has passed | `fact list` (every page), `fact delete` |

The scribe files everything that came in through a ticket as `kind=ticket, ttl 7 days`, including three notes that are really
a standing carrier agreement, a partner agreement and a notice about next week. The retention job does exactly what the
metadata says. On its second pass answer-agent gets **3 of 6** questions wrong, and nothing in its own output says why.

1. **Every memory operation is measured as it runs.** Each job runs in its own time window. Latency per operation (p50 / p95 /
   max, at the client), the retrieval **hit ratio** (is the expected answer in the top 5?), **facts held**, **documents**, and
   **evictions**.
2. **Three alert rules.** `MemoryHitRatioLow` (< 0.8) and `MemoryFactsDropped` (> 30 % in one window) **fire**;
   `SearchLatencyHigh` (p95 > 6 s) stays ok. Search p95 was 3–4.5 s in every run, so latency is not the cause.
3. **Each new miss is traced to the eviction that caused it.** All three questions that went from ✓ to ✗ were answered by a
   fact the retention job deleted (`kind=ticket, ttl 7d`). The other two evictions, real one-off tickets, cost no answer.
4. **MemoryLake's own usage records, per job.** `memorylake usage records --start-time … --end-time …` lists one record per
   billed platform operation (`files_process`, `memory_input`, `retrieval_call`, …) with its tokens and the member who made
   it. Read for each job's window, it gives the cost of each job from MemoryLake's side, not from the demo's guess.
   **Usage records arrive in batches, an hour or more after the work**. The run says when a window has
   not landed yet, saves the windows in `out/runs.jsonl`, and `python3 demo.py telemetry` reads them again later.
5. **It all goes out as Prometheus text.** `out/metrics.prom` (latency summaries, hit ratio, facts, documents, evictions,
   alert state and, once landed, usage records and tokens per job) and `out/alerts.json`. Set `ALERT_WEBHOOK_URL` and the
   firing alerts are also POSTed there as JSON.

**What MemoryLake provides and what the demo computes.** MemoryLake provides the operations, the facts with their metadata, the
document counts (`proj stats`) and the usage records (`usage summary`, `usage records`). The latency, hit ratio, alert rules,
miss tracing and the Prometheus file are computed by `demo.py` from those. There is no Prometheus exporter, PagerDuty or
Opsgenie integration in the CLI or the free tier; the demo shows what you can build on what is there.

**Watch it run** (real recordings, unedited): [CLI demo, 2:24](https://github.com/memorylake-ai/memorylake-usecases/releases/download/memory-telemetry-v1/memory-telemetry-cli-demo.mp4) · [web demo, 2:48](https://github.com/memorylake-ai/memorylake-usecases/releases/download/memory-telemetry-v1/memory-telemetry-web-demo.mp4)
(GitHub serves release videos as downloads.)

## Prerequisites

1. **A MemoryLake account and API key.** Sign up at [app.memorylake.ai](https://app.memorylake.ai),
   open **API Keys**, create a key and copy it. Details: [Authentication and API Keys](https://docs.memorylake.ai/authentication).
2. **The `memorylake` CLI** (v20261013 or later: `usage` arrived in that release):

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
cd memorylake-usecases/memory-telemetry

export MEMORYLAKE_API_KEY=sk-…
python3 demo.py
```

A run takes about 2 minutes, half of it the runbook import. Then, an hour or so later:

```bash
python3 demo.py telemetry
```

With `MEMORYLAKE_API_KEY` set, the demo logs into its own CLI profile under `./.memorylake-demo/`
(git-ignored) and leaves `~/.memorylake` alone. Without it, it uses your existing `memorylake auth login`.

| | |
|---|---|
| `MEMORYLAKE_BASE_URL` | API endpoint; default global, China is `https://app.memorylake.cn/openapi/memorylake` |
| `MEMORYLAKE_WORKSPACE` | Workspace id; default is the one remembered by `ws use`, else your default workspace |
| `ALERT_WEBHOOK_URL` | Optional: POST the firing alerts as JSON to this URL |
| `python3 demo.py --reset` | Delete everything the demo created first, then run |
| `python3 demo.py telemetry` | Read the usage records for the job windows of your last five runs, and today's usage summary (reads only) |
| `python3 demo.py metrics` | Print `out/metrics.prom` |
| `python3 demo.py ask "Do gift cards expire?"` | Search the support memory: the top 5 hits and how long the search took |
| `python3 demo.py cleanup` | Delete the support project (its documents and facts with it) and the runbook folder. `out/runs.jsonl` stays |
| `--show-json` | Print the raw JSON the CLI returns |

**Every run is a fresh workload.** A run that finds the project or the folder of an earlier run deletes them first and says so.
The project is found by `custom_id` (`mlu-tel-support`), the folder by name. The job windows of every run are appended to
`out/runs.jsonl`, so `telemetry` can read them after the data is gone.

## Or run it in the browser

```bash
python3 web/server.py        # http://127.0.0.1:8768 — standard library only
```

![Web companion — answer-agent pass 2: same questions, 3 of 6 now miss](https://github.com/memorylake-ai/memorylake-usecases/raw/main/memory-telemetry/web/screenshot-pass2.png)

![Web companion — telemetry: hit ratio 1.00 → 0.50, facts 6 → 1, two alert rules firing](https://github.com/memorylake-ai/memorylake-usecases/raw/main/memory-telemetry/web/screenshot-telemetry.png)

Paste the key, press **Run demo**, and follow the eight steps: the four jobs (each with its window and operations), the two
answer passes side by side with latency per question, the evictions, and the telemetry page (hit ratio, facts, alert rules,
miss tracing, latency table, usage records). **Ask · usage** searches the memory and reads the usage records of your saved runs
again. The terminal drawer shows every `memorylake` command as it runs.

## What happens, step by step

### 1. Connect

`auth login --api-key` into the isolated profile, `team get`, and the team's default workspace.

### 2. Set up — one shared memory

```bash
memorylake lib mkdir mlu-tel-bramblecrest-runbooks --parent MY_SPACE --on-conflict deny
memorylake proj create --name 'Bramblecrest support memory' --custom-id mlu-tel-support --description '…' --workspace <ws>
```

If the project or the folder of an earlier run is still there, they are deleted first: every run measures a fresh workload.

### 3. intake-agent — the runbooks go into memory

```bash
memorylake lib upload data/runbooks/returns.md --parent <folder> --on-conflict overwrite      # and shipping.md, warranty.md
memorylake proj doc import --project <support> --workspace <ws> --wait <folder> --recursive
memorylake proj stats <support> --workspace <ws>
```

```
  [intake-agent] imports the support runbooks   window opens 05:27:04 UTC
  [intake-agent] window closes 05:27:37 UTC — 3 lib upload, 1 doc import, 1 proj stats
    3 imported in 25s · 3 of 3 documents okay
```

Each job opens and closes a **window** (whole Unix seconds, never overlapping). The windows are what the usage records are
read against in step 8.

### 4. scribe-agent — notes with a time-to-live

```bash
memorylake fact add --project <support> 'Carrier agreement: Sierra Freight picks up oversized freight …' '…' --workspace <ws>   # six texts, one call
memorylake fact update <fact> --project <support> --metadata '{"kind": "ticket", "noted": "2026-09-29", "ttl_days": 7, "by": "scribe-agent"}' --workspace <ws>
```

```
    fact                 kind    noted       ttl   text
    fact-bda74e1b4e9b…   ticket  2026-09-29  7d    Carrier agreement: Sierra Freight picks up oversized frei…
    fact-9da552e917f6…   ticket  2026-09-30  7d    Exception agreed with the Alpine Club: club members get a…
    fact-3d8630b3487f…   ticket  2026-10-01  7d    The Reno repair bench is closed for inventory from Octobe…
    fact-377711364fca…   policy  2026-09-15  —     Gift cards never expire and cannot be redeemed for cash.
    fact-db6bee7b2ae5…   ticket  2026-09-30  7d    Ticket 48213: a delayed kayak delivery to Boise was resol…
    fact-7c615c65478c…   ticket  2026-10-01  7d    Ticket 48240: the customer asked whether the Ridgeline 2 …
```

`fact add` takes several texts and returns the facts in the same order; `fact add` has no `--metadata`, so each fact gets its
metadata from a `fact update` right after.

### 5. answer-agent, pass 1

```bash
memorylake search 'Which days does the freight carrier pick up oversized items?' --projects <support> --top-k 5 --workspace <ws>
```

```
    ✓  3.01s  Which days does the freight carrier pick up oversized items?     fact fact-bda74e1b4e9b…
    ✓  4.20s  Do Alpine Club members get a longer return window on climbing r… fact fact-9da552e917f6…
    ✓  3.28s  When do warranty repairs resume after the repair bench inventor… fact fact-3d8630b3487f…
    ✓  3.41s  How long do customers have to return an item?                    doc returns.md
    ✓  3.41s  Can tents be sent with expedited shipping?                       doc shipping.md
    ✓  2.63s  Do gift cards expire?                                            fact fact-377711364fca…

    hit ratio 6/6 = 1.00
```

A hit means the expected answer ("Tuesdays and Fridays", "90-day", …) is in one of the five results: a fact, or a passage of a
runbook. The time is the whole CLI call, start-up included.

### 6. retention-job — eviction by metadata

```bash
memorylake fact list --projects <support> --workspace <ws> --page-size 200
memorylake fact delete <fact> --project <support> --workspace <ws>          # one id per call
```

```
    rule: delete when noted + ttl_days < 2026-10-13
    evicted fact-7c615c65478c…   ticket  noted 2026-10-01 ttl 7d  Ticket 48240: the customer asked whether the Ridg…
    evicted fact-db6bee7b2ae5…   ticket  noted 2026-09-30 ttl 7d  Ticket 48213: a delayed kayak delivery to Boise w…
    evicted fact-3d8630b3487f…   ticket  noted 2026-10-01 ttl 7d  The Reno repair bench is closed for inventory fro…
    evicted fact-9da552e917f6…   ticket  noted 2026-09-30 ttl 7d  Exception agreed with the Alpine Club: club membe…
    evicted fact-bda74e1b4e9b…   ticket  noted 2026-09-29 ttl 7d  Carrier agreement: Sierra Freight picks up oversi…

    facts in the project: 6 → 1  (5 evicted)
```

`fact list` returns each fact with its metadata, so the rule runs on what the scribe wrote, and nothing else.

### 7. answer-agent, pass 2

```
    ✗  3.05s  Which days does the freight carrier pick up oversized items?     “Tuesdays and Fridays” not in the top 5
    ✗  2.87s  Do Alpine Club members get a longer return window on climbing r… “90-day” not in the top 5
    ✗  2.66s  When do warranty repairs resume after the repair bench inventor… “October 27” not in the top 5
    ✓  2.91s  How long do customers have to return an item?                    doc returns.md
    ✓  2.54s  Can tents be sent with expedited shipping?                       doc shipping.md
    ✓  2.43s  Do gift cards expire?                                            fact fact-377711364fca…

    hit ratio 3/6 = 0.50
```

Same agent, same questions, same latency. Only the memory changed.

### 8. Telemetry

```
  latency at the client (each CLI call, start-up included)
    operation     calls     p50     p95     max
    doc import        1  24.27s  24.27s  24.27s
    fact add          1   1.35s   1.35s   1.35s
    fact delete       5   0.79s   1.04s   1.04s
    fact list         2   0.88s   1.04s   1.04s
    fact update       6   0.55s   0.79s   0.79s
    lib upload        3   2.20s   2.35s   2.35s
    proj stats        1   0.96s   0.96s   0.96s
    search           12   2.91s   4.20s   4.20s

  memory health
    retrieval hit ratio   1.00 → 0.50   (answer-agent, 6 questions, top 5)
    facts held            6 → 1
    evictions             5   (retention-job, ttl)
    documents             3   (3 okay)

  alert rules
    🔴 FIRING  MemoryHitRatioLow       0.5 (rule < 0.8)  answer-agent hit ratio 1.00 → 0.50
    🔴 FIRING  MemoryFactsDropped     0.83 (rule > 0.3)  facts 6 → 1 in one retention window
    🟢 ok      SearchLatencyHigh       4.2 (rule > 6.0s)  search p95 4.20s at the client (includes CLI start-up)

  why answer-agent got worse — each new miss, matched to what the retention job evicted
    ✗ Which days does the freight carrier pick up oversized items?
        ← evicted fact-bda74e1b4e9b… (kind=ticket, ttl 7d): “Carrier agreement: Sierra Freight picks up oversized freight from the Reno ware…”
    ✗ Do Alpine Club members get a longer return window on climbi…
        ← evicted fact-9da552e917f6… (kind=ticket, ttl 7d): “Exception agreed with the Alpine Club: club members get a 90-day return window …”
    ✗ When do warranty repairs resume after the repair bench inve…
        ← evicted fact-3d8630b3487f… (kind=ticket, ttl 7d): “The Reno repair bench is closed for inventory from October 20 to October 24; wa…”

    3 of 3 new misses trace to an eviction; 2 of 5 evictions cost no answer
```

Then MemoryLake's own usage records for each window:

```bash
memorylake usage records --page-size 1                                          # how far the record stream has got
memorylake usage records --start-time 1791869224 --end-time 1791869257 --page-size 100   # one job's window, every page
```

```
  usage records — this run   (newest record in the account: 03:56:09 UTC, 92 min ago)
    agent                 window (UTC)      what the agent did         records  by operation (records · tokens)
    intake-agent          05:27:04–05:27:37 3 lib upload, 1 doc impor…       —  not landed yet
    scribe-agent          05:27:37–05:27:43 1 fact add, 6 fact update        —  not landed yet
    …
```

A window counts as landed once the newest record in the account is later than its end. Until then the demo says so instead
of printing zeros. `python3 demo.py telemetry` reads the windows of the last five runs (from `out/runs.jsonl`) again, plus
`usage summary` for the day.

Last, `out/metrics.prom` (41 samples: `memorylake_demo_op_latency_seconds` summaries, `memorylake_demo_retrieval_hit_ratio`,
`memorylake_demo_project_facts`, `memorylake_demo_project_documents`, `memorylake_demo_evictions_total`,
`memorylake_demo_alert_firing`, and the usage records and tokens per job once landed) and `out/alerts.json`.

## Measured

Eleven full runs on a free account (2026-10-13, CLI v20261013): seven from the terminal (one of them the recording), two from
the web companion (one the recording), one with `--reset`, one fresh after a cleanup:

| | every run |
|---|---|
| hit ratio, pass 1 → pass 2 | **6/6 → 3/6** (11 of 11) |
| facts held | 6 → 1, 5 evicted (11 of 11) |
| new misses traced to an eviction | **3 of 3** (11 of 11); the 2 real one-off tickets cost no answer |
| alerts | `MemoryHitRatioLow` and `MemoryFactsDropped` FIRING, `SearchLatencyHigh` ok (11 of 11) |
| search p95 at the client | 3.0–4.5 s |
| runbook import (3 Markdown files, `--wait`) | 24–41 s |
| whole run | 83–104 s |

**Usage records.** In none of the nine runs had the job windows landed by the end of the run. While these runs were made,
the newest record in the account stayed at 03:56 UTC from 04:48 to at least 05:28 UTC.

Read back two hours later (07:15 UTC), the stall had been flushed, but not into the job windows: the account had **no record
stamped between 03:56 and 06:13 UTC, then 791 records stamped within 17 seconds (06:13:47–06:14:04)** — about 2 h 17 min
of the team's work, each record carrying the time it arrived rather than the time of the operation. Every window of the
saved runs therefore reads 0. `telemetry` now prints `none stamped in this window` for a job that did work but has no record
in its window, instead of `no billed operations`, and leaves it out of the per-agent totals. By 07:17 UTC the newest record
was still 06:14:04.

## Wiring it into your own agent ops

- **Give every job its own window, or its own API key.** On a team with members, usage records carry the member who made them
  (`--member`); on a personal account every key belongs to the same member, so a window is the only way to tell jobs apart.
  Keep windows from overlapping, and keep other work on the account out of them.
- **Measure hit ratio with questions you know the answer to.** A handful of canaries with an expected answer, run after every
  memory change, catch what latency and error rates never show.
- **Watch facts held and evictions next to hit ratio.** A retention rule that keys on metadata deletes whatever the metadata says;
  the drop in facts and the drop in hit ratio land in the same window.
- **Read usage records late, and do not trust an empty window.** They arrive in batches, and a batch that arrives after a stall
  is stamped with its arrival time, so the work of a stalled period shows up as one burst outside every job window. A window
  that reads 0 while the job did work means "not attributable", not "free". For per-job cost, use one API key (member) per
  job rather than time windows.
- **`usage records` allows about 20 calls a minute** (then `429 rate limit exceeded`); page with `--page-size 100` and narrow by
  `--start-time` / `--end-time`, `--model`, `--kind`.

## If something goes wrong

- **`not landed yet`** on every window: usage records have not caught up. Run `python3 demo.py telemetry` later.
- **`none stamped in this window`**: the record stream has moved past the window, but nothing in it carries the window's time.
  After a stall the records are stamped with the time they arrive (see Measured), so they sit in a later burst.
- **`429 rate limit exceeded`**: `usage records` is limited to about 20 calls a minute. The demo waits 20 s and retries.
- **The hit ratio stays 6/6 after the retention job**: an answer was found somewhere else (a runbook passage). The demo prints
  where each hit came from.
- **`DRIVE_ITEM_CONFLICT` on `lib mkdir`**: a folder named `mlu-tel-bramblecrest-runbooks` exists but the demo did not find it.
  Run `python3 demo.py cleanup`.
- **`tls handshake eof` / `could not connect`**: a dropped connection. Read-only commands retry four times. Otherwise run again;
  it starts over.

## Files

```
demo.py                  the runner; prints every CLI command it runs (and emits events for the web app)
data/story.json          the company, the four jobs, the six notes (kind, date, TTL), the six questions and their answers, the alert rules
data/runbooks/           returns, shipping and warranty runbooks (Markdown, ASCII)
web/server.py            local web companion (stdlib HTTP server + Server-Sent Events over demo.py)
web/static/              the page — no build step
out/                     runs.jsonl (one line per run, with its job windows), metrics.prom, alerts.json (git-ignored)
```

The company, its agents and everyone in it are fictional.
