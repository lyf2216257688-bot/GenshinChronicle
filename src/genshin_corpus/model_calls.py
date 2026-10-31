"""Shared serial model-call recovery, budgets and channel health; no SDK retries.

Adapters own one raw-first attempt and strict task validation. This module owns
logical requests, attempts and decisions, never changes model input or facts.
Policy is not authorization: adapters must still check their live authorization.
"""
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import math
from pathlib import Path
import time

from .evidence_io import copy, digest, inventory, read, require, seal, sha, verify, write

VERSION = 'model-calls-1'


class Deferred(ValueError):
    """No dispatch now; the same task may resume without an identity change."""


@contextmanager
def locked(path):
    """Process lock released by the OS on crash. Never delete another lock file."""
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as handle:
        handle.seek(0, 2)
        if not handle.tell():
            handle.write(b'0'); handle.flush()
        handle.seek(0)
        if __import__('os').name == 'nt':
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            handle.seek(0)
            if __import__('os').name == 'nt':
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


@dataclass(frozen=True)
class Policy:
    max_attempts: int = 3
    unknown_retries: int = 0
    format_retries: int = 0
    cost_risk_authorization: str = ''
    initial_delay: float = 1
    delay_cap: float = 30
    channel_failure_limit: int = 3
    channel_cooldown: float = 60

    def __post_init__(self):
        for k in ('max_attempts', 'unknown_retries', 'format_retries', 'channel_failure_limit'):
            require(type(getattr(self, k)) is int and getattr(self, k) >= 0, 'Invalid retry count')
        require(self.max_attempts > 0 and self.channel_failure_limit > 0, 'Positive attempt/channel limits required')
        for k in ('initial_delay', 'delay_cap', 'channel_cooldown'):
            require(type(getattr(self, k)) in (int, float) and math.isfinite(getattr(self, k))
                    and getattr(self, k) >= 0, 'Invalid retry timing')
        require(not (self.unknown_retries or self.format_retries) or bool(self.cost_risk_authorization.strip()),
                'Charged/uncertain retries require explicit cost risk authorization')


def token_count(result):
    """Only arithmetic-validated provider counts. Never infer zero from failure."""
    if isinstance(result.get('accounting'),dict):
        n=result['accounting']['total_tokens']
        return n if type(n) is int else None
    for u in (result.get('usage'), result.get('provider_reported_usage')):
        if not isinstance(u, dict):
            continue
        a = u.get('input_tokens', u.get('prompt_tokens'))
        b = u.get('output_tokens', u.get('completion_tokens'))
        n = u.get('total_tokens')
        if all(type(v) is int and v >= 0 for v in (a, b, n)) and a+b == n:
            return n
    return None


def classify(result, trace=()):
    """Transport uncertainty and task acceptance are independent dimensions."""
    status = result.get('http_status')
    text = str(result.get('error_codes', []))+' '+str(result.get('failure_type', ''))
    pre_send = (status is None and
                not any('send_request_headers.' in e or 'send_request_body.' in e for e in trace) and
                any(e in {'connection.connect_tcp.failed', 'connection.start_tls.failed'} for e in trace))
    if result.get('state') == 'CONTENT_VERIFIED':
        return {'kind': 'SUCCESS', 'execution': 'COMPLETED', 'channel_failure': False}
    if pre_send:
        kind, execution, channel = 'NOT_SENT', 'PROVEN_NOT_SENT', True
    elif status == 429:
        kind, execution, channel = 'RATE_LIMIT', 'UNKNOWN', True
    elif type(status) is int and 500 <= status <= 599:
        kind, execution, channel = 'HTTP_5XX', 'UNKNOWN', True
    elif type(status) is int and status >= 400:
        kind, execution, channel = 'HTTP_REJECTED', 'REJECTED', status in {401, 403}
    elif (status is None and result.get('content_validation',{}).get('status') not in {'PASS','FAIL'}) or result.get('stream_validation', {}).get('status') == 'FAIL':
        kind, execution, channel = 'TRANSPORT_OR_STREAM', 'UNKNOWN', True
    elif 'TRUNCAT' in text or 'length' in text:
        kind, execution, channel = 'OUTPUT_TRUNCATED', 'COMPLETED', False
    elif any(k in text for k in ('NON_JSON', 'JSON_PARSE', 'JSONDecodeError')):
        kind, execution, channel = 'NON_JSON', 'COMPLETED', False
    else:
        kind, execution, channel = 'CONTRACT_INVALID', 'COMPLETED', False
    return {'kind': kind, 'execution': execution, 'channel_failure': channel}


