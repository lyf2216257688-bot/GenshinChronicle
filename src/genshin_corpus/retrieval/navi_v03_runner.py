"""Single-request v0.3 execution seam; default CLI is offline preparation/audit.

No F2 dispatch entrypoint is used. SDK/transport retries are disabled. Only an
identical, durably archived, proven TCP/TLS pre-send failure can be attempted
again, within the same invocation, at most three attempts in total.
"""
import argparse
from collections import defaultdict
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import time

import httpx
import openai
from jsonschema import Draft202012Validator, ValidationError

from . import navi_prompt_candidate as candidate
from . import navi_f2_c1 as c1
from . import navi_f2_sdk_transport as transport
from .navi_f1_prepare import read, sha, value_hash, ru_spans
from .navi_g1_s1_audit import verify_span
from .navi_v02_message_verifier import inspect

VERSION = 'p05-navi-v03-runner-1'
REPO = Path(__file__).resolve().parents[3]
FREEZE = REPO / 'data/retrieval/p05-navi-prompt-final-20261028-r1/candidate-freeze.json'
LEDGER = REPO / 'data/retrieval/p05-navi-v03-request-ledger'
MAX_ATTEMPTS = 3
ENGINE = ['src/genshin_corpus/retrieval/navi_prompt_candidate.py',
          'docs/research/phase-05/p05-navi-b-prompts-v03.md',
          'docs/research/phase-05/p05-navi-b-v02.schema.json',
          'src/genshin_corpus/retrieval/navi_v02_contract.py',
          'src/genshin_corpus/retrieval/navi_f2_preflight.py']
EXECUTOR = [Path(__file__).relative_to(REPO).as_posix()] + [
    'src/genshin_corpus/retrieval/' + name + '.py' for name in
    ['navi_f2_sdk_transport', 'navi_f2_c1', 'navi_v02_message_verifier',
     'navi_f2_instruction_diagnostic', 'navi_f1_prepare', 'navi_g1_s1_audit']]


def require(ok, message):
    if not ok:
        raise ValueError(message)


