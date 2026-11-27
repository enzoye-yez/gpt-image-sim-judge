"""Reconstruction-fidelity judge: seven single-dimension GPT judges, REFERENCE vs CANDIDATE, each an integer 0-5.

The CANDIDATE is an image that tries to reproduce the REFERENCE (for example: an image regenerated from a description of
the REFERENCE). Each dimension is one call with its own prompt and strict JSON schema; the judge first reads the REFERENCE,
lists the differences it finds (where the schema has an issue list), then picks the score.

  content      what is depicted: subjects, their identity and appearance             inputs: REFERENCE, CANDIDATE
  composition  where things are, how large, framing, viewpoint, subject structure    inputs: REFERENCE, CANDIDATE
  layout       graphic designs only: blocks, grid, alignment, reading order         inputs: REFERENCE, CANDIDATE (+ design gate)
  text         what the readable text says                                          inputs: QUERY, REFERENCE, CANDIDATE, OCR lines
  lighting     key light, light effects, tone and light color, plausibility         inputs: QUERY, REFERENCE, CANDIDATE
  style        medium, style words, mood                                            inputs: QUERY, REFERENCE, CANDIDATE
  integrity    whether the CANDIDATE is drawn broken (anatomy, clipping, ...)       inputs: QUERY, REFERENCE, CANDIDATE

Prompts sent to the model: fidelity/prompts/<dim>.md (Chinese reading copies: <dim>.zh.md); schemas: fidelity/schemas.json.
Defaults: gpt-6.2-sol, reasoning effort medium, images downscaled to a 1024 px long side (WebP q90, detail high).

Input pairs (JSONL), one per line:
    {"pid": str, "image_a": REFERENCE path-or-URL, "image_b": CANDIDATE path-or-URL,
     "query": str            # the generation request; required for text / lighting / style / integrity
     "is_design": bool}      # optional; layout only runs on designs. Missing -> the design gate decides (one cheap call per REFERENCE)

Online (concurrent, resumable; one output file per dimension under OUTDIR):
    python fidelity_judge.py ocr pairs.jsonl ocr.jsonl                   # RapidOCR lines for every image (text dimension)
    python fidelity_judge.py run pairs.jsonl OUTDIR --dims all --ocr ocr.jsonl [--concurrency 8]
    python fidelity_judge.py run pairs.jsonl OUTDIR --dims content,composition
    python fidelity_judge.py merge OUTDIR --out scores.jsonl             # one row per pid: {pid, content, ..., integrity}
Batch API (half price, up to 24 h):
    python fidelity_judge.py gate pairs.jsonl OUTDIR                     # only if layout is wanted and pairs lack is_design
    python fidelity_judge.py batch-build pairs.jsonl WORK --dims all --ocr ocr.jsonl [--gate OUTDIR/design_gate.jsonl]
    python fidelity_judge.py batch-submit WORK; python fidelity_judge.py batch-poll WORK
    python fidelity_judge.py batch-collect WORK OUTDIR

Output rows (OUTDIR/<dim>.jsonl): {"pid", "dim", "score": 0-5, "reading" or "reference_reading", "issues" (most dims),
    "version", "model", "usage"}. A dimension that does not apply gets {"pid", "dim", "score": null, "skipped": reason}
    (layout on a non-design; text when OCR finds fewer than 3 characters in both images). Failures get "error"; re-run `run`
    to retry them (pids that are already ok are skipped).
The total score over the seven dimensions is not defined yet (see README).
The API key comes from $OPENAI_API_KEY (or the file in $OPENAI_API_KEY_FILE); it is never printed or written.
"""
import argparse, concurrent.futures as cf, json, sys, threading, time
from pathlib import Path

import httpx

import sim_judge as s

HERE = Path(__file__).resolve().parent / 'fidelity'
DIMS = ('content', 'composition', 'layout', 'text', 'lighting', 'style', 'integrity')
VERSION = {'content': 'content-c05-r4', 'composition': 'composition-c05-r2', 'layout': 'layout-l05-r3', 'text': 'text-v8',
           'lighting': 'lighting-v5', 'style': 'style-v5', 'integrity': 'integrity-v11'}
NEEDS_QUERY = {'text', 'lighting', 'style', 'integrity'}
MODEL, EFFORT = 'gpt-6.2-sol', 'medium'
GATE_MODEL, GATE_EFFORT = 'gpt-6-luna', 'low'
MAX_OUTPUT_TOKENS = 16000
MAX_OCR_LINES = 250          # per image, top to bottom
MIN_TEXT_CHARS = 3           # text is judged when OCR reads at least this many characters in either image

