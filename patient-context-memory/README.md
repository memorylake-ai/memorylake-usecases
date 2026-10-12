# Patient context memory for clinicians

A runnable version of the MemoryLake use case
[*Give Clinicians Patient Context Memory That Persists Across Every Visit*](https://www.memorylake.ai/en/usecase/patient-context-memory-for-clinicians),
built entirely on the [`memorylake` CLI](https://github.com/memorylake-ai/memorylake-cli).

**The problem.** *Yesterday's note mentioned a new allergy. Today's AI scribe doesn't know.* Every AI tool in a clinic sees
one visit at a time, so a reaction recorded somewhere else last week never reaches the order drafted today.

**What this demo shows.** Brightwater Family Practice (fictional) keeps one memory per patient. Walter Brandt's chart lives on
his own actor, and every entry is pinned word for word with its **date, signing clinician and source** as metadata.

| When | Who | What goes into his memory |
|---|---|---|
| 2018 → 2026-09-30 | the practice | 13 chart entries: **"Allergies: no known drug allergies (NKDA)."**, problems, medications, labs, visits |
| **2026-10-05** | Northside Urgent Care | started on cefalexin; **hives and lip swelling** two hours later. A visit note, a triage note (it says "Keflex"), a new **allergy line**, and the physician's dictated note (a conversation MemoryLake extracts facts from) |
| **2026-10-12, today** | Dr. Okafor + the AI scribe | the sinusitis is not better; the scribe drafts **cefuroxime** (a cephalosporin) and **doxycycline** |

Then:

1. **The contradiction detector flags last week's allergy against the 2018 "no known drug allergies" line**: `fact conflict list --actor`
   (`m2m · logical`), about 30 s after the allergy is written. In every run it also raised a second conflict: NKDA against the visit and triage notes.
2. **Reconcile without losing a record.** `keep_fact` keeps one fact and forgets **every other fact in the conflict**. On the
   two-fact conflict that retires exactly the stale NKDA line. On the three-fact one it would also forget the triage note,
   so that one is dismissed instead. The forgotten NKDA line is still readable, word for word, in `fact conflict get`, and `fact trace` shows ADD → FORGET.
3. **Today's order check runs `fact list --query`, the first demo here to use it**, for every name of the drug's class,
   across his actor and the clinic project. It is an exact, case-insensitive substring match over **every page**, with nothing ranked away.
   **cefuroxime ✗ blocked** (citing the allergy line, the visit and triage notes, and what was extracted from the dictation),
   **doxycycline ✓ clear**. The control keeps only entries dated on or before 2026-10-04, his chart before urgent care. There
   **cefuroxime ✓ goes through**: the scribe can only check what the chart holds.
4. **Exact vs semantic.** An exact search only finds what you name. The class name *cephalosporin* finds 2–3 of the 5 reaction
   records. The triage note is found **only** by *Keflex*. So the check names every member of the class. `search` finds by
   meaning with no list, but returns its top-k: 4–5 of 5 in the top 5. Once it was the allergy line itself that fell out.
5. **Decision record.** Each decision is pinned on the clinic project with the id and `updated_at` of every entry it saw, then
   re-read with `fact get`. *"What did the AI see at decision time?"* has an answer, and it says whether any of it has changed since.

```
chart      ── fact add --actor <Walter> 'e1' 'e2' … (one call) + fact update --metadata (date, clinician, source, kind)
read back  ── fact list --actors <Walter> --page-size 5 [--continuation-token …]   (until the token is null)
urgent care─ fact add (visit + triage) · 15 s · fact add 'Allergy: cephalosporins …' ──▶ fact conflict list --actor (m2m)
dictation  ── conv create --kind DIRECT + msg append ×6 + cook ──▶ extracted facts (shown, not relied on)
reconcile  ── fact conflict resolve --strategy keep_fact (2-fact conflict) · dismiss (3-fact conflict) · fact trace
order check─ fact list --actors <Walter> --projects <clinic> --query <name>  × every name in the class
semantic   ── search "Has Walter ever reacted badly to an antibiotic?" --actors … --projects … --top-k 5
record     ── fact add --project <clinic> + fact update --metadata {seen: [{id, updated_at}]} ──▶ fact get each, compare
```

**Watch it run** (real recordings, unedited): recording in progress.

Runs in about 3–4 minutes on a free personal account (most of it is the detector and turning the dictated note into memory).
The only credential you need is a MemoryLake API key.

**Measured** (all fresh runs while building this demo, free account): **5 of 5** raised the allergy-vs-NKDA conflict (two facts)
29–34 s after the allergy line, and **5 of 5** also raised NKDA against the visit + triage notes (three facts). Verdicts were the same every time:
cefuroxime ✗, doxycycline ✓, and in the 2026-10-04 control cefuroxime ✓. Facts extracted from the dictated note varied from 3 to 7 per run. The
order check does not depend on them. `search` had 4 or 5 of the 5 reaction records in its top 5.

**Why the chart has two kinds of entry.** In probes, a dated allergy (*"Allergy 2026-10-05: cephalosporins …"*) against a dated
NKDA (*"Intake 2018-05-14: no known drug allergies"*) raised **nothing** in 8 of 8 runs, waiting up to 10 minutes. The same two
written as undated status lines raised a conflict in 10–19 s (2 of 2, either order). The detector reads dates. *No allergies in 2018*
and *an allergy in 2026* can both be true, and it is right not to flag them. So events (visits, labs) carry their date in the
text, and the current-status lines (allergy list, problems, medications) do not. Their date, clinician and source are in `metadata`.

**What the detector does not do.** An order (*"amoxicillin 875 mg"*) written next to a penicillin allergy raised no conflict
within two minutes in a probe. An allergy and a prescription are not contradictory statements. The order check is this demo's own
code: exact lookups plus a rule (a hit that is an allergy line or describes a reaction blocks the order).

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
cd memorylake-usecases/patient-context-memory

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
| `python3 demo.py chart` | His chart, page by page, as current status + dated timeline |
| `python3 demo.py check cefuroxime` | The order check for one drafted drug, against his chart as it is now |
| `python3 demo.py ask "Keflex"` | `fact list --query` and `search` side by side for the same text |
| `python3 demo.py conflicts` | Every conflict on his actor, open or resolved |
| `python3 demo.py records` | Re-read every entry the recorded decisions saw and say whether it changed |
| `python3 demo.py cleanup` | Delete the dictated note, the clinic project, his chart and the urgent care physician |

It is safe to re-run. Everything is found again by `custom_id` (prefix `mlu-pcm-`), and a chart entry is written only if it is
not there yet. The NKDA line that an earlier run's `keep_fact` forgot is recognised from the resolved conflict's snapshot and is
**not** pinned again. A re-run reads the earlier conflicts instead of waiting. `--reset` replays everything.

## Or run it in the browser

```bash
python3 web/server.py        # http://127.0.0.1:8765 — standard library only
```

![Web companion — today's order check: cefuroxime blocked by last week's entries, doxycycline clear](https://github.com/memorylake-ai/memorylake-usecases/raw/main/patient-context-memory/web/screenshot-check.png)

Paste the key, press **Run demo**, and watch his chart come back page by page, last week's entries and the detector's
conflicts, the reconcile step, the order check with its control, the exact-vs-semantic table and the decision record. **Ask** runs
`fact list --query` and `search` side by side on any text, re-checks an order, or re-verifies the records. The terminal
drawer shows every `memorylake` command as it runs.

## What happens, step by step

Output below is from real runs (ids shortened).

**1. Connect**: validate the key, pick a workspace.

**2. Set up.** `proj create` for the clinic, and `actor create --tags patient` + `actor bind` for Walter. A second actor for Dr.
Whitaker, who speaks in last week's dictated note.

**3. Chart history.** The 13 entries go in **one** `fact add` call (it takes several texts and returns their ids in order),
then each gets `fact update --metadata` (`fact add` takes no metadata):

```
$ memorylake fact add --actor actor-… 'Allergies: no known drug allergies (NKDA).' 'Intake 2018-05-14: new patient registered; …' 'Problem: hypertension.' …
$ memorylake fact update fact-… --actor actor-… --metadata '{"chart_key": "allergy-nkda", "kind": "allergy", "entry": "current", "date": "2018-05-14", "clinician": "Ava Lindqvist, RN", "source": "new-patient intake form; reconfirmed at the 2026-09-30 annual visit"}'
```

Then it is read back **page by page** with `fact list --page-size 5` and `--continuation-token`. When the last page is exactly full
the server still returns a token, and the page after it is empty (re-run, 15 entries: 5 / 5 / 5 / 0). Read until the token is null:

```
$ memorylake fact list --actors actor-… --page-size 5
    page 1: 5 entries · continuation_token MjAyNi0xMC0xMlQw…
$ memorylake fact list --actors actor-… --page-size 5 --continuation-token MjAyNi0xMC0xMlQw…
    page 2: 5 entries · continuation_token MjAyNi0xMC0xMlQw…
    page 3: 3 entries · continuation_token null (last page)

    Current status (no date in the text; signed date in metadata):
      allergy      Allergies: no known drug allergies (NKDA).                Ava Lindqvist, RN, 2018-05-14
      medication   Medication: lisinopril 10 mg once daily.                  Dr. Lena Okafor, 2021-03-08
      …
    Timeline (events, dated):
      2018-05-14  encounter    Intake 2018-05-14: new patient registered; no drug allergies listed on the intake form.
      2026-09-30  encounter    Encounter 2026-09-30 annual visit (Dr. Lena Okafor): blood pressure 136/82; allergies reviewed,…
```

**4. Last week.** The visit and triage notes go in first, and the allergy line 15 s later, so the detector checks it as it lands:

```
$ memorylake fact add --actor actor-… 'Allergy: cephalosporins (cefalexin) - hives and lip swelling.'
$ memorylake fact conflict list --actor actor-…

  [m2m · logical] Drug allergy vs NKDA   cfl-…  OPEN
    Fact 0 reports an allergy to cephalosporins (cefalexin) with hives and lip swelling, while fact 3 states no known drug
    allergies (NKDA). These cannot both be true as current allergy status.
    “Allergy: cephalosporins (cefalexin) - hives and lip swelling.”   (Dr. Sam Whitaker, 2026-10-05)
    “Allergies: no known drug allergies (NKDA).”                      (Ava Lindqvist, RN, 2018-05-14)

  [m2m · logical] NKDA vs cephalexin/Keflex reaction   cfl-…  OPEN
    “Encounter 2026-10-05 Northside Urgent Care (Dr. Sam Whitaker): acute sinusitis, started cefalexin 500 mg; hiv…”
    “Triage 2026-10-05 (Jo Park, RN): lips swollen, itchy rash on the chest after his first Keflex capsule this mo…”
    “Allergies: no known drug allergies (NKDA).”
```

The dictated note (a DIRECT conversation between Dr. Whitaker and Walter on the clinic project, `--timestamp` 2026-10-05) is
appended and turned into memory (about 80 s). What it yields varies by run, so it is shown and nothing depends on it:

```
  Extracted from the dictated note: 6 fact(s), 4 naming a cephalosporin (shown, not relied on — extraction varies run to run):
    • [clinic project] Walter Brandt’s symptoms were assessed as an allergic reaction to cefalexin. (as of 2026-10-05)
    • [clinic project] Walter Brandt developed lip swelling and an itchy rash across the chest about two hours after the f…
```

**5. Reconcile.**

```
  cfl-… (2 facts): `keep_fact` keeps one fact and forgets every other fact in the conflict. Keeping “Allergy: cephalosporins…” would forget:
    - “Allergies: no known drug allergies (NKDA).”  ← the stale status line
$ memorylake fact conflict resolve cfl-… --actor actor-… --strategy keep_fact --keep-fact-id fact-…
  · two facts, one of them stale: resolved with keep_fact, forgotten fact-…

  cfl-… (3 facts): … Keeping “Encounter 2026-10-05 Northside Urgent Care (Dr. S…” would forget:
    - “Triage 2026-10-05 (Jo Park, RN): lips swollen, itchy rash on the chest after his first Ke…”  ← a record that SUPPORTS the allergy
    - “Allergies: no known drug allergies (NKDA).”  ← the stale status line
  · not keep_fact: it would forget a supporting record. The NKDA line it names is already retired, so it is dismissed

  Forgotten, not erased — `fact conflict get cfl-…` keeps it word for word:
    “Allergies: no known drug allergies (NKDA).”
    `fact trace fact-…`: ADD (MANUAL) → FORGET (MANUAL)
```

If no two-fact conflict comes up, the NKDA line is rewritten in place with `fact update --text` (the old text stays in `fact trace`)
and the remaining conflicts are dismissed. That branch never ran in our measured runs.

**6. Today's order check.** One `fact list --query` per name in the class, over his actor **and** the clinic project
(`--actors … --projects …` together), every page:

```
$ memorylake fact list --actors actor-… --projects proj-… --query cephalosporin --page-size 50
$ memorylake fact list --actors actor-… --projects proj-… --query cefalexin --page-size 50
…
  ✗  cefuroxime 500 mg twice daily for 7 days  (cephalosporin) → blocked — a recorded reaction to this drug class
     ✗ Allergy: cephalosporins (cefalexin) - hives and lip swelling.
       Dr. Sam Whitaker, 2026-10-05 · matched “cephalosporin”, “cefalexin”
     ✗ Encounter 2026-10-05 Northside Urgent Care (Dr. Sam Whitaker): acute sinusitis, started cef…
     ✗ Triage 2026-10-05 (Jo Park, RN): lips swollen, itchy rash on the chest after his first Kefl…
       Jo Park, RN, 2026-10-05 · matched “Keflex”
     · Cefalexin was stopped for Walter Brandt. (as of 2026-10-05)
       extracted from a dictated note · matched “cefalexin”
     ✗ Walter Brandt’s symptoms were assessed as an allergic reaction to cefalexin. (as of 2026-10…

  ✓  doxycycline 100 mg twice daily for 7 days  (tetracycline) → clear — no record mentions this drug class

$ memorylake fact get fact-… --actor actor-…
  Why it is blocked (`fact get`): “Allergy: cephalosporins (cefalexin) - hives and lip swelling.”
    signed by Dr. Sam Whitaker on 2026-10-05 · source: Northside Urgent Care visit note

  Control — the same results, keeping only entries dated on or before 2026-10-04 (his chart before urgent care):
  ✓  cefuroxime 500 mg twice daily for 7 days  (cephalosporin) → clear — no record mentions this drug class
```

A hit blocks the order when it is an allergy line or describes a reaction (*hives, swelling, rash, allerg…*; negations like
*"no reaction"* are ignored). A hit that only mentions the class (*"Cefalexin was stopped"*) is shown with `·` and does not block by itself.

**7. Exact vs semantic.**

```
  `fact list --query` is exact: each name finds only the entries that contain it.
    name           entries found
    cephalosporin  2  allergy-ceph, visit-1005
    cefalexin      6  dictated note (extracted) ×4, allergy-ceph, visit-1005
    Keflex         1  triage-1005
    cefuroxime     0
    → “Triage 2026-10-05 (Jo Park, RN): lips swollen, itchy rash on the ches…” is found only by “Keflex”
    The class name alone finds 2 of 5 reaction records; all 8 names find 5.

$ memorylake search 'Has Walter ever reacted badly to an antibiotic?' --actors actor-… --projects proj-… --types fact --top-k 5
    1. ✓ Walter Brandt developed lip swelling and an itchy rash across the chest about two hours after the f…
    2. ✓ Encounter 2026-10-05 Northside Urgent Care (Dr. Sam Whitaker): acute sinusitis, started cefalexin 5…
    3. ✓ Walter Brandt’s symptoms were assessed as an allergic reaction to cefalexin. (as of 2026-10-05)
    4. ✓ Triage 2026-10-05 (Jo Park, RN): lips swollen, itchy rash on the chest after his first Keflex capsu…
    5. · Cefalexin was stopped for Walter Brandt. (as of 2026-10-05)
    4 of 5 reaction records in the top 5; 1 other hit(s); missed: Allergy: cephalosporins (cefalexin) - hives and l…
```

`--query` is case-insensitive (`penicillin` / `PENICILLIN` / `icill` all match) and has no synonyms (`penicillin` does not find
*"PCN"*). A blank query is rejected by the CLI.

**8. Decision record.**

```
$ memorylake fact add --project proj-… 'Order check 2026-10-12 (AI scribe for Dr. Lena Okafor): cefuroxime 500 mg twice daily for 7 days - blocked, recorded reaction to a cephalosporin.'
$ memorylake fact update fact-… --project proj-… --metadata '{"kind": "decision", "order": "cefuroxime", "verdict": "✗", "checked_at": "…", "seen": [{"id": "fact-…", "updated_at": "2026-10-12T06:14:14.809297Z", "owner": "Walter"}, …]}'
$ memorylake fact get fact-… --actor actor-…
    ✓ unchanged     fact-…  Allergy: cephalosporins (cefalexin) - hives and lip swelling.
    ✓ unchanged     fact-…  Encounter 2026-10-05 Northside Urgent Care (Dr. Sam Whitaker): acute …
    …
  Decision record written to out/order-check-walter-brandt-2026-10-12.md
```

## Wiring it into your own EHR integration

- **One actor per patient.** Pin each chart entry verbatim, one per fact, with date, signing clinician and source in
  `fact update --metadata`. `fact add` takes several texts in one call and returns their ids in order.
- **Events dated, status undated.** Write visits, labs and reactions with their date in the text. Write the current allergy list,
  problems and medications as plain status lines. The contradiction detector only flags what cannot be true *at the same time*.
- **Before `keep_fact`, read the whole conflict.** It keeps one fact and forgets all the others in that conflict, including
  ones on the same side. Use `dismiss` (changes nothing) or `fact update --text` (history kept in `fact trace`) when a
  conflict names more than one record you need.
- **For safety checks, list; don't rank.** `fact list --query` returns every matching entry across pages, so a check built
  on it cannot lose a hit to ranking. Give it a complete name list (generic, brand and class names). Use `search` for open questions.
- **Record what the AI saw**: the ids and `updated_at` of the entries behind each decision, pinned next to the decision.

This is a demo of memory behaviour, not clinical software. The drug-class lists here are deliberately short and incomplete.

## If something goes wrong

- **"✗ nothing raised on the new allergy"**: the detector check was deferred (it happens after many writes in a row). Run
  `python3 demo.py conflicts` a few minutes later, then `python3 demo.py` again and it resumes.
- **The dictated note yields fewer facts than above**: extraction varies by run (3–7 facts in ours). The order check does not depend on it.
- **`tls handshake eof` / `could not connect`**: a dropped connection. Read-only commands retry four times; otherwise re-run
  `python3 demo.py` and it resumes.
- **`401 service account not found`**: the key was deleted or belongs to the other deployment.
- **Conflict names differ from the ones above**: they are written by the server per run. The demo matches on fact ids only.

## Files

```
demo.py                  the runner; prints every CLI command it runs (and emits events for the web app)
data/story.json          the clinic, the patient, his chart, last week's urgent care visit, today's draft orders, drug-class names
web/server.py            local web companion (stdlib HTTP server + Server-Sent Events over demo.py)
web/static/              the page — no build step
out/                     the order-check record and runs.jsonl, one line per run (git-ignored)
```

All people, the clinic and the records are fictional.
