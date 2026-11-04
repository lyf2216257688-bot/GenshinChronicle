"""Run the same Phase04 retrieval pipeline for both retained Embedding spaces."""

from __future__ import annotations

import argparse
from collections import Counter
from hashlib import sha256
from importlib import import_module
import json
import os
from pathlib import Path
from statistics import median

from genshin_corpus.canonical.fingerprints import canonical_json_bytes, sha256_json
from genshin_corpus.collector.storage import atomic_write
from genshin_corpus.generation.generation import project_generation_request
from genshin_corpus.generation.measure import load_m2_runtime_questions
from genshin_corpus.rag.backend import SingleQuestionBackendConfig, prepare_rag_state, run_single_question
from genshin_corpus.rag.config import LOCAL_QWEN3_PROFILE, ONLINE_QWEN_PROFILE, ProductionRagArtifactPaths
from genshin_corpus.retrieval.bge_reranker_v2_m3 import BgeRerankerV2M3, BgeRerankerV2M3Config, MODEL_REVISION
from genshin_corpus.retrieval.evidence_assembly import EvidenceAssemblyConfig
from genshin_corpus.retrieval.local_qwen3_build import offline_only
from genshin_corpus.retrieval.local_qwen3_embedding import EmbeddingBatch, file_sha256
from genshin_corpus.retrieval.local_qwen3_query import encode_local_queries
from genshin_corpus.retrieval.qwen_m2_query_vectors import load_accepted_qwen_query_vectors
from genshin_corpus.retrieval.reranking import RankFusionConfig


M2 = Path(".local/p04-rag-m2")
OLD_QUERIES = Path(".local/p04-qwen-m2-query-vectors-beijing-20260910-162427")
PROFILES = (LOCAL_QWEN3_PROFILE, ONLINE_QWEN_PROFILE)
PIPELINE_MODULES = (
    "rag.backend", "rag.config", "retrieval.candidate_retrieval", "retrieval.retrieval_units",
    "retrieval.local_qwen3_embedding", "retrieval.local_qwen3_query",
    "retrieval.bge_reranker_v2_m3", "retrieval.reranking", "retrieval.evidence_assembly",
)


def write_json(path: Path, data) -> None:
    atomic_write(path, canonical_json_bytes(data) + b"\n")


def write_jsonl(path: Path, rows) -> None:
    atomic_write(path, b"".join(canonical_json_bytes(row) + b"\n" for row in rows))


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def artifact(path: Path) -> dict:
    return {"path": str(path.resolve()), "sha256": file_sha256(path)}


def run_arm(output, profile, paths, questions, accepted_old, bge_config, config, completed):
    local = None
    if profile == LOCAL_QWEN3_PROFILE:
        print("Encoding the 70 local queries in the isolated runtime", flush=True)
        contract = json.loads(paths.dense_manifest_path.read_bytes())["local_qwen3_contract"]
        local = encode_local_queries([q.question for q in questions], expected_contract=contract)
    print(f"Loading shared RU/BM25 inputs and {profile} Dense index", flush=True)
    state = prepare_rag_state(
        retrieval_unit_manifest_path=paths.retrieval_unit_manifest_path,
        lexical_manifest_path=paths.lexical_manifest_path,
        dense_manifest_path=paths.dense_manifest_path,
        embedding_profile=profile,
    )
    try:
        if local is not None:
            from io import BytesIO
            import numpy as np

            buffer = BytesIO()
            np.save(buffer, local.vectors, allow_pickle=False)
            vector_path = output / "local-query-vectors.npy"
            atomic_write(vector_path, buffer.getvalue())
            write_json(output / "local-query-binding.json", {
                "vectors": artifact(vector_path),
                "vector_space_identity": local.space_identity,
                "questions": [{"question_id": q.question_id, "text_sha256": digest, "token_audit": audit}
                              for q, digest, audit in zip(questions, local.text_sha256, local.token_audit)],
                "encoder_batch_size": 1,
            })
        print(f"Loaded {profile}; starting retrieval, BGE and Assembly", flush=True)
        reranker = BgeRerankerV2M3(bge_config)
        for index, question in enumerate(questions):
            qid = question.question_id
            bindings = {"precomputed_query": accepted_old[index]} if local is None else {
                "local_query": EmbeddingBatch(local.vectors[index:index+1], local.space_identity, local.role,
                                              local.text_sha256[index:index+1], local.token_audit[index:index+1])
            }
            result = run_single_question(
                state, question.question, **bindings, execution_mode="evidence_only",
                reranker=reranker, config=config, execution_identity=qid, request_label=qid,
                output_root=output / profile,
            )
            if result["status"] != "succeeded":
                raise RuntimeError(f"{profile}/{qid}: {result['error']}")
            if result["telemetry"]["counts"]["rerank"] != config.rerank_depth:
                raise RuntimeError(f"{profile}/{qid}: rerank supply was incomplete")
            completed[profile] += 1
            print(f"{profile} {qid} {completed[profile]}/70  BGE {result['timing_seconds']['rerank']:.1f}s", flush=True)
    finally:
        state.close()


