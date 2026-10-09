# Codebase memory for engineering teams

A runnable version of the MemoryLake use case
[*Give Engineering Teams a Codebase Memory Every AI Tool Can Read*](https://www.memorylake.ai/en/usecase/codebase-memory-for-engineering-teams),
built entirely on the [`memorylake` CLI](https://github.com/memorylake-ai/memorylake-cli).

**The problem.** Architectural decisions die in old PR descriptions. Gotchas live in Slack threads. New
hires re-discover them every quarter, and AI coding tools keep suggesting exactly the patterns the team
learned to avoid, because nothing teaches them otherwise.

**What this demo shows.** Tidewell's payments team owns two repositories, `ledger-service` and
`checkout-web`. Jun Park joined two weeks ago. The demo gives **each repository its own memory** (one
MemoryLake project per repo) and fills it the way a real team would:

- each repo's **`docs/` folder** — ADRs, a runbook, last year's architecture-review **PowerPoint deck** —
  is mirrored into the Library as folders and imported with **one `--recursive` command per repo**;
- **PR review threads and an incident review** become conversations, and MemoryLake extracts dated facts
  from them; the gotcha each thread taught is pinned verbatim.

Then the memory earns its keep:

- **Pre-commit check.** Jun's AI assistant wants to add moment.js, or a `NOT NULL` column to
  `ledger_entries`. One search per repo: the repo that banned the pattern blocks it and says why; **the
  other repo has never heard of the rule** — one memory per repo means rules do not leak across.
- **Day-one questions across both repos.** One search over both projects (`--projects a,b`), every hit
  labelled with the repo it came from — including *why we don't use Kafka*, a decision that lives only in
  a slide deck from November 2025 (`ppt_file`).

```
ledger-service/docs/** ── lib mkdir + lib upload ──▶ Library folder ── import --recursive ──▶ project: ledger-service ─┐
checkout-web/docs/**   ── lib mkdir + lib upload ──▶ Library folder ── import --recursive ──▶ project: checkout-web   ─┤
PR reviews, incident review ── conversations ──▶ dated facts; gotchas ── fact add ──▶ pinned facts ──────────────────┤
                                                                                                                      ▼
                         pre-commit check: search --projects <one repo>      day one: search --projects <both repos>
```

Runs in about 6 minutes on a free personal account (most of it is MemoryLake parsing files and
extracting facts). The only credential you need is a MemoryLake API key.

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
cd memorylake-usecases/codebase-memory

export MEMORYLAKE_API_KEY=sk-…
python3 demo.py
```

With `MEMORYLAKE_API_KEY` set, the demo logs into its own CLI profile under `./.memorylake-demo/`
(git-ignored) and leaves `~/.memorylake` alone. Without it, it uses your existing `memorylake auth login`.

| | |
|---|---|
| `MEMORYLAKE_BASE_URL` | API endpoint; default global, China is `https://app.memorylake.cn/openapi/memorylake` |
| `MEMORYLAKE_WORKSPACE` | Workspace id; default is the one remembered by `ws use`, else your default workspace |
| `python3 demo.py --reset` | Delete both repo projects, the threads and the mirrored docs folder first, then start over |
| `python3 demo.py check` | Run the pre-commit checks again |
| `python3 demo.py brief` | Ask the day-one questions again and rewrite `out/day-one-brief.md` |
| `python3 demo.py facts` | Print what each repo's memory holds |
| `python3 demo.py cleanup` | Delete the threads, both projects (with their documents and facts), the actors and the Library folder |

Safe to re-run: everything is found again by `custom_id` (prefix `mlu-cdb-`) or by folder name;
re-uploads keep the same Library item ids, re-imports are reported as already in the project, threads
resume where they stopped, and gotchas are pinned only in the run that stores their thread.

## Or run it in the browser

```bash
python3 web/server.py        # http://127.0.0.1:8765 — standard library only
```

![Web companion — the pre-commit check](https://github.com/memorylake-ai/memorylake-usecases/raw/main/codebase-memory/web/screenshot-check.png)

Paste the key, press **Run demo**, and watch the docs tree upload, the two recursive imports expand,
the review threads replay, each repo's facts arrive side by side, the pre-commit verdicts come in per
repo, and the day-one answers fill in with repo labels. **Ask the memory** runs the same search for any
question, over the repos you tick. The terminal drawer shows every `memorylake` command as it runs.

## What happens, step by step

**1. Connect** — validate the key, pick a workspace.

**2. Set up.** Priya (owns ledger-service), Tomás (owns checkout-web) and Jun (new hire) are `HUMAN`
actors bound to the workspace — they are the speakers in the threads. Each repository is a project:

```bash
memorylake actor create --custom-id mlu-cdb-priya-raman --display-name "Priya Raman" --type HUMAN …
memorylake proj create --name "ledger-service — codebase memory" --custom-id mlu-cdb-ledger-service …
memorylake proj create --name "checkout-web — codebase memory" --custom-id mlu-cdb-checkout-web …
```

**3. Mirror each repo's `docs/` into the Library.** The demo recreates the folder tree and uploads every
file into its folder. In a real setup this is a CI job on every merge to `main`:

```bash
memorylake lib mkdir mlu-cdb-docs --on-conflict deny
memorylake lib mkdir ledger-service --parent <mlu-cdb-docs id> --on-conflict deny
memorylake lib mkdir docs --parent <ledger-service id> --on-conflict deny
memorylake lib mkdir adr --parent <docs id> --on-conflict deny
memorylake lib upload data/repos/ledger-service/docs/adr/0004-transactional-outbox.md --parent <adr id> --on-conflict overwrite
…
memorylake lib upload data/repos/ledger-service/docs/reviews/2025-11-ledger-architecture-review.pptx --parent <reviews id> --on-conflict overwrite
```

```
  mlu-cdb-docs/
   ledger-service/
     docs/adr/0004-transactional-outbox.md
     docs/adr/0007-money-as-integer-minor-units.md
     docs/reviews/2025-11-ledger-architecture-review.pptx
     docs/runbooks/ledger-on-call.md
   checkout-web/
     docs/adr/0003-date-library.md
     docs/guides/frontend-conventions.md
```

**4. One recursive import per repo.** Hand `proj doc import` the repo's folder id and `--recursive`; the
CLI expands the whole subtree, and `--wait` blocks until every file is parsed (about 45 seconds):

```bash
memorylake proj doc import --project <ledger-service project> <ledger-service folder id> --recursive --wait
```

```
  Importing 4 file(s) resolved from 1 argument(s)
  · ledger-service: 4 new, 0 already in the project, 0 failed (46s)
  Importing 2 file(s) resolved from 1 argument(s)
  · checkout-web: 2 new, 0 already in the project, 0 failed (41s)
```

On a re-run the same command reports `0 new, 4 already in the project`.

**5. PR reviews and an incident review become memory.** Three threads — ledger-service PR #482
(DOUBLE PRECISION amounts, a `NOT NULL` column on a 400-million-row table), checkout-web PR #1290
(moment.js, calling the ledger from the browser), and the INC-2291 review (double charges on retry). Each
is a `GROUP` conversation in its repo's project, every message attributed to its speaker and
time-stamped to the day it happened. Threads are stored one at a time: the demo waits for `cook-status`
before the next. The gotcha each thread taught is pinned with `fact add`.

```bash
memorylake conv create --custom-id mlu-cdb-checkout-pr-1290 --project <checkout-web project> \
  --actors actor-jun,actor-tomas --kind GROUP --name "checkout-web PR #1290 review — …" --metadata "source=GitHub PR review"
memorylake conv msg append conv-… --actor actor-tomas --custom-id turn-02 --timestamp 2026-09-23T09:04:00Z \
  --text "Tomás Ortega (Frontend lead, owns checkout-web): Please take moment.js out. We removed it from checkout-web in March 2026 …"
memorylake conv cook-status conv-…
memorylake fact add --project <checkout-web project> "Do not add moment.js to checkout-web: it was removed in March 2026 (67 KB gzipped). …"
```

**6. What does each repo remember?** A real run (extraction varies a little from run to run):

```bash
memorylake fact list --projects <ledger-service project>
memorylake fact list --projects <checkout-web project>
```

```
  ledger-service: 14 facts — 12 extracted by MemoryLake from the threads, 2 pinned verbatim
   - Making refund_reason NOT NULL with no default on ledger_entries takes a production write lock for around 20 minutes
     because the table has about 400 million rows. (as of 2026-09-15)
   - ledger-service schema changes follow an expand-contract approach: add the column nullable, backfill in batches,
     then add the constraint in a later release. (as of 2026-09-15)
   - Ledger-service rejects POST requests without an Idempotency-Key header with HTTP 428. (as of 2026-09-30)
   - Idempotency keys for ledger-service stay valid for 72 hours. (as of 2026-09-30)
   📌 Every POST to ledger-service must carry an Idempotency-Key header; a retry must reuse the key of the first attempt. …
   …

  checkout-web: 6 facts — 4 extracted by MemoryLake from the threads, 2 pinned verbatim
   - checkout-web removed moment.js in March 2026 because it added 67 KB gzipped to the bundle. (as of 2026-09-23)
   - In checkout-web, the browser never calls ledger-service directly. (as of 2026-09-23)
   📌 Do not add moment.js to checkout-web: it was removed in March 2026 (67 KB gzipped). Use date-fns format() and …
   …
```

**7. Pre-commit check.** Each change the AI assistant proposes is searched against each repo's facts,
one repo at a time. A fact blocks the change when it is about the pattern *and* forbids or warns against
it ("do not", "never", "removed", "locks writes", "expand-contract"…) — the fact "moment.js was added to
package.json", which extraction sometimes writes from the PR thread, is about moment.js but forbids nothing:

```bash
memorylake search "add the moment.js library to format dates" --projects <ledger-service project> --types fact --top-k 3
memorylake search "add the moment.js library to format dates" --projects <checkout-web project> --types fact --top-k 3
```

```
  Proposed change: “Add moment.js to format the payment date on the receipt”
     ledger-service  ✓ nothing in this repo's memory speaks against it
     checkout-web    ✋ blocked — this repo's memory says:
                     Do not add moment.js to checkout-web: it was removed in March 2026 (67 KB gzipped). Use date-fns
                     format() and Intl.DateTimeFormat instead (ADR-0003).
                     checkout-web removed moment.js in March 2026 because it added 67 KB gzipped to the bundle. (as of 2026-09-23)

  Proposed change: “Add a NOT NULL column to ledger_entries in one migration”
     ledger-service  ✋ blocked — this repo's memory says:
                     Never add a NOT NULL column without a default to ledger_entries: the table has about 400 million
                     rows and the migration locks writes for around 20 minutes. Use expand-contract migrations …
     checkout-web    ✓ nothing in this repo's memory speaks against it
```

**8. Day one — Jun's questions, across both repos.** One search over both projects. Search results do
not say which project a hit came from, so the demo maps every fact and document id back to its repo
(from `fact list` and `proj doc list`). Questions whose answer lives in a file search documents only:

```bash
memorylake search "why was Kafka rejected as the ledger source of truth" --projects <ledger>,<checkout> --top-k 4 --types document
memorylake search "retry a payment request idempotency key" --projects <ledger>,<checkout> --top-k 4
```

```
  Q: Why doesn't the ledger use Kafka?
     [ledger-service] doc   2025-11-ledger-architecture-review.pptx  (PowerPoint)
                     The document discusses the architecture of Tidewell payments platform's ledger service, focusing on
                     the event store using PostgreSQL and rejecting…
     [ledger-service] doc   0004-transactional-outbox.md  (Text)

  Q: What must a client do when it retries a payment?
     [ledger-service] fact  Every POST to ledger-service must carry an Idempotency-Key header; a retry must reuse the key
                            of the first attempt. Keys are kept for 72 hours (decided in the INC-2291 review on 2026-09-30).
     [ledger-service] fact  The retry helper in checkout-api created a fresh Idempotency-Key per attempt. (as of 2026-09-30)
     [checkout-web] doc   frontend-conventions.md  (Text)
```

All four answers are written to `out/day-one-brief.md`.

## Wiring it into your own repos

- **One project per repository.** Scope every search with `--projects <repo>` for "what are this repo's
  rules?", and list several projects for questions that cross service boundaries.
- **Sync `docs/` from CI.** On merge to `main`: `lib upload --parent <folder> --on-conflict overwrite`
  for changed files (the item id stays the same), and once per new folder
  `proj doc import --project <repo> <folder id> --recursive`. Files already in the project are skipped.
- **Record review threads and post-incident reviews** as conversations with `--timestamp` set to when they
  happened, one speaker per actor. Pin the rule you want verbatim with `fact add --project <repo>`.
- **Pre-commit / pre-suggestion hook.** Before your AI tool proposes a dependency or a migration, run
  `memorylake search "<the change>" --projects <repo> --types fact` and put the hits in its context.

## If something goes wrong

- **`tls handshake eof` / `could not connect`** while polling: a dropped connection. Read-only commands
  retry four times; otherwise re-run `python3 demo.py` and it resumes.
- **`401 service account not found`**: the key was deleted or belongs to the other deployment.
- **A check says “nothing speaks against it” where you expected a block**: the verdict only counts
  facts that mention the pattern (moment.js, `NOT NULL`) and forbid or warn against it. Extraction varies from run to run, but the
  pinned gotchas are always there; `python3 demo.py facts` shows what each repo holds.
- **Markdown hits have no summary**: the server writes summaries for some files (the PowerPoint deck
  always got one in our runs) and not others. Search returns one summary per document, never quoted
  passages.
- **Changed a file and re-uploaded it?** `lib upload --on-conflict overwrite` keeps the item id and
  `proj doc download` returns the new bytes, but re-importing reports it as a duplicate. To be sure the
  project re-parses a changed file, `proj doc delete` it and import it again.

## Files

```
demo.py                          the runner; prints every CLI command it runs (and emits events for the web app)
data/repos/<repo>/docs/**        each repo's docs tree: ADRs, a runbook, conventions, the architecture-review deck
data/make_sources.py             regenerates the .pptx deck (optional; needs python-pptx)
data/threads/*.json              two PR reviews and an incident review, plus the gotcha pinned after each
web/server.py                    local web companion (stdlib HTTP server + Server-Sent Events over demo.py)
web/static/                      the page — no build step
out/                             the day-one brief (git-ignored)
```

All names, companies and figures are fictional.
