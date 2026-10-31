from copy import deepcopy
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch
import httpx
from genshin_corpus.retrieval.navi_offline import cards_v2 as v, card_generation as g,metro_chat as chat
from genshin_corpus.retrieval.navi_offline.common import verify


class GenerationTests(unittest.TestCase):
    def setUp(self):
        p=patch.object(socket.socket,'connect',side_effect=AssertionError('No real sockets'));p.start();self.addCleanup(p.stop)
        self.q=[{'id':'original-hash','text':'传说甲归来，乙留守。','ru_start':0}]
        self.view={'title':'fixture','mode':'read','card_version':v.VERSION,'evidence':self.q,'children':[]}
    def obj(self):
        mv,_=g.aliases(self.view)
        return {'id':'chat1','object':'chat.completion','created':1,'model':'qwen3.8-flash',
            'choices':[{'index':0,'finish_reason':'stop','message':{'role':'assistant','content':json.dumps(v.mock(mv),ensure_ascii=False)}}],
            'usage':{'prompt_tokens':10,'completion_tokens':20,'total_tokens':31}}
    def test_aliases_exact_schema_source_and_warning(self):
        obj=self.obj();result,value=g.assess(json.dumps(obj),obj,self.view,self.q)
        self.assertEqual(result['content_validation']['status'],'PASS');self.assertEqual(result['protocol_validation']['status'],'PASS')
        self.assertIn('PROVIDER_USAGE_ARITHMETIC_MISMATCH',result['warnings'])
        self.assertEqual(result['conservative_accounting_tokens'],31)
        self.assertEqual(value['read_ids'],['original-hash'])
        self.assertEqual(value['source_observations'][0]['anchors'][0]['ru_end'],len(self.q[0]['text']))
        for kind in ('foreign','quote','strings','truncated','model','raw-sdk'):
            bad=deepcopy(obj);val=json.loads(bad['choices'][0]['message']['content'])
            if kind=='foreign':val['read_ids']=['E999']
            if kind=='quote':val['source_observations'][0]['anchors'][0]['quote']='不存在'
            if kind=='strings':val['hypotheses']=['不得替我补来源']
            if kind=='truncated':bad['choices'][0]['finish_reason']='length'
            if kind=='model':bad['model']='foreign'
            bad['choices'][0]['message']['content']=json.dumps(val)
            rec,_=g.assess(json.dumps(bad),{} if kind=='raw-sdk' else bad,self.view,self.q)
            self.assertFalse(rec['protocol_validation']['status']==rec['content_validation']['status']=='PASS')
    def test_raw_transport_structured_body_resume_and_no_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            obj=self.obj();calls=[]
            class Body(httpx.SyncByteStream):
                def __iter__(self):yield json.dumps(obj).encode()
            def handler(req):
                calls.append(1);body=json.loads(req.content)
                self.assertEqual(body['response_format']['json_schema']['schema'],v.SCHEMA)
                self.assertFalse(body['enable_thinking']);self.assertNotIn('extra_body',body)
                return httpx.Response(200,headers={'content-type':'application/json'},stream=Body())
            root=Path(tmp)/'one'
            result=chat.execute(root,self.view,self.q,mock=True,transport_override=httpx.MockTransport(handler),strategy=g.STRATEGY)
            self.assertEqual(result['state'],'CONTENT_VERIFIED');verify(root)
            self.assertEqual(chat.execute(root,self.view,self.q,mock=True,strategy=g.STRATEGY),result)
            self.assertEqual(len(calls),1)
            with self.assertRaises(FileExistsError):chat.execute(Path(tmp)/'two',self.view,self.q,mock=True,transport_override=httpx.MockTransport(handler),strategy=g.STRATEGY)
    def test_safe_lexical_repair_does_not_rewrite_prose(self):
        mv,_=g.aliases(self.view);value=v.mock(mv)
        text=json.dumps(value,ensure_ascii=False).replace('"child_ids": []','"child_ids": []\'')
        decoded,edits=g.decode(text,self.view,self.q)
        self.assertEqual(decoded,value);self.assertEqual(len(edits),1)
        with self.assertRaises(ValueError):g.decode('{"overview":"unfinished',self.view,self.q)
    def test_exact_quote_rebind_is_explicit_and_never_cross_document(self):
        qs=[{'id':'one','text':'甲出发。','ru_start':0,'source_id':'same'},
            {'id':'two','text':'乙归来。','ru_start':0,'source_id':'same'}]
        view=self.view|{'evidence':qs};mv,_=g.aliases(view);value=v.mock(mv)
        value['source_observations'][0]['anchors'][0]['evidence_id']='E002'
        value['source_observations'][0]['evidence_ids']=['E002']
        converted,edits=g.decode(json.dumps(value),view,qs)
        self.assertEqual(converted['source_observations'][0]['anchors'][0]['evidence_id'],'E001')
        self.assertEqual(edits[0]['kind'],'EXACT_UNIQUE_QUOTE_SAME_DOCUMENT_REBIND')
        bad=deepcopy(qs);bad[1]['source_id']='other'
        with self.assertRaises(ValueError):g.decode(json.dumps(value),view,bad)
    def test_escaped_newline_requires_exact_unique_bound_fragment(self):
        for ending in ('\n','\r\n'):
            qs=[{'id':'one','text':'甲出发。'+ending+'乙留守。','ru_start':0,'source_id':'same'}]
            view=self.view|{'evidence':qs};mv,_=g.aliases(view);value=v.mock(mv)
            original=value['source_observations'][0]['anchors'][0]['quote']
            value['source_observations'][0]['anchors'][0]['quote']=original.replace('\r','\\r').replace('\n','\\n')
            result,edits=g.decode(json.dumps(value),view,qs)
            self.assertEqual(result['source_observations'][0]['anchors'][0]['quote'],original)
            g.validate_value(result,view,qs)
            self.assertEqual(edits[0]['kind'],'EXACT_UNIQUE_BOUND_QUOTE_NEWLINE_DECODE')
        for text in ('甲出发。\n乙归来。','甲出发。\n乙留守。甲出发。\n乙留守。'):
            bad=[qs[0]|{'text':text}]
            with self.assertRaises(ValueError):g.decode(json.dumps(value),view,bad)
        # A normalized quote may not move to a different fragment/document.
        bad=[qs[0]|{'text':'本片段没有引文。'},qs[0]|{'id':'two','source_id':'other'}]
        with self.assertRaises(ValueError):g.decode(json.dumps(value),view,bad)
        unchanged=[qs[0]|{'text':r'原文就是字面量\n不能改写'}]
        uv=self.view|{'evidence':unchanged};mv,_=g.aliases(uv);obj=v.mock(mv)
        decoded,edits=g.decode(json.dumps(obj),uv,unchanged)
        self.assertEqual(decoded,obj);self.assertFalse(edits)

    def test_deepseek_response_spelling_alias_and_cap(self):
        obj=self.obj();obj['model']='deepseek-v4-1-flash'
        rec,val=g.assess(json.dumps(obj),obj,self.view,self.q,g.DEEPSEEK)
        self.assertEqual(rec['protocol_validation']['status'],'PASS')
        self.assertIn('PROVIDER_MODEL_SPELLING_ALIAS',rec['warnings'])
        obj['usage']['completion_tokens']=13000
        rec,_=g.assess(json.dumps(obj),obj,self.view,self.q,g.DEEPSEEK)
        self.assertIn('OUTPUT_LIMIT_EXCEEDED',rec['error_codes'])
    def test_projection_retains_story_uncertainty_not_catalog(self):
        card={'schema_version':v.VERSION,'title':'fixture','level':'document','overview':'混合摘要攻击力18%',
              'source_observations':[{'text':'攻击力18%','subject':'套装','action':'提升','object':'攻击力','modality':'机制说明'},
                 {'text':'传说甲归来','subject':'甲','action':'归来','object':'故乡','modality':'传说'}],
              'interpretations':[{'text':'行动可能互有关联'}],'hypotheses':[{'text':'身份尚未证实'}],'open_questions':[]}
        text=v.projection(card)
        self.assertNotIn('攻击力',text);self.assertIn('传说甲归来',text)
        self.assertIn('HYPOTHESIS_NOT_FACT: 身份尚未证实',text)


if __name__=='__main__':unittest.main()
