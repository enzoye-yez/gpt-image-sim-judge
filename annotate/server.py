"""Human 2AFC annotation server for the image-similarity judge (11-24).
Raters see the query, a REFERENCE image and two candidates A / B, and answer: which candidate looks more like the reference?
(A / B / same / bad). The key (which render is which, v2 / v3 picks) stays server-side in task/key.json.
  GET  /                      the annotation page
  GET  /api/items             [{iid, query}] in presentation order
  GET  /img/<iid>_<ref|A|B>.webp
  GET  /api/mine?rater=R      that rater's votes
  GET  /api/progress          votes per rater (no key)
  POST /api/vote              {rater, iid, choice: A|B|same|bad, note}
Votes are appended to votes.jsonl (last vote of a rater on an item wins).
usage: server.py [port=8771]
"""
import json, os, re, sys, threading, time
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
H = os.path.dirname(os.path.abspath(__file__)); TD = f'{H}/task'; VF = f'{H}/votes.jsonl'; LOCK = threading.Lock()
IMG_RE = re.compile(r'^/img/(t\d{3}_(?:ref|A|B))\.webp$'); CHOICES = {'A', 'B', 'same', 'bad'}
ITEMS = json.load(open(f'{TD}/items.json')); IIDS = {x['iid'] for x in ITEMS}


def votes():
    out = {}
    if os.path.exists(VF):
        for l in open(VF):
            try: v = json.loads(l)
            except Exception: continue
            out[(v['rater'], v['iid'])] = v
    return out


class Handler(BaseHTTPRequestHandler):
    def send(self, code, body, ctype='application/json; charset=utf-8', cache=False):
        b = body if isinstance(body, bytes) else (body.encode() if isinstance(body, str) else json.dumps(body, ensure_ascii=False).encode())
        self.send_response(code); self.send_header('Content-Type', ctype); self.send_header('Content-Length', str(len(b)))
        self.send_header('Cache-Control', 'public, max-age=86400' if cache else 'no-store'); self.end_headers(); self.wfile.write(b)

    def log_message(self, fmt, *a): sys.stderr.write('%s %s\n' % (time.strftime('%H:%M:%S'), fmt % a))

    def do_GET(self):
        u = urlparse(self.path); q = parse_qs(u.query)
        if u.path in ('/', '/index.html'): return self.send(200, open(f'{H}/page.html', 'rb').read(), 'text/html; charset=utf-8')
        m = IMG_RE.match(u.path)
        if m and os.path.exists(f'{TD}/img/{m[1]}.webp'): return self.send(200, open(f'{TD}/img/{m[1]}.webp', 'rb').read(), 'image/webp', cache=True)
        if u.path == '/api/items': return self.send(200, ITEMS)
        if u.path == '/api/mine':
            r = (q.get('rater') or [''])[0].strip()[:40]
            return self.send(200, {iid: {'choice': d['choice'], 'note': d.get('note', '')} for (x, iid), d in votes().items() if x == r})
        if u.path == '/api/progress':
            c = {}
            for (r, _), _v in votes().items(): c[r] = c.get(r, 0) + 1
            return self.send(200, {'items': len(ITEMS), 'raters': c})
        self.send(404, {'error': 'not found'})

    def do_POST(self):
        if urlparse(self.path).path != '/api/vote': return self.send(404, {'error': 'not found'})
        try:
            d = json.loads(self.rfile.read(min(int(self.headers.get('Content-Length', 0)), 10000)))
            r, iid, c = str(d['rater']).strip()[:40], d['iid'], d['choice']
            assert r and iid in IIDS and c in CHOICES
        except Exception:
            return self.send(400, {'error': 'bad vote'})
        rec = {'rater': r, 'iid': iid, 'choice': c, 'note': str(d.get('note', ''))[:500], 't': time.time()}
        with LOCK, open(VF, 'a') as f: f.write(json.dumps(rec, ensure_ascii=False) + '\n')
        self.send(200, {'ok': True})


if __name__ == '__main__':
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8771
    print('serving on', port, flush=True); ThreadingHTTPServer(('127.0.0.1', port), Handler).serve_forever()
