"""Short model-facing segment IDs must never weaken stable source verification."""
import unittest
from copy import deepcopy
from genshin_corpus.retrieval.navi_offline import anchors,card_generation

class CompactSegmentTests(unittest.TestCase):
    def input(self):
        def q(i,t):
            return dict(id=i,source_id='s1',canonical_sha256='c'*64,unit_id='ru'+i,ru_start=10,ru_end=10+len(t),text=t)
        return dict(mode='read',title='example',children=[],evidence=[q('e1','甲出发。乙归来。'),q('e2','甲出发。丙等待。')])

    def test_aliases_are_compact_unique_and_have_exact_original_locations(self):
        v=self.input();orig=deepcopy(v)
        shown=anchors.selection_view(v)
        ids=[s['id'] for e in shown['evidence'] for s in e['segments']]
        self.assertEqual(ids,['S0001','S0002','S0003','S0004'])
        self.assertTrue(all(len(i)==5 for i in ids))
        self.assertEqual(v,orig)
        mapping=anchors.location_map(v,v['evidence'])
        self.assertEqual(mapping['contract'],'card-evidence-selection-2')
        self.assertEqual(mapping['evidence'][0]['segments'][0]['id'],'S0001')
        self.assertTrue(mapping['evidence'][0]['segments'][0]['stable_id'].startswith('S'))
        self.assertEqual(len(mapping['evidence'][0]['segments'][0]['stable_id']),17)
        self.assertEqual(mapping['evidence'][0]['segments'][0]['start'],0)

    def test_selected_short_ids_bind_only_their_evidence(self):
        v=self.input()
        def extract(a):
            return anchors.materialize({'source_observations':[{'anchors':[a]}]},v,v['evidence'])['source_observations'][0]['anchors'][0]
        good=extract({'evidence_id':'e1','start_segment_id':'S0001','end_segment_id':'S0002'})
        self.assertEqual(good['quote'],'甲出发。乙归来。')
        self.assertEqual(good['ru_start'],10)
        self.assertEqual(good['ru_end'],len(good['quote'])+10)
        for bad in [
            {'evidence_id':'e1','start_segment_id':'S0003','end_segment_id':'S0004'},
            {'evidence_id':'e1','start_segment_id':'Sffff','end_segment_id':'S0002'},
            {'evidence_id':'e1','start_segment_id':'S0002','end_segment_id':'S0001'}
        ]:
            with self.assertRaises(ValueError):extract(bad)

    def test_real_wire_alias_contract_model_and_old_32k_model(self):
        v=self.input()
        a=card_generation.model_view(v,card_generation.EVIDENCE_32K)
        self.assertEqual(a['evidence'][0]['segments'][0]['id'],'S0001')
        self.assertEqual(a['evidence'][1]['segments'][0]['id'],'S0003')
        self.assertEqual(card_generation.wire(v,card_generation.EVIDENCE_32K)['model'],'deepseek-v4.1-flash-1017')
        self.assertEqual(card_generation.wire(v,card_generation.DEEPSEEK_32K)['model'],'deepseek-v4.1-flash')
if __name__=='__main__':unittest.main()
