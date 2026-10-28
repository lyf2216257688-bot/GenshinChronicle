"""Bounded F2 baseline execution in frozen order; no probes or retries.

Run only under the user's explicit F2 authorization. Raw streams, incomplete
requests and original probes remain append-only. This does not publish links.
"""
import hashlib
import json
from pathlib import Path
import time

from genshin_corpus.rag.navi_r1_experiment import environment
from . import navi_f2_c1 as c1
from . import navi_f2_preflight as pre
from . import navi_f2_sdk_transport as sdk
from .navi_f1_prepare import put, read, sha, value_hash

PRE = Path('data/retrieval/p05-navi-f2-preflight-20261026-r1')
C1 = Path('data/retrieval/p05-navi-f2-c1-20261027-r1')
F1 = Path('data/retrieval/p05-navi-f1-20261026-r2')
REPAIR = Path('data/retrieval/p05-navi-f2-sdk-repair-20261027-r1')
PROBES = [Path('data/retrieval/p05-navi-f2-live-20261027-r1'),
          Path('data/retrieval/p05-navi-f2-live-20261027-r2')]
OUT = Path('data/retrieval/p05-navi-f2-experiment-20261027-r1')


def verify_seal(root):
    seal = read(root/'freeze-manifest.json')
    for name, item in seal['files'].items():
        path = root/name
        if not path.is_file() or sha(path) != item['sha256']:
            raise ValueError('Protected artifact changed: '+str(path))
    files = {str(p.relative_to(root)) for p in root.rglob('*')
             if p.is_file() and p.name != 'freeze-manifest.json'}
    if files != set(seal['files']):
        raise ValueError('Protected artifact set changed: '+str(root))
    return seal