def put(path, value):
    """Create-only, fsync before any dispatch or subsequent attempt."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Preserve input key order: candidate.prepare_request embeds JSON as text.
    # Sorting a reloaded view would change the actual model-visible request.
    write_bytes(path, (json.dumps(value, ensure_ascii=False, indent=2,
                                 allow_nan=False) + '\n').encode())


def write_bytes(path, data):
    with Path(path).open('xb') as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())


def local(root, name):
    target = (Path(root) / name).resolve()
    require(target.is_relative_to(Path(root).resolve()), 'Archive path escapes its root')
    return target


def file_map(root, *, exclude=()):
    return {p.relative_to(root).as_posix(): sha(p) for p in sorted(Path(root).rglob('*'))
            if p.is_file() and p.relative_to(root).as_posix() not in exclude}


def verify_files(root, files, *, exclude=()):
    require(file_map(root, exclude=exclude) == files, 'Archive file set or hashes changed')
    for name, digest in files.items():
        require(sha(local(root, name)) == digest, 'Archive identity changed')


def frozen_engine(freeze):
    require(freeze['version'] == candidate.VERSION and freeze['policy'] == candidate.POLICY,
            'Wrong candidate version/policy')
    require(value_hash(freeze['files']) == freeze['freeze_identity_sha256'], 'Candidate freeze identity mismatch')
    hashes = {}
    for name in ENGINE:
        expected = freeze['files'][name]['sha256']
        require(sha(local(REPO, name)) == expected, 'Frozen semantic engine changed: ' + name)
        hashes[name] = expected
    return hashes


def verify_sources(view, binding, canonical_root):
    """Recheck only delivered evidence, through exact Canonical and whole RU spans."""
    packet, evidence, rows = (binding[k] for k in ['packet', 'evidence', 'ru_rows'])
    require(set(packet['sides']) == {'A', 'B'} and len(set(packet['sides'].values())) == 2, 'Source endpoints differ')
    require({s: view[s] for s in ['A', 'B']} == packet['payload'], 'View differs from delivered source packet')
    aliases = {q['id'] for s in ['A', 'B'] for q in view[s]['snippets']}
    require(aliases == set(packet['aliases']), 'Delivery alias set differs')
    require(set(evidence) == set(packet['aliases'].values()), 'Evidence registry scope differs')
    grouped = defaultdict(list)
    for uid, row in rows.items():
        require(row['unit_id'] == uid, 'RU identity differs')
        a = row['source']['canonical_address']
        grouped[(a['source_identity_key'].rsplit(':', 1)[-1], a['section_ordinal'], a['canonical_unit_ordinal'])].append(row)
    for side in ['A', 'B']:
        source = packet['sides'][side]
        require(isinstance(source, str) and source.isdecimal(), 'Expected verified OBC source ID')
        path = local(canonical_root, source + '.json')
        record = read(path)
        spans = ru_spans(record, grouped, source)
        for q in view[side]['snippets']:
            bound = evidence[packet['aliases'][q['id']]]
            require(bound['source'] == source and bound['text'] == q['text'] and
                    bound['canonical_sha256'] == sha(path), 'Source text/hash/endpoint mismatch')
            verify_span(bound, record)
            ids = [uid for lo, hi, uid in spans[(bound['section'], bound['unit'])]
                   if lo < bound['end'] and hi > bound['start']]
            require(ids == bound['official_unit_ids'], 'RU span binding mismatch')


def prepare(plan_root, view, binding, canonical_paths, *, request_id, mode,
            format_mode='json_object', cap=30000, candidate_freeze=FREEZE):
    """Freeze one source-bound request. Never creates a client or reads credentials."""
    require(isinstance(request_id, str) and bool(request_id.strip()), 'Request ID required')
    freeze = read(candidate_freeze)
    engine = frozen_engine(freeze)
    draft = candidate.prepare_request(view, mode=mode, model=c1.MODEL, route='jizhi',
                                      max_output_tokens=cap, format_mode=format_mode)
    root = Path(plan_root)
    root.mkdir(parents=True, exist_ok=False)
    (root / 'canonical').mkdir()
    for source in binding['packet']['sides'].values():
        require(isinstance(source, str) and source.isdecimal(), 'Invalid source ID')
        write_bytes(root / 'canonical' / (source + '.json'), Path(canonical_paths[source]).read_bytes())
    verify_sources(view, binding, root / 'canonical')
    put(root / 'view.json', view)
    put(root / 'binding.json', binding)
    put(root / 'request.json', draft)
    put(root / 'candidate-freeze.json', freeze)
    plan = {'runner_version': VERSION, 'candidate_version': candidate.VERSION,
            'request_id': request_id, 'mode': mode, 'format_mode': format_mode,
            'cap': cap, 'model': c1.MODEL, 'route': 'jizhi', 'base_url': c1.BASE_URL,
            'wire_fingerprint': draft['wire_sha256'], 'candidate_engine': engine,
            'executor_files': {name: sha(REPO / name) for name in EXECUTOR},
            'files': file_map(root), 'max_attempts': MAX_ATTEMPTS,
            'send_authorized': False, 'strict_provider_enforcement': 'UNKNOWN'}
    put(root / 'plan.json', plan)
    verify_plan(root)
    return plan


def prepare_from_f1(plan_root, source_root, pair_id, *, mode, format_mode='json_object', cap=30000):
    """Reuse F1's score-free source view, never an F2 Prompt or execution entrypoint."""
    require(isinstance(pair_id, str) and len(pair_id) == 20 and all(c in '0123456789abcdef' for c in pair_id), 'Invalid pair ID')
    source_root = Path(source_root)
    view = read(source_root / 'model-inputs/rc1' / (pair_id + '.json'))
    packet = read(source_root / 'deliveries' / (pair_id + '.json'))
    all_evidence = read(source_root / 'evidence.json')
    evidence = {eid: all_evidence[eid] for eid in packet['aliases'].values()}
    keys = {(e['source'], e['section'], e['unit']) for e in evidence.values()}
    rows = {}
    for uid, row in read(source_root / 'bound-ru-rows.json').items():
        a = row['source']['canonical_address']
        if (a['source_identity_key'].rsplit(':', 1)[-1], a['section_ordinal'], a['canonical_unit_ordinal']) in keys:
            rows[uid] = row
    return prepare(plan_root, view, {'packet': packet, 'evidence': evidence, 'ru_rows': rows},
                   {s: source_root / 'canonical' / (s + '.json') for s in packet['sides'].values()},
                   request_id=pair_id + '-' + mode + '-v03', mode=mode, format_mode=format_mode, cap=cap)


