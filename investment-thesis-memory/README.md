# Investment thesis memory for portfolio managers

A runnable version of the MemoryLake use case
[*Give Portfolio Managers Investment Memory That Tracks Every Thesis*](https://www.memorylake.ai/en/usecase/investment-memory-for-portfolio-managers),
built entirely on the [`memorylake` CLI](https://github.com/memorylake-ai/memorylake-cli).

**Watch it run:** videos are being recorded.

**The problem.** Research is filed by holding: one folder, one notebook, one AI research thread per position. A thesis rests
on premises, and premises rest on other companies — a big customer, a tenant, a foundry. When news about one of those
companies breaks, it gets written down under the holding the analyst was working on that day. Every other holding whose
thesis rests on the same company never hears about it. Each holding's memory is complete and correct, and the review that
reads it one holding at a time is blind.

**What this demo shows.** Ardent Hollow Partners (fictional) runs a seven-stock book. Each holding has its own MemoryLake
project. Every record is one fact, word for word, with metadata: the holding, who logged it, when — and for a **premise**,
the company it depends on (`entity`) and the events that would break it (`breaks_on`) or bear it out (`holds_on`); for a
**note**, the companies it is about (`entities`) and the events it reports (`events`).

On September 30 an analyst filed a note under **Corvane Robotics**: its largest customer, Halvern Logistics, will build its
warehouse automation and software in-house from 2027, freeze new third-party technology contracts, and leave its leased
Rotterdam hub. The same premise check, run two ways:

| Read | Flagged | Why |
|---|---|---|
| **Holding by holding** — search each holding's project, check its premises against its own memory | **1 of 7** (Corvane, 12%) | Tessaly's memory says the Halvern migration is on schedule (Aug 12); Brightmoor's says renewal talks start in Q4 (Jul 29). Both are true — and out of date |
| **The whole book** — the same check, every premise against every note about the company it depends on | **3 of 7** (32%) | Tessaly's P1 (Halvern moves 60 warehouses onto its software) and Brightmoor's P1 (Halvern renews the Rotterdam lease) break on the Corvane note — filed under a different holding |

Plus two things the whole-book read sees that no single holding can:

- **Tessaly's conviction was reaffirmed "high" on October 7** — seven days after the note that breaks its main premise was
  already in memory, under Corvane.
- **The control**: the same check as of 2026-09-29, the day before the news, flags nothing — but the concentration is
  already visible: Halvern Logistics carries premises of three holdings, 32% of the book (and Feldspar Foundry two, 18%).
  The audit does not need hindsight.

Then it is written back: each blind holding gets a cross-reference (`fact add`, with `xref_of` pointing at the original
note) and every thesis with a broken premise gets `review: premise-broken` in its metadata. The holding-by-holding review
now flags three of seven, and Tessaly's own search returns the cross-reference first.

```
setup ────────── proj create  "<TICKER> <name>" × 7   (one project per holding)
intake ───────── fact add --project <holding> '<record>' …   + fact update --metadata {holding, kind, logged_on, entity, breaks_on, holds_on, entities, events, …}
holding review ─ search '<is the thesis intact?>' --projects <one holding>  × 7 · premise check over that holding's memory
whole book ───── fact list --projects <holding> × 7  → the same premise check over all of it; shared dependencies; control date
write back ───── fact add --project <blind holding> '<cross-reference>' (xref_of) · fact update --metadata {…, review: premise-broken}
```

The premise check is plain code over `fact list` and metadata, not a model: the same memory gives the same answer every time.
The only difference between the two reads is **which projects it reads**. No conversations and no extraction: every record
is pinned word for word.

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
cd memorylake-usecases/investment-thesis-memory
export MEMORYLAKE_API_KEY=sk-...           # China region: also export MEMORYLAKE_BASE_URL=https://app.memorylake.cn/openapi/memorylake
python3 demo.py
```

About two minutes the first time (seven projects, 31 facts, each with its metadata), about a minute on a re-run, which reads
back what is already there. With the key set, the demo logs into an isolated CLI profile under `./.memorylake-demo/` and
never touches your own `~/.memorylake`. Everything it creates has a `custom_id` starting with `mlu-ivm-`.

```bash
python3 demo.py exposure                                         # the premise check today, both ways, and the shared dependencies
python3 demo.py audit 2026-09-29                                 # the same as of any date
python3 demo.py ask "What do we know about Halvern Logistics?"   # search's top 8 across every holding, labelled by where it was filed
python3 demo.py --reset                                          # delete everything the demo created, then replay
python3 demo.py cleanup                                          # delete everything the demo created
```

## Or run it in the browser

```bash
python3 web/server.py                # opens http://127.0.0.1:8765
```

Paste the key, connect, run. The page drives the same functions as `demo.py` and streams every `memorylake` command into a
terminal drawer as it runs: the seven projects, every record with its metadata, the holding-by-holding review, the
whole-book check with the shared dependencies and the control date next to it, and the write-back. **Ask the book** takes
any question across every holding, or runs the premise check as of a date.

## What happens, step by step

Output below is from a real run (ids shortened).

### Steps 2–3 — one project per holding; every record, one fact each

```
$ memorylake proj create --name 'CRVN Corvane Robotics' --custom-id mlu-ivm-crvn --description '…'
  · … and 6 more `proj create` the same way (TSLY, BMLR, QLNS, VRDA, OSTP, MRWD)

    CRVN  Corvane Robotics              12%   Theo Ambrose     proj-9d70…
    TSLY  Tessaly Freight Software      11%   Theo Ambrose     proj-baf4…
    BMLR  Brightmoor Logistics REIT      9%   Joss Whitcombe   proj-8571…
    QLNS  Quillon Sensors               10%   Mina Okafor      proj-753d…
    VRDA  Veridane Motion                8%   Mina Okafor      proj-31e5…
    OSTP  Ostia Payments                13%   Mina Okafor      proj-b712…
    MRWD  Marrowind Energy               9%   Joss Whitcombe   proj-ef82…
                                        72%   of the book in these seven

$ memorylake fact add --project proj-9d70… 'Corvane Robotics thesis (Mar 11): …' 'Corvane premise P1: Halvern Logistics keeps rolling Corvane pickers out across its European sites; Halvern is 41 percent of the backlog. The thesis breaks if Halvern builds its own robots or freezes third-party orders.' …
$ memorylake fact update fact-27b7… --project proj-9d70… --metadata '{"key": "thesis:CRVN-T", "record": "CRVN-T", "holding": "CRVN", "kind": "thesis", "logged_by": "Theo Ambrose", "logged_on": "2026-03-11", "conviction": "high"}'
  · … then the same `fact add` + `fact update --metadata` for every other holding's records

  29 records logged before today (2026-10-14):
    CRVN   6 records   2 premise · 1 thesis · 1 earnings note · 1 news note · 1 conviction update
    TSLY   5 records   2 premise · 1 thesis · 1 earnings note · 1 conviction update
    BMLR   5 records   2 premise · 1 thesis · 1 earnings note · 1 conviction update
    …

  CRVN-N1 2026-09-30 — filed under CRVN by Theo Ambrose:
    “Halvern Logistics capital markets day (Sep 30): from 2027 Halvern will build its warehouse automation and software
     in-house, freeze new third-party technology contracts, and leave its leased Rotterdam hub when the lease ends in March
     2027. For Corvane, about 41 percent of the backlog is at risk.”
```

A premise's metadata, for example Tessaly's P1: `{"entity": "Halvern Logistics", "breaks_on": ["contract-freeze",
"in-sourcing"], "holds_on": ["migration-on-schedule"]}`. The Corvane note's: `{"entities": ["Halvern Logistics"], "events":
["in-sourcing", "contract-freeze", "lease-exit"]}`. `fact add` takes no metadata, so each fact gets a `fact update
--metadata` right after. On a re-run, facts are matched by the `key` in their metadata, so nothing is pinned twice.

### Step 4 — the weekly review, holding by holding

```
$ memorylake search 'Is the Corvane Robotics thesis still intact? Any new risk to it?' --projects proj-9d70… --types fact --top-k 8
$ memorylake search 'Is the Tessaly Freight Software thesis still intact? Any new risk to it?' --projects proj-baf4… --types fact --top-k 8
  …

  CRVN — top 3 of 6:
      #1  CRVN-N1  2026-09-30  Halvern Logistics capital markets day (Sep 30): from 2027 Halvern will build its warehouse …
  TSLY — top 3 of 5:
      #1  TSLY-T   2026-04-02  Tessaly Freight Software thesis (Apr 2): the warehouse-management platform that mid-size 3P…
      #2  TSLY-C1  2026-10-07  Tessaly conviction reaffirmed high (Oct 7): Q2 was clean and the Halvern migration is on sc…
      #3  TSLY-E1  2026-08-12  Tessaly Q2 (Aug 12): net revenue retention 119 percent, ARR up 34 percent; the Halvern migr…
  …

  the premise check, each holding against its own memory:
    holding                            wt  conviction        premises
    CRVN Corvane Robotics             12%  medium (10-02)    P1 Halvern Logistics: ✗ broken (CRVN-N1 2026-09-30)
                                                             P2 Corvane Robotics: ✓ holding (CRVN-E1 2026-08-05)  ← FLAGGED
    TSLY Tessaly Freight Software     11%  high (10-07)      P1 Halvern Logistics: ✓ holding (TSLY-E1 2026-08-12)
                                                             P2 Tessaly Freight Software: ✓ holding (TSLY-E1 2026-08-12)
    BMLR Brightmoor Logistics REIT     9%  high (09-08)      P1 Halvern Logistics: ✓ holding (BMLR-E1 2026-07-29)
                                                             P2 Brightmoor Logistics REIT: ✓ holding (BMLR-E1 2026-07-29)
    QLNS Quillon Sensors              10%  high (09-18)      P1 Feldspar Foundry: ✓ holding (QLNS-N1 2026-09-17)
    VRDA Veridane Motion               8%  medium (05-06)    P1 Feldspar Foundry: · untested
    OSTP Ostia Payments               13%  high (03-25)      P1 Ostia Payments: ✓ holding (OSTP-E1 2026-08-07)
    MRWD Marrowind Energy              9%  medium (06-10)    P1 Marrowind Energy: ✓ holding (MRWD-E1 2026-09-24)

  → holding by holding: 1 of 7 holdings flagged (12% of the book)
```

Nothing here is wrong. Search returns that holding's own records, and the check reads them correctly: the latest note on
Halvern in Tessaly's memory says the migration is on schedule.

### Step 5 — the same check over the whole book

```
$ memorylake fact list --projects proj-9d70… --page-size 50
$ memorylake fact list --projects proj-baf4… --page-size 50
  … (one per holding, every page)

    CRVN Corvane Robotics             12%  medium (10-02)    P1 Halvern Logistics: ✗ broken (CRVN-N1 2026-09-30)
                                                             P2 Corvane Robotics: ✓ holding (CRVN-E1 2026-08-05)  ← FLAGGED
    TSLY Tessaly Freight Software     11%  high (10-07)      P1 Halvern Logistics: ✗ broken (CRVN-N1 2026-09-30, filed under CRVN)
                                                             P2 Tessaly Freight Software: ✓ holding (TSLY-E1 2026-08-12)  ← FLAGGED
    BMLR Brightmoor Logistics REIT     9%  high (09-08)      P1 Halvern Logistics: ✗ broken (CRVN-N1 2026-09-30, filed under CRVN)
                                                             P2 Brightmoor Logistics REIT: ✓ holding (BMLR-E1 2026-07-29)  ← FLAGGED
    QLNS Quillon Sensors              10%  high (09-18)      P1 Feldspar Foundry: ✓ holding (QLNS-N1 2026-09-17)
    VRDA Veridane Motion               8%  medium (05-06)    P1 Feldspar Foundry: ✓ holding (QLNS-N1 2026-09-17, filed under QLNS)
    OSTP Ostia Payments               13%  high (03-25)      P1 Ostia Payments: ✓ holding (OSTP-E1 2026-08-07)
    MRWD Marrowind Energy              9%  medium (06-10)    P1 Marrowind Energy: ✓ holding (MRWD-E1 2026-09-24)

  → the whole book: 3 of 7 holdings flagged (32% of the book)
  → 2 of those broken premises rest on a note their own holding's memory does not hold:
      TSLY P1 (Halvern Logistics) ← CRVN-N1 2026-09-30, filed under CRVN
      BMLR P1 (Halvern Logistics) ← CRVN-N1 2026-09-30, filed under CRVN

  shared dependencies — companies more than one holding's premises rest on:
    company the premises depend on   holdings            book  status
    Halvern Logistics                CRVN, TSLY, BMLR     32%  3 premise(s) broken
    Feldspar Foundry                 QLNS, VRDA           18%  2 holding

  → TSLY conviction reaffirmed high on 2026-10-07 (TSLY-C1, Theo Ambrose) — 7 days after CRVN-N1 was in memory under CRVN:
      “Tessaly conviction reaffirmed high (Oct 7): Q2 was clean and the Halvern migration is on schedule. Add on weakness.”

  control — the same book-wide check as of 2026-09-29, the day before the news:
    0 of 7 holdings flagged; already visible: Halvern Logistics under 3 holdings (CRVN, TSLY, BMLR), 32% of the book; Feldspar Foundry under 2 holdings (QLNS, VRDA), 18% of the book
```

It cuts both ways: Veridane's Feldspar premise is untested in its own memory and borne out by a note filed under Quillon.
Cross-project `search` results carry no project id, so the demo reads each holding's `fact list` and labels every record
with the holding it was filed under.

### Step 6 — write it back

```
$ memorylake fact add --project proj-baf4… 'Cross-reference (2026-10-14) from CRVN-N1, filed 2026-09-30 under Corvane Robotics: Halvern Logistics capital markets day (Sep 30): … This breaks Tessaly Freight Software premise P1.'
$ memorylake fact update fact-c3b0… --project proj-baf4… --metadata '{"key": "thesis:TSLY-X1", "record": "TSLY-X1", "holding": "TSLY", "kind": "xref", "logged_by": "Rhea Castellan", "logged_on": "2026-10-14", "xref_of": "CRVN-N1", "entities": ["Halvern Logistics"], "events": ["in-sourcing", "contract-freeze", "lease-exit"]}'
$ memorylake fact update fact-27b7… --project proj-9d70… --metadata '{…, "review": "premise-broken", "broken": ["P1"], "flagged_on": "2026-10-14"}'
  · … and 2 more the same way (TSLY, BMLR)

  cross-references filed: TSLY-X1 ← CRVN-N1 (TSLY P1); BMLR-X1 ← CRVN-N1 (BMLR P1)
  theses marked premise-broken: CRVN (P1), TSLY (P1), BMLR (P1)

  the weekly review again, each holding against its own memory — now holding the cross-references:
    TSLY Tessaly Freight Software     11%  high (10-07)      P1 Halvern Logistics: ✗ broken (TSLY-X1 2026-10-14)          ← FLAGGED
    BMLR Brightmoor Logistics REIT     9%  high (09-08)      P1 Halvern Logistics: ✗ broken (BMLR-X1 2026-10-14)          ← FLAGGED
    …
  → holding by holding, after the write-back: 3 of 7 holdings flagged (32% of the book)

$ memorylake search 'Is the Tessaly Freight Software thesis still intact? Any new risk to it?' --projects proj-baf4… --types fact --top-k 8
      #1  TSLY-X1  2026-10-14  Cross-reference (2026-10-14) from CRVN-N1, filed 2026-09-30 under Corvane Robotics: Halvern…
  → the cross-reference is hit #1
```

The cross-reference copies the original note's `entities` and `events`, so any reader scoped to that one holding — the
premise check, or an AI tool's search — now sees it. `fact update --metadata` replaces the whole object, so each thesis
keeps its old keys and gains `review`, `broken` and `flagged_on`; its text stays word for word.

## Measured

| What | Result |
|---|---|
| Holding by holding 1 / 7 flagged; whole book 3 / 7 (32%); 2 broken premises filed under another holding | every run (deterministic over `fact list`) |
| Tessaly reaffirmed high 7 days after the note; control on 2026-09-29 flags 0 with Halvern at 32% | every run |
| After the write-back: holding by holding 3 / 7; the cross-reference is Tessaly's search hit #1 | every run so far |
| Time | 111–157 s from scratch, 60 s re-run |

The search lines are what `search` returned; the demo prints whatever comes back. Only the order of each holding's own
records moves between runs — search is never what decides whether a premise is broken.

## Wiring it into your own book

- **Intake**: one fact per record, verbatim (`fact add --project <holding>`). When a thesis is written, write each premise
  as its own record and ask for two fields: *which company does this rest on?* and *what would break it?* Store them as
  `entity` and `breaks_on` in `metadata`.
- **Notes**: tag every note with the companies it is about (`entities`) and what happened (`events`), whatever holding it is
  filed under. That tag is what lets a note reach the other holdings.
- **Reading**: for the weekly review, read the whole book (`fact list --projects <p>` for each holding, every page) and test
  each holding's premises against every note about their entity. `check` in `demo.py` is that function.
- **Write-back**: when a note breaks a premise elsewhere, file a cross-reference into that holding (`xref_of`) so the
  holding-scoped tools see it too, and flag the thesis.
- **Concentration**: the same metadata gives the shared-dependency table — before anything breaks.

## If something goes wrong

- **`the memorylake CLI is not on PATH`**: install it (above) or `export MEMORYLAKE_BIN=/path/to/memorylake`.
- **Search results differ on your run**: ranking can move between runs. Steps 4 and 6 print exactly what came back; the
  premise checks do not depend on search.
- **Something half-created after an interrupted run**: `python3 demo.py --reset`, or `python3 demo.py cleanup`.

## Files

```
investment-thesis-memory/
├── demo.py              # the CLI runner (every step is a plain `memorylake` command, echoed)
├── data/story.json      # 7 holdings, 29 records, the review question (fictional, plain ASCII)
├── web/server.py        # local web companion: same functions, Server-Sent Events
├── web/static/          # no-build front end
└── out/runs.jsonl       # one line per run (git-ignored)
```
