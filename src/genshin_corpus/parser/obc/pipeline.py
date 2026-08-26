from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from ..classification import classification_dependencies
from ..contracts import (
    PARSED_SCHEMA_VERSION,
    PARSER_VERSION,
    ContractMetadata,
    Diagnostic,
    ParsedIdentity,
    RawRef,
    SourcePosition,
)
from ..fingerprints import parsed_fingerprint, source_fingerprint
from ..storage import ParsedRunStore, blank_manifest
from .adapter import OBCIntegrityError, parse_obc_detail


SOURCE = "mihoyo_obc"
PARSED_PIPELINE_VERSION = "obc-parsed-run-0.2"
_STATUS_NAMES = ("parsed", "parsed_with_anomalies", "preserved_unsupported", "blocked_integrity")


@dataclass(frozen=True)
class ParseDependencies:
    schema_version: str = PARSED_SCHEMA_VERSION
    parser_version: str = PARSER_VERSION
    pipeline_version: str = PARSED_PIPELINE_VERSION
    rule_versions: tuple[tuple[str, str], ...] = ()

    @classmethod
    def current(cls) -> "ParseDependencies":
        return cls(rule_versions=tuple(sorted(classification_dependencies().items())))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "parser_version": self.parser_version,
            "pipeline_version": self.pipeline_version,
            "rule_versions": dict(self.rule_versions),
        }


@dataclass(frozen=True)
class _RawInput:
    content_id: str
    channels: tuple[str, ...]
    raw_path: Path | None
    metadata_path: Path | None
    manifest_sha256: str | None
    validation_error: str | None = None