PROMPTS = {d: (HERE / 'prompts' / f'{d}.md').read_text() for d in DIMS}
GATE_PROMPT = (HERE / 'prompts' / 'design_gate.md').read_text()
SCHEMAS = json.loads((HERE / 'schemas.json').read_text())


# ---------------------------------------------------------------- requests

def _img(x, detail='high'):
    return {'type': 'input_image', 'image_url': s.encode_image(x), 'detail': detail}


def ocr_block(lines):
    """OCR lines -> the text block shown to the text judge: '- (x%, y%) text', sorted top to bottom."""
    if lines is None: return '(no OCR reading available)'
    if not lines: return '(OCR read no text)'
    lines = sorted(lines, key=lambda x: ((x['box'][1] + x['box'][3]) / 2, x['box'][0]))[:MAX_OCR_LINES]
    return '\n'.join(f"- ({100 * (l['box'][0] + l['box'][2]) / 2:.0f}%, {100 * (l['box'][1] + l['box'][3]) / 2:.0f}%) {l['text']}" for l in lines)


def request_body(dim, pair, model=MODEL, effort=EFFORT, ocr=None):
    """Responses API body for one dimension of one pair. ocr: {image path: [lines]} (text only)."""
    if dim not in DIMS: raise ValueError(f'unknown dimension {dim!r}; choose from {DIMS}')
    content = []
    if dim in NEEDS_QUERY:
        if not pair.get('query'): raise ValueError(f'{dim} needs "query" in the pair ({pair["pid"]})')
        content.append({'type': 'input_text', 'text': f"QUERY: {pair['query']}"})
    content += [{'type': 'input_text', 'text': 'REFERENCE:'}, _img(pair['image_a']),
                {'type': 'input_text', 'text': 'CANDIDATE:'}, _img(pair['image_b'])]
    if dim == 'text':
        ocr = ocr or {}
        content += [{'type': 'input_text', 'text': f"OCR of the REFERENCE:\n{ocr_block(ocr.get(pair['image_a']))}"},
                    {'type': 'input_text', 'text': f"OCR of the CANDIDATE:\n{ocr_block(ocr.get(pair['image_b']))}"}]
    return {'model': model, 'reasoning': {'effort': effort}, 'max_output_tokens': MAX_OUTPUT_TOKENS, 'store': False,
            'text': {'format': SCHEMAS[dim]},
            'input': [{'role': 'developer', 'content': PROMPTS[dim]}, {'role': 'user', 'content': content}]}


def gate_body(image, model=GATE_MODEL, effort=GATE_EFFORT):
    return {'model': model, 'reasoning': {'effort': effort}, 'max_output_tokens': 4000, 'store': False,
            'text': {'format': SCHEMAS['design_gate']},
            'input': [{'role': 'developer', 'content': GATE_PROMPT}, {'role': 'user', 'content': [_img(image, 'low')]}]}


def row_from_response(dim, pid, body, model=MODEL):
    v = s._json(s.output_text(body))
    if v.get('score') not in (0, 1, 2, 3, 4, 5): raise ValueError(f"score {v.get('score')!r} not on the 0-5 scale")
    return dict(pid=pid, dim=dim, **v, version=VERSION[dim], model=body.get('model', model), usage=body.get('usage'))


# ---------------------------------------------------------------- applicability

def _chars(lines):
    return sum(len(''.join(l['text'].split())) for l in lines or [])


def skip_reason(dim, pair, ocr=None, gate=None):
    """None if the dimension applies to this pair, else a short reason."""
    if dim == 'layout':
        d = pair.get('is_design')
        if d is None and gate is not None: d = gate.get(pair['image_a'])
        if d is None: raise ValueError(f'layout needs "is_design" or a design-gate result for {pair["pid"]}')
        return None if d else 'not a design'
    if dim == 'text' and ocr is not None and pair['image_a'] in ocr and pair['image_b'] in ocr:
        if max(_chars(ocr[pair['image_a']]), _chars(ocr[pair['image_b']])) < MIN_TEXT_CHARS: return 'no text'
    return None


def load_ocr(paths):
    out = {}
    for p in paths or []:
        for line in filter(str.strip, open(p)):
            r = json.loads(line); out[r['path']] = r['lines']
    return out


