# Sales call memory for revenue teams

A runnable version of the MemoryLake use case
[*Give Revenue Teams Sales Call Memory That Survives Every Hand-Off*](https://www.memorylake.ai/en/usecase/sales-call-memory-for-revenue-teams),
built entirely on the [`memorylake` CLI](https://github.com/memorylake-ai/memorylake-cli).

**The problem.** Sales context dies between calls and hand-offs. The SDR runs discovery, the AE
inherits half of it, the CSM inherits even less and re-asks questions the prospect already answered.

**What this demo shows.** Three calls and two documents about one account go into MemoryLake.
MemoryLake extracts the durable facts from the transcripts on its own, the reps pin a few notes by
hand, and the CSM taking over the pilot gets a pre-kickoff brief in seconds by *searching the
account memory* instead of re-reading transcripts. Every bullet in that brief is a retrieved memory
with the date it was learned.

```
SDR: discovery call ──┐
AE: security + pricing call ──┼──▶  conversations ──▶  facts (extracted, dated)  ──┐
AE: CFO approval call ──┘                                                          ├──▶ search ──▶ CSM brief
rep notes after each call ──▶  facts (pinned verbatim) ────────────────────────────┤
hand-off doc + email thread ──▶  documents (indexed) ──────────────────────────────┘
```

Runs in about 3 minutes on a free personal account. The only credential you need is a MemoryLake API key.

## Prerequisites

1. **A MemoryLake account and API key.** Sign up at [app.memorylake.ai](https://app.memorylake.ai),
   open **API Keys** in the sidebar, create a key and copy it (it is shown in full only once).
   Details: [Authentication and API Keys](https://docs.memorylake.ai/authentication).
2. **The `memorylake` CLI** (macOS, Linux or Windows):

   ```bash
   curl -fsSL https://raw.githubusercontent.com/memorylake-ai/memorylake-cli/main/scripts/install.sh | sh
   ```

   ```powershell
   irm https://raw.githubusercontent.com/memorylake-ai/memorylake-cli/main/scripts/install.ps1 | iex
   ```

   The installer offers to log you in. You can accept, or skip it and give the key to the demo
   through the environment as shown below.
3. **Python 3.9+.** The demo runner uses only the standard library.

## Run it

```bash
git clone https://github.com/memorylake-ai/memorylake-usecases.git
cd memorylake-usecases/sales-call-memory

export MEMORYLAKE_API_KEY=sk-…      # the key you created in the console
python3 demo.py
```

With `MEMORYLAKE_API_KEY` set, the demo logs into its own profile under `./.memorylake-demo/`
(git-ignored) and leaves your `~/.memorylake` untouched. Without it, the demo uses whatever
`memorylake auth login` session you already have.

Other knobs:

| | |
|---|---|
| `MEMORYLAKE_BASE_URL` | API endpoint. Default is the global one; the China deployment is `https://app.memorylake.cn/openapi/memorylake` |
| `MEMORYLAKE_WORKSPACE` | Workspace id to use. Default: the one remembered by `memorylake ws use`, else the first one listed (your default workspace) |
| `python3 demo.py --reset` | Delete and recreate the demo project before ingesting |
| `python3 demo.py --with-agent` | Also ask a MemoryLake agent for a brief over the same memory. Needs model quota on your account; free personal accounts get an HTTP 402 and the step is skipped |
| `python3 demo.py --show-json` | Print the raw JSON every command returns |
| `python3 demo.py brief` | Re-generate only the brief (fast; the memory is already there) |
| `python3 demo.py facts` | List what MemoryLake remembers about the account |
| `python3 demo.py cleanup` | Delete the project, actors, agent and uploaded files the demo created |

The demo is safe to re-run: it finds what already exists by `custom_id` and only appends what is
missing. Everything it creates is prefixed `mlu-scm-` so it cannot collide with your own data.

## What happens, step by step

The runner prints every CLI command before running it, so you can follow along or copy a line
into your own shell. The commands, in order:

**1. Connect.** Validate the key, pick a workspace.

```bash
memorylake auth login --api-key sk-… --profile memorylake-usecases
memorylake team get
memorylake ws list
```

**2. Set up the people and the account.** An *actor* is who a memory is attributed to: two reps
on our side (Northwind) and three people at the prospect (Acme Corp). Actors are account-wide and
have to be bound to a workspace. A *project* holds everything about one account.

```bash
memorylake actor create --custom-id mlu-scm-dana-li --display-name "Dana Li" --tags acme,prospect …
memorylake actor bind --actor actor-… --workspace ws-…
memorylake proj create --name "Acme Corp — account memory" --custom-id mlu-scm-acme-corp
```

**3. Ingest the calls.** Each call becomes a *conversation* between its participants, and each line
of the transcript a message attributed to its speaker. `--timestamp` dates the memory to when it was
said, not when it was uploaded; that is where the "(as of 2026-09-16)" on the extracted facts comes
from. After each call the rep's CRM notes are pinned with `fact add`: those are stored verbatim and
searchable immediately.

```bash
memorylake conv create --custom-id mlu-scm-call-01-discovery --project proj-… --actors actor-…,actor-… --kind GROUP
memorylake conv msg append conv-… --actor actor-… --custom-id turn-01 --timestamp 2026-09-16T09:02:00Z \
  --text "Jordan Park (SDR, Northwind): Thanks for making time, Dana. …"
memorylake fact add --project proj-… "Dana Li (VP Ops) is the champion at Acme Corp; she prefers email …"
```

Messages are stored instantly, but the facts are extracted in the background. The runner polls
until all three conversations report `cook_finished`, which takes about a minute:

```bash
memorylake conv cook-status conv-…
```

**4. Ingest the notes.** The SDR→AE hand-off document and the pricing email thread go into the
Library and are imported into the project, where they are parsed and indexed.

```bash
memorylake lib upload data/notes/handoff-sdr-to-ae.md
memorylake proj doc import --project proj-… <item-id> <item-id> --wait
```

**5. What did MemoryLake remember?** Twenty to thirty dated facts, extracted from the calls without
anyone tagging anything, plus the six pinned notes.

```bash
memorylake fact list --projects proj-…
```

**6. The hand-off.** The CSM's brief is six searches against the account memory. Facts and documents
come back as separate sets, so the brief lists the facts and names the documents they came from.

```bash
memorylake search "who is the main contact at Acme now and who else is involved in the decision" --projects proj-… --top-k 5
memorylake search "Acme pricing, budget approval and billing terms" --projects proj-…
```

The brief is written to `out/acme-handoff-brief.md`. An excerpt from a real run:

```markdown
## Who is who, and who is the main contact now?
- From November onward, Northwind's main contact at Acme is Priya Raman (as of 2026-10-06).
- Anything above 20k a year requires sign-off from Acme's CFO, Mark Chen (as of 2026-09-16).
- Dana Li (VP Ops) is the champion at Acme Corp; she prefers email over phone and has Tuesdays blocked.

## Pilot timeline and success criteria
- The pilot kicks off on October 20 (as of 2026-10-06).
- The pilot success criterion is reducing vendor onboarding time from three weeks to one week … (as of 2026-10-06).
```

Notice what the brief does that transcripts cannot: the newest fact wins ("main contact is Priya
Raman", from call 3) while the earlier context stays attached with its date (Dana was the champion
on call 1). That is the deal timeline the use case page describes.

**7. Optional: ask an agent.** With `--with-agent`, the demo creates a MemoryLake agent bound to the
workspace and asks it for the brief with `--project` pointed at the account. The agent reads the same
memory. This step needs model quota on the account.

```bash
memorylake agent create --name "Deal assistant (demo)" --custom-id mlu-scm-deal-assistant --system-prompt "…"
memorylake agent bind agent-… --workspace ws-…
memorylake agent send agent-… --project proj-… --text "Write a 6-bullet pre-kickoff brief for the Acme Corp pilot …"
```

## Adapting it to your own calls

- Drop your own transcripts into `data/calls/*.json` using the same shape (`participants`, `turns`,
  optional `rep_notes`) and your notes into `data/notes/*.md`. Add the speakers to `PEOPLE` in `demo.py`.
- Keep speaker labels in the text of each turn (`Name (role, company): …`), as call recorders export
  them. That is how the extractor tells "we" from "you" when it writes the facts.
- One project per account is the natural scope: `search --projects` is the account memory. Deleting
  the project removes its documents and facts; conversations are workspace-level objects, so
  `cleanup` deletes them explicitly (and `--reset` does the same before recreating the project).
- For a real pipeline, append messages as they happen with a stable `--custom-id` per message; a retry
  returns the message created the first time instead of duplicating it.

## Files

```
demo.py                      the runner; prints every CLI command it runs
data/calls/*.json            three call transcripts with speaker, date and the rep's post-call notes
data/notes/*.md              SDR→AE hand-off document and the pricing email thread
out/acme-handoff-brief.md    generated by step 6 (git-ignored)
.memorylake-demo/            the demo's isolated CLI profile when MEMORYLAKE_API_KEY is set (git-ignored)
```

All names, companies and figures in the data are fictional.
