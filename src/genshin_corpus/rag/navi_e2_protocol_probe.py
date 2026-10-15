"""Bounded E2 action-protocol canary. Never invokes search/read or research."""
import argparse
import json
from hashlib import sha256
from pathlib import Path

from genshin_corpus.canonical.fingerprints import canonical_json_bytes, sha256_json
from genshin_corpus.retrieval.navi_exploration import ExplorationError
from .navi_e2_experiment import E2SDK, PROMPT, HYPOTHESIS, once
from .navi_e2_routes import E2_ROUTES


FIXTURE_UID = 'a' * 64
PROBE = {
    'purpose': 'Synthetic action protocol test only; do not conduct story research.',
    'fixture': {'source_unit_id': FIXTURE_UID, 'source_binding_status': 'synthetic_fixture_only',
                'already_read_text': 'A fictional librarian says a catalogue entry is missing. No history is established.'},
    'instruction': 'Return one batch JSON decision with exactly four ordered actions: search, read, clue, stop. '
        'search for the missing catalogue (top_k=1); read the supplied fixture unit_id (max_chars=100, neighbors=0); '
        'clue cites only that fixture ID and explicitly remains an unverified hypothesis; stop after this protocol test. '
        'Include memory.status=unverified_hypothesis_not_fact and a short rationale. Do not use Markdown fences. '
        'These actions will only be validated, never executed.',
}


def validate_probe(terminal):
    if not terminal.get('decision_complete'):
        return {'accepted': False, 'reason': 'Responses or E2 decision validation failed'}
    decision = json.loads(terminal['content'])
    actions = decision['actions']
    checks = {
        'four_action_types': [a['action'] for a in actions] == ['search', 'read', 'clue', 'stop'],
        'fixture_read_binding': len(actions) == 4 and actions[1].get('unit_id') == FIXTURE_UID,
        'fixture_clue_binding': len(actions) == 4 and actions[2].get('source_unit_ids') == [FIXTURE_UID],
        'memory_hypothesis': decision['memory']['status'] == HYPOTHESIS,
        'tools_executed': 0,
    }
    return {'accepted': all(v for k, v in checks.items() if k != 'tools_executed'), 'checks': checks}


def _verified_prior(prior):
    """Read a frozen predecessor; never resume or reinterpret its UNKNOWNs."""
    prior = Path(prior).resolve()
    manifest_path = prior/'artifact-manifest.json'
    manifest = json.loads(manifest_path.read_text('utf-8'))
    for row in manifest['files']:
        path = (prior/row['path']).resolve()
        if (not path.is_relative_to(prior) or path.stat().st_size != row['bytes']
                or sha256(path.read_bytes()).hexdigest() != row['sha256']):
            raise ExplorationError('frozen prior campaign integrity mismatch')
    return prior, sha256(manifest_path.read_bytes()).hexdigest()


def initialize_followup_campaign(root, prior):
    """Explicit 2026-10-15 operator follow-up: one NEW probe per affected model."""
    root = Path(root).resolve()
    prior, digest = _verified_prior(prior)
    if root == prior or root.is_relative_to(prior) or prior.is_relative_to(root):
        raise ExplorationError('follow-up must be separate from frozen prior')
    once(root/'authorization.json', {
        'schema_version': 'p05-e2-protocol-campaign-0.2',
        'authorization': '2026-10-15 operator requested new DeepSeek probe and independent Jizhi GLM credential/route',
        'models': [{'model':'glm-5.3-flash','route':'jizhi_glm'},
                   {'model':'deepseek-v4.1-flash','route':'jizhi'}],
        'prior_root': str(prior), 'prior_manifest_sha256': digest,
        'max_new_requests_per_model': 1, 'max_new_total_requests': 2,
        'max_requests_per_model': 3, 'max_total_requests': 12,
        'research_authorized': False, 'fallback': False, 'protocol': 'responses',
        'historical_unknowns': 'preserved unresolved; fresh operator-authorized probes only, no replay/resume'})
    return root


def initialize_campaign(root):
    root = Path(root).resolve()
    contract = {'schema_version': 'p05-e2-protocol-campaign-0.1',
        # Original v1 authorization is historical and cannot change with routes.
        'models': [{'model': m, 'route': r} for m, r in (
            ('gpt-6-luna','tokenmetro'), ('glm-5.3-flash','tokenmetro'),
            ('qwen3.8-flash','tokenmetro_qwen'), ('deepseek-v4.1-flash','jizhi'))],
        'max_requests_per_model': 3, 'max_total_requests': 12,
        'research_authorized': False, 'fallback': False, 'protocol': 'responses'}
    if (root/'authorization.json').exists():
        saved = json.loads((root/'authorization.json').read_text('utf-8'))
        if saved.get('schema_version') == 'p05-e2-protocol-campaign-0.2':
            prior, digest = _verified_prior(saved['prior_root'])
            expected_models = [{'model':'glm-5.3-flash','route':'jizhi_glm'}, {'model':'deepseek-v4.1-flash','route':'jizhi'}]
            if (digest != saved['prior_manifest_sha256'] or saved['models'] != expected_models
                    or saved['max_new_requests_per_model'] != 1 or saved['max_new_total_requests'] != 2
                    or saved['max_requests_per_model'] != 3 or saved['max_total_requests'] != 12
                    or saved['research_authorized'] is not False or saved['fallback'] is not False
                    or saved['protocol'] != 'responses'):
                raise ExplorationError('follow-up authorization mismatch')
        elif saved != contract:
            raise ExplorationError('campaign authorization mismatch')
    else:
        if root.exists() and any(root.iterdir()): raise ExplorationError('campaign root must be empty')
        once(root/'authorization.json', contract)
    return root


