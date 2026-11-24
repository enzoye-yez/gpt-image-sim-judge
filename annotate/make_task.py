"""Build a human 2AFC task for the annotation server: "which candidate looks more like the reference?".
Input jsonl, one item per line: {"id", "query"?, "ref", "b", "c", ...any judge scores you want to keep in the key}
  (image paths or URLs are not fetched: local paths only)
Writes <out>/task/items.json (what raters see), <out>/task/key.json (server-side: which side is b / c, your extra fields),
<out>/task/img/t###_{ref,A,B}.webp. A/B sides are randomised per item; items are shuffled.
usage: python make_task.py items.jsonl OUT_DIR    then: cp server.py page.html OUT_DIR/ && python OUT_DIR/server.py 8771
"""
import hashlib, json, random, sys
from pathlib import Path
from PIL import Image
src, out = sys.argv[1], Path(sys.argv[2]); T = out / 'task'; (T / 'img').mkdir(parents=True, exist_ok=True)
rows = [json.loads(l) for l in open(src) if l.strip()]
random.Random(0).shuffle(rows)
items, key = [], {}
for n, r in enumerate(rows):
    iid = f't{n + 1:03d}'; swap = int(hashlib.md5(str(r['id']).encode()).hexdigest(), 16) % 2 == 1
    A, B = ('c', 'b') if swap else ('b', 'c')
    for side, k in (('ref', 'ref'), ('A', A), ('B', B)):
        im = Image.open(r[k]).convert('RGB'); im.thumbnail((900, 900)); im.save(T / 'img' / f'{iid}_{side}.webp', 'WEBP', quality=86)
    items.append({'iid': iid, 'query': r.get('query', '')})
    key[iid] = dict(r, A=A, B=B)
json.dump(items, open(T / 'items.json', 'w'), ensure_ascii=False)
json.dump(key, open(T / 'key.json', 'w'), ensure_ascii=False, indent=1)
print(len(items), 'items ->', T)
