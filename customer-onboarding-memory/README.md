# Customer onboarding memory that survives every handoff

A runnable version of the MemoryLake use case
[*Give SaaS Onboarding Teams Memory That Holds Every Customer Through Activation*](https://www.memorylake.ai/en/usecase/customer-onboarding-memory-for-saas-teams),
built entirely on the [`memorylake` CLI](https://github.com/memorylake-ai/memorylake-cli).

**The problem.** SaaS onboarding passes the customer from sales to kickoff to implementation to training.
Each handoff is a summary that keeps about half of what was said. AI assistants make it worse in one
specific way: what they *found* (a tool call that checked the customer's setup) sits in a chat log
nobody re-reads. So the trainer re-asks discovery questions the customer already answered, and finds out
about the broken SSO setup in front of 30 dispatchers.

**What this demo shows.** Tallyfield (fictional) sells field-service scheduling software. Corvane HVAC
Services (240 technicians, 14 branches) bought it in August. Since then Corvane has talked to:

- **Lena Ortiz, the account executive.** Sales discovery call, Aug 27.
- **Theo Park, the CSM.** Kickoff, Sep 8.
- **Tallyfield's onboarding assistant.** Sep 22: Corvane's IT admin asked it to check the setup, and it
  ran **three tool calls**: `check_sso_config`, `validate_import` and `connect_quickbooks`.

Each human stage left a **handoff note** pinned as a fact. It is now Oct 13, and Ines Calder runs
dispatcher training tomorrow. All three stages live in one customer project:

- **Ask memory before you ask the customer.** Ines's eight discovery questions run as eight searches.
  Memory answers **8 of 8**; the handoff notes alone would have answered **4**. Each answer is tagged with
  its source: a handoff note, or a fact MemoryLake extracted from the sales or kickoff conversation.
- **What the tools found, and nobody passed on.** The assistant's calls are stored as **`TOOL_USE` /
  `TOOL_RESULT` content blocks**. MemoryLake keeps them with the conversation but **does not extract
  them into facts**, so a search about the Okta problem finds only symptoms ("the Okta app has been set
  up"). The demo then:
  - replays the chat and pairs each call with its result by `tool_call_id`;
  - promotes the two that failed into the customer's memory.

  After that, the same searches find them: **0 → 1** hit about the cause, for each of the two.
- **The onboarding timeline from message metadata.** Every message that matters was appended with its
  own `--metadata event=… topic=…`. Listing the three conversations and keeping those messages gives the
  timeline: 11 events (goal, deadline, decision, the two blockers, the QuickBooks win, the admin's
  vacation) with no search involved.

```
sales call ─┐                                    ┌─ handoff notes (fact add --project)
kickoff ────┼── conversations (stage=…) ──▶ project: Corvane HVAC ◀─┤
impl. chat ─┘   messages: --metadata event=…                        └─ promoted tool findings (fact add)
                TOOL_USE / TOOL_RESULT blocks
                        │
          search ──▶ discovery checklist · blockers before/after
          conv msg list ──▶ tool calls paired · event timeline ──▶ out/training-brief-corvane.md
```

## Prerequisites

1. **A MemoryLake account and API key.** Sign up at [app.memorylake.ai](https://app.memorylake.ai),
   open **API Keys**, create a key and copy it. Details: [Authentication and API Keys](https://docs.memorylake.ai/authentication).
   A free personal account is enough.
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
cd memorylake-usecases/customer-onboarding-memory

export MEMORYLAKE_API_KEY=sk-…
python3 demo.py
```

About 4 minutes, most of it MemoryLake extracting the facts of each conversation (35–80 s each).

With `MEMORYLAKE_API_KEY` set, the demo logs into its own CLI profile under `./.memorylake-demo/`
(git-ignored) and leaves `~/.memorylake` alone. Without it, it uses your existing `memorylake auth login`.

| | |
|---|---|
| `MEMORYLAKE_BASE_URL` | API endpoint; default global, China is `https://app.memorylake.cn/openapi/memorylake` |
| `MEMORYLAKE_WORKSPACE` | Workspace id; default is the one remembered by `ws use`, else your default workspace |
| `python3 demo.py --reset` | Delete the conversations, the project and the demo actors first, then start over |
| `python3 demo.py check` | Run the trainer's discovery checklist again |
| `python3 demo.py blockers` | Take back the promoted findings, search, replay the tool calls, promote, search again |
| `python3 demo.py timeline` | Rebuild the timeline from message metadata |
| `python3 demo.py brief` | All three, then rewrite `out/training-brief-corvane.md` |
| `python3 demo.py facts` | Print what the customer's memory holds |
| `python3 demo.py cleanup` | Delete the conversations, the project (with its facts) and the actors (with theirs) |

Safe to re-run: everything is found again by `custom_id` (prefix `mlu-com-`). Conversations resume
from the last stored message, and a handoff note is pinned only by the run that appended its
conversation. Step 5 first takes back the findings an earlier run promoted, so its before/after is the
same every time.

## Or run it in the browser

```bash
python3 web/server.py        # http://127.0.0.1:8765 — standard library only
```

Paste the key and press **Run demo**. As it runs you see:

- the three stages fill in message by message, with the tool calls drawn as `TOOL_USE` / `TOOL_RESULT`
  blocks and every message's `event=` metadata next to it;
- the checklist count: answered from memory, versus covered by the handoff notes alone;
- the tool-findings view: the searches before, the replayed calls, the promoted facts, and the
  searches after;
- the timeline, grouped by day, and the training brief.

**Ask Corvane's memory** searches the same scope for any question, with every hit tagged by stage. The
terminal drawer shows every `memorylake` command as it runs.

## What happens, step by step

**1. Connect.** `auth login` into the isolated profile, `team get`, pick the workspace.

**2. Set up.** One project for the customer:

```bash
memorylake proj create --name "Corvane HVAC Services — onboarding" --custom-id mlu-com-corvane
```

Plus one actor per person. Corvane's people and Tallyfield's people are `HUMAN`, the onboarding
assistant is `ASSISTANT`:

```bash
memorylake actor create --custom-id mlu-com-dana-whitlock --display-name "Dana Whitlock" --type HUMAN \
    --tags customer:corvane,champion --description "Director of Operations, Corvane HVAC"
memorylake actor bind --actor <id>
```

**3. Three stages, one at a time.** Each stage is a conversation in the customer project, tagged with
its stage:

```bash
memorylake conv create --custom-id mlu-com-implementation --project <corvane> \
    --actors <marcus>,<assistant> --metadata customer=corvane --metadata stage=implementation \
    --metadata source=in-app-chat
```

Every message is appended with its historical timestamp and **its own metadata**. A tool call and its
result go in as content blocks:

```bash
memorylake conv msg append <conv> --actor <assistant> --custom-id turn-02 --timestamp 2026-09-22T14:02:00Z \
    --metadata stage=implementation --metadata source=in-app-chat --content-json '[
      {"block_type": "TEXT", "text": "Checking the sign-in setup first."},
      {"block_type": "TOOL_USE", "tool_call_id": "tc-sso-01", "tool_name": "check_sso_config",
       "arguments": {"tenant": "corvane", "idp": "okta"}}]'

memorylake conv msg append <conv> --actor <assistant> --custom-id turn-03 --timestamp 2026-09-22T14:03:00Z \
    --metadata stage=implementation --metadata event=blocker --metadata topic=sso --content-json '[
      {"block_type": "TOOL_RESULT", "tool_call_id": "tc-sso-01",
       "result": {"status": "error", "finding": "The SAML assertion has no email attribute …", "fix": "…"}}]'
```

The last message of each conversation adds `--wait`, so the CLI keeps polling `cook-status` until
MemoryLake has extracted the facts. The next stage starts only then: conversations that cook in
parallel finish in any order. After the sales call and the kickoff, the owner's handoff note is pinned:

```bash
memorylake fact add --project <corvane> "Handoff note (sales, Lena Ortiz, 2026-08-27): Corvane HVAC Services bought 240 technician seats across 14 branches." …
```

**4. The trainer's discovery checklist.** One search per question:

```bash
memorylake search "go-live deadline" --projects <corvane> --actors <dana>,<marcus> --types fact --top-k 5
```

Two details shape the search:

- **The scope.** Some extracted facts land on the people's actors rather than the project, so the scope
  is the union of the project and Corvane's people.
- **The filter.** A search always returns its top-k, so each question has a topic pattern and hits not
  about it are left out (and counted).

Every answer is tagged with where it came from:

- **a handoff note:** the exact text that was pinned;
- **the conversation it was extracted from:** the fact's `(as of …)` date names the stage. That date can
  be the next calendar day for an evening (UTC) conversation, and the demo allows for it.

```
  ✓ Which accounting system?
      [sales · 08-27] Tallyfield needs to connect to QuickBooks Online for invoicing (as of 2026-08-27)
      (4 other hit(s) not about this question, left out)
  …
  8 of 8 discovery answers already in memory: 0 to re-ask. The handoff notes alone covered 4 of 8.
```

**5. What the tools found.** First, the same kind of search for the cause of each problem:

```
   Okta sign-in: 0 hit(s) about the cause
      (5 other hit(s), none about the cause, e.g. “The Okta app for Tallyfield has been set up for Corvane HVAC.”)
```

Then the implementation chat is found by its metadata (`conv list` cannot filter on the server) and
replayed with `conv msg list`. Every `TOOL_USE` is paired with the `TOOL_RESULT` that has the same
`tool_call_id`:

```
   14:02  check_sso_config(tenant=corvane, idp=okta)   [tc-sso-01]
          ✗ error: The SAML assertion has no email attribute and the NameID format is unspecified, …
   14:04  validate_import(file=corvane-technicians.csv)   [tc-imp-01]
          ✗ partial: 37 of 412 technician rows were rejected because column F (EPA 608 certification number) is empty
   14:06  connect_quickbooks(company=Corvane HVAC Services)   [tc-qbo-01]
          ✓ ok: QuickBooks Online connected; 1,284 customers and 312 service items synced
```

The two that did not pass are promoted with `fact add --project`, each with its date, tool name and call
id. The same searches then find them: **0 → 1** hit about the cause, for each.

**6. The timeline.** Each conversation's messages are listed (`conv msg list`). The ones whose metadata
has an `event` are kept and sorted by timestamp. No search and no extraction are involved: the timeline is
exactly what the stages wrote down.

**7. The brief.** `out/training-brief-corvane.md` has four parts: what not to re-ask (with sources), the
open blockers (with fixes and call ids), the timeline, and what to check before tomorrow (Marcus is back
only on Oct 12).

## Wiring it into your own onboarding flow

- **One project per customer, created at closed-won.** Every system that talks to the customer
  (call recorder, kickoff notes, in-app chat, support) appends to a conversation in it. Each conversation
  carries a `stage=` tag, and each message has the time it really happened.
- **Tag the messages you will want back.** `--metadata event=deadline|decision|blocker|…` on the message
  costs nothing and gives you a timeline without a search.
- **Hand tool calls over as they are.** If your assistant runs on an agent framework, append its
  `TOOL_USE` / `TOOL_RESULT` blocks rather than flattening them to text, and the conversation can be
  replayed call by call. But tool results are **not extracted into facts**: promote the ones that matter
  (failures, decisions) with `fact add`, or have the assistant say them in its reply text, which is
  extracted (only in part, in our probes).
- **Before any customer call, search first.** Scope it `--projects <customer> --actors <customer's people>`.

## If something goes wrong

- **`400 tool_call_id is required` / `tool_name is required`**: a `TOOL_USE` block needs `tool_call_id`
  and `tool_name`, and a `TOOL_RESULT` needs `tool_call_id`. A `TOOL_USE` whose input is named `input`
  instead of `arguments` is rejected with a **500**.
- **The reply of `msg append` shows `"metadata": null` and `"timestamp": null`**: that is the reply only.
  `conv msg list` returns both as sent.
- **`tls handshake eof` / `could not connect`** while polling: a dropped connection. Read-only commands
  retry four times; otherwise re-run `python3 demo.py` and it resumes.
- **The extracted facts differ between runs**: extraction is server-side and varies (19–22 project facts
  per run in ours, plus a few on the people's actors). If a checklist question finds nothing, the demo
  says "ask the customer". It does not hide the miss.
- **`conv list` shows other conversations too**: it lists the whole workspace. The demo picks its own by
  `metadata.customer=corvane`.

## Files

```
demo.py                    the runner; prints every CLI command it runs (and emits events for the web app)
data/sessions/*.json       the three stages: turns (text, tool calls, tool results), per-message metadata, handoff notes
data/checklist.json        the trainer's discovery questions, each with a search query and a topic pattern
web/server.py              local web companion (stdlib HTTP server + Server-Sent Events over demo.py)
web/static/                the page — no build step
out/                       the training brief (git-ignored)
```

All names, companies and figures are fictional.
