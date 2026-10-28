"""Offline F2 request construction and response replay. No sending/credential seam."""
import argparse
import ast
from collections import Counter
from copy import deepcopy
from hashlib import sha256
import json
from math import isfinite
from math import ceil
from pathlib import Path
import re
from types import SimpleNamespace

from .navi_f1_prepare import read, put, sha, value_hash, verify_frozen
from .navi_v02_contract import validate_bindings

ORDER_NAMESPACE = 'navi-v02-f2-order-1'
ARMS = ('legacy', 'rc1')
FORBIDDEN_INPUT = {'risk_tags', 'primary_stratum', 'probe_type', 'construction_origin',
                   'expected_grade', 'grade_recommendation', 'model_grade', 'model_verdict',
                   'verdict', 'human_acceptance', 'navigation_status', 'basis_evidence',
                   'historical_grade', 'review_disposition', 'previous_response_id',
                   'conversation', 'messages', 'reasoning_cache'}
PROFILE = {  # Planning illustrations only; never copied into wire parameters.
    'legacy_generation': {'typical': 1600, 'ceiling': 8192},
    'legacy_review': {'typical': 1600, 'ceiling': 8192},
    'rc1_generation': {'typical': 3200, 'ceiling': 16384},
    'rc1_review': {'typical': 4800, 'ceiling': 24576},
}


