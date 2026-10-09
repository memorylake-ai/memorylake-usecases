# Reproduce an AI agent's bug by replaying its memory

A runnable version of the MemoryLake use case
[*Reproducing Agent Bugs Through Memory Replay*](https://www.memorylake.ai/en/usecase/reproducing-agent-bugs-through-memory-replay),
built entirely on the [`memorylake` CLI](https://github.com/memorylake-ai/memorylake-cli).

**The problem.** An agent with long-term memory misbehaves on Tuesday. By Friday, when an engineer
picks up the ticket, the memory has changed: new conversations rewrote facts and someone fixed things
by hand. Asking the agent the same question again gives a different answer. So the bug "does not
reproduce", and nobody can say what the agent actually knew when it made the call.

**What this demo shows.** Quillstone (fictional) makes lab label printers. Its support agent, **Quill**,
keeps one MemoryLake memory per customer account. Lena Fischer of Brightmoor Labs talked to Quill four
times:

| date | chat | what was said |
|---|---|---|
| Sep 20 | First report | the QL-40 jams with E-41; "ship any hardware to our Lyon office, 22 Quai Perrache" |
| Oct 1 | Moving notice | "we are moving out of Lyon; keep shipping there until the end of October" |
| **Oct 3** | **The bug** | the printer jams again. Quill calls its `memory_search` tool and ships a replacement to Lyon |
| Oct 5 | Complaint | "the Lyon office closed on October 2; ship only to Grenoble, 9 Rue Felix Viallet" |

After the complaint, the support lead corrects the memory by hand. A support engineer picks up the
ticket today:

- **The tool call is part of the record.** On Oct 3 the agent's search really runs at that point in the
  story. The call and the facts it got back (ids and text) are stored in the conversation as
  **`TOOL_USE` / `TOOL_RESULT`** blocks.
- **Asking again does not reproduce it.** The engineer re-runs the agent's exact tool call against
  today's memory. Then it shipped to **22 Quai Perrache, Lyon**; today the same call says **9 Rue Felix
  Viallet, Grenoble**. Of the 6 facts the agent saw, 3 have been rewritten since.
- **Pin the memory to the exact message.** **`fact trace`** gives every fact's full history: `ADD`,
  `UPDATE` and `FORGET`, each one either `COOK` (with its conversation, message ids and `cookrun-…`) or
  `MANUAL`. **`conv fact-actions`** gives the facts each conversation touched, so forgotten facts are
  found too. Each history entry is dated by the **messages it came from**, and the demo keeps each fact's
  last change at or before the `TOOL_USE` message. Result: **6/6** facts identical, word for word, to what
  the agent recorded at the time.
- **The memory diff, with a source on every line.** It lists every change after the call: the Oct 3 chat's
  own extraction (two minutes *after* the call, so not in the replay), the Oct 5 complaint, and the
  manual `fact delete` / `fact update`. Each line names its conversation and message, or "MANUAL via API".
- **An audit record.** `out/replay-call-ship-0001.json` keeps what was replayed, at which message, from
  which history entries, and the verdict. Nothing is written back to memory.

The verdict, read off the data: at the moment of the call the memory said Lyon, and the agent shipped
exactly there. The Grenoble address first reached memory on Oct 5. **The agent is not at fault; the
process is.**

```
 Sep 20 ─ Oct 1 ─ Oct 3 ──────────────── Oct 5 ─ today
  chat    chat    chat: TOOL_USE memory_search → TOOL_RESULT {fact ids + text}
                        │                              fact delete / fact update (MANUAL)
                        ▼
   today:   search again ─────────────────▶ different answer (Grenoble)
   replay:  conv fact-actions → every fact id
            fact trace → ADD / UPDATE / FORGET per fact
            source_entry_ids → conv msg list → message time ─▶ memory at the TOOL_USE message
                                                              = TOOL_RESULT, 6/6 ✓ (Lyon)
            changes after that moment ─▶ diff + out/replay-call-ship-0001.json
```

**Watch it run** (real recordings, unedited): *recording in progress*

## Prerequisites

1. **A MemoryLake account and API key.** Sign up at [app.memorylake.ai](https://app.memorylake.ai),
   open **API Keys**, create a key and copy it. Details: [Authentication and API Keys](https://docs.memorylake.ai/authentication).
   A free personal account is enough.
2. **The `memorylake` CLI, v20261009 or newer.** The fact-history commands (`fact trace`,
   `conv fact-actions`, `fact update`) arrived in v20261009; the demo checks for them and stops with an
   upgrade hint otherwise.
   ```bash
   curl -fsSL https://raw.githubusercontent.com/memorylake-ai/memorylake-cli/main/scripts/install.sh | sh
   memorylake version
   ```
3. **Python 3.9+.** Standard library only, nothing to `pip install`.

## Run it

```bash
git clone https://github.com/memorylake-ai/memorylake-usecases.git
cd memorylake-usecases/agent-bug-replay

export MEMORYLAKE_API_KEY=sk-…
python3 demo.py
```

About 5 minutes. Most of it is MemoryLake updating the memory after each chat (35–55 s each). The
chats go in one at a time, because each one has to see the memory the previous one left behind.

With `MEMORYLAKE_API_KEY` set, the demo logs into its own CLI profile under `./.memorylake-demo/`
(git-ignored) and leaves `~/.memorylake` alone. Without it, it uses your existing `memorylake auth login`.

| | |
|---|---|
| `MEMORYLAKE_BASE_URL` | API endpoint; default global, China is `https://app.memorylake.cn/openapi/memorylake` |
| `MEMORYLAKE_WORKSPACE` | Workspace id; default is the one remembered by `ws use`, else your default workspace |
| `python3 demo.py --reset` | Delete the conversations, the project and the demo actors first, then start over |
| `python3 demo.py replay` | Steps 5–7 only: re-run the tool call today, pin memory to the call, diff, audit record |
| `python3 demo.py facts` | Print what the account's memory holds now |
| `python3 demo.py cleanup` | Delete the conversations, the project (with its facts) and the actors (with theirs) |

Safe to re-run: everything is found again by `custom_id` (prefix `mlu-abr-`). Conversations resume
from the last stored message. The recorded tool result is never re-recorded. The manual fixes in step 4
find nothing left to do on a second run, and the replay comes out the same.

## Or run it in the browser

```bash
python3 web/server.py        # http://127.0.0.1:8765 — standard library only
```

Paste the key and press **Run demo**. As it runs you see:

- the four chats fill in message by message, with the agent's `TOOL_USE` and the facts in its
  `TOOL_RESULT`, and under each chat the `conv fact-actions` counts (ADD / UPDATE / FORGET);
- the manual fixes;
- **then vs today** side by side: the address the agent used, the address the same call gives today, and
  which of the facts it saw were rewritten or forgotten since;
- the replay: replayed vs recorded, fact by fact, and the full `fact trace` of the fact the agent shipped
  by;
- the diff with its sources and the verdict, and a button to download the audit record.

**Replay only** re-runs steps 5–7 on the existing data. **Ask the agent's memory** searches the
scope the agent's tool uses (today's memory). The terminal drawer shows every `memorylake` command as it
runs.

## What happens, step by step

Ids below are shortened; the real ones are printed as the demo runs.

### 1. Connect

`auth login` into the isolated profile, `team get`, pick the workspace. Then the demo checks that
`fact trace`, `conv fact-actions` and `fact update` exist in your CLI.

### 2. Set up — one project for the account, the customer and the agent as actors

```bash
memorylake proj create --name 'Brightmoor Labs — support account' --custom-id mlu-abr-brightmoor --description '…' --workspace ws-…
memorylake actor create --custom-id mlu-abr-lena --display-name 'Lena Fischer' --tags customer:brightmoor --description 'Operations lead, Brightmoor Labs'
memorylake actor create --custom-id mlu-abr-quill --display-name Quill --tags quillstone,ai --description 'Quillstone support agent (AI)'
memorylake actor bind --actor actor-… --workspace ws-…
```

MemoryLake files what it extracts from these chats partly on the **project** and partly on **Lena's
actor**. So the agent's tool, and the replay, always read both scopes.

### 3. Four support chats, one at a time — the agent's tool call recorded as it happened

Each chat is a DIRECT conversation in the project. Messages carry their real dates (`--timestamp`),
and the last one waits for the memory update (`--wait`):

```bash
memorylake conv create --custom-id mlu-abr-2026-09-20 --project proj-… --actors actor-…,actor-… --kind DIRECT --name 'Support chat — QL-40 jams with E-41' --metadata account=brightmoor --workspace ws-…
memorylake conv msg append conv-… --actor actor-… --custom-id turn-03 --timestamp 2026-09-20T09:03:00Z --text 'Lena Fischer (Operations lead, Brightmoor Labs): Ship any hardware to our Lyon office: Brightmoor Labs, 22 Quai Perrache, 69002 Lyon. We are on the Team plan, billed annually.' --parent conv-entry-… --workspace ws-…
memorylake conv fact-actions conv-… --project proj-… --page-size 100 --workspace ws-…
```

On Oct 3 the agent's tool call goes in as content blocks. Between the two, its search really runs:

```bash
memorylake conv msg append conv-… --actor actor-… --custom-id turn-02 --timestamp 2026-10-03T09:02:00Z --content-json '[{"block_type": "TOOL_USE", "tool_call_id": "call-ship-0001", "tool_name": "memory_search", "arguments": {"query": "where to ship replacement hardware for Brightmoor Labs"}}]' …
memorylake search 'where to ship replacement hardware for Brightmoor Labs' --projects proj-… --actors actor-… --types fact --top-k 6 --workspace ws-…
memorylake conv msg append conv-… --actor actor-… --custom-id turn-03 --timestamp 2026-10-03T09:03:00Z --content-json '[{"block_type": "TOOL_RESULT", "tool_call_id": "call-ship-0001", "result": {"facts": [{"id": "fact-…", "fact": "…"}, …]}}]' …
```

From our run:

```
  ▸ 2026-09-20 · First report
   memory changes from this conversation (conv fact-actions): 6 ADD
  ▸ 2026-10-01 · Moving notice
   memory changes from this conversation (conv fact-actions): 2 ADD, 1 UPDATE
  ▸ 2026-10-03 · Replacement shipped (the bug)
   Quill calls memory_search("where to ship replacement hardware for Brightmoor Labs")  [call-ship-0001]
   the tool returned 6 fact(s); stored verbatim in the TOOL_RESULT:
     ec4274ca  Shipments should be sent to the Lyon office at 22 Quai Perrache, Lyon until the end of October (as of 2026-10-01, previously Hardware should be shipped to Brightmoor Labs, 22 Quai Perrache, 69002 Lyon as of 2026-09-20)
     f314e0fc  The Lyon office at 22 Quai Perrache closes at the end of October (as of 2026-10-01)
     …
   Quill: Done: a replacement QL-40 ships today to 22 Quai Perrache, 69002 Lyon. Tracking number QS-TRK-55102.
   memory changes from this conversation (conv fact-actions): 1 ADD, 1 UPDATE
  ▸ 2026-10-05 · Complaint
   memory changes from this conversation (conv fact-actions): 1 ADD, 1 UPDATE
```

The agent's reply is not scripted: the demo fills in the address from the first fact in its tool
result that names a street.

### 4. Today, after the complaint — memory corrected by hand

```bash
memorylake fact delete --project proj-… fact-… --workspace ws-…
memorylake fact update fact-… --project proj-… --text 'Brightmoor Labs is on the Team plan and is billed monthly (as of 2026-09-20)' --workspace ws-…
```

```
  The Lyon address must never be used again: 1 fact(s) still say to ship there.
   ✗ forgot 546bdf09  A replacement QL-40 ships to 22 Quai Perrache, 69002 Lyon with tracking number QS-TRK-55102 (on 2026-10-03).
  Finance moved Brightmoor to monthly billing last week; fixing that by hand too.
   ✎ f87a0ec4  Brightmoor Labs is on the Team plan and is billed annually (as of 2026-09-20)
        → Brightmoor Labs is on the Team plan and is billed monthly (as of 2026-09-20)
```

Both are recorded as `MANUAL` in the facts' history.

### 5. Reproduce it today — the agent's own tool call, re-run

The demo reads the `TOOL_USE` block back (`conv msg list`) and runs the very same search:

```
   then (recorded in the TOOL_RESULT): ships to 22 Quai Perrache, 69002 Lyon
   today (same call):                  ships to 9 Rue Felix Viallet, 38000 Grenoble
   of the 6 fact(s) the agent saw: 3 unchanged, 3 rewritten, 0 forgotten
   → asking the agent again does not reproduce the bug: its memory has moved on.
```

### 6. Pin memory to the moment of the call — fact trace, dated by message

The demo collects every fact id the account ever had. `fact list` gives the live ones, and
`conv fact-actions` (per conversation, per scope) adds the forgotten ones. Then it traces each fact:

```bash
memorylake fact trace fact-… --project proj-… --workspace ws-…
```

A trace entry's own `timestamp` is when the **server** processed the chat, not when the chat happened.
So each `COOK` entry is dated by the newest of its `source_entry_ids`, looked up in `conv msg list`; a
`MANUAL` entry keeps its API call time. The memory at `2026-10-03T09:02:00Z` (the `TOOL_USE` message) is
each fact's last change at or before that moment:

```
  Replayed vs recorded — the 6 fact(s) in the agent's TOOL_RESULT:
   ✓ ec4274ca  Shipments should be sent to the Lyon office at 22 Quai Perrache, Lyon until the end of October (…)
   ✓ f314e0fc  The Lyon office at 22 Quai Perrache closes at the end of October (as of 2026-10-01)
   …
   6/6 identical → the replay reproduces what the agent saw
   address on file at 2026-10-03 09:02: 22 Quai Perrache, 69002 Lyon

  The fact the agent shipped by, ec4274ca — full history (fact trace), oldest first:
   2026-09-20 09:04  ADD    COOK   First report (2026-09-20), msg 4  cookrun-a26e…
        Hardware should be shipped to Brightmoor Labs, 22 Quai Perrache, 69002 Lyon (as of 2026-09-20)
   2026-10-01 09:02  UPDATE COOK   Moving notice (2026-10-01), msg 2  cookrun-3a13…
        Shipments should be sent to the Lyon office at 22 Quai Perrache, Lyon until the end of October (…)
   2026-10-05 09:02  UPDATE COOK   Complaint (2026-10-05), msg 2  cookrun-df7d…
        Replacement shipments must be sent only to Brightmoor Labs, 9 Rue Felix Viallet, 38000 Grenoble (…)
```

One fact id, three versions: Lyon at the call, Grenoble now. The extractor does not always merge like
this. In other runs the Grenoble address arrived as a **new** fact and the Lyon one was forgotten by hand
in step 4. The replay handles both: a forgotten fact is still traced, and still in the memory at the call.

### 7. Memory diff, then vs now, and the audit record

```
  What changed in the account's memory after 2026-10-03 09:02 — 6 change(s):
   ~ UPDATE e0c6c52a  [project]  2026-10-03 09:04  ← Replacement shipped (the bug) (2026-10-03), msg 4 · cookrun-f68c…
   + ADD    546bdf09  [project]  2026-10-03 09:04  ← Replacement shipped (the bug) (2026-10-03), msg 4 · cookrun-f68c…
   ~ UPDATE ec4274ca  [project]  2026-10-05 09:02  ← Complaint (2026-10-05), msg 2 · cookrun-df7d…
   + ADD    5e92c2e7  [project]  2026-10-05 09:02  ← Complaint (2026-10-05), msg 2 · cookrun-df7d…
   - FORGET 546bdf09  [project]  2026-10-09 13:34 (API call time)  ← MANUAL via API · api-ca8f…
   ~ UPDATE f87a0ec4  [project]  2026-10-09 13:34 (API call time)  ← MANUAL via API · api-6004…

  Verdict: Not an agent bug: at the moment of the call the memory said 22 Quai Perrache, 69002 Lyon, and
  the agent shipped exactly there. The Grenoble address first reached memory on 2026-10-05 (Complaint
  (2026-10-05), msg 2). Fix the process, not the agent: confirm the address before shipping when a move is pending.
```

The first two lines come from the bug conversation itself, two minutes after the tool call. Pinning to
the message, not the day, is what keeps them out of the replay. The audit record
`out/replay-call-ship-0001.json` holds the pinned message and tool call, recorded vs replayed for each
fact, the memory at that moment, every change since (with conversation id, message ids and
`cookrun`/`api` event id), and the verdict.

## Wiring it into your own agent

- **Store tool calls as content blocks.** When your agent framework calls a memory tool, append a
  `TOOL_USE` (`tool_call_id`, `tool_name`, `arguments`) and a `TOOL_RESULT` (`tool_call_id`, `result`)
  carrying the **fact ids** it got back. They cost nothing to keep, and they are what a replay is checked
  against.
- **Send real message times** (`--timestamp`). The replay dates every history entry by its messages.
- **Replay = trace + filter.** Get fact ids from `conv fact-actions` (forgotten ones included) and
  `fact list`; `fact trace` each one; keep each fact's last entry at or before the moment you care about.
  A `FORGET` there means the fact was gone at that moment.
- **Correct memory with `fact update` / `fact delete`, not by editing around it.** Manual changes show up
  in the same history, marked `MANUAL`.

## If something goes wrong

- **“this demo needs `memorylake fact trace`”**: your CLI is older than v20261009. Re-run the install
  command above.
- **“this CLI cannot set an actor type”**: CLI v20261009 removed `actor create --type`, so Quill is created
  as an ordinary (HUMAN) actor and picks up a couple of facts about itself. The replay only reads the
  project and Lena, so it is not affected.
- **The extracted facts differ between runs.** Extraction is server-side and varies. In our runs the
  account had 10 live facts each time, but the address arrived either as one fact rewritten in place
  (Lyon → Grenoble) or as a new Grenoble fact next to the Lyon one. The replay and the 6/6 check do not
  depend on which.
- **“nothing left to forget” on a re-run**: the manual fixes were applied by the first run. They stay
  in the history, so the diff still shows them.
- **`tls handshake eof` / `could not connect`** while polling: a dropped connection. Read-only commands
  retry four times; otherwise re-run `python3 demo.py` and it resumes.

## Files

```
demo.py                    the runner; prints every CLI command it runs (and emits events for the web app)
data/sessions/*.json       the four support chats (the Oct 3 one holds the tool call)
web/server.py              local web companion (stdlib HTTP server + Server-Sent Events over demo.py)
web/static/                the page — no build step
out/                       the replay audit record (git-ignored)
```

All names, companies and figures are fictional.
