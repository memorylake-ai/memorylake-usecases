# Add long-term memory to your chatbot

A runnable version of the MemoryLake use case
[*Your Chatbot Forgets Every User. MemoryLake Fixes That.*](https://www.memorylake.ai/en/usecase/add-long-term-memory-to-your-chatbot),
built entirely on the [`memorylake` CLI](https://github.com/memorylake-ai/memorylake-cli).

**The problem.** Chatbot platforms give you session context. When the session ends it is gone, and the
returning user re-introduces themselves, re-states their preferences and re-explains their situation.

**What this demo shows.** "Nimbus" is a travel concierge bot. Two users talk to it over five sessions on
four channels (web widget, mobile app, WhatsApp, Slack). Each session is written to MemoryLake as a
conversation between *the user* and *the bot*. MemoryLake extracts the durable facts and attaches them
to **the user's actor**, not to the bot or the project, so every user has a memory of their own:

- a returning user on a new channel is recognised with **one scoped search** that becomes the memory
  block your backend prepends to the model prompt;
- a preference that changed ("aisle, not window"; "moved to Hamburg") shows up dated, latest first;
- **one user's memory never answers for another** — the demo checks this explicitly;
- a user can ask to be **forgotten**, one fact at a time (`fact delete`) or entirely (`actor delete`).

```
Alice: web ──┐
Alice: app  ─┼─▶ conversations (user ↔ bot) ──▶ facts owned by Alice ──▶ search --actors alice ──▶ <memory> block
Alice: WhatsApp ┘
Bob: web ────┐
Bob: Slack  ─┴─▶ conversations (user ↔ bot) ──▶ facts owned by Bob   ──▶ search --actors bob   ──▶ <memory> block
```

Runs in about 2 minutes on a free personal account. The only credential you need is a MemoryLake API key.

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
cd memorylake-usecases/chatbot-user-memory

export MEMORYLAKE_API_KEY=sk-…
python3 demo.py
```

With `MEMORYLAKE_API_KEY` set, the demo logs into its own CLI profile under `./.memorylake-demo/`
(git-ignored) and leaves `~/.memorylake` alone. Without it, it uses your existing `memorylake auth login`.

| | |
|---|---|
| `MEMORYLAKE_BASE_URL` | API endpoint; default global, China is `https://app.memorylake.cn/openapi/memorylake` |
| `MEMORYLAKE_WORKSPACE` | Workspace id; default is the one remembered by `ws use`, else your default workspace |
| `python3 demo.py --reset` | Delete the demo project and sessions first |
| `python3 demo.py --with-llm` | Also generate a real reply through the Model Router with the same key (needs model quota; free accounts get `insufficient_quota` and the step is skipped) |
| `python3 demo.py profiles` | Print what the bot knows about each user |
| `python3 demo.py cleanup` | Delete the sessions, the project, the users (and their memory) and the bot |

Safe to re-run: everything is found again by `custom_id`, prefixed `mlu-cbm-`.

## Or run it in the browser

```bash
python3 web/server.py        # http://127.0.0.1:8765 — standard library only
```

![Web companion — the returning-user view](https://github.com/memorylake-ai/memorylake-usecases/raw/main/chatbot-user-memory/web/screenshot-returning.png)

Paste the key, press **Run demo**, and watch the sessions replay per user, the memory panel fill in,
the returning-user memory block appear, the isolation check pass and a fact get forgotten. An **Ask as
user** box runs the same scoped search for any question you type, as either user. The terminal drawer
shows every `memorylake` command as it runs.

## What happens, step by step

**1. Connect** — validate the key, pick a workspace.

**2. Set up.** The bot is an `ASSISTANT` actor; each end user is a `HUMAN` actor whose `custom_id` is
your own user id (`u-1001`, `u-2002` here); one project holds the bot's sessions.

```bash
memorylake actor create --custom-id mlu-cbm-nimbus --display-name Nimbus --type ASSISTANT …
memorylake actor create --custom-id mlu-cbm-user-u-1001 --display-name "Alice Weber" --type HUMAN …
memorylake actor bind --actor actor-… --workspace ws-…
memorylake proj create --name "Nimbus — chat sessions" --custom-id mlu-cbm-nimbus-sessions
```

**3. Replay the sessions.** One `DIRECT` conversation per chat session between the user and the bot,
channel in metadata, every message attributed to its speaker and time-stamped. Memory is built in the
background; the runner waits for `cook-status`.

```bash
memorylake conv create --custom-id mlu-cbm-alice-s1-web --project proj-… --actors actor-alice,actor-nimbus \
  --kind DIRECT --metadata "channel=web widget" --metadata user_id=u-1001
memorylake conv msg append conv-… --actor actor-alice --custom-id turn-01 --timestamp 2026-09-20T18:05:40Z \
  --text "Hi, I'm Alice. Can you help me find a place for dinner on Friday?"
memorylake conv cook-status conv-…
```

**4. What the bot knows.** The facts are owned by the user's actor — the bot and the project own none.

```bash
memorylake fact list --actors actor-alice
```

**5. A returning user.** Alice comes back on WhatsApp and asks what she booked. One search scoped to
her actor returns the facts for this turn; the demo prints them as the memory block a backend would
prepend to the model prompt. Then it checks isolation: a Bob-only query asked in Alice's memory must
return none of Bob's facts, and vice versa.

```bash
memorylake search "what did I book with you, and did you remember my sister's thing?" --actors actor-alice --types fact --top-k 8
memorylake search "steak and company invoices" --actors actor-alice     # must not return Bob's facts
```

**6. The right to be forgotten.** Alice asks the bot to forget where she lives. Facts are immutable, so
forgetting is a delete: find it with a scoped search, delete it by id, search again.

```bash
memorylake search "where does the user live" --actors actor-alice --top-k 3
memorylake fact delete --actor actor-alice fact-…
```

Deleting the actor removes every fact they own, which is the whole-account "forget me" — `cleanup` does
exactly that.

**7. Optional: a real reply.** With `--with-llm`, the memory block and the user's question go to the
Model Router (`/v1/chat/completions`, same API key) and Nimbus answers for real.

## Wiring it into your own bot

- Create one `HUMAN` actor per user with `--custom-id <your user id>`; look it up with
  `actor get <id> --by-custom-id` on every request (or cache it).
- Create one conversation per session with the user and the bot as actors, append each turn with a
  stable `--custom-id` (retries are idempotent), and set `--timestamp` to the real time of the message.
- At the start of a turn, run `search "<incoming message>" --actors <user>` and put the facts in the
  prompt. For a small per-user memory, `fact list --actors <user>` as a standing profile works too.
- Keep the project per bot or per tenant; keep the actor per user. Isolation then comes from the scope,
  not from your code.
- Facts extracted from a conversation are not searchable until `cook-status` says so (tens of seconds).
  Facts you add yourself with `fact add --actor <user> "…"` are searchable immediately.

## If something goes wrong

- **`tls handshake eof` / `could not connect`** while polling: a dropped connection. Read-only commands
  retry four times; otherwise re-run `python3 demo.py` and it resumes.
- **`401 service account not found`**: the key was deleted or belongs to the other deployment.
- **`insufficient_quota`** only affects `--with-llm`.
- **Facts come back in another language than the transcript**: extraction language is chosen server-side
  and is not configurable from the CLI today.

## Files

```
demo.py                  the runner; prints every CLI command it runs (and emits events for the web app)
data/users/*.json        two users: sessions per channel, the returning question, the forget request
web/server.py            local web companion (stdlib HTTP server + Server-Sent Events over demo.py)
web/static/              the page — no build step
out/context-blocks.md    the memory blocks from step 5 (git-ignored)
```

All names, companies and figures are fictional.
