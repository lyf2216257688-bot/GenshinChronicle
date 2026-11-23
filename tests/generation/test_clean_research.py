"""G5 real protocol/persistence/source paths with tiny fixtures and no sockets."""
from copy import deepcopy
import json
import os
from pathlib import Path
import socket
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from genshin_corpus.evidence_io import read, sha, verify, write
from genshin_corpus.generation.clean_chat import MODEL, encoded
from genshin_corpus.generation.clean_chat_transport import ChatSessions
from genshin_corpus.rag.clean_conversation import ConversationStore
from genshin_corpus.rag.clean_conversation_graph import citation_view
from genshin_corpus.rag.clean_mock import FixturePipeline, MockOpener, MockResponse, fixture_inputs
from genshin_corpus.rag import clean_research as r
from genshin_corpus.rag import clean_research_live as live
from genshin_corpus.retrieval.clean_based.query_v2 import ResearchQuery, validate_ancestry
from genshin_corpus.retrieval.clean_based.source import InputReader
from tests.scratch import scratch_root

REPO = Path(__file__).resolve().parents[2]


class Crash(BaseException): pass


class Pipeline(FixturePipeline):
    def __init__(self, *args): super().__init__(*args); self.queries = []
    def run(self, query, *, parents=(), evidence_refs=()):
        validate_ancestry(query, parents, evidence_refs); self.queries.append(query)
        return super().run(query)


