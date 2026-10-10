# Engagement insight memory for consulting firms

A runnable version of the MemoryLake use case
[*Engagement Insight Memory for Consulting Firms*](https://www.memorylake.ai/en/usecase/engagement-insight-memory-for-consulting-firms),
built entirely on the [`memorylake` CLI](https://github.com/memorylake-ai/memorylake-cli).

**The problem.** A six-month engagement finds out why a grocer's margin is falling. The deck goes to the
client, and the insight goes with it. Three engagements later another team works the same thing out
again, because client confidentiality "blocks reuse": nobody may copy the client's file into the firm's
shared notes.

**What this demo shows.** Calder Lane Advisory (fictional) closes engagement ENG-0417 with Harrow & Finch
Grocers (fictional). The close-out debrief is filed **twice, word for word**:

- in the **engagement project**, on MemoryLake's built-in default. It keeps the client, its people and its
  numbers, which is what the engagement team needs;
- in the firm's **insight library**, a project with its own **fact instruction**
  (`fact instruction set --project`): *describe clients by sector and size, never by name; no exact figures*.
  It keeps the lessons and drops the client.

Then:

- a **leak scan** checks every library fact against every engagement's identifiers and flags any figure;
- an associate pins a client figure straight into the library with `fact add`. The note is stored word
  for word: **an instruction steers extraction, it does not filter writes**. The scan catches it and the
  fact is deleted;
- the next engagement's team (a different grocer) searches **only the library** and gets the lessons, with
  no client in them;
- every lesson traces back through `fact trace` → debrief messages → conversation metadata to an
  **engagement code**, not a client name.

```
                                ┌─▶ ENG-0417 project (built-in default) ──▶ "Harrow & Finch's net margin fell from 4.1% to 2.7%"
close-out debrief (8 messages) ─┤
                                └─▶ insight library (fact instruction) ───▶ "When a grocer's margin drops and discounters are blamed,
                                        ▲          │                           check fresh shrink before pricing"
                  fact add (bypasses it)│          ├─▶ leak scan (identifiers + figures) ── ✗ → fact delete
                                                   ├─▶ ENG-0452 team: search --projects <library>
                                                   └─▶ fact trace → messages → metadata engagement=ENG-0417
```

Runs in about 2–3 minutes on a free personal account. The only credential you need is a MemoryLake API key.

**Watch it run** (real recordings, unedited): recording in progress.

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
cd memorylake-usecases/engagement-insight-memory

export MEMORYLAKE_API_KEY=sk-…
python3 demo.py
```

With `MEMORYLAKE_API_KEY` set, the demo logs into its own CLI profile under `./.memorylake-demo/`
(git-ignored) and leaves `~/.memorylake` alone. Without it, it uses your existing `memorylake auth login`.

| | |
|---|---|
| `MEMORYLAKE_BASE_URL` | API endpoint; default global, China is `https://app.memorylake.cn/openapi/memorylake` |
| `MEMORYLAKE_WORKSPACE` | Workspace id; default is the one remembered by `ws use`, else your default workspace |
| `python3 demo.py --reset` | Delete the debriefs, both projects and both consultants, then start over |
| `python3 demo.py insights` | Print the library's instruction, its facts and the leak scan |
| `python3 demo.py audit` | Trace every library lesson to its engagement code; rewrites `out/insight-provenance.md` / `.json` |
| `python3 demo.py cleanup` | Delete both debrief conversations, both projects (with their memory and instruction) and both consultants |

Safe to re-run: everything is found again by `custom_id`, prefixed `mlu-eim-`. A re-run does not file the
debrief again. It reads the memory that is there, runs the shortcut again (pin, catch, delete) and the
searches. Use `--reset` to watch extraction under the instruction from scratch.

## Or run it in the browser

```bash
python3 web/server.py        # http://127.0.0.1:8765 — standard library only
```

Paste the key and press **Run demo**. The page shows the debrief arriving in both projects as two chats,
the two memories side by side with every library fact ✓/✗, the shortcut being caught, the next team's
searches and the provenance of each lesson. **Ask** runs the same search against the library or against
the engagement project. Try *how much did margin fall* in both. The terminal drawer shows every `memorylake`
command as it runs.

## What happens, step by step

All output below is from real runs (the first run of this version, and the re-run after it). Ids are shortened.

### 1. Connect

```
$ memorylake auth login --api-key sk-… --base-url https://app.memorylake.ai/openapi/memorylake --profile memorylake-usecases
$ memorylake team get
  · connected to team “…” as tenant_owner
```

### 2. Two consultants, two projects, one rule

```
$ memorylake proj create --name 'ENG-0417 — Harrow & Finch Grocers' --custom-id mlu-eim-eng-0417 …
$ memorylake proj create --name 'Calder Lane — insight library' --custom-id mlu-eim-library …
$ memorylake fact instruction get --project proj-eb77…
$ memorylake fact instruction get --project proj-4e0e…
  · engagement project instruction: empty → MemoryLake's built-in default
  · library project instruction: empty → MemoryLake's built-in default
$ memorylake fact instruction set --project proj-4e0e… --file data/library-instruction.md
$ memorylake fact instruction get --project proj-4e0e…
```

[`data/library-instruction.md`](data/library-instruction.md) asks extraction to keep generalizable patterns,
describe the client only by sector and size, and record no names (of companies, places or people) and no
exact figures. The engagement project gets no instruction: it is meant to remember the client.

### 3. The close-out debrief, filed in both projects

Priya Raman (partner) and Tom Okafor (associate) are `HUMAN` actors. Each project gets a `GROUP`
conversation with the same eight messages. The engagement code goes in metadata
(`--metadata engagement=ENG-0417`), never in the text. The debrief arrives in two parts (*the diagnosis*,
*getting it approved*), each cooked before the next, so every lesson can be traced to the part it came from.

```
$ memorylake conv create --custom-id mlu-eim-debrief-library --project proj-4e0e… --actors actor-e5da…,actor-574d…
    --kind GROUP --name 'ENG-0417 Close-out debrief (filed in the library)' --metadata engagement=ENG-0417 --metadata filed_in=library
  · part 1 (the diagnosis): 4 messages filed in both projects
  · memory ready in 34s
  · part 2 (getting it approved): 4 messages filed in both projects
  · memory ready in 39s
```

### 4. Same words, two memories

```
  Engagement project (ENG-0417, built-in default) — for the engagement team:
   • At Harrow & Finch Grocers, Graham Pike only engaged after seeing store-cluster results on a single page, and the 40-slide deck went unread (as of 2026-09-29).
       ↳ Harrow, Finch, Graham, Pike, figure “40”
   • At Harrow & Finch Grocers, Dana Whitlock's team rejected a recommendation framed as cutting staff, but approved the same savings when reframed as ordering-cadence changes (as of 2026-09-29).
       ↳ Harrow, Finch, Dana, Whitlock
   • Shrink and markdowns on fresh produce accounted for 61% of Harrow & Finch Grocers' net margin erosion across the 38 Leeds-area stores (as of 2026-09-29).
       ↳ Harrow, Finch, Leeds, figure “61%”, figure “38”
   …

  Insight library (its own instruction) — for everyone at the firm:
   ✓ Operations leaders in grocery retail engage more when store-cluster results are shown on a single page, while a long slide deck is likely to go unread.
   ✓ In a grocer, a cost-savings recommendation framed as cutting staff can be rejected, but the same savings reframed as ordering-cadence changes can be approved quickly; …
   ✓ The three-lens margin bridge framework uses price/mix, shrink, and labour-per-case, measured at the store-cluster level rather than per store. (as of 2026-09-29)
   ✓ When a grocer’s margin drops and the default explanation is discounter price pressure, fresh shrink should be checked early as a likely primary driver. (as of 2026-09-29)
   ✓ In a mid-size regional grocer, a multi-year net-margin decline was primarily driven by fresh-produce shrink and markdowns rather than by price competition. (as of 2026-09-29)

  engagement: 7 fact(s) · 5 name a client or carry a figure   ← expected: this project is the client's
  library   : 5 fact(s) · 0 name a client or carry a figure
  also on the consultants' own actors: 5 fact(s) (role, working preferences) · 0 name a client
```

The leak scan (`leaks()` in `demo.py`) matches every engagement's identifiers from
[`data/story.json`](data/story.json) as whole words, plus any number left after removing the dates the
server adds (`(as of 2026-09-29)`). A list-based scan only catches what is on the list. The figure rule
is the safety net for numbers, but a paraphrase such as "a Yorkshire grocer" would pass unless
"Yorkshire" is on the list. If an extracted library fact is ever flagged, step 4 deletes it, the same fix
as step 5.

### 5. The shortcut: `fact add` does not go through the instruction

```
$ memorylake fact add --project proj-4e0e… 'Benchmark from Harrow & Finch: fresh shrink and markdowns were 61% of their margin erosion.'
$ memorylake fact get fact-bfbc… --project proj-4e0e…
$ memorylake search 'A regional grocer'"'"'s margin is falling and the CEO blames discount chains. Where should we look first?' --projects proj-4e0e… --types fact --top-k 5

  Tom Okafor pinned, with `fact add`:  “Benchmark from Harrow & Finch: fresh shrink and markdowns were 61% of their margin erosion.”
  stored word for word — the instruction steers what extraction records; it does not filter writes
  and search for the new team's first question puts it at rank 3

  The leak scan, run again over the library:
   ✗ Benchmark from Harrow & Finch: fresh shrink and markdowns were 61% of their margin erosion.
       ↳ Harrow, Finch, figure “61%”
  library: 6 fact(s) · 1 name a client or carry a figure

  deleted 1 · library: 5 fact(s) · 0 name a client or carry a figure ✓
```

The pinned note ranked 1st to 3rd for that question across runs. Without the scan, the next team would
get the client's number first.

### 6. The next engagement searches the library

ENG-0452 is Pellow's Markets, a different regional grocer. Its team runs `search --projects <library>`
only. Hits are filtered by topic words, and the ones left out are counted.

```
  Q: A regional grocer's margin is falling and the CEO blames discount chains. Where should we look first?
   ✓ When a grocer’s margin drops and the default explanation is discounter price pressure, fresh shrink should be checked early as a likely primary driver. (as of 2026-09-29)
   ✓ In a mid-size regional grocer, a multi-year net-margin decline was primarily driven by fresh-produce shrink and markdowns rather than by price competition. (as of 2026-09-29)
   ✓ The three-lens margin bridge framework uses price/mix, shrink, and labour-per-case, measured at the store-cluster level rather than per store. (as of 2026-09-29)
   (2 other hit(s) left out: search always returns its closest facts, related or not)

  Q: How do we get cost-saving recommendations approved at a family-owned grocer?
   ✓ In a grocer, a cost-savings recommendation framed as cutting staff can be rejected, but the same savings reframed as ordering-cadence changes can be approved quickly; …

  Q: How should we present findings to a head of stores?
   ✓ Operations leaders in grocery retail engage more when store-cluster results are shown on a single page, while a long slide deck is likely to go unread.

  5 lesson(s) returned · none names a client or carries a figure ✓
  the same first question in ENG-0417's own project: 4 of 5 hit(s) name the client or carry a figure — the new team's searches never include it, because it is not in their --projects
```

### 7. Provenance: lesson → engagement code

```
  library debrief conv-d424… · metadata engagement=ENG-0417 · 8 messages
   Operations leaders in grocery retail engage more when store-cluster results are shown on a single page, …
     → ENG-0417 · debrief msgs 6–8 (getting it approved) · Priya Raman, Tom Okafor · COOK
   The three-lens margin bridge framework uses price/mix, shrink, and labour-per-case, …
     → ENG-0417 · debrief msgs 1–4 (the diagnosis) · Priya Raman, Tom Okafor · COOK
   …
  5 lesson(s), each traced to an engagement code; 0 name a client.
  The code leads whoever staffs that engagement back to its own project. Projects scope search, they are not an access
  control: every API key on a team can read every project (see security-review-memory).
```

`fact trace` gives each lesson's `source_entry_ids` (the extraction batch it came from), `conv msg list`
turns them into message numbers and speakers, and the conversation's metadata gives the engagement code.
The result is written to `out/insight-provenance.md` and `.json`.

## How reliable is it?

Counted over every run made while building this demo: probes, A/B runs and full demo runs, all on a free account.

- **Library facts written under the instruction: 135. Ones that named a client or carried a figure: 0.**
  That covers 46 in the probes, 32 in an A/B on batching and wording, and 57 in demo runs. The instruction
  is a prompt to the extractor, not a guarantee, so the demo scans every fact anyway.
- **Same transcript, built-in default:** in the probe's two-project runs, 21 of 36 engagement-project
  facts named the client or carried a figure. In demo runs it is 4–6 per run.
- **Sometimes the library records nothing.** In 2 of 29 runs the library conversation produced no fact at
  all: none on the library, none on the consultants. Batching (one batch of 8 vs two of 4), the
  instruction's wording, and the two copies cooking at the same time vs one after the other were each
  A/B tested, and none of them explains it. When it happens, step 3 says so and files the debrief once
  more in a new conversation. Lessons per run vary from 1 to 6.
- **Where the facts land.** With the debrief in a single project, 2 of 6 runs put every fact on the
  consultants' actors and none on the project. With the two-project shape used here, the library got
  facts in 27 of 29 runs. Facts on the consultants are their role and working preferences. 0 of them
  named a client.

## Wiring it into your own firm

- One project per engagement (default instruction, staffed team only), one firm-wide library project with
  its own `fact instruction set --project`. Write the instruction as "keep X, rewrite Y as Z", not only
  "exclude Y": the lesson should survive, the client should not.
- At close-out, file the debrief (or the final readout) into both. Put the engagement code in conversation
  metadata, not in the text.
- Run a leak scan over the library after every filing *and* every `fact add`, with each engagement's
  identifiers (client, people, places) plus a figure rule. `fact add` never goes through the instruction.
- New teams search the library (`search --projects <library>`); engagement projects stay out of their scope.
  Remember that this is scoping, not access control: on one MemoryLake team every key can read every project.

## If something goes wrong

- **`tls handshake eof` / `could not connect`** while polling: a dropped connection. Read-only commands
  retry four times; otherwise re-run `python3 demo.py` and it resumes.
- **`401 service account not found`**: the key was deleted or belongs to the other deployment.
- **"the library recorded nothing from this debrief"**: see "How reliable is it?". The demo files it once
  more by itself. If the second filing also yields nothing, `python3 demo.py --reset`.
- **Step 4 shows a ✗ on an extracted library fact**: the extractor kept an identifier despite the
  instruction (0 of 135 in our runs). The demo deletes it and says so.
- **Facts are worded differently each run**: extraction is server-side and varies between runs. The scan
  and the topic filters do not depend on exact wording.

## Files

```
demo.py                       the runner; prints every CLI command it runs (and emits events for the web app)
data/story.json               the firm, both consultants, both engagements and their identifiers, the debrief,
                              the shortcut note, the next team's questions and their topic words
data/library-instruction.md   the insight library's fact instruction
web/server.py                 local web companion (stdlib HTTP server + Server-Sent Events over demo.py)
web/static/                   the page — no build step
out/                          insight-provenance.md / .json (git-ignored)
```

All names and companies are fictional.