def verify_plan(root):
    root = Path(root)
    plan = read(root / 'plan.json')
    require(plan['runner_version'] == VERSION and plan['candidate_version'] == candidate.VERSION and
            plan['model'] == c1.MODEL and plan['route'] == 'jizhi' and plan['base_url'] == c1.BASE_URL and
            plan['max_attempts'] == MAX_ATTEMPTS and plan['send_authorized'] is False,
            'Plan version/route/policy differs')
    verify_files(root, plan['files'], exclude=('plan.json',))
    require(frozen_engine(read(root / 'candidate-freeze.json')) == plan['candidate_engine'], 'Candidate engine differs')
    require(plan['executor_files'] == {name: sha(REPO / name) for name in EXECUTOR}, 'Execution code changed since preparation')
    view, binding = read(root / 'view.json'), read(root / 'binding.json')
    expected = candidate.prepare_request(view, mode=plan['mode'], model=plan['model'], route=plan['route'],
                                         max_output_tokens=plan['cap'], format_mode=plan['format_mode'])
    require(expected == read(root / 'request.json') and expected['wire_sha256'] == plan['wire_fingerprint'],
            'Frozen v0.3 request fingerprint differs')
    verify_sources(view, binding, root / 'canonical')
    return plan


def valid_usage(usage, cap):
    if not isinstance(usage, dict):
        return False
    a, b, total = (usage.get(k) for k in ['input_tokens', 'output_tokens', 'total_tokens'])
    if any(type(v) is not int or v < 0 for v in [a, b, total]) or a + b != total or b > cap:
        return False
    for field, key, maximum in [('input_tokens_details', 'cached_tokens', a),
                                ('output_tokens_details', 'reasoning_tokens', b)]:
        detail = usage.get(field)
        if detail is not None:
            if not isinstance(detail, dict):
                return False
            value = detail.get(key)
            if value is not None and (type(value) is not int or not 0 <= value <= maximum):
                return False
    return True


