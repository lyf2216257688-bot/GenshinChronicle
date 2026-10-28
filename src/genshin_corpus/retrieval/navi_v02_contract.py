"""Offline rc1 binding checks, after JSON Schema validation; no provider or release seam.

This checks declared IDs, reading receipts and immutable candidate text. It does
not verify comprehension, extract quotes, grade meaning, or publish anything.
Delivery IDs must already be bound to verified Canonical/RU by their owner.
"""
from collections import Counter
from hashlib import sha256

VERSION = 'p05-navi-b-v0.2-rc1'
ORIGINAL = {'ORIGINAL_CORE', 'ORIGINAL_COMPONENT'}
ROLES = ORIGINAL | {'SAFE_REPHRASING', 'NEW_DISCOVERY', 'COUNTERFACTUAL_GUARD'}
TOP_FIELDS = {'schema_version', 'decision', 'reading', 'source_roles', 'propositions', 'no_link_reason'}


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def validate_bindings(value, delivery, candidates=(), *, mode):
    """Check a schema-validated object against trusted offline input registries.

    delivery: A/B -> unique verified snippet/alias IDs supplied in this call.
    candidates: id/text/role/text_sha256, with optional historical metadata kept
    by the caller. Every review input must receive its own unchanged-text row.
    No input or historical record is mutated. Returned IDs are content
    recommendations only, never human acceptance or actual release eligibility.
    """
    _require(mode in {'generation', 'review'}, 'Unknown mode')
    _require(isinstance(value, dict) and set(value) == TOP_FIELDS, 'Unexpected model-owned top fields')
    _require(value['schema_version'] == VERSION, 'Wrong interface version')
    _require(set(delivery) == {'A', 'B'}, 'Delivery endpoint set')
    for side in ['A', 'B']:
        _require(len(delivery[side]) == len(set(delivery[side])), 'Duplicate delivered ID')
    _require(not set(delivery['A']) & set(delivery['B']), 'Ambiguous endpoint IDs')
    originals = {}
    for c in candidates:
        _require(_text(c.get('id')) and c['id'] not in originals, 'Duplicate/empty original candidate ID')
        _require(_text(c.get('text')) and c.get('role') in ORIGINAL, 'Invalid original candidate')
        _require(sha256(c['text'].encode('utf-8')).hexdigest() == c.get('text_sha256'), 'Candidate text hash mismatch')
        originals[c['id']] = c
    _require(mode != 'generation' or not originals, 'Generation cannot overwrite a review registry')
    read = {}
    for side in ['A', 'B']:
        receipt = value['reading'][side]
        rr, uu = receipt['read_ids'], receipt['unread_ids']
        _require(len(rr) == len(set(rr)) and len(uu) == len(set(uu)), 'Duplicate reading receipt ID')
        read[side] = set(rr)
        _require(not read[side] & set(uu), 'Read/unread overlap')
        _require(read[side] | set(uu) == set(delivery[side]), 'Reading receipt must account for actual delivery')
        for role in value['source_roles'][side]:
            _require(set(role['evidence_ids']) <= read[side], 'Source role cites unread/other-end evidence')
            _require(role['role'] == 'unknown' or bool(role['evidence_ids']), 'Known source role needs evidence')
            _require(_text(role['basis']), 'Source role basis missing')
    ids, assessed, eligible, pending = set(), Counter(), [], False
    for p in value['propositions']:
        _require(_text(p.get('id')) and p['id'] not in ids, 'Duplicate/empty proposition ID')
        ids.add(p['id'])
        _require(p['role'] in ROLES, 'Unknown proposition role')
        _require(all(_text(p[k]) for k in ['text', 'reason', 'boundary']), 'Missing proposition account')
        for side, key in [('A', 'a_ids'), ('B', 'b_ids')]:
            _require(len(p[key]) == len(set(p[key])) and set(p[key]) <= read[side], 'Proposition cites unread/other-end evidence')
        grade, support, release = p['grade'], p['support'], p['release']
        if grade in {'A', 'B'}:
            _require(bool(p['a_ids']) and bool(p['b_ids']), 'A/B requires both evidence endpoints')
            _require(_text(p['increment_a_from_b']) or _text(p['increment_b_from_a']), 'A/B needs a declared increment')
        if grade == 'A':
            _require(support == 'EXPLICIT', 'A requires explicit support')
        if grade == 'B':
            _require(support in {'EXPLICIT', 'GROUNDED_INTERPRETATION'}, 'B support conflict')
            _require(p['navigation_label'] == '解释性联读 / 非确定史实', 'B label missing')
        if grade == 'D':
            _require(support == 'INCORRECT_OR_UNSUPPORTED_KEY_ASSERTION' and _text(p['critical_error']), 'D needs a specific error')
            _require(bool(p['a_ids'] or p['b_ids']), 'D needs an evidence basis')
        if grade == 'U':
            _require(support == 'UNDETERMINED' and _text(p['evidence_gap']) and release == 'UNRESOLVED', 'U needs a necessary evidence gap')
        if support in {'UNDETERMINED', 'INCORRECT_OR_UNSUPPORTED_KEY_ASSERTION'}:
            _require(grade == ('U' if support == 'UNDETERMINED' else 'D'), 'Support/grade conflict')
        if release == 'ELIGIBLE_PENDING_RELEASE':
            _require(grade in {'A', 'B'}, 'Only A/B may be content candidates')
        cid, role, action = p['candidate_id'], p['role'], p['review_action']
        if cid is not None:
            _require(cid in originals, 'Unknown original candidate reference')
        if mode == 'generation':
            _require(role in ORIGINAL and cid is None and action == 'PROPOSE', 'Generation origin conflict')
        elif role in ORIGINAL:
            _require(cid in originals and action in {'KEEP', 'REJECT', 'UNRESOLVED'}, 'Original assessment missing')
            _require(p['text'] == originals[cid]['text'] and role == originals[cid]['role'], 'Original text/role must not be rewritten')
            expected_action = 'REJECT' if grade == 'D' else 'UNRESOLVED' if grade == 'U' else 'KEEP'
            _require(action == expected_action, 'Original action/grade conflict')
            assessed[cid] += 1
        elif role == 'SAFE_REPHRASING':
            _require(cid in originals and action == 'REWRITE' and p['text'] != originals[cid]['text'], 'Safe rewrite must bind a distinct original')
        elif role == 'NEW_DISCOVERY':
            _require(cid is None and action == 'PROPOSE', 'New discovery cannot claim old candidate identity')
        elif role == 'COUNTERFACTUAL_GUARD':
            _require(action == 'PROPOSE' and release in {'EXCLUDED', 'UNRESOLVED'}, 'Counterfactual guard cannot be admitted')
        if role != 'COUNTERFACTUAL_GUARD':
            if release == 'ELIGIBLE_PENDING_RELEASE':
                eligible.append(p['id'])
            if release in {'REVIEW_REQUIRED', 'UNRESOLVED'}:
                pending = True
    if mode == 'review':
        _require(set(assessed) == set(originals) and all(n == 1 for n in assessed.values()), 'Every original needs exactly one unchanged assessment')
    decision = 'CANDIDATES' if eligible else 'UNRESOLVED' if pending else 'NO_FORMAL_NAVI'
    _require(value['decision'] == decision, 'Pair decision disagrees with proposition dispositions')
    if not value['propositions']:
        _require(_text(value['no_link_reason']), 'Zero propositions need a no-link reason')
    return {'mechanical_status': 'PASS', 'decision': decision, 'content_candidate_ids': eligible,
            'original_assessment_count': sum(assessed.values()), 'human_acceptance': 'NOT_REVIEWED',
            'navigation_status': 'INACTIVE', 'semantic_quality': 'NOT_VALIDATED'}
