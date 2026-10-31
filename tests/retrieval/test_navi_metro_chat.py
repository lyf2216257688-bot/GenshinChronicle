"""Chat audit and single-send controls, no real sockets."""
from copy import deepcopy
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch
import httpx
from genshin_corpus.retrieval.navi_offline import cards_v2,metro_chat as chat
from genshin_corpus.retrieval.navi_offline.common import read,verify


class ChatTests(unittest.TestCase):
    def http_response(self,obj):
        class Body(httpx.SyncByteStream):
            def __iter__(self):yield json.dumps(obj,ensure_ascii=False).encode()
        return httpx.Response(200,headers={'content-type':'application/json'},stream=Body())
    def setUp(self):
        self.guard=patch.object(socket.socket,'connect',side_effect=AssertionError('No real network'))
        self.guard.start();self.addCleanup(self.guard.stop)
        self.evidence=[{'id':'e1','text':'她归来。','ru_start':0}]
        self.view={'title':'MOCK','evidence':self.evidence,'children':[],'mode':'read','card_version':cards_v2.VERSION}
        self.obj={'id':'chat1','object':'chat.completion','created':1,'model':'deepseek-v4.1-flash',
                  'choices':[{'index':0,'finish_reason':'stop','message':{'role':'assistant',
                      'content':json.dumps(cards_v2.mock(self.view),ensure_ascii=False)}}],
                  'usage':{'prompt_tokens':4,'completion_tokens':5,'total_tokens':9}}
    def assess(self,obj,sdk=None):
        return chat.assess(json.dumps(obj),obj if sdk is None else sdk,self.view,self.evidence)[0]
    def test_independent_usage_protocol_and_content(self):
        for mutation,content_pass in [('reason',True),('cap',True),('length',True),('model',True),('json',False),('source',False)]:
            obj=deepcopy(self.obj)
            if mutation=='reason':obj['choices'][0]['message']['reasoning_content']='reasoning must stay separate'
            if mutation=='cap':obj['usage'].update(completion_tokens=9000,total_tokens=9004)
            if mutation=='length':obj['choices'][0]['finish_reason']='length'
            if mutation=='model':obj['model']='foreign'
            if mutation=='json':obj['choices'][0]['message']['content']='not JSON'
            if mutation=='source':
                value=json.loads(obj['choices'][0]['message']['content']);value['source_observations'][0]['anchors'][0]['quote']='不存在'
                obj['choices'][0]['message']['content']=json.dumps(value)
            result=self.assess(obj)
            self.assertEqual(result['usage']['total_tokens'],obj['usage']['total_tokens'])
            self.assertEqual(result['content_validation']['status']=='PASS',content_pass)
            if content_pass:self.assertEqual(result['protocol_validation']['status'],'FAIL')
        self.assertIn('RAW_SDK_MISMATCH',self.assess(self.obj,{})['error_codes'])
    def test_actual_body_replay_pending_and_ledger(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'job';count=[]
            def handler(req):
                count.append(1);body=json.loads(req.content)
                self.assertEqual(body,chat.actual_body(chat.wire(self.view)))
                self.assertEqual(body['thinking'],{'type':'disabled'})
                self.assertEqual(body['max_tokens'],8192)
                self.assertEqual(body['messages'][0]['content'],cards_v2.wire(self.view)['input'][0]['content'][0]['text'])
                return self.http_response(self.obj)
            result=chat.execute(root,self.view,self.evidence,mock=True,transport_override=httpx.MockTransport(handler))
            self.assertEqual(result['state'],'CONTENT_VERIFIED');verify(root)
            self.assertEqual((root/'reasoning.txt').read_text(),'')
            self.assertEqual(chat.execute(root,self.view,self.evidence,mock=True),result)
            self.assertEqual(len(count),1)
            with self.assertRaises(FileExistsError):chat.execute(Path(tmp)/'duplicate',self.view,self.evidence,mock=True,transport_override=httpx.MockTransport(handler))
            pending=Path(tmp)/'pending';pending.mkdir()
            with self.assertRaises(ValueError):chat.execute(pending,self.view,self.evidence,mock=True)
    def test_sdk_failure_still_preserves_raw_usage(self):
        with tempfile.TemporaryDirectory() as tmp:
            obj=deepcopy(self.obj);obj['choices'][0]['message']['reasoning_content']='visible'
            root=Path(tmp)/'job'
            result=chat.execute(root,self.view,self.evidence,mock=True,transport_override=httpx.MockTransport(lambda req:self.http_response(obj)))
            self.assertEqual(result['state'],'PROTOCOL_OR_CONTENT_INVALID')
            self.assertEqual(result['usage']['total_tokens'],9)
            self.assertFalse((root/'value.json').exists());verify(root)
            self.assertEqual((root/'reasoning.txt').read_text(),'visible')
    def test_inconsistent_usage_does_not_claim_output_over_cap(self):
        obj=deepcopy(self.obj);obj['usage']['total_tokens']=12
        result=self.assess(obj)
        self.assertEqual(result['protocol_validation']['status'],'FAIL')
        self.assertEqual(result['output_limit']['status'],'PASS')
        self.assertEqual(result['provider_reported_usage']['total_tokens'],12)
        self.assertEqual(result['usage'],'UNKNOWN')


if __name__=='__main__':unittest.main()
