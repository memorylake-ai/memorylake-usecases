# Candidate pipeline memory that holds every candidate across every stage

A runnable version of the MemoryLake use case
[*Give Recruiting Teams Pipeline Memory That Holds Every Candidate Across Every Stage*](https://www.memorylake.ai/en/usecase/candidate-pipeline-memory-for-recruiting-teams),
built entirely on the [`memorylake` CLI](https://github.com/memorylake-ai/memorylake-cli).

**The problem.** A candidate goes through a recruiter screen, a technical interview and a values
interview. Each interviewer keeps a private note. The debrief goes to whoever talks loudest, the
pipeline is lost when the recruiter rotates off the requisition, and when a candidate withdraws nobody
is sure what to do with what was collected.

**What this demo shows.** Halden Robotics (fictional) is hiring a Senior Controls Engineer. Two
candidates, Priya Raman and Tomás Ferreira, each go through three stages with three different
interviewers (Dana Okafor, recruiter; Marcus Lee, hiring manager; Aiko Sato, values). Every candidate is
an **actor**: every interview is a conversation with them, and every interviewer's scorecard is pinned
to them.

- **Debrief: every scorecard, one view.** Five debrief questions per candidate, each answered from the
  candidate's memory. Every answer carries its source: a scorecard (`technical · Marcus Lee`) or what the
  candidate said, dated by the conversation. When Priya shortens her notice in the values interview,
  MemoryLake revises the screen's answer in place: *"start on November 16 (as of 2026-09-24, previously
  November 30 as of 2026-09-14)"*.
- **Recruiter rotation: nothing to hand over.** Dana leaves the requisition and Eli Brandt takes over.
  Eli lists the candidates bound to the workspace and asks the whole pipeline one question.
- **A candidate withdraws: `actor unbind`.** Tomás takes another offer and asks Halden to stop using his
  information. Unbinding his actor seals his memory in this workspace. Reading his facts, searching his
  memory and writing a message as him are all **refused** (`HTTP 404` / `400 … is not bound to
  workspace`). The actor itself is kept, and the pipeline search drops him automatically.
- **He applies again: `actor bind`.** Two months later Tomás applies for a new opening. Binding the same
  actor back restores his memory: **N facts before · refused while unbound · N after, identical fact
  ids**. The new conversation then revises September's answer: *"$178,000 base salary (as of
  2026-11-10, previously $170,000 as of 2026-09-15)"*.

```
screen ─────┐                                  ┌─ scorecards (fact add --actor <candidate>)
technical ──┼── conversations (candidate ↔ interviewer) ──▶ actor: candidate ◀─┤
values ─────┘                                  └─ extracted facts, dated
                                │
   search --actors <candidate>       ──▶ debrief, every answer labelled by stage
   actor list --tags candidate       ──▶ ACTIVE bindings ──▶ one search across the pipeline
   actor unbind / actor bind         ──▶ sealed (refused) ──▶ restored (identical ids)
```

**Watch it run** (real recordings, unedited):

