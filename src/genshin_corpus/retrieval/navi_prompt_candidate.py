"""Final offline Navi prompt candidate. No client, credentials, retry or release seam.

Model schemas describe semantic work only. The program computes bookkeeping,
then applies the unchanged rc1 Schema AND context checks as an internal invariant
check. A new story-admission policy is applied prospectively, never to F2 scores.
"""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import re

from jsonschema import Draft202012Validator

from .navi_f2_preflight import check_view, strict_json
from .navi_v02_contract import VERSION as RC1_VERSION, validate_bindings

VERSION = 'p05-navi-b-v0.3-rc1'
POLICY = 'story-admission-v1'
DOCS = Path(__file__).resolve().parents[3] / 'docs/research/phase-05'
STORY_STATES = ['CONCRETE_STORY', 'DISPUTED_STORY', 'LITERARY_ONLY', 'NON_STORY', 'UNKNOWN']
DERIVED = {'id', 'review_action', 'release', 'navigation_label'}


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _nonblank(value):
    return isinstance(value, str) and bool(value.strip())


def _hash(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                             separators=(',', ':')).encode()).hexdigest()


def _rc1_schema():
    return strict_json((DOCS / 'p05-navi-b-v02.schema.json').read_text(encoding='utf8'))


def _object(properties):
    return {'type': 'object', 'properties': properties,
            'required': list(properties), 'additionalProperties': False}


def model_schema(mode):
    """Conservative Structured Outputs subset; cross-field checks stay local.

    Removing provider-unsupported conditionals here does NOT remove local rc1
    invariants: validate_output below runs the complete unchanged rc1 Schema.
    """
    _require(mode in {'generation', 'review'}, 'Unknown mode')
    old = _rc1_schema()['properties']
    prop = deepcopy(old['propositions']['items']['properties'])
    for key in DERIVED | ({'candidate_id', 'role'} if mode == 'generation' else set()):
        prop.pop(key)
    prop['story'] = _object({'status': {'type': 'string', 'enum': STORY_STATES},
                             'bridge': {'type': 'string'}})
    schema = _object({'reading': deepcopy(old['reading']),
                      'source_roles': deepcopy(old['source_roles']),
                      'propositions': {'type': 'array', 'items': _object(prop)},
                      'no_link_reason': {'type': 'string'}})

    def subset(node):
        if isinstance(node, list):
            for value in node:
                subset(value)
        elif isinstance(node, dict):
            # All of these are still checked locally, after program projection.
            for key in ['minLength', 'pattern', 'uniqueItems', 'minItems']:
                node.pop(key, None)
            if 'enum' in node and 'type' not in node:
                node['type'] = 'string'
            for value in node.values():
                subset(value)
    subset(schema)
    Draft202012Validator.check_schema(schema)
    return schema


def _disposition(p):
    """Compute a recommendation conditional on declared semantic judgments."""
    if p['role'] == 'COUNTERFACTUAL_GUARD':
        return ('UNRESOLVED' if p['grade'] == 'U' else 'EXCLUDED'), 'GUARD_NEVER_ADMITTED'
    if p['grade'] == 'D':
        return 'EXCLUDED', 'KEY_ASSERTION_REJECTED'
    if p['grade'] == 'U':
        return 'UNRESOLVED', 'NECESSARY_EVIDENCE_GAP'
    status = p['story']['status']
    if status == 'UNKNOWN':
        return 'UNRESOLVED', 'STORY_EVIDENCE_GAP'
    if status == 'DISPUTED_STORY':
        return 'REVIEW_REQUIRED', 'STORY_BRIDGE_DISPUTED'
    if status == 'LITERARY_ONLY':
        return 'EXCLUDED', 'LITERARY_WITHOUT_SHARED_STORY'
    if status == 'NON_STORY':
        if p['relation'] in {'duplicate', 'gameplay_only'}:
            return 'CATALOG_ONLY', 'CATALOG_WITHOUT_STORY_INCREMENT'
        return 'EXCLUDED', 'NO_CONCRETE_STORY_BRIDGE'
    if p['grade'] in {'A', 'B'}:
        return 'ELIGIBLE_PENDING_RELEASE', 'DECLARED_CONCRETE_STORY_AND_AB_NOT_SEMANTIC_ACCEPTANCE'
    return 'REVIEW_REQUIRED', 'STORY_PROPOSED_BUT_GRADE_INSUFFICIENT'


def _decision(props):
    relevant = [p for p in props if p['role'] != 'COUNTERFACTUAL_GUARD']
    if any(p['release'] == 'ELIGIBLE_PENDING_RELEASE' for p in relevant):
        return 'CANDIDATES'
    if any(p['release'] in {'UNRESOLVED', 'REVIEW_REQUIRED'} for p in relevant):
        return 'UNRESOLVED'
    return 'NO_FORMAL_NAVI'


