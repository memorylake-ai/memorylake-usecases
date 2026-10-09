# Onboarding memory that answers with the policy in force

A runnable version of the MemoryLake use case
[*Give HR Teams Onboarding Memory That Scales Beyond Slack Threads*](https://www.memorylake.ai/en/usecase/onboarding-memory-for-hr-teams),
built entirely on the [`memorylake` CLI](https://github.com/memorylake-ai/memorylake-cli).

**The problem.** HR answers the same onboarding questions every hiring cycle, and policies change in
between. The handbook from January still says the old numbers, the update lives in a memo or a Slack
thread, and an AI tool that finds the handbook first gives a confident, wrong answer. And when an auditor
asks "what was the policy in March?", someone digs through an archive.

**What this demo shows.** Fernhill Robotics (fictional, about 400 people) changed parental leave from
16 to 20 weeks on 2026-07-01, changed its travel meal allowance twice, and has signed off a 25-day
vacation allowance that only starts on 2027-01-01. Two people start on 2026-10-12: Priya Nair, a senior
software engineer, and Tom Alvarez, an engineering intern. The demo builds:

- **a policies project** — the January handbook (PDF) and two policy-update memos, plus **every version of
  every policy pinned as a fact**, stamped with its id, version, effective date, sign-off and source:
  `POL-LEAVE v2 · Parental leave · effective 2026-07-01 · signed off 2026-06-12 by Dana Whitfield · source policy-update-memo-2026-06.md §1 — 20 weeks …`.
  Facts are immutable; a new version is a new fact and the old one stays. That is the audit trail;
- **one actor per new hire** — the onboarding stage is a **tag** (`stage:pre-boarding`), the role, start
  date and manager are **metadata**; Priya's pre-boarding chat with the onboarding assistant becomes facts
  about her.

Then:

- **The current answer, with stale ones marked.** Priya asks "How long is parental leave?". The search
  brings back **both** the 16-week and the 20-week version, and the January handbook. Reading the stamps,
  the latest version whose effective date has passed is the answer; the older version, and the handbook
  that only backs it, are marked stale and not used. The answer carries its sign-off and source.
- **An audit for any date.** `fact list` returns every version; "what was in force on 2026-03-15?" gives
  16 weeks of leave and USD 50 for meals, today gives 20 weeks and USD 60 — and the 25-day vacation
  allowance shows up as scheduled, not in force.
- **Day one, a plan per role.** `actor update` moves both hires to `stage:day-1`; `actor list --tags
  stage:day-1` finds who starts today, and the same question — "What happens on my first day?" — gives
  the engineer and the intern different plans, picked by the role in their metadata.

```
handbook PDF + memos ── import ──▶ project: policies ◀── fact add --project  (every version, stamped; role flows)
                                         │
pre-boarding chat ── conversation ──▶ project: chats ──▶ facts about Priya (her actor)
                                         │
ask:   search --projects <policies> --actors <Priya>  →  read the stamps → in force / stale / scheduled
audit: fact list --projects <policies>                →  the version in force on any date
day 1: actor update --tags stage:day-1 → actor list --tags stage:day-1 → search per hire → flow for metadata.role
```

Runs in about 2½ minutes on a free personal account (most of it is MemoryLake parsing the files and
extracting facts from the chat). The only credential you need is a MemoryLake API key.

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
cd memorylake-usecases/onboarding-memory

export MEMORYLAKE_API_KEY=sk-…
python3 demo.py
```

With `MEMORYLAKE_API_KEY` set, the demo logs into its own CLI profile under `./.memorylake-demo/`
(git-ignored) and leaves `~/.memorylake` alone. Without it, it uses your existing `memorylake auth login`.

| | |
|---|---|
| `MEMORYLAKE_BASE_URL` | API endpoint; default global, China is `https://app.memorylake.cn/openapi/memorylake` |
| `MEMORYLAKE_WORKSPACE` | Workspace id; default is the one remembered by `ws use`, else your default workspace |
| `python3 demo.py --reset` | Delete the chat, both projects, the new-hire actors and the Library folder first, then start over |
| `python3 demo.py ask` | Priya's three questions again: the version in force, stale ones marked |
| `python3 demo.py audit --as-of 2026-05-01` | Which version of every policy was in force on that date (repeat `--as-of` for several dates; default 2026-03-15 and today) |
| `python3 demo.py brief` | The questions, the first-day plans, then rewrite `out/onboarding-brief-priya-nair.md` |
| `python3 demo.py facts` | Print what each scope holds |
| `python3 demo.py cleanup` | Delete the chat, both projects (with their documents and facts), the actors (with what was remembered about them) and the Library folder |

Safe to re-run: everything is found again by `custom_id` (prefix `mlu-ohr-`) or by folder name;
re-imports are reported as already in the project, the chat resumes where it stopped, a policy version is
pinned only if that exact text is not already in the project, and the new hires are put back to
`stage:pre-boarding` so step 8 can move them on again.

"Today" is the date you run it: the answers and the audit are computed for that day. Run it after
2027-01-01 and the 25-day vacation allowance is the one in force.

## Or run it in the browser

```bash
python3 web/server.py        # http://127.0.0.1:8765 — standard library only
```

![Web companion — Priya's question: the version in force, the stale version and the stale handbook](https://github.com/memorylake-ai/memorylake-usecases/raw/main/onboarding-memory/web/screenshot-answers.png)

Paste the key, press **Run demo**, and watch the files import, every policy version get pinned on its
timeline, Priya's chat turn into facts about her, each answer come back with its versions marked in force,
stale or scheduled, the audit tables for March and today, both hires move to `stage:day-1` with a plan
each, and the brief fill in. **Audit** takes any date; **Ask the policies** asks as either hire, today or
on a date you pick. The terminal drawer shows every `memorylake` command as it runs.

## What happens, step by step

**1. Connect** — validate the key, pick a workspace.

**2. Set up.** Two projects, the onboarding assistant, and one actor per new hire — stage in the tags,
role in the metadata:

```bash
memorylake proj create --name "Fernhill people policies" --custom-id mlu-ohr-policies …
memorylake proj create --name "Fernhill onboarding chats" --custom-id mlu-ohr-chats …
memorylake actor create --custom-id mlu-ohr-priya-nair --display-name "Priya Nair" --type HUMAN \
  --tags new-hire,stage:pre-boarding \
  --metadata '{"role":"engineer","start_date":"2026-10-12","team":"robot-fleet","manager":"Jonas Berg","office":"Denver","stage":"pre-boarding"}'
```

The chat gets its own project on purpose: facts extracted from a conversation land on the project it
belongs to, and the policies project must hold nothing but the signed-off text.

**3. The handbook and both memos** go into the Library and, with one recursive import, into the policies
project:

```
$ memorylake proj doc import --project proj-… <handbook folder> --recursive --wait
  Importing 3 file(s) resolved from 1 argument(s)
  · Fernhill people policies: 3 new, 0 already in the project, 0 failed (26s)

  Fernhill people policies: 3 document(s)
   - employee-handbook-2026-01.pdf            okay
   - policy-update-memo-2026-06.md            okay
   - policy-update-memo-2026-09.md            okay
```

**4. Every version, pinned.** Eight policy versions and four role-specific flows, one `fact add` each batch:

```
  POL-LEAVE  Parental leave
   📌 v1  effective 2026-01-01  signed off 2025-12-10 by Dana Whitfield  (employee-handbook-2026-01.pdf §4.2)
        16 weeks of fully paid leave for the primary caregiver and 4 weeks for the secondary caregiver, after 6 months of service.
   📌 v2  effective 2026-07-01  signed off 2026-06-12 by Dana Whitfield  (policy-update-memo-2026-06.md §1)
        20 weeks of fully paid leave for every parent, available from the first day of employment.
```

We checked what the server does with two versions of the same policy: nothing — 90 seconds after pinning
both, both are still `expired: false` and a search returns both. So the stamp is what decides, and every
version stays queryable for the audit.

**5. Priya's pre-boarding chat** (a DIRECT conversation dated 2026-10-05) is appended message by message;
once MemoryLake has processed it, her actor holds facts like these (one run):

```
  Priya Nair (new-hire actor): 8 fact(s)
   - The person and their partner are expecting their first child at the end of November (as of 2026-10-05).
   - The person prefers a Linux laptop and has used Ubuntu for ten years (as of 2026-10-05).
   - The user will travel to the Boston lab for two days in their first month (as of 2026-10-05).
   …
  Fernhill people policies (project): 12 fact(s)
   📌 12 of 12 pinned versions and flows, stored verbatim
```

**6. Priya asks.** One search per question, scoped to the policies project and Priya's actor:

```
$ memorylake search 'How long is parental leave?' --projects proj-… --actors actor-… --top-k 6

  Q: How long is parental leave?   (asked by Priya Nair, answered as of 2026-10-09)
     retrieved 2 version(s) of POL-LEAVE Parental leave and 2 source document(s)
     ✓ CURRENT   v2 · effective 2026-07-01 · signed off 2026-06-12 by Dana Whitfield
                 20 weeks of fully paid leave for every parent, available from the first day of employment.
                 source: policy-update-memo-2026-06.md §1  (✓ in the policies project)
     ✗ STALE     v1 · effective 2026-01-01 · superseded by v2 on 2026-07-01
                 16 weeks of fully paid leave for the primary caregiver and 4 weeks for the secondary caregiver, after 6 months of service.
     ✗ STALE DOC employee-handbook-2026-01.pdf — only cites v1, no longer in force
     ✓ SOURCE    policy-update-memo-2026-06.md — cites the version in force
     (1 other hit(s) not about parental leave, left out)
  → 20 weeks of fully paid leave for every parent, available from the first day of employment.  [POL-LEAVE v2, effective 2026-07-01]
```

The meal allowance comes back in three versions (v3, USD 60 with receipts above USD 25, is in force);
vacation days come back as v1 in force plus v2 *scheduled* for 2027-01-01. Hits that are not about the
question (a search always returns its top-k) are left out and counted, never hidden.

**7. Audit.** `fact list --projects <policies>` returns every version; each policy's version in force on a
date is the latest one effective on or before it. A version signed off after that date did not exist yet,
so it is not even listed as scheduled:

```
  In force on 2026-03-15:
   POL-LEAVE  v1  (effective 2026-01-01, signed off 2025-12-10 by Dana Whitfield, employee-handbook-2026-01.pdf §4.2)
   POL-MEAL   v1  (effective 2025-10-01, signed off 2025-09-15 by Omar Reyes, employee-handbook-2026-01.pdf §6.1)
   …
  In force on 2026-10-09:
   POL-LEAVE  v2  (effective 2026-07-01, signed off 2026-06-12 by Dana Whitfield, policy-update-memo-2026-06.md §1)
   POL-MEAL   v3  (effective 2026-09-15, signed off 2026-09-01 by Omar Reyes, policy-update-memo-2026-09.md §1)
   POL-PTO    v1  … scheduled: v2 from 2027-01-01
   …
  Changed between 2026-03-15 and 2026-10-09: POL-LEAVE, POL-MEAL
```

**8. Day one.** Both flags of `actor update` **replace** what is stored — send every tag and every
metadata key you want to keep:

```
$ memorylake actor list --tags stage:day-1
  · 0 actor(s) tagged stage:day-1 before today
$ memorylake actor update actor-… --tags new-hire,stage:day-1 --metadata '{"role":"engineer",…,"stage":"day-1"}'
$ memorylake actor update actor-… --tags new-hire,stage:day-1 --metadata '{"role":"intern",…,"stage":"day-1"}'
$ memorylake actor list --tags stage:day-1
  · 2 actor(s) tagged stage:day-1 now: Tom Alvarez, Priya Nair

  Priya Nair  · metadata.role=engineer · tags new-hire, stage:day-1
   Q: What happens on my first day?
   → 09:00 laptop pickup at the IT desk (Linux or macOS); 10:00 security and secrets-handling briefing; … [FLOW engineer · day-1]

  Tom Alvarez  · metadata.role=intern · tags new-hire, stage:day-1
   Q: What happens on my first day?
   → 09:30 welcome session for the intern cohort; 10:30 laptop pickup (macOS); 11:00 meet your mentor; … [FLOW intern · day-1]

  ✓ same question, 2 different first-day plans — picked by each hire's metadata.role
```

**9. Priya's onboarding brief** — `out/onboarding-brief-priya-nair.md`: each answer with its version,
sign-off and source, the stale versions named ("Not this: v1 …; employee-handbook-2026-01.pdf
(outdated)"), the upcoming change, the policies in force today, her first-day plan and what the assistant
remembers about her.

## Wiring it into your own HR stack

- **One fact per policy version, never edited.** Put a machine-readable stamp at the start —
  policy id, version, effective date, sign-off, source — and pin with `fact add --project <policies>`.
  When a policy changes, pin the new version; don't delete the old one.
- **Resolve on read.** Your HR bot searches `--projects <policies> --actors <employee>`, groups the hits by
  policy id, and answers with the latest version effective today. Treat documents the same way: a
  document is only as current as the versions it backs.
- **Audit with `fact list`, not search.** Search ranks and truncates; `fact list --projects` gives every
  version, so "what applied on date X" is complete.
- **Lifecycle in tags, profile in metadata.** `actor update --tags` moves a person through stages and
  `actor list --tags stage:…` finds everyone at a stage. Both `--tags` and `--metadata` replace the stored
  value, so always send the full set.
- **Keep conversations out of the policies project**, or the extractor's facts mix with signed-off text.

## If something goes wrong

- **`tls handshake eof` / `could not connect`**: a dropped connection or VPN. Read-only commands retry
  four times; otherwise re-run `python3 demo.py` and it resumes. A failed `auth login` prints the command
  with the key masked as `sk-…`.
- **`401 service account not found`**: the key was deleted or belongs to the other deployment.
- **The facts about Priya differ between runs**: extraction is server-side and varies, and the "about
  Priya" lines in step 6 depend on which of them the search ranks in. Nothing else depends on them: the
  answers, the audit and the plans come from the pinned versions and flows.
- **`actor update` lost a tag or a metadata key**: both flags replace the stored value; the server does
  not merge.
- **`fact list` without `--projects` or `--actors`** is rejected by the CLI; always give a scope.

## Files

```
demo.py                                   the runner; prints every CLI command it runs (and emits events for the web app)
data/policies.json                        every policy version (with its stamp) and the role-specific onboarding flows
data/library/handbook/*.pdf|*.md          the January 2026 handbook and the June and September policy memos
data/sessions/*.json                      Priya's pre-boarding chat
data/make_sources.py                      regenerates the handbook PDF (optional; needs reportlab)
web/server.py                             local web companion (stdlib HTTP server + Server-Sent Events over demo.py)
web/static/                               the page — no build step
out/                                      the onboarding brief (git-ignored)
```

All names, companies and figures are fictional.
