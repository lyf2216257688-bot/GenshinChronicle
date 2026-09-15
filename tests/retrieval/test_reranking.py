from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from genshin_corpus.retrieval.qwen_rerank import DashScopeQwenRerankConfig, DashScopeQwenRerankTransport, QwenRerankTransportError
from genshin_corpus.retrieval.reranking import (
    RankFusionConfig,
    RerankCandidate,
    RerankRequest,
    RerankScore,
    RerankingError,
    fuse_ranked_candidates,
    project_ranked_candidates,
    project_reranker_text,
    stable_rank_scores,
)


class _Response:
    status = 200
    headers = {}

    def __init__(self, body: bytes):
        self.body = body

    def read(self):
        return self.body

    def getcode(self):
        return self.status

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class _Opener:
    def __init__(self, body: bytes):
        self.body = body
        self.requests = []

    def open(self, request, timeout):
        self.requests.append(request)
        return _Response(self.body)


def _request() -> RerankRequest:
    return RerankRequest("Q005", "query", tuple(RerankCandidate(f"u{i}", f"text {i}", i) for i in range(1, 4)))


class RerankingTests(unittest.TestCase):
    def test_provider_indices_map_to_original_candidate_identity(self):
        opener = _Opener(json.dumps({"request_id": "req-1", "usage": {"total_tokens": 9}, "output": {"results": [{"index": 2, "relevance_score": 0.9}, {"index": 0, "relevance_score": 0.8}, {"index": 1, "relevance_score": 0.7}]}}).encode())
        config = DashScopeQwenRerankConfig("cn-beijing", "https://ws.cn-beijing.maas.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank", "ws")
        transport = DashScopeQwenRerankTransport(config, "secret", opener=opener)
        scores = transport.rerank(_request())
        self.assertEqual([row.unit_id for row in scores], ["u3", "u1", "u2"])
        self.assertEqual(transport.last_response_metadata["request_id"], "req-1")
        payload = json.loads(opener.requests[0].data)
        self.assertEqual(payload["input"]["documents"], ["text 1", "text 2", "text 3"])

    def test_stable_ties_use_original_rank(self):
        result = stable_rank_scores(_request(), [RerankScore("u3", 3, 1.0, 0), RerankScore("u1", 1, 1.0, 0), RerankScore("u2", 2, 0.5, 0)])
        self.assertEqual([(row.unit_id, row.rerank_rank) for row in result], [("u1", 1), ("u3", 2), ("u2", 3)])

    def test_malformed_response_fails_closed(self):
        opener = _Opener(b'{"output":{"results":[{"index":0,"relevance_score":0.1}]}}')
        config = DashScopeQwenRerankConfig("cn-beijing", "https://ws.cn-beijing.maas.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank", "ws")
        with self.assertRaises(QwenRerankTransportError):
            DashScopeQwenRerankTransport(config, "secret", opener=opener).rerank(_request())

    def test_projection_preserves_original_and_rerank_fields_without_provider_dependency(self):
        rows = [{"unit_id": "u1", "rank": 1, "retrieval": {"mode": "hybrid"}}, {"unit_id": "u2", "rank": 2, "retrieval": {"mode": "hybrid"}}]
        projected = project_ranked_candidates(rows, [RerankScore("u2", 2, 0.8, 1), RerankScore("u1", 1, 0.2, 2)])
        self.assertEqual([(row["unit_id"], row["original_hybrid_rank"], row["rerank_rank"]) for row in projected], [("u2", 2, 1), ("u1", 1, 2)])

    def test_missing_or_duplicate_identity_is_rejected(self):
        with self.assertRaises(RerankingError):
            stable_rank_scores(_request(), [RerankScore("u1", 1, 1.0, 0), RerankScore("u1", 1, 0.9, 0), RerankScore("u3", 3, 0.8, 0)])

    def test_reranker_projection_is_deterministic_and_bounded(self):
        fields = {
            "record_title": "  标题 ",
            "section_name": "章节",
            "speaker": "派蒙",
            "retrieval_visible_text": "正文" * 20,
        }
        first = project_reranker_text(fields, max_chars=32)
        second = project_reranker_text(fields, max_chars=32)
        self.assertEqual(first, second)
        self.assertEqual(first[1]["projected_char_count"], 32)
        self.assertTrue(first[1]["truncated"])
        self.assertIsNone(first[1]["token_estimate"])
        self.assertEqual(first[1]["identity"], second[1]["identity"])

    def test_reranker_projection_requires_main_text_and_tolerates_missing_optional_fields(self):
        text, audit = project_reranker_text({"retrieval_visible_text": "正文"})
        self.assertEqual(text, "Text: 正文")
        self.assertEqual(audit["truncated"], False)
        with self.assertRaises(RerankingError):
            project_reranker_text({"record_title": "标题"})
        with self.assertRaises(RerankingError):
            project_reranker_text({"retrieval_visible_text": "  \n\t"})

    def test_rank_fusion_uses_actual_ranks_and_deterministic_ties(self):
        hybrid = [
            {"unit_id": "u1", "rank": 1, "retrieval": {"mode": "hybrid"}},
            {"unit_id": "u2", "rank": 2, "retrieval": {"mode": "hybrid"}},
        ]
        reranked = [
            {"unit_id": "u2", "rank": 1, "original_hybrid_rank": 2, "rerank_rank": 1, "rerank_score": 9.0},
            {"unit_id": "u1", "rank": 2, "original_hybrid_rank": 1, "rerank_rank": 2, "rerank_score": 8.0},
        ]
        result = fuse_ranked_candidates(hybrid, reranked, config=RankFusionConfig())
        self.assertEqual([row["unit_id"] for row in result], ["u2", "u1"])
        self.assertAlmostEqual(result[0]["fusion_score"], 0.35 / 62 + 0.65 / 61)
        self.assertEqual(result[0]["retrieval"]["fusion"]["config_identity"], RankFusionConfig().identity)
        partial = fuse_ranked_candidates(hybrid, reranked[:1])
        self.assertEqual([row["unit_id"] for row in partial], ["u2"])


if __name__ == "__main__":
    unittest.main()
