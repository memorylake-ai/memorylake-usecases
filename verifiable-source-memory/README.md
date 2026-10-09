# Verifiable source memory for agents

A runnable version of the MemoryLake use case
[*Build Agents That Cite Verifiable Sources for Every Fact They State*](https://www.memorylake.ai/en/usecase/memory-patterns-for-agents-that-need-verifiable-sources),
built entirely on the [`memorylake` CLI](https://github.com/memorylake-ai/memorylake-cli).

**The problem.** The agent states a fact. The user, or compliance, asks "where did you get that?". In most agent memory the
answer is a chunk of text with no address. In legal, medical, financial or government work, that makes the agent unusable.

**What this demo shows.** Larch Valley Credit Union (fictional) is reviewing its member-support agent before launch.
Compliance has one rule: every statement the agent makes must cite a source a person can check by hand.

- **The published documents** go into MemoryLake with their **provenance**: two versions of the fee schedule, the funds
  availability policy and the wire transfer procedures, all as PDFs. The provenance is stored as Library attributes
  (`lib upload --xattrs`): title, version, effective date, the version each one replaces, publisher, and the
  **sha256 of the exact bytes**.
- **A member**, Dana Whitlock, has one chat on record (an actor and a conversation; MemoryLake extracts facts from it)
  and one note from Member Services, pinned word for word (`fact add --actor`).
- **Four member questions** come in. The agent's draft answers are scripted, because LLM calls are not available on a
  free account. Everything checked against them is real:
  - every **document claim** is cited to the passage that states it, with the document, version, **page number, box on
    the page** and the sentence, verbatim. These come from `search`: each hit's `items[].highlight.chunks[]` carries the
    passage text and a `range` such as `P2[2:0.12,0.17,0.88,0.28]`;
  - every **member claim** is cited to the fact that states it, with **how that fact came to exist**. `fact trace` tells a
    fact MemoryLake extracted from her own message (quoted, with its time) apart from one that staff stated through the API;
  - a claim that **only a superseded version** supports is replaced with the current one, and a claim that **nothing**
    supports is dropped before the answer goes out.
- **Every citation is then checked against the original file**, not against the index that produced it.
  `proj doc download` fetches each cited PDF; its sha256 must equal the one pinned at ingest, and the excerpt must be in
  **that page's text layer**. As a control, the same check runs on the claims that were not sent, and comes out ✗.
- **The citations are exported** as JSON (source link, version, page, box, excerpt, sha256, verification) plus a
  natural-language rendering. `python3 demo.py verify <file>` re-checks them any time later.

| Question | Draft claims | Outcome |
|---|---|---|
| What is the overdraft fee, and how many can I be charged in one day? | 29 dollars per item · at most 3 a day · no fee at 50 dollars or less | ✓ ✓ ✓ all cited to *Schedule of Fees and Charges* v2026.1, p. 1 |
| I deposited a 7,000 dollar check today. When can I use the money? | first 225 dollars next day · the rest on the 7th business day | ✓ ✓ cited to a **table row** of the *Funds Availability Policy*, p. 2 |
| How much does an international wire cost, and when do I have to send the request? | 45 dollars · request by 1:00 pm Eastern · can be recalled at any time until credited | ⚠ 45 dollars is only in the **superseded** 2025 schedule → replaced by 40 dollars (v2026.1 p. 2) · ✓ · ✗ **no source** → dropped |
| Will my debit card purchases still go into overdraft? | her opt-out is processed (LV-88213) · coverage applies only to members who opt in | ✓ cited to the **staff note** (stated through the API) · ✓ fee schedule p. 1; the facts **extracted from her chat** are shown with the message they came from |

```
PDFs ── lib upload --xattrs {version, effective, superseded, sha256…} ──▶ proj doc import ──▶ project: published documents
Dana ── actor create · conv create --project <chats> · msg append  ──▶ extracted facts      fact add --actor ──▶ staff note

question ─▶ search --projects <documents> --types document   → passages with page + box  ─┐
         └▶ search --projects <chats> --actors <dana> --types fact → facts · fact trace   ─┤→ each claim: ✓ cite / ⚠ superseded / ✗ none
                                                                                           ▼
          proj doc download → sha256 = pinned? → excerpt on that page's text layer?  →  out/citations-2026-10-08.json
```

**Watch it run** (real recordings, unedited):
[CLI demo, 3:10](https://github.com/memorylake-ai/memorylake-usecases/releases/download/verifiable-source-memory-v1/verifiable-source-memory-cli-demo.mp4) · [Web companion demo, 4:53](https://github.com/memorylake-ai/memorylake-usecases/releases/download/verifiable-source-memory-v1/verifiable-source-memory-web-demo.mp4)

Runs in about 2–3 minutes on a free personal account (most of it is importing the four PDFs and MemoryLake reading
the chat). The only credential you need is a MemoryLake API key.

**Measured** (all runs while building and recording this demo, free account): in **7 of 7** fresh runs (both recordings included), the 4 questions gave the same
outcome: 9 citations, each with the same page, and **9 of 9** checked out against the downloaded originals. The 2 dropped
claims were ✗ in the control each time. Before the build, 5 questions searched 3 times each
returned their passages in the same order 15 of 15 times. The chat produced at least one extracted fact about the overdraft opt-out every time (1 or 2), with
different wording each run. That is why the claim itself is cited to the staff note, which is pinned word for word.

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
cd memorylake-usecases/verifiable-source-memory

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
| `python3 demo.py ask "What does a stop payment order cost?"` | The citable passages for any question: document, version, page, box, text |
| `python3 demo.py verify out/citations-2026-10-08.json` | Download the originals again and re-check every exported citation |
| `python3 demo.py cleanup` | Delete both projects (documents and facts), Dana's chat, the two actors and the four uploaded PDFs |

It is safe to re-run. Everything is found again by `custom_id` (prefix `mlu-vsm-`) or by file name. A document is imported
only if the project does not hold it yet, chat messages are appended only up to the ones already there, and the staff note
is pinned only if that exact text is not on Dana yet. If `data/provenance.json` changed since the upload, the Library
attributes are updated in place (`lib xattr set`).

## Or run it in the browser

```bash
python3 web/server.py        # http://127.0.0.1:8765 — standard library only
```

![Web companion — the draft checked claim by claim: a superseded figure replaced, an unsupported claim dropped, the answer as sent](https://github.com/memorylake-ai/memorylake-usecases/raw/main/verifiable-source-memory/web/screenshot-answer.png)

![Web companion — a citation to a table row, with the cited page drawn from the PDF and MemoryLake's box over the table](https://github.com/memorylake-ai/memorylake-usecases/raw/main/verifiable-source-memory/web/screenshot-table-citation.png)

[Watch the web companion demo (mp4, 4:53)](https://github.com/memorylake-ai/memorylake-usecases/releases/download/verifiable-source-memory-v1/verifiable-source-memory-web-demo.mp4).

Paste the key, press **Run demo**, and watch the sources and their provenance, Dana's chat and memory, then each answer:
the draft checked claim by claim, the answer as sent, and one card per citation. Each card draws the **cited page from the
PDF's own text positions, with MemoryLake's box laid over it**, so you can see that the box lands on the passage it quotes.
**Verify** shows the checks against the originals and the control; **Export** shows the JSON. **Ask the sources** returns
the citable passages for any question. The terminal drawer shows every `memorylake` command as it runs.

## What happens, step by step

**1. Connect**: validate the key, pick a workspace.

**2. Sources, with their provenance.** Each PDF is uploaded with its provenance and the sha256 of its bytes, then the four
are imported together:

```bash
memorylake proj create --name 'Larch Valley CU — published member documents' --custom-id mlu-vsm-sources …
memorylake lib upload data/sources/larch-fee-schedule-2026.pdf --name larch-fee-schedule-2026.pdf --on-conflict overwrite \
  --xattrs '{"title":"Schedule of Fees and Charges","doc_type":"fee-schedule","version":"2026.1","effective":"2026-01-01","supersedes":"2025.1","publisher":"Larch Valley Credit Union, Member Services","sha256":"14f113ae714cbfcc791b15760ab633c00d9d6bdf5721474eec8ba61f312bfc19"}'
…
memorylake proj doc import --project <documents> <item> <item> <item> <item> --wait
memorylake lib get <item>          # the provenance is read back from x_attrs

  document                                 version  effective   on 2026-10-08  pages sha256 (pinned)
  larch-fee-schedule-2025.pdf              2025.1   2025-01-01  superseded     2     1f87c13baf61…
  larch-fee-schedule-2026.pdf              2026.1   2026-01-01  current        2     14f113ae714c…
  larch-funds-availability-policy.pdf      2024.3   2024-07-01  current        2     3bfc5f86d0c1…
  larch-wire-transfer-procedures.pdf       2026.2   2026-06-15  current        1     f246f56bc29d…
```

**3. Member memory.** Dana and the agent are actors; the chat goes into its own project, so nothing extracted from it mixes
with the published documents. The staff note is pinned on Dana:

```bash
memorylake conv create --custom-id mlu-vsm-chat-2026-09-30 --project <chats> --actors <dana>,<agent> --kind DIRECT …
memorylake conv msg append <conv> --actor <dana> --timestamp 2026-09-30T15:10:50Z --text 'Hi, this is Dana Whitlock. … Please turn off overdraft coverage on my debit card. …'
…
memorylake conv cook-status <conv>
memorylake fact add --actor <dana> 'Member Services note (Jordan Pike): Dana Whitlock'"'"'s opt-out of overdraft coverage for one-time debit card and ATM transactions has been processed. Confirmation LV-88213.'
```

**4. Answers, every claim cited.** One document search per question; every passage it returns has a page and a box. A claim
is cited to a passage from a **current** version that states it; the sentence (or table row) is the excerpt:

```
$ memorylake search 'How much does an outgoing international wire cost and what is the cutoff time for international wires?' --projects <documents> --types document --top-k 8
  · 9 passage(s) retrieved from 4 document(s), each with a page and a box

  Draft from the agent (scripted), checked claim by claim:
    ⚠ An outgoing international wire costs 45 dollars.
        only a superseded version says this: larch-fee-schedule-2025.pdf v2025.1 (superseded since 2026-01-01)
      → replaced with the current version: ✓ An outgoing international wire costs 40 dollars.
    ✓ International wires must be requested by 1:00 pm Eastern time.
    ✗ You can recall an international wire at any time until the receiving bank credits it.
        no passage or fact states this → dropped from the answer

  Answer as sent, every sentence cited:
    An outgoing international wire costs 40 dollars. [6]
    International wires must be requested by 1:00 pm Eastern time. [7]

    [6] larch-fee-schedule-2026.pdf · v2026.1 (effective 2026-01-01) · p.2 · box x 0.11–0.83, y 0.08–0.17
        says: “Outgoing international wire: 40 dollars.”
    [7] larch-wire-transfer-procedures.pdf · v2026.2 (effective 2026-06-15) · p.1 · box x 0.11–0.88, y 0.07–0.39
        says: “Outgoing international wires must be requested by 1:00 pm Eastern time and are usually credited to the beneficiary bank within 2 business days.”
```

A table passage comes back as CSV (`Situation,First available,Remainder available` …), with the table's box; the cited excerpt
is the row. The member claim is cited to a fact, with its origin from `fact trace`:

```
    [8] Dana's memory · fact fact-… (on her profile)
        “Member Services note (Jordan Pike): Dana Whitlock's opt-out of overdraft coverage for one-time debit card and ATM transactions has been processed. Co…”
        stated through the API by staff (`fact add`), recorded 2026-10-09T18:14:14Z
    [9] larch-fee-schedule-2026.pdf · v2026.1 (effective 2026-01-01) · p.1 · box x 0.11–0.87, y 0.07–0.34
        says: “Overdraft coverage for one-time debit card and ATM transactions applies only to members who opt in.”
        also remembered: “Dana Whitlock wants overdraft coverage turned off for the debit card and would rather the card be declined wh…”
          extracted from the chat of 2026-09-30 (MemoryLake read it)
          source message 2026-09-30 15:10Z (member): “Hi, this is Dana Whitlock. Two small card purchases overdrew my account last month and I paid two o…”
        not cited: larch-fee-schedule-2025.pdf v2025.1 p.1 says “Overdraft coverage for one-time debit card and ATM transactions appli…” (superseded)
```

**5. Verify against the originals.** Each cited document is downloaded to `out/originals/`. Its sha256 must equal the pinned
one, and the excerpt must be in that page's text layer, read from the PDF bytes by `pdf_pages_text()` in `demo.py`:

```
$ memorylake proj doc download <doc> --project <documents> -o out/originals/<doc>.pdf --force
  [1] larch-fee-schedule-2026.pdf p.1: ✓ sha256 14f113ae714c… matches the pinned one · ✓ the excerpt is in page 1's text
  [4] larch-funds-availability-policy.pdf p.2: ✓ sha256 3bfc5f86d0c1… matches the pinned one · ✓ every cell of the row is in page 2's text
  [8] fact fact-…: ✓ still in Dana's memory, word for word
  …
  9 of 9 citation(s) check out against the original files and current memory.

  Control — the claims that were not sent, checked the same way:
  ✗ “international wire: 45 dollars” is on no page of a current document — only on larch-fee-schedule-2025.pdf p.2 (superseded)
  ✗ “recall an international wire at any time” is on no page of a current document
```

Edit an excerpt or a page number in the exported file and `verify` says so:

```
  [1] larch-fee-schedule-2026.pdf p.1: ✓ sha256 14f113ae714c… matches the pinned one · ✗ the excerpt is NOT in page 1's text
  [6] larch-fee-schedule-2026.pdf p.1: ✓ sha256 14f113ae714c… matches the pinned one · ✗ the excerpt is NOT in page 1's text (found on page 2)
  7 of 9 citation(s) check out against the original files and current memory.
```

**6. Export.** `out/citations-2026-10-08.json` holds one record per citation:

```json
{
  "n": 1,
  "claim": "The overdraft fee is 29 dollars per item.",
  "type": "document",
  "document_id": "doc-…",
  "document": "larch-fee-schedule-2026.pdf",
  "title": "Schedule of Fees and Charges",
  "library_item_id": "sc-…:inode-…",
  "version": "2026.1",
  "effective": "2026-01-01",
  "in_force": "current",
  "sha256": "14f113ae714cbfcc791b15760ab633c00d9d6bdf5721474eec8ba61f312bfc19",
  "page": 1,
  "box": [0.112, 0.071, 0.872, 0.336],
  "chunk_id": "chunk-…",
  "kind": "paragraph",
  "excerpt": "Overdraft fee: 29 dollars per item paid into overdraft.",
  "verified": {"sha256_ok": true, "found": true, "ok": true, "…": "…"},
  "rendered": "[1] Larch Valley Credit Union, Schedule of Fees and Charges, version 2026.1 (effective 2026-01-01), p. 1: \"Overdraft fee: 29 dollars per item paid into overdraft.\""
}
```

## Wiring it into your own agent

- **Put provenance on the file, not in a side table.** `lib upload --xattrs` (or `lib xattr set` later) keeps version,
  dates and the sha256 next to the bytes. A citation reads them back with `lib get`.
- **Ask for documents and facts separately.** `--top-k` is shared across types, so a document search with `--types document`
  guarantees passages. Every passage has `range` = `P<page>[<page>:x0,y0,x1,y1]`, a box with its origin at the top left of the page.
- **Cite the sentence, keep the passage.** A passage is often a heading plus a paragraph (its box covers both). The excerpt
  to quote is the sentence or table row that states the claim; the export keeps the chunk id too.
- **Decide what is in force at answer time.** Old versions stay searchable, which is what you want for an audit, so filter
  by your provenance (`effective` / `superseded`) before citing.
- **Facts carry their origin.** `fact trace` returns `source_kind` COOK (extracted, with `source_entry_ids` → the messages)
  or MANUAL (someone called the API). Pin what must be quoted exactly; extracted facts are reworded.

## If something goes wrong

- **A passage's text has `$ … $` around a number or a unit**: the passages come from OCR of the rendered page, which
  writes some symbols as LaTeX (`25 °C` → `$ 25\ °C $`). The verbatim check then fails on that sentence. This demo's PDFs
  are plain ASCII and spell amounts as "29 dollars" for that reason.
- **`verify` cannot read the text of your own PDF**: `pdf_pages_text()` reads uncompressed text-layer PDFs like the ones
  `data/make_sources.py` writes. For other PDFs, use a PDF library for that one function.
- **A member claim falls back to `fact list`**: search did not rank the stating fact in its top 5. The demo says so and
  still cites it.
- **`tls handshake eof` / `could not connect`**: a dropped connection. Read-only commands retry four times; otherwise
  re-run `python3 demo.py` and it resumes.
- **`401 service account not found`**: the key was deleted or belongs to the other deployment.

## Files

```
demo.py                  the runner; prints every CLI command it runs (and emits events for the web app)
data/sources/*.pdf       the four published documents (generated by data/make_sources.py, standard library only)
data/provenance.json     version, dates and publisher of each document (written to the Library item's x_attrs)
data/story.json          Dana, her chat, the staff note, the four questions and the agent's draft claims
web/server.py            local web companion (stdlib HTTP server + Server-Sent Events over demo.py)
web/static/              the page — no build step
out/                     the citations export, the downloaded originals and runs.jsonl (git-ignored)
```

All names, organisations and documents are fictional.
