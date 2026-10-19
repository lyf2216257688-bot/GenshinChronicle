"""Offline unit tests for Byakuyakoku sequential continuous understanding experiment."""

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

import httpx

from genshin_corpus.retrieval.byakuyakoku_continuity_experiment import (
    DOC_STEPS,
    ContinuitySDK,
    build_step_input,
    generate_derived_continuity_artifacts,
    load_sections_from_sample,
    parse_model_output,
    resolve_clue_sources,
    run_byakuyakoku_continuity_experiment,
    split_source_reference,
)
from genshin_corpus.retrieval.clue_state import ContextContinuityState


def fake_stream_events(model_output: str, total_tokens: int = 150):
    output_items = [
        {
            "id": "msg_001",
            "type": "message",
            "role": "assistant",
            "status": "completed",
            "content": [{"type": "output_text", "text": model_output}],
        }
    ]
    events = [
        {
            "type": "response.output_text.delta",
            "output_index": 0,
            "item_id": "msg_001",
            "content_index": 0,
            "delta": model_output,
        },
        {
            "type": "response.completed",
            "response": {
                "id": "resp_001",
                "model": "deepseek-v4.1-flash",
                "status": "completed",
                "output": deepcopy(output_items),
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 50,
                    "total_tokens": total_tokens,
                },
            },
        },
    ]
    return events


class MockTransport(httpx.BaseTransport):
    def __init__(self, responses_by_step: list[str]):
        self.responses = responses_by_step
        self.call_count = 0

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        idx = self.call_count
        self.call_count += 1
        output_text = self.responses[idx] if idx < len(self.responses) else "{}"
        events = fake_stream_events(output_text, total_tokens=200 + idx * 50)

        lines = []
        for ev in events:
            lines.append(f"event: {ev['type']}\ndata: {json.dumps(ev)}\n\n")

        content = "".join(lines).encode("utf-8")
        return httpx.Response(
            status_code=200,
            headers={"content-type": "text/event-stream"},
            content=content,
            request=request,
        )


class ByakuyakokuContinuityExperimentTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_doc_steps_integrity(self):
        self.assertEqual(len(DOC_STEPS), 7)
        steps_num = [s["step"] for s in DOC_STEPS]
        self.assertEqual(steps_num, list(range(1, 8)))
        expected_cids = ["3429", "3470", "3226", "3611", "3227", "508883", "2403"]
        self.assertEqual([s["content_id"] for s in DOC_STEPS], expected_cids)

    def test_parse_model_output_clean_and_fenced(self):
        step_spec = DOC_STEPS[0]
        sections = [
            {
                "section_id": "3429:s7",
                "content_id": "3429",
                "record_title": "龙蛇藏归辑录",
                "section_ordinal": 7,
                "section_name": "剧情对话",
                "char_count": 500,
                "full_text": "绘真：目前没有追回的书分别是《常世国龙蛇传》...",
                "units": [{"ordinal": 3, "text": "绘真对话"}],
            }
        ]

        raw_json = json.dumps({
            "step_understanding": "绘真作为图书管理员，通报了五本馆藏遗失情况，并提到《日月前事》嫌疑人安贞被关押在大日御舆。",
            "clue_updates": [
                {
                    "operation": "add",
                    "clue_id": "clue_01",
                    "text": "绘真通报《日月前事》等5本馆藏失窃，安贞等嫌疑人被关押于大日御舆。",
                    "source_genre": "official_duty_dialogue",
                    "epistemic_status": "duty_testimony",
                    "narrative_significance": "确立失书调查起点",
                }
            ],
            "unresolved_questions": ["《日月前事》记载了什么秘密？"],
            "updated_summary": "初步掌握绘真失书委托与安贞案情。",
        })

        # Test with markdown fence
        fenced = f"```json\n{raw_json}\n```"
        parsed, state = parse_model_output(fenced, 1, step_spec, sections, prev_state=None)
        self.assertIn("绘真", parsed["step_understanding"])
        self.assertEqual(len(state.clue_events), 1)
        active = state.derive_active_clues()
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0].clue_id, "clue_01")
        self.assertEqual(len(state.source_ledger), 1)

    def test_multi_step_offline_mock_dispatch(self):
        resp_step1 = json.dumps({
            "step_understanding": "Step 1: Ema dialogue understanding.",
            "clue_updates": [
                {
                    "operation": "add",
                    "clue_id": "clue_1_1",
                    "text": "绘真通报失窃案情。",
                    "narrative_significance": "第一手证言",
                }
            ],
            "unresolved_questions": ["日月前事内容？"],
            "updated_summary": "绘真委托找回失书。",
        })

        resp_step2 = json.dumps({
            "step_understanding": "Step 2: Read Before Sun and Moon and Hydrography.",
            "clue_updates": [
                {
                    "operation": "supplement",
                    "clue_id": "clue_2_1",
                    "target_clue_id": "clue_1_1",
                    "text": "《日月前事》确认记载了原初创世与阿倍良久造日。",
                    "narrative_significance": "印证失窃禁书为编年史原典",
                    "reason": "补充失窃典籍的原典记载内容",
                }
            ],
            "unresolved_questions": ["常世国龙蛇传小说与原典的关系？"],
            "updated_summary": "绘真委托失窃书目《日月前事》确为编年史原典。",
        })

        transport = MockTransport([resp_step1, resp_step2])
        env = {
            "JIZHI_API_KEY": "mock_jizhi_key_12345",
            "JIZHI_BASE_URL": "https://jizhiapi.site/v1",
        }
        sdk = ContinuitySDK(transport=transport, env=env)

        two_steps = [DOC_STEPS[0], DOC_STEPS[1]]
        result = run_byakuyakoku_continuity_experiment(
            output_root=self.root / "exp_run",
            sdk=sdk,
            custom_steps=two_steps,
            max_requests=2,
        )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["total_calls"], 2)
        self.assertEqual(transport.call_count, 2)
        self.assertEqual(len(result["step_records"]), 2)

        # Verify artifacts
        run_dir = Path(result["output_root"])
        self.assertTrue((run_dir / "config.json").exists())
        self.assertTrue((run_dir / "session.json").exists())
        self.assertTrue((run_dir / "final_continuity_state.json").exists())
        self.assertTrue((run_dir / "steps/step_01/terminal.json").exists())
        self.assertTrue((run_dir / "steps/step_02/terminal.json").exists())
        self.assertTrue((run_dir / "steps/step_01/model_output_raw.txt").exists())
        self.assertTrue((run_dir / "steps/step_02/model_output_raw.txt").exists())

    def test_focused_clue_resolution_targets(self):
        """Verify focused resolution for clue_2_05, clue_3_03, and clue_6_05."""
        # 1. clue_2_05: Hydrography text
        res_2_05 = resolve_clue_sources("《白夜国水文考》第三卷“风与水之事”")
        self.assertEqual(res_2_05["section_ids"], ["3470:s4"])
        self.assertEqual(res_2_05["local_component_keys"], ["3470:s4:u1"])
        self.assertEqual(
            res_2_05["production_ru_ids"],
            ["ffdaf5bfbc41b755658f3b14db7c109075066eadd484e506860db4415138c3c7"],
        )
        self.assertEqual(res_2_05["overall_status"], "resolved")

        # 2. clue_3_03: Folklore novel body
        res_3_03 = resolve_clue_sources("《常世国龙蛇传》正文")
        self.assertEqual(res_3_03["section_ids"], ["3226:s1"])
        self.assertEqual(res_3_03["local_component_keys"], ["3226:s1:u1"])
        self.assertEqual(
            res_3_03["production_ru_ids"],
            ["0e59547688f1e8f64e695165d6b0f70a0965914de8b12f6de45cb6647694c5b6"],
        )
        self.assertEqual(res_3_03["overall_status"], "resolved")

        # 3. clue_6_05: Encyclopedia character roster
        res_6_05 = resolve_clue_sources("《白夜国》百科条目“其他文本中提到的人”")
        self.assertEqual(res_6_05["section_ids"], ["508883:s10"])
        self.assertEqual(res_6_05["local_component_keys"], ["508883:s10:u1"])
        self.assertEqual(
            res_6_05["production_ru_ids"],
            ["cadf35a74a452a81933b0a186850a77d8901869c8684de9592299d23b90e6105"],
        )
        self.assertEqual(res_6_05["overall_status"], "resolved")

    def test_compound_and_section_only_fallback(self):
        """Verify compound sources and section-only / unresolved fallback handling."""
        # Compound with ；
        res_2_01 = resolve_clue_sources(
            "《常世国龙蛇传》第一卷；《日月前事》第二卷“目盲之年”“目明之年”“日月的二年”"
        )
        self.assertEqual(res_2_01["section_ids"], ["3470:s1", "3470:s2"])
        self.assertEqual(res_2_01["local_component_keys"], ["3470:s1:u1", "3470:s2:u1"])
        self.assertEqual(
            res_2_01["production_ru_ids"],
            [
                "bb1b9f9614c33ba846b438ea242df3ba928a3f81ebc6ae78a5840fc72d0382ce",
                "6961332b1b0fbb412aa0d99fa8fa538e14603348c485295c52c615ca9441113b",
            ],
        )
        self.assertEqual(res_2_01["overall_status"], "resolved")

        # Compound with 与 between works (without text context degrades 3429 to section_only)
        res_3_06_bare = resolve_clue_sources("《常世国龙蛇传》资料页与《龙蛇藏归辑录》剧情对话")
        self.assertEqual(res_3_06_bare["section_ids"], ["3226:s0", "3429:s7"])
        self.assertEqual(res_3_06_bare["local_component_keys"], ["3226:s0:u0"])
        self.assertEqual(res_3_06_bare["overall_status"], "partially_resolved")

        # Compound with 与 between works (with text context resolves exact u3 dialogue)
        res_3_06_text = resolve_clue_sources(
            "《常世国龙蛇传》资料页与《龙蛇藏归辑录》剧情对话",
            clue_text="绘真所见旅行者归还的异本封面与文字均非渊下宫馆藏所识",
        )
        self.assertEqual(res_3_06_text["section_ids"], ["3226:s0", "3429:s7"])
        self.assertEqual(res_3_06_text["local_component_keys"], ["3226:s0:u0", "3429:s7:u3"])
        self.assertEqual(
            res_3_06_text["production_ru_ids"],
            [
                "6fb5641569df9190db9b2d861d87e0743b749673966953ca096cff8e1a134a66",
                "d9fed6511005a303649581f1eeb4df2d601b3334237129528d2d63428f5c90b6",
            ],
        )
        self.assertEqual(res_3_06_text["overall_status"], "resolved")

        # Section-only fallback for unindexed unit (clue_6_01 离可琉)
        res_6_01 = resolve_clue_sources("《白夜国》百科条目“重要事件·离可琉”")
        self.assertEqual(res_6_01["section_ids"], ["508883:s4"])
        self.assertEqual(res_6_01["local_component_keys"], ["508883:s4:u2"])
        self.assertEqual(res_6_01["production_ru_ids"], [])
        self.assertEqual(res_6_01["overall_status"], "section_only")

        # Unresolved fallback
        res_unknown = resolve_clue_sources("未知野史碎片")
        self.assertEqual(res_unknown["section_ids"], [])
        self.assertEqual(res_unknown["production_ru_ids"], [])
        self.assertEqual(res_unknown["overall_status"], "unresolved")

    def test_generate_derived_continuity_artifacts_live_run(self):
        """Verify derived artifacts generation against the completed run root."""
        run_root = Path("data/retrieval/p05-navi-byakuyakoku-continuity-deepseek-20261019-r1")
        if not run_root.exists() or not (run_root / "final_continuity_state.json").exists():
            self.skipTest("Live run directory not present; skipping.")

        # Capture hash of final_continuity_state.json before generation
        orig_bytes = (run_root / "final_continuity_state.json").read_bytes()

        derived_index, derived_state = generate_derived_continuity_artifacts(run_root)

        # 1. Assert non-destructive behavior
        self.assertEqual((run_root / "final_continuity_state.json").read_bytes(), orig_bytes)

        # 2. Assert index properties
        self.assertEqual(derived_index["total_clues"], 41)
        self.assertEqual(derived_index["status_summary"]["resolved"], 37)
        self.assertEqual(derived_index["status_summary"]["section_only"], 4)
        self.assertEqual(derived_index["status_summary"].get("unresolved", 0), 0)

        # 3. Assert focus clues in derived state
        active_map = {c["clue_id"]: c for c in derived_state["active_clues"]}
        self.assertIn("clue_2_05", active_map)
        self.assertIn("clue_3_03", active_map)
        self.assertIn("clue_6_05", active_map)

        self.assertEqual(
            active_map["clue_2_05"]["source_unit_ids"],
            ["ffdaf5bfbc41b755658f3b14db7c109075066eadd484e506860db4415138c3c7"],
        )
        self.assertEqual(
            active_map["clue_3_03"]["source_unit_ids"],
            ["0e59547688f1e8f64e695165d6b0f70a0965914de8b12f6de45cb6647694c5b6"],
        )
        self.assertEqual(
            active_map["clue_6_05"]["source_unit_ids"],
            ["cadf35a74a452a81933b0a186850a77d8901869c8684de9592299d23b90e6105"],
        )

        # Assert section-only fallback for clue_6_01
        self.assertEqual(
            active_map["clue_6_01"]["source_unit_ids"],
            ["508883:s4:u2"],
        )


if __name__ == "__main__":
    unittest.main()
