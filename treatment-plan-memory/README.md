# Treatment plan memory for solo practitioners

A runnable version of the MemoryLake use case
[*Give Solo Practitioners Treatment Plan Memory That Holds Across Every Patient Visit*](https://www.memorylake.ai/en/usecase/treatment-plan-memory-for-solo-practitioners),
built entirely on the [`memorylake` CLI](https://github.com/memorylake-ai/memorylake-cli).

**Watch it run:** _videos are being recorded_

**The problem.** *The plan has evolved through three iterations. Today's AI scribe sees only this visit's notes.* Each new
version of a treatment plan is written from the last one. Anything the rewrite leaves out is gone from the plan, even though
nobody decided to drop it. The covering clinician is handed the current version, and that is all they see.

**What this demo shows.** Harbour Line Physio (fictional) is Mira Lindqvist's solo practice. Jonah Reyes, 54, has been coming
in for his right shoulder since August. Everything in his chart is pinned to his actor word for word, with `metadata` saying
what each entry is: a plan version and the precautions it carries, a precaution being imposed / revised / reaffirmed / lifted,
or a visit note.

| When | What goes into Jonah's memory |
|---|---|
| 2026-08-04 | **Plan v1** with three precautions: **P1** no loaded overhead work until the orthopaedic review clears it · **P2** he takes apixaban, so no dry needling · **P3** exercise pain ceiling 3/10 |
| 2026-08-25 | P3 **changed on the record**: 3/10 → 5/10, with the reason. **Plan v2**, drafted by the AI scribe from v1 and the visit, carries P3 only |
| 2026-09-08 | Visit: ortho review moved to 2026-10-20, *"the overhead restriction stays until then"* (P1 repeated) |
| 2026-09-15 | Visit: *"GP renewed apixaban for six more months"* (P2 repeated) |
| 2026-09-29 | **Plan v3**, drafted the same way: next phase adds **overhead dumbbell press** and **dry needling** |
| 2026-10-14, today | Mira goes on leave; Theo Adeyemi PT covers. *"Follow the current plan."* |

Then:

1. **The usual handoff.** Theo takes the newest plan version. It lists one precaution (pain ceiling 5/10), and his first
   session, straight from it, has 5 items: **overhead dumbbell press** and **dry needling** among them.
2. **The audit.** One `fact list --actors <Jonah>`, then plain code, no model: every precaution from the day it was imposed,
   through every plan version written since, and every revise / reaffirm / lift on record. **P1 and P2 were never lifted.
   Both fell out in plan v2 (the scribe's draft), and both were repeated at a visit after that.** P3 changed on the record,
   and v3 carries the new value. **2 of the 5 session items are put on hold.**
3. **A built-in control.** The same audit, run as of 2026-08-04 (the day v1 was written), finds 3/3 carried and flags
   nothing. Run as of 2026-08-25, it would have caught both drops on the day v2 was signed.
4. **Why a keyword check doesn't catch it.** `fact list --query overhead` finds 4 entries, **plan v3 among them**: *"add
   overhead dumbbell press"*. "Does the current plan mention overhead?" Yes, but a mention is not a precaution.
   `--query apixaban` finds 3 entries, none of them in v3.
5. **The handoff is written back** to the practice project, with the id of every chart entry it rests on in
   `metadata.basis`. Each one is then read back (7/7).

```
chart     ── fact add --actor <Jonah> 'Plan v1 …' 'Precaution P1 …' … (one call) + fact update --metadata (kind, date, version, precautions, pid, action)
handoff   ── fact list --actors <Jonah> → newest plan → its "next" items
audit     ── same list: per precaution, impose → revise / reaffirm / lift, and each plan version since: carried ✓ / ✗
keyword   ── fact list --actors <Jonah> --query overhead | apixaban
write     ── fact add --project <practice> 'Covering handoff …' + metadata.basis = [chart fact ids] → read back
```

## Prerequisites

- **Python 3.9+** (standard library only, nothing to `pip install`)
- **The `memorylake` CLI** on your `PATH`:
  ```bash
  curl -fsSL https://raw.githubusercontent.com/memorylake-ai/memorylake-cli/main/scripts/install.sh | sh
  ```
- **A MemoryLake API key.** Sign up at [app.memorylake.ai](https://app.memorylake.ai), open **API Keys**, create one. A free
  personal account is enough: nothing here needs an agent or a model call.

## Run it

```bash
git clone https://github.com/memorylake-ai/memorylake-usecases.git
cd memorylake-usecases/treatment-plan-memory
export MEMORYLAKE_API_KEY=sk-...           # China region: also export MEMORYLAKE_BASE_URL=https://app.memorylake.cn/openapi/memorylake
python3 demo.py
```

About a minute the first time, about 25 s on a re-run, which reads back what is already there. With the key set, the demo
logs into an isolated CLI profile under `./.memorylake-demo/` and never touches your own `~/.memorylake`. Everything it creates
has a `custom_id` starting with `mlu-tpm-`.

```bash
python3 demo.py audit 2026-08-25                 # the audit as of any date (here: the day plan v2 was signed)
python3 demo.py plan v1                          # one plan version as pinned, with the precautions it carries
python3 demo.py ask "Can Jonah press overhead?"  # search's top 8 over Jonah's chart
python3 demo.py --reset                          # delete everything the demo created, then replay
python3 demo.py cleanup                          # delete everything the demo created
```

## Or run it in the browser

```bash
python3 web/server.py                # opens http://127.0.0.1:8765
```

Paste the key, connect, run. The page drives the same functions as `demo.py` and streams every `memorylake` command into a
terminal drawer as it runs: the chart's timeline, the current plan and Theo's session, the audit (control, today, the
session check, the keyword reading) and the handoff with every cited entry. **Ask the chart** takes any question, opens any
plan version, or runs the audit as of a date.

## What happens, step by step

Output below is from real runs (ids shortened).

### Steps 2–3 — the practice, the patient, the chart

Jonah is an actor, and his chart lives on him. The practice project only holds the covering handoff. All 14 chart entries go
in with one `fact add`, then one `fact update --metadata` each (`fact add` takes no metadata):

```
$ memorylake fact add --actor actor-7123… 'Plan v1 (2026-08-04, Mira Lindqvist PT, initial evaluation): right shoulder rotator cuff tendinopathy, … Precautions: no loaded overhead work until the orthopaedic review clears it; Jonah takes apixaban, so no dry needling and no instrument-assisted soft-tissue work; exercise pain ceiling 3/10.' 'Precaution P1 (2026-08-04, …' …
$ memorylake fact update fact-ed8a… --actor actor-7123… --metadata '{"key": "plan:v1", "kind": "plan", "version": 1, "date": "2026-08-04", "author": "mira", "drafted_by": "mira", "precautions": ["P1", "P2", "P3"], "pain_ceiling": "3/10", "next": [{"item": "Isometric external rotation, 5 x 30 s", "tags": []}, …]}'
  · … and 13 more `fact update --metadata` the same way (kind, date, version, precaution id, action)
$ memorylake fact list --actors actor-7123… --page-size 50

  3 plan versions · 4 precaution entries · 6 visit notes · 1 handoff note

    2026-08-04  plan         plan v1 (written by Mira) — carries P1, P2, P3
    2026-08-04  P1 impose    no loaded overhead work above shoulder height until the orthopaedic review clears it - suspected partia…
    2026-08-04  P2 impose    Jonah takes apixaban, an anticoagulant - no dry needling and no instrument-assisted soft-tissue work (I…
    2026-08-04  P3 impose    exercise pain ceiling 3/10 - stop the set if pain goes above it.
    2026-08-11  visit        pain 2/10 during banded external rotation, settled by the next morning. Sleeping on the left side only.
    2026-08-25  plan         plan v2 (drafted by the AI scribe, signed by Mira) — carries P3
    2026-08-25  P3 revise    exercise pain ceiling raised from 3/10 to 5/10 - pain settles within 24 hours after every session.
    2026-08-25  visit        MRI report in - partial-thickness supraspinatus tear, no full-thickness tear. Orthopaedic review booked…
    2026-09-08  P1 reaffirm  orthopaedic review moved to 2026-10-20, the surgeon is on leave. The overhead restriction stays until t…
    2026-09-15  P2 reaffirm  Jonah's GP renewed apixaban for six more months (atrial fibrillation). He bruises easily on the right a…
    2026-09-29  plan         plan v3 (drafted by the AI scribe, signed by Mira) — carries P3
    2026-09-29  visit        external rotation strength 4/5, sleeping on the right side most nights. Tender trigger points in the ri…
    2026-10-06  visit        upper trapezius trigger points still there. Rows and external rotation at 2 kg, pain 3/10, gone by even…
    2026-10-14  handoff      on leave 2026-10-15 to 2026-10-29. Theo Adeyemi PT covers Jonah; first covering visit 2026-10-16. Follo…
```

### Step 4 — the handoff, the usual way

```
    current plan: v3 (2026-09-29, drafted by the AI scribe, signed by Mira)
    precautions it carries: P3 Exercise pain ceiling 5/10

    Theo's first session (2026-10-16), straight from plan v3:
      ✓ Overhead dumbbell press, 2 kg
      ✓ Dry needling, right upper trapezius
      ✓ Farmer carries
      ✓ Seated rows
      ✓ Dumbbell external rotation, 2 kg

$ memorylake search "Can we do dry needling for Jonah's upper trapezius?" --actors actor-7123… --types fact --top-k 3
       1  P2 impose      Precaution P2 (2026-08-04, Mira Lindqvist PT): Jonah takes apixaban, an anticoagulant - no dry need…
       2  P2 reaffirm    Visit 2026-09-15 (Mira Lindqvist PT): Jonah's GP renewed apixaban for six more months (atrial fibri…
       3  plan v3        Plan v3 (2026-09-29, drafted by the AI scribe from plan v2 and today's visit, signed Mira Lindqvist…
```

If Theo thinks to ask, the chart has the answer: `search` ranks the precaution first. But the plan he was told to follow
gives him no reason to ask.

### Step 5 — audit every precaution through every version

```
    control — the same audit on 2026-08-04, the day plan v1 was written:
    P1 No loaded overhead work                 2026-08-04  v1✓                never     ✓ carried
    P2 Anticoagulant: no dry needling / IASTM  2026-08-04  v1✓                never     ✓ carried
    P3 Exercise pain ceiling (3/10)            2026-08-04  v1✓                never     ✓ carried
    ✓ 3/3 carried — the audit does not flag a plan that is right

    on 2026-08-25, the day plan v2 was signed, it would already have said: P1 dropped, P2 dropped

    today (2026-10-14):
    current plan: v3 (2026-09-29) · carries P3
    precaution                                 imposed     versions since     lifted?   verdict
    P1 No loaded overhead work                 2026-08-04  v1✓ v2✗ v3✗        never     ✗ DROPPED
    P2 Anticoagulant: no dry needling / IASTM  2026-08-04  v1✓ v2✗ v3✗        never     ✗ DROPPED
    P3 Exercise pain ceiling (5/10)            2026-08-04  v1✓ v2✓ v3✓        never     ✓ carried

    next session from plan v3, checked against what is in force:
      ✗ hold  Overhead dumbbell press, 2 kg          breaks P1
      ✗ hold  Dry needling, right upper trapezius    breaks P2
      ✓ ok    Farmer carries
      ✓ ok    Seated rows
      ✓ ok    Dumbbell external rotation, 2 kg

    ✗ P1 No loaded overhead work: imposed 2026-08-04, never lifted. It fell out in plan v2 (2026-08-25, the AI scribe's draft) — and was repeated after that: 2026-09-08: orthopaedic review moved to 2026-10-20, …
    ✗ P2 Anticoagulant: no dry needling / IASTM: imposed 2026-08-04, never lifted. It fell out in plan v2 (2026-08-25, the AI scribe's draft) — and was repeated after that: 2026-09-15: Jonah's GP renewed apixaban …
    ✓ P3 Exercise pain ceiling: changed on the record 2026-08-25 → 5/10 (“pain settles within 24 hours after every session.”); plan v3 carries 5/10

$ memorylake fact list --actors actor-7123… --query overhead --page-size 50
$ memorylake fact list --actors actor-7123… --query apixaban --page-size 50
    “overhead”: 4 entries (plan v3, P1 reaffirm, P1 impose, plan v1) · in plan v3: yes
    “apixaban”: 3 entries (P2 reaffirm, P2 impose, plan v1) · in plan v3: no

  → “does the current plan mention overhead?” — yes: plan v3 says “add overhead dumbbell press”. A mention is not a precaution; only the precaution's own record says whether it was lifted.

  → 2 of 3 precautions were dropped without anyone lifting them; 2 of 5 items in Theo's first session break them.
```

The rule is the one a clinician would use: a precaution stays in force until someone lifts it on the record. A precaution
that is missing from a newer plan has not been lifted. A changed value (P3) counts only when the change itself is on the
record, and then the plan has to carry the new value. If v3 had carried 3/10, the audit would mark P3 as a stale value.

### Step 6 — write the handoff back, with its sources

```
$ memorylake fact add --project proj-99c0… 'Covering handoff for Jonah Reyes (2026-10-14, for Theo Adeyemi PT): precautions in force - P1 No loaded overhead work (missing from the current plan); P2 Anticoagulant: no dry needling / IASTM (missing from the current plan); P3 Exercise pain ceiling 5/10. Hold from plan v3: Overhead dumbbell press, 2 kg (breaks P1); Dry needling, right upper trapezius (breaks P2). Session 2026-10-16: Farmer carries, Seated rows, Dumbbell external rotation, 2 kg.'
$ memorylake fact update fact-ece0… --project proj-99c0… --metadata '{"kind": "cover-handoff", "key": "cover:2026-10-14", …, "basis": ["fact-d0f2…", "fact-0270…", …]}'

      ← fact-d0f2…  plan v3        Plan v3 (2026-09-29, drafted by the AI scribe from plan v2 and today's visit, signed Mira…
      ← fact-0270…  P1 impose      Precaution P1 (2026-08-04, Mira Lindqvist PT): no loaded overhead work above shoulder hei…
      ← fact-a953…  P1 reaffirm    Visit 2026-09-08 (Mira Lindqvist PT): orthopaedic review moved to 2026-10-20, the surgeon…
      ← fact-2413…  P2 impose      Precaution P2 (2026-08-04, Mira Lindqvist PT): Jonah takes apixaban, an anticoagulant - n…
      ← fact-cc68…  P2 reaffirm    Visit 2026-09-15 (Mira Lindqvist PT): Jonah's GP renewed apixaban for six more months (at…
      ← fact-0d1b…  P3 impose      Precaution P3 (2026-08-04, Mira Lindqvist PT): exercise pain ceiling 3/10 - stop the set …
      ← fact-…      P3 revise      Precaution P3 changed (2026-08-25, Mira Lindqvist PT): exercise pain ceiling raised from …

    ✓ 7/7 cited chart entries read back
```

## Measured

_filled in after the recordings_

## Wiring it into your own practice

- **Make precautions their own entries**, not just a line inside the plan: one fact when a precaution is imposed, with an id
  in `metadata`. Changing or lifting it is another fact (`action: revise | lift`) with the date and the reason. A visit note
  that repeats it gets `action: reaffirm`.
- **When a plan version is written** (by you or by a scribe), record in `metadata` which precaution ids it carries. That is
  the one field the audit reads; the text stays free-form.
- **At every handoff, and before signing a drafted plan**, run the audit: a precaution in force but missing from the new plan
  was dropped, not lifted. Running it as of the signing date (`audit 2026-08-25`) would have caught this one six weeks early.
- **Don't audit by keyword.** A plan that says "add overhead press" mentions overhead. Only the precaution's own record says
  whether it is still in force.
- `search` is good for "what do we know about …?", and here it ranks the precaution first. But it only helps someone who
  already suspects there is something to ask.

## If something goes wrong

- **`the memorylake CLI is not on PATH`**: install it (above) or `export MEMORYLAKE_BIN=/path/to/memorylake`.
- **Something half-created after an interrupted run**: `python3 demo.py --reset`, or `python3 demo.py cleanup`. Cleanup
  deletes the patient's facts before the actor itself (an actor deleted first leaves its facts readable and undeletable).
- **`the demo data does not exist yet`** from `plan` / `audit` / `ask`: run `python3 demo.py` first.

## Files

```
treatment-plan-memory/
├── demo.py              # the CLI runner (every step is a plain `memorylake` command, echoed)
├── data/story.json      # 3 plan versions, 4 precaution entries, 6 visit notes, 1 handoff note (fictional, plain ASCII)
├── web/server.py        # local web companion: same functions, Server-Sent Events
├── web/static/          # no-build front end
└── out/runs.jsonl       # one line per run (git-ignored)
```

*Fictional practice, clinicians and patient. This is a demo of memory, not clinical guidance.*
