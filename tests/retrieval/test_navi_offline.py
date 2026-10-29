"""Portable checked-in upstream fixture; socket-denied complete offline workflow."""
from copy import deepcopy
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

import httpx
import numpy as np

from genshin_corpus.canonical.fingerprints import canonical_json_bytes
from genshin_corpus.retrieval import retrieval_units as ru, navi_v03_runner as old
from genshin_corpus.retrieval.navi_offline import cards, index, pipeline, requests
from genshin_corpus.retrieval.navi_offline.common import digest, read, sha, verify, write
from genshin_corpus.retrieval.navi_offline.sources import Sources
from genshin_corpus.retrieval.navi_offline.testing import MockEncoder

FIXTURE=Path(__file__).parents[1]/'fixtures/retrieval/canonical-rag-w1-record.json'


def fixture(root):
    base=json.loads(FIXTURE.read_text(encoding='utf8'))
    entries=[]
    folder=root/'canonical';folder.mkdir()
    for number in range(2):
        record=deepcopy(base)
        record['record_id']='record-'+str(number)
        key='mihoyo_obc:zh-cn:'+str(number)
        record['source_identity']['key']=key
        record['source_identity']['components']['content_id']=str(number)
        record['record_metadata']['name']='合成资料'+str(number)
        path=folder/(str(number)+'.json')
        path.write_bytes(canonical_json_bytes(record))
        entries.append({'record_id':record['record_id'],'canonical_record_path':str(path),
                        'canonical_record_sha256':sha(path),'canonical_status':'canonical','parsed_identity_key':key})
    manifest={'status':'complete','canonical_run_id':'offline-fixture','source':'mihoyo_obc','locale':'zh-cn',
              'input_record_count':2,'accounted_record_count':2,'input_integrity_failure_count':0,
              'dependencies':{'canonical_versions':base['versions']},'records':entries}
    cp=folder/'metadata/manifest.json';write(cp,manifest)
    ru.build_retrieval_units(cp,root/'ru',config=ru.RetrievalUnitBuildConfig(text_fragment_chars=1200,structured_fragment_chars=1200))
    return cp,root/'ru/metadata/manifest.json'


class OfflineTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        files={name:{'sha256':sha(old.REPO/name)} for name in old.ENGINE}
        freeze=self.root/'freeze.json'
        write(freeze,{'version':old.candidate.VERSION,'policy':old.candidate.POLICY,'files':files,
                      'freeze_identity_sha256':old.value_hash(files)})
        self.freeze=patch.object(old,'FREEZE',freeze);self.freeze.start();self.addCleanup(self.freeze.stop)
        # No test may accidentally reach any external service.
        self.socket=patch.object(socket.socket,'connect',side_effect=AssertionError('network forbidden'))
        self.denied=self.socket.start();self.addCleanup(self.socket.stop)
        self.cp,self.rp=fixture(self.root)

    def prepared(self,name='run',**kwargs):
        root=self.root/name
        pipeline.prepare(root,self.cp,self.rp,['0','1'],body_limit=kwargs.pop('body_limit',8000),**kwargs)
        return root

    def test_complete_mock_chain_review_audit_export_resume(self):
        root=self.prepared()
        result=pipeline.run(root,MockEncoder())
        self.assertEqual(result['failures'],0)
        self.assertGreater(result['links'],0)
        links=read(pipeline.output_root(root)/'links.json')
        self.assertTrue(all(x['review'] is not None for x in links))
        self.assertEqual(pipeline.audit(root)['archive_integrity'],'PASS')
        before={p:sha(p) for p in root.rglob('*') if p.is_file()}
        self.assertEqual(pipeline.run(root,MockEncoder()),result)
        self.assertEqual(before,{p:sha(p) for p in root.rglob('*') if p.is_file()})
        exported=self.root/'review-export';pipeline.export_review(root,exported);verify(exported)
        row=pipeline.record_review(root,pipeline.output_root(root)/'links.json','fixture-reviewer',
                                  '2026-10-29T12:00:00+09:00','NEEDS_INFORMATION','测试审核接口')
        self.assertEqual(row['navigation_status'],'INACTIVE')
        self.denied.assert_not_called()

    def test_typed_source_quotes_and_lineage_tamper(self):
        root=self.prepared();sources=Sources(root/'prepared/sources');self.addCleanup(sources.close)
        kinds=set()
        for source in sources.documents:
            for row in sources.rows(source):
                q=sources.quote(row['unit_id']);sources.verify_quote(q)
                kinds.add(q['canonical_address']['canonical_unit_kind'])
                if q['canonical_address']['canonical_unit_kind']!='rich_text':
                    self.assertNotIn('canonical_start',q)
                bad=deepcopy(q);bad['nested_selector']={'forged':True}
                with self.assertRaises(ValueError):sources.verify_quote(bad)
        self.assertIn('structured_observation',kinds)
        self.assertIn('dialogue_graph',kinds)

    def test_adaptive_sections_do_not_claim_parent_reading(self):
        root=self.prepared(body_limit=80)
        result=pipeline.run(root,MockEncoder())
        self.assertEqual(result['failures'],0)
        values=read(root/'card-stage/cards.json')['cards']
        self.assertIn('section',{c['level'] for c in values})
        for doc in [c for c in values if c['level']=='document']:
            self.assertTrue(doc['coverage']['derived_from_cards'])
            self.assertFalse(doc['coverage']['direct_delivered_ids'])
            self.assertFalse(doc['coverage']['whole_page_semantic_coverage'])

    def test_missing_ru_and_canonical_mismatch_rejected(self):
        manifest=read(self.cp)
        target=Path(manifest['records'][0]['canonical_record_path'])
        target.write_text('{}',encoding='utf8')
        with self.assertRaises(ValueError):self.prepared()

    def test_input_code_drift_rejected(self):
        root=self.prepared()
        with patch.object(pipeline,'code_identity',return_value={'changed':'yes'}):
            with self.assertRaises(ValueError):pipeline.run(root,MockEncoder())

    def test_budget_dispositions_and_document_nodes(self):
        root=self.prepared(max_cards=2,max_ru=2,max_pairs=1,max_card_jobs=1)
        result=pipeline.run(root,MockEncoder())
        self.assertEqual(result['cards'],2)
        self.assertGreater(result['deferred'],0)
        self.assertEqual(result['ru_indexed'],2)

    def test_card_read_receipt_and_reference_rejected(self):
        view={'title':'x','evidence':[{'id':'e','text':'原文'}]}
        value=cards.mock(view);value['read_ids']=[]
        with self.assertRaises(ValueError):cards.validate(value,view)
        value=cards.mock(view);value['queries'][0]['evidence_ids']=['foreign']
        with self.assertRaises(ValueError):cards.validate(value,view)

    def test_query_overflow_rejected(self):
        with self.assertRaises(ValueError):index.query(MockEncoder(),'字'*2100)

    def test_windows_cover_long_text_without_truncation(self):
        text='剧'*5000
        parts=list(index.windows(text,MockEncoder()))
        self.assertEqual(''.join(p[2] for p in parts),text)
        self.assertTrue(all(len(p[2])+1<=2048 for p in parts))
        self.assertEqual(parts[1][0],parts[0][1])

    def test_index_exact_topk_shards_and_type_rejection(self):
        enc=MockEncoder();root=self.root/'index'
        items=[{'id':str(i),'source_id':str(i%2),'text':t} for i,t in enumerate(['剑与旅程','书籍','剧情与剑','无关','剑'])]
        index.build(root,iter(items),enc,kind='ru',input_identity='fixture',shard_size=2)
        reader=index.Index(root,kind='ru',encoder_space=enc.space_identity)
        query=index.query(enc,'剑')
        actual=reader.search(query,kind='ru',top_k=3)
        vectors=enc.encode_documents([i['text'] for i in items]).vectors
        expected=sorted(zip(items,vectors@query.vectors[0]),key=lambda x:(-float(x[1]),x[0]['id']))[:3]
        self.assertEqual([r['id'] for r in actual],[i['id'] for i,s in expected])
        for kind,space in [('card',enc.space_identity),('ru','foreign')]:
            with self.assertRaises(ValueError):index.Index(root,kind=kind,encoder_space=space)
        with self.assertRaises(ValueError):reader.search(query,kind='card')
        (root/'spec.json').write_text('{}',encoding='utf8')
        with self.assertRaises(ValueError):reader.search(query,kind='ru')

    def test_shard_crash_resume_retains_unfinished_evidence(self):
        enc=MockEncoder();root=self.root/'shards'
        items=[{'id':str(i),'source_id':'s','text':'原文'+str(i)} for i in range(5)]
        original=enc.encode_documents;count=0
        def fail(texts,**kwargs):
            nonlocal count
            count+=1
            if count==2:raise RuntimeError('injected crash')
            return original(texts,**kwargs)
        with patch.object(enc,'encode_documents',side_effect=fail):
            with self.assertRaises(RuntimeError):index.build(root,iter(items),enc,kind='ru',input_identity='x',shard_size=2)
        completed=next((root/'shards').glob('*/complete.json'));before=sha(completed)
        result=index.build(root,iter(items),enc,kind='ru',input_identity='x',shard_size=2)
        self.assertEqual(result['window_count'],5)
        self.assertEqual(sha(completed),before)
        self.assertGreater(len(list((root/'shards').iterdir())),len(result['shards']))

    def test_pending_request_not_reissued_and_failed_content_archived(self):
        view={'title':'x','evidence':[{'id':'e','text':'原文'}]};wire=cards.wire(view)
        root=self.root/'pending';root.mkdir();write(root/'plan.json',{})
        with self.assertRaises(ValueError):requests.execute(root,'card',wire,view,[],backend='mock',mock_value=cards.mock(view))
        bad=cards.mock(view);bad['read_ids']=[]
        result=requests.execute(self.root/'bad','card',wire,view,[],backend='mock',mock_value=bad,ledger=self.root/'bad-ledger')
        self.assertEqual(result['state'],'STREAM_OR_CONTENT_INVALID')
        self.assertTrue((self.root/'bad/response.body.raw').exists())
        self.assertFalse(result['automatic_retry'])

    def test_live_authorization_checked_before_credentials_or_network(self):
        root=self.prepared()
        with self.assertRaises(ValueError):pipeline.run(root,MockEncoder(),backend='live')
        self.denied.assert_not_called()

    def test_new_typed_adapter_preserves_frozen_wire_and_output(self):
        from tests.retrieval.test_navi_v03_runner import inputs
        path=self.root/'old-inputs';path.mkdir()
        view,binding,paths,value=inputs(path,'generation')
        prepared=self.root/'old-plan'
        old.prepare(prepared,view,binding,paths,request_id='compat',mode='generation',candidate_freeze=old.FREEZE)
        wire=old.candidate.prepare_request(view,mode='generation',model=old.c1.MODEL,route='jizhi',max_output_tokens=30000)['wire']
        self.assertEqual(read(prepared/'request.json')['wire'],wire)
        result=requests.execute(self.root/'new-run','generation',wire,view,binding,backend='mock',mock_value=value,ledger=self.root/'compat-ledger')
        self.assertEqual(result['state'],'CONTENT_VERIFIED')
        self.assertEqual(read(self.root/'new-run/value.json'),old.candidate.validate_output(json.dumps(value,ensure_ascii=False),{'A':['A1'],'B':['B1']},mode='generation'))

    def test_replay_accepts_real_job_directory_names_and_actual_body(self):
        view={'title':'x','evidence':[{'id':'e','text':'原文'}]};wire=cards.wire(view)
        source=self.root/'recorded/jobs/arbitrary-job-id'
        requests.execute(source,'card',wire,view,[],backend='mock',mock_value=cards.mock(view),ledger=self.root/'replay-ledger')
        result=requests.execute(self.root/'replayed','card',wire,view,[],backend='replay',replay_root=source.parent)
        self.assertEqual(result['state'],'CONTENT_VERIFIED')

    def test_pending_card_isolated_and_review_block_retains_generation(self):
        root=self.prepared()
        original=pipeline._run_job
        def injected(*args,**kwargs):
            if args[1]=='review':raise ValueError('pending review reservation')
            return original(*args,**kwargs)
        with patch.object(pipeline,'_run_job',side_effect=injected):
            result=pipeline.run(root,MockEncoder())
        links=read(pipeline.output_root(root)/'links.json')
        self.assertTrue(links)
        self.assertTrue(all(l['generation'] and l['review_disposition']=='BLOCKED_RETAIN_ORIGINAL_GENERATION' for l in links))
        self.assertGreater(result['failures'],0)
        self.assertEqual(pipeline.audit(root)['archive_integrity'],'PASS')

    def test_index_identity_binds_content_and_foreign_dimension_rejected(self):
        enc=MockEncoder();identities=[]
        for n,text in enumerate(('甲','乙')):
            v=index.build(self.root/str(n),iter([{'id':'same','source_id':'s','text':text}]),enc,kind='ru',input_identity='same')
            identities.append(v['identity'])
        self.assertNotEqual(*identities)
        reader=index.Index(self.root/'0',kind='ru',encoder_space=enc.space_identity)
        vector=index.query(enc,'甲')
        from dataclasses import replace
        with self.assertRaises(ValueError):reader.search(replace(vector,vectors=np.zeros((1,2048))),kind='ru')

    def test_historical_audit_retains_code_but_resume_rejects_drift(self):
        root=self.prepared();pipeline.run(root,MockEncoder())
        with patch.object(pipeline,'code_identity',return_value={'later':'version'}):
            self.assertFalse(pipeline.audit(root)['current_code_matches_run'])
            with self.assertRaises(ValueError):pipeline.run(root,MockEncoder())

    def test_crash_after_output_seal_recovers_without_resending(self):
        root=self.prepared();original=pipeline.write
        def crash(path,value):
            if Path(path).name.startswith('completion-'):raise RuntimeError('crash after commit')
            return original(path,value)
        with patch.object(pipeline,'write',side_effect=crash):
            with self.assertRaises(RuntimeError):pipeline.run(root,MockEncoder())
        before={p:sha(p) for p in (root/'jobs').rglob('*') if p.is_file()}
        result=pipeline.run(root,MockEncoder())
        self.assertEqual(result['failures'],0)
        self.assertEqual(before,{p:sha(p) for p in (root/'jobs').rglob('*') if p.is_file()})

    def test_partial_deterministic_card_stage_is_preserved_on_resume(self):
        root=self.prepared();write(root/'card-stage/incomplete.json',{'crash':'before seal'})
        result=pipeline.run(root,MockEncoder())
        self.assertEqual(result['failures'],0)
        self.assertTrue((root/'card-stage/incomplete.json').exists())
        self.assertNotEqual(read(root/'card-stage-reference.json')['directory'],'card-stage')
        self.assertEqual(pipeline.audit(root)['archive_integrity'],'PASS')

    def test_rich_only_pipeline_path_calls_unchanged_native_runner(self):
        root=self.prepared();sources=Sources(root/'prepared/sources');self.addCleanup(sources.close)
        quotes=[]
        for source in sources.documents:
            row=next(r for r in sources.rows(source) if r['source']['canonical_address']['canonical_unit_kind']=='rich_text')
            quotes.append(sources.quote(row['unit_id']))
        view,binding=pipeline.link_view(sources,list(sources.documents),quotes)
        jid,result,value=pipeline._run_job(root,'generation',view,binding,backend='mock',mock_value=pipeline.mock_link(view),
                                          replay_root=None,live=False,api_key=None,sources=sources)
        self.assertEqual(result['state'],'CONTENT_VERIFIED')
        self.assertEqual(read(root/'jobs'/jid/'plan.json')['executor'],'UNCHANGED_V03_RUNNER')
        self.assertEqual(old.audit_run(root/'jobs'/jid/'native-run')['archive_integrity'],'PASS')


if __name__=='__main__':unittest.main()