def summary(results, classifications=None):
    counts = [0 if classifications and classifications[i]['execution']=='PROVEN_NOT_SENT' else token_count(r) for i,r in enumerate(results)]
    unknown = [i+1 for i, n in enumerate(counts) if n is None]
    return {'attempts': sum(r.get('accounting',{}).get('attempts',1) for r in results),
            'external_invocations':sum(r.get('external_invocations',0) for r in results), 'requests': sum(r.get('calls', 0) for r in results),
            'request_total':'UNKNOWN' if any(r.get('request_count')=='UNKNOWN' for r in results) else sum(r.get('calls',0) for r in results),
            'retry_attempts': max(0, sum(r.get('accounting',{}).get('attempts',1) for r in results)-1),
            'unknown_transmission_attempts':sum(r.get('request_count')=='UNKNOWN' for r in results),
            'known_tokens': sum(n if n is not None else r.get('accounting',{}).get('known_tokens',0) for n,r in zip(counts,results)),
            'unknown_usage_attempts': unknown,
            'total_tokens': 'UNKNOWN' if unknown else sum(counts),
            'actual_fee': 'UNKNOWN' if any(r.get('actual_fee', 'UNKNOWN') == 'UNKNOWN' for r in results) else
                [r['actual_fee'] for r in results],
            'provider_usage_observations': [r.get('provider_reported_usage', r.get('usage', 'UNKNOWN')) for r in results]}


def delay(headers, number, policy):
    value = headers.get('retry-after')
    if value is not None:
        try:
            seconds = float(value)
        except (ValueError, TypeError):
            try:
                seconds = (parsedate_to_datetime(value)-datetime.now(timezone.utc)).total_seconds()
            except (ValueError, TypeError, OverflowError):
                seconds = -1
        if math.isfinite(seconds) and seconds >= 0:
            return min(seconds, policy.delay_cap)
    return min(policy.initial_delay*2**min(number-1, 30), policy.delay_cap)


def retry_decision(classification, policy, number, unknown_used, format_used, dedup):
    kind = classification['kind']
    risk = classification['execution'] == 'UNKNOWN' and not dedup
    eligible = kind == 'NOT_SENT'
    if kind in {'RATE_LIMIT', 'HTTP_5XX', 'TRANSPORT_OR_STREAM'}:
        eligible = dedup or unknown_used < policy.unknown_retries
    if kind == 'NON_JSON':
        eligible = format_used < policy.format_retries
    return {'retry': eligible and number < policy.max_attempts,
            'reason': kind, 'duplicate_execution_risk': risk,
            'deduplication': 'VERIFIED' if dedup else 'NOT_VERIFIED',
            'authorization': policy.cost_risk_authorization or None}


