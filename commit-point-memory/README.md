# Commit-point memory: restart the agent the moment your rule says "safe"

A runnable version of the MemoryLake use case
[*Give Autonomous Agents a Memory Store Built for Long-Running Work*](https://www.memorylake.ai/en/usecase/memory-store-for-autonomous-agents),
built entirely on the [`memorylake` CLI](https://github.com/memorylake-ai/memorylake-cli) (v20261015 or later).

**Watch it run:** recording in progress.

**The problem.** A long-running agent writes every step into MemoryLake as it goes, so that a crash at hour five
does not send it back to step one. But "written as it goes" has a delay in it: the server accepts the message at
once, extraction reads it a few seconds later, and the facts it yields are written a few seconds after that. A crash
wipes whatever the agent kept in its process, so the question that decides what survives is: **when does a step count
as committed?** Pick too early a signal and the restarted agent finds the value from *before* the step, and uses it
with confidence (one run's lines for one change; the steps in between left out):

```
  step  6  + 21.6s  Mei: The warehouse is under load tonight, so drop the batch size to 2,000 rows instead of 5,…
           ▶ batch size: 5,000 rows → 2,000 rows. Restarting Tern at each rule's commit moment:
      ↻ B  appended  + 23.6s ( +2.0s after it was said)  restart → ⟲ batch size
          | The batch size for tonight's run is 5,000 rows.
      ↻ C  read      + 38.2s (+16.6s after it was said)  restart → ⟲ batch size
          | The batch size for tonight's run is 5,000 rows.
      ↻ D  written   + 43.5s (+21.9s after it was said)  restart → ✓ batch size
          | The batch size for tonight's September order-events backfill is 2,000 rows.
```

Tern is the autonomous agent that runs the overnight backfills at Halvorsen Analytics (a fictional company). Three
past sessions with Mei Okafor, a data engineer, settled five run settings: target schema `analytics_v3`, batches of
5,000 rows, alerts to `#pipeline-alerts`, staging in `s3://hv-stage-a`, writes only between 01:00 and 05:00 UTC.
Tonight, in a 16-step backfill (a step every 4 seconds or so), Mei changes three of them: the schema to
`analytics_v4` at step 2, the batch size to 2,000 rows at step 6, the alert channel to `#backfill-oncall` at step 10.
Tern appends every step to a MemoryLake conversation as it happens. Three rules for when a step is committed:

- **B — once the append returns.** `conv msg append` came back: the server has the message.
- **C — once extraction has read it.** `conv consumed-messages --message <id>` lists the batch it was read in.
- **D — once memory has written it.** `conv fact-actions --message <id>` shows what that batch added, updated or
  forgot.

For each change, the demo restarts Tern at the exact moment each rule calls the step committed — for that rule, the
riskiest moment a crash can come — and asks the restarted agent what the setting is now. The restarted agent has no
notes of its own: only the playbook, the date, the run it is resuming, and what MemoryRouter attaches from its memory
(`router chat --boundary`). At each of B's restarts it is also asked an unchanged setting, as a control.

<!-- measured-summary -->
<!-- /measured-summary -->

## What it shows

- **"The server has it" is not "memory has it".** B restarts about two seconds after the change and always gets the
  old value back: the message is stored, but nothing has been extracted from it yet.
- **"Extraction has read it" is not "memory has it" either.** `consumed-messages` fills in when a batch is *read*;
  the facts land several seconds later. Restarted at that moment, C found the old value every time.
- **The receipt is per batch, and it comes when the write does.** `fact-actions --message` answers for the whole
  batch the message was read in (every message in it gets the same list), and it is empty until the batch's changes
  are written. Restarted on the receipt, D found the new value.
- **A batch with nothing to write never gets a receipt.** Progress chatter ("Partitions 6 to 10 loaded") yields no
  facts, so its `fact-actions` stays empty forever. A rule that waits for every message's own receipt would keep
  those steps in the process for good; D also releases a batch once a *later* batch has its receipt.
- **`cook-status` is no commit point while the agent keeps working.** It is one flag for the whole conversation,
  true only in the moments extraction has caught up with everything — a sample or two out of twenty during the run.
- **The memory itself was fine.** The unchanged settings asked at B's restarts were right every time; what fails is
  the moment the agent decides it no longer needs its own copy.

## Prerequisites

- Python 3.9 or later (standard library only).
- The `memorylake` CLI, v20261015 or later:
  `curl -fsSL https://raw.githubusercontent.com/memorylake-ai/memorylake-cli/main/scripts/install.sh | sh`
- A MemoryLake API key from [app.memorylake.ai](https://app.memorylake.ai) (**API Keys**).
- Router calls are billed to your MemoryLake balance: 12 calls on gpt-4o-mini per run, well under 0.01 USD.

## Run it

```bash
cd commit-point-memory
export MEMORYLAKE_API_KEY=sk-...
python3 demo.py            # about 4 minutes: past sessions extracted, a 16-step run with 9 restarts, the commit log
python3 demo.py log        # the last night's commit log again: every batch, what it read and wrote
python3 demo.py ask "Which schema is tonight's backfill writing to?"   # a restarted Tern asks its memory
python3 demo.py cleanup    # deletes everything the demo created
```

The key is used to log in to an isolated CLI profile in `.memorylake-demo/`; your own `~/.memorylake` is not touched.
Everything the demo creates has a custom_id starting `mlu-cmt-`. A run leaves tonight's new values in memory, so the
next run rebuilds the memory from the past sessions (a restart would otherwise find the new values whatever the
rule); `--reset` does the same on purpose.

## Or run it in the browser

```bash
python3 web/server.py      # opens http://127.0.0.1:8765
```

Paste the key, press **Run demo**: the same pipeline, with every `memorylake` command in the terminal drawer.
**Tonight** shows each change with its three restarts as they happen; **Commit log** shows every extraction batch and
D's verdict on it; **Ask one** asks a restarted Tern.

<!-- screenshots -->

## What happens, step by step

### Step 2: Tern's memory

```
$ memorylake actor create --custom-id mlu-cmt-mei --display-name 'Mei Okafor' --description '…' --tags demo
$ memorylake proj create --name 'Overnight backfills' --custom-id mlu-cmt-proj --workspace ws-…
$ memorylake boundary create --name 'Tern'"'"'s memory' --custom-id mlu-cmt-bnd --projects proj-… --workspace ws-…
```

One project holds Tern's memory; the boundary over it is what MemoryRouter attaches when the restarted agent asks.

### Step 3: the past

Three September sessions go in as DIRECT conversations (`conv create`, `conv msg append`, `conv cook-status`), and the
demo checks that each of the five settings came out as a fact:

```
    Facts extracted from the past sessions: 10
    ✓  target schema: analytics_v3         “The dashboards read from analytics_v3. (as of 2026-09-07)”
    ✓  batch size: 5,000 rows              “The warehouse backfill process must load data in batches of 5,000 rows…”
    ✓  alert channel: #pipeline-alerts     “Each alert in #pipeline-alerts includes the partition and the error li…”
    ✓  staging bucket: s3://hv-stage-a     “Backfills stage files in the s3://hv-stage-a bucket. (as of 2026-09-07…”
    ✓  write window: 01:00–05:00 UTC       “If a backfill run reaches 05:00 UTC, it pauses until 01:00 UTC the nex…”
  · 5/5 agreed settings are in Tern's memory
```

### Step 4: tonight

Every step is two messages appended in order (Mei's, then Tern's). When a step carries a change, a watcher starts:

```
$ memorylake conv consumed-messages conv-… --message conv-entry-… --workspace ws-…      # C: read yet?
$ memorylake conv fact-actions conv-… --project proj-… --message conv-entry-… --workspace ws-…   # D: written yet?
$ memorylake router chat --body out/body.json --model gpt-4o-mini --max-tokens 100 --temperature 0 \
    --boundary bnd-… 'Restarted mid-run. Which schema is tonight'"'"'s backfill writing to?'
```

The watcher polls both every half second and restarts Tern (one `router chat`, nothing in the body but the system
prompt) the moment the append returns (B), the moment `consumed-messages` is non-empty (C), and the moment
`fact-actions` is non-empty (D):

```
  step  2  +  4.3s  Mei: One change for tonight: write to the analytics_v4 schema, not analytics_v3. v3 is froze…
           ▶ target schema: analytics_v3 → analytics_v4. Restarting Tern at each rule's commit moment:
      ↻ B  appended  +  6.2s ( +1.9s after it was said)  restart → ⟲ target schema
          | Tonight's backfill is writing to the analytics_v3 schema.
             control, same restart: staging bucket (unchanged) ✓  “The staging bucket is s3://hv-stage-a.”
      ↻ C  read      + 24.2s (+19.9s after it was said)  restart → ⟲ target schema
          | Tonight's backfill is writing to the analytics_v3 schema.
      ↻ D  written   + 27.2s (+22.9s after it was said)  restart → ✓ target schema
          | Tonight's backfill is writing to the analytics_v4 schema.
```

### Step 5: the commit log

After the run, every message is looked up once more (`conv msg list`, then `consumed-messages` and `fact-actions`
per batch):

```
    32 messages, read in 7 extraction batches (m = Mei's message of a step, t = Tern's):
        batch  steps                     wrote        D says
        1      1m–1t (2 msgs)            —            committed: a later batch has its receipt
        2      2m–3t (4 msgs)            2 change(s)  committed: receipt
           ADD    “The analytics_v3 schema is frozen for the audit (as of 2026-10-19).”
           ADD    “Tonight's September order-events backfill writes to the analytics_v4 schema, not
                   analytics_v3 (as of 2026-10-19).”
        3      4m–7t (8 msgs)            1 change(s)  committed: receipt
           ADD    “The September order-events backfill uses a batch size of 2,000 rows instead of 5,000.
                   (as of 2026-10-19)”
        …
        5      11m–13m (5 msgs)          —            committed: a later batch has its receipt
  · 2 batch(es), 7 messages of progress chatter, wrote nothing and so never got a receipt: a rule that waits for every message's own receipt would keep them in the process forever

    `cook-status` sampled every 3 s: while steps were arriving (up to +66 s) cook_finished was true in 1 of 18 samples; true for good from +100 s, after the last step
```

Batch sizes follow the rhythm of the appends (2 to 8 messages here) and change from run to run.

### Step 6: tally

```
        restart at the commit moment of                  target schema        batch size     alert channel    right
        B  committed once the append returns                   ⟲ +1.9s           ⟲ +2.0s           ∅ +1.8s     0/3
        C  committed once extraction has read it              ⟲ +19.9s          ⟲ +16.6s          ∅ +11.3s     0/3
        D  committed once memory has written it               ✓ +22.9s          ✓ +21.9s          ✓ +17.4s     3/3
```

`⟲` is the value from before tonight's change; `∅` is "not on file" (the old rule was in memory, the agent would not
commit to it for this run).

### `log` and `ask`

`python3 demo.py log` prints the last night's commit log again. `python3 demo.py ask "<question>"` asks a restarted
Tern with nothing but its memory; after a run, all three of tonight's changes are written, so it knows them.

## Measured

<!-- measured-table -->
<!-- /measured-table -->

## Use it in your own app

- Commit a step — drop your in-process copy, advance your checkpoint — on `conv fact-actions --message <id>`, not on
  the append returning and not on `conv consumed-messages`. Read is not written.
- The receipt is per batch, not per message: one lookup answers for every message read with it. Look up the oldest
  message you still hold and release everything in its batch.
- Batches of chatter never get a receipt. Release them once a later batch has one, or you will hold them forever.
- Don't use `cook-status` as a commit point while the agent is busy: it is one flag for the whole conversation and
  stays false while messages keep arriving.
- Give the restarted agent the date and the run it is resuming. Extraction often writes tonight's change as a new
  fact next to the old rule ("the September backfill uses 2,000 rows instead of 5,000"), not as a revision of it;
  without knowing which run is "tonight", a model can read past it (see Measured).

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `this memorylake CLI has no router command` | Upgrade the CLI (v20261015 or later). |
| `MemoryRouter refused the call for lack of balance` | Router calls are billed; the commit moments and the log still run. Top up in the console. |
| `still extracting after 420 s` | Extraction is slow right now; the run continues and the settings check says what is missing. |
| A setting shows `·` in step 3 | Extraction left it out or wrote it in another language (it happens); the check reads English only. |
| `extraction had not written it 240 s after it was said` | D never got a receipt for that change in time; its column shows `—`. |
| C gets one right | The write landed while C's MemoryRouter call was in flight (read and written are a few seconds apart). It counts as it stands. |

## Files

| File | What it is |
|---|---|
| `demo.py` | The demo: CLI wrapper, setup and rebuild (`setup()`), ingestion and the settings check (`show_memory()`), the run (`run_night()`), the per-change watcher that restarts Tern at each commit moment (`ChangeWatch`), the `cook-status` sampler (`StatusSampler`), the commit log (`commit_log()`, `show_log()`), grading (`grade()`, `check_grader()`), `ask`, `log`, cleanup |
| `data/run.json` | The playbook, the three past sessions, the five settings (old and new values, the restart questions), tonight's 16 steps |
| `web/server.py`, `web/static/` | Browser companion (standard library server, no build step) |
| `out/` | `report.json` with every restart, reply, timing and batch; `out/runs/` keeps earlier reports (git-ignored) |
