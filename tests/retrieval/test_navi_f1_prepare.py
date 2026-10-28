"""Tiny synthetic F1 fixtures; no local corpus, API, or semantic gold."""
import ast
from copy import deepcopy
import gzip
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from genshin_corpus.retrieval import navi_f1_prepare as f1


def selection_fixture():
    pool, catalog = [], {}
    for layer in range(1, 9):
        for n in range(3):
            ids = [f'{layer}{n}a', f'{layer}{n}b']
            for cid in ids:
                catalog[cid] = {'canonical_sha256': sha256(cid.encode()).hexdigest()}
            pool.append({'pair_id': f'p{layer}{n}', 'sources': ids, 'primary_stratum': layer,
                         'risk_tags': [layer], 'eligibility': 'ELIGIBLE'})
    return pool, catalog


def packet_fixture(root):
    catalog, all_rows = {}, []
    for cid in ['1', '2']:
        text = '月影与记忆。\n' * 200
        record = {'record_id': 'r'+cid, 'lineage': {}, 'source_identity': {},
                  'metadata_lineage': {}, 'versions': {}, 'sections': [
            {'ordinal': 0, 'source_metadata': {'name': '书目信息'},
             'units': [{'ordinal': 1, 'kind': 'rich_text', 'value': {'normalized_text': '保留的目录信息'}}]},
            {'ordinal': 1, 'source_metadata': {'name': '正文'},
             'units': [{'ordinal': 1, 'kind': 'rich_text', 'value': {'normalized_text': text}}]}]}
        path = root/f'source{cid}.json'
        path.write_text(json.dumps(record, ensure_ascii=False), encoding='utf-8')
        catalog[cid] = {'source_id': cid, 'title': '合成资料'+cid, 'channels': ['68'],
                       'canonical_path': str(path), 'canonical_sha256': f1.sha(path), 'record_id': 'r'+cid}
        for so, body in [(0, '保留的目录信息'), (1, text)]:
            for i, start in enumerate(range(0, len(body), 600)):
                all_rows.append({'unit_id': f'{cid}-{so}-{i}', 'source': {'canonical_address': {
                    'source_identity_key': 'test:zh-cn:'+cid, 'record_id': 'r'+cid,
                    'section_ordinal': so, 'canonical_unit_ordinal': 1}},
                    'fragment_selector': {'fragment_index': i}, 'retrieval_visible_text': body[start:start+600]})
    ru = root/'ru.jsonl.gz'
    with gzip.open(ru, 'wt', encoding='utf-8') as h:
        for row in all_rows:
            h.write(json.dumps(row, ensure_ascii=False)+'\n')
    pairs = [{'pair_id': 'p', 'sources': ['1', '2']}]
    out = root/'out'
    docs, evidence, summaries = f1.build_packets(out, pairs, catalog, ru, {'p': {'1': [[1, 1]]}})
    return out, docs, evidence, summaries, {r['unit_id']: r for r in all_rows}