def assess(folder, prepared):
    """Read-only full response and v0.3 content verification; no repair/reissue."""
    folder, prepared = Path(folder), Path(prepared)
    plan = verify_plan(prepared)
    enc = read(folder / 'http.json')['headers'].get('content-encoding', '')
    events = c1.sse_events(transport.decoded_body((folder / 'response.body.raw').read_bytes(), enc))
    sdk = [candidate.strict_json(line) for line in (folder / 'sdk-events.jsonl').read_text(encoding='utf8').splitlines()]
    checked = inspect(events, sdk)
    require(checked['full_stream'] == 'PASS', 'Full stream integrity failed, including reasoning IDs')
    terminal = events[-1]['response']
    require(events[0]['type'] == 'response.created' and terminal['model'] == plan['model'], 'Stream start/model differs')
    items = terminal['output']
    indexed_events = {'response.output_item.added', 'response.output_item.done',
        'response.content_part.added', 'response.content_part.done',
        'response.output_text.delta', 'response.output_text.done',
        'response.reasoning_summary_part.added', 'response.reasoning_summary_part.done',
        'response.reasoning_summary_text.delta', 'response.reasoning_summary_text.done',
        'response.reasoning_text.delta', 'response.reasoning_text.done'}
    allowed_events = indexed_events | {'response.created', 'response.in_progress', 'response.completed'}
    require(all(e['type'] in allowed_events for e in events), 'Unsupported stream event; never silently ignored')
    for index, item in enumerate(items):
        added = [i for i, e in enumerate(events) if e.get('type') == 'response.output_item.added' and e.get('output_index') == index]
        done = [i for i, e in enumerate(events) if e.get('type') == 'response.output_item.done' and e.get('output_index') == index]
        require(len(added) == len(done) == 1 and added[0] < done[0] < len(events)-1 and
                events[done[0]]['item'] == item, 'Output item completeness/order differs')
        if item['type'] == 'message':
            require(len(item['content']) == 1 and item['content'][0]['type'] == 'output_text', 'Unsupported message content')
    seq = [e.get('sequence_number') for e in events]
    if any(n is not None for n in seq):
        require(all(type(n) is int for n in seq) and seq == list(range(seq[0], seq[0] + len(seq))), 'Stream event sequence differs')
    for e in events:
        if e['type'] in indexed_events:
            require('output_index' in e, 'Missing stream output binding')
            if e['type'] not in {'response.output_item.added', 'response.output_item.done'}:
                require('item_id' in e, 'Missing stream item binding')
        if 'output_index' in e:
            index = e['output_index']
            require(type(index) is int and 0 <= index < len(items), 'Unbound stream output index')
            if 'item_id' in e:
                require(e['item_id'] == items[index]['id'], 'Unbound stream item ID')
        if e['type'] in {'response.created', 'response.in_progress'}:
            require(e['response']['id'] == terminal['id'], 'Response identity changed')
    require(valid_usage(checked['usage'], plan['cap']), 'Usage contract failed')
    view = read(prepared / 'view.json')
    text = checked['message_text']
    try:
        value = candidate.strict_json(text)
    except (ValueError, TypeError):
        return {'state': 'CONTENT_INVALID', 'content_status': 'JSON_PARSE_FAILURE', 'usage': checked['usage'], 'full_stream': 'PASS'}
    try:
        Draft202012Validator(candidate.model_schema(plan['mode'])).validate(value)
        result = candidate.validate_output(text, {s: [q['id'] for q in view[s]['snippets']] for s in ['A', 'B']},
                                           view.get('candidates', []), mode=plan['mode'])
    except ValidationError:
        return {'state': 'CONTENT_INVALID', 'content_status': 'SCHEMA_FAILURE', 'usage': checked['usage'], 'full_stream': 'PASS'}
    except ValueError:
        return {'state': 'CONTENT_INVALID', 'content_status': 'CONTEXT_CONTRACT_FAILURE', 'usage': checked['usage'], 'full_stream': 'PASS'}
    return {'state': 'CONTENT_VERIFIED', 'content_status': 'PASS', 'usage': checked['usage'],
            'full_stream': 'PASS', 'candidate': result, 'response_id': terminal['id'], 'model': terminal['model']}


