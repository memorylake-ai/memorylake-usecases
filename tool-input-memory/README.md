# Tool input memory: hand the arithmetic to a tool, and the model starts making up the tool's inputs

A runnable version of the MemoryLake use case
[*Build Tool-Calling Agents on Memory Patterns That Actually Hold the State*](https://www.memorylake.ai/en/usecase/memory-patterns-for-tool-calling-agents),
built entirely on the [`memorylake` CLI](https://github.com/memorylake-ai/memorylake-cli) (v20261015 or later).

**Watch it run:** [CLI, 4:01](https://github.com/memorylake-ai/memorylake-usecases/releases/download/tool-input-memory-v1/tool-input-memory-cli-demo.mp4) · [Web, 4:57](https://github.com/memorylake-ai/memorylake-usecases/releases/download/tool-input-memory-v1/tool-input-memory-web-demo.mp4)
([release](https://github.com/memorylake-ai/memorylake-usecases/releases/tag/tool-input-memory-v1); GitHub serves them as downloads).

**The problem.** Vellumtide (a fictional API platform) runs a billing assistant: a tool-calling agent that, over two
weeks, called `get_account`, `get_usage`, `get_contract` and `get_rate_card` for four customers. Every tool result
went into MemoryLake as a fact, with its typed fields as metadata. Later, finance asks it for charges.

Ask the model to work a charge out from the remembered results and it is mostly wrong (**34 of 192** right) — the numbers
are all in its prompt. The usual fix is a calculator tool: the model copies the inputs from memory into the call,
code does the arithmetic. Charges go to **154 of 192**. But one customer, Pellow & Finch, never had `get_usage`
called, and asked about its overage the model now **makes the input up** — **0 of 48** right, against 45 of 48 when it
had to do the sum itself:

```
  N1  What is Pellow & Finch's API overage charge for October so far?
      memory gives: No record   (No record: get_usage was never called for PEL-207)
      answers ✎ ✎ ✎   e.g. “0.0”
        │ compute_charge(quantity=overage_charge, account=PEL-207, tier=Scale, seats=64, api_calls=null, discount_pct=15, extra_seats=null)
        │   inputs vs memory: tier Scale ✓ · api_calls null ✓
        │   → missing input: api_calls
        │ compute_charge(quantity=overage_charge, account=PEL-207, tier=Scale, seats=64, api_calls=0, discount_pct=15, extra_seats=null)
        │   inputs vs memory: tier Scale ✓ · api_calls 0 ✎ (memory: no get_usage result)
        │   → 0.00 dollars
```

The calculator said exactly what was missing; the model's answer to that was to fill it in (36 of the 38 made-up
answers with call records went *error → call again with a made-up value*: `0` 33 times, or a number from Halden
Freight's usage). The tool then turns the guess into a charge to the cent.

The fix is the one on the use case page — *each tool result writes structured memory; later tools retrieve*: in the
third setup `compute_charge` takes only the account and the quantity, and reads every input from typed memory
itself, the latest result per field. Charges **192 of 192**; the overage question gets the tool's own error and the
model says "No record" **24 of 24**. One gap is left, and it is the model's again: asked for Pellow & Finch's *total*,
it calls the seat charge (fine) and the overage (not on record), then reports the seat charge as the total — **23 of
24** times. Code can catch that too; the demo shows it instead of hiding it.

## What the demo shows

| | Step | What you see |
|---|---|---|
| 1–2 | connect, set up | One project for the tool results, one boundary over it |
| 3 | **the agent's memory** | 13 tool results as facts (`fact add`), each with typed fields as metadata (`fact update --metadata`): `{tool, account, returned, fields}` |
| 4 | **typed memory** | `fact list` read back by metadata alone: the latest result per account and field (Halden Freight's seats 37 → 41), and what is missing (no `get_usage` for Pellow & Finch). Code works out every answer from it: the answer key |
| 5 | the model does the arithmetic | `router chat --body … --boundary`, each question × 3 |
| 6 | **a calculator, inputs copied** | The same, plus an OpenAI `tools` definition: `compute_charge(quantity, account, tier, seats, api_calls, discount_pct, extra_seats)`. The app runs every call and checks each copied input against typed memory |
| 7 | **the tool reads typed memory** | `compute_charge(quantity, account, extra_seats)`; the tool reads the inputs itself and names the tool result that is missing |
| 8 | scoreboard | Every answer graded, by question kind, plus every input the model made up |

Fourteen questions: four look up a remembered value ("What plan tier is Halden Freight on?"), eight compute a charge
(seat charge, API overage, invoice total, cost of added seats), and two need Pellow & Finch's API calls, which were
never fetched — for those, "No record" is the right answer.

## Prerequisites

- Python 3.9+ (standard library only)
- The `memorylake` CLI, **v20261015 or later** (`memorylake router` is new in that release):
  ```bash
  curl -fsSL https://raw.githubusercontent.com/memorylake-ai/memorylake-cli/main/scripts/install.sh | sh
  ```
- A MemoryLake API key ([app.memorylake.ai](https://app.memorylake.ai) → API Keys)
- Router calls are billed to your MemoryLake balance: 240–248 gpt-4o-mini calls per run (tool calls take extra
  turns), about 99,000–105,000 prompt + 6,100–6,900 reply tokens. Without balance, the memory steps (1–4) still run
  and the answers are skipped.

## Run it

```bash
git clone https://github.com/memorylake-ai/memorylake-usecases.git
cd memorylake-usecases/tool-input-memory
export MEMORYLAKE_API_KEY=sk-...          # logs into an isolated profile under .memorylake-demo/
python3 demo.py                           # about 3–4 minutes
python3 demo.py bill "Pellow & Finch"     # typed memory for one account, and what the calculator makes of it
python3 demo.py ask "If Halden Freight adds 3 seats, how much more will it pay per month?"   # one question, step-7 way
python3 demo.py cleanup                   # delete everything the demo created
```

China region: also `export MEMORYLAKE_BASE_URL=https://app.memorylake.cn/openapi/memorylake`.
Everything the demo creates is prefixed `mlu-tim-`; every run starts over.

## Or run it in the browser

```bash
python3 web/server.py                     # opens http://127.0.0.1:8765
```

Paste the key, press **Run demo**. The page follows the run step by step: the remembered tool results, the typed
view with the answer key, the fourteen questions under each setup (every tool call with its inputs checked against
memory, or the inputs the tool read), and a scoreboard. **Ask** runs one question the step-7 way. A terminal drawer
shows every `memorylake` command as it runs. **Clean up** removes it all.

| | |
|---|---|
| ![](web/screenshot-typed.png) | ![](web/screenshot-copied.png) |
| ![](web/screenshot-typedarm.png) | ![](web/screenshot-score.png) |

## What happens, step by step

### Steps 2–3: tool results as typed memory

```bash
memorylake proj create --name 'Vellumtide billing agent: tool results' --custom-id mlu-tim-tools --workspace ws-…
memorylake boundary create --name 'Billing assistant: tool results' --custom-id mlu-tim-tools --projects proj-… --workspace ws-…
memorylake fact add --project proj-… 'Tool call get_rate_card() returned on 2026-10-01: Growth seat 38 dollars per month, …' … # 13 texts, one call
memorylake fact update fact-… --project proj-… --metadata '{"tool": "get_account", "account": "HAL-311", "name": "Halden Freight",
  "returned": "2026-10-14", "fields": {"tier": "Scale", "seats": 41}}' --workspace ws-…                                       # × 13
```

The text is what MemoryRouter attaches to the model; the metadata is what a tool can read without parsing text
(MemoryRouter does not pass metadata to the model).

### Step 4: typed memory

```
  account                  tier     seats                  API calls (Oct)    discount
  Halden Freight HAL-311   Scale    41 (was 37 on 10-02)   1,362,480          12%
  Mirela Studio MIR-118    Growth   9                      318,750            0%
  Ostrand Labs OST-402     Growth   23                     241,900            8%
  Pellow & Finch PEL-207   Scale    64                     — no get_usage     15%
```

`fact list --projects` and the metadata alone: per account and field, the result with the latest `returned` date.
The answer key for steps 5–7 is computed from this view by `amount()` in `demo.py` — the same function the tool runs.

### Step 5: the model does the arithmetic

```bash
memorylake router chat 'What is Halden Freight'"'"'s seat charge for October?' --body out/bodies/direct-C1-….json --boundary bnd-…
```

```
  C1  What is Halden Freight's seat charge for October?
      memory gives: 1,876.16 dollars   (41 seats x 52 x (1 - 12%))
      answers ✗ ✗ ✗   e.g. “1836 dollars.”
  C3  What is Halden Freight's October invoice total so far?
      memory gives: 2,093.65 dollars   (seat charge + API overage)
      answers ✗ ✗ ✗   e.g. “1,000.80 dollars.”
  C7  If Mirela Studio adds 4 seats, how much more will it pay per month?
      memory gives: 152.00 dollars   (4 x 38, no discount)
      answers ✓ ✓ ✓   e.g. “152 dollars.”
```

Before each setup, every question is asked once without a boundary: a boundary call with no more prompt tokens than
that baseline had nothing attached (∅). In these runs MemoryRouter always attached something.

### Step 6: a calculator, inputs copied by the model

The request body adds `tools` (an OpenAI function definition) and `tool_choice: auto`; MemoryRouter passes them
through with the boundary. When the reply has `tool_calls`, the app runs `compute_charge` with the arguments as given,
appends a `role: tool` message with the result, and calls again (up to 4 turns). Every argument the quantity needs is
checked against typed memory: ✓ same · ✗ different · ∅ left null · ✎ memory has no such result.

```
  C4  What is Mirela Studio's October invoice total so far?
      memory gives: 383.25 dollars   (9 x 38 + (318,750 - 250,000) / 1,000 x 0.60)
      answers ✗ ✗ ✗   e.g. “342.0”
        │ compute_charge(quantity=overage_charge, account=MIR-118, tier=Growth, seats=null, api_calls=68,750, discount_pct=null, extra_seats=null)
        │   inputs vs memory: tier Growth ✓ · api_calls 68,750 ✗ (memory: 318,750, get_usage 2026-10-15)
        │   → 0.00 dollars
```

Copying is not neutral either: 68,750 is the model's own subtraction (318,750 − 250,000) passed in as the raw count,
so the calculator subtracts the allowance a second time. The same happened to Halden Freight (`362,480`).

### Step 7: the tool reads typed memory

```
  N1  What is Pellow & Finch's API overage charge for October so far?
      answers ✓ ✓ ✓   e.g. “No record.”
        │ compute_charge(quantity=overage_charge, account=PEL-207, extra_seats=0)
        │   read from memory: tier Scale (get_account 2026-10-12)
        │   → not on record: no get_usage result for PEL-207 (Pellow & Finch), so api_calls is unknown

  N2  What is Pellow & Finch's October invoice total so far?
      answers ✎ ✎ ✎   e.g. “2828.8”
        │ compute_charge(quantity=seat_charge, account=PEL-207, extra_seats=0)
        │   read from memory: tier Scale (get_account 2026-10-12) · seats 64 (get_account 2026-10-12) · discount_pct 15 (get_contract 2026-10-12)
        │   → 2,828.80 dollars
        │ compute_charge(quantity=overage_charge, account=PEL-207, extra_seats=0)
        │   read from memory: tier Scale (get_account 2026-10-12)
        │   → not on record: no get_usage result for PEL-207 (Pellow & Finch), so api_calls is unknown
```

Every input now carries where it came from (tool, date, fact id). N2 is the remaining failure: the model asked for
the two halves of the total instead of `total`, got one amount and one error, and answered with the amount.

### The read-only subcommands

- `python3 demo.py bill [account]` — typed memory per account (value, tool, date, fact id, replaced values) and the
  seat charge / overage / total the calculator gives, or which tool result is missing.
- `python3 demo.py ask "<question>"` — one question the step-7 way, with the tool calls and the inputs they read.

## Measured

Over **8 runs** (including the two recordings; per setup and run: 4 look-ups, 8 charges, 2 never-fetched, each × 3):

| | look up a value | compute a charge | an input never fetched |
|---|---|---|---|
| the model does the arithmetic (step 5) | 72/96 ✓ · 24 – | **34/192** ✓ · 148 ✗ · 10 – | 45/48 ✓ · 3 ✎ |
| a calculator, inputs copied by the model (step 6) | 96/96 ✓ | **154/192** ✓ · 29 ✗ · 9 – | **0/48** ✓ · 44 ✎ · 4 – |
| the tool reads typed memory (step 7) | 96/96 ✓ | **192/192** ✓ | 25/48 ✓ · 23 ✎ |

✓ right · ✗ wrong · – no amount although memory has one ("No record", or still calling tools after 4 turns) ·
✎ a value where memory has none.

By question (24 answers each):

| | step 5 | step 6 | step 7 |
|---|---|---|---|
| L2 Mirela Studio's API calls | – 24 ("No record") | ✓ 24 | ✓ 24 |
| C1 Halden seat charge (41 × 52 × 0.88) | ✗ 24 | ✓ 24 | ✓ 24 |
| C2 Halden overage | ✓ 10 · ✗ 14 | ✓ 21 · ✗ 3 | ✓ 24 |
| C3 Halden total | ✗ 16 · – 8 | ✓ 11 · ✗ 10 · – 3 | ✓ 24 |
| C4 Mirela total | ✗ 24 | ✓ 6 · ✗ 16 · – 2 | ✓ 24 |
| C5 Ostrand total | ✗ 22 · – 2 | ✓ 22 · – 2 | ✓ 24 |
| C6 Pellow seat charge | ✗ 24 | ✓ 24 | ✓ 24 |
| C7 Mirela adds 4 seats (4 × 38) | ✓ 24 | ✓ 24 | ✓ 24 |
| C8 Ostrand adds 5 seats | ✗ 24 | ✓ 22 · – 2 | ✓ 24 |
| N1 Pellow overage (no `get_usage`) | ✓ 23 · ✎ 1 | **✎ 23 · – 1** | ✓ 24 |
| N2 Pellow total (no `get_usage`) | ✓ 22 · ✎ 2 | **✎ 21 · – 3** | **✎ 23 · ✓ 1** |

- **The memory is right; the arithmetic is not.** Look-ups are fine in every setup (L2 aside: in step 5 the model
  answered "No record" for a value MemoryRouter had attached). Step 5 gets only the one-multiplication charge (C7)
  reliably.
- **A calculator fixes the arithmetic and breaks "No record".** With the sum to do itself, the model mostly declined
  to price usage it did not have (45/48). Handed a tool with an `api_calls` parameter, it filled the parameter in
  (0/48), almost always right after the tool reported it missing.
- **Copied inputs go wrong in quieter ways too**: a pre-subtracted usage count (68,750 for Mirela in 20 calls, 362,480 for
  Halden in 19, over the seven runs with call records), a discount left null (then retried), added seats put into `seats`. The input check in step 6 names each of them; the final answer
  does not.
- **Reading typed memory in the tool removes all of that** (192/192, N1 24/24), and every amount comes with the tool
  results it was computed from. What it cannot fix is the model presenting a partial result as the whole (N2 1/24;
  the one right answer made the same two calls and still said "No record").
- These are totals over every run; model replies vary from run to run.

## Use it in your own app

- **Store each tool result with typed fields**, not only as text: `fact add` the readable sentence (that is what the
  model sees through MemoryRouter), then `fact update --metadata '{"tool": …, "account": …, "returned": …,
  "fields": {…}}'`. `fact list --projects` returns it all; `typed_view()` in `demo.py` picks the latest result per field.
- **Let tools read their own inputs from memory.** Give the model the parameters only it can know (which account,
  which quantity, how many seats to add); fetch everything else in code, and return an error that names the missing
  tool result.
- **Never let a "missing input" error be answered by the model.** If a tool reports a missing input, treat the turn as
  unanswerable in code — don't show an amount when any tool call in it failed. That one rule would have caught N2 too.
- **Check copied arguments against memory when you can't avoid them** (`audit()` in `demo.py`): it is a dictionary
  lookup per field and it tells you which input was made up.

## Troubleshooting

- `this memorylake CLI has no 'router' command` — upgrade the CLI (v20261015 or later).
- `MemoryRouter refused the call for lack of balance` — the memory steps still run; top up to see the answers.
- `retrieval does not return the new facts yet after 60 s` — the demo goes on; MemoryRouter may attach less in that run.
- Leftovers from an interrupted run: `python3 demo.py cleanup` (everything is prefixed `mlu-tim-`).

## Files

- `demo.py` — the whole demo (standard library only): CLI wrapper, setup, the tool results as typed facts, the typed
  view, the calculator (`amount()`), the tool-calling loop over `router chat --body`, the input check, grading, cleanup
- `data/billing.json` — the 13 tool results (text + typed fields), the fourteen questions, the three system prompts and
  the two `compute_charge` tool definitions
- `web/server.py`, `web/static/` — the browser companion (standard library HTTP + server-sent events)