def campaign_guard(campaign, model):
    """Count dispatch-boundary evidence, including failed and unresolved requests."""
    campaign = initialize_campaign(campaign)
    issued = list(campaign.glob('*/probe-*/rounds/**/issued.json'))
    contract = json.loads((campaign/'authorization.json').read_text('utf-8'))
    selected = [p for p in issued if p.relative_to(campaign).parts[0] == model]
    prior_issued = []
    if contract.get('prior_root'):
        prior = Path(contract['prior_root'])
        prior_issued = list(prior.glob('*/probe-*/rounds/**/issued.json'))
        if (len(issued) >= contract['max_new_total_requests']
                or len(selected) >= contract['max_new_requests_per_model']):
            raise ExplorationError('follow-up issued request ceiling reached')
    if model not in {row['model'] for row in contract['models']}:
        raise ExplorationError('model not authorized in campaign')
    inherited_model_calls = sum(p.relative_to(Path(contract['prior_root'])).parts[0] == model for p in prior_issued)
    if len(issued)+len(prior_issued) >= 12 or len(selected)+inherited_model_calls >= 3:
        raise ExplorationError('campaign issued request ceiling reached')
    for path in selected:
        terminal_path = path.with_name('terminal.json')
        if not terminal_path.exists(): raise ExplorationError('EXECUTION UNKNOWN; channel cannot reissue')
        terminal = json.loads(terminal_path.read_text('utf-8'))
        if terminal['execution_state'] == 'EXECUTION UNKNOWN':
            raise ExplorationError('EXECUTION UNKNOWN; channel stopped')


def run_probe(*, model, route, output_root, output_tokens=2048, transport=None, environment=None, campaign=None):
    root = Path(output_root).resolve()
    # A root cannot reset the immutable per-model ceiling by resuming/reissuing.
    if root.exists() and any(root.iterdir()):
        raise ExplorationError('probe root already contains evidence; no automatic resume')
    if campaign is not None:
        campaign = initialize_campaign(campaign)
        if root.parent.parent != campaign or root.parent.name != model or not root.name.startswith('probe-'):
            raise ExplorationError('probe must be in its fixed campaign model directory')
        contract = json.loads((campaign/'authorization.json').read_text('utf-8'))
        if {'model':model, 'route':route} not in contract['models']:
            raise ExplorationError('model/route not authorized in campaign')
        campaign_guard(campaign, model)
    limit = contract.get('max_new_requests_per_model', 3) if campaign else 3
    provider = E2SDK(route, model=model, transport=transport, environment=environment, max_attempts=limit,
        before_dispatch=(lambda: campaign_guard(campaign, model)) if campaign else None)
    if not 1 <= output_tokens <= 4096:
        raise ExplorationError('probe output ceiling must be 1..4096')
    body = {'model': provider.profile.model_id, 'instructions': canonical_json_bytes(PROMPT).decode(),
            'input': canonical_json_bytes(PROBE).decode(), 'stream': True, 'max_output_tokens': output_tokens}
    if campaign and contract.get('schema_version') == 'p05-e2-protocol-campaign-0.2':
        body['input'] = canonical_json_bytes(PROBE | {
            'diagnostic_authorization_identity': sha256_json(contract),
            'diagnostic_scope': 'New operator-authorized protocol probe; never resume or replay earlier unresolved requests.'}).decode()
    config = {'schema_version': 'p05-e2-four-model-protocol-0.1', 'provider': provider.config,
              'request': body, 'max_issued_requests': limit, 'max_reserved_tokens': limit * (
                  len(body['instructions'].encode()) + len(body['input'].encode()) + 256 + output_tokens),
              'synthetic_fixture_only': True, 'research_authorized': False}
    identity = sha256_json(config)
    once(root/'config.json', config)
    terminal = provider(root/'rounds'/'00', body, identity, decision_mode='batch')
    validation = validate_probe(terminal)
    once(root/'action-validation.json', validation)
    report = {'run_identity': identity, 'logical_model': model, 'route': route,
        'wire_model': body['model'], 'returned_models': terminal['returned_models'],
        'url': provider.profile.base_url + '/responses', 'requests': terminal['calls'],
        'route_authentication': 'PASS' if terminal['http_status'] == 200 else 'FAIL_OR_UNKNOWN',
        'responses_protocol': 'PASS' if terminal['responses_protocol_success'] else 'FAIL',
        'e2_action_protocol': 'PASS' if validation['accepted'] else 'FAIL',
        'result': 'PASS' if validation['accepted'] else
            'BLOCKED' if terminal['execution_state'] == 'EXECUTION UNKNOWN' else 'FAIL',
        'terminal_event': terminal['terminal_event'], 'response_status': terminal['response_status'],
        'execution_state': terminal['execution_state'], 'usage': terminal['usage'],
        'usage_normalized': terminal['usage_normalized'],
        'reasoning_event_types': terminal['reasoning_event_types'],
        'reasoning_availability': terminal['reasoning_availability'],
        'error': terminal['error'], 'fallback_calls': 0, 'chat_completions_calls': 0,
        'tools_executed': 0, 'actual_cost': 'UNKNOWN', 'currency': 'UNKNOWN'}
    once(root/'report.json', report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', required=True, choices=sorted({m for m, r in E2_ROUTES}))
    parser.add_argument('--route', required=True, choices=sorted({r for m, r in E2_ROUTES}))
    parser.add_argument('--output-root', required=True, type=Path)
    parser.add_argument('--campaign-root', required=True, type=Path)
    args = parser.parse_args()
    result = run_probe(model=args.model, route=args.route, output_root=args.output_root, campaign=args.campaign_root)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result['result'] == 'PASS' else 2


if __name__ == '__main__':
    raise SystemExit(main())
