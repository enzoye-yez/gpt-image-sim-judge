# GPT image-similarity judge

Given a REFERENCE image and a CANDIDATE image, ask a GPT model how closely the candidate reproduces the reference.

- **Recommended (2026-11-27): the reconstruction-fidelity judges** in [`fidelity_judge.py`](fidelity_judge.py): seven
  single-dimension judges, each an integer 0–5 with a written reading and (for most dimensions) a list of issues.
  See [the next section](#reconstruction-fidelity-judges-2026-11-27-recommended).
- Older single-file judges in [`sim_judge.py`](sim_judge.py), kept working and unchanged: rate-v4 (seven 0–10
  dimensions + a fixed total), rate-v3, rate-v2. They are described further down.

## Reconstruction-fidelity judges (2026-11-27, recommended)

The CANDIDATE is meant to reproduce the REFERENCE (for example, an image regenerated from a written description of the
REFERENCE). Each dimension is one call with its own prompt and strict JSON schema. The judge first states what the
REFERENCE shows. It then lists the differences it finds, each tagged by importance (main / secondary / detail) and
severity (major / minor). Finally it picks a score from 5 down; the score is set by the most serious issue, not by
counting issues.

| dimension | checks | does **not** check (rated elsewhere) | inputs |
|---|---|---|---|
| `content` | subjects present or missing, still the same thing, appearance (design, shape, pose, own colors, parts) | position and size, text, lighting, style, breakage | REFERENCE, CANDIDATE |
| `composition` | placement, scale and crop, viewpoint, the subject's overall structure (pose, orientation, grouping), arrangement | what the things are, whether they are missing, colors | REFERENCE, CANDIDATE |
| `layout` | graphic designs only: block positions, size hierarchy, grid and grouping, alignment and spacing, reading order | what a block shows or says, its colors, composition inside a photo block | REFERENCE, CANDIDATE, design gate |
| `text` | what the readable text says: key text (requested text, headline, brand), other text, incidental small text | font, color, size, position, how many times a text repeats, text partly hidden or cropped | QUERY, REFERENCE, CANDIDATE, OCR lines |
| `lighting` | key light, light effects (shadows, highlights, reflections, glow), tone and light color, plausibility | what is lit, palette of objects, style | QUERY, REFERENCE, CANDIDATE |
| `style` | medium (photo, 3D, illustration, ...), style words (minimalist, cinematic, ...), overall mood | texture, brush strokes, grain, finish, content | QUERY, REFERENCE, CANDIDATE |
| `integrity` | whether the CANDIDATE is drawn broken: counts and ownership of limbs, clipping, continuity, surfaces, foreign or duplicated parts, deformation | text of any kind; differences that are themselves coherent; oddities the REFERENCE shares | QUERY, REFERENCE, CANDIDATE |

- **Prompts.** The English prompts sent to the model are in [`fidelity/prompts/<dim>.md`](fidelity/prompts). Chinese
  reading copies are in `<dim>.zh.md`. The JSON schemas are in [`fidelity/schemas.json`](fidelity/schemas.json).
- **Model settings.** `gpt-6.2-sol`, reasoning effort `medium`, images downscaled to a 1024 px long side (WebP q90,
  `detail: high`). This is the same as `sim_judge.encode_image`.
- **Layout: design gate.** `layout` only runs when the REFERENCE is a graphic design (poster, ad, card, infographic,
  slide, UI, ...). Give `"is_design": true/false` in the pair. If it is missing, the design gate decides: one
  `gpt-6-luna` call per REFERENCE, prompt [`fidelity/prompts/design_gate.md`](fidelity/prompts/design_gate.md). Pairs
  that are not designs get `{"score": null, "skipped": "not a design"}`.
- **Text: OCR.** `text` gets the RapidOCR lines of both images (top to bottom, with rough positions) as leads. The
  judge is told to trust the images over the OCR. If OCR finds fewer than 3 characters in both images, the pair is
  skipped (`"skipped": "no text"`). Without `--ocr` the judge still runs, but without OCR lines.
- **QUERY.** The generation request behind the images. `text`, `lighting`, `style` and `integrity` read it to know
  what was intended.

### Usage

```bash
pip install -r requirements.txt                  # + rapidocr_onnxruntime opencv-python-headless numpy for the text dimension
export OPENAI_API_KEY=...                        # or OPENAI_API_KEY_FILE=path
# pairs.jsonl: {"pid": "p1", "image_a": "ref.png", "image_b": "cand.png", "query": "a poster for ...", "is_design": true}
python fidelity_judge.py ocr pairs.jsonl ocr.jsonl                                  # OCR lines for the text dimension
python fidelity_judge.py run pairs.jsonl out/ --dims all --ocr ocr.jsonl          # or --dims content,composition
python fidelity_judge.py merge out/ --out scores.jsonl                             # {"pid", "content", ..., "integrity"}
```

- Output: `out/<dim>.jsonl`, one row per pair: `{"pid", "dim", "score": 0-5, "reading" | "reference_reading",
  "issues": [...], "version", "model", "usage"}`. `run` is resumable; failed rows get `"error"` and are retried on the
  next `run`.
- Batch API (half price, up to 24 h):
  1. Run `gate` first if you need layout and the pairs lack `is_design`.
  2. Run `batch-build pairs.jsonl work/ --dims all --ocr ocr.jsonl --gate out/design_gate.jsonl`.
  3. Run `batch-submit work/`, then `batch-poll work/` until the shards are done.
  4. Run `batch-collect work/ out/`.
- Cost: about $3.3 per 400 pairs per dimension at `gpt-6.2-sol` / medium (about 3.5–5k input and 0.1–0.8k output
  tokens per call). The prices assumed are $2 / 1M input, $0.1 / 1M cached input and $10 / 1M output.

### Total (TBD)

How the seven scores combine into one fidelity score is still being designed. This section will define it.
Until then, use the per-dimension scores (`merge`).

### How each dimension was validated

The test material is reconstructions of text-to-image outputs: 100–120 reference images, with 4–5 reconstructions
each, from different models and seeds. "ICC" is ICC(1) over reference images: it shows how much of the score is set
by the reference rather than by the individual reconstruction. "Retest" means the same pairs judged again. Where
people labelled the pairs, AUC is the probability that a pair labelled bad scores lower than a pair labelled good.

- **content** (400 pairs)
  - Scores: mostly 4 (72%), mean 3.7. A score of 3 or less needs a concrete reason, such as a main subject redesigned
    (an aircraft's wing layout changed), a secondary subject missing, or a secondary subject turned into another kind
    of thing.
  - Redrawn faces and patterns count as minor.
  - ICC 0.49. $3.46.
  - Limitation: it separates reconstructions of the same reference only modestly.
- **composition** (400 pairs)
  - Scores: 4 / 3 / 2 = 30% / 32% / 38%, mean 2.91.
  - Pairs that reviewers had flagged for composition score lowest (2.68).
  - Spearman with content is 0.22, so the two dimensions are complementary.
  - ICC 0.56. Two seeds of the same model differ by 0.32 on average. $3.59.
- **layout** (400 pairs, 100 design references)
  - Scores: 5 / 4 / 3 / 2 = 2% / 49% / 19% / 30%, mean 3.22.
  - The score matches the most serious issue in every pair: 4 = minor issues only, 3 = exactly one major issue,
    2 = two or more major issues.
  - ICC 0.59. Two seeds agree exactly 76% of the time. Spearman with composition on shared designs is 0.76. $3.31.
- **text** (457 pairs, 120 references with text: short, medium and long)
  - Within-reference Spearman with an OCR text-similarity score is 0.56 [0.44, 0.66]. The OCR score counts repeats
    and incidental text, which this judge deliberately ignores.
  - Retest: 92% identical, 100% within 1 point. $3.28.
  - Repeated, partly hidden or cropped text and missing incidental text score 5. Garbled or misspelled key text and
    replaced signs score 0–2. A typo in the REFERENCE that the CANDIDATE spells correctly counts as the same.
- **lighting** (328 pairs, 167 labelled by a person as reproduced or not)
  - AUC 0.77. Within the same reference: 0.66.
  - Mean 2.82 for "not reproduced", 3.68 for "reproduced".
  - Losing the signature light (dappled shadows becoming flat light, back light becoming front light) costs 1–2 points.
- **style** (316 pairs, 90 labelled)
  - AUC 0.62. Within the same reference: 0.67.
  - Mean 3.45 for "not reproduced", 3.91 for "reproduced".
  - This is the weakest dimension. Many labelled failures differ in texture or finish, which this definition leaves
    out on purpose.
- **integrity** (400 pairs chosen to be rich in breakage, 34 of them labelled broken)
  - AUC 0.84. At "score ≤ 2 = broken", 79% of the broken pairs are caught and 14% of the other pairs are flagged
    (some of those may be unlabelled breakage).
  - On 45 held-out references (180 pairs, 5 labelled broken), it caught 1 of 5. Recall on unseen cases is the open
    problem.

## Older judges in `sim_judge.py`

- Among these: **rate-v4** (seven single-dimension calls + a fixed total), see [below](#rate-v4-2026-11-25-one-dimension-per-call--a-fixed-total).
  rate-v3 lost to both v2 and v4 on the blind human test and is kept for reference only.
- Prompt **rate-v3** (CLI default), verbatim: [`PROMPT.md`](PROMPT.md) (generated from `sim_judge.py`).
  Seven dimensions, each with a one-sentence justification, plus a holistic `overall`:

  | dimension | what it covers |
  |---|---|
  | `content` | subjects, objects, scene and their key attributes; for charts/UI: chart type, data, modules |
  | `lighting_color` | palette, saturation, color temperature, brightness/contrast, light direction, shadows, highlights |
  | `composition` | subject placement and scale, focal point, viewpoint, framing/crop, balance, depth layering |
  | `layout` | grid, alignment, spacing, whitespace, typographic hierarchy, reading order (`null` if no text blocks/modules) |
  | `style` | medium and rendering style (photo / 3D / flat / painting), art direction and aesthetic |
  | `detail` | textures, local structures, background and decorative details, sharpness — within the same style |
  | `text` | the words (spelling included), their position, size and typeface style (`null` if neither image has text) |

- Built from Qwen-Image-PE's design dimensions (lighting / composition / layout / detail) plus content, style and
  text, written in the style of the PRISM-Bench and VQA judge prompts: start at 10 and deduct within fixed bands,
  justification before score, each difference counted in one dimension only, `null` when not applicable.
- **Use `score` (= `overall`, the model's holistic 0–10 integer) as the similarity score.** The seven dimensions say
  *what* differs; `overall_calc` (plain mean of the non-null dimensions) is still returned for analysis. An earlier
  release blended them (`0.5 · overall + 0.5 · overall_calc`); it ranked the synthetic perturbations a little better
  but agreed with people less on a blind test, so it was dropped (validation, part 5).
- OpenAI Responses API with a strict JSON schema, reasoning effort `low`, images downscaled to ≤ 1024 px long side
  (webp q90, `detail: high`). Default model `gpt-6.1-terra`.
- `--prompt rate-v2` runs the earlier symmetric prompt (5 aspects + overall), byte-identical to its 2026-11-19 run.

## rate-v4 (2026-11-25): one dimension per call + a fixed total

v4 asks seven separate questions, one dimension per call, each answered with a single integer 0–10 (strict JSON
schema, no justification), REFERENCE vs CANDIDATE, then combines them with a formula fixed before any human data was
looked at. Prompts, verbatim: [`PROMPT.md`](PROMPT.md#rate-v4-2026-11-25).

| dimension | what it covers | in the total |
|---|---|---|
| `content` | what is depicted: subjects, key attributes, setting, secondary elements (for charts/UI: chart and module types, data) | main |
| `text` | what the readable text says and how much of it there is (10 if neither image has text) | main |
| `arrangement` | where the main elements sit, how large, viewpoint and crop, grid, alignment, hierarchy, reading order | main |
| `style` | medium and rendering style, art direction, aesthetic, typeface style | main |
| `color` | overall palette, color cast, saturation, overall brightness (an object's own color is content) | secondary |
| `lighting` | light direction and quality, shadows, highlights, light effects | secondary |
| `detail` | detail density, texture, sharpness | secondary |

```
total = main^0.85 × secondary^0.15 − 1
main      = geometric mean of (content, text, arrangement, style), each + 1   → any low main dimension pulls the total down
secondary = arithmetic mean of (color, lighting, detail), each + 1
```

```bash
for d in content text arrangement style color lighting detail; do
  python sim_judge.py run pairs.jsonl out_v4_$d.jsonl --prompt rate-v4-$d          # or batch-build ... --prompt rate-v4-$d
done
python sim_judge.py v4-total out_v4_*.jsonl --out v4_total.jsonl                   # {"pid", "total", <7 dims>}
```

From Python: `sim_judge.v4_total({"content": 8, "text": 10, ...})`.

**Why v4.** On a blind human test (one rater, round 2: a reference and two candidates, "which looks more like the
reference?", 125 items with a clear choice; judges gpt-6.1-terra, effort low) the v4 total agreed with the person
78% of the time, v2 70% and v3 69%; v4 − v2 = +0.076, 95% CI [+0.01, +0.14]. The gain comes from far fewer ties:
v2 ties on half the items (and is right 90% of the time when it does decide), the v4 total separates 98% of them
(79% right). Single v4 dimensions alone are weaker (best: arrangement 0.66, lighting 0.61); the combination is what works.
Caveats: one rater and 125 items; the formula was fixed in advance, not fitted.

**Cost.** Seven calls per pair, each ≈ 2.3–2.6k input tokens (two images ≤ 1024 px + a ~500-token prompt) and
50–80 output tokens. A real run (2026-11-25, gpt-6.1-sol, 4,127 pairs × 7 = 28,889 calls through the Batch API,
0 failures) cost about $86 at listed prices (Batch = half price).

## Run

```bash
pip install -r requirements.txt
export OPENAI_API_KEY=...            # or OPENAI_API_KEY_FILE=path; never commit it
python sim_judge.py run examples/pairs.jsonl out.jsonl                    # --prompt --model --effort --concurrency 16 --dry-run
python tests/test_offline.py                                              # offline checks, no API calls
```

Input, one pair per line: `{"pid", "image_a", "image_b"}` — local paths or http(s) URLs, `pid` unique. With rate-v3,
`image_a` is the REFERENCE and `image_b` the CANDIDATE.

Output (rate-v3), one row per pair:
`{"pid", "score", "content", "lighting_color", "composition", "layout", "style", "detail", "text", "overall", "overall_calc",
"justifications": {dim: str}, "prompt", "model", "usage"}`; a pair that failed after retries gets `"error"` instead.
Re-running with the same output file judges only the pairs that are missing or failed.

Large runs through the Batch API (half price, finishes within 24 h):

```bash
python sim_judge.py batch-build pairs.jsonl work/     # request shards < 180 MB in work/in/  (--prompt as for run)
python sim_judge.py batch-submit work/
python sim_judge.py batch-poll work/                  # repeat until every shard is done; outputs in work/out/
python sim_judge.py batch-collect work/ out.jsonl     # same rows as `run`
```

From Python: `sim_judge.request_body(image_a, image_b, model, effort, prompt="rate-v3")` builds the request body (also
the `body` of a Batch line), `sim_judge.row_from_response(pid, response_json, model, prompt="rate-v3")` reads the answer.

## How rate-v3 was validated (gpt-6.1-terra, 4,536 calls, 2026-11-24, 0 failures)

**1. Does each dimension react only to its own kind of change?** 100 reference images, each paired with a copy changed
in one known way (mean score per dimension; 10 = no difference):

| change | dimension(s) that drop | the rest |
|---|---|---|
| identical copy | none (all 10) | — |
| hue rotated 45° / grayscale / brightness ×0.7 / contrast ×0.6 / warmer white balance | `lighting_color` 4.3 / 3.6 / 7.0 / 7.0 / 8.2 | ≥ 9.8 |
| mirrored | `composition` 3.9, `layout` 4.6, `text` 3.5 (the text is mirrored too) | `content` 10 |
| center crop 60% then upscaled | `composition` 3.5, `layout` 4.1, `text` 3.3, `content` 7.3 (cropped-out elements), `detail` 7.7 | ≥ 8.7 |
| Gaussian blur σ=6 / downscaled 1/8 | `detail` 8.0, `text` 5.0 / 6.6 (text becomes hard to read) | ≥ 9.6 |
| largest text erased | `text` 2.3, `layout` 4.3 | `detail` 7.4, `content` 8.4 |
| an unrelated image | `content` 0.3, others 1.8–4.6 | `overall` 0.4 |

Color and light changes are fully isolated; geometry changes land in composition / layout (and text, when the words
move or mirror); blur is penalized only mildly (−2 in detail).

**2. Do the totals rank real pairs sensibly?** 2,000 real pairs from a text-to-design pipeline, by how the two images
differ. Spearman within each level against the earlier rate-v2 scores of `gpt-6.1-sol` (a reference, not ground truth):

| level | n | mean `overall` | v3 `score` vs v2 sol | v3 `overall` vs v2 sol | v3 `overall_calc` vs v2 sol | v2 terra vs v2 sol |
|---|---|---|---|---|---|---|
| same brief, different seed | 300 | 8.0 | **0.70** | 0.61 | 0.69 | 0.57 |
| different brief, same request | 700 | 5.3 | **0.86** | 0.83 | 0.81 | 0.85 |
| brief from a different writer | 400 | 5.2 | **0.81** | 0.80 | 0.76 | 0.85 |
| brief written from the image | 200 | 5.2 | **0.86** | 0.82 | 0.81 | 0.87 |
| nearest-neighbour request | 250 | 1.4 | 0.88 | 0.90 | 0.73 | 0.92 |
| random pair | 150 | 0.5 | 0.72 | 0.80 | 0.55 | 0.84 |

(The `score` column is the earlier blended total `0.5 · overall + 0.5 · overall_calc`.) Against the reference judge
the blend looked better on the levels where images are close, and it broke ties within the different-brief level
(`overall` 31.8% → 2.1%); on the perturbations, mild-above-strong ordering went 0.944 → 0.970 and identical-copy-above-
perturbed 0.960 → 0.991. Part 5 shows that people disagreed: the current `score` is `overall`.

v3 `overall` agrees with v2 sol about as well as v2 terra did, and better on seed-only differences. Level means fall
monotonically. Among the single dimensions, style / layout / composition track the v2 overall most (ρ ≈ 0.55–0.63 on
the different-brief levels) and text least (0.1–0.36): v2's overall gave little weight to text.

A content cap (`overall_calc` capped at `content` when content ≤ 3) was tried and dropped: content is ≤ 3 for many
different-brief pairs, and the cap lowered agreement there to 0.64–0.72.

**3. Are the dimensions distinct?** On the different-brief levels the highest dimension–dimension Spearman is 0.56
(composition–layout), then content–detail 0.53 and style–detail 0.50; everything else is ≤ 0.36.

**4. Direction and stability.** Swapping REFERENCE and CANDIDATE (300 pairs) changes a dimension by 0.3–0.5 on average
(Spearman 0.78–0.96, `overall` 0.96), about the same as judging the same pair twice (200 pairs: 0.2–0.4, Spearman
0.80–0.97, `overall` 0.97). `detail` is the noisiest dimension (re-test ρ 0.80). `layout` switches between `null` and a
score in 5% of re-tests and 11% of swaps.

**5. Blind human test.** Same request, same model, three renders from different briefs: which candidate looks more
like the reference? 200 items chosen where v2 and v3 pick differently (plus 50 where they agree); one rater, 100
items so far, 84 with a clear pick. Agreement with the person (ties count ½):

| total | items where v2 and v3 disagree (61) | all (84) |
|---|---|---|
| v3 `overall` (current `score`) | 0.57 | 0.63 |
| v3 blend `0.5 · overall + 0.5 · overall_calc` | 0.49 | 0.57 |
| v3 mean / geometric / harmonic mean of the dimensions | 0.46–0.49 | 0.55–0.57 |
| v2 `overall` | 0.59 | 0.64 |

Single dimensions against the person: content and composition ≈ 0.61, style / lighting_color / detail ≈ 0.51, layout
0.48, text 0.46. Small samples (intervals ≈ ±0.08); a second rater is pending.

## Cost

rate-v3: about 3.3k input tokens per call (two images at ≤ 1024 px plus the ~1k-token prompt) and ~250 output tokens
with terra. rate-v2: about 2.2k input and 60–150 output tokens. The Batch API is half the online price.

## Known limits

- Blur and resolution loss cost only ~2 points in `detail`; it is not a pixel-fidelity metric.
- `text` is judged by eye; for exact text correctness use OCR alongside.
- Different models use the scale differently; do not mix models within one comparison.

## rate-v2 results (2026-11-19, 17,812 pairs)

terra and sol ranked pairs nearly the same way once there was a real difference (within-level Spearman ≈ 0.86 on
different-brief pairs; 0.59 on seed-only pairs); luna agreed less (0.75–0.78). terra scored ~0.75 higher than sol on
average. Self-consistency on 350 anchor/B/C triplets asked in both orders: terra 0.92, sol 0.83, luna 0.75.

## Human check (`annotate/`)

A small annotation server for the question the judges are trying to answer: given a REFERENCE and two candidates,
which candidate looks more like the reference? (A / B / about the same / broken image). Raters see only the request
text and the three images; which candidate is which, and any judge scores, stay in the server-side key.

```bash
python annotate/make_task.py items.jsonl task_dir/       # items: {"id", "query"?, "ref", "b", "c", "<judge>": [score_b, score_c], ...}
cp annotate/server.py annotate/page.html task_dir/ && python task_dir/server.py 8771     # http://127.0.0.1:8771/
python annotate/analyze.py task_dir/ v2 v3               # agreement of each judge with the human majority, 95% bootstrap
```

Votes are appended to `task_dir/votes.jsonl` (the last vote of a rater on an item wins). Only items where the judges
disagree can tell them apart, so build the item list with mostly such items.
