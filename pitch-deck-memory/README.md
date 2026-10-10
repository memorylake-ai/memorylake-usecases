# Pitch deck memory for marketing agencies

A runnable version of the MemoryLake use case
[*Give Marketing Agencies Pitch Deck Memory That Builds Slides Faster*](https://www.memorylake.ai/en/usecase/pitch-deck-memory-for-marketing-agencies),
built entirely on the [`memorylake` CLI](https://github.com/memorylake-ai/memorylake-cli).

**The problem.** Every new pitch deck starts close to scratch. The slides that worked, the case-study numbers and the
lessons from lost pitches live in old decks and debrief notes that the new-business team digs through by hand.

**What this demo shows.** Harbor and Pine (a fictional agency) is pitching Fernhill Cycles, an outdoor brand. Its past
pitches are in one MemoryLake project: three decks (.pptx) — an outdoor pitch it lost, a beverage pitch and a fintech pitch
it won — and the debrief written after each one (.docx).

- **Every slot of the new deck gets a cited slide.** For the case study, credentials, process, pricing and team slots,
  `search --types document` returns the decks **one passage per slide**. Each passage has a `range` such as
  `P4[4:0.06,0.09,0.93,0.45]`: **slide 4**, and the box on the slide. The demo quotes the slide's own words. Case study and credentials come from the pitch in the
  prospect's category. Process, pricing and team come from the newest pitch the agency won.
- **The debriefs reorder the outline.** The outline starts from the last outdoor pitch's order. Each debrief is searched the
  same way, and the sentence that says what to change is quoted with its file and page:
  *"put pricing after the process slide"* (lost), *"open with a case study from the prospect's own category, then
  credentials"* (won).
- **Approved content flags the stale slide.** The case-study figures were audited and changed. Pinning the approved
  figures as a project fact (`fact add`) makes MemoryLake's contradiction detector raise an `m2d` conflict **against the
  old deck**. The conflict carries the slide's text, which the demo matches back to **slide 4** of that .pptx. The outline
  keeps the slide and marks its figures for replacement. Two approved lines that agree with the decks raise nothing.
- **Every citation is checked against the original file**, not against the index that produced it. `proj doc download`
  fetches each cited file; its sha256 must equal the one pinned at upload (a Library attribute), and the quote must be on
  **that slide** of the .pptx (read from `ppt/slides/*.xml` in presentation order, standard library only) or in the .docx.
  As a control, the same quotes are checked against the neighbouring slide, and every one must fail.
- **The outline is exported** as Markdown plus a JSON file of citations. `python3 demo.py verify <file>` re-checks them later.

| Slot | Rule | Slide used |
|---|---|---|
| Case study | same category (outdoor) | Cedarline Outdoor 2025-09 deck, **slide 4**. Approved figures flag it as stale |
| Credentials | same category | Cedarline Outdoor 2025-09 deck, **slide 2** |
| Process | newest won pitch | Quillpay 2026-06 deck, **slide 4** |
| Pricing | newest won pitch | Quillpay 2026-06 deck, **slide 5** |
| Team | newest won pitch | Quillpay 2026-06 deck, **slide 6** (the Brightwell deck has no team slide) |

```
3 decks (.pptx) + 3 debriefs (.docx) ── lib upload --xattrs {client, category, won/lost, sha256} ──▶ proj doc import ──▶ pitch library

slot query ─▶ search --types document ─▶ one passage per slide: P<slide>[…] + text ─▶ rule (category / won) ─▶ deck · slide N · quote
debriefs   ─▶ search --types document ─▶ the "next time" sentence, file + page   ─▶ outline order
approved   ─▶ fact add ─▶ fact conflict list (m2d, slide text) ─▶ slide N of the old deck is out of date
                                     ▼
       proj doc download → sha256 = pinned? → quote on that slide of the .pptx?  →  out/fernhill-cycles-deck-outline.md
```

Runs in about 3 minutes on a free personal account. Most of that is importing the six files and waiting for the detector.
The only credential you need is a MemoryLake API key.

**Measured** (free account, while building this demo): **5 of 5** fresh runs (three of them in parallel) gave the same
result: 5/5 slots got the expected slide, 3/3 debrief lessons were found, the approved figures were flagged against
slide 4 and the other two lines stayed clean, **8/8** citations checked out, and the control rejected 5/5. MemoryLake ranked
the chosen slide first within its deck in 29 of 30 slot lookups (6 runs). In the other one it came second; the demo picks by slide type, so the
result did not change. The detector raised the conflict before the demo read it (about 35 s after the last write) every time.

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
cd memorylake-usecases/pitch-deck-memory

export MEMORYLAKE_API_KEY=sk-…
python3 demo.py
```

With `MEMORYLAKE_API_KEY` set, the demo logs into its own CLI profile under `./.memorylake-demo/`
(git-ignored) and leaves `~/.memorylake` alone. Without it, it uses your existing `memorylake auth login`.

| | |
|---|---|
| `MEMORYLAKE_BASE_URL` | API endpoint; default global, China is `https://app.memorylake.cn/openapi/memorylake` |
| `MEMORYLAKE_WORKSPACE` | Workspace id; default is the one remembered by `ws use`, else your default workspace |
| `python3 demo.py --reset` | Delete everything the demo created first, then start over |
| `python3 demo.py ask "Which case study had the lowest cost per acquisition?"` | The citable slides and debrief pages for any question |
| `python3 demo.py conflicts` | What the contradiction detector raised, with the slide number of each excerpt |
| `python3 demo.py verify out/fernhill-cycles-citations.json` | Download the originals again and re-check every exported citation |
| `python3 demo.py cleanup` | Delete the project (documents, approved-content facts, conflicts) and the six uploaded files |

It is safe to re-run. The project is found again by `custom_id` (prefix `mlu-pdm-`), and files by name. A file is
imported only if the project does not hold it yet, and an approved line is pinned only if that exact text is not there yet.
A re-run takes about 40 seconds.

## Or run it in the browser

```bash
python3 web/server.py        # http://127.0.0.1:8765 — standard library only
```

Paste the key, press **Run demo**, and watch each step. **Slide finder** shows one card per slot: the slide drawn from the
.pptx's own text, **MemoryLake's box over it**, the quote, and the candidates from the other decks with their category and
result. **Debriefs** shows the quoted sentence that moved each slide. **Approved content** shows the conflict and the slide it
points to. **Verify** and **Export** show the checks and the outline. **Ask the library** returns citable slides for any question. The terminal drawer shows
every `memorylake` command as it runs.

## What happens, step by step

### 1. Connect

`auth login --api-key` into the isolated profile, `team get`, and the workspace (`ws current`, else the first of `ws list`).

### 2. Pitch library

```
$ memorylake lib upload data/sources/2025-09-cedarline-outdoor-pitch.pptx --name 2025-09-cedarline-outdoor-pitch.pptx --on-conflict overwrite --xattrs '{"kind":"deck","client":"Cedarline Outdoor","vertical":"outdoor","pitched":"2025-09-18","outcome":"lost","debrief":"2025-09-cedarline-debrief.docx","sha256":"6a494646…"}'
…
$ memorylake proj doc import --project proj-… <six item ids> --wait
  · imported 6 file(s) (55s)

  file                                     client               category  pitched     result  slides sha256 (pinned)
  2025-09-cedarline-outdoor-pitch.pptx     Cedarline Outdoor    outdoor   2025-09-18  lost    6      6a494646af6d…
  2026-03-brightwell-kombucha-pitch.pptx   Brightwell Kombucha  beverage  2026-03-04  won     6      7ad76bcf4a52…
  2026-06-quillpay-fintech-pitch.pptx      Quillpay             fintech   2026-06-10  won     6      f626582f1f58…
  2025-09-cedarline-debrief.docx           Cedarline Outdoor    outdoor   2025-09-18  lost    —      77ba7a508d36…
  …
```

What the team knows about each pitch (client, category, date, won or lost, its debrief) and the sha256 of the bytes are
Library attributes. They are read back with `lib get`, so every rule and check below uses what the server holds.

### 3. Slide finder

```
  Slot: Case study — “case study for an outdoor brand launch with results”
$ memorylake search 'case study for an outdoor brand launch with results' --projects proj-… --types document --top-k 8
   → Cedarline Outdoor 2025-09 deck slide 4  “Case study: Northbay Kayak launch”  [outdoor, lost]
     Brightwell Kombucha 2026-03 deck slide 2  “Case study: Sundew Tea relaunch”  [beverage, won]
     Quillpay 2026-06 deck        slide 2  “Case study: Ledgerline referral campaign”  [fintech, won]
     ✓ use Cedarline Outdoor 2025-09 deck, slide 4 (box x 0.06–0.93, y 0.09–0.45)
       says: “The Northbay Kayak launch film reached 4.2 million views and cut cost per acquisition to 18 dollars.”
…
  Slide finder: 5/5 slots got the expected slide.
```

A deck hit's `items[]` holds **every slide**, each a `paragraph` whose `highlight.chunks[0]` is the slide's title and
body (bullets as `- ` / `• `) with `range` = `P<slide>[<slide>:x0,y0,x1,y1]`. The order follows the query. Items carry
no score, so the demo takes, from each deck, the first slide whose title is that kind of slide. Then a team rule picks
between decks. If a candidate was not first in its deck, the line says so (`#2 in its deck`).

### 4. Debriefs

```
  Starting order (from Cedarline Outdoor 2025-09 deck): Credentials → Pricing → Case study → Process → Team
$ memorylake search 'what to change next time in the pitch, and what the client said' --projects proj-… --types document --top-k 8
  · 3 debrief passage(s) from 3 debrief(s), each with a page and a box

  Cedarline Outdoor 2025-09 debrief (lost), p.1: “Next time: put pricing after the process slide.”
    → moved Pricing: Credentials → Case study → Process → Pricing → Team

  Brightwell Kombucha 2026-03 debrief (won), p.1: “Next time: open with a case study from the prospect's own category, then credentials.”
    → moved Case study: Case study → Credentials → Process → Pricing → Team

  Quillpay 2026-06 debrief (won), p.1: “Next time: close the pitch on the named team slide, after pricing.”
    ✓ already in place: Team
```

A short .docx comes back as one passage on page 1, holding the heading and every paragraph. The demo quotes the sentence that
contains the lesson. If a debrief is missing from the results, the lesson is printed as ✗ and not applied.

### 5. Approved content

```
$ memorylake fact add --project proj-… 'Approved case-study figures, audited 2026-08: the Northbay Kayak launch film reached 3.6 million views and cut cost per acquisition to 21 dollars.'
$ memorylake fact add --project proj-… 'Approved credentials line: Harbor and Pine has worked with eleven outdoor brands since 2019.'
$ memorylake fact add --project proj-… 'Approved pricing floor: Harbor and Pine does not quote a retainer below 35,000 dollars per month.'
$ memorylake fact conflict list --project proj-… --page-size 100
  · conflicts read 67s after the first write, 35s after the last

  “Approved case-study figures, audited 2026-08: the Northbay Kayak launch film reached 3.6 million views and cut cost per…”
    ⚠ m2d vs Cedarline Outdoor 2025-09 deck, slide 4: Fact 0 states the Northbay Kayak launch film reached 3.6 million views and CPA was $21 (audited 2026-08), but chunk 0 states 4.2 million vi…
      the slide says: “The Northbay Kayak launch film reached 4.2 million views and cut cost per acquisition to 18 dollars.”
    → the outline reuses that slide for Case study: keep the slide, replace its figures with the approved ones

  “Approved credentials line: …”      ✓ no conflict
  “Approved pricing floor: …”         ✓ no conflict
```

The detector checks each new fact against the documents in the project. A conflict's `file_chunks[]` carries the slide's
text and the deck's name but **no slide number**, so the demo finds that text in the downloaded .pptx. Writes are 15 s apart,
because back-to-back writes are checked minutes later as one batch. The demo reads the conflicts once the expected one is
in and at least 30 s after the last write; it waits at most 150 s and otherwise says the check may have been deferred
(`python3 demo.py conflicts` later).

### 6. Verify

```
  [1] Cedarline Outdoor 2025-09 deck slide 4: ✓ sha256 matches · ✓ the quote is on slide 4 of the file
  …
  [8] Quillpay 2026-06 debrief page 1: ✓ sha256 matches · ✓ the quote is on page 1 of the file

  8 of 8 citation(s) check out against the original files.
  Control: the same quotes checked against the neighbouring slide → 5/5 rejected (the check can fail).
```

### 7. Export

`out/fernhill-cycles-deck-outline.md`: the outline with one row per slide (which deck and slide to reuse, what it says,
the approved figures to swap in), the debrief sentences behind the order, and the citations.
`out/fernhill-cycles-citations.json`: file, document id, slide or page, box, quote, sha256 and verification per citation.

## Wiring it into your own pitch library

- **One passage per slide.** You do not need to split decks yourself. Import the .pptx and a document search returns its
  slides with `P<slide>` ranges, in query order. Ask with `--types document`, because `--top-k` is shared across types.
- **Put what you know about the pitch on the file.** `lib upload --xattrs` (or `lib xattr set` later) keeps client,
  category, result and sha256 next to the bytes. Rules like "same category" or "won pitches only" read them back with `lib get`.
- **Pin approved numbers as facts.** The detector compares each new fact with the decks and debriefs already in the
  project. That is how an old slide with old numbers gets found. It only checks **new** facts: a deck imported after the
  fact is not compared with it. To re-check, add the fact again.
- **Name slides consistently** ("Case study: …", "Pricing: …"). The slide type comes from the title, which is in every passage.

## If something goes wrong

- **A slot shows ✗ or another deck's slide**: the ranking of slides is query-dependent. Run
  `python3 demo.py ask "<the slot query>"` to see what MemoryLake returns, in its order.
- **"not raised within 150s"**: the detector deferred the check. Run `python3 demo.py conflicts` a few minutes later.
- **A quote is not found on its slide in `verify`**: the passage text comes from the rendered slide. Non-ASCII characters
  and symbols can come back changed, so keep slide text plain where you need verbatim checks. This demo's files are ASCII.
- **`tls handshake eof` / `could not connect`**: a dropped connection. Read-only commands retry four times; otherwise
  re-run `python3 demo.py` and it resumes.
- **`401 service account not found`**: the key was deleted or belongs to the other deployment.

## Files

```
demo.py                  the runner; prints every CLI command it runs (and emits events for the web app)
data/sources/*.pptx      the three past pitch decks
data/sources/*.docx      the three debriefs
data/decks.json          the slides and paragraphs of every file, plus client, category, date and result
data/make_sources.py     regenerates data/sources/ from decks.json (needs python-pptx and python-docx; the demo does not)
data/story.json          the prospect, the slots and their rules, the debrief lessons and the approved content
web/server.py            local web companion (stdlib HTTP server + Server-Sent Events over demo.py)
web/static/              the page — no build step
out/                     the outline, the citations, the downloaded originals and runs.jsonl (git-ignored)
```

All names, organisations and documents are fictional.