def dumps(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def strict_json(text):
    def pairs(items):
        value = {}
        for k, v in items:
            if k in value:
                raise ValueError('Duplicate JSON key: '+k)
            value[k] = v
        return value
    return json.loads(text, object_pairs_hook=pairs,
                      parse_constant=lambda v: (_ for _ in ()).throw(ValueError('Nonfinite JSON: '+v)))


def check_view(view, mode, arm):
    expected = {'A', 'B'} | ({'candidate' if arm == 'legacy' else 'candidates'} if mode == 'review' else set())
    if set(view) != expected:
        raise ValueError('Unexpected input fields')
    def walk(x):
        if isinstance(x, dict):
            if set(x) & FORBIDDEN_INPUT:
                raise ValueError('Reviewer/history/session metadata leaked')
            for v in x.values(): walk(v)
        elif isinstance(x, list):
            for v in x: walk(v)
    walk(view)
    if mode == 'review':
        if arm == 'legacy':
            if set(view['candidate']) != {'candidate_id', 'proposition', 'interpretation_boundary'}:
                raise ValueError('Legacy candidate envelope')
        else:
            for c in view['candidates']:
                if set(c) != {'id', 'text', 'role', 'text_sha256'}:
                    raise ValueError('rc1 candidate envelope')
                if sha256(c['text'].encode()).hexdigest() != c['text_sha256']:
                    raise ValueError('Original hash changed')


def prompt_parts(snapshot, arm, mode):
    if arm == 'legacy':
        return {'native_prompt': read(snapshot/'effective-legacy.json')[
            'generation' if mode == 'generation' else 'audit']}
    text = (snapshot/'p05-navi-b-prompts-v02.md').read_text(encoding='utf-8')
    blocks = re.findall(r'```text\n(.*?)\n```', text, re.S)
    templates = re.findall(r'```json\n(.*?)\n```', text, re.S)
    if len(blocks) != 3 or len(templates) != 1:
        raise ValueError('Frozen prompt block structure changed')
    return {'native_prompt': blocks[0]+'\n'+blocks[1 if mode == 'generation' else 2],
            'complete_template': '\n完整字段模板：\n'+dumps(strict_json(templates[0])),
            'complete_schema': '\n完整JSON Schema（必须满足）：\n'+dumps(read(snapshot/'p05-navi-b-v02.schema.json'))}


def compile_case(pair, arm, view, parts, input_hash, schema_hash):
    mode = 'generation' if pair['cohort'] == 'GENERATION' else 'review'
    check_view(view, mode, arm)
    system = ''.join(parts.values())
    # Exactly two messages, rebuilt per case. No history, tools, mother or other output.
    messages = [{'role': 'system', 'content': system},
                {'role': 'user', 'content': json.dumps(view, ensure_ascii=False)}]
    original = None
    if mode == 'review':
        original = (view['candidate']['proposition'] if arm == 'legacy' else view['candidates'][0]['text'])
    return {'request_id': pair['pair_id']+'-'+arm, 'pair_id': pair['pair_id'], 'arm': arm,
            'mode': mode, 'source_ids': dict(zip(['A', 'B'], pair['sources'])),
            'source_payload_sha256': value_hash({s: view[s] for s in ['A', 'B']}),
            'f1_view_file_sha256': input_hash,
            'original_text_sha256': sha256(original.encode()).hexdigest() if original is not None else None,
            'messages': messages, 'messages_sha256': value_hash(messages),
            'prompt_sha256': sha256(system.encode()).hexdigest(),
            'contract': 'G1-v0.1-native' if arm == 'legacy' else 'p05-navi-b-v0.2-rc1',
            'schema_sha256': schema_hash,
            'model': None, 'route': None, 'parameters': None, 'send_authorized': False}


def request_order(pairs, seed):
    ordered = sorted(pairs, key=lambda p: (sha256((ORDER_NAMESPACE+'|'+seed+'|'+p['pair_id']).encode()).hexdigest(), p['pair_id']))
    counters, result = Counter(), []
    for p in ordered:
        cohort = p['cohort']; first = ARMS[counters[cohort] % 2]; counters[cohort] += 1
        for arm in (first, ARMS[1-ARMS.index(first)]):
            result.append(p['pair_id']+'-'+arm)
    return result


def measure(case, parts):
    components = {k: {'characters': len(v), 'utf8_bytes': len(v.encode())} for k, v in parts.items()}
    user = case['messages'][1]['content']
    components['user_payload'] = {'characters': len(user), 'utf8_bytes': len(user.encode())}
    text_bytes = sum(x['utf8_bytes'] for x in components.values())
    return {'request_id': case['request_id'], 'pair_id': case['pair_id'], 'arm': case['arm'], 'mode': case['mode'],
            'components': components, 'content_characters': sum(x['characters'] for x in components.values()),
            'content_utf8_bytes': text_bytes, 'messages_json_utf8_bytes': len(dumps(case['messages']).encode()),
            'actual_model_tokens': None, 'api_framing_tokens': None,
            'proxy_bytes_div4': ceil(text_bytes/4), 'proxy_bytes_div2': ceil(text_bytes/2),
            'proxy_bytes_div1': text_bytes, 'proxy_authority': 'ILLUSTRATIVE_NOT_TOKENIZER_OR_BILLING',
            'output_planning_not_approved': PROFILE[case['arm']+'_'+case['mode']]}


def priced_cost(tokens, prices, *, reasoning_in_output):
    """Replaceable prices per million tokens; tool price per call. No defaults."""
    keys = ('input', 'cached_input', 'output', 'reasoning', 'tool_calls')
    price_keys = ('P_in', 'P_cache', 'P_out', 'P_reason', 'P_tool', 'other_fees')
    if any(tokens.get(k) is None for k in keys) or any(prices.get(k) is None for k in price_keys) or reasoning_in_output not in {True, False}:
        return {'status': 'UNKNOWN', 'amount': None}
    if any(type(v) not in {int, float} or not isfinite(v) or v < 0 for v in [tokens[k] for k in keys]+[prices[k] for k in price_keys]):
        raise ValueError('Invalid budget quantity')
    if tokens['cached_input'] > tokens['input']: raise ValueError('Cached input exceeds total')
    amount = ((tokens['input']-tokens['cached_input'])*prices['P_in'] +
              tokens['cached_input']*prices['P_cache'] + tokens['output']*prices['P_out'] +
              (0 if reasoning_in_output else tokens['reasoning']*prices['P_reason']))/1_000_000
    amount += tokens['tool_calls']*prices['P_tool'] + prices['other_fees']
    return {'status': 'CALCULATED_FROM_SUPPLIED_VALUES_NOT_INVOICE', 'amount': amount}


def render_wire(case, runtime):
    """Pure renderer, never sends. Explicit hypothetical/user config is mandatory.

    Supported non-streaming wire shapes derive from existing repository contracts.
    Provider/model support, exact counting and execution authority are separate gates.
    """
    if value_hash(case['messages']) != case['messages_sha256']:
        raise ValueError('Message bytes changed')
    required = {'model', 'route', 'api_surface', 'max_output_tokens', 'generation_parameters', 'capabilities_confirmed'}
    if not required <= set(runtime) or not runtime['model'] or not runtime['route']:
        raise ValueError('Unresolved runtime configuration')
    if runtime['capabilities_confirmed'] is not True:
        raise ValueError('Provider capability unverified')
    cap = runtime['max_output_tokens']
    if type(cap) is not int or cap <= 0:
        raise ValueError('Missing explicit output cap')
    params = runtime['generation_parameters']
    # Keep provider-specific extensions out until their mapping has been validated.
    if set(params) - {'temperature', 'top_p'}:
        raise ValueError('Unverified runtime parameter')
    common = {'model': runtime['model'], 'stream': False, **params}
    if runtime['api_surface'] == 'responses':
        wire = common | {'instructions': case['messages'][0]['content'],
                         'input': case['messages'][1]['content'], 'max_output_tokens': cap}
    elif runtime['api_surface'] == 'chat_completions':
        wire = common | {'messages': deepcopy(case['messages']), 'max_tokens': cap}
    else:
        raise ValueError('Unsupported API surface')
    return {'wire': wire, 'route': runtime['route'], 'send_authorized': False,
            'format_strategy': 'complete_prompt_template_and_schema' if case['arm'] == 'rc1' else 'native_complete_prompt'}


def native_legacy_validator(contracts):
    """Execute only the two snapshotted pure validators, never import old runner."""
    source = (contracts/'legacy-native-validators.py').read_text(encoding='utf-8')
    tree = ast.parse(source)
    if len(tree.body) != 2 or any(not isinstance(n, ast.FunctionDef) for n in tree.body):
        raise ValueError('Unexpected native validator snapshot')
    roles = {'game_text', 'in_world_literature', 'attributed_testimony', 'editorial_compilation', 'secondary_analysis', 'gameplay', 'unknown'}
    namespace = {'sha256': sha256, 'ROLES': roles}
    exec(compile(ast.Module(body=[tree.body[0]], type_ignores=[]), '<frozen-legacy-qa-validate>', 'exec'), namespace)
    namespace['qa'] = SimpleNamespace(validate=namespace['validate'])
    exec(compile(ast.Module(body=[tree.body[1]], type_ignores=[]), '<frozen-legacy-new-review>', 'exec'), namespace)
    return namespace['validate_new_review']


def validate_content(text, case, packet, evidence, schema, legacy_validator):
    try:
        value = strict_json(text)
    except (ValueError, TypeError) as exc:
        return {'status': 'JSON_PARSE_FAILURE', 'error': str(exc)}
    if case['arm'] == 'legacy':
        try:
            native = legacy_validator(value, packet['aliases'], evidence, packet['sides'])
        except (ValueError, TypeError, KeyError) as exc:
            return {'status': 'LEGACY_NATIVE_CONTRACT_FAILURE', 'parsed': value, 'error': str(exc)}
        return {'status': 'VALIDATED_MECHANICAL_ONLY', 'parsed': value,
                'native_mechanical': native['mechanical_quote_validation'],
                'original_assessment': 'NOT_ASSESSED_BY_MACHINE_SINGLE_SAFE_CLAIM_INTERFACE',
                'human_acceptance': 'NOT_REVIEWED', 'navigation_status': 'INACTIVE'}
    try:
        from jsonschema import Draft202012Validator
    except ImportError:
        return {'status': 'LOCAL_VALIDATOR_UNAVAILABLE', 'parsed': value}
    errors = list(Draft202012Validator(schema).iter_errors(value))
    if errors:
        return {'status': 'SCHEMA_FAILURE', 'parsed': value, 'errors': [e.message for e in errors]}
    view = strict_json(case['messages'][1]['content'])
    delivery = {s: [q['id'] for q in view[s]['snippets']] for s in ['A', 'B']}
    try:
        binding = validate_bindings(value, delivery, view.get('candidates', []), mode=case['mode'])
    except (ValueError, TypeError, KeyError) as exc:
        return {'status': 'CONTEXT_CONTRACT_FAILURE', 'parsed': value, 'error': str(exc)}
    return {'status': 'VALIDATED_MECHANICAL_ONLY', 'parsed': value, 'binding': binding}


def decode_response(raw, surface):
    """Non-streaming response adapter. Raw bytes are preserved before calling this."""
    try:
        body = strict_json(raw.decode('utf-8'))
        if not isinstance(body, dict): raise ValueError('Not an envelope object')
        usage, model = body.get('usage'), body.get('model')
        if surface == 'chat_completions':
            choices = body.get('choices', [])
            if len(choices) != 1: raise ValueError('Ambiguous/missing choices')
            choice = choices[0]; message = choice['message']; finish = choice.get('finish_reason')
            if finish == 'length': return {'status': 'OUTPUT_TRUNCATED', 'usage': usage, 'model': model}
            if message.get('refusal') or finish == 'content_filter':
                return {'status': 'PROVIDER_REFUSAL', 'usage': usage, 'model': model}
            if finish != 'stop': raise ValueError('Unverified finish reason')
            text = message['content']
            if message.get('role') != 'assistant' or not isinstance(text, str): raise ValueError('Unknown message form')
        elif surface == 'responses':
            if body.get('status') == 'incomplete':
                reason = body.get('incomplete_details', {}).get('reason')
                return {'status': 'OUTPUT_TRUNCATED' if reason == 'max_output_tokens' else 'RESPONSE_INCOMPLETE',
                        'usage': usage, 'model': model}
            if body.get('status') != 'completed': raise ValueError('Not a completed response')
            messages = [x for x in body.get('output', []) if x.get('type') == 'message']
            if len(messages) != 1 or messages[0].get('role') != 'assistant': raise ValueError('Ambiguous message outputs')
            content = messages[0]['content']
            if any(c.get('type') == 'refusal' for c in content):
                return {'status': 'PROVIDER_REFUSAL', 'usage': usage, 'model': model}
            if not content or any(c.get('type') != 'output_text' or not isinstance(c.get('text'), str) for c in content):
                raise ValueError('Unverified content type')
            text = ''.join(c['text'] for c in content)
        else: raise ValueError('Unverified surface / streaming is not supported')
        return {'status': 'CONTENT' if text.strip() else 'EMPTY_RESPONSE', 'text': text, 'usage': usage, 'model': model}
    except (ValueError, TypeError, KeyError, UnicodeError) as exc:
        return {'status': 'RESPONSE_ENVELOPE_FAILURE', 'error': str(exc)}


def analyze_response(raw, case, packet, evidence, schema, legacy_validator, *,
                     surface, execution_state, http_status=None, expected_model=None):
    """Offline transport/envelope/content layers; never repair or retry a failure."""
    if execution_state in {'NOT_SENT', 'EXECUTION_UNKNOWN'}:
        return {'status': execution_state, 'usage': 'UNKNOWN' if execution_state == 'EXECUTION_UNKNOWN' else 'NOT_ISSUED',
                'retry_allowed': False}
    if execution_state != 'RESPONSE_RECEIVED':
        return {'status': 'EXECUTION_UNKNOWN', 'usage': 'UNKNOWN', 'retry_allowed': False}
    if http_status is not None and http_status >= 400:
        try:
            body = strict_json(raw.decode('utf-8')); error = body.get('error', {})
            code = error.get('code') if isinstance(error, dict) else None
            usage = body.get('usage')
        except (ValueError, UnicodeError, AttributeError): code, usage = None, None
        state = ('CONTEXT_LIMIT' if code == 'context_length_exceeded' else
                 'OUTPUT_LIMIT_REJECTED' if code in {'max_output_tokens_exceeded', 'max_tokens_exceeded'} else 'REQUEST_REJECTED')
        return {'status': state, 'usage': usage if usage is not None else 'UNKNOWN', 'retry_allowed': False}
    decoded = decode_response(raw, surface)
    if decoded['status'] != 'CONTENT':
        return decoded | {'usage': decoded.get('usage') if decoded.get('usage') is not None else 'UNKNOWN'}
    if expected_model is None or not decoded.get('model'):
        return {'status': 'MODEL_IDENTITY_UNVERIFIED', 'decoded': decoded, 'usage': decoded.get('usage') or 'UNKNOWN'}
    if decoded['model'] != expected_model:
        return {'status': 'MODEL_IDENTITY_MISMATCH', 'decoded': decoded, 'usage': decoded.get('usage') or 'UNKNOWN'}
    return validate_content(decoded['text'], case, packet, evidence, schema, legacy_validator) | {
        'usage': decoded['usage'] if decoded['usage'] is not None else 'UNKNOWN',
        'returned_model': decoded['model'], 'semantic_quality': 'NOT_VALIDATED',
        'human_acceptance': 'NOT_REVIEWED', 'navigation_status': 'INACTIVE'}


def record_attempt(root, attempt_id, case, raw_request, raw_response, metadata, process):
    """Create-only archive; raw evidence precedes decoding, even on validator crash."""
    if not re.fullmatch(r'[a-zA-Z0-9_-]+', attempt_id): raise ValueError('Unsafe attempt ID')
    target = Path(root)/'attempts'/attempt_id
    target.mkdir(parents=True, exist_ok=False)
    (target/'request.bin').write_bytes(raw_request)
    if raw_response is not None: (target/'response.bin').write_bytes(raw_response)
    put(target/'case.json', case)
    put(target/'metadata.json', metadata | {'usage_policy': 'Missing usage is UNKNOWN, never zero',
        'raw_response_present': raw_response is not None, 'human_acceptance': 'NOT_REVIEWED', 'navigation_status': 'INACTIVE'})
    put(target/'events/0001-archived.json', {'state': 'RAW_ARCHIVED', 'request_sha256': sha(target/'request.bin'),
        'response_sha256': sha(target/'response.bin') if raw_response is not None else None})
    try: result = process(raw_response)
    except Exception as exc: result = {'status': 'LOCAL_PROCESSING_FAILURE', 'exception': type(exc).__name__, 'error': str(exc)}
    put(target/'events/0002-result.json', result)
    return result


def prepare(f1, out):
    f1, out = Path(f1), Path(out)
    verification = verify_frozen(f1, require_seal=True)
    if verification['provisionally_unblocked_pairs'] != 24 or not verification['seed_confirmed_by_user']:
        raise ValueError('Wrong F1 input authority')
    out.mkdir(parents=True, exist_ok=False)
    pairs = read(f1/'selected-pairs.json')['pairs']; seed = read(f1/'selected-pairs.json')['seed']
    expected_legacy = read(f1/'prompt-snapshots/effective-legacy.json')['runner_source_sha256']
    if sha('src/genshin_corpus/rag/navi_g1_experiment.py') != expected_legacy:
        raise ValueError('Frozen effective legacy source changed')
    (out/'contracts').mkdir()
    for f in (f1/'prompt-snapshots').iterdir(): (out/'contracts'/f.name).write_bytes(f.read_bytes())
    # Snapshot just the exact pure native validators; don't import transport-bearing modules.
    segments = []
    for path, name in [(Path('src/genshin_corpus/rag/navi_r1_qa.py'), 'validate'),
                       (Path('src/genshin_corpus/rag/navi_g1_experiment.py'), 'validate_new_review')]:
        source = path.read_text(encoding='utf-8'); tree = ast.parse(source)
        node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
        segments.append(ast.get_source_segment(source, node))
    (out/'contracts/legacy-native-validators.py').write_text('\n\n'.join(segments)+'\n', encoding='utf-8')
    legacy_hash = sha(out/'contracts/legacy-native-validators.py'); schema_hash = sha(out/'contracts/p05-navi-b-v02.schema.json')
    cases, measurements = [], []
    for pair in pairs:
        views = {arm: read(f1/'model-inputs'/arm/(pair['pair_id']+'.json')) for arm in ARMS}
        if value_hash({s: views['legacy'][s] for s in ['A', 'B']}) != value_hash({s: views['rc1'][s] for s in ['A', 'B']}):
            raise ValueError('Unequal source evidence')
        for arm in ARMS:
            mode = 'generation' if pair['cohort'] == 'GENERATION' else 'review'
            parts = prompt_parts(out/'contracts', arm, mode)
            case = compile_case(pair, arm, views[arm], parts, sha(f1/'model-inputs'/arm/(pair['pair_id']+'.json')),
                                legacy_hash if arm == 'legacy' else schema_hash)
            put(out/'requests'/(case['request_id']+'.json'), case)
            measurements.append(measure(case, parts))
            cases.append({k: v for k, v in case.items() if k != 'messages'} | {
                'case_file': 'requests/'+case['request_id']+'.json', 'case_sha256': sha(out/'requests'/(case['request_id']+'.json'))})
    for pair in pairs:
        rows = [c for c in cases if c['pair_id'] == pair['pair_id']]
        if rows[0]['original_text_sha256'] != rows[1]['original_text_sha256']: raise ValueError('Original text mismatch')
    put(out/'base-request-manifest.json', {'stage': 'PREFLIGHT_NOT_F2', 'f1_root': str(f1.resolve()),
        'f1_freeze_sha256': sha(f1/'freeze-manifest.json'), 'selection_seed': seed,
        'order_namespace': ORDER_NAMESPACE, 'order': request_order(pairs, seed), 'base_calls_planned': 48,
        'targeted_max_calls_planned': 8, 'total_max_planned': 56, 'requests': cases,
        'actual_calls': 0, 'f2_authorized': False})
    put(out/'token-measurements.json', {'actual_model_tokenizer': None, 'pricing_currency': 'UNKNOWN',
        'proxy_limits': 'Byte-ratio scenarios are not measured tokens, guaranteed bounds, chat framing or a bill.', 'requests': measurements})
    return {'requests': 48, 'project_model_calls': 0, 'embedding_inferences': 0}


def verify_preflight(root, *, require_seal=True):
    root = Path(root); manifest = read(root/'base-request-manifest.json')
    f1 = Path(manifest['f1_root']); verify_frozen(f1, require_seal=True)
    if sha(f1/'freeze-manifest.json') != manifest['f1_freeze_sha256']:
        raise ValueError('F1 seal identity changed')
    rows = manifest['requests']
    if len(rows) != 48 or len({r['request_id'] for r in rows}) != 48:
        raise ValueError('Request denominator changed')
    pairs = read(f1/'selected-pairs.json')['pairs']; byid = {p['pair_id']: p for p in pairs}
    if manifest['order'] != request_order(pairs, manifest['selection_seed']):
        raise ValueError('Order changed')
    for row in rows:
        path = root/row['case_file']
        if sha(path) != row['case_sha256']: raise ValueError('Request file changed')
        c = read(path); view = read(f1/'model-inputs'/c['arm']/(c['pair_id']+'.json'))
        parts = prompt_parts(root/'contracts', c['arm'], c['mode'])
        rebuilt = compile_case(byid[c['pair_id']], c['arm'], view, parts,
                               sha(f1/'model-inputs'/c['arm']/(c['pair_id']+'.json')), c['schema_sha256'])
        if rebuilt != c: raise ValueError('Request content changed')
    seal = root/'freeze-manifest.json'
    if seal.exists():
        files = {str(p.relative_to(root)) for p in root.rglob('*') if p.is_file() and p != seal}
        frozen = read(seal)['files']
        if files != set(frozen) or any(sha(root/p) != info['sha256'] for p, info in frozen.items()):
            raise ValueError('Preflight frozen files changed')
    elif require_seal: raise ValueError('Missing preflight seal')
    return {'mechanical_status': 'PASS', 'static_requests': 48, 'project_model_calls': 0,
            'embedding_inferences': 0, 'model_experiment_ready': False, 'f2_authorized': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify', type=Path)
    parser.add_argument('--f1', type=Path); parser.add_argument('--out', type=Path)
    args = parser.parse_args()
    if args.verify: result = verify_preflight(args.verify)
    else:
        if args.f1 is None or args.out is None: parser.error('Preparation requires --f1 and new --out')
        result = prepare(args.f1, args.out)
    print(dumps(result))


if __name__ == '__main__': main()
