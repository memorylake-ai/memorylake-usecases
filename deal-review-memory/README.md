# Deal review memory for sales teams

A runnable version of the MemoryLake use case
[*Give Sales Teams Deal Review Memory That Compounds Across Every Meeting*](https://www.memorylake.ai/en/usecase/deal-review-memory-for-sales-teams),
built entirely on the [`memorylake` CLI](https://github.com/memorylake-ai/memorylake-cli).

**Watch it run:** videos are being recorded.

**The problem.** "Cross-deal patterns never get named because nobody connects the reviews." Once somebody does name one,
it lives on as reflection memory: *pilots close more often, open every deal with a pilot*. The count behind a note like
that can be exactly right while the advice is wrong. If one play was mostly run on small deals, and small deals close
more often anyway, the play gets credit for the segment it was used on. Follow the note and enterprise deals go down the
play that wins less often in enterprise.

**What this demo shows.** Tallgrass Software (fictional) sells inventory-planning software to SMB and enterprise
accounts. Its deal reviews live in one MemoryLake project: 28 closed deals from the CRM (word for word: segment, opening
play, when the security questionnaire went out, won or lost) and the 2 review notes Marcus Hale left when he handed the
team to Priya Raman. Priya's first deal review is on 2026-10-15 with three open deals:

| Deal | Quick read: the closest review note | Memory read: every closed deal, inside its own segment | |
|---|---|---|---|
| R-1 Harbor Logistics · Enterprise · opening play | "pilot-first closes more often, 8 of 14 vs 7 of 14; open every deal with a pilot, enterprise included" → **pilot-first** | Enterprise: exec-sponsor call **4/10 (40%)** vs pilot-first 1/4 (25%) → **exec-sponsor call** | ✗ |
| R-2 Meridian Health Systems · Enterprise · security questionnaire (control) | "with the proposal closed 11 of 14, after it 4 of 14" → **with the proposal** | Enterprise: with the proposal 4/6 (67%) vs after 1/8 (12%) → **with the proposal** | ✓ |
| R-3 Corner Bakery Co · SMB · opening play | → **pilot-first** | SMB: exec-sponsor call 3/4 (75%) vs pilot-first 7/10 (70%), under 10 points apart → **either** | ✓ |

Marcus's count is right: 8 of 14 deals won with a pilot, 7 of 14 with an exec-sponsor call. Counted inside each
segment, though, the exec-sponsor call wins as often or more in **both** segments. Pilot-first only leads overall
because it was run on SMB deals 10 of 14 times, and SMB deals close 71% of the time against 36% for enterprise. That is
a mix effect (Simpson's paradox), not a play effect. The security note is the control: it leads overall and inside
both segments, and both reads agree.

Step 6 rewrites the note that read a segment mix as a play effect where the next review will read it (`fact update
--text`, with the old text, the old advice and the counts kept in metadata). `fact trace` keeps both versions. Later
that day, three more deals go through the **same quick-read code** and agree with the closed deals, 3 of 3.

```
setup ──────── proj create
intake ─────── fact add --project <p> '<record>' …   (30 records, one call)  + fact update --metadata {segment, play, security, outcome…}
quick read ─── search '<deal question>' --projects <p> --types fact --top-k 8  → closest review note → the play it prefers
memory read ── fact list --projects <p>   (every closed deal, every page)
               → the note's factor: won / closed for each option, overall and inside each segment
               → inside the deal's own segment: the higher win rate if it leads by 10 points or more, else either
write back ─── fact update <note> --text '<conclusion from the closed deals>' --metadata {…, prefer: {SMB, Enterprise}, previous: '<old text>', basis: {…}}
               fact trace <note>  → ADD, UPDATE (old text kept) → quick read again (new wording)
```

No conversations and no extraction: every record is pinned word for word, and both reads are plain code over what the
CLI returns. No model decides anything.

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
cd memorylake-usecases/deal-review-memory
export MEMORYLAKE_API_KEY=sk-...           # China region: also export MEMORYLAKE_BASE_URL=https://app.memorylake.cn/openapi/memorylake
python3 demo.py
```

About a minute and a half the first time (one project, 30 facts, each with its metadata), about a minute on a re-run.
Every run replays Priya's first deal review: a note an earlier run rewrote is put back word for word from
`metadata.previous` first. With the key set, the demo logs into an isolated CLI profile under `./.memorylake-demo/` and
never touches your own `~/.memorylake`. Everything it creates has a `custom_id` starting with `mlu-drv-`.

Then ask a deal question of your own, or slice every closed deal by one factor:

```bash
python3 demo.py review "Should a 3,000-seat hospital group start with a pilot?" --segment Enterprise
python3 demo.py review "Do we send the security questionnaire before the proposal goes out?" --segment SMB
python3 demo.py slice play                 # or: slice security
python3 demo.py cleanup                    # deletes the project and every fact on it
```

`slice play` lists the 28 closed deals and ends with the table:

```
      opening play             all deals          SMB   Enterprise
      pilot-first              8/14  57%    7/10  70%     1/4  25%
      exec-sponsor call        7/14  50%     3/4  75%    4/10  40%
      (every deal)            15/28  54%   10/14  71%    5/14  36%
  · overall leader: pilot-first; it does not lead inside SMB and Enterprise
  · mix: pilot-first ran on SMB deals 10 of 14 times; exec-sponsor call ran on SMB deals 4 of 14 times — and SMB deals close 10/14 (71%), Enterprise deals close 5/14 (36%)
```

## Or run it in the browser

```bash
python3 web/server.py                # opens http://127.0.0.1:8765
```

Paste the key, connect, run. The page drives the same functions as `demo.py` and streams every `memorylake` command into a
terminal drawer as it runs: the closed deals and notes, the quick read of each deal with its top 8, the memory read as
a win-rate table with the deal's own segment highlighted, and the write-back with `fact trace`. **Review** takes any deal
question and segment, and slices the closed deals by either factor.

## What happens, step by step

### Steps 2–3: one project, two sources, one fact per record

```
$ memorylake proj create --name 'Tallgrass - deal reviews' --custom-id mlu-drv-pipeline --description '…'
$ memorylake fact add --project proj-… 'Closed deal D-2201: Brightwater Foods, SMB segment, 60 seats. Opening play: a two-week pilot (pilot-first). Security questionnaire sent with the proposal. Closed won on 2026-04-08, 14,400 USD ARR signed. Rep: Jonah Pike.' …
$ memorylake fact update fact-… --project proj-… --metadata '{"key": "drv:D-2201", "record": "D-2201", "tool": "crm", "kind": "deal", "by": "Jonah Pike", "on": "2026-04-08", "account": "Brightwater Foods", "segment": "SMB", "seats": 60, "play": "pilot-first", "security": "with the proposal", "outcome": "won"}'
  · … and 29 more `fact update --metadata` the same way

    closed deals   28   SMB 14 · Enterprise 14
    review notes    2

  Marcus's notes, as he left them:
    NOTE-PLAY Deal review notes (Marcus Hale, 2026-09-05), opening play, pilot or exec-sponsor call: pilot-first closes more often - 8 of 14 closed deals won with a two-week pilot, 7 of 14 with an exec-sponsor call. Open every new deal with a two-week pilot, enterprise deals included.
    NOTE-SEC  Deal review notes (Marcus Hale, 2026-09-05), security questionnaire timing: deals that got the security questionnaire with the proposal closed 11 of 14; sent after the proposal, 4 of 14. Send the security questionnaire with the proposal on every deal.
```

`fact add` takes many texts in one call and returns their ids in order; it takes no metadata, so each fact gets a
`fact update --metadata` right after. A note's metadata names the factor it is about and the option it prefers.

### Step 4: the quick read

```
$ memorylake search 'Harbor Logistics, enterprise, 1,200 seats, in discovery: open with a two-week pilot or an exec-sponsor call?' --projects proj-… --types fact --top-k 8

  R-1 (Enterprise): “Harbor Logistics, enterprise, 1,200 seats, in discovery: open with a two-week pilot or an exec-sponsor call?”
  search · top 8:
      #1   NOTE-PLAY review notes Deal review notes (Marcus Hale, 2026-09-05), opening play, pilot or exec-sponso…   ← closest note
      #2   D-2210    closed deals Closed deal D-2210: Granite Peak Distribution, Enterprise segment, 1,400 seats.…
      #3   D-2224    closed deals Closed deal D-2224: Ironwood Manufacturing, Enterprise segment, 2,200 seats. Op…
      …
  → quick read: pilot-first
```

The note's text and advice are read from `fact list` by the hit's fact id, not from the hit itself: `search` can answer
a repeated question from a cache of older text.

### Step 5: the memory read

```
$ memorylake fact list --projects proj-… --page-size 50

  R-1 Harbor Logistics · Enterprise · opening play
      opening play             all deals          SMB   Enterprise
      pilot-first              8/14  57%    7/10  70%     1/4  25%  ← the note
      exec-sponsor call        7/14  50%     3/4  75%    4/10  40%  ← memory read (Enterprise)
      (every deal)            15/28  54%   10/14  71%    5/14  36%
  · the note's comparison is the “all deals” column: pilot-first leads there, and leads in neither segment. pilot-first ran on SMB deals 10 of 14 times; exec-sponsor call ran on SMB deals 4 of 14 times — and SMB deals close 10/14 (71%), Enterprise deals close 5/14 (36%)
  → quick read:  pilot-first
  → memory read: exec-sponsor call (4/10 (40%) vs 1/4 (25%) in Enterprise)

  R-2 Meridian Health Systems · Enterprise · security questionnaire
      security questionnaire   all deals          SMB   Enterprise
      with the proposal       11/14  79%     7/8  88%     4/6  67%  ← the note  ← memory read (Enterprise)
      after the proposal       4/14  29%     3/6  50%     1/8  12%
      (every deal)            15/28  54%   10/14  71%    5/14  36%
  · the note holds: with the proposal leads overall and inside every segment
  …
  deal  account                  the note says        the closed deals say
  ------------------------------------------------------------------------------
  R-1   Harbor Logistics         pilot-first          exec-sponsor call      ✗ differs
  R-2   Meridian Health Systems  with the proposal    with the proposal      ✓ same
  R-3   Corner Bakery Co         pilot-first          either                 ✓ same

  → 1 of 3 reviews: the note sends the deal down the play that wins less often in its own segment
```

The rules are a few lines of code (`slice_by()` and `decide()` in `demo.py`), reading only the closed deals' metadata.
For each option of the note's factor: won / closed over all deals and inside each segment. Inside the deal's own
segment, the option with the higher win rate is the call if it leads by 10 points or more; otherwise either option will
do. With 4 and 10 deals in a cell, a 5-point gap (SMB) is not a reason to switch plays, and the demo says so instead of
picking one. The 15-point gap in enterprise is.

### Step 6: write it back

```
    NOTE-PLAY was “Deal review notes (Marcus Hale, 2026-09-05), opening play, pilot or exec-sponsor call: pilot-first closes more often - 8 of 14 closed deals won with a two-week…”
              now “Deal review notes (rewritten by Priya Raman from the closed-deal log), opening play, pilot or exec-sponsor call: pilot-first only looks better overall because it was run mostly on SMB deals, which close more often anyway. Inside each segment the exec-sponsor call wins as often or more. SMB deals: either play. Enterprise deals: open with the exec-sponsor call.”
$ memorylake fact trace fact-… --project proj-…

  fact trace NOTE-PLAY (newest first):
    UPDATE  MANUAL  2026-10-14T16:20:05  Deal review notes (rewritten by Priya Raman from the closed-deal log), opening play, pilo…
    ADD     MANUAL  2026-10-14T16:19:13  Deal review notes (Marcus Hale, 2026-09-05), opening play, pilot or exec-sponsor call: pi…

  R-4 Oakridge Hospitals · Enterprise: “Oakridge Hospitals is an enterprise account with 2,000 seats. Which opening play: pilot first, or the executive sponsor call?”
    closest note NOTE-PLAY (#1) · quick read: exec-sponsor call · memory read: exec-sponsor call  ✓ same
  R-5 Pine Street Dental · SMB: “Pine Street Dental is an SMB account with 20 seats. Which opening play: pilot first, or the executive sponsor call?”
    closest note NOTE-PLAY (#1) · quick read: either · memory read: either  ✓ same
  R-6 Northfield Grocers · Enterprise: “Northfield Grocers is an enterprise account with 1,500 seats. When does the security questionnaire go out: with or after the proposal?”
    closest note NOTE-SEC (#1) · quick read: with the proposal · memory read: with the proposal  ✓ same

  → the quick read now agrees with the closed deals on 3 of 3: the note it follows is the one the closed-deal log rewrote
```

The rewritten note states only the conclusion, and its advice becomes one answer per segment (`metadata.prefer =
{"SMB": "either", "Enterprise": "exec-sponsor call"}`). The counts change with every closed deal, so they go to
`metadata.basis` (per option: won and closed, overall and per segment), next to the old text and the old advice. Text
and metadata change in one `fact update` call, so `fact trace` records the rewrite together with its basis. The later
deals are worded differently on purpose: `search` caches results by query text, so asking the morning's exact question
again could return the morning's answer.

## Measured

| What | Result |
|---|---|
| Closest note = the note for that factor, ranked #1 in the top 8 | 6 / 6 deal questions, every run |
| Quick read vs memory read: R-1 ✗, R-2 ✓, R-3 ✓ (either) | 4 / 4 runs (2 from scratch, 1 re-run, 1 in the browser) |
| After write-back: quick read agrees with the closed deals | 3 / 3, every run, first try |
| Time | 83 s from scratch, 58 s re-run (122 s with `--reset` deleting the previous run first) |

## Wiring it into your own deal reviews

- **Pin every closed deal with the dimensions a pattern could hide behind**: segment, deal size, source, rep, quarter.
  A note that compares plays across all deals is only as good as the deals it mixed together.
- **Keep reflections next to their evidence.** A pattern note is a judgment built from records in the same project.
  Count the records inside each segment before you follow the note, and write the conclusion back into the note,
  per segment, so the next review reads the corrected version.
- **Say "either" when the gap is small.** Four deals in a cell can't tell a 75% play from a 70% one; the demo's 10-point
  rule is deliberately blunt. Use whatever threshold your deal volume supports, but write down which one.

## If something goes wrong

- **`the memorylake CLI is not on PATH`**: install it (above) or `export MEMORYLAKE_BIN=/path/to/memorylake`.
- **Search results differ on your run**: ranking moves between runs. Step 4 prints exactly what came back; the memory
  read uses search only to pick the note, and with it the factor.
- **"no review note in the top 8"**: the question's words matched closed deals more closely than any note. Name the
  factor the way the notes do ("pilot or exec-sponsor call", "security questionnaire").
- **"search answered from its cache"**: step 4 asks the same questions every run, and `search` can serve a repeated
  question from a cache made by an earlier run; the line names the hits whose fact ids are not in `fact list` now.
- **Something half-created after an interrupted run**: `python3 demo.py --reset`, or `python3 demo.py cleanup`.

## Files

```
deal-review-memory/
├── demo.py              # the CLI runner (every step is a plain `memorylake` command, echoed)
├── data/story.json      # 28 closed deals, 2 review notes; 3 open deals + 3 later ones (fictional, plain ASCII)
├── web/server.py        # local web companion: same functions, Server-Sent Events
├── web/static/          # no-build front end
└── out/runs.jsonl       # one line per run (git-ignored)
```
