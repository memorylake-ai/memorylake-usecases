# Buyer profile memory for a real estate team

A runnable version of the MemoryLake use case
[*Give Real Estate Teams Buyer Profile Memory That Holds Across Every Showing and Agent*](https://www.memorylake.ai/en/usecase/buyer-profile-memory-for-real-estate-teams),
built entirely on the [`memorylake` CLI](https://github.com/memorylake-ai/memorylake-cli).

**The problem.** A buyer tells the first agent she hates cul-de-sacs. The second agent shows her one, she loves it, and
writes "cul-de-sac is fine" in his notes. Now the buyer's record says two opposite things. The third agent, taking her over
cold, either never sees the dealbreaker or never learns it changed.

**What this demo shows.** Harborline Realty (fictional) keeps two kinds of memory for buyer Lena Park:

- **her profile, on her own actor**: dealbreakers, budget, financing, preferences. Each one is pinned verbatim (`fact add --actor`), with the rule, the agent and the date as metadata;
- **the showing history, on the team project**: each showing as an event (listing, street type, price, how she reacted), read by every agent covering her.

| Who | When | What they write |
|---|---|---|
| Priya Shah | 2026-09-02 kickoff | 4 profile facts: **no cul-de-sac (dealbreaker)**, **max 800,000 (hard ceiling)**, FHA loan (no major repairs), quiet street |
| Tom Becker | 2026-09-20 / 09-27 | 4 showings on the team project. She **loved** both cul-de-sac houses, one of them at 815,000 |
| Tom Becker | 2026-09-27 | 2 call notes on her profile: **"happy to buy on a cul-de-sac"**, **"max 850,000"** |
| Maya Ortiz | 2026-10-08, today | takes Lena over |

MemoryLake checks every fact written to an actor against what that actor already holds. When the two **cannot both be true** it
raises a conflict on the actor: `fact conflict list --actor` (category `m2m`, type `logical`). Tom's two notes each raise one.
Every earlier conflict demo in this repository was on a project. This one is on a person.

Then Maya:

1. **screens her tour against Lena's memory.** Where two live facts on Lena's actor disagree, the screen does not guess. It says *ask Lena first* and names the conflict;
2. **compares what Lena said with what she responded to** in the showing history (said "no cul-de-sac" → loved 2 of 2 cul-de-sac houses);
3. **resolves both conflicts with `keep_fact`**, keeping Tom's notes because the showings back them. Priya's two kickoff notes are **forgotten**;
4. **screens the tour again**, and writes a handoff record from `fact conflict get`, which keeps the forgotten notes word for word.

| Tour listing | Before (both versions live) | After `keep_fact` |
|---|---|---|
| 5 Heron Court (cul-de-sac, 790,000) | **? ask Lena first** (`cfl-…` cul-de-sac) | **✓ show it** |
| 41 Elm Street (through street, 840,000) | **? ask Lena first** (`cfl-…` budget) | **✓ show it** |
| 17 Birch Way (needs a new roof, 735,000) | ✗ skip (FHA: no major repairs) | ✗ skip |
| 602 Harbor Boulevard (busy road, 780,000) | ! show, but flag it (quiet street) | ! show, but flag it |

```
kickoff  ── fact add --actor <Lena> + fact update --metadata (one every 15 s) ──▶ actor: Lena's profile
showings ── fact add --project <team> + metadata (event, listing, reaction)   ──▶ project: team history
call notes ─ fact add --actor <Lena> ──▶ server: fact conflict list --actor (m2m · logical)
takeover ── fact list --actors + fact list --projects ──▶ tour screen (? where two facts disagree) · said vs responded
resolve  ── fact conflict resolve --actor … --strategy keep_fact --keep-fact-id <Tom's note>  ──▶ the other fact is forgotten
record   ── fact conflict get (strategy, kept, forgotten, snapshots) ──▶ out/buyer-profile-lena-park.md
ask      ── search --projects <team> --actors <Lena>  (profile + showings, one search)
```

**Watch it run** (real recordings, unedited): recording in progress.

Runs in about 3½ minutes on a free personal account. Most of that is pacing the writes (15 s apart) and waiting for the
detector. The only credential you need is a MemoryLake API key.

**Measured** (all runs while building this demo, free account): the detector raised **both** conflicts on Lena's actor in
**6 of 6** fresh runs, 29–34 s after Tom's second note. It raised **nothing else** in any of them: no conflict among the
kickoff facts, none on the financing or quiet-street facts, none on the showings. Conflict names and descriptions are written
by the server and differ from run to run; the category and the facts they name do not.

**Why the showings are not on Lena's profile.** We first put everything on her actor. With *"she called this cul-de-sac house
her favorite"* sitting next to the dealbreaker, the detector stayed quiet about the cul-de-sac contradiction in **0 of 7**
runs (the budget one still came up). With no showing on the actor, it raised it in **4 of 4** probe runs. With the showings on the team project, it
raised it in every demo run above. Profile on the person, events on the team, is also how the use case page describes it.

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
cd memorylake-usecases/buyer-profile-memory

export MEMORYLAKE_API_KEY=sk-…
python3 demo.py
```

With `MEMORYLAKE_API_KEY` set, the demo logs into its own CLI profile under `./.memorylake-demo/`
(git-ignored) and leaves `~/.memorylake` alone. Without it, it uses your existing `memorylake auth login`.

| | |
|---|---|
| `MEMORYLAKE_BASE_URL` | API endpoint; default global, China is `https://app.memorylake.cn/openapi/memorylake` |
| `MEMORYLAKE_WORKSPACE` | Workspace id; default is the one remembered by `ws use`, else your default workspace |
| `python3 demo.py --reset` | Delete the team project and the buyer first, then replay the whole story |
| `python3 demo.py profile` | Lena's profile (her actor) and the showing history (the team project) |
| `python3 demo.py conflicts` | Every conflict on Lena's actor, open or resolved |
| `python3 demo.py screen` | Screen the tour against her memory as it is now |
| `python3 demo.py ask "How does Lena feel about busy streets?"` | One search over profile + showings, each hit labelled with where it lives |
| `python3 demo.py cleanup` | Delete the team project (with the showings) and the buyer actor (with her profile and conflicts) |

It is safe to re-run. The project and the buyer are found again by `custom_id` (prefix `mlu-bpm-`), and a fact is written only if
that exact text is not already there. Kickoff notes that an earlier run's resolution forgot are recognised from the resolved
conflicts' snapshots and are **not** pinned again. Otherwise every re-run would re-raise the same two conflicts. A re-run reads
the earlier conflicts instead of waiting, and says the screen now shows the resolved profile. `--reset` replays everything.

## Or run it in the browser

```bash
python3 web/server.py        # http://127.0.0.1:8765 — standard library only
```

Paste the key, press **Run demo**, and watch the profile and the showings fill in, then the detector's two conflicts on
Lena's actor, Maya's tour screen with the two *ask first* rows, the said-vs-responded table, the resolution and the
before/after screen. **Ask Lena's memory** runs one search over her profile and the showings. The terminal drawer shows every
`memorylake` command as it runs.

## What happens, step by step

Output below is from a real run (ids shortened).

**1. Connect**: validate the key, pick a workspace.

**2. Team project + buyer actor.** `proj create` for the team, `actor create --tags buyer,stage:touring` + `actor bind` for Lena.

**3. Kickoff.** Priya's four statements go onto Lena's actor, **one every 15 seconds** (back-to-back writes are checked for
contradictions minutes later, as a batch). `fact add` stores the text verbatim and takes no metadata, so each is followed by
`fact update --metadata` with the rule (`forbid` / `max` / `prefer`), the agent and the date:

```
$ memorylake fact add --actor actor-… 'Dealbreaker: Lena Park will not buy a house on a cul-de-sac; she wants a through street.'
$ memorylake fact update fact-… --actor actor-… --metadata '{"kind": "street", "field": "street_type", "label": "no cul-de-sac (dealbreaker)", "agent": "Priya Shah", "said_on": "2026-09-02", "source": "kickoff", "forbid": "cul-de-sac"}'
```

**4. Showing history.** Four showings go onto the **team project**, each with `event: showing`, the listing, street type,
price, condition and Lena's reaction as metadata:

```
  Showing history on the team project (4 showings):
    2026-09-20  14 Alder Court     cul-de-sac        795,000  loved     (Tom Becker)
    2026-09-20  88 Marsh Road      through street    760,000  disliked  (Tom Becker)
    2026-09-27  230 Ridge Avenue   through street    780,000  lukewarm  (Tom Becker)
    2026-09-27  9 Wren Lane        cul-de-sac        815,000  loved     (Tom Becker)
```

**5. Call notes.** Tom's two notes go onto Lena's actor. The demo polls `fact conflict list --actor` until each note is named
by a conflict **and** the last write is at least 30 s old (so a late conflict is not missed), for up to 150 s:

```
  `fact conflict list --actor actor-…` — 2 conflict(s) naming Tom's notes (after 30s):

  [m2m · logical] Maximum budget amount mismatch   cfl-…  OPEN
    Fact 0 states Lena Park's maximum budget is 850,000 dollars, while fact 1 states her maximum budget is 800,000 dollars
    (a hard ceiling). Both describe the same single-valued attribute and cannot both be true as stated.
    fact-…  (Tom Becker, 2026-09-27)
      “Lena Park's maximum budget is 850,000 dollars.”
    fact-…  (Priya Shah, 2026-09-02)
      “Lena Park's maximum budget is 800,000 dollars, a hard ceiling.”

  [m2m · logical] Cul-de-sac preference contradiction   cfl-…  OPEN
    Fact 0 says Lena Park is happy to buy a house on a cul-de-sac, while fact 4 says it is a dealbreaker and she will not buy
    on a cul-de-sac (prefers a through street). Both cannot be true at the same time.
    fact-…  (Tom Becker, 2026-09-27)
      “Lena Park is happy to buy a house on a cul-de-sac.”
    fact-…  (Priya Shah, 2026-09-02)
      “Dealbreaker: Lena Park will not buy a house on a cul-de-sac; she wants a through street.”
```

**6. Takeover.** Maya reads Lena's profile (`fact list --actors`) and the open conflicts, and screens her four tour listings.
Every rule is checked. A field with two live facts that disagree on this listing is **?**, with the conflict id:

```
  ?  5 Heron Court          → ask Lena first — her memory disagrees
       ? cul-de-sac                       Tom 09-27: cul-de-sac is fine ✓  vs  Priya 09-02: no cul-de-sac (dealbreaker) ✗  (cfl-…)

  ?  41 Elm Street          → ask Lena first — her memory disagrees
       ? 840,000 dollars                  Tom 09-27: max 850,000 ✓  vs  Priya 09-02: max 800,000 ✗  (cfl-…)

  ✗  17 Birch Way           → skip
       ✗ needs major repairs (new roof)   Priya 09-02: FHA: no major repairs ✗

  !  602 Harbor Boulevard   → show, but flag it
       ! high traffic                     Priya 09-02: quiet street ✗
```

Then the kickoff profile against the showing history (`fact list --projects`), by what Lena *reacted* to:

```
  Said vs responded — the kickoff profile against 4 showings:
    ≠ said “no cul-de-sac (dealbreaker)” (Priya Shah, 09-02)  →  loved 14 Alder Court (cul-de-sac, 09-20); loved 9 Wren Lane (cul-de-sac, 09-27)
    ≠ said “max 800,000” (Priya Shah, 09-02)  →  loved 9 Wren Lane (815,000 dollars, 09-27)
    · said “FHA: no major repairs” (Priya Shah, 09-02)  →  no showing tested it
    = said “quiet street” (Priya Shah, 09-02)  →  disliked 88 Marsh Road (high traffic, 09-20)
```

**7. Resolve.** For each conflict Maya keeps Tom's note and cites the showings that back it:

```
  cfl-…: keep Tom Becker's “max 850,000” — the showings back it: loved 9 Wren Lane (815,000 dollars, 2026-09-27, fact-…)
$ memorylake fact conflict resolve cfl-… --actor actor-… --strategy keep_fact --keep-fact-id fact-…
  · resolved with keep_fact: forgotten fact-…
```

Lena's profile drops to 4 facts (Priya's financing and quiet-street notes, Tom's two), and the same tour screens
**✓ ✓ ✗ !** instead of **? ? ✗ !**.

**8. Handoff record.** `fact conflict get` for each resolved conflict: when it was raised and resolved, the strategy, which fact
was kept and which was forgotten, and both texts in full:

```
  cfl-…  Cul-de-sac preference contradiction
    raised 2026-10-10T04:35:25Z · resolved 2026-10-10T04:36:14Z · keep_fact
    kept      “Lena Park is happy to buy a house on a cul-de-sac.” (Tom Becker, 2026-09-27)
    forgotten “Dealbreaker: Lena Park will not buy a house on a cul-de-sac; she wants a through st…” (Priya Shah, 2026-09-02)
```

Profile, showings, resolutions and the before/after screen are written to `out/buyer-profile-lena-park.md`.

## Wiring it into your own CRM

- **One actor per buyer, statements only.** Pin what the buyer *states* (dealbreakers, budget, financing, preferences) on the
  buyer's actor, verbatim, one statement per fact, with your structured fields in `fact update --metadata`.
- **Events go on the team project.** Showings, reactions and offers are events. Kept apart from the profile, they are evidence
  you can read, and they do not stop the detector from flagging a contradiction between two statements.
- **Pace the writes** (≈15 s) and read `fact conflict list --actor <buyer> --resolved false` before anyone acts on the profile.
  Resolve with `keep_fact` (the other fact is forgotten) or `dismiss` (a false alarm; nothing changes).
- **Phrase a change as a statement, not a story.** "Max budget is 850,000" contradicts "800,000, a hard ceiling".
  "Max budget is 850,000 after her new pre-approval" reads as a change over time, and was flagged less reliably in our probes.
- **Don't expect conversation extraction to raise these.** In a probe, two conversations whose extracted facts disagreed raised
  no conflict. Extraction merges them as a revision ("… previously …") instead. Pin the notes you want checked.

## If something goes wrong

- **"✗ nothing raised" within 150 s**: the check was deferred. That happens when facts were written back to back. Run
  `python3 demo.py conflicts` a few minutes later and it is there. Then `python3 demo.py` resumes and resolves it.
- **The screen shows ✓ for Heron Court on a re-run**: an earlier run already resolved the conflicts. `--reset` replays the story.
- **`tls handshake eof` / `could not connect`** while polling: a dropped connection. Read-only commands retry four times;
  otherwise re-run `python3 demo.py` and it resumes.
- **`401 service account not found`**: the key was deleted or belongs to the other deployment.
- **Conflict names differ from the ones above**: names and descriptions are written by the server per run. The demo never
  matches on them, only on the fact ids.

## Files

```
demo.py                  the runner; prints every CLI command it runs (and emits events for the web app)
data/buyer.json          the team, the buyer, the agents, the kickoff profile, the showings, Tom's notes and Maya's tour
web/server.py            local web companion (stdlib HTTP server + Server-Sent Events over demo.py)
web/static/              the page — no build step
out/                     the handoff record and runs.jsonl, one line per run (git-ignored)
```

All names, the team and the listings are fictional.