def export_review(output, questions, review_path, run_id, config):
    references = read_jsonl(review_path)
    if [(r["question_id"], r["question"]) for r in references] != [(q.question_id, q.question) for q in questions]:
        raise ValueError("review-only rows do not match the runtime questions")
    blinded, key, decisions, comparisons = [], [], [], []
    rerank_seconds = {profile: [] for profile in PROFILES}
    for question, reference in zip(questions, references):
        qid = question.question_id
        projection, result_artifacts, lexical = {}, {}, {}
        for profile in PROFILES:
            path = output / profile / qid / "rag_result.json"
            result = json.loads(path.read_bytes())
            if (result["status"] != "succeeded" or result["execution_mode"] != "evidence_only"
                    or result["audit"]["embedding_profile"] != profile
                    or result["audit"]["config"] != config
                    or result["query"]["question_text"] != question.question
                    or result["query"]["request_label"] != qid
                    or result["telemetry"]["provider_call_counts"]["generation"] != 0):
                raise ValueError(f"{profile}/{qid}: result is outside the paired retrieval contract")
            lexical[profile] = result["retrieval_trace"]["windows"]["lexical"]
            projection[profile] = [item.to_dict() for item in
                                   project_generation_request(result["evidence_packet"], question=question.question).evidence]
            result_artifacts[profile] = artifact(path)
            rerank_seconds[profile].append(result["timing_seconds"]["rerank"])
        if lexical[PROFILES[0]] != lexical[PROFILES[1]]:
            raise ValueError(f"{qid}: BM25 results differ between profiles")
        order = PROFILES if sha256(f"{run_id}:{qid}".encode()).digest()[0] % 2 == 0 else PROFILES[::-1]
        blinded.append({
            "question_id": qid, "question": question.question, "reference_answer": reference["reference_answer"],
            "packet_a": projection[order[0]], "packet_b": projection[order[1]],
        })
        key.append({"question_id": qid, "side_a": order[0], "side_b": order[1]})
        decisions.append({"question_id": qid, "run_identity": run_id, "status": "pending", "verdict": None,
                          "side_a_support": None, "side_b_support": None, "evidence_ids": [], "rationale": None})
        comparisons.append({
            "question_id": qid, "results": result_artifacts,
            "identical_evidence_blocks": projection[PROFILES[0]] == projection[PROFILES[1]],
        })
    write_jsonl(output / "review/blinded-packets.jsonl", blinded)
    write_jsonl(output / "review/unblinding.jsonl", key)
    write_jsonl(output / "review/decisions-template.jsonl", decisions)
    write_jsonl(output / "paired-results.jsonl", comparisons)
    return {
        "paired_questions": len(comparisons), "human_review": "pending",
        "identical_evidence_blocks": sum(row["identical_evidence_blocks"] for row in comparisons),
        "median_bge_seconds": {profile: median(values) for profile, values in rerank_seconds.items()},
    }