def main():
    pre.verify_preflight(PRE)
    repair = verify_seal(REPAIR)
    for path in ['src/genshin_corpus/retrieval/navi_f2_sdk_transport.py',
                 'src/genshin_corpus/retrieval/navi_f2_c1.py']:
        # C1 predates the repair seal; it is verified by the C1 seal and tests.
        if path in repair.get('repository_files', {}) and sha(Path(path)) != repair['repository_files'][path]:
            raise ValueError('Adapter changed since offline verification')
    for path in PROBES:
        verify_seal(path)
    status = read(PROBES[1]/'canary-01/terminal.json')
    if status['status'] != 'EXECUTION_OR_STREAM_UNKNOWN':
        raise ValueError('Prior UNKNOWN state differs from recorded evidence')
    base_slots = read(PROBES[0]/'base-slots.json')
    if base_slots['planned'] != 48 or any(x['status'] != 'NOT_SENT' for x in base_slots['slots']):
        raise ValueError('Base slot state changed')
    manifest = read(PRE/'base-request-manifest.json')
    order = manifest['order']; rows = {x['request_id']: x for x in manifest['requests']}
    if len(order) != 48 or len(set(order)) != 48 or set(order) != set(rows):
        raise ValueError('Frozen denominator or order changed')
    quarantined = [read(path/'canary-01/request.json') for path in PROBES]
    cases = [read(PRE/'requests'/(rid+'.json')) for rid in order]
    fingerprints = [value_hash(c1.render_stream(case, sdk.CAP)) for case in cases]
    if len(set(fingerprints)) != 48 or set(fingerprints) & {value_hash(w) for w in quarantined}:
        raise ValueError('Duplicate or quarantined request content')
    env = environment()
    if not env.get('JIZHI_API_KEY') or env.get('JIZHI_BASE_URL', '').rstrip('/') != c1.BASE_URL:
        raise ValueError('Credential missing or unexpected route')
    snapshot_path = C1/'stream-checker.snapshot.py'
    c1_seal = verify_seal(C1)
    snapshot = snapshot_path.read_text(encoding='utf8')
    # The sealed file is verified byte-for-byte above; text-mode newline
    # normalization on Windows changes the hash of the source string.
    checker = c1.frozen_stream_checker(snapshot, hashlib.sha256(snapshot.encode('utf8')).hexdigest())
    if not OUT.exists():
        OUT.mkdir(parents=True, exist_ok=False)
        put(OUT/'run-config.json', {'stage':'F2_BASELINE_AUTHORIZED','model':c1.MODEL,
            'route':'jizhi','base_url':c1.BASE_URL,'api':'responses','stream':True,
            'max_output_tokens':30000,'generation_parameters':{},'proxy':None,
            'trust_env':False,'sdk_retries':0,'transport_retries':0,
            'fee_cap':None,'planned_base':48,'planned_targeted_max':8,
            'prior_probe_unknown_retained':True,'new_probe_calls':0,
            'preflight_manifest_sha256':sha(PRE/'base-request-manifest.json'),
            'f1_seal_sha256':sha(F1/'freeze-manifest.json'),
            'repair_seal_sha256':sha(REPAIR/'freeze-manifest.json'),
            'prior_probe_seals':[sha(p/'freeze-manifest.json') for p in PROBES],
            'order':order, 'fingerprints':fingerprints,
            'human_acceptance':'NOT_REVIEWED','navigation_status':'INACTIVE'})
    else:
        config = read(OUT/'run-config.json')
        if (config['order'] != order or config['fingerprints'] != fingerprints or
            config['preflight_manifest_sha256'] != sha(PRE/'base-request-manifest.json') or
            config['repair_seal_sha256'] != sha(REPAIR/'freeze-manifest.json')):
            raise ValueError('Resume configuration changed')
    sdk.safe_resume(OUT)
    reservations = sorted((OUT/'reservations').glob('*.json')) if (OUT/'reservations').exists() else []
    issued = {read(p)['fingerprint'] for p in reservations}
    if issued != set(fingerprints[:len(issued)]):
        raise ValueError('Issued identities are not the frozen prefix')
    schema = read(PRE/'contracts/p05-navi-b-v02.schema.json')
    legacy = pre.native_legacy_validator(PRE/'contracts')
    evidence = read(F1/'evidence.json')
    for index, (rid, case, fingerprint) in enumerate(zip(order, cases, fingerprints),1):
        if fingerprint in issued:
            terminal = read(OUT/'attempts'/fingerprint/'terminal.json')
        else:
            print(json.dumps({'event':'DISPATCH','slot':index,'total':48,'request_id':rid}),flush=True)
            terminal = sdk.run_case(case, OUT, api_key=env['JIZHI_API_KEY'], proxy=None,
                checker=checker, quarantined_wires=quarantined, send_authorized=True)
            print(json.dumps({'event':'RESULT','slot':index,'status':terminal['status'],
                'http_status':terminal.get('http_status'),'usage':terminal['usage']}),flush=True)
        if terminal['status'] != 'COMPLETE_STREAM_VALIDATED':
            put(OUT/'STOP.json', {'slot':index,'request_id':rid,'status':terminal['status'],
                'http_status':terminal.get('http_status'),'usage':terminal['usage'],
                'remaining_not_sent':48-index,'actual_fee':'UNKNOWN', 'automatic_retry':False})
            return
        analysis_path=OUT/'mechanical-validation'/(rid+'.json')
        if not analysis_path.exists():
            packet=read(F1/'deliveries'/(case['pair_id']+'.json'))
            output=read(OUT/'attempts'/fingerprint/'output.json')
            try:
                verdict=pre.validate_content(output['text'],case,packet,evidence,schema,legacy)
            except Exception as exc:
                verdict={'status':'VALIDATOR_EXCEPTION','type':type(exc).__name__}
            put(analysis_path,verdict | {'usage':terminal['usage'],'actual_fee':'UNKNOWN',
                'authority':'PROGRAM_MECHANICAL_ONLY','human_acceptance':'NOT_REVIEWED',
                'navigation_status':'INACTIVE'})
            print(json.dumps({'event':'VALIDATION','slot':index,'status':verdict['status']}),flush=True)
            if verdict['status'] in {'VALIDATOR_EXCEPTION','LOCAL_VALIDATOR_UNAVAILABLE'}:
                put(OUT/'STOP.json',{'slot':index,'request_id':rid,'status':verdict['status'],
                    'remaining_not_sent':48-index,'automatic_retry':False})
                return
    put(OUT/'BASE_COMPLETE.json',{'completed':48,'targeted_calls':0,'actual_fee':'UNKNOWN',
        'human_acceptance':'NOT_REVIEWED','navigation_status':'INACTIVE','completed_epoch':time.time()})
    print('BASE_COMPLETE 48',flush=True)


if __name__ == '__main__':
    main()
