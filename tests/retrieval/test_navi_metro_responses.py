"""Offline Metro accounting, independent final/reasoning and fail-closed cases."""
from copy import deepcopy
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch
import httpx

from genshin_corpus.retrieval.navi_offline import cards,requests
from genshin_corpus.retrieval.navi_offline.common import read


def transport(events):
    raw=''.join('data: '+json.dumps(e,ensure_ascii=False)+'\n\n' for e in events).encode()
    class Body(httpx.SyncByteStream):
        def __iter__(self):yield raw
    return httpx.MockTransport(lambda req:httpx.Response(200,headers={'content-type':'text/event-stream'},stream=Body()))


class MetroTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.addCleanup(self.tmp.cleanup)
        p=patch.object(socket.socket,'connect',side_effect=AssertionError('No real socket'));p.start();self.addCleanup(p.stop)
        self.view={'title':'fixture','evidence':[{'id':'e1','text':'原文'}]};self.wire=cards.wire(self.view)
    def run_events(self,events,name='job'):
        folder=self.root/'jobs'/name
        return folder,requests.execute(folder,'card',self.wire,self.view,[],backend='mock',route='tokenmetro',
                    transport_override=transport(events),ledger=self.root/('ledger-'+name))
    def test_metro_route_identity_success_replay_and_no_duplicate_send(self):
        events=requests.mock_events(json.dumps(cards.mock(self.view),ensure_ascii=False))
        folder,result=self.run_events(events)
        self.assertEqual(result['state'],'CONTENT_VERIFIED')
        self.assertEqual(result['provider_reported_usage']['total_tokens'],5)
        self.assertEqual(result['stream_validation']['status'],'PASS')
        self.assertEqual(result['content_validation']['status'],'PASS')
        self.assertEqual((folder/'reasoning.txt').read_text(), '')
        self.assertEqual(read(folder/'plan.json')['base_url'],requests.METRO_URL)
        self.assertNotEqual(requests.request_fingerprint(self.wire,'tokenmetro'),requests.request_fingerprint(self.wire))
        replay=requests.execute(self.root/'replay','card',self.wire,self.view,[],backend='replay',route='tokenmetro',replay_root=folder.parent)
        self.assertEqual(replay['state'],'CONTENT_VERIFIED')
        again=requests.execute(folder,'card',self.wire,self.view,[],backend='mock',route='tokenmetro',ledger=self.root/'ledger-job')
        self.assertEqual(again,result)
    def test_incomplete_usage_saved_without_acceptance(self):
        events=requests.mock_events('not json')
        events[-1]['response'].update(status='incomplete',incomplete_details={'reason':'max_output_tokens'})
        folder,result=self.run_events(events)
        self.assertEqual(result['provider_reported_usage']['total_tokens'],5)
        self.assertEqual(result['stream_validation']['status'],'FAIL')
        self.assertTrue({'TERMINAL_CONTRADICTION','OUTPUT_TRUNCATED','NON_JSON'}<=set(result['error_codes']))
        self.assertFalse((folder/'value.json').exists())
    def test_id_drift_rejected_even_with_valid_json(self):
        events=requests.mock_events(json.dumps(cards.mock(self.view),ensure_ascii=False))
        events=deepcopy(events);events[-1]=deepcopy(events[-1]);events[-1]['response']['output'][0]['id']='different'
        folder,result=self.run_events(events)
        self.assertIn('OBJECT_ID_DRIFT',result['error_codes'])
        self.assertEqual(result['content_validation']['status'],'PASS')
        self.assertNotEqual(result['state'],'CONTENT_VERIFIED')
        self.assertFalse((folder/'value.json').exists())
    def test_schema_source_binding_and_non_json_distinct(self):
        values=[('text','NON_JSON'),(json.dumps({'x':1}),'SCHEMA_INVALID')]
        bad=cards.mock(self.view);bad['read_ids']=[];values.append((json.dumps(bad),'SOURCE_OR_CONTENT_CONTRACT_INVALID'))
        for i,(text,code) in enumerate(values):
            _,result=self.run_events(requests.mock_events(text),str(i))
            self.assertIn(code,result['error_codes'])
            self.assertEqual(result['stream_validation']['status'],'PASS')
            self.assertEqual(result['provider_reported_usage']['total_tokens'],5)
    def test_pending_root_never_reissued(self):
        folder=self.root/'pending';folder.mkdir()
        with self.assertRaises(ValueError):requests.execute(folder,'card',self.wire,self.view,[],backend='mock',route='tokenmetro',mock_value=cards.mock(self.view))

    def test_reasoning_and_final_are_separate_and_never_substituted(self):
        text=json.dumps(cards.mock(self.view),ensure_ascii=False)
        events=requests.mock_events(text)
        for e in events:
            if 'output_index' in e:e['output_index']=1
        reason={'id':'reason-item','type':'reasoning','summary':[{'type':'summary_text','text':'diagnostic reasoning only'}]}
        extra=[{'type':'response.output_item.added','output_index':0,'item':reason},
               {'type':'response.output_item.done','output_index':0,'item':reason}]
        events=events[:1]+extra+events[1:]
        events[-1]['response']['output'].insert(0,reason)
        folder,result=self.run_events(events)
        self.assertEqual(result['state'],'CONTENT_VERIFIED')
        self.assertEqual((folder/'reasoning.txt').read_text(encoding='utf8'),'diagnostic reasoning only')
        self.assertEqual((folder/'final.txt').read_text(encoding='utf8'),text)


if __name__=='__main__':unittest.main()
