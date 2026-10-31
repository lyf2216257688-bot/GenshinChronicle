"""Parameter serialization is NOT a Metro server capability test."""
from copy import deepcopy
import json
import socket
import unittest
from unittest.mock import patch
import httpx
import openai
from genshin_corpus.retrieval.navi_offline import cards_v2,metro_parameter_drafts as drafts


class DraftTests(unittest.TestCase):
    def setUp(self):
        p=patch.object(socket.socket,'connect',side_effect=AssertionError('No real network'));p.start();self.addCleanup(p.stop)
        self.evidence=[{'id':'e1','text':'她归来。','ru_start':0}]
        self.view={'title':'MOCK','evidence':self.evidence,'children':[],'mode':'read','card_version':cards_v2.VERSION}
        self.text=json.dumps(cards_v2.mock(self.view),ensure_ascii=False)
    def response(self,plan):
        usage={'input_tokens':4,'output_tokens':5,'total_tokens':9}
        if plan['api_surface']=='responses':
            return {'id':'r1','object':'response','created_at':1,'model':'deepseek-v4.1-flash','status':'completed',
                    'output':[{'id':'m1','type':'message','role':'assistant','status':'completed',
                               'content':[{'type':'output_text','text':self.text}]}],'usage':usage}
        return {'id':'c1','object':'chat.completion','created':1,'model':'qwen3.8-flash',
                'choices':[{'index':0,'finish_reason':'stop','message':{'role':'assistant','content':self.text}}],
                'usage':{'prompt_tokens':4,'completion_tokens':5,'total_tokens':9}}
    def test_all_drafts_forward_exact_body_without_real_socket(self):
        for profile in drafts.PROFILES:
            plan=drafts.draft(self.view,profile)
            # Streaming drafts are checked at the request hook; empty SSE sends no generation.
            def handler(req):
                self.assertEqual(str(req.url),plan['url'])
                self.assertEqual(json.loads(req.content),plan['expected_actual_body'])
                if plan['sdk_kwargs']['stream']:
                    return httpx.Response(200,headers={'content-type':'text/event-stream'},content=b'data: [DONE]\n\n')
                return httpx.Response(200,json=self.response(plan))
            with httpx.Client(transport=httpx.MockTransport(handler),trust_env=False) as client:
                with openai.OpenAI(base_url=drafts.BASE_URL,api_key='offline-only',http_client=client,max_retries=0) as sdk:
                    if plan['api_surface']=='responses':out=sdk.responses.create(**plan['sdk_kwargs'])
                    else:out=sdk.chat.completions.create(**plan['sdk_kwargs'])
                    if plan['sdk_kwargs']['stream']:list(out)
                    else:
                        result=drafts.parse_nonstream(json.dumps(self.response(plan)),out.model_dump(mode='json',exclude_unset=True),
                                                      plan,self.view,self.evidence)
                        self.assertEqual(result['content_validation'],'PASS')
            self.assertFalse(plan['send_authorized'])
            self.assertEqual(cards_v2.wire(self.view)['model'],'deepseek-v4.1-flash')
    def test_prompt_and_schema_unchanged_and_extra_body_top_level(self):
        baseline=cards_v2.wire(self.view)
        for p in drafts.PROFILES:
            plan=drafts.draft(self.view,p);args=plan['sdk_kwargs'];body=plan['expected_actual_body']
            text=args['input'][0]['content'][0]['text'] if p.startswith('ds-') else args['messages'][0]['content']
            self.assertEqual(text,baseline['input'][0]['content'][0]['text'])
            if '-schema-' in p:
                schema=args['text']['format']['schema'] if p.startswith('ds-') else args['response_format']['json_schema']['schema']
                self.assertEqual(schema,cards_v2.SCHEMA)
            if p.startswith('qwen-'):
                self.assertNotIn('extra_body',body)
                self.assertIn('enable_thinking',body)
                self.assertNotIn('reasoning_effort',body)
    def test_no_silent_semantic_or_protocol_acceptance(self):
        plan=drafts.draft(self.view,'ds-sync-none');good=self.response(plan)
        for change in ({'status':'incomplete'},{'model':'other'},{'usage':{'input_tokens':4,'output_tokens':9000,'total_tokens':9004}}):
            bad=deepcopy(good);bad.update(change)
            with self.assertRaises(ValueError):drafts.parse_nonstream(json.dumps(bad),bad,plan,self.view,self.evidence)
        bad=deepcopy(good);bad['output'][0]['content'][0]['text']='reasoning only'
        with self.assertRaises(ValueError):drafts.parse_nonstream(json.dumps(bad),bad,plan,self.view,self.evidence)
        with self.assertRaises(ValueError):drafts.parse_nonstream(json.dumps(good),{},plan,self.view,self.evidence)
    def test_chat_length_and_ignored_thinking_disable_rejected(self):
        plan=drafts.draft(self.view,'qwen-sync-none');good=self.response(plan)
        bad=deepcopy(good);bad['choices'][0]['finish_reason']='length'
        with self.assertRaises(ValueError):drafts.parse_nonstream(json.dumps(bad),bad,plan,self.view,self.evidence)
        bad=deepcopy(good);bad['choices'][0]['message']['reasoning_content']='unexpected reasoning'
        with self.assertRaises(ValueError):drafts.parse_nonstream(json.dumps(bad),bad,plan,self.view,self.evidence)


if __name__=='__main__':unittest.main()