class Batch:
    """Shared persistent attempt budget. Unknowns consume reservations, not zero.

    Independent tasks can continue while finite reservations fit. This is a
    conservative operating budget, never a tokenizer guarantee or an invoice.
    """
    def __init__(self, root, *, max_requests, max_tokens=None, dispatch_limit=None):
        self.root = Path(root)
        require(dispatch_limit is None or type(dispatch_limit) is int and dispatch_limit>0,'Invalid invocation request bound')
        self.dispatch_limit=dispatch_limit
        self._outcome_cache={}
        require(type(max_requests) is int and max_requests > 0, 'Finite request budget required')
        require(max_tokens is None or type(max_tokens) is int and max_tokens > 0, 'Invalid token budget')
        config = {'version': VERSION, 'max_requests': max_requests, 'max_tokens': max_tokens}
        with locked(self.root/'budget.lock'):
            path = self.root/'config.json'
            if path.exists():
                require(read(path) == config, 'Batch budget changed; use an explicit new campaign')
            else:
                write(path, config)
        self.config = config
        self.window_start=len(list((self.root/'requests').glob('*/reservation.json')))

    def _outcome(self, reservation):
        path = reservation.parent/'outcome.json'
        if path in self._outcome_cache:return self._outcome_cache[path]
        if not path.exists():
            return {'observed_tokens': None, 'request_observations': None,
                    'external_invocations': 0, 'actual_fee': 'UNKNOWN'}
        value = read(path)
        attempt = Path(read(reservation)['attempt'])
        verify(attempt)
        require(value['attempt_complete_sha256'] == sha(attempt/'complete.json') and
                value['observed_tokens'] == token_count(read(attempt/'result.json')),
                'Budget outcome differs from sealed raw-attempt result')
        self._outcome_cache[path]=value
        return value

    def reserve(self, fingerprint, attempt, allowance):
        require(type(allowance) is int and allowance > 0, 'Positive conservative token allowance required')
        with locked(self.root/'budget.lock'):
            slots = sorted((self.root/'requests').glob('*/reservation.json'))
            if self.dispatch_limit is not None and len(slots)-self.window_start>=self.dispatch_limit:raise Deferred('INVOCATION_REQUEST_BUDGET_DEFERRED')
            if len(slots)>=self.config['max_requests']:raise Deferred('BATCH_REQUEST_BUDGET_DEFERRED')
            charged = 0
            for p in slots:
                reserved = read(p)
                n = self._outcome(p)['observed_tokens']
                charged += reserved['token_allowance'] if n is None else n
            cap = self.config['max_tokens']
            if cap is not None and charged+allowance>cap:raise Deferred('BATCH_TOKEN_BUDGET_DEFERRED')
            slot = self.root/'requests'/f'{len(slots)+1:06d}'
            write(slot/'reservation.json', {'fingerprint': fingerprint, 'attempt': str(Path(attempt).resolve()),
                   'token_allowance': allowance, 'usage': 'UNKNOWN', 'actual_fee': 'UNKNOWN'})
            return slot

    def finish(self, slot, result, attempt):
        write(Path(slot)/'outcome.json', {'observed_tokens': token_count(result),
              'attempt_complete_sha256': sha(Path(attempt)/'complete.json'),
              'request_observations': None if result.get('request_count') == 'UNKNOWN' else result.get('calls'),
              'external_invocations': result.get('external_invocations', 0),
              'actual_fee': result.get('actual_fee', 'UNKNOWN')})

    def accounting(self):
        rows = []
        for p in sorted((self.root/'requests').glob('*/reservation.json')):
            rows.append(self._outcome(p))
        unknown = sum(r['observed_tokens'] is None for r in rows)
        known = sum(r['observed_tokens'] or 0 for r in rows)
        known_requests = sum(r['request_observations'] or 0 for r in rows)
        unknown_requests = sum(r['request_observations'] is None for r in rows)
        return {'request_reservations': len(rows),'new_request_reservations':len(rows)-self.window_start, 'known_tokens': known,
                'known_api_request_observations': known_requests,
                'unknown_request_counts': unknown_requests,
                'api_requests_total': 'UNKNOWN' if unknown_requests else known_requests,
                'external_invocations': sum(r['external_invocations'] for r in rows),
                'unknown_usage_requests': unknown, 'total_tokens': 'UNKNOWN' if unknown else known,
                'actual_fee': 'UNKNOWN'}


def _channel_open(ledger, channel, policy):
    rows = sorted((Path(ledger)/'.channel'/digest(channel)).glob('*.json'))
    streak = []
    for p in reversed(rows):
        r = read(p)
        if not r['channel_failure']:
            break
        streak.append(r)
    return (len(streak) >= policy.channel_failure_limit and
            time.time()-streak[0]['time'] < policy.channel_cooldown)


def _channel_record(ledger, channel, classification, attempt):
    ns=(Path(attempt)/'complete.json').stat().st_mtime_ns
    path=Path(ledger)/'.channel'/digest(channel)/(f'{ns:020d}-'+digest(str(Path(attempt).resolve()))[:16]+'.json')
    value=classification | {'time':ns/1e9,'attempt':str(Path(attempt).resolve()),'attempt_complete_sha256':sha(Path(attempt)/'complete.json')}
    if path.exists():require(read(path)==value,'Channel evidence changed')
    else:write(path,value)


