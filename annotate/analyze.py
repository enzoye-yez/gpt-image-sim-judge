"""Score judges against the human votes of a make_task.py task.
Each key item may carry judge scores as "<judge>": [score_b, score_c]; pass the judge field names on the command line.
Human verdict per item = majority over raters (A/B; 'same' or a tie between raters = no verdict; 'bad' dropped).
A judge gets 1 when its higher-scored candidate is the human pick, 0 for the other, 0.5 when it scores both the same.
usage: python analyze.py OUT_DIR judge_field [judge_field ...]"""
import json, random, statistics as st, sys
from collections import Counter, defaultdict
from pathlib import Path
D = Path(sys.argv[1]); judges = sys.argv[2:]
key = json.load(open(D / 'task' / 'key.json')); V = {}
for l in open(D / 'votes.jsonl'):
    v = json.loads(l); V[(v['rater'], v['iid'])] = v
per = defaultdict(list)
for (_, iid), v in V.items():
    if v['choice'] != 'bad': per[iid].append(key[iid][v['choice']] if v['choice'] in ('A', 'B') else 'same')
human = {}
for iid, vs in per.items():
    top = Counter(vs).most_common()
    if top[0][0] != 'same' and (len(top) == 1 or top[0][1] > top[1][1]): human[iid] = top[0][0]
print(f'{len(V)} votes from {len(set(r for r, _ in V))} raters; {len(per)} items voted, {len(human)} with a verdict')
for j in judges:
    xs = []
    for iid, h in human.items():
        sb, sc = key[iid][j]
        xs.append(0.5 if sb == sc else 1.0 if ('b' if sb > sc else 'c') == h else 0.0)
    rng = random.Random(0); bs = sorted(st.mean(rng.choices(xs, k=len(xs))) for _ in range(2000)) if xs else []
    print(f'{j:24s} agreement {st.mean(xs):.3f} (95% {bs[50]:.3f}-{bs[1949]:.3f}, n={len(xs)})' if xs else f'{j}: no items')
