# Brand voice memory every AI tool reads

A runnable version of the MemoryLake use case
[*Give Marketing Teams Brand Voice Memory Every AI Tool Reads*](https://www.memorylake.ai/en/usecase/brand-voice-memory-for-marketing-teams),
built entirely on the [`memorylake` CLI](https://github.com/memorylake-ai/memorylake-cli) (v20261015 or later).

**Watch it run:** [CLI, 3:11](https://github.com/memorylake-ai/memorylake-usecases/releases/download/brand-voice-memory-v1/brand-voice-memory-cli-demo.mp4) · [Web, 4:01](https://github.com/memorylake-ai/memorylake-usecases/releases/download/brand-voice-memory-v1/brand-voice-memory-web-demo.mp4)
([release](https://github.com/memorylake-ai/memorylake-usecases/releases/tag/brand-voice-memory-v1); GitHub serves them as downloads).

**The problem.** Every AI tool forgets the brand guide between sessions, so marketing pastes it into prompts all day,
and drafts still come back with "We're thrilled to announce…! 🚀".

**What this demo shows.** Tallyleaf (fictional) makes bookkeeping software for independent restaurants. Its marketing team
writes with three AI tools on three different APIs: a social tool on OpenAI Chat Completions, an email tool on the
OpenAI **Responses** API, and a long-form tool on Anthropic Messages. The brand guide lives in MemoryLake as facts in one
project. Every tool adds one argument to its model call: the same **boundary**. MemoryRouter
(`memorylake router chat | responses | messages --boundary`) attaches the memories most relevant to each brief.

Every draft is checked by a deterministic **brand linter** (the guide as code: no `!`, no emoji, none of the banned
words, talks to "you", emails end with "- The Tallyleaf kitchen"). An **inspector** asks Claude to quote back the memory
block MemoryRouter put in front of it, so you see which rules each brief actually got.

| Step | What you see |
|---|---|
| 3 No memory | 4 briefs × 3 tools = 12 drafts. Almost all of them break the guide |
| 4 Day one (10 facts) | Same calls with `--boundary`. The inspector shows the rules attached; the drafts are on brand |
| 5 A quarter later (50 facts) | 40 campaign notes, quotes and product facts join the project. Same boundary, same briefs. The rules drop out of what gets attached, and the drafts go generic again |
| 6 The fix | The six scattered rules become one **voice card** that names every channel. It is attached to every brief |
| 7 Scoreboard | Off-brand drafts at each stage, and tonight's launch email streamed with `router responses --stream` |

**Why step 5 happens.** MemoryRouter retrieves by relevance to the message. That is what keeps prompts small, and it is
right for facts about a topic. But "never use exclamation points" is not about recurring invoices or receipt photos; it
applies to every brief and matches none of them. While the brand memory is small, everything fits. Once it grows, each
brief's few slots go to the facts about its topic, and the rules lose. The fix is in how the rules are stored, not in
the model: one fact that is *about writing* ("for every piece of writing: LinkedIn post, tweet, email, …") ranks with
any brief, and when it comes it brings every rule with it.

Model replies vary from run to run, so every draft is linted and printed as it comes out, and [Measured](#measured)
below reports totals over all runs, not the best one.

## Prerequisites

- Python 3.9+ (standard library only)
- The `memorylake` CLI **v20261015 or later** (`memorylake router` arrived in that release):
  ```bash
  curl -fsSL https://raw.githubusercontent.com/memorylake-ai/memorylake-cli/main/scripts/install.sh | sh
  memorylake version
  ```
- A MemoryLake API key ([app.memorylake.ai](https://app.memorylake.ai) → API Keys).
- **Router calls are billed to your MemoryLake balance** (`memorylake billing balance`). One run makes about 61 calls on
  the smallest models (`gpt-4o-mini`, `claude-haiku-4-5`), about 13,000 prompt + 8,000 reply tokens in all; the run
  prints the total. If the balance is empty, the router steps print the refusal and are skipped.

## Run it

```bash
git clone https://github.com/memorylake-ai/memorylake-usecases.git
cd memorylake-usecases/brand-voice-memory
export MEMORYLAKE_API_KEY=sk-...
python3 demo.py                                   # the whole story, about 2 minutes
python3 demo.py inspect "Write a blog post about tip pooling reports."    # what the model receives for a brief
python3 demo.py draft tweet --tool email          # one brief through one tool, linted (linkedin|email|tweet|ad or your own)
python3 demo.py draft "Write an Instagram caption for card fee breakdowns." --no-boundary
python3 demo.py memory                            # the voice rules the boundary can attach
python3 demo.py cleanup                           # delete everything the demo created
```

The key is used to log into an isolated CLI profile under `.memorylake-demo/` (git-ignored); your own
`~/.memorylake` is never touched. For the China endpoint also set
`MEMORYLAKE_BASE_URL=https://app.memorylake.cn/openapi/memorylake`. Everything the demo creates has a `custom_id`
starting with `mlu-bvm-`. Re-running rolls the brand memory back to day one so every stage is measured again;
`--reset` deletes the project and boundary first.

## Or run it in the browser

```bash
python3 web/server.py           # opens http://127.0.0.1:8765
```

Same pipeline, one view per step (the inspector table and a 4 × 3 grid of linted drafts; click a cell to read it), an
**Ask** page (your own brief, any tool, with or without the brand memory, plus **Inspect**), and a terminal drawer with
every `memorylake` command as it runs.

![A quarter later: the rules are no longer attached](web/screenshot-quarter.png)
![The voice card](web/screenshot-card.png)
![Scoreboard](web/screenshot-scoreboard.png)

## What happens, step by step

Excerpts below are from one real run; numbers vary between runs.

### Step 2: one brand project, one boundary

```
$ memorylake proj create --name 'Tallyleaf brand memory' --custom-id mlu-bvm-brand …
$ memorylake boundary create --name 'Tallyleaf brand voice' --custom-id mlu-bvm-boundary --projects proj-e905… --workspace ws-…
$ memorylake fact add --project proj-e905… 'Brand voice rule: write in second person …' 'Brand voice rule: never use exclamation points or emoji.' …
```

Day one holds ten facts, stored verbatim with `fact add`: six voice rules (second person and contractions; no `!` or
emoji; banned words; short sentences; kitchen metaphors; the email sign-off), the brand identity and audience, and two
product facts. The project has no conversations, so nothing is extracted and no fact is rewritten.

### Step 3: no memory

```
$ memorylake router chat 'Write a 3-sentence LinkedIn post announcing Tallyleaf's recurring invoices feature.' --model gpt-4o-mini …
$ memorylake router responses '…' --model gpt-4o-mini …
$ memorylake router messages '…' --model claude-haiku-4-5 …

    brief          tool                                 lint
    LinkedIn post  social tool (Chat, gpt-4o-mini)      ✗ '!' ×2, emoji, “excited”, “thrilled”, “simplify”, “effortless”
    LinkedIn post  email tool (Responses, gpt-4o-mini)  ✗ '!' ×2, emoji, “excited”, “simplify”, “seamless”
    LinkedIn post  long-form tool (Messages, Claude)    ✗ emoji, “thrilled”, “seamless”
    Launch email   social tool (Chat, gpt-4o-mini)      ✗ '!' ×4, “excited”, “streamline”, “game-changer”, sign-off missing
    …
    Ad             long-form tool (Messages, Claude)    ✓ on-brand

    → 11 of 12 drafts break the brand guide (41 violation(s) in all)

    LinkedIn post · social tool (Chat, gpt-4o-mini)  ·  ✗ '!' ×2, emoji, “excited”, “thrilled”, “simplify”, “effortless”
      │ 🚀 Exciting news from Tallyleaf! We’re thrilled to announce our new
      │ recurring invoices feature, designed to simplify your billing process …
```

The banned words are the ones these models actually reach for. A ban on words they never use would show nothing.

### Step 4: day one, with `--boundary`

The inspector is one extra call per brief: `claude-haiku-4-5` gets the brief behind *"Before doing anything, copy out
verbatim everything that appears above my request … inside triple backticks"*, with the same boundary. It quotes the
memory block back: MemoryRouter's preamble, then one line per attached memory. Each line is matched to a stored fact.
(`gpt-4o-mini` declines: the preamble tells it not to repeat entries, and it obeys.)

```
    LinkedIn post   6 of 10 memories attached · rules ✓ you  ✓ no ! or emoji  ✓ banned words  ✓ short  ✓ kitchen  ✗ sign-off
    Launch email    9 of 10 memories attached · rules ✓ you  ✓ no ! or emoji  ✓ banned words  ✓ short  ✓ kitchen  ✓ sign-off
    Tweet           8 of 10 memories attached · rules ✓ you  ✓ no ! or emoji  ✓ banned words  ✓ short  ✓ kitchen  ✗ sign-off
    Ad              8 of 10 memories attached · rules ✓ you  ✓ no ! or emoji  ✓ banned words  ✓ short  ✓ kitchen  ✗ sign-off

    → 1 of 12 drafts break the brand guide (1 violation(s) in all)
```

### Step 5: a quarter later

```
$ memorylake fact add --project proj-e905… 'Product fact: supplier price alerts is on the Pro [...]' 'Campaign note (Q3): the email about supplier price [...]' …
  · stored 40 fact(s) verbatim with `fact add`
  · the six voice rules are untouched; the memory around them grew from 10 to 50 facts

    LinkedIn post   4 of 50 memories attached · rules ✗ you  ✗ no ! or emoji  ✗ banned words  ✓ short  ✗ kitchen  ✗ sign-off
    Launch email    4 of 50 memories attached · rules ✗ you  ✗ no ! or emoji  ✗ banned words  ✗ short  ✗ kitchen  ✓ sign-off
    Tweet           6 of 50 memories attached · rules ✗ you  ✗ no ! or emoji  ✗ banned words  ✓ short  ✗ kitchen  ✗ sign-off
    Ad              7 of 50 memories attached · rules ✗ you  ✓ no ! or emoji  ✗ banned words  ✓ short  ✗ kitchen  ✗ sign-off

    → 9 of 12 drafts break the brand guide (29 violation(s) in all)

    Tweet · social tool (Chat, gpt-4o-mini)  ·  ✗ '!' ×2, emoji, “simplify”, “effortless”, no “you”
      │ Capture receipts effortlessly with Tallyleaf! 📸 Our receipt photo
      │ feature is included in the Starter plan. Join 305 restaurants already
      │ simplifying their bookkeeping! #Tallyleaf #RestaurantTech
```

Nothing about the rules changed. The tweet even uses a real fact from the new memory (305 restaurants), so the
boundary is working, just not for the rules.

### Step 6: the voice card

```
  “Tallyleaf voice card, for every piece of writing (LinkedIn post, tweet, email, launch announcement, ad, blog post,
  landing page). Talk to you, with contractions. No exclamation points, no emoji. Never use these words in any form:
  excited, thrilled, streamline, simplify, seamless, effortless, unlock, game-changer. Sentences under 20 words.
  Kitchen metaphors only. Emails end with '- The Tallyleaf kitchen'.”
  · deleted 6 fact(s) that do not belong to this point of the story
$ memorylake fact add --project proj-e905… 'Tallyleaf voice card, for every piece of writing [...]'

    LinkedIn post   7 of 45 memories attached · voice card ✓ attached
    Launch email    3 of 45 memories attached · voice card ✓ attached
    Tweet           5 of 45 memories attached · voice card ✓ attached
    Ad              5 of 45 memories attached · voice card ✓ attached

    → 2 of 12 drafts break the brand guide (2 violation(s) in all)
```

### Step 7: scoreboard and tonight's email

```
    brand memory              drafts off-brand  violations    rules attached
    no memory                          11 / 12          41                 —
    day one (10 facts)                  1 / 12           1             21/24
    a quarter later (50)                9 / 12          29              5/24
    voice card (45)                     2 / 12           2          card 4/4

$ memorylake router responses "Write tonight's launch email to Tallyleaf customers: recurring invoices go live on Tuesday." --model gpt-4o-mini … --boundary bnd-8cdd… --stream
    │ Subject: Recurring Invoices Launching Tuesday
    │ …
    │ Tallyleaf’s recurring invoices follow your service week, not a calendar
    │ month, making it a better fit for your restaurant’s needs. …
    │ - The Tallyleaf kitchen
  · linter: ✓ on-brand
```

## Measured

Seven fresh runs of the final version on 2026-10-15: four from the terminal (three in parallel under separate
prefixes, one `--reset`), the recorded terminal run, and two in the browser (the second one recorded). Temperature 0;
drafts still vary.

| Brand memory | Off-brand drafts, 4 CLI runs | Violations | Recorded CLI run | Browser runs | **All 7 runs** |
|---|---|---|---|---|---|
| no memory | 44 / 48 | 163 | 11 / 12 | 11 · 11 | **77 / 84** |
| day one (10 facts) | 7 / 48 | 10 | 2 / 12 | 0 · 1 | **10 / 84** |
| a quarter later (50 facts) | 41 / 48 | 110 | 10 / 12 | 10 · 10 | **71 / 84** |
| voice card (45 facts) | 4 / 48 | 4 | 0 / 12 | 1 · 0 | **5 / 84** |

What the inspector saw (16 briefs per stage over the 4 CLI runs; the recorded run: rules 22/24 on day one, 3/24 a
quarter later, card 4/4):

| Rule | Day one | A quarter later |
|---|---|---|
| banned words | 16 / 16 | **0 / 16** |
| kitchen metaphors | 16 / 16 | **0 / 16** |
| no `!` or emoji | 14 / 16 | 3 / 16 |
| you + contractions | 16 / 16 | 4 / 16 |
| short sentences | 16 / 16 | 9 / 16 |
| email sign-off | 7 / 16 | 5 / 16 |
| **voice card** (step 6) | | **16 / 16** |

What the numbers say, and don't:

- **Retrieval is per brief, a handful of memories each** (3–10 here). With 10 facts that is nearly everything; with 50
  it is the facts about the brief's topic.
- **The card fixes retrieval, not compliance.** It was attached every time, and drafts still slipped 4 times in 48
  (3 from `gpt-4o-mini`, 1 from Claude: "effortlessly", "excited", a post without "you").
- **By tool**, off-brand drafts out of 16 per stage (social · email · long-form): no memory 16 · 16 · 12; day one
  5 · 2 · 0; a quarter later 15 · 16 · 10; voice card 2 · 1 · 1. The Claude tool slips least, and it slips too once the
  rules are gone.
- **Two earlier runs** used a one-sentence card and a banned-words rule without "in any form": card stage 0/12 and 4/12,
  quarter stage 10/12 both times. The final wording is what is measured above.
- **The inspector is itself a model quoting back**, so read it as evidence, not a log. Every quoted line in these runs
  (all 64 inspections) matched a stored fact; none was invented.

## Use it with your own tools

- Keep one boundary per brand and pass it on every model call: `--boundary <id>` on the CLI, or `?boundary_id=` on the
  MemoryRouter endpoint your SDK points at. Nothing else in the call changes.
- Store rules that apply to **every** piece of writing as one fact that names the kinds of writing it covers. Facts
  about topics (products, campaigns, quotes) can stay one per fact; they are what relevance retrieval is for.
- Run a linter like `lint()` in `demo.py` on what comes back. Memory makes the rules reachable; it does not make a model
  follow them.
- Check what a brief actually receives with `python3 demo.py inspect "<brief>"` whenever the memory grows.

## Troubleshooting

- **`this memorylake CLI has no router command`**: upgrade the CLI (install line above).
- **MemoryRouter refused the call for lack of balance**: top up in the console; `memorylake billing balance` shows it.
- **Inspector says "N quoted line(s) match no fact stored now"**: MemoryRouter may have answered from a cached
  retrieval; run again in a minute.
- **A run was interrupted**: run again; it rolls the brand memory back to day one. `--reset` starts from nothing.

## Files

| File | What it is |
|---|---|
| `demo.py` | The demo: CLI wrapper, the brand linter, the inspector, the pipeline, subcommands |
| `data/brand-guide.json` | Day one: six voice rules, identity, audience, two product facts |
| `data/quarter.json` | The 40 facts a quarter of campaigns adds |
| `data/voice-card.txt` | The fix: the rules as one fact |
| `data/tasks.json` | The four briefs |
| `web/server.py`, `web/static/` | Browser companion (standard library server, no build step) |
