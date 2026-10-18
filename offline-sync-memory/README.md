# Offline sync memory: an edge agent reconnects — replaying its transcript lets an older request win

A runnable version of the MemoryLake use case
[*Give Edge-Deployed AI Agents Memory That Works Offline and Syncs on Reconnect*](https://www.memorylake.ai/en/usecase/memory-for-edge-deployed-ai-agents),
built entirely on the [`memorylake` CLI](https://github.com/memorylake-ai/memorylake-cli) (v20261015 or later).

**Watch it run:** recording in progress.

**The problem.** An agent on a kiosk, a robot or a vehicle loses its connection. While it is offline it keeps
answering from a local copy of its memory and writes down what people tell it. Meanwhile the cloud does not stand
still: HQ changes some of the same records. When the device reconnects, whose change wins?

- **Replay the transcript** — the obvious move: append the device's conversation to MemoryLake, each message with the
  time it was said (`conv msg append --timestamp`), and let the extractor update memory as it does for any
  conversation. **The extractor does not order by the message's timestamp.** A request the kiosk heard 30 seconds
  *before* HQ changed the order lands *after* it, and the kiosk now reads the older request. Nothing flags it. In
  other runs the extractor wrote nothing at all from the replayed messages, and the customers' requests were simply
  gone. In every run, at least one order came out wrong.
- **Check the version** — an outbox of structured changes; apply one only if the record's `updated_at` is still the
  one in the offline snapshot, otherwise hold it for a person. It never overwrites HQ — and it also holds a request
  the customer made *after* HQ's change. The customer's latest wish waits in a queue; the order still says the old
  date.
- **Compare the request's time with the record's last write** (`fact trace`) — apply when the request is newer, keep
  the record and log the request as superseded when HQ wrote later. Every order right, every run.

**What this demo shows.** Fernhill Garden Centres (fictional) runs a kiosk assistant, Sprout, in Store 14. Order
memory is one fact per order. The kiosk goes offline for 70 seconds, in real time:

| when | where | what |
|---|---|---|
| +10 s | kiosk (offline) | Dana asks to move order 5521 to **Friday 23 October** |
| +20 s | HQ (online) | Omar emailed: order 5530 → **Wednesday 28 October** (`fact update`) |
| +30 s | kiosk (offline) | Mei asks to move order 5547 to **Sunday 25 October** |
| +40 s | HQ (online) | Dana phoned: order 5521 → **Saturday 24 October** (`fact update`) |
| +50 s | HQ (online) | the nursery's truck moved: order 5560 → **Friday 30 October** (`fact update`) |
| +55 s | kiosk (offline) | Ravi asks when his apple trees arrive: the cloud is really unreachable (`--base-url` points nowhere), the kiosk answers **Thursday 29** from its snapshot |
| +60 s | kiosk (offline) | Omar asks to move order 5530 to **Monday 26 October** |

Order 5521 was changed in both places, the kiosk first: it should end on Saturday. Order 5530 too, HQ first: it
should end on Monday. The kiosk then syncs three ways, each on its own copy of the orders project:

| strategy | what it does on reconnect |
|---|---|
| **A replay the transcript** | appends the kiosk's three messages to a conversation on the orders project, each with its own `--timestamp`; the extractor updates the orders |
| **B version check** | an outbox of changes; `fact get` each order: apply if `updated_at` is still the snapshot's, else hold for review |
| **C request vs last write** | the same outbox; `fact trace` each order: apply if the last write is older than the request, else keep it and log the request as superseded |

B and C also sync the transcript — to a separate kiosk-log project, for the audit trail, where the extractor cannot
touch an order. No model calls: nothing is billed to your MemoryLake balance.

## Prerequisites

- **Python 3.9+** (standard library only).
- **The `memorylake` CLI, v20261015 or later** (`memorylake version`):
  ```bash
  curl -fsSL https://raw.githubusercontent.com/memorylake-ai/memorylake-cli/main/scripts/install.sh | sh
  ```
- **A MemoryLake API key**: sign up at [app.memorylake.ai](https://app.memorylake.ai), open **API Keys**, create one.
  Everything the demo uses (projects, facts, conversations, `fact trace`) is part of the free plan.

## Run it

```bash
git clone https://github.com/memorylake-ai/memorylake-usecases.git
cd memorylake-usecases/offline-sync-memory
export MEMORYLAKE_API_KEY=sk-...        # logs into an isolated profile under .memorylake-demo/, never touches ~/.memorylake
python3 demo.py                         # about 3 minutes: 70 s offline window in real time, then the extractor
python3 demo.py timeline                # the offline window against every order's history, and the review notes
python3 demo.py cleanup                 # deletes everything the demo created
```

China region: also `export MEMORYLAKE_BASE_URL=https://app.memorylake.cn/openapi/memorylake`. To use a specific
workspace: `export MEMORYLAKE_WORKSPACE=ws-…` (default: the team's default workspace). Without `MEMORYLAKE_API_KEY`
the demo uses your existing `memorylake auth login` session.

Every object the demo creates has a `custom_id` starting with `mlu-ofs-`. Each run starts over: it deletes what a
previous run left behind first (`--reset` is accepted and does the same).

## Or run it in the browser

```bash
python3 web/server.py                   # opens http://127.0.0.1:8765
```

Paste the API key, press **Run demo**, and follow the steps: the orders, the snapshot, the offline window as it
happens, the three syncs, and the score. The terminal drawer at the bottom shows every `memorylake` command as it
runs. The server is standard library only and binds to localhost; the key stays in that process and in
`.memorylake-demo/`.

## What happens, step by step

Output below is from one real run (the third terminal run, with `--reset`; ids shortened).

### Step 2: memory

```
$ memorylake proj create --name 'Fernhill orders (A replay the transcript)' --custom-id mlu-ofs-orders-a --workspace ws-…
$ memorylake fact add 'Order 5521 (Dana Ruiz, olive tree): pickup at Store 14 on Thursday 22 October.' 'Order 5530 (Omar Haddad, raised bed kit): delivery on Tuesday 27 October.' … --project proj-… --workspace ws-…
  · the same 4 orders on three copies of the project, one per sync strategy (B and C: same commands, quietly)
$ memorylake proj create --name 'Fernhill Store 14 kiosk log' --custom-id mlu-ofs-kiosk-log --workspace ws-…
$ memorylake actor create --custom-id mlu-ofs-kiosk --display-name 'Sprout (Store 14 kiosk)' --description 'In-store kiosk assistant, Fernhill Store 14. Demo data.' --tags demo
$ memorylake conv create --custom-id mlu-ofs-kiosk-a --project proj-… --actors actor-… --kind DIRECT --name 'Store 14 kiosk' --workspace ws-…
```

### Steps 3–4: snapshot, then 70 seconds offline

```
  · the kiosk caches every order locally at 16:43:45 — its offline memory
    +10 s  kiosk (offline)  Dana Ruiz (customer, order 5521) at the Store 14 kiosk: Please move the pickup of my olive tree, order 5521, to Friday 23 October.
            → written to the kiosk's local outbox at 16:43:55: order 5521 → Friday 23 October
$ memorylake fact update fact-… --text 'Order 5530 (Omar Haddad, raised bed kit): delivery on Wednesday 28 October.' --project proj-… --workspace ws-…
    +20 s  HQ (online)      Omar emailed HQ: Tuesday is no good, deliver on Wednesday
    …
    +40 s  HQ (online)      Dana phoned HQ: Saturday after all, her van is free then
            → `fact update` at 16:44:25: Order 5521 (Dana Ruiz, olive tree): pickup at Store 14 on Saturday 24 October.
    …
$ memorylake fact list --projects proj-… --query 5560 --base-url http://127.0.0.1:9/openapi/memorylake --workspace ws-…
            → cloud unreachable (Error: list facts Caused by: 0: could not connect to MemoryLake API; c…)
            → answered from the local snapshot taken at 16:43:45: Thu 29 Oct
    +60 s  kiosk (offline)  Omar Haddad (customer, order 5530) at the Store 14 kiosk: Please deliver my raised bed kit, order 5530, on Monday 26 October.
  · back online after 70 s: 3 requests in the outbox, HQ changed 3 orders meanwhile
```

### Step 5: reconnect and sync

```
$ memorylake conv msg append conv-… --actor actor-… --custom-id kiosk-5521 --timestamp 2026-10-18T16:43:55.806Z --text 'Dana Ruiz (customer, order 5521) at the Store 14 kiosk: Please move the pickup of my olive tree, order 5521, to Friday 23 October.' --workspace ws-…
    …
$ memorylake fact get fact-… --project proj-… --workspace ws-…
    B  5521  updated_at 16:43:36 in the snapshot, 16:44:27 now → changed: held for review (keeps Sat 24 Oct)
    B  5547  updated_at 16:43:36 in the snapshot, 16:43:36 now → unchanged: applied
    B  5530  updated_at 16:43:36 in the snapshot, 16:44:07 now → changed: held for review (keeps Wed 28 Oct)
$ memorylake fact trace fact-… --project proj-… --workspace ws-…
    C  5521  request 16:43:55 vs last write 16:44:27 (UPDATE · MANUAL) → HQ wrote 31 s after the request: kept Sat 24 Oct, request logged
    C  5547  request 16:44:15 vs last write 16:43:38 (ADD · MANUAL) → request is newer: applied
    C  5530  request 16:44:45 vs last write 16:44:07 (UPDATE · MANUAL) → request is newer: applied
$ memorylake fact add 'Superseded (C, kiosk sync): order 5521: the kiosk'"'"'s request of 16:43:55 UTC for Friday 23 October came before HQ'"'"'s change at 16:44:27 UTC; tell the customer the order stays on Sat 24 Oct.' --project proj-… --workspace ws-…
  · A: the extractor touched 3 fact(s) on the orders project, done 49 s after the first append
```

C trusts two clocks — the kiosk's and the server's. A request and a write less than 2 s apart are held, not decided.

### Step 6: score

What the kiosk reads for an order is the newest fact that names it (what `fact list --query 5521` returns first).

```
    order  customer     should be    A replay the transcript    B version check            C request vs last write
    5521   Dana Ruiz    Sat 24 Oct   ✗ Fri 23 Oct               ✓ Sat 24 Oct (held)        ✓ Sat 24 Oct (held)
    5530   Omar Haddad  Mon 26 Oct   ✓ Mon 26 Oct               ✗ Wed 28 Oct (held)        ✓ Mon 26 Oct
    5547   Mei Tanaka   Sun 25 Oct   ✓ Sun 25 Oct               ✓ Sun 25 Oct               ✓ Sun 25 Oct
    5560   Ravi Menon   Fri 30 Oct   ✓ Fri 30 Oct               ✓ Fri 30 Oct               ✓ Fri 30 Oct
    A replay the transcript: 3/4 current, 1 overwritten silently   B version check: 3/4 current, 2 held   C request vs last write: 4/4 current, 1 held

    A, order 5521: 2 fact(s) name it, newest first (the kiosk reads the first):
      Fri 23 Oct  last write ADD · COOK 16:45:36, from a message said at 16:43:55, 16:44:15, 16:44:45  (a new fact)
      Sat 24 Oct  last write UPDATE · MANUAL 16:44:27  (the order's own fact)
    the customer asked at 16:43:55; HQ changed it at 16:44:25; the extractor wrote the older request last — the kiosk now reads Fri 23 Oct
  · C kept order 5521 on Sat 24 Oct and logged the 16:43:55 request as superseded in the kiosk-log project — the customer gets a call, not a silent change
  · B held order 5530: HQ had changed it, so its version moved — but the customer's 16:44:45 request came after that change; it waits for a person, and the order still says Wed 28 Oct
  · offline answer to order 5560: told Thu 29 Oct from the snapshot; memory now says Fri 30 Oct → the kiosk flags a call-back
  · A's extractor added facts beside the orders' (0 rewritten, 3 added) — 3/4 orders current
```

In this run the extractor wrote each request as a new fact (*"…was requested to be moved to Friday 23 October"*)
beside the order's own one, so order 5521 has two facts that disagree and the newer one is the stale request. In
other runs it rewrote the order's own fact instead (*"…Friday 23 October (previously Saturday 24 October)"*) — same
result for the kiosk.

### `python3 demo.py timeline`

```
  the offline window (UTC):
    16:43:55  +10 s  kiosk heard: order 5521 → Friday 23 October
    16:44:05  +20 s  HQ changed: order 5530 → Wednesday 28 October
    16:44:15  +30 s  kiosk heard: order 5547 → Sunday 25 October
    16:44:25  +40 s  HQ changed: order 5521 → Saturday 24 October
    …
  A replay the transcript — every fact that names an order, newest first, with its history (`fact trace`):
    5521   Fri 23 Oct (COOK 16:45:36)
           Thu 22 Oct (MANUAL 16:43:34) → Sat 24 Oct (MANUAL 16:44:27)
    …
  C request vs last write — every fact that names an order, newest first, with its history (`fact trace`):
    5521   Thu 22 Oct (MANUAL 16:43:38) → Sat 24 Oct (MANUAL 16:44:27)
    5530   Tue 27 Oct (MANUAL 16:43:38) → Wed 28 Oct (MANUAL 16:44:07) → Mon 26 Oct (MANUAL 16:45:11)
    …
  kiosk-log project: 3 review note(s) written by B and C
    Superseded (C, kiosk sync): order 5521: the kiosk's request of 16:43:55 UTC for Friday 23 October came before HQ's change at 16:44:27 UTC; tell the customer the order stays on Sat 24 Oct.
    Review (B, kiosk sync): order 5530 was changed at HQ while Store 14's kiosk was offline; the kiosk's request of 16:44:45 UTC for Monday 26 October is on hold.
    …
```

## Measured

6 full runs: four in this directory (two terminal runs, one with `--reset`, and a browser run) and two copies run
side by side with their own prefix. Every number comes from the saved reports (`out/runs/`).

| over 6 runs | A replay the transcript | B version check | C request vs last write |
|---|---|---|---|
| orders current (of 24) | 16 | 18 | **24** |
| runs with every order current | 0 of 6 | 0 of 6 | 6 of 6 |
| order 5521 (kiosk first, HQ later) | Friday 4 · Saturday 2 | held, Saturday 6 | held, Saturday 6 |
| order 5530 (HQ first, kiosk later) | Monday 4 · Wednesday 2 | **held, Wednesday 6** | Monday 6 |
| order 5547 (kiosk only) | Sunday 4 · Saturday 2 | Sunday 6 | Sunday 6 |

What the extractor did with the replayed messages:

| over 6 runs | |
|---|---|
| rewrote the orders' own facts ("…Friday 23 October (previously Saturday 24 October)") | 1 |
| added a new fact per request beside the order's ("…was requested to be moved to Friday 23 October") | 3 |
| wrote nothing (cook finished, three messages stored, 0 facts) — Mei's and Omar's requests lost | 2 (the two copies run side by side) |
| the older request for 5521 ended up as what the kiosk reads, when it wrote anything | 4 of 4 |

Before the demo, a probe of the same shape (3 copies) gave the same answer 3 of 3 times, rewriting the order's own
fact. A, B and C are measured, never assumed: the score reads each project back with `fact list` after the sync.

## Use it in your own edge agents

- **Do not use the extractor as a sync protocol.** It applies messages in the order they arrive. A replayed
  message's `--timestamp` is kept on the message, but it does not make the
  message older than a write that came before it. Sync structured changes yourself; send the transcript for the
  record.
- **Keep the transcript away from the records it mentions.** A conversation's facts land on the project the
  conversation belongs to. B and C put the kiosk's transcript on a separate kiosk-log project.
- **"Changed since I read it" is not "changed after the request".** The version check is safe, but it holds every
  collision — including the ones the device should win. Compare the request's time with the record's last write
  (`fact trace`, newest entry) to decide; hold only what is too close to call.
- **Two clocks are involved.** The device's clock stamps the request, the server's clock stamps the write. Keep the
  device on NTP and hold anything within the skew you can't rule out.
- **Log what you didn't apply.** A superseded request is still something a customer asked for: write it down
  (here, a fact on the kiosk-log project) and call them back. The same goes for anything the device answered from its
  snapshot that turned out stale.

## Troubleshooting

- **`this memorylake CLI has no fact trace with full history`** — the CLI is older than v20261015; reinstall with the
  `install.sh` line above.
- **`B/C disagree with the story`** — B and C are plain code over `fact get` / `fact trace`; if they held something
  else, a write took longer than the gap between events. Run again; if it repeats, open an issue with the files in
  `out/`.
- **A waits up to 240 s** for the extractor, then scores what is there. "touched 0 fact(s)" means the extractor wrote
  nothing from the replayed messages — that happens, and the score shows what it costs.
- **Leftovers after an interrupted run** — `python3 demo.py cleanup`.

## Files

| file | what it is |
|---|---|
| `demo.py` | the whole demo: CLI wrapper, the offline window, the three syncs, the score, timeline, cleanup |
| `data/story.json` | the orders, the offline timeline and the three strategies |
| `web/server.py`, `web/static/` | the browser companion (standard library server, no build step) |
| `out/world.json`, `out/snapshot.json`, `out/outbox.json`, `out/offline.json`, `out/report.json` | ids, the kiosk's snapshot and outbox, the offline window, and the score (`cleanup` moves them to `out/runs/`) |