def validate_output(text, delivery, candidates=(), *, mode):
    """Strict parse -> model shape -> deterministic fields -> full local checks.

    `delivery` is a trusted alias registry already verified by its upstream
    Canonical/RU owner. This function cannot establish upstream provenance or
    judge whether a declared story bridge is true. It never repairs raw text.
    The caller must retain the raw provider bytes before invoking this function.
    """
    value = strict_json(text)
    Draft202012Validator(model_schema(mode)).validate(value)
    props, dispositions = [], []
    for index, original in enumerate(value['propositions'], 1):
        p = deepcopy(original)
        _require(_nonblank(p['story']['bridge']), 'Story assessment needs a specific bridge or rejection reason')
        if p['story']['status'] in {'CONCRETE_STORY', 'DISPUTED_STORY'}:
            _require(bool(p['a_ids']) and bool(p['b_ids']), 'Story bridge needs both endpoints')
        if p['story']['status'] == 'UNKNOWN':
            _require(_nonblank(p['evidence_gap']), 'Unknown story needs a necessary evidence gap')
        p['id'] = 'p' + str(index)
        if mode == 'generation':
            p.update(candidate_id=None, role='ORIGINAL_CORE', review_action='PROPOSE')
        elif p['role'] in {'ORIGINAL_CORE', 'ORIGINAL_COMPONENT'}:
            p['review_action'] = {'D': 'REJECT', 'U': 'UNRESOLVED'}.get(p['grade'], 'KEEP')
        else:
            p['review_action'] = 'REWRITE' if p['role'] == 'SAFE_REPHRASING' else 'PROPOSE'
        p['navigation_label'] = '解释性联读 / 非确定史实' if p['grade'] == 'B' else None
        p['release'], reason = _disposition(p)
        dispositions.append({'id': p['id'], 'release': p['release'], 'policy_reason': reason})
        props.append(p)
    # Internal compatibility projection only. Neither old text nor old outcomes
    # are modified, and no model-owned grade/support/evidence field is repaired.
    compatible = {'schema_version': RC1_VERSION, 'decision': _decision(props),
                  'reading': deepcopy(value['reading']), 'source_roles': deepcopy(value['source_roles']),
                  'propositions': [{k: v for k, v in p.items() if k != 'story'} for p in props],
                  'no_link_reason': value['no_link_reason']}
    Draft202012Validator(_rc1_schema()).validate(compatible)
    binding = validate_bindings(compatible, delivery, candidates, mode=mode)
    result = compatible | {'schema_version': VERSION, 'propositions': props,
                          'admission_policy': POLICY, 'dispositions': dispositions,
                          'mechanical': binding, 'raw_text_sha256': sha256(text.encode()).hexdigest(),
                          'input_registry_sha256': _hash({'delivery': delivery, 'candidates': candidates}),
                          'human_acceptance': 'NOT_REVIEWED', 'navigation_status': 'INACTIVE',
                          'semantic_quality': 'NOT_VALIDATED'}
    return result


def prompt(mode):
    _require(mode in {'generation', 'review'}, 'Unknown mode')
    text = (DOCS / 'p05-navi-b-prompts-v03.md').read_text(encoding='utf8')
    blocks = re.findall(r'```text\n(.*?)\n```', text, re.S)
    _require(len(blocks) == 3, 'Candidate prompt block structure changed')
    return blocks[0] + '\n' + blocks[1 if mode == 'generation' else 2]


def prepare_request(view, *, mode, model, route, max_output_tokens, format_mode='json_object'):
    """Pure UNSENT Responses draft. No capability or authorization is inferred.

    json_object is the observed route format, with known F2 failures. json_schema
    is an inspectable alternative draft only: strict enforcement is UNKNOWN for
    the historical proxy/model. Never fallback or send from this module.
    """
    _require(mode in {'generation', 'review'}, 'Unknown mode')
    check_view(view, mode, 'rc1')
    _require(_nonblank(model) and _nonblank(route), 'Explicit model and route required')
    _require(type(max_output_tokens) is int and max_output_tokens > 0, 'Explicit positive output cap required')
    _require(format_mode in {'json_object', 'json_schema'}, 'Unsupported format strategy')
    schema = model_schema(mode)
    fmt = {'type': format_mode}
    if format_mode == 'json_schema':
        fmt.update(name='navi_b_v03_' + mode, strict=True, schema=schema)
    # Keep instructions/schema in the explicit user message, as in final F2.
    content = prompt(mode) + '\n完整输出JSON Schema：\n' + json.dumps(schema, ensure_ascii=False)
    content += '\n本次交付材料：\n' + json.dumps(view, ensure_ascii=False)
    wire = {'model': model, 'stream': True, 'max_output_tokens': max_output_tokens,
            'input': [{'role': 'user', 'content': [{'type': 'input_text', 'text': content}]}],
            'text': {'format': fmt}}
    return {'candidate_version': VERSION, 'admission_policy': POLICY, 'mode': mode,
            'route': route, 'wire': wire, 'wire_sha256': _hash(wire),
            'source_view_sha256': _hash(view), 'send_authorized': False,
            'strict_provider_enforcement': 'UNKNOWN',
            'local_validation_required': True, 'automatic_retry': False}
