# Competitive intel memory for sales teams

A runnable version of the MemoryLake use case
[*Give Sales Teams Competitive Intel Memory That Reps Can Actually Pull Mid-Call*](https://www.memorylake.ai/en/usecase/competitive-intel-memory-for-sales-teams),
built entirely on the [`memorylake` CLI](https://github.com/memorylake-ai/memorylake-cli).

**Watch it run:** videos are being recorded.

**The problem.** *"Battle cards go stale by Friday."* A claim about a competitor lands in an analyst newsletter, then in a
deal note, a Slack digest, the battle card, and from the battle card into more deal notes. A week later the newsletter prints
a correction. The correction is one record; the claim is now seven. Count the records, ask search, or read the newest note,
and the claim wins. Repetition looks like corroboration, and a retraction does not reach the copies by itself.

**What this demo shows.** Lumora (fictional) sells team-scheduling software against Pendrel. Every piece of competitive
intel the sales team logged is one fact in a MemoryLake project, word for word, with metadata: who logged it, when, the claim
it makes, and `derived_from` — the record(s) the person logging it said they copied it from. Ten records answer one
question, *does Pendrel include SSO on its Business plan?*

| Read | Answer | Why |
|---|---|---|
| **Ask `search`, go with most hits** | Enterprise-only, 4 to 3 | the #1 hit is the correction itself, but most of the SSO hits are copies of the claim |
| **Count every record** (`fact list`) | Enterprise-only, 7 to 3 | seven records say it |
| **Trust the newest record** | Enterprise-only | the newest is a deal note from Oct 6, written from the battle card |
| **Walk the lineage** | **Business** | the seven are **one** source — an Aug 18 newsletter item, corrected on Aug 25; three of the copies were written after the correction was already in memory. The three "Business" records are three independent sources: the correction, Pendrel's pricing page, a loss debrief |

Then the lineage is written back: every copy gets `status: retracted-upstream` in its metadata (the text stays word for
word), a new battle card cites the three sources it rests on, and the deal notes in the withdrawn lineage give the list of
prospects who were told the wrong thing — one already lost over it, one the rep calls tomorrow. The same search afterwards
returns the copies just as before (**search does not read status**); anything that reads the metadata with the hits gets the
right answer.

```
setup ────────── proj create  "Lumora competitive intel"
intake ───────── fact add --project <p> '<record>' × 14   + fact update --metadata {kind, logged_by, logged_on, claim, derived_from, corrects, …}
quick reads ──── search '<question>' --top-k 8 (majority) · fact list (vote) · fact list (newest)
lineage ──────── fact list --projects <p>  → follow derived_from to the source; a correction in memory withdraws it
write back ───── fact update --metadata {…, status: retracted-upstream, retracted_by, root} × 7 · fact add (battle card v5, basis)
                 search '<question>' again → same copies come back; read their metadata
```

The lineage audit is plain code over `fact list` and metadata, not a model: the same memory gives the same answer every time.
No conversations and no extraction: every record is pinned word for word.

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
cd memorylake-usecases/competitive-intel-memory
export MEMORYLAKE_API_KEY=sk-...           # China region: also export MEMORYLAKE_BASE_URL=https://app.memorylake.cn/openapi/memorylake
python3 demo.py
```

About 35–60 s the first time, about 20 s on a re-run, which reads back what is already there. With the key set, the demo logs
into an isolated CLI profile under `./.memorylake-demo/` and never touches your own `~/.memorylake`. Everything it creates has a
`custom_id` starting with `mlu-cim-`.

```bash
python3 demo.py lineage                                   # every SSO record as a tree, and which ones are marked in memory
python3 demo.py audit 2026-08-24                          # the lineage audit as of any date
python3 demo.py ask "Why did we lose Kilnworth Foods?"    # search's top 8, with each hit's claim and status from its metadata
python3 demo.py --reset                                   # delete everything the demo created, then replay
python3 demo.py cleanup                                   # delete everything the demo created
```

## Or run it in the browser

```bash
python3 web/server.py                # opens http://127.0.0.1:8765
```

Paste the key, connect, run. The page drives the same functions as `demo.py` and streams every `memorylake` command into a
terminal drawer as it runs: the records as they are pinned, the three quick reads, the lineage tree with the control audit
next to it, and the write-back with the prospects to call. **Ask the intel** takes any question, or runs the audit as of a
date.

## What happens, step by step

Output below is from a real run (ids shortened).

### Steps 2–3 — one project; every record, one fact each

```
$ memorylake proj create --name 'Lumora competitive intel' --custom-id mlu-cim-intel …
$ memorylake fact add --project proj-c60d… 'Stackwatch Brief (Aug 18): Pendrel will move SAML single sign-on off its Business plan; from September 1, SSO is Enterprise-only.' 'Kilnworth Foods discovery call: …' …
$ memorylake fact update fact-f450… --project proj-c60d… --metadata '{"key": "intel:I01", "record": "I01", "kind": "analyst", "logged_by": "Marco Vell", "logged_on": "2026-08-18", "topic": "sso", "claim": "sso-enterprise", "derived_from": [], "source": "Stackwatch Brief, issue of 2026-08-18"}'
  · … and 13 more `fact update --metadata` the same way (kind, logged_by, logged_on, topic, claim, derived_from)

  14 records logged before today (2026-10-14); 10 of them about SSO
    I01 2026-08-18  ENT-ONLY analyst newsletter                     -            Stackwatch Brief (Aug 18): Pendrel will move SAML single sign-o…
    I02 2026-08-19  ENT-ONLY deal note · Kilnworth Foods            from I01     Kilnworth Foods discovery call: their IT lead needs Okta SSO. T…
    I03 2026-08-20  ENT-ONLY Slack #compete digest                  from I01     #compete digest: heads up, Pendrel SSO goes Enterprise-only fro…
    I04 2026-08-22  ENT-ONLY battle card                            from I03     Pendrel battle card v4: SAML SSO is Enterprise-only at Pendrel …
    I05 2026-08-26  BUSINESS newsletter correction                  corrects I01 Stackwatch Brief correction (Aug 25): the Pendrel change on Sep…
    I06 2026-09-03  ENT-ONLY deal note · Marisol Transit            from I04     Marisol Transit demo: used the battle card. Pendrel charges Ent…
    …
    I08 2026-09-16  BUSINESS pricing page capture                   -            Pendrel public pricing page, captured Sept 16: SAML SSO is list…
    I09 2026-09-29  BUSINESS loss debrief · Kilnworth Foods         -            Kilnworth Foods loss debrief: lost to Pendrel. Their IT lead sa…
    I10 2026-10-06  ENT-ONLY deal note · Vantry Hotels              from I04     Vantry Hotels first call: they asked about SSO for 14 propertie…
```

`fact add` takes no metadata, so each fact gets a `fact update --metadata` right after. `derived_from` is what the person
logging the record said it came from ("per Stackwatch", "used the battle card"); four unrelated records (prices, a win
debrief, another competitor) are there so search has something else to return. On a re-run, facts are matched by the `key`
in their metadata, so nothing is pinned twice.

### Step 4 — three quick reads

```
$ memorylake search 'Does Pendrel include SSO on its Business plan?' --projects proj-c60d… --types fact --top-k 8

      #   score  claim     hit
      1   1.000  BUSINESS  I05 Stackwatch Brief correction (Aug 25): the Pendrel change on September 1…
      2   0.500  -         N01 Pendrel list prices, Sept 16: Team 12 dollars, Business 24 dollars, Ent…
      3   0.333  BUSINESS  I08 Pendrel public pricing page, captured Sept 16: SAML SSO is listed under…
      4   0.250  BUSINESS  I09 Kilnworth Foods loss debrief: lost to Pendrel. Their IT lead said our S…
      5   0.200  ENT-ONLY  I01 Stackwatch Brief (Aug 18): Pendrel will move SAML single sign-on off it…
      6   0.167  ENT-ONLY  I04 Pendrel battle card v4: SAML SSO is Enterprise-only at Pendrel from Sep…
      7   0.143  ENT-ONLY  I03 #compete digest: heads up, Pendrel SSO goes Enterprise-only from Septem…
      8   0.125  ENT-ONLY  I07 Ostrander Dental Group call: their IT manager had also read that Pendre…

  → among the hits: Enterprise-only 4 · Business 3 · not about SSO 1 — “SSO is Enterprise-only at Pendrel”

  every SSO record: Enterprise-only 7 · Business 3
  → 7 to 3: “SSO is Enterprise-only at Pendrel”

  newest: I10 2026-10-06 deal note · Vantry Hotels (Dana Whitlock)
    “Vantry Hotels first call: they asked about SSO for 14 properties. Went through the battle card - Pendrel SSO is Enterpr…”
  → “SSO is Enterprise-only at Pendrel”
```

Search ranks the correction first — it is the closest text to the question. A tool that summarises the top 8 still sees a
majority of copies.

### Step 5 — walk the lineage

```
$ memorylake fact list --projects proj-c60d… --page-size 50

  as of 2026-10-14: every SSO record, drawn from its source down to its copies
    I01 2026-08-18  ENT-ONLY  analyst newsletter (Marco Vell)   ✗ withdrawn by I05 (2026-08-26)
    ├─ I02 2026-08-19  ENT-ONLY  deal note · Kilnworth Foods (Priya Nandakumar)
    ├─ I03 2026-08-20  ENT-ONLY  Slack #compete digest (Marco Vell)
    │  └─ I04 2026-08-22  ENT-ONLY  battle card (Marco Vell)
    │     ├─ I06 2026-09-03  ENT-ONLY  deal note · Marisol Transit (Dana Whitlock)   ← written after the correction
    │     └─ I10 2026-10-06  ENT-ONLY  deal note · Vantry Hotels (Dana Whitlock)   ← written after the correction
    └─ I07 2026-09-10  ENT-ONLY  deal note · Ostrander Dental Group (Sam Oduya)   ← written after the correction
    I05 2026-08-26  BUSINESS  newsletter correction (Leo Marsh)   corrects I01
    I08 2026-09-16  BUSINESS  pricing page capture (Dana Whitlock)
    I09 2026-09-29  BUSINESS  loss debrief · Kilnworth Foods (Priya Nandakumar)

    claim                                records sources standing
    SSO is Enterprise-only at Pendrel          7       1        0   —   (withdrawn: I01)
    SSO is on Pendrel's Business plan          3       3        3   I05, I08, I09

  → 7 records rest on I01, withdrawn by I05 on 2026-08-26; 3 of them (I06, I07, I10) were written after that correction was already in memory
  → standing sources decide: “SSO is on Pendrel's Business plan”

  control — the same audit as of 2026-08-24, before the correction existed:
    SSO is Enterprise-only at Pendrel          4 records → 1 standing source(s)
    SSO is on Pendrel's Business plan          0 records → 0 standing source(s)
    → “SSO is Enterprise-only at Pendrel”, on 1 source — not 4 votes. The audit takes no side; it folds copies back into their source.
```

The control matters: on Aug 24 the audit backs the claim — on one source, not four votes. And `python3 demo.py audit
2026-09-01` already flips to Business, two days before the first copy was written from the stale battle card: the
correction was in memory; nothing read it back.

### Step 6 — write it back

```
$ memorylake fact update fact-f450… --project proj-c60d… --metadata '{…, "status": "retracted-upstream", "retracted_by": "I05", "root": "I01", "flagged_on": "2026-10-14"}'
  · … and 6 more the same way (I02, I03, I04, I06, I07, I10)
$ memorylake fact add --project proj-c60d… 'Pendrel battle card v5: SAML SSO is on Pendrel Business (24 dollars per seat); SCIM provisioning is Enterprise-only. …'

  battle card v5 (I11, fact-f075…) — basis I05, I08, I09, cited 3/3 back to records in memory; supersedes I04:
    I05 2026-08-26  newsletter correction              “Stackwatch Brief correction (Aug 25): the Pendrel change on September…”
    I08 2026-09-16  pricing page capture               “Pendrel public pricing page, captured Sept 16: SAML SSO is listed und…”
    I09 2026-09-29  loss debrief · Kilnworth Foods     “Kilnworth Foods loss debrief: lost to Pendrel. Their IT lead said our…”

  prospects told “SSO is Enterprise-only at Pendrel” — every deal note in the withdrawn lineage:
    Kilnworth Foods          told 2026-08-19 by Priya Nandakumar   (I02)   lost 2026-09-29 (I09)
    Marisol Transit          told 2026-09-03 by Dana Whitlock      (I06)   open
    Ostrander Dental Group   told 2026-09-10 by Sam Oduya          (I07)   open
    Vantry Hotels            told 2026-10-06 by Dana Whitlock      (I10)   open — Sam Oduya calls them tomorrow

$ memorylake search 'Does Pendrel include SSO on its Business plan?' --projects proj-c60d… --types fact --top-k 8
      #   score  claim     hit
      1   1.000  BUSINESS  I08 Pendrel public pricing page, captured Sept 16: SAML SSO is listed under…
      2   0.500  BUSINESS  I11 Pendrel battle card v5: SAML SSO is on Pendrel Business (24 dollars per…
      3   0.333  BUSINESS  I05 Stackwatch Brief correction (Aug 25): the Pendrel change on September 1…
      4   0.250  ENT-ONLY  I04 Pendrel battle card v4: SAML SSO is Enterprise-only at Pendrel from Sep…  [retracted-upstream]
      5   0.200  ENT-ONLY  I01 Stackwatch Brief (Aug 18): Pendrel will move SAML single sign-on off it…  [retracted-upstream]
      6   0.167  ENT-ONLY  I03 #compete digest: heads up, Pendrel SSO goes Enterprise-only from Septem…  [retracted-upstream]
      7   0.143  ENT-ONLY  I02 Kilnworth Foods discovery call: their IT lead needs Okta SSO. Told them…  [retracted-upstream]
      8   0.125  ENT-ONLY  I10 Vantry Hotels first call: they asked about SSO for 14 properties. Went …  [retracted-upstream]

  → search does not read status: 5 of the 8 hits are marked retracted-upstream and still come back
  → read the metadata with them: standing hits say Business 3 · Enterprise-only 0 — “SSO is on Pendrel's Business plan”
```

`fact update --metadata` replaces the whole object, so each copy keeps its old keys and gains `status`, `retracted_by` and
`root`. Search hits carry each fact's `metadata`, so a rep's tool can drop or flag the withdrawn ones without another call.

## Measured

| What | Result |
|---|---|
| Lineage audit: 7 records → 1 source, withdrawn; 3 → 3 sources standing; 3 copies after the correction | every run (deterministic over `fact list`) |
| Control audit on 2026-08-24: Enterprise-only on 1 source | every run |
| Search top 8 before the write-back, from scratch: #1 is the correction; 4 Enterprise-only · 3 Business · 1 other | 3 / 3 fresh runs, same order each time |
| Search top 8 after the write-back: 5 of 8 hits marked retracted-upstream; standing hits Business 3 · Enterprise-only 0 | every run (the order moves once the metadata is updated and the new card is written) |
| Time | 32–57 s from scratch, 17–20 s re-run |

The search lines are what `search` returned; the demo prints whatever comes back.

## Wiring it into your own team

- **Intake**: one fact per piece of intel, verbatim (`fact add --project`), from wherever it lands — a Slack shortcut, the
  battle-card tool, a CRM note. Ask for one extra field when it is logged: *where did you get this?* Store it as
  `derived_from` (a record id) or `source` (an outside source) in `metadata`.
- **Corrections**: log a correction as its own record with `corrects: <record id>`. Never edit the original — the original is
  what the copies point at.
- **Reading**: `fact list --projects <p>` every page, fold each record back to its source, count sources, not records.
  `audit` in `demo.py` is that function.
- **Write-back**: when a source is withdrawn, mark every record in its lineage (`fact update --metadata`, whole object) and
  list the deal notes among them — those are the conversations to correct.
- **Retrieval mid-call**: search is the right tool for "what is closest to this question?". Read `metadata.status` on the
  hits before showing them.

## If something goes wrong

- **`the memorylake CLI is not on PATH`**: install it (above) or `export MEMORYLAKE_BIN=/path/to/memorylake`.
- **Search results differ on your run**: ranking can move, especially after facts are updated. Steps 4 and 6 print exactly
  what came back; step 5 and the write-back do not depend on search.
- **Something half-created after an interrupted run**: `python3 demo.py --reset`, or `python3 demo.py cleanup`.

## Files

```
competitive-intel-memory/
├── demo.py              # the CLI runner (every step is a plain `memorylake` command, echoed)
├── data/story.json      # 14 records, the new battle card, the question (fictional, plain ASCII)
├── web/server.py        # local web companion: same functions, Server-Sent Events
├── web/static/          # no-build front end
└── out/runs.jsonl       # one line per run (git-ignored)
```
