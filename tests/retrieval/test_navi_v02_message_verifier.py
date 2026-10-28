import unittest
from copy import deepcopy
from genshin_corpus.retrieval.navi_v02_message_verifier import inspect
from tests.retrieval.test_navi_f2_instruction_diagnostic import fixture

class MessageVerificationTests(unittest.TestCase):
    def test_reasoning_conflict_reported_message_remains_checkable(self):
        ev=fixture(conflict=True); r=inspect(ev,deepcopy(ev)); self.assertEqual(r['message'],'PASS');self.assertEqual(r['full_stream'],'FAIL');self.assertTrue(r['reasoning_id_conflicts'])
    def test_message_identity_mismatch_rejected(self):
        ev=fixture();ev[4]['item_id']='other'
        with self.assertRaises(ValueError):inspect(ev,deepcopy(ev))
    def test_wrong_content_index_rejected(self):
        ev=fixture();ev[4]['content_index']=1
        with self.assertRaises(ValueError):inspect(ev,deepcopy(ev))
    def test_sdk_divergence_rejected(self):
        ev=fixture(); sdk=deepcopy(ev);sdk[4]['delta']='changed'
        with self.assertRaises(ValueError):inspect(ev,sdk)
