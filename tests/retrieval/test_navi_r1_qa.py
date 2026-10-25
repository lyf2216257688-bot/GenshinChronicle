"""Provider-free guards for the independent R1-QA review contract."""
import json
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from genshin_corpus.rag.navi_r1_qa import chunks,pid,context_for,validate,QACampaign,DeepSeekCampaign,once,extract_response_text,decode_review_json,pair_statistics,quote_diagnosis,clarify
from genshin_corpus.rag.navi_r1_experiment import CircuitOpen


class QAGuards(unittest.TestCase):
    def test_format_clarification_cannot_select_unknown_execution(self):
        with TemporaryDirectory() as tmp:
            run=Path(tmp)
            once(run/'adjudications-initial.json',{'pairs':[{'pair_id':'unknown','primary':{'execution_state':'UNKNOWN',
                'mechanical_quote_validation':'NOT_VERIFIED','model_semantic_review':'NOT_REVIEWED'}}]})
            once(run/'documents.json',{'documents':{}});once(run/'snippets.json',{});once(run/'candidates.json',{'pairs':[]})
            fake=SimpleNamespace(totals=lambda:{},review=unittest.mock.Mock())
            with patch('genshin_corpus.rag.navi_r1_qa.DeepSeekCampaign',return_value=fake):clarify(run)
            fake.review.assert_not_called()
            self.assertEqual(json.loads((run/'clarification-summary.json').read_text())['selected'],0)

    def test_unreviewed_pairs_stay_in_metric_denominator(self):
        good={'mechanical_quote_validation':'PASS','model_semantic_review':'REVIEWED_NOT_HUMAN_GOLD',
            'judgment':{'verdict':'useful','relation':'background'}}
        duplicate=good|{'judgment':{'verdict':'useful','relation':'duplicate'}}
        reviews={'a':{'primary':good},'b':{'primary':duplicate},'c':{'primary':{'mechanical_quote_validation':'NOT_VERIFIED'}}}
        stats=pair_statistics(set(reviews),reviews)
        self.assertEqual(stats['source_pair_count'],3)
        self.assertEqual(stats['counts']['unreviewed'],1)
        self.assertEqual(stats['non_duplicate_story_useful'],1)
        self.assertEqual(stats['model_useful_completion_bounds'],[2/3,1])

    def test_legacy_whitespace_diagnosis_extracts_original_without_rewriting(self):
        quote='原文前句后句';payload={'segments':[{'id':'u1','text':'原文前句\n后句'}]}
        result=quote_diagnosis(quote,['u1'],payload)
        self.assertEqual(result['category'],'whitespace_difference')
        self.assertEqual(result['candidate_spans'][0]['exact_source_text'],'原文前句\n后句')
        self.assertEqual(quote,'原文前句后句')
        self.assertIn('NOT_SEMANTIC',result['status'])

    def test_complete_json_fence_is_packaging_not_content_repair(self):
        value,wrapped=decode_review_json('```json\n{"explanation":"原文无需修改"}\n```')
        self.assertEqual(value['explanation'],'原文无需修改');self.assertTrue(wrapped)
        for invalid in ['before {"x":1}', '{"x":1}{"x":2}', '```json\n{"x":1}\n```\nafter','{"x": "broken)']:
            with self.assertRaises(ValueError):decode_review_json(invalid)

    def test_deepseek_unknown_is_cached_and_not_reissued_under_another_name(self):
        with TemporaryDirectory() as tmp:
            run=Path(tmp)/'p05-navi-r1-qa-fixture'
            once(run/'protocol.json',{'route':'jizhi','identity':'fixture','circuit':{'max_unknown_attempts':100,
                'max_model_attempts':10,'max_embedding_attempts':0,'max_tokens':1000000,'max_accounting_rmb':100,
                'rates_rmb_per_million':{'model':[15,80],'embedding':[5,0]}}})
            campaign=DeepSeekCampaign(run)
            campaign.env={'JIZHI_API_KEY':'fixture-key','JIZHI_BASE_URL':'https://jizhiapi.site/v1'}
            campaign.clients.deepseek_client=SimpleNamespace()
            calls=[]
            class SDK:
                def __init__(self,*args,**kwargs):
                    self_test.assertEqual(kwargs['max_attempts'],1)
                    self_test.assertFalse(kwargs['format_retry_enabled'])
                def __call__(self,*args,**kwargs):
                    calls.append(1)
                    return {'execution_state':'EXECUTION UNKNOWN','complete':False,'usage':'UNKNOWN','error':{'type':'fixture'}}
            self_test=self
            with patch('genshin_corpus.rag.navi_e2_experiment.E2SDK',SDK):
                self.assertEqual(campaign.review('one',{'source':'body'},'deepseek-v4.1-flash')['state'],'UNKNOWN')
                self.assertEqual(campaign.review('one',{'source':'body'},'deepseek-v4.1-flash')['state'],'UNKNOWN')
                with self.assertRaises(CircuitOpen):campaign.review('renamed',{'source':'body'},'deepseek-v4.1-flash')
            self.assertEqual(len(calls),1)

    def response_events(self):
        text='{"verdict":"reject"}'
        item={'id':'item','role':'assistant','type':'message','status':'completed','content':[{'type':'output_text','text':text}]}
        terminal=json.loads(json.dumps(item));terminal['phase']=None;terminal['content'][0]['annotations']=None
        return [{'type':'response.created','response':{'id':'response'}},
            {'type':'response.output_text.delta','item_id':'item','output_index':0,'content_index':0,'delta':text},
            {'type':'response.output_text.done','item_id':'item','output_index':0,'content_index':0,'text':text},
            {'type':'response.content_part.done','item_id':'item','output_index':0,'content_index':0,'part':{'text':text}},
            {'type':'response.output_item.done','item':item},
            {'type':'response.completed','response':{'id':'response','model':'deepseek-v4.1-flash','status':'completed',
                'output':[terminal],'usage':{'input_tokens':10,'output_tokens':5,'total_tokens':15}}}]

    def test_empty_response_metadata_reconciliation_keeps_identical_text(self):
        text,usage=extract_response_text(self.response_events(),'deepseek-v4.1-flash')
        self.assertEqual(text,'{"verdict":"reject"}');self.assertEqual(usage['total_tokens'],15)

    def test_response_text_or_usage_conflict_is_not_reconciled(self):
        for mutate in [lambda e:e[-1]['response']['output'][0]['content'][0].update(text='changed'),
            lambda e:e[-1]['response']['usage'].update(total_tokens=16),
            lambda e:e[1].update(item_id='other')]:
            events=self.response_events();mutate(events)
            with self.assertRaises(ValueError):extract_response_text(events,'deepseek-v4.1-flash')

    def test_nonempty_metadata_or_ambiguous_response_is_not_reconciled(self):
        events=self.response_events();events[-1]['response']['output'][0]['content'][0]['annotations']=['new']
        with self.assertRaises(ValueError):extract_response_text(events,'deepseek-v4.1-flash')
        events=self.response_events();events[-1]['response']['output']*=2
        with self.assertRaises(ValueError):extract_response_text(events,'deepseek-v4.1-flash')
        events=self.response_events()[:-1]
        with self.assertRaises(ValueError):extract_response_text(events,'deepseek-v4.1-flash')

    def snippet(self,text='三姐妹驾车，灾祸来临。'):
        return {'id':'1:0001','text':text,'text_sha256':sha256(text.encode()).hexdigest(),'official_unit_ids':['formal-ru'],'section_name':'故事'}

    def judgment(self,verdict='useful'):
        return {'verdict':verdict,'relation':'literary_parallel','claim_level':'interpretive',
            'a_ids':['A001'],'b_ids':['B001'],'explanation':'具体对应',
            'increment_a_from_b':'补充','increment_b_from_a':'对照','counterevidence':'非确定历史',
            'proposed_claim':'有价值联读','literary_value':True,'unsupported_identity_or_cause':False}

    def test_exact_range_partition_preserves_all_unicode_and_whitespace(self):
        text=('三月。\r\n「长句」 不要改变空格！'*80)+'末尾\n'
        parts=list(chunks(text,140))
        self.assertEqual(''.join(t for a,b,t in parts),text)
        for a,b,t in parts:self.assertEqual(text[a:b],t)

    def test_unordered_source_pair_keeps_stable_identity(self):
        self.assertEqual(pid('10','2'),pid('2','10'))
        self.assertNotEqual(pid('10','2'),pid('10','3'))

    def test_model_selects_ids_and_program_owns_quotes(self):
        s=self.snippet();v=self.judgment();v['a_quote']='伪造内容不得作为引文'
        result=validate(v,{'A001':'1:0001','B001':'1:0001'},{'1:0001':s})
        self.assertEqual(result['evidence']['A'][0]['text'],s['text'])
        self.assertEqual(result['human_semantic_acceptance'],'NOT_REVIEWED')

    def test_rejection_also_requires_both_endpoint_evidence(self):
        v=self.judgment('reject');v['b_ids']=[]
        with self.assertRaises(ValueError):validate(v,{'A001':'1:0001'},{'1:0001':self.snippet()})

    def test_foreign_endpoint_and_unknown_ids_rejected(self):
        for ids in [['A001'],['B999']]:
            v=self.judgment();v['b_ids']=ids
            with self.assertRaises(ValueError):validate(v,{'A001':'1:0001','B001':'1:0001'},{'1:0001':self.snippet()})

    def test_tampered_source_snippet_rejected(self):
        s=self.snippet();s['text']='changed'
        with self.assertRaises(ValueError):validate(self.judgment(),{'A001':'1:0001','B001':'1:0001'},{'1:0001':s})

    def test_semantic_label_error_does_not_erase_mechanical_evidence(self):
        v=self.judgment();v['relation']='unknown_model_label'
        result=validate(v,{'A001':'1:0001','B001':'1:0001'},{'1:0001':self.snippet()})
        self.assertEqual(result['mechanical_quote_validation'],'PASS')
        self.assertEqual(result['model_semantic_review'],'MODEL_OUTPUT_SCHEMA_INCOMPLETE')
        self.assertEqual(result['judgment']['relation'],'unknown_model_label')

    def test_many_real_ids_remain_valid_without_silent_truncation(self):
        mapping={f'A{i:03d}':'1:0001' for i in range(1,20)}|{'B001':'1:0001'}
        v=self.judgment();v['a_ids']=[i for i in mapping if i.startswith('A')]
        result=validate(v,mapping,{'1:0001':self.snippet()})
        self.assertEqual(len(result['evidence']['A']),19)

    def test_payload_contains_no_method_rank_or_old_verdict(self):
        docs={cid:{'title':cid,'body_policy':'verified','snippet_ids':[cid]} for cid in ['1','2']}
        snippets={cid:self.snippet()|{'id':cid} for cid in docs}
        pair={'sources':['1','2'],'pair_id':pid('1','2'),'discoveries':[{'method':'secret','rank':1}],'legacy':[{'verdict':'reject'}]}
        payload,_,_=context_for(pair,docs,snippets)
        serialized=json.dumps(payload)
        for forbidden in ['secret','rank','legacy','verdict']:self.assertNotIn(forbidden,serialized)

    def test_qa_unresolved_request_is_quarantined_across_job_names(self):
        with TemporaryDirectory() as tmp:
            run=Path(tmp)/'p05-navi-r1-qa-fixture'
            once(run/'protocol.json',{'circuit':{'max_unknown_attempts':100,'max_model_attempts':10,
                'max_embedding_attempts':0,'max_tokens':1000000,'max_accounting_rmb':100,
                'rates_rmb_per_million':{'model':[15,80],'embedding':[5,0]}}})
            wire={'model':'qwen3.8-max','messages':[{'role':'user','content':'test'}]}
            once(run/'attempts/old/issued.json',{'kind':'model','reserved_tokens':10,'reserved_rmb':1,'input_rate':15,'output_rate':80})
            once(run/'attempts/old/request.json',wire)
            c=QACampaign(run,audit_only=True)
            with self.assertRaises(CircuitOpen):c.check_quarantine(wire)


if __name__=='__main__':unittest.main()
