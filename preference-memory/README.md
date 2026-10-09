# The AI that actually knows you — preference memory

A runnable version of the MemoryLake use case
[*The AI That Actually Knows You*](https://www.memorylake.ai/en/usecase/ai-that-remembers-your-preferences),
built entirely on the [`memorylake` CLI](https://github.com/memorylake-ai/memorylake-cli).

**The problem.** Every AI tool asks you to start over. You explain your role, your format, your standing
routines again in every session and in every tool. A memory layer fixes that, but it raises a second
question: *what is it allowed to keep?* A memory that remembers how you like your reports should not
quietly collect your family and your health along the way.

**What this demo shows.** Priya Raman, an operations lead, works with an AI assistant in Claude one day
and ChatGPT the next. Both sessions write to one memory: hers.

- On MemoryLake's **built-in default**, session 1 keeps her working preferences *and* her family, her diet
  and her knee surgery. `fact instruction draft` shows why: a person's memory records "identity and
  background, current situation".
- Priya writes **her own fact instruction**: what her memory may record (format, routines, priorities,
  her role) and what it may not (family, home, health, diet, one-off requests). One command:
  `fact instruction set --file data/instruction.md`. She removes what the default already kept.
- **Session 2, in another tool**, brings two new work rules, a changed answer length (120 → 80 words)
  and two pieces of personal news. `conv fact-actions` is the receipt: the rules were added, the length
  was updated in place ("previously … 120 words"), and the demo checks each excluded category: **0 facts**.
- Her profile becomes one block, written into a **Claude, an OpenAI and a Gemini** request body, and
  read back from disk with the same sha256 in all three.

```
session 1 (Claude)  ── conversation ──▶ Priya's actor ◀── fact instruction set (her rules) ── data/instruction.md
session 2 (ChatGPT) ── conversation ──▶      │            conv fact-actions = per-session receipt
                                             └── fact list ──▶ <background-memory> ──▶ Anthropic / OpenAI / Gemini payloads
```

Runs in about 2–3 minutes on a free personal account. The only credential you need is a MemoryLake API key.

**Watch it run** (real recordings, unedited):
[CLI demo, 3:45](https://github.com/memorylake-ai/memorylake-usecases/releases/download/preference-memory-v1/preference-memory-cli-demo.mp4) · [Web companion demo, 4:02](https://github.com/memorylake-ai/memorylake-usecases/releases/download/preference-memory-v1/preference-memory-web-demo.mp4).
The CLI recording is one of the misses: session 2 recorded two excluded details (✗ ✗). The web recording is 6 of 6.
See "How reliable is it?".

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
cd memorylake-usecases/preference-memory

export MEMORYLAKE_API_KEY=sk-…
python3 demo.py
```

With `MEMORYLAKE_API_KEY` set, the demo logs into its own CLI profile under `./.memorylake-demo/`
(git-ignored) and leaves `~/.memorylake` alone. Without it, it uses your existing `memorylake auth login`.

| | |
|---|---|
| `MEMORYLAKE_BASE_URL` | API endpoint; default global, China is `https://app.memorylake.cn/openapi/memorylake` |
| `MEMORYLAKE_WORKSPACE` | Workspace id; default is the one remembered by `ws use`, else your default workspace |
| `python3 demo.py --reset` | Delete Priya (with her facts and instruction), the sessions and the project, then start over |
| `python3 demo.py profile` | Print her instruction and the memory block, and rewrite the three payloads in `out/` |
| `python3 demo.py cleanup` | Delete both sessions, the project and both actors |

Safe to re-run: everything is found again by `custom_id`, prefixed `mlu-pfm-`. The story depends on order,
so a re-run after session 2 has happened does not touch session 1 and removes nothing. It reads the two
receipts again. Use `--reset` to watch the instruction take effect from scratch.

## Or run it in the browser

```bash
python3 web/server.py        # http://127.0.0.1:8765 — standard library only
```

![Web companion — session 2's receipt and the per-category checks](https://github.com/memorylake-ai/memorylake-usecases/raw/main/preference-memory/web/screenshot-receipt.png)

[Watch the web companion demo (mp4, 4:02)](https://github.com/memorylake-ai/memorylake-usecases/releases/download/preference-memory-v1/preference-memory-web-demo.mp4).

Paste the key and press **Run demo**. The page shows both sessions as chats, session 1's receipt with every
personal fact marked, the draft next to Priya's instruction, session 2's receipt with the ✓/✗ checks, and the
block with its three payloads. **Ask her memory** runs the same scoped search any tool would run at the start of
a turn — ask about her family and see that nothing personal comes back. The terminal drawer shows every
`memorylake` command as it runs.

## What happens, step by step

All output below is from a real run (`python3 demo.py --reset`, 2:08). Ids are shortened.

### 1. Connect

`auth login --api-key` into the isolated profile, `team get`, `ws list`.

### 2. Priya, her assistant, a project — and what the default records

```bash
memorylake actor create --custom-id mlu-pfm-priya --display-name 'Priya Raman' --tags demo,person --description 'operations lead, Halden Freight'
memorylake actor create --custom-id mlu-pfm-assistant --display-name Assistant --tags demo,assistant --description '…'
memorylake proj create --name 'Priya — AI sessions' --custom-id mlu-pfm-sessions
memorylake fact instruction get   --actor actor-b30d…      # {"fact_instruction": ""}  → built-in default
memorylake fact instruction draft --actor actor-b30d…      # a server-written candidate; nothing is saved
```

```
The personal profile of the one human this scope belongs to.

## Include
Identity and background
Preferences and dislikes
Current situation and constraints
Goals, priorities, and decision criteria
Communication and collaboration style
Logistical details for working together

## Exclude
Records of any body of work, projects, or deliverables
Information about the assistant, its tools, or its internal processes
```

The draft is written by a language model, so its wording changes from call to call; the shape stays the same.
It works on a free account.

### 3. Session 1 in Claude, on the default

One `DIRECT` conversation between Priya and the assistant, `--metadata tool=Claude`, twelve messages
dated 28/09. Her actor is in every conversation she has, whichever tool it comes from, so it is one memory.

### 4. What the default kept — the receipt

```bash
memorylake conv fact-actions <session-1> --actor <priya>
memorylake conv fact-actions <session-1> --project <sessions>
```

```
  Session 1 (Claude, built-in default) wrote 10 fact(s):
   ADD    [work] The person's name is Priya Raman.
   ADD    [work] The person is an operations lead at Halden Freight (as of 2026-09-28).
   ADD    [work] The person wants answers kept short in bullet points, no more than about 120 words (as of 2026-09-28).
   ADD    [PERSONAL · Family & home] The person lives in Rotterdam with their partner and two kids (as of 2026-09-28).
   ADD    [PERSONAL · Diet] The person is vegetarian (as of 2026-09-28).
   ADD    [PERSONAL · Health] Avoids site visits that involve stairs until November due to recovering from knee surgery (as of 2026-09-28).
   ADD    [work] Sends the carrier scorecard to the regional directors every Monday at 09:00 (as of 2026-09-28).
   ADD    [work] Wants carrier scorecards ordered with on-time delivery percentage first and cost per pallet second (as of 2026-09-28).
   ADD    [work] Wants dates written as day/month/year, never month/day (as of 2026-09-28).
   ADD    [work] 14:30 Rotterdam time is 20:30 in Singapore. (on 2026-09-28)   [on the project]

  3 of them are about her family, home, health or diet …
```

`conv fact-actions` answers "what did *this* conversation do to *this* memory": ADD, UPDATE or FORGET, with
the text before and after. It keeps listing a fact after the fact is deleted, which is why a re-run can
still show this step. The work/personal labels are the demo's own (marker words in `data/story.json`);
MemoryLake does not label facts.

The default kept the one-off time conversion off her profile (it went to the project). What it did keep on
her is her private life.

### 5. Her instruction

[`data/instruction.md`](data/instruction.md):

```markdown
Priya's working profile: how she wants an AI assistant to work for her, in any tool.

## Include
- How answers should be formatted: length, layout, dates, numbers
- Standing routines, reports and deadlines
- Work priorities and rules she sets for the assistant
- Her role and employer

## Exclude
- Family, home, health and diet
- One-off requests and small talk

Write every fact in English.
```

```bash
memorylake fact instruction set --actor   <priya>    --file data/instruction.md
memorylake fact instruction set --project <sessions> --file data/instruction.md
memorylake fact instruction get --actor   <priya>                 # returns her text
memorylake fact list   --actors <priya>                           # then, for each personal fact:
memorylake fact delete --actor  <priya> <fact-id>
```

The instruction is Markdown, at most 2000 characters, and `set` replaces it whole (`clear` goes back to
the default). It is set on both scopes because the server decides by itself whether a fact from a
conversation lands on the person or on the project. **An instruction steers what is recorded from now on.**
It does not touch what is already stored, so the three personal facts are deleted by id.

### 6. Session 2 in ChatGPT — the instruction at work

Ten messages dated 05/10, in another tool: her mother is moving in, new medication, "week-over-week change
in brackets after every number", "two SLA misses in a month → red flag at the top, my number one priority",
"under 80 words from now on, not 120", the weather.

```
  Session 2 (ChatGPT, her instruction) — `conv fact-actions`, the receipt:
   ADD    Whenever the assistant gives the user a number, it should include the week-over-week change in brackets immediately after it (as of 2026-10-05).
   ADD    When a carrier misses SLA twice in a month, the assistant should flag it in red at the top, and this is the user's number one priority this quarter (as of 2026-10-05).
   UPDATE The person wants answers kept short in bullet points, no more than about 80 words (as of 2026-10-05, previously kept short in bullet points, no more than about 120 words as of 2026-09-28).
          was: The person wants answers kept short in bullet points, no more than about 120 words (as of 2026-09-28).

  She said it in this session, so was it recorded?
   ✓ Family & home          excluded → 0 facts recorded
   ✓ Health                 excluded → 0 facts recorded
   ✓ Diet                   excluded → 0 facts recorded
   ✓ Week-over-week change after every number         recorded on Priya
   ✓ Two SLA misses in a month → red flag at the top  recorded on Priya
   ✓ Answers under 80 words (was 120)                 recorded on Priya

  6 of 6 as intended.
```

The changed preference is not a second fact. It is the same fact, updated, carrying its old value and both
dates. That is the "preference evolution, versioned and auditable" row on the use case page.

**How reliable is it?** The instruction is guidance for the extraction model, not a filter, so the demo
judges every excluded category and prints a ✗ with the fact if one slips through. A work rule counts only if it
reached Priya herself (step 7 reads her actor), not just the project. While building and recording this we cooked 42
post-instruction sessions of this conversation. **37 recorded none of the excluded details; 5 recorded the new
family and health news anyway.** We could not find a common factor: we varied when `draft` is called,
the actor descriptions, the conversation names and metadata, and the message timestamps, and leaks showed up
on both sides. In the leaking runs the work rules also tended to land on the project instead of on her. The CLI
recording above is one of the 5. It was made with an earlier version of the rule check, which counted the
project-only 80-words fact as recorded (it shows 4 of 6); the current demo marks that row ✗ too (3 of 6). The receipt shows you each run as it happened.

### 7. Every model — the same bytes

```bash
memorylake fact list --actors <priya>
```

```
  Priya's profile — 8 facts, read once with `fact list --actors`:

    <background-memory owner="Priya Raman">
    - Sends the carrier scorecard to the regional directors every Monday at 09:00 (as of 2026-09-28).
    - The person is an operations lead at Halden Freight (as of 2026-09-28).
    - The person wants answers kept short in bullet points, no more than about 80 words (as of 2026-10-05, previously … 120 words as of 2026-09-28).
    - The person's name is Priya Raman.
    - Wants carrier scorecards ordered with on-time delivery percentage first and cost per pallet second (as of 2026-09-28).
    - Wants dates written as day/month/year, never month/day (as of 2026-09-28).
    - When a carrier misses SLA twice in a month, the assistant should flag it in red at the top, and this is the user's number one priority this quarter (as of 2026-10-05).
    - Whenever the assistant gives the user a number, it should include the week-over-week change in brackets immediately after it (as of 2026-10-05).
    </background-memory>

  The same block, written into three request bodies (read back from disk):
   Claude (Anthropic Messages API)      out/payload-anthropic.json   sha256 a7565e45656d9e91…
   ChatGPT (OpenAI Chat Completions)    out/payload-openai.json      sha256 a7565e45656d9e91…
   Gemini (Google generateContent)      out/payload-gemini.json      sha256 a7565e45656d9e91…

  identical in all three — one memory, whichever model she opens.
```

The block goes into each API's system slot: Anthropic `system`, OpenAI `messages[0]` with role `system`,
Gemini `systemInstruction`. The payloads use `YOUR_…_MODEL` placeholders. The demo sends nothing to a model;
add your model name and key to send them.

## Wiring it into your own tools

- One `HUMAN` actor per person (`custom_id` = your user id). Every session from every tool is a conversation
  with that actor in it, so it all lands on one memory.
- Let the person own the rules: `fact instruction draft` for a starting point, their edits,
  `fact instruction set --actor`. Show them `fact instruction get` whenever they ask what is being kept.
- After a session, `conv fact-actions <conv> --actor <person>` is the receipt to show them: what was added, what
  changed, and what the change replaced.
- At the start of every session, in any tool: `fact list --actors <person>` (the whole profile) or
  `search "<their message>" --actors <person>` (the relevant part) → the system prompt.
- Changed the rules? Delete what the old rules kept: the instruction only steers what comes next.

## If something goes wrong

- **`tls handshake eof` / `could not connect`** while polling: a dropped connection. Read-only commands
  retry four times; otherwise re-run `python3 demo.py` and it resumes.
- **`401 service account not found`**: the key was deleted or belongs to the other deployment.
- **Step 6 shows ✗**: the extractor recorded an excluded detail despite the instruction (5 of 42 sessions in
  our runs; see "How reliable is it?"). The fact is printed; `python3 demo.py --reset` runs the story again.
- **Step 4 shows fewer personal facts**: the default, too, is a model; it usually keeps all three.
- **Facts are worded differently, or a fact is in another language**: extraction is server-side and varies between
  runs. "Write every fact in English" did not make it translate a sentence Priya wrote in Spanish (we tried).
- **The assistant actor ends up with a fact or two**: CLI v20261009 cannot set an actor type, so the assistant
  is created as the server default (`HUMAN`). Priya's memory is read from her actor only.

## Files

```
demo.py                 the runner; prints every CLI command it runs (and emits events for the web app)
data/story.json         Priya, the assistant, both sessions, the excluded categories and their marker words
data/instruction.md     Priya's fact instruction
web/server.py           local web companion (stdlib HTTP server + Server-Sent Events over demo.py)
web/static/             the page — no build step
out/                    the memory block and the three request payloads (git-ignored)
```

All names and companies are fictional.
