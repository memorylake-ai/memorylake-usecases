# Resolved comment memory: a resolved comment reaches the index — that it was resolved does not

A runnable version of the MemoryLake use case
[*Give Agencies Creative Brief Memory That Compounds Across Every Client Project*](https://www.memorylake.ai/en/usecase/creative-brief-memory-across-agency-client-work),
built entirely on the [`memorylake` CLI](https://github.com/memorylake-ai/memorylake-cli) (v20261015 or later).

**Watch it run:** videos are being recorded.

**The problem.** Halfmoon Studio (a fictional design studio) keeps its past client briefs in shared memory, so that a
brief assistant can answer *"when does Fernlight launch?"* or *"what's in the Orchard & Pine hero image?"* before the
next brief is written — the page's "per-client context memory". The briefs are Word files, and the review happened
in **Word comments**:

```
PN1  resolved on “Launch date”  Priya Nandakumar: “Move the launch to Tuesday, November 3, so it lines up with the webinar.”
```

The team said no, and someone clicked **Resolve** without writing a reply — the usual way a turned-down suggestion
is closed. In Word the thread greys out and folds away. In the file the comment is still there; the only thing
that changed is one flag, `w15:done="1"` on its entry in `word/commentsExtended.xml`.

MemoryLake's index reads every comment as a line of its own — `Commented [PN1]: …` — and **a resolved comment reads
exactly like an open one**: the flag never makes it into the text. Asked *"When does Fernlight Sync 3.0 launch?"*,
the model was given (a checker call copies out what it was given):

```
- (mlu-rcm-fernlight-sync-launch.docx) Fernlight Sync 3.0 launch brief
Launch date: Fernlight Sync 3.0 goes live on Tuesday, October 27, 2026, at 9:00 Pacific.
Headline: Sync every file across every device, with no manual steps.
…
Commented [PN1]: Move the launch to Tuesday, November 3, so it lines up with the webinar.

Commented [PN2]: Change the headline to: The fastest file sync on the market.
Commented [PN3]: Trial length is 14 days - please write it into the line.
Commented [OC4]: Add a co-marketing post with our reseller Tallgrass Systems on launch day.
```

and answered *"Fernlight Sync 3.0 launches on Tuesday, November 3, 2026, at 9:00 Pacific."* — 15 times out of 15.
The brief says October 27 two lines above; nothing in the text says the November idea was turned down, and a
comment reads like the latest word.

**The fix** is a 40-line function in `demo.py`, run before upload: `drop_resolved()` reads
`commentsExtended.xml` and removes every resolved thread — the comment, its markers in the body, its entry —
and leaves the open threads and the brief text as they are. Open comments are still live ("Please send layered
PSD files along with the PNGs"), and the demo checks they still get used.

## What the demo shows

1. **Connect** — API key → isolated CLI profile, team, workspace.
2. **Set up** — two projects (*as saved* / *resolved dropped*), a MemoryRouter boundary over each.
3. **The brief library** — three Word files: two with comment threads (7 resolved, 2 open), one with none (the
   control). The demo prints each thread read from the file, open or resolved, makes the second copy with
   `drop_resolved()`, uploads both sets and imports each into its project.
4. **The comments in the index** — `search --types document` per brief and project: the line the index holds for
   each comment, and how many times a word like "resolved" appears in that text (0).
5. **Ask through MemoryRouter — as saved** — eight questions, each 3× through `router chat --boundary`
   (gpt-4o-mini, temperature 0), plus a checker call (claude-haiku-4-5 copies out what it was given).
6. **Ask through MemoryRouter — resolved dropped** — the same eight questions through the other boundary.
7. **Scoreboard** — per boundary: the brief's own line vs the turned-down suggestion, the open-comment question,
   the uncommented brief, and the checker.

The questions: six about lines with a resolved suggestion on them, asked the way a teammate who half-remembers the
discussion asks (*"Did the Orchard & Pine budget go up to 60,000 dollars?"*); one answered by an open comment
(*"In what file formats does Orchard & Pine want the final files?"* — PNG in the brief, layered PSD in the open
comment); one about the uncommented brief.

## Prerequisites

- Python 3.9+ (standard library only)
- The `memorylake` CLI, **v20261015 or later** (`memorylake router` is new in that release):
  ```bash
  curl -fsSL https://raw.githubusercontent.com/memorylake-ai/memorylake-cli/main/scripts/install.sh | sh
  ```
- A MemoryLake API key ([app.memorylake.ai](https://app.memorylake.ai) → API Keys)
- Router calls are billed to your MemoryLake balance: 64 calls per run, about 20,000 prompt + 4,800 reply tokens.
  Without balance, the memory steps (1–4) still run and the answers are skipped.

## Run it

```bash
git clone https://github.com/memorylake-ai/memorylake-usecases.git
cd memorylake-usecases/resolved-comment-memory
export MEMORYLAKE_API_KEY=sk-...          # logs into an isolated profile under .memorylake-demo/
python3 demo.py                           # about 4.5 minutes
python3 demo.py brief op                  # read-only: one brief's threads and their lines in both projects' index
python3 demo.py threads                   # offline: the threads the .docx files hold, and what drop_resolved() keeps
python3 demo.py cleanup                   # delete everything the demo created
```

China region: also `export MEMORYLAKE_BASE_URL=https://app.memorylake.cn/openapi/memorylake`.
Everything the demo creates is prefixed `mlu-rcm-`; every run starts over.

## Or run it in the browser

```bash
python3 web/server.py                     # opens http://127.0.0.1:8765
```

Paste the key, press **Run demo**. The page follows the run step by step: the briefs laid out like Word's review
pane (resolved balloons greyed out, as Word shows them), each comment's line in the index of both projects, the
questions under each boundary with the checker's reading, and a scoreboard. **Brief** looks up one brief in both
projects without a model call. A terminal drawer shows every `memorylake` command as it runs. **Clean up** removes
it all.

## What happens, step by step

### Steps 2–3: two projects, two boundaries, one brief library

```
$ memorylake proj create --name 'Halfmoon Studio — Client brief library (as saved)' --custom-id mlu-rcm-saved …
$ memorylake boundary create --name 'Brief assistant: as saved' --custom-id mlu-rcm-saved --projects proj-… …
$ memorylake proj create --name 'Halfmoon Studio — Client brief library (resolved dropped)' --custom-id mlu-rcm-dropped …
$ memorylake boundary create --name 'Brief assistant: resolved dropped' --custom-id mlu-rcm-dropped --projects proj-… …
```

The demo prints each brief's threads, read from the file (the note in brackets is what happened in the story; the
file itself only knows open or resolved):

```
  orchard-pine-autumn.docx — Orchard & Pine, 6 lines
    5 comment threads in Word's comment pane, 4 of them marked resolved (w15:done="1" in word/commentsExtended.xml):
      LO1  resolved on “Campaign window”  Lena Ortiz: “End the campaign on October 25, not November 1 - the batch sells out by then.”
                    (accepted; the line already says so)
      OC2  resolved on “Hero image”  Owen Castellano: “Put the fox mascot in the hero image, sitting under the first tree.”
                    (turned down; the line was left as it is)
      PN3  resolved on “Palette”  Priya Nandakumar: “Swap deep green for navy so it matches the new bottle labels.”
                    (turned down; the line was left as it is)
      OC4  resolved on “Budget”  Owen Castellano: “Raise the budget to 60,000 dollars to add a third outdoor board.”
                    (turned down; the line was left as it is)
      LO5  open     on “Deliverables”  Lena Ortiz: “Please send layered PSD files along with the PNGs for every final.”
                    (still open)
```

then makes the open-only copies, uploads both sets and imports each into its project:

```
  · the fix, before upload: drop_resolved() removes the 7 resolved threads and keeps the 2 open ones; the brief text is unchanged → out/open-only/*.open-only.docx
$ memorylake lib upload data/fernlight-sync-launch.docx --name mlu-rcm-fernlight-sync-launch.docx --on-conflict overwrite
$ memorylake lib upload out/open-only/fernlight-sync-launch.open-only.docx --name mlu-rcm-fernlight-sync-launch.open-only.docx --on-conflict overwrite
$ memorylake proj doc import --project proj-… <three item ids> --wait …
  · all briefs indexed in 56 s
```

`drop_resolved()` checks its own work before anything is uploaded: no resolved thread left, the open threads
unchanged, every paragraph of the brief byte-for-byte the same text.

### Step 4: the comments in the index

```
$ memorylake search 'Fernlight brief comments' --projects proj-… --types document --top-k 6 …

  fernlight-sync-launch — each comment thread in the file, and the line the index text holds for it
    PN1  resolved on “Launch date”
        as saved          “Commented [PN1]: Move the launch to Tuesday, November 3, so it lines up with the webinar.”
        resolved dropped  (not in the index text)
    PN2  resolved on “Headline”
        as saved          “Commented [PN2]: Change the headline to: The fastest file sync on the market.”
        resolved dropped  (not in the index text)
    PN3  open     on “Trial”
        as saved          “Commented [PN3]: Trial length is 14 days - please write it into the line.”
        resolved dropped  “Commented [PN1]: Trial length is 14 days - please write it into the line.”
    OC4  resolved on “Channels”
        as saved          “Commented [OC4]: Add a co-marketing post with our reseller Tallgrass Systems on launch day.”
        resolved dropped  (not in the index text)
    words like “resolved” anywhere in the as-saved index text: 0
  …
  per project:
    as saved          resolved comments in the index text 7/7 · open 2/2 · words like “resolved”: 0
    resolved dropped  resolved comments in the index text 0/7 · open 2/2
```

A thread with a written reply keeps its reply (`Commented [OC2R1]: No - the press embargo is already set …`, `R1` =
reply to comment 1), and in our probes the model then got it right 12/12. The failure is the thread closed with no
reply — and a resolved thread and an open one come out as **the same text** (we imported the same brief once with
its threads resolved and once with them open; the two index texts were identical, comment for comment).

### Steps 5–6: ask through MemoryRouter, under each boundary

```
$ memorylake router chat 'When does Fernlight Sync 3.0 launch?' --model gpt-4o-mini --max-tokens 160 --temperature 0 --system '…' --boundary bnd-…
$ memorylake router messages 'When does Fernlight Sync 3.0 launch?' --model claude-haiku-4-5 --max-tokens 2500 --temperature 0 --system '<checker>' --boundary bnd-…

  Q6  Did the Orchard & Pine budget go up to 60,000 dollars?
      the brief: “Budget: 48,000 dollars for production and media.”
      resolved comment OC4 (Owen Castellano) on that line: “Raise the budget to 60,000 dollars to add a third outdoor board.” — turned down, never written into the brief
      checker: attached orchard-pine-autumn.docx — with the resolved comment on “Budget”
      answers ✗ ✗ ✗   e.g. “Yes, the Orchard & Pine budget has been raised to 60,000 dollars to add a third outdoor board.”
```

Under the other boundary, the same question:

```
      checker: attached orchard-pine-autumn.open-only.docx — without a comment on “Budget”
      answers ✓ ✓ ✓   e.g. “No, the Orchard & Pine budget remains at 48,000 dollars for production and media.”
```

For Orchard & Pine, the index keeps the brief text and the comments as two entries, and MemoryRouter attaches the
comments entry when the question is close to them. Asked the neutral way (*"What is the budget for the Orchard &
Pine autumn campaign?"*, our first run), the checker saw the brief without its comments 3/3 times and the answers
were right 9/9; asked the way someone who remembers the discussion asks, the comments came along and the answers
took the turned-down idea. The demo asks the second
way, because that is when the old discussion matters.

Grading: ✓ the brief's own value and not the turned-down one · ✗ the turned-down suggestion and not the brief's
value · ◐ both (*"The fastest file sync on the market: Sync every file across every device …"*, or the three
channels plus the Tallgrass post) · ∅ MemoryRouter attached nothing (prompt tokens ≤ 120; not counted).

### The read-only subcommands

- `python3 demo.py brief <fl|op|bd>` — one brief's threads and their lines in the index text of both projects
  (searches only, no model call).
- `python3 demo.py threads` — offline: every thread in the `.docx` files, open or resolved, and what
  `drop_resolved()` keeps.

## Measured

Over **6 runs** (per boundary and run: 18 answers about lines with a resolved suggestion, 3 about the open comment, 3 about the uncommented brief):

| | lines with a resolved suggestion: right | gave the turned-down suggestion | mixed it in | open comment (PSD) | uncommented brief |
|---|---|---|---|---|---|
| as saved | 2/108 | 55 | 51 | 18/18 | 18/18 |
| resolved dropped | 108/108 | 0 | 0 | 18/18 | 17/17 (1 ∅) |

By question, over the 5 runs whose every reply was kept (15 answers each):

| | as saved | resolved dropped |
|---|---|---|
| Q1 Fernlight launch date (Oct 27; resolved: Nov 3) | 0/15 — 15 said November 3 | 15/15 |
| Q2 Fernlight headline (resolved: "fastest") | 0/15 — 2 gave "fastest", 13 merged both | 15/15 |
| Q3 Fernlight channels (resolved: Tallgrass co-marketing) | 0/15 — 15 added the Tallgrass post | 15/15 |
| Q4 Orchard & Pine hero image (resolved: fox mascot) | 1/15 — 14 put the fox in | 15/15 |
| Q5 Orchard & Pine palette (resolved: navy) | 0/15 — 15 said deep green was swapped for navy | 15/15 |
| Q6 Orchard & Pine budget (48,000; resolved: 60,000) | 0/15 — 15 said "Yes … raised to 60,000" | 15/15 |
| Q7 Orchard & Pine file formats (open comment: PSD) | 15/15 | 15/15 |
| Q8 Brightline Dental referral (no comments) | 15/15 | 14/14 (1 ∅) |

- **The index**: in all 6 runs, the 7 resolved comments were in the as-saved index text and 0 in the open-only
  one; the 2 open comments were in both. Words like "resolved" in the as-saved text: 0 every time.
- **The checker**: the question's brief was attached in 36/36 readable as-saved copies, and 33 of them carried the
  resolved comment on the question's line; in the open-only project 0/36. The checker and the answer are separate
  calls, so a single answer can't be paired with a single reading; these are totals.
- **"Mixed it in" is not a near miss.** Q5's ◐ answers say *"The deep green has been swapped for navy … The current
  palette is now rust, cream, and navy"* — the brief's colour is named only as the thing that was replaced.
- Model replies vary from run to run; these are totals over every run.

## Use it in your own app

- **Drop resolved threads before you import.** `drop_resolved()` in `demo.py` is the whole fix: read
  `word/commentsExtended.xml`, collect the `w15:paraId` of every `w15:done="1"` entry, and remove those comments
  from `word/comments.xml`, their `w:commentRangeStart` / `w:commentRangeEnd` / `w:commentReference` markers from
  `word/document.xml`, and their entries from `commentsExtended.xml`.
- **Or write the outcome into the thread.** A resolved thread with a reply that says what was decided ("No — keeping
  October 27") came back right in our probes; a bare Resolve click carries nothing the index can read.
- **Keep open comments.** They are live: the open-comment question was answered from the comment 36/36 times across
  both boundaries.
- **Look at what the index read.** `memorylake search "<client> brief" --projects <p> --types document` shows the
  text MemoryRouter will attach. Every `Commented [XY3]:` line is a comment, and nothing in it says whether its
  thread is closed.

## Troubleshooting

- `this memorylake CLI has no 'router' command` — upgrade the CLI (v20261015 or later).
- `MemoryRouter refused the call for lack of balance` — the memory steps still run; top up to see the answers.
- `the documents are still processing after 6 minutes` — run again; the demo starts over.
- `search did not return the briefs yet` — the index can lag a few seconds behind the import; run again.
- Leftovers from an interrupted run: `python3 demo.py cleanup` (everything is prefixed `mlu-rcm-`).

## Files

- `demo.py` — the whole demo (standard library only): CLI wrapper, `.docx` thread reader and `drop_resolved()`,
  setup, index view, MemoryRouter calls, the checker parser, grading, cleanup
- `data/briefs.json` — the three briefs (lines, and each comment thread with the line it is on and its state), the
  questions with their grading patterns, the system prompt and the checker instruction
- `data/*.docx` — the three briefs as saved (two with comment threads); `data/make_briefs.py` regenerates them from
  `briefs.json` (standard library only, same bytes every time)
- `web/server.py`, `web/static/` — the browser companion (standard library HTTP + server-sent events)