class ToolOpener:
    def __init__(self, calls, prefix="call", argument_strings=False):
        self.calls = calls; self.sent = 0; self.prefix = prefix; self.argument_strings=argument_strings
    def open(self, request, *, timeout):
        self.sent += 1
        calls = [{"index": i, "id": self.prefix + "-" + str(i), "type": "function",
                  "function": {"name": name, "arguments": args if self.argument_strings else encoded(args).decode()}}
                 for i, (name, args) in enumerate(self.calls)]
        def frame(delta, finish=None, **extra):
            return b"data: " + encoded({"id": "g5-fixture", "model": MODEL, "object": "chat.completion.chunk",
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish}], **extra}) + b"\n\n"
        return MockResponse([frame({"role": "assistant", "content": None, "tool_calls": calls}), frame({}, "tool_calls"),
            b"data: " + encoded({"id": "g5-fixture", "model": MODEL, "object": "chat.completion.chunk",
                "choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}}) + b"\n\n",
            b"data: [DONE]\n\n"], 0)


def search(kind="rewrite", text="条件限定"):
    return ("search", {"gap": "核验主问题条件与反例", "subquestion": "条件是什么", "search_text": text,
        "rerank_text": "原文是否给出条件的反例", "kind": kind, "parent_query": "original", "evidence_handles": []})


def finish(reason="sufficient"):
    return ("finish", {"reason": reason, "explanation": "已核验关键限定", "subquestions": ["条件"],
        "hypotheses": ["尚待检验的解释"], "counterevidence": ["未找到不等于不存在"], "unresolved": []})


class ResearchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory(dir=scratch_root())
        self.addCleanup(self.tmp.cleanup)
        self.store = ConversationStore(self.tmp.name)
        manifest = fixture_inputs(REPO, self.store.root / "corpus")
        self.reader = InputReader(manifest); self.addCleanup(self.reader.close)
        self.pipeline = Pipeline(self.reader, REPO)
        self.settings = r.fixture_settings()
        self.sid = self.store.create_chat("G5 fixture", self.settings)
        self.senders = []
        guard = patch.object(socket.socket, "connect", side_effect=AssertionError("No sockets in G5 fixtures"))
        mocked = guard.start(); self.addCleanup(guard.stop); self.addCleanup(mocked.assert_not_called)

    def turn(self, token="1", text="原文条件是什么？", **kwargs): return self.store.enqueue(self.sid, token, text, **kwargs)
    def execute(self, tid, replies, **kwargs):
        def factory(manifest):
            reply = replies.pop(0)
            sender = reply(manifest) if callable(reply) else reply
            self.senders.append(sender); return sender
        return r.run_turn(self.store, tid, self.pipeline, opener_factory=factory, **kwargs)
    def bridge(self):
        chat = self.store.chat(self.sid)
        return ChatSessions(self.store, chat["campaign"], self.settings["limits"])

    def official_settings(self):
        cfg = read(REPO / "config/clean-generation-v1.json")
        self.settings = {**r.fixture_settings(), "roles": cfg["roles"], "route": cfg["route"],
            "context_tokens": cfg["context_tokens"], "timeout_seconds": cfg["timeout_seconds"]}
        self.settings["limits"] = {"campaign_requests": 8, "campaign_tokens": 8*196608,
            "turn_requests": 6, "turn_tokens": 6*196608}
        self.sid = self.store.create_chat("official fixture", self.settings)

    def test_official_dynamic_analysis_boundary_and_exact_thinking_tool_history(self):
        self.official_settings(); sent=[]
        def factory(manifest):
            wire = manifest["wire"]; sent.append(manifest)
            self.assertEqual(wire["model"], "deepseek-flash")
            n=len(sent)
            if n == 1:
                calls=[("research_plan", {"gap":"复杂因果需要反证", "subquestions":["锁是否解开"], "mode":"analysis"})]
            elif n == 2:
                payload=json.loads(wire["messages"][1]["content"])
                # The independent analysis task explicitly includes exact completed feedback as data.
                prior=json.loads(wire["messages"][2]["content"])
                self.assertIn("completed_nonthinking_tool_units", prior)
                initial=self.store.load(self.store.published_artifact(tid,"g5-data-initial"))
                calls=[("read_source", {"gap":"核验整段", "handle":next(iter(initial["handles"])), "scope":"natural_parent"})]
            elif n == 3:
                assistants=[m for m in wire["messages"] if m["role"]=="assistant"]
                self.assertEqual(assistants[-1]["reasoning_content"], "fixture-private-thinking-2")
                self.assertEqual(wire["messages"][-1]["tool_call_id"], "native-2-0")
                calls=[finish()]
            else:
                self.assertNotIn("tools",wire); calls=[]
            def frame(delta,reason=None,usage=None):
                obj={"id":"official-fixture-"+str(n),"model":"deepseek-flash","object":"chat.completion.chunk",
                    "choices":[{"index":0,"delta":delta,"finish_reason":reason}]}
                if usage is not None: obj["usage"]=usage
                return b"data: "+encoded(obj)+b"\n\n"
            reasoning="fixture-private-thinking-"+str(n) if wire["thinking"]["type"]=="enabled" else ""
            delta={"role":"assistant", "content":"原文可核验。[E01]" if not calls else ""}
            if reasoning: delta["reasoning_content"]=reasoning
            if calls:
                delta["tool_calls"]=[{"index":i,"id":f"native-{n}-{i}","type":"function",
                    "function":{"name":name,"arguments":encoded(args).decode()}} for i,(name,args) in enumerate(calls)]
            usage={"prompt_tokens":10,"completion_tokens":20,"total_tokens":30,
                "completion_tokens_details":{"reasoning_tokens":5 if reasoning else 0}}
            class Sender:
                def open(_,request,**kwargs):
                    return MockResponse([frame(delta),frame({},"tool_calls" if calls else "stop",usage),b"data: [DONE]\n\n"],0)
            return Sender()
        tid=self.turn()
        result=r.run_turn(self.store,tid,self.pipeline,opener_factory=factory)
        self.assertEqual(result["status"],"completed")
        self.assertEqual([m["wire"]["thinking"]["type"] for m in sent],["disabled","enabled","enabled","enabled"])
        self.assertEqual([m["wire"]["max_tokens"] for m in sent],[8192,16384,16384,32768])
        self.assertTrue(self.store.published_artifact(tid,"g5-analysis-boundary-0"))
        self.assertEqual(r.run_turn(self.store,tid,self.pipeline),result)
        self.assertEqual(len(sent),4)

    def test_official_capability_limits_and_default_route_are_explicit(self):
        cfg=read(REPO / "config/clean-generation-v1.json")
        self.assertEqual(cfg["route"],"deepseek_official")
        self.assertEqual(cfg["api_key_env"],"DEEPSEEK_API_KEY")
        self.assertEqual(cfg["roles"]["synthesis"]["output_tokens"],32768)
        s={**r.fixture_settings(),"mode":"LIVE","thinking":"dynamic","generation_profile":cfg["version"],
            "roles":cfg["roles"],"route":cfg["route"],"context_tokens":196608,"timeout_seconds":300,
            "worker_timeout_seconds":1800,"fee_risk_usd":2.5,"tool_error_corrections":2,"stop_policy":"evidence-value-1"}
        s["limits"]={"campaign_requests":8,"campaign_tokens":8*196608,"turn_requests":6,"turn_tokens":6*196608}
        live.validate_settings(s)
        for update in ({"fee_risk_usd":3},{"route":"tokenmetro"},{"timeout_seconds":301},
                       {"roles":{**s["roles"],"analysis":{"thinking":"enabled","output_tokens":32768}}}):
            with self.assertRaises(ValueError): live.validate_settings({**s,**update})

    def test_current_citation_catalog_uses_exact_receipt_mapping(self):
        tid=self.turn()
        def inspect(manifest):
            payload=json.loads(manifest["wire"]["messages"][1]["content"])
            sources=self.store.load(self.store.published_artifact(tid,"g5-initial-sources"))
            mapping=sources["packet"]["citations"]
            for c in payload["catalog"]:
                expected=[cid for cid,v in mapping.items() if v["receipt_id"]==c["receipt_id"] and v["fragment_id"]==c["fragment_id"]]
                self.assertEqual(c["citation_ids"],expected)
                self.assertTrue(c["source_item"])
            self.assertEqual({cid for c in payload["catalog"] for cid in c["citation_ids"]},set(mapping))
            return MockOpener("对应正确。[E01]")
        self.execute(tid,[inspect])

    def test_analysis_projection_defers_bodies_explicitly_and_preserves_original(self):
        history=[{"role":"system","content":"sys"},{"role":"user","content":"packet"},
            {"role":"assistant","content":"","tool_calls":[]},
            {"role":"tool","tool_call_id":"native","content":json.dumps({"sources":[
                {"fragment_id":"a","handle":"h1","content":{"whole_text":"甲"*10000}},
                {"fragment_id":"b","handle":"h2","content":{"whole_text":"乙"*10000}}]})}]
        original=deepcopy(history)
        projection=r.analysis_input(history,[{"fragment_id":"a","citation_ids":["E03"]},
            {"fragment_id":"b","citation_ids":[]}])
        self.assertEqual(history,original)
        self.assertEqual(projection["deferred_fragment_ids"],["b"])
        self.assertLess(len(encoded(projection)),len(encoded(history))//10)
        sources=json.loads(projection["completed_nonthinking_tool_units"][-1]["content"])["sources"]
        self.assertEqual([s["model_body"] for s in sources],["in_current_packet","deferred_read_available"])
        self.assertTrue(all("content" not in s for s in sources))

    def test_feedback_references_exact_fragment_and_body_without_editing_history(self):
        body={"whole_source":"甲"*1000}
        history=[{"role":"user","content":json.dumps({"packet":{"evidence":[{"citation_id":"E01","content":body}]},
            "catalog":[{"fragment_id":"a","citation_ids":["E01"]}]})}]
        original=deepcopy(history)
        full={"sources":[{"fragment_id":"a","content":body},{"fragment_id":"b","content":body},
            {"fragment_id":"a","content":{"whole_source":"changed"}}]}
        result=r.reference_feedback(history,full)
        self.assertNotIn("content",result["sources"][0])
        self.assertEqual(result["sources"][1:],full["sources"][1:])
        self.assertEqual(history,original)
        self.assertTrue(all("content" in s for s in full["sources"]))
        self.assertIn("content",r.reference_feedback([],full)["sources"][0])

    def test_code_only_revision_cannot_change_quota_or_reopen_closed_campaign(self):
        self.live_fixture();base=read(self.store.root/"live/permit.json")
        from genshin_corpus.evidence_io import seal
        revision=self.store.root/"live/code-revision"
        changed={**base,"code":r.code_identity(),"code_revision":{"base_permit_sha256":sha(self.store.root/"live/permit.json"),"sealed_calls":{}}}
        write(revision/"permit.json",changed);seal(revision)
        self.assertEqual(live.permit_for(self.store,self.settings),changed)
        original_read=live.read
        expanded={**changed,"settings":{**changed["settings"],"limits":{**changed["settings"]["limits"],"campaign_requests":99}}}
        with patch.object(live,"read",side_effect=lambda path: expanded if path==revision/"permit.json" else original_read(path)):
            with self.assertRaisesRegex(ValueError,"G5_CODE_REVISION_EXPANDED_SCOPE"):
                live.permit_for(self.store,self.settings)
        write(self.store.root/"live/closed.json",{"reason":"fixture_closed"})
        with self.assertRaisesRegex(ValueError,"G5_CAMPAIGN_CLOSED"):
            with patch.dict(os.environ,{"TOKENMETRO_API_KEY":"fixture"}):
                r.run_turn(self.store,self.turn(),self.pipeline)

    def test_select_extra_singular_alias_is_rejected_without_any_tool_execution(self):
        result=self.execute(self.turn(),[ToolOpener([("select",{"gap":"选择","fragment_ids":[],"fragment_id":"x"})])])
        self.assertEqual(result["stop_reason"],"model_fault")
        self.assertIsNone(result["answer_text"])
        self.assertEqual(self.store.load(result["research_ref"])["tool_count"],0)
        self.assertEqual(self.bridge().batch.accounting()["request_reservations"],1)

    def correction_settings(self):
        self.settings = {**self.settings, "tool_error_corrections": 2}
        self.sid = self.store.create_chat("correctable fixture", self.settings)

    def stop_settings(self):
        self.settings={**self.settings,"tool_error_corrections":2,"stop_policy":"evidence-value-1"}
        self.sid=self.store.create_chat("value-based stopping",self.settings)

    def test_sufficient_evidence_model_finish_is_distinct_from_guard(self):
        self.stop_settings()
        def choose(manifest):
            card=json.loads(manifest["wire"]["messages"][1]["content"])["decision_feedback"]
            self.assertEqual(card["checkpoint"],"assess_core_answerability_after_evidence")
            self.assertEqual(card["gain_semantics"],"identity_novelty_only_not_relevance_or_independent_support")
            return ToolOpener([finish()])
        result=self.execute(self.turn(),[choose,MockOpener("原文足以回答核心问题。[E01]")])
        self.assertEqual(result["status"],"completed")
        self.assertEqual(result["termination"]["method"],"finish")
        self.assertEqual(result["termination"]["actor"],"model")
        self.assertGreater(result["termination"]["remaining"]["research_requests_before_reserved_final"],0)
        self.assertEqual(len(self.pipeline.queries),1)

    def test_novel_parent_same_source_feedback_then_model_limited_stop(self):
        self.stop_settings()
        def read_parent(manifest):
            c=json.loads(manifest["wire"]["messages"][1]["content"])["catalog"][0]
            return ToolOpener([("read_source",{"gap":"必要细节需更大原文范围核验","handle":c["handle"],"scope":"natural_parent"})])
        def judge(manifest):
            card=json.loads(manifest["wire"]["messages"][-1]["content"])["decision_feedback"]
            gain=card["recent_checks"][-1]["gain"]
            self.assertEqual(gain["new_source_items"],0)
            self.assertGreater(gain["new_fragments"],0)
            return ToolOpener([finish("limited")],prefix="limited")
        result=self.execute(self.turn(),[read_parent,judge,MockOpener("现有材料能确认甲，关键细节乙未载。[E01]")])
        self.assertEqual(result["stop_reason"],"limited")
        self.assertEqual(result["termination"]["actor"],"model")
        self.assertEqual(result["status"],"partial")

    def test_model_no_gain_stops_before_automatic_repeat_guard(self):
        self.stop_settings()
        def repeat(manifest):
            c=json.loads(manifest["wire"]["messages"][1]["content"])["catalog"][0]
            return ToolOpener([("read_source",{"gap":"核验同一限定","handle":c["handle"],"scope":"chunk"})])
        def stop(manifest):
            card=json.loads(manifest["wire"]["messages"][-1]["content"])["decision_feedback"]
            self.assertEqual(card["no_new_receipt_streak"],1)
            self.assertTrue(all(v==0 for v in card["recent_checks"][-1]["gain"].values()))
            return ToolOpener([finish("no_gain")],prefix="stop")
        result=self.execute(self.turn(),[repeat,stop,MockOpener("补检无新增支持，保留有限结论。[E01]")])
        self.assertEqual(result["termination"]["actor"],"model")
        self.assertEqual(result["stop_reason"],"no_gain")
        self.assertEqual(self.store.load(result["research_ref"])["no_gain"],1)

    def test_optional_unresolved_plan_and_select_finish_need_no_extra_confirmation(self):
        self.stop_settings()
        def choose(manifest):
            c=json.loads(manifest["wire"]["messages"][1]["content"])["catalog"][0]
            return ToolOpener([("research_plan",{"gap":"核心已解决，身世旁支不影响结论","mode":"simple","subquestions":["原文限定"],
                "resolved":["核心结论有直接文本"],"unresolved":[],"deferred":["可选人物身世"]}),
                ("select",{"gap":"保留直接依据","fragment_ids":[c["fragment_id"]]}),finish()])
        result=self.execute(self.turn(),[choose,MockOpener("核心依据充分，身世不扩展。[E01]")])
        self.assertEqual(result["status"],"completed")
        self.assertEqual(self.store.load(result["research_ref"])["plan"]["deferred"],["可选人物身世"])
        self.assertEqual(self.bridge().batch.accounting()["request_reservations"],2)

    def test_stop_policy_direct_answer_and_budget_guard_keep_honest_actors(self):
        self.stop_settings()
        result=self.execute(self.turn(),[MockOpener("简单充分。[E01]")])
        self.assertEqual(result["termination"]["method"],"direct_answer")
        self.assertEqual(self.store.load(result["research_ref"])["stop"],"sufficient")
        self.settings={**self.settings,"max_searches":1};self.sid=self.store.create_chat("guard",self.settings)
        result=self.execute(self.turn("guard"),[ToolOpener([search()]),MockOpener("预算受限。[E01]")])
        self.assertEqual(result["termination"]["actor"],"guard")
        self.assertEqual(result["status"],"partial")

    def test_model_stop_receipt_recovery_keeps_actor_and_uses_no_repeat_call(self):
        self.stop_settings();tid=self.turn()
        def fault(stage):
            if stage=="tool_receipt_before_checkpoint":raise Crash()
        with self.assertRaises(Crash):self.execute(tid,[ToolOpener([finish()])],fault=fault)
        self.assertEqual(self.bridge().batch.accounting()["request_reservations"],1)
        result=self.execute(tid,[MockOpener("保留已选择的停止判断。[E01]")],continue_turn=True)
        self.assertEqual(result["termination"]["actor"],"model")
        self.assertEqual(result["termination"]["method"],"finish")
        self.assertEqual(len(self.store.load(result["research_ref"])["checks"]),1)
        self.assertEqual(self.bridge().batch.accounting()["request_reservations"],2)

    def test_malformed_stop_json_records_rejection_then_model_can_finish(self):
        self.stop_settings()
        def fix(manifest):
            feedback=json.loads(manifest["wire"]["messages"][-1]["content"])
            self.assertFalse(feedback["executed"])
            self.assertEqual(feedback["validation_error"]["issues"][0]["code"],"INVALID_ARGUMENT_JSON")
            self.assertIsNone(feedback["decision_feedback"]["recent_checks"][-1]["gap"])
            return ToolOpener([finish("limited")],prefix="fixed")
        result=self.execute(self.turn(),[ToolOpener([("finish",'{"reason":"limited",')],argument_strings=True),
            fix,MockOpener("原文未交代，保留未知。[E01]")])
        self.assertEqual(result["stop_reason"],"limited")
        self.assertEqual(result["termination"]["actor"],"model")
        self.assertEqual(self.store.load(result["research_ref"])["tool_error_rounds"],1)
        self.assertEqual(self.bridge().batch.accounting()["request_reservations"],3)

    def test_argument_feedback_then_revalidated_selection_and_final(self):
        self.correction_settings(); tid=self.turn()
        def fixed(manifest):
            replies=[json.loads(m["content"]) for m in manifest["wire"]["messages"] if m["role"]=="tool"]
            self.assertEqual(replies[0]["validation_error"]["issues"][0]["extra"],["fragment_id"])
            self.assertFalse(replies[0]["executed"])
            self.assertEqual(replies[1]["status"],"batch_not_executed")
            payload=json.loads(manifest["wire"]["messages"][1]["content"])
            fid=next(c["fragment_id"] for c in payload["catalog"] if c["citation_ids"])
            return ToolOpener([("select",{"gap":"合法选段","fragment_ids":[fid]}),finish()],prefix="fixed")
        result=self.execute(tid,[ToolOpener([("select",{"gap":"选择","fragment_ids":[],"fragment_id":"x"}),search()]),
            fixed,MockOpener("直接依据。[E01]")])
        self.assertEqual(result["status"],"completed")
        data=self.store.load(result["research_ref"])
        self.assertEqual(data["tool_error_rounds"],1)
        self.assertEqual(data["search_count"],1)
        self.assertEqual(self.bridge().batch.accounting()["request_reservations"],3)

    def test_missing_type_enum_and_json_errors_have_specific_feedback(self):
        from genshin_corpus.generation.clean_chat import validate_calls,tool_registry
        cases=[({"fragment_ids":[]},"TOOL_ARGUMENT_KEYS","$"),
               ({"gap":"x","fragment_ids":"bad"},"TOOL_ARGUMENT_TYPE","$.fragment_ids"),
               ({"gap":"x","fragment_ids":[7]},"TOOL_ARGUMENT_TYPE","$.fragment_ids[0]")]
        for args,code,path in cases:
            call={"id":"bad","type":"function","function":{"name":"select","arguments":encoded(args).decode()}}
            with self.assertRaises(ValueError):validate_calls([call],tool_registry(r.TOOLS))
            parsed=validate_calls([call],tool_registry(r.TOOLS),argument_errors=True)[0]
            issue=parsed["validation_error"]["issues"][0]
            self.assertEqual((issue["code"],issue["path"]),(code,path))
        call["function"]["arguments"]='{"gap":'
        self.assertEqual(validate_calls([call],tool_registry(r.TOOLS),argument_errors=True)[0]["validation_error"]
            ["issues"][0]["code"],"INVALID_ARGUMENT_JSON")

    def test_fragment_id_used_as_handle_is_rejected_then_model_corrects(self):
        self.correction_settings(); tid=self.turn()
        def wrong(manifest):
            c=json.loads(manifest["wire"]["messages"][1]["content"])["catalog"][0]
            return ToolOpener([("read_source",{"gap":"回读","handle":c["fragment_id"],"scope":"natural_parent"})])
        def corrected(manifest):
            output=json.loads(manifest["wire"]["messages"][-1]["content"])
            issue=output["validation_error"]["issues"][0]
            self.assertEqual(issue["given_field_is"],"fragment_id")
            self.assertEqual(self.store.load(self.store.published_artifact(tid,"g5-data-0"))["read_count"],
                             self.store.load(self.store.published_artifact(tid,"g5-data-initial"))["read_count"])
            return ToolOpener([("read_source",{"gap":"按反馈修正","handle":issue["catalog_handle"],"scope":"natural_parent"}),finish()],prefix="fixed")
        result=self.execute(tid,[wrong,corrected,MockOpener("完整上下文支持。[E01]")])
        self.assertEqual(result["status"],"completed")
        self.assertEqual(self.store.load(result["research_ref"])["tool_error_rounds"],1)
        self.assertTrue(self.store.load(result["sources_ref"])["parents"])

    def test_corrections_stop_at_limit_without_extra_summary(self):
        self.correction_settings()
        result=self.execute(self.turn(),[ToolOpener([("select",{"fragment_ids":[]})],prefix=str(i)) for i in range(3)])
        self.assertEqual(result["stop_reason"],"tool_correction_limit")
        self.assertIsNone(result["answer_text"])
        self.assertEqual(self.bridge().batch.accounting()["request_reservations"],3)

    def test_error_receipt_crash_replays_without_repeating_bad_model_call(self):
        self.correction_settings(); tid=self.turn()
        def fault(stage):
            if stage=="tool_receipt_before_checkpoint":raise Crash()
        with self.assertRaises(Crash):self.execute(tid,[ToolOpener([("select",{"gap":"x"})])],fault=fault)
        self.assertEqual(self.bridge().batch.accounting()["request_reservations"],1)
        result=self.execute(tid,[MockOpener("可用直接原文。[E01]"),MockOpener("有限综合。[E01]")],continue_turn=True)
        self.assertEqual(self.bridge().batch.accounting()["request_reservations"],3)
        self.assertEqual(self.store.load(result["research_ref"])["tool_error_rounds"],1)

    def test_unknown_or_source_failure_cannot_enter_correction_path(self):
        self.correction_settings()
        result=self.execute(self.turn(),[ToolOpener([("not_a_tool",{"gap":"x"})])])
        self.assertEqual(result["stop_reason"],"model_fault")
        self.assertEqual(self.bridge().batch.accounting()["request_reservations"],1)

    def test_simple_path_one_request_and_scoped_citation(self):
        tid = self.turn(); result = self.execute(tid, [MockOpener("明确条件。[E01]")])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(self.bridge().batch.accounting()["request_reservations"], 1)
        self.assertEqual(citation_view(self.store, tid, "E01")["turn_id"], tid)

    def test_citation_batch_verifies_once_and_rechecks_corruption_on_refresh(self):
        from genshin_corpus.rag import clean_conversation_graph as cg
        tid=self.turn();result=self.execute(tid,[MockOpener("明确来源。[E01]")])
        ids=list(self.store.load(result["sources_ref"])["packet"]["citations"])
        with patch.object(cg,"verify_sources",wraps=cg.verify_sources) as check:
            views=cg.citation_views(self.store,tid,ids)
            self.assertEqual(check.call_count,1)
            self.assertEqual(set(views),set(ids))
        (self.store.root/result["sources_ref"]["path"]).write_text("{}",encoding="utf8")
        with self.assertRaises(ValueError):cg.citation_views(self.store,tid,ids)

    def test_initial_remaining_uses_shared_campaign_balance_and_reserves_final(self):
        self.settings={**self.settings,"limits":{**self.settings["limits"],"campaign_requests":4,"campaign_tokens":4*67072}}
        self.sid=self.store.create_chat("shared remaining",self.settings)
        self.execute(self.turn("one"),[MockOpener("已核验。[E01]")])
        self.execute(self.turn("two"),[MockOpener("已核验。[E01]")])
        def inspect(manifest):
            value=json.loads(manifest["wire"]["messages"][1]["content"])["remaining"]
            self.assertEqual(value["requests"],2)
            self.assertEqual(value["research_requests_before_reserved_final"],1)
            return MockOpener("无需继续研究。[E01]")
        self.execute(self.turn("three"),[inspect])

    def test_cached_read_reports_no_new_read_and_references_visible_body(self):
        self.correction_settings();tid=self.turn();chosen=[]
        def first(manifest):
            c=json.loads(manifest["wire"]["messages"][1]["content"])["catalog"][0];chosen.append(c["handle"])
            return ToolOpener([("read_source",{"gap":"读完整资料","handle":chosen[0],"scope":"natural_parent"})])
        def finish_after_repeat(manifest):
            value=json.loads(manifest["wire"]["messages"][-1]["content"])
            self.assertEqual(value["status"],"already_read");self.assertFalse(value["new_read"])
            self.assertNotIn("content",value["sources"][0])
            self.assertIn("remaining",value)
            return ToolOpener([finish()],prefix="done")
        def repeat(manifest):return ToolOpener([("read_source",{"gap":"复核同资料","handle":chosen[0],"scope":"natural_parent"})],prefix="repeat")
        result=self.execute(tid,[first,repeat,finish_after_repeat,MockOpener("足够且已核验。[E01]")])
        self.assertEqual(result["status"],"completed")

    def test_rewrite_subquestions_counterevidence_keep_query_local_order(self):
        tid = self.turn()
        result = self.execute(tid, [ToolOpener([search("rewrite"), search("counterevidence", "反例")]),
            MockOpener("事实依据，推论仍有限。[E01]")])
        self.assertEqual(result["stop_reason"], "no_gain")
        self.assertEqual(result["status"], "partial")
        data = self.store.load(result["research_ref"])
        self.assertEqual([q["kind"] for q in data["queries"]], ["original", "rewrite", "counterevidence"])
        self.assertTrue(all(q["original_question"] == "原文条件是什么？" for q in data["queries"]))
        sources = self.store.load(result["sources_ref"])
        self.assertEqual(len(sources["packet"]["input_group_digests"]), 3)
        self.assertEqual(data["no_gain"], 2)

    def test_controller_text_after_tool_uses_new_synthesis_operation(self):
        result = self.execute(self.turn(), [ToolOpener([search()]), MockOpener("暂定解释。[E01]"),
                                            MockOpener("基于最终原文的有限综合。[E01]")])
        self.assertEqual(result["stop_reason"], "limited")
        self.assertEqual(self.bridge().batch.accounting()["request_reservations"], 3)

    def test_select_records_dispositions_and_never_invents_source(self):
        def choose(manifest):
            payload = json.loads(manifest["wire"]["messages"][1]["content"])
            return ToolOpener([("select", {"gap": "聚焦直接证据", "fragment_ids": [payload["catalog"][0]["fragment_id"]]}), finish()])
        result = self.execute(self.turn(), [choose, MockOpener("直接依据。[E01]")])
        self.assertEqual(len(self.store.load(result["sources_ref"])["packet"]["selection_ids"]), 1)

    def test_sealed_response_checkpoint_gap_replay_zero_send(self):
        tid = self.turn()
        def fault(stage):
            if stage == "chat_sealed_before_checkpoint": raise Crash()
        with self.assertRaises(Crash): self.execute(tid, [MockOpener("明确。[E01]")], fault=fault)
        before = self.bridge().batch.accounting()
        replay = MockOpener("must not send")
        result = self.execute(tid, [replay], continue_turn=True)
        self.assertEqual(replay.calls, 0); self.assertEqual(result["status"], "completed")
        self.assertEqual(self.bridge().batch.accounting()["request_reservations"], before["request_reservations"])

    def test_tool_receipt_gap_does_not_repeat_search_or_model(self):
        tid = self.turn(); trigger = [True]
        def fault(stage):
            if stage == "tool_receipt_before_checkpoint" and trigger.pop(): raise Crash()
        with self.assertRaises(Crash): self.execute(tid, [ToolOpener([search()])], fault=fault)
        self.assertEqual(len(self.pipeline.queries), 2)
        result = self.execute(tid, [ToolOpener([finish()]), MockOpener("已综合。[E01]")], continue_turn=True)
        self.assertEqual(len(self.pipeline.queries), 2)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(self.bridge().batch.accounting()["request_reservations"], 3)

    def test_unknown_retains_reservation_and_no_final_summary_send(self):
        tid = self.turn(); sender = MockOpener("断流", disconnect=True)
        result = self.execute(tid, [sender])
        self.assertIsNone(result["answer_text"]); self.assertEqual(result["stop_reason"], "model_fault")
        again = self.execute(tid, [])
        self.assertEqual(again, result); self.assertEqual(sender.calls, 1)
        self.assertEqual(self.bridge().batch.accounting()["unknown_usage_requests"], 1)

    def test_repeat_queries_end_no_gain_without_extra_search(self):
        tid = self.turn()
        same = search(text="原文条件是什么？"); same[1]["rerank_text"] = "原文条件是什么？"
        result = self.execute(tid, [ToolOpener([same, same]), MockOpener("证据有限。[E01]")])
        self.assertEqual(len(self.pipeline.queries), 1)
        self.assertEqual(result["stop_reason"], "no_gain")

    def test_action_bound_preserves_synthesis_request(self):
        self.settings["max_searches"] = 1
        # Settings persisted in the new chat, not mutated behind a submitted turn.
        self.sid = self.store.create_chat("bounded", self.settings)
        result = self.execute(self.turn(), [ToolOpener([search()]), MockOpener("有限。[E01]")])
        self.assertEqual(result["stop_reason"], "budget_exhausted")
        self.assertEqual(len(self.senders), 2)

    def test_cancel_after_tool_receipt_prevents_later_dispatch(self):
        tid = self.turn()
        def fault(stage):
            if stage == "tool_receipt_before_checkpoint": self.store.cancel(tid)
        result = self.execute(tid, [ToolOpener([search()])], fault=fault)
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(len(self.senders), 1)

    def test_cancel_before_start_does_no_search_and_replays(self):
        tid = self.turn(); self.store.cancel(tid)
        result = self.execute(tid, [])
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(self.pipeline.queries, [])
        self.assertEqual(self.execute(tid, []), result)

    def test_read_selection_only_known_handles_and_fragments(self):
        tid = self.turn()
        def choose(manifest):
            payload = json.loads(manifest["wire"]["messages"][1]["content"])
            c = payload["catalog"][0]
            return ToolOpener([("read_source", {"gap": "需要自然资料语境", "handle": c["handle"], "scope": "natural_parent"})])
        result = self.execute(tid, [choose, ToolOpener([finish()]), MockOpener("语境已核验。[E01]")])
        self.assertTrue(self.store.load(result["sources_ref"])["parents"])
        self.assertEqual(citation_view(self.store, tid, "E01")["parent_status"], "available")

    def test_foreign_handle_and_invalid_citation_rejected(self):
        tid = self.turn()
        with self.assertRaisesRegex(ValueError, "UNKNOWN_EVIDENCE_HANDLE"):
            self.execute(tid, [ToolOpener([("read_source", {"gap": "查证", "handle": "x"*64, "scope": "chunk"})])])
        self.store.cancel(tid)
        result = self.execute(self.turn("2"), [MockOpener("错误。[E999]")])
        self.assertIsNone(result["answer_text"])
        self.assertEqual(result["stop_reason"], "citation_validation_failed")

    def test_chat_corrections_and_new_chat_isolation(self):
        self.execute(self.turn(text="前提甲", note_kind="user_assertion"), [MockOpener("有限。[E01]")])
        result = self.execute(self.turn("2", "纠正，甲仍未证实", note_kind="correction_fact"), [MockOpener("需核验。[E01]")])
        context = self.store.load(result["context_ref"])
        self.assertTrue(context["research_state"])
        other = self.store.create_chat("new", self.settings)
        self.assertEqual(self.store.history(other), [])

    def test_research_state_survives_reopen_and_correction_requires_review(self):
        first = self.turn()
        result = self.execute(first, [ToolOpener([finish("limited")]), MockOpener("假说仍待核验。[E01]")])
        self.assertTrue(self.store.load(result["research_ref"])["research"]["hypotheses"])
        self.store = ConversationStore(self.store.root)
        second = self.execute(self.turn("2", "纠正：不要沿用刚才的假说", note_kind="correction_fact"),
                              [MockOpener("依本轮原文重新回答。[E01]")])
        prior = self.store.load(second["context_ref"])["research_state"]["prior_research"]
        self.assertEqual(prior[0]["status"], "needs_review")
        self.assertEqual(prior[0]["research"], {})
        self.assertEqual(prior[0]["research_ref"], result["research_ref"])
        self.assertNotEqual(result["citations"][0]["packet_id"], second["citations"][0]["packet_id"])

    def test_live_limits_cannot_expand(self):
        s = {**r.fixture_settings(), "mode": "LIVE", "thinking": "disabled", "timeout_seconds": 120,
             "worker_timeout_seconds": 1800, "history_bytes": 12000}
        live.validate_settings(s)
        for k, v in [("thinking", "enabled"), ("max_searches", 5), ("output_tokens", 2049)]:
            with self.assertRaises(ValueError): live.validate_settings({**s, k: v})
        with self.assertRaises(ValueError): self.store.create_chat("no permit", s)

    def live_fixture(self, requests=4):
        from genshin_corpus.retrieval.clean_based.runtime_v2 import code_identity as upstream
        s = {**r.fixture_settings(), "mode": "LIVE", "thinking": "disabled", "timeout_seconds": 120,
             "worker_timeout_seconds": 1800, "history_bytes": 12000, "campaign": "G5_LIVE_FIXTURE_NO_NETWORK"}
        s["limits"] = {**s["limits"], "campaign_requests": requests, "campaign_tokens": requests * 67072}
        proof = self.store.root / "proof/complete.json"; write(proof, {"fixture_only": True})
        permit = {"version": r.VERSION, "settings": s, "live_enabled": True, "repo": str(REPO),
            "data_root": str(self.store.root), "campaign": s["campaign"], "expires_at": __import__("time").time() + 300,
            "code": r.code_identity(), "upstream_code": upstream(), "endpoint": live.ENDPOINT, "model": MODEL,
            "tool_schema": __import__("genshin_corpus.evidence_io", fromlist=["digest"]).digest(r.TOOLS),
            "assets": {}, "protocol_evidence": {"path": str(proof.parent), "complete_sha256": sha(proof)},
            "max_attempts": 1, "unknown_retries": 0, "format_retries": 0}
        write(self.store.root / "live/permit.json", permit)
        self.settings = s; self.sid = self.store.create_chat("Live fixture", s)
        return permit

    def test_live_gate_tools_sse_budget_shared_new_chat_and_closed_replay(self):
        self.live_fixture(requests=2)
        tid = self.turn()
        with patch.dict(os.environ, {"TOKENMETRO_API_KEY": "fixture"}), patch(
                "genshin_corpus.generation.clean_chat_transport.build_opener",
                side_effect=[ToolOpener([finish()]), MockOpener("已综合。[E01]")]) as sender:
            result = r.run_turn(self.store, tid, self.pipeline)
            self.assertEqual(sender.call_count, 2)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["remote_calls"], 2)
        write(self.store.root / "live/closed.json", {"reason": "fixture closed"})
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(r.run_turn(self.store, tid, self.pipeline), result)
        self.sid = self.store.create_chat("new", self.settings)
        with self.assertRaisesRegex(ValueError, "G5_CAMPAIGN_CLOSED"):
            r.run_turn(self.store, self.turn("other"), self.pipeline)

    def test_live_no_key_never_reserves_and_corrupt_source_blocks_dispatch(self):
        self.live_fixture()
        tid = self.turn()
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "MODEL_API_KEY_REQUIRED"):
                r.run_turn(self.store, tid, self.pipeline)
        self.assertEqual(self.bridge().batch.accounting()["request_reservations"], 0)
        self.assertFalse((self.store.root/"live/closed.json").exists())
        initial = self.store.published_artifact(tid, "g5-initial-sources")
        (self.store.root / initial["path"]).write_text("{}", encoding="utf8")
        with patch.dict(os.environ, {"TOKENMETRO_API_KEY": "fixture"}):
            with self.assertRaises(ValueError): r.run_turn(self.store, tid, self.pipeline, continue_turn=True)
        self.assertEqual(self.bridge().batch.accounting()["request_reservations"], 0)
        self.assertEqual(read(self.store.root/"live/closed.json")["reason"],"integrity_fault")

    def test_live_unknown_closes_without_resend(self):
        self.live_fixture()
        tid = self.turn()
        with patch.dict(os.environ, {"TOKENMETRO_API_KEY": "fixture"}), patch(
                "genshin_corpus.generation.clean_chat_transport.build_opener", return_value=MockOpener("断流", disconnect=True)) as sender:
            result = r.run_turn(self.store, tid, self.pipeline)
        self.assertIsNone(result["answer_text"])
        self.assertTrue((self.store.root / "live/closed.json").exists())
        r.run_turn(self.store, tid, self.pipeline)
        self.assertEqual(sender.call_count, 1)
        self.assertEqual(self.bridge().batch.accounting()["unknown_usage_requests"], 1)

    def staged_pipeline(self, tid, *, resume=False):
        return live.ResearchPipeline(self.store, tid, REPO, staged_local_queries=True, explicit_resume=resume)

    def local_fixture(self, *, fail_at=lambda _query, _phase: None):
        reader = self.reader

        class LocalFixture(Pipeline):
            def __init__(self, repo, _config, _selector, output, *, worker_factory):
                super().__init__(reader, repo)
                self.output = output
                self.output.mkdir(parents=True)

            def run(self, query, **kwargs):
                (self.output / "worker.log").write_bytes(b"local worker started")
                fail_at(query, "started")
                result = super().run(query, **kwargs)
                write(self.output / "ranked.json", result)
                fail_at(query, "ranked")
                return result

        return patch.object(live, "LocalPipeline", LocalFixture)

    def test_staged_live_local_interruptions_continue_same_turn_without_extra_reservation(self):
        self.live_fixture()
        tid = self.turn()
        failures = iter(["started", "ranked"])
        interruption = next(failures)

        def interrupt(_query, phase):
            nonlocal interruption
            if phase == interruption:
                interruption = next(failures, None)
                raise live.LocalQueryInterrupted(phase)

        response = MockOpener("已恢复。[E01]")
        with self.local_fixture(fail_at=interrupt), patch.dict(os.environ, {"TOKENMETRO_API_KEY": "fixture"}), patch(
                "genshin_corpus.generation.clean_chat_transport.build_opener", return_value=response) as opener:
            for resume, phase in [(False, "started"), (True, "ranked")]:
                with self.assertRaisesRegex(live.LocalQueryInterrupted, phase):
                    r.run_turn(self.store, tid, self.staged_pipeline(tid, resume=resume), continue_turn=resume)
                self.assertFalse((self.store.root / "live/closed.json").exists())
                self.assertEqual(self.bridge().batch.accounting()["request_reservations"], 0)
                opener.assert_not_called()
            with self.assertRaisesRegex(ValueError, "requires explicit continue"):
                r.run_turn(self.store, tid, self.staged_pipeline(tid))
            result = r.run_turn(self.store, tid, self.staged_pipeline(tid, resume=True), continue_turn=True)
            opener.assert_called_once()

        self.assertEqual(result["turn_id"], tid)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(self.bridge().batch.accounting()["request_reservations"], 1)
        attempts = self.store.root / "local_attempts" / tid / "original"
        self.assertEqual(len(list(attempts.glob("*/worker.log"))), 2)
        self.assertEqual(len(list(attempts.glob("*/ranked.json"))), 1)
        self.assertEqual(list(attempts.glob("*/complete.json")), [])
        committed = self.store.root / "local_queries" / tid / "original"
        verify(committed)
        published = self.store.published_artifact(tid, "g5-ranked-original")
        self.assertEqual(published["sha256"], sha(committed / "ranked.json"))
        query = ResearchQuery.parse(read(committed / "ranked.json")["query"])
        with patch.object(live, "LocalPipeline", side_effect=AssertionError("sealed query must replay")):
            self.assertEqual(self.staged_pipeline(tid, resume=True).run(query), self.store.load(published))
            (committed / "ranked.json").write_bytes(b"{}")
            with self.assertRaisesRegex(ValueError, "Artifact set/hash changed"):
                self.staged_pipeline(tid, resume=True).run(query)
            (committed / "complete.json").unlink()
            with self.assertRaisesRegex(ValueError, "G5_PUBLISHED_RANKING_SEAL_MISSING"):
                self.staged_pipeline(tid, resume=True).run(query)

    def test_staged_legacy_partial_is_retained_and_requires_explicit_resume(self):
        tid = self.turn()
        text = self.store.turn(tid)["question"]
        query = ResearchQuery(self.sid, tid, "original", text, text, text, text)
        old = self.store.root / "local_queries" / tid / "original"
        old.mkdir(parents=True)
        (old / "worker.log").write_bytes(b"legacy partial")
        with self.local_fixture():
            with self.assertRaises(live.LocalQueryInterrupted):
                self.staged_pipeline(tid).run(query)
            self.assertEqual((old / "worker.log").read_bytes(), b"legacy partial")
            self.staged_pipeline(tid, resume=True).run(query)
        verify(old)
        attempts = self.store.root / "local_attempts" / tid / "original"
        self.assertEqual([p.read_bytes() for p in attempts.glob("legacy-*/worker.log")], [b"legacy partial"])

    def test_cancel_parked_initial_local_query_does_not_restart_it(self):
        self.live_fixture()
        tid = self.turn()

        def fail(_query, _phase):
            raise live.LocalQueryInterrupted("worker stopped")

        with self.local_fixture(fail_at=fail), self.assertRaises(live.LocalQueryInterrupted):
            r.run_turn(self.store, tid, self.staged_pipeline(tid))
        self.store.cancel(tid)
        with patch.object(live, "LocalPipeline", side_effect=AssertionError("cancelled query started")):
            result = r.run_turn(self.store, tid, self.staged_pipeline(tid, resume=True), continue_turn=True)
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(self.bridge().batch.accounting()["request_reservations"], 0)

    def test_cancel_parked_followup_search_retains_prior_sealed_reservation(self):
        self.live_fixture()
        tid = self.turn()

        def fail_followup(query, _phase):
            if query.query_id != "original":
                raise live.LocalQueryInterrupted("follow-up worker stopped")

        with self.local_fixture(fail_at=fail_followup), patch.dict(os.environ, {"TOKENMETRO_API_KEY": "fixture"}), patch(
                "genshin_corpus.generation.clean_chat_transport.build_opener", return_value=ToolOpener([search()])) as opener:
            with self.assertRaises(live.LocalQueryInterrupted):
                r.run_turn(self.store, tid, self.staged_pipeline(tid))
            self.assertEqual(self.bridge().batch.accounting()["request_reservations"], 1)
            self.store.cancel(tid)
            with patch.object(live, "LocalPipeline", side_effect=AssertionError("cancelled search started")):
                result = r.run_turn(self.store, tid, self.staged_pipeline(tid, resume=True), continue_turn=True)
            opener.assert_called_once()
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(self.bridge().batch.accounting()["request_reservations"], 1)

    def test_cancel_after_unsealed_reservation_keeps_unknown_without_resending(self):
        self.live_fixture()
        tid = self.turn()

        class InterruptedOpener:
            def open(self, _request, *, timeout):
                raise Crash()

        with self.local_fixture(), patch.dict(os.environ, {"TOKENMETRO_API_KEY": "fixture"}), patch(
                "genshin_corpus.generation.clean_chat_transport.build_opener", return_value=InterruptedOpener()) as opener:
            with self.assertRaises(Crash):
                r.run_turn(self.store, tid, self.staged_pipeline(tid))
            totals = self.bridge().batch.accounting()
            self.assertEqual((totals["request_reservations"], totals["unknown_usage_requests"]), (1, 1))
            self.store.cancel(tid)
            result = r.run_turn(self.store, tid, self.staged_pipeline(tid, resume=True), continue_turn=True)
            self.assertEqual(result["status"], "cancelled")
            opener.assert_called_once()
            self.assertEqual(self.bridge().batch.accounting(), totals)

    def test_staged_inner_worker_eof_is_recoverable_but_invalid_output_is_not(self):
        worker = object.__new__(live.TimedWorker)
        worker.recover_eof = True
        with patch.object(live.JsonWorker, "receive", side_effect=ValueError("Worker exited unexpectedly; inspect stderr")):
            with self.assertRaises(live.LocalQueryInterrupted):
                worker.receive()
        with patch.object(live.JsonWorker, "receive", side_effect=ValueError("Invalid worker result")):
            with self.assertRaisesRegex(ValueError, "Invalid worker result"):
                worker.receive()


if __name__ == "__main__": unittest.main()
