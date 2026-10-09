# Meeting memory across tools

A runnable version of the MemoryLake use case
[*Give Teams One Meeting Memory That Works Across Every Tool*](https://www.memorylake.ai/en/usecase/meeting-memory-across-tools),
built entirely on the [`memorylake` CLI](https://github.com/memorylake-ai/memorylake-cli).

**The problem.** The decision made in Tuesday's sync lives in one meeting tool, the follow-up in another,
the Slack thread tying them together is buried, and the runbook is in a wiki. Each AI tool sees one slice.
By Friday, three people remember three different dates.

**What this demo shows.** Halden Freight's operations team is moving its Riverside warehouse to a new
system. The meetings about it are spread over five tools: a weekly ops sync recorded in **Otter**, a
check-in noted in **Granola**, a **Slack** thread, a go-live runbook exported from **Notion**, and a
confidential vendor call summarised in **Fathom**. All of it goes into one MemoryLake project. Then:

- **the decision chain**: "when is the cutover, and did it change?" comes back as one fact that carries
  the new date *and* the one it replaced, each with the date of the meeting that set it —
  "October 21 (as of 2026-09-29, previously October 14 as of 2026-09-22)";
- **who owes what**: `actor list --tags riverside` finds the project team, and each person's action items
  come back with their due date, the meeting and tool they were raised in, and — once closed — where;
- **one query, every tool**: a single search returns facts from the three meetings next to the Word
  document that answers the question;
- **forget a meeting**: the confidential vendor call is deleted, and the same search no longer finds it.

```
Otter sync ─┐
Granola     ├─ GROUP conversations, one actor per speaker ──▶ dated facts ───────┐
Slack       ┘                                                                    │
action items ── fact add --actor <owner> ──▶ facts owned by each person ─────────┼─▶ one project ──▶ search
Notion, Fathom exports (.docx) ── lib upload + proj doc import ──▶ documents ────┘      actor list --tags
                                                                                        proj doc delete
```

Runs in about 4 minutes on a free personal account. The only credential you need is a MemoryLake API key.

**Watch it run** (real recordings, unedited):
[CLI demo, 4:50](https://github.com/memorylake-ai/memorylake-usecases/releases/download/meeting-memory-v1/meeting-memory-cli-demo.mp4) · [Web companion demo, 5:35](https://github.com/memorylake-ai/memorylake-usecases/releases/download/meeting-memory-v1/meeting-memory-web-demo.mp4)

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
cd memorylake-usecases/meeting-memory

export MEMORYLAKE_API_KEY=sk-…
python3 demo.py
```

With `MEMORYLAKE_API_KEY` set, the demo logs into its own CLI profile under `./.memorylake-demo/`
(git-ignored) and leaves `~/.memorylake` alone. Without it, it uses your existing `memorylake auth login`.

| | |
|---|---|
| `MEMORYLAKE_BASE_URL` | API endpoint; default global, China is `https://app.memorylake.cn/openapi/memorylake` |
| `MEMORYLAKE_WORKSPACE` | Workspace id; default is the one remembered by `ws use`, else your default workspace |
| `python3 demo.py --reset` | Delete the demo meetings, project and people first, then start over |
| `python3 demo.py actions` | List everyone tagged `riverside` and their action items |
| `python3 demo.py brief` | Ask the three questions again and rewrite `out/meeting-brief.md` |
| `python3 demo.py facts` | Print what MemoryLake extracted from the meetings |
| `python3 demo.py cleanup` | Delete the meetings, the project (and its documents and facts), the four people (and their action items) and the uploaded files |

Safe to re-run: everything is found again by `custom_id`, prefixed `mlu-mmt-`; action items are pinned
only in the run that stores their meeting, and closing an item is idempotent. A re-run imports the deleted
vendor call again and step 8 deletes it again.

## Or run it in the browser

```bash
python3 web/server.py        # http://127.0.0.1:8765 — standard library only
```

![Web companion — the decision chain, with the new cutover date and the one it replaced](https://github.com/memorylake-ai/memorylake-usecases/raw/main/meeting-memory/web/screenshot-chain.png)

[Watch the web companion demo (mp4, 5:35)](https://github.com/memorylake-ai/memorylake-usecases/releases/download/meeting-memory-v1/meeting-memory-web-demo.mp4).

Paste the key, press **Run demo**, and watch the three meetings replay one after another with each
speaker's avatar, the action items get pinned and closed, the Word exports get parsed, the decision chain
get highlighted, the action items line up per person, and the vendor call disappear from search.
**Ask the memory** runs the same search for any question you type. The terminal drawer shows every
`memorylake` command as it runs.

## What happens, step by step

**1. Connect** — validate the key, pick a workspace.

**2. Set up.** Four `HUMAN` actors with **tags**: Ana (operations lead), Ben (systems engineer) and Cara
(floor manager) are tagged `riverside`; Dev from finance is not. One project holds the cutover.

```bash
memorylake actor create --custom-id mlu-mmt-ana-ruiz --display-name "Ana Ruiz" --type HUMAN \
  --tags riverside,team-ops --description "Operations lead, Halden Freight"
memorylake actor bind --actor actor-… --workspace ws-…
memorylake proj create --name "Riverside cutover — meeting memory" --custom-id mlu-mmt-riverside-cutover
```

**3. Meetings from three tools.** Each meeting is a `GROUP` conversation, labelled with the tool it came
from; each message is sent by the actor who said it and time-stamped to the meeting. Meetings go in one at
a time, and each is processed before the next — the way they would arrive days apart. (Appended all at
once, they are processed in parallel and out of order; in one of our runs the September 22 decision was
folded into the October 1 fact and lost.)

```bash
memorylake conv create --custom-id mlu-mmt-meeting-02-checkin --project proj-… --actors actor-ana,actor-cara \
  --kind GROUP --name "Cutover check-in" --metadata source=granola --metadata meeting_date=2026-09-29
memorylake conv msg append conv-… --actor actor-ana --custom-id turn-03 --timestamp 2026-09-29T09:06:00Z \
  --text "Ana Ruiz (Operations lead, Halden Freight): Agreed. Decision: we move the Riverside cutover from October 14 to October 21. …"
memorylake conv cook-status conv-…
```

Action items are pinned to their **owner** with `fact add --actor`, stating where they came from —
extraction does not reliably turn "I'll have it tested by October 3" into a fact, so the item is written
down explicitly:

```bash
memorylake fact add --actor actor-ben "Action item RIV-2 (open) — owner Ben Okafor: test the inventory migration script on a copy of production inventory and report the mismatch count, due 2026-10-03. Source: Weekly ops sync (Otter, 2026-09-22)."
```

When a later meeting closes an item (Ben reports the test done in Slack), the open item is replaced:

```bash
memorylake fact delete --actor actor-ben fact-…
memorylake fact add --actor actor-ben "Action item RIV-2 (done 2026-10-01) — owner Ben Okafor: … Raised in Weekly ops sync (Otter, 2026-09-22); closed in #riverside thread (Slack, 2026-10-01): 18,240 SKUs tested, zero mismatches."
```

```
  · 8 message(s) from Otter appended to “Weekly ops sync”
  · 3 action item(s) pinned to their owners
  · memory ready (1 meeting) in 72s
  · 5 message(s) from Granola appended to “Cutover check-in”
  · memory ready (1 meeting) in 42s
  · 3 message(s) from Slack appended to “#riverside thread”
  · 1 action item(s) pinned to their owners
  · memory ready (1 meeting) in 30s
  · RIV-1 closed in Granola: Ana Ruiz's open item replaced with a done one
  · RIV-2 closed in Slack: Ben Okafor's open item replaced with a done one
```

**4. Notes exported from Notion and Fathom.** Two `.docx` files go into the Library and are imported into
the same project:

```bash
memorylake lib upload data/sources/riverside-go-live-runbook.docx --on-conflict overwrite
memorylake lib upload data/sources/scanner-vendor-pricing-call.docx --on-conflict overwrite
memorylake proj doc import --project proj-… <item ids…> --wait
```

```
  · imported: 2 new, 0 already in project, 0 failed (37s)

   - riverside-go-live-runbook.docx       from Notion  okay
   - scanner-vendor-pricing-call.docx     from Fathom  okay
```

**5. The decision chain.** What MemoryLake extracted from the three meetings — a real run (extraction
varies a little from run to run):

```bash
memorylake fact list --projects proj-…
```

```
   - If the label printer firmware is old, pick tickets might not print. (as of 2026-09-22)
   - Forty pickers need training on the new handheld scanners. (as of 2026-09-22)
   - Going live on October 14 is not possible because nobody can pick without the handhelds. (as of 2026-09-29)
   - The scanner vendor handhelds are now expected to arrive on October 16 because the shipment slipped two weeks. (as of 2026-09-29)
   - A second dry run is planned for October 15. (as of 2026-10-01)
   …

  ▶ Two meetings in two tools, chained: the new cutover date, and the one it replaced, with both dates:
     The Riverside warehouse cutover to the new warehouse system is scheduled for October 21.
     (as of 2026-09-29, previously scheduled for October 14 as of 2026-09-22)
```

The Granola check-in did not create a second, competing fact, and it did not erase the Otter decision:
MemoryLake updated the existing fact and kept the old date with when it was set. (If a run extracts the
two dates as separate facts instead, the demo shows both; if a run keeps only the latest, it says so.)

**6. Who owes what.** Find the team by tag, then each person's facts:

```bash
memorylake actor list --tags riverside
memorylake fact list --actors actor-…
```

```
  `actor list --tags riverside` → 3 people (Dev Mehta is not tagged riverside, so not listed)

  Ana Ruiz · riverside, team-ops
   [done] Action item RIV-1 (done 2026-09-29) — owner Ana Ruiz: get the signed scanner vendor contract back.
          Raised in Weekly ops sync (Otter, 2026-09-22); closed in Cutover check-in (Granola, 2026-09-29): signed contract received.

  Ben Okafor · riverside, team-eng
   [done] Action item RIV-2 (done 2026-10-01) — owner Ben Okafor: test the inventory migration script … Raised in
          Weekly ops sync (Otter, 2026-09-22); closed in #riverside thread (Slack, 2026-10-01): 18,240 SKUs tested, zero mismatches.
   [open] Action item RIV-4 (open) — owner Ben Okafor: run a second migration dry run, due 2026-10-15.
          Source: #riverside thread (Slack, 2026-10-01).
   also remembered: The person is a systems engineer at Halden Freight. (as of 2026-09-22)

  Cara Lind · riverside, team-ops
   [open] Action item RIV-3 (open) — owner Cara Lind: run two scanner training sessions for the forty pickers
          before the cutover, due 2026-10-13. Source: Weekly ops sync (Otter, 2026-09-22).

  ▶ 2 open, 2 done — each with its owner, the meeting and tool it came from, and where it was closed.
```

Several tags combine with AND: `actor list --tags riverside,team-ops` returns Ana and Cara only. The
"also remembered" lines are facts MemoryLake attributed to the person on its own.

**7. One query, every tool.** One search per question over the whole project; facts from the meetings and
the document that answers it come back together. All answers, plus the action items, are written to
`out/meeting-brief.md`.

```bash
memorylake search "What are the go-live risks for the Riverside cutover?" --projects proj-… --top-k 5
```

```
  Q: What are the go-live risks?
     fact  The scanner vendor handhelds are now expected to arrive on October 16 because the shipment slipped two weeks. (as of 2026-09-29)
     fact  The Riverside warehouse cutover to the new warehouse system is scheduled for October 21. (as of 2026-09-29, previously …)
     fact  Going live on October 14 is not possible because nobody can pick without the handhelds. (as of 2026-09-29)
     fact  The second training session is scheduled for October 19 so it happens on the real devices. (as of 2026-09-29)
     doc   riverside-go-live-runbook.docx  (Word, exported from Notion)
           This document outlines the cutover process from the legacy system to the new warehouse system at the Riverside warehouse…
```

**8. Forget a meeting.** The Fathom summary of the vendor pricing call is confidential. `proj doc delete`
removes the document, its indexed content and every memory derived from it — no confirmation prompt:

```bash
memorylake search "scanner vendor pricing unit price discount penalty" --projects proj-… --types document
memorylake proj doc delete --project proj-… doc-…
memorylake search "scanner vendor pricing unit price discount penalty" --projects proj-… --types document
```

```
  · before: 2 document hit(s) — scanner-vendor-pricing-call.docx, riverside-go-live-runbook.docx
  · after:  1 document hit(s) — riverside-go-live-runbook.docx

  ▶ scanner-vendor-pricing-call.docx: found before, gone after — what was only in that call can no longer be retrieved.
```

**Deleting a conversation is different.** `conv delete` removes the conversation and its messages, but
the facts already extracted from it stay in the project (we checked for a minute after the delete), and
a fact does not record which conversation it came from. To forget a recorded meeting completely, delete
the conversation *and* the facts it produced (`fact delete --project …`), or keep sensitive meetings in
their own project and delete the project.

## Wiring it into your own meeting tools

- One project per initiative, team or customer. Every meeting tool feeds it: transcripts and notes as
  `GROUP` conversations (`--metadata source=<tool>`, one actor per speaker, `--timestamp` set to when it
  was said), exports and wiki pages as documents (`lib upload` + `proj doc import`).
- Send a meeting's messages and wait for `cook-status` before sending the next meeting, so later
  decisions revise earlier ones in order.
- Tag people (`actor create --tags …`; `actor update --tags …` replaces the whole list) by project and team; `actor list --tags`
  is then your roster, with no separate list to keep in sync.
- Pin action items to their owners with `fact add --actor`, with the source meeting in the text; replace
  them when they close. Any assistant can then answer "what is still open for Ben?" with
  `fact list --actors <ben>` or `search … --actors <ben>`.
- Before a meeting, run `search "<topic>" --projects <initiative>` and hand both result sets to your
  model or your notes.

## If something goes wrong

- **`tls handshake eof` / `could not connect`** while polling: a dropped connection. Read-only commands
  retry four times; otherwise re-run `python3 demo.py` and it resumes.
- **`401 service account not found`**: the key was deleted or belongs to the other deployment.
- **The cutover shows only one date**: extraction is server-side and varies between runs. The demo labels
  what it got; `python3 demo.py --reset` replays the meetings in order.
- **Facts come back worded differently, or in another language**: same cause. The action items are
  always verbatim, because they are pinned.
- **A fact appears under the wrong person**: in a group conversation, the server decides which actor a
  fact belongs to, and it occasionally picks a different participant. The pinned action items are not
  affected.
- **Import is slow**: `proj doc import --wait` waits up to 10 minutes; two small Word files take about
  35 seconds.

## Files

```
demo.py                          the runner; prints every CLI command it runs (and emits events for the web app)
data/meetings/*.json             the three meetings (Otter, Granola, Slack), with the action items each raises or closes
data/sources/*.docx              the Notion runbook export and the Fathom vendor-call summary
data/make_sources.py             regenerates the two .docx files from text (standard library only)
web/server.py                    local web companion (stdlib HTTP server + Server-Sent Events over demo.py)
web/static/                      the page — no build step
out/                             the meeting brief (git-ignored)
```

All names, companies and figures are fictional. Otter, Granola, Slack, Notion and Fathom are named only as
examples of where meeting notes come from; this demo does not connect to them.
