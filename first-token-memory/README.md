# First token memory: stream answers fast — a retrieval deadline quietly turns memory off

A runnable version of the MemoryLake use case
[*Support Streaming Agent Responses Without Sacrificing Memory Retrieval*](https://www.memorylake.ai/en/usecase/memory-for-streaming-agent-responses),
built entirely on the [`memorylake` CLI](https://github.com/memorylake-ai/memorylake-cli) (v20261015 or later).

**Watch it run:** [CLI, 2:49](https://github.com/memorylake-ai/memorylake-usecases/releases/download/first-token-memory-v1/first-token-memory-cli-demo.mp4) · [Web, 2:36](https://github.com/memorylake-ai/memorylake-usecases/releases/download/first-token-memory-v1/first-token-memory-web-demo.mp4)
([release](https://github.com/memorylake-ai/memorylake-usecases/releases/tag/first-token-memory-v1); GitHub serves them as downloads). Each video is one real run.

**The problem.** A streaming assistant feels fast only if the first token shows quickly. Memory retrieval sits in
front of that first token. Three common ways to wire it, measured here on the same six questions:

- **Retrieve, then stream** (`router chat --stream --boundary`: MemoryRouter looks the memory up, then the model
  starts). The answers use the memory, but the first token waits for the lookup: **4.7–5.4 s median** over five runs,
  against about 1 s for the same call without memory.
- **Give retrieval a deadline** (`search` gets 1.0 s; when it misses, stream without memory and let the search
  finish). The first token is always on time. **The answers are the no-memory answers**: `npm install date-fns` for
  a pnpm user, `FROM node:18` for a Node 20 service, `heroku ps:scale` for a Fly.io team. Nothing errors. The search
  comes back 2.6–4.6 s after the question, holding the exact fact the answer needed, and is thrown away. On a
  latency dashboard this way looks perfect: 6/6 on time, 0/6 using memory, half of them contradicting it.
- **Prefetch at session open** (`fact list --actors` once when the chat opens, 0.9–2.2 s in these runs, while the user is
  still typing; every answer streams with that profile in the prompt). First token about 1 s, memory used 6/6.

**What this demo shows.** Tallow Labs (fictional) puts a coding assistant, Quill, in Priya's editor. Her memory is
eight facts on her actor: pnpm, Node 20, Fly.io (ams), Postgres 15 on Neon, Vitest, tabs, Hono, Drizzle. Six
everyday questions go through the three ways, one after another, and every first token is timed against the team's
rule: **first token within 2.5 s**. An answer counts as *using memory* when it names what Priya's memory says, and
as *contradicting* it when it names something else instead (npm, Jest, Vercel, Prisma, Express, another Node
version). For the deadline way, the demo prints the signal that way never logs: the deadline missed, when the search
came back, and whether it held the fact the answer needed.

The whole run takes about 70 seconds and makes 18 short model calls (gpt-4o-mini through MemoryRouter, at most 110
output tokens each). Everything it creates is prefixed `mlu-ftk-` and removed by `cleanup`.

## Prerequisites

- **Python 3.9+** — standard library only, nothing to `pip install`
- **The `memorylake` CLI, v20261015 or later** (it needs `router chat --stream --body`):

  ```bash
  curl -fsSL https://raw.githubusercontent.com/memorylake-ai/memorylake-cli/main/scripts/install.sh | sh
  ```

- **A MemoryLake API key** — sign up at [app.memorylake.ai](https://app.memorylake.ai), open **API Keys**, create
  one. The model calls are billed to your MemoryLake balance; if it has none, MemoryRouter answers 402 and the demo
  stops with that message.

## Run it

```bash
git clone https://github.com/memorylake-ai/memorylake-usecases.git
cd memorylake-usecases/first-token-memory
export MEMORYLAKE_API_KEY=sk-...          # optional: MEMORYLAKE_BASE_URL, MEMORYLAKE_WORKSPACE
python3 demo.py                           # the full demo, about 70 s
python3 demo.py latency                   # time each way's read five times
python3 demo.py cleanup                   # delete the boundary, the facts and the actor
```

With `MEMORYLAKE_API_KEY` set, the demo logs into an isolated CLI profile in `./.memorylake-demo/` (git-ignored)
and never touches your own `~/.memorylake`. Without it, it uses your existing `memorylake auth login` session. For
the China endpoint, set `MEMORYLAKE_BASE_URL=https://app.memorylake.cn/openapi/memorylake`.

Every run starts over (`--reset` is accepted, for symmetry with the other demos): if an earlier run left the demo
memory behind, it is cleaned up first.

## Or run it in the browser

```bash
python3 web/server.py                     # opens http://127.0.0.1:8765
```

[Watch the browser run (2:36)](https://github.com/memorylake-ai/memorylake-usecases/releases/download/first-token-memory-v1/first-token-memory-web-demo.mp4). Paste the API key, press **Run demo**, and watch the three ways answer the same six questions: each answer streams
into its card, with a first-token bar against the 2.5 s budget, and the deadline way shows when its search came back
and what it held. Step 6 scores them side by side. The page drives the same `demo.py` (it imports it); the
**Terminal** drawer at the bottom shows every `memorylake` command as it runs. **Clean up** removes everything.

## What happens, step by step

All output below is from a real run (run 2 in [Measured](#measured)).

### Step 2: memory

```
$ memorylake actor create --custom-id mlu-ftk-priya --display-name 'Priya Raman' --description '…' --tags demo
$ memorylake actor bind --actor actor-… --workspace ws-…
$ memorylake fact add 'Priya uses pnpm for every package install; never suggest npm or yarn commands.' … --actor actor-… --workspace ws-…
$ memorylake boundary create --name 'Quill · Priya Raman' --custom-id mlu-ftk-boundary --human-actor actor-… --workspace ws-…

    key      fact
    pkg      Priya uses pnpm for every package install; never suggest npm or yarn commands.
    node     Priya's service runs on Node 20 LTS.
    deploy   Priya's team deploys every service to Fly.io in the ams region with flyctl.
    db       Priya's database is Postgres 15 hosted on Neon.
    test     Priya writes tests with Vitest, not Jest.
    indent   Priya's repo uses tabs for indentation.
    api      Priya's API is built with the Hono framework.
    orm      Priya's schema and migrations are managed with Drizzle ORM.
```

Pinned facts, no conversations: nothing here waits for extraction. The boundary is what arm A hands MemoryRouter.

### Step 3: A — retrieve, then stream

```
  Q1  “Add date-fns to the project. What's the command?”
$ memorylake router chat 'Add date-fns to the project. What'"'"'s the command?' --model gpt-4o-mini --max-tokens 110 --temperature 0 --stream --system '…' --boundary bnd-…
    │ pnpm add date-fns
    first token 5.69 s ✗ (budget 2.5 s) · done 5.81 s · ✓ used memory
```

MemoryRouter finds the right facts (5–6 of 6 answers use them), but the lookup happens before the model's first
token. Every first token in A is over the budget.

### Step 4: B — a 1.0 s deadline on retrieval

```
  Q1  “Add date-fns to the project. What's the command?”
$ memorylake search 'Add date-fns to the project. What'"'"'s the command?' --actors actor-… --types fact --top-k 5 --workspace ws-…
$ memorylake router chat 'Add date-fns to the project. What'"'"'s the command?' --model gpt-4o-mini --max-tokens 110 --temperature 0 --stream --body out/body-B.json
    │ npm install date-fns
    first token 2.20 s ✓ (budget 2.5 s) · done 2.28 s · ✗ contradicts memory — memory says pnpm
    the search came back at 2.66 s, after the answer had streamed, holding “Priya uses pnpm for every package install; never suggest npm or yarn commands.” — the deadline had already decided: discarded
```

`search` takes about 3 s here, so the 1.0 s deadline is missed on every question and every answer streams without
memory. The first token is on time (the 1.0 s wait plus about 1 s for the model). The search is not wrong and not
empty: it comes back with the right fact every time — after the answer has already streamed. Nothing in this
path raises an error; without the last line above, nobody would know.

### Step 5: C — prefetch the profile when the chat opens

```
$ memorylake fact list --actors actor-… --page-size 200 --workspace ws-…
    prefetched 8 facts in 0.90 s — before the first question

  Q1  “Add date-fns to the project. What's the command?”
$ memorylake router chat 'Add date-fns to the project. What'"'"'s the command?' --model gpt-4o-mini --max-tokens 110 --temperature 0 --stream --body out/body-C.json
    │ pnpm add date-fns
    first token 1.02 s ✓ (budget 2.5 s) · done 1.04 s · ✓ used memory
```

The profile read happens once, when the chat opens — in a real editor, while Priya is still typing her first
question. After that, each answer is one model call with the profile in its system prompt (`--body`), so the first
token is as fast as with no memory at all.

### Step 6: score

```
                                   first token: median · max   within 2.5 s used memory  contradicted it
    A  retrieve, then stream       5.38 s · 6.78 s             0/6         5/6          0/6
    B  1 s deadline                2.03 s · 2.25 s             6/6         0/6          3/6
    C  prefetch at session open    0.88 s · 1.03 s             6/6         6/6          0/6
  · B missed its 1.0 s deadline on 6/6 questions; on 6 of them the search came back holding the fact the answer needed — and nothing raised an error
  · C paid 0.90 s once, when the chat opened — not on any answer
```

### `python3 demo.py latency`

Times each read five times, plus two routed answers with and without memory:

```
  search --actors (top 5 facts)      2.94  2.94  2.95  2.87  3.07   s
  fact list --actors (whole profile)  0.86  0.63  0.96  0.73  1.41   s
  router chat --stream no memory     first token 1.51  1.00   s
  router chat --stream --boundary    first token 6.12  4.49   s
  · times are measured around each CLI call, so they include starting the CLI process
```

## Measured

Times are wall-clock around each CLI call, from this machine to app.memorylake.ai, so they include starting the CLI
process and the network; yours will differ, the ordering should not.

| run | A first token (median) · used memory | B first token (median) · used · contradicted | C prefetch · first token (median) · used |
|---|---|---|---|
| 2 | 5.38 s · 5/6 | 2.03 s · 0/6 · 3/6 | 0.90 s · 0.88 s · 6/6 |
| 3 (`--reset`) | 4.87 s · 6/6 | 2.01 s · 0/6 · 3/6 | 0.94 s · 1.01 s · 6/6 |
| web check | 4.89 s · 6/6 | 2.08 s · 0/6 · 3/6 | 1.01 s · 0.89 s · 6/6 |
| CLI video | 4.71 s · 6/6 | 1.91 s · 0/6 · 3/6 | 1.28 s · 0.94 s · 6/6 |
| web video | 5.19 s · 6/6 | 1.99 s · 0/6 · 3/6 | 2.21 s · 0.91 s · 6/6 |
| **all 5** | **0/30 on time · 29/30 used** | **30/30 on time · 0/30 used · 15/30 contradicted** | **30/30 on time · 30/30 used** |

In every run B missed its deadline on 6/6 questions, and the late search held the needed fact 30/30 times — always
after the answer had finished streaming (2.6–4.6 s after the question). A's one miss (run 2, the health route) was
MemoryRouter's answer naming no framework: retrieval in A is per question and not guaranteed to attach the fact.
The prefetch itself took 0.9–2.2 s; it is paid once, before the first question, not on any answer. (Run 1, not in
the table, used a 2.0 s budget and a different sixth question; B was over that budget on 5/6 and used memory 0/6.)

## Use it in your own agents

- **Don't let a retrieval timeout fall back silently.** If you race memory against a deadline, log the miss, log
  when the result arrived and whether it would have changed the answer — that is the only place this failure is
  visible. A latency SLO alone will show this way as the best of the three.
- **Move the read off the critical path.** A per-user profile of tens of facts is one `fact list --actors` call;
  do it when the session opens (or when the user focuses the input), and refresh it between turns, not inside one.
- **Know where it stops working.** Prefetching the whole profile fits a scope of tens of facts. For a large store
  (thousands of facts, documents), keep a per-question lookup — `search` or MemoryRouter's `--boundary` — and
  budget the first token for it, or stream a short acknowledgement first.

## Troubleshooting

- **`MemoryRouter answered 402`** — the model calls need a MemoryLake balance.
- **`this memorylake CLI has no router chat --stream --body`** — upgrade the CLI (install command above).
- **Different numbers** — expected: they depend on your network and region. If your `search` returns within 1.0 s,
  arm B uses the memory (the demo prints *search back in … s, inside the deadline*), which is the point: whether
  the memory is used depends on a timing you do not see.
- **An answer marked generic** — the grader looks for the word Priya's memory names (pnpm, Vitest, Fly.io, Drizzle,
  Hono, Node 20). A correct answer that does not name it counts as generic, not as used.

## Files

| file | what it is |
|---|---|
| `demo.py` | the demo: CLI wrapper, the three ways, grading, `latency`, `cleanup` |
| `data/story.json` | the eight facts, six questions (with the words that count as using or contradicting memory), budget and deadline |
| `web/server.py` | the browser companion: standard-library HTTP + Server-Sent Events, imports `demo.py` |
| `web/static/` | the page (no build step) |
| `out/` | per-run output (git-ignored): `report.json`, request bodies; `cleanup` keeps reports under `out/runs/` |