def run(root, plan, ledger, send_once, *, channel, policy=None, batch=None,
        token_allowance=1, deduplication=None, sleep=time.sleep, legacy_origin=None):
    """Run/recover a logical call without overwriting any attempt or reservation.

    send_once(attempt_path, idempotency_headers) must seal raw artifacts and
    result.json even after SDK failure; it MUST disable its own retries. A
    verified dedup contract must cover this exact route and byte-identical body.
    Legacy recovery needs an explicit original sealed archive, not a new hash.
    """
    root, ledger = Path(root), Path(ledger)
    policy = policy or Policy()
    fp = plan['fingerprint']; claim = ledger/fp
    deduplication = deduplication or {'status': 'NOT_VERIFIED'}
    dedup = deduplication.get('status') == 'VERIFIED'
    require(not dedup or (deduplication.get('channel') == channel and
            deduplication.get('contract_evidence_sha256') and deduplication.get('header') == 'Idempotency-Key'),
            'Verified idempotency requires a route-bound provider contract')
    origin_snapshot=None
    if legacy_origin:
        origin=Path(legacy_origin)
        require(not root.resolve().is_relative_to(origin.resolve()) and not origin.resolve().is_relative_to(root.resolve()),'Recovery must not modify origin tree')
        if (origin/'complete.json').exists():verify(origin)
        origin_snapshot=inventory(origin)
    identity = {'version': VERSION, 'plan': plan, 'policy': asdict(policy),
                'deduplication': deduplication, 'channel': channel,
                'shared_code': code_identity(), 'legacy_origin': str(Path(legacy_origin).resolve()) if legacy_origin else None,
                'legacy_artifacts':origin_snapshot}
    # Old completed archives stay readable, including their original result schema.
    if (root/'complete.json').exists():
        verify(root); require(read(root/'plan.json') == plan, 'Completed request identity changed')
        if (root/'call.json').exists():
            require(read(root/'call.json') == identity, 'Completed call policy/code changed')
        return read(root/'result.json')
    with locked(ledger/'.locks'/fp):
        if (root/'call.json').exists():
            require(read(root/'call.json') == identity, 'Resume request/policy/code changed')
            require(read(claim/'reservation.json')['root'] == str(Path(legacy_origin).resolve() if legacy_origin else root.resolve()),
                    'Request belongs to another run')
        else:
            require(not root.exists(), 'Legacy pending request requires explicit recovery, never implicit resend')
            if legacy_origin:
                origin = Path(legacy_origin)
                require(inventory(origin)==origin_snapshot,'Recovery origin artifacts changed')
                require(read(origin/'plan.json') == plan, 'Recovery origin identity differs')
                require(read(claim/'reservation.json')['root'] == str(origin.resolve()), 'Legacy ledger origin differs')
                require(not list((claim/'recoveries').glob('*/reservation.json')), 'Existing recovery: resume that root, not a second recovery')
                recovery = claim/'recoveries'/'0001'
                write(recovery/'reservation.json', {'root': str(root.resolve()), 'origin_artifacts':origin_snapshot})
            else:
                reservation={'fingerprint':fp,'root':str(root.resolve()),'state':'SHARED_MANAGED_NO_UNRECORDED_RESEND','call_identity':digest(identity)}
                if (claim/'reservation.json').exists():
                    if read(claim/'reservation.json')!=reservation:raise FileExistsError('Fingerprint already reserved by another operation')
                else:
                    claim.mkdir(parents=True, exist_ok=False)
                    write(claim/'reservation.json',reservation)
            write(root/'call.json', identity); write(root/'plan.json', plan)
        results, classifications = [], []
        if legacy_origin:
            origin = Path(legacy_origin)
            require(inventory(origin)==origin_snapshot,'Recovery origin artifacts changed')
            result = read(origin/'result.json') if (origin/'complete.json').exists() else {
                'state':'UNKNOWN','usage':'UNKNOWN','calls':0,'request_count':'UNKNOWN','actual_fee':'UNKNOWN',
                'partial_artifacts':origin_snapshot}
            trace = read(origin/'trace.json').get('events', []) if (origin/'trace.json').exists() else []
            results.append(result); classifications.append(classify(result, trace))
        attempts = root/'attempts'
        if batch:
            for slot in (batch.root/'requests').glob('*/reservation.json'):
                reserved=read(slot);p=Path(reserved['attempt'])
                if p.parent==attempts.resolve():
                    dispatch=root/'dispatches'/(p.name+'.json')
                    if not dispatch.exists():write(dispatch,{'attempt':p.name,'batch_slot':str(slot.parent.resolve()),'usage':'UNKNOWN'})
        # Reconcile a crash after dispatch reservation but before directory creation.
        for dispatch in sorted((root/'dispatches').glob('*.json')):
            p=attempts/dispatch.stem
            if not p.exists():p.mkdir(parents=True)
        for p in sorted(attempts.glob('*')):
            if (p/'complete.json').exists():
                verify(p); result = read(p/'result.json')
                trace = read(p/'trace.json').get('events', []) if (p/'trace.json').exists() else []
                state = classify(result, trace)
                _channel_record(ledger,channel,state,p)
                dispatch=root/'dispatches'/(p.name+'.json')
                if batch and dispatch.exists():
                    slot=read(dispatch)['batch_slot']
                    if slot and not (Path(slot)/'outcome.json').exists():batch.finish(slot,result,p)
            else:
                # No proof of non-transmission: retain partial artifacts, consume risk.
                result = {'state': 'UNKNOWN', 'usage': 'UNKNOWN', 'calls': 0,
                          'request_count': 'UNKNOWN', 'actual_fee': 'UNKNOWN', 'partial_artifacts': inventory(p)}
                state = {'kind': 'TRANSPORT_OR_STREAM', 'execution': 'UNKNOWN', 'channel_failure': True}
            results.append(result); classifications.append(state)
        selected = None
        while True:
            if results:
                last = classifications[-1]
                unknown_used = sum(c['execution'] == 'UNKNOWN' for c in classifications[:-1])
                format_used = sum(c['kind'] == 'NON_JSON' for c in classifications[:-1])
                decision = retry_decision(last, policy, len(results), unknown_used, format_used, dedup)
                path = root/'decisions'/f'{len(results):04d}.json'
                if path.exists():
                    saved = read(path); require(all(saved[k] == v for k, v in decision.items()), 'Retry decision changed')
                else:
                    headers = {}
                    prior = attempts/f'{len(results)-(1 if legacy_origin else 0):04d}'
                    if (prior/'http.json').exists():
                        headers = read(prior/'http.json')['headers']
                    saved = decision | {'not_before': time.time()+delay(headers, len(results), policy)}
                    write(path, saved)
                if not decision['retry']:
                    break
            if _channel_open(ledger, channel, policy):
                # Do not finalize: cooldown permits a later resume without policy mutation.
                raise Deferred('CHANNEL_COOLDOWN_DEFERRED')
            if results:
                sleep(max(0, min(saved['not_before']-time.time(), policy.delay_cap)))
            folder = attempts/f'{len(results)+1-(1 if legacy_origin else 0):04d}'
            slot = batch.reserve(fp, folder, token_allowance) if batch else None
            # A pre-send durable reservation covers crashes before/inside the adapter.
            write(root/'dispatches'/(folder.name+'.json'), {'attempt': folder.name,
                  'batch_slot': str(slot.resolve()) if slot else None, 'usage': 'UNKNOWN'})
            folder.mkdir(parents=True, exist_ok=False)
            result = send_once(folder, {'Idempotency-Key': fp} if dedup else {})
            verify(folder); require(read(folder/'result.json') == result, 'Attempt result differs')
            if batch:
                batch.finish(slot, result, folder)
            trace = read(folder/'trace.json').get('events', []) if (folder/'trace.json').exists() else []
            classification = classify(result, trace)
            _channel_record(ledger, channel, classification, folder)
            results.append(result); classifications.append(classification); selected = folder
        if selected is None:
            complete = [p for p in sorted(attempts.glob('*')) if (p/'complete.json').exists()]
            if complete and classifications[-1]['kind'] != 'TRANSPORT_OR_STREAM':
                selected = complete[-1]
            elif legacy_origin and len(results) == 1 and (Path(legacy_origin)/'complete.json').exists():
                selected = Path(legacy_origin)
        # Compatibility projection: originals remain immutable inside attempts.
        if selected:
            for p in selected.iterdir():
                if p.is_file() and p.name not in {'plan.json', 'result.json', 'complete.json', 'call.json'}:
                    target = root/p.name
                    if target.exists():
                        require(sha(target) == sha(p), 'Selected projection differs')
                    else:
                        copy(p, target)
        account = summary(results, classifications)
        final = results[-1] | {'calls': account['requests'], 'accounting': account,
                'attempt_classifications': classifications, 'automatic_retry': len(results) > 1,
                'navigation_status': 'INACTIVE', 'human_acceptance': 'NOT_REVIEWED',
                'actual_fee': account['actual_fee']}
        if account['total_tokens'] != 'UNKNOWN':
            final['conservative_accounting_tokens'] = account['total_tokens']
        else:
            final.pop('conservative_accounting_tokens', None)
        if (root/'result.json').exists():require(read(root/'result.json')==final,'Interrupted final result differs')
        else:write(root/'result.json', final)
        seal(root)
        outcome = {'root_complete_sha256': sha(root/'complete.json'), 'state': final['state']}
        target = claim/'recoveries'/'0001' if legacy_origin else claim
        write(target/'outcome.json', outcome)
        return final


def code_identity():
    return {p.name: sha(p) for p in (Path(__file__), Path(__file__).with_name('evidence_io.py'))}


def snapshot(target):
    for name in code_identity():
        copy(Path(__file__).with_name(name), Path(target)/name)


def main():
    import argparse
    import json
    parser = argparse.ArgumentParser(description='Read-only model request diagnosis; sends no requests')
    parser.add_argument('command', choices=['inspect'])
    parser.add_argument('--request', required=True, type=Path)
    args = parser.parse_args(); verify(args.request)
    result = read(args.request/'result.json')
    trace = read(args.request/'trace.json')['events'] if (args.request/'trace.json').exists() else []
    print(json.dumps({'classification': classify(result, trace), 'accounting': summary([result]),
                      'new_calls': 0, 'origin_complete_sha256': sha(args.request/'complete.json')}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