def attempt(prepared, folder, *, api_key, plan_sha256, transport_factory=None, expected_body_sha256=None):
    plan = verify_plan(prepared)
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=False)
    wire = read(Path(prepared) / 'request.json')['wire']
    put(folder / 'request.json', wire)
    put(folder / 'reservation.json', {'state': 'RESERVED_NOT_RETRYABLE', 'runner_version': VERSION,
        'candidate_version': candidate.VERSION, 'plan_sha256': plan_sha256, 'fingerprint': plan['wire_fingerprint']})
    state = {'http_status': None, 'calls': 0}
    trace, started = [], time.monotonic()
    result = {'state': 'UNKNOWN', 'usage': 'UNKNOWN', 'retry_allowed': False,
              'human_acceptance': 'NOT_REVIEWED', 'navigation_status': 'INACTIVE', 'actual_fee': 'UNKNOWN'}

    def request_hook(req):
        state['calls'] += 1
        require(state['calls'] == 1 and req.method == 'POST' and str(req.url) == c1.BASE_URL + '/responses' and
                candidate.strict_json(req.content.decode()) == wire, 'SDK dispatch identity mismatch')
        if expected_body_sha256 is not None:
            require(candidate.sha256(req.content).hexdigest() == expected_body_sha256, 'SDK body bytes changed across attempts')
        req.extensions['trace'] = lambda name, info: trace.append(name)
        write_bytes(folder / 'request.body.bin', req.content)
        put(folder / 'request-metadata.json', {'url': str(req.url), 'method': req.method,
            'trust_env': False, 'proxy': None, 'redirects': False, 'sdk_retries': 0, 'transport_retries': 0,
            'dispatch_intent_utc_epoch': time.time(),
            'headers': {k: req.headers[k] for k in ['user-agent', 'content-type', 'accept', 'accept-encoding'] if k in req.headers}})

    def response_hook(res):
        state['http_status'] = res.status_code
        put(folder / 'http.json', {'status': res.status_code,
            'headers': {k: res.headers[k] for k in ['content-type', 'content-encoding', 'x-request-id', 'cf-ray', 'retry-after'] if k in res.headers}})
        require(not res.is_stream_consumed, 'Response consumed before raw archival')
        res.stream = transport.RawTee(res.stream, folder / 'response.body.raw')

    try:
        underlying = transport_factory() if transport_factory is not None else httpx.HTTPTransport(trust_env=False, retries=0)
        with httpx.Client(transport=underlying, trust_env=False, follow_redirects=False,
                          timeout=httpx.Timeout(180, connect=15),
                          event_hooks={'request': [request_hook], 'response': [response_hook]}) as client:
            with openai.OpenAI(base_url=c1.BASE_URL, api_key=api_key, max_retries=0, timeout=180, http_client=client) as sdk:
                with sdk.responses.create(**wire) as response:
                    with (folder / 'sdk-events.jsonl').open('x', encoding='utf8') as events:
                        for event in response:
                            events.write(json.dumps(event.model_dump(mode='json', exclude_unset=True), ensure_ascii=False) + '\n')
                            events.flush()
                            os.fsync(events.fileno())
                            require(time.monotonic() - started <= 180, 'Wall-clock deadline')
        require(state['http_status'] == 200, 'Unexpected HTTP status')
        result.update(assess(folder, prepared))
        if result['state'] == 'CONTENT_VERIFIED':
            put(folder / 'candidate.json', result.pop('candidate'))
    except Exception as exc:
        result['diagnostics'] = transport.diagnostics(exc)
        if state['http_status'] is not None:
            result['state'] = 'HTTP_REJECTED' if state['http_status'] != 200 else 'STREAM_OR_VALIDATION_FAILURE'
        elif transport.pre_send_failure(trace):
            result['state'] = 'PRE_SEND_FAILURE_CANDIDATE'  # not NOT_SENT until archive proof below
    finally:
        # Preserve separately observed usage/terminal even when stream/content is invalid.
        if (folder / 'http.json').exists() and (folder / 'response.body.raw').exists():
            try:
                enc = read(folder / 'http.json')['headers'].get('content-encoding', '')
                raw = c1.sse_events(transport.decoded_body((folder / 'response.body.raw').read_bytes(), enc))
                terms = [e for e in raw if e['type'] in {'response.completed', 'response.incomplete', 'response.failed'}]
                result['terminal_observations'] = [{k: e.get('response', {}).get(k) for k in
                    ['id', 'status', 'model', 'usage', 'incomplete_details']} for e in terms]
                if state['http_status'] == 200 and len(terms) == 1 and terms[0]['type'] == 'response.incomplete':
                    result['state'] = 'OUTPUT_TRUNCATED' if terms[0]['response'].get('incomplete_details', {}).get('reason') == 'max_output_tokens' else 'RESPONSE_INCOMPLETE'
            except (ValueError, OSError, UnicodeError, KeyError, TypeError):
                pass
        result.update(http_status=state['http_status'], calls=state['calls'], elapsed_seconds=round(time.monotonic()-started, 3))
        put(folder / 'trace.json', {'events': trace, 'exception_text_omitted': True})
        result['artifacts'] = file_map(folder)
        put(folder / 'result.json', result)
    return result


