# Contract review that remembers your positions

A runnable version of the MemoryLake use case
[*AI-Assisted Contract Review That Remembers Your Positions*](https://www.memorylake.ai/en/usecase/ai-memory-for-contract-review-teams),
built entirely on the [`memorylake` CLI](https://github.com/memorylake-ai/memorylake-cli).

**The problem.** Every contract review depends on institutional knowledge: your standard positions, the
exceptions you accepted from each counterparty, and what was said in last year's review. When that lives
in email threads and one senior reviewer's head, every new contract — and every new reviewer — starts
from scratch.

**What this demo shows.** Harbrook Logistics' legal team (fictional) reviews supplier contracts against
a playbook. In 2025 Elena Voss accepted an 18-month liability cap from Northwind Freight (their cargo
insurance stood behind it) and a 24-month data-breach super-cap from Halden Analytics. Now Northwind's
**renewal redline** lands on the desk of Sam Okafor, who joined in September. The demo builds three kinds
of memory:

- **the playbook** — a project Legal curates: the standard positions pinned as facts, plus the playbook PDF;
- **one actor per counterparty** — the exceptions accepted from it, pinned as that actor's facts;
- **the review sessions** — one conversation per review, with the redline under review **attached to the
  first message as a `FILE` block**, tagged with `counterparty=` and `contract=` metadata.

Then Sam's review starts with memory:

- **Our positions + this counterparty's precedents, nobody else's.** One search per clause, scoped
  `--projects <playbook> --actors <Northwind Freight>`. The scopes combine as a union: the 12-month
  standard and Northwind's 18-month exception come back together — and Halden's 24-month super-cap does
  not. Scope the same question to Halden and it is the other way round.
- **Reopen last year's review.** The session is found by its metadata and **replayed message by message**
  (`conv msg list`), 2025 timestamps and the attachment included. The attachment's URI leads back to the
  Library item; `proj doc download` returns the original redline, and a clause-by-clause diff against the
  new one shows what Northwind is asking for now: **liability 18 → 36 months, payment net 30 → net 15,
  and an automatic renewal**.

```
playbook PDF ── import ──▶ project: playbook ◀── fact add --project  (standard positions)
                                  │
counterparty actors ◀── fact add --actor  (accepted exceptions: Northwind, Halden)
                                  │
review sessions ── conversations (+ FILE block → redline in the Library) ──▶ project: reviews
                                  │
renewal: search --projects <playbook> --actors <Northwind>    replay: conv list → conv msg list → lib get → proj doc download → diff
```

Runs in about 5 minutes on a free personal account (most of it is MemoryLake parsing files and extracting
facts). The only credential you need is a MemoryLake API key.

**Watch it run** (real recordings, unedited): _recording in progress_

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
cd memorylake-usecases/contract-review-memory

export MEMORYLAKE_API_KEY=sk-…
python3 demo.py
```

With `MEMORYLAKE_API_KEY` set, the demo logs into its own CLI profile under `./.memorylake-demo/`
(git-ignored) and leaves `~/.memorylake` alone. Without it, it uses your existing `memorylake auth login`.

| | |
|---|---|
| `MEMORYLAKE_BASE_URL` | API endpoint; default global, China is `https://app.memorylake.cn/openapi/memorylake` |
| `MEMORYLAKE_WORKSPACE` | Workspace id; default is the one remembered by `ws use`, else your default workspace |
| `python3 demo.py --reset` | Delete the sessions, both projects, the counterparty actors and the Library folder first, then start over |
| `python3 demo.py context` | Run the scoped searches again (Northwind, then the Halden contrast) |
| `python3 demo.py replay` | Find and replay last year's Northwind session, fetch its redline, diff it against the new one |
| `python3 demo.py brief` | Both of the above, then rewrite `out/renewal-brief.md` |
| `python3 demo.py facts` | Print what each scope holds |
| `python3 demo.py cleanup` | Delete the sessions, both projects (with their documents and facts), the actors (with their facts) and the Library folder |

Safe to re-run: everything is found again by `custom_id` (prefix `mlu-crm-`) or by folder name;
re-uploads keep the same Library item ids (so the attachments keep pointing at them), re-imports are
reported as already in the project, sessions resume where they stopped, and a position or precedent is
pinned only if that exact text is not already in its scope.

## Or run it in the browser

```bash
python3 web/server.py        # http://127.0.0.1:8765 — standard library only
```

Paste the key, press **Run demo**, and watch the files upload and import, the positions get pinned, the
two 2025 sessions replay with their attachments, each scope's facts arrive, the renewal context come back
with every hit labelled by its owner, last year's session reappear with the redline diff, and the brief
fill in clause by clause. **Ask the memory** runs the same scoped search for any question, for the
counterparty you pick. The terminal drawer shows every `memorylake` command as it runs.

## What happens, step by step

**1. Connect** — validate the key, pick a workspace.

**2. Set up.** Two projects, the review participants and one actor per counterparty:

```bash
memorylake proj create --name "Harbrook contract playbook" --custom-id mlu-crm-playbook …
memorylake proj create --name "Harbrook contract reviews" --custom-id mlu-crm-reviews …
memorylake actor create --custom-id mlu-crm-elena-voss --display-name "Elena Voss" --type HUMAN …
memorylake actor create --custom-id mlu-crm-assistant --display-name "Harbrook review assistant" --type ASSISTANT …
memorylake actor create --custom-id mlu-crm-northwind-freight --display-name "Northwind Freight" --type HUMAN \
  --tags counterparty,carrier --description "Counterparty — freight carrier, MSA NWF-MSA-2025"
memorylake actor bind --actor <id> --workspace <ws>          # for each actor
```

An actor is whatever you want memory scoped to — here, a company on the other side of the table.

**3. The playbook PDF and last year's redlines.** Mirrored into the Library, then one recursive import
per project:

```
  mlu-crm-contracts/
   playbook/harbrook-contract-playbook-2026.pdf
   redlines/halden-analytics/halden-saas-redline-v1-2025-06.md
   redlines/northwind-freight/northwind-msa-redline-v2-2025-03.md
$ memorylake proj doc import --project <playbook> <playbook folder id> --recursive --wait
  · Harbrook contract playbook: 1 new, 0 already in the project, 0 failed (37s)
$ memorylake proj doc import --project <reviews> <redlines folder id> --recursive --wait
  Importing 2 file(s) resolved from 1 argument(s)
  · Harbrook contract reviews: 2 new, 0 already in the project, 0 failed (25s)
```

**4. Pin the standard positions** to the playbook project, verbatim:

```bash
memorylake fact add --project <playbook> \
  'Standard position: total liability is capped at 12 months of fees; any higher cap needs General Counsel sign-off.' \
  'Standard position: payment terms are net 45 days; net 30 only in exchange for an early-payment discount of at least 2%; never shorter than net 30.' \
  'Standard position: renewal must be an express written decision — never automatic renewal; notice of non-renewal is 60 days.' …
```

**5. Last year's review sessions.** Each is a `DIRECT` conversation between Elena and the assistant in the
reviews project, with metadata so the next reviewer can find it. The first message carries the redline
as a `FILE` block next to the text:

```bash
memorylake conv create --custom-id mlu-crm-review-nwf-msa-2025 --project <reviews> --actors <elena>,<assistant> \
  --kind DIRECT --name "Review — Northwind Freight MSA, redline v2" \
  --metadata kind=contract-review --metadata counterparty=northwind-freight --metadata contract=NWF-MSA-2025
memorylake conv msg append <conv> --actor <elena> --custom-id turn-01 --timestamp 2025-03-12T09:02:00Z --content-json '[
  {"block_type": "TEXT", "text": "Elena Voss (Senior counsel, Harbrook Logistics): Starting the review of Northwind Freight'"'"'s redline v2 …; attached."},
  {"block_type": "FILE", "uri": "drive://…/<library item id>", "name": "northwind-msa-redline-v2-2025-03.md", "mime_type": "text/markdown"}]'
memorylake conv msg append <conv> --actor <assistant> --custom-id turn-02 --text "…"      # and so on
memorylake conv cook-status <conv> --workspace <ws>                                      # wait before the next session
memorylake fact add --actor <northwind> \
  'Northwind Freight precedent (MSA signed 2025-03-20): liability cap accepted at 18 months of fees, above the 12-month standard, approved by the General Counsel because Northwind carries USD 10M cargo liability insurance.' \
  'Northwind Freight precedent (MSA signed 2025-03-20): payment terms accepted at net 30 in exchange for a 2% early-payment discount.'
```

MemoryLake also extracts dated facts from the sessions into the reviews project (13–18 per run in ours,
e.g. *“Marcus Hale, Harbrook's General Counsel, signed off on accepting the 18-month liability cap. (as of
2025-03-12)”*). It does **not** read the attached file — the redline is searchable because it was imported
in step 3, and the `FILE` block records *which* file the session was about.

**6. Northwind's renewal lands.** One search per clause, scoped to the playbook project **and** the
Northwind actor. Hits carry no owner, so the demo maps fact ids back to their scope (`fact list --projects`
/ `--actors`); a search always returns its top-k, so hits not about the clause are left out:

```
$ memorylake search 'limitation of liability cap in months of fees' --projects <playbook> --actors <northwind> --top-k 4 --types fact

  9.2 Limitation of liability — context for Northwind Freight
     [Harbrook standard] Standard position: total liability is capped at 12 months of fees; any higher cap needs General Counsel sign-off.
     [Northwind Freight precedent] Northwind Freight precedent (MSA signed 2025-03-20): liability cap accepted at 18 months of fees, above the 12-month standard, approved by the General Counsel because Northwind carries USD 10M cargo liability insurance.
     (2 other hit(s) not about this clause, left out)

  5.1 Payment terms — context for Northwind Freight
     [Harbrook standard] Standard position: payment terms are net 45 days; net 30 only in exchange for an early-payment discount of at least 2%; never shorter than net 30.
     [Northwind Freight precedent] Northwind Freight precedent (MSA signed 2025-03-20): payment terms accepted at net 30 in exchange for a 2% early-payment discount.
     (2 other hit(s) not about this clause, left out)

  2.1 Term and renewal — context for Northwind Freight
     [Harbrook standard] Standard position: renewal must be an express written decision — never automatic renewal; notice of non-renewal is 60 days.
     (3 other hit(s) not about this clause, left out)

  Same liability question, scoped to Halden Analytics instead:

  9.2 Limitation of liability — context for Halden Analytics
     [Harbrook standard] Standard position: total liability is capped at 12 months of fees; any higher cap needs General Counsel sign-off.
     [Halden Analytics precedent] Halden Analytics precedent (SaaS agreement signed 2025-06-18): liability cap 12 months of fees, with a 24-month super-cap for personal-data breach claims only.
     (2 other hit(s) not about this clause, left out)

  ✓ no Halden Analytics precedent in Northwind Freight's context, and no Northwind Freight precedent in Halden Analytics'
```

**7. Reopen last year's review.** `conv list` has no server-side filter, so the demo picks review
sessions by their metadata, replays the Northwind one, follows the attachment back to the original file
and diffs it against the new redline:

```
$ memorylake conv list --page-size 50 --workspace <ws>
  · 2 conversation(s) in the workspace; 1 review session(s) with metadata counterparty=northwind-freight

  ▸ Review — Northwind Freight MSA, redline v2   conv-…
    metadata: contract=NWF-MSA-2025, counterparty=northwind-freight, kind=contract-review
$ memorylake conv msg list <conv> --page-size 50
   2025-03-12 09:02  Elena Voss: Starting the review of Northwind Freight's redline v2 of the MSA (NWF-MSA-2025). Their counsel sent it on 2025-03-10; attached.
                     📎 text/markdown  drive://drive-…/sc-…:inode-…
   2025-03-12 09:04  Harbrook review assistant: Northwind moved two clauses off the playbook: 9.2 caps liability at 18 months of fees (our standard is 12 months), and 5.1 asks for payment within 30 days (our standard is net 45). 9.3 adds their cargo liability insurance of USD 10M.
   2025-03-12 09:06  Elena Voss: The 18-month cap is acceptable for Northwind only because their USD 10M cargo insurance stands behind it. Marcus Hale, our General Counsel, signed off this morning.
   …
$ memorylake lib get sc-…:inode-…
$ memorylake proj doc list --project <reviews>
$ memorylake proj doc download --project <reviews> <doc> --output out/northwind-msa-redline-v2-2025-03.md --force
  · attachment fetched back from MemoryLake → out/northwind-msa-redline-v2-2025-03.md (948 bytes)

  northwind-msa-redline-v2-2025-03.md  →  northwind-msa-redline-v3-2026-09.md: 3 of 6 clauses changed

   2.1
     - Term. Initial term of three years from 2025-04-01. Renewal requires the written agreement of both parties.
     + Term. Renewal term of three years from 2027-04-01. This Agreement renews automatically for further one-year terms unless either party gives 30 days' notice.

   5.1
     - Payment. Harbrook pays correct invoices within 30 days, less a 2% early-payment discount.
     + Payment. Harbrook pays correct invoices within 15 days.

   9.2
     - Limitation of liability. Each party's total liability is capped at 18 months of fees paid under this Agreement.
     + Limitation of liability. Each party's total liability is capped at 36 months of fees paid under this Agreement.
```

The downloaded file is byte-for-byte the file that was uploaded in step 3.

**8. Renewal brief.** For each changed clause: the 2025 text, the 2026 text, the retrieved position and
precedent, and an action — a clause with a Northwind precedent goes to the General Counsel with the
precedent attached (playbook §6); one without is checked against the standard position. Written to
`out/renewal-brief.md`:

```
   2.1  check against the standard position
   5.1  escalate — departs from the 2025 precedent
   9.2  escalate — departs from the 2025 precedent
```

## Wiring it into your own review flow

- **Positions in one curated project.** Pin each standard position as one atomic fact with
  `fact add --project <playbook>`; keep conversations out of that project so its facts stay exactly as
  written. Import the playbook document itself for the long form.
- **One actor per counterparty.** After signing, pin each accepted exception with
  `fact add --actor <counterparty>`. Before a review, search `--projects <playbook> --actors <counterparty>`
  and put the hits in your AI tool's context.
- **Record every review session** as a conversation with `--timestamp` set to when it happened and
  metadata you will look it up by (`counterparty`, `contract`). Attach the document under review as a
  `FILE` block pointing at its Library item — and import that item into a project if you want it
  searchable and downloadable.

## If something goes wrong

- **`500 INTERNAL_ERROR` when appending a message with a `FILE` block**: the block needs `mime_type`.
  `{"block_type": "FILE", "uri": "drive://…", "name": "…", "mime_type": "text/markdown"}` works; the same
  block without `mime_type`, or with a bare Library item id as `uri`, is rejected with a 500.
- **`tls handshake eof` / `could not connect`** while polling: a dropped connection. Read-only commands
  retry four times; otherwise re-run `python3 demo.py` and it resumes.
- **`401 service account not found`**: the key was deleted or belongs to the other deployment.
- **The extracted facts differ between runs**: extraction is server-side and varies (13 to 18 facts per
  run in ours). Nothing in steps 6–8 depends on them: the context comes from the pinned positions and
  precedents, the diff from the files.
- **`conv list` shows other conversations too**: it lists the whole workspace and cannot filter on the
  server; the demo picks its sessions by `metadata.kind=contract-review` and `metadata.counterparty`.

## Files

```
demo.py                                   the runner; prints every CLI command it runs (and emits events for the web app)
data/library/playbook/*.pdf               the contract playbook (standard positions)
data/library/redlines/<counterparty>/*.md last year's redlines, attached to the review sessions
data/incoming/*.md                        Northwind's renewal redline (v3) — stays local, diffed in step 7
data/positions.json                       the standard positions pinned in step 4
data/sessions/*.json                      the two 2025 review sessions, plus the precedents pinned after each
data/make_sources.py                      regenerates the playbook PDF (optional; needs reportlab)
web/server.py                             local web companion (stdlib HTTP server + Server-Sent Events over demo.py)
web/static/                               the page — no build step
out/                                      the fetched redline and the renewal brief (git-ignored)
```

All names, companies and figures are fictional.
