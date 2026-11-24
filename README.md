# GPT image-similarity judge

One file, one job: given a REFERENCE image and a CANDIDATE image, ask a GPT model how closely the candidate reproduces
the reference's design — the way a designer checks a render against an approved reference — and get 0–10 scores back.

- Prompt **rate-v3** (default), verbatim: [`PROMPT.md`](PROMPT.md) (generated from `sim_judge.py`).
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
- **Use `overall` as the similarity score.** `overall_calc` (plain mean of the non-null dimensions) is a secondary
  summary; the seven dimensions are there to say *what* differs.
- OpenAI Responses API with a strict JSON schema, reasoning effort `low`, images downscaled to ≤ 1024 px long side
  (webp q90, `detail: high`). Default model `gpt-6.1-terra`.
- `--prompt rate-v2` runs the earlier symmetric prompt (5 aspects + overall), byte-identical to its 2026-11-19 run.

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
`{"pid", "content", "lighting_color", "composition", "layout", "style", "detail", "text", "overall", "overall_calc",
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

| level | n | mean `overall` | v3 `overall` vs v2 sol | v3 `overall_calc` vs v2 sol | v2 terra vs v2 sol |
|---|---|---|---|---|---|
| same brief, different seed | 300 | 8.0 | 0.61 | 0.69 | 0.57 |
| different brief, same request | 700 | 5.3 | 0.83 | 0.81 | 0.85 |
| brief from a different writer | 400 | 5.2 | 0.80 | 0.76 | 0.85 |
| brief written from the image | 200 | 5.2 | 0.82 | 0.81 | 0.87 |
| nearest-neighbour request | 250 | 1.4 | 0.90 | 0.73 | 0.92 |
| random pair | 150 | 0.5 | 0.80 | 0.55 | 0.84 |

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
