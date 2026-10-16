# Win rate memory: the right chart page is attached — whose numbers they are is not

A runnable version of the MemoryLake use case
[*Give Strategy Teams AI That Builds Cumulative Competitor Knowledge*](https://www.memorylake.ai/en/usecase/ai-memory-for-competitive-intelligence),
built entirely on the [`memorylake` CLI](https://github.com/memorylake-ai/memorylake-cli) (v20261015 or later).

**Watch it run:** videos are being recorded.

**The problem.** The sales team at Lumen Fieldworks keeps its competitive-intelligence pack in shared memory, so that
a rep's assistant can answer *"what was Halvard's win rate against us in May?"* from it. The pack is six chart pages
exported from the team's slides — one rival, one year, one line, every point labelled — and the only place a page
says *which rival* and *which year* is the chart title. The slide export names the files `ci-pack-p1.pdf` …
`ci-pack-p6.pdf`.

MemoryLake's index reads each chart into a *chart item*: the labels read off it and a summary. On four of the six
pages the chart item starts with the title. On page 2 (Halvard, 2026) the index splits the title off into a
paragraph of its own, and the chart item is eight numbers that belong to nobody:

```
chart item, text read off it:  Jan 31.4 ⏎ Feb 29.8 ⏎ Mar 33.1 ⏎ Apr 35.6 ⏎ May 34.2 ⏎ Jun 37.9 ⏎ Jul 36.5 ⏎ Aug 39.2
chart item, summary:           The figure is a line graph showing the percentage values from January to August. …
paragraph:                     Halvard win rate in head-to-head deals, 2026 (%)
```

Search still finds the page by its title, and MemoryRouter attaches the right page. What it attaches is the chart
item, labelled with the file name. Asked *"Which competitor had the highest head-to-head win rate against us in July
2026?"*, the model was given (a checker call copies out what it was given):

```
- (mlu-wrm-ci-pack-p2.pdf) The line graph shows the percentage values from January to August. …
Jan 31.4
…
Jul 36.5
…
- (mlu-wrm-ci-pack-p4.pdf) The line graph illustrates the monthly win rate percentage for Brisk … throughout 2026. …
Brisk win rate in head-to-head deals, 2026 (%)
…
- (mlu-wrm-ci-pack-p6.pdf) The figure shows the monthly win rate percentage for Ostrava … throughout 2026. …
```

Halvard's 36.5 is right there, under a label that says nothing. The assistant answered *"In July 2026, Brisk had the
highest head-to-head win rate against us at 24.9%"* — and in another call *"Ostrava had the highest … at 24.6%"*.
Comparing Ostrava's January figures, it once took the anonymous page for Ostrava 2025: *"29.7%, compared to 31.4% in
January 2025"* — 31.4 is Halvard's January 2026.

**What fixes it.** Store the same bytes under a file name that says whose numbers they are:
`halvard-win-rate-2026.pdf`. The chart item is just as anonymous, but the file name is the one label that travels
with every attached entry. The July question came back *"Halvard had the highest head-to-head win rate against us at
36.5%"* in every call.

**What a file name cannot fix.** On page 5 (Ostrava, 2025) the index kept the title in the chart item — and read
it wrong, in every import: `Ostrava Win Rate in Head to Head Deals, 2023 (%)`, summary *"throughout 2023"*. Asked
about March 2025, MemoryRouter either attached only the title paragraph (which it read right) and left the numbers
behind, or attached the numbers under the right file name with *2023* written in them. Either way the assistant said
*"I do not have the figure for Ostrava's head-to-head win rate against us in March 2025"*, every time, under both
sets of names. A misread year beats a correct label. For that, put the numbers where nothing has to be read off a
picture: a table under the chart whose rows name what they are ([plan-chart-memory](../plan-chart-memory)), or the
chart's data as a CSV next to the deck ([deck-chart-memory](../deck-chart-memory)).

## Prerequisites

- Python 3.9+ (standard library only)
- The `memorylake` CLI, **v20261015 or later** (`memorylake router` is new in that release):
  ```bash
  curl -fsSL https://raw.githubusercontent.com/memorylake-ai/memorylake-cli/main/scripts/install.sh | sh
  memorylake version
  ```
- A MemoryLake API key: sign up at [app.memorylake.ai](https://app.memorylake.ai), open **API Keys**, create one.
- MemoryRouter calls are billed to your MemoryLake balance: 48 calls per run, about 16,000 prompt + 3,600 reply
  tokens. With no balance the demo stops after step 4 and says so.

## Run it

```bash
git clone https://github.com/memorylake-ai/memorylake-usecases.git
cd memorylake-usecases/win-rate-memory
export MEMORYLAKE_API_KEY=sk-...          # logs into an isolated profile under .memorylake-demo/
python3 demo.py                           # about 5 minutes
python3 demo.py page 2                    # read-only: page 2 as the index holds it in both projects
python3 demo.py cleanup                   # delete everything the demo created
```

China region: also `export MEMORYLAKE_BASE_URL=https://app.memorylake.cn/openapi/memorylake`.
Everything the demo creates is prefixed `mlu-wrm-`; every run starts over.

## Or run it in the browser

```bash
python3 web/server.py                     # opens http://127.0.0.1:8766
```

Paste the key, press **Run demo**. The page follows the run step by step: the six charts, each page as the index
holds it (with the chart item's text and the split-off paragraph), the questions under each set of file names with
the checker's copy of what was attached, and a scoreboard. **Page** looks up one page in both projects without a
model call. A terminal drawer shows every `memorylake` command as it runs. **Clean up** removes it all.

## What happens, step by step

### Steps 2–3: two projects, two boundaries, one pack

```
$ memorylake proj create --name 'Lumen Fieldworks — Competitive intelligence pack, September 2026 (as exported)' --custom-id mlu-wrm-exported …
$ memorylake boundary create --name 'Rep assistant: as exported' --custom-id mlu-wrm-exported --projects proj-… …

  Competitive intelligence pack, September 2026 — six chart pages, one rival and one year each, every point labelled:
    page 1  Halvard  2025  Jan 22.4  Feb 23.9  Mar 21.7  Apr 24.8  May 26.1  Jun 25.3  Jul 27.4  Aug 28.0
    page 2  Halvard  2026  Jan 31.4  Feb 29.8  Mar 33.1  Apr 35.6  May 34.2  Jun 37.9  Jul 36.5  Aug 39.2
    page 3  Brisk    2025  Jan 14.2  Feb 15.8  Mar 13.9  Apr 16.4  May 17.7  Jun 16.9  Jul 18.3  Aug 19.5
    page 4  Brisk    2026  Jan 18.6  Feb 20.3  Mar 19.1  Apr 21.8  May 23.4  Jun 22.7  Jul 24.9  Aug 25.6
    page 5  Ostrava  2025  Jan 38.5  Feb 36.9  Mar 37.4  Apr 35.2  May 34.6  Jun 33.3  Jul 32.8  Aug 31.5
    page 6  Ostrava  2026  Jan 29.7  Feb 28.4  Mar 27.9  Apr 26.3  May 27.1  Jun 25.4  Jul 24.6  Aug 23.6
    the rival and the year are written in each chart's title, and nowhere else on the page

  project “as exported” — the pages under the names the slide export gave them:
    ci-pack-p1.pdf, ci-pack-p2.pdf, ci-pack-p3.pdf, ci-pack-p4.pdf, ci-pack-p5.pdf, ci-pack-p6.pdf
  project “named files” — the same pages, each file named for its rival, metric and year:
    halvard-win-rate-2025.pdf, halvard-win-rate-2026.pdf, brisk-win-rate-2025.pdf, …

$ memorylake lib upload data/halvard-win-rate-2025.pdf --name mlu-wrm-ci-pack-p1.pdf --on-conflict overwrite
$ memorylake proj doc import --project proj-… sc-…:inode-… … --wait --workspace ws-…
```

The six PDFs are uploaded twice — the same bytes under two sets of names — and each project imports its own six.
Every number in the pack is different and none is an axis label, so a number in an answer points at exactly one
page and month. The pages are generated by `data/make_pages.py` (matplotlib; the demo itself does not need it).

### Step 4: the pages in the index

`search --types document` returns, for each page, everything the index holds: the chart item (text and summary)
and any paragraph it split off.

```
  mlu-wrm-ci-pack-p1.pdf  (page 1: Halvard 2025 · as exported)
    chart item, text read off it:  Halvard win rate in head-to-head deals, 2025 (%) ⏎ ⏎ % ⏎ 50 ⏎ 40 ⏎ 30 ⏎ 20 ⏎ 10 ⏎ 0
                                   ⏎ Jan Feb Mar Apr May Jun Jul Aug ⏎ 22.4 23.9 21.7 24.8 26.1 25.3 27.4 28.0
    ✓ the chart item names its rival and year  ·  8 of 8 values in the chart item
    same page as mlu-wrm-halvard-win-rate-2025.pdf:  ✓ the chart item names its rival and year

  mlu-wrm-ci-pack-p2.pdf  (page 2: Halvard 2026 · as exported)
    chart item, text read off it:  Jan 31.4 ⏎ Feb 29.8 ⏎ Mar 33.1 ⏎ Apr 35.6 ⏎ May 34.2 ⏎ Jun 37.9 ⏎ Jul 36.5 ⏎ Aug
                                   39.2
    chart item, summary:           The figure is a line graph showing the percentage values from January to August. …
    paragraph:                     Halvard win rate in head-to-head deals, 2026 (%)
    ✗ title split off into a paragraph of its own — the chart item names neither rival nor year  ·  8 of 8 values in the chart item
    same page as mlu-wrm-halvard-win-rate-2026.pdf:  ✗ title split off into a paragraph of its own — …

  mlu-wrm-ci-pack-p5.pdf  (page 5: Ostrava 2025 · as exported)
    chart item, text read off it:  Ostrava Win Rate in head to head deals, 2023 (%) ⏎ ⏎ 50 ⏎ 40 ⏎ 30 ⏎ 20 ⏎ 10 ⏎ 0 ⏎ %
                                   ⏎ Jan Feb Mar Apr May Jun Jul Aug ⏎ 38.5 36.9 37.4 35.2 34.6 33.3 32.8 31.5
    chart item, summary:           The figure shows the monthly win rate percentage for Ostrava in head-to-head deals
                                   throughout 2023. …
    ✗ the chart item reads the title as 2023  ·  8 of 8 values in the chart item

  what the index holds, per project:
    as exported  the chart item names its rival and year on 4 of 6 pages  ·  lost on page(s) 2, 5
    named files  the chart item names its rival and year on 4 of 6 pages  ·  lost on page(s) 2, 5
```

The index reads the chart off the rendered page, so the same bytes give the same result under either name. Which
pages lose their title depends on the page's layout: in our probes a page whose labels sit near the title was split
more often, but we did not pin the rule down — check your own pages with `python3 demo.py page <n>`.

### Steps 5–6: ask through MemoryRouter, under each set of names

Six questions a rep asks — three single figures, two year-on-year comparisons, which rival is ahead — through each
boundary, each answered three times (`gpt-4o-mini`, temperature 0). A checker call (`claude-haiku-4-5`) copies out
what it was given, so you can see which page MemoryRouter attached, under which label, and whether the entry says
whose numbers they are.

```
$ memorylake router chat "What was Halvard's head-to-head win rate against us in May 2026?" --model gpt-4o-mini --max-tokens 200 --temperature 0 --body out/body.json --boundary bnd-…

  Q5  Which competitor had the highest head-to-head win rate against us in July 2026 (Halvard, Brisk or Ostrava), and what was it?
      on the pages: Jul 2026: Halvard 36.5% · Brisk 24.9% · Ostrava 24.6% → Halvard  (pages 2, 4, 6)
      checker: attached p2 (Halvard 2026) as “ci-pack-p2.pdf”: 8/8 values, the text names neither rival nor year;
               p4 (Brisk 2026) as “ci-pack-p4.pdf”: 8/8 values, the text names its rival and year; p6 (Ostrava 2026) …
      answers ✗ · ✗   e.g. “In July 2026, Brisk had the highest head-to-head win rate against us at 24.9%.”
      ↳ named Brisk; on the pages Halvard was ahead at 36.5%
```

Under the named files, the same question:

```
      checker: attached p2 (Halvard 2026) as “halvard-win-rate-2026.pdf”: 8/8 values, the text names neither rival nor year; …
      answers ✓ ✓ ✓   e.g. “In July 2026, Halvard had the highest head-to-head win rate against us at 36.5%.”
```

And page 5, under both:

```
  Q2  What was Ostrava's head-to-head win rate against us in March 2025?
      on the pages: Ostrava Mar 2025: 37.4%  (page 5)
      checker: attached p5 (Ostrava 2025) as “ostrava-win-rate-2025.pdf”: 8/8 values, the text reads it as 2023
      answers · · ·   e.g. “I do not have the figure for Ostrava's head-to-head win rate against us in March 2025.”
```

Grading: a single figure is right if the first figure in the reply is the asked one; a comparison needs both years'
figures (or one and the change) and no other figure; "which rival is ahead" needs the right rival and its figure.
Every wrong figure is traced to the page and month it came from. A reply that says it was not given the figure is
counted as "no figure" — the honest failure.

### The read-only subcommand

`python3 demo.py page 2` shows one page as the index holds it in both projects, next to the numbers on the page —
searches only, no model call.

## Measured

Over **6 runs** (108 answers per set of names; the totals are recomputed from every reply with the grading above):

| | right | wrong | incomplete | no figure |
|---|---|---|---|---|
| as exported (`ci-pack-p2.pdf`) | 36/108 | 23 | 14 | 35 |
| named files (`halvard-win-rate-2026.pdf`) | 66/108 | 4 | 14 | 24 |

By question:

| | as exported | named files |
|---|---|---|
| Q1 Halvard May 2026 (page 2, title split off) | 9/18 | 18/18 |
| Q5 who was ahead in July 2026 (pages 2, 4, 6) | 2/18 (14 named Brisk or Ostrava) | 18/18 |
| Q4 Halvard Aug 2025 vs Aug 2026 (pages 1, 2) | 14/18 | 14/18 |
| Q3 Brisk June 2026 (page 4, title kept) | 11/18 | 14/18 |
| Q2 Ostrava March 2025 (page 5, year read as 2023) | 0/18 | 0/18 |
| Q6 Ostrava Jan 2025 vs Jan 2026 (pages 5, 6) | 0/18 | 2/18 |

- **The index**: page 2's title was split off in 12 of 12 imports (6 runs × 2 projects); page 5's year was read as
  2023 in 12 of 12. The other four pages kept their title every time.
- **Q3's wrong answers** (11 over both) are all *23.4%* — Brisk's May, the figure next to June in a chart item that
  lists the months on one line and the values on the next. That is the model misreading an attached, labelled
  entry, not a missing label.
- What the index reads and what MemoryRouter attaches vary from run to run; these are totals over every run,
  including the ones recorded for the videos.

## Use it in your own app

- **Name files for what is in them.** MemoryRouter labels every attached entry with its file name, and when the
  text read off a chart does not say whose numbers they are, the label is all the model has. `rival-metric-year.pdf`
  costs nothing; `export-p2.pdf` costs answers.
- **Check what the index read off your charts.** `memorylake search "<page title>" --projects <p> --types document`
  shows each page's chart item; if it does not name what the chart is about, or names it wrong, neither will the
  answers.
- **Don't let a picture carry the facts.** A year or a name that is only in a chart title is read off an image and
  can be split off or misread. Put the numbers in a table whose rows say what they are, or store the chart's data as
  a CSV next to it.
- **See what was attached.** A checker call through the same boundary (`router messages … --system "<copy out
  everything above my request>"`) shows the entries and their labels; the demo's `read_attached()` parses it.

## Troubleshooting

- `this memorylake CLI has no 'router' command` — upgrade the CLI (v20261015 or later).
- `MemoryRouter refused the call for lack of balance` — the memory steps still run; top up to see the answers.
- `the documents are still processing after 6 minutes` — run again; the demo starts over.
- `search did not return the pages yet` — the index can lag a few seconds behind the import; run again.
- Leftovers from an interrupted run: `python3 demo.py cleanup` (everything is prefixed `mlu-wrm-`).

## Files

- `demo.py` — the whole demo (standard library only): CLI wrapper, setup, index view, MemoryRouter calls, the
  checker parser, grading, cleanup
- `data/pack.json` — the six pages' numbers, the two naming schemes, the questions, the system prompt and the
  checker instruction
- `data/*.pdf` — the six chart pages (vector PDF); `data/*.png` — pictures of them for the web page
- `data/make_pages.py` — regenerates the pages from `pack.json` (needs matplotlib)
- `web/server.py`, `web/static/` — the browser companion (standard library HTTP + server-sent events)
