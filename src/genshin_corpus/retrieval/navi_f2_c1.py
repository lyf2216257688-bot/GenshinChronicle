"""Offline C1 extension: archived Responses events and empirical token proxies.

No provider client, credentials, network, retry or execution authority.
The frozen Preflight adapter remains unchanged.
"""
import ast
from hashlib import sha256
import json
from math import ceil
from pathlib import Path

from . import navi_f2_preflight as pre

MODEL = 'deepseek-v4.1-flash'
BASE_URL = 'https://jizhiapi.site/v1'


def frozen_stream_checker(source, expected_hash):
    """Load only the historical pure checker, never its transport module."""
    if sha256(source.encode()).hexdigest() != expected_hash:
        raise ValueError('Stream checker snapshot changed')
    tree = ast.parse(source)
    if len(tree.body) != 1 or not isinstance(tree.body[0], ast.FunctionDef) or tree.body[0].name != 'extract_response_text':
        raise ValueError('Expected one pure stream checker')
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            raise ValueError('Imports not allowed in checker snapshot')
    scope = {}
    exec(compile(tree, '<frozen-g1-stream-checker>', 'exec'), scope)
    return scope['extract_response_text']


def sse_events(raw):
    """Decode finite archived SSE; require complete frames, never repair text."""
    text = raw.decode('utf-8').replace('\r\n', '\n')
    if text and not text.endswith('\n\n'):
        raise ValueError('Truncated SSE frame')
    result = []
    done = False
    for frame in text.split('\n\n'):
        if not frame: continue
        data, names = [], []
        for line in frame.split('\n'):
            if not line or line.startswith(':'): continue
            field, _, value = line.partition(':')
            if value.startswith(' '): value = value[1:]
            if field == 'data': data.append(value)
            elif field == 'event': names.append(value)
            elif field not in {'id', 'retry'}: raise ValueError('Unknown SSE field')
        if not data: continue
        payload = '\n'.join(data)
        if payload == '[DONE]':
            if done: raise ValueError('Duplicate DONE')
            done = True
            continue
        if done: raise ValueError('Data after DONE')
        event = pre.strict_json(payload)
        if not isinstance(event, dict) or not isinstance(event.get('type'), str):
            raise ValueError('Missing Responses event type')
        if len(names) > 1 or (names and names[0] != event['type']):
            raise ValueError('SSE event name conflict')
        result.append(event)
    return result


def project_stream(events, checker):
    """Check exact deltas/items/terminal/usage before reusing Preflight decoder.

    Original bytes/events must be archived by the existing raw-first protocol.
    An incomplete/error stream fails here; callers preserve its usage as UNKNOWN
    or separately observed, never infer zero cost or silently retry it.
    """
    text, usage = checker(events, MODEL)
    if any(type(usage[k]) is not int for k in ('input_tokens', 'output_tokens', 'total_tokens')):
        raise ValueError('Boolean usage is not token usage')
    terminal = events[-1]['response']
    # Bound the new non-streaming projection to the same validated final text.
    projected = pre.decode_response(json.dumps(terminal, ensure_ascii=False).encode(), 'responses')
    if projected.get('status') != 'CONTENT' or projected.get('text') != text:
        raise ValueError('Preflight terminal projection disagreement')
    return {'status': 'STREAM_MECHANICAL_PASS_NOT_SEMANTIC_ACCEPTANCE',
            'text': text, 'usage': usage, 'model': terminal['model'],
            'terminal': terminal, 'send_authorized': False}


def render_stream(case, output_cap):
    """Draft shape based on historical success; does not certify current limits."""
    if pre.value_hash(case['messages']) != case['messages_sha256']:
        raise ValueError('Frozen messages changed')
    if type(output_cap) is not int or output_cap <= 0:
        raise ValueError('Explicit output cap required')
    return {'model': MODEL, 'instructions': case['messages'][0]['content'],
            'input': case['messages'][1]['content'], 'stream': True,
            'max_output_tokens': output_cap}


def content_size(wire):
    text = wire['instructions'] + wire['input']
    return {'characters': len(text), 'utf8_bytes': len(text.encode())}


def empirical_proxy(size, tokens_per_byte):
    """Historical provider-usage ratio, not the actual tokenizer or a bound."""
    if size < 0 or tokens_per_byte <= 0: raise ValueError('Invalid proxy basis')
    return ceil(size * tokens_per_byte)


def guard_budget(*, known_spend, unknown_reservations, next_reservation, hard_cap):
    """Pure pre-dispatch gate; missing trustworthy money inputs blocks execution."""
    values = (known_spend, unknown_reservations, next_reservation, hard_cap)
    if any(v is None for v in values): return 'BLOCKED_UNKNOWN_BUDGET'
    from math import isfinite
    if any(type(v) not in (int, float) or not isfinite(v) or v < 0 for v in values):
        raise ValueError('Invalid budget')
    return 'WITHIN_SUPPLIED_CAP_NOT_AUTHORIZATION' if sum(values[:3]) <= hard_cap else 'STOP_BEFORE_DISPATCH'
