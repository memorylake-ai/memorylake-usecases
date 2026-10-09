# Conflict check memory for a small law firm

A runnable version of the MemoryLake use case
[*Give Small Law Firms Conflict Check Memory That Surfaces Every Prior Relationship*](https://www.memorylake.ai/en/usecase/conflict-check-memory-for-small-law-firms),
built entirely on the [`memorylake` CLI](https://github.com/memorylake-ai/memorylake-cli).

**The problem.** A small firm's conflict check depends on whoever remembers the firm's history. The senior partner
knows the firm defended a landlord six years ago. The associate running intake today does not, and the CRM only goes
back to 2021. The intake goes through, and the conflict surfaces later, at real cost.

**What this demo shows.** Hollis & Reyes LLP (fictional) keeps its firm history in MemoryLake:

- **a firm-history project**: one pinned fact per matter (2019–2024) plus the archived engagement and closing letters, imported as documents;
- **one actor per party** the firm has ever met, tagged `party` + `client` / `adverse` / `witness` + `current` / `former`, with the relationship pinned on it.

Senior partner Elena Hollis is on leave. Theo Lindqvist, an associate who joined in September, runs three intakes.
Each gets **two independent checks**:

1. **The party check.** Each party is looked up in per-party memory (`actor get --by-custom-id`, `fact list --actors`) and searched
   across the firm history (facts and letters). The firm's own rules then decide: a former client may not be the adverse party
   without consent, and so on.
2. **MemoryLake's contradiction detector.** The intake is recorded in the firm history (`fact add`). The server compares it with
   every fact and document already there, and raises a **conflict** when two statements cannot both be true
   (`fact conflict list`). Fact vs fact is `m2m`. Fact vs document is `m2d`, and it **quotes the document**.

| Intake | Party check | Detector |
|---|---|---|
| Harbor & Pine Coffee v. Tallis Roasting Co. | ✓ clear: neither party is anywhere in firm history | nothing raised |
| Gregory Penn v. Marlow Health Partners | ✗ conflict: **Marlow is a former client** (Matter 2021-007) and would be the adverse party | nothing raised. "Penn wants to sue Marlow" contradicts nothing. It is a conflict of *interest*, which is the firm's rule to apply |
| Dovetail Bakery v. Brightline Properties LLC (intake form: *"no prior relationship with Brightline"*) | ✗ conflict: **Brightline is a former client** (Matter 2019-014) | **2 conflicts**: the claim vs the 2019 matter record (`m2m`), and the claim vs the 2020 closing letter (`m2d`, with the excerpt) |

Theo then **resolves** both conflicts. Fact vs document → `trust_document`; fact vs fact → `keep_fact` with the 2019 matter record.
The wrong claim is **forgotten**, and the corrected intake record goes in (the detector raises nothing on it). The resolved conflicts,
read back with `fact conflict get`, keep the strategy, the time and the forgotten claim's exact words. Together with the party checks
they become the **audit trail** for the check (`out/conflict-check-2026-10-08.md`).

```
matter index ── fact add --project (one every 15 s) ──▶ project: firm history ◀── proj doc import  (archived letters)
                                                              │
parties ── actor create --tags party,client|adverse|witness   │   fact add --actor  (relationship)
                                                              │
intake ─▶ party check: actor get · fact list --actors · search --projects <history>   →  firm rules
       └▶ fact add --project <history> "Intake …"  ─▶ server: fact conflict list (m2m / m2d)
                                                  ─▶ fact conflict resolve (keep_fact / trust_document) ─▶ fact conflict get = audit trail
```

**Watch it run** (real recordings, unedited):
[CLI demo, 6:25](https://github.com/memorylake-ai/memorylake-usecases/releases/download/conflict-check-memory-v1/conflict-check-memory-cli-demo.mp4) · [Web companion demo, 8:06](https://github.com/memorylake-ai/memorylake-usecases/releases/download/conflict-check-memory-v1/conflict-check-memory-web-demo.mp4)

Runs in about 5 minutes on a free personal account. Most of that is pacing the writes and waiting for the detector.
The only credential you need is a MemoryLake API key.

**Measured** (all runs while building this demo, free account): the detector raised both conflicts on the Brightline intake in
**8 of 8** fresh runs (both recordings included), within the 45-second wait. It raised **nothing** on the clean intake, the Penn intake or the corrected record in any of them (0 of 24 checks, and none showed up later).
Conflict names and descriptions are written by the server and differ from run to run; the categories and the facts they name do not.

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
cd memorylake-usecases/conflict-check-memory

export MEMORYLAKE_API_KEY=sk-…
python3 demo.py
```

With `MEMORYLAKE_API_KEY` set, the demo logs into its own CLI profile under `./.memorylake-demo/`
(git-ignored) and leaves `~/.memorylake` alone. Without it, it uses your existing `memorylake auth login`.

| | |
|---|---|
| `MEMORYLAKE_BASE_URL` | API endpoint; default global, China is `https://app.memorylake.cn/openapi/memorylake` |
| `MEMORYLAKE_WORKSPACE` | Workspace id; default is the one remembered by `ws use`, else your default workspace |
| `python3 demo.py --reset` | Delete the firm history, the parties and the uploaded letters first, then start over |
| `python3 demo.py conflicts` | List every conflict in the firm history, open or resolved |
| `python3 demo.py check "Lena Brandt"` | Run the party check for any name (as the adverse party) |
| `python3 demo.py cleanup` | Delete the firm history project (with its facts, documents and conflicts), the 11 party actors and the two uploaded letters |

It is safe to re-run. Everything is found again by `custom_id` (prefix `mlu-ccm-`) or by file name. A matter record, a relationship
or an intake is written only if that exact text is not already there. An intake that an earlier run recorded and resolved is
recognised from the conflicts that carry its text, so it is not recorded a second time.

## Or run it in the browser

```bash
python3 web/server.py        # http://127.0.0.1:8765 — standard library only
```

![Web companion — the detector's two conflicts on the Brightline intake, with the closing-letter excerpt](https://github.com/memorylake-ai/memorylake-usecases/raw/main/conflict-check-memory/web/screenshot-conflicts.png)

[Watch the web companion demo (mp4, 8:06)](https://github.com/memorylake-ai/memorylake-usecases/releases/download/conflict-check-memory-v1/conflict-check-memory-web-demo.mp4).

Paste the key, press **Run demo**, and watch the letters import, the matter index and the parties fill in, then each intake's party
check and what the detector raises (with the document excerpt for `m2d`). The resolution and the audit trail follow.
**Check a party** runs the same party check for any name, as the adverse party or as the prospective client. The terminal drawer
shows every `memorylake` command as it runs.

## What happens, step by step

**1. Connect**: validate the key, pick a workspace.

**2. Firm history.** The archived letters are uploaded and imported. The matter index is pinned, **one fact every 15 seconds**.
Each matter's parties are created during the wait:

```bash
memorylake proj create --name "Hollis & Reyes — firm history" --custom-id mlu-ccm-firm-history …
memorylake lib upload data/letters/closing-letter-2019-014.md --on-conflict overwrite
memorylake lib upload data/letters/engagement-letter-2021-007.md --on-conflict overwrite
memorylake proj doc import --project <history> <item> <item> --wait
memorylake fact add --project <history> 'Matter 2019-014 (closed 2020-06): Hollis & Reyes LLP represented Brightline Properties LLC, defending it against tenant Dovetail Bakery in a commercial lease dispute. Responsible partner: Elena Hollis.'
memorylake actor create --custom-id mlu-ccm-party-brightline-properties --display-name "Brightline Properties LLC" \
  --tags party,client,former --description "Matter 2019-014"
memorylake actor bind --actor <id> --workspace <ws>
memorylake fact add --actor <id> 'Brightline Properties LLC: former client of Hollis & Reyes LLP (Matter 2019-014, commercial lease dispute against Dovetail Bakery, closed 2020-06).'
…
memorylake actor list --tags party
memorylake fact conflict list --project <history> --resolved false        # 0 before today
```

Why the pacing: the detector checks a fact right after it is written to a quiet project (10–20 s). Facts written back to back are
checked later, as one batch, about **8 minutes later** in our measurements. That includes several texts in one `fact add` call.
Nothing is lost, but an intake would wait minutes for its answer.

**3–5. Three intakes.** For each party: is there an actor for it, what is pinned on it, what does the firm history say. Search always
returns its top-k, so hits that do not name the party are left out (and counted):

```
  Marlow Health Partners (adverse party)  →  ✗ CONFLICT
    rule: former client (Matter 2021-007) would be the adverse party — needs its informed written consent
    per-party memory: actor actor-…  tags party,client,former
      · Marlow Health Partners: former client of Hollis & Reyes LLP (Matter 2021-007, employment arbitration against Gregory Penn, closed 2022-03).
    firm history: 2 matter record(s), 1 letter(s) name it  (5 other hit(s) left out: they do not name this party)
      · Matter 2021-007 (closed 2022-03): Hollis & Reyes LLP represented Marlow Health Partners in an employment arbitration; adverse party: former COO Grego…
      · Witness: in Matter 2021-007 the firm deposed Lena Brandt, CFO of Marlow Health Partners.
      · [engagement-letter-2021-007.md] This engagement letter from Hollis & Reyes LLP to Marlow Health Partners outlines the representation of Marlow Health Partners in…
```

Then the intake is recorded, and the demo polls the detector for up to 45 seconds:

```
$ memorylake fact add --project <history> 'Intake 2026-10-08: prospective client Dovetail Bakery wants to sue Brightline Properties LLC over a new lease. Intake form: the firm has no prior relationship with Brightline Properties LLC.'
$ memorylake fact conflict list --project <history> --page-size 100 --resolved false

  The detector raised 2 conflict(s) naming this intake:

  [m2d · knowledge] Intake says no prior relationship   cfl-…
    Fact 0 states the firm has no prior relationship with Brightline Properties LLC, but the closing letter for Matter 2019-014 states Hollis & Reyes LLP represented Brightline Properties LLC and that Brightline is a former client.
    fact fact-…: Intake 2026-10-08: prospective client Dovetail Bakery wants to sue Brightline Properties LLC over a new lease. Intake form: the firm has no prior relationship with Brightline Properties LLC.
    document closing-letter-2019-014.md — the excerpt says:
      “This letter confirms that our engagement to represent Brightline Properties LLC in the commercial lease dispute brought by its tenant Dovetail Bakery has concluded with the settlement signed on 2020-06-02.”
      “Brightline Properties LLC is a former client of the firm.”

  [m2m · logical] Prior relationship with Brightline   cfl-…
    Fact 0 states the firm has no prior relationship with Brightline Properties LLC, but Fact 1 states the firm previously represented Brightline Properties LLC in Matter 2019-014 (closed 2020-06). Both cannot be true.
    fact fact-…: Intake 2026-10-08: prospective client Dovetail Bakery wants to sue Brightline Properties LLC over a new lease. Intake form: the firm has no prior relationship with Brightline Properties LLC.
    fact fact-…: Matter 2019-014 (closed 2020-06): Hollis & Reyes LLP represented Brightline Properties LLC, defending it against tenant Dovetail Bakery in …
```

The `m2d` conflict carries `file_chunks`: the matched passage of the document, with `document_id` and `document_name`. The demo
prints the sentences of it that name the party.

**6. Resolve.** Each category accepts its own strategies (`m2m`: `keep_fact` / `dismiss`; `m2d`: `trust_fact` / `trust_document` / `dismiss`;
`self`: `edit_fact` / `dismiss`):

```
$ memorylake fact conflict resolve cfl-… --project <history> --strategy trust_document
  · m2d resolved with trust_document: forgotten fact-…
$ memorylake fact conflict resolve cfl-… --project <history> --strategy keep_fact --keep-fact-id <Matter 2019-014>
  · m2m resolved with keep_fact: forgotten fact-…
$ memorylake fact add --project <history> 'Intake 2026-10-08 conflict check result: Brightline Properties LLC is a former client of Hollis & Reyes LLP (Matter 2019-014). The Dovetail Bakery matter is on hold pending Brightline'"'"'s informed written consent and partner review.'

  ✓ the intake form's claim (“no prior relationship with Brightline Properties LLC”) is gone — forgotten by the resolution
  ✓ the corrected record is there: …
  ✓ the detector raised 0 conflict(s) on the corrected record
```

**7. Audit trail.** `fact conflict get` returns each resolution: `strategy`, `keep_fact_id`, `forgotten_fact_ids`, `created_at`.
The `fact_snapshots` still hold the forgotten claim's exact words:

```
  cfl-…  [m2m] Prior relationship with Brightline
    raised   2026-10-09T17:19:52Z
    resolved 2026-10-09T17:19:58Z  strategy keep_fact  kept fact-…
    forgotten fact-…: Intake 2026-10-08: prospective client Dovetail Bakery wants to sue Brightline Properties LLC over a new lease…
    kept      fact-…: Matter 2019-014 (closed 2020-06): Hollis & Reyes LLP represented Brightline Properties LLC, defending it agai…
```

Everything, including the party checks with their evidence, is written to `out/conflict-check-2026-10-08.md`.

## Wiring it into your own intake

- **Import the matter history once**: one fact per matter (`fact add --project`), written one at a time. Import engagement and
  closing letters as documents. A fact written *after* a document is checked against it; one written before is not.
- **One actor per party.** `custom_id` = a normalised party name, tags for role and status. The party check is then an exact
  lookup, not a fuzzy search.
- **Record what intake asserts as a fact**, then read `fact conflict list --resolved false` before the matter is opened.
  Resolve each conflict with the strategy that matches the evidence. `dismiss` marks a false alarm and changes nothing.
- **Keep your own rules.** The detector finds statements that cannot both be true. Whether a former client may be sued is a
  rule of professional conduct, not a contradiction, so it stays in your check (see `rule_for()` in `demo.py`).

## If something goes wrong

- **The detector raised nothing on the Brightline intake within 45 s**: the check was deferred. That happens when facts were
  written back to back. Run `python3 demo.py conflicts` a few minutes later and it is there. Then `python3 demo.py` resumes and
  resolves it.
- **The excerpt shows `â€”` instead of `—`**: the server mis-decodes non-ASCII characters in `file_chunks[].text` of Markdown files. The
  demo's letters are plain ASCII for that reason.
- **`tls handshake eof` / `could not connect`** while polling: a dropped connection. Read-only commands retry four times;
  otherwise re-run `python3 demo.py` and it resumes.
- **`401 service account not found`**: the key was deleted or belongs to the other deployment.
- **Conflict names differ from the ones above**: names and descriptions are written by the server per run. The demo never
  matches on them, only on the category and the fact ids.

## Files

```
demo.py                  the runner; prints every CLI command it runs (and emits events for the web app)
data/firm.json           the firm, its matter index, the 11 parties, the three intakes and the firm's rules
data/letters/*.md        the archived closing letter (2019-014) and engagement letter (2021-007)
web/server.py            local web companion (stdlib HTTP server + Server-Sent Events over demo.py)
web/static/              the page — no build step
out/                     the audit log and runs.jsonl, one line per run (git-ignored)
```

All names, firms and matters are fictional.
