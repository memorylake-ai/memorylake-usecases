# Escalation pattern memory for support teams

A runnable version of the MemoryLake use case
[*Escalation Pattern Memory for Support Teams*](https://www.memorylake.ai/en/usecase/escalation-pattern-memory-for-support-teams),
built entirely on the [`memorylake` CLI](https://github.com/memorylake-ai/memorylake-cli).

**The problem.** Which team gets a mobile crash, what that team bounces an escalation for, how to put
something in front of the VP: that knowledge lives in the senior agents' heads. When one of them leaves,
junior agents escalate to the wrong team, without the right attachments, and slowly.

**What this demo shows.** Quayside (fictional) is a field-service scheduling app. Maya, its senior support
agent, is leaving. She talks her escalation notes through with **Relay**, the team's escalation agent.
Her notes name customers, a contact, ticket and account numbers and a contract value, the way real notes do.

Relay has a **fact instruction of its own** (`fact instruction set --agent`): record escalation paths,
context packages and framing, *even when a teammate says them*; never a customer, a contact, an id or a
figure. A second agent on MemoryLake's built-in default hears the same words, for comparison. Then:

- **same words, three memories.** Relay keeps Maya's lessons with no customer in them. The default agent
  keeps little or nothing of what *Maya* said: an agent's memory, by default, is about the agent. The
  support project the conversations live in keeps the lessons *and* the customers. A **leak scan**
  checks every fact;
- every lesson on Relay traces back (`fact trace --agent` → `conv msg list`) to the messages it came from
  and who sent them;
- Maya's actor is **sealed** (`actor unbind`). Her own memory now answers 404, and a search that names
  her is rejected. Relay's lessons are where they were;
- Tomás, three weeks in, takes the next mobile crash alone and asks Relay's memory where it goes, what to
  attach and how to frame it for the VP. The default agent's memory answers none of the three;
- Tomás reports the outcome to Relay. One more lesson lands on Relay; the customer does not.

```
                         ┌─▶ Relay (agent, its own fact instruction) ──▶ "Mobile app crashes should be escalated to the
Maya's handoff ──────────┤                                                mobile team's on-call rotation."   0 customers
(same 6 messages,        ├─▶ Relay, built-in default (comparison) ─────▶ (nothing)
 two DIRECT convs)       └─▶ support project (where convs live) ───────▶ "Brightwell Dental account ACC-55210 pays $48,000 a year."
                                    │
 actor unbind Maya ── her memory: 404 · Relay: unchanged
 Tomás: search --actors <Relay's actor>  →  route · package · framing   (default agent: 0 of 3)
 Tomás ↔ Relay outcome  →  "upload failures: add upload size + network type"   0 customers
```

Runs in about 3 minutes on a free personal account. The only credential you need is a MemoryLake API key.

**Watch it run** (real recordings, unedited): recording in progress.

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
cd memorylake-usecases/escalation-memory

export MEMORYLAKE_API_KEY=sk-…
python3 demo.py
```

With `MEMORYLAKE_API_KEY` set, the demo logs into its own CLI profile under `./.memorylake-demo/`
(git-ignored) and leaves `~/.memorylake` alone. Without it, it uses your existing `memorylake auth login`.

| | |
|---|---|
| `MEMORYLAKE_BASE_URL` | API endpoint; default global, China is `https://app.memorylake.cn/openapi/memorylake` |
| `MEMORYLAKE_WORKSPACE` | Workspace id; default is the one remembered by `ws use`, else your default workspace |
| `python3 demo.py --reset` | Delete everything the demo created, then start over |
| `python3 demo.py memory` | Print Relay's instruction, its lessons and the leak scan |
| `python3 demo.py audit` | Trace every lesson on Relay to its messages; rewrites `out/relay-provenance.md` / `.json` |
| `python3 demo.py cleanup` | Delete the conversations, both agents' memories and the agents, the project, both people's facts and the people |

Safe to re-run: everything is found again by `custom_id`, prefixed `mlu-esc-`. A re-run does not tell the
handoff again and leaves Maya sealed (she has left). It reads the memories that are there and runs the
searches again. Use `--reset` to watch extraction under the instruction from scratch.

## Or run it in the browser

```bash
python3 web/server.py        # http://127.0.0.1:8765 — standard library only
```

Paste the key and press **Run demo**. The page shows the handoff arriving at both agents as two chats,
the three memories side by side with every fact checked and a table of which of Maya's lessons each one
kept, the provenance of each lesson, Maya's sealed memory next to Relay's, Tomás's three questions and
the outcome. **Ask** runs the same search against Relay's memory or the default agent's. Try *a data
export is missing rows, who gets it* in both. The terminal drawer shows every `memorylake` command as it runs.

## What happens, step by step

All output below is from real `--reset` runs (steps 1–7 from one, step 8 from the last run of this version). Ids are shortened; commands that repeat are left out.

### 1. Connect

```
$ memorylake auth login --api-key sk-… --base-url https://app.memorylake.ai/openapi/memorylake --profile memorylake-usecases
$ memorylake team get
  · connected to team “niuniu@…” as tenant_owner
  · using workspace “Default Workspace” (ws-83f0…)
```

### 2. Two people, one project, two agents, one instruction

Every agent comes with an actor of its own. An agent's memory is listed with `fact list --agents <agent>`
and searched with `search --actors <the agent's actor>` (search has no `--agents`).

```
$ memorylake agent create --name Relay --custom-id mlu-esc-relay --description '…'
  · created the agent Relay → agent-f848…, with its own actor actor-1a1b…
$ memorylake agent bind agent-f848… --workspace ws-83f0…
$ memorylake agent create --name 'Relay (built-in default, for comparison)' --custom-id mlu-esc-relay-default …
$ memorylake fact instruction set --agent agent-f848… --file data/relay-instruction.md --workspace ws-83f0…
$ memorylake fact instruction get --agent agent-f848… --workspace ws-83f0…

  Relay's fact instruction, read back with `fact instruction get --agent`:

    ## Record
    - Escalation paths: which kind of issue goes to which internal team or on-call rotation, and which team it should NOT go to.
    - Context packages: exactly what the receiving team needs attached for each kind of escalation.
    - Framing that worked or failed with the receiving team or with leadership, and how the escalation turned out.
    - Record these lessons even when a teammate says them rather than the agent.

    ## Never record
    - Customer company names, contact names, account IDs, ticket numbers, prices, contract values or any other figures.
    - Describe a customer only by plan tier and size (for example "an enterprise customer with a large mobile fleet").

  Relay (built-in default, for comparison): `fact instruction get --agent` → "" (built-in default)
```

### 3. Maya's handoff, told to both agents

One `DIRECT` conversation per agent, between Maya's actor and the agent's actor, in the support project.
Six messages: four from Maya (three escalations and how to approach the VP), two short questions from the agent.

```
$ memorylake conv create --custom-id mlu-esc-handoff-relay --project proj-2233… --actors actor-570f…,actor-1a1b… --kind DIRECT …
$ memorylake conv msg append conv-63b5… --actor actor-570f… --custom-id msg-01 --timestamp 2026-10-05T09:00:00Z \
    --text 'Maya Lindqvist (Tier 2 support, Quayside): Relay, I'm handing over my escalation notes before I leave on Wednesday.
            First one: Brightwell Dental, account ACC-55210, ticket TCK-88412. Their iOS app crashed on launch after the 4.12 update. …'
  · … and 5 more message(s) appended the same way
  · memory ready in 57s
```

### 4. Same words, three memories

```
  Relay — the agent's own memory, under its instruction (9 fact(s)):
   ✓ Escalations to the VP of Support should lead with the revenue at risk and the number of affected customers, not with the ticket history. (as of 2026-10-05)
   ✓ Billing disputes should go to finance ops first, and engineering should be involved only if finance ops confirms a system bug. (as of 2026-10-05)
   ✓ Leading a data-pipeline escalation with a row-count mismatch gets it picked up faster than leading with “the customer is angry”. (as of 2026-10-05)
   ✓ Escalations for an export that silently skips rows should include the export job id, the time window, and row counts from both sides. (as of 2026-10-05)
   ✓ S3 data exports that silently skip rows should be escalated to the data-pipeline team, not to platform. (as of 2026-10-05)
   ✓ The mobile team bounces crash escalations that do not include a crash log. (as of 2026-10-05)
   ✓ A mobile crash escalation should include the device model, OS version, app build number, and a crash log from the device. (as of 2026-10-05)
   ✓ The platform team owns API and auth outages, not mobile app crash issues. (as of 2026-10-05)
   ✓ Mobile app crashes should be escalated to the mobile team's on-call rotation. (as of 2026-10-05)

  Relay (built-in default, for comparison) — same words, built-in default (0 fact(s)):
   (nothing)

  The support project the conversations live in (9 fact(s)):
   • Lumen Logistics ticket TCK-90107 about a data export to S3 silently skipping rows should be escalated to the data-pipeline team, not to platform. (as of 2026-10-05)
       ↳ Lumen, TCK-90107
   • Brightwell Dental account ACC-55210 pays $48,000 a year. (as of 2026-10-05)
       ↳ Brightwell, ACC-55210, figure “$48,000”
   • … 7 more, the same lessons without a customer

  Maya's 8 lessons found in each memory:
   mobile crash → mobile on-call                      Relay ✓   default ·   project ✓
   platform owns only API/auth outages                Relay ✓   default ·   project ✓
   mobile package: device, OS, build, crash log       Relay ✓   default ·   project ✓
   export gaps → data-pipeline                        Relay ✓   default ·   project ✓
   export package: job id, window, row counts         Relay ✓   default ·   project ✓
   billing disputes → finance ops first               Relay ✓   default ·   project ✓
   open with the evidence, not the mood               Relay ✓   default ·   project ✓
   to the VP: revenue at risk + customers affected    Relay ✓   default ·   project ✓
                                                      8/8     0/8       8/8

  Relay  : 9 fact(s) · 0 name a customer or carry a figure
  default: no facts, so nothing to check
  project: 9 fact(s) · 2 name a customer or carry a figure   ← the project keeps the customers
```

The leak scan matches each customer's identifiers (names, contacts, ticket and account numbers) as whole
words, plus any figure once the server's own dates are taken out. It is in `leaks()` in `demo.py`. A
lesson counts as found when every keyword group for it matches (`patterns` in `data/story.json`).

Why the project's memory is not the answer: it has the lessons, but next to the customers, and its
facts are about the project rather than the agent that routes escalations. Relay's memory is the one
an escalation agent reads.

### 5. Provenance: lesson → messages → speaker

```
$ memorylake fact trace fact-a183… --agent agent-f848… --workspace ws-83f0…
   Mobile app crashes should be escalated to the mobile team's on-call rotation. (as of 2026-10-05)
     → handoff-relay msg 1, 2, 3 · Maya Lindqvist, Relay · COOK
   S3 data exports that silently skip rows should be escalated to the data-pipeline team, not to platform. (as of 2026-10-05)
     → handoff-relay msg 4, 5, 6 · Maya Lindqvist, Relay · COOK

  9 lesson(s) on Relay; 9 trace to a batch with Maya's messages in it.
```

A trace entry's `source_entry_ids` are the whole batch the extractor read, so Relay's own questions show
up beside Maya's answers. `conv msg list` gives each message's `actor_id` (a person) or `agent_id` (Relay).
Written to `out/relay-provenance.md` and `.json`.

### 6. Maya leaves

```
  · Maya Lindqvist's last day: her actor is sealed — unbound from the workspace, not deleted
$ memorylake actor unbind --actor actor-570f… --workspace ws-83f0…

  Maya's own memory:  fact list --actors → HTTP 404 Actor not found or not bound to workspace not found with id: actor-570f…
                     a search that names her → HTTP 400 Actor 'actor-570f…' is not bound to workspace 'ws-83f0…'
  Relay's memory:     9 lesson(s) before · 9 after  ✓ unchanged
  search --actors <Relay> “where does a mobile app crash go”:
   1. Mobile app crashes should be escalated to the mobile team's on-call rotation. (as of 2026-10-05)
   2. The mobile team bounces crash escalations that do not include a crash log. (as of 2026-10-05)
   3. A mobile crash escalation should include the device model, OS version, app build number, and a crash log from the device. (as of 2026-10-05)
```

Sealing is reversible: `actor bind` brings her memory back as it was. **Deleting** an actor does not
delete its facts either: they stay readable and can no longer be deleted (`ACTOR_INACTIVE`), the same as
for a deleted agent. That is why `cleanup` deletes every fact first and the actor or agent after.

### 7. Tomás's first escalation

*Harbor Lane Plumbing: the Android app freezes when a technician uploads a job photo, since the 4.13
update. The customer says they will cancel if it is not fixed this week.*

```
$ memorylake search 'The Android app freezes after an update. Which team should this escalation go to?' --actors actor-1a1b… --types fact --top-k 5 …
  Q: The Android app freezes after an update. Which team should this escalation go to?
   ✓ Mobile app crashes should be escalated to the mobile team's on-call rotation. (as of 2026-10-05)
   ✓ The platform team owns API and auth outages, not mobile app crash issues. (as of 2026-10-05)
   expected: mobile crash → mobile on-call → ✓ found

  Q: What do I attach when I escalate a mobile app crash?
   ✓ A mobile crash escalation should include the device model, OS version, app build number, and a crash log from the device. (as of 2026-10-05)
   expected: mobile package: device, OS, build, crash log → ✓ found

  Q: The customer threatens to cancel and I need the VP of Support. How do I frame the escalation?
   ✓ Escalations to the VP of Support should lead with the revenue at risk and the number of affected customers, not with the ticket history. (as of 2026-10-05)
   (4 other hit(s) left out: search always returns its closest facts, related or not)
   expected: to the VP: revenue at risk + customers affected → ✓ found

  Relay's memory answered 3 of 3 · the built-in-default agent's memory: 0 of 3
  10 lesson(s) shown · 0 name a customer or carry a figure
```

Hits are filtered by topic words and the rest counted, not hidden. A ✓ means the hit states the lesson
Maya taught. A `·` hit is on topic but answers something else.

### 8. The outcome

A `DIRECT` conversation between Tomás and Relay. His message names the customer, the ticket, the contact
and how long the mobile team took.

```
  · memory ready in 49s

  What Tomás's outcome changed on Relay (2):
   ✓ [added] For photo or file upload failures, whether the issue occurs on cellular versus Wi‑Fi can be a key diagnostic clue to include in the escalation. (as of 2026-10-09)
   ✓ [added] For photo or file upload failures, the escalation package must include the upload size and the network type (Wi‑Fi or cellular). (as of 2026-10-09)

  expected: upload failures: add upload size + network type → ✓ on Relay
  Relay: 10 fact(s) · 0 name a customer or carry a figure
  the support project, for contrast: 3 fact(s) name a customer or carry a figure
```

Sometimes the outcome revises a lesson Relay already had instead of adding one: same fact id, new text.
The demo shows those as `[updated]` with the old text under them.

## How reliable is it?

Counted over every fresh run made while building this demo, on a free account. See the build notes for
the run-by-run table; the numbers are updated as more runs are made.

- **Facts on Relay after the handoff: 47 over 6 runs. Ones that named a customer or carried a figure: 0.**
  The instruction is a prompt to the extractor, not a guarantee, so the demo scans every fact and deletes
  what it flags. One fact from Tomás's outcome was flagged and deleted (1 of 6 runs).
- **Maya's 8 lessons on Relay:** 8/8 in 5 runs, 7/8 in 1.
- **The default agent, same words:** 1 fact over 6 runs (“The AI assistant serves as an escalation agent
  for Quayside”), and none of Maya's lessons. In probes where the agent's last message summed up the
  routes, the default agent kept that summary as its own promise (“The assistant will route billing
  disputes to finance ops first”) and still none of Maya's reasons or framing.
- **Tomás's three questions:** Relay's memory answered 3 of 3 in every run; the default agent's, 0 of 3.
- **The outcome lesson** reached Relay in every run: added as a new fact, or merged into the
  mobile-package fact.
- **The project** kept 0–4 customer-identifying facts per run (2–4 in 5 of 6).

## Wiring it into your own team

- One agent per job that should learn (escalations, triage, onboarding), each with its own
  `fact instruction set --agent`. Write it as "record X even when a teammate says it; never Y".
- Let senior people talk to the agent (a `DIRECT` conversation with the agent's actor), the way they
  would brief a colleague. Put ticket ids and links in conversation metadata, not in the text.
- Scan the agent's memory after each conversation with your customers' identifiers and a figure rule.
  The instruction steers extraction; a scan is what you can rely on.
- Agents read the agent's memory with `search --actors <agent's actor>`. When someone leaves, `actor unbind`
  seals their own memory and leaves the agent's alone.
- Never delete an actor or agent before deleting its facts: a deleted actor's or agent's facts stay
  readable and become impossible to delete.

## If something goes wrong

- **`tls handshake eof` / `could not connect`** while polling: a dropped connection. Read-only commands
  retry four times; otherwise re-run `python3 demo.py` and it resumes.
- **`401 service account not found`**: the key was deleted or belongs to the other deployment.
- **"Relay recorded nothing from this handoff"**: the demo tells the handoff once more in a new
  conversation by itself. If that also yields nothing, `python3 demo.py --reset`.
- **"Maya is sealed but her handoff is incomplete"**: an earlier run stopped in an unusual place.
  `python3 demo.py --reset`.
- **A ✗ on a Relay fact**: the extractor kept an identifier or a figure despite the instruction. The demo
  deletes it and says so.
- **Facts are worded differently each run**: extraction is server-side and varies. The scan and the lesson
  check use keyword groups, not exact wording.
- **`cleanup` says facts are still on an actor or agent**: it stops before deleting that actor or agent,
  because its facts would become undeletable. Run `python3 demo.py cleanup` again.

## Files

```
demo.py                     the runner; prints every CLI command it runs (and emits events for the web app)
data/story.json             the people, both agents, the customers and their identifiers, the handoff,
                            Maya's 8 lessons as keyword groups, Tomás's ticket and questions, the outcome
data/relay-instruction.md   Relay's fact instruction
web/server.py               local web companion (stdlib HTTP server + Server-Sent Events over demo.py)
web/static/                 the page — no build step
out/                        relay-provenance.md / .json (git-ignored)
```

All names and companies are fictional.
