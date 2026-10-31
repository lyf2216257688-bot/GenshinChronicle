"""Partial-failure anonymous review; synthetic fixtures, no model calls."""
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

from genshin_corpus.retrieval.navi_offline import card_blind as b,card_generation as g,cards_v2 as v
from genshin_corpus.retrieval.navi_offline.common import read,write,seal,sha
from genshin_corpus.retrieval.navi_offline.sources import Sources
from tests.retrieval.test_navi_offline import fixture


class PartialReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name);cp,rp=fixture(self.base)
        self.root=self.base/'control'
        b.prepare(self.root,cp,rp,[{'id':f'mihoyo_obc:zh-cn:{n}','stratum':'fixture'} for n in range(2)],
                  workspace=self.base/'workspace')
        net=patch.object(socket.socket,'connect',side_effect=AssertionError('No network'))
        net.start();self.addCleanup(net.stop)

    def saved(self,provider,case,passed=True,overview=None):
        folder=self.root/'runs'/provider/case
        if passed:
            job=read(self.root/'prepared/cases'/case/'job.json');mv,_=g.aliases(job['view'])
            value=g.validate_value(v.mock(mv),job['view'],job['evidence'])
            if overview is not None:value['overview']=overview
            write(folder/'value.json',value)
            sources=Sources(self.root/'prepared/sources')
            try:
                card=v.make_card(sources,job['source_id'],'document',None,
                      {'job_id':provider,'value':value,'authority':provider},job['evidence'],
                      plan=read(self.root/'prepared/materials'/case/'reading-plan.json'))
            finally:sources.close()
            write(folder/'card.json',card)
        write(folder/'result.json',{'state':'CONTENT_VERIFIED' if passed else 'PROTOCOL_OR_CONTENT_INVALID',
              'content_validation':{'status':'PASS' if passed else 'FAIL'},
              'provider_reported_usage':{'total_tokens':123456},'elapsed_seconds':999,
              'error_codes':[] if passed else ['CONTENT_INVALID:Quotation not in delivered source; native '+provider]})
        seal(folder)

    def test_pair_and_single_retained_without_provider_metadata(self):
        for provider in ('gemini','deepseek'):self.saved(provider,'T01')
        self.saved('gemini','T02',False);self.saved('deepseek','T02')
        before=sha(self.root/'prepared/complete.json');target=self.base/'review'
        b.export_review(self.root,target)
        cases=read(target/'cases.json')['cases']
        self.assertEqual([x['comparison_scope'] for x in cases],['PAIRED_VALID','SINGLE_VALID'])
        self.assertEqual(len(list((target/'T01').glob('?-projection.txt'))),2)
        self.assertEqual(len(list((target/'T02').glob('?.json'))),1)
        self.assertEqual(len(list((target/'T02').glob('?-mechanical.json'))),2)
        for file in target.rglob('*'):
            if file.is_file():
                text=file.read_text(encoding='utf8').casefold()
                for name in ('gemini','deepseek','tokenmetro','antigravity','123456'):
                    self.assertNotIn(name,text)
        self.assertEqual(sha(self.root/'prepared/complete.json'),before)
        failed=[read(f) for f in (target/'T02').glob('?-mechanical.json') if read(f)['status']=='FAIL'][0]
        self.assertEqual(failed['failure_categories'],['QUOTATION_NOT_EXACT'])
        self.assertIsNone(failed['semantic_score'])
        self.assertTrue((self.root/'review-key.json').exists())
        self.assertFalse((target/'review-key.json').exists())
        with self.assertRaises(ValueError):b.export_review(self.root,self.base/'again')

    def test_both_failed_and_unsent_pending_are_not_zero_semantic_scores(self):
        for provider in ('gemini','deepseek'):self.saved(provider,'T01',False)
        pending=self.root/'runs/gemini/T02';write(pending/'reservation.json',{'state':'UNKNOWN'})
        write(pending/'result.json',{'state':'CONTENT_VERIFIED'}) # Unsealed is never accepted.
        target=self.base/'review';b.export_review(self.root,target)
        self.assertEqual([x['comparison_scope'] for x in read(target/'cases.json')['cases']],
                         ['NO_VALID_CARD','NO_VALID_CARD'])
        for case in ('T01','T02'):
            self.assertFalse(list((target/case).glob('?.json')))
            for f in (target/case).glob('?-mechanical.json'):
                self.assertFalse(read(f)['semantic_eligible']);self.assertIsNone(read(f)['semantic_score'])

    def test_model_self_identification_is_not_silently_redacted(self):
        for provider in ('gemini','deepseek'):
            for case in ('T01','T02'):self.saved(provider,case,overview='I am DeepSeek' if provider=='deepseek' else None)
        target=self.base/'review'
        with self.assertRaises(ValueError):b.export_review(self.root,target)
        self.assertFalse(target.exists())
        self.assertFalse((self.root/'review-key.json').exists())

    def test_explicit_five_case_unknown_exception_never_resends_or_zeroes_usage(self):
        base=self.base/'five';base.mkdir();cp,rp=fixture(base)
        root=base/'control'
        prepared=b.prepare(root,cp,rp,[{'id':f'mihoyo_obc:zh-cn:{n%2}','stratum':'budget-fixture'} for n in range(5)],
                           workspace=base/'workspace')
        b.export_workspace(root)
        first=root/'runs/deepseek/T01'
        write(first/'reservation.json',{'state':'RESERVED_NEVER_AUTO_RESEND'})
        write(first/'result.json',{'state':'PROTOCOL_OR_CONTENT_INVALID','http_status':524,
                                   'calls':1,'provider_reported_usage':'UNKNOWN'})
        seal(first);before=sha(first/'complete.json')
        with patch.object(b,'check_revision',return_value={}):b.authorize_independent_deepseek(root)
        sent=[]
        def dispatch(folder,job):
            sent.append(job['case'])
            if job['case']=='T03':
                return {'state':'PROTOCOL_OR_CONTENT_INVALID','provider_reported_usage':'UNKNOWN','http_status':524,'calls':1}
            mv,_=g.aliases(job['view']);write(folder/'value.json',g.validate_value(v.mock(mv),job['view'],job['evidence']))
            return {'state':'CONTENT_VERIFIED','conservative_accounting_tokens':3}
        with patch.object(b,'_deepseek',side_effect=dispatch):
            with self.assertRaises(ValueError):b.run(root,'deepseek',execute=True,ack=prepared['prepared_sha256'])
            result=b.run(root,'deepseek',execute=True,ack=prepared['prepared_sha256'],max_requests=4,independent_unknown=True)
            self.assertEqual(sent,['T02','T03','T04','T05']);self.assertEqual(result['new_invocations'],4)
            self.assertEqual(result['unknown_usage_cases'],['T01','T03'])
            self.assertEqual(result['total_token_accounting'],'UNKNOWN')
            self.assertEqual(b.run(root,'deepseek',execute=True,ack=prepared['prepared_sha256'],independent_unknown=True)['new_invocations'],0)
            self.assertEqual(len(sent),4);self.assertEqual(sha(first/'complete.json'),before)
            with self.assertRaises(ValueError):b.run(root,'gemini',execute=True,ack=prepared['prepared_sha256'],independent_unknown=True)


if __name__=='__main__':unittest.main()
