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
    B_V2_EXPERIMENT_REVISION,
    B_V3_EXPERIMENT_REVISION,
    B_V4_EXPERIMENT_REVISION,
    B_V5_EXPERIMENT_REVISION,
    ChannelConfig,
    SemanticLiveRunnerError,
    b_experiment_contract,
    b_v2_experiment_contract,
    b_v3_experiment_contract,
    b_v4_experiment_contract,
    b_v5_experiment_contract,
    load_adapter,
    load_offline_adapter,
    replay_response,
    run_b_zero_network_preflight,
    run_channel,
)


def _semantic_experiment(revision: str | None):
    return {
        B_EXPERIMENT_REVISION: b_experiment_contract,
        B_V2_EXPERIMENT_REVISION: b_v2_experiment_contract,
        B_V3_EXPERIMENT_REVISION: b_v3_experiment_contract,
        B_V4_EXPERIMENT_REVISION: b_v4_experiment_contract,
        B_V5_EXPERIMENT_REVISION: b_v5_experiment_contract,
    }.get(revision, lambda: None)()


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
    parser.add_argument("--experiment-revision", choices=(B_EXPERIMENT_REVISION, B_V2_EXPERIMENT_REVISION, B_V3_EXPERIMENT_REVISION, B_V4_EXPERIMENT_REVISION, B_V5_EXPERIMENT_REVISION))
    parser.add_argument("--legacy-direct-http", action="store_true", required=True,
                        help="explicit historical direct-HTTP diagnostic path")
    selector = parser.add_mutually_exclusive_group(required=True)
    selector.add_argument("--unit-id")
    selector.add_argument("--remaining", action="store_true")
    args = parser.parse_args(argv)
    experiment = _semantic_experiment(args.experiment_revision)
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
    parser.add_argument("--experiment-revision", choices=(B_EXPERIMENT_REVISION, B_V2_EXPERIMENT_REVISION, B_V3_EXPERIMENT_REVISION, B_V4_EXPERIMENT_REVISION, B_V5_EXPERIMENT_REVISION))
    args = parser.parse_args(argv)
    experiment = _semantic_experiment(args.experiment_revision)
    config = ChannelConfig.for_b_json_object(args.channel) if experiment is not None else ChannelConfig.from_environment(args.channel)
    adapter = load_offline_adapter(config.adapter_factory, config)
    print(canonical_json_bytes(replay_response(args.run_root, config, adapter, args.unit_id, experiment=experiment)).decode("utf-8"))
    return 0


def _semantic_b_preflight_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Run the P05-W2 B zero-network mechanical preflight")
    parser.add_argument("--channel", choices=("gemini_a", "gemini_b", "deepseek", "glm"), required=True)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--experiment-revision", choices=(B_EXPERIMENT_REVISION, B_V2_EXPERIMENT_REVISION, B_V3_EXPERIMENT_REVISION, B_V4_EXPERIMENT_REVISION, B_V5_EXPERIMENT_REVISION))
    args = parser.parse_args(argv)
    config = ChannelConfig.for_b_json_object(args.channel)
    report = run_b_zero_network_preflight(args.output_root, config, experiment=_semantic_experiment(args.experiment_revision))
    print(canonical_json_bytes(report).decode("utf-8"))
    return 0 if report.get("status") == "PASS" else 1


def _load_batch_executor(path: str | None, outcome_map: Path | None, *, sdk_route: str | None = None,
                         sdk_model: str | None = None, sdk_prompt: Path | None = None,
                         sdk_prompt_identity: str | None = None, sdk_source_identity: str | None = None,
                         sdk_max_tokens: int = 500000, sdk_timeout_seconds: float = 300.0):
    from .semantic_batch_runner import AttemptOutcome, UNKNOWN
    if sdk_route is not None:
        if path is not None or outcome_map is not None or sdk_model is None or sdk_prompt is None or sdk_prompt_identity is None:
            raise ValueError("SDK batch execution requires route, model, prompt, and prompt identity only")
        from .semantic_sdk_runner import make_sdk_batch_executor
        prompt = json.loads(sdk_prompt.read_bytes())
        if not isinstance(prompt, dict):
            raise ValueError("SDK prompt must be a JSON object")
        return make_sdk_batch_executor(
            route=sdk_route, model=sdk_model, prompt=prompt,
            prompt_identity=sdk_prompt_identity, source_identity=sdk_source_identity,
            max_tokens=sdk_max_tokens, timeout_seconds=sdk_timeout_seconds)
    if outcome_map is not None:
        values = json.loads(outcome_map.read_bytes())
        if not isinstance(values, dict):
            raise ValueError("outcome map must be a JSON object")
        def fake(unit, _stem, _manifest):
            value = values.get(unit["compilation_unit_id"], {"disposition": UNKNOWN, "execution_certainty": "unknown", "failure_class": "not_provided"})
            return AttemptOutcome(**value) if isinstance(value, dict) else value
        return fake
    if path is None:
        raise ValueError("run requires an explicit executor adapter")
    import importlib
    module_name, separator, attr = path.partition(":")
    if not separator or not module_name or not attr:
        raise ValueError("executor must use module:function syntax")
    value = getattr(importlib.import_module(module_name), attr)
    if not callable(value):
        raise ValueError("executor is not callable")
    return value


