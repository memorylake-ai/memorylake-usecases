# AI memory for small SaaS support teams

A runnable version of the MemoryLake use case
[*Give Small SaaS Support Teams AI Memory That Holds Every Ticket and Customer*](https://www.memorylake.ai/en/usecase/ai-memory-for-small-saas-support-teams),
built entirely on the [`memorylake` CLI](https://github.com/memorylake-ai/memorylake-cli).

<!-- videos -->

**The problem.** *A customer reported the bug last month; the fix was a specific workaround. Today another customer hits the
same symptom, and the AI support tool doesn't know who it is talking to.* A tool that sees one ticket at a time can match a
ticket to a known issue by its symptom. What it cannot see is whether that issue applies to **this** customer, and whether
the workaround that helped someone else is harmful on this customer's account.

**What this demo shows.** Tallyfold (fictional) sells invoicing software, and four people answer its support queue. Two
kinds of memory sit behind every ticket:

| Memory | Where | What is in it |
|---|---|---|
| **Known issues** (team-wide) | one project | 4 issues, each pinned word for word with `metadata`: symptom, the conditions it applies under, its workaround, **what that workaround breaks**, status |
| **Per-customer** | one actor per customer | what the customer told us in a September ticket (a DIRECT conversation MemoryLake turns into memory), plus the facts support relies on pinned with the **ticket and date** they came from: browser, currencies, default currency, integrations, how they want replies |

Today, 2026-10-14, two customers write in with the same symptom: *the totals on our PDF invoices are blank.*

1. **By symptom alone, both get the same reply.** `search` over the known issues ranks KI-101 first for both tickets
   (*blank totals in Safari; switch the PDF renderer to Legacy*), in every run so far.
2. **With customer memory, the answers split.** Every known issue with that symptom is checked against what each customer
   told us, condition by condition, citing the ticket and date. Marlow Dental Group runs Safari, so KI-101 ✓.
   Copperleaf Bakery runs **Chrome 129** and bills in **EUR + USD** with no default currency, so KI-101 ✗ and KI-117 ✓.
3. **The symptom-only reply was not just wrong, it was harmful.** KI-101's workaround (the Legacy renderer) is exactly what
   **KI-109** forbids on multi-currency accounts: it drops the VAT lines. The check follows that link between two known
   issues and holds the workaround back, saying why.
4. **The reply goes into memory.** What was matched, what was said and what was held back (and why) goes onto the
   customer's actor. Next week's agent asks *"what did we tell this customer about blank PDF totals?"* and gets today's
   record as the #1 hit.

```
known issues ── fact add --project <KI> 'KI-101 …' 'KI-109 …' 'KI-117 …' 'KI-122 …'  (one call)
                 + fact update --metadata {symptom, applies_when, workaround, breaks, status}
customers ───── conv create --project <tickets> --kind DIRECT · msg append × 5 · cook-status   (per customer)
                 fact add --actor <customer> '…' × 5 + fact update --metadata {attr, value, ticket, date}
today, naive ── search '<ticket>' --projects <KI> --top-k 3                  → same top hit for both
today, memory ─ search '<ticket>' --projects <KI> --actors <customer>        (one call: issues ∪ customer)
                 fact list --projects <KI>  ×  fact list --actors <customer>  → every condition ✓/✗, cited
                 known issue whose `breaks` = this workaround and applies here → ⚠ held back
remember ────── fact add --actor <customer> 'Ticket T-4631 … Matched KI-117 … Did not suggest KI-101 (…)'
come back ───── search '<what did we tell them …>' --actors <customer> --top-k 3
```

The check is plain code over the pinned facts' metadata, not a model: the same memory gives the same answer every time,
and every ✓ and ✗ names the fact (and the ticket) behind it. `search` is used where ranking is the point, the candidate
list is `fact list` (every page, nothing ranked away), and nothing depends on what extraction happens to pull out of a
conversation. Extracted facts are shown as "also remembered".

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
cd memorylake-usecases/saas-support-memory
export MEMORYLAKE_API_KEY=sk-...           # China region: also export MEMORYLAKE_BASE_URL=https://app.memorylake.cn/openapi/memorylake
python3 demo.py
```

About 2½–3 minutes the first time (most of it is MemoryLake turning the two September tickets into memory, 60–80 s), about
45 s on a re-run, which reads back what is already there. With the key set, the demo logs into an isolated CLI profile under
`./.memorylake-demo/` and never touches your own `~/.memorylake`. Everything it creates has a `custom_id` starting with
`mlu-ssm-`.

```bash
python3 demo.py ask copperleaf "The VAT lines are missing from my PDF invoices"   # any ticket, either customer: naive vs memory-checked
python3 demo.py customer marlow      # what support knows about a customer, with sources
python3 demo.py issues               # the known issues and their conditions
python3 demo.py --reset              # delete everything the demo created, then replay
python3 demo.py cleanup              # delete everything the demo created
```

## Or run it in the browser

```bash
python3 web/server.py                # opens http://127.0.0.1:8765
```

Paste the key, connect, run. The page drives the same functions as `demo.py` and streams every `memorylake` command into a
terminal drawer as it runs: the known issues, both customers' September tickets turning into memory, the two tickets side by
side by symptom only, then condition by condition with each customer's memory, then the record and the come-back search.
**Try a ticket** takes any text for either customer and shows the symptom-only answer next to the memory-checked one.

<!-- screenshots -->

## What happens, step by step

All output below is from a real run (`python3 demo.py --reset` on a free account, 2026-10-14). Ids will differ on yours.

### Steps 2–3 — two projects, one actor per customer; the known issues

Known issues are team memory: one project every agent reads. Past ticket conversations go in a **separate** project:
conversations are turned into memory on the project they live in, and the known-issues project should hold only issues.
The four issues go in with one `fact add`, then `fact update --metadata` on each (`fact add` takes no metadata):

```
  issue   symptom             applies when                                 workaround / note
  KI-101  pdf-totals-blank    browser Safari                               Settings > Invoices > PDF renderer > Legacy, then export …
  KI-109  vat-lines-missing   currencies more than one                     BREAKS the “legacy-renderer” workaround
  KI-117  pdf-totals-blank    currencies more than one and default curren… Settings > Billing > Default currency, pick one, then exp…
  KI-122  duplicate-customers integrations QuickBooks                      trim the customer name in Tallyfold, then run the sync ag…
```

### Step 4 — customer memory

Each customer's September ticket is a DIRECT conversation between the customer and Dana (senior agent) in the ticket-history
project. MemoryLake turns it into facts on the customer's actor (73 s for both in this run). What an agent must be able to rely
on is pinned, with the ticket and date it came from:

```
  Marlow Dental Group — what support knows (`fact list --actors …`):
    browser           Safari 17              from T-4388, 2026-09-09 · fact-7a94b27c958c4cf7bf2812f792533a11
    currencies        USD                    from T-4388, 2026-09-09 · fact-60c2118c87f048b29b39fe27abb914c9
    default_currency  USD                    from T-4388, 2026-09-09 · fact-2255e869c36e43fdac3c07368fddc880
    integrations      none                   from T-4388, 2026-09-09 · fact-57058305e3ca4d4b9aeebc3f9335bde4
    contact           email only             from T-4388, 2026-09-09 · fact-4485cc6b30f645f79d2f98a2e2095c8d
    + 3 fact(s) MemoryLake extracted from the ticket conversation (shown, not relied on):
```

### Step 5 — today's tickets, by symptom only

Leo started this week. The way a tool that sees one ticket at a time would answer: the ticket text against the known issues,
nothing else.

```
  T-4630 Marlow Dental Group: “Since this morning the totals on our PDF invoices are blank. The line items are there but Subtotal, Tax and Total are empty.”
$ memorylake search 'Since this morning the totals on our PDF invoices are blank. The line items are there but Subtotal, Tax and Total are empty.' --projects proj-c3f8bf1ede074307b0dfe55273fafcc5 --types fact --top-k 3 --workspace ws-83f0737bd3354e26908ff6f39cdfe5bc
    1. KI-101  KI-101 (open): PDF invoice export shows blank totals in Safari 17 and later on app 4.2. Workaro…
    2. KI-117  KI-117 (open): Invoice totals come out empty in any browser when an account bills in more than …
    3. KI-109  KI-109 (open): The Legacy PDF renderer drops the VAT lines from invoices that mix currencies, s…
    → reply: KI-101 — Settings > Invoices > PDF renderer > Legacy, then export again

  T-4631 Copperleaf Bakery: “Our PDF invoices show blank totals. The line items print fine but the Total box is empty. Can you help?”
$ memorylake search 'Our PDF invoices show blank totals. The line items print fine but the Total box is empty. Can you help?' --projects proj-c3f8bf1ede074307b0dfe55273fafcc5 --types fact --top-k 3 --workspace ws-83f0737bd3354e26908ff6f39cdfe5bc
    1. KI-101  KI-101 (open): PDF invoice export shows blank totals in Safari 17 and later on app 4.2. Workaro…
    2. KI-117  KI-117 (open): Invoice totals come out empty in any browser when an account bills in more than …
    3. KI-109  KI-109 (open): The Legacy PDF renderer drops the VAT lines from invoices that mix currencies, s…
    → reply: KI-101 — Settings > Invoices > PDF renderer > Legacy, then export again

  Both tickets get the same reply: KI-101 — the lookup knows the symptom, not the customer.
```

### Step 6 — the same tickets, with customer memory

One `search` over known issues ∪ the customer's actor shows what an agent would see. The decision does not rest on that
top-k: **every** known issue tagged with the ticket's symptom (from `fact list`) is checked against the customer's pinned
facts, and every known issue whose `breaks` names the candidate's workaround is checked too.

```
  Copperleaf Bakery — `search` over known issues ∪ Copperleaf Bakery's memory (top 8, one call):
    1. [known issue KI-117] KI-117 (open): Invoice totals come out empty in any browser when an account bills in mo…
    2. [Copperleaf: browser] Copperleaf Bakery runs Tallyfold in Chrome 129 on a Windows laptop.
    3. [known issue KI-101] KI-101 (open): PDF invoice export shows blank totals in Safari 17 and later on app 4.2.…
    4. [Copperleaf: default_currency] Copperleaf Bakery has not set a default currency in Tallyfold.
    5. [Copperleaf: extracted] The person uses Chrome 129 on a Windows laptop. (as of 2026-09-18)
    6. [Copperleaf: currencies] Copperleaf Bakery invoices in EUR (Dublin cafes) and USD (US cafes).
    7. [Copperleaf: integrations] Copperleaf Bakery syncs every invoice to QuickBooks.
    8. [Copperleaf: extracted] The person is the owner of Copperleaf Bakery. (as of 2026-09-18)

    Every known issue tagged pdf-totals-blank (from `fact list`, not the top-k), checked against Copperleaf Bakery's memory:
    ✗ KI-101  does not apply
        ✗ needs browser: Safari                        customer: Chrome 129  (T-4471, 2026-09-18)
        ⚠ its workaround (Settings > Invoices > PDF renderer > Legacy, then…) is forbidden by KI-109:
          “KI-109 (open): The Legacy PDF renderer drops the VAT lines from invoices that mix currencies, so the PDF total no longe…”
    ✓ KI-117  applies
        ✓ needs currencies: more than one currency     customer: EUR+USD  (T-4471, 2026-09-18)
        ✓ needs default currency: no default currency  customer: unset  (T-4471, 2026-09-18)
    → reply: KI-117 — Settings > Billing > Default currency, pick one, then export again  (as short numbered steps)
    ✗ the symptom-only reply (KI-101) was wrong for Copperleaf Bakery: needs browser Safari, Copperleaf has Chrome 129 (T-4471, 2026-09-18) — and it would trigger KI-109

  ticket   customer             symptom only    with customer memory
  T-4630   Marlow Dental Group  KI-101 ✓        KI-101 ✓
  T-4631   Copperleaf Bakery    KI-101 ✗        KI-117 ✓
```

Marlow's half of the same step: KI-101 ✓ (Safari 17, from T-4388), KI-117 ✗ (USD only, default currency USD), reply KI-101
*by email only*, which is what Marlow asked for in September.

### Step 7 — the reply goes into memory

```
  Marlow Dental Group: fact-e1486540c01e499daef7a90ac1f676aa
    “Ticket T-4630 (2026-10-14, Leo Park): PDF invoice totals blank. Matched KI-101; told Marlow Dental Group: Settings > Invoices > PDF renderer > Legacy, then export again. Did not suggest KI-117 (currencies is USD; default currency is USD).”
$ memorylake fact add --actor actor-217c2dcef8644ee2bda2479b085bf7a8 'Ticket T-4631 (2026-10-14, Leo Park): PDF invoice totals blank. Matched KI-117; told Copperleaf Bakery: Settings > Billing > Default currency, pick one, then export again. Did not suggest KI-101 (browser is Chrome 129; KI-109 forbids its workaround on this account).' --workspace ws-83f0737bd3354e26908ff6f39cdfe5bc
$ memorylake fact update fact-d6a98127ddb74d908ab14de6794df7f1 --actor actor-217c2dcef8644ee2bda2479b085bf7a8 --metadata '{"kind": "ticket", "ticket": "T-4631", "date": "2026-10-14", "agent": "Leo Park", "matched": "KI-117", "withheld": ["KI-101"]}' --workspace ws-83f0737bd3354e26908ff6f39cdfe5bc

  Copperleaf Bakery: fact-d6a98127ddb74d908ab14de6794df7f1
    “Ticket T-4631 (2026-10-14, Leo Park): PDF invoice totals blank. Matched KI-117; told Copperleaf Bakery: Settings > Billing > Default currency, pick one, then export again. Did not suggest KI-101 (browser is Chrome 129; KI-109 forbids its workaround on this account).”
  · next week another agent opens Copperleaf Bakery's next ticket: one `search` on the customer's actor
$ memorylake search 'What did we tell this customer about blank PDF totals, and what did we decide not to suggest?' --actors actor-217c2dcef8644ee2bda2479b085bf7a8 --types fact --top-k 3 --workspace ws-83f0737bd3354e26908ff6f39cdfe5bc

  “What did we tell this customer about blank PDF totals, and what did we decide not to suggest?” — Copperleaf Bakery's memory, top 3:
    1. ✓ Ticket T-4631 (2026-10-14, Leo Park): PDF invoice totals blank. Matched KI-117; told Copperleaf Bakery: Setti…
    2. · Copperleaf Bakery has not set a default currency in Tallyfold.
    3. · Copperleaf Bakery runs Tallyfold in Chrome 129 on a Windows laptop.
    today's record is #1
```

## Measured

Every run so far, CLI and web (see `out/runs.jsonl` on your machine for yours):

| | runs | result |
|---|---|---|
| symptom-only top hit, both tickets | 4 / 4 | KI-101 for both (same reply) |
| memory-checked reply | 4 / 4 | Marlow KI-101 · Copperleaf KI-117 |
| KI-101's workaround held back for Copperleaf (KI-109) | 4 / 4 | ⚠ cited every time |
| today's record found again by the come-back question | 4 / 4 | #1 of 3 |
| facts extracted from the September tickets (shown, not relied on) | 3 fresh runs | Marlow 3–5, Copperleaf 5–7 |

4 runs = 3 in the terminal (first run, resume, `--reset`) and 1 in the web companion. First run 2¼–3 min, resume 45 s.

## Wiring it into your own support stack

- **Known issues**: write one fact per issue from your tracker (`fact add --project`), and keep the conditions, workaround
  and what it breaks in `metadata` (`fact update --metadata` replaces the whole object). When an issue is fixed, update its
  status the same way; the check reads metadata, so nothing else changes.
- **Customer memory**: one actor per customer account (`actor create --custom-id <your account id>`). Append ticket
  conversations from Zendesk / Intercom as DIRECT conversations, and pin the facts your agents must rely on (plan, browser,
  currencies, integrations, contact preference) with the ticket they came from.
- **On a new ticket**: `search '<ticket>' --projects <known issues> --actors <customer>` for the agent's context, then the
  condition check over `fact list` before any workaround goes out. `ask` in `demo.py` is that function.
- **After the reply**: `fact add --actor <customer>` with what was said and what was held back.

## If something goes wrong

- **`the memorylake CLI is not on PATH`**: install it (above) or `export MEMORYLAKE_BIN=/path/to/memorylake`.
- **`gave up after 600s`** while turning the tickets into memory: the server is slow, not broken. Run `python3 demo.py` again;
  it resumes, appends nothing twice and waits again.
- **The symptom-only top hit is different on your run**: search ranking can move. The demo prints what it got and judges it
  with the same check; the table at the end of step 6 says whether the naive reply was right.
- **Something half-created after an interrupted run**: `python3 demo.py --reset`, or `python3 demo.py cleanup`. Cleanup deletes
  every fact on each actor before the actor itself (`actor delete` leaves an actor's facts readable but undeletable).

## Files

```
saas-support-memory/
├── demo.py              # the CLI runner (every step is a plain `memorylake` command, echoed)
├── data/story.json      # known issues, customers, September tickets, today's tickets (fictional, plain ASCII)
├── web/server.py        # local web companion: same functions, Server-Sent Events
├── web/static/          # no-build front end
└── out/runs.jsonl       # one line per run (git-ignored)
```
