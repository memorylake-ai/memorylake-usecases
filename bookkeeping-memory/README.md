# Bookkeeping memory for small accounting firms

A runnable version of the MemoryLake use case
[*Give Small Accounting Firms Bookkeeping Memory That Captures Every Client's Quirks*](https://www.memorylake.ai/en/usecase/bookkeeping-memory-for-small-accounting-firms),
built entirely on the [`memorylake` CLI](https://github.com/memorylake-ai/memorylake-cli).

**The problem.** Every client has its own bookkeeping quirks. One books card-processing fees as cost of revenue, another as
bank fees; one insurance policy gets amortized, a new vendor needs a question to the owner. The senior bookkeeper knows.
The AI tool sees one transaction at a time, and a junior picking up the client guesses.

**What this demo shows.** Ledgerline Bookkeeping (a fictional three-person firm) keeps the books for two clients, each in
its own MemoryLake project: **Fernhill Supply**, an online store, and **Marrow & Pine Café**. Each project holds the owner's
onboarding thread and the client's past ledger exports (.xlsx).

- **Per-client rules, pinned from the owner's own words.** The threads are stored as conversations; the rules agreed in them
  are pinned verbatim (`fact add`) with metadata: vendor, kind of line, category, thread, message.
- **The same line, two categories.** September's bank feed is categorized line by line: a client **rule** first; otherwise
  the **last time that payee was booked** in that client's own ledgers; otherwise **ask the client**. The Paylane card-fee
  line becomes **Cost of revenue** for Fernhill and **Bank and merchant fees** for Marrow & Pine. Marrow & Pine's August
  ledger booked it the wrong way (a junior's mistake). The rule wins, and the row it overrides is shown.
- **Every precedent is cited to its ledger row.** An .xlsx search hit is a `table` item: a whole table as one passage, one
  `Column: value` line per row, with the cells it covers (`range` = `A2:E19`). Line *i* is row *start + i*, so a precedent is
  cited as `fernhill-ledger-2026-q3.xlsx · Ledger!A15:E15`.
- **How each workbook was read.** `proj doc inspect` shows the tables MemoryLake found on each sheet. Fernhill's single
  sheet holds two: the ledger (`A1:E19`, 18 rows) and a **"Recurring vendors"** table below it (`A22:E26`, title row
  recognized). It also gives every column's type.
- **A corrupted export, fixed in place.** Marrow & Pine's September export arrives as a broken download.
  `proj stats` counts `error 1`, `proj doc get` names it (`ERROR_FILE_CORRUPTED`), and `inspect` leaves it out. Its Orchard
  Print line has no precedent, so the demo says *ask the client*. The client re-sends the file. `lib upload --on-conflict overwrite`
  replaces the Library item in place, and `proj doc reload` processes **the same document** again. It goes error → running → okay,
  and the line now cites `marrow-ledger-2026-09.xlsx · Transactions!A4:E4`.
- **Every cited row is checked against the original workbook.** The check runs on the file, not on the index that produced the row:
  - `proj doc download` fetches it, and its sha256 must equal the committed export's
  - each `Column: value` must equal the cell under that header **in that row** of the sheet XML (standard library)
  - as a control, the same cells are checked against the next row down, and every one must fail
- **"Why is it booked that way?"** The pinned rule names the message it was agreed in. `conv consumed-messages` returns
  **the batch of messages MemoryLake read together** when it read that message, and the owner's reason is in it. The
  thread arrived in two sittings, so the batch is messages 1–4; 5–6 were read later. MemoryLake's own extracted fact
  about the rule, traced with `fact trace`, comes from the same four messages.

| Line | Fernhill Supply | Marrow & Pine Café |
|---|---|---|
| Paylane card fees | **Cost of revenue** (rule) | **Bank and merchant fees** (rule; overrides August's row `Ledger!A10:E10`) |
| Utilities / hosting | Northwind Hosting → Cost of revenue, `Ledger!A15:E15` | Saltmarsh Utilities → Utilities, `Ledger!A13:E13` |
| Exception | Kestrel cyber policy → Prepaid expense, amortize over 12 months (rule) | Forkful payout → Delivery sales (rule) |
| Only in the re-sent file | — | Orchard Print → *ask*, then Marketing, `Transactions!A4:E4` |
| New vendor | Lumen Ads → ask the client | Tidewell Linen → ask the client |

```
owner's thread ── conv msg append (two sittings) ──▶ extracted facts  +  rules pinned verbatim (fact add + metadata)
ledger .xlsx   ── lib upload → proj doc import ──▶ proj stats · proj doc get (error) · proj doc inspect (tables per sheet)

bank-feed line ─▶ rule on file? ─▶ else search --types document --projects <client> ─▶ table rows ─▶ latest same payee
                                                                                     ─▶ file · Sheet!A15:E15
broken export  ─▶ lib upload --on-conflict overwrite ─▶ proj doc reload (same doc id) ─▶ error → okay ─▶ the line gets a precedent
cited rows     ─▶ proj doc download ─▶ sha256 ─▶ that row's cells in the sheet XML (control: the next row must fail)
rule           ─▶ its message ─▶ conv consumed-messages (the batch MemoryLake read) ─▶ the owner's reason
```

**Watch it run** (real recordings, unedited):
[CLI demo, 6:17](https://github.com/memorylake-ai/memorylake-usecases/releases/download/bookkeeping-memory-v1/bookkeeping-memory-cli-demo.mp4) · [Web companion demo, 7:48](https://github.com/memorylake-ai/memorylake-usecases/releases/download/bookkeeping-memory-v1/bookkeeping-memory-web-demo.mp4)

Runs in about 5 minutes on a free personal account. Most of that is MemoryLake reading the two threads (about 30 s per
sitting) and importing the exports. The only credential you need is a MemoryLake API key.

**Measured** (free account, every fresh run while building and recording this demo, both recordings included): **8 of 8** fresh runs (three of them in parallel) gave the same result. Each time:

- 12/12 bank-feed lines were as expected (Orchard Print: *ask*, then Marketing after the reload)
- the broken export ended in `ERROR_FILE_CORRUPTED` and the reload took it to `okay` (24–35 s)
- 6/6 cited rows checked out, and the control rejected 6/6
- `consumed-messages` returned a batch of 4 for both rules, and MemoryLake's own extracted fact about the rule traced to the same 4 messages

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
cd memorylake-usecases/bookkeeping-memory

export MEMORYLAKE_API_KEY=sk-…
python3 demo.py
```

With `MEMORYLAKE_API_KEY` set, the demo logs into its own CLI profile under `./.memorylake-demo/`
(git-ignored) and leaves `~/.memorylake` alone. Without it, it uses your existing `memorylake auth login`.

| | |
|---|---|
| `MEMORYLAKE_BASE_URL` | API endpoint; default global, China is `https://app.memorylake.cn/openapi/memorylake` |
| `MEMORYLAKE_WORKSPACE` | Workspace id; default is the one remembered by `ws use`, else your default workspace |
| `python3 demo.py --reset` | Delete everything the demo created first, then start over (replays the broken export) |
| `python3 demo.py categorize [fernhill\|marrow]` | Categorize the bank feed against what MemoryLake holds now |
| `python3 demo.py files` | What each project holds: `proj stats`, failed documents, `proj doc inspect` tables |
| `python3 demo.py why [fernhill\|marrow]` | Where the Paylane rule came from: message, consumed batch, extracted fact |
| `python3 demo.py verify` | Download the workbooks again and re-check every row in `out/citations.json` |
| `python3 demo.py cleanup` | Delete the two projects, the two threads, the three actors and the uploaded files |

It is safe to re-run. Projects, actors and threads are found again by `custom_id` (prefix `mlu-bkm-`), and files by name.
A message is appended only if the thread does not have it yet, and a rule is pinned only if that exact text is not there yet.
The re-sent export cannot be broken again. On a re-run the demo says it was reloaded in an earlier run, and `--reset`
replays it. A re-run takes about a minute.

## Or run it in the browser

```bash
python3 web/server.py        # http://127.0.0.1:8765 — standard library only
```

![Web companion — Ledger exports: proj stats per client, and the two tables proj doc inspect found on Fernhill's single sheet, drawn over the workbook](https://github.com/memorylake-ai/memorylake-usecases/raw/main/bookkeeping-memory/web/screenshot-files.png)

![Web companion — Categorize: the September bank feed, each line with its client rule or the ledger row it copies](https://github.com/memorylake-ai/memorylake-usecases/raw/main/bookkeeping-memory/web/screenshot-categorize.png)

![Web companion — Why?: the rule's message and the batch MemoryLake read it in (conv consumed-messages)](https://github.com/memorylake-ai/memorylake-usecases/raw/main/bookkeeping-memory/web/screenshot-why.png)

[Watch the web companion demo (mp4, 7:48)](https://github.com/memorylake-ai/memorylake-usecases/releases/download/bookkeeping-memory-v1/bookkeeping-memory-web-demo.mp4).

Paste the key, press **Run demo**, and watch each step:

- **Ledger exports** shows each project's status counts, the failed document and its error code, and the tables
  `inspect` found, drawn over the workbook's own grid
- **Categorize** shows both bank feeds side by side, each line with its rule or its cited row
- **Re-sent export** shows the reload timeline and the line that got its answer
- **Verify** shows every cited row against the original
- **Why?** shows the thread with the consumed batch marked
- **Categorize a line** runs the same lookup for any payee

The terminal drawer shows every `memorylake` command as it runs.

## What happens, step by step

Output below is from a real run (ids shortened).

### 1. Connect

`auth login --api-key` into the isolated profile, `team get`, and the workspace (`ws current`, else the first of `ws list`).

### 2. Clients

Three actors (the bookkeeper and the two owners, `actor create` + `actor bind`) and one project per client
(`proj create --custom-id mlu-bkm-fernhill` / `mlu-bkm-marrow`).

### 3. Onboarding threads → client rules

```
$ memorylake conv create --custom-id mlu-bkm-fernhill-onboarding --project proj-… --actors actor-…,actor-… --kind GROUP --name 'Fernhill Supply — onboarding thread' --metadata client=fernhill
$ memorylake conv msg append conv-… --actor actor-… --custom-id turn-01 --timestamp 2026-09-01T09:00:00Z --text 'Rafe Okafor (Owner, Fernhill Supply): Our investors read gross margin with card processing included, …'
  · messages 1–4 stored; waiting for MemoryLake to read them before the rest of the thread arrives
$ memorylake conv cook-status conv-…
$ memorylake conv msg append conv-… --custom-id turn-05 --timestamp 2026-09-01T09:28:00Z --text 'Rafe Okafor (Owner, Fernhill Supply): One more: the Kestrel cyber policy …'
…
$ memorylake fact add --project proj-… 'Fernhill Supply rule: Paylane card processing fees are booked to Cost of revenue (investors read gross margin with processing included). Agreed with Rafe Okafor on 2026-09-01.'
$ memorylake fact update fact-… --project proj-… --metadata '{"kind": "rule", "client": "fernhill", "vendor": "Paylane", "line": "fee", "match": "fee", "category": "Cost of revenue", …, "thread": "conv-…", "turn": 4, "reason_turn": 1}'

  Fernhill Supply: 3 rule(s) on file
    + Paylane fee                      → Cost of revenue
    + Paylane payout                   → Clearing
    + Kestrel Insurance annual policy  → Prepaid expense (amortize monthly over 12 months)
    also extracted from the thread by MemoryLake: 3 fact(s)
      · For Fernhill Supply, Paylane fees go to Cost of revenue, and Paylane payouts go to the Clearing account until…
      · Fernhill Supply's investors read gross margin with card processing included, so processor costs should be boo…

  Marrow & Pine Café: 2 rule(s) on file
    + Paylane fee                      → Bank and merchant fees
    + Forkful payout                   → Delivery sales
```

The thread arrives in two sittings (messages 1–4, then 5–6), and the demo waits for MemoryLake to read the first one
before appending the rest. That makes them two extraction batches, which step 8 shows.

### 4. Ledger exports

```
$ memorylake lib upload data/sources/marrow-ledger-2026-09-broken.xlsx --name mlu-bkm-marrow-ledger-2026-09.xlsx --on-conflict overwrite
$ memorylake proj doc import --project proj-… <two item ids> --wait
  · import finished in 39s; 1 document(s) ended in status `error`: doc-0b68…
$ memorylake proj stats proj-…
$ memorylake proj doc get --project proj-… doc-0b68…
$ memorylake proj doc inspect --project proj-… doc-5f9f… doc-0b68…

  Fernhill Supply: 1 document(s) · pending 0 · running 0 · okay 1 · error 0
    ✓ fernhill-ledger-2026-q3.xlsx  excel_file · 2 table(s) found:
        Ledger!A1:E19    (no title row)         18 row(s) · Date:string, Payee:string, Memo:string, Amount:double, Category:string
        Ledger!A22:E26   “Recurring vendors”    3 row(s) · Vendor:string, Cadence:string, Typical amount:string, Treatment:string

  Marrow & Pine Café: 2 document(s) · pending 0 · running 0 · okay 1 · error 1
    ✗ marrow-ledger-2026-09.xlsx  doc-0b68…  status error · ERROR_FILE_CORRUPTED — The file is corrupted or unreadable
    ✓ marrow-ledger-2026-q3.xlsx  excel_file · 1 table(s) found:
        Ledger!A1:E13    (no title row)         12 row(s) · Date:string, Payee:string, Memo:string, Amount:double, Category:string
    inspect left out doc-0b68… — the server omits documents in status `error`
```

`proj doc inspect` also returns pre-signed storage links (`persist_path`). They work for anyone holding them until
they expire, so the demo never prints them. It does not print a column's `min_value` / `max_value` either: for a
`double` column they are compared as text (-1250 came back as the minimum of a column holding -3900).

### 5. Categorize the September bank feed

```
$ memorylake search 'Northwind Hosting Servers Sep' --projects proj-… --types document --top-k 5

  Fernhill Supply — September 2026 bank feed
  ✓ 2026-09-02  Paylane             Card processing fees Aug        -231.18  → Cost of revenue
      client rule fact-…: “Fernhill Supply rule: Paylane card processing fees are booked to Cost of revenue (investors…”
  ✓ 2026-09-03  Northwind Hosting   Servers Sep                   -1,480.00  → Cost of revenue
      precedent fernhill-ledger-2026-q3.xlsx · Ledger!A15:E15: 2026-08-04 Servers Aug -1480  (+2 earlier)
  ✓ 2026-09-15  Kestrel Insurance   Cyber policy renewal 2026-27  -3,900.00  → Prepaid expense (amortize monthly over 12 months)
  ✓ 2026-09-18  Teal Freight        Pallet shipping                 -402.50  → Shipping and delivery
      precedent fernhill-ledger-2026-q3.xlsx · Ledger!A18:E18: 2026-08-19 Pallet shipping -412.3  (+2 earlier)
  ✓ 2026-09-21  Lumen Ads           Search campaign Sep             -800.00  → ask the client
  6/6 as expected · rule 3 · precedent 2 · ask 1

  Marrow & Pine Café — September 2026 bank feed
  ✓ 2026-09-02  Paylane             Card processing fees Aug         -88.40  → Bank and merchant fees
      client rule fact-…: “Marrow & Pine Café rule: Paylane card processing fees are booked to Bank and merchant fees …”
      ! precedent disagrees: marrow-ledger-2026-q3.xlsx · Ledger!A10:E10 booked it to Cost of revenue — the rule wins
  ✓ 2026-09-16  Saltmarsh Utilities Electricity Sep                 -309.65  → Utilities
      precedent marrow-ledger-2026-q3.xlsx · Ledger!A13:E13: 2026-08-19 Electricity Aug -318.4  (+2 earlier)
  ✓ 2026-09-26  Orchard Print Co    Menus reprint                   -455.00  → ask the client
      no rule, and no booking of Orchard Print Co in the 1 file(s) search returned
  …
  6/6 as expected · rule 2 · precedent 2 · ask 2
```

An .xlsx document hit carries `items[]` of type `table`. Each is a whole table as one chunk, for example:

```
"range": "A2:E19",
"text": "Date: 2026-06-02, Payee: Paylane, Memo: Card processing fees May, Amount: -204.18, Category: Cost of revenue\nDate: 2026-06-03, …"
```

`inner_tables[].columns` gives the column names. Line *i* of the chunk is row *2 + i*. If a chunk's line count differs
from its range, the demo does not cite it. The search scope is always one client's project, so Marrow & Pine never sees
Fernhill's ledger (try `Northwind Hosting` for Marrow & Pine in the web app's **Categorize a line**: *ask the client*).

### 6. The re-sent export

```
$ memorylake lib upload data/sources/marrow-ledger-2026-09.xlsx --name mlu-bkm-marrow-ledger-2026-09.xlsx --on-conflict overwrite
  · uploaded mlu-bkm-marrow-ledger-2026-09.xlsx (1,804 bytes) → same Library item inode-6d16…
$ memorylake proj doc reload --project proj-… doc-0b68…
$ memorylake proj doc get --project proj-… doc-0b68…
  · doc-0b68…: error → running (0s) → okay (24s)
  Marrow & Pine Café: 2 document(s) · pending 0 · running 0 · okay 2 · error 0
    ✓ marrow-ledger-2026-09.xlsx  doc-0b68…  excel_file · 1 table(s) found:
        Transactions!A1:E4     (no title row)         3 row(s) · …

  ✓ 2026-09-26  Orchard Print Co    Menus reprint                   -455.00  → Marketing
      precedent marrow-ledger-2026-09.xlsx · Transactions!A4:E4: 2026-09-12 Menus reprint -430
  ✓ 2026-09-28  Tidewell Linen      Table linen service             -118.00  → ask the client
```

Only a document in status `error` can be reloaded. It keeps its id, and the reload reads the bytes now in the Library.
An overwrite alone does not reprocess a document.

### 7. Verify

```
$ memorylake proj doc download doc-… --project proj-… -o out/originals/mlu-bkm-fernhill-ledger-2026-q3.xlsx --force
  [1] fernhill-ledger-2026-q3.xlsx · Ledger!A15:E15        ✓ sha256 · ✓ 5 cells match row 15
  [2] fernhill-ledger-2026-q3.xlsx · Ledger!A18:E18        ✓ sha256 · ✓ 5 cells match row 18
  [3] marrow-ledger-2026-q3.xlsx · Ledger!A10:E10          ✓ sha256 · ✓ 5 cells match row 10
  [4] marrow-ledger-2026-q3.xlsx · Ledger!A13:E13          ✓ sha256 · ✓ 5 cells match row 13
  [5] marrow-ledger-2026-q3.xlsx · Ledger!A11:E11          ✓ sha256 · ✓ 5 cells match row 11
  [6] marrow-ledger-2026-09.xlsx · Transactions!A4:E4      ✓ sha256 · ✓ 5 cells match row 4

  6 of 6 cited row(s) check out against the original workbooks.
  Control: the same cells checked against the next row down → 6/6 rejected (the check can fail).
```

Numbers are compared as numbers: MemoryLake prints `-430.00` as `-430`. The citations go to `out/citations.json`, and
`python3 demo.py verify` checks them again.

### 8. Why is it booked that way?

```
$ memorylake conv msg list conv-… --page-size 50
$ memorylake conv consumed-messages conv-… --message conv-entry-…
$ memorylake fact trace fact-… --project proj-…

  Fernhill Supply: “Fernhill Supply rule: Paylane card processing fees are booked to Cost of revenue (investors read gross margin with proc…”
    pinned rule fact-…, from message 4 of “Fernhill Supply — onboarding thread”
    MemoryLake read that message in a batch of 4 (conv consumed-messages):
    ★  1 turn-01  Rafe Okafor (Owner, Fernhill Supply): Our investors read gross margin with card processing included, so…
       2 turn-02  Dana Ruiz (Senior bookkeeper, Ledgerline Bookkeeping): Understood. Most of my clients book Paylane fees…
       3 turn-03  Rafe Okafor (Owner, Fernhill Supply): Not for us. Please book every Paylane fee line to Cost of revenue…
    ▶  4 turn-04  Dana Ruiz (Senior bookkeeper, Ledgerline Bookkeeping): Done. Rule for Fernhill: Paylane fees go to Cost…
      ▶ the message the rule was agreed in   ★ the reason given for it
    messages 5–6 of the thread are not in it: MemoryLake read them in a later batch
    MemoryLake also extracted: “For Fernhill Supply, Paylane fees go to Cost of revenue, and Paylane payouts go to the Clearing account until…”
      fact trace: ADD · COOK · from 4 message(s) — the same batch
```

`consumed-messages` returns each message's `custom_id` (`turn-01` …) and `sequence_no`. The message id comes from
`conv msg list`, where `custom_id` is always null.

## Wiring it into your own practice

- **One project per client.** Every lookup is `--projects <client>`, so a vendor can mean two different things for two
  clients without either one's memory leaking into the other's.
- **Pin the rules; let extraction catch the rest.** A rule is applied verbatim, so pin it (`fact add`) with metadata you can
  match on (`fact update --metadata`, which replaces the whole object). MemoryLake still extracts its own facts from the thread;
  they are useful context, but extraction rewords and merges.
- **Import ledger exports as they are.** An .xlsx comes back from search as tables with cell ranges, a title row when there is one,
  and typed columns (`proj doc inspect`). Keep **one worksheet per export**: a search hit carries one `sheet_name` per
  document, not per table.
- **Failed files are visible and fixable.** Watch `proj stats` for `error`, read the code with `proj doc get`, and after a
  corrected upload under the same name, `proj doc reload` reprocesses the same document. Only documents in `error` can be reloaded.
- **Provenance per rule.** Keep the thread and message in the rule's metadata. `conv consumed-messages` gives the batch the
  message was read in. The message id comes from `conv msg list`, whose `custom_id` is null; `consumed-messages` returns the
  `custom_id` you appended with.

## If something goes wrong

- **A line shows ✗**: run `python3 demo.py categorize <client>` to see what MemoryLake returns now. Freshly imported files can take a few seconds more to be searchable.
- **"N line(s) for that range — not cited"**: a table passage whose line count does not match its cell range. The demo
  refuses to guess a row. Keep exports to a few dozen rows per table.
- **`import finished … status error`** on a first run: that is the broken September export, and it is part of the story.
- **`tls handshake eof` / `could not connect`**: a dropped connection. Read-only commands retry four times; otherwise
  re-run `python3 demo.py` and it resumes.
- **`401 service account not found`**: the key was deleted or belongs to the other deployment.

## Files

```
demo.py                  the runner; prints every CLI command it runs (and emits events for the web app)
data/sources/*.xlsx      the ledger exports (one worksheet each) and the broken September download
data/make_sources.py     regenerates data/sources/ (standard library only)
data/story.json          the firm, the two clients, their threads, rules and September bank feeds
web/server.py            local web companion (stdlib HTTP server + Server-Sent Events over demo.py)
web/static/              the page — no build step
out/                     citations.json, the downloaded originals and runs.jsonl (git-ignored)
```

All names, organisations and figures are fictional.
