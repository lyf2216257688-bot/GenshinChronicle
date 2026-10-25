"""Provider-free guards for the isolated R1 research runner."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from genshin_corpus.rag.navi_r1_experiment import (
    Campaign, CircuitOpen, once, source_units, split_occurrence, validate_profile,
    proven_not_sent, sparse_bm25, traced_not_sent, stable_top, query_bank, request_fingerprint, record_warming, bound_judgment,
)


class R1ResearchGuards(unittest.TestCase):
    def test_rejection_does_not_claim_unchecked_quote_binding(self):
        payload={'segments':[{'id':'u1','text':'source text'}]}
        result=bound_judgment({'verdict':'reject','a_refs':['bad'],'a_quote':'made up'},payload,payload)
        self.assertTrue(result['source_binding_status'].startswith('not_verified'))
        with self.assertRaises(ValueError):bound_judgment({'verdict':'useful'},payload,payload)

    def test_warming_log_preserves_pre_reservation_restart(self):
        with TemporaryDirectory() as tmp:
            run=Path(tmp)
            record_warming(run,'job',[{'status':401}])
            original=(run/'connection-warming/job.json').read_bytes()
            record_warming(run,'job',[{'status':404}])
            self.assertEqual(original,(run/'connection-warming/job.json').read_bytes())
            self.assertTrue((run/'connection-warming/job-restart-1.json').exists())
            self.assertFalse((run/'attempts').exists())

    def protocol(self, run, **limits):
        circuit = {"max_unknown_attempts": 3, "max_model_attempts": 8,
            "max_embedding_attempts": 8, "max_tokens": 1000000,
            "max_accounting_rmb": 100, "rates_rmb_per_million": {"model": [15,80], "embedding": [5,0]}}
        circuit.update(limits)
        once(run / "protocol.json", {"identity": "fixture", "circuit": circuit})

    def test_unknown_is_not_reissued_and_survives_reload(self):
        with TemporaryDirectory() as tmp:
            run = Path(tmp)
            self.protocol(run)
            campaign = Campaign(run)
            stem = campaign.reserve("unit-a", "model", 100, 100)
            campaign.resolve(stem, {"state": "UNKNOWN", "usage": None})
            restored = Campaign(run)
            self.assertEqual(restored.totals()["unknown_attempts"], 1)
            with self.assertRaises(CircuitOpen):
                restored.reserve("unit-a", "model", 100, 100)
            self.assertGreater(restored.totals()["accounted_or_reserved_rmb"], 0)

    def test_unknown_content_cannot_be_reissued_under_new_job_or_cohort(self):
        with TemporaryDirectory() as tmp:
            parent=Path(tmp)
            old=parent/'p05-navi-r1-discovery-20261025-r1'
            new=parent/'p05-navi-r1-discovery-20261025-r3-max'
            self.protocol(old);self.protocol(new)
            wire={'model':'qwen3.8-max','messages':[{'role':'user','content':'same'}]}
            campaign=Campaign(old)
            stem=campaign.reserve('first','model',100,100)
            once(stem/'request.json',wire)
            campaign.resolve(stem,{'state':'UNKNOWN','usage':None})
            with self.assertRaises(CircuitOpen):Campaign(new).check_quarantine(wire)
            changed=dict(wire,model='qwen3.7-plus-2026-05-26')
            Campaign(new).check_quarantine(changed)

    def test_unknown_fingerprint_cannot_be_evaded_by_json_or_accounting_changes(self):
        a={'model':'qwen3.8-max','messages':[{'role':'system','content':'task'},
            {'role':'user','content':json.dumps({'title':'A','segments':[{'text':'source'}],'coverage':{'all':True}})}],'max_tokens':4096}
        b={'model':'qwen3.8-max','instructions':'task','input':json.dumps({'segments':[{'text':'source'}],'title':'A','coverage':{'all':False}}),'max_output_tokens':8192}
        self.assertEqual(request_fingerprint(a),request_fingerprint(b))
        b['input']=json.dumps({'title':'A','segments':[{'text':'different source'}]})
        self.assertNotEqual(request_fingerprint(a),request_fingerprint(b))

    def test_unresolved_reservation_counts_as_unknown(self):
        with TemporaryDirectory() as tmp:
            run = Path(tmp)
            self.protocol(run, max_unknown_attempts=1)
            Campaign(run).reserve("crashed", "model", 100, 100)
            restored = Campaign(run)
            with self.assertRaises(CircuitOpen):
                restored.reserve("new", "model", 100, 100)

    def test_request_ceiling_precedes_dispatch(self):
        with TemporaryDirectory() as tmp:
            run = Path(tmp)
            self.protocol(run, max_model_attempts=1)
            c = Campaign(run)
            stem = c.reserve("one", "model", 100, 100)
            c.resolve(stem, {"state": "RESOLVED", "usage": {"input_tokens": 1, "output_tokens": 1}})
            with self.assertRaises(CircuitOpen):
                c.reserve("two", "model", 1, 1)
            self.assertFalse((run / "attempts/two/issued.json").exists())

    def test_token_and_money_reservations_precede_dispatch(self):
        for limits in ({"max_tokens": 1}, {"max_accounting_rmb": .000001}):
            with TemporaryDirectory() as tmp:
                run = Path(tmp)
                self.protocol(run, **limits)
                with self.assertRaises(CircuitOpen):
                    Campaign(run).reserve("blocked", "model", 100, 100)
                self.assertFalse((run / "attempts/blocked/issued.json").exists())

    def test_feature_quote_and_refs_must_bind(self):
        payload = {"segments": [{"id": "u1", "text": "少女远行求学，归来时亲人已逝。"}]}
        result, rejected = validate_profile({"content": "少女归乡", "content_refs": ["u1"],
            "needs": [{"text": "寻找身份", "refs": ["u2"], "quote": "少女"}],
            "supplies": [{"text": "经历", "refs": ["u1"], "quote": "少女远行求学"}],
            "motifs": [{"text": "神明转世", "refs": ["u1"], "quote": "神明转世"}]}, payload, "a0")
        self.assertEqual(len(result["supplies"]), 1)
        self.assertEqual(len(rejected), 2)

    def test_locator_diagnostic_never_promotes_bad_quote_to_evidence(self):
        payload={'segments':[{'id':'u1','text':'原文分为两处叙述。'}]}
        value={'content':'资料内容','content_refs':['u1'],'needs':[], 'motifs':[],
            'supplies':[{'text':'可供探索的解释','refs':['u1'],'quote':'拼接后的非原文'}]}
        strict,rejected=validate_profile(value,payload,'a0')
        shadow,_=validate_profile(value,payload,'a0',require_quote=False)
        self.assertEqual(strict['supplies'],[])
        self.assertEqual(shadow['supplies'][0]['quote_binding'],'UNBOUND_NOT_EVIDENCE')
        value['supplies'][0]['refs']=['u9']
        shadow,rejected=validate_profile(value,payload,'a0',require_quote=False)
        self.assertEqual(shadow['supplies'],[])

    def test_split_keeps_exact_body_and_coverage(self):
        body = ("一二三四五。\n" * 10000)
        pieces = list(split_occurrence({"text": body}, limit=8000))
        self.assertEqual("".join(p["text"] for p in pieces), body)
        self.assertEqual(pieces[0]["start"], 0)
        self.assertEqual(pieces[-1]["end"], len(body))
        self.assertTrue(all(len(p["text"]) <= 8000 for p in pieces))

    def test_gameplay_recommendation_cannot_enter_story_body(self):
        record = {"record_metadata": {"channel_memberships": ["218"]}, "sections": [
            {"ordinal": 1, "source_metadata": {"name": "生之花"}, "units": [
                {"ordinal": 1, "kind": "rich_text", "parent_component_key": "a", "value": {"normalized_text": "剧情故事" * 30}}]},
            {"ordinal": 6, "source_metadata": {"name": "搭配推荐"}, "units": [
                {"ordinal": 1, "kind": "rich_text", "parent_component_key": "b", "value": {"normalized_text": "可莉火伤推荐" * 30}}]}]}
        kept, excluded = source_units(record)
        self.assertEqual(len(kept), 1)
        self.assertEqual(excluded[0]["reason"], "gameplay_editorial_or_unselected_module")

    def test_only_proven_dns_failure_can_be_reconciled(self):
        base = {"http_status": None, "stream_complete": False, "error": {"diagnostics": {
            "observations": {"http_response_hook_observed": False, "stream_events_observed": 0,
                             "responses_terminal_observed": False},
            "exceptions": [{"type": "socket.gaierror", "codes": {"errno": 11001}}]}}}
        self.assertTrue(proven_not_sent(base))
        base["error"]["diagnostics"]["exceptions"] = [{"type": "ssl.SSLError", "codes": {}}]
        self.assertFalse(proven_not_sent(base))
        base["http_status"] = 524
        self.assertFalse(proven_not_sent(base))

    def test_trace_reconnect_only_before_any_http_header_send(self):
        self.assertTrue(traced_not_sent(['connection.connect_tcp.complete','connection.start_tls.started','connection.start_tls.failed']))
        self.assertFalse(traced_not_sent(['connection.connect_tcp.complete','http11.send_request_headers.started','http11.receive_response_headers.failed']))
        self.assertFalse(traced_not_sent([]))
        self.assertFalse(traced_not_sent(['http2.send_request_headers.started','connection.start_tls.failed']))

    def test_query_bank_is_six_and_does_not_need_target_material(self):
        doc={'title':'Source A','units':[{'text':'来源正文'}]}
        slots=[{'role':'content','text':'具体内容'},{'role':'needs','text':'开放问题'},
               {'role':'supplies','text':'可提供的信息'},{'role':'motifs','text':'特殊机制'}]
        bank=query_bank(doc,slots)
        self.assertEqual(len(bank),6)
        self.assertEqual(bank[1],{'role':'needs','text':'开放问题'})
        self.assertEqual(bank[3],{'role':'supplies','text':'可提供的信息'})
        self.assertEqual(bank[-1],{'role':'motifs','text':'特殊机制'})

    def test_topk_ties_and_negative_dense_scores_are_explicit(self):
        import numpy as np
        self.assertEqual(stable_top(np.array([1.,1.,0.]),['b','a','c'],1),[1])
        self.assertEqual(stable_top(np.array([-1.,-.5]),['a','b'],2,positive_only=False),[1,0])
        self.assertEqual(stable_top(np.array([-1.,-.5]),['a','b'],2),[])

    def test_traced_unknown_paid_request_is_never_retried(self):
        import httpx
        with TemporaryDirectory() as tmp:
            run=Path(tmp);self.protocol(run)
            campaign=Campaign(run);campaign.env={'DASHSCOPE_API_KEY':'fixture-key'}
            class Client:
                calls=0
                def get(self,url,**kwargs):return SimpleNamespace(status_code=404)
                def build_request(self,*args,**kwargs):return SimpleNamespace(extensions={})
                def send(self,request):
                    self.calls+=1
                    request.extensions['trace']('http11.send_request_headers.started',{})
                    raise httpx.ReadError('fixture')
            client=Client()
            with patch('httpx.Client',return_value=client):
                result=campaign.qwen_traced('unknown','judge',{'model':'qwen3.8-max'},'https://example.invalid/chat/completions','DASHSCOPE_API_KEY')
            self.assertEqual(result['state'],'UNKNOWN')
            self.assertEqual(client.calls,1)
            self.assertEqual(campaign.totals()['unknown_attempts'],1)

    def test_traced_reconnect_requires_positive_unsent_evidence(self):
        import httpx
        with TemporaryDirectory() as tmp:
            run=Path(tmp);self.protocol(run)
            campaign=Campaign(run);campaign.env={'DASHSCOPE_API_KEY':'fixture-key'}
            class Client:
                calls=0
                def get(self,url,**kwargs):return SimpleNamespace(status_code=404)
                def build_request(self,*args,**kwargs):return SimpleNamespace(extensions={})
                def send(self,request):
                    self.calls+=1
                    if self.calls==1:
                        request.extensions['trace']('connection.start_tls.failed',{})
                        raise httpx.ConnectError('fixture')
                    request.extensions['trace']('http11.send_request_headers.started',{})
                    raw=json.dumps({'model':'qwen3.8-max','usage':{'prompt_tokens':10,'completion_tokens':2},
                        'choices':[{'finish_reason':'stop','message':{'content':'{}'}}]}).encode()
                    return SimpleNamespace(status_code=200,content=raw)
            client=Client()
            with patch('httpx.Client',return_value=client),patch('time.sleep'):
                result=campaign.qwen_traced('unsent','judge',{'model':'qwen3.8-max'},'https://example.invalid/chat/completions','DASHSCOPE_API_KEY')
            self.assertEqual(result['state'],'RESOLVED')
            self.assertEqual(client.calls,2)
            self.assertEqual(campaign.totals()['unknown_attempts'],0)
            self.assertEqual(campaign.terminals['unsent-connection-1']['state'],'NOT_SENT')

    def test_sparse_scorer_matches_existing_formula_including_empty_fields(self):
        import numpy as np
        from scipy import sparse
        from genshin_corpus.retrieval.candidate_retrieval import (
            _field_aware_lexical_candidates_from_loaded, FIELD_AWARE_LEXICAL_FIELDS,
            DEFAULT_FIELD_AWARE_LEXICAL_WEIGHTS,
        )
        from genshin_corpus.retrieval.lexical import analyze
        from collections import Counter
        rows = []
        for index, values in enumerate([['月亮月亮', '古树', '', '种子在过去成长'],
                                         ['月亮', '时间', '', '时间与树木'],
                                         ['火', '', '', '过去不是现在']]):
            fields = {}
            for field, text in zip(FIELD_AWARE_LEXICAL_FIELDS, values):
                tf = Counter(analyze(text))
                fields[field] = {"tf": dict(tf), "length": sum(tf.values())}
            rows.append({"unit_id": str(index), "fields": fields})
        tokens = sorted({t for r in rows for f in r['fields'].values() for t in f['tf']})
        matrices = {f: sparse.csr_matrix([[r['fields'][f]['tf'].get(t, 0) for t in tokens] for r in rows]) for f in FIELD_AWARE_LEXICAL_FIELDS}
        lengths = {f: [r['fields'][f]['length'] for r in rows] for f in FIELD_AWARE_LEXICAL_FIELDS}
        matrix = sparse_bm25(matrices, lengths, DEFAULT_FIELD_AWARE_LEXICAL_WEIGHTS)
        query = '月亮种子过去'
        q = Counter(analyze(query))
        scores = np.asarray(matrix @ np.array([q.get(t,0) for t in tokens])).ravel()
        expected = _field_aware_lexical_candidates_from_loaded({'analyzer_version':'phase04-cjk-unigram-ascii-token-0.1','arm_build_identity':'fixture'},
            rows, query, top_k=3, k1=1.2, b=.75, analyzer=analyze, field_weights=DEFAULT_FIELD_AWARE_LEXICAL_WEIGHTS)
        for result in expected:
            self.assertAlmostEqual(scores[int(result['unit_id'])], result['retrieval']['score'], places=12)


if __name__ == "__main__":
    unittest.main()