def proven_pre_send(folder, plan, plan_sha256):
    """F2/G1 trace predicate plus full, independent, create-only archival proof."""
    folder = Path(folder)
    result = read(folder / 'result.json')
    required = {'request.json', 'reservation.json', 'request.body.bin', 'request-metadata.json', 'trace.json'}
    verify_files(folder, result['artifacts'], exclude=('result.json',))
    if (set(result['artifacts']) != required or result.get('state') != 'PRE_SEND_FAILURE_CANDIDATE' or
            result.get('http_status') is not None or result.get('calls') != 1):
        return False
    trace = read(folder / 'trace.json')['events']
    if (not isinstance(trace, list) or not all(isinstance(e, str) for e in trace) or
            not transport.pre_send_failure(trace) or any(any(marker in e for marker in
                ['send_request_headers.', 'send_request_body.', 'receive_response_headers.', 'receive_response_body.']) for e in trace)):
        return False
    wire = read(folder / 'request.json')
    reserved, meta = read(folder / 'reservation.json'), read(folder / 'request-metadata.json')
    return (value_hash(wire) == plan['wire_fingerprint'] and candidate.strict_json((folder / 'request.body.bin').read_text(encoding='utf8')) == wire and
            reserved == {'state': 'RESERVED_NOT_RETRYABLE', 'runner_version': VERSION,
                'candidate_version': candidate.VERSION, 'plan_sha256': plan_sha256, 'fingerprint': plan['wire_fingerprint']} and
            meta['url'] == c1.BASE_URL + '/responses' and meta['method'] == 'POST' and
            meta['sdk_retries'] == meta['transport_retries'] == 0 and meta['trust_env'] is False and
            meta['proxy'] is None and meta['redirects'] is False)


