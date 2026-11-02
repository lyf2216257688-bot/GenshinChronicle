from pathlib import Path
from copy import deepcopy
import tempfile,unittest
from genshin_corpus.retrieval.navi_offline import card_generation as g,anchors,card_contract
from genshin_corpus.evidence_io import write,seal,sha
from genshin_corpus.retrieval.navi_offline import card_normalize as n

class ContractProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=Path(__file__).parent);self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        for jid,strategy in [('old',g.EVIDENCE_32K),('new',g.BOUNDED)]:
            write(self.root/jid/'plan.json',{'strategy':strategy,'prompt_sha256':'p'+jid,'schema_sha256':'s'+jid})
            write(self.root/jid/'result.json',{'state':'CONTENT_VERIFIED'});seal(self.root/jid)
    def data(self):
        def c(cid,level,jid=None,parent=None):
            return {'card_id':cid,'level':level,'parent_id':parent,'parts':[{'job_id':jid,'value':{'overview':'原语义'}}] if jid else [],
                'quote_contract':card_contract.CONTRACT,'source_observations':[{'text':'原文说甲离开。','anchors':[{'evidence_id':'e','quote':'甲离开。','start':0,'end':4}]}],
                'coverage':{'supported_ru_ranges':{'u':[[0,4]]}}}
        old=c('old-card','reading_batch','old');new=c('new-card','document','new');clue=c('clue','clue',parent='old-card')
        return {'status':'COMPLETE','failures':[],'cards':[new,clue],'reading_batches':[old],'synthesis_nodes':[deepcopy(new)]}
    def test_reused_old_part_is_not_upgraded_and_semantics_quotes_bytes_stay(self):
        before=self.data();saved=deepcopy(before);hashes={p:sha(p) for p in self.root.rglob('*') if p.is_file()}
        after,changes=n.normalize(before,self.root)
        self.assertEqual(before,saved);self.assertEqual(n.semantic_bytes(before),n.semantic_bytes(after))
        self.assertEqual(after['reading_batches'][0]['quote_contract'],anchors.CONTRACT)
        self.assertEqual(after['cards'][0]['quote_contract'],card_contract.CONTRACT)
        self.assertEqual(after['cards'][1]['quote_contract'],anchors.CONTRACT)
        self.assertEqual(after['cards'][1]['generation_derivation'],'PARENT_OPEN_QUESTION')
        self.assertEqual(len(changes),2)
        self.assertEqual({p:sha(p) for p in hashes},hashes)
    def test_unsupported_strategy_never_guessed_as_old_contract(self):
        with self.assertRaisesRegex(ValueError,'Unsupported'):n.wire_contract({'strategy':'unknown'})
    def test_conflicting_card_identity_is_rejected(self):
        value=self.data();value['synthesis_nodes'][0]['coverage']={'supported_ru_ranges':{}}
        with self.assertRaisesRegex(ValueError,'Conflicting'):n.normalize(value,self.root)

if __name__=='__main__':unittest.main()
