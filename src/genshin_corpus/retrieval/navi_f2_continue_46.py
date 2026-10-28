"""Frozen F2 continuation: at most three proven pre-send attempts per slot."""
import argparse
import json
import time
from pathlib import Path

from genshin_corpus.rag.navi_r1_experiment import environment
from . import navi_f2_json_mode_execute_v2 as run
from . import navi_f2_json_mode_30k_preflight as plan30
from .navi_f1_prepare import put, read, sha, value_hash

BASE = Path('data/retrieval')
PRIOR = BASE / 'p05-navi-f2-json-mode-execution-20261028-r1'
PRIOR_AUDIT = BASE / 'p05-navi-f2-json-mode-execution-audit-20261028-r1/audit.json'
PRIOR_AUDIT_SHA = '7a04adc93d24df5a8588cf1284351d849fcbdb0c5744ea1e5048617c50d08d2c'
HISTORICAL = BASE / 'p05-navi-f2-json-mode-continuation-46-20261028-r1'
HISTORICAL_AUDIT = BASE / 'p05-navi-f2-json-mode-continuation-audit-20261028-r1/audit.json'
HISTORICAL_AUDIT_SHA = 'b52f706b93e803a2e07b342c00e2161d6ae2b8b751ea01d63730655805ac204e'
OUT = BASE / 'p05-navi-f2-json-mode-continuation-retry-20261028-r1'
MAX_ATTEMPTS = 3
CONTENT_FAILURES = {'JSON_PARSE_FAILURE', 'LEGACY_NATIVE_CONTRACT_FAILURE',
                    'SCHEMA_FAILURE', 'CONTEXT_CONTRACT_FAILURE'}


class ValidatorFault(Exception):
    """Unexpected validator errors cannot be classified as model failures."""


