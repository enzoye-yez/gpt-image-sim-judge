"""Offline checks (no API calls): request shape, parsing, and the batch build/collect round trip.
python -m pytest tests/   or   python tests/test_offline.py"""
import json, sys, tempfile, types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT))
import sim_judge as s

A, B = str(ROOT / 'examples/images/a.png'), str(ROOT / 'examples/images/c_other.png')


def fake_response(text, model='gpt-6.1-terra'):
    return {'model': model, 'usage': {'input_tokens': 2100, 'output_tokens': 60},
            'output': [{'type': 'reasoning'}, {'type': 'message', 'content': [{'type': 'output_text', 'text': text}]}]}


def test_request_body():
    b = s.request_body(A, B, prompt='rate-v2')
    assert b['input'][0] == {'role': 'developer', 'content': s.PROMPT}
    user = b['input'][1]['content']
    assert [c['type'] for c in user] == ['input_text', 'input_image', 'input_text', 'input_image']
    assert user[1]['image_url'].startswith('data:image/webp;base64,') and user[1]['detail'] == 'high'
    assert b['reasoning'] == {'effort': 'low'} and b['store'] is False


def test_parse():
    r = s.row_from_response('p', fake_response('```json\n{"content": 7, "layout": 8, "color": 6, "style": 7, "text": null, '
                                                '"overall": 7, "reason": "same layout"}\n```'), 'm')
    assert r['overall'] == 7.0 and r['text'] is None and r['reason'] == 'same layout'
    for bad in ('{"overall": 11, "content": 1, "layout": 1, "color": 1, "style": 1, "text": 1}', 'no json here'):
        try: s.parse_scores(bad); raise AssertionError('should fail')
        except (ValueError, json.JSONDecodeError): pass


def test_batch_roundtrip():
    with tempfile.TemporaryDirectory() as d:
        d = Path(d); pairs = d / 'pairs.jsonl'
        pairs.write_text(''.join(json.dumps({'pid': f'p{i}', 'image_a': A, 'image_b': B}) + '\n' for i in range(3)))
        s.cmd_batch_build(types.SimpleNamespace(pairs=str(pairs), work=str(d / 'w'), model='gpt-6.1-terra', effort='low', prompt='rate-v2'))
        lines = [json.loads(l) for l in open(d / 'w/in/shard-000.jsonl')]
        assert [l['custom_id'] for l in lines] == ['p0', 'p1', 'p2']
        (d / 'w/out').mkdir()
        txt = '{"content": 2, "layout": 3, "color": 1, "style": 4, "text": 2, "overall": 2, "reason": "different"}'
        out = [{'custom_id': 'p0', 'response': {'status_code': 200, 'body': fake_response(txt)}},
               {'custom_id': 'p1', 'response': {'status_code': 200, 'body': fake_response('oops')}},
               {'custom_id': 'p2', 'error': {'code': 'x'}, 'response': None}]
        (d / 'w/out/shard-000.jsonl').write_text(''.join(json.dumps(o) + '\n' for o in out))
        s.cmd_batch_collect(types.SimpleNamespace(work=str(d / 'w'), out=str(d / 'out.jsonl')))
        rows = [json.loads(l) for l in open(d / 'out.jsonl')]
        assert rows[0]['overall'] == 2.0 and 'error' in rows[1] and 'error' in rows[2]
        assert s.done_pids(d / 'out.jsonl') == {'p0'}



V3 = lambda **kw: json.dumps({d: {'justification': 'x', 'score': kw.get(d, 8)} for d in s.DIMS_V3 + ('overall',)})


def test_request_body_v3():
    b = s.request_body(A, B, prompt='rate-v3')
    assert b['input'][0]['content'] == s.PROMPT_V3 and b['text']['format']['strict'] is True
    user = b['input'][1]['content']
    assert user[0]['text'] == 'REFERENCE:' and user[2]['text'] == 'CANDIDATE:'
    sch = b['text']['format']['schema']
    assert sch['required'] == list(s.DIMS_V3) + ['overall']
    assert [d for d in s.DIMS_V3 if 'null' in sch['properties'][d]['properties']['score']['type']] == ['layout', 'text']


def test_parse_v3():
    r = s.row_from_response('p', fake_response(V3(layout=None, text=None)), 'm', 'rate-v3')
    assert r['layout'] is None and r['text'] is None and r['overall_calc'] == 8.0 and r['prompt'] == 'rate-v3'
    assert r['score'] == 8.0
    r = s.parse_scores_v3(V3(overall=6, content=9, lighting_color=9, composition=9, layout=None, style=9, detail=9, text=None))
    assert r['overall_calc'] == 9.0 and r['score'] == 7.5            # 0.5 * overall + 0.5 * mean of dims
    r = s.parse_scores_v3(V3(content=2, lighting_color=9, composition=9, layout=9, style=9, detail=9, text=9))
    assert r['overall_calc'] == round((2 + 9 * 6) / 7, 3)   # plain mean, no content cap
    r = s.parse_scores_v3(V3(content=4, lighting_color=10, composition=10, layout=None, style=10, detail=10, text=None))
    assert r['overall_calc'] == 8.8
    for bad in (V3(content=None), V3(overall=12), '{}'):
        try: s.parse_scores_v3(bad); raise AssertionError('should fail')
        except (ValueError, json.JSONDecodeError): pass


def test_batch_v3_collect_uses_build_prompt():
    with tempfile.TemporaryDirectory() as d:
        d = Path(d); pairs = d / 'pairs.jsonl'
        pairs.write_text(json.dumps({'pid': 'p0', 'image_a': A, 'image_b': B}) + '\n')
        s.cmd_batch_build(types.SimpleNamespace(pairs=str(pairs), work=str(d / 'w'), model='gpt-6.1-terra', effort='low', prompt='rate-v3'))
        (d / 'w/out').mkdir()
        (d / 'w/out/shard-000.jsonl').write_text(json.dumps({'custom_id': 'p0', 'response': {'status_code': 200, 'body': fake_response(V3())}}) + '\n')
        s.cmd_batch_collect(types.SimpleNamespace(work=str(d / 'w'), out=str(d / 'out.jsonl')))
        row = json.loads(open(d / 'out.jsonl').readline())
        assert row['prompt'] == 'rate-v3' and row['overall_calc'] == 8.0


if __name__ == '__main__':
    for name, fn in list(globals().items()):
        if name.startswith('test_'): fn(); print('ok', name)