def _semantic_batch_main(argv: list[str]) -> int:
    from .semantic_batch_runner import (
        aggregate_status, create_rerun, create_resume, dry_run as batch_dry_run,
        offline_audit, prepare_batch, run_batch,
    )
    commands = argparse.ArgumentParser(description="Immutable semantic batch coordinator")
    sub = commands.add_subparsers(dest="command", required=True)

    def add_execution_options(parser):
        parser.add_argument("--workers", type=int, choices=(1, 6, 12), default=1)
        parser.add_argument("--executor")
        parser.add_argument("--outcome-map", type=Path)
        parser.add_argument("--sdk-route", choices=("tokenmetro", "jizhi"))
        parser.add_argument("--model")
        parser.add_argument("--prompt", type=Path)
        parser.add_argument("--prompt-identity")
        parser.add_argument("--source-identity")
        parser.add_argument("--max-tokens", type=int, default=500000)
        parser.add_argument("--timeout-seconds", type=float, default=300.0)
        parser.add_argument("--max-units", type=int)
        parser.add_argument("--request-budget", type=int)
        parser.add_argument("--wall-clock-seconds", type=float)
        parser.add_argument("--stop-on-unknown", action=argparse.BooleanOptionalAction, default=True)

    def execute_child(args, child_root: Path):
        if args.sdk_route is not None:
            manifest = json.loads((child_root / "manifest.json").read_bytes())
            if manifest.get("route") != args.sdk_route:
                raise ValueError("SDK route differs from immutable batch manifest route")
        return run_batch(child_root, workers=args.workers,
                         executor=_load_batch_executor(
                             args.executor, args.outcome_map, sdk_route=args.sdk_route,
                             sdk_model=args.model, sdk_prompt=args.prompt,
                             sdk_prompt_identity=args.prompt_identity,
                             sdk_source_identity=args.source_identity,
                             sdk_max_tokens=args.max_tokens,
                             sdk_timeout_seconds=args.timeout_seconds),
                         max_units=args.max_units, request_budget=args.request_budget,
                         wall_clock_seconds=args.wall_clock_seconds,
                         stop_on_unknown=args.stop_on_unknown)
    prepare = sub.add_parser("prepare")
    prepare.add_argument("--root", required=True, type=Path)
    prepare.add_argument("--units", required=True, type=Path)
    prepare.add_argument("--route", required=True)
    prepare.add_argument("--contract", type=Path)
    dry = sub.add_parser("dry-run")
    dry.add_argument("--root", required=True, type=Path)
    run = sub.add_parser("run")
    run.add_argument("--root", required=True, type=Path)
    add_execution_options(run)
    resume = sub.add_parser("resume")
    resume.add_argument("--root", required=True, type=Path)
    add_execution_options(resume)
    rerun = sub.add_parser("rerun")
    rerun.add_argument("--root", required=True, type=Path)
    rerun.add_argument("--unit-id", action="append")
    rerun.add_argument("--state", action="append")
    rerun.add_argument("--route")
    add_execution_options(rerun)
    status = sub.add_parser("status")
    status.add_argument("--root", required=True, type=Path)
    audit = sub.add_parser("audit")
    audit.add_argument("--root", required=True, type=Path)
    args = commands.parse_args(argv)
    if args.command == "prepare":
        units = json.loads(args.units.read_bytes())
        if isinstance(units, dict):
            units = units.get("units", units.get("items"))
        if not isinstance(units, list):
            raise ValueError("units file must contain a JSON list or units/items object")
        contract = json.loads(args.contract.read_bytes()) if args.contract else None
        result = prepare_batch(args.root, units=units, route=args.route, contract=contract)
    elif args.command == "dry-run":
        result = batch_dry_run(args.root)
    elif args.command == "run":
        if args.sdk_route is not None:
            manifest = json.loads((args.root / "manifest.json").read_bytes())
            if manifest.get("route") != args.sdk_route:
                raise ValueError("SDK route differs from immutable batch manifest route")
        result = run_batch(args.root, workers=args.workers,
                           executor=_load_batch_executor(
                               args.executor, args.outcome_map, sdk_route=args.sdk_route,
                               sdk_model=args.model, sdk_prompt=args.prompt,
                               sdk_prompt_identity=args.prompt_identity,
                               sdk_source_identity=args.source_identity,
                               sdk_max_tokens=args.max_tokens,
                               sdk_timeout_seconds=args.timeout_seconds),
                           max_units=args.max_units, request_budget=args.request_budget,
                           wall_clock_seconds=args.wall_clock_seconds,
                           stop_on_unknown=args.stop_on_unknown)
    elif args.command == "resume":
        child = create_resume(args.root)
        result = {"child_root": str(child), "status": "prepared"}
        if args.executor or args.outcome_map or args.sdk_route:
            result.update(execute_child(args, child))
    elif args.command == "rerun":
        child = create_rerun(args.root, unit_ids=args.unit_id, states=args.state, route=args.route)
        result = {"child_root": str(child), "status": "prepared"}
        if args.executor or args.outcome_map or args.sdk_route:
            result.update(execute_child(args, child))
    elif args.command == "status":
        result = aggregate_status(args.root)
    else:
        result = offline_audit(args.root)
    print(canonical_json_bytes(result).decode("utf-8"))
    return 0


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
    if argv and argv[0] == "semantic-batch":
        return _semantic_batch_main(argv[1:])
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
