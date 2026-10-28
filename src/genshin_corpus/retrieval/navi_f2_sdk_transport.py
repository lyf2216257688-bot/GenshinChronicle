"""Isolated F2 Responses SDK transport; raw-first, explicit proxy, no retries.

No environment loading or execution at import time. The caller owns frozen order,
content validators and authorization. This module cannot issue capability probes.
"""
import gzip
import hashlib
import json
import os
from pathlib import Path
import time
import zlib

import httpx
import openai

from . import navi_f2_c1 as c1
from .navi_f1_prepare import put, value_hash

CAP = 30000


def diagnostics(exc):
    """Keep cause types and OS codes, never arbitrary exception/request text."""
    seen, rows = set(), []
    while isinstance(exc, BaseException) and id(exc) not in seen:
        seen.add(id(exc))
        row = {'type': type(exc).__module__+'.'+type(exc).__name__}
        for name in ('errno', 'winerror', 'verify_code'):
            v = getattr(exc, name, None)
            if type(v) is int:
                row[name] = v
        rows.append(row)
        reason = getattr(exc, 'reason', None)
        exc = exc.__cause__ or (reason if isinstance(reason, BaseException) else exc.__context__)
    return rows


def pre_send_failure(events):
    """Reuse G1's conservative traced-not-sent criterion; never auto-retry."""
    return (not any(e.endswith('send_request_headers.started') for e in events)
            and any(e in {'connection.connect_tcp.failed', 'connection.start_tls.failed'} for e in events))


class RawTee(httpx.SyncByteStream):
    """Transport body bytes, before content decoding or SDK SSE parsing.

    HTTP transfer framing/TLS packets are not available here. Content-Encoding
    remains intact; this is not a serialization of parsed SDK events.
    """
    def __init__(self, stream, path):
        self.stream = stream
        self.file = Path(path).open('xb')

    def __iter__(self):
        for chunk in self.stream:
            self.file.write(chunk)
            self.file.flush()
            os.fsync(self.file.fileno())
            yield chunk

    def close(self):
        try:
            self.stream.close()
        finally:
            self.file.close()


def decoded_body(raw, encoding):
    if encoding in ('', 'identity'):
        return raw
    if encoding == 'gzip':
        return gzip.decompress(raw)
    if encoding == 'deflate':
        return zlib.decompress(raw)
    raise ValueError('Unsupported Content-Encoding')


def safe_resume(run):
    """Check every prior reservation and its archived files before advancing."""
    run = Path(run)
    for path in (run/'reservations').glob('*.json'):
        reserved = json.loads(path.read_text(encoding='utf8'))
        attempt = run/'attempts'/reserved['fingerprint']
        terminal_path = attempt/'terminal.json'
        if not terminal_path.exists():
            raise ValueError('Pending reservation; no automatic continuation')
        terminal = json.loads(terminal_path.read_text(encoding='utf8'))
        files = {p.name for p in attempt.iterdir() if p.is_file() and p.name != 'terminal.json'}
        if files != set(terminal['artifacts']) or any(
                hashlib.sha256((attempt/name).read_bytes()).hexdigest() != digest
                for name, digest in terminal['artifacts'].items()):
            raise ValueError('Prior attempt archive changed')
        if value_hash(json.loads((attempt/'request.json').read_text(encoding='utf8'))) != reserved['fingerprint']:
            raise ValueError('Prior request identity changed')
        if terminal.get('status') != 'COMPLETE_STREAM_VALIDATED' or terminal.get('pause_batch') is not False:
            raise ValueError('Unresolved or stopped run; no automatic continuation')


