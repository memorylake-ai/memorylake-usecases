# Reflection memory for a self-improving agent

A runnable version of the MemoryLake use case
[*Give Reflexion-Style Agents the Memory Their Self-Improvement Loop Requires*](https://www.memorylake.ai/en/usecase/memory-for-reflexion-style-self-improving-agents),
built entirely on the [`memorylake` CLI](https://github.com/memorylake-ai/memorylake-cli).

**The problem.** A Reflexion-style agent writes a thoughtful reflection on why a run failed. The reflection lives in the next
prompt and is then gone. Next run, same failure, same reflection. Self-improvement doesn't happen unless the reflections have
somewhere durable to live.

**What this demo shows.** Kestrel Labs (fictional) runs **Tern**, a CI triage agent. Between 2026-09-22 and 10-03 Tern got five
failures wrong, and after each one Dana, the on-call engineer, told it what had really happened:

| Run | Failure | What Tern did | What was really going on | Reflection (cause) |
|---|---|---|---|---|
| r-001 | `test_ledger_sync`: ECONNRESET against the staging DB | quarantined it | the DB restarted for patching | **retry once** before quarantining (environment) |
| r-002 | `test_checkout_refund`: HTTP 503 from the payments sandbox | quarantined it | the sandbox was down for maintenance | check the sandbox status page first (environment) |
| r-003 | `search-api` deploy failed its smoke check | rolled it back | the smoke runner had a stale DNS cache | rule out the smoke runner first (environment) |
| r-004 | `test_invoice_pdf` timed out | raised the timeout | a real 4× regression | compare duration history first (code) |
| r-005 | `test_ledger_sync`: ECONNRESET again | **retried once**, as r-001 says | a connection-pool leak, which reached production | **never retry**; open a pool-leak ticket (code) |

Each reflection is written into **Tern's own memory**, an agent scope: `fact add --agent`, then `fact update --metadata` with
the cause, the adjustment, the expected outcome and the signals it applies to. The runs themselves are conversations in the
team's CI project, so the incidents land on the project and the lessons land on the agent. Then:

1. **Two lessons disagree.** r-005's lesson contradicts r-001's. MemoryLake checks every fact written to an agent against what
   that agent already holds, and flags it on the agent (`fact conflict list --agent`, `m2m · logical`). While the conflict is
   open, Tern's plan for an ECONNRESET says *ask first*. `keep_fact` with r-005 as the evidence settles it, and r-001's lesson
   is forgotten.
2. **Today's failures.** Two failures come in. Tern's planning step searches its memory (`search --actors <Tern's actor>`) and
   plans around the lesson that matches. A **twin made with `agent fork`** has the same prompt and configuration but no memory,
   so it plans the same mistake Tern made in September.
3. **A pattern becomes a skill.** Two live lessons share the cause *environment*, so they are written up as a `SKILL.md`,
   published (`skill create`), checked by MemoryLake's security review (`safe`), and attached to **Tern version 2**
   (`agent version create --from-version latest --config`). Tern's memory is the same size before and after: memory is not
   configuration.
4. **Audit trail.** `fact trace` for every lesson (including the forgotten one), the conflict and its resolution
   (`fact conflict get`), each skill version with its review, and each agent version, in one timeline.

| Today's failure | Tern (its memory) | Twin (`agent fork`, no memory) |
|---|---|---|
| `test_refund_webhook`: HTTP 503 from the payments sandbox | **r-002** → check the sandbox status page; re-run after it recovers ✓ | no lesson → quarantine as flaky |
| `test_ledger_sync`: ECONNRESET against the staging DB | **r-005** → don't retry; open a connection-pool leak ticket ✓ | no lesson → quarantine as flaky |

```
runs        ── conv create --project <ci> --actors <Dana>,<Tern's actor> + msg append ×3 ──▶ project: the incidents
reflect     ── fact add --agent <Tern> + fact update --metadata (cause, adjustment, …), 15 s apart ──▶ agent: the lessons
detector    ── server: fact conflict list --agent (m2m · logical) ──▶ resolve --strategy keep_fact
plan        ── search "<failure>" --actors <Tern's actor>  vs  --actors <twin's actor> ──▶ plan from the matching lessons
skill       ── skill create --package SKILL.md.zip ──▶ skill get (security review: safe)
version 2   ── agent version create <Tern> --from-version latest --config {"skills": [...]} ──▶ agent version list
audit       ── fact trace · fact conflict get · skill version list · agent version list ──▶ out/tern-improvement.md
```

**Watch it run** (real recordings, unedited): *recording in progress*

Runs in about 4 minutes on a free personal account. Most of that is MemoryLake processing the five conversations, pacing the
writes (15 s apart) and waiting for the detector. The only credential you need is a MemoryLake API key. Nothing here calls an
LLM or `agent send`: Tern's planning step is the retrieval and a fixed rule, so the result is the same on every run.

**Measured** (every fresh run while building this demo, free account): the detector flagged r-005 against r-001 on the agent in
**5 of 5** fresh runs, 28–31 s after the last lesson, with **no** other conflict raised. In 1 of the 5 it also named a fact that
extraction had put on Tern ("The assistant retries a failing CI job once, following … r-001"), and `keep_fact` forgot that one
too. Both of today's plans retrieved the expected lesson at rank 1 in **5 of 5** runs, and the twin's search returned **0**
facts every time. The skill passed its security review within seconds in every run once the front matter was valid YAML (see
*If something goes wrong*).

## Prerequisites

1. **A MemoryLake account and API key.** Sign up at [app.memorylake.ai](https://app.memorylake.ai),
   open **API Keys**, create a key and copy it. Details: [Authentication and API Keys](https://docs.memorylake.ai/authentication).
2. **The `memorylake` CLI:**

   ```bash
   curl -fsSL https://raw.githubusercontent.com/memorylake-ai/memorylake-cli/main/scripts/install.sh | sh
   ```

   On Windows (PowerShell):

   ```powershell
   irm https://raw.githubusercontent.com/memorylake-ai/memorylake-cli/main/scripts/install.ps1 | iex
   ```
3. **Python 3.9+** (standard library only).

## Run it

```bash
git clone https://github.com/memorylake-ai/memorylake-usecases.git
cd memorylake-usecases/reflexion-memory

export MEMORYLAKE_API_KEY=sk-…
python3 demo.py
```

With `MEMORYLAKE_API_KEY` set, the demo logs into its own CLI profile under `./.memorylake-demo/`
(git-ignored) and leaves `~/.memorylake` alone. Without it, it uses your existing `memorylake auth login`.

| | |
|---|---|
| `MEMORYLAKE_BASE_URL` | API endpoint; default global, China is `https://app.memorylake.cn/openapi/memorylake` |
| `MEMORYLAKE_WORKSPACE` | Workspace id; default is the one remembered by `ws use`, else your default workspace |
| `python3 demo.py --reset` | Delete everything the demo created first, then replay the whole story |
| `python3 demo.py reflections` | What Tern remembers (agent scope), what the project holds, and the twin's (empty) memory |
| `python3 demo.py plan "test_invoice_pdf timed out in CI"` | Tern's and the twin's plan for any failure, from their own memory |
| `python3 demo.py audit` | Every conflict on Tern's memory, and the timeline of how Tern changed |
| `python3 demo.py cleanup` | Delete the conversations, **Tern's and the twin's memory**, both agents, the skill, the project and Dana |

It is safe to re-run. Everything is found again by `custom_id` (prefix `mlu-rfx-`) or by name, messages are appended only if the
conversation doesn't have them yet, and a lesson is written only if that exact text is not already in Tern's memory. The lesson
that an earlier run's `keep_fact` forgot is recognised from the resolved conflict's snapshots and is **not** written again,
otherwise every re-run would re-raise the same conflict. A re-run reads the earlier conflict instead of waiting for it.

## Or run it in the browser

```bash
python3 web/server.py        # http://127.0.0.1:8765 — standard library only
```

![Web companion — today's failures: Tern plans from the matching lesson, the twin falls back to its default playbook](https://github.com/memorylake-ai/memorylake-usecases/raw/main/reflexion-memory/web/screenshot-today.png)

Paste the key, press **Run demo**, and watch the runs and their reflections fill in, then Tern's memory next to the project's,
the detector's conflict with the plan before and after `keep_fact`, Tern and the twin side by side on today's failures, the
skill and the version table, and the audit timeline. **Plan a failure** runs the planning step for any failure you type, for
both agents. The terminal drawer shows every `memorylake` command as it runs.

## What happens, step by step

Output below is from a real run (ids shortened).

**1. Connect**: validate the key, pick a workspace.

**2. Agent, twin, CI project.** `agent create` makes Tern (version 1, with a system prompt), and the agent comes with **its own
actor**. Facts written with `--agent` are Tern's memory, and `search --actors <that actor>` searches it (`search` has no
`--agents`). `agent fork` copies Tern into a twin, which gets a new id, a new actor, the same configuration, and no memory:

```
$ memorylake agent create --name 'Tern (CI triage agent)' --custom-id mlu-rfx-tern --description '…' --system-prompt 'You are Tern, the CI triage agent …'
  · created the agent Tern (CI triage agent) → agent-… (version 1); it comes with its own actor actor-…, which is how its memory is searched
$ memorylake agent fork agent-… --custom-id mlu-rfx-tern-twin --name 'Tern twin (same prompt, no memory)'
```

**3. Five runs, five reflections.** Each run is a DIRECT conversation between Dana and Tern's actor in the CI project (three
messages with their real dates). The five cook in parallel. Then comes the Reflexion *write* step: each reflection goes
into Tern's memory verbatim, one every 15 seconds (back-to-back writes are checked for contradictions minutes later, as a
batch), typed with metadata:

```
$ memorylake fact add --agent agent-… 'Lesson from run r-005: never retry a test that fails with ECONNRESET against the staging database; open a connection-pool leak ticket for the service instead.'
$ memorylake fact update fact-… --agent agent-… --metadata '{"type": "reflection", "run": "r-005", "date": "2026-10-03", "kind": "test-failure", "cause": "code", "adjustment": "Do not retry an ECONNRESET failure against the staging database; open a connection-pool leak ticket for the service.", "expected": "No connection leak ships behind a passing retry.", "signals": "ECONNRESET|staging database"}'
```

**4. What Tern remembers.** The two scopes hold different things:

```
  Tern's memory (`fact list --agents agent-…`): 5 typed reflection(s)
    r-001  cause=environment Retry an ECONNRESET failure against the staging database once before quarantining the t…
    r-002  cause=environment Check the payments sandbox status page first; if the sandbox is down, re-run the test a…
    …
  Also on Tern, extracted from its own turns in the conversations (1; not relied on — extraction varies from run to run):
    · The assistant retries a failing CI job once, following the guidance in r-001 (as of 2026-10-03).

  The CI project (`fact list --projects proj-…`): 13 fact(s) about the incidents
    · The connection resets came from a connection-pool leak in ledger-sync that exhausted the production poo…
    · The search-api production deploy smoke check failed because the smoke runner had a stale DNS cache, not…
    …
  The twin (`fact list --agents agent-…`): 0 fact(s)
```

**5. Two lessons disagree.** The demo polls `fact conflict list --agent` until r-005's lesson is named by a conflict **and**
the last write is at least 30 s old, for up to 150 s. With the conflict open, Tern's plan for an ECONNRESET doesn't pick a side.
Then `keep_fact` keeps r-005:

```
  `fact conflict list --agent agent-…` — 1 conflict(s) naming r-005's lesson (after 30s):

  [m2m · logical] ECONNRESET retry guidance conflicts   cfl-…  OPEN
    Fact 0 says to never retry a test failing with ECONNRESET against the staging database, while facts 1 and 2 say to retry once …
    fact-…  (r-005 lesson)
    fact-…  (r-001 lesson)
    fact-…  (extracted from a run conversation)

  Tern's plan for “test_ledger_sync failed in the nightly run with ECONNRESET against th…” right now:
    #1 r-005 (2026-10-03, cause code): Do not retry an ECONNRESET failure against the staging database; open a connection-…
    #2 r-001 (2026-09-22, cause environment): Retry an ECONNRESET failure against the staging database once before quarantining t…
    plan:
      1. Two of my lessons disagree (cfl-…): ask the on-call engineer before …

$ memorylake fact conflict resolve cfl-… --agent agent-… --strategy keep_fact --keep-fact-id fact-…
  · resolved with keep_fact: forgotten fact-…, fact-…
```

**6. Today's failures.** Both agents run the same planning step: `search "<failure>" --actors <its actor> --types fact`. Since
`search` always returns its top k, a hit counts only if one of its signals appears in the failure; the rest are reported as
left out:

```
  ── test_refund_webhook failed in the nightly run with HTTP 503 from the payments sandbox.

  Tern (search --actors <Tern's actor>):
    #1 r-002 (2026-09-24, cause environment): Check the payments sandbox status page first; if the sandbox is down, re-run the te…
    (3 other hit(s) left out: none of their signals is in the task)
    plan:
      1. Check the payments sandbox status page first; if the sandbox is down, re-run the test after it reco…
      2. Only if those checks are clean: quarantine the test as flaky and open a ticket for its owner.
    ✓ top lesson r-002 (expected r-002)

  Twin (search --actors <twin's actor>):
    no lesson in memory matches this failure
    plan:
      1. Quarantine the test as flaky and open a ticket for its owner.
```

**7. Skill + version 2.** The live lessons with cause *environment* (r-002, r-003; r-001 was forgotten) become `out/SKILL.md`,
zipped and published. The security review takes a few seconds:

```
$ memorylake skill create --name mlu-rfx-rule-out-the-environment --title 'Rule out the environment first' --description 'Learned by Tern from runs r-002, r-003.' --package out/mlu-rfx-rule-out-the-environment.zip
  · published skill skill-… v1 — security review pending
$ memorylake skill get skill-…
  · security review: safe
$ memorylake agent version create agent-… --from-version latest --config out/tern-next-version.json
  · Tern is now version 2: same system prompt, plus the skill

  `agent version list agent-…`:
    v1  2026-10-10T06:29:16Z  no skills
    v2  2026-10-10T06:32:44Z  skill-… v1

  Tern's memory: 4 fact(s) before the new version, 4 after — memory is not configuration
  Twin: version 1, skills 0, memory 0 — forked from v1 before any of this
```

**8. Audit trail.** Written to `out/tern-improvement.md`:

```
  How Tern changed, in the order MemoryLake recorded it (13 events):
    2026-10-10T06:29:16Z  agent version 1                no skills
    2026-10-10T06:30:27Z  ADD · MANUAL                   r-001 lesson: Lesson from run r-001: retry a test that fails with ECONNRESE…
    …
    2026-10-10T06:31:42Z  conflict raised · m2m          ECONNRESET retry guidance conflicts
    2026-10-10T06:32:15Z  resolved · keep_fact           forgot: “Lesson from run r-001: retry a test that fails with ECONNRESET aga…
    2026-10-10T06:32:15Z  FORGET · MANUAL                r-001 lesson: Lesson from run r-001: retry a test that fails with ECONNRESE…
    2026-10-10T06:32:40Z  skill v1 · safe                mlu-rfx-rule-out-the-environment
    2026-10-10T06:32:44Z  agent version 2                skill-…
```

The times are MemoryLake's server times (when each change was recorded). The run dates are in each lesson's metadata.

## Wiring it into your own agent

- **One agent, one memory.** `agent create` gives the agent an actor; write its reflections with `fact add --agent <id>` and
  read them with `fact list --agents <id>` or `search --actors <its actor_id>`.
- **Type the reflections.** `fact add` stores the text verbatim and takes no metadata; follow it with `fact update --metadata`
  (cause, adjustment, expected outcome, the signals it applies to). `search` returns the metadata with each hit.
- **Filter the retrieval.** `search` returns its top k whether or not they match. Decide relevance yourself (here: a signal
  phrase that appears in the failure) and say how many hits you left out.
- **Pace the writes** (≈15 s) and read `fact conflict list --agent <id> --resolved false` before the agent acts on two lessons
  that might disagree. Resolve with `keep_fact`; `fact conflict get` keeps the forgotten lesson's text.
- **Memory is not configuration.** `agent version create` and `agent fork` copy configuration (prompt, skills); neither touches
  memory. A fork starts with none.
- **Delete an agent's memory before the agent.** After `agent delete`, the agent's facts are still readable
  (`fact list --agents`, `search --actors`), but every write, including `fact delete`, is refused with `ACTOR_INACTIVE`.
  `python3 demo.py cleanup` deletes every fact first and refuses to delete an agent that still has any.

## If something goes wrong

- **The skill's security review says `blocked`**: check the `SKILL.md` front matter is valid YAML. An unquoted colon in
  `description:` (`… (cause: environment).`) is enough to get it blocked, and the review gives no reason. The demo quotes it.
  If an earlier copy published a blocked version, the next run publishes a new version over it.
- **"✗ nothing raised" within 150 s**: the check was deferred (it happens when facts were written back to back). Run
  `python3 demo.py audit` a few minutes later and it is there; `python3 demo.py` then resumes and resolves it.
- **A conflict names a third fact**: extraction sometimes puts a sentence like "the assistant retries once, following r-001"
  on Tern from its own turn in a conversation. The detector includes it, and `keep_fact` forgets it along with r-001's lesson.
- **`tls handshake eof` / `could not connect`** while polling: a dropped connection. Read-only commands retry four times;
  otherwise re-run `python3 demo.py` and it resumes.
- **`401 service account not found`**: the key was deleted or belongs to the other deployment.
- **Conflict names differ from the ones above** (sometimes in another language): names and descriptions are written by the
  server per run. The demo never matches on them, only on the fact ids.

## Files

```
demo.py                  the runner; prints every CLI command it runs (and emits events for the web app)
data/agent.json          the team, Tern, Dana, the five runs with their reflections, today's failures and the skill rule
web/server.py            local web companion (stdlib HTTP server + Server-Sent Events over demo.py)
web/static/              the page — no build step
out/                     SKILL.md, the skill package, the version config, the audit trail and runs.jsonl (git-ignored)
```

All names, the team and the services are fictional.
