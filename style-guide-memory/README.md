# Style guide memory for design teams

A runnable version of the MemoryLake use case
[*Give Design Teams Style Guide Memory That AI Tools Apply Automatically*](https://www.memorylake.ai/en/usecase/style-guide-memory-for-design-teams),
built entirely on the [`memorylake` CLI](https://github.com/memorylake-ai/memorylake-cli).

**The problem.** *The style guide defines everything, and the AI you use produces work that ignores it.* The page promises
**"versioned guide evolution — when rules update, every AI tool sees the new version."** This demo puts that promise under a
real update, and checks every answer the AI tools would get against the file it quotes.

**What this demo shows.** Kestrel Studio (fictional) keeps its style guide, Kestrel UI, as **six Markdown files in one Library
folder**, imported into a **project** that every AI design tool searches before it generates. Version 2 ships, and three people
put their updated files back into the Library three different ways:

| who | command | what the Library does | what search answers |
|---|---|---|---|
| Dev (buttons) | `lib upload buttons.md` (no flag) | the server default is **`rename`**: the new file is stored as **`buttons_1.md`**; `buttons.md` is untouched | the old radius, **4 px**: correct for `buttons.md`, which nobody changed. The new one is in a file nobody indexed |
| Mira (color) | `--on-conflict overwrite` | **same Library item**, new bytes (`size` and `updated_at` change) | **Harbor Teal 500**, while `proj doc download` of the very same document returns **Copper 400** |
| Lena (type) | `--on-conflict replace` | the old item is deleted and a new one created | the old typeface, **Source Serif**, from a document whose file is gone (`proj doc download`: **404**), still ranked #1 |
| release script | `--on-conflict deny` | adds the new `motion.md`; refuses `voice.md` (a draft) with **409 `DRIVE_ITEM_CONFLICT`** | (unchanged, correctly) |

Afterwards the project still lists **6 documents, all `okay`**. Nothing on either listing says an answer is out of date.

1. **Every answer is checked against the file it quotes.** For each question the demo runs `search --types document`, takes
   the sentence the top hit quotes, downloads that document's file as it is now (`proj doc download`), and compares.
   Version 1: **3 of 3 match**. After the release: **0 of 3**, each failing in a different way (*stale*, *gone*, *a newer copy
   is not indexed*).
2. **The two obvious fixes do nothing.** Importing `color.md` again returns `result: duplicate` (the same document, not
   processed again). `proj doc reload` is refused: *"Only a document that failed to process can be reloaded"*
   (`STATE_NOT_READY`). Asked again, the guide still says Harbor Teal 500.
3. **A freshness audit names every problem.** Page both listings to the end (`lib list`, `proj doc list`), download each
   document, and apply one rule per row: **STALE** (the file's `updated_at` is later than the document's `created_at`),
   **ORPHAN** (download 404), **COPY** (`name_N.ext` next to `name.ext`: what the default upload leaves behind),
   **SHADOWED** (the original of such a copy), **NOT INDEXED** (in the Library, not in the project). The three files nobody
   touched must come out **FRESH**, because an audit that flags everything is not an audit.
4. **The repair touches exactly what the audit found.** The copy is promoted. The Library has no rename, so the demo indexes the copy,
   downloads its bytes, overwrites `buttons.md` with them and deletes the copy. Then `proj doc delete` removes the stale and
   orphaned documents and `proj doc import` brings in the current files. Audit: **FRESH 7**. Asked again: **3 of 3 answers
   match their file and quote version 2**. The three untouched files keep their original document ids.

In every run so far (see [Measured](#measured)) the stale index did not refresh by itself: in a separate check, an overwritten
file was still answered from its old text 15 minutes later.

**Watch it run** (real recordings, unedited): *recording in progress*

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
cd memorylake-usecases/style-guide-memory

export MEMORYLAKE_API_KEY=sk-…
python3 demo.py
```

A run takes about 4–5 minutes. Most of it is the three imports: version 1 (30–50 s), promoting the copy (about 20–35 s),
and the repair (about 30–45 s).

With `MEMORYLAKE_API_KEY` set, the demo logs into its own CLI profile under `./.memorylake-demo/`
(git-ignored) and leaves `~/.memorylake` alone. Without it, it uses your existing `memorylake auth login`.

| | |
|---|---|
| `MEMORYLAKE_BASE_URL` | API endpoint; default global, China is `https://app.memorylake.cn/openapi/memorylake` |
| `MEMORYLAKE_WORKSPACE` | Workspace id; default is the one remembered by `ws use`, else your default workspace |
| `python3 demo.py --reset` | Delete everything the demo created first, then run the story |
| `python3 demo.py audit` | The freshness audit, now (reads only; repairs nothing) |
| `python3 demo.py ask "What color is the primary button?"` | Search the guide; each hit is checked against its file (every quoted line must still be in it) |
| `python3 demo.py cleanup` | Delete the style guide project and the Library folder (every file in it) |
| `--show-json` | Print the raw JSON the CLI returns, with pre-signed URLs masked |

**Re-running starts over.** The story moves the Library from version 1 to version 2 for good, so a run that finds the folder or
the project of an earlier run deletes them first and says so (`found the data of an earlier run — this story starts at version 1`).
The project is found by `custom_id` (`mlu-sgm-guide`), the folder by name.

## Or run it in the browser

```bash
python3 web/server.py        # http://127.0.0.1:8767 — standard library only
```

![Web companion — after the release: search says Harbor Teal 500, the file says Copper 400; a newer copy is not indexed; the file behind typography.md is gone](https://github.com/memorylake-ai/memorylake-usecases/raw/main/style-guide-memory/web/screenshot-stale.png)

![Web companion — the freshness audit: shadowed, stale, orphaned, copy, not indexed, and the three untouched files fresh](https://github.com/memorylake-ai/memorylake-usecases/raw/main/style-guide-memory/web/screenshot-audit.png)

![Web companion — after the repair: FRESH 7, untouched documents keep their ids, 3 of 3 answers match and quote version 2](https://github.com/memorylake-ai/memorylake-usecases/raw/main/style-guide-memory/web/screenshot-repair.png)

Paste the key, press **Run demo**, and follow the nine steps: version 1 and its answers, the release table, the same questions
after the release (each answer next to what its file says), the two obvious fixes, the audit table and the repair. **Ask** searches
the guide and checks each hit against its file, and can run the audit again. The terminal drawer shows every `memorylake` command
as it runs.

## What happens, step by step

### 1. Connect

`auth login --api-key` into the isolated profile, `team get`, and the workspace.

### 2. Set up

```bash
memorylake lib mkdir mlu-sgm-kestrel-style-guide --parent MY_SPACE --on-conflict deny
memorylake lib mkdir guide --parent <that folder> --on-conflict deny
memorylake proj create --name 'Kestrel style guide' --custom-id mlu-sgm-guide --description '…' --workspace <ws>
```

### 3. Version 1

```bash
memorylake lib upload data/v1/color.md --parent <guide folder>          # and the other five
memorylake proj doc import --project <proj> --wait <guide folder> --recursive
```

```
  · imported 6 · duplicates 0 · failed 0 in 48s

    document           doc id                 status   Library item
    buttons.md         doc-198f82eb3f0340…    okay     inode-a4aa8ff5e42f…
    color.md           doc-25344aa59a424b…    okay     inode-3295e3eb2067…
    …
```

### 4. Ask the guide

```bash
memorylake search 'What color is the primary button?' --projects <proj> --types document --top-k 3
memorylake proj doc download --project <proj> doc-25344aa59a424b1e9a19d776132aa5e6 --output -
```

```
  Q: What color is the primary button?
    search      #1 color.md (doc-25344aa59a424b…)  “Primary button color is Harbor Teal 500 (#1F7A80).”
    the file    “Primary button color is Harbor Teal 500 (#1F7A80).”
    ✓ the answer matches the file it quotes

  version 1: 3 of 3 answers match their file · 0 of 3 quote version 2
```

`--output -` streams the file to stdout. The sentence compared is the one that starts with the rule's subject
(`Primary button color is`), taken from the hit's `highlight.chunks[].text` and from the file.

### 5. Version 2 ships

```bash
memorylake lib upload data/v2/buttons.md    --parent <guide folder>
memorylake lib upload data/v2/color.md      --parent <guide folder> --on-conflict overwrite
memorylake lib upload data/v2/typography.md --parent <guide folder> --on-conflict replace
memorylake lib upload data/v2/motion.md     --parent <guide folder> --on-conflict deny
memorylake lib upload data/v2/voice.md      --parent <guide folder> --on-conflict deny
```

```
  Dev Raman (components) — uploads the new buttons.md with the plain command
    → stored as buttons_1.md (new item inode-db982ab1f3a4…); buttons.md is untouched
  Mira Okafor (color) — uploads the new color.md with --on-conflict overwrite
    → same item inode-3295e3eb2067…; size 408 → 403 bytes; updated_at 02:33:07 → 02:34:34
  Lena Fischer (type) — uploads the new typography.md with --on-conflict replace
    → new item inode-fa58f4b3c268…; the old item inode-f9a726b0831f… is gone
  the release script — adds the new motion.md with --on-conflict deny
    → new file motion.md → inode-b372ef6ad231…
  the release script — tries voice.md (still a draft) with --on-conflict deny
    → refused — 409 DRIVE_ITEM_CONFLICT; nothing changed, the script stops and asks a person

    the Library folder now: buttons.md, buttons_1.md, color.md, iconography.md, logo.md, motion.md, typography.md, voice.md
    the project still lists 6 documents, all okay — nothing on either listing says an answer is out of date
```

### 6. Ask again

```
  Q: What color is the primary button?
    search      #1 color.md (doc-25344aa59a424b…)  “Primary button color is Harbor Teal 500 (#1F7A80).”
    the file    “Primary button color is Copper 400 (#C8743A).”
    ✗ the file says something else — the index still holds an older version

  Q: What corner radius do buttons use?
    search      #1 buttons.md (doc-198f82eb3f0340…)  “Button corner radius is 4 px on every button size.”
    the file    “Button corner radius is 4 px on every button size.”
    Library     also holds buttons_1.md
    ✗ matches its file — but a newer copy of that file sits next to it, never indexed

  Q: Which typeface is used for body text?
    search      #1 typography.md (doc-281a6b156df64d…)  “Body text typeface is Source Serif at 16 px with a 1.5 line height.”
    the file    404 — the Library file behind this document is gone
    ✗ the file this answer quotes no longer exists (download: 404) — and search still serves it

  after the release: 0 of 3 answers match their file · 0 of 3 quote version 2
```

Same document id, two answers: search returns what was indexed on version 1 day, and download returns the bytes in the Library now.

### 7. The two obvious fixes

```bash
memorylake proj doc import --project <proj> <color.md item>
memorylake proj doc reload --project <proj> <color.md document>
```

```
  Fix 1 — import color.md into the project again:
    result: duplicate · duplicate_count 1 · same document doc-25344aa59a424b… — nothing is processed again
  Fix 2 — reload the document:
    refused: Only a document that failed to process can be reloaded [STATE_NOT_READY]
```

and the guide still answers Harbor Teal 500.

### 8. Freshness audit

```
  Freshness audit (after the release) — 8 Library files · 6 documents
      status       name             indexed   changed   why
    ✗ SHADOWED     buttons.md       02:33:20  02:33:09  current for its file, but buttons_1.md is newer and not indexed
    ✗ STALE        color.md         02:33:20  02:34:34  the file changed after it was indexed; search serves the old text
    ✓ FRESH        iconography.md   02:33:20  02:33:15  indexed after the file's last change
    ✓ FRESH        logo.md          02:33:20  02:33:18  indexed after the file's last change
    ✗ ORPHAN       typography.md    02:33:20  —         its Library file was replaced or deleted (download: 404); search still serves it
    ✓ FRESH        voice.md         02:33:20  02:33:13  indexed after the file's last change
    ✗ COPY         buttons_1.md     —         02:34:29  a newer buttons.md, stored under another name (the default upload renames)
    ✗ NOT INDEXED  motion.md        —         02:34:41  in the Library, never imported
    ✗ NOT INDEXED  typography.md    —         02:34:38  replaced the orphaned typography.md; never imported
    COPY 1 · FRESH 3 · NOT INDEXED 2 · ORPHAN 1 · SHADOWED 1 · STALE 1
```

`indexed` is the document's `created_at` (`proj doc list`), `changed` the file's `updated_at` (`lib list`), shown as UTC time of day.
The Library item's `etag` is **not** a usable fingerprint: a new upload gets a multipart-style etag (`…-1`), an overwrite a plain MD5,
so the two never compare equal even for identical bytes.

### 9. Repair

```bash
# the copy: the Library has no rename, so index it, read its bytes back, put them under the right name
memorylake proj doc import --project <proj> --wait <buttons_1.md item>
memorylake proj doc download --project <proj> <its document> --output -
memorylake lib upload out/buttons.md --parent <guide folder> --on-conflict overwrite
memorylake proj doc delete --project <proj> <its document>
memorylake lib delete <buttons_1.md item>
# stale and orphaned documents out, current files in
memorylake proj doc delete --project <proj> <buttons.md doc>      # and color.md, typography.md
memorylake proj doc import --project <proj> --wait <buttons.md> <color.md> <motion.md> <typography.md (the new item)>
```

```
  Freshness audit (after the repair) — 7 Library files · 7 documents
    ✓ FRESH        buttons.md … ✓ FRESH        voice.md
    FRESH 7

  Files nobody touched keep their documents (the audit did not flag them, the repair did not touch them):
    ✓ voice.md         doc-70ed0d54e3154c… → doc-70ed0d54e3154c…
    ✓ iconography.md   doc-a0ca8068f33941… → doc-a0ca8068f33941…
    ✓ logo.md          doc-6c69533dfb0842… → doc-6c69533dfb0842…

  Q: What color is the primary button?
    search      #1 color.md (doc-3f9a8f6e75184b…)  “Primary button color is Copper 400 (#C8743A).”
    the file    “Primary button color is Copper 400 (#C8743A).”
    ✓ the answer matches the file it quotes
  …
  after the repair: 3 of 3 answers match their file · 3 of 3 quote version 2
```

## Measured

Four complete runs (first run, re-run, `--reset`, web companion), 2026-10-13, all exit 0:

| | every run |
|---|---|
| version 1: answers that match their file | 3 / 3 (4 of 4 runs) |
| after the release | **0 / 3**, every time the same three ways: color *stale*, buttons *newer copy not indexed*, typography *file gone* (4 of 4) |
| re-import / reload | `duplicate` / `STATE_NOT_READY`, and color still *stale* afterwards (4 of 4) |
| audit after the release | SHADOWED 1 · STALE 1 · ORPHAN 1 · COPY 1 · NOT INDEXED 2 · **FRESH 3** (4 of 4) |
| audit after the repair | **FRESH 7** (4 of 4); the 3 untouched documents keep their ids (12 of 12) |
| after the repair | **3 / 3 match and quote version 2** (4 of 4) |
| run time | 245–260 s (imports: version 1 30–48 s, repair 27–44 s) |

The expected document was the #1 hit in every search checked (39 of 39: 10 per CLI run, 9 in the web run).

## Wiring it into your own design tools

- **Publish with `--on-conflict overwrite` (or `deny` first), never the plain upload.** The default `rename` leaves the new version
  under another name. Then **re-index explicitly**: `proj doc delete` the document and `proj doc import` the file again. Re-importing
  without deleting is a no-op (`duplicate`), and `reload` only takes failed documents.
- **Prefer `overwrite` to `replace`.** Both leave the index stale, but `replace` also orphans the document (its file is gone, search still
  serves it, and nothing links the new item to it except the name).
- **Run the audit on a schedule**, or right after every publish: document `created_at` against file `updated_at`, download 404 for
  orphans, `name_N.ext` for copies. Page both listings to the end.
- **Check an answer against its source before trusting it**: the hit's `document_id` downloads the file as it is now; if the quoted
  sentence is not in it, the answer is out of date.

## If something goes wrong

- **`DRIVE_ITEM_CONFLICT` on `lib mkdir`**: a folder named `mlu-sgm-kestrel-style-guide` exists but the demo did not find it (for
  example under another workspace). Delete it, or run `python3 demo.py cleanup`.
- **Step 6 says ✓ instead of ✗**: the server re-indexed the overwritten file on its own. That would be good news; it has not happened
  in any run so far. The demo prints what it sees either way.
- **`tls handshake eof` / `could not connect`**: a dropped connection. Read-only commands retry four times; uploads, folder and project
  creation check whether the call went through before retrying. Otherwise run again; it starts over.
- **`401 service account not found`**: the key was deleted or belongs to the other deployment.

## Files

```
demo.py                  the runner; prints every CLI command it runs (and emits events for the web app)
data/story.json          the studio, the release (who uploads what, how), the three questions and the sentence each one is about
data/v1/                 the six files of version 1 (Markdown, ASCII)
data/v2/                 the four files of version 2 that the release puts back (motion.md is new; voice.md is a draft)
web/server.py            local web companion (stdlib HTTP server + Server-Sent Events over demo.py)
web/static/              the page — no build step
out/                     runs.jsonl, one line per run; the promoted copy's bytes (git-ignored)
```

The studio, its style guide and everyone in it are fictional.
