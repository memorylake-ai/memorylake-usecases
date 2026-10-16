# Empty retrieval memory: the on-call assistant says "No record", and you cannot tell whether that is true

A runnable version of the MemoryLake use case
[*Run Production Agents With Memory Observability You Don't Have to Build*](https://www.memorylake.ai/en/usecase/memory-observability-for-production-agents),
built entirely on the [`memorylake` CLI](https://github.com/memorylake-ai/memorylake-cli) (v20261015 or later).

**Watch it run:** (recording in progress)

**The problem.** Larkspur Pay (a fictional payments company) keeps its platform runbooks in shared memory: six short
Markdown documents — rollback sign-off, Postgres failover, partner rate limits, Sev1 escalation, certificate
rotation, the holiday change freeze. An on-call assistant answers from them. It runs on MemoryRouter: every question
is an ordinary model call with `--boundary` added, and MemoryRouter attaches the runbook pages that match.

Some calls come back with **nothing attached**. MemoryRouter found no page for the question as it was typed, and the
reply does not say so. The model answers anyway:

```
Q9  How long do we keep payment webhook logs?                      (no runbook covers this)
    ✎∅ "Answer: We keep payment webhook logs for 90 days."
Q7  Who do I wake up if a Sev1 is still burning after half an hour?  (paging.md: Rosa Lindqvist, Head of Platform)
    ✎∅ "Answer: Wake up the on-call engineer and the platform team lead."
Q8  Can I ship a hotfix the Friday after Thanksgiving?               (freeze.md: only with the VP of Engineering's approval)
    ✎∅ "Answer: No, hotfixes should not be deployed during holiday weekends ..."
```

The usual fix is one more sentence in the system prompt: *"If you do not know the answer, reply 'Answer: No record.'
Do not guess."* It helps — invented answers go from 66 to 30 over 4 runs — but look at what the "No record" replies are for:

```
“Do not guess” prompt:  “No record” 33 times;  21 of them for a question a runbook answers
    –∅ Q6 How many calls can a partner make before we throttle them?   "Answer: No record."   (ratelimits.md: 240/min)
    –∅ Q8 Can I ship a hotfix the Friday after Thanksgiving?           "Answer: No record."   (freeze.md)
    ✓∅ Q10 Which Kafka cluster does the ledger service publish to?     "Answer: No record."   (truly not covered)
```

A retrieval miss and a question no runbook covers now get **the same reply**. The engineer on call cannot tell "we
have no rule about this" from "the assistant didn't look in the right place" — and the first one reads as permission.

The app can tell them apart, because each MemoryRouter call leaves one trace: `usage.prompt_tokens`. If it is no
higher than the same call **without** a boundary, nothing was attached (∅). In the third setup the app checks that on
every call; on ∅ it runs `search --types document --top-k 1` itself (which finds the right runbook for every question
here) and asks again with that page, and it labels where the answer came from: **117 of 120 right over 4 runs**, no invented answers.

## What the demo shows

| | Step | What you see |
|---|---|---|
| 1–3 | connect, set up, store | One project with six runbooks (Library → project), one boundary over it |
| 4 | **Can the index find it?** | `search --types document --top-k 1` with each question as typed: the right runbook for 8 of 8. The answers are in memory |
| 5 | **What MemoryRouter hands the model** | claude-haiku-4-5 copies out what it was given through the same boundary: a runbook page — or nothing at all |
| 6 | plain prompt | Ten questions × 3. Where nothing was attached, the model invents (✎∅) |
| 7 | "Do not guess" prompt | Fewer inventions; "No record" for retrieval misses and true gaps alike (–∅ vs ✓) |
| 8 | "Do not guess" + the app sees ∅ | Attached tokens checked per call; on ∅ the app's own search, a second call, and the source on the answer (↻) |
| 9 | **per-retrieval metrics** | For every question: boundary calls, how many attached something, ∅ count, median attached tokens |
| 10 | scoreboard | Every answer, graded; the "No record" replies split into right and wrong |

Ten questions: four in the runbooks' own words, four the way someone on call actually types them ("Do I need anyone's
OK?"), and two that no runbook covers (webhook log retention, the ledger's Kafka cluster) — for those, "No record" is
the right answer.

## Prerequisites

- Python 3.9+ (standard library only)
- The `memorylake` CLI, **v20261015 or later** (`memorylake router` is new in that release):
  ```bash
  curl -fsSL https://raw.githubusercontent.com/memorylake-ai/memorylake-cli/main/scripts/install.sh | sh
  ```
- A MemoryLake API key ([app.memorylake.ai](https://app.memorylake.ai) → API Keys)
- Router calls are billed to your MemoryLake balance: about 155 calls per run, about 24,000–25,000 prompt +
  3,200–3,400 reply tokens. Without balance, the memory steps (1–4) still run and the answers are skipped.

## Run it

```bash
git clone https://github.com/memorylake-ai/memorylake-usecases.git
cd memorylake-usecases/empty-retrieval-memory
export MEMORYLAKE_API_KEY=sk-...          # logs into an isolated profile under .memorylake-demo/
python3 demo.py                           # about 4 minutes
python3 demo.py ask "Can I ship a hotfix the Friday after Thanksgiving?"   # one question, with its source
python3 demo.py runbooks                  # offline: the six runbooks and the ten questions
python3 demo.py cleanup                   # delete everything the demo created
```

China region: also `export MEMORYLAKE_BASE_URL=https://app.memorylake.cn/openapi/memorylake`.
Everything the demo creates is prefixed `mlu-erm-`; every run starts over.

## Or run it in the browser

```bash
python3 web/server.py                     # opens http://127.0.0.1:8765
```

Paste the key, press **Run demo**. The page follows the run step by step: the runbooks, what search finds, the
checker's copy of what the model was handed (or "nothing attached (∅)"), the questions under each setup (in the third,
every answer is labelled "MemoryRouter attached 183 tokens" or "MemoryRouter attached nothing → the app's search:
freeze.md"), the per-retrieval metrics and a scoreboard. **Ask** runs one question the observed way. A terminal drawer
shows every `memorylake` command as it runs. **Clean up** removes it all.



## What happens, step by step

### Steps 2–3: one project, six runbooks

```bash
memorylake proj create --name 'Larkspur Pay runbooks' --custom-id mlu-erm-runbooks --workspace ws-…
memorylake boundary create --name 'On-call assistant: runbooks' --custom-id mlu-erm-runbooks --projects proj-… --workspace ws-…
memorylake lib upload data/runbooks/rollback.md --name mlu-erm-rollback.md --on-conflict overwrite    # × 6
memorylake proj doc import --project proj-… <six item ids> --wait --workspace ws-…                      # about 60 s
```

### Step 4: can the index find the answer?

```bash
memorylake search 'Under the release rollback runbook, who must sign off on a payments-api rollback?' \
  --projects proj-… --types document --top-k 1 --workspace ws-…
```

```
  Q5   I'm on call and want to roll back. Do I need anyone's OK?           top 1: rollback.md     ← the runbook with the answer
  Q7   Who do I wake up if a Sev1 is still burning after half an hour?     top 1: paging.md       ← the runbook with the answer
  Q9   How long do we keep payment webhook logs?                           top 1: rollback.md     ← closest page; no runbook covers this
  · search finds the right runbook for 8 of the 8 questions a runbook answers. The answers are in memory
```

Search always returns its top k, relevant or not — for Q9 it returns the closest page. That is fine here: the
model is told to answer only from it.

### Step 5: what MemoryRouter hands the model

```bash
memorylake router messages 'Under the release rollback runbook, …' --model claude-haiku-4-5 --max-tokens 2500 \
  --temperature 0 --system 'You are a context inspector … copy out everything above the question …' --boundary bnd-…
```

```
  Q7  Who do I wake up if a Sev1 is still burning after half an hour?
      1. nothing attached (∅)
      2. nothing attached (∅)
  Q8  Can I ship a hotfix the Friday after Thanksgiving?
      1. freeze.md  ← has the answer
      2. nothing attached (∅)
```

"Nothing attached" is read from the checker's own prompt tokens: equal to the same call without a boundary.

### Steps 6–8: ask the on-call assistant

```bash
memorylake router chat 'How long do we keep payment webhook logs?' --model gpt-4o-mini --max-tokens 120 \
  --temperature 0 --system "You are the on-call assistant for Larkspur Pay's platform team. … Do not guess." --boundary bnd-…
```

Before each setup, each question is asked once **without** a boundary, with the same system prompt: those prompt
tokens are the baseline. In step 8, a boundary call at the baseline means ∅, and the app does this:

```bash
memorylake search 'Who do I wake up if a Sev1 is still burning after half an hour?' --projects proj-… --types document --top-k 1
memorylake router chat 'Who do I wake up …' --model gpt-4o-mini --system '… Do not guess.
MemoryRouter attached nothing for this question, so the app searched the runbooks itself and found this page:
(paging.md)
# Policy - Incident paging and escalation …'          # no --boundary
```

```
  Q7  Who do I wake up if a Sev1 is still burning after half an hour?
      runbook: paging.md  ·  answer: Rosa Lindqvist, Head of Platform, by phone
      answers ✓∅↻ ✓∅↻ ✓∅↻   [∅ → paging.md via search]   e.g. “Answer: Rosa Lindqvist, the Head of Platform, by phone.”
```

### Step 9: per-retrieval metrics

```
  question                                                              calls  attached  ∅   median tokens
  Q1   Under the release rollback runbook, who must sign off on a pay     9         9   0     178
  Q2   What is the partner API rate limit per key?                        9         9   0     167
  Q3   Which replica do we promote in a Postgres failover of the ledg     9         7   2     169
  Q4   When does the 2026 holiday change freeze start?                    9         7   2     161
  Q5   I'm on call and want to roll back. Do I need anyone's OK?          9         7   2     274
  Q6   How many calls can a partner make before we throttle them?         9         2   7     167
  Q7   Who do I wake up if a Sev1 is still burning after half an hour     9         0   9       —
  Q8   Can I ship a hotfix the Friday after Thanksgiving?                 9         2   7     161
  Q9   How long do we keep payment webhook logs?                          9         0   9       —
  Q10  Which Kafka cluster does the ledger service publish to?            9         0   9       —
```

Nothing in a MemoryRouter reply says what it attached. The prompt-token difference is the per-call retrieval metric
an app can log, alert on and put next to the answer.

### The read-only subcommands

- `python3 demo.py ask "<question>"` — one question the observed way: baseline, boundary call, attached tokens, and
  the search fallback on ∅. The output says where the answer came from.
- `python3 demo.py runbooks` — offline: the six runbooks and the ten questions with their answers.

## Measured

Over **4 runs** (including the recordings; per setup and run: 10 questions × 3 answers):

| | right | ✎ invented (all with nothing attached) | “No record” | of those, a runbook has the answer | ∅ calls | answered from the app's search |
|---|---|---|---|---|---|---|
| plain prompt | 54/120 | 66 | 0 | 0 | 66 | – |
| “Do not guess” prompt | 69/120 | 30 | 33 | 21 | 63 | – |
| “Do not guess” + the app sees ∅ | 117/120 | 0 | 27 | 3 | 64 | 64 |

By question (12 answers each):

| | plain prompt | “Do not guess” | + the app sees ∅ |
|---|---|---|---|
| Q1 Under the release rollback runbook, who must sign off on a payments-api rollback? (rollback.md) | ✓ 12 | ✓ 12 | ✓ 12 |
| Q2 What is the partner API rate limit per key? (ratelimits.md) | ✓ 12 | ✓ 12 | ✓ 11, ✓∅ 1 |
| Q3 Which replica do we promote in a Postgres failover of the ledger database? (failover.md) | ✎∅ 8, ✓ 4 | ✓ 7, ✎∅ 5 | ✓ 8, ✓∅ 4 |
| Q4 When does the 2026 holiday change freeze start? (freeze.md) | ✓ 10, ✎∅ 2 | ✓ 11, –∅ 1 | ✓ 10, ✓∅ 2 |
| Q5 I'm on call and want to roll back. Do I need anyone's OK? (rollback.md) | ✎∅ 6, ✓ 6 | ✓ 11, ✎∅ 1 | ✓ 9, ✓∅ 3 |
| Q6 How many calls can a partner make before we throttle them? (ratelimits.md) | ✎∅ 8, ✓ 4 | –∅ 8, ✓ 4 | ✓∅ 11, ✓ 1 |
| Q7 Who do I wake up if a Sev1 is still burning after half an hour? (paging.md) | ✎∅ 9, ✓ 3 | ✎∅ 12 | ✓∅ 10, ✓ 2 |
| Q8 Can I ship a hotfix the Friday after Thanksgiving? (freeze.md) | ✎∅ 9, ✓ 3 | –∅ 12 | ✓∅ 9, – 3 |
| Q9 How long do we keep payment webhook logs? (not covered) | ✎∅ 12 | ✎∅ 12 | ✓∅ 12 |
| Q10 Which Kafka cluster does the ledger service publish to? (not covered) | ✎∅ 12 | ✓∅ 12 | ✓∅ 12 |

- **Every invented answer, in every setup, had nothing attached** (66 + 30 + 0 = 96 of 96). When MemoryRouter attaches a
  runbook page, the model uses it.
- **"Do not guess" stops some inventions, not all.** Q9 (webhook logs) was answered "90 days" every time with the
  instruction too; Q7 still got "the on-call engineer and the platform team lead". Where it works, it turns ∅ into
  "No record" — and those were more often for questions a runbook answers (21) than for the one none does.
- **The observed setup answers from the app's search on ∅** (in the third column, ✓∅ = MemoryRouter attached
  nothing and the answer came from the app's search). It is still the model reading a page: Q8 ("the Friday after
  Thanksgiving") needs the date worked out against the freeze window, and 3 times the model said "No record"
  with freeze.md attached by MemoryRouter. The label on those answers ("MemoryRouter attached … tokens") says this
  "No record" is the model's reading, not a retrieval miss.
- These are totals over every run. Model replies vary from run to run, and so does which calls come back empty.

## Use it in your own app

- **Log attached tokens on every MemoryRouter call.** Keep a no-boundary baseline per system prompt (or per
  prompt + question) and record `prompt_tokens − baseline`. That is your hit rate and your ∅ count; nothing else in
  the reply carries it.
- **Treat ∅ as a retrieval event, not as an answer.** Retry, rephrase, or — as here — run your own
  `search --types document` and pass the page in yourself. `observed()` in `demo.py` is about 20 lines.
- **Put the source on the answer.** "From MemoryRouter: freeze.md" / "MemoryRouter attached nothing; searched:
  rollback.md" lets a reader tell a real gap from a miss. A bare "No record" can't.
- **Don't rely on "Do not guess" alone.** It changes how the model fails, not whether retrieval found anything.

## Troubleshooting

- `this memorylake CLI has no 'router' command` — upgrade the CLI (v20261015 or later).
- `MemoryRouter refused the call for lack of balance` — the memory steps still run; top up to see the answers.
- `the documents are still processing after 6 minutes` — run again; the demo starts over.
- Leftovers from an interrupted run: `python3 demo.py cleanup` (everything is prefixed `mlu-erm-`).

## Files

- `demo.py` — the whole demo (standard library only): CLI wrapper, setup, upload and import, search, the checker,
  MemoryRouter calls with baselines, the observed path with its search fallback, grading, metrics, cleanup
- `data/oncall.json` — the questions (with the runbook and the answer used for grading), the two system prompts,
  the fallback wording and the checker prompt
- `data/runbooks/` — the six runbooks (plain ASCII Markdown)
- `web/server.py`, `web/static/` — the browser companion (standard library HTTP + server-sent events)