def guard_validator(fn):
    def guarded(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except (KeyError, TypeError) as exc:
            raise ValidatorFault(type(exc).__name__) from exc
    return guarded


def verify_prior():
    manifest = run.verify_plan()
    if manifest != plan30.build_plan() or manifest['max_output_tokens'] != 30000:
        raise ValueError('Frozen 30k plan differs')
    if sha(PRIOR_AUDIT) != PRIOR_AUDIT_SHA:
        raise ValueError('Prior audit identity changed')
    audit = read(PRIOR_AUDIT)
    expected = {x['path'] for x in audit['run_files']}
    actual = {p.relative_to(PRIOR).as_posix() for p in PRIOR.rglob('*') if p.is_file()}
    if actual != expected or any(sha(PRIOR / x['path']) != x['sha256'] or
                                 (PRIOR / x['path']).stat().st_size != x['bytes']
                                 for x in audit['run_files']):
        raise ValueError('Prior run artifacts differ from frozen audit')
    if read(PRIOR / 'run-config.json')['prepared_manifest_sha256'] != sha(run.PLAN / 'prepared-manifest.json'):
        raise ValueError('Prior run used a different plan')
    if read(PRIOR / 'STOP.json') != {'fee': 'UNKNOWN', 'no_retry': True,
                                    'remaining_not_sent': 46, 'slot': 2,
                                    'state': 'CONTENT_INVALID'}:
        raise ValueError('Prior stop differs')
    if len(audit['slots']) != 48 or any(audit['slots'][i]['request_id'] != row['request_id']
                                        for i, row in enumerate(manifest['records'])):
        raise ValueError('Prior ledger differs from plan')
    if [x['state'] for x in audit['slots'][:2]] != ['CONTENT_VERIFIED', 'CONTENT_INVALID'] or any(
            x['state'] != 'NOT_SENT' for x in audit['slots'][2:]):
        raise ValueError('Prior slot accounting differs')
    evidence = read(run.F1 / 'evidence.json')
    schema = read(run.PRE / 'contracts/p05-navi-b-v02.schema.json')
    legacy = guard_validator(run.pre.native_legacy_validator(run.PRE / 'contracts'))
    for row, expected_state in zip(manifest['records'][:2], ('CONTENT_VERIFIED', 'CONTENT_INVALID')):
        checked = run.verify_archived_content(PRIOR / row['request_id'],
                    read(run.PRE / 'requests' / (row['request_id'] + '.json')),
                    evidence=evidence, legacy_validator=legacy, schema=schema)
        if checked['state'] != expected_state or checked['full_stream_status'] != 'PASS':
            raise ValueError('Prior replay differs')
    verify_historical_continuation(manifest)
    return manifest


def verify_historical_continuation(manifest):
    if sha(HISTORICAL_AUDIT) != HISTORICAL_AUDIT_SHA:
        raise ValueError('Historical continuation audit changed')
    audit = read(HISTORICAL_AUDIT)
    expected = {x['path'] for x in audit['continuation_files']}
    actual = {p.relative_to(HISTORICAL).as_posix() for p in HISTORICAL.rglob('*') if p.is_file()}
    if actual != expected or any(sha(HISTORICAL / x['path']) != x['sha256'] or
                                 (HISTORICAL / x['path']).stat().st_size != x['bytes']
                                 for x in audit['continuation_files']):
        raise ValueError('Historical continuation artifacts changed')
    row = manifest['records'][2]
    slots = audit['slots']
    if (len(slots) != 48 or any(x['request_id'] != r['request_id']
                                for x, r in zip(slots, manifest['records'])) or
            [x['state'] for x in slots[:3]] != ['CONTENT_VERIFIED', 'CONTENT_INVALID', 'NOT_SENT_TLS_ATTEMPT'] or
            any(x['state'] != 'NOT_SENT_NOT_ATTEMPTED' for x in slots[3:])):
        raise ValueError('Historical continuation accounting differs')
    stop = read(HISTORICAL / 'STOP.json')
    if stop != {'no_retry': True, 'remaining_not_sent': 45, 'request_id': row['request_id'],
                'slot': 3, 'state': 'NOT_SENT'}:
        raise ValueError('Historical continuation stop differs')
    folder = HISTORICAL / row['request_id']
    recorded = read(folder / 'result.json')
    if not proven_pre_send(folder, row, recorded):
        raise ValueError('Historical slot 3 pre-send proof invalid')


def proven_pre_send(folder, row, recorded):
    """Reuse the F2/G1 trace predicate and require consistent archived evidence."""
    if (recorded.get('state') != 'NOT_SENT' or recorded.get('http_status') is not None or
            recorded.get('calls') != 1 or recorded.get('retry') is not False or
            (folder / 'http.json').exists() or (folder / 'response.body.raw').exists() or
            (folder / 'sdk-events.jsonl').exists()):
        return False
    trace = read(folder / 'trace.json').get('events')
    if not isinstance(trace, list) or not all(isinstance(x, str) for x in trace):
        return False
    if (not run.transport.pre_send_failure(trace) or any(
            any(marker in event for marker in ('send_request_headers.', 'send_request_body.',
                                               'receive_response_headers.', 'receive_response_body.'))
            for event in trace)):
        return False
    wire = read(folder / 'request.json')
    if value_hash(wire) != row['wire_fingerprint']:
        return False
    if json.loads((folder / 'request.body.bin').read_bytes()) != wire:
        return False
    reservation = read(folder / 'reservation.json')
    metadata = read(folder / 'request-metadata.json')
    if (reservation.get('fingerprint') != row['wire_fingerprint'] or
            reservation.get('state') != 'RESERVED_NOT_RETRYABLE' or
            reservation.get('stream') is not True or metadata.get('retries') != 0 or
            metadata.get('method') != 'POST' or metadata.get('url') != run.c1.BASE_URL + '/responses'):
        return False
    artifacts = recorded.get('artifacts')
    required = {'request-metadata.json', 'request.body.bin', 'request.json',
                'reservation.json', 'trace.json'}
    return (isinstance(artifacts, dict) and set(artifacts) == required and
            {p.name for p in folder.iterdir()} == required | {'result.json'} and
            all((folder / name).is_file() and sha(folder / name) == digest
                for name, digest in artifacts.items()))


def may_continue(result, recorded, cap=30000):
    if (recorded.get('state') != 'COMPLETED' or recorded.get('calls') != 1 or
            recorded.get('http_status') != 200 or recorded.get('retry') is not False or
            recorded.get('returned_model') != run.c1.MODEL or
            not isinstance(recorded.get('message_text'), str) or not recorded['message_text'].strip()):
        return False
    usage = result.get('usage')
    if not isinstance(usage, dict):
        return False
    values = [usage.get(k) for k in ('input_tokens', 'output_tokens', 'total_tokens')]
    if any(type(x) is not int or x < 0 for x in values) or values[0] + values[1] != values[2] or values[1] > cap:
        return False
    detail = usage.get('output_tokens_details')
    if detail is not None:
        if not isinstance(detail, dict):
            return False
        reasoning = detail.get('reasoning_tokens')
        if reasoning is not None and (type(reasoning) is not int or reasoning < 0 or reasoning > values[1]):
            return False
    if result.get('full_stream_status') != 'PASS' or result.get('protocol_warning') or result.get('reasoning_id_conflicts'):
        return False
    if result.get('state') == 'CONTENT_VERIFIED':
        return result.get('validation', {}).get('status') == 'VALIDATED_MECHANICAL_ONLY'
    return (result.get('state') == 'CONTENT_INVALID' and
            result.get('validation', {}).get('status') in CONTENT_FAILURES)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--execute-authorized', action='store_true')
    parser.add_argument('--ack-slots-3-through-48', action='store_true')
    args = parser.parse_args()
    manifest = verify_prior()
    if OUT.exists():
        raise ValueError('Continuation root exists; never replay or resume')
    print(json.dumps({'state': 'EXECUTION_REQUESTED' if args.execute_authorized else 'DRY_RUN',
                      'slots': '3-48', 'count': 46, 'cap': 30000,
                      'max_attempts_per_slot': MAX_ATTEMPTS, 'slot_3_prior_attempts': 1}), flush=True)
    if not args.execute_authorized:
        return
    if not args.ack_slots_3_through_48:
        raise ValueError('Explicit continuation acknowledgment required')
    env = environment()
    key = env.get('JIZHI_API_KEY')
    if not key or env.get('JIZHI_BASE_URL', '').rstrip('/') != run.c1.BASE_URL:
        raise ValueError('Route mismatch or missing credential')
    evidence = read(run.F1 / 'evidence.json')
    schema = read(run.PRE / 'contracts/p05-navi-b-v02.schema.json')
    legacy = guard_validator(run.pre.native_legacy_validator(run.PRE / 'contracts'))
    OUT.mkdir(parents=True, exist_ok=False)
    put(OUT / 'run-config.json', {'version': manifest['version'], 'prior_audit_sha256': PRIOR_AUDIT_SHA,
        'prepared_manifest_sha256': sha(run.PLAN / 'prepared-manifest.json'),
        'order': [r['request_id'] for r in manifest['records'][2:]],
        'slots': '3-48', 'planned_requests': 46, 'cap': 30000,
        'max_attempts_per_slot_including_historical': MAX_ATTEMPTS,
        'historical_slot_3_attempts': 1, 'historical_continuation_audit_sha256': HISTORICAL_AUDIT_SHA,
        'retry_only': 'proven_tcp_or_tls_pre_header_failure', 'continue_on': sorted(CONTENT_FAILURES),
        'human_acceptance': 'NOT_REVIEWED', 'navigation_status': 'INACTIVE'})
    old_out, old_binding = run.OUT, run.pre.validate_bindings
    run.pre.validate_bindings = guard_validator(old_binding)
    try:
        for index, row in enumerate(manifest['records'][2:], start=3):
            first_attempt = 2 if index == 3 else 1
            for attempt in range(first_attempt, MAX_ATTEMPTS + 1):
                attempt_root = OUT / 'attempts' / row['request_id'] / ('attempt-%03d' % attempt)
                attempt_root.mkdir(parents=True, exist_ok=False)
                (attempt_root / 'validation').mkdir(exist_ok=False)
                run.OUT = attempt_root
                print(json.dumps({'dispatch_slot': index, 'request_id': row['request_id'],
                                  'attempt': attempt}), flush=True)
                try:
                    result = run.execute_one(row, 30000, key, evidence, legacy, schema)
                    folder = attempt_root / row['request_id']
                    recorded = read(folder / 'result.json')
                    eligible = proven_pre_send(folder, row, recorded) if result.get('state') == 'NOT_SENT' else False
                    proceed = may_continue(result, recorded)
                except Exception as exc:
                    put(OUT / 'STOP.json', {'slot': index, 'request_id': row['request_id'],
                        'attempt': attempt, 'state': 'LOCAL_PROCESSING_UNKNOWN',
                        'error_type': type(exc).__name__, 'remaining_not_sent': 48-index,
                        'no_retry': True})
                    return
                retry = eligible and attempt < MAX_ATTEMPTS
                put(attempt_root / 'retry-decision.json', {'slot': index, 'attempt': attempt,
                    'state': result['state'], 'pre_send_proven': eligible, 'retry': retry,
                    'request_fingerprint': row['wire_fingerprint'], 'actual_fee': 'UNKNOWN',
                    'result_sha256': sha(folder / 'result.json'),
                    'validation_sha256': sha(attempt_root / 'validation' / (row['request_id'] + '.json')),
                    'prior_attempt_count': attempt-1, 'max_attempts': MAX_ATTEMPTS})
                print(json.dumps({'slot': index, 'attempt': attempt, 'state': result['state'],
                                  'validation': result.get('validation', {}).get('status'),
                                  'retry': retry, 'usage': result.get('usage', 'UNKNOWN')}, ensure_ascii=False), flush=True)
                if proceed:
                    break
                if retry:
                    time.sleep(.5)  # Same bounded delay as qwen_traced.
                    continue
                put(OUT / 'STOP.json', {'slot': index, 'request_id': row['request_id'],
                    'attempt': attempt, 'state': result['state'], 'remaining_not_sent': 48-index,
                    'no_retry': True})
                return
        put(OUT / 'COMPLETE.json', {'attempted_slots': 46,
                                   'human_acceptance': 'NOT_REVIEWED', 'navigation_status': 'INACTIVE'})
    finally:
        run.OUT, run.pre.validate_bindings = old_out, old_binding


if __name__ == '__main__':
    main()