def run_case(case, run, *, api_key, proxy, checker, transport=None,
             quarantined_wires=(), send_authorized=False):
    """One frozen case, exactly one SDK attempt. Existing identity fails closed.

    `proxy` must explicitly be None (direct) or the previously documented loopback
    proxy. No implicit system/environment proxy, IP selection or UA override.
    MockTransport fixtures exercise the same SDK/archival path without networking.
    """
    if send_authorized is not True:
        raise ValueError('Explicit execution authorization required')
    if proxy not in (None, 'http://127.0.0.1:7890'):
        raise ValueError('Unverified proxy configuration')
    if transport is not None and proxy is not None:
        raise ValueError('Injected transport and proxy must not conflict')
    if not isinstance(api_key, str) or not api_key:
        raise ValueError('Missing credential')
    wire = c1.render_stream(case, CAP)
    if api_key in json.dumps(wire, ensure_ascii=False):
        raise ValueError('Credential in input')
    fingerprint = value_hash(wire)
    if any(value_hash(w) == fingerprint for w in quarantined_wires):
        raise ValueError('Issued unresolved request is quarantined')
    run = Path(run)
    safe_resume(run)
    put(run/'reservations'/(fingerprint+'.json'), {'fingerprint': fingerprint,
        'request_id': case['request_id'], 'messages_sha256': case['messages_sha256']})
    attempt = run/'attempts'/fingerprint
    attempt.mkdir(parents=True, exist_ok=False)
    put(attempt/'request.json', wire)
    put(attempt/'case.json', {k: v for k, v in case.items() if k != 'messages'})
    state = {'http_status': None, 'calls': 0, 'encoding': '', 'peer': None}
    traces, sdk_events = [], []
    started = time.monotonic()

    def trace(event, info):
        traces.append(event)

    def request_hook(req):
        state['calls'] += 1
        if (state['calls'] != 1 or str(req.url) != c1.BASE_URL+'/responses'
                or json.loads(req.content) != wire):
            raise ValueError('SDK request identity mismatch')
        req.extensions['trace'] = trace
        with (attempt/'request.body.bin').open('xb') as f:
            f.write(req.content); f.flush(); os.fsync(f.fileno())
        put(attempt/'request-metadata.json', {'url': str(req.url), 'method': req.method,
            'headers': {k: req.headers[k] for k in ('user-agent', 'accept', 'content-type', 'accept-encoding') if k in req.headers},
            'trust_env': False, 'proxy': proxy, 'redirects': False,
            'sdk_retries': 0, 'transport_retries': 0,
            'public_egress_ip': 'UNKNOWN', 'dispatch_intent_utc_epoch': time.time()})

    def response_hook(res):
        state['http_status'] = res.status_code
        state['encoding'] = res.headers.get('content-encoding', '').lower()
        stream = res.extensions.get('network_stream')
        if stream is not None:
            try:
                peer = stream.get_extra_info('server_addr')
                if isinstance(peer, (tuple, list)) and len(peer) == 2:
                    state['peer'] = list(peer)
            except (AttributeError, TypeError):
                pass
        put(attempt/'http.json', {'status': res.status_code,
            'headers': {k: res.headers[k] for k in ('content-type', 'content-encoding', 'x-request-id', 'cf-ray', 'retry-after') if k in res.headers},
            'tcp_peer': state['peer'], 'public_egress_ip': 'UNKNOWN'})
        if res.is_stream_consumed:
            raise ValueError('Response was consumed before raw archival')
        res.stream = RawTee(res.stream, attempt/'response.body.raw')

    result = {'status': 'EXECUTION_UNKNOWN', 'usage': 'UNKNOWN', 'pause_batch': True,
              'retry_allowed': False, 'human_acceptance': 'NOT_REVIEWED', 'navigation_status': 'INACTIVE'}
    try:
        selected_transport = transport if transport is not None else httpx.HTTPTransport(
            proxy=proxy, trust_env=False, retries=0,
            limits=httpx.Limits(max_connections=1, max_keepalive_connections=1))
        with httpx.Client(transport=selected_transport, trust_env=False,
                follow_redirects=False, timeout=httpx.Timeout(180, connect=15),
                limits=httpx.Limits(max_connections=1, max_keepalive_connections=1),
                event_hooks={'request': [request_hook], 'response': [response_hook]}) as hc:
            with openai.OpenAI(base_url=c1.BASE_URL, api_key=api_key,
                    max_retries=0, timeout=180, http_client=hc) as client:
                with client.responses.create(**wire) as stream:
                    with (attempt/'sdk-events.jsonl').open('x', encoding='utf8') as archive:
                        for event in stream:
                            mapped = event.model_dump(mode='json', exclude_unset=True)
                            sdk_events.append(mapped)
                            archive.write(json.dumps(mapped, ensure_ascii=False)+'\n')
                            archive.flush(); os.fsync(archive.fileno())
                            if time.monotonic()-started > 180:
                                raise TimeoutError('Wall-clock deadline')
        raw = decoded_body((attempt/'response.body.raw').read_bytes(), state['encoding'])
        parsed = c1.sse_events(raw)
        projected = c1.project_stream(parsed, checker)
        sdk_projected = c1.project_stream(sdk_events, checker)
        if projected['text'] != sdk_projected['text'] or projected['usage'] != sdk_projected['usage']:
            raise ValueError('SDK/raw projection disagreement')
        put(attempt/'output.json', {'text': projected['text'], 'usage': projected['usage'],
            'model': projected['model'], 'authority': 'MECHANICAL_ONLY'})
        result.update(status='COMPLETE_STREAM_VALIDATED', usage=projected['usage'],
            model=projected['model'], pause_batch=False)
    except Exception as exc:
        result['diagnostics'] = diagnostics(exc)
        if state['http_status'] is not None and state['http_status'] >= 400:
            result['status'] = 'HTTP_REJECTED'
        elif pre_send_failure(traces):
            result['status'] = 'NOT_SENT'
            result['usage'] = 'NOT_ISSUED'
        elif (attempt/'response.body.raw').exists():
            result['status'] = 'STREAM_OR_ENVELOPE_FAILURE'
            # Keep a terminal's observations even when other surfaces disagree.
            try:
                parsed = c1.sse_events(decoded_body((attempt/'response.body.raw').read_bytes(), state['encoding']))
                terminals = [e for e in parsed if e.get('type') in {'response.completed', 'response.incomplete', 'response.failed'}]
                result['terminal_observations'] = [e.get('response', {}) for e in terminals]
                if any(e.get('type') == 'response.incomplete' and e.get('response', {}).get('incomplete_details', {}).get('reason') == 'max_output_tokens' for e in terminals):
                    result['status'] = 'OUTPUT_TRUNCATED'
            except (ValueError, UnicodeError, OSError):
                pass
    finally:
        result.update(http_status=state['http_status'], calls=state['calls'],
            elapsed_seconds=round(time.monotonic()-started, 3))
        put(attempt/'trace.json', {'events': traces, 'error_text_intentionally_omitted': True})
        result['artifacts'] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in attempt.iterdir() if p.is_file()}
        put(attempt/'terminal.json', result)
    return result
