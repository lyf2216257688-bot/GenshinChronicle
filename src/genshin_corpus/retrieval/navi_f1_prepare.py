"""F1 offline selection and exact evidence packets. No models, network or publication."""
from collections import Counter, defaultdict
import argparse
import csv
import gzip
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from .navi_g1_s1_audit import verify_span

SKIP_SECTIONS = {'成长数值', '推荐角色', '搭配推荐', '推荐装备', '推荐配队', '队伍搭配',
                 '攻略推荐', '角色突破', '天赋升级', '突破材料', '关联活动', '相关任务'}


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def sha(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def put(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8', newline='\n') as f:
        json.dump(value, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write('\n')


def value_hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def rank_select(pool, catalog, seed):
    """Predeclared hash ordering and source disjointness; never inspect grades."""
    used, selected, ledger, seen = set(), [], [], set()
    for p in pool:
        if p['pair_id'] in seen or p['sources'] != sorted(p['sources']) or len(set(p['sources'])) != 2:
            raise ValueError('Duplicate pair or orientation mismatch')
        seen.add(p['pair_id'])
        if p['primary_stratum'] != min(p['risk_tags']):
            raise ValueError('Primary priority mismatch')
    for layer in range(1, 9):
        options = []
        for p in pool:
            if p['primary_stratum'] != layer:
                continue
            a, b = p['sources']
            rank = hashlib.sha256((seed+'|'+catalog[a]['canonical_sha256']+'|'+catalog[b]['canonical_sha256']).encode()).hexdigest()
            options.append(dict(p, sort_sha256=rank))
        admitted = 0
        for row in sorted(options, key=lambda p: (p['sort_sha256'], p['pair_id'])):
            if row['eligibility'] != 'ELIGIBLE':
                disposition = 'EXCLUDED'
            elif set(row['sources']) & used:
                disposition = 'SOURCE_CONFLICT'
            elif admitted >= 3:
                disposition = 'ELIGIBLE_NOT_SELECTED_QUOTA'
            else:
                disposition = 'SELECTED'
                used.update(row['sources'])
                admitted += 1
                row.update(cohort='GENERATION' if admitted <= 2 else 'AUDIT', stratum_selection_ordinal=admitted)
                selected.append(row.copy())
            ledger.append(row | {'disposition': disposition})
    return selected, ledger


def inventory(record):
    result = []
    for sec in record['sections']:
        name = sec['source_metadata'].get('name', '')
        for unit in sec['units']:
            text = unit.get('value', {}).get('normalized_text', '')
            reason = ('NON_RICH_CANONICAL_UNIT' if unit['kind'] != 'rich_text' else
                      'EMPTY_RICH_TEXT' if not text else
                      'DECLARED_GAMEPLAY_OR_DIRECTORY_SECTION' if name in SKIP_SECTIONS else None)
            result.append({'section': sec['ordinal'], 'unit': unit['ordinal'], 'section_name': name,
                'kind': unit['kind'], 'characters': len(text), 'reason': reason,
                'disposition': 'OMITTED' if reason else 'MOTHER_SCOPE',
                'parent_component_key': unit.get('parent_component_key'),
                'content_role': unit.get('content_role'), 'provenance': unit.get('provenance')})
    return result


def ru_spans(record, rows, cid):
    """Reconstruct complete occurrences, never bind by substring in another unit."""
    spans = {}
    for key, rr in rows.items():
        if key[0] != cid:
            continue
        _, so, uo = key
        sec = next(s for s in record['sections'] if s['ordinal'] == so)
        unit = next(u for u in sec['units'] if u['ordinal'] == uo)
        rr = sorted(rr, key=lambda r: r['fragment_selector'].get('fragment_index', 0))
        if ([r['fragment_selector'].get('fragment_index', 0) for r in rr] != list(range(len(rr)))
                or len({r['unit_id'] for r in rr}) != len(rr)):
            raise ValueError('RU fragment identity/sequence mismatch')
        for row in rr:
            a = row['source']['canonical_address']
            if a['record_id'] != record['record_id'] or a['source_identity_key'].rsplit(':', 1)[-1] != cid:
                raise ValueError('RU source/record mismatch')
        if ''.join(r['retrieval_visible_text'] for r in rr) != unit['value']['normalized_text']:
            raise ValueError('Complete RU occurrence differs')
        offset, parts = 0, []
        for row in rr:
            end = offset+len(row['retrieval_visible_text'])
            parts.append((offset, end, row['unit_id']))
            offset = end
        spans[(so, uo)] = parts
    return spans


def snippets(record, source, spans):
    result = []
    for item in inventory(record):
        if item['disposition'] != 'MOTHER_SCOPE':
            continue
        so, uo = item['section'], item['unit']
        sec = next(s for s in record['sections'] if s['ordinal'] == so)
        unit = next(u for u in sec['units'] if u['ordinal'] == uo)
        text = unit['value']['normalized_text']
        if (so, uo) not in spans:
            raise ValueError('Missing RU occurrence')
        for start in range(0, len(text), 1000):
            end = min(start+1000, len(text))
            q = {'id': f'f1:{source["source_id"]}:s{so}:u{uo}:{start}-{end}', 'source': source['source_id'],
                'record_id': record['record_id'], 'canonical_sha256': source['canonical_sha256'],
                'section': so, 'unit': uo, 'section_name': item['section_name'], 'start': start, 'end': end,
                'text': text[start:end], 'text_sha256': hashlib.sha256(text[start:end].encode()).hexdigest(),
                'official_unit_ids': [uid for lo, hi, uid in spans[(so, uo)] if lo < end and hi > start]}
            verify_span(q, record)
            if not q['official_unit_ids']:
                raise ValueError('Empty RU identity')
            result.append(q)
    return result


def build_packets(out, selected, catalog, ru_path, omission_plan):
    out = Path(out)
    needed, records = set(), {}
    for cid in sorted({s for p in selected for s in p['sources']}):
        d = catalog[cid]
        if sha(d['canonical_path']) != d['canonical_sha256']:
            raise ValueError('Canonical snapshot changed')
        record = read(d['canonical_path'])
        if record['record_id'] != d['record_id']:
            raise ValueError('Canonical identity changed')
        records[cid] = record
        needed.update((cid, r['section'], r['unit']) for r in inventory(record) if r['disposition'] == 'MOTHER_SCOPE')
        target = out/'canonical'/f'{cid}.json'
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('xb') as f:
            f.write(Path(d['canonical_path']).read_bytes())
    rows = defaultdict(list)
    with gzip.open(ru_path, 'rt', encoding='utf-8') as f:
        for line in f:
            row = json.loads(line)
            a = row['source']['canonical_address']
            key = (a['source_identity_key'].rsplit(':', 1)[-1], a['section_ordinal'], a['canonical_unit_ordinal'])
            if key in needed:
                rows[key].append(row)
    evidence, documents, all_ru = {}, {}, {}
    for cid, record in records.items():
        ss = snippets(record, catalog[cid], ru_spans(record, rows, cid))
        evidence.update({s['id']: s for s in ss})
        documents[cid] = catalog[cid] | {'inventory': inventory(record), 'snippet_ids': [s['id'] for s in ss],
            'frozen_canonical_path': str((out/'canonical'/f'{cid}.json').resolve()),
            'lineage': record['lineage'], 'source_identity': record['source_identity'],
            'metadata_lineage': record['metadata_lineage'], 'versions': record['versions']}
        for key, rr in rows.items():
            if key[0] == cid:
                all_ru.update({r['unit_id']: r for r in rr})
    put(out/'evidence.json', evidence)
    put(out/'documents.json', documents)
    put(out/'bound-ru-rows.json', all_ru)
    summaries = []
    for pair in selected:
        pid = pair['pair_id']
        payload, aliases, withheld = {}, {}, []
        for side, cid in zip(['A', 'B'], pair['sources']):
            d = documents[cid]
            ss = [evidence[sid] for sid in d['snippet_ids']]
            payload[side] = {'title': d['title'], 'source_category': {'channels': d['channels'],
                'category_screen': 'secondary_analysis' if '62' in d['channels'] else 'catalog',
                'passage_authorship': 'UNVERIFIED', 'independent_history_witness': False},
                'body_policy': 'Complete rich-text occurrences in declared scope; Canonical order is not inferred chronology.',
                'snippets': [], 'coverage': {'whole_page_coverage': False, 'complete_verified_scope': True,
                'verified_scoped_characters': sum(len(s['text']) for s in ss),
                'delivered_characters': sum(len(s['text']) for s in ss), 'omitted_snippet_count': 0}}
            for i, s in enumerate(ss, 1):
                alias = f'{side}{i:03d}'
                aliases[alias] = s['id']
                payload[side]['snippets'].append({'id': alias, 'section': s['section_name'], 'text': s['text']})
        mother = {'pair_id': pid, 'sides': dict(zip(['A', 'B'], pair['sources'])), 'aliases': aliases, 'payload': payload}
        put(out/'mother-packets'/f'{pid}.json', mother)
        delivery = json.loads(json.dumps(mother, ensure_ascii=False))
        for side in ['A', 'B']:
            cid = delivery['sides'][side]
            rules = omission_plan.get(pid, {}).get(cid, [])
            keep = []
            for s in delivery['payload'][side]['snippets']:
                q = evidence[delivery['aliases'][s['id']]]
                if [q['section'], q['unit']] in rules:
                    withheld.append({'side': side, 'alias': s['id'], 'evidence_id': q['id'],
                                     'section': q['section'], 'unit': q['unit']})
                else:
                    keep.append(s)
            delivery['payload'][side]['snippets'] = keep
            coverage = delivery['payload'][side]['coverage']
            coverage['delivered_characters'] = sum(len(s['text']) for s in keep)
            coverage['omitted_snippet_count'] = sum(w['side'] == side for w in withheld)
            coverage['complete_verified_scope'] = not coverage['omitted_snippet_count']
            if coverage['omitted_snippet_count']:
                delivery['payload'][side]['body_policy'] = 'Partial declared scope; only supplied exact snippets are available; no full-page coverage claim.'
        exposed = {s['id'] for side in ['A', 'B'] for s in delivery['payload'][side]['snippets']}
        delivery['aliases'] = {a: sid for a, sid in delivery['aliases'].items() if a in exposed}
        put(out/'deliveries'/f'{pid}.json', delivery)
        summaries.append({'pair_id': pid, 'withheld': withheld, 'source_payload_sha256': value_hash(delivery['payload']),
            'mother_payload_sha256': value_hash(mother['payload']),
            'source_payload_characters': sum(c['coverage']['delivered_characters'] for c in delivery['payload'].values())})
    put(out/'coverage-conditions.json', {'cases': summaries})
    return documents, evidence, summaries


def check_packet(packet, evidence, documents, ru_rows):
    """Mechanical check only; declared reading and semantic truth are not proven."""
    exposed, characters = set(), 0
    for side, cid in packet['sides'].items():
        if sha(documents[cid]['frozen_canonical_path']) != documents[cid]['canonical_sha256']:
            raise ValueError('Frozen Canonical hash changed')
        record = read(documents[cid]['frozen_canonical_path'])
        source_rows = defaultdict(list)
        for row in ru_rows.values():
            a = row['source']['canonical_address']
            if a['source_identity_key'].rsplit(':', 1)[-1] == cid:
                source_rows[(cid, a['section_ordinal'], a['canonical_unit_ordinal'])].append(row)
        spans = ru_spans(record, source_rows, cid)
        count = 0
        for item in packet['payload'][side]['snippets']:
            alias = item['id']
            if alias in exposed or not alias.startswith(side):
                raise ValueError('Alias duplicate or endpoint mismatch')
            exposed.add(alias)
            q = evidence[packet['aliases'][alias]]
            if q['source'] != cid or q['text'] != item['text']:
                raise ValueError('Model text differs from bound source')
            verify_span(q, record)
            expected = [uid for lo, hi, uid in spans[(q['section'], q['unit'])] if lo < q['end'] and hi > q['start']]
            if q['official_unit_ids'] != expected:
                raise ValueError('RU exact intersection mismatch')
            count += len(item['text'])
        if count != packet['payload'][side]['coverage']['delivered_characters']:
            raise ValueError('Character accounting mismatch')
        characters += count
    if exposed != set(packet['aliases']):
        raise ValueError('Undelivered alias leaked')
    return characters


def csv_pairs(path, pairs):
    with Path(path).open('x', encoding='utf-8-sig', newline='') as f:
        w = csv.writer(f)
        w.writerow(['pair_id', 'stratum', 'cohort', 'source_A', 'title_A', 'source_B', 'title_B', 'input_status'])
        for p in pairs:
            w.writerow([p['pair_id'], p['primary_stratum'], p['cohort'], p['sources'][0], p['titles'][0],
                        p['sources'][1], p['titles'][1], 'CANDIDATE_FROZEN_NOT_AUTHORIZED_FOR_F2'])


def verify_frozen(root, *, require_seal=False):
    root = Path(root)
    pairs = read(root/'selected-pairs.json')['pairs']
    catalog = {d['source_id']: d for d in read(root/'canonical-catalog.json')}
    pool = read(root/'candidate-pool-frozen.json')
    chosen, _ = rank_select(pool['pairs'], catalog, pool['frozen_seed_value'])
    if chosen != pairs or len(pairs) != 24 or len({s for p in pairs for s in p['sources']}) != 48:
        raise ValueError('Frozen selection/denominator mismatch')
    if Counter((p['primary_stratum'], p['cohort']) for p in pairs) != Counter(
            {(i, c): n for i in range(1, 9) for c, n in [('GENERATION', 2), ('AUDIT', 1)]}):
        raise ValueError('Stratum/cohort mismatch')
    base = read(root/'base-exclusions.snapshot.json')
    extra = {r['source_id'] for r in read(root/'additional-source-exclusions-final.json')['items']}
    sources = {s for p in pairs for s in p['sources']}
    if sources & (set(base['source_ids']) | extra):
        raise ValueError('Excluded source selected')
    if {catalog[s]['canonical_sha256'] for s in sources} & set(base['canonical_sha256_exclusions']):
        raise ValueError('Excluded Canonical hash selected')
    docs, evidence, ru = read(root/'documents.json'), read(root/'evidence.json'), read(root/'bound-ru-rows.json')
    for cid, d in docs.items():
        d['frozen_canonical_path'] = str(root/'canonical'/f'{cid}.json')
    shared = {r['pair_id']: r for r in read(root/'shared-arm-input-manifest.json')['cases']}
    coverage_cases = {r['pair_id']: r for r in read(root/'coverage-conditions.json')['cases']}
    kinds, delivered_chars, aliases, withheld_count = Counter(), 0, 0, 0
    forbidden = {'risk_tags', 'primary_stratum', 'probe_type', 'construction_origin',
                 'expected_grade', 'grade_recommendation', 'model_grade', 'human_acceptance',
                 'navigation_status', 'construction_process', 'basis_evidence'}
    def no_leak(obj):
        if isinstance(obj, dict):
            if set(obj) & forbidden:
                raise ValueError('Reviewer-only field leaked into model input')
            for v in obj.values():
                no_leak(v)
        elif isinstance(obj, list):
            for v in obj:
                no_leak(v)
    for p in pairs:
        pid = p['pair_id']
        mother, child = read(root/'mother-packets'/f'{pid}.json'), read(root/'deliveries'/f'{pid}.json')
        expected_sides = dict(zip(['A', 'B'], p['sources']))
        if mother['sides'] != expected_sides or child['sides'] != expected_sides:
            raise ValueError('Frozen endpoint orientation changed')
        if (set(mother['aliases'])-set(child['aliases'])
                != {w['alias'] for w in coverage_cases[pid]['withheld']}
                or any(mother['aliases'].get(a) != sid for a, sid in child['aliases'].items())):
            raise ValueError('Controlled omission/alias ledger mismatch')
        check_packet(mother, evidence, docs, ru)
        delivered_chars += check_packet(child, evidence, docs, ru)
        aliases += len(child['aliases'])
        withheld_count += len(mother['aliases'])-len(child['aliases'])
        if value_hash(child['payload']) != shared[pid]['source_payload_sha256']:
            raise ValueError('Shared source payload hash mismatch')
        for arm in ['legacy', 'rc1']:
            view = read(root/'model-inputs'/arm/f'{pid}.json')
            no_leak(view)
            if value_hash({s: view[s] for s in ['A', 'B']}) != value_hash(child['payload']):
                raise ValueError('Arm source input inequality')
            if sha(root/'model-inputs'/arm/f'{pid}.json') != shared[pid][arm+'_sha256']:
                raise ValueError('Arm input file changed')
        if p['cohort'] == 'AUDIT':
            reg = read(root/'reviewer-only/candidate-registries'/f'{pid}.json')
            kinds[reg['construction_origin']] += 1
            if len(reg['candidates']) not in [1, 2]:
                raise ValueError('Audit candidate denominator')
            for c in reg['candidates']:
                if c['text_sha256'] != hashlib.sha256(c['text'].encode()).hexdigest():
                    raise ValueError('Original candidate text hash')
            for quotes in reg['basis_evidence'].values():
                if any(q != evidence[q['id']] for q in quotes):
                    raise ValueError('Constructed probe quote is not exact evidence')
            old, new = (read(root/'model-inputs'/arm/f'{pid}.json') for arm in ['legacy', 'rc1'])
            if old['candidate']['proposition'] != reg['candidates'][0]['text'] or new['candidates'] != reg['candidates']:
                raise ValueError('Arm candidate text mismatch')
    if kinds != Counter({'NATURAL_CANDIDATE': 4, 'CONTROLLED_PROBE': 4}):
        raise ValueError('Natural/controlled cohorts mixed')
    seal = root/'freeze-manifest.json'
    if seal.exists():
        manifest = read(seal)
        actual = {str(f.relative_to(root)) for f in root.rglob('*') if f.is_file() and f != seal}
        if actual != set(manifest['files']) or any(sha(root/n) != d['sha256'] for n, d in manifest['files'].items()):
            raise ValueError('Frozen file set/hash changed')
    elif require_seal:
        raise ValueError('Missing freeze manifest')
    independence_path = root/'independence-dispositions-final.json'
    independence = read(independence_path) if independence_path.exists() else None
    if require_seal and independence is None:
        raise ValueError('Missing final independence dispositions')
    if independence is not None:
        rows = independence['cases']
        if len(rows) != 24 or {r['pair_id'] for r in rows} != {p['pair_id'] for p in pairs}:
            raise ValueError('Independence disposition denominator mismatch')
        admitted = sum(r['status'] == 'PROVISIONAL_NO_IDENTIFIED_BLOCK' for r in rows)
    else:
        admitted = None
    return {'mechanical_status': 'PASS', 'pairs': 24, 'sources': 48, 'generation': 16, 'audit': 8,
            'mother_snippets': len(evidence), 'delivered_aliases': aliases, 'withheld_snippets': withheld_count,
            'delivered_characters': delivered_chars,
            'seed_confirmed_by_user': pool.get('seed_authority') == 'USER_CONFIRMED',
            'semantic_independence': 'TARGETED_AGENT_SCREEN_NOT_HUMAN_GOLD',
            'f1_independence_status': 'PARTIAL' if admitted != 24 else 'PROVISIONAL_SCREEN_COMPLETE',
            'provisionally_unblocked_pairs': admitted,
            'project_model_calls': 0, 'embedding_calls': 0, 'f2_authorized': False}


def replay_packets(root, out):
    """Reconstruct source payloads using only frozen Canonical and bound RU rows."""
    root, out = Path(root), Path(out)
    verify_frozen(root, require_seal=True)
    out.mkdir(parents=True, exist_ok=False)
    pairs = read(root/'selected-pairs.json')['pairs']
    docs = read(root/'documents.json')
    for cid, d in docs.items():
        d['canonical_path'] = str(root/'canonical'/f'{cid}.json')
    with TemporaryDirectory() as temporary:
        rp = Path(temporary)/'ru.jsonl.gz'
        with gzip.open(rp, 'wt', encoding='utf-8') as f:
            for row in read(root/'bound-ru-rows.json').values():
                f.write(json.dumps(row, ensure_ascii=False)+'\n')
        build_packets(out, pairs, docs, rp, read(root/'omission-plan-frozen.json')['rules'])
    expected = read(root/'coverage-conditions.json')
    actual = read(out/'coverage-conditions.json')
    if expected != actual or read(root/'evidence.json') != read(out/'evidence.json'):
        raise ValueError('Replay source evidence/payload differs')
    result = {'status': 'PASS', 'pair_count': 24, 'replayed_from_frozen_evidence_only': True,
              'semantic_judgments_regenerated': False, 'model_calls': 0, 'embedding_calls': 0}
    put(out/'replay-comparison.json', result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage', choices=['verify', 'replay'])
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--out', type=Path)
    args = parser.parse_args()
    if args.stage == 'verify':
        result = verify_frozen(args.root, require_seal=True)
    else:
        if args.out is None:
            parser.error('replay requires a new --out directory')
        result = replay_packets(args.root, args.out)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
