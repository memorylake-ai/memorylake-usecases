# Macro and template memory for a support team

A runnable version of the MemoryLake use case
[*Give Support Teams Macro and Template Memory That Stays Current and AI Can Use*](https://www.memorylake.ai/en/usecase/macro-and-template-memory-for-support-teams),
built entirely on the [`memorylake` CLI](https://github.com/memorylake-ai/memorylake-cli).

**The problem.** A support team builds up saved replies (macros) over years, in more than one help desk. The policy changes; the
macros do not. Half of them quote a refund window or a shipping threshold that changed last quarter, two tools say different
things about the same case, and the AI assistant either ignores the macros or pastes a stale one.

**What this demo shows.** Larkspur Gear (fictional) sells outdoor gear. Support lead Priya Raman moves **7 macros** — 4 exported from
Zendesk, 3 from Intercom — into one MemoryLake project, next to the **support policy** that is supposed to be their source of truth.
Each macro becomes **one fact**, named, with its source tool, source id and the policy sections it depends on in the fact's metadata.

MemoryLake's **contradiction detector** (`fact conflict`) checks every write, and raises three kinds of conflict:

| Category | Means | Raised here on |
|---|---|---|
| `m2d` | a macro contradicts a project **document** — the server quotes the policy section back | *Refund request* (30 days vs the policy's 14), *Call me back* (weekend hours the policy does not have yet), *Damaged item* ("replace rather than refund" for everyone; the policy lets Trailhead Plus members choose a refund) |
| `self` | a macro contradicts **itself** | *Password reset* ("expires after 24 hours, and the link never expires") |
| `m2m` | two macros contradict **each other** — here one from each tool | *Damaged item* (Zendesk) vs *Damaged item (Trailhead Plus)* (Intercom) |

Priya settles each one with the strategy its category allows — **all four strategies the detector offers**:

| Conflict | Strategy | What happens |
|---|---|---|
| *Password reset* · `self` | `edit_fact --edit <id>=<text>` | the macro's text is replaced in place, same fact id |
| *Refund request* · `m2d` | `trust_document` | the macro is forgotten; version 2 (14 days) is published and checked — clean |
| *Call me back* · `m2d` | `trust_fact` | the extended hours were approved on 2026-10-06 and go live with policy v4: the macro is right, the policy is behind. The fact is kept — **but the server rewrites it** to record the decision (3/3 runs it appended a clause like "…despite the Support Policy stating … (which we do not trust here)"). A macro is pasted into replies word for word, so the approved wording is put back with `fact update`, and `fact trace` shows all three versions |
| *Damaged item* · `m2d` | `trust_document` | version 2 is scoped ("Trailhead Plus members can choose a full refund instead") — clean |
| *Damaged item* ↔ *(Trailhead Plus)* · `m2m` | `dismiss` | it was raised against the old wording. After version 2 the server marks it **`stale`** (a fact in it changed after it was raised); the demo finds it with `fact conflict list --stale true` and dismisses it — no fact changes |

**Then the policy changes.** Policy v4 (effective 2026-11-01) lowers the free-shipping threshold from 75 to 50 dollars and
extends the phone hours. v3 leaves the project, v4 goes in — and **the detector raises nothing**: it checks what is
written, when it is written, so macros already in the project are not re-checked against a new document (a `fact update`
does not re-run it either). The demo then **re-files** the macros whose metadata says they depend on a changed section
(`fact add` the same text, `fact delete` the old copy). Now the detector answers: *Shipping cost* is flagged against
**policy-v4.md**, *Call me back* and *Where is my order* come back clean. *Shipping cost* gets version 2 the same way.

Finally a support assistant pulls macros **by name** (`fact list`, read by `metadata.macro`) and **by question**
(`search --types fact`, top macro hit). The forgotten versions never come back. Every resolved conflict is read back
with `fact conflict get` — strategy, time, and a snapshot of the macro's words when it was flagged — into an audit file.

```
policy-v3.md ── lib upload ─▶ proj doc import ──▶ project: support KB ◀── fact add + fact update --metadata
                                                     │                    (7 macros, one every 15 s:
                                                     │                     Zendesk export + Intercom export)
                                                     ▼
                              fact conflict list  (m2d · self · m2m)
                              fact conflict resolve  edit_fact · trust_document · trust_fact · dismiss
                                                     │
policy-v4.md ── proj doc delete v3 · import v4 ─▶ 0 new conflicts
                re-file dependent macros ─────────▶ m2d on "Shipping cost" vs policy-v4.md ─▶ version 2
                                                     │
support assistant ── fact list (by name) · search --types fact (by question) · fact conflict get (audit)
```

**Watch it run** (real recordings, unedited): recording in progress.

Runs in about 6 minutes on a free personal account. Most of that is pacing the writes and waiting for the detector.
The only credential you need is a MemoryLake API key.

**Measured** (every fresh run of the final data set while building this demo — 8 runs, free account, 4 of them in parallel):
the detector raised all 6 planned (macro, category) pairs in **8 of 8** runs and nothing on *Shipping cost* or *Where is my order*
before the policy change; **0** new conflicts in the 45 s after policy v4 landed (8/8). After re-filing, *Shipping cost* was flagged
against v4 in **8 of 8** runs — in 7 within the wait, in 1 about 85 s after it (that run used a 60 s wait; the demo now waits up to
150 s and says so if the check is deferred). `trust_fact` rewrote the macro text every time (9 of 9 runs, plus 3 of 3 probes), and the
m2m was marked `stale` after the fix in 5 of 5 runs that checked it. Conflict names and descriptions are written by the server and
differ from run to run; the categories and the facts they name did not.

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
cd memorylake-usecases/macro-memory

export MEMORYLAKE_API_KEY=sk-…
python3 demo.py
```

With `MEMORYLAKE_API_KEY` set, the demo logs into its own CLI profile under `./.memorylake-demo/`
(git-ignored) and leaves `~/.memorylake` alone. Without it, it uses your existing `memorylake auth login`.

| | |
|---|---|
| `MEMORYLAKE_BASE_URL` | API endpoint; default global, China is `https://app.memorylake.cn/openapi/memorylake` |
| `MEMORYLAKE_WORKSPACE` | Workspace id; default is the one remembered by `ws use`, else your default workspace |
| `python3 demo.py --reset` | Delete the support KB and the uploaded policies first, then start over |
| `python3 demo.py conflicts` | List every conflict in the support KB, open (and whether stale) or resolved (and how) |
| `python3 demo.py macro shipping-cost` | One macro by name: current version, source tool, what it was checked against |
| `python3 demo.py ask "Can someone call me on Saturday?"` | The macro a support assistant would use for this question |
| `python3 demo.py cleanup` | Delete the support KB project (with its macros, policy documents and conflicts) and the two uploaded policy files |

It is safe to re-run. The project is found again by `custom_id` (prefix `mlu-mtm-`), macros by `metadata.macro`, the policies by
file name. A macro is imported only if no fact carries its name; a conflict is resolved only if it is still open; the policy swap
and the re-file are skipped once the project is on v4. `--reset` starts the story over.

## Or run it in the browser

```bash
python3 web/server.py        # http://127.0.0.1:8765 — standard library only
```

![Web companion — triage: trust_fact, the server's rewrite and the macro's fact trace](https://github.com/memorylake-ai/memorylake-usecases/raw/main/macro-memory/web/screenshot-triage.png)

Paste the key, press **Run demo**, and watch the policy and the 7 macros go in, then the detector report per macro (with the
policy excerpt for `m2d`), the four strategies side by side with before / after text, the policy swap that flags nothing, the
re-file that does, and the assistant's lookups. **Ask the KB** runs the assistant's lookup for any question. The terminal drawer
shows every `memorylake` command as it runs.

## What happens, step by step

Output below is from a real run (ids shortened; conflict names and descriptions are the server's, and vary per run).

**1 · Connect.** `auth login --api-key` into the isolated profile, `team get`, pick the workspace.

**2 · Support KB.** `proj create --custom-id mlu-mtm-support-kb`, then the current policy:

```
$ memorylake lib upload out/mlu-mtm-policy-v3.md --on-conflict overwrite
$ memorylake proj doc import --project proj-… sc-…:inode-… --wait
  · imported mlu-mtm-policy-v3.md into the support KB (20s)
```

**3 · Import macros.** `data/zendesk-macros.json` and `data/intercom-macros.csv` are the two exports. Each macro is one
`fact add`, then `fact update --metadata` with its name, tool, source id, dependencies, version and the policy it was checked
against. One write every 15 s, so the detector checks each as it lands:

```
$ memorylake fact add --project proj-… 'Macro '"'"'Refund request'"'"' (Zendesk): Thanks for reaching out about a refund. You can get a full refund within 30 days of delivery; …'
$ memorylake fact update fact-… --project proj-… --metadata '{"macro": "refund-request", "title": "Refund request", "tool": "Zendesk", "source_id": "360041", "depends_on": ["refunds"], "version": 1, "checked_against": "mlu-mtm-policy-v3.md"}'
```

**4 · Detector report.** `fact conflict list --project …`, mapped back to macros through the fact ids:

```
  ✗ m2d        Refund request (Zendesk)   [as planned]
      m2d · Refund window contradicts policy   cfl-…
        Fact 0 macro says customers can get a full refund within 30 days of delivery, but the policy chunk states full refunds are available only within 14 d…
        policy (mlu-mtm-policy-v3.md): "Refunds: Customers can request a full refund within 14 days of delivery. After 14 days we offer store credit only. …"
  ✓ clean      Shipping cost (Zendesk)   [as planned]
  ✗ m2d, m2m   Damaged item (Zendesk)   [as planned]
      m2m · Damaged item: refund vs replacement-only   cfl-…
        with: damaged-item-trailhead-plus
      m2d · Damaged items: refund vs replacement   cfl-…
        policy (mlu-mtm-policy-v3.md): "Damaged items: If an item arrives damaged, send a free replacement. Trailhead Plus members may instead choose a full refund and keep the item."
  ✗ self       Password reset (Zendesk)   [as planned]
      self · Password reset link expiry contradiction   cfl-…
  ✗ m2d        Call me back (Intercom)   [as planned]
        policy (mlu-mtm-policy-v3.md): "Phone support: Phone support is available Monday to Friday, 8am to 6pm Central Time. There is no weekend phone support."
  ✗ m2m        Damaged item (Trailhead Plus) (Intercom)   [as planned]
  ✓ clean      Where is my order (Intercom)   [as planned]

  Detector: 5 conflict(s) on 7 macros — 6/6 planned (macro, category) pairs raised
```

**5 · Triage.** `fact conflict resolve <id> --strategy …`:

```
  [self → edit_fact] Password reset: the link cannot both expire after 24 hours and never expire
    updated fact-… in place (same fact id)
    ✓ the text is exactly the replacement we sent

  [m2d → trust_document] Refund request: the macro still says 30 days; policy v3 cut the window to 14 days
    forgotten: fact-… (version 1)
    published version 2 → fact-…
    ✓ the detector raised 0 conflict(s) on version 2

  [m2d → trust_fact] Call me back: … the macro is right, the policy document is behind
    the fact is kept — but the server rewrote it to record the decision:
      before: Our phone team is available every day, 7am to 9pm Central Time, including weekends. Pick a time
              that works for you and we will call you back.
      after:  Our phone team is available every day, 7am to 9pm Central Time, including weekends. Pick a time
              that works for you and we will call you back, despite the Support Policy stating phone support
              is only Monday–Friday 8am–6pm Central Time with no weekend support (which we do not trust here).
    A macro is pasted into replies word for word, so put the approved wording back:
$ memorylake fact update fact-… --project proj-… --text 'Macro '"'"'Call me back'"'"' (Intercom): …' --metadata '{… "approved_exception": "extended phone hours approved 2026-10-06, ahead of policy v4"}'
$ memorylake fact trace fact-… --project proj-…
    `fact trace` — every version of this macro:
      ADD    2026-10-09T19:49:12Z  MANUAL: …e, including weekends. Pick a time that works for you and we will call you back.
      UPDATE 2026-10-09T19:50:06Z  MANUAL: …riday 8am–6pm Central Time with no weekend support (which we do not trust here).
      UPDATE 2026-10-09T19:50:08Z  MANUAL: …e, including weekends. Pick a time that works for you and we will call you back.
    ✓ the macro reads exactly as approved

  [m2d → trust_document] Damaged item: the macro states "replace rather than refund" as a rule for everyone; …
    ✓ the detector raised 0 conflict(s) on version 2

  [m2m → dismiss] Damaged item vs Damaged item (Trailhead Plus): raised against the old wording
$ memorylake fact conflict list --project proj-… --page-size 100 --resolved false --stale true --category m2m
    cfl-…  stale: true — a macro in it changed after it was raised (`fact conflict list --stale true` finds these)
    dismissed — facts changed: 0; the Trailhead Plus macro stays exactly as it is

  Open after triage: 0
```

**6 · Policy v4 replaces v3.** `proj doc delete` v3, upload and import v4, then watch `fact conflict list` for 45 s:

```
  New conflicts in 46s after the new policy landed: 0
  The detector checks what is written, when it is written. Macros already in the KB are not
  re-checked against a new document — so the old shipping threshold sits there, unflagged.
  Macros that depend on a changed section (shipping, phone): Shipping cost, Call me back, Where is my order
```

**7 · Re-verify.** Re-file each dependent macro (`fact add` the same text and metadata, `fact delete` the old copy), wait for the detector:

```
  ✗ m2d        Shipping cost (Zendesk)   [as planned]
      m2d · Shipping free-threshold mismatch   cfl-…
        Fact says standard shipping is free on orders over 75 dollars, but the policy document says standard shipping is free on orders over 50 dollars (with…
        policy (mlu-mtm-policy-v4.md): "Shipping: Standard shipping is free on orders over 50 dollars. Orders under 50 dollars pay a flat 6 dollar shipping fee. …"
  ✓ clean      Call me back (Intercom)   [as planned]
  ✓ clean      Where is my order (Intercom)   [as planned]

  [m2d → trust_document] Shipping cost: policy v4 lowers the free-shipping threshold from 75 to 50 dollars
    published version 2 → fact-…: Macro 'Shipping cost' (Zendesk): Standard shipping is free on orders over 50 dollars; …
    ✓ the detector raised 0 conflict(s) on version 2
```

*Call me back* — the macro Priya trusted over the old policy in step 5 — is now simply consistent with v4.

**8 · Support AI.** By name (`fact list`, by `metadata.macro`) and by question (`search --projects … --types fact --top-k 5`, the top macro hit):

```
  Shipping cost  v2 · Zendesk #360042 · checked against mlu-mtm-policy-v4.md
    Standard shipping is free on orders over 50 dollars; smaller orders pay a flat 6 dollar shipping fee.

  ✓ “A customer's order came to 60 dollars. Do they pay for shipping?”
    → Shipping cost (rank 1 of 5 fact hits)
  ✓ “Customer wants their money back 20 days after delivery”
    → Refund request (rank 1 of 5 fact hits)
  ✓ “Can someone call me on Saturday?”
    → Call me back (rank 1 of 5 fact hits)

  3/3 questions answered with the expected macro; forgotten versions never come back (trust_document / delete take them out of search)
```

**9 · Audit.** `fact conflict list --resolved true`, then `fact conflict get` for each:

```
  6 resolved conflict(s), 0 open:
    trust_document  [m2d] Shipping free-threshold mismatch                        shipping-cost
    dismiss         [m2m] Damaged item: refund vs replacement-only                damaged-item-trailhead-plus, damaged-item
    trust_document  [m2d] Damaged items: refund vs replacement                    damaged-item
    trust_fact      [m2d] Phone support hours contradict policy                   call-me-back
    trust_document  [m2d] Refund window contradicts policy                        refund-request
    edit_fact       [self] Password reset link expiry contradiction                password-reset
```

The audit file `out/macro-audit-2026-10-10.md` lists the macros in use (version, tool, what each was checked against) and every
resolved conflict with its snapshots, the policy excerpt, and — for `trust_fact` — the text the server wrote.

## Wiring it into your own help desk

- **One fact per macro**, the macro's name in the text and in `metadata`. Keep the policy sections it depends on in the metadata:
  that is what tells you which macros to re-check when a section changes.
- **Write macros one at a time, after the policy document is in.** The detector checks a fact against the documents already in the
  project when the fact is written. Back-to-back writes are checked later, as a batch (measured: up to about 8 minutes).
- **A new policy document flags nothing by itself.** Re-file the dependent macros (`fact add` + `fact delete`), or publish their next
  version, and read `fact conflict list --resolved false`. `fact update` does not re-run the detector.
- **Pick the strategy by who is right.** Policy right → `trust_document` and publish the next version. Macro right → `trust_fact`,
  then restore the approved wording, because the server rewrites the fact. A macro that contradicts itself → `edit_fact`.
  Raised against wording you have since changed → it shows up with `--stale true`; `dismiss` it.

## If something goes wrong

- **A planned conflict is "not raised within …"**: the check was deferred into a batch. Run `python3 demo.py conflicts` a few
  minutes later and it is there; `python3 demo.py` then resumes and settles it.
- **`trust_fact` left the macro unchanged**: the server's rewrite is a behaviour of the service, not a promise; the demo only
  restores the text when it changed, and says so either way.
- **An extra conflict "not in the plan"**: the detector found something else. The demo shows it and leaves it open; read it —
  it is often right.
- **The excerpt shows `â€”` instead of `—`**: the server mis-decodes non-ASCII characters in `file_chunks[].text` of Markdown
  files. The policies are plain ASCII for that reason.
- **`tls handshake eof` / `could not connect`** while polling: a dropped connection. Read-only commands retry four times;
  otherwise re-run `python3 demo.py` and it resumes.
- **`401 service account not found`**: the key was deleted or belongs to the other deployment.

## Files

```
demo.py                     the runner; prints every CLI command it runs (and emits events for the web app)
data/policy-v3.md           the support policy in force (effective 2026-09-01)
data/policy-v4.md           the next policy (effective 2026-11-01): free shipping from 50 dollars, longer phone hours
data/zendesk-macros.json    4 macros, as a Zendesk export
data/intercom-macros.csv    3 macros, as an Intercom export
data/playbook.json          the plan: which conflicts are expected, how each is settled, the fixed wording, the assistant's questions
web/server.py               local web companion (stdlib HTTP server + Server-Sent Events over demo.py)
web/static/                 the page — no build step
out/                        the audit file and runs.jsonl, one line per run (git-ignored)
```

All names and companies are fictional.
