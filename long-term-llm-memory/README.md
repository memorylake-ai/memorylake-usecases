# Long-term memory for any LLM, without bloating the context window

A runnable version of the MemoryLake use case
[*Give Any LLM Long-Term Memory Without Bloating the Context Window*](https://www.memorylake.ai/en/usecase/long-term-memory-for-llms),
built entirely on the [`memorylake` CLI](https://github.com/memorylake-ai/memorylake-cli) (v20261015 or later).

**Watch it run:** videos coming soon.

**The problem.** A chat model starts every session blank. The usual fix is to keep everything the app has learned about
the user and paste it into the system prompt. That works on day one. Nine months later the prompt carries hundreds of
facts on every call, and the model has to pick the right few out of all of them.

**What this demo shows.** An assistant app remembers one user, Maya Lindqvist (a fictional freelance architect): her
contacts and their birthdays, her projects and clients, her door code, what she avoids at restaurants. The memory grows
from **20 facts in week 1** to **100 by month 3** and **400 by month 9**, stored as facts in one MemoryLake project with
one **boundary** over it. At each point the same four questions are asked three ways, and every reply is graded against
what is stored at that moment:

| Way | The call |
|---|---|
| no memory | `memorylake router chat "<question>" --model gpt-4o-mini --system "…"` |
| everything pasted | `memorylake router chat "<question>" --model gpt-4o-mini --body out/everything-month9.json` (a whole request body whose system message lists every stored fact) |
| MemoryRouter | `memorylake router chat "<question>" --model gpt-4o-mini --system "…" --boundary bnd-…` |

Two questions are look-ups (the studio door code, the passport expiry date). Two are lists ("everyone whose birthday is
in November", "… in March"); they are graded name by name: right, missed, and **extra** (a name that does not belong).

| Step | What you see |
|---|---|
| 3 Week 1 (20 facts) | All three work except no-memory. Pasting costs 395 tokens per question; MemoryRouter attaches 78–97 |
| 4 Month 3 (100 facts) | Pasting costs 1,822 tokens per question, and a list picks up names that do not belong. MemoryRouter: 78–158 tokens, exact |
| 5 Month 9 (400 facts) | Pasting costs 6,911 tokens per question; both lists come back with wrong names. MemoryRouter: still 78–158 tokens, still exact |
| 6 Extra names | Every wrong name looked up in memory with `fact list --query`. The stored birthday is in another month |
| 7 Scoreboard | Tokens per question and exact answers at each point, then the same boundary through Claude on the Anthropic API |

**Why step 5 happens.** With every fact in the prompt, the model reads 60 contacts, their birthdays and 300 other dated
facts (project deadlines, trips, repairs) at once. It gets the look-ups right; it is the lists that slip. A contact
whose birthday is May 8 comes back as "November 8"; a name gets the day of a neighbouring line. Nothing in the memory
is wrong. The answer is assembled from too much at once. MemoryRouter attaches only what is relevant to the question
(for "November birthdays", the five contacts born in November), so the prompt stays small and there is nothing to mix
up.

Model replies vary from run to run, so every reply is graded and printed as it comes out, and [Measured](#measured)
below reports totals over all runs, not the best one.

## Prerequisites

- Python 3.9+ (standard library only)
- The `memorylake` CLI **v20261015 or later** (`memorylake router` arrived in that release):
  ```bash
  curl -fsSL https://raw.githubusercontent.com/memorylake-ai/memorylake-cli/main/scripts/install.sh | sh
  memorylake version
  ```
- A MemoryLake API key ([app.memorylake.ai](https://app.memorylake.ai) → API Keys).
- **Router calls are billed to your MemoryLake balance** (`memorylake billing balance`). One run makes 37 calls on
  `gpt-4o-mini` (one on `claude-haiku-4-5`), about 39,000 prompt + 1,900 reply tokens in all, most of them in the
  pasted prompts; the run prints the total. If the balance is empty, the router steps print the refusal and are skipped.

## Run it

```bash
git clone https://github.com/memorylake-ai/memorylake-usecases.git
cd memorylake-usecases/long-term-llm-memory
export MEMORYLAKE_API_KEY=sk-...
python3 demo.py                                          # the whole story, about 2 minutes
python3 demo.py ask november                             # one question, three ways (door|passport|november|march)
python3 demo.py ask "Who are my climbing partners?"      # or your own question, against the memory as it is now
python3 demo.py ask "Which projects are due in March?" --mode router
python3 demo.py memory                                   # what is stored, and the right answers to the lists
python3 demo.py cleanup                                  # delete everything the demo created
```

The key is used to log into an isolated CLI profile under `.memorylake-demo/` (git-ignored); your own
`~/.memorylake` is never touched. For the China endpoint also set
`MEMORYLAKE_BASE_URL=https://app.memorylake.cn/openapi/memorylake`. Everything the demo creates has a `custom_id`
starting with `mlu-ltm-`. Every run starts over from week 1 with a fresh project (deleting the previous one takes
about a minute and a half), so no answer can come from an earlier run's retrieval.

## Or run it in the browser

```bash
python3 web/server.py           # opens http://127.0.0.1:8765
```

Same pipeline, one view per step (a table per checkpoint, click an answer to read the reply), the extra names with the
facts that mention them, a scoreboard, an **Ask** page (your own question, any of the three ways), and a terminal drawer
with every `memorylake` command as it runs.

## What happens, step by step

Excerpts below are from one real run; numbers vary between runs.

### Step 2: one project, one boundary

```
$ memorylake proj create --name 'Maya'"'"'s assistant memory' --custom-id mlu-ltm-memory …
$ memorylake boundary create --name 'Maya'"'"'s assistant memory' --custom-id mlu-ltm-boundary --projects proj-324b… --workspace ws-…
```

The facts are stored verbatim with `fact add` (up to 50 per call). There are no conversations, so nothing is
extracted and no fact is rewritten. The 400 facts come from `data/make_memory.py` (fixed seed): 6 personal details,
60 contacts with a relation and a birthday, and 334 everyday facts (project deadlines, client notes, restaurants,
repairs, books, trips, rates).

### Step 3: week 1, 20 facts

```
$ memorylake router chat 'What'"'"'s the code for my studio door?' --model gpt-4o-mini … --system '…'
$ memorylake router chat 'What'"'"'s the code for my studio door?' --model gpt-4o-mini … --body out/everything-week1.json
$ memorylake router chat 'What'"'"'s the code for my studio door?' --model gpt-4o-mini … --system '…' --boundary bnd-04ba…

    question            asked with          memory in prompt   time   answer
    Studio door code    no memory                          —   1.4s   ✗
                        everything pasted            395 tok   1.3s   ✓
                        MemoryRouter                  88 tok   4.4s   ✓
    …
    November birthdays  no memory                          —   2.0s   ✗ 0/1
                        everything pasted            395 tok   1.2s   ✓ 1/1
                        MemoryRouter                  83 tok   4.5s   ✓ 1/1
```

*Memory in prompt* is the provider's `prompt_tokens` minus the same question with no memory: what the memory cost on
that call. The response does not say which memories MemoryRouter attached; this difference is the visible part.

### Step 4: month 3, 100 facts

```
    March birthdays     no memory                          —   2.4s   ✗ 0/5
                        everything pasted          1,822 tok   1.4s   ✗ 3/5  + Gideon Galloway, Della Lund
                        MemoryRouter                 158 tok   4.8s   ✓ 5/5
  · memory in prompt = prompt tokens minus the same question with no memory. Pasted: 1,822 tokens on every question. MemoryRouter attached 78–158 tokens.
```

### Step 5: month 9, 400 facts

```
    Studio door code    no memory                          —   1.6s   ✗
                        everything pasted          6,911 tok   1.4s   ✓
                        MemoryRouter                  88 tok   4.8s   ✓
    …
    November birthdays  no memory                          —   1.9s   ✗ 0/5
                        everything pasted          6,911 tok   1.9s   ✗ 4/5  + Bea Nakata, Farid Rinaldi, Zora Moreau, Juno Moreau
                        MemoryRouter                 155 tok   5.0s   ✓ 5/5
    March birthdays     no memory                          —   4.0s   ✗ 0/5
                        everything pasted          6,911 tok   1.9s   ✗ 4/5  + Dmitri Castellan, Gideon Galloway, Della Castellan
                        MemoryRouter                 158 tok   5.0s   ✓ 5/5
```

The look-ups stay right in every way that has the memory. The lists are where the pasted prompt goes wrong.

### Step 6: where the extra names came from

```
  Month 9 · everything pasted · November birthdays: the reply said “5. Bea Nakata - November 8”
$ memorylake fact list --projects proj-324b… --query 'Bea Nakata' --page-size 50 --workspace ws-…
      memory: Contact: Bea Nakata is Maya's former colleague; birthday May 8.
      memory: Maya's window latch was fixed by Bea Nakata in January.
      memory: Maya bills Bea Nakata at 80 euros an hour for landscape work.
  · stored birthday: May 8
```

`fact list --query` is an exact substring match over the stored facts, so it shows everything memory says about that
name. The memory has the right date.

### Step 7: scoreboard

```
    memory             pasted, tokens  MemoryRouter, tokens        exact answers        extra names
                         per question          per question      pasted · router    pasted · router
    Week 1 (20)                   395                 78–97            4/4 · 4/4              0 · 0
    Month 3 (100)               1,822                78–158            3/4 · 4/4              2 · 0
    Month 9 (400)               6,911                78–158            2/4 · 4/4              7 · 0
  · median seconds per call (CLI round trip included): no memory 1.9s · everything pasted 1.4s · MemoryRouter 4.7s

  Same boundary, different model and API: claude-haiku-4-5 (Anthropic Messages)
$ memorylake router messages 'List everyone whose birthday is in November.' --model claude-haiku-4-5 … --boundary bnd-04ba…
      │ Here are the people with November birthdays:
      │ 1. Greta Pereira - November 2
      │ …
  · November birthdays: ✓ 5/5
```

## Measured

Seven fresh runs of the final version on 2026-10-15: six from the terminal (three in parallel under separate prefixes,
then three in a row, one of them `--reset`) and one in the browser. Temperature 0; replies still vary. Every run
started from an empty project.

Memory in each prompt was the same in every run: pasted 395 / 1,822 / 6,911 tokens per question at 20 / 100 / 400
facts; MemoryRouter 78–97 / 78–158 / 78–158.

Exact answers, 6 terminal runs (lists: extra names · missed names in total):

| Memory | Question | no memory | everything pasted | MemoryRouter |
|---|---|---|---|---|
| Week 1 (20) | 2 look-ups | 0 / 12 | 12 / 12 | 12 / 12 |
| | 2 lists | 0 / 12 | 12 / 12 | 12 / 12 |
| Month 3 (100) | 2 look-ups | 0 / 12 | 12 / 12 | 12 / 12 |
| | 2 lists | 0 / 12 | **3 / 12** (16 extra · 8 missed) | 12 / 12 |
| Month 9 (400) | 2 look-ups | 0 / 12 | 12 / 12 | 12 / 12 |
| | 2 lists | 0 / 12 | **0 / 12** (43 extra · 21 missed) | 12 / 12 |

The browser run: pasted exact 4/4 · 3/4 · 2/4 with 0 · 2 · 7 extra names; MemoryRouter 4/4 at every point, none
extra. Claude through the same boundary (step 7) listed the five November birthdays exactly in all 7 runs.

What the numbers say, and don't:

- **The cost of pasting grows with the memory; MemoryRouter's doesn't.** At 400 facts a pasted prompt carries about
  44 times the memory MemoryRouter attaches for the same question. The attached amount depends on the question (more
  for a five-name list than for a door code), not on how much is stored.
- **The pasted prompt fails on lists, not look-ups.** One fact answers "what is my door code"; a list has to be
  assembled from many lines, and that is where neighbouring dates and names get mixed in. The extra names are real
  contacts, with real dates moved to another month, which is what makes them hard to spot.
- **MemoryRouter is slower per call here**: a median of about 4.7 s against 1.4 s for the pasted prompt and 1.9 s for
  no memory, measured from the CLI (retrieval and one more network hop included).
- **Relevance retrieval attaches a handful of memories.** It covered the five-name lists here; a list of fifty would
  need a deterministic listing instead (see below).
- One model (`gpt-4o-mini`) was measured for the three-way comparison; a larger model may mix up fewer lines, and it
  still pays for every pasted token.

## Use it in your own app

- Store what your app learns about a user as facts (`fact add`, or let MemoryLake extract them from conversations),
  in a project per user or per app, and create one boundary over it.
- Keep your model call as it is and add the boundary: `--boundary <id>` on the CLI, or `?boundary_id=` on the
  MemoryRouter endpoint your OpenAI or Anthropic SDK points at. Don't paste the memory into the prompt.
- To see what memory costs on a call, compare `usage.prompt_tokens` with and without the boundary, as step 3 does.
- For questions that must enumerate everything of a kind ("all my contacts in Porto"), a deterministic listing
  (`fact list --query`) is the safe path; relevance retrieval attaches a handful of memories, which covered the five
  names here but is not a guarantee for a list of fifty.

## Troubleshooting

- **`this memorylake CLI has no router command`**: upgrade the CLI (install line above).
- **MemoryRouter refused the call for lack of balance**: top up in the console; `memorylake billing balance` shows it.
- **A run was interrupted**: run again; it deletes the previous project and starts over from week 1.
- **`HTTP 502` while deleting**: deletes are retried; if one still fails, run `python3 demo.py cleanup` again.

## Files

| File | What it is |
|---|---|
| `demo.py` | The demo: CLI wrapper, the grader, the pipeline, subcommands |
| `data/memory.json` | The 400 facts in the order the assistant learned them, the three checkpoints, the contact names |
| `data/make_memory.py` | Regenerates `memory.json` (fixed seed) |
| `data/questions.json` | The four questions and what a right answer contains |
| `web/server.py`, `web/static/` | Browser companion (standard library server, no build step) |
