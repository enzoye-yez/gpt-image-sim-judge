# GPT image-similarity judge (prompt rate-v2)

One file, one job: given two images, ask a GPT model how similar they look **to a person** — the way a designer
judges whether two renders are "the same design" — and get 0–10 scores back.

- Prompt **rate-v2**, verbatim as run: [`PROMPT.md`](PROMPT.md) (generated from `sim_judge.py`).
- OpenAI Responses API, reasoning effort `low`, images downscaled to ≤ 1024 px long side (webp q90, `detail: high`).
- Output: `overall` plus five aspects `content`, `layout`, `color`, `style`, `text` (`text` is `null` when neither
  image has readable text), and a one-sentence `reason`. In our analyses only `overall` was used as the similarity score.
- Default model `gpt-6.1-terra`; `--model gpt-6.1-sol` / `gpt-6-luna` send exactly the same request with a different model.
- The prompt is symmetric (Image 1 / Image 2, no "reference"); it was not asked in both orders.

## Run

```bash
pip install -r requirements.txt
export OPENAI_API_KEY=...            # or OPENAI_API_KEY_FILE=path; never commit it
python sim_judge.py run examples/pairs.jsonl out.jsonl                    # --model --effort --concurrency 16 --dry-run
python tests/test_offline.py                                              # offline checks, no API calls
```

Input, one pair per line: `{"pid", "image_a", "image_b"}` — local paths or http(s) URLs, `pid` unique.
Output, one row per pair: `{"pid", "overall", "content", "layout", "color", "style", "text", "reason", "model", "usage"}`;
a pair that failed after retries gets `"error"` instead. Re-running with the same output file judges only the pairs that
are missing or failed.

Large runs through the Batch API (half price, finishes within 24 h):

```bash
python sim_judge.py batch-build pairs.jsonl work/     # request shards < 180 MB in work/in/
python sim_judge.py batch-submit work/
python sim_judge.py batch-poll work/                  # repeat until every shard is done; outputs in work/out/
python sim_judge.py batch-collect work/ out.jsonl     # same rows as `run`
```

From Python: `sim_judge.request_body(image_a, image_b, model, effort)` builds the request body (also the `body` of a
Batch line), `sim_judge.row_from_response(pid, response_json, model)` reads the answer.

## How it behaved (17,812 pairs, 2026-11-19)

Pairs of images rendered by a text-to-design pipeline, grouped by how the two images differ:

| level | what differs | n | mean `overall` (terra / sol / luna) | Spearman sol vs terra | sol vs luna |
|---|---|---|---|---|---|
| L1 | same brief, different image seed | 1,500 | 9.12 / 8.98 / 8.64 | 0.59 | 0.54 |
| L2 | different design brief for the same request | 12,072 | 6.88 / 6.13 / 5.77 | 0.86 | 0.78 |
| L3 | brief written by a different writer model | 2,792 | 6.76 / 5.98 / 5.62 | 0.86 | 0.75 |

- terra and sol rank pairs nearly the same way once there is a real difference (L2/L3, ρ ≈ 0.86); luna agrees less.
  Seed-only differences (L1) are near the top of the scale for every model and are ranked much less consistently.
- Aspect agreement sol vs terra (Spearman over all pairs): overall 0.91, layout 0.95, text 0.95, content 0.91,
  color 0.91, style 0.86.
- Self-consistency on 350 anchor/B/C triplets asked in both orders (a separate triplet prompt, not in this repo):
  terra 0.92, sol 0.83, luna 0.75.
- Different models use the scale differently (terra scores ~0.75 higher than sol on L2/L3), so do not mix models within
  one comparison.

## Cost

About 2.2k input tokens per call (two images at ≤ 1024 px plus the ~350-token prompt) and 60–150 output tokens
(terra/sol ≈ 65, luna ≈ 150). Batch API is half the online price.

## Known limits / what to refine

- `overall` is free-form; nothing ties it to the aspect scores.
- The prompt treats both images as equals; for "reference vs. render" use it may help to say which is the reference.
- Very small differences (fine texture, slight shifts) are deliberately downweighted, so it is not a pixel-fidelity metric.
- Text is judged by eye; for exact text correctness use OCR alongside.