def load_gate(path):
    if not path or not Path(path).exists(): return {}
    return {r['image']: r['is_design'] for r in map(json.loads, filter(str.strip, open(path))) if 'error' not in r}


def parse_dims(x):
    dims = DIMS if x == 'all' else tuple(d.strip() for d in x.split(','))
    bad = [d for d in dims if d not in DIMS]
    if bad: sys.exit(f'unknown dimension(s) {bad}; choose from {DIMS} or "all"')
    return dims


def read_pairs(path):
    return s.read_pairs(path)


# ---------------------------------------------------------------- online

def _post(cl, body, retries=5):
    err = None
    for attempt in range(retries):
        try:
            r = cl.post(f'{s.API}/responses', json=body)
            if r.status_code in (429, 500, 502, 503, 504): raise RuntimeError(f'HTTP {r.status_code}')
            r.raise_for_status(); return r.json(), None
        except (httpx.HTTPError, RuntimeError) as e:
            err = f'{type(e).__name__}: {e}'[:300]; time.sleep(min(60, 2 ** attempt * 2))
    return None, err


def judge_one(cl, dim, pair, model, effort, ocr, retries=5):
    err = None
    for _ in range(2):   # a reply that does not parse is asked again once
        body, err = _post(cl, request_body(dim, pair, model, effort, ocr), retries)
        if body is None: break
        try:
            return row_from_response(dim, pair['pid'], body, model)
        except (ValueError, KeyError, json.JSONDecodeError) as e:
            err = f'{type(e).__name__}: {e}'[:300]
    return dict(pid=pair['pid'], dim=dim, error=err, version=VERSION[dim], model=model)


def run_gate(pairs, outdir, concurrency=8):
    """Design gate for every REFERENCE that has no is_design; cached in OUTDIR/design_gate.jsonl."""
    gp = Path(outdir) / 'design_gate.jsonl'; have = load_gate(gp)
    todo = sorted({p['image_a'] for p in pairs if p.get('is_design') is None} - set(have))
    if todo:
        print(f'design gate: {len(todo)} reference images ({GATE_MODEL})', file=sys.stderr)
        def one(img):
            body, err = _post(cl, gate_body(img))
            if body is None: return dict(image=img, error=err)
            try:
                v = s._json(s.output_text(body)); return dict(image=img, **v, model=body.get('model'), usage=body.get('usage'))
            except (ValueError, KeyError, json.JSONDecodeError) as e:
                return dict(image=img, error=f'{type(e).__name__}: {e}'[:300])
        with s.client() as cl, open(gp, 'a') as f, cf.ThreadPoolExecutor(concurrency) as ex:
            for r in ex.map(one, todo): f.write(json.dumps(r, ensure_ascii=False) + '\n'); f.flush()
    return load_gate(gp)


def _done(path):
    if not path.exists(): return set()
    return {r['pid'] for r in map(json.loads, filter(str.strip, open(path))) if 'error' not in r}


def cmd_run(a):
    pairs = read_pairs(a.pairs); dims = parse_dims(a.dims); out = Path(a.outdir); out.mkdir(parents=True, exist_ok=True)
    ocr = load_ocr(a.ocr) if a.ocr else None
    if 'text' in dims and ocr is None: print('warning: text without --ocr; the judge gets no OCR lines', file=sys.stderr)
    gate = run_gate(pairs, out, a.concurrency) if 'layout' in dims and not a.dry_run else load_gate(out / 'design_gate.jsonl')
    tasks, skipped = [], {}
    for d in dims:
        done = _done(out / f'{d}.jsonl')
        for p in pairs:
            if p['pid'] in done: continue
            if d == 'layout' and p.get('is_design') is None and p['image_a'] not in gate:
                tasks.append((d, p, 'error: no design-gate result')); continue
            why = skip_reason(d, p, ocr, gate)
            tasks.append((d, p, why)); skipped[d] = skipped.get(d, 0) + (why is not None)
    n_call = sum(1 for _, _, w in tasks if w is None)
    print(f'{len(pairs)} pairs x {len(dims)} dims: {n_call} calls, {len(tasks) - n_call} not applicable, model {a.model} ({a.effort})', file=sys.stderr)
    if a.dry_run:
        first = next(((d, p) for d, p, w in tasks if w is None), None)
        if first: print(f'dry run: first request body ({first[0]}) is {len(json.dumps(request_body(first[0], first[1], a.model, a.effort, ocr))) / 1024:.0f} KB; no API calls made', file=sys.stderr)
        return
    files = {d: open(out / f'{d}.jsonl', 'a') for d in dims}; lock = threading.Lock(); n_ok = n_err = 0

    def work(t):
        d, p, why = t
        if why is None: return judge_one(cl, d, p, a.model, a.effort, ocr)
        if why.startswith('error'): return dict(pid=p['pid'], dim=d, error=why[7:], version=VERSION[d])
        return dict(pid=p['pid'], dim=d, score=None, skipped=why, version=VERSION[d])
    with s.client() as cl, cf.ThreadPoolExecutor(a.concurrency) as ex:
        for row in ex.map(work, tasks):
            with lock:
                files[row['dim']].write(json.dumps(row, ensure_ascii=False) + '\n'); files[row['dim']].flush()
                n_err += 'error' in row; n_ok += 'error' not in row
                if (n_ok + n_err) % 50 == 0: print(f'  {n_ok + n_err}/{len(tasks)} (errors {n_err})', file=sys.stderr)
    for f in files.values(): f.close()
    print(f'done: {n_ok} ok, {n_err} errors -> {out}/<dim>.jsonl' + (' (re-run to retry errors)' if n_err else ''), file=sys.stderr)