class F1PrepareTests(unittest.TestCase):
    def test_seed_is_reproducible_under_pool_permutation(self):
        pool, cat = selection_fixture()
        a, _ = f1.rank_select(pool, cat, 'seed')
        b, _ = f1.rank_select(list(reversed(pool)), cat, 'seed')
        self.assertEqual(a, b)
        self.assertEqual(len(a), 24)
        self.assertEqual(len({s for p in a for s in p['sources']}), 48)
        self.assertEqual(sum(p['cohort']=='GENERATION' for p in a), 16)

    def test_source_conflict_yields_partial_without_reuse(self):
        pool, cat = selection_fixture()
        pool[3]['sources'] = sorted([pool[0]['sources'][0], pool[3]['sources'][1]])
        chosen, ledger = f1.rank_select(pool, cat, 'seed')
        self.assertEqual(len(chosen), 23)
        self.assertTrue(any(r['disposition']=='SOURCE_CONFLICT' for r in ledger))

    def test_primary_priority_and_pair_uniqueness_enforced(self):
        pool, cat = selection_fixture()
        pool[0]['risk_tags'] = [0, 1]
        with self.assertRaisesRegex(ValueError, 'priority'):
            f1.rank_select(pool, cat, 'seed')
        pool, cat = selection_fixture()
        pool.append(deepcopy(pool[0]))
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            f1.rank_select(pool, cat, 'seed')

    def test_exact_unicode_and_cross_RU_ranges(self):
        with TemporaryDirectory() as tmp:
            out, docs, ev, _, ru = packet_fixture(Path(tmp))
            mother = f1.read(out/'mother-packets/p.json')
            f1.check_packet(mother, ev, docs, ru)
            q = next(q for q in ev.values() if q['section']==1 and q['start']==0)
            self.assertEqual(q['end'], 1000)
            self.assertEqual(len(q['official_unit_ids']), 2)

    def test_controlled_omission_retains_mother_and_hides_aliases(self):
        with TemporaryDirectory() as tmp:
            out, docs, ev, summaries, ru = packet_fixture(Path(tmp))
            child = f1.read(out/'deliveries/p.json')
            mother = f1.read(out/'mother-packets/p.json')
            self.assertTrue(summaries[0]['withheld'])
            self.assertFalse(child['payload']['A']['coverage']['complete_verified_scope'])
            self.assertLess(len(child['aliases']), len(mother['aliases']))
            self.assertGreater(f1.check_packet(mother, ev, docs, ru), f1.check_packet(child, ev, docs, ru))

    def test_rehashed_spliced_quote_still_rejected(self):
        with TemporaryDirectory() as tmp:
            out, docs, ev, _, ru = packet_fixture(Path(tmp))
            child = f1.read(out/'deliveries/p.json')
            q = ev[child['aliases']['A001']]
            q['text'] = '捏造的目录信息'
            q['text_sha256'] = sha256(q['text'].encode()).hexdigest()
            child['payload']['A']['snippets'][0]['text'] = q['text']
            with self.assertRaises(ValueError):
                f1.check_packet(child, ev, docs, ru)

    def test_undelivered_alias_leak_is_rejected(self):
        with TemporaryDirectory() as tmp:
            out, docs, ev, _, ru = packet_fixture(Path(tmp))
            child = f1.read(out/'deliveries/p.json')
            child['aliases']['A999'] = next(iter(ev))
            with self.assertRaisesRegex(ValueError, 'Undelivered'):
                f1.check_packet(child, ev, docs, ru)

    def test_missing_or_reordered_RU_identity_rejected(self):
        with TemporaryDirectory() as tmp:
            out, docs, ev, _, ru = packet_fixture(Path(tmp))
            mother = f1.read(out/'mother-packets/p.json')
            q = next(q for q in ev.values() if len(q['official_unit_ids']) > 1)
            q['official_unit_ids'].reverse()
            with self.assertRaisesRegex(ValueError, 'intersection'):
                f1.check_packet(mother, ev, docs, ru)

    def test_wrong_RU_record_or_modified_canonical_rejected(self):
        with TemporaryDirectory() as tmp:
            out, docs, ev, _, ru = packet_fixture(Path(tmp))
            mother = f1.read(out/'mother-packets/p.json')
            next(iter(ru.values()))['source']['canonical_address']['record_id'] = 'other'
            with self.assertRaisesRegex(ValueError, 'mismatch'):
                f1.check_packet(mother, ev, docs, ru)
            Path(docs['1']['frozen_canonical_path']).write_text('{}', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'hash'):
                f1.check_packet(mother, ev, docs, ru)

    def test_create_only_artifacts_prevent_silent_overwrite(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp)/'frozen.json'
            f1.put(path, {'a': 1})
            with self.assertRaises(FileExistsError):
                f1.put(path, {'a': 2})

    def test_no_provider_or_embedding_import(self):
        tree = ast.parse(Path(f1.__file__).read_text(encoding='utf-8'))
        allowed = {'collections', 'argparse', 'csv', 'gzip', 'hashlib', 'json', 'pathlib', 'tempfile', 'navi_g1_s1_audit'}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                self.assertTrue({n.name for n in node.names} <= allowed)
            elif isinstance(node, ast.ImportFrom):
                self.assertIn(node.module, allowed)


if __name__ == '__main__':
    unittest.main()