def run_case(prepared, run_root, *, api_key, send_authorized=False, acknowledge_plan_sha256=None,
             ledger=LEDGER, transport_factory=None):
    """Explicitly authorized one-request run. Existing reservation always blocks.

    Use the same persistent ledger for every run, including renamed output roots.
    There is no automatic resume, batch continuation or content retry.
    """
    require(send_authorized is True, 'Explicit execution authorization required')
    prepared, root, ledger = Path(prepared), Path(run_root), Path(ledger)
    plan = verify_plan(prepared)
    plan_sha256 = sha(prepared / 'plan.json')
    require(acknowledge_plan_sha256 == plan_sha256, 'Exact prepared plan acknowledgment required')
    require(isinstance(api_key, str) and bool(api_key), 'Missing credential')
    require(api_key not in json.dumps(read(prepared / 'request.json'), ensure_ascii=False), 'Credential in model input')
    require(not root.exists(), 'Run directory already exists; never resume/overwrite')
    claim = ledger / plan['wire_fingerprint']
    claim.mkdir(parents=True, exist_ok=False)  # persistent lock survives crashes and renamed run roots
    put(claim / 'reservation.json', {'request_id': plan['request_id'], 'plan_sha256': plan_sha256,
        'fingerprint': plan['wire_fingerprint'], 'run_root': str(root.resolve()), 'state': 'RESERVED_NO_AUTOMATIC_RESUME'})
    root.mkdir(parents=True, exist_ok=False)
    shutil.copytree(prepared, root / 'prepared')
    put(root / 'run-config.json', {'runner_version': VERSION, 'candidate_version': candidate.VERSION,
        'plan_sha256': plan_sha256, 'fingerprint': plan['wire_fingerprint'], 'ledger': str(ledger.resolve()),
        'execution_authorized': True, 'ack_plan_sha256': plan_sha256,
        'execution_surface': 'INJECTED_TRANSPORT_NOT_LIVE_PROOF' if transport_factory is not None else 'LIVE_SDK',
        'max_attempts': MAX_ATTEMPTS, 'sdk_retries': 0, 'transport_retries': 0,
        'strict_provider_enforcement': 'UNKNOWN', 'semantic_quality': 'NOT_VALIDATED'})
    final = {'state': 'LOCAL_PROCESSING_UNKNOWN', 'usage': 'UNKNOWN'}
    for number in range(1, MAX_ATTEMPTS + 1):
        try:
            require(sha(prepared / 'plan.json') == plan_sha256, 'Prepared plan changed between attempts')
            require(verify_plan(prepared) == plan and verify_plan(root / 'prepared') == plan, 'Retry request identity changed')
            folder = root / 'attempts' / ('attempt-%03d' % number)
            final = attempt(root / 'prepared', folder, api_key=api_key, plan_sha256=plan_sha256,
                            transport_factory=transport_factory,
                            expected_body_sha256=sha(root / 'attempts/attempt-001/request.body.bin') if number > 1 else None)
            proof = proven_pre_send(folder, plan, plan_sha256) if final['state'] == 'PRE_SEND_FAILURE_CANDIDATE' else False
            retry = proof and number < MAX_ATTEMPTS
            put(root / ('retry-decision-%03d.json' % number), {'attempt': number,
                'result_sha256': sha(folder / 'result.json'), 'pre_send_proven': proof, 'retry': retry,
                'plan_sha256': plan_sha256, 'fingerprint': plan['wire_fingerprint'], 'max_attempts': MAX_ATTEMPTS})
            if proof:
                final = final | {'state': 'NOT_SENT', 'usage': 'NOT_ISSUED'}
            elif final['state'] == 'PRE_SEND_FAILURE_CANDIDATE':
                final = final | {'state': 'UNKNOWN', 'usage': 'UNKNOWN'}
            if not retry:
                break
            time.sleep(.5)
        except Exception as exc:
            final = {'state': 'LOCAL_PROCESSING_UNKNOWN', 'usage': 'UNKNOWN', 'diagnostics': transport.diagnostics(exc)}
            break
    summary = {k: v for k, v in final.items() if k != 'artifacts'}
    attempt_count = len(list((root / 'attempts').glob('attempt-*')))
    summary.update(attempts=attempt_count, plan_sha256=plan_sha256, fingerprint=plan['wire_fingerprint'],
                   stopped=True, automatic_resume=False, retry=False, human_acceptance='NOT_REVIEWED', navigation_status='INACTIVE')
    summary['artifacts'] = file_map(root)
    put(root / 'run-result.json', summary)
    put(claim / 'outcome.json', {'run_result_sha256': sha(root / 'run-result.json'), 'state': summary['state']})
    return summary


