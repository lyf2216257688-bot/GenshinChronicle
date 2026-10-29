"""Small source-bound synthetic fixtures, real SDK with MockTransport only."""
from copy import deepcopy
import gzip
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx

from genshin_corpus.retrieval import navi_v03_runner as runner
from genshin_corpus.retrieval.navi_f1_prepare import sha, value_hash, read
from tests.retrieval.test_navi_prompt_candidate import cases
from tests.retrieval.test_navi_f2_instruction_diagnostic import fixture as stream_fixture
from tests.retrieval.test_navi_f2_sdk_transport import Pieces, encode


def inputs(root, mode):
    """Synthetic Canonical/RU lineage, not a reconstructed official snapshot."""
    view, evidence, rows, paths, aliases = {}, {}, {}, {}, {}
    for side, source in [('A','1'),('B','2')]:
        text='合成剧情片段'+side
        record={'record_id':'record-'+source,'sections':[{'ordinal':0,'units':[
            {'ordinal':0,'value':{'normalized_text':text}}]}]}
        path=root/(source+'.json');runner.put(path,record);paths[source]=path
        alias=side+'1';eid='evidence-'+source;uid='ru-'+source;aliases[alias]=eid
        view[side]={'title':'合成来源'+side,'snippets':[{'id':alias,'text':text,'section':'剧情'}]}
        evidence[eid]={'id':eid,'source':source,'record_id':record['record_id'],'canonical_sha256':sha(path),
                       'section':0,'unit':0,'start':0,'end':len(text),'text':text,
                       'text_sha256':runner.candidate.sha256(text.encode()).hexdigest(),'official_unit_ids':[uid]}
        rows[uid]={'unit_id':uid,'retrieval_visible_text':text,'fragment_selector':{'kind':'whole'},
                   'source':{'canonical_address':{'record_id':record['record_id'],
                    'source_identity_key':'mihoyo_obc:zh-cn:'+source,'section_ordinal':0,'canonical_unit_ordinal':0}}}
    output=deepcopy(cases()[0]['authored_output'])
    for side in ['A','B']:
        output['reading'][side]={'read_ids':[side+'1'],'unread_ids':[],'limitations':'仅合成测试范围'}
        output['source_roles'][side][0]['evidence_ids']=[side+'1']
    p=output['propositions'][0];p.update(text='合成文本的剧情承接',a_ids=['A1'],b_ids=['B1'])
    if mode=='review':
        c={'id':'original-1','text':p['text'],'role':'ORIGINAL_CORE',
           'text_sha256':runner.candidate.sha256(p['text'].encode()).hexdigest()}
        view['candidates']=[c];p.update(candidate_id=c['id'],role='ORIGINAL_CORE')
    packet={'sides':{'A':'1','B':'2'},'payload':{s:view[s] for s in ['A','B']},'aliases':aliases}
    return view,{'packet':packet,'evidence':evidence,'ru_rows':rows},paths,output


