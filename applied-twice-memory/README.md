# Applied twice: the rule is in memory and in the tool, and the model applies it before the tool does

A runnable version of the MemoryLake use case
[*Give Small Teams Prompt and Workflow Memory That Works Across Every AI Tool*](https://www.memorylake.ai/en/usecase/cross-tool-prompt-and-workflow-memory-for-small-teams),
built entirely on the [`memorylake` CLI](https://github.com/memorylake-ai/memorylake-cli) (v20261015 or later).

**Watch it run:** videos are being recorded.

**The problem.** Quillhaven Bookkeeping (a fictional six-person firm) tuned five calculator tools in its custom GPT
(overtime pay, commission, mileage reimbursement, late fees and insurance payouts) and now stores them once in
MemoryLake as workflow memory, so any model the team uses can call them. The firm's records are in memory too, and so
are its policies: overtime above 40 hours, commission above a 50,000 dollar quota, the first 30 miles of each mileage
log are commuting, late fees start after a 10-day grace period. **Each tool already applies its own threshold.**

The model reads the policy and applies the threshold before it calls the tool. Then the tool applies it again:

```
  Q6  How much should we reimburse Dana Ruiz for her October mileage?
      the record gives: 267.40 dollars   (ML-7)
      answers ⇣ ⇣ ⇣   e.g. “246.4 dollars”
        │ mileage_reimbursement(miles=382)
        │   inputs vs memory: miles 382 ⇣ (the record says 412; 412 − 30 = 382, and the tool takes 30 off again)
        │   → 246.40 dollars

  Q7  What late fee do we charge Marrowby Dental on invoice INV-2207?
      the record gives: 76.80 dollars   (INV-2207)
      answers ⇣ ⇣ ⇣   e.g. “52.8 dollars”
        │ late_fee(days_overdue=32, invoice_amount=4,800)
        │   inputs vs memory: days_overdue 32 ⇣ (the record says 42; 42 − 10 = 32, and the tool takes 10 off again) · invoice_amount 4,800 ✓
        │   → 52.80 dollars
```

The answer comes back exact to the cent, the model states it confidently, and it is wrong. **The policies cause it.**
With the same bare tools and only the records attached (no policies in the memory the model sees), the mileage and
late-fee questions are right every time: **103 → 143 of 144** record questions right, and the threshold is applied
twice **41 → 0** times. The memory is not wrong. The rule is in two places, and the model applies both.

Two fixes, measured:

- **Describe the unit.** Each parameter says *TOTAL, exactly as recorded … never subtract the threshold yourself*.
  This fixes mileage, overtime and the what-ifs (**126 of 144**, what-if 35 of 36), but **not the late fee: 0 of 18**.
  The model still passes 32 days, with the instruction not to do that in the same request.
- **Pass the record, not the number.** The tools take a record id (`late_fee(record="INV-2207")`), plus the change
  for a what-if (`overtime_pay(record="TS-41", extra_hours=4)`), and read the recorded value from typed memory
  themselves. There is no number left for the model to pre-process: **144 of 144**, what-if **36 of 36**.

The same workflow memory, read by a second model (claude-haiku-4-5, through the same `router chat`): with the bare
tools it takes the threshold off first in **140 of 244** tool calls, and often never answers. It calls the tool
four times with the same pre-reduced number (Q2, Q6, Q7). With the record-id tools: **96 of 96**, what-if 24 of 24.

Totals are over 6 runs (5 CLI runs + 1 browser run, every question × 3 with gpt-4o-mini and × 2 with claude-haiku-4-5).

## What the demo shows

| | Step | What you see |
|---|---|---|
| 1–2 | connect, set up | Three projects (workflows, records, policies) and two boundaries: records + policies, and records only |
| 3 | **workflow memory** | Five tools × three versions (bare / described / record id), one fact each, the OpenAI tool definition in its metadata. The app reads them back with `fact list` and hands them to whichever model it calls |
| 4 | records and policies | 8 records (typed fields in metadata) and 4 policies, one fact each |
| 5 | what memory gives | The recorded values put through the same five tools in code: the answer key |
| 6 | **bare tools** | `router chat --body … --boundary` (records + policies), tools from step 3; every number the model passes is checked against the record |
| 7 | **bare tools, records only** | The control: the same request with the policies left out of the boundary |
| 8 | described tools | Parameter descriptions that pin the unit |
| 9 | **tools take the record id** | The tool reads the recorded value itself |
| 10 | a second model | claude-haiku-4-5 with the bare and the record-id tools |
| 11 | scoreboard | By arm: record questions, what-ifs, and tool calls that carried a threshold already taken off |

Ten questions: eight answered from a record ("How much overtime pay does Dana Ruiz get for the week of 5
October?"), and two what-ifs ("If Dana Ruiz had worked 50 hours that week …"). The what-ifs are the reverse check
on any fix: a tool that only takes the record id must still be able to answer them (it takes the change, not a new
total).

## Prerequisites

- Python 3.9+ (standard library only)
- The `memorylake` CLI, **v20261015 or later** (`memorylake router` is new in that release):
  ```bash
  curl -fsSL https://raw.githubusercontent.com/memorylake-ai/memorylake-cli/main/scripts/install.sh | sh
  ```
- A MemoryLake API key ([app.memorylake.ai](https://app.memorylake.ai) → API Keys)
- Router calls are billed to your MemoryLake balance: about 390 calls per run (gpt-4o-mini, plus claude-haiku-4-5 in
  step 10), about 260,000 prompt + 14,000 reply tokens. Without balance, the memory steps (1–5) still run and the
  answers are skipped.

## Run it

```bash
git clone https://github.com/memorylake-ai/memorylake-usecases.git
cd memorylake-usecases/applied-twice-memory
export MEMORYLAKE_API_KEY=sk-...          # logs into an isolated profile under .memorylake-demo/
python3 demo.py                           # about 6 minutes
python3 demo.py workflows                 # the tools as memory holds them, and what a pre-applied threshold does
python3 demo.py ask "What late fee do we charge Marrowby Dental on invoice INV-2207?"   # one question, record-id tools
python3 demo.py cleanup                   # delete everything the demo created
```

China region: also `export MEMORYLAKE_BASE_URL=https://app.memorylake.cn/openapi/memorylake`.
Everything the demo creates is prefixed `mlu-atm-`; every run starts over.

## Or run it in the browser

```bash
python3 web/server.py                     # opens http://127.0.0.1:8765
```

Paste the key, press **Run demo**. The page follows the run step by step: the three versions of each tool, the
records and policies, the answer key, the ten questions under each arm (every tool call with its inputs checked
against the record), and a scoreboard. **Ask** runs one question with the record-id tools. A terminal drawer shows
every `memorylake` command as it runs. **Clean up** removes it all.

## What happens, step by step

### Steps 2–3: workflow memory

```bash
memorylake proj create --name 'Quillhaven Bookkeeping: workflows' --custom-id mlu-atm-workflows --workspace ws-…   # also: records, policies
memorylake boundary create --name 'Bookkeeping assistant: records + policies' --custom-id mlu-atm-full --projects proj-…,proj-… --workspace ws-…
memorylake boundary create --name 'Bookkeeping assistant: records only' --custom-id mlu-atm-records --projects proj-… --workspace ws-…
memorylake fact add --project proj-… 'Workflow tool overtime_pay, bare version: Overtime pay for one week. Parameters: hours (Hours.); hourly_rate (Hourly rate in dollars.)' … # 15 texts
memorylake fact update fact-… --project proj-… --metadata '{"kind": "workflow", "schema": "bare", "tool": {"type": "function", "function": {"name": "overtime_pay", …}}}' --workspace ws-…
```

The fact text is what a person (or a search) reads. The metadata holds the exact tool definition the app sends as
OpenAI `tools`. The workflows project is never in a boundary: the tools reach the model as tool definitions, not as
attached memory.

| tool | bare (the custom GPT's) | described | record id |
|---|---|---|---|
| `overtime_pay` | `hours`: Hours. · `hourly_rate` | `hours`: TOTAL hours worked in the week, exactly as the timesheet records them, including the first 40. Never subtract 40 yourself. | `record`, `extra_hours` (what-ifs only) |
| `commission` | `bookings`: Bookings in dollars. | TOTAL … including the quota. Never subtract the quota yourself. | `record`, `extra_bookings` |
| `mileage_reimbursement` | `miles`: Miles. | TOTAL miles on the log … Never subtract commuting miles yourself. | `record` |
| `late_fee` | `invoice_amount`, `days_overdue`: Days overdue. | TOTAL days after the due date … Never subtract the grace period yourself. | `record` |
| `insurance_payout` | `claimed`: Amount in dollars. | TOTAL amount claimed … before the deductible. | `record` |

### Steps 4–5: records, policies, and the answer key

```
    TS-41       Timesheet TS-41 (Dana Ruiz, week of 5 October 2026): 46 hours worked; hourly rate 32 dollars.
    ML-7        Mileage log ML-7 (Dana Ruiz, October 2026 client visits): 412 miles driven.
    INV-2207    Invoice INV-2207 (client Marrowby Dental): 4,800 dollars, paid 42 days after its due date.
    …
    P-MIL       Policy: mileage is reimbursed at 0.70 dollars per mile; the first 30 miles of each monthly log are commuting and not reimbursed.
    P-LATE      Policy: overdue invoices carry a late fee of 0.05% of the invoice per day overdue, after a 10-day grace period.
```

The answer key is the recorded values put through the same `calc()` the tools run, e.g. `late_fee(days_overdue
42, invoice_amount 4,800)` → 76.80. Each tool takes its own threshold off: `hours − 40`, `bookings − 50,000`,
`miles − 30`, `days_overdue − 10`, `claimed − 500`.

### Steps 6–9: four ways to call the same tools

```bash
memorylake router chat 'How much overtime pay does Dana Ruiz get for the week of 5 October?' --body out/bodies/bare-Q1-0011.json --boundary bnd-…
```

The body is the system prompt plus the tool definitions read back in step 3 (`tool_choice: auto`). When the reply
has `tool_calls`, the app runs each one, appends `{"role": "tool", …}` with the result, and calls again with the
whole conversation, up to 4 turns. Each question is asked once first without a boundary (the ∅ baseline: a boundary
call with no more prompt tokens than that had nothing attached; in these runs MemoryRouter always attached
something).

Every number the model passes is checked against the record:
✓ as recorded · ⇣ the recorded value with the tool's threshold already taken off · Δ a what-if's change passed as the
new total · ✗ anything else.

Per question, over 6 runs (gpt-4o-mini, 18 answers each):

| | Q1 OT Dana | Q2 OT Priya | Q3 OT Marco | Q4 com. Lena | Q5 com. Omar | Q6 mileage | Q7 late fee | Q8 payout | W1 50 h | W2 +5,000 |
|---|---|---|---|---|---|---|---|---|---|---|
| bare, records + policies | 14 (⇣4) | 17 (⇣1) | 18 | 18 | 18 | **0 (⇣18)** | **0 (⇣18)** | 18 | **0 (⇣18)** | 5 (Δ13) |
| bare, records only | 18 | 18 | 18 | 18 | 18 | 18 | 17 | 18 | 0 (Δ18) | 8 (Δ10) |
| described | 18 | 18 | 18 | 18 | 18 | 18 | **0 (⇣18)** | 18 | 18 | 17 |
| record id | 18 | 18 | 18 | 18 | 18 | 18 | 18 | 18 | 18 | 18 |

Notes from the runs:

- **Only some rules get applied twice.** Overtime for Dana (46 h) is pre-reduced in 4 of 18 answers, Priya (51.5 h)
  in 1, the commission never, and mileage and the late fee every time. Questions under the threshold (Marco 38 h,
  Omar 48,000) come out right either way: zero is zero.
- **The control also fails on what-ifs, in a different way.** Without the policies, the model passes the *change*
  as the total (`hours=4`, `bookings=5000`). That is not the threshold being applied twice, and the described tools
  fix it (35 of 36).
- **"Never subtract the grace period yourself" does not stop the late-fee one.** It goes 0 of 18 in the described arm,
  in every run. The description sits in the same request as the policy, and the model still passes 32.
- **claude-haiku-4-5** with the bare tools (12 answers per question): Q2 0/12, Q6 0/12, Q7 0/12, the rest right.
  Mostly no answer at all: it repeats the same pre-reduced call until the 4-turn limit (counted as –, no amount).
  140 of its 244 tool calls carried a pre-reduced value. With the record-id tools every answer was right (120 of 120).

### Step 11: scoreboard (one run)

```
                                         from a record              what-if                tool calls, threshold already off
  Bare tools, records + policies         17/24 ✓ · 7 ⇣              1/6 ✓ · 3 ⇣ · 2 ✗      10/27
  Bare tools, records only               23/24 ✓ · 1 ✗              2/6 ✓ · 4 ✗            0/33
  Described tools, records + policies    21/24 ✓ · 3 ⇣              6/6 ✓                  3/31
  Tools take the record id               24/24 ✓                    6/6 ✓                  0/30
  claude-haiku-4-5, bare tools           10/16 ✓ · 2 ⇣ · 4 –        4/4 ✓                  18/36
  claude-haiku-4-5, record id            16/16 ✓                    4/4 ✓                  0/20
```

## Use it in your own system

- **When a rule lives in a tool, keep its number out of the model's hands.** If the tool applies the threshold, pass
  the record (an id the tool resolves from memory) rather than a value the model copies and may pre-process.
  Records stored as facts with typed metadata (`fact update --metadata`) make that lookup a `fact list`, with no
  parsing.
- **A schema description is not a guarantee.** Here it fixed three of four double applications and missed the
  fourth in every run. If you rely on descriptions, test each rule. Don't assume one test generalises.
- **Workflow memory works across models.** One set of tool definitions, stored once as facts, sent as OpenAI
  `tools` to gpt-4o-mini and claude-haiku-4-5 through the same `router chat`. The failure travels too, so put the fix
  in the tool definition, not in a prompt for one model.
- Keep a what-if question in your test set: it tells you whether the fix still lets the model express a change.

## Troubleshooting

- **`unexpected argument` / no `router` command**: upgrade the CLI (v20261015 or later).
- **402 / insufficient balance**: the memory steps still run; the answers are skipped. Top up in the console.
- **Different counts from the ones above**: model replies vary from run to run; the README reports totals over all
  runs. The late-fee and mileage double applications showed up in every run.
- **Left-over demo data** (an interrupted run): `python3 demo.py cleanup`, or just run again: every run starts over.

## Files

| file | what it is |
|---|---|
| `demo.py` | the demo: CLI wrapper, the five tools (`calc()`), the input audit (`audit()`), the agent loop (`ask()`), subcommands |
| `data/workflows.json` | the three tool schemas, 8 records, 4 policies, 10 questions, the system prompt, the arms |
| `web/server.py` | local web companion (standard library HTTP + Server-Sent Events), imports `demo.py` |
| `web/static/` | the page (no build step) |
| `out/report.json` | written by each run: every answer, every tool call with its audit |
