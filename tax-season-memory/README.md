# Tax season memory for accounting teams

A runnable version of the MemoryLake use case
[*Give Accounting Teams AI That Carries Client Context From One Tax Season to the Next*](https://www.memorylake.ai/en/usecase/ai-memory-for-accounting-teams),
built entirely on the [`memorylake` CLI](https://github.com/memorylake-ai/memorylake-cli).

**The problem.** The page promises that "full client context, prior-year positions, and open issues load
automatically" when a staff accountant picks up a return. That only works if the memory knows whose record each record
is. A client file is full of records that never say: an email signed "Dan", a voicemail, a sticky note in team chat.
Most of the time the name is enough. When two clients share it, father and son, the closest record with the right
name can belong to the other one. Every record is true. The answer is still wrong, because it comes from the other
client's records.

**What this demo shows.** Lindqvist & Oduya CPAs (fictional) keeps its 2025 tax season in one MemoryLake project: the
client directory, last year's return exports, client emails and voicemails, workpaper notes, team chat and the IRS
notice log, 22 records pinned word for word. It is 2026-10-15, the extended deadline. Tom Brennan is on leave, and
Priya Nair picks up his two Okafor returns: **Daniel Okafor Sr. (C-1187)** and his son **Daniel Okafor Jr. (C-2290)**.
Both sign their emails "Dan Okafor".

| Item | Quick read: `search`, the closest record on the topic that mentions Okafor | Memory read: whose record is it? | |
|---|---|---|---|
| Q1 Jr.'s Schedule D carryover | EM-0211 "…use it together with my $11,800 carryover" → **$11,800** (from `dan.okafor@…`, the father) | RX-2290-D, Jr.'s own 2024 export: **no carryover**. In search it ranks below his father's records, or misses the top 8. | ✗ |
| Q2 Sr.'s 2025 estimated payments | EM-0402 "…$8,400 in total" → **$8,400** (from `dokafor.jr@…`, the son) | RX-1187-P + VM-0320: **$3,250 overpayment applied, no 2025 payments** | ✗ |
| Q3 Jr.'s state returns (control) | WP-0410 (binder C-2290) → **Oregon part-year (OR-40-P) only** | WP-0410 → same | ✓ |

Had they gone on the returns: Q1 puts an $11,800 loss on the son's return that is really his father's. Q2 claims
$8,400 of payments on the father's return that were made under the son's SSN.

The memory read takes every record that says "Okafor" (`fact list --query`, an exact substring, every page) and
resolves each one by what it came in by. A client id or a workpaper binder gives the client directly. An address,
number or SSN is looked up in the client directory (`fact list --query <it>`): if exactly one entry has it, the record
belongs to that client, otherwise to nobody. Then it goes the other way round: each Okafor's addresses, numbers and SSN
are looked up in every record's text. That is how the IRS notice gets resolved, since it has only "SSN ending 9031" and
never the name. Two records stay unplaced: Tom's chat note "Okafor - carryover is $11,800" (no channel at all) and a
voicemail from a number in no directory entry. They are set aside, not guessed.

Step 6 files each resolved record into the client's own memory: one actor per client, `fact add --actor` with the full
name and client id in the text, and the resolution in metadata. The season file keeps the original record and gets the
client tag. Later that day, three questions asked in new words go through **the same quick read**, scoped to the
client's own memory (`search --actors`): 3 of 3 answer from the right client, and 0 hits come from anyone else.

```
setup ──────── proj create
intake ─────── fact add --project <p> '<record>' …   (22 records, one call)  + fact update --metadata {client | channel | binder | ssn, topic, value}
quick read ─── search '<question>' --projects <p> --types fact --top-k 8  → first hit on the topic that names Okafor, not tagged with another client
memory read ── fact list --projects <p> --query Okafor          (every record with the name, every page)
               fact list --projects <p> --query '<address | number | SSN>'   → exactly one directory entry = that client; none = not confirmed
               → each Okafor's directory channels looked up in every record's text (the IRS notice has only the SSN)
file it ────── actor create (one per client) → fact add --actor <client> '<record with full name + client id>' + metadata {resolved_via, source_fact}
               fact update <season record> --metadata {client, resolved_via, filed_to}   (unplaced: {unresolved: why})
               search '<new question>' --actors <client>   → the quick read again, in the client's own memory
```

There are no conversations and no extraction. Every record is pinned word for word, and both reads are plain code over
what the CLI returns. No model decides anything.

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
cd memorylake-usecases/tax-season-memory
export MEMORYLAKE_API_KEY=sk-...           # China region: also export MEMORYLAKE_BASE_URL=https://app.memorylake.cn/openapi/memorylake
python3 demo.py
```

The first run takes about two minutes (one project, 22 facts with their metadata, two client actors). A re-run takes
about a minute. Every run replays Priya's morning: if an earlier run tagged season records with a client, the tags are
taken off first, so step 4 reads the file as it came in. The client files an earlier run made stay, and step 6 finds
them. With the key set, the demo logs into an isolated CLI profile under `./.memorylake-demo/` and never touches your
own `~/.memorylake`. Everything it creates has a `custom_id` starting with `mlu-tsm-`.

Then ask a question of your own, or list every record that is one client's:

```bash
python3 demo.py client C-2290            # every record resolved to Daniel Okafor Jr., and how; what was filed nowhere
python3 demo.py ask "How much estimated tax did Dan Okafor send in for 2025?" --client C-1187 --topic estimates
python3 demo.py ask "Did Dan Okafor carry a capital loss into 2025?" --client C-2290 --topic carryover
python3 demo.py cleanup                  # deletes both client actors (their facts first), the project and every fact on it
```

`ask` puts three reads side by side: the quick read over the season file as it came in, the memory read, and, once a
run has filed the records, the quick read in that client's own memory:

```
  “How much estimated tax did Dan Okafor send in for 2025?” · return Daniel Okafor Sr. (C-1187) · topic estimates
  the season file, as it came in — top 8:
      #1   EM-0402    client email             Dan Okafor: my 2025 estimated tax payments were $2,100 each on April …   ← used
  …
  → quick read:  $8,400 paid  (EM-0402, Daniel Okafor Jr. (C-2290))
      RX-1187-P  $3,250 applied                     C-1187  used             client id on the record
      RX-2290-P  nothing applied                    C-2290  not this client  client id on the record
      VM-0320    no 2025 payments                   C-1187  used             (541) 555-0142 → DIR-1187
      EM-0402    $8,400 paid                        C-2290  not this client  dokafor.jr@fernmail.example → DIR-2290
  → memory read: $3,250 applied; no 2025 payments  ✗ differs
  → in C-1187's own memory (actor-…): $3,250 applied (RX-1187-P)  ✓
```

## Or run it in the browser

```bash
python3 web/server.py                # opens http://127.0.0.1:8765
```

Paste the key, connect and run. The page drives the same functions as `demo.py` and streams every `memorylake` command
into a terminal drawer as it runs. You see the records that only say "Okafor" with what they came in by, the quick read
of each item with its top 8, the memory read with every record's resolution, and the two client files. **Ask** takes any
question for either return (pick the client and the topic) and lists either client's records.

## What happens, step by step

### Steps 2–3: one season file, one fact per record

All 22 records go in with one `fact add`, then one `fact update --metadata` each with what the record itself carries:
the client id on exports and directory entries, the sender address on an email, the calling number on a voicemail, the
binder on a workpaper, the SSN on an IRS notice. Team chat carries nothing.

```
    client directory  4   client id on every record
    return export     8   client id on every record
    client email      5   dan.okafor@fernmail.example, dokafor.jr@fernmail.example, ingrid.moss@quillpost.example
    voicemail         2   (503) 555-0109, (541) 555-0142
    workpaper         1   binder C-2290
    team chat         1   nothing
    IRS notice log    1   SSN ending 9031

  8 records that only say “Okafor” — whose they are is only in what they came in by:
    EM-0211   client email   dan.okafor@fernmail.example  Dan Okafor: I sold the last of my Ridgeway Mutual shares in May 2025 at a loss of about $6,…
    EM-0228   client email   dan.okafor@fernmail.example  Dan Okafor: my Social Security and pension forms are in the portal now. Nothing else change…
    EM-0305   client email   dokafor.jr@fernmail.example  Dan Okafor: I bought a new laptop and desk for the home office in 2025, about $2,600 in tot…
    VM-0320   voicemail      (541) 555-0142               Voicemail, Dan Okafor: I did not send in any estimated tax payments for 2025. The overpayme…
    EM-0402   client email   dokafor.jr@fernmail.example  Dan Okafor: my 2025 estimated tax payments were $2,100 each on April 15, June 16 and Septem…
    WP-0410   workpaper      binder C-2290                Dan Okafor - moved to Washington on June 30, 2025. File Oregon part-year (Form OR-40-P) onl…
    CH-0414   team chat      —                            Okafor - carryover is $11,800, checked against last year's return. It goes on the 2025 Sche…
    VM-0918   voicemail      (503) 555-0109               Voicemail, Dan Okafor: I got a letter from the IRS about my 2023 return. Can someone call m…
```

### Step 4: the quick read

`search` the question in the season file and take the first hit on that topic that mentions the client's name and is
not tagged with another client. Exports carry their client id, so the father's own export is skipped on the son's
question. His email is not tagged, and it comes next:

```
  Q1 · return Daniel Okafor Jr. (C-2290) · Schedule D carryover
  “What capital loss carryover goes on Dan Okafor's 2025 Schedule D?”
  search · top 8:
      #1   RX-1187-D  return export    C-1187  C-1187 Daniel Okafor Sr., 2024 Form 1040: Schedule D capital loss car…
      #2   EM-0211    client email             Dan Okafor: I sold the last of my Ridgeway Mutual shares in May 2025 …   ← used
      #3   CH-0414    team chat                Okafor - carryover is $11,800, checked against last year's return. It…
      #4   DIR-1187   client directory C-1187  Client C-1187 - Daniel Okafor Sr. (born 1958, SSN ending 4417). Conta…
      #5   RX-1187-P  return export    C-1187  C-1187 Daniel Okafor Sr., 2024 Form 1040: overpayment of $3,250 appli…
      #6   RX-1187-S  return export    C-1187  C-1187 Daniel Okafor Sr., 2024 returns: Oregon full-year resident, Fo…
      #7   EM-0402    client email             Dan Okafor: my 2025 estimated tax payments were $2,100 each on April …
      #8   WP-0410    workpaper                Dan Okafor - moved to Washington on June 30, 2025. File Oregon part-y…
  → EM-0211: “Dan Okafor: I sold the last of my Ridgeway Mutual shares in May 2025 at a loss of about $6,900. Please use it together with my $11,800 carryover from…”
  → quick read: $11,800 carryover + $6,900 loss
```

Searching by name groups the results by person. Five of the top 8 are the father's records (four tagged, one not), two
are the son's on other topics, and one is nobody's. The son's own export (RX-2290-D, "no capital loss carryover") is
missing here. In other runs it came in at #7 or #8, still below his father's email, so "the first hit on the topic"
lands on the father's record either way.

### Step 5: the memory read

```
  16 records say “Okafor”; 1 more never uses the name but carries an address, number or SSN from their directory entries:
    record     came in by                     whose       how
    …
    EM-0211    dan.okafor@fernmail.example    C-1187      dan.okafor@fernmail.example → DIR-1187
    VM-0320    (541) 555-0142                 C-1187      (541) 555-0142 → DIR-1187
    EM-0402    dokafor.jr@fernmail.example    C-2290      dokafor.jr@fernmail.example → DIR-2290
    WP-0410    binder C-2290                  C-2290      workpaper binder C-2290
    CH-0414    team chat                      ?           not confirmed — no address, number, binder or client id on the record
    VM-0918    (503) 555-0109                 ?           not confirmed — (503) 555-0109 is in no directory entry
    IRS-0921   SSN ending 9031                C-2290      SSN ending 9031 in the text → DIR-2290

  Q1 · return Daniel Okafor Jr. (C-2290) · Schedule D carryover
      RX-1187-D  return export  $11,800 long-term                  C-1187  not this client
      RX-2290-D  return export  none                               C-2290  used
      EM-0211    client email   $11,800 carryover + $6,900 loss    C-1187  not this client  ← quick read
      CH-0414    team chat      $11,800                            ?       set aside
  → quick read:  $11,800 carryover + $6,900 loss  (EM-0211, Daniel Okafor Sr. (C-1187))
  → memory read: none  (RX-2290-D)  ✗ differs
  · on the return as the quick read has it: an $11,800 loss deducted on the son's return - a loss that is his father's

  item  return                     the quick read says                the client’s own records say
  --------------------------------------------------------------------------------------------------------
  Q1    Daniel Okafor Jr. (C-2290) $11,800 carryover + $6,900 loss    none                           ✗
  Q2    Daniel Okafor Sr. (C-1187) $8,400 paid                        $3,250 applied; no 2025 payme… ✗
  Q3    Daniel Okafor Jr. (C-2290) Oregon part-year (OR-40-P) only    Oregon part-year (OR-40-P) on… ✓

  → 2 of 3: the closest record with the right name belongs to the other Okafor
```

`fact list --query` is an exact, case-insensitive substring match over the fact text (the metadata isn't searched), so
looking up `dan.okafor@fernmail.example` returns only the directory entry that lists it. The chat note's "$11,800"
happens to match the father's carryover, but nothing on the record says it is about him, so it is set aside rather than
guessed. Q3 is the control: the workpaper's binder is the son's, so the quick read was right by luck.

### Step 6: file it under the client

```
  Daniel Okafor Jr. (C-2290) — 8 records in the client's own memory (actor-…):
    DIR-2290   Client C-2290 - Daniel Okafor Jr. (born 1986, SSN ending 9031). Contact: dokafor.jr@fernmail.ex…
    RX-2290-C  C-2290 Daniel Okafor Jr., 2024 Form 1040: Schedule C consulting net profit $64,300; home office…
    RX-2290-D  C-2290 Daniel Okafor Jr., 2024 Form 1040: no capital loss carryover; no Schedule D filed.
    RX-2290-P  C-2290 Daniel Okafor Jr., 2024 Form 1040: refund of $410 issued; nothing applied to 2025 estima…
    EM-0305    Daniel Okafor Jr. (C-2290): I bought a new laptop and desk for the home office in 2025, about $…
    EM-0402    Daniel Okafor Jr. (C-2290): my 2025 estimated tax payments were $2,100 each on April 15, June 1…
    WP-0410    Daniel Okafor Jr. (C-2290) - moved to Washington on June 30, 2025. File Oregon part-year (Form …
    IRS-0921   Daniel Okafor Jr. (C-2290): IRS CP2000 dated 2026-09-08 for SSN ending 9031, tax year 2023: $1,…

  2 record(s) filed nowhere — nobody can say which Okafor they are:
    CH-0414    Okafor - carryover is $11,800, checked against last year's return. It goes on t…  (no address, number, binder or client id on the record)
    VM-0918    Voicemail, Dan Okafor: I got a letter from the IRS about my 2023 return. Can so…  ((503) 555-0109 is in no directory entry)

  Q4 · Daniel Okafor Jr. (C-2290) · “Does Dan Okafor have any loss carryforward from 2024 to use?”
    8 hits in C-2290's memory, 0 from anyone else · used RX-2290-D (#1): none  ✓ same record as the memory read

  Q5 · Daniel Okafor Sr. (C-1187) · “What credit for 2025 estimated tax does Dan Okafor have on file?”
    7 hits in C-1187's memory, 0 from anyone else · used RX-1187-P (#1): $3,250 applied  ✓ same record as the memory read

  Q6 · Daniel Okafor Jr. (C-2290) · “Has Dan Okafor had any letter from the IRS this year?”
    8 hits in C-2290's memory, 0 from anyone else · used IRS-0921 (#1): CP2000, $1,240 proposed  ✓ same record as the memory read

  → 3 of 3: in the client's own memory, the closest record is the client's
```

The filed copy carries the full name and client id in its text, plus `resolved_via` and the season fact's id
(`source_fact`) in metadata, so anyone reading the client file can see why each record is there. The voicemail about an
IRS letter is probably the son's, since there is a CP2000 on his SSN. But "probably" is a call-back, not a filing: it
stays out of both files. The later questions are worded differently on purpose: `search` caches results by query text.

## Measured

| What | Result |
|---|---|
| Quick read vs memory read: Q1 ✗, Q2 ✗, Q3 ✓ | every run (see `out/runs.jsonl`) |
| RX-2290-D (Jr.'s own carryover record) in the Q1 search top 8 | sometimes (#7, #8), always below the father's email |
| After filing: the quick read in the client's own memory answers from that client | 3 / 3, every run, first try; 0 hits from anyone else |
| Time | about 2 minutes from scratch, 1 minute re-run |

## Wiring it into your own client file

- **Keep the channel with every record.** The sender address, the calling number, the binder, the SSN on a notice:
  that is the identity. A name is only a label, and labels collide in families, in firms and in the same small town.
- **Resolve against the directory, and accept "nobody".** One directory entry means the record belongs to that client.
  None or several means a call-back, not a guess. A record without any channel (a chat note, a sticky) has no owner
  until someone gives it one.
- **Look it up both ways.** Records that never use the name (IRS notices, bank letters, 1099s) are found by the
  client's numbers, not by the name.
- **File it where the next question will look.** Once a record is resolved, scope the next read to the client
  (`search --actors <client>`), so the other client's records cannot crowd the top-k.

## If something goes wrong

- **`the memorylake CLI is not on PATH`**: install it (above) or `export MEMORYLAKE_BIN=/path/to/memorylake`.
- **Search results differ on your run**: ranking moves between runs, and step 4 prints exactly what came back. The
  memory read does not use search at all.
- **"search answered from its cache"**: step 4 asks the same questions every run, and `search` can serve a repeated
  question from a cache made by an earlier run. The line names the hits whose fact ids are not in `fact list` now.
- **Something half-created after an interrupted run**: `python3 demo.py --reset`, or `python3 demo.py cleanup`.
  Cleanup deletes each client actor's facts before the actor (an actor's facts outlive `actor delete` and can't be
  deleted afterwards).

## Files

```
tax-season-memory/
├── demo.py              # the CLI runner (every step is a plain `memorylake` command, echoed)
├── data/story.json      # 22 records (4 clients, 2 named Okafor); 3 questions + 3 later ones (fictional, plain ASCII)
├── web/server.py        # local web companion: same functions, Server-Sent Events
├── web/static/          # no-build front end
└── out/runs.jsonl       # one line per run (git-ignored)
```
