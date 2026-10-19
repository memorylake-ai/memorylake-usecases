# Compensating event memory: a reversal that names an event id never reaches the value it undoes

A runnable version of the MemoryLake use case
[*Memory for Event-Sourced AI Agents*](https://www.memorylake.ai/en/usecase/memory-for-event-sourced-ai-agents),
built entirely on the [`memorylake` CLI](https://github.com/memorylake-ai/memorylake-cli) (v20261015 or later).

**Watch it run:** videos are being recorded.

**The problem.** In an event-sourced system nothing is edited in place. When an event turns out to be wrong, you
append a *compensating event* that undoes it — and, the way most event stores write it, it names the event it undoes
by id and does not repeat the value:

```
evt-1002  Wed 02 Sep  AccountOpened          Tallow & Finch Bakery; service address 14 Quarry Lane, Halden.
evt-1010  Thu 10 Sep  ServiceAddressChanged  from 14 Quarry Lane to 3 Mill Yard, Halden.
evt-1018  Tue 29 Sep  EventReversed          reverses evt-1010. The request came from an unverified caller; fraud review FR-22 confirmed it.
```

Write those events into memory as they happen and MemoryLake extracts a fact from each. The facts keep the values but
not the event ids, so the reversal has nothing to attach to:

```
what memory holds afterwards:  "The service address for the Tallow & Finch Bakery account is 3 Mill Yard, Halden (as of 2026-09-10, previously 14 Quarry Lane …)"
                               "evt-1010 was reversed because the request came from an unverified caller and fraud review FR-22 confirmed it. (on 2026-09-29)"
asked later:                   "What is Tallow & Finch Bakery's service address right now?"
the account assistant:         "Answer: 3 Mill Yard, Halden. The service address for Tallow & Finch Bakery is currently 3 Mill Yard, Halden."
```

The reversal is in memory — as a fact of its own, next to the fact it should have undone. Nothing in memory says what
`evt-1010` was.

**What this demo shows.** Ferrowyn Energy (a fictional energy supplier) keeps each customer account as an event
stream and writes every event into memory; its account assistant answers from that memory through MemoryRouter.
Eight accounts, three events each: six end with an `EventReversed` that undoes an earlier change (service address,
plan, billing email, payment method, meter tariff, contract end date); two controls end with an ordinary change. The
same twenty-four events go into four memories:

| | memory | what the account assistant reads |
|---|---|---|
| **A** | one message per event, as the event store writes it — the reversal names the event id | facts extracted from the events |
| **B** | the same messages, except that the app spells out each reversal: what the undone event was, and what the value is now ("That change is undone: Tallow & Finch Bakery's service address is 14 Quarry Lane again.") | facts extracted from self-describing events |
| **C** | A's extracted facts, copied word for word, plus the **event log** (one fact per event, id and day kept) and one **state fact** per account that the app folds from the events itself | A's facts and the app's projection side by side |
| **D** | only the event log and the state facts — nothing extracted | the app's projection |

Eight questions ("What is Tallow & Finch Bakery's service address right now?"), three answers each per memory, on
`gpt-4o-mini` at temperature 0.7. Last, the app reads the event log back out of memory (`fact list`) and folds it to
any day — the state on 25 September, and today — without a model.

Over 8 runs: in A, the latest fact about the field states the **reversed value for 45 of 48** reversed accounts (for
the other 3, the change itself never reached memory). Questions about the reversed accounts: **A 38/144** (↺ the
reversed value 86, Unknown 25), **B 130/144**, **C 124/144**, **D 54/54** (3 runs). B pays for it on a control:
Pellow & Daughters 9/24 against A's 20/24. Replay from the event log in memory: **64/64**. Details in
[Measured](#measured).

## Prerequisites

- Python 3.9+ (standard library only)
- The `memorylake` CLI **v20261015 or later** (`memorylake router` arrived in that release):
  ```bash
  curl -fsSL https://raw.githubusercontent.com/memorylake-ai/memorylake-cli/main/scripts/install.sh | sh
  memorylake version
  ```
- A MemoryLake API key ([app.memorylake.ai](https://app.memorylake.ai) → API Keys).
- **Router calls are billed to your MemoryLake balance** (`memorylake billing balance`). One run makes 104 calls on
  `gpt-4o-mini`, about 28,700 prompt + 2,100 reply tokens; the run prints the total. If the balance is empty, the
  router calls print the refusal and are skipped; the memory steps and the replay still run.

## Run it

```bash
git clone https://github.com/memorylake-ai/memorylake-usecases.git
cd memorylake-usecases/compensating-event-memory
export MEMORYLAKE_API_KEY=sk-...
python3 demo.py                     # the whole story, about 7 minutes
python3 demo.py memory              # re-read what A's and B's facts say about each account
python3 demo.py replay 2026-10-01   # fold the event log in memory to that day and to today
python3 demo.py ask "What is Tallow & Finch Bakery's service address right now?"
python3 demo.py cleanup             # delete everything the demo created
```

The key is used to log into an isolated CLI profile under `.memorylake-demo/` (git-ignored); your own
`~/.memorylake` is never touched. For the China endpoint, also set
`MEMORYLAKE_BASE_URL=https://app.memorylake.cn/openapi/memorylake`. Every run starts over. Everything it creates is
prefixed `mlu-cev-`. `cleanup` keeps the run's `out/report.json`, moving it to `out/runs/`.

## Or run it in the browser

```bash
python3 web/server.py                # opens http://127.0.0.1:8765
```

Paste the API key and press **Run the demo**. Follow the steps on the left; the terminal drawer shows every
`memorylake` command as it runs. **Replay** folds the event log to any day you pick. **Ask one** sends any question
through A, B, C and D and grades it if it is one of the eight. **Clean up** removes everything.

## What happens, step by step

The output below is from one real run (the "fourth run" in [Measured](#measured)), trimmed.

### Step 2: four projects, one event-store actor, one boundary each

```
$ memorylake proj create --name 'Ferrowyn Energy account events' --custom-id mlu-cev-plain --description 'Ferrowyn Energy customer account events. Demo data.' --workspace ws-…
  · four projects with the same name: A and B get the event messages, C gets A's facts + the event log + the app's state, D only the event log + the app's state
$ memorylake actor create --custom-id mlu-cev-bus --display-name account-event-store --description 'Event store that writes each customer account'"'"'s events into memory. Demo data.' --tags demo
$ memorylake boundary create --name 'Account assistant (the events as they are)' --custom-id mlu-cev-plain --projects proj-… --workspace ws-…
  · one boundary per project — what the account assistant reads through MemoryRouter
```

The four projects share one plain name: a project's name is context for extraction, so it must not differ between
the arms. The arm is in the `custom_id` and the boundary name.

### Step 3: twenty-four account events, six of them reversals

```
    account                event     day         what happened
    Tallow & Finch Bakery  evt-1002  Wed 02 Sep  AccountOpened → 14 Quarry Lane
                           evt-1010  Thu 10 Sep  ServiceAddressChanged → 3 Mill Yard
                           evt-1018  Tue 29 Sep  EventReversed: reverses evt-1010 — the request came from an unverified caller; fraud r
                                                 = service address now: 14 Quarry Lane
    …
    Pellow & Daughters     evt-1001  Tue 01 Sep  AccountOpened → Standard
                           evt-1011  Fri 11 Sep  PlanChanged → Business Plus
                           evt-1020  Fri 02 Oct  PlanChanged → Business
                                                 = plan now: Business   (control: no reversal)

      A: Event evt-1018 on account Tallow & Finch Bakery: EventReversed: reverses evt-1010. The request came
      from an unverified caller; fraud review FR-22 confirmed it.
      B: Event evt-1018 on account Tallow & Finch Bakery: EventReversed: reverses evt-1010
      (ServiceAddressChanged: Service address changed from 14 Quarry Lane to 3 Mill Yard, Halden.) The request
      came from an unverified caller; fraud review FR-22 confirmed it. That change is undone: Tallow & Finch
      Bakery's service address is 14 Quarry Lane again.
$ memorylake conv create --project proj-… --actors actor-… --kind GROUP --name 'Tallow & Finch Bakery account events' --custom-id mlu-cev-conv-plain-tallow --workspace ws-…
$ memorylake conv msg append conv-… --actor actor-… --custom-id evt-1002 --timestamp 2026-09-02T09:00:00Z --text 'Event evt-1002 on account Tallow & Finch Bakery: AccountOpened. …'
$ memorylake conv consumed-messages conv-… --message conv-entry-… --workspace ws-…
  · 16 conversations, 48 messages, all read in 371 s
```

One GROUP conversation per account and memory; the events are written in order, each stamped with its own time
and read (`conv consumed-messages --message` + `cook-status`) before the next is written. A and B are the same except
the six reversal messages; in B the app looks the undone event up in its own store and writes it out.

### Step 4: what memory says about each account now

```
$ memorylake fact list --projects proj-… --page-size 200 --workspace ws-…
$ memorylake conv fact-actions conv-… --project proj-… --page-size 100 --workspace ws-…

    account                field              true now                    A · as they are   B · spelled out
    Tallow & Finch Bakery  service address    14 Quarry Lane              STALE             current
    Brackenridge Dental    plan               Standard                    STALE             current
    Ostler Row Studios     billing email      accounts@ostlerrow.example  STALE             current
    Marrowgate Laundry     payment method     card ending 4417            STALE             current
    Kestle Yard Brewery    meter tariff       Day/Night                   STALE             STALE
    Wrenfield Clinic       contract end date  31 March 2027               STALE             current
    Pellow & Daughters     plan               Business                    current           current              (control)
    Corran Ferries         service address    9 Pier Street               current           current              (control)

    A: accounts whose latest fact does not state the current value
      · Tallow & Finch Bakery [STALE]: The service address for the Tallow & Finch Bakery account is 3 Mill Yard,
      · Halden (as of 2026-09-10, previously 14 Quarry Lane, Halden as of 2026-09-02).
          and, in the same memory: evt-1010 was reversed because the request came from an unverified caller and fraud
          review FR-22 confirmed it. (on 2026-09-29)
      · Ostler Row Studios [STALE]: Invoices for the Ostler Row Studios account go to billing@ostler-row.example. (as
      · of 2026-09-14, previously accounts@ostlerrow.example as of 2026-09-04)
          and, in the same memory: The new billing address set by evt-1013 was a typo, and invoices sent to it
          bounced. (as of 2026-10-06)
      · Wrenfield Clinic [STALE]: The Wrenfield Clinic account contract end date is 30 September 2027. (as of
      · 2026-09-20)
          and, in the same memory: Event evt-1024 on account Wrenfield Clinic reversed evt-1016 because the contract
          extension was signed by someone without authority to sign for the clinic. (on 2026-10-12)
      …
    A: of the 6 accounts with a reversal — current 0 · stale (the reversed value) 6 · tie 0 · missing 0; a fact about the reversal is in memory for 6; …
    B: of the 6 accounts with a reversal — current 5 · stale (the reversed value) 1 · …
```

Each fact is attributed to its account by the conversation that wrote it (`conv fact-actions`) — extracted facts
often leave the account's name out. For each account the demo takes the latest-dated fact that states a value: the
part before any "previously", without "from X to". A sentence about the reversal states a value only when it says
what the value went back to ("… is 14 Quarry Lane again"); a sentence that rejects a value ("the bank rejected
mandate DD-5530", "was a typo") states none.

### Step 5: C and D — the app folds each account's events itself

```
    The app's projection, folded in code from each account's events (a reversal takes the event it names out of the stream):
      Tallow & Finch Bakery  service address    14 Quarry Lane              evt-1010 undone by evt-1018
      …
$ memorylake fact add 'evt-1017 reversed evt-1014 because the bank rejected direct debit mandate DD-5530. (on 2026-09-23)' … --project proj-… --workspace ws-…
$ memorylake fact add 'Event log · Tallow & Finch Bakery · evt-1002 · 2026-09-02 · AccountOpened · service address = 14 Quarry Lane: …' … --project proj-…
$ memorylake fact add 'Account state for Tallow & Finch Bakery, service address: 14 Quarry Lane. Folded by the app from …' … --project proj-…
  · C: the 26 facts MemoryLake extracted into A, copied word for word, then 24 event-log facts (id and day kept) and 8 state facts that the app wrote
  · D: the same 24 event-log facts and 8 state facts, and nothing extracted
      · Account state for Tallow & Finch Bakery, service address: 14 Quarry Lane. Folded by the app from the account's
      · event stream (evt-1002 … evt-1018, up to 2026-09-29). evt-1010 (ServiceAddressChanged to 3 Mill Yard) was
      · reversed by evt-1018 on 2026-09-29, so it no longer counts.
```

`fold()` is the projection: replay the events in order; a reversal takes the event it names out of the stream. It
is ten lines of code. The event-log facts are written in a form the app can parse back (`Event log · <account> ·
<id> · <day> · <type> · <field> = <value>: …`), which is what step 8 reads.

### Step 6: ask the account assistant

```
  A  the events as they are
    Tallow & Finch Bakery · service address   ↺ ↺ ↺    Answer: 3 Mill Yard, Halden. The service address for Tallow & Finch Ba…
    Ostler Row Studios · billing email        ↺ ↺ ↺    Answer: billing@ostler-row.example. Invoices for Ostler Row Studios ar…
    Marrowgate Laundry · payment method       ? ? ?    Answer: Unknown. The current payment method for Marrowgate Laundry is …
    Wrenfield Clinic · contract end date      ↺ ↺ ↺    Answer: 30 September 2027. The Wrenfield Clinic's contract is set to e…
  B  reversals spelled out
    Tallow & Finch Bakery · service address   ↺ ↺ ↺    Answer: 3 Mill Yard, Halden. The service address for Tallow & Finch Ba…
    Wrenfield Clinic · contract end date      ✓ ✓ ✓    Answer: 31 March 2027. The Wrenfield Clinic's contract is set to end o…
  C  A + event log + app state
    Tallow & Finch Bakery · service address   ✓ ✓ ✓    Answer: 14 Quarry Lane. The service address for Tallow & Finch Bakery …
    Ostler Row Studios · billing email        ✓ ↺ ✓    Answer: accounts@ostlerrow.example. Invoices for Ostler Row Studios ar…
  D  event log + app state only
    Tallow & Finch Bakery · service address   ✓ ✓ ✓    Answer: 14 Quarry Lane. The service address for Tallow & Finch Bakery …
```

`router chat --boundary <memory>`, system prompt "Reply in one line that starts with "Answer: <the current value>""
(the grader reads that line). Each question also runs once without a boundary: a boundary call with exactly as many
prompt tokens had nothing attached (∅, not graded; none occurred in these runs).

### Step 7: the score

```
                                        current  ↺ reversed   ?   –    reversed accounts   controls
    A  the events as they are            10/24           11   3   0               5/18        5/6
    B  reversals spelled out             18/24            3   3   0              12/18        6/6
    C  A + event log + app state         23/24            1   0   0              17/18        6/6
    D  event log + app state only        24/24            0   0   0              18/18        6/6
```

### Step 8: replay — the event log, folded to any day

```
$ memorylake fact list --projects proj-… --page-size 200 --workspace ws-…

    24 event-log facts read back from C (of 58 facts there); folded per account:

    account                field              on Fri 25 Sep                 today
    Tallow & Finch Bakery  service address    3 Mill Yard                   14 Quarry Lane                ✓   (evt-1010 reversed by evt-1018)
    Brackenridge Dental    plan               Business Plus                 Standard                      ✓   (evt-1008 reversed by evt-1019)
    Marrowgate Laundry     payment method     card ending 4417              card ending 4417              ✓   (evt-1014 reversed by evt-1017)
    …
    Corran Ferries         service address    22 Slipway Road               9 Pier Street                 ✓
  · 8/8 accounts replay to the right value on both days — from the event log in memory, folded in code
```

`fact list` returns every fact in the project (a search returns the top k), so the whole log comes back; the app
parses the event-log facts and folds them up to the day asked. On 25 September the Marrowgate reversal (23 September)
has already happened, the others have not. No model is involved; each cell is checked against the data file's own
fold.

## Measured

Per run. "A: latest fact states the reversed value" and "B: latest fact current" are about the 6 reversed accounts,
read from the saved facts. Answers are graded from the saved `out/report.json` with the current grader: reversed
accounts (18 answers), controls (6); ↺ = the value the reversal undid (for a control, the value before the latest
change), ? = Unknown, – = neither.

| run | A: latest fact states the reversed value | B: latest fact current | A the events as they are | B reversals spelled out | C A + event log + app state | D event log + app state only | replay |
|---|---|---|---|---|---|---|---|
| first run | 5/6 (Brackenridge Dental: the change never reached memory) | 6/6 | 8/18 · controls 6/6 (↺ 7 · ? 3) | 14/18 · controls 3/6 (↺ 6 · ? 1) | 18/18 · controls 6/6 | — | 8/8 |
| second run | 6/6 | 6/6 | 4/18 · controls 3/6 (↺ 12 · ? 5) | 16/18 · controls 3/6 (↺ 4 · ? 1) | 15/18 · controls 6/6 (↺ 1 · ? 2) | — | 8/8 |
| third run (--reset) | 5/6 (Brackenridge Dental: the change never reached memory) | 6/6 | 5/18 · controls 6/6 (↺ 9 · ? 4) | 18/18 · controls 3/6 (↺ 3) | 16/18 · controls 6/6 (↺ 2) | — | 8/8 |
| PREFIX copy 1 | 6/6 | 5/6 (Kestle Yard Brewery stale) | 2/18 · controls 5/6 (↺ 14 · ? 3) | 18/18 · controls 3/6 (↺ 3) | 12/18 · controls 6/6 (↺ 6) | — | 8/8 |
| PREFIX copy 2 | 5/6 (Ostler Row Studios: the change never reached memory) | 6/6 | 8/18 · controls 6/6 (↺ 9 · ? 1) | 16/18 · controls 3/6 (↺ 3 · ? 2) | 15/18 · controls 6/6 (↺ 3) | — | 8/8 |
| fourth run (D added) | 6/6 | 5/6 (Kestle Yard Brewery stale) | 5/18 · controls 5/6 (↺ 11 · ? 3) | 12/18 · controls 6/6 (↺ 3 · ? 3) | 17/18 · controls 6/6 (↺ 1) | 18/18 · controls 6/6 | 8/8 |
| PREFIX copy 3 | 6/6 | 5/6 (Kestle Yard Brewery stale) | 3/18 · controls 6/6 (↺ 12 · ? 3) | 18/18 · controls 6/6 | 15/18 · controls 6/6 (↺ 3) | 18/18 · controls 6/6 | 8/8 |
| PREFIX copy 4 | 6/6 | 6/6 | 3/18 · controls 6/6 (↺ 12 · ? 3) | 18/18 · controls 5/6 (↺ 1) | 16/18 · controls 6/6 (↺ 2) | 18/18 · controls 6/6 | 8/8 |
| **total** | **45/48** | **45/48** | **38/144** · controls 43/48 (↺ 86 · ? 25) | **130/144** · controls 32/48 (↺ 23 · ? 7) | **124/144** · controls 48/48 (↺ 18 · ? 2) | **54/54** · controls 18/18 (↺ 0) | **64/64** |

D was added after the first five runs (A, B and C are unchanged). The PREFIX copies are the same demo run side by side
under another prefix (`mlu-cev1-`, `mlu-cev2-`).

Per question, all runs (✓ / answers):

| question | now | A | B | C | D |
|---|---|---|---|---|---|
| Tallow & Finch Bakery · service address | 14 Quarry Lane | 0/24 (↺ 24) | 20/24 (↺ 4) | 15/24 (↺ 9) | 9/9 |
| Brackenridge Dental · plan | Standard | 11/24 (↺ 13) | 24/24 | 21/24 (↺ 3) | 9/9 |
| Ostler Row Studios · billing email | accounts@ostlerrow.example | 9/24 (↺ 15) | 24/24 | 20/24 (↺ 4) | 9/9 |
| Marrowgate Laundry · payment method | card ending 4417 | 6/24 | 17/24 (↺ 2) | 22/24 | 9/9 |
| Kestle Yard Brewery · meter tariff | Day/Night | 12/24 (↺ 5) | 21/24 (↺ 1) | 24/24 | 9/9 |
| Wrenfield Clinic · contract end date | 31 March 2027 | 0/24 (↺ 24) | 24/24 | 22/24 (↺ 2) | 9/9 |
| Pellow & Daughters · plan (control) | Business | 20/24 (↺ 4) | 9/24 (↺ 15) | 24/24 | 9/9 |
| Corran Ferries · service address (control) | 9 Pier Street | 23/24 (↺ 1) | 23/24 (↺ 1) | 24/24 | 9/9 |

What goes wrong where:

- **A**: in 45 of 48 cases the latest fact about the reversed field states the undone value; the reversal itself is
  in memory as a fact of its own in 46 of 48 ("evt-1018 on account Tallow & Finch Bakery reversed evt-1010 …"), but
  no fact says what `evt-1010` was. The model then answers with the undone value (Tallow & Finch, Wrenfield: 0 right
  in every run) or, where the reason gives a hint ("the bank rejected mandate DD-5530"), says Unknown. In 3 of 48
  cases the change itself was never extracted (Brackenridge twice, Ostler once), so A was right by accident there.
- **B** fixes most of it: the latest fact is current for 45 of 48. But extraction now and then drops the *control*
  account's last ordinary change (Pellow & Daughters' move to Business was missing from B's memory in 3 of 8 runs; in
  two more it was there and the assistant still answered Business Plus — we did not find why). Spelling the reversal
  out is a fix to the message, not to the projection.
- **C** has the app's state fact *and* A's wrong fact in the same memory. Mostly the state fact wins (124/144), but
  not always: Tallow & Finch 15/24, with the copied "is 3 Mill Yard, Halden (as of 2026-09-10 …)" fact attached
  next to it.
- **D** reads only what the app projected: 54/54, controls 18/18.

## What else we measured

Probes before the demo (one account, ten events in two sessions; 2026-10-19):

- **The reversal was applied 0 of 3 times.** In one run the whole second session — two reversals, a plan downgrade,
  a deposit release — produced **no fact at all** (`conv fact-actions` empty, though all four messages were read in
  one batch). In the other two the reversals became facts of their own ("evt-0107 reversed evt-0104 because …") next
  to the facts they should have undone.
- With the reversals taken out of the second session, the deposit release was dropped 2 of 2 times — extraction
  drops ordinary events too; reversals are just the case where a dropped or stranded event flips the answer.

## Use it in your own agent

- **Keep the projection in your code.** An event store already knows how to fold its own events; a reversal is one
  line in that fold. Write the result into memory as one `fact add` per entity and field, saying which events set it
  and which were reversed. D: 54/54.
- **Don't let extraction write the same field next to it.** In C the app's state fact shared the memory with the
  facts extracted from the same events, and the stale ones still won 18 times out of 144. If you also send the raw
  events through a conversation, send them to a different project than the one your assistant reads for current
  state.
- **If you must send events through extraction, make reversals self-describing.** Write what the undone event was and
  what the value is now — B took the reversed accounts from 38/144 to 130/144. It does not stop extraction from
  dropping other events (the Pellow control).
- **Keep the event log in memory with its ids.** One `fact add` per event, in a form you can parse back, gives you
  replay to any day from memory alone (`fact list` returns all of it): 64/64 here.

## Troubleshooting

- **`this memorylake CLI has no router command`**: upgrade the CLI (install line above).
- **MemoryRouter refused the call for lack of balance**: top up in the console; `memorylake billing balance` shows it.
  Steps 2–5 and the replay do not need the Router.
- **`still extracting after 300 s`**: once in a while a message takes longer to read; the demo carries on with what
  memory holds. Run `python3 demo.py memory` a few minutes later to re-read the facts.
- **A looks right this time**: extraction varies; in some runs the change the reversal undoes was never extracted,
  and the old value is all memory has. That is why the table above adds up several runs.
- **The question in `ask` contains a `'`**: quote it with double quotes, as above.
- **`the demo memory does not exist yet`**: `memory`, `replay` and `ask` need a full run first (and `cleanup` removes it).
- **A run was interrupted**: run again; it deletes what the previous run left and starts over.

## Files

| File | What it is |
|---|---|
| `demo.py` | The demo: CLI wrapper, setup, the events (`message_text()`, `ingest_one()`, `wait_read()`), the census (`census_one()`, `asserted()`, `conv_fact_ids()`), the app's projection (`fold()`, `state_text()`, `log_text()`), Router questions, the grader (`grade()`), `replay` (`parse_log()` + `fold()`), `memory`, `ask`, cleanup (facts before the actor) |
| `data/events.json` | The eight accounts, their three events each (id, type, day, text, value or the event reversed), the questions and the value patterns, the replay day, the system prompt |
| `web/server.py`, `web/static/` | Browser companion (standard library server, no build step) |
| `out/` | `report.json` with every fact, every answer and grade, the replay; `out/runs/` keeps earlier runs' reports (git-ignored) |