class V03RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        files={name:{'sha256':sha(runner.REPO/name),'bytes':(runner.REPO/name).stat().st_size} for name in runner.ENGINE}
        self.freeze=self.root/'candidate-freeze.json'
        runner.put(self.freeze,{'version':runner.candidate.VERSION,'policy':runner.candidate.POLICY,
                                'files':files,'freeze_identity_sha256':value_hash(files)})
        self.count=0;self.sent=[]
    def tearDown(self):self.temp.cleanup()
    def prepared(self,mode='generation',fmt='json_object'):
        self.count+=1;source=self.root/('source-'+str(self.count));source.mkdir()
        view,binding,paths,output=inputs(source,mode)
        plan=self.root/('plan-'+str(self.count))
        runner.prepare(plan,view,binding,paths,request_id='test-'+str(self.count),mode=mode,
                       format_mode=fmt,candidate_freeze=self.freeze)
        return plan,output
    def execute(self,plan,handler,**kwargs):
        def counted(req):self.sent.append(json.loads(req.content));return handler(req)
        with patch.object(runner.time,'sleep'):
            return runner.run_case(plan,self.root/kwargs.pop('out','run'),api_key='offline-key-value',
                send_authorized=True,acknowledge_plan_sha256=sha(plan/'plan.json'),ledger=self.root/'ledger',
                transport_factory=lambda:httpx.MockTransport(counted),**kwargs)
    def response(self,data,*,compressed=False,fail=False):
        raw=encode(data);headers={'content-type':'text/event-stream'}
        if compressed:raw=gzip.compress(raw);headers['content-encoding']='gzip'
        return httpx.Response(200,headers=headers,stream=Pieces(raw,fail))
    def attempt(self,number=1):return self.root/'run/attempts'/('attempt-%03d'%number)

    def test_success_both_formats_and_modes_replay_exact_v03(self):
        for mode in ['generation','review']:
            for fmt in ['json_object','json_schema']:
                with self.subTest(mode=mode,fmt=fmt),tempfile.TemporaryDirectory() as tmp:
                    # Isolated persistent ledger per independent test universe.
                    old=self.root;self.root=Path(tmp)
                    try:
                        plan,out=self.prepared(mode,fmt)
                        result=self.execute(plan,lambda req:self.response(stream_fixture(json.dumps(out,ensure_ascii=False))))
                        self.assertEqual(result['state'],'CONTENT_VERIFIED')
                        self.assertEqual(result['attempts'],1)
                        self.assertEqual(self.sent[-1],read(plan/'request.json')['wire'])
                        self.assertEqual(self.sent[-1]['text']['format']['type'],fmt)
                        text=self.sent[-1]['input'][0]['content'][0]['text']
                        self.assertIn(runner.candidate.prompt(mode),text)
                        self.assertNotIn('完整字段模板',text)
                        self.assertEqual(runner.audit_run(self.root/'run')['archive_integrity'],'PASS')
                        self.assertFalse(any(b'offline-key-value' in p.read_bytes() for p in self.root.rglob('*') if p.is_file()))
                    finally:self.root=old

    def test_tls_pre_send_retries_three_identical_attempts_then_success(self):
        plan,out=self.prepared()
        def handler(req):
            if len(self.sent)<3:
                req.extensions['trace']('connection.start_tls.failed',{})
                raise httpx.ConnectError('TLS offline-key-value',request=req)
            return self.response(stream_fixture(json.dumps(out,ensure_ascii=False)))
        r=self.execute(plan,handler)
        self.assertEqual((r['state'],r['attempts']),('CONTENT_VERIFIED',3))
        self.assertTrue(all(w==self.sent[0] for w in self.sent))
        self.assertEqual(len({sha(self.attempt(n)/'request.body.bin') for n in [1,2,3]}),1)
        for n in [1,2]:
            decision=read(self.root/('run/retry-decision-%03d.json'%n))
            self.assertTrue(decision['pre_send_proven']);self.assertTrue(decision['retry'])
        self.assertEqual(runner.audit_run(self.root/'run')['archive_integrity'],'PASS')

    def test_exhausted_tcp_pre_send_stops_at_three(self):
        plan,_=self.prepared()
        def handler(req):
            req.extensions['trace']('connection.connect_tcp.failed',{})
            raise httpx.ConnectTimeout('offline',request=req)
        r=self.execute(plan,handler)
        self.assertEqual((r['state'],r['attempts'],r['usage']),('NOT_SENT',3,'NOT_ISSUED'))
        self.assertFalse(read(self.root/'run/retry-decision-003.json')['retry'])
        self.assertEqual(runner.audit_run(self.root/'run')['archive_integrity'],'PASS')

    def test_unknown_without_trace_is_not_retried(self):
        plan,_=self.prepared()
        def handler(req):raise httpx.ConnectTimeout('no conclusive trace',request=req)
        r=self.execute(plan,handler)
        self.assertEqual((r['state'],r['attempts']),('UNKNOWN',1))

    def test_send_or_receive_trace_forbids_retry_even_with_tls_failure(self):
        for marker in ['http11.send_request_headers.started','http11.send_request_headers.complete',
                       'http2.send_request_body.failed','http11.receive_response_headers.started']:
            with self.subTest(marker=marker),tempfile.TemporaryDirectory() as tmp:
                old=self.root;self.root=Path(tmp)
                try:
                    plan,_=self.prepared()
                    def handler(req):
                        for event in [marker,'connection.start_tls.failed']:req.extensions['trace'](event,{})
                        raise httpx.ReadTimeout('sent or uncertain',request=req)
                    r=self.execute(plan,handler)
                    self.assertEqual((r['state'],r['attempts']),('UNKNOWN',1))
                finally:self.root=old

    def test_any_http_status_prevents_retry_and_archives_error_bytes(self):
        for status in [302,403,429,500]:
            with self.subTest(status=status),tempfile.TemporaryDirectory() as tmp:
                old=self.root;self.root=Path(tmp)
                try:
                    plan,_=self.prepared()
                    def handler(req):
                        req.extensions['trace']('connection.start_tls.failed',{})
                        return httpx.Response(status,headers={'location':'https://forbidden.invalid'},stream=Pieces(b'blocked'))
                    r=self.execute(plan,handler)
                    self.assertEqual((r['state'],r['attempts']),('HTTP_REJECTED',1))
                    self.assertEqual((self.attempt()/'response.body.raw').read_bytes(),b'blocked')
                finally:self.root=old

    def test_partial_stream_and_truncation_never_retry(self):
        for kind in ['disconnect','frame','missing_terminal','terminal_truncated','failed']:
            with self.subTest(kind=kind),tempfile.TemporaryDirectory() as tmp:
                old=self.root;self.root=Path(tmp)
                try:
                    plan,out=self.prepared();events=stream_fixture(json.dumps(out))
                    if kind=='missing_terminal':events.pop()
                    if kind in ['terminal_truncated','failed']:
                        events[-1]['type']='response.incomplete' if kind=='terminal_truncated' else 'response.failed'
                        events[-1]['response']['status']='incomplete' if kind=='terminal_truncated' else 'failed'
                        events[-1]['response']['incomplete_details']={'reason':'max_output_tokens'}
                    def handler(req):
                        if kind=='frame':return httpx.Response(200,headers={'content-type':'text/event-stream'},stream=Pieces(encode(events)[:-1]))
                        return self.response(events,fail=kind=='disconnect')
                    r=self.execute(plan,handler)
                    self.assertEqual(r['attempts'],1)
                    self.assertEqual(r['state'],'OUTPUT_TRUNCATED' if kind=='terminal_truncated' else 'STREAM_OR_VALIDATION_FAILURE')
                    self.assertTrue((self.attempt()/'response.body.raw').is_file())
                    if kind=='terminal_truncated':self.assertEqual(r['terminal_observations'][0]['usage']['total_tokens'],5)
                finally:self.root=old

    def test_full_stream_rejects_reasoning_identity_extra_item_order_sequence(self):
        for kind in ['reasoning','extra_index','item_snapshot','order','sequence','model','response_id','content_index','unknown_event']:
            with self.subTest(kind=kind),tempfile.TemporaryDirectory() as tmp:
                old=self.root;self.root=Path(tmp)
                try:
                    plan,out=self.prepared();events=stream_fixture(json.dumps(out),conflict=kind=='reasoning')
                    if kind=='extra_index':events.insert(2,{'type':'response.output_item.added','output_index':9,'item':{'id':'ghost','type':'reasoning'}})
                    elif kind=='item_snapshot':events[2]['item']=deepcopy(events[2]['item']);events[2]['item']['summary']=[{'type':'summary_text','text':'different'}]
                    elif kind=='order':events[1],events[2]=events[2],events[1]
                    elif kind=='sequence':
                        for n,e in enumerate(events):e['sequence_number']=n
                        events[2]['sequence_number']=90
                    elif kind=='model':events[-1]['response']['model']='wrong'
                    elif kind=='response_id':events[0]['response']['id']='wrong'
                    elif kind=='content_index':events[4]['content_index']=1
                    elif kind=='unknown_event':events.insert(2,{'type':'response.unrecognized_critical_event'})
                    r=self.execute(plan,lambda req:self.response(events))
                    self.assertEqual((r['state'],r['attempts']),('STREAM_OR_VALIDATION_FAILURE',1))
                finally:self.root=old

    def test_usage_booleans_sums_limits_and_reasoning_fail(self):
        for usage in [{'input_tokens':True,'output_tokens':2,'total_tokens':3},
                      {'input_tokens':3,'output_tokens':2,'total_tokens':99},
                      {'input_tokens':3,'output_tokens':30001,'total_tokens':30004},
                      {'input_tokens':3,'output_tokens':2,'total_tokens':5,'output_tokens_details':{'reasoning_tokens':3}}]:
            with self.subTest(usage=usage),tempfile.TemporaryDirectory() as tmp:
                old=self.root;self.root=Path(tmp)
                try:
                    plan,out=self.prepared();events=stream_fixture(json.dumps(out));events[-1]['response']['usage']=usage
                    r=self.execute(plan,lambda req:self.response(events))
                    self.assertEqual((r['state'],r['attempts']),('STREAM_OR_VALIDATION_FAILURE',1))
                    self.assertEqual(r['terminal_observations'][0]['usage'],usage)
                finally:self.root=old

    def test_json_duplicate_keys_fences_and_illegal_generation_role_stop(self):
        for kind in ['duplicate','fence','role','summary','binding','missing_field']:
            with self.subTest(kind=kind),tempfile.TemporaryDirectory() as tmp:
                old=self.root;self.root=Path(tmp)
                try:
                    plan,out=self.prepared()
                    if kind=='role':out['propositions'][0]['role']='COUNTERFACTUAL_GUARD'
                    elif kind=='summary':out['decision']='CANDIDATES'
                    elif kind=='binding':out['propositions'][0]['a_ids']=['B1']
                    elif kind=='missing_field':out['propositions'][0].pop('support')
                    text=json.dumps(out)
                    if kind=='duplicate':text=text[:-1]+',"no_link_reason":""}'
                    elif kind=='fence':text='```json\n'+text+'\n```'
                    r=self.execute(plan,lambda req:self.response(stream_fixture(text)))
                    self.assertEqual((r['state'],r['attempts']),('CONTENT_INVALID',1))
                    self.assertEqual(r['full_stream'],'PASS')
                    self.assertFalse((self.attempt()/'candidate.json').exists())
                finally:self.root=old

    def test_review_original_text_cannot_be_rewritten_in_place(self):
        plan,out=self.prepared('review');out['propositions'][0]['text']='另一个安全句，不能替代原句'
        r=self.execute(plan,lambda req:self.response(stream_fixture(json.dumps(out))))
        self.assertEqual((r['state'],r['content_status']),('CONTENT_INVALID','CONTEXT_CONTRACT_FAILURE'))

    def test_program_aggregation_stays_proposition_level(self):
        plan,out=self.prepared();u=deepcopy(out['propositions'][0]);u.update(grade='U',support='UNDETERMINED',evidence_gap='合成必要缺口')
        u['story']['status']='UNKNOWN';out['propositions'].append(u)
        r=self.execute(plan,lambda req:self.response(stream_fixture(json.dumps(out))))
        self.assertEqual(r['state'],'CONTENT_VERIFIED')
        candidate=read(self.attempt()/'candidate.json')
        self.assertEqual(candidate['decision'],'CANDIDATES')
        self.assertEqual([p['release'] for p in candidate['propositions']],['ELIGIBLE_PENDING_RELEASE','UNRESOLVED'])

    def test_success_unknown_and_pending_reservations_block_renamed_reissue(self):
        plan,out=self.prepared();self.execute(plan,lambda req:self.response(stream_fixture(json.dumps(out))))
        count=len(self.sent)
        with self.assertRaises(FileExistsError):self.execute(plan,lambda req:None,out='renamed')
        with self.assertRaises(ValueError):self.execute(plan,lambda req:None)
        self.assertEqual(len(self.sent),count)
        plan2,_=self.prepared();fingerprint=read(plan2/'plan.json')['wire_fingerprint']
        self.assertEqual(fingerprint,read(plan/'plan.json')['wire_fingerprint'])
        with self.assertRaises(FileExistsError):self.execute(plan2,lambda req:None,out='different-plan')

    def test_crash_pending_ledger_blocks_even_without_result(self):
        plan,_=self.prepared();fp=read(plan/'plan.json')['wire_fingerprint']
        (self.root/'ledger'/fp).mkdir(parents=True)
        with self.assertRaises(FileExistsError):self.execute(plan,lambda req:None)
        self.assertFalse(self.sent)

    def test_authorization_and_plan_tampering_fail_before_client(self):
        plan,_=self.prepared()
        with self.assertRaises(ValueError):runner.run_case(plan,self.root/'run',api_key='dummy')
        with self.assertRaises(ValueError):runner.run_case(plan,self.root/'run',api_key='dummy',send_authorized=True,acknowledge_plan_sha256='wrong')
        request=read(plan/'request.json');request['wire']['input']='old F2 prompt';(plan/'request.json').write_text(json.dumps(request))
        with self.assertRaises(ValueError):self.execute(plan,lambda req:None)
        self.assertFalse(self.sent)

    def test_bad_upstream_evidence_refused_without_sending(self):
        source=self.root/'bad-source';source.mkdir();view,binding,paths,_=inputs(source,'generation')
        binding['evidence']['evidence-1']['official_unit_ids']=['forged-ru']
        with self.assertRaisesRegex(ValueError,'RU span'):
            runner.prepare(self.root/'bad-plan',view,binding,paths,request_id='bad',mode='generation',candidate_freeze=self.freeze)
        self.assertFalse(self.sent)

    def test_retry_plan_or_archive_mutation_stops_before_second_dispatch(self):
        for kind in ['plan','archive']:
            with self.subTest(kind=kind),tempfile.TemporaryDirectory() as tmp:
                old=self.root;self.root=Path(tmp)
                try:
                    plan,_=self.prepared()
                    def handler(req):
                        req.extensions['trace']('connection.start_tls.failed',{})
                        if kind=='plan':(plan/'request.json').write_bytes(b'changed')
                        else:(self.attempt()/'request.body.bin').write_bytes(b'changed')
                        raise httpx.ConnectError('pre-send',request=req)
                    r=self.execute(plan,handler)
                    self.assertEqual((r['state'],r['attempts']),('LOCAL_PROCESSING_UNKNOWN',1))
                    self.assertFalse((self.root/'run/attempts/attempt-002').exists())
                finally:self.root=old

    def test_raw_compression_preserved_and_postrun_tamper_detected(self):
        plan,out=self.prepared();data=stream_fixture(json.dumps(out));raw=gzip.compress(encode(data))
        self.execute(plan,lambda req:httpx.Response(200,headers={'content-type':'text/event-stream','content-encoding':'gzip'},stream=Pieces(raw)))
        self.assertEqual((self.attempt()/'response.body.raw').read_bytes(),raw)
        runner.audit_run(self.root/'run')
        (self.attempt()/'response.body.raw').write_bytes(b'changed')
        with self.assertRaises(ValueError):runner.audit_run(self.root/'run')

    def test_prepare_and_run_dry_cli_do_not_read_credentials(self):
        plan,_=self.prepared()
        with patch('sys.argv',['runner','run','--plan',str(plan),'--out',str(self.root/'absent')]),\
             patch('genshin_corpus.rag.navi_r1_experiment.environment',side_effect=AssertionError('No credentials in dry run')):
            runner.main()
        self.assertFalse((self.root/'absent').exists())


if __name__=='__main__':unittest.main()
