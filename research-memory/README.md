# Research memory for analysts

A runnable version of the MemoryLake use case
[*Give Analysts a Research Memory That Compounds Quarter Over Quarter*](https://www.memorylake.ai/en/usecase/research-memory-for-analysts),
built entirely on the [`memorylake` CLI](https://github.com/memorylake-ai/memorylake-cli).

**The problem.** Last quarter's literature review lives in a doc nobody opens again. The AI tool has no
memory of the papers you already vetted, or of why you changed your mind about one of them. Every new
question starts at zero.

**What this demo shows.** Mira Holt, an energy-storage analyst, spends Q3 on one question for her
portfolio manager: sodium-ion or LFP for grid storage? She reads a lab study and a field report that
**disagree** on cycle life (4,200 vs 2,900 cycles), keeps an **Excel** cost model with two sheets, writes
reading notes, and presents in two model reviews — July sets a base case of 4,200 cycles, September cuts it
to 2,900. All of it goes into one MemoryLake project. In Q4:

- each new question comes back with **the right PDF and the right spreadsheet sheet** (`excel_file`,
  sheet `Assumptions`) next to the conclusions drawn from them;
- the revised assumption is **one fact that keeps both values and both dates** — "2,900 cycles (set
  2026-09-15, previously 4,200 … as of 2026-07-14)" — so "why did this change?" has an answer;
- the original source file comes back from memory with `proj doc download`, byte for byte.

```
Q3 ─ PDFs, Excel, notes ── lib upload + proj doc import ──▶ documents ─┐
   ─ July / September reviews ── conversations ──▶ dated facts ────────┼─▶ one project ──▶ Q4: search per question
   ─ analyst rules & conclusions ── fact add ──▶ pinned facts ─────────┘                 ──▶ proj doc download
```

Runs in about 3 minutes on a free personal account. The only credential you need is a MemoryLake API key.

**Watch it run** (real recordings, unedited):
[CLI demo, 3:51](https://github.com/memorylake-ai/memorylake-usecases/releases/download/research-memory-v1/research-memory-cli-demo.mp4) · [Web companion demo, 4:03](https://github.com/memorylake-ai/memorylake-usecases/releases/download/research-memory-v1/research-memory-web-demo.mp4)

## Prerequisites

1. **A MemoryLake account and API key.** Sign up at [app.memorylake.ai](https://app.memorylake.ai),
   open **API Keys**, create a key and copy it. Details: [Authentication and API Keys](https://docs.memorylake.ai/authentication).
2. **The `memorylake` CLI:**

   ```bash
   curl -fsSL https://raw.githubusercontent.com/memorylake-ai/memorylake-cli/main/scripts/install.sh | sh
   ```

   ```powershell
   irm https://raw.githubusercontent.com/memorylake-ai/memorylake-cli/main/scripts/install.ps1 | iex
   ```
3. **Python 3.9+** (standard library only).

## Run it

```bash
git clone https://github.com/memorylake-ai/memorylake-usecases.git
cd memorylake-usecases/research-memory

export MEMORYLAKE_API_KEY=sk-…
python3 demo.py
```

With `MEMORYLAKE_API_KEY` set, the demo logs into its own CLI profile under `./.memorylake-demo/`
(git-ignored) and leaves `~/.memorylake` alone. Without it, it uses your existing `memorylake auth login`.

| | |
|---|---|
| `MEMORYLAKE_BASE_URL` | API endpoint; default global, China is `https://app.memorylake.cn/openapi/memorylake` |
| `MEMORYLAKE_WORKSPACE` | Workspace id; default is the one remembered by `ws use`, else your default workspace |
| `python3 demo.py --reset` | Delete the demo project and reviews first, then import everything again |
| `python3 demo.py --with-agent` | Also ask a MemoryLake agent the Q4 question (needs model quota; free accounts get HTTP 402 and the step is skipped) |
| `python3 demo.py brief` | Ask the four Q4 questions again and rewrite `out/q4-research-brief.md` |
| `python3 demo.py facts` | Print what the research memory holds |
| `python3 demo.py sources` | List the source documents and their processing status |
| `python3 demo.py cleanup` | Delete the reviews, the project (and its documents and facts), the actors and the uploaded files |

Safe to re-run: everything is found again by `custom_id`, prefixed `mlu-rma-`; re-imports are reported
as duplicates; notes are pinned only in the run that stores their review.

## Or run it in the browser

```bash
python3 web/server.py        # http://127.0.0.1:8765 — standard library only
```

![Web companion — the memory view with the revised assumption](https://github.com/memorylake-ai/memorylake-usecases/raw/main/research-memory/web/screenshot-memory.png)

[Watch the web companion demo (mp4, 4:03)](https://github.com/memorylake-ai/memorylake-usecases/releases/download/research-memory-v1/research-memory-web-demo.mp4).

Paste the key, press **Run demo**, and watch the files get parsed, the two reviews replay, the facts
arrive with the revision highlighted, the Q4 answers fill in with their PDF and Excel sources, and the
original file come back. **Ask the memory** runs the same search for any question you type; every source
file opens from the page. The terminal drawer shows every `memorylake` command as it runs.

## What happens, step by step

**1. Connect** — validate the key, pick a workspace.

**2. Set up.** Mira and Theo are `HUMAN` actors bound to the workspace; one project holds the research
thesis.

```bash
memorylake actor create --custom-id mlu-rma-mira-holt --display-name "Mira Holt" …
memorylake actor bind --actor actor-… --workspace ws-…
memorylake proj create --name "Grid storage: sodium-ion vs LFP — research memory" --custom-id mlu-rma-grid-storage
```

**3. Q3 reading.** Five files go into the Library and are imported into the project. `--wait` blocks until
the server has parsed them (about a minute):

```bash
memorylake lib upload data/sources/halvorsen-2026-lab-study.pdf --on-conflict overwrite
memorylake lib upload data/sources/storage-cost-model.xlsx --on-conflict overwrite
…
memorylake proj doc import --project proj-… <item ids…> --wait
memorylake proj doc list --project proj-…
```

```
  · imported: 5 new, 0 already in project, 0 failed (64s)

  5 source documents in the research memory:

   - gridfield-2026-field-report.pdf      okay
   - halvorsen-2026-lab-study.pdf         okay
   - reading-notes-q3.md                  okay
   - storage-cost-model.xlsx              okay
   - storage-policy-brief-q3.pdf          okay
```

**4. Q3 model reviews.** Each review is a `GROUP` conversation between Mira and Theo, every line
attributed to its speaker and time-stamped to the day of the review. Mira's own rules and conclusions
are pinned with `fact add`. Facts are extracted in the background; the runner waits for `cook-status`.

```bash
memorylake conv create --custom-id mlu-rma-review-02-september --project proj-… --actors actor-theo,actor-mira \
  --kind GROUP --name "Storage model review — September" --metadata review_date=2026-09-15
memorylake conv msg append conv-… --actor actor-mira --custom-id turn-04 --timestamp 2026-09-15T09:08:00Z \
  --text "Mira Holt (Energy storage analyst, Larkspur Research): … I am cutting our base case cycle life for sodium-ion from 4,200 to 2,900 cycles."
memorylake fact add --project proj-… "Mira's rule: discount results from vendor-funded studies by 20% until independent data confirms them." …
memorylake conv cook-status conv-…
```

**5. What did MemoryLake remember?** A real run (extraction varies a little from run to run):

```bash
memorylake fact list --projects proj-…
```

```
  14 facts in the research memory: 11 written by MemoryLake from the reviews, 3 pinned verbatim by the analyst.
   - The 2026 pack cost assumption is 61 dollars per kWh for sodium-ion and 74 dollars per kWh for LFP. (as of 2026-07-14)
   - The domestic-content bonus favours LFP until a domestic sodium-ion plant exists, which is 2028 at the earliest (as of 2026-09-15).
   - The sodium-ion thesis shifts from "cheaper" to "cheaper only if cycle life improves." (as of 2026-09-15)
   - Mira's rule: discount results from vendor-funded studies by 20% until independent data confirms them.
   …

  ▶ September revised July's number without erasing it — one fact, both values, both dates:
     Base case sodium-ion cycle life is 2,900 cycles (Gridfield field data, set 2026-09-15, previously 4,200 cycles
     to 80% capacity as of 2026-07-14); the Halvorsen lab figure of 4,200 is kept only as an upper bound.
```

The September review did not create a second, competing fact: MemoryLake updated the existing one and
kept the old value with its date. (If a run happens to extract the two values as separate facts instead,
the demo shows both, each with its date.)

**6. Q4 — new questions.** One search per question over the whole project. Facts and documents come back
as two sets; documents carry their type and, for workbooks, the sheet:

```bash
memorylake search "sodium-ion cycle life assumption base case and why it changed" --projects proj-… --top-k 5
```

```
  Q: What cycle life do we assume for sodium-ion, and why?
     fact  Base case sodium-ion cycle life is 2,900 cycles to 80% capacity (Gridfield field data, set 2026-09-15,
           previously 4,200 cycles to 80 percent capacity as of 2026-07-14); …
     doc   gridfield-2026-field-report.pdf  (PDF)
           This report details the performance of sodium-ion grid storage systems in the field, showing lower cycle life…
     doc   storage-cost-model.xlsx  (Excel, sheet "Assumptions")
           This document describes a cost model for storage, detailing assumptions and pack costs for LFP and Sodium-ion…
     doc   halvorsen-2026-lab-study.pdf  (PDF)
           This document describes a study on sodium-ion cells for stationary storage, funded by Corvane Cells…
```

The cost question lands on the other sheet (`Excel, sheet "Pack costs"`), the policy question on the
policy brief. All four answers are written to `out/q4-research-brief.md`.

**7. Back to the source.** A document-only search finds the file; `proj doc download` brings back the
original:

```bash
memorylake search "field report grid sites cycle life" --projects proj-… --types document --top-k 1
memorylake proj doc download doc-… --project proj-… --output out/gridfield-2026-field-report.pdf --force
```

```
  · saved out/gridfield-2026-field-report.pdf (3,230 bytes) — byte-for-byte the file that was uploaded in Q3
```

**8. Optional: an agent.** With `--with-agent`, a MemoryLake agent answers "should we raise our sodium-ion
allocation?" from the same project. It needs model quota; on a free account the step prints the 402 and
moves on.

## Wiring it into your own research workflow

- One project per thesis, coverage universe or client. Import every source you read
  (`lib upload` + `proj doc import`): PDFs, workbooks, Markdown notes all work.
- Record review meetings or analyst chats as conversations with `--timestamp` set to when they happened.
  That date is what lets a later revision say *previously … as of …* instead of overwriting.
- Pin the judgments you want verbatim (`fact add --project …`): your rules for weighing sources, your
  current position. They are searchable immediately.
- Before drafting anything new, run `search "<question>" --projects <thesis>` and hand both result sets
  to your model or your reader. Use `--types document` when you need the file, then `proj doc download`.

## If something goes wrong

- **`tls handshake eof` / `could not connect`** while polling: a dropped connection. Read-only commands
  retry four times; otherwise re-run `python3 demo.py` and it resumes.
- **`401 service account not found`**: the key was deleted or belongs to the other deployment.
- **Import is slow**: `proj doc import --wait` waits up to 10 minutes; the first import of five files takes
  about a minute. `python3 demo.py sources` shows each file's status.
- **Document hits have a summary but no quoted passage**: search returns one summary per document (and
  the sheet name for workbooks), not text excerpts. Download the file for the passage itself.
- **Facts come back worded differently, or in another language**: extraction is server-side and varies
  between runs; the pinned notes are always verbatim.
- **HTTP 402** only affects `--with-agent`.

## Files

```
demo.py                          the runner; prints every CLI command it runs (and emits events for the web app)
data/sources/*.pdf|.xlsx|.md     the Q3 corpus: lab study, field report, policy brief, cost model, reading notes
data/make_sources.py             regenerates the PDFs and the workbook from text (optional; needs reportlab, openpyxl)
data/reviews/*.json              the July and September model reviews, plus the notes pinned after each
web/server.py                    local web companion (stdlib HTTP server + Server-Sent Events over demo.py)
web/static/                      the page — no build step
out/                             the Q4 brief and the downloaded source file (git-ignored)
```

All names, companies and figures are fictional.
