"""Citation publication and replays use the original Packet and a local parent sidecar."""
from pathlib import Path
import socket
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from genshin_corpus.generation.clean_evidence import EvidenceReader
from genshin_corpus.rag import clean_research
from genshin_corpus.rag.clean_conversation import ConversationStore
from genshin_corpus.rag.clean_conversation_graph import citation_view
from genshin_corpus.rag.clean_mock import MockOpener, fixture_inputs
from genshin_corpus.retrieval.clean_based.source import InputReader
from tests.generation.test_clean_research import Pipeline, ToolOpener, finish
from tests.scratch import scratch_root


REPO = Path(__file__).resolve().parents[2]


class Interrupted(BaseException):
    pass


class CitationParentTests(unittest.TestCase):
    def setUp(self):
        scratch = TemporaryDirectory(dir=scratch_root())
        self.addCleanup(scratch.cleanup)
        self.store = ConversationStore(scratch.name)
        reader = InputReader(fixture_inputs(REPO, self.store.root / "corpus"))
        self.addCleanup(reader.close)
        self.pipeline = Pipeline(reader, REPO)
        settings = {**clean_research.fixture_settings(), "generation_profile": "gc-g5-official-5",
                    "tool_error_corrections": 2}
        self.session = self.store.create_chat("Parent citation fixtures", settings)
        guard = patch.object(socket.socket, "connect", side_effect=AssertionError("No sockets in citation fixtures"))
        self.addCleanup(guard.stop)
        self.addCleanup(guard.start().assert_not_called)

    def turn(self, token="one"):
        return self.store.enqueue(self.session, token, "原文与条件是什么？")

    def run_turn(self, tid, replies, **options):
        return clean_research.run_turn(self.store, tid, self.pipeline,
                                       opener_factory=lambda _: replies.pop(0), **options)

    def assert_parent_matches_request(self, tid, result, final_round):
        request = self.store.load(self.store.published_artifact(tid, f"g5-request-{final_round}"))
        self.assertEqual(result["sources_ref"], request["sources_ref"])
        view = citation_view(self.store, tid, "E01", packet_id=result["citations"][0]["packet_id"])
        self.assertEqual(view["parent"]["source_snapshot"], view["receipt"]["source_snapshot"])
        self.assertEqual(view["parent"]["scope"], "natural_parent")
        self.assertEqual(view["parent_stage"], "after_answer")
        self.assertTrue(view["parent"]["fragments"][0]["structure_text_positions"])
        self.assertEqual(view["parent_status"], "available")
        self.assertEqual(view["semantic_support"], "NOT_EVALUATED")

    def test_direct_and_synthesis_answers_have_original_packet_and_parent(self):
        direct = self.turn()
        result = self.run_turn(direct, [MockOpener("这段原文有条件限制。[E01]")])
        self.assert_parent_matches_request(direct, result, 0)
        self.assertEqual(result["budget"]["request_reservations"], 1)

        synthesis = self.turn("two")
        result = self.run_turn(synthesis, [ToolOpener([finish()]), MockOpener("可核验的限定条件见原文。[E01]")])
        self.assert_parent_matches_request(synthesis, result, 1)
        self.assertEqual(result["budget"]["request_reservations"], 3)

    def test_interruption_after_parent_seal_reuses_existing_answer_and_context(self):
        tid = self.turn()

        def stop(boundary):
            if boundary == "citation_parents_saved_before_result":
                raise Interrupted()

        with self.assertRaises(Interrupted):
            self.run_turn(tid, [MockOpener("见原文。[E01]")], fault=stop)
        request = self.store.load(self.store.published_artifact(tid, "g5-request-0"))
        packet_id = self.store.load(request["sources_ref"])["packet"]["packet_id"]
        sealed = self.store.published_artifact(tid, "g5-citation-parents-" + packet_id)
        self.assertTrue(sealed)
        with patch.object(EvidenceReader, "read", side_effect=AssertionError("Parent already sealed")):
            result = self.run_turn(tid, [], continue_turn=True)
        self.assertEqual(result["parent_context_ref"], sealed)
        self.assertEqual(result["budget"]["request_reservations"], 1)
        self.assertEqual(len(self.pipeline.queries), 1)
        self.assert_parent_matches_request(tid, result, 0)

    def test_parent_failure_leaves_original_quote_available(self):
        tid = self.turn()
        original = EvidenceReader.read

        def read_source(reader, handle, scope="chunk"):
            if scope == "natural_parent":
                raise FileNotFoundError("Fixture missing parent asset")
            return original(reader, handle, scope)

        with patch.object(EvidenceReader, "read", read_source):
            result = self.run_turn(tid, [MockOpener("见原文。[E01]")])
        self.assertEqual(result["status"], "completed")
        view = citation_view(self.store, tid, "E01")
        self.assertEqual(view["parent_status"], "read_failed")
        self.assertIsNone(view["parent"])
        self.assertTrue(view["fragment"]["texts"])

    def test_parent_integrity_failure_isolated_from_bound_citation(self):
        tid = self.turn()
        result = self.run_turn(tid, [MockOpener("见原文。[E01]")])
        sidecar = self.store.load(result["parent_context_ref"])
        receipt = next(iter(sidecar["parents"].values()))["ref"]
        (self.store.root / receipt["path"]).write_text("{}", encoding="utf8")
        view = citation_view(self.store, tid, "E01")
        self.assertEqual(view["parent_status"], "integrity_error")
        self.assertIsNone(view["parent"])
        self.assertTrue(view["fragment"]["texts"])
