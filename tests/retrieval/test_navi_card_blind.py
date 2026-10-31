"""Provider-free comparison controls; fixtures are not semantic answer keys."""
from copy import deepcopy
import json
from pathlib import Path
import socket
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from jsonschema.exceptions import ValidationError

from genshin_corpus.retrieval.navi_offline import card_blind as b, card_generation as g, cards_v2 as v
from genshin_corpus.retrieval.navi_offline.common import read, seal, sha, verify, write, copy
from tests.retrieval.test_navi_offline import fixture


class BlindTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name)
        denied=patch.object(socket.socket,'connect',side_effect=AssertionError('No model/network'))
        denied.start();self.addCleanup(denied.stop)

    def prepare(self):
        cp,rp=fixture(self.base)
        root=self.base/'control'; workspace=self.base/'workspace'
        result=b.prepare(root,cp,rp,[{'id':'mihoyo_obc:zh-cn:0','stratum':'fixture'},
                                   {'id':'mihoyo_obc:zh-cn:1','stratum':'fixture'}],workspace=workspace)
        b.export_workspace(root)
        return root,workspace,result

    def test_identical_input_and_readonly_preparation(self):
        root,workspace,result=self.prepare()
        self.assertEqual(result['generation_calls'],0)
        verify(root/'prepared');verify(workspace)
        for case in result['cases']:
            job=read(root/'prepared/cases'/case['case']/'job.json')
            self.assertEqual(job['wire'],g.wire(job['view'],g.DEEPSEEK))
            expected=job['wire']['messages'][0]['content']+'\n\n实际交付原文:\n'+job['wire']['messages'][1]['content']
            self.assertEqual((workspace/case['case']/'request.txt').read_text(encoding='utf8'),expected)
            self.assertEqual(read(workspace/'card.schema.json'),v.SCHEMA)
        with patch.object(b,'_deepseek',side_effect=AssertionError('No dispatch')):
            self.assertTrue(b.run(root,'deepseek')['dry_run'])
        self.assertFalse((root/'runs').exists())

    def test_cli_is_fresh_and_denies_tools(self):
        args=b.gemini_command('agy.exe','C:/fixture','settings.json','log.txt')
        self.assertIn('--new-project',args);self.assertIn('--sandbox',args)
        for flag in ('--continue','--conversation','--add-dir','--dangerously-skip-permissions'):
            self.assertNotIn(flag,args)
        self.assertIn('stream-json',args)
        self.assertEqual(len(b.SETTINGS['permissions']['deny']),6)
        self.assertFalse(b.SETTINGS['allowNonWorkspaceAccess'])

    def test_native_envelope_exact_validation_and_reasoning_separation(self):
        q=[{'id':'source-id','text':'甲归来。','ru_start':0}]
        view={'title':'fixture','mode':'read','card_version':v.VERSION,'evidence':q,'children':[]}
        model_view,_=g.aliases(view);value=v.mock(model_view)
        final={'status':'SUCCESS','num_turns':1,'response':json.dumps(value),
               'structured_output':value,'usage':{'input_tokens':1,'output_tokens':2,'total_tokens':3}}
        raw=json.dumps({'event':'step_update','step_update':{'thinking_delta':'not a Card'}})+'\n'
        raw+=json.dumps({'event':'result','result':final})
        _,valid,_=b.parse_gemini(raw,view,q)
        self.assertEqual(valid['read_ids'],['source-id'])
        self.assertEqual(valid['source_observations'][0]['anchors'][0]['ru_end'],4)
        for mutation in ('foreign','schema','multiturn','failed','mismatch','truncated'):
            bad=deepcopy(final)
            if mutation=='foreign':bad['structured_output']['read_ids']=['E999']
            elif mutation=='schema':bad['structured_output']['hypotheses']=['string']
            elif mutation=='multiturn':bad['num_turns']=2
            elif mutation=='failed':bad['status']='ERROR'
            elif mutation=='truncated':bad['response']='{"overview":'
            else:bad['response']='{}'
            if mutation in {'foreign','schema'}:bad['response']=json.dumps(bad['structured_output'])
            with self.assertRaises((ValueError,ValidationError)):
                b.parse_gemini(json.dumps({'event':'result','result':bad}),view,q)

    def test_recovery_no_dispatch_and_pending_or_failed_reservations_block(self):
        root,_,result=self.prepare();ack=result['prepared_sha256'];calls=[]
        def done(folder,job,**kwargs):
            calls.append(1);mv,_=g.aliases(job['view'])
            value=g.validate_value(v.mock(mv),job['view'],job['evidence'])
            write(folder/'value.json',value)
            return {'state':'CONTENT_VERIFIED','conservative_accounting_tokens':3}
        with patch.object(b,'_deepseek',side_effect=done):
            out=b.run(root,'deepseek',execute=True,ack=ack,max_requests=1,token_budget=500000)
            self.assertEqual(out['new_invocations'],1)
            out=b.run(root,'deepseek',execute=True,ack=ack,max_requests=1,token_budget=500000)
            self.assertEqual(out['new_invocations'],1)
            b.run(root,'deepseek',execute=True,ack=ack,max_requests=1,token_budget=500000)
            self.assertEqual(len(calls),2)
        with self.assertRaises(ValueError):b.run(root,'deepseek',execute=True,ack='wrong')
        (root/'runs/gemini/T01').mkdir(parents=True)
        write(root/'runs/gemini/T01/reservation.json',{'state':'UNKNOWN'})
        with patch.object(b,'_gemini',side_effect=AssertionError('Never resend')):
            with self.assertRaises(FileNotFoundError):b.run(root,'gemini',execute=True,ack=ack)

    def test_immutable_materials_and_private_randomization(self):
        root,workspace,result=self.prepare()
        for provider in ('gemini','deepseek'):
            for case in result['cases']:
                folder=root/'runs'/provider/case['case']
                job=read(root/'prepared/cases'/case['case']/'job.json');mv,_=g.aliases(job['view'])
                value=g.validate_value(v.mock(mv),job['view'],job['evidence'])
                sources=b.Sources(root/'prepared/sources')
                try:
                    card=v.make_card(sources,job['source_id'],'document',None,
                         {'job_id':'fixture','value':value,'authority':'MOCK'},job['evidence'],
                         plan=read(root/'prepared/materials'/case['case']/'reading-plan.json'))
                finally:sources.close()
                write(folder/'value.json',value);write(folder/'card.json',card)
                write(folder/'result.json',{'state':'CONTENT_VERIFIED'})
                seal(folder)
        target=self.base/'review';b.export_review(root,target)
        verify(target)
        key=read(root/'review-key.json')
        for labels in key['assignment'].values():self.assertEqual(set(labels.values()),{'gemini','deepseek'})
        self.assertFalse((target/'review-key.json').exists())
        self.assertNotIn('gemini', (target/'cases.json').read_text(encoding='utf8'))
        with self.assertRaises(ValueError):b.export_review(root,self.base/'another')
        (workspace/'T01/request.txt').write_text('tampered',encoding='utf8')
        with self.assertRaises(ValueError):b.run(root,'gemini')

    def test_gemini_archives_native_stream_and_failed_usage_without_retry(self):
        root,workspace,result=self.prepare();ack=result['prepared_sha256'];calls=[]
        cli=self.base/'agy-fixture.exe';cli.write_bytes(b'not executable; subprocess replaced')
        def process(args,**kwargs):
            calls.append(1)
            case='T01' if len(calls)==1 else 'T02'
            job=read(root/'prepared/cases'/case/'job.json');view,_=g.aliases(job['view'])
            val=v.mock(view)
            if len(calls)==2:val['hypotheses']=['invalid shape']
            final={'status':'SUCCESS','num_turns':1,'response':json.dumps(val),
                   'structured_output':val,'usage':{'input_tokens':10,'output_tokens':20,'total_tokens':30}}
            kwargs['stdout'].write((json.dumps({'event':'result','result':final})+'\n').encode())
            return subprocess.CompletedProcess(args,0)
        with patch.object(b.subprocess,'run',side_effect=process),patch.object(b.requests,'METRO_LEDGER',self.base/'metro-ledger'):
            b.run(root,'gemini',execute=True,ack=ack,cli=cli,max_requests=1,token_budget=500000)
            self.assertEqual(read(root/'runs/gemini/T01/card.json')['level'],'document')
            b.run(root,'gemini',execute=True,ack=ack,cli=cli,max_requests=1,token_budget=500000)
            failed=read(root/'runs/gemini/T02/result.json')
            self.assertEqual(failed['provider_reported_usage']['total_tokens'],30)
            self.assertFalse((root/'runs/gemini/T02/value.json').exists())
            b.run(root,'gemini',execute=True,ack=ack,cli=cli,max_requests=1,token_budget=500000)
            self.assertEqual(len(calls),2)

    def test_append_only_recovery_and_code_revision_guard(self):
        root,workspace,prepared=self.prepare()
        with self.assertRaises(ValueError):b.adopt_revision(root)
        original=root/'runs/gemini/T01';job=read(root/'prepared/cases/T01/job.json')
        mv,_=g.aliases(job['view']);value=v.mock(mv)
        final={'status':'SUCCESS','num_turns':1,'response':json.dumps(value),
               'structured_output':value,'usage':{'input_tokens':10,'output_tokens':20,'total_tokens':30}}
        write(original/'result.json',{'state':'FAILED_OR_UNKNOWN_NO_RESEND','exit_code':0,
              'provider_reported_usage':final['usage'],'reason':'old validation failed'})
        b.text_file(original/'stdout.raw.jsonl',json.dumps({'event':'result','result':final}))
        seal(original);before=sha(original/'complete.json')
        runtime=self.base/'updated-runtime'
        for p in Path(b.__file__).parent.glob('*.py'):copy(p,runtime/p.name)
        with (runtime/'card_generation.py').open('a',encoding='utf8') as f:
            f.write('\n# Simulated source-only validator revision.\n')
        with patch.object(b,'__file__',str(runtime/'card_blind.py')):
            adopted=b.adopt_revision(root)
            self.assertEqual(adopted['changed_files'],['card_generation.py'])
            with patch.object(b.subprocess,'run',side_effect=AssertionError('No request')):
                result=b.recover_gemini(root,'T01')
                self.assertEqual(result['conservative_accounting_tokens'],30)
                self.assertEqual(b.validate_saved(root,'gemini','T01')['state'],'CONTENT_VERIFIED')
                self.assertEqual(b.recover_gemini(root,'T01'),result)
                self.assertEqual(sha(original/'complete.json'),before)
                self.assertEqual(sha(root/'prepared/complete.json'),prepared['prepared_sha256'])
            sent=[]
            def next_case(folder,job,*args,**kwargs):
                sent.append(job['case']);mv,_=g.aliases(job['view'])
                write(folder/'value.json',g.validate_value(v.mock(mv),job['view'],job['evidence']))
                return {'state':'CONTENT_VERIFIED','conservative_accounting_tokens':3}
            with patch.object(b,'_gemini',side_effect=next_case):
                b.run(root,'gemini',execute=True,ack=prepared['prepared_sha256'],max_requests=1)
                self.assertEqual(sent,['T02'])
                b.run(root,'gemini',execute=True,ack=prepared['prepared_sha256'])
                self.assertEqual(sent,['T02'])
            (runtime/'card_generation.py').write_text('changed again',encoding='utf8')
            with self.assertRaises(ValueError):b.run(root,'gemini')
            b.adopt_revision(root)
            self.assertEqual(len(b.revision_dirs(root)),2)
            self.assertEqual(b.validate_saved(root,'gemini','T01')['state'],'CONTENT_VERIFIED')
        with patch.object(b,'code_identity',return_value={}):
            with self.assertRaises(ValueError):b.run(root,'gemini')
        (original/'result.json').write_text('{}',encoding='utf8')
        with self.assertRaises(ValueError):b.validate_saved(root,'gemini','T01')

    def test_completed_content_failure_can_isolate_but_never_become_accepted(self):
        root,workspace,prepared=self.prepare();original=root/'runs/gemini/T01'
        job=read(root/'prepared/cases/T01/job.json');mv,_=g.aliases(job['view']);value=v.mock(mv)
        value['source_observations'][0]['anchors'][0]['quote']='not delivered'
        usage={'input_tokens':7,'output_tokens':11,'total_tokens':18}
        final={'status':'SUCCESS','num_turns':1,'response':json.dumps(value),'structured_output':value,'usage':usage}
        write(original/'result.json',{'state':'FAILED_OR_UNKNOWN_NO_RESEND','exit_code':0,
              'provider_reported_usage':usage,'reason':'Quotation not in delivered source'})
        write(original/'reservation.json',{'state':'RESERVED_NEVER_AUTO_RESEND'})
        b.text_file(original/'stdout.raw.jsonl',json.dumps({'event':'result','result':final}));seal(original)
        revision=root/'runtime-revision'
        write(revision/'revision.json',{'code':b.code_identity(),'prepared_sha256':prepared['prepared_sha256'],
               'existing_records':{'runs/gemini/T01/complete.json':sha(original/'complete.json')}})
        for p in Path(b.__file__).parent.glob('*.py'):copy(p,revision/'execution-code'/p.name)
        b.model_calls.snapshot(revision/'execution-code')
        seal(revision)
        with patch.object(b.subprocess,'run',side_effect=AssertionError('No model')):
            result=b.isolate_completed_failure(root,'T01')
            self.assertEqual(result['conservative_accounting_tokens'],18)
            self.assertEqual(b.existing_result(root,'gemini','T01')['state'],'CONTENT_REJECTED_ISOLATED_NO_RESEND')
            with self.assertRaises(ValueError):b.validate_saved(root,'gemini','T01')
            with self.assertRaises(ValueError):b.recover_gemini(root,'T01')
        sent=[]
        def done(folder,job,*args,**kwargs):
            sent.append(job['case']);mv,_=g.aliases(job['view'])
            write(folder/'value.json',g.validate_value(v.mock(mv),job['view'],job['evidence']))
            return {'state':'CONTENT_VERIFIED','conservative_accounting_tokens':3}
        with patch.object(b,'_gemini',side_effect=done):
            b.run(root,'gemini',execute=True,ack=prepared['prepared_sha256'])
            self.assertEqual(sent,['T02'])
