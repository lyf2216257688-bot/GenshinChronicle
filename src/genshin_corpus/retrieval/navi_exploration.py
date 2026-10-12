"""Local E1 tools and reviewed topic projection; no model or network transport."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
from hashlib import sha256
import gzip
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from genshin_corpus.canonical.fingerprints import canonical_json_bytes, sha256_json
from . import candidate_retrieval as lexical
from . import retrieval_units as ru
from .semantic_compilation import SemanticBuild, SemanticItem, SemanticStage, SourceBinding


class ExplorationError(ValueError):
    """Invalid source, action, review, or replay binding."""


class ExplorationBudgetExceeded(ExplorationError):
    """An action cannot fit the remaining hard budget."""


@dataclass(frozen=True)
class ExplorationBudget:
    max_rounds: int = 12
    max_searches: int = 4
    max_reads: int = 6
    max_candidates: int = 10
    max_context_chars: int = 20000
    max_read_chars: int = 20000
    max_query_chars: int = 160
    max_neighbors: int = 1
    max_clues: int = 16

    def __post_init__(self) -> None:
        for key, value in asdict(self).items():
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise ExplorationError(f"{key} must be a positive integer")


def _text(value: Any, label: str, limit: int = 1000) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ExplorationError(f"invalid {label}")
    return value


def _verified_artifact(manifest_path: Path, descriptor: Mapping[str, Any]) -> Path:
    root = manifest_path.parent.parent.resolve()
    path = (root / descriptor["path"]).resolve()
    if not path.is_relative_to(root):
        raise ExplorationError("artifact escapes its snapshot root")
    digest = sha256()
    size = 0
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    if digest.hexdigest() != descriptor["sha256"] or size != descriptor["byte_count"]:
        raise ExplorationError("artifact hash/size mismatch")
    return path


class LocalExplorationTools:
    """Bounded responses over existing artifacts, with Canonical projection checks.

    The lexical adapter streams query-relevant term frequencies but retains
    every document length and the full corpus denominator for unchanged BM25.
    """

    def __init__(self, canonical_manifest: Path, ru_manifest: Path, lexical_manifest: Path):
        self.canonical_path = Path(canonical_manifest).resolve()
        self.ru_path = Path(ru_manifest).resolve()
        self.lexical_path = Path(lexical_manifest).resolve()
        canonical_bytes = self.canonical_path.read_bytes()
        self.canonical = json.loads(canonical_bytes)
        self.entries = ru._validate_manifest(self.canonical)
        self.ru_manifest = json.loads(self.ru_path.read_bytes())
        self.lexical_manifest = json.loads(self.lexical_path.read_bytes())
        self.ru_identity = self.ru_manifest["build_identity"]
        if self.ru_manifest.get("status") != "complete" or self.ru_manifest.get("schema_version") != ru.RETRIEVAL_UNIT_BUILD_SCHEMA_VERSION:
            raise ExplorationError("unsupported RU snapshot")
        expected_input = ru._canonical_input_identity(self.canonical, self.entries)
        if self.ru_manifest["canonical_input"]["canonical_input_identity"] != expected_input:
            raise ExplorationError("Canonical/RU input identity mismatch")
        config = ru.RetrievalUnitBuildConfig(
            text_fragment_chars=self.ru_manifest["projection_config"]["text_fragment_chars"],
            structured_fragment_chars=self.ru_manifest["projection_config"]["structured_fragment_chars"],
        )
        if self.ru_manifest["build_identity"] != ru._build_identity(self.canonical, expected_input, config):
            raise ExplorationError("RU build identity does not match its Canonical/config inputs")
        lm = self.lexical_manifest
        if lm.get("status") != "complete" or lm.get("retrieval_unit_build_identity") != self.ru_identity:
            raise ExplorationError("lexical/RU identity mismatch")
        if lm.get("schema_version") not in {lexical.LEXICAL_INDEX_SCHEMA_VERSION, lexical.FIELD_AWARE_LEXICAL_INDEX_SCHEMA_VERSION}:
            raise ExplorationError("unsupported lexical snapshot")
        if lm.get("analyzer_version") != lexical.LEXICAL_ANALYZER_VERSION or lm.get("scorer_version") not in {lexical.LEXICAL_SCORER_VERSION, lexical.FIELD_AWARE_LEXICAL_SCORER_VERSION}:
            raise ExplorationError("lexical implementation identity mismatch")
        if lm["schema_version"] == lexical.FIELD_AWARE_LEXICAL_INDEX_SCHEMA_VERSION:
            if (tuple(lm.get("fields", ())) != lexical.FIELD_AWARE_LEXICAL_FIELDS
                or lm.get("projection_version") != lexical.FIELD_AWARE_LEXICAL_PROJECTION_VERSION
                or lm.get("row_order_policy") != lexical.FIELD_AWARE_LEXICAL_ROW_ORDER_POLICY
                or lm.get("arm_build_identity") != lexical._field_aware_lexical_arm_identity(self.ru_identity)):
                raise ExplorationError("field-aware lexical projection identity mismatch")
        elif lm.get("arm_build_identity") != lexical._arm_identity({
            "schema_version": lexical.LEXICAL_INDEX_SCHEMA_VERSION, "ru_build_identity": self.ru_identity,
            "analyzer_version": lm["analyzer_version"], "row_order_policy": "W1 manifest source_order then unit_id",
        }):
            raise ExplorationError("lexical build identity mismatch")
        self.by_record = {e["record_id"]: e for e in self.entries}
        if len(self.by_record) != len(self.entries):
            raise ExplorationError("duplicate Canonical record identity")
        self._manifest_hashes = {path: sha256(path.read_bytes()).hexdigest() for path in (self.canonical_path, self.ru_path, self.lexical_path)}
        self.identity = sha256_json({
            "tool_contract": "p05-navi-e1-local-tools-0.1",
            "canonical_manifest_sha256": sha256(canonical_bytes).hexdigest(),
            "ru_manifest_sha256": sha256(self.ru_path.read_bytes()).hexdigest(),
            "lexical_manifest_sha256": sha256(self.lexical_path.read_bytes()).hexdigest(),
        })
        self._ru_cache: dict[str, Mapping[str, Any]] = {}

    def verify_snapshot(self) -> None:
        for path, digest in self._manifest_hashes.items():
            if sha256(path.read_bytes()).hexdigest() != digest:
                raise ExplorationError("input manifest changed during experiment")
        _verified_artifact(self.ru_path, self.ru_manifest["artifacts"]["retrieval_units"])

    def _load_selected_ru(self, unit_ids: Sequence[str]) -> dict[str, Mapping[str, Any]]:
        self.verify_snapshot()
        requested = tuple(dict.fromkeys(unit_ids))
        missing = [unit_id for unit_id in requested if unit_id not in self._ru_cache]
        if missing:
            try:
                _, rows = ru.load_retrieval_units(self.ru_path, selected_unit_ids=missing)
            except ru.RetrievalUnitError as exc:
                raise ExplorationError(str(exc)) from exc
            self._ru_cache.update((row["unit_id"], row) for row in rows)
        return {unit_id: deepcopy(self._ru_cache[unit_id]) for unit_id in requested}

    def search(self, query: str, *, top_k: int) -> dict[str, Any]:
        self.verify_snapshot()
        _text(query, "query", 160)
        if not isinstance(top_k, int) or isinstance(top_k, bool) or not 1 <= top_k <= 10:
            raise ExplorationError("invalid search top_k")
        manifest = self.lexical_manifest
        path = _verified_artifact(self.lexical_path, manifest["artifacts"]["index"])
        terms = set(lexical.analyze(query))
        rows = []
        seen = set()
        field_aware = manifest["schema_version"] == lexical.FIELD_AWARE_LEXICAL_INDEX_SCHEMA_VERSION
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            for line in handle:
                row = json.loads(line)
                uid = _text(row.get("unit_id"), "lexical unit id")
                if uid in seen:
                    raise ExplorationError("duplicate lexical unit id")
                seen.add(uid)
                if field_aware:
                    if set(row["fields"]) != set(lexical.FIELD_AWARE_LEXICAL_FIELDS):
                        raise ExplorationError("invalid lexical fields")
                    stats = row["fields"].values()
                else:
                    stats = (row,)
                for stat in stats:
                    tf, length = stat.get("tf"), stat.get("length")
                    if not isinstance(length, int) or isinstance(length, bool) or length < 0 or not isinstance(tf, dict):
                        raise ExplorationError("invalid lexical statistics")
                    if any(not isinstance(t, str) or not isinstance(n, int) or isinstance(n, bool) or n < 0 for t, n in tf.items()) or sum(tf.values()) != length:
                        raise ExplorationError("invalid lexical frequencies")
                if field_aware:
                    row = {"unit_id": uid, "fields": {k: {"length": v["length"], "tf": {t: n for t, n in v["tf"].items() if t in terms}} for k, v in row["fields"].items()}}
                else:
                    row = {"unit_id": uid, "length": row["length"], "tf": {t: n for t, n in row["tf"].items() if t in terms}}
                rows.append(row)
        if len(rows) != manifest["artifacts"]["index"]["row_count"] or len(rows) != manifest["row_count"]:
            raise ExplorationError("lexical row accounting mismatch")
        kwargs = dict(top_k=top_k, k1=1.2, b=0.75, analyzer=lexical.analyze)
        if field_aware:
            candidates = lexical._field_aware_lexical_candidates_from_loaded(manifest, rows, query, field_weights=lexical.DEFAULT_FIELD_AWARE_LEXICAL_WEIGHTS, **kwargs)
        else:
            candidates = lexical._lexical_candidates_from_loaded(manifest, rows, query, **kwargs)
        del rows, seen
        hits = []
        if candidates:
            index = self._load_selected_ru([c["unit_id"] for c in candidates])
            for c in candidates:
                row = index[c["unit_id"]]
                text = row["retrieval_visible_text"]
                hits.append({**c, "canonical_address": row["source"]["canonical_address"], "record_context": row["source"]["record_context"], "snippet": text[:200], "snippet_omitted_chars": max(0, len(text) - 200)})
        return {"mode": "local_lexical_not_hybrid", "query": query, "hits": hits, "corpus_rows": manifest["row_count"]}

    def read(self, unit_id: str, *, max_chars: int, neighbors: int = 0) -> dict[str, Any]:
        if not isinstance(max_chars, int) or isinstance(max_chars, bool) or not 1 <= max_chars <= 20000:
            raise ExplorationError("invalid read budget")
        if not isinstance(neighbors, int) or isinstance(neighbors, bool) or not 0 <= neighbors <= 2:
            raise ExplorationError("invalid neighbor budget")
        try:
            row = self._load_selected_ru([unit_id])[unit_id]
        except (KeyError, ru.RetrievalUnitError) as exc:
            raise ExplorationError("unknown Retrieval Unit") from exc
        address = row["source"]["canonical_address"]
        entry = self.by_record.get(address["record_id"])
        if entry is None:
            raise ExplorationError("RU Canonical record absent from bound manifest")
        record_path = Path(entry["canonical_record_path"]).resolve()
        # Manifest paths are evidence, but a source read must remain in its run.
        if not record_path.is_relative_to(self.canonical_path.parent.parent):
            raise ExplorationError("Canonical record escapes snapshot root")
        record_index = self.entries.index(entry)
        record = ru._load_record(entry, record_index)
        config = ru.RetrievalUnitBuildConfig(
            text_fragment_chars=self.ru_manifest["projection_config"]["text_fragment_chars"],
            structured_fragment_chars=self.ru_manifest["projection_config"]["structured_fragment_chars"],
        )
        projected, _ = ru._validate_record(record, entry, record_index, config, source=self.canonical["source"], locale=self.canonical["locale"])
        expected = next((r for r in projected if r["unit_id"] == unit_id), None)
        if expected != row:
            raise ExplorationError("RU text/address/lineage differs from Canonical projection")
        section = next(s for s in record["sections"] if s["ordinal"] == address["section_ordinal"])
        context = next(c for c in section["component_contexts"] if c["observation_key"] == address["component_observation_key"])
        units = [u for u in section["units"] if u["parent_component_key"] == context["observation_key"]]
        target = next(i for i, u in enumerate(units) if u["ordinal"] == address["canonical_unit_ordinal"])
        chosen = units[max(0, target - neighbors):target + neighbors + 1]
        package = {
            "units": [{"ordinal": u["ordinal"], "kind": u["kind"], "value": u["value"], "metadata": u["metadata"], "lineage": u["lineage"], "content_role": u["content_role"], "provenance": u["provenance"]} for u in chosen],
            "section_metadata": section["source_metadata"], "component_context": context,
        }
        canonical_text = json.dumps(package, ensure_ascii=False, separators=(",", ":"))
        visible = row["retrieval_visible_text"]
        result = {
            "unit_id": unit_id, "source": row["source"],
            "nested_selector": row["nested_selector"], "fragment_selector": row["fragment_selector"],
            "ru_text": "", "canonical_payload": "",
            "ru_text_sha256": sha256(visible.encode("utf-8")).hexdigest(),
            "canonical_record_sha256": entry["canonical_record_sha256"],
            "canonical_payload_sha256": sha256(canonical_text.encode("utf-8")).hexdigest(),
            "omitted_chars": len(visible) + len(canonical_text), "partial": True,
            "context_unit_ordinals": [u["ordinal"] for u in chosen],
            "context_scope": "same_component_ordinal_neighbors_not_dialogue_branch_inference",
            "omitted_component_unit_count": len(units) - len(chosen),
            "source_binding_status": "verified", "semantic_acceptance_status": "not_accepted",
            "serialization": "canonical_json_unicode_characters_including_metadata",
        }
        if response_chars(result) > max_chars:
            raise ExplorationBudgetExceeded("read metadata cannot fit context budget")
        # Fit the entire delivered response, including locator and truncation
        # metadata. Canonical payload is a visible prefix when truncated.
        low, high = 0, len(visible) + len(canonical_text)
        while low < high:
            length = (low + high + 1) // 2
            result["ru_text"] = visible[:length]
            result["canonical_payload"] = canonical_text[:max(0, length - len(visible))]
            result["omitted_chars"] = len(visible) + len(canonical_text) - length
            result["partial"] = result["omitted_chars"] > 0
            if response_chars(result) <= max_chars:
                low = length
            else:
                high = length - 1
        result["ru_text"] = visible[:low]
        result["canonical_payload"] = canonical_text[:max(0, low - len(visible))]
        result["omitted_chars"] = len(visible) + len(canonical_text) - low
        result["partial"] = result["omitted_chars"] > 0
        return result


def response_chars(value: Mapping[str, Any]) -> int:
    """Budget the complete tool response, not just its text fields."""
    return len(canonical_json_bytes(value).decode("utf-8"))



def reviewed_topic_build(
    session: Mapping[str, Any], groups: Sequence[Mapping[str, Any]],
    reviews: Sequence[Mapping[str, Any]], ru_index: Mapping[str, Mapping[str, Any]],
    *, allow_mechanical_fixture: bool = False,
) -> SemanticBuild:
    """Consume explicit review; never infer acceptance from source binding.

    Mechanical fixture reviews require opt-in and produce fixture acceptance
    only. They are never promoted to curated human or production acceptance.
    """
    if session.get("status") not in {"stopped", "budget_exhausted", "decisions_exhausted"}:
        raise ExplorationError("only a completed exploration checkpoint can be projected")
    if session.get("checkpoint_identity") != sha256_json({k: v for k, v in session.items() if k != "checkpoint_identity"}):
        raise ExplorationError("session checkpoint integrity mismatch")
    receipts = {r["unit_id"]: r for r in session["read_sources"]}
    if len({g["group_id"] for g in groups}) != len(groups):
        raise ExplorationError("duplicate navigation group")
    review_by_group = {r["group_id"]: r for r in reviews}
    if len(review_by_group) != len(reviews):
        raise ExplorationError("duplicate review")
    if not set(review_by_group) <= {g["group_id"] for g in groups}:
        raise ExplorationError("review references unknown group")
    items = []
    for group in groups:
        if set(group) != {"group_id", "label", "unit_ids", "topic_path"}:
            raise ExplorationError("only navigation group fields are supported")
        gid = _text(group["group_id"], "group id")
        if not isinstance(group["topic_path"], list) or not group["topic_path"]:
            raise ExplorationError("navigation topic path required")
        ids = group["unit_ids"]
        if not isinstance(ids, list) or not ids or len(ids) != len(set(ids)):
            raise ExplorationError("invalid group sources")
        review = review_by_group.get(gid)
        accepted = False
        if review is not None:
            accepted = review.get("decision") == "accept_navigation_only"
            if not accepted and review.get("decision") != "reject":
                raise ExplorationError("unsupported human review decision")
            for field in ("reviewer", "rationale", "source_qualification", "narrative_caveat"):
                _text(review.get(field), field)
            if review.get("source_qualification") not in {"official_game_text_reviewed", "fixture_only", "unqualified"}:
                raise ExplorationError("unknown source qualification")
            if accepted and review.get("source_qualification") == "unqualified":
                raise ExplorationError("unqualified source cannot be active")
            if review.get("reviewer_kind") == "recorded_mechanical_fixture":
                if not allow_mechanical_fixture or review.get("source_qualification") != "fixture_only":
                    raise ExplorationError("mechanical fixture acceptance requires explicit opt-in")
            elif review.get("reviewer_kind") != "human":
                raise ExplorationError("human review required")
            if review.get("group_identity") != sha256_json(group) or review.get("session_identity") != session["checkpoint_identity"]:
                raise ExplorationError("human review binding mismatch")
        bindings = []
        for uid in ids:
            receipt = receipts.get(uid)
            row = ru_index.get(uid)
            if receipt is None or row is None or receipt["source"] != row["source"] or receipt["ru_text_sha256"] != sha256(row["retrieval_visible_text"].encode("utf-8")).hexdigest():
                raise ExplorationError("group source was not read from this verified snapshot")
            if accepted and receipt["partial"]:
                raise ExplorationError("partial reading cannot receive E1 navigation acceptance")
            bindings.append(SourceBinding(uid, row["source"]["canonical_address"], row["source"]["lineage"]))
        items.append(SemanticItem(
            item_id=sha256_json({"session": session["checkpoint_identity"], "group": group}),
            item_kind="topic", label=_text(group["label"], "group label"),
            source_bindings=tuple(bindings), topic_path=tuple(group["topic_path"]),
            semantic_acceptance_status=("accepted_for_fixture_test" if review and review.get("source_qualification") == "fixture_only" else "curated_recorded") if accepted else "rejected" if review else "not_accepted",
            qualifiers={"role": "navigation_only_not_fact", "human_review": review, "group_identity": sha256_json(group)},
            compiler_stage_identity="p05-navi-e1-reviewed-topics-0.1",
            compiler_run_identity=session["checkpoint_identity"], raw_response_identity=session["checkpoint_identity"],
            input_content_fingerprint=sha256_json(group),
        ))
    return SemanticBuild.from_items(
        input_identity=session["session_identity"], ru_index=ru_index, items=items,
        stages=(SemanticStage("human_topic_projection", "p05-navi-e1-reviewed-topics-0.1", session["session_identity"], "complete", {"provider_calls": 0}),),
    )
