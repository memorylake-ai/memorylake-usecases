# Parent communication memory for schools

A runnable version of the MemoryLake use case
[*Give Schools Parent Communication Memory That Holds Across Teachers and Channels*](https://www.memorylake.ai/en/usecase/parent-communication-memory-for-schools),
built entirely on the [`memorylake` CLI](https://github.com/memorylake-ai/memorylake-cli).

**The problem.** *The math teacher heard about the homework tears. The English teacher doesn't know. The counselor knows about the
separation, and nobody else should.* At a small school, what a family has told the school is split across email, phone calls, the
parent portal and hallway chats, and nobody can say who has (or hasn't) talked to the family before a parent meeting.

**What this demo shows.** Maplebrook Academy (fictional) keeps **one Library folder per family**. Every communication goes in as a
file, **tagged as it is uploaded** (`lib upload --xattrs`) with who logged it, the channel, the date, how sensitive it is and
whether someone still owes the family an answer. Those tags do three jobs:

| Tag | Job |
|---|---|
| `sensitivity` = `staff` / `counselor` | decides which project the file is imported into: the **teachers' project** (every teacher's tools search it) or the **counselor-only project** (only the counselor's tools search it) |
| `teacher`, `channel`, `logged_on` | a **completeness audit** before the parent meeting: who has no logged contact with the family, and when each teacher last heard from them |
| `follow_up` = `open` → `resolved_on` + `outcome` | promises still owed, closed after the meeting with `lib xattr delete` + `lib xattr set` |

The story: Leo Whitfield (Grade 6), parents Dana and Marcus. Six communications between 2026-09-08 and 2026-10-09: an email
about fractions homework, a hallway chat (chess club; *call him Leo, never Leonard*), a phone call (a family trip to Lisbon, Nov 3–7),
a **confidential counselor note** (the parents separated in August), and two portal messages (a math tutor; a reading-log extension).
The parent meeting is on 2026-10-14.

What you see:

1. **The default `lib list` does not return your tags.** Its `x_attrs` holds one key, `x_thumbnail_uri` (a pre-signed S3 URL with an
   access key id and a signature, which the demo never prints), or nothing at all for files whose thumbnail isn't ready yet. Custom tags shown: **0 of 6**.
   The same folder with `--xattr-keys family,channel,teacher,logged_on,sensitivity,follow_up` returns **exactly those keys, 6 of 6**,
   and no signed URL. This is the first demo here to use `--xattr-keys`.
2. **Route by tag.** 5 files go into the teachers' project, the counselor note into the counselor-only one. A leak check
   intersects the teachers' document list with the folder's counselor-tagged files: **0**. If someone imported the note into the
   teachers' project by hand, the check finds it and removes it with `proj doc delete`.
3. **The audit is one call.** `lib list <family folder> --xattr-keys channel,teacher,logged_on,sensitivity,follow_up,resolved_on,outcome`:
   **Mr. Lindqvist (science) has no logged contact** with the family, and **3 follow-ups are open** (the oldest for 34 days). No
   file is opened and nothing is searched.
4. **The same question, two scopes.** *"Is anything going on at home that we should know before Leo's parent meeting?"*. From the
   teachers' project, 0 of the hits mention the separation. From the counselor's scope, the counselor note is hit **#1**.
   Every hit is shown with its tags (channel · who logged it · date · sensitivity), read back by file name. And when the English
   teacher asks *"What did the family say about Leo's math homework?"*, the top hit is the math teacher's email thread.
5. **After the meeting**, two follow-ups are closed (`lib xattr delete --keys follow_up`, then `lib xattr set` with `resolved_on` and
   `outcome`), and the audit now shows **1 open**: the Lisbon worksheets, still owed by October 31.

```
setup     ── lib mkdir mlu-pcs-maplebrook --xattrs '{"school":…}' · lib mkdir whitfield --parent … · proj create ×2
capture   ── lib upload <file> --parent <family folder> --xattrs '{"family":…,"channel":…,"teacher":…,"logged_on":…,"sensitivity":…,"follow_up":…}' ×6
             lib list <family folder>                         (default: x_attrs has no custom tags)
             lib list <family folder> --xattr-keys a,b,c,…    (exactly the tags asked for)
route     ── proj doc import --project <teachers|counselor> --wait <items by sensitivity> · proj doc list (leak check)
audit     ── lib list <family folder> --xattr-keys channel,teacher,logged_on,sensitivity,follow_up,resolved_on,outcome
prep      ── search "<question>" --projects <teachers>             --types document --top-k 5
             search "<question>" --projects <teachers>,<counselor> --types document --top-k 5
after     ── lib xattr delete <file> --keys follow_up · lib xattr set <file> --attrs '{"resolved_on":…,"outcome":…}' · audit again
```

Runs in about 2 minutes on a free personal account (most of it is the two document imports). Documents do not go through fact
extraction, so nothing here depends on an LLM. The only credential you need is a MemoryLake API key.

**Measured** (every complete run while building this demo, free account): default listing 0 of 6 files with custom tags and
projection 6 of 6 with exactly their tags, every time. The two imports took 31–48 s and 19–36 s. In the runs with the files where they belong,
teachers' scope hits mentioning the separation were 0 and the counselor note was hit #1 for the counselor every time. With the note
deliberately imported into the teachers' project as well (a negative control), the leak check reported 1, the teachers' scope
showed the note, and the next run removed it. The math-homework question put the math teacher's email first in every run.

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
cd memorylake-usecases/parent-communication-memory

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
| `python3 demo.py audit` | The completeness audit, now (one `lib list --xattr-keys` call) |
| `python3 demo.py tags` | Every file in the family folder with its tags |
| `python3 demo.py ask "When is Leo away in November?"` | Search as a teacher (the teachers' project) |
| `python3 demo.py ask "How is Leo doing at home?" --as counselor` | Search as the counselor (both projects) |
| `python3 demo.py cleanup` | Delete both projects and the school's Library folder (every file and tag in it) |
| `--show-json` | Print the raw JSON the CLI returns, with pre-signed URLs masked |

It is safe to re-run. The projects are found again by `custom_id` (prefix `mlu-pcs-`), the folder and files by name. A re-run
resets every file's tags to the story's start (follow-ups open again, no outcomes), so the audit tells the same story twice.

## Or run it in the browser

```bash
python3 web/server.py        # http://127.0.0.1:8765 — standard library only
```

![Web companion — capture: the default listing shows no custom tags; --xattr-keys returns exactly the six files' tags](https://github.com/memorylake-ai/memorylake-usecases/raw/main/parent-communication-memory/web/screenshot-capture.png)

![Web companion — meeting prep: the same question from a teacher (no hit mentions the home situation) and from the counselor (her note is #1)](https://github.com/memorylake-ai/memorylake-usecases/raw/main/parent-communication-memory/web/screenshot-prep.png)

Paste the key, press **Run demo**, and watch the folder fill, the two listings side by side, the routing and leak check, the
audit, the two scopes, and the follow-ups closing. **Ask** searches as a teacher or as the counselor and shows each hit's tags, and can
re-run the audit. The terminal drawer shows every `memorylake` command as it runs.

## What happens, step by step

### 1. Connect

`auth login --api-key` into the demo's own profile, `team get`, and the workspace (`ws current`, else your first workspace).

### 2. Set up — a folder per family, two projects

```
$ memorylake lib mkdir mlu-pcs-maplebrook --parent MY_SPACE --on-conflict deny --xattrs '{"school":"Maplebrook Academy"}'
$ memorylake lib mkdir whitfield --parent <school folder> --on-conflict deny --xattrs '{"school":"Maplebrook Academy","family":"whitfield"}'
$ memorylake proj create --name 'Whitfield family — teachers' --custom-id mlu-pcs-staff …
$ memorylake proj create --name 'Whitfield family — counselor only' --custom-id mlu-pcs-counselor …
```

The files live once, in the family folder. A project only holds the files its readers may search.

### 3. Capture — six communications, tagged as they are uploaded

```
$ memorylake lib upload data/whitfield/2026-09-08-email-adebayo.md --parent <family folder> --xattrs '{"family":"whitfield","channel":"email","teacher":"Mr. Adebayo","logged_on":"2026-09-08","sensitivity":"staff","follow_up":"open"}'
…
  The default listing — what does `x_attrs` hold?
$ memorylake lib list <family folder> --page-size 50
    2026-10-09-portal-reyes.md         (none)
    2026-10-06-portal-adebayo.md       (none)
    2026-09-30-counselor-ibarra.md     (none)
    2026-09-22-phone-reyes.md          x_thumbnail_uri = <signed URL, not printed>
    2026-09-15-hallway-reyes.md        x_thumbnail_uri = <signed URL, not printed>
    2026-09-08-email-adebayo.md        x_thumbnail_uri = <signed URL, not printed>

  custom tags in the default listing: 0 of 6 files  ✓ (they are stored, just not returned unless asked for)

  The same folder, asking for the tags by name (--xattr-keys family,channel,teacher,logged_on,sensitivity,follow_up):
$ memorylake lib list <family folder> --xattr-keys family,channel,teacher,logged_on,sensitivity,follow_up --page-size 50
    channel         teacher      logged_on   sensitivity  follow_up
    email           Mr. Adebayo  2026-09-08  staff        open
    hallway         Ms. Reyes    2026-09-15  staff        ·
    phone           Ms. Reyes    2026-09-22  staff        open
    counselor-note  Ms. Ibarra   2026-09-30  counselor    ·
    portal          Mr. Adebayo  2026-10-06  staff        ·
    portal          Ms. Reyes    2026-10-09  staff        open

  6 of 6 files carry exactly the tags they were logged with · signed URLs in the projection: 0
```

The files are plain Markdown with a header (From / To / Date, or Logged by / With / Date). Email (`.eml`) and HTML files did
not import in our probes, so an export from a mail system should be converted to text or Markdown first.

### 4. Route by tag — sensitivity decides who can search it

```
$ memorylake proj doc import --project <teachers> --wait <5 items tagged staff>
  · 5 file(s) imported into the staff project in 31s
$ memorylake proj doc import --project <counselor> --wait <1 item tagged counselor>
  · 1 file(s) imported into the counselor project in 19s

  leak check: counselor-tagged files in the teachers' project: 0  ✓
```

### 5. Before the parent meeting — a completeness audit from the tags

```
$ memorylake lib list <family folder> --xattr-keys channel,teacher,logged_on,sensitivity,follow_up,resolved_on,outcome --page-size 50

  Who has talked to the Whitfield family (as of 2026-10-12, window 21 days):
    ✓ Mr. Adebayo    Grade 6 math                2 logged · last 2026-10-06 · email, portal
    ✓ Ms. Reyes      Grade 6 English, homeroom   3 logged · last 2026-10-09 · hallway, phone, portal
    ✗ Mr. Lindqvist  Grade 6 science             0 logged  ← no logged contact
    ✓ Ms. Ibarra     School counselor            1 logged · last 2026-09-30 · counselor-note

  Open follow-ups: 3
    • 2026-09-08-email-adebayo.md        Mr. Adebayo  email    open 34 days
    • 2026-09-22-phone-reyes.md          Ms. Reyes    phone    open 20 days
    • 2026-10-09-portal-reyes.md         Ms. Reyes    portal   open 3 days
```

The staff roster (who *should* have heard from the family) is in `data/story.json`; the audit compares it with the tags.

### 6. Meeting prep — the same question, two scopes

```
  Ms. Reyes (teachers’ project) asks: “Is anything going on at home that we should know before Leo's parent meeting?”
$ memorylake search '…' --projects <teachers> --types document --top-k 5
    · 1. 2026-09-08-email-adebayo.md  [email · Mr. Adebayo · 2026-09-08 · staff]
         “Hi Mr. Adebayo, Leo has been spending more than an hour on the fractions homework most nights, …”
    · 2. 2026-10-06-portal-adebayo.md  [portal · Mr. Adebayo · 2026-10-06 · staff]
    · 3. 2026-10-09-portal-reyes.md  [portal · Ms. Reyes · 2026-10-09 · staff]
    · 4. 2026-09-22-phone-reyes.md  [phone · Ms. Reyes · 2026-09-22 · staff]
    · 5. 2026-09-15-hallway-reyes.md  [hallway · Ms. Reyes · 2026-09-15 · staff]

  Ms. Ibarra (teachers’ + counselor projects) asks: “Is anything going on at home …?”
$ memorylake search '…' --projects <teachers>,<counselor> --types document --top-k 5
    ! 1. 2026-09-30-counselor-ibarra.md  [counselor-note · Ms. Ibarra · 2026-09-30 · counselor]
         “Leo's parents separated in August. Leo lives with his mother on weekdays and with his father on alternate weekends. …”
    · 2. 2026-09-08-email-adebayo.md  [email · Mr. Adebayo · 2026-09-08 · staff]
    …
  hits mentioning the home situation (“separated”, “handover”, “alternate weekends”): teachers 0 · counselor 1 (rank 1)
  ✓ the counselor's note stays out of the teachers' scope

  Ms. Reyes (English) asks the teachers' project: “What did the family say about Leo's math homework?”
    · 1. 2026-09-08-email-adebayo.md  [email · Mr. Adebayo · 2026-09-08 · staff]
  top hit logged by Mr. Adebayo: ✓ another teacher's thread, found from hers
```

`search` returns its top-k whether or not a hit is relevant: with five files in scope, every file shows up. The point here is
*which* files can show up, and the tags on each one. The teachers' result can never contain the counselor note, because that
document is not in the teachers' project.

### 7. After the meeting — close follow-ups, audit again

```
$ memorylake lib xattr delete <email file> --keys follow_up
$ memorylake lib xattr set <email file> --attrs '{"resolved_on":"2026-10-14","outcome":"Shorter fractions sheet agreed; Leo practises 20 minutes a night."}'
$ memorylake lib xattr delete <reading-log file> --keys follow_up
$ memorylake lib xattr set <reading-log file> --attrs '{"resolved_on":"2026-10-14","outcome":"Reading log extension to October 20 granted."}'

  Open follow-ups: 1
    • 2026-09-22-phone-reyes.md          Ms. Reyes    phone    open 22 days
  Closed: 2
    ✓ 2026-09-08-email-adebayo.md        2026-10-14 — Shorter fractions sheet agreed; Leo practises 20 minutes a night.
    ✓ 2026-10-09-portal-reyes.md         2026-10-14 — Reading log extension to October 20 granted.
```

## Wiring it into your own school tools

- **Tag at the moment of capture.** `lib upload --xattrs '{…}'` stores the file and its provenance in one call; `lib xattr set`
  adds or overwrites keys later (it merges by key, it does not replace the whole set); `lib xattr delete --keys` removes keys.
  Values must be JSON strings.
- **Read tags with `--xattr-keys`.** The default listing does not include custom keys, and does include a pre-signed thumbnail
  URL for most files. Ask for the keys you need: you get exactly those, and no URL to keep out of your logs.
- **Don't name your keys `x_…`.** Those are the server's (`x_source`, `x_origin_type`, `x_origin_driver`, `x_thumbnail_uri`). In our
  probes `lib xattr set` and `lib xattr delete` on them reported success and changed nothing.
- **Let sensitivity decide the project, not the folder.** Files live once; each reader's tools search only the projects they
  may read. Keep a leak check (project documents ∩ files tagged for a narrower audience) and run it after every import.
- **Projects don't link back to Library items.** `proj doc list` returns document ids and names, not item ids. Map them by file
  name (unique within the family folder), or keep the `drive_item_id → document_id` pairs that `proj doc import` returns.
- **Personal accounts have no team members**, so "the counselor" here is a scope the demo chooses, not a separate login. On a
  team account, give each staff role its own API key and only the projects it should search.

## If something goes wrong

- **Some files show `(none)` in the default listing**: their thumbnail isn't generated yet. The custom tags are there either way;
  `python3 demo.py tags` reads them.
- **`DRIVE_ITEM_CONFLICT` on `lib mkdir`**: a folder named `mlu-pcs-maplebrook` exists but the demo did not find it (for example
  under another workspace). Delete it, or run `python3 demo.py cleanup`.
- **`tls handshake eof` / `could not connect`**: a dropped connection. Read-only commands and `lib xattr` retry four times; folder,
  project and file creation check whether the call went through before retrying. Otherwise re-run `python3 demo.py`; it resumes.
- **`401 service account not found`**: the key was deleted or belongs to the other deployment.
- **"✗ sensitive text reached the teachers' scope"**: the counselor note was imported into the teachers' project. The leak check
  in step 4 removes it, so the next run is clean.

## Files

```
demo.py                  the runner; prints every CLI command it runs (and emits events for the web app)
data/story.json          the school, the family, the staff roster, each file's tags, the questions, the meeting outcomes
data/whitfield/          the six communications (Markdown)
web/server.py            local web companion (stdlib HTTP server + Server-Sent Events over demo.py)
web/static/              the page — no build step
out/                     runs.jsonl, one line per run (git-ignored)
```

All people, the school and the communications are fictional.