def cmd_gate(a):
    g = run_gate(read_pairs(a.pairs), Path(a.outdir), a.concurrency)
    print(f'{sum(g.values())} of {len(g)} reference images are designs -> {a.outdir}/design_gate.jsonl', file=sys.stderr)


def cmd_ocr(a):
    """RapidOCR lines on every image of the pairs (native resolution) -> {path, lines: [{text, score, box (0-1)}]}."""
    try:
        import numpy as np
        from PIL import Image
        from rapidocr_onnxruntime import RapidOCR
    except ImportError:
        sys.exit('pip install rapidocr_onnxruntime opencv-python-headless numpy (only needed for the text dimension)')
    pairs = read_pairs(a.pairs); out = Path(a.out)
    done = {json.loads(l)['path'] for l in filter(str.strip, open(out))} if out.exists() else set()
    paths = sorted({p[k] for p in pairs for k in ('image_a', 'image_b')} - done)
    eng = RapidOCR()
    with open(out, 'a') as f:
        for i, path in enumerate(paths):
            im = Image.open(path).convert('RGB'); w, h = im.size
            res, _ = eng(np.asarray(im)[:, :, ::-1].copy()); lines = []
            for quad, txt, sc in res or []:
                if sc < a.min_score or not txt.strip(): continue
                xs, ys = [q[0] for q in quad], [q[1] for q in quad]
                lines.append(dict(text=txt, score=float(sc), box=[min(xs) / w, min(ys) / h, max(xs) / w, max(ys) / h]))
            f.write(json.dumps(dict(path=path, lines=lines), ensure_ascii=False) + '\n')
            if i % 100 == 0: f.flush(); print(f'  {i}/{len(paths)}', file=sys.stderr)
    print(f'{len(paths)} images -> {out}', file=sys.stderr)


def cmd_merge(a):
    by = {}
    for d in DIMS:
        fp = Path(a.outdir) / f'{d}.jsonl'
        if not fp.exists(): continue
        for r in map(json.loads, filter(str.strip, open(fp))):
            if 'error' not in r: by.setdefault(r['pid'], {})[d] = r['score']
    with open(a.out, 'w') as f:
        for pid, sc in by.items(): f.write(json.dumps({'pid': pid, **{d: sc.get(d) for d in DIMS}}) + '\n')
    print(f'{len(by)} pairs -> {a.out} (null = not applicable or not run)', file=sys.stderr)


# ---------------------------------------------------------------- batch

