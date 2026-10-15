# Compliance memory with audit trails

A runnable version of the MemoryLake use case
[*Give Compliance Teams Memory With Audit Trails Auditors Accept*](https://www.memorylake.ai/en/usecase/compliance-memory-for-audit-trails),
built entirely on the [`memorylake` CLI](https://github.com/memorylake-ai/memorylake-cli).

**Watch it run:** recording in progress.

**The problem.** An auditor reviewing an alert closure asks one question: *what did the analyst know when she closed
it?* Every record in a casebook says when something happened: "article dated 2026-09-10", "rating review,
2026-08-20". Most records also enter the casebook that day, so nobody notices there is a second date. Late entries are
different. A branch keeps paper rating forms and types them in weeks later. A screening inbox is cleared in October. A
false match is checked in September and written up in October. Each of these records has two dates: when it happened,
and when the casebook got it. An audit that reads only one of the two gets the findings wrong, and the two possible
mistakes point in opposite directions.

**What this demo shows.** Halden Mutual (a fictional bank) keeps its Q3 transaction-monitoring casebook in one
MemoryLake project: 21 records (policy, risk ratings, screening items and their resolutions, four alert closures),
each pinned word for word. The text says when the record happened; metadata adds `event_on`, `logged_on` and who
logged it. Four of the records are late entries (16 to 28 days). Today, 2026-10-15, internal auditor Mara Quist checks
every Q3 closure against two rules of policy TM-4: §3 (a high-risk customer's alert needs a second reviewer) and §5
(no closing an alert while an adverse-media or sanctions item is unresolved on the file).

| Check | Event clock: the dates in the records | Record clock: latest entry wins | Both clocks |
|---|---|---|---|
| A-1042 (Rhea, Orion Freight) §3 | **breach** ✗: rating high (a branch form dated 09-10) | **breach** ✗: rating high (a branch form typed in on 09-15) | ok: the file showed medium (09-01 review) |
| A-1042 §5 | **breach** ✗: the 09-10 article was open | ok | ok: the article reached the casebook on 10-06 |
| A-1059 (Rhea, Lusk Marine) §5 | **ok** ✗: the false match was cleared on 09-15 | breach | **breach**: the clearance was not logged until 10-01 |
| A-1063 (Tomas, Brightwater) §5 (control) | breach | breach | breach |
| Orion Freight's rating today | medium | **high** ✗: the 09-10 form, typed in on 10-08 | medium (10-02 review) |
| Findings | Rhea: A-1042 ×2 · Tomas: A-1063 | Rhea: A-1042 §3, A-1059 · Tomas: A-1063 | **Rhea: A-1059 · Tomas: A-1063** |

The event clock is hindsight. It blames Rhea for an article that reached the casebook three weeks after she closed
A-1042, and excuses her A-1059 closure because of a clearance nobody had written up yet. The record clock treats a
paper form typed in on 8 October as Orion's current rating, and on the day of A-1042 it picks the branch form typed in
on 09-15 over the compliance review of 09-01. An audit needs both clocks. The **record clock** decides what was on the
analyst's file that day, and the **event clock** decides which of those records was current. All three reads run the
same code. Only two choices differ: from which day a record counts as on file, and which of several records is the
current one. Brightwater is the control: nothing about it was logged late, so all three reads agree.

Step 6 writes the second clock where every reader will see it. Each late entry's text gets "(Late entry: logged
2026-10-06 by Sam Orrin, 26 days after the event.)" appended, with the old text kept in `metadata.previous` and both
versions in `fact trace`. Before the write-back no record contained "Late entry". Afterwards 4 do, and a read that only
knows what the text says gets 12 of 12 right.

```
setup ──────── proj create
intake ─────── fact add --project <p> '<record>' …   (21 records, one call)  + fact update --metadata {event_on, logged_on, by, kind, customer, rating | rule | resolves | signoff}
one clock ──── search '<what was on file about X when alert A was closed?>' --projects <p> --types fact --top-k 5   (per alert: the hits carry one date)
               fact list --projects <p>   (every record, every page)
               → event clock: on file from event_on, current = latest event_on
               → record clock: on file from logged_on, current = latest logged_on
both clocks ── the same records → on file from logged_on, current = latest event_on
write back ─── fact list --projects <p> --query 'Late entry'   (before)
               fact update <late entry> --text '<text> (Late entry: logged <day> by <who>, N days after the event.)' --metadata {…, previous}
               fact list --projects <p> --query 'Late entry'   (after)
               fact trace <late entry>  → ADD, UPDATE (old text kept) → the plain read again
```

There are no conversations and no extraction: every record is pinned word for word, and every read is plain code over
`fact list`. (We checked whether extraction keeps the two dates apart. Over three fresh projects, a late entry
appended with the day it was logged was stamped with the day it happened in 2 runs. In the third it was stamped with
the logged day, which made a stale rating look newer than the current one. An audit cannot rest on that.)

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
cd memorylake-usecases/audit-trail-memory
export MEMORYLAKE_API_KEY=sk-...           # China region: also export MEMORYLAKE_BASE_URL=https://app.memorylake.cn/openapi/memorylake
python3 demo.py
```

The first run takes about a minute and a half (one project, 21 facts, each with its metadata); a re-run takes about
40 seconds. Every run replays the morning of the audit: an earlier run's write-back is undone first, putting each late
entry back word for word from `metadata.previous`. With the key set, the demo logs into an isolated CLI profile under
`./.memorylake-demo/` and never touches your own `~/.memorylake`. Everything it creates has a `custom_id` starting with
`mlu-cat-`.

Then open the casebook as it stood on a day, or search it with a day in mind:

```bash
python3 demo.py asof 2026-09-18 orion      # what had happened, what was on file, and what each clock reads from it
python3 demo.py asof 2026-09-24            # every customer
python3 demo.py ask "Was anything known about Orion Freight's director?" --as-of 2026-09-18
python3 demo.py cleanup                    # deletes the project and every fact on it
```

```
  Orion Freight Ltd on 2026-09-18
    record     happened    logged      on file?  text
    ORN-R0     2025-11-03  2025-11-03  yes       Onboarding, 2025-11-03: Orion Freight Ltd rated low risk. Regio…
    ORN-R1     2026-08-20  2026-09-15  yes       Harbour Street branch rating review, 2026-08-20 (paper form): O…
    ORN-R2     2026-09-01  2026-09-02  yes       Rating review, 2026-09-01: Orion Freight Ltd set to medium risk…
    ORN-AM1    2026-09-10  2026-10-06  NO        Adverse media, article dated 2026-09-10: Orion Freight director…
    ORN-R3     2026-09-10  2026-10-08  NO        Harbour Street branch rating review, 2026-09-10 (paper form): O…
    A-1042     2026-09-18  2026-09-18  yes       Alert A-1042 (Orion Freight Ltd, outgoing wire of 48,200 dollar…
    event clock   rating high (ORN-R3) · open items ORN-AM1
    record clock  rating high (ORN-R1) · open items none
    both clocks   rating medium (ORN-R2) · open items none
    · 2 record(s) describe something that had happened but was not on file yet: ORN-AM1, ORN-R3
```

```
  “Was anything known about Orion Freight's director?” — top 8:
      #   record     happened    logged      on file?  text
      #1  ORN-AM1    2026-09-10  2026-10-06  NO        Adverse media, article dated 2026-09-10: Orion Freight dire…
      #2  ORN-AM1-X  2026-10-02  2026-10-02  later     Adverse media item on K. Varo (Orion Freight Ltd) closed on…
      #3  ORN-SAR    2026-09-30  2026-09-30  later     SAR filed for Orion Freight Ltd on 2026-09-30, citing the c…
      #4  ORN-R4     2026-10-02  2026-10-02  later     Rating review, 2026-10-02: Orion Freight Ltd set to medium …
      #5  ORN-R3     2026-09-10  2026-10-08  NO        Harbour Street branch rating review, 2026-09-10 (paper form…
      #6  A-1042     2026-09-18  2026-09-18  yes       Alert A-1042 (Orion Freight Ltd, outgoing wire of 48,200 do…
      #7  ORN-R2     2026-09-01  2026-09-02  yes       Rating review, 2026-09-01: Orion Freight Ltd set to medium …
      #8  POL-3      2026-07-01  2026-07-01  yes       Policy TM-4 §3 (in force 2026-07-01): an alert on a custome…
  · on 2026-09-18: 2 of these had happened but were not on file yet
```

`NO` = it had happened by that day but was not on file yet; `later` = it had not happened yet.

## Or run it in the browser

```bash
python3 web/server.py                # opens http://127.0.0.1:8765
```

Paste the key, connect, run. The page drives the same functions as `demo.py` and streams every `memorylake` command into a
terminal drawer as it runs. It shows the casebook by customer with both dates and the late entries marked; each
alert's search hits, with the ones not yet on file highlighted; the event-clock and record-clock reads with ✗ wherever
they disagree with both clocks; the comparison table and the file each analyst had on the day; and the write-back with
the `Late entry` census and `fact trace`. **Ask** searches the casebook as of any day, and opens it as it stood on a
day for one customer.

## What happens, step by step

### Steps 2–3: one project, 21 records, two dates each

`fact add` takes many texts in one call and returns them in order. `fact update --metadata` then attaches the two
dates and the record's kind to each (`fact add` takes no metadata):

```
    record     happened    logged                   text
    --------------------------------------------------------------------------------------------------------
    policy
    POL-3      2026-07-01  2026-07-01               Policy TM-4 §3 (in force 2026-07-01): an alert on a customer rated hi…
    POL-5      2026-07-01  2026-07-01               Policy TM-4 §5 (in force 2026-07-01): no alert may be closed while an…
    Orion Freight Ltd
    ORN-R0     2025-11-03  2025-11-03               Onboarding, 2025-11-03: Orion Freight Ltd rated low risk. Regional ha…
    ORN-R1     2026-08-20  2026-09-15  late +26 d   Harbour Street branch rating review, 2026-08-20 (paper form): Orion F…
    ORN-R2     2026-09-01  2026-09-02               Rating review, 2026-09-01: Orion Freight Ltd set to medium risk. The …
    ORN-AM1    2026-09-10  2026-10-06  late +26 d   Adverse media, article dated 2026-09-10: Orion Freight director K. Va…
    ORN-R3     2026-09-10  2026-10-08  late +28 d   Harbour Street branch rating review, 2026-09-10 (paper form): Orion F…
    A-1042     2026-09-18  2026-09-18               Alert A-1042 (Orion Freight Ltd, outgoing wire of 48,200 dollars) clo…
    ORN-SAR    2026-09-30  2026-09-30               SAR filed for Orion Freight Ltd on 2026-09-30, citing the customs fra…
    ORN-AM1-X  2026-10-02  2026-10-02               Adverse media item on K. Varo (Orion Freight Ltd) closed on 2026-10-0…
    ORN-R4     2026-10-02  2026-10-02               Rating review, 2026-10-02: Orion Freight Ltd set to medium risk after…
    Lusk Marine Supply
    LSK-R0     2025-06-12  2025-06-12               Onboarding, 2025-06-12: Lusk Marine Supply rated low risk. Ship chand…
    LSK-AM1    2026-09-08  2026-09-09               Adverse media, article dated 2026-09-08: a port-fuel smuggling report…
    LSK-AM1-X  2026-09-15  2026-10-01  late +16 d   Adverse media item on Lusk Marine closed: checked on 2026-09-15, the …
    A-1059     2026-09-24  2026-09-24               Alert A-1059 (Lusk Marine Supply, incoming wire of 61,300 dollars) cl…
    …
  · 4 late entries: the record says when it happened; only metadata.logged_on says when the casebook got it (LSK-AM1-X +16 d, ORN-AM1 +26 d, ORN-R1 +26 d, ORN-R3 +28 d)
```

### Step 4: one clock

What retrieval hands the auditor: a `search` per alert. The `←` marks come from `metadata.logged_on`. The text of a hit
carries only the day it happened:

```
  A-1042 · Orion Freight Ltd · closed 2026-09-18 by Rhea Ilves — top 5:
      #1  A-1042     2026-09-18  Alert A-1042 (Orion Freight Ltd, outgoing wire of 48,200 dollar…
      #2  ORN-R2     2026-09-01  Rating review, 2026-09-01: Orion Freight Ltd set to medium risk…
      #3  ORN-R0     2025-11-03  Onboarding, 2025-11-03: Orion Freight Ltd rated low risk. Regio…
      #4  ORN-R1     2026-08-20  Harbour Street branch rating review, 2026-08-20 (paper form): O…
      #5  ORN-AM1    2026-09-10  Adverse media, article dated 2026-09-10: Orion Freight director…   ← not on file until 2026-10-06
```

Then every record (`fact list`, every page), read through one clock at a time:

```
  read through the event clock (dates in the records)  (every record: `fact list`, every page)
    A-1050  Tomas Brandt  2026-09-16  rating low (CAL-R0) → §3 ok                              open items none → §5 ok
    A-1042  Rhea Ilves    2026-09-18  rating high (ORN-R3) → §3 ✗ needed a second reviewer     open items ORN-AM1 → §5 ✗ should have been escalated
    A-1059  Rhea Ilves    2026-09-24  rating low (LSK-R0) → §3 ok                              open items none → §5 ok
    A-1063  Tomas Brandt  2026-09-29  rating medium (BRW-R0) → §3 ok                           open items BRW-S1 → §5 ✗ should have been escalated
    rating today: Orion Freight Ltd medium (ORN-R4) · Lusk Marine Supply low (LSK-R0) · Brightwater Imports medium (BRW-R0) · Calder Textiles low (CAL-R0)
    → findings: Rhea Ilves: A-1042 §3, A-1042 §5 · Tomas Brandt: A-1063 §5

  read through the record clock (latest entry wins)  (every record: `fact list`, every page)
    A-1050  Tomas Brandt  2026-09-16  rating low (CAL-R0) → §3 ok                              open items none → §5 ok
    A-1042  Rhea Ilves    2026-09-18  rating high (ORN-R1) → §3 ✗ needed a second reviewer     open items none → §5 ok
    A-1059  Rhea Ilves    2026-09-24  rating low (LSK-R0) → §3 ok                              open items LSK-AM1 → §5 ✗ should have been escalated
    A-1063  Tomas Brandt  2026-09-29  rating medium (BRW-R0) → §3 ok                           open items BRW-S1 → §5 ✗ should have been escalated
    rating today: Orion Freight Ltd high (ORN-R3) · Lusk Marine Supply low (LSK-R0) · Brightwater Imports medium (BRW-R0) · Calder Textiles low (CAL-R0)
    → findings: Rhea Ilves: A-1042 §3, A-1059 §5 · Tomas Brandt: A-1063 §5
```

### Step 5: both clocks

The same records read through both clocks: on file from `logged_on`, current by `event_on`:

```
  A-1042 · Orion Freight Ltd · closed 2026-09-18 by Rhea Ilves
    on file that day: ORN-R0, ORN-R1, ORN-R2, A-1042
    happened by then but not on file yet: ORN-AM1 (logged 2026-10-06), ORN-R3 (logged 2026-10-08)
    rating medium (ORN-R2) → §3 ok                           open items none → §5 ok

  A-1059 · Lusk Marine Supply · closed 2026-09-24 by Rhea Ilves
    on file that day: LSK-R0, LSK-AM1, A-1059
    happened by then but not on file yet: LSK-AM1-X (logged 2026-10-01)
    rating low (LSK-R0) → §3 ok                              open items LSK-AM1 → §5 ✗ should have been escalated
  …
  check                                event clock   record clock  both clocks
  ----------------------------------------------------------------------------
  A-1042 TM-4 §3                       breach ✗      breach ✗      ok
  A-1042 TM-4 §5                       breach ✗      ok            ok
  A-1059 TM-4 §5                       ok ✗          breach        breach
  A-1063 TM-4 §5                       breach        breach        breach
  Orion Freight Ltd: rating today      medium        high ✗        medium
  Lusk Marine Supply: rating today     low           low           low
  Brightwater Imports: rating today    medium        medium        medium
  Calder Textiles: rating today        low           low           low
  (4 more checks: all three reads say ok)

  → event clock wrong on 3 of 12; record clock wrong on 2 of 12
    findings by the event clock   Rhea Ilves: A-1042 §3, A-1042 §5 · Tomas Brandt: A-1063 §5
    findings by the record clock  Rhea Ilves: A-1042 §3, A-1059 §5 · Tomas Brandt: A-1063 §5
    findings by both clocks       Rhea Ilves: A-1059 §5 · Tomas Brandt: A-1063 §5
```

A-1059 is a real breach even though the smuggling report turned out to be about another company. §5 is about what was
on the file when the alert was closed, and on 24 September the item was still open there.

### Step 6: write it back

```
    LSK-AM1-X now ends “… (Late entry: logged 2026-10-01 by Sam Orrin, 16 days after the event.)”
    ORN-AM1 now ends “… (Late entry: logged 2026-10-06 by Sam Orrin, 26 days after the event.)”
    ORN-R1 now ends “… (Late entry: logged 2026-09-15 by Lena Hart, 26 days after the event.)”
    ORN-R3 now ends “… (Late entry: logged 2026-10-08 by Lena Hart, 28 days after the event.)”

  `fact list --query 'Late entry'` (exact substring, every record): before 0
                                     after  4 — LSK-AM1-X, ORN-AM1, ORN-R1, ORN-R3

  fact trace ORN-AM1 (newest first) — MemoryLake's own record clock for this fact:
    UPDATE  MANUAL  2026-10-15T03:20:06  … Unresolved. (Late entry: logged 2026-10-06 by Sam Orrin, 26 days after the event.)
    ADD     MANUAL  2026-10-15T03:19:17  Adverse media, article dated 2026-09-10: Orion Freight director K. Varo is named in…

  a read that only knows what each record's text says (its event date, and a logged date if it states one):
    before the write-back: wrong on 3 of 12 (A-1042 §3, A-1042 §5, A-1059 §5) — the same as the event clock
    after:                 wrong on 0 of 12
    findings now: Rhea Ilves: A-1059 §5 · Tomas Brandt: A-1063 §5
```

`fact trace` is MemoryLake's own record clock: it says when *the memory* got each version, and how it was
written (`MANUAL` here: an API call, not extraction). In this demo the whole Q3 history is loaded today, so the ADD is today. On a re-run, `fact trace` also
shows an UPDATE pair for each earlier run (put back, then stamped again).

## Measured

| What | Result |
|---|---|
| Event clock wrong on A-1042 §3, A-1042 §5, A-1059 §5; record clock wrong on A-1042 §3, Orion's rating today; both clocks: findings A-1059 §5 + A-1063 §5 | 4 / 4 runs (from scratch, re-run, `--reset`, browser) |
| `fact list --query 'Late entry'` | 0 records before the write-back, 4 after, every run |
| Plain read of the text after the write-back | 12 / 12 every run |
| The not-yet-on-file article (ORN-AM1) in A-1042's search top 5 | shown at #5 in the runs we looked at; the ranking can move, and no answer depends on it |
| Time | 38–82 s |

## Wiring it into your own team

- **Write each record when it enters the casebook, not in a batch later.** Then `fact trace`'s ADD *is* the logged
  date. MemoryLake keeps it for every version, and nobody can backdate it. Here the history is loaded in one go, so the
  logged date has to come from metadata.
- **Keep the two dates apart from the start.** A connector that copies a paper form should write the form's date in
  the text and the day it was typed in as the logged date, never one date for both.
- **Ask both questions an audit asks.** "What was on the file that day?" uses the logged date. "Which record was
  current?" uses the event date. Answering both from either date alone gives the wrong findings in the table above.

## If something goes wrong

- **`the memorylake CLI is not on PATH`**: install it (above) or `export MEMORYLAKE_BIN=/path/to/memorylake`.
- **Search results differ on your run**: ranking moves between runs. Step 4 prints exactly what came back; the three
  reads do not use search at all.
- **`asof` says "not a date"**: days are `YYYY-MM-DD`.
- **Something half-created after an interrupted run**: `python3 demo.py --reset`, or `python3 demo.py cleanup`.

## Files

```
audit-trail-memory/
├── demo.py              # the CLI runner (every step is a plain `memorylake` command, echoed)
├── data/story.json      # 21 records (2 policy, 8 ratings, 3 screening items, 3 resolutions, 4 alert closures, 1 SAR), 4 of them late entries (fictional)
├── web/server.py        # local web companion: same functions, Server-Sent Events
├── web/static/          # no-build front end
└── out/runs.jsonl       # one line per run (git-ignored)
```