- [CLI demo (mp4, 5:47)](https://github.com/memorylake-ai/memorylake-usecases/releases/download/candidate-pipeline-memory-v1/candidate-pipeline-memory-cli-demo.mp4): `git clone`, `python3 demo.py`, `python3 demo.py brief priya`, `python3 demo.py cleanup`
- [Web companion demo (mp4, 7:10)](https://github.com/memorylake-ai/memorylake-usecases/releases/download/candidate-pipeline-memory-v1/candidate-pipeline-memory-web-demo.mp4)

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
cd memorylake-usecases/candidate-pipeline-memory

export MEMORYLAKE_API_KEY=sk-…
python3 demo.py
```

About 5 minutes, most of it MemoryLake extracting the facts of each interview (35–60 s per stage; the
two candidates' interviews are processed side by side).

With `MEMORYLAKE_API_KEY` set, the demo logs into its own CLI profile under `./.memorylake-demo/`
(git-ignored) and leaves `~/.memorylake` alone. Without it, it uses your existing `memorylake auth login`.

| | |
|---|---|
| `MEMORYLAKE_BASE_URL` | API endpoint; default global, China is `https://app.memorylake.cn/openapi/memorylake` |
| `MEMORYLAKE_WORKSPACE` | Workspace id; default is the one remembered by `ws use`, else your default workspace |
| `python3 demo.py --reset` | Delete everything from earlier runs (conversations, project, actors and their facts), then start over |
| `python3 demo.py brief` | Print the debrief for both candidates again (read-only) |
| `python3 demo.py brief priya` | The same, for one candidate |
| `python3 demo.py cleanup` | Delete the conversations, the project and every actor the demo created, with their facts |

Safe to re-run: everything is found again by `custom_id` (prefix `mlu-cpm-`). Conversations resume from
the last stored message, and a scorecard is pinned only by the run that appended its interview. Setup
binds every actor again, so a run that stopped while Tomás was unbound starts cleanly.

## Or run it in the browser

```bash
python3 web/server.py        # http://127.0.0.1:8765 — standard library only
```

![Web companion — the withdrawal: three refused calls after actor unbind, the actor kept, the pipeline down to one candidate](https://github.com/memorylake-ai/memorylake-usecases/raw/main/candidate-pipeline-memory/web/screenshot-withdraw.png)

Paste the key and press **Run demo**. As it runs you see:

- the three interviews per candidate, each with its scorecard pinned after the interview;
- the debrief, side by side, with every answer labelled by stage and interviewer;
- Eli's pipeline search, before and after the withdrawal;
- the three refusals after `actor unbind`, and the before / refused / after counts after `actor bind`.

**Ask about a candidate** searches one candidate's memory for any question, with every hit labelled by
stage. Asked while a candidate is unbound, it shows the refusal.

[Watch the web companion demo (mp4, 7:10)](https://github.com/memorylake-ai/memorylake-usecases/releases/download/candidate-pipeline-memory-v1/candidate-pipeline-memory-web-demo.mp4). The terminal drawer shows every
`memorylake` command as it runs.

## What happens, step by step

**1. Connect.** `auth login` into the isolated profile, `team get`, pick the workspace.

**2. Set up.** One `HUMAN` actor per person. The candidates' `custom_id` is their ATS candidate id, and
they are tagged `candidate`. Binding an actor to the workspace is what lets the workspace read and write
that actor's memory:

```bash
memorylake actor create --custom-id mlu-cpm-cand-20417 --display-name "Priya Raman" --type HUMAN \
    --tags candidate,role:senior-controls-engineer --description "Candidate cand-20417 for Senior Controls Engineer"
memorylake actor bind --actor <priya>
memorylake proj create --name "Halden Robotics — Senior Controls Engineer pipeline" --custom-id mlu-cpm-pipeline-senior-controls-engineer
```

**3. Three stages, three interviewers.** Each interview is a `DIRECT` conversation between the candidate
and that stage's interviewer, with the historical timestamp on every message:

```bash
memorylake conv create --custom-id mlu-cpm-priya-technical --project <pipeline> --actors <priya>,<marcus> \
    --kind DIRECT --name "Priya Raman — Technical interview" --metadata stage=technical --metadata candidate_id=cand-20417
memorylake conv msg append <conv> --actor <priya> --custom-id turn-02 --timestamp 2026-09-21T09:01:30Z \
    --text "Priya Raman (candidate): A cascaded position-velocity loop for a six-axis arm. …"
memorylake conv cook-status <conv>
```

One candidate's stages are processed **in order**: the next stage starts only when `cook-status` says
the previous one is done. That is what lets a later interview revise an earlier answer instead of
racing it. Then the interviewer's scorecard is pinned to the candidate, stamped with stage, author and
date:

```bash
memorylake fact add --actor <priya> "[scorecard · technical · Marcus Lee · 2026-09-21] Lean yes. Excellent loop design … Concern: no hands-on certification of safety-rated functions (SIL 2 / PL d) …"
```

The facts of a `DIRECT` conversation land on the candidate's actor, not on the project. So the
candidate is the unit of memory, and everything below is scoped with `--actors`.

**4. Debrief.** `fact list --actors <candidate>` counts what memory holds. Then there is one search per
question:

```bash
memorylake search "notice period earliest start date" --actors <priya> --types fact --top-k 8
```

```
  When can they start?
    [values · Aiko Sato · 2026-09-24] Strong yes. … Update: notice shortened, earliest start is now November 16 (was November 30).
    [values · said by candidate · revises an earlier answer · 2026-09-24] The earliest the candidate could start a new role is November 16 (as of 2026-09-24, previously November 30 as of 2026-09-14).
    [screen · Dana Okafor · 2026-09-14] Strong yes from the screen. … notice period eight weeks (earliest start November 30) …
    (4 other hit(s) left out: they do not mention start, notice …)
```

- **The recommendation row lists every scorecard** (from `fact list`), not the top hits of a search.
  The debrief has to hear all three interviewers.
- **A search always returns its top-k**, so the other rows keep only hits that mention the topic, and
  count the rest.
- **The source label** comes from the scorecard stamp, or from the extracted fact's `(as of …)` date,
  matched to the interview on that day. The extractor dates facts in UTC+8, so the next day also
  matches.

**5. Recruiter rotation.** Eli lists the candidates this workspace may read, and searches all of them at
once:

```bash
memorylake actor list --tags candidate          # bindings with status ACTIVE
memorylake search "notice period, earliest start date and compensation expectations" --actors <priya>,<tomas> --types fact --top-k 10
```

Search hits carry no owner, so the demo maps each hit back to its candidate through the fact ids from
`fact list --actors`.

**6. Tomás withdraws.**

```bash
memorylake actor unbind --actor <tomas>
memorylake fact list --actors <tomas>            # 404 Actor not found or not bound to workspace
memorylake search compensation --actors <tomas>  # 400 Actor '…' is not bound to workspace '…'
memorylake conv msg append <conv> --actor <tomas> …   # 400 … is not bound to workspace
memorylake actor get <tomas>                     # still there
```

The withdrawal itself is recorded on the pipeline project, not on the candidate:
`fact add --project <pipeline> "[pipeline · 2026-10-02] Tomás withdrew …"`. Eli's pipeline search now
runs over one candidate.

**Unbind is a pause, not an erasure.**

- **What it seals:** the candidate's memory, inside this workspace.
- **What it leaves:** the raw interview transcripts are still listed by `conv msg list` (the demo prints
  how many).
- **For an erasure request:** delete the conversations and the actor. Deleting the actor deletes every
  fact on it. That is what `python3 demo.py cleanup` does.

**7. Tomás applies again.**

```bash
memorylake actor bind --actor <tomas>
memorylake fact list --actors <tomas>            # the same fact ids as before the withdrawal
```

A re-engagement call with Eli on 2026-11-10 is appended and processed, and his scorecard is pinned. One
search then returns September and November together:

```
  What compensation are they expecting? — September and November, one search:
    [re-engage · said by candidate · revises an earlier answer · 2026-11-10] The person is targeting a $178,000 base salary (as of 2026-11-10, previously $170,000 as of 2026-09-15).
    [re-engage · Eli Brandt · 2026-11-10] Re-engaged for Controls Engineer, Safety Systems. Base expectation now $178k (was $170k in September) …
    [screen · Dana Okafor · 2026-09-15] Yes from the screen. … Base expectation $170k …
```

`out/report.json` keeps the debrief, both pipeline searches, the refusals and the before/after ids.

## Data

`data/team.json` holds the recruiting team, the project and the five debrief questions (each with the
words a hit must mention). `data/candidates/*.json` holds one file per candidate: the three stages
(turns and scorecard), plus Tomás's withdrawal and re-engagement call. Every turn starts with
`Name (role, company):`, which helps the extractor tell the candidate from the interviewer. All people
and companies are fictional.

## Troubleshooting

- **`not logged in`**: export `MEMORYLAKE_API_KEY`, or run `memorylake auth login` first.
- **The extracted facts are in another language.** MemoryLake sometimes writes extracted facts in a
  language other than the conversation's (one of our test runs got Italian from English interviews). The
  scorecards are pinned verbatim, so every debrief question still has an English answer. Extracted facts
  in another language are still counted and listed.
- **A different number of extracted facts on each run.** Extraction is not deterministic (we saw 21–23
  for Priya and 12–13 for Tomás before his November call). Nothing in the demo depends on one particular
  extracted fact.
- **`… is not bound to workspace` outside step 6**: a run was interrupted between `unbind` and `bind`.
  Run `python3 demo.py` again (setup re-binds every actor), or `python3 demo.py --reset`.
- **`memorylake actor list --workspace <ws>` shows actors you deleted.** Deleted actors stay in the
  workspace's binding list with `status: INACTIVE`. The demo only counts `ACTIVE` bindings. An unbound
  actor, by contrast, disappears from the list.
- **Gave up waiting for memory**: the service is slow; run again and the demo resumes where it stopped.
