# UX research memory

A runnable version of the MemoryLake use case
[*Give UX Researchers AI That Retains Every User Insight Across the Project Lifecycle*](https://www.memorylake.ai/en/usecase/ai-memory-for-ux-researchers),
built entirely on the [`memorylake` CLI](https://github.com/memorylake-ai/memorylake-cli).

**Watch it run:** recording in progress.

**The problem.** "When a later study contradicts an earlier finding, MemoryLake flags the conflict so you have an
explicit record of where the evidence shifted." It does. What happens next is up to the team, and the common habit is
"the most recent study wins". That is right when the same kind of people were asked again after something changed. It
is wrong when the two studies asked *different* people. The synthesis wrote both findings as "New users …", the sample
fell off on the way, and keeping the newer one quietly deletes a finding that is still true for everyone the newer
study didn't talk to.

**What this demo shows.** Ledgerline (a fictional invoicing SaaS for small businesses) ran three interview studies in
2026:

| Round | Study | Participants | Findings (as the synthesis wrote them) |
|---|---|---|---|
| R1 · 2026-03-12 | Onboarding drop-off study (Mina Okafor) | P01–P08, self-serve signups who churned in week one | CSV import is the main setup blocker · pricing page unclear about which plan has CSV import · wants SMS reminders |
| R2 · 2026-06-18 | Pricing comprehension follow-up (Mina Okafor) | P09–P14, same segment, after the 2026-05-20 pricing page redesign | pricing page **clear** · can't find duplicate-invoice |
| R3 · 2026-09-23 | Enterprise trial setup study (Theo Brandt) | E01–E08, enterprise trial admins | CSV import is **not** a blocker, SSO is · needs an audit log export |

Each finding keeps its participant ids in metadata; each participant is a roster fact with a segment. MemoryLake's
conflict check flags two pairs: R1 vs R3 on CSV import, R1 vs R2 on the pricing page. The same research then runs in two
projects that differ only in how they resolve those two conflicts:

| | Latest study wins | Check who said it |
|---|---|---|
| CSV import (R1 vs R3) | keep R3, **forget R1** | different participants (8 churned self-serve vs 8 enterprise admins) → **segment split**: dismiss, rewrite both with their sample |
| Pricing page (R1 vs R2) | keep R2, forget R1 | same segment, R2 after the redesign → **reversal**: keep R2, forget R1 |
| New conflicts after the rewrite | — | **0**: the check agrees the scoped findings can both be true |
| Q4: "Is the CSV import step a setup blocker for new users?" | one finding: "New users complete the CSV import step without trouble" — from the people who are **26%** of new signups | both scoped findings — **100%** of new signups |

The column-mapping screen has not changed since January. Nothing made March's finding untrue. A different group of
people was asked.

```
setup ──────── proj create  ×2                         (same facts, same order, both projects)
record ─────── fact add '<participant>' … '<context>' …  (27 facts, one call)  + fact update --metadata {segment, …}
               fact add '<R1 findings>' → 15 s → '<R2 findings>' → 15 s → '<R3 findings>'  + metadata {participants, study, …}
flagged ────── fact conflict list --project <p>         (two m2m conflicts in each project)
latest wins ── fact conflict resolve <c> --strategy keep_fact --keep-fact-id <newer>   ×2
check ──────── finding.metadata.participants → roster facts' segment → same segment? (plain code)
resolve ────── reversal: keep_fact <newer>
               split:    resolve --strategy dismiss → fact delete ×2 → fact add '<finding (who, n, study)>' ×2
               → fact conflict list again after 60 s: nothing new
Q4 question ── search '<question>' --projects <p> --types fact --top-k 8   (both projects)
```

No conversations and no extraction: every fact is pinned word for word, and every decision is plain code over what the
CLI returns. The conflict check is MemoryLake's own, server side.

## Prerequisites

- **Python 3.9+** (standard library only, nothing to `pip install`)
- **The `memorylake` CLI** on your `PATH`:
  ```bash
  curl -fsSL https://raw.githubusercontent.com/memorylake-ai/memorylake-cli/main/scripts/install.sh | sh
  ```
- **A MemoryLake API key.** Sign up at [app.memorylake.ai](https://app.memorylake.ai), open **API Keys**, create one. A free
  personal account is enough: nothing here needs an agent or a model call.

## Run it

```bash
git clone https://github.com/memorylake-ai/memorylake-usecases.git
cd memorylake-usecases/ux-research-memory
export MEMORYLAKE_API_KEY=sk-...           # China region: also export MEMORYLAKE_BASE_URL=https://app.memorylake.cn/openapi/memorylake
python3 demo.py
```

About 3½–4½ minutes (two projects × 34 facts with their metadata, 15 s between studies, waiting for the conflict check twice). A resolved conflict can't be un-resolved (a forgotten finding pinned back would just raise the same
conflict again), so every run starts from the first study: if the demo's projects exist from an earlier run, it deletes
them and starts over, and says so. With the key set, the demo logs into an isolated CLI profile under
`./.memorylake-demo/` and never touches your own `~/.memorylake`. Everything it creates has a `custom_id` starting with
`mlu-uxr-`.

Then ask both projects anything, or list their conflicts and how each one reads:

```bash
python3 demo.py ask "What blocks new users during setup?"
python3 demo.py conflicts
python3 demo.py cleanup                    # deletes both projects and every fact on them
```

## Or run it in the browser

```bash
python3 web/server.py                # opens http://127.0.0.1:8765
```

Paste the key, connect, run. The page drives the same functions as `demo.py` and streams every `memorylake` command into a
terminal drawer as it runs: the three studies, the flagged conflicts, both resolutions side by side, and the Q4 question
in both projects. **Ask** sends any question to both projects and lists the conflicts.

## What happens, step by step

### Steps 2–3: the same research, twice

Two projects, `mlu-uxr-latest` and `mlu-uxr-provenance`, get identical facts in identical order. First the 22 participants
and 5 context notes in one `fact add` (each then gets `fact update --metadata`: a participant's `segment`, a context
note's date), then each study's findings, 15 s apart, so each study is checked against what was already there:

```
  R1 2026-03-12  Onboarding drop-off study — Mina Okafor, 8 churned self-serve signups
      R1-import     Finding: New users abandon onboarding at the CSV import step because the column-mapping screen …
      R1-pricing    Finding: New users cannot tell which plan includes CSV import; the pricing page is unclear abou…
      R1-reminders  Finding: New users want overdue-invoice reminders sent by text message, not only by email.
  R2 2026-06-18  Pricing comprehension follow-up — Mina Okafor, 6 churned self-serve signups
  …
  R3 2026-09-23  Enterprise trial setup study — Theo Brandt, 8 enterprise trial admins
      R3-import     Finding: New users complete the CSV import step without trouble; the column-mapping screen is c…
```

A finding's metadata names its study and participant ids (`"participants": ["P01", …, "P08"]`); its text says "New users".

### Step 4: flagged

```
$ memorylake fact conflict list --project <latest> --page-size 100
  · both projects flagged after 38 s (last write 38 s ago)

  m2m “CSV import blocker vs not a blocker”
      R1 2026-03-12  Finding: New users abandon onboarding at the CSV import step because the column-mapping screen is c…
      R3 2026-09-23  Finding: New users complete the CSV import step without trouble; the column-mapping screen is clear…

  m2m “Pricing page clarity on CSV import plan limits”
      R1 2026-03-12  Finding: New users cannot tell which plan includes CSV import; the pricing page is unclear about pl…
      R2 2026-06-18  Finding: New users can tell which plan includes CSV import; the pricing page is clear about plan li…
```

Exactly these two, in both projects, every run. The conflict names are written by the server and change from run to run.

### Step 5: latest study wins

```
$ memorylake fact conflict resolve <c1> --project <latest> --strategy keep_fact --keep-fact-id <R3-import>
$ memorylake fact conflict resolve <c2> --project <latest> --strategy keep_fact --keep-fact-id <R2-pricing>

  findings left on the project: 5 of 7  (forgotten: R1-import, R1-pricing)
```

### Step 6: check who said it

Plain code over `fact list` and `fact conflict list`: each side's participants → their roster facts → segments.

```
  “CSV import as onboarding blocker disagreement”
      R1 Onboarding drop-off study          P01, P02, P03…P08  → 8 churned self-serve signups
      R3 Enterprise trial setup study       E01, E02, E03…E08  → 8 enterprise trial admins
      → SEGMENT SPLIT — different people; both findings stand, each for its own segment

  “CSV import plan clarity disagreement”
      R1 Onboarding drop-off study          P01, P02, P03…P08  → 8 churned self-serve signups
      R2 Pricing comprehension follow-up    P09, P10, P11…P14  → 6 churned self-serve signups
      → REVERSAL — same kind of participant, later study; the newer finding replaces the older
        (… R2 came later, after the 2026-05-20 change: “the pricing page was redesigned with a plan comparison table …”)
```

### Step 7: resolve each one its own way

```
$ memorylake fact conflict resolve <c1> --project <provenance> --strategy dismiss
$ memorylake fact delete --project <provenance> <R1-import>          (and R3-import)
$ memorylake fact add --project <provenance> 'Finding (churned self-serve signups, n=8, Onboarding drop-off study, March 2026): these users abandon …' 'Finding (enterprise trial admins, n=8, Enterprise trial setup study, September 2026): these users complete …'
$ memorylake fact conflict resolve <c2> --project <provenance> --strategy keep_fact --keep-fact-id <R2-pricing>
$ memorylake fact conflict list --project <provenance>                (again, until 60 s after the rewrite)

  new conflicts after the rewrite: 0 — the check itself agrees the scoped findings can both be true
  findings on the project: 6 of 7
```

The rewrite is the test. Written as "New users …", the same two claims were flagged every time; written with their
sample, never (see Measured).

### Step 8: the Q4 question

```
$ memorylake search 'Is the CSV import step a setup blocker for new users?' --projects <latest> --types fact --top-k 8
$ memorylake search 'Is the CSV import step a setup blocker for new users?' --projects <provenance> --types fact --top-k 8

  Ledgerline research (latest study wins): 1 finding(s) on the import step in the top 8
      #1   R3-import   Finding: New users complete the CSV import step without trouble; the column-mapping screen is cle…
      → the participants behind it: enterprise trial admins = 26% of new signups

  Ledgerline research (check who said it): 2 finding(s) on the import step in the top 8
      #1   R3-import   Finding (enterprise trial admins, n=8, Enterprise trial setup study, September 2026): these users…
      #3   R1-import   Finding (churned self-serve signups, n=8, Onboarding drop-off study, March 2026): these users aba…
      → the participants behind it: churned self-serve signups + enterprise trial admins = 100% of new signups
```

The percentage is computed, not asserted: the participants behind each import-step finding that search returned, by
segment, against the signup mix pinned in the project (74% self-serve, 26% enterprise).

## Measured

| What | Result |
|---|---|
| Conflicts flagged, as written ("New users …") | exactly the two pairs (CSV import R1×R3, pricing R1×R2), in both projects: 4 / 4 demo runs × 2 projects, plus 6 / 6 probe projects for the CSV pair and 3 / 3 for the pricing pair; 0 unexpected; 38–40 s after the last write |
| Check who said it: CSV import → split, pricing → reversal | 4 / 4 runs |
| New conflicts after the scoped rewrite (60 s window) | 0 in 4 / 4 runs; probes: the scoped pair alone, 0 / 5 over 180 s |
| Q4 question, findings on the import step in the top 8 | latest study wins: R3 only (26%) · check who said it: R1 + R3 (100%) — 3 / 3 runs after a filter fix (the first run also counted the pricing finding as "about the import step") |
| Time | 215–275 s per run (including the cleanup of an earlier run's projects) |

## Wiring it into your own research memory

- **Keep the sample with the finding.** Store each finding with the participant ids behind it (metadata), and each
  participant with their segment. A synthesis that writes "users" loses that; the ids are what let you check later.
- **Let the conflict check flag, don't let a rule resolve.** Before `keep_fact`, compare who was in the room on each
  side. Same people asked again after a change → keep the newer one. Different people → `dismiss`, then rewrite both
  findings with their sample, so the record says who each one is about.
- **Use the check as the test of the rewrite.** A rewritten pair is a new write and gets checked; if it is flagged again,
  the scoping didn't say enough.
- Keep a change log in the same project (redesigns, releases). It is what tells a reversal from a stale finding.

## If something goes wrong

- **`the memorylake CLI is not on PATH`**: install it (above) or `export MEMORYLAKE_BIN=/path/to/memorylake`.
- **"not all expected conflicts within 150 s"**: after many writes in a row the conflict check can run deferred, minutes
  later. Wait, then `python3 demo.py conflicts`; or run the demo again.
- **"new conflicts after the rewrite: 1"**: the check read the two scoped findings as contradicting after all. The demo
  prints the conflict as it is, it doesn't hide it.
- **Search results differ on your run**: ranking moves between runs. Step 8 prints exactly what came back; the
  percentages come from the participants behind the findings that came back.
- **Something half-created after an interrupted run**: `python3 demo.py cleanup`, then run again.

## Files

```
ux-research-memory/
├── demo.py               # the CLI runner (every step is a plain `memorylake` command, echoed)
├── data/research.json    # 3 studies, 22 participants, 7 findings, 5 context notes (fictional, plain ASCII)
├── web/server.py         # local web companion: same functions, Server-Sent Events
├── web/static/           # no-build front end
└── out/runs.jsonl        # one line per run (git-ignored)
```
