"""Small, provider-free source-integrity and acceptance-boundary guards for G1."""
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from genshin_corpus.rag import navi_g1_experiment as g1


def fixture():
    text='传说甲离开。\n乙否认因果。'
    record={'record_id':'record','sections':[{'ordinal':2,'units':[{'ordinal':3,'value':{'normalized_text':text}}]}]}
    address={'record_id':'record','source_identity_key':'source:locale:7','section_ordinal':2,'canonical_unit_ordinal':3}
    rows={'ru1':{'unit_id':'ru1','source':{'canonical_address':address},'fragment_selector':{'fragment_index':0},'retrieval_visible_text':text[:7]},
          'ru2':{'unit_id':'ru2','source':{'canonical_address':address},'fragment_selector':{'fragment_index':1},'retrieval_visible_text':text[7:]}}
    snippet={'id':'s','source':'7','record_id':'record','section':2,'unit':3,'start':0,'end':len(text),
             'text':text,'text_sha256':sha256(text.encode()).hexdigest(),'official_unit_ids':['ru1','ru2']}
    return snippet,record,rows


class G1Guards(unittest.TestCase):
    def test_exact_cross_fragment_quote_resolves(self):
        g1.validate_evidence(*fixture())

    def test_edited_or_spliced_quote_fails_even_with_matching_hash(self):
        s,r,rows=fixture();s['text']=s['text'].replace('否认','认定');s['text_sha256']=sha256(s['text'].encode()).hexdigest()
        with self.assertRaisesRegex(ValueError,'exact range'):
            g1.validate_evidence(s,r,rows)

    def test_wrong_ru_endpoint_fails(self):
        s,r,rows=fixture();rows=deepcopy(rows);rows['ru1']['source']['canonical_address']['source_identity_key']='source:locale:8'
        with self.assertRaisesRegex(ValueError,'endpoint'):
            g1.validate_evidence(s,r,rows)

    def test_missing_ru_fragment_fails(self):
        s,r,rows=fixture();s['official_unit_ids']=['ru1']
        with self.assertRaisesRegex(ValueError,'reconstruction'):
            g1.validate_evidence(s,r,rows)

    def test_source_record_identity_is_mandatory(self):
        s,r,rows=fixture();rows=deepcopy(rows);rows['ru1']['source']['canonical_address']['record_id']='other'
        with self.assertRaisesRegex(ValueError,'record mismatch'):
            g1.validate_evidence(s,r,rows)

    def card(self):
        return {'overview':'甲离开，乙否认二者因果。','details':[{'text':'乙否认因果','evidence_ids':['s2']}],
            'open_leads':[],'queries':[],'read_ids':['s1','s2'],'limitations':'传闻不是确定历史'}

    def test_empty_optional_leads_and_queries_are_legal(self):
        g1.validate_card(self.card(),['s1','s2'])

    def test_missing_reading_coverage_fails(self):
        card=self.card();card['read_ids']=['s1']
        with self.assertRaisesRegex(ValueError,'reading'):
            g1.validate_card(card,['s1','s2'])

    def test_unknown_query_evidence_fails(self):
        card=self.card();card['queries']=[{'text':'隐藏目标','evidence_ids':['unknown']}]
        with self.assertRaisesRegex(ValueError,'unbound'):
            g1.validate_card(card,['s1','s2'])

    def test_model_quotes_cannot_become_card_evidence(self):
        card=self.card();card['details'][0]['quote']='我拼接的原文'
        with self.assertRaisesRegex(ValueError,'quotation'):
            g1.validate_card(card,['s1','s2'])

    def test_literary_value_can_be_B_without_history_acceptance(self):
        j={'verdict':'useful','claim_level':'interpretive','relation':'literary_parallel','unsupported_identity_or_cause':False}
        self.assertEqual(g1.grade(j,'PASS'),'B')

    def test_identity_risk_is_not_promoted_by_useful_verdict(self):
        j={'verdict':'useful','claim_level':'textual','relation':'direct_reference','unsupported_identity_or_cause':True}
        self.assertEqual(g1.grade(j,'PASS',qualified=True),'C')

    def test_secondary_or_unqualified_source_cannot_receive_A(self):
        j={'verdict':'useful','claim_level':'textual','relation':'direct_reference'}
        self.assertEqual(g1.grade(j,'PASS',qualified=True),'A')
        self.assertEqual(g1.grade(j,'PASS',secondary=True,qualified=True),'B')
        self.assertEqual(g1.grade(j,'PASS',qualified=False),'B')

    def test_invalid_binding_never_receives_model_grade(self):
        self.assertEqual(g1.grade({'verdict':'useful'},'NOT_VERIFIED'),'U')
        self.assertEqual(g1.grade({'verdict':'insufficient'},'PASS'),'U')
        self.assertEqual(g1.grade({'verdict':'reject'},'PASS'),'D')

    def test_artifact_replay_refuses_changed_content(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp);g1.artifact(root,'f.json',{'value':1})
            g1.artifact(root,'f.json',{'value':1})
            with self.assertRaisesRegex(ValueError,'immutable'):
                g1.artifact(root,'f.json',{'value':2})

    def test_failed_model_result_retains_U_inactive(self):
        value=g1.link_from_review('pair',{}, {}, {'failed':True})
        self.assertEqual(value['grade_recommendation'],'U')
        self.assertEqual(value['human_acceptance'],'NOT_REVIEWED')
        self.assertEqual(value['navigation_status'],'INACTIVE')

    def test_g1_unknown_content_is_quarantined_after_process_restart(self):
        from genshin_corpus.rag.navi_r1_experiment import CircuitOpen,request_fingerprint
        with TemporaryDirectory() as tmp:
            root=Path(tmp);run=root/'p05-navi-g1-fixture';run.mkdir()
            wire={'model':g1.MODEL,'instructions':'frozen','input':'{"source":"same body"}'}
            config={'source_root':str(root/'read-only-source'),'route':'jizhi','identity':'fixture',
                'circuit':{'max_unknown_attempts':3,'max_model_attempts':10,'max_embedding_attempts':0,
                    'max_tokens':100000,'max_accounting_rmb':100,'rates_rmb_per_million':{'model':[0,0],'embedding':[0,0]}}}
            g1.once(run/'protocol.json',config)
            g1.once(run/'attempts'/'old-job'/'issued.json',{'job':'old-job','kind':'model','reserved_tokens':10,
                'reserved_rmb':0,'input_rate':0,'output_rate':0})
            g1.once(run/'attempts'/'old-job'/'terminal.json',{'state':'UNKNOWN','usage':None})
            g1.once(run/'attempts'/'old-job'/'request.json',wire)
            original=g1.qa.PROMPT
            try:
                with patch.object(g1.r1,'environment',return_value={}):
                    campaign=g1.Campaign(run,'audit')
                self.assertIn(request_fingerprint(wire),campaign.quarantined)
                with self.assertRaises(CircuitOpen):
                    campaign.check_quarantine(wire)
            finally:
                g1.qa.PROMPT=original

    def test_future_card_payload_never_exposes_undelivered_ids(self):
        d={'title':'source','source_category':{},'g1_snippet_ids':['delivered','unread-secret-id']}
        snippets={'delivered':{'section':1,'unit':2,'section_name':'chapter','text':'actual text'}}
        payload=g1.card_payload(d,['delivered'],snippets)
        self.assertNotIn('unread-secret-id',str(payload))
        self.assertEqual(payload['coverage']['selected_snippet_count'],2)
        self.assertEqual(payload['coverage']['delivered_ids'],['delivered'])

    def test_resolved_invalid_json_is_not_semantically_repaired(self):
        with self.assertRaisesRegex(ValueError,'not_JSON_object'):
            g1.model_value({'state':'RESOLVED','complete':True,'parse_error':'invalid_JSON'})


if __name__=='__main__':
    unittest.main()