def _sha256(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _diagnostic_codes(value: Any) -> tuple[str, ...]:
    """Return unique diagnostics carried by nested Parsed contract metadata."""

    codes: set[str] = set()

    def visit(node: Any) -> None:
        if isinstance(node, Mapping):
            metadata = node.get("metadata")
            if isinstance(metadata, Mapping):
                diagnostics = metadata.get("diagnostics")
                if isinstance(diagnostics, list):
                    for diagnostic in diagnostics:
                        if isinstance(diagnostic, Mapping) and isinstance(diagnostic.get("code"), str):
                            codes.add(diagnostic["code"])
            for child in node.values():
                visit(child)
        elif isinstance(node, list):
            for child in node:
                visit(child)

    visit(value)
    return tuple(sorted(codes))


def _effective_channel_memberships(channels: Iterable[str]) -> tuple[str, ...]:
    """Match the adapter's ordered, duplicate-free detail membership output."""

    return tuple(dict.fromkeys(str(channel) for channel in channels))


class OBCParsedRunPipeline:
    """Build one auditable Parsed observation run from an immutable OBC Raw run."""

    def __init__(
        self,
        *,
        raw_run_root: Path,
        output_root: Path = Path("data/parsed"),
        parsed_run_id: str,
        reuse_manifest_path: Path | None = None,
        dependencies: ParseDependencies | None = None,
        detail_parser: Callable[..., Any] = parse_obc_detail,
    ) -> None:
        self.raw_run_root = raw_run_root
        self.output_root = output_root
        self.parsed_run_id = parsed_run_id
        self.reuse_manifest_path = reuse_manifest_path
        self.dependencies = dependencies or ParseDependencies.current()
        self.detail_parser = detail_parser

    def run(self) -> dict[str, Any]:
        raw_manifest_path = self.raw_run_root / "metadata" / "manifest.json"
        raw_manifest_body = raw_manifest_path.read_bytes()
        raw_manifest = json.loads(raw_manifest_body.decode("utf-8"))
        source = raw_manifest.get("source_system")
        locale = raw_manifest.get("locale")
        raw_run_id = raw_manifest.get("run_id")
        if source != SOURCE or not isinstance(locale, str) or not isinstance(raw_run_id, str):
            raise ValueError("Raw manifest is not a recognized mihoyo_obc run")
        if raw_manifest.get("status") != "complete":
            raise ValueError("OBC Parsed runner requires a complete Raw manifest")

        store = ParsedRunStore(self.output_root, source, locale, self.parsed_run_id)
        manifest = blank_manifest(
            source=source,
            locale=locale,
            parsed_run_id=self.parsed_run_id,
            raw_run_id=raw_run_id,
            raw_manifest_sha256=_sha256(raw_manifest_body),
            dependencies=self.dependencies.to_dict(),
        )
        inputs = self._raw_inputs(raw_manifest)
        existing = self._completed_manifest_if_current(
            store=store,
            inputs=inputs,
            raw_manifest_sha256=manifest["raw_manifest_sha256"],
            raw_run_id=raw_run_id,
        )
        if existing is not None:
            return existing
        manifest["input_detail_count"] = len(inputs)
        reusable = self._load_reusable_records(source=source, locale=locale, raw_run_id=raw_run_id)
        records: list[dict[str, Any]] = []
        all_diagnostics: Counter[str] = Counter()
        status_counts: Counter[str] = Counter()
        reused = 0
        reparsed = 0

        for item in inputs:
            record, did_reuse = self._process_item(
                item,
                source=source,
                locale=locale,
                raw_run_id=raw_run_id,
                reusable=reusable,
                store=store,
            )
            records.append(record)
            status = record["parse_status"]
            status_counts[status] += 1
            all_diagnostics.update(record.get("diagnostic_codes", ()))
            if did_reuse:
                reused += 1
            else:
                reparsed += 1

        manifest["records"] = records
        manifest["accounted_detail_count"] = len(records)
        manifest["reuse_count"] = reused
        manifest["reparse_count"] = reparsed
        manifest["counts"] = {name: status_counts[name] for name in _STATUS_NAMES}
        manifest["diagnostics"] = [
            {"code": code, "count": all_diagnostics[code]}
            for code in sorted(all_diagnostics)
        ]
        manifest["status"] = "complete"
        store.write_manifest(manifest)
        return manifest

    def _raw_inputs(self, raw_manifest: Mapping[str, Any]) -> tuple[_RawInput, ...]:
        details = raw_manifest.get("details")
        paths = raw_manifest.get("paths")
        if not isinstance(details, list) or not isinstance(paths, Mapping):
            raise ValueError("Raw manifest missing details or paths")
        inputs: list[_RawInput] = []
        seen: set[str] = set()
        for detail in details:
            if not isinstance(detail, Mapping) or detail.get("content_id") is None:
                raise ValueError("Raw manifest contains detail without content_id")
            content_id = str(detail["content_id"])
            if content_id in seen:
                raise ValueError(f"Raw manifest duplicates detail content_id: {content_id}")
            seen.add(content_id)
            path_entry = paths.get(f"details:{content_id}")
            channels = tuple(str(item) for item in detail.get("channels", ()) if item is not None)
            if detail.get("status") != "completed" or not isinstance(path_entry, Mapping):
                inputs.append(_RawInput(content_id, channels, None, None, None, "RAW_DETAIL_NOT_COMPLETED"))
                continue
            raw_value = path_entry.get("raw")
            metadata_value = path_entry.get("metadata")
            expected_sha = path_entry.get("sha256")
            raw_path = self._resolve_path(raw_value) if isinstance(raw_value, str) else None
            metadata_path = self._resolve_path(metadata_value) if isinstance(metadata_value, str) else None
            validation_error = None
            if raw_path is None or metadata_path is None or not isinstance(expected_sha, str):
                validation_error = "RAW_PATH_RECORD_INVALID"
            inputs.append(_RawInput(content_id, channels, raw_path, metadata_path, expected_sha, validation_error))
        return tuple(inputs)

    def _completed_manifest_if_current(
        self,
        *,
        store: ParsedRunStore,
        inputs: tuple[_RawInput, ...],
        raw_manifest_sha256: str,
        raw_run_id: str,
    ) -> dict[str, Any] | None:
        """Reuse a complete same-run result only after rechecking all Raw bytes."""

        existing = store.read_manifest()
        if not isinstance(existing, Mapping) or existing.get("status") != "complete":
            return None
        if (
            existing.get("raw_manifest_sha256") != raw_manifest_sha256
            or existing.get("raw_run_id") != raw_run_id
            or existing.get("dependencies") != self.dependencies.to_dict()
            or existing.get("schema_version") != self.dependencies.schema_version
            or existing.get("parser_version") != self.dependencies.parser_version
            or existing.get("input_detail_count") != len(inputs)
        ):
            return None
        indexed = {
            str(record.get("content_id")): record
            for record in existing.get("records", ())
            if isinstance(record, Mapping) and record.get("content_id") is not None
        }
        if len(indexed) != len(inputs):
            return None
        for item in inputs:
            body, raw_ref, error = self._validated_body(item, source=SOURCE, locale=str(existing.get("locale", "")), raw_run_id=raw_run_id)
            record = indexed.get(item.content_id)
            if error is not None or body is None or raw_ref is None or not isinstance(record, Mapping):
                return None
            if record.get("dependency_fingerprint") != self._dependency_fingerprint(item, raw_ref):
                return None
            if record.get("raw_artifact_sha256") != raw_ref.artifact_sha256:
                return None
            record_path = record.get("record_path")
            record_sha = record.get("record_sha256")
            if not isinstance(record_path, str) or not isinstance(record_sha, str) or not Path(record_path).is_file():
                return None
            if _sha256(Path(record_path).read_bytes()) != record_sha:
                return None
        return dict(existing)

    def _resolve_path(self, value: str) -> Path:
        candidate = Path(value)
        if candidate.is_absolute():
            return candidate
        if candidate.exists():
            return candidate
        return self.raw_run_root / candidate

    def _load_reusable_records(
        self,
        *,
        source: str,
        locale: str,
        raw_run_id: str,
    ) -> dict[str, Mapping[str, Any]]:
        if self.reuse_manifest_path is None:
            return {}
        prior = _read_json(self.reuse_manifest_path)
        if prior.get("status") != "complete":
            raise ValueError("reuse manifest must be a complete mihoyo_obc Parsed manifest")
        if (
            prior.get("source") != source
            or prior.get("locale") != locale
            or prior.get("raw_run_id") != raw_run_id
        ):
            # Reused records retain their RawRef. Cross-run reuse needs an
            # explicit observation/projection contract, deferred from Phase 02.
            return {}
        records = prior.get("records")
        if not isinstance(records, list):
            raise ValueError("reuse manifest records is not a list")
        return {
            str(record["content_id"]): record
            for record in records
            if isinstance(record, Mapping) and record.get("content_id") is not None
        }

    def _process_item(
        self,
        item: _RawInput,
        *,
        source: str,
        locale: str,
        raw_run_id: str,
        reusable: Mapping[str, Mapping[str, Any]],
        store: ParsedRunStore,
    ) -> tuple[dict[str, Any], bool]:
        body, raw_ref, error = self._validated_body(item, source=source, locale=locale, raw_run_id=raw_run_id)
        dependency = self._dependency_fingerprint(item, raw_ref)
        memberships = _effective_channel_memberships(item.channels)
        prior = reusable.get(item.content_id)
        if error is None and prior is not None and prior.get("dependency_fingerprint") == dependency:
            record_path = prior.get("record_path")
            record_sha = prior.get("record_sha256")
            if isinstance(record_path, str) and isinstance(record_sha, str) and Path(record_path).is_file():
                if _sha256(Path(record_path).read_bytes()) == record_sha:
                    reused = dict(prior)
                    reused["diagnostic_codes"] = list(_diagnostic_codes(_read_json(Path(record_path))))
                    reused["reuse"] = True
                    return reused, True

        if error is not None or body is None or raw_ref is None:
            return self._blocked_record(item, dependency, raw_ref, error or "RAW_ARTIFACT_INVALID", store), False
        try:
            detail = self.detail_parser(body, raw_ref=raw_ref, content_id=item.content_id, channel_memberships=memberships)
            value = detail.to_dict()
            status = detail.metadata.parse_status
            diagnostic_codes = _diagnostic_codes(value)
        except OBCIntegrityError as exc:
            return self._blocked_record(item, dependency, raw_ref, str(exc), store), False
        except Exception as exc:
            return self._blocked_record(item, dependency, raw_ref, f"PARSER_EXCEPTION:{type(exc).__name__}:{exc}", store), False

        written = store.write_record(detail.identity.key, value)
        return {
            "content_id": item.content_id,
            "channels": list(memberships),
            "dependency_fingerprint": dependency,
            "raw_artifact_sha256": raw_ref.artifact_sha256,
            "parse_status": status,
            "diagnostic_codes": list(diagnostic_codes),
            "record_path": written["path"],
            "record_sha256": written["sha256"],
            "record_identity_key": written["identity_key"],
            "reuse": False,
        }, False

    def _validated_body(
        self,
        item: _RawInput,
        *,
        source: str,
        locale: str,
        raw_run_id: str,
    ) -> tuple[bytes | None, RawRef | None, str | None]:
        if item.validation_error:
            return None, None, item.validation_error
        assert item.raw_path is not None and item.metadata_path is not None and item.manifest_sha256 is not None
        raw_ref = RawRef(
            source=source,
            locale=locale,
            run_id=raw_run_id,
            artifact_kind="details",
            artifact_path=str(item.raw_path),
            artifact_sha256=item.manifest_sha256,
            content_id=item.content_id,
        )
        try:
            body = item.raw_path.read_bytes()
            metadata_body = item.metadata_path.read_bytes()
            metadata = json.loads(metadata_body.decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            return None, raw_ref, f"RAW_ARTIFACT_READ_ERROR:{type(exc).__name__}"
        actual_sha = _sha256(body)
        if actual_sha != item.manifest_sha256:
            actual_ref = RawRef(
                source=source,
                locale=locale,
                run_id=raw_run_id,
                artifact_kind="details",
                artifact_path=str(item.raw_path),
                artifact_sha256=actual_sha,
                content_id=item.content_id,
            )
            return None, actual_ref, "RAW_MANIFEST_SHA256_MISMATCH"
        if not isinstance(metadata, Mapping):
            return None, raw_ref, "RAW_METADATA_NOT_OBJECT"
        if metadata.get("kind") != "details" or str(metadata.get("key")) != item.content_id:
            return None, raw_ref, "RAW_METADATA_IDENTITY_MISMATCH"
        if metadata.get("sha256") != actual_sha or metadata.get("ok") is not True or metadata.get("status") != 200:
            return None, raw_ref, "RAW_METADATA_INTEGRITY_MISMATCH"
        return body, raw_ref, None

    def _dependency_fingerprint(self, item: _RawInput, raw_ref: RawRef | None) -> str:
        return parsed_fingerprint({
            "content_id": item.content_id,
            "raw_artifact_sha256": raw_ref.artifact_sha256 if raw_ref else item.manifest_sha256,
            "channel_memberships": list(_effective_channel_memberships(item.channels)),
            "dependencies": self.dependencies.to_dict(),
        })

    def _blocked_record(
        self,
        item: _RawInput,
        dependency: str,
        raw_ref: RawRef | None,
        error: str,
        store: ParsedRunStore,
    ) -> dict[str, Any]:
        refs = () if raw_ref is None else (raw_ref,)
        metadata = ContractMetadata(
            parse_status="blocked_integrity",
            raw_refs=refs,
            source_position=SourcePosition(),
            source_fingerprint=source_fingerprint({"content_id": item.content_id, "expected_sha256": item.manifest_sha256}),
            parsed_fingerprint=parsed_fingerprint({"content_id": item.content_id, "blocked": error}),
            diagnostics=(Diagnostic("PARSED_INPUT_INTEGRITY", error, "error"),),
        )
        identity = ParsedIdentity(
            kind="detail_observation",
            key=f"blocked:{item.content_id}:{dependency}",
            stability="snapshot_only",
            components=(("content_id", item.content_id), ("dependency", dependency)),
        )
        value = {
            "identity": identity.to_dict(),
            "metadata": metadata.to_dict(),
            "content_id": item.content_id,
            "channels": list(item.channels),
            "error": error,
        }
        written = store.write_record(identity.key, value)
        return {
            "content_id": item.content_id,
            "channels": list(item.channels),
            "dependency_fingerprint": dependency,
            "raw_artifact_sha256": None if raw_ref is None else raw_ref.artifact_sha256,
            "parse_status": "blocked_integrity",
            "diagnostic_codes": ["PARSED_INPUT_INTEGRITY"],
            "record_path": written["path"],
            "record_sha256": written["sha256"],
            "record_identity_key": written["identity_key"],
            "reuse": False,
        }


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Parse one local MiHoYo OBC Raw run into immutable Parsed records.")
    parser.add_argument("--raw-run-root", type=Path, required=True)
    parser.add_argument("--parsed-run-id", required=True)
    parser.add_argument("--output-root", type=Path, default=Path("data/parsed"))
    parser.add_argument("--reuse-manifest", type=Path)
    args = parser.parse_args(argv)
    manifest = OBCParsedRunPipeline(
        raw_run_root=args.raw_run_root,
        output_root=args.output_root,
        parsed_run_id=args.parsed_run_id,
        reuse_manifest_path=args.reuse_manifest,
    ).run()
    print(json.dumps({
        "parsed_run_id": manifest["parsed_run_id"],
        "input_detail_count": manifest["input_detail_count"],
        "accounted_detail_count": manifest["accounted_detail_count"],
        "counts": manifest["counts"],
        "reuse_count": manifest["reuse_count"],
        "reparse_count": manifest["reparse_count"],
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
