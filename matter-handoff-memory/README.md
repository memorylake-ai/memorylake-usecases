# Client matter handoff memory for law firms

A runnable version of the MemoryLake use case
[*Give Law Firms Matter Handoff Memory That Protects Every Client and Position*](https://www.memorylake.ai/en/usecase/client-matter-handoff-memory-for-law-firms),
built entirely on the [`memorylake` CLI](https://github.com/memorylake-ai/memorylake-cli).

**Watch it run:** recording in progress.

**The problem.** A small firm keeps one memory per matter: that is the privilege wall. It also wants memory about the
*people* it meets again and again, opposing counsel above all: how he negotiates, how long his client takes to sign off,
what he does with discovery. That memory has to be shared across matters, or it is useless. So a lawyer's notes about
opposing counsel end up on the opposing counsel. Some of them are portable patterns. Some are one matter's secrets with
his name in the sentence. When the next matter loads "the matter, plus what we know about opposing counsel", the wall
still holds, and the secrets come in through the door.

**What this demo shows.** Brannock & Lise LLP (fictional). Garrick Holm (Tessaly Partners) was opposing counsel in
**Fenwick Bakeries v. Corlett Freight Lines** (settled 2026-09-22, amount confidential under clause 9) and is opposing
counsel in **Pell Harbor Marine v. Ardent Cold Storage** (mediation 2026-11-05). Mira Castell ran both. She leaves on
October 16; Jonah Reyes takes Pell Harbor over today, October 14.

Each matter is a project; Holm is an actor. Mira's eight notes on Holm are on his actor: four portable patterns, one Pell
Harbor note, and three Fenwick specifics (two labelled as such, one with no label at all). The same question, two scopes:

| Search | Fenwick material in the hits | Other matters' names and figures |
|---|---|---|
| `--projects <Pell Harbor>` (the matter wall) | **0** | none |
| `--projects <Pell Harbor> --actors <Holm>` (the handoff read: the matter plus the counsel's patterns) | **3 of 9** distinct facts across two questions | **Fenwick, Corlett, 412,000, 640,000, 350,000**: the confidential settlement amount, Fenwick's settlement authority, a gap in Fenwick's evidence |

`search --projects P --actors A` is the **union** of the two scopes, by design: that is exactly what makes "our matter
plus what we know about this person" one call. It also means everything on the person comes along, whichever matter it
was written on.

Then the fix, in two parts:

1. **The wall check** reads every fact on the shared actor (`fact list`, not search's top 8) against the matter being
   handed over. A fact's **label** (`matter`, and `scope`: portable pattern or matter-specific) says where it came from; a
   **scan** looks for any other matter's names and figures. Either one withholds it. The note nobody labelled is caught
   only by the scan.
2. **Move them back.** Read-time filtering protects this handoff; the source would still leak into the next one. Each
   withheld fact is pinned word for word on its own matter's project and deleted from the actor. Asked again: 0 Fenwick
   names or figures, Holm's patterns still on top, and the Fenwick project still answers "what did Fenwick settle for?"
   with all three moved facts.

```
setup ──────── proj create  fenwick · pellharbor   ·   actor create holm + actor bind
intake ─────── fact add --project <matter> '<record>' …  /  fact add --actor <holm> '<note>' …
               + fact update --metadata {matter, scope: pattern|matter, privileged, by, on}
handoff ────── search '<q>' --projects <pellharbor>                    (control: the wall)
               search '<q>' --projects <pellharbor> --actors <holm>     (the union: Fenwick's secrets come in)
wall check ─── fact list --actors <holm>  → label + other-matter identifier scan → portable · this matter · withheld
move back ──── fact add --project <fenwick> '<same words>' + fact delete --actor <holm>  → ask again (new wording)
```

No conversations and no extraction: every record is pinned word for word, and the wall check is plain code over
`fact list` and metadata.

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
cd memorylake-usecases/matter-handoff-memory
export MEMORYLAKE_API_KEY=sk-...           # China region: also export MEMORYLAKE_BASE_URL=https://app.memorylake.cn/openapi/memorylake
python3 demo.py
```

About a minute and a half the first time (two projects, one actor, 20 facts, each with its metadata), under a minute on
a re-run. Every run replays handoff day: the facts an earlier run moved back to Fenwick are put back on Holm's actor first.
With the key set, the demo logs into an isolated CLI profile under `./.memorylake-demo/` and never touches your own
`~/.memorylake`. Everything it creates has a `custom_id` starting with `mlu-mho-`.

```bash
python3 demo.py wall                                                       # the wall check of Holm's actor against Pell Harbor, now
python3 demo.py ask "How does Garrick Holm negotiate at mediation?"        # top 8 on Pell Harbor + Holm's actor
python3 demo.py ask "What did Fenwick settle for?" --matter fenwick        # top 8 on another matter
python3 demo.py ask "What did Fenwick settle for?" --no-actor              # the matter project alone
python3 demo.py --reset                                                    # delete everything the demo created, then replay
python3 demo.py cleanup                                                    # delete everything the demo created
```

## Or run it in the browser

```bash
python3 web/server.py                # opens http://127.0.0.1:8765
```

Paste the key, connect, run. The page drives the same functions as `demo.py` and streams every `memorylake` command into a
terminal drawer as it runs: the matters and the actor, every record with its label, the handoff read with the control next
to it, the wall check, and the move back. **Ask** takes any question on either matter, with or without Holm's actor in
scope, and re-runs the wall check.

## What happens, step by step

Output below is from a real run (ids shortened).

### Steps 2–3 — one project per matter, one actor for the opposing counsel; every record, one fact each

```
    project  Fenwick Bakeries v. Corlett Freight Lines    settled 2026-09-22     proj-…6e3eabda
    project  Pell Harbor Marine v. Ardent Cold Storage    mediation 2026-11-05   proj-…f9cb8f93
    actor    Garrick Holm, Tessaly Partners — opposing counsel in both           actor-…7a36a1b08
```

One `fact add` per scope (it takes many texts at once), then `fact update --metadata` on each fact (`fact add` takes no
metadata):

```
$ memorylake fact add --actor actor-…7a36a1b08 'Garrick Holm opens a mediation far below what he expects to pay and does not move in the first session.' …
$ memorylake fact update fact-…2b888 --actor actor-…7a36a1b08 --metadata '{"key": "mho:H1", "record": "H1", "where": "holm", "kind": "counsel pattern", "by": "Mira Castell", "on": "2026-08-05", "matter": "fenwick", "scope": "pattern"}'

  Garrick Holm — opposing-counsel actor, 8 records:
    H1  2026-08-05  fenwick/pattern                   Garrick Holm opens a mediation far below what he expects to pay and d…
    H2  2026-09-16  fenwick/pattern                   Garrick Holm moves only after he has a written expert damages model i…
    H3  2026-06-11  pellharbor/pattern                Garrick Holm never agrees to a deadline extension without asking for …
    H4  2026-09-23  fenwick/pattern                   Garrick Holm needs about 48 hours to get settlement authority from hi…
    H5  2026-08-20  pellharbor/matter                 Garrick Holm withheld Ardent's sprinkler maintenance logs until a mot…
    H6  2026-09-23  fenwick/matter         privileged Garrick Holm settled Fenwick v. Corlett at 412,000 dollars, 64 percen…
    H7  2026-09-23  fenwick/matter         privileged Garrick Holm's final offer cleared Fenwick's authority of 350,000 dol…
    H8  2026-09-16  — no label —                      Garrick Holm knows about the six-hour gap in Fenwick's temperature lo…
```

### Step 4 — handoff day: the matter, plus the opposing counsel's patterns

The control first, the question on the Pell Harbor project alone: six Pell Harbor records, no other matter's names or
figures. Then the handoff read:

```
$ memorylake search 'What should we expect from Garrick Holm at the mediation, and how does he settle?' --projects proj-…f9cb8f93 --actors actor-…7a36a1b08 --types fact --top-k 8

  “What should we expect from Garrick Holm at the mediation, and how does he settle?” · --projects Pell Harbor --actors Holm · top 8:
      #1    H1  Holm actor           Garrick Holm opens a mediation far below what he expects to pay and does no…
      #2    H2  Holm actor           Garrick Holm moves only after he has a written expert damages model in hand…
      #3    H4  Holm actor           Garrick Holm needs about 48 hours to get settlement authority from his clie…
      #4    H6  Holm actor           Garrick Holm settled Fenwick v. Corlett at 412,000 dollars, 64 percent of o…  ✗ fenwick
      #5    H7  Holm actor           Garrick Holm's final offer cleared Fenwick's authority of 350,000 dollars b…  ✗ fenwick
      #6    H8  Holm actor           Garrick Holm knows about the six-hour gap in Fenwick's temperature logs and…  ✗ fenwick
      #7    H5  Holm actor           Garrick Holm withheld Ardent's sprinkler maintenance logs until a motion to…
      #8    P5  Pell Harbor project  Pell Harbor discovery: Ardent produced the sprinkler maintenance logs only …

  “What figures has Garrick Holm settled at, and how far does he move?” · --projects Pell Harbor --actors Holm · top 8:
      #1    H6  Holm actor           Garrick Holm settled Fenwick v. Corlett at 412,000 dollars, 64 percent of o…  ✗ fenwick
      #2    H7  Holm actor           Garrick Holm's final offer cleared Fenwick's authority of 350,000 dollars b…  ✗ fenwick
      …

  → 9 distinct facts across the two questions; 3 of them are Fenwick's material:
      H6  privileged Garrick Holm settled Fenwick v. Corlett at 412,000 dollars, 64 percent of our 640,000 dollar de…
      H7  privileged Garrick Holm's final offer cleared Fenwick's authority of 350,000 dollars by 62,000 dollars; he…
      H8  Garrick Holm knows about the six-hour gap in Fenwick's temperature logs and used it at the seco…
  → names and figures from another matter now in the Pell Harbor handoff: Fenwick, Corlett, 412,000, 640,000, 350,000
```

The second question is the one a mediation-prep assistant asks. Its top two answers are Fenwick's confidential settlement
and Fenwick's settlement authority.

### Step 5 — the wall check

```
$ memorylake fact list --actors actor-…7a36a1b08 --page-size 50

    id  label                  verdict        reason
    H1  fenwick/pattern        ✓ portable     label: portable pattern
    H2  fenwick/pattern        ✓ portable     label: portable pattern
    H3  pellharbor/pattern     ✓ portable     label: portable pattern
    H4  fenwick/pattern        ✓ portable     label: portable pattern
    H5  pellharbor/matter      ✓ this matter  label: pellharbor-specific
    H6  fenwick/matter         ✗ withheld     label: fenwick-specific
    H7  fenwick/matter         ✗ withheld     label: fenwick-specific
    H8  — no label —           ✗ withheld     no scope label; scan: Fenwick

  → 8 facts on Holm: 4 portable patterns · 1 Pell Harbor-specific · 3 withheld (another matter's)
  → caught only by the scan (no label): H8

  the Pell Harbor handoff through the wall — the same two searches (q1, q2), withheld facts dropped (6 kept):
      q1#1  H1  Holm actor           Garrick Holm opens a mediation far below what he expects to pay and does no…
      q1#2  H2  Holm actor           Garrick Holm moves only after he has a written expert damages model in hand…
      q1#3  H4  Holm actor           Garrick Holm needs about 48 hours to get settlement authority from his clie…
      q1#7  H5  Holm actor           Garrick Holm withheld Ardent's sprinkler maintenance logs until a motion to…
      q1#8  P5  Pell Harbor project  Pell Harbor discovery: Ardent produced the sprinkler maintenance logs only …
      q2#7  H3  Holm actor           Garrick Holm never agrees to a deadline extension without asking for one of…
  → other matters' names and figures left: none
```

The scan's identifier list is each matter's client, parties, people and figures (`data/story.json`, `identifiers`),
matched as whole tokens: "412,000" does not match "4120,000". The label is the cheap check and the scan is the backstop. A
label alone misses the note nobody labelled, and a scan alone misses a secret written without names ("our client's floor
was 350k" passes a name scan if 350,000 is not on the list).

### Step 6 — move them back

```
$ memorylake fact add --project proj-…6e3eabda 'Garrick Holm settled Fenwick v. Corlett at 412,000 dollars, 64 percent of our 640,000 dollar demand, after the second mediation session.'
$ memorylake fact delete --actor actor-…7a36a1b08 fact-…12dc4
  · … and the same for the other 2

  moved: H6 → Fenwick project, H7 → Fenwick project, H8 → Fenwick project
  Holm's actor now: 5 facts — H1, H2, H3, H4, H5

  “How does opposing counsel Garrick Holm negotiate and settle mediations?” · --projects Pell Harbor --actors Holm · top 8:
      #1    H1  Holm actor           Garrick Holm opens a mediation far below what he expects to pay and does no…
      #2    H2  Holm actor           Garrick Holm moves only after he has a written expert damages model in hand…
      #3    H4  Holm actor           Garrick Holm needs about 48 hours to get settlement authority from his clie…
      #4    P6  Pell Harbor project  Handoff note from Mira Castell: Jonah Reyes takes over Pell Harbor on 2026-…
      …
  → other matters' names and figures: none · Holm's portable patterns in the top 8: 3 (H1, H2, H4)

  “What did Fenwick settle for, and what was the client's authority?” · --projects Fenwick · top 4 of 8:
      #1    F5  Fenwick project      Fenwick v. Corlett settled on 2026-09-22 for 412,000 dollars. The amount is …
      #2    H6  Fenwick project      Garrick Holm settled Fenwick v. Corlett at 412,000 dollars, 64 percent of o…
      #3    F2  Fenwick project      Privileged client instruction, Fenwick Bakeries: CFO Ilse Dorrance authoriz…
      #4    H7  Fenwick project      Garrick Holm's final offer cleared Fenwick's authority of 350,000 dollars b…
  → the moved facts found on the Fenwick project: H6, H7, H8
```

The question after the move is worded differently on purpose: `search` can answer a repeated question from a cache made
before the write. Step 6 also asks again (every 4 s, up to 8 times) until the deleted facts have left the search index, and
prints how many tries it took.

## Measured

| What | Result |
|---|---|
| Control (`--projects` Pell Harbor alone): other matters' names and figures in the top 8 | none, 5 / 5 runs |
| Handoff read (`--projects` Pell Harbor `--actors` Holm): Fenwick facts H6, H7, H8 in the hits | 3 of 9 distinct facts, 5 / 5 runs (4 CLI from scratch or re-run, 1 web); the top 8 of both questions identical in every run |
| Wall check: 4 portable · 1 this matter · 3 withheld, H8 caught only by the scan | 5 / 5 |
| After the move: other matters' names and figures in the top 8; patterns H1, H2, H4 at #1–#3; all three moved facts found on Fenwick | none; 5 / 5; 5 / 5 (deleted facts gone from search on the first try every time) |
| Time | 70–88 s from scratch, 48–52 s re-run |

## Wiring it into your own matters

- **One project per matter** for everything that belongs to the matter: pleadings, client instructions, settlement terms,
  work product.
- **One actor per recurring party** (opposing counsel, an expert, a judge's chambers) for what you learn about *them*,
  and label every fact: `matter` (where you learned it) and `scope` (`pattern` if it holds on any matter, `matter` if
  it only makes sense in that one). Write patterns without the matter's names and figures; put the specifics on the
  matter.
- **Before a handoff**, run the wall check against the receiving matter, and keep each matter's `identifiers` list
  (client, parties, people, figures) up to date. Move anything withheld back to its matter rather than filtering it
  forever.
- **At read time**, if your assistant searches `--projects <matter> --actors <party>`, drop hits the wall check withholds.
  Search ranks by relevance; it does not read your labels.

## If something goes wrong

- **`the memorylake CLI is not on PATH`**: install it (above) or `export MEMORYLAKE_BIN=/path/to/memorylake`.
- **Search results differ on your run**: ranking can move between runs. Steps 4 and 6 print exactly what came back; the
  wall check does not depend on search.
- **Step 6 says deleted facts are still returned**: the search index was slower than 30 s; run `python3 demo.py` again.
- **"search answered from its cache"**: step 4 asks the same questions every run, and `search` can serve a repeated
  question from a cache; the note names the hits whose fact ids belong to an earlier run.
- **Something half-created after an interrupted run**: `python3 demo.py --reset`, or `python3 demo.py cleanup`.
  Cleanup deletes every fact on the actor before the actor: an actor deleted with facts on it leaves them readable and
  undeletable.

## Files

```
matter-handoff-memory/
├── demo.py              # the CLI runner (every step is a plain `memorylake` command, echoed)
├── data/story.json      # 2 matters, 1 opposing counsel, 20 records (fictional, plain ASCII)
├── web/server.py        # local web companion: same functions, Server-Sent Events
├── web/static/          # no-build front end
└── out/runs.jsonl       # one line per run (git-ignored)
```
