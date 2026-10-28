"""Tiny offline SDK fixtures; no provider network calls."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
import httpx

from genshin_corpus.retrieval import navi_f2_instruction_diagnostic as diag
from tests.retrieval.test_navi_f2_sdk_transport import Pieces, encode


def fixture(text='{"probe":"INST_OK_4826"}', *, conflict=False):
    message={'id':'m1','type':'message','role':'assistant','status':'completed',
             'content':[{'type':'output_text','text':text}]}
    reason={'id':'r-item','type':'reasoning','summary':[]}
    end_reason=deepcopy(reason)
    if conflict:end_reason['id']='other'
    response={'id':'response1','status':'completed','model':diag.c1.MODEL,
              'output':[end_reason,message],
              'usage':{'input_tokens':3,'output_tokens':2,'total_tokens':5}}
    ix={'item_id':'m1','output_index':1,'content_index':0}
    return [{'type':'response.created','response':{'id':'response1'}},
            {'type':'response.output_item.added','output_index':0,'item':reason},
            {'type':'response.output_item.done','output_index':0,'item':reason},
            {'type':'response.output_item.added','output_index':1,'item':message},
            {'type':'response.output_text.delta',**ix,'delta':text},
            {'type':'response.output_text.done',**ix,'text':text},
            {'type':'response.content_part.done',**ix,'part':message['content'][0]},
            {'type':'response.output_item.done','output_index':1,'item':message},
            {'type':'response.completed','response':response}]


class DiagnosticTests(unittest.TestCase):
    def setUp(self):self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
    def tearDown(self):self.temp.cleanup()
    def mock(self,data):
        return httpx.MockTransport(lambda req:httpx.Response(200,headers={'content-type':'text/event-stream'},stream=Pieces(encode(data))))
    def test_only_cap_differs(self):
        a=diag.wire(8192);b=diag.wire(30000)
        self.assertEqual({k:v for k,v in a.items() if k!='max_output_tokens'},
                         {k:v for k,v in b.items() if k!='max_output_tokens'})
        self.assertEqual(set(a),{'model','instructions','input','stream','max_output_tokens'})
    def test_reasoning_and_message_are_accepted_when_ids_consistent(self):
        result=diag.compare(fixture(),deepcopy(fixture()))
        self.assertTrue(result['stream_structure_consistent'])
        self.assertTrue(result['instructions_followed_json_object'])
        self.assertEqual(result['output_type_counts'],{'reasoning':1,'message':1})
    def test_reasoning_id_conflict_does_not_erase_completion(self):
        data=fixture(conflict=True);result=diag.compare(data,deepcopy(data))
        self.assertEqual(result['communication'],'COMPLETE_RESPONSE_RECEIVED')
        self.assertFalse(result['stream_structure_consistent'])
        self.assertFalse(result['items'][0]['id_consistent'])
        self.assertTrue(result['items'][1]['id_consistent'])
    def test_complete_nonjson_d1_permits_d2_and_never_reissues(self):
        result=diag.run_one(self.root,'D1',8192,api_key='offline-fake',mock_transport=self.mock(fixture('not json',conflict=True)))
        self.assertEqual(result['communication'],'COMPLETE_RESPONSE_RECEIVED')
        self.assertFalse(result['instructions_followed_json_object'])
        self.assertFalse(result['stream_structure_consistent'])
        d2=diag.run_one(self.root,'D2',30000,api_key='offline-fake',mock_transport=self.mock(fixture()))
        self.assertTrue(d2['instructions_followed_json_object'])
        with self.assertRaises(FileExistsError):
            diag.run_one(self.root,'D1',8192,api_key='offline-fake',mock_transport=self.mock(fixture()))
        self.assertFalse(any(b'offline-fake' in p.read_bytes() for p in self.root.rglob('*') if p.is_file()))
    def test_rejection_or_incomplete_blocks_d2(self):
        for kind in ('403','incomplete','timeout'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as d:
                def handler(req):
                    if kind=='timeout':raise httpx.ReadTimeout('offline-fake',request=req)
                    if kind=='403':return httpx.Response(403,stream=Pieces(b'blocked'))
                    return httpx.Response(200,headers={'content-type':'text/event-stream'},stream=Pieces(encode(fixture()[:-1])))
                result=diag.run_one(Path(d),'D1',8192,api_key='offline-fake',mock_transport=httpx.MockTransport(handler))
                self.assertNotEqual(result['communication'],'COMPLETE_RESPONSE_RECEIVED')
                with self.assertRaises(ValueError):diag.run_one(Path(d),'D2',30000,api_key='offline-fake',mock_transport=self.mock(fixture()))
                self.assertFalse((Path(d)/'D2').exists())


if __name__=='__main__':unittest.main()
