"""Command-line entry point for the Phase 04 read-only profiler."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from genshin_corpus.canonical.fingerprints import canonical_json_bytes

from .profiler import profile_canonical_run, write_profile
from .qwen_embedding import (
    DashScopeQwenEmbeddingConfig,
    QwenSynchronousPreflightConfig,
    run_qwen_dashscope_synchronous_preflight,
)
from .qwen_full_batch_runner import qwen_full_batch_disk_preflight, qwen_full_batch_dry_run
from .semantic_live_runner import (
    B_EXPERIMENT_REVISION,
    ChannelConfig,
    SemanticLiveRunnerError,
    b_experiment_contract,
    load_adapter,
    load_offline_adapter,
    replay_response,
    run_b_zero_network_preflight,
    run_channel,
)


def _qwen_dashscope_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Run the explicit live DashScope Qwen embedding preflight")
    parser.add_argument("--retrieval-unit-manifest", required=True, type=Path)
    parser.add_argument("--document-unit-id", action="append", required=True)
    parser.add_argument("--query-text", required=True)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--region", required=True)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--endpoint", required=True)
    args = parser.parse_args(argv)
    result = run_qwen_dashscope_synchronous_preflight(
        QwenSynchronousPreflightConfig(
            args.retrieval_unit_manifest,
            tuple(args.document_unit_id),
            args.query_text,
        ),
        args.output_root,
        DashScopeQwenEmbeddingConfig(
            region=args.region,
            workspace=args.workspace,
            endpoint=args.endpoint,
        ),
    )
    print(canonical_json_bytes(result).decode("utf-8"))
    return 0 if result.get("status") == "complete" else 1


def _qwen_full_batch_dry_run_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Report a Qwen full Batch run without provider access")
    parser.add_argument("--packing-root", required=True, type=Path)
    parser.add_argument("--run-root", type=Path)
    args = parser.parse_args(argv)
    print(canonical_json_bytes(qwen_full_batch_dry_run(args.packing_root, args.run_root)).decode("utf-8"))
    return 0


def _qwen_full_batch_disk_preflight_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Report Qwen full Batch disk needs without provider access")
    parser.add_argument("--packing-root", required=True, type=Path)
    parser.add_argument("--run-root", type=Path)
    parser.add_argument("--target-path", type=Path)
    parser.add_argument("--safety-margin-bytes", type=int)
    args = parser.parse_args(argv)
    kwargs = {} if args.safety_margin_bytes is None else {"safety_margin_bytes": args.safety_margin_bytes}
    print(canonical_json_bytes(qwen_full_batch_disk_preflight(args.packing_root, args.run_root, target_path=args.target_path, **kwargs)).decode("utf-8"))
    return 0


def _semantic_live_run_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Run one frozen P05-W2 semantic channel canary or its unattempted remainder")
    parser.add_argument("--channel", choices=("gemini_a", "gemini_b", "deepseek", "glm"), required=True)
    parser.add_argument("--preflight-root", required=True, type=Path)
    parser.add_argument("--run-root", required=True, type=Path)
    parser.add_argument("--prior-attempt-root", action="append", default=[], type=Path)
    parser.add_argument("--experiment-revision", choices=(B_EXPERIMENT_REVISION,))
    parser.add_argument("--legacy-direct-http", action="store_true", required=True,
                        help="explicit historical direct-HTTP diagnostic path")
    selector = parser.add_mutually_exclusive_group(required=True)
    selector.add_argument("--unit-id")
    selector.add_argument("--remaining", action="store_true")
    args = parser.parse_args(argv)
    experiment = b_experiment_contract() if args.experiment_revision == B_EXPERIMENT_REVISION else None
    config = ChannelConfig.for_b_json_object(args.channel) if experiment is not None else ChannelConfig.from_environment(args.channel)
    adapter = load_adapter(config.adapter_factory, config, allow_legacy_live=args.legacy_direct_http)
    result = run_channel(args.preflight_root, args.run_root, config, adapter, unit_id=args.unit_id, remaining=args.remaining, prior_attempt_roots=args.prior_attempt_root, experiment=experiment)
    print(canonical_json_bytes(result).decode("utf-8"))
    return 0 if result.get("status") in {"complete", "no_remaining_units"} else 1


def _semantic_live_replay_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Replay one preserved P05-W2 semantic response without provider access")
    parser.add_argument("--channel", choices=("gemini_a", "gemini_b", "deepseek", "glm"), required=True)
    parser.add_argument("--run-root", required=True, type=Path)
    parser.add_argument("--unit-id", required=True)
    parser.add_argument("--experiment-revision", choices=(B_EXPERIMENT_REVISION,))
    args = parser.parse_args(argv)
    experiment = b_experiment_contract() if args.experiment_revision == B_EXPERIMENT_REVISION else None
    config = ChannelConfig.for_b_json_object(args.channel) if experiment is not None else ChannelConfig.from_environment(args.channel)
    adapter = load_offline_adapter(config.adapter_factory, config)
    print(canonical_json_bytes(replay_response(args.run_root, config, adapter, args.unit_id, experiment=experiment)).decode("utf-8"))
    return 0


def _semantic_b_preflight_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Run the P05-W2 B zero-network mechanical preflight")
    parser.add_argument("--channel", choices=("gemini_a", "gemini_b", "deepseek", "glm"), required=True)
    parser.add_argument("--output-root", required=True, type=Path)
    args = parser.parse_args(argv)
    config = ChannelConfig.for_b_json_object(args.channel)
    report = run_b_zero_network_preflight(args.output_root, config)
    print(canonical_json_bytes(report).decode("utf-8"))
    return 0 if report.get("status") == "PASS" else 1


def main(argv: list[str] | None = None) -> int:
    import sys

    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "qwen-dashscope-preflight":
        return _qwen_dashscope_main(argv[1:])
    if argv and argv[0] == "qwen-full-batch-dry-run":
        return _qwen_full_batch_dry_run_main(argv[1:])
    if argv and argv[0] == "qwen-full-batch-disk-preflight":
        return _qwen_full_batch_disk_preflight_main(argv[1:])
    if argv and argv[0] == "semantic-live-run":
        return _semantic_live_run_main(argv[1:])
    if argv and argv[0] == "semantic-sdk-run":
        from .semantic_sdk_runner import main as sdk_main
        return sdk_main(argv[1:])
    if argv and argv[0] == "semantic-live-replay":
        return _semantic_live_replay_main(argv[1:])
    if argv and argv[0] == "semantic-b-preflight":
        return _semantic_b_preflight_main(argv[1:])
    parser = argparse.ArgumentParser(description="Read-only profile of one accepted Canonical run")
    parser.add_argument("--manifest", required=True, type=Path, help="Canonical run metadata/manifest.json")
    parser.add_argument("--output", type=Path, help="Optional aggregate JSON output path; Canonical data is never written")
    parser.add_argument("--top-n", type=int, default=30, help="Maximum ranked values per aggregate table")
    args = parser.parse_args(argv)
    profile = profile_canonical_run(args.manifest, top_n=args.top_n)
    if args.output is None:
        print(json.dumps(profile, ensure_ascii=False, sort_keys=True, indent=2))
    else:
        write_profile(profile, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
