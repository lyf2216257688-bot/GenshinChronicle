import unittest
from genshin_corpus.retrieval.navi_f2_response_observation import classify_terminal

class TerminalObservationTests(unittest.TestCase):
    def test_known_output_truncation_not_unknown(self):
        r={'id':'r1','model':'m','status':'incomplete','incomplete_details':{'reason':'max_output_tokens'},'usage':{'input_tokens':3,'output_tokens':4,'total_tokens':7}}
        self.assertEqual(classify_terminal(r,'m')['state'],'INCOMPLETE')
        self.assertEqual(classify_terminal(r,'m')['usage']['total_tokens'],7)
    def test_missing_identity_remains_unknown(self):
        self.assertEqual(classify_terminal({'status':'completed'},'m')['state'],'UNKNOWN')
    def test_invalid_usage_not_invented(self):
        r={'id':'r','model':'m','status':'completed','usage':{'input_tokens':1,'output_tokens':1,'total_tokens':9}}
        self.assertEqual(classify_terminal(r,'m')['usage'],'UNKNOWN')