def audit_run(root):
    root = Path(root)
    summary = read(root / 'run-result.json')
    verify_files(root, summary['artifacts'], exclude=('run-result.json',))
    plan = verify_plan(root / 'prepared')
    require(summary['fingerprint'] == plan['wire_fingerprint'] and sha(root / 'prepared/plan.json') == summary['plan_sha256'], 'Run identity differs')
    config = read(root / 'run-config.json')
    claim = Path(config['ledger']) / plan['wire_fingerprint']
    require(read(claim / 'reservation.json') == {'request_id': plan['request_id'],
        'plan_sha256': summary['plan_sha256'], 'fingerprint': plan['wire_fingerprint'],
        'run_root': str(root.resolve()), 'state': 'RESERVED_NO_AUTOMATIC_RESUME'}, 'Persistent reservation differs')
    require(read(claim / 'outcome.json') == {'run_result_sha256': sha(root / 'run-result.json'),
        'state': summary['state']}, 'Persistent outcome differs')
    folders = sorted((root / 'attempts').iterdir())
    require(1 <= len(folders) <= MAX_ATTEMPTS and len(folders) == summary['attempts'], 'Attempt accounting differs')
    for number, folder in enumerate(folders, 1):
        require(folder.name == 'attempt-%03d' % number, 'Attempt sequence differs')
        result = read(folder / 'result.json')
        verify_files(folder, result['artifacts'], exclude=('result.json',))
        require(value_hash(read(folder / 'request.json')) == plan['wire_fingerprint'], 'Attempt identity differs')
        require(candidate.strict_json((folder / 'request.body.bin').read_text(encoding='utf8')) == read(folder / 'request.json'), 'Actual request body differs')
        require(sha(folder / 'request.body.bin') == sha(folders[0] / 'request.body.bin'), 'Actual body bytes differ across attempts')
        decision = read(root / ('retry-decision-%03d.json' % number))
        proof = proven_pre_send(folder, plan, summary['plan_sha256']) if result['state'] == 'PRE_SEND_FAILURE_CANDIDATE' else False
        require(decision == {'attempt': number, 'result_sha256': sha(folder / 'result.json'),
            'pre_send_proven': proof, 'retry': proof and number < MAX_ATTEMPTS,
            'plan_sha256': summary['plan_sha256'], 'fingerprint': plan['wire_fingerprint'],
            'max_attempts': MAX_ATTEMPTS}, 'Retry evidence/decision differs')
        require(number == len(folders) or proof, 'Another attempt followed an unproven failure')
        if result['state'] in {'CONTENT_VERIFIED', 'CONTENT_INVALID'}:
            replay = assess(folder, root / 'prepared')
            require(replay['state'] == result['state'] and replay['content_status'] == result['content_status'], 'Archived replay differs')
            if result['state'] == 'CONTENT_VERIFIED':
                require(replay['candidate'] == read(folder / 'candidate.json'), 'Archived candidate differs')
    return {'state': summary['state'], 'archive_integrity': 'PASS', 'candidate_version': candidate.VERSION,
            'semantic_quality': 'NOT_VALIDATED', 'strict_provider_enforcement': 'UNKNOWN'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    prep = sub.add_parser('prepare')
    prep.add_argument('--source-root', required=True, type=Path)
    prep.add_argument('--pair-id', required=True)
    prep.add_argument('--mode', choices=['generation', 'review'], required=True)
    prep.add_argument('--format', choices=['json_object', 'json_schema'], default='json_object')
    prep.add_argument('--cap', type=int, default=30000)
    prep.add_argument('--plan', required=True, type=Path)
    execute = sub.add_parser('run')
    execute.add_argument('--plan', required=True, type=Path)
    execute.add_argument('--out', required=True, type=Path)
    execute.add_argument('--execute-authorized', action='store_true')
    execute.add_argument('--ack-plan-sha256')
    audit = sub.add_parser('audit')
    audit.add_argument('--run', required=True, type=Path)
    args = parser.parse_args()
    if args.action == 'prepare':
        plan = prepare_from_f1(args.plan, args.source_root, args.pair_id, mode=args.mode, format_mode=args.format, cap=args.cap)
        result = {'state': 'PREPARED_NOT_AUTHORIZED', 'wire_fingerprint': plan['wire_fingerprint'], 'plan_sha256': sha(args.plan / 'plan.json')}
    elif args.action == 'audit':
        result = audit_run(args.run)
    elif not args.execute_authorized:
        plan = verify_plan(args.plan)
        result = {'state': 'DRY_RUN_NOT_AUTHORIZED', 'wire_fingerprint': plan['wire_fingerprint'], 'plan_sha256': sha(args.plan / 'plan.json')}
    else:
        require(args.ack_plan_sha256 == sha(args.plan / 'plan.json'), 'Exact plan acknowledgment required')
        # Environment/credentials are read ONLY after explicit run authorization.
        from genshin_corpus.rag.navi_r1_experiment import environment
        env = environment()
        require(env.get('JIZHI_BASE_URL', '').rstrip('/') == c1.BASE_URL, 'Configured route mismatch')
        result = run_case(args.plan, args.out, api_key=env.get('JIZHI_API_KEY'),
                          send_authorized=True, acknowledge_plan_sha256=args.ack_plan_sha256)
    print(json.dumps({k: v for k, v in result.items() if k != 'artifacts'}, ensure_ascii=False))
    if args.action == 'run' and args.execute_authorized and result['state'] != 'CONTENT_VERIFIED':
        raise SystemExit(2)


if __name__ == '__main__':
    main()