def tally_review(output: Path, decisions_path: Path) -> dict:
    """Validate explicit reviewer decisions and unblind the verdict counts."""
    manifest = json.loads((output / "manifest.json").read_bytes())
    if manifest["status"] != "complete" or manifest["completed"] != dict.fromkeys(PROFILES, 70):
        raise ValueError("review requires a completed 70Q comparison")
    review_root = output / "review"
    packets = read_jsonl(review_root / "blinded-packets.jsonl")
    key = read_jsonl(review_root / "unblinding.jsonl")
    decisions = read_jsonl(decisions_path)
    ids = [f"Q{i:03}" for i in range(1, 71)]
    if any([row["question_id"] for row in rows] != ids for rows in (packets, key, decisions)):
        raise ValueError("review input, blind key and decisions must contain ordered Q001–Q070")
    counts: Counter[str] = Counter()
    support: dict[str, Counter] = {profile: Counter() for profile in PROFILES}
    for packet, mapping, decision in zip(packets, key, decisions):
        qid = decision["question_id"]
        if decision.get("run_identity") != manifest["run_identity"]:
            raise ValueError(f"{qid}: decision belongs to a different run")
        if {mapping["side_a"], mapping["side_b"]} != set(PROFILES):
            raise ValueError(f"{qid}: invalid blind mapping")
        if decision["status"] == "pending" and decision["verdict"] is None:
            counts["pending"] += 1
            continue
        if decision["status"] != "reviewed" or not str(decision.get("rationale") or "").strip():
            raise ValueError(f"{qid}: reviewed decision requires a rationale")
        verdicts = {
            "a_better": f"{mapping['side_a']}_better",
            "b_better": f"{mapping['side_b']}_better",
            "equivalent": "equivalent", "both_insufficient": "both_insufficient",
        }
        if decision["verdict"] not in verdicts:
            raise ValueError(f"{qid}: invalid verdict")
        allowed_ids = {f"{side.upper()}:{block['evidence_id']}" for side in ("a", "b")
                       for block in packet[f"packet_{side}"]}
        if not set(decision["evidence_ids"]) <= allowed_ids:
            raise ValueError(f"{qid}: evidence ID does not belong to the blinded Packet")
        for side in ("a", "b"):
            label = decision[f"side_{side}_support"]
            if label not in {"sufficient", "partial", "insufficient"}:
                raise ValueError(f"{qid}: missing reviewed support for side {side}")
            support[mapping[f"side_{side}"]][label] += 1
        counts[verdicts[decision["verdict"]]] += 1
    return {"run_identity": manifest["run_identity"], "decisions": artifact(decisions_path),
            "verdict_counts": dict(counts), "support_counts": support}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--runtime-input", type=Path, default=M2 / "questions.runtime.jsonl")
    parser.add_argument("--review-input", type=Path, default=M2 / "questions.review-only.jsonl")
    parser.add_argument("--old-query-root", type=Path, default=OLD_QUERIES)
    parser.add_argument("--candidate-depth", type=int, default=500)
    parser.add_argument("--rrf-k", type=int, default=60)
    parser.add_argument("--rerank-depth", type=int)
    parser.add_argument("--final-top-n", type=int)
    parser.add_argument("--hybrid-weight", type=float, default=0.35)
    parser.add_argument("--context-chars", type=int)
    parser.add_argument("--block-chars", type=int)
    parser.add_argument("--tally-review", type=Path, help="Summarize a filled review copy for the existing output root.")
    args = parser.parse_args()
    if args.tally_review is not None:
        print(json.dumps(tally_review(args.output, args.tally_review), ensure_ascii=False, indent=2))
        return
    assembly = EvidenceAssemblyConfig(**{
        key: value for key, value in {
            "total_context_chars": args.context_chars, "per_block_chars": args.block_chars,
        }.items() if value is not None
    })
    config = SingleQuestionBackendConfig(
        candidate_supply_depth=args.candidate_depth, rerank_depth=args.rerank_depth,
        rrf_k=args.rrf_k, final_top_n=args.final_top_n, assembly_config=assembly,
        fusion_config=RankFusionConfig(hybrid_weight=args.hybrid_weight, rerank_weight=1 - args.hybrid_weight),
    )
    bge_config = BgeRerankerV2M3Config(Path(".local/models/bge-reranker-v2-m3") / MODEL_REVISION)
    paths = {profile: ProductionRagArtifactPaths(embedding_profile=profile) for profile in PROFILES}
    inputs = {
        "runtime_questions": artifact(args.runtime_input),
        "review_only": artifact(args.review_input),
        "old_query_manifest": artifact(args.old_query_root / "metadata/manifest.json"),
        "ru_manifest": artifact(paths[PROFILES[0]].retrieval_unit_manifest_path),
        "lexical_manifest": artifact(paths[PROFILES[0]].lexical_manifest_path),
        "dense_manifests": {profile: artifact(paths[profile].dense_manifest_path) for profile in PROFILES},
    }
    questions = load_m2_runtime_questions(args.runtime_input)
    accepted_old = load_accepted_qwen_query_vectors(args.old_query_root)
    if [(q.question_id, q.question) for q in questions] != [(q.question_id, q.question) for q in accepted_old]:
        raise ValueError("accepted online query vectors do not match the 70 runtime questions")
    completed = dict.fromkeys(PROFILES, 0)
    manifest = {
        "schema_version": "phase04-embedding-70q-comparison-1",
        "execution_mode": "offline_evidence_only",
        "runner": artifact(Path(__file__)),
        "pipeline_source": {module: artifact(Path(import_module(f"genshin_corpus.{module}").__file__))
                            for module in PIPELINE_MODULES},
        "openblas_threads": os.environ.get("OPENBLAS_NUM_THREADS"),
        "inputs": inputs, "config": config.audit_projection(), "reranker": bge_config.identity_projection(),
    }
    run_id = sha256_json(manifest)
    manifest.update(run_identity=run_id, status="running", completed=completed)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "manifest.json", manifest)
    os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", TOKENIZERS_PARALLELISM="false")
    try:
        with offline_only():
            for profile in PROFILES:
                print(f"Preparing {profile}", flush=True)
                run_arm(output, profile, paths[profile], questions, accepted_old, bge_config, config, completed)
                write_json(output / "manifest.json", manifest)
        if artifact(args.runtime_input) != inputs["runtime_questions"] or artifact(args.review_input) != inputs["review_only"]:
            raise ValueError("70Q input files changed during the run")
        manifest.update(status="complete", summary=export_review(output, questions, args.review_input, run_id, manifest["config"]))
    except BaseException as exc:
        manifest.update(status="failed", error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        write_json(output / "manifest.json", manifest)


if __name__ == "__main__":
    main()
