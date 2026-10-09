# Brand asset memory for design teams

A runnable version of the MemoryLake use case
[*Give Design Teams Brand Asset Memory the AI Actually Sees*](https://www.memorylake.ai/en/usecase/brand-asset-memory-for-design-teams),
built entirely on the [`memorylake` CLI](https://github.com/memorylake-ai/memorylake-cli).

**The problem.** Brand assets live in a DAM, a shared drive or a Figma library, and they come out of it
as `IMG_2031.png`. An AI design tool cannot open any of them, and a file name says nothing about what is
in the picture. So every new designer asks the brand lead "which logo do I use?" and gets a file pasted
into chat.

**What this demo shows.** Fernway Coffee Roasters exports six brand images from its DAM, all with
camera-style names. They go into one MemoryLake project, and MemoryLake **looks at each picture**:

- it writes down what is in it ("a logo for 'FERNWAY coffee roasters' … a stylized tree or fern icon"),
  **reads the text on it** ("Logo misuse - DON'T  Don't stretch it  Don't recolor it") and lists **the
  questions the picture answers** ("What is the hexadecimal code for Fern Green?");
- a new designer asks in plain words — "what must I never do with the logo?" — and gets **the right
  picture at rank 1, picked by what is in it**, plus the original file, downloaded from memory into a
  brief;
- the brand lead's sign-off meeting goes in too, with the pictures sent **in the chat** as `IMAGE`
  content blocks, and her rules (hex codes, accent-only red, clear space) pinned as facts;
- the retired 2019 wordmark is removed from memory, and the question that returned it at rank 1 no
  longer returns it at all.

```
DAM export (IMG_*.png/.jpg) ── lib upload + proj doc import ──▶ image documents ─┐  (described, text read,
brand review (IMAGE blocks) ── conversation ──▶ facts from the words ─────────────┼─▶ one     questions listed)
brand rules ── fact add ──▶ pinned facts ─────────────────────────────────────────┘   project ──▶ search ──▶ proj doc download
```

Runs in about 3 minutes on a free personal account. The only credential you need is a MemoryLake API key.

**Watch it run** (real recordings, unedited):
[CLI demo, 4:06](https://github.com/memorylake-ai/memorylake-usecases/releases/download/brand-asset-memory-v1/brand-asset-memory-cli-demo.mp4) · [Web companion demo, 5:04](https://github.com/memorylake-ai/memorylake-usecases/releases/download/brand-asset-memory-v1/brand-asset-memory-web-demo.mp4).
The CLI recording shows one miss (✗, 4/5); the web recording 5/5 — see "Ranking is sensitive to wording".

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
cd memorylake-usecases/brand-asset-memory

export MEMORYLAKE_API_KEY=sk-…
python3 demo.py
```

With `MEMORYLAKE_API_KEY` set, the demo logs into its own CLI profile under `./.memorylake-demo/`
(git-ignored) and leaves `~/.memorylake` alone. Without it, it uses your existing `memorylake auth login`.

| | |
|---|---|
| `MEMORYLAKE_BASE_URL` | API endpoint; default global, China is `https://app.memorylake.cn/openapi/memorylake` |
| `MEMORYLAKE_WORKSPACE` | Workspace id; default is the one remembered by `ws use`, else your default workspace |
| `python3 demo.py --reset` | Delete the demo project and the review first, then import everything again |
| `python3 demo.py ask` | Ask the designer's five questions again and rewrite `out/brand-brief.md` |
| `python3 demo.py seen` | Print what MemoryLake saw in each image |
| `python3 demo.py cleanup` | Delete the review, the project (with its documents and facts), the actors and the uploaded images |

Safe to re-run: everything is found again by `custom_id`, prefixed `mlu-bam-`; re-imports are reported
as duplicates (the wordmark that step 7 removed is imported again); the rules are pinned only in the run
that stores the review.

## Or run it in the browser

```bash
python3 web/server.py        # http://127.0.0.1:8765 — standard library only
```

![Web companion — the designer's answers, each with the picture that won](https://github.com/memorylake-ai/memorylake-usecases/raw/main/brand-asset-memory/web/screenshot-answers.png)

[Watch the web companion demo (mp4, 5:04)](https://github.com/memorylake-ai/memorylake-usecases/releases/download/brand-asset-memory-v1/brand-asset-memory-web-demo.mp4).

Paste the key and press **Run demo**. The page shows the six images as they are processed, what
MemoryLake saw in each one, the review with its pictures inline, every answer with the picture that won
and the two runners-up, and the retired wordmark before and after. **Ask the memory** runs the same
search for anything you type — describe a picture ("something with a red cross on it") and get the file
back. The terminal drawer shows every `memorylake` command as it runs.

## What happens, step by step

**1. Connect** — validate the key, pick a workspace.

**2. Set up.** Dana Reyes (brand lead) and Canvas (the team's design assistant) are actors bound to the
workspace; one project holds the brand. Ivo Marsh, the freelancer, only asks questions, so he needs no
actor.

```bash
memorylake actor create --custom-id mlu-bam-dana --display-name "Dana Reyes" --tags brand,fernway …
memorylake actor bind --actor actor-… --workspace ws-…
memorylake proj create --name "Fernway Coffee Roasters — brand asset memory" --custom-id mlu-bam-fernway-brand
```

**3. The DAM export.** Six images (five PNG, one JPEG) go into the Library and are imported into the
project. `--wait` blocks until the server has processed them, about 50 seconds:

```bash
memorylake lib upload data/assets/IMG_2031.png --on-conflict overwrite
…
memorylake proj doc import --project proj-… <item ids…> --wait
memorylake proj doc list --project proj-…
```

```
  · imported: 6 new, 0 already in project, 0 failed (50s)

  6 brand images in memory — this is all a file name tells you:

   - IMG_1960.png     okay
   - IMG_2031.png     okay
   - IMG_2047.png     okay
   - IMG_2052.png     okay
   - IMG_2088.jpg     okay
   - IMG_2093.png     okay
```

**4. What MemoryLake saw.** One wide document search brings every image back. Each hit is an
`image_file` with a `document_summary` (a description of the picture); its `items` hold a `figure`
(with `prequestion_list`, the questions the picture answers) and, when the image has words on it, a
`paragraph` whose `highlight.chunks` are **the text read off the image**. A real run:

```bash
memorylake search "Fernway brand image" --projects proj-… --types document --top-k 10
```

```
  ▶ None of these names says what is in the picture. MemoryLake looked at each one:

   IMG_1960.png  (image_file)
     saw:        The figure is a logo for a company named 'Fernway & Co.', indicating that it was established in 2019. …
     read on it: "Fernway & Co. ~ est. 2019 ~"
     answers:    What is the name of the company in the logo?
                 In what year was Fernway & Co. established?

   IMG_2047.png  (image_file)
     saw:        This figure displays a color palette consisting of four colors: Fern Green, Kiln Red, Oat, and Roast Brown. …
     read on it: "Fern Green #2B5D4F primary / Oat #F4EFE6 backgrounds / Roast Brown #4A3428 body text"
     answers:    What is the hexadecimal code for Fern Green?
                 What is the purpose of Kiln Red in this palette?

   IMG_2052.png  (image_file)
     saw:        The figure illustrates improper ways to use the 'FERNWAY coffee roasters' logo. It advises against
                 stretching the logo, changing its colors, and plac…
     read on it: "Logo misuse - DON'T Don't stretch it Don't recolor it / Don't place it on busy patterns"
     answers:    What are the three main guidelines for logo misuse shown in the figure?

   IMG_2088.jpg  (image_file)
     saw:        The image shows a label for a coffee product named 'FERNWAY Harvest Blend.' It indicates that the coffee
                 is available in whole bean form, weighs 340…
     read on it: "FERNWAY Harvest Blend 340 g whole bean Autumn 2026"
   …
```

The `figure` item also carries `persist_path`, a signed download URL for the processed image. It
contains credentials, so the demo never prints it.

**5. The brand review.** Dana signs off the autumn bag and retires the old wordmark, sending each
picture in the chat as an `IMAGE` content block that points at the Library file. Her rules are pinned
verbatim:

```bash
memorylake conv create --custom-id mlu-bam-review-2026-09-15 --project proj-… --actors actor-dana,actor-canvas \
  --kind DIRECT --name "Brand review — autumn packaging sign-off" --metadata kind=brand-review
memorylake conv msg append conv-… --actor actor-dana --custom-id turn-01 --timestamp 2026-09-15T09:02:00Z \
  --content-json '[{"block_type":"TEXT","text":"Dana Reyes (Brand lead, Fernway Coffee Roasters): Here is the approved Harvest Blend bag …"},
                   {"block_type":"IMAGE","uri":"drive://…/<item id>","mime_type":"image/jpeg"}]'
memorylake fact add --project proj-… "Kiln Red #C8553D is an accent color only; never use it as a background." …
memorylake conv msg list conv-…
memorylake lib get <item id from the IMAGE uri>
```

```
  The review, read back from memory (IMAGE blocks resolved to their Library files):

   2026-09-15 09:02  Dana Reyes (Brand lead, Fernway Coffee Roasters): Here is the approved Harvest Blend bag for au…
       [IMAGE image/jpeg] → IMG_2088.jpg
   2026-09-15 09:04  Canvas (Design assistant (AI) used by the Fernway design team): Noted: the Harvest Blend bag is…
   2026-09-15 09:06  Dana Reyes (Brand lead, Fernway Coffee Roasters): And this is the old Fernway & Co. wordmark fr…
       [IMAGE image/png] → IMG_1960.png
   …

  11 facts: 4 brand rules pinned verbatim, 7 written by MemoryLake from the review.

   pinned   Kiln Red #C8553D is an accent color only; never use it as a background.
   pinned   The Fernway & Co. serif wordmark (2019–2023) is retired as of 2026-09-15; use the green-circle Fernway logo instead.
   learned  The Fernway & Co. wordmark from 2019 is retired (as of 2026-09-15).
   learned  The approved Harvest Blend bag for autumn 2026 is the reference for every seasonal bag, using a Fern Green
            body, an Oat label, and the blend name in Kiln Red (as of 2026-09-15).
   …
```

The pictures in the chat are stored with the messages and come back on replay, but facts are written
**only from the words**: nothing about the green circle or the fern came from the `IMAGE` blocks. What a
picture shows is read when the file is imported as a document (steps 3–4). Facts from a `DIRECT`
conversation attached to a project can land on the project, on the actor, or on both, so the demo reads
both scopes.

**6. The new designer asks.** One document search per question; the top hit is checked against the file
the brand lead would have sent, and its original is downloaded from memory. The colour question also
pulls the pinned rules.

```bash
memorylake search "examples of incorrect logo usage to avoid" --projects proj-… --types document --top-k 6
memorylake proj doc download doc-… --project proj-… --output out/assets/IMG_2052.png --force
```

```
  Q (Ivo Marsh): The logo with the fern in a green circle: which file is it?
     #1 IMG_2031.png   The image is a logo for 'FERNWAY coffee roasters'. It features a stylized tree or fern icon next to the…
     #2 IMG_1960.png   The figure is a logo for a company named 'Fernway & Co.', indicating that it was established in 2019. T…
     #3 IMG_2047.png   This figure displays a color palette consisting of four colors: Fern Green, Kiln Red, Oat, and Roast Br…
     ✓ rank 1 is IMG_2031.png — the file the brand lead would have sent
     ↓ out/assets/IMG_2031.png (15,919 bytes, byte-for-byte the file that was exported)

  Q (Ivo Marsh): What must I never do with the logo?
     #1 IMG_2052.png   The figure illustrates improper ways to use the 'FERNWAY coffee roasters' logo. It advises against stre…
     ✓ rank 1 is IMG_2052.png — the file the brand lead would have sent
  …
  · 5/5 questions answered with the expected image at rank 1
  · brief with the images embedded written to out/brand-brief.md
```

`out/brand-brief.md` has one section per question with the downloaded image embedded and the
description under it.

**Ranking is sensitive to wording.** Describe what is in the picture, the way you would to a colleague.
With all six images in the project, each of the five questions in `demo.py` put the expected image at
rank 1 in 5 of 5 tries; "the Fernway logo: a fern inside a green circle" put the same logo at rank 4 in
5 of 5, and "the current Fernway logo to put on packaging" put it at rank 1 in some runs and rank 4 in
others. The colour palette (the image with the most text on it) is the usual runner-up, and right after
import it sometimes ranks first: across four fresh full runs with these questions, 19 of 20 answers
had the expected image at rank 1 (5/5, 5/5, 4/5, 5/5); the one miss — in the CLI recording — put the
palette above the bag. If a question misses, the demo prints ✗ with the file it expected instead of
hiding it.

**7. Retire the old wordmark.** `proj doc delete` removes the document and everything MemoryLake
derived from it; the Library file stays. The same question before and after:

```bash
memorylake search "the Fernway & Co. serif wordmark" --projects proj-… --types document --top-k 6
memorylake proj doc delete doc-… --project proj-…
memorylake search "the Fernway & Co. serif wordmark" --projects proj-… --types document --top-k 6
```

```
  Q: Is there another Fernway wordmark?
     before: IMG_1960.png is rank 1 of 6 — "The figure is a logo for a company named 'Fernway & Co.', indicating that it was establis…"
     after:  IMG_1960.png is not returned (5 image(s) came back, none of them it)
```

## Wiring it into your own brand workflow

- One project per brand (or per client). Export the DAM folder and import every image
  (`lib upload` + `proj doc import`); PNG and JPEG both come back as `image_file` documents.
- Do not rename anything first: the description, the text read off the image and the question list are
  what search matches against.
- Pin the rules that must stay exact (hex codes, clear space, retired assets) with `fact add --project`;
  the pictures do not carry them as text you can rely on.
- From any tool, run `search "<what you need>" --projects <brand> --types document` and hand the top
  hit to the model or the designer; `proj doc download` gives you the file itself.
- Retire an asset with `proj doc delete`. Removing it from the Library alone does not take it out of the
  project's memory.

## If something goes wrong

- **`tls handshake eof` / `could not connect`** while polling: a dropped connection. Read-only commands
  retry four times; otherwise re-run `python3 demo.py` and it resumes.
- **`401 service account not found`**: the key was deleted or belongs to the other deployment.
- **A question shows ✗**: the ranking moved; see "Ranking is sensitive to wording" above. Run
  `python3 demo.py ask` again, or describe the picture more concretely.
- **The text read off an image has a typo** (we have seen "FERNWA" for "FERNWAY" on a small label):
  it is what the server read; the description and the search usually still get it right.
- **Facts come back worded differently, or in another language**: extraction is server-side and varies
  between runs; the pinned rules are always verbatim.

## Files

```
demo.py                       the runner; prints every CLI command it runs (and emits events for the web app)
data/assets/IMG_*.png|.jpg    the DAM export: logo, palette, logo misuse sheet, autumn bag, social template, 2019 wordmark
data/make_assets.py           regenerates the six images (optional; needs Pillow)
data/brand-review.json        the sign-off conversation (with the images it sends) and the rules pinned after it
web/server.py                 local web companion (stdlib HTTP server + Server-Sent Events over demo.py)
web/static/                   the page — no build step
out/                          the brief and the downloaded images (git-ignored)
```

All names, companies and brand assets are fictional.
