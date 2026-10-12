# Newsroom memory: scanned records and conflicting sources

A runnable version of the MemoryLake use case
[*Give Journalists AI That Holds the Full Story, Not Just the Last Prompt*](https://www.memorylake.ai/en/usecase/ai-memory-for-journalists-and-newsrooms),
built entirely on the [`memorylake` CLI](https://github.com/memorylake-ai/memorylake-cli).

**The problem.** *Sources contradict each other. Facts shift.* An investigation builds over weeks. The documents often come back
from a records request as **scans**: pictures of paper with no text to search or copy. Interview claims sit in notebooks. Nobody
notices that the spokesperson's line contradicts page 1 of the city's own inspection until an editor does (or a reader).

**What this demo shows.** Dana Ruiz, a reporter at *The Marlow Ledger* (fictional), is investigating the Harbor Street Bridge.

1. **Scans become quotable records.** The records request returns three PDFs. **There is no text layer in them**: 4 page images and
   **0 fonts** in total, which the demo counts in the files' bytes. After `proj doc import`, `proj doc inspect` gives a signed link to MemoryLake's
   **OCR layout**: one JSON line per block read off each page (`page_num`, `element_in_page`, `type`, `content`). The demo
   reads it back (the link is used, never printed) and compares it with the text the scans were printed from: **30 of 30 lines word for word**.
   This is the first demo here built on image-only PDFs and the first to read the OCR layout.
2. **Ask a question, get the record, the page and the line.** *"What condition rating did the inspectors give pier 4?"*:
   `search --types document` returns each scan **page** with its box on the page. The 2026 inspection's page 1 has
   *"Condition rating: POOR (4 of 9)."*, the 2024 one *"FAIR (5 of 9)"*. `proj doc list --name 2024` finds the earlier inspection by name.
3. **Every claim is pinned with who said it.** Four interview claims, `fact add` word for word, then `fact update --metadata` with
   the source, their role, the date and how it was said (phone, in person, council meeting).
4. **Contradictions are flagged, with attribution on both sides.** MemoryLake's contradiction detector checks every new fact against
   the project's documents (`m2d`) and its other facts (`m2m`). About 30 seconds after the last claim:
   - the spokesperson's *"passed its latest inspection with no structural defects"* ⟂ **inspection-2026-03-14.pdf, page 1**,
     *"Condition rating: POOR (4 of 9)."*
   - the spokesperson's *"about 800,000 dollars"* ⟂ **capital-plan-memo-fy2027.pdf, page 1**, *"Requested amount: 2.4 million dollars."*
   - the unnamed engineer's *"rated … as poor"* ⟂ the spokesperson's *"passed …"*: **source against source**
   - the council member's traffic count: **no flag** (no record covers it)

   The conflict gives the facts and the text of the record excerpt, **but no page number**. The demo finds the page by matching the
   excerpt against the OCR layout (4 of 4 excerpts placed), and takes the source of each claim from the fact's metadata.
5. **Before filing, every quote is checked against the page it cites**, character for character, in the OCR layout. Two quotes
   match. One planted misquote, *"POOR (3 of 9)"*, is caught: *page 1 says "Condition rating: POOR (4 of 9)."*

```
setup     ── lib mkdir mlu-nws-marlow-ledger · lib mkdir records-request-2026-0917 --parent … · proj create
records   ── lib upload <scan>.pdf --parent <folder> --xattrs '{"request":…,"released":…}' ×3
             proj doc import --project <desk> --wait <items>
             proj doc inspect --project <desk> <docs>       (→ llm_artifact.ocr_layout_jsonl_s3_uri, read, never printed)
find      ── search "<question>" --projects <desk> --types document --top-k 3     (page + box per hit)
             proj doc list --project <desk> --name 2024
claims    ── fact add --project <desk> "<claim>" · fact update <id> --project <desk> --metadata '{"source":…,"said_on":…}'  ×4, 15 s apart
flags     ── fact conflict list --project <desk>                          (m2d: fact ⟂ document excerpt · m2m: fact ⟂ fact)
quotes    ── the OCR layout, page by page (no extra call)
```

**Watch it run** (real recordings, unedited):
[CLI demo, 3:23](https://github.com/memorylake-ai/memorylake-usecases/releases/download/newsroom-memory-v1/newsroom-memory-cli-demo.mp4) · [Web companion demo, 4:47](https://github.com/memorylake-ai/memorylake-usecases/releases/download/newsroom-memory-v1/newsroom-memory-web-demo.mp4)

Runs in about 2½ minutes on a free personal account: about 25 s to import the three scans, about 35 s for the contradiction detector
after the last claim, and 15 s between claims (back-to-back writes delay detection by several minutes). Nothing here depends on an
LLM answer. The only credential you need is a MemoryLake API key.

**Measured** (every complete run while building this demo, free account): the OCR layout matched the source text **30/30 lines in
every run**. In 7 of 7 fresh runs (including the two recordings) the detector raised exactly the three expected flags (2 × m2d, 1 × m2m),
nothing on the traffic count, and nothing unplanned. In the runs we timed, the flags were in 32–34 s after the last claim. **Negative control:** with the spokesperson's cost claim changed
to agree with the records ("about 2.4 million dollars"), the detector raised no m2d for it. The demo waited the full 150 s and
printed `✗ m2d moss-cost`. The question in step 2 put the 2026 inspection's page 1 at rank 1 or 2 (the 2024 inspection, which
answers it too, took the other place).

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
cd memorylake-usecases/newsroom-memory

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
| `python3 demo.py conflicts` | The flags, now, with both sources |
| `python3 demo.py ask "What did the city say the repair will cost?"` | One search: claims (with who said them) and record pages |
| `python3 demo.py quote "Repair estimate: 2.4 million dollars." --file inspection-2026-03-14.pdf --page 2` | Check a quote against a scan |
| `python3 demo.py cleanup` | Delete the desk project (records, claims, flags) and the newsroom's Library folder |
| `--show-json` | Print the raw JSON the CLI returns, with pre-signed URLs masked |

It is safe to re-run. The project is found again by `custom_id` (prefix `mlu-nws-`), the folders and scans by name, and the claims by
their exact text. A re-run pins nothing new, so the detector has nothing to check: it reads the flags raised when the claims were
first pinned and says so.

## Or run it in the browser

```bash
python3 web/server.py        # http://127.0.0.1:8765 — standard library only
```

![Web companion — the records arrive: three PDFs with page images and 0 fonts, and what MemoryLake's OCR read off each page](https://github.com/memorylake-ai/memorylake-usecases/raw/main/newsroom-memory/web/screenshot-records.png)

![Web companion — flags: the spokesperson's claims against page 1 of the inspection and the capital-plan memo, each side attributed](https://github.com/memorylake-ai/memorylake-usecases/raw/main/newsroom-memory/web/screenshot-flags.png)

Paste the key, press **Run demo**, and watch the scans arrive (pages, images, fonts, what the OCR read), the question answered
with a page and a line, the claims pinned, the flags with both sources, and the quote check. **Ask** searches claims and records
together, and checks any quote against any page. The terminal drawer shows every `memorylake` command as it runs.

[Watch the web companion demo (mp4, 4:47)](https://github.com/memorylake-ai/memorylake-usecases/releases/download/newsroom-memory-v1/newsroom-memory-web-demo.mp4).

## What happens, step by step

### 1. Connect

`auth login --api-key` into the demo's own profile, `team get`, and the workspace (`ws current`, else your first workspace).

### 2. Set up — a records folder, one project

```
$ memorylake lib mkdir mlu-nws-marlow-ledger --parent MY_SPACE --on-conflict deny
$ memorylake lib mkdir records-request-2026-0917 --parent <newsroom folder> --on-conflict deny
$ memorylake proj create --name 'Marlow Ledger desk - Harbor Street Bridge' --custom-id mlu-nws-desk --description '…'
```

One project holds both the records and the claims. The contradiction detector compares a new fact only with documents and facts
in the same scope.

### 3. The records arrive — three scans, no text layer

```
  Released 2026-10-02 by City of Marlow, City Clerk — what is inside each file:
    inspection-2026-03-14.pdf        2 page(s) · 2 page image(s) · 0 fonts  → no text layer: a scan
    inspection-2024-02-20.pdf        1 page(s) · 1 page image(s) · 0 fonts  → no text layer: a scan
    capital-plan-memo-fy2027.pdf     1 page(s) · 1 page image(s) · 0 fonts  → no text layer: a scan
$ memorylake lib upload data/records/inspection-2026-03-14.pdf --parent <folder> --xattrs '{"request": "2026-0917", "released": "2026-10-02"}'
…
$ memorylake proj doc import --project <desk> --wait <item> <item> <item>
  · 3 scan(s) uploaded, 3 imported and processed in 25s
$ memorylake proj doc inspect --project <desk> <doc> <doc> <doc>

    inspection-2026-03-14.pdf  (paragraph_title, text)
      page 1: 9 lines read · 9/9 identical to the paper
        | CITY OF MARLOW - BRIDGE INSPECTION RECORD
        | Structure: Harbor Street Bridge (No. 14)
        | Inspection date: 2026-03-14
      page 2: 5 lines read · 5/5 identical to the paper
    …
  30 of 30 typed lines came back word for word from 3 scans with no text layer
```

`proj doc inspect` returns, per document, `figures`, `subtables`, a `summary` and `llm_artifact.ocr_layout_jsonl_s3_uri`, a
**pre-signed** storage URL that works for anyone holding it until it expires. The demo downloads it with `urllib` and keeps only the
lines (`out/ocr/<file>.jsonl`); `--show-json` masks it. One OCR element can hold several lines. In the memo, the item and the requested
amount came back as a single element, so the demo splits elements on newlines before comparing.

The scans in `data/records/` were made by `data/make_scans.py` (Pillow, Courier, a slight rotation, speckle and blur) from the
text in `story.json`. That is how the demo knows what the paper said.

### 4. Find the line

```
$ memorylake search 'What condition rating did the inspectors give pier 4?' --projects <desk> --types document --top-k 3
    1. inspection-2026-03-14.pdf · page 1 · box [0.08, 0.07, 0.88, 0.33] · 9 lines
         | Condition rating: POOR (4 of 9).
    1. inspection-2026-03-14.pdf · page 2 · box [0.08, 0.07, 0.88, 0.23] · 5 lines
         | Prior inspection 2024-02-20 rated pier 4: FAIR (5 of 9).
    2. inspection-2024-02-20.pdf · page 1 · box [0.08, 0.07, 0.88, 0.3] · 8 lines
         | Condition rating: FAIR (5 of 9).
    3. capital-plan-memo-fy2027.pdf · page 1 · box [0.07, 0.07, 0.88, 0.3] · 8 lines

  the line: “Condition rating: POOR (4 of 9).”  — inspection-2026-03-14.pdf, page 1, rank 1  ✓

$ memorylake proj doc list --project <desk> --name 2024
    inspection-2024-02-20.pdf
```

Each document hit has one `paragraph` item per page, and each chunk's `range` is `P<page>[<page>:x0,y0,x1,y1]` (fractions of the page,
origin top-left). The chunk text is the OCR text. `--name` on `proj doc list` is a case-insensitive **substring** filter: `INSPEC`
matches, `inspct` does not.

### 5. Interviews — each claim pinned with who said it

```
$ memorylake fact add --project <desk> 'The Harbor Street Bridge passed its latest inspection with no structural defects.'
$ memorylake fact update <fact> --project <desk> --metadata '{"kind": "claim", "key": "moss-passed", "source": "Lena Moss", "role": "city spokesperson", "said_on": "2026-10-06", "medium": "phone interview"}'
…
  Claims on the record (4 pinned now, 0 from an earlier run):
    • Lena Moss (city spokesperson), phone interview, 2026-10-06
        “The Harbor Street Bridge passed its latest inspection with no structural defects.”
    • Lena Moss (city spokesperson), phone interview, 2026-10-06
        “Repairing the Harbor Street Bridge will cost about 800,000 dollars.”
    • Source E (structural engineer, not for attribution), in-person interview, 2026-10-08
        “Inspectors rated the pier 4 bearing of the Harbor Street Bridge as poor.”
    • Ray Ostrowski (city council member), council meeting, 2026-10-09
        “The Harbor Street Bridge carries about 9,000 vehicles a day.”
```

`fact add` does not take metadata, so each claim gets a `fact update --metadata` right after (the metadata does not affect detection).
The claims are written **15 s apart** and **after** the records are imported: the detector compares a new fact with the documents that
are already in the project, and it does not re-check older facts when a new document arrives.

### 6. Flags — contradictions, with both sources

```
  · waiting for MemoryLake's contradiction detector (up to 150s after the last claim)
$ memorylake fact conflict list --project <desk>
  · 3 flag(s) 33s after the last claim

  ⚠ m2d · knowledge · claim vs record — Latest inspection defect-free vs records
      said by  Lena Moss (city spokesperson), phone interview, 2026-10-06
               “The Harbor Street Bridge passed its latest inspection with no structural defects.”
      record   inspection-2026-03-14.pdf, page 1 (released 2026-10-02)
               “Condition rating: POOR (4 of 9).”  (+8 more lines in the excerpt)
      record   capital-plan-memo-fy2027.pdf, page 1 (released 2026-10-02)
               “Item 7: Harbor Street Bridge pier 4 bearing repair.”  (+7 more lines in the excerpt)

  ⚠ m2d · knowledge · claim vs record — Bridge repair cost amount mismatch
      said by  Lena Moss (city spokesperson), phone interview, 2026-10-06
               “Repairing the Harbor Street Bridge will cost about 800,000 dollars.”
      record   capital-plan-memo-fy2027.pdf, page 1 (released 2026-10-02)
               “Requested amount: 2.4 million dollars.”  (+7 more lines in the excerpt)

  ⚠ m2m · logical · claim vs claim — Inspection outcome contradicts pier 4 rating
      said by  Source E (structural engineer, not for attribution), in-person interview, 2026-10-08
               “Inspectors rated the pier 4 bearing of the Harbor Street Bridge as poor.”
      said by  Lena Moss (city spokesperson), phone interview, 2026-10-06
               “The Harbor Street Bridge passed its latest inspection with no structural defects.”

  Against what the reporting predicts:
    ✓ m2d  moss-passed ⟂ inspection-2026-03-14.pdf
    ✓ m2m  engineer-poor ⟂ moss-passed
    ✓ m2d  moss-cost ⟂ inspection-2026-03-14.pdf or capital-plan-memo-fy2027.pdf
    ✓ no flag  ostrowski-traffic (no record covers it)
  record excerpts traced back to a page through the OCR layout: 4/4
```

A conflict carries `category` (`m2d` / `m2m`), `fact_ids`, `fact_snapshots` and, for `m2d`, `file_chunks[]` with the excerpt's text,
`document_id` and `document_name`, **but no page**. The flag's name and description are written by the server's model and change from
run to run. The demo only checks category and fact ids. The engineer's claim **agrees** with the records, so it gets no `m2d`. It
contradicts the spokesperson, so it gets an `m2m`. Which record excerpts the detector cites for a flag varies between runs (page 1
of the inspection, the memo, or both). The step waits until every expected flag is in and the last claim is at least 30 s old, at
most 150 s. If it gives up, it says so, and `python3 demo.py conflicts` reads the flags later.

### 7. Before filing — every quote checked against the page it cites

```
  Every quote of a record in the draft, checked word for word against the page it cites:
    ✓ “Condition rating: POOR (4 of 9).”  — inspection-2026-03-14.pdf, page 1
    ✓ “Repair estimate: 2.4 million dollars.”  — inspection-2026-03-14.pdf, page 2
    ✗ “Condition rating: POOR (3 of 9).”  — inspection-2026-03-14.pdf, page 1
        page 1 says: “Condition rating: POOR (4 of 9).”

  2 of 3 quotes match the scan; the misquote is caught before the story is filed
```

The third quote is planted to show the check failing. `python3 demo.py quote "…" --file … --page N` runs the same check on anything.

## Wiring it into your own newsroom tools

- **Image-only PDFs need no OCR step of your own.** Import them like any PDF. Search hits come back per page with a box, and
  `proj doc inspect` exposes the OCR layout if you want the lines themselves. Treat every `*_s3_uri` / `persist_path` in `inspect`
  output as a credential: it is a pre-signed link.
- **Put the claims in the same project as the records, after the records.** The detector checks new facts against what is already
  there; it does not re-check old facts when a new document arrives (re-pin a claim, `fact add` + `fact delete` of the old one, to re-check it).
- **Attribution is yours to store.** Put the source, the date and the medium in the fact's metadata; the conflict gives you the fact
  ids to look them up. Store the exact words in the fact text: the detector reads the text, not the metadata.
- **Undated present-tense claims are what the detector compares.** In our probes for an earlier demo, two statements that each
  carried their own date were not flagged (both can be true). Write claims the way they were made.
- **Map excerpts to pages yourself.** `file_chunks` has no page number. Match the excerpt against the OCR layout (or against the
  `range` of a search hit for the same text).
- **Space your writes.** Several facts written back to back push detection out by minutes. 15 s apart, each flag arrived within about 35 s.

## If something goes wrong

- **`· N flag(s) 150s after the last claim — some may still be on the way`**: the detector was slow this time. Run
  `python3 demo.py conflicts` a few minutes later.
- **`error: could not read the OCR layout`**: the signed link expired or the download failed. Re-run; `inspect` hands out a fresh link.
- **A line shows `n-1/n identical to the paper`**: the OCR misread a character. The demo prints that as it is. The quote check
  would then fail for that line, which is the point of the check.
- **`DRIVE_ITEM_CONFLICT` on `lib mkdir`**: a folder named `mlu-nws-marlow-ledger` exists but the demo did not find it (for example
  under another workspace). Delete it, or run `python3 demo.py cleanup`.
- **`tls handshake eof` / `could not connect`**: a dropped connection. Read-only commands and `fact update` retry four times; folder,
  project, upload and import check whether the call went through before retrying. Otherwise re-run `python3 demo.py`; it resumes.
- **`401 service account not found`**: the key was deleted or belongs to the other deployment.

## Files

```
demo.py                  the runner; prints every CLI command it runs (and emits events for the web app)
data/story.json          the newsroom, the records (with the text they were printed from), the claims and their sources, the draft's quotes
data/records/            the three scanned PDFs (image-only)
data/make_scans.py       how the scans were made (Pillow; not needed to run the demo)
web/server.py            local web companion (stdlib HTTP server + Server-Sent Events over demo.py)
web/static/              the page — no build step
out/                     runs.jsonl and the OCR layouts read back (git-ignored)
```

All people, the newspaper, the city and the records are fictional.