def cmd_batch_build(a):
    """One shard set for all requested dimensions; custom_id = '<dim>|<pid>'. Rows that do not apply are written at collect."""
    work = Path(a.work); (work / 'in').mkdir(parents=True, exist_ok=True)
    sp, st = s._state(work)
    if st: sys.exit(f'{sp} exists; use a fresh work dir')
    pairs = read_pairs(a.pairs); dims = parse_dims(a.dims); ocr = load_ocr(a.ocr) if a.ocr else None
    gate = load_gate(a.gate); skipped = []
    buf, size, shard, n = [], 0, 0, 0
    def flush():
        nonlocal buf, size, shard
        if not buf: return
        name = f'shard-{shard:03d}'; (work / 'in' / f'{name}.jsonl').write_text(''.join(buf))
        st[name] = dict(n=len(buf), bytes=size, status='built'); shard += 1; buf, size = [], 0
    for d in dims:
        for p in pairs:
            why = skip_reason(d, p, ocr, gate)
            if why: skipped.append(dict(pid=p['pid'], dim=d, score=None, skipped=why, version=VERSION[d])); continue
            line = json.dumps({'custom_id': f"{d}|{p['pid']}", 'method': 'POST', 'url': '/v1/responses',
                               'body': request_body(d, p, a.model, a.effort, ocr)}) + '\n'
            if size + len(line) > s.MAX_SHARD_BYTES: flush()
            buf.append(line); size += len(line); n += 1
    flush(); sp.write_text(json.dumps(st, indent=1))
    (work / 'meta.json').write_text(json.dumps({'dims': dims, 'model': a.model, 'effort': a.effort}))
    (work / 'skipped.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in skipped))
    print(f'{n} requests in {len(st)} shards ({len(skipped)} not applicable); next: batch-submit {work}', file=sys.stderr)


def cmd_batch_collect(a):
    work, out = Path(a.work), Path(a.outdir); out.mkdir(parents=True, exist_ok=True)
    meta = json.loads((work / 'meta.json').read_text()); rows = {d: [] for d in meta['dims']}; n_ok = n_err = 0
    for fp in sorted((work / 'out').glob('*.jsonl')):
        for line in filter(str.strip, open(fp)):
            r = json.loads(line); d, pid = r['custom_id'].split('|', 1); resp = r.get('response') or {}
            try:
                if r.get('error') or resp.get('status_code') != 200: raise ValueError(str(r.get('error') or resp.get('status_code')))
                row = row_from_response(d, pid, resp['body'], meta['model']); n_ok += 1
            except (ValueError, KeyError, json.JSONDecodeError) as e:
                row = dict(pid=pid, dim=d, error=f'{type(e).__name__}: {e}'[:300], version=VERSION[d]); n_err += 1
            rows[d].append(row)
    sk = work / 'skipped.jsonl'
    for r in map(json.loads, filter(str.strip, open(sk))) if sk.exists() else []: rows[r['dim']].append(r)
    for d, rs in rows.items():
        with open(out / f'{d}.jsonl', 'a') as f: f.writelines(json.dumps(r, ensure_ascii=False) + '\n' for r in rs)
    print(f'{n_ok} ok, {n_err} errors -> {out}/<dim>.jsonl (judge the errors again with `run`, it skips pids that are already ok)', file=sys.stderr)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    def common(p):
        p.add_argument('--dims', default='all', help='"all" or a comma list of ' + ','.join(DIMS))
        p.add_argument('--model', default=MODEL); p.add_argument('--effort', default=EFFORT)
        p.add_argument('--ocr', nargs='*', help='OCR JSONL file(s) from the `ocr` command (text dimension)')
    p = sub.add_parser('run'); p.add_argument('pairs'); p.add_argument('outdir'); common(p)
    p.add_argument('--concurrency', type=int, default=8); p.add_argument('--dry-run', action='store_true'); p.set_defaults(f=cmd_run)
    p = sub.add_parser('gate'); p.add_argument('pairs'); p.add_argument('outdir'); p.add_argument('--concurrency', type=int, default=8); p.set_defaults(f=cmd_gate)
    p = sub.add_parser('ocr'); p.add_argument('pairs'); p.add_argument('out'); p.add_argument('--min-score', type=float, default=0.5); p.set_defaults(f=cmd_ocr)
    p = sub.add_parser('merge'); p.add_argument('outdir'); p.add_argument('--out', required=True); p.set_defaults(f=cmd_merge)
    p = sub.add_parser('batch-build'); p.add_argument('pairs'); p.add_argument('work'); common(p)
    p.add_argument('--gate', help='design_gate.jsonl from the `gate` command (layout, when pairs lack is_design)'); p.set_defaults(f=cmd_batch_build)
    for name, fn in (('batch-submit', s.cmd_batch_submit), ('batch-poll', s.cmd_batch_poll)):
        p = sub.add_parser(name); p.add_argument('work'); p.set_defaults(f=fn)
    p = sub.add_parser('batch-collect'); p.add_argument('work'); p.add_argument('outdir'); p.set_defaults(f=cmd_batch_collect)
    a = ap.parse_args(); a.f(a)


if __name__ == '__main__':
    main()
