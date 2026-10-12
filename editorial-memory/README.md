# Editorial memory for publishing teams

A runnable version of the MemoryLake use case
[*Give Publishing Teams Editorial Memory That Holds Across Every Writer*](https://www.memorylake.ai/en/usecase/editorial-memory-for-publishing-teams),
built entirely on the [`memorylake` CLI](https://github.com/memorylake-ai/memorylake-cli).

**The problem.** *The fact-checker re-verifies sources cleared last month. A source flagged as unreliable still turns up in drafts.
The editor flags the same comma in three drafts a week.* Editorial memory exists, but it lives in heads, threads and PDFs, and
nobody can say for sure what the archive holds.

**What this demo shows.** The Tidewater Ledger (fictional) keeps **every published story in one Library folder**, tagged at upload
(`lib upload --xattrs`) with its publication date, desk and author, and indexed in an **archive project** so writers can ask
"have we written about this?". The archive is **bigger than one page**, and that is where it gets interesting:

1. **One call is not the archive.** `lib list <folder>` returns **50 items by default, with a continuation token**. Asking for
   51 per page is refused (`400 page_size must be less than or equal to 50`). Reading every page until the token is null
   returns **55 stories in 2 calls**. The listing is newest first, so the 5 stories past the first page are the 5 oldest.
2. **The list calls page differently.** `proj doc list` returns **20** by default (up to 100 per page). Reconcile "what is in the
   Library folder" against "what is in the index" with one call of each and you get **"35 not indexed", 35 false alarms**.
   Page both lists to the end: **55 = 55, 0 missing**. (4 of 4 runs: 35 false alarms, 0 real gaps.)
3. **The real gap is named.** A late story arrives after indexing. The paged reconciliation finds **exactly 1 not indexed** (the
   one-call version is still lost in its own 35 false alarms, so it can't point to it). Only that story is imported (19–20 s),
   and the index description is updated with `proj update --description` (*"Indexed: 56 stories, latest published 2026-10-09."*).
4. **A retraction flags a whole folder, but files don't inherit folder tags.** Sources sit in one sub-folder per organisation.
   When the Harbor Authority withdraws its tide-gauge data, the standards editor tags the folder with one call:
   `lib xattr set <sources/harbor-authority> --attrs '{"trust":"retracted",…}'`. Ask the files inside for the same keys
   (`lib list --xattr-keys trust,…`) and **0 of 3** carry it, because the server does not propagate folder tags. The demo resolves a
   file's **effective** tag by walking up `parent_item_id` (`lib get`, one level at a time): **3 of 3 retracted, inherited from
   `harbor-authority/`**. The other 4 source files have no trust tag.
5. **A new writer's draft is checked against all of it.** Sam Achebe's first draft on the seawall budget:
   - **Sources**: it cites 4 files. **2 ✗ retracted** (the March 2025 gauge release and the director's statement), with the
     reason, who flagged them and when. 2 ✓. The archive's own story on the retraction is the top hit for *"Harbor Authority retracts tide gauge
     data"* (#1 in 4 of 4 runs).
   - **House style**: four rules, two of them an editor's corrections, stored as **facts on the archive project** (`fact add` with
     several texts in one call), each with its check pattern in metadata (`fact update --metadata '{"rule","pattern","fix"}'`).
     The draft gets **4 ✗**: `$3.2 million`, `54%`, `slammed`, and a missing serial comma. As a negative control, the
     same rules on a published story give **0** flags, and on all 56 archive stories too.
   - **Prior coverage**: `search --types document --top-k 6` over the archive, each hit dated from the folder's tags (read with
     every page). The six hits trace the seawall story from 2023 to 2026. When a hit comes from past the first page of the listing
     it's marked, because a one-call tag lookup would have no date for it. That happened in 1 of 4 runs: the 2023 story ranked #6.
     Retrieval order varies, so this is shown, not asserted.

The story: Port Calder, a coastal town. 56 stories from 2023-03 to 2026-10 across eight desks (13 of them follow the Marsh Road
seawall from the first flood meeting to the construction budget), 7 source files from three organisations, one draft.

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
cd memorylake-usecases/editorial-memory

export MEMORYLAKE_API_KEY=sk-…
python3 demo.py
```

The first run takes about 6–8 minutes. Most of that is indexing the 55 stories (190–210 s); the uploads run 8 at a time (about
20 s). A re-run takes about 100 s.

With `MEMORYLAKE_API_KEY` set, the demo logs into its own CLI profile under `./.memorylake-demo/`
(git-ignored) and leaves `~/.memorylake` alone. Without it, it uses your existing `memorylake auth login`.

| | |
|---|---|
| `MEMORYLAKE_BASE_URL` | API endpoint; default global, China is `https://app.memorylake.cn/openapi/memorylake` |
| `MEMORYLAKE_WORKSPACE` | Workspace id; default is the one remembered by `ws use`, else your default workspace |
| `python3 demo.py --reset` | Delete everything the demo created first, then replay the whole story |
| `python3 demo.py reconcile` | Library folder vs index, now: one call each, then every page |
| `python3 demo.py trust` | Every source file and its effective trust (nearest tag up the folder chain) |
| `python3 demo.py check [FILE]` | Check a draft (default `data/draft.md`): prior coverage, cited sources, house style |
| `python3 demo.py ask "What have we written about the ferry?"` | Search the archive; hits dated from the tags |
| `python3 demo.py cleanup` | Delete the archive project (and the style facts on it) and the Library folder (every file and tag in it) |
| `--show-json` | Print the raw JSON the CLI returns, with pre-signed URLs masked |

It is safe to re-run. The project is found again by `custom_id` (`mlu-edm-archive`), folders and files by name. A re-run starts the story
over without re-indexing: the late story is removed again (from the folder and the index) and the retraction tag is taken off the folder,
so steps 5 and 6 replay the same way.

## Or run it in the browser

```bash
python3 web/server.py        # http://127.0.0.1:8767 — standard library only
```

![Web companion — reconcile: one call each says 35 stories are not indexed; every page says 55 = 55](https://github.com/memorylake-ai/memorylake-usecases/raw/main/editorial-memory/web/screenshot-reconcile.png)

![Web companion — a retraction tags the sources/harbor-authority folder; 0 of 3 files carry it, 3 of 3 inherit it up the parent chain](https://github.com/memorylake-ai/memorylake-usecases/raw/main/editorial-memory/web/screenshot-retraction.png)

![Web companion — the draft check: two retracted sources and four house-style problems highlighted in the draft](https://github.com/memorylake-ai/memorylake-usecases/raw/main/editorial-memory/web/screenshot-check.png)

Paste the key, press **Run demo**, and follow the eight steps: the folder count (one call vs every page), the reconciliation table,
the late story, the folder-level retraction and the effective-trust table, the house-style facts, and the draft with its problems
highlighted. **Ask** searches the archive and can re-run the reconciliation. The terminal drawer shows every `memorylake` command as it runs.

## What happens, step by step

### 1. Connect

`auth login --api-key` into the isolated profile, `team get`, and the workspace.

### 2. Set up: Library tree + archive project

```
mlu-edm-tidewater-ledger/        {"publication": "The Tidewater Ledger"}
├── archive/                     {"collection": "archive"}
└── sources/                     {"collection": "sources"}
    ├── harbor-authority/        {"org": "harbor-authority"}
    ├── state-coastal-office/    {"org": "state-coastal-office"}
    └── town-records/            {"org": "town-records"}
```

`lib mkdir <name> --parent <id> --on-conflict deny --xattrs '{…}'` for each folder, and `proj create --custom-id mlu-edm-archive`.

### 3. Archive: 55 stories, tagged at upload, then counted

```
$ memorylake lib upload data/archive/2023-03-14-marsh-road-residents-ask-council-for-a-seawall-a.md --parent <archive> --xattrs '{"published":"2023-03-14","desk":"metro","author":"Ruth Okafor"}'
  · … the same command for the other 54 stories, oldest first, 8 at a time (echo off)
  · 55 stories uploaded with their tags in 22s; 0 were already there

  How many stories are in the archive folder?
$ memorylake lib list <archive> --xattr-keys published
    one call, default page size:  50 files  + a continuation_token (there is more)
$ memorylake lib list <archive> --page-size 51
    asking for 51 per page:       refused — page_size must be less than or equal to 50
$ memorylake lib list <archive> --xattr-keys published,desk,author --page-size 50
    every page until the token is null: 55 files in 2 calls

  The 5 the first page never shows (the listing is newest first, so these are the oldest):
    2023-03-14  metro        2023-03-14-marsh-road-residents-ask-council-for-a-seawall-a.md
    2023-04-02  schools      2023-04-02-calder-high-adds-a-robotics-elective-after-a-par.md
    2023-05-19  business     2023-05-19-harbor-street-bakery-closes-after-41-years.md
    2023-06-30  environment  2023-06-30-eelgrass-beds-in-calder-bay-shrink-by-a-third-st.md
    2023-08-11  sports       2023-08-11-calder-gulls-win-the-county-sailing-regatta-for.md

  55 of 55 stories carry all three tags (published, desk, author)
```

The listing is ordered by `created_at`, newest first. The first upload batch is exactly the stories that won't fit on page 1, so
the oldest stories are the ones a one-call listing misses.

### 4. Index, then reconcile

```
$ memorylake proj doc import --project <archive project> --wait <archive folder> --recursive
  · imported 55 · duplicates 0 · failed 0 in 211s
$ memorylake proj stats <archive project>
    proj stats: 55 documents · okay 55 · running 0 · error 0

  Reconcile the Library folder against the index (after indexing)
$ memorylake lib list <archive>
$ memorylake proj doc list --project <archive project>
$ memorylake lib list <archive> --page-size 50
$ memorylake proj doc list --project <archive project> --page-size 100
    one call each (default page sizes):  Library 50 · index 20  → “35 not indexed”  ✗ false alarm — 35 of them are indexed
    every page:                           Library 55 · index 55  → not indexed 0 · indexed but not in the folder 0  ✓
$ memorylake proj update <archive project> --description 'Every story The Tidewater Ledger has published, for prior-coverage searches. Indexed: 55 stories, latest published 2026-10-07.'
```

`lib list` and `proj doc list` don't return items in the same order, so their default first pages overlap by only 15. A reconciliation
that reads one page of each finds 35 "unindexed" stories that are all indexed.

### 5. A late story: find exactly what the index is missing

```
  2026-10-09: the politics desk files “2026-10-09-council-to-set-seawall-construction-budget-in-no.md” to the archive folder
    one call each (default page sizes):  Library 50 · index 20  → “35 not indexed”  ✗ false alarm — 34 of them are indexed
    every page:                           Library 56 · index 55  → not indexed 1 · indexed but not in the folder 0
      not indexed: 2026-10-09-council-to-set-seawall-construction-budget-in-no.md
$ memorylake proj doc import --project <archive project> --wait <that file's item id>
  · imported 1 · duplicates 0 · failed 0 in 19s
    every page:                           Library 56 · index 56  → not indexed 0 · indexed but not in the folder 0  ✓
```

### 6. Sources: a retraction flags a whole folder

```
  2025-09-25: Elena Voss, standards editor flags the whole sources/harbor-authority/ folder — one call:
$ memorylake lib xattr set <sources/harbor-authority> --attrs '{"trust":"retracted","flagged_on":"2025-09-25","flagged_by":"Elena Voss","reason":"State inspection found a faulty gauge sensor; the Authority withdrew the March 2025 data"}'

  The files inside it, asking for the same keys (--xattr-keys trust,flagged_on,flagged_by,reason):
    2025-04-02-harbor-director-statement.md      trust = (none)
    2025-03-06-tide-gauge-table.md               trust = (none)
    2025-03-06-tide-gauge-release.md             trust = (none)
    files carrying the tag themselves: 0 of 3  — folder tags are not inherited; the server returns none for the files

  Effective trust = the nearest tag walking up parent_item_id (lib get, one level at a time):
    ✗ harbor-authority/2025-04-02-harbor-director-statement.md       retracted (inherited from harbor-authority/)
    ✗ harbor-authority/2025-03-06-tide-gauge-table.md                retracted (inherited from harbor-authority/)
    ✗ harbor-authority/2025-03-06-tide-gauge-release.md              retracted (inherited from harbor-authority/)
    · state-coastal-office/2026-09-15-corrected-sea-level-series.md  no trust tag on the file or its folders
    …
  retracted by inheritance: 3 of 3 files in sources/harbor-authority/  ✓
```

Inheritance is resolved **by the demo**, not by MemoryLake: the server keeps tags on the item you set them on. The walk stops at
the first item (the file itself, then each parent folder) that has a `trust` tag, so a file-level tag would override its folder's.

### 7. House style as facts

```
$ memorylake fact add --project <archive project> 'House style: write '"'"'percent'"'"' in copy; never use the % sign.' '…' '…' '…'
$ memorylake fact update <fact id> --project <archive project> --metadata '{"kind":"house-style","rule":"house-style-percent","pattern":"\\d+(?:\\.\\d+)?\\s*%","fix":"write '"'"'percent'"'"'"}'
```

A writer or an AI drafting tool reads the sentence; the draft check runs the pattern. Both live on one fact.

### 8. A new writer's draft

```
  Sources cited in the draft:
    ✗ 2025-03-06-tide-gauge-release.md                 retracted — State inspection found a faulty gauge sensor; … (Elena Voss, 2025-09-25; tag on harbor-authority/)
    ✗ 2025-04-02-harbor-director-statement.md          retracted — … (Elena Voss, 2025-09-25; tag on harbor-authority/)
    ✓ 2026-05-07-state-grant-award-letter.md           town-records, no retraction
    ✓ 2026-03-12-bond-election-results.md              town-records, no retraction
    the archive on it (“Harbor Authority retracts tide gauge data”): #1 2025-09-25 2025-09-25-harbor-authority-retracts-tide-gauge-data-after.md  ✓

  House style (4 rules from the archive project's facts):
    ✗ line 9   house-style-money          “$3.2 million” → write 'N million dollars'
    ✗ line 9   house-style-percent        “54%” → write 'percent'
    ✗ line 11  banned-slammed             “slammed” → say 'criticized'
    ✗ line 11  house-style-oxford-comma   “Residents, business owners and fishermen” → add a comma before 'and'
    same rules on a published story (2026-10-09-council-to-set-seawall-construction-budget-in-no.md): 0 flag(s)  ✓ (the checks are not just noisy)

  Verdict: 2 source(s) to replace · 4 style fix(es) · 6 prior stories to read first
```

Prior coverage (from the first run; the order varies between runs):

```
  Prior coverage — “Marsh Road seawall flooding residents council bond” (top 6, shown oldest first, dates from the tags):
    2023-03-14  #6  Ruth Okafor      2023-03-14-marsh-road-residents-ask-council-for-a-seawall-a.md   ← past page 1
    2024-08-22  #5  Ruth Okafor      2024-08-22-seawall-study-three-designs-for-marsh-road-none.md
    2025-02-14  #4  Ruth Okafor      2025-02-14-king-tide-floods-marsh-road-and-the-ferry-lot.md
    2025-07-31  #2  Ruth Okafor      2025-07-31-seawall-cost-estimate-rises-to-14-million-dollar.md
    2025-10-09  #3  Dana Whitlock    2025-10-09-seawall-bond-question-will-go-to-voters-in-march.md
    2026-03-12  #1  Dana Whitlock    2026-03-12-voters-approve-marsh-road-seawall-bond-54-to-46.md
    1 of these comes from past the first page of the listing — a one-call tag lookup would have no date for it
```

## Wiring it into your own editorial tools

- **Page every list.** Loop until `continuation_token` is null. Don't stop when a page comes back short or when you hit the page
  size you asked for. Page sizes differ per call: `lib list` up to 50 (default 50), `proj doc list` up to 100 (default 20),
  `fact list` up to 200 (default 50).
- **Reconcile by name, both directions**: "in the folder, not indexed" (import it) and "indexed, not in the folder" (someone
  deleted or renamed a file; `proj doc delete` it).
- **Folder tags are yours to resolve.** Tag the folder once (`lib xattr set <folder>`), and resolve a file's effective tag by
  walking `parent_item_id` with `lib get` (cache the folders; one call per level). Decide whether a file-level tag overrides its
  folder's; this demo says yes.
- **Rules as facts**: the sentence is the memory an AI tool retrieves; a machine-checkable pattern in `metadata` keeps the checker
  and the rule in one place. `fact update --metadata` replaces the whole metadata object.

## If something goes wrong

- **The run seems stuck at `proj doc import … --wait`**: indexing 55 stories takes about 3.5 minutes. `proj stats` in another
  terminal shows `running` counting down.
- **`DRIVE_ITEM_CONFLICT` on `lib mkdir`**: a folder named `mlu-edm-tidewater-ledger` exists but the demo did not find it (for example
  under another workspace). Delete it, or run `python3 demo.py cleanup`.
- **`tls handshake eof` / `could not connect`**: a dropped connection. Read-only commands, `lib xattr` and `fact update` retry four
  times; uploads, folder and project creation check whether the call went through before retrying. Otherwise re-run; it resumes.
- **"0 of these come from past the first page"**: normal in most runs; it depends on retrieval order (see step 8).
- **`401 service account not found`**: the key was deleted or belongs to the other deployment.

## Files

```
demo.py                  the runner; prints every CLI command it runs (and emits events for the web app)
data/story.json          the paper, each story's tags, the source folders, the retraction, the house-style rules, the draft settings
data/archive/            the 56 published stories (Markdown, ASCII)
data/sources/<org>/      7 source documents from three organisations
data/draft.md            the new writer's draft
web/server.py            local web companion (stdlib HTTP server + Server-Sent Events over demo.py)
web/static/              the page — no build step
out/                     runs.jsonl, one line per run (git-ignored)
```

The paper, the town, everyone in it and every story are fictional.
