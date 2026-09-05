"""Versioned lexical, local Dense, and deterministic RRF candidate retrieval.

This layer consumes the accepted W1 Retrieval Unit artifact.  It owns ranking
audit metadata only; Canonical provenance remains authoritative in W1 RU and
Evidence Assembly.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
import gzip
import json
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable

from genshin_corpus.canonical.fingerprints import canonical_json_bytes, sha256_json
from genshin_corpus.collector.storage import atomic_write

from .lexical import LEXICAL_ANALYZER_VERSION, LEXICAL_SCORER_VERSION, analyze
from .retrieval_units import load_retrieval_units


LEXICAL_INDEX_SCHEMA_VERSION = "phase04-rag-w2-lexical-index-0.1"
DENSE_INDEX_SCHEMA_VERSION = "phase04-rag-w2-dense-index-0.1"
CANDIDATE_SCHEMA_VERSION = "phase04-rag-w2-candidate-0.1"
RRF_FUSION_VERSION = "phase04-rag-w2-rrf-0.1"
DEFAULT_DENSE_MODEL_REVISION = "7999e1d3359715c523056ef9478215996d62a620"


class CandidateRetrievalError(ValueError):
    pass


def _freeze_candidate_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze_candidate_value(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_candidate_value(item) for item in value)
    return deepcopy(value)


def _thaw_candidate_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw_candidate_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_candidate_value(item) for item in value]
    return deepcopy(value)


@dataclass(frozen=True)
class CandidateBundle:
    """Immutable snapshot of the candidates returned by the current arm windows.

    The bundle intentionally preserves only the lexical, Dense, and Hybrid
    windows supplied by their current retrievers.  It does not represent an
    exhaustive corpus-wide candidate set.
    """

    candidate_windows: Mapping[str, tuple[Mapping[str, Any], ...]]
    retrieval_unit_build_identity: str | None

    @classmethod
    def from_current_windows(
        cls,
        candidates_by_mode: Mapping[str, Sequence[Mapping[str, Any]]],
        *,
        retrieval_unit_build_identity: str | None = None,
    ) -> "CandidateBundle":
        if not candidates_by_mode:
            raise CandidateRetrievalError("CandidateBundle requires at least one candidate window")
        windows: dict[str, tuple[Mapping[str, Any], ...]] = {}
        for mode, rows in candidates_by_mode.items():
            if not isinstance(mode, str) or not mode:
                raise CandidateRetrievalError("CandidateBundle mode must be a non-empty string")
            if isinstance(rows, (str, bytes)) or not isinstance(rows, Sequence):
                raise CandidateRetrievalError(f"CandidateBundle {mode} window must be a sequence")
            copied: list[Mapping[str, Any]] = []
            for index, row in enumerate(rows):
                value = _mapping(row, f"CandidateBundle {mode} candidate {index}")
                copied.append(_freeze_candidate_value(value))
            windows[mode] = tuple(copied)
        if retrieval_unit_build_identity is not None and (
            not isinstance(retrieval_unit_build_identity, str) or not retrieval_unit_build_identity
        ):
            raise CandidateRetrievalError("CandidateBundle Retrieval Unit build identity must be a non-empty string")
        return cls(
            candidate_windows=MappingProxyType(windows),
            retrieval_unit_build_identity=retrieval_unit_build_identity,
        )

    def candidates_for(self, mode: str) -> tuple[Mapping[str, Any], ...]:
        try:
            return tuple(_thaw_candidate_value(row) for row in self.candidate_windows[mode])
        except KeyError as exc:
            raise CandidateRetrievalError(f"CandidateBundle lacks {mode} window") from exc

    def audit_projection(self) -> dict[str, Any]:
        """Return detached rows so diagnostics cannot mutate the stored windows."""

        return {
            "schema_version": "phase04-rag-a1-candidate-bundle-0.1",
            "retrieval_unit_build_identity": self.retrieval_unit_build_identity,
            "candidate_windows": {
                mode: [_thaw_candidate_value(row) for row in rows]
                for mode, rows in sorted(self.candidate_windows.items())
            },
        }


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise CandidateRetrievalError(f"{label} must be an object")
    return value


def _positive_int(value: Any, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise CandidateRetrievalError(f"{label} must be a positive integer")
    return value


def _finite_float(value: Any, label: str) -> float:
    result = float(value)
    if result != result or result in (float("inf"), float("-inf")):
        raise CandidateRetrievalError(f"{label} must be finite")
    return result


def _gzip_jsonl(rows: Sequence[Mapping[str, Any]]) -> bytes:
    raw = b"".join(canonical_json_bytes(dict(row)) + b"\n" for row in rows)
    out = BytesIO()
    with gzip.GzipFile(fileobj=out, mode="wb", mtime=0) as handle:
        handle.write(raw)
    return out.getvalue()


def _read_gzip_jsonl(path: Path) -> list[Mapping[str, Any]]:
    try:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            values = [json.loads(line) for line in handle if line.strip()]
    except (OSError, UnicodeError, json.JSONDecodeError, gzip.BadGzipFile) as exc:
        raise CandidateRetrievalError(f"cannot read candidate artifact: {path}") from exc
    return [_mapping(value, f"candidate artifact row {index}") for index, value in enumerate(values, 1)]


def _descriptor(path: str, body: bytes, count: int | None = None) -> dict[str, Any]:
    result = {"path": path, "sha256": sha256(body).hexdigest(), "byte_count": len(body)}
    if count is not None:
        result["row_count"] = count
    return result


def _validate_ru_manifest(path: Path) -> tuple[Mapping[str, Any], list[Mapping[str, Any]]]:
    try:
        manifest, units = load_retrieval_units(path)
    except Exception as exc:
        raise CandidateRetrievalError("W1 Retrieval Unit artifact is not a supported complete build") from exc
    if not isinstance(manifest.get("build_identity"), str) or not manifest["build_identity"]:
        raise CandidateRetrievalError("W1 manifest lacks build_identity")
    for index, unit in enumerate(units, 1):
        if not isinstance(unit.get("unit_id"), str) or not isinstance(unit.get("retrieval_visible_text"), str):
            raise CandidateRetrievalError(f"invalid W1 Retrieval Unit row {index}")
    return manifest, units


def _ru_rows(units: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [{"unit_id": str(unit["unit_id"]), "text": str(unit["retrieval_visible_text"])} for unit in units]


def _arm_identity(payload: Mapping[str, Any]) -> str:
    return sha256_json(dict(payload))


def _document_frequencies(rows: Sequence[Mapping[str, Any]], terms: Sequence[str] | None = None) -> dict[str, int]:
    """Count in how many distinct indexed RUs each term occurs."""
    selected = set(terms) if terms is not None else {
        str(token)
        for row in rows
        for token in _mapping(row.get("tf"), "tf")
    }
    return {
        token: sum(1 for row in rows if int(_mapping(row.get("tf"), "tf").get(token, 0)) > 0)
        for token in sorted(selected)
    }


def build_lexical_index(
    retrieval_unit_manifest_path: Path,
    output_root: Path,
    *,
    analyzer: Callable[[str], list[str]] = analyze,
    analyzer_version: str = LEXICAL_ANALYZER_VERSION,
) -> dict[str, Any]:
    """Build a deterministic lexical index over W1 retrieval_visible_text."""
    ru_manifest, units = _validate_ru_manifest(Path(retrieval_unit_manifest_path))
    rows = _ru_rows(units)
    token_rows = [Counter(analyzer(row["text"])) for row in rows]
    index_rows = [
        {"unit_id": row["unit_id"], "length": sum(tokens.values()), "tf": dict(sorted(tokens.items()))}
        for row, tokens in zip(rows, token_rows)
    ]
    index_body = _gzip_jsonl(index_rows)
    identity = _arm_identity({
        "schema_version": LEXICAL_INDEX_SCHEMA_VERSION,
        "ru_build_identity": ru_manifest["build_identity"],
        "analyzer_version": analyzer_version,
        "row_order_policy": "W1 manifest source_order then unit_id",
    })
    output_root = Path(output_root)
    artifact_rel = "artifacts/lexical_index.jsonl.gz"
    manifest = {
        "schema_version": LEXICAL_INDEX_SCHEMA_VERSION,
        "status": "complete",
        "arm": "lexical",
        "arm_build_identity": identity,
        "retrieval_unit_build_identity": ru_manifest["build_identity"],
        "analyzer_version": analyzer_version,
        "scorer_version": LEXICAL_SCORER_VERSION,
        "row_count": len(rows),
        "artifacts": {"index": _descriptor(artifact_rel, index_body, len(index_rows))},
    }
    atomic_write(output_root / artifact_rel, index_body)
    atomic_write(output_root / "metadata" / "manifest.json", canonical_json_bytes(manifest))
    return manifest


def _load_lexical(path: Path) -> tuple[Mapping[str, Any], list[Mapping[str, Any]]]:
    manifest = _mapping(json.loads(Path(path).read_text(encoding="utf-8")), "lexical manifest")
    if manifest.get("status") != "complete" or manifest.get("schema_version") != LEXICAL_INDEX_SCHEMA_VERSION:
        raise CandidateRetrievalError("lexical manifest is not complete")
    artifact = _mapping(_mapping(manifest.get("artifacts"), "lexical artifacts").get("index"), "lexical index descriptor")
    body_path = Path(path).parent.parent / str(artifact["path"])
    body = body_path.read_bytes()
    if sha256(body).hexdigest() != artifact.get("sha256"):
        raise CandidateRetrievalError("lexical index SHA-256 mismatch")
    rows = _read_gzip_jsonl(body_path)
    if artifact.get("row_count") != len(rows):
        raise CandidateRetrievalError("lexical index row accounting mismatch")
    return manifest, rows


def lexical_candidates(
    lexical_manifest_path: Path,
    query: str,
    *,
    top_k: int = 20,
    k1: float = 1.2,
    b: float = 0.75,
    analyzer: Callable[[str], list[str]] = analyze,
) -> list[dict[str, Any]]:
    top_k = _positive_int(top_k, "top_k")
    k1, b = _finite_float(k1, "k1"), _finite_float(b, "b")
    if k1 < 0 or not 0 <= b <= 1:
        raise CandidateRetrievalError("invalid BM25 parameters")
    manifest, rows = _load_lexical(Path(lexical_manifest_path))
    return _lexical_candidates_from_loaded(manifest, rows, query, top_k=top_k, k1=k1, b=b, analyzer=analyzer)


def _lexical_candidates_from_loaded(
    manifest: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
    query: str,
    *,
    top_k: int,
    k1: float,
    b: float,
    analyzer: Callable[[str], list[str]],
) -> list[dict[str, Any]]:
    """Score one query against a previously validated lexical artifact."""

    terms = Counter(analyzer(str(query)))
    lengths = [int(row.get("length", 0)) for row in rows]
    average = sum(lengths) / len(lengths) if lengths else 0.0
    df = _document_frequencies(rows, list(terms))
    scored: list[tuple[float, str]] = []
    for row in rows:
        tf = _mapping(row.get("tf"), "tf")
        length = int(row.get("length", 0))
        score = 0.0
        for token, qf in terms.items():
            freq = int(tf.get(token, 0))
            if not freq:
                continue
            idf = __import__("math").log(1 + (len(rows) - df[token] + 0.5) / (df[token] + 0.5))
            denom = freq + k1 * (1 - b + b * (length / average if average else 0.0))
            score += qf * idf * ((freq * (k1 + 1)) / denom)
        if score > 0:
            scored.append((score, str(row["unit_id"])))
    scored.sort(key=lambda item: (-item[0], item[1]))
    query_identity = sha256_json({"analyzer_version": manifest.get("analyzer_version"), "scorer_version": LEXICAL_SCORER_VERSION, "k1": k1, "b": b, "top_k": top_k})
    return [
        {"unit_id": unit_id, "rank": rank, "retrieval": {"mode": "lexical", "score": score, "arm_build_identity": manifest["arm_build_identity"], "query_config_identity": query_identity}}
        for rank, (score, unit_id) in enumerate(scored[:top_k], 1)
    ]


def _dense_metadata_identity(metadata: Mapping[str, Any]) -> str:
    fields = {key: metadata.get(key) for key in ("model_name", "model_revision", "model_sha256", "embedding_dimension", "dtype", "normalization", "vectorization_schema_version", "row_mapping_policy")}
    return _arm_identity(fields)


def build_dense_index(
    retrieval_unit_manifest_path: Path,
    output_root: Path,
    *,
    model_dir: Path,
    model_name: str = "BAAI/bge-small-zh-v1.5",
    model_revision: str = DEFAULT_DENSE_MODEL_REVISION,
    instruction: str = "为这个句子生成表示以用于检索相关文章：",
    vectors: Any | None = None,
) -> dict[str, Any]:
    """Build Dense vectors; ``vectors`` is injectable for deterministic tests."""
    ru_manifest, units = _validate_ru_manifest(Path(retrieval_unit_manifest_path))
    model_dir = Path(model_dir)
    model_sha = None
    model_file = model_dir / "model.safetensors"
    if model_file.exists():
        model_sha = sha256(model_file.read_bytes()).hexdigest()
    if vectors is None:
        try:
            from sentence_transformers import SentenceTransformer
            import numpy as np
            model = SentenceTransformer(str(model_dir), local_files_only=True, device="cpu")
            values = model.encode([str(unit["retrieval_visible_text"]) for unit in units], convert_to_numpy=True, normalize_embeddings=True, show_progress_bar=False)
            vectors = np.asarray(values, dtype=np.float32)
        except Exception as exc:
            raise CandidateRetrievalError("local Dense model/runtime unavailable") from exc
    try:
        import numpy as np
        array = np.asarray(vectors, dtype=np.float32)
    except Exception as exc:
        raise CandidateRetrievalError("Dense vectors are not numeric") from exc
    if array.ndim != 2 or array.shape[0] != len(units) or array.shape[1] <= 0 or not np.isfinite(array).all():
        raise CandidateRetrievalError("Dense vector shape/count is invalid")
    norms = np.linalg.norm(array, axis=1)
    if np.any(norms == 0):
        raise CandidateRetrievalError("Dense vectors must be non-zero")
    array = array / norms[:, None]
    vector_file = BytesIO()
    np.save(vector_file, array.astype(np.float32), allow_pickle=False)
    vector_body = vector_file.getvalue()
    row_rows = [{"occurrence_index": index, "unit_id": str(unit["unit_id"])} for index, unit in enumerate(units)]
    row_body = _gzip_jsonl(row_rows)
    metadata = {
        "model_name": model_name,
        "model_revision": model_revision,
        "model_sha256": model_sha,
        "embedding_dimension": int(array.shape[1]),
        "dtype": "float32",
        "normalization": "L2",
        "instruction": instruction,
        "vectorization_schema_version": DENSE_INDEX_SCHEMA_VERSION,
        "row_mapping_policy": "W1 manifest order",
    }
    identity = _dense_metadata_identity({**metadata, "ru_build_identity": ru_manifest["build_identity"]})
    identity = _arm_identity({"ru_build_identity": ru_manifest["build_identity"], "dense": identity})
    output_root = Path(output_root)
    manifest = {
        "schema_version": DENSE_INDEX_SCHEMA_VERSION, "status": "complete", "arm": "dense",
        "arm_build_identity": identity, "retrieval_unit_build_identity": ru_manifest["build_identity"], **metadata,
        "row_count": len(units),
        "artifacts": {"vectors": _descriptor("artifacts/vectors.f32.npy", vector_body), "rows": _descriptor("artifacts/rows.jsonl.gz", row_body, len(row_rows))},
    }
    atomic_write(output_root / "artifacts" / "vectors.f32.npy", vector_body)
    atomic_write(output_root / "artifacts" / "rows.jsonl.gz", row_body)
    atomic_write(output_root / "metadata" / "manifest.json", canonical_json_bytes(manifest))
    return manifest


def _load_dense(path: Path) -> tuple[Mapping[str, Any], Any, list[Mapping[str, Any]]]:
    manifest = _mapping(json.loads(Path(path).read_text(encoding="utf-8")), "Dense manifest")
    if manifest.get("status") != "complete" or manifest.get("schema_version") != DENSE_INDEX_SCHEMA_VERSION:
        raise CandidateRetrievalError("Dense manifest is not complete")
    import numpy as np
    artifacts = _mapping(manifest.get("artifacts"), "Dense artifacts")
    vectors_path = Path(path).parent.parent / str(_mapping(artifacts.get("vectors"), "vectors descriptor")["path"])
    rows_path = Path(path).parent.parent / str(_mapping(artifacts.get("rows"), "rows descriptor")["path"])
    vectors_body, rows_body = vectors_path.read_bytes(), rows_path.read_bytes()
    if sha256(vectors_body).hexdigest() != _mapping(artifacts["vectors"], "vectors descriptor").get("sha256") or sha256(rows_body).hexdigest() != _mapping(artifacts["rows"], "rows descriptor").get("sha256"):
        raise CandidateRetrievalError("Dense artifact SHA-256 mismatch")
    vectors = np.load(BytesIO(vectors_body), allow_pickle=False)
    rows = _read_gzip_jsonl(rows_path)
    if vectors.ndim != 2 or vectors.dtype != np.dtype("float32"):
        raise CandidateRetrievalError("Dense vector dtype/shape contract is invalid")
    expected_dimension = manifest.get("embedding_dimension")
    if not isinstance(expected_dimension, int) or vectors.shape[1] != expected_dimension:
        raise CandidateRetrievalError("Dense vector dimension does not match manifest")
    if not np.isfinite(vectors).all():
        raise CandidateRetrievalError("Dense vectors contain non-finite values")
    norms = np.linalg.norm(vectors, axis=1)
    if np.any(norms == 0) or not np.allclose(norms, 1.0, rtol=0.0, atol=1e-5):
        raise CandidateRetrievalError("Dense vectors violate L2 normalization contract")
    if vectors.shape[0] != len(rows) or len(rows) != manifest.get("row_count"):
        raise CandidateRetrievalError("Dense row/vector accounting mismatch")
    seen: set[str] = set()
    for index, row in enumerate(rows):
        if row.get("occurrence_index") != index or not isinstance(row.get("unit_id"), str) or not row["unit_id"] or row["unit_id"] in seen:
            raise CandidateRetrievalError("Dense row mapping is invalid or non-deterministic")
        seen.add(row["unit_id"])
    return manifest, vectors, rows


def dense_candidates(dense_manifest_path: Path, query_vector: Any, *, top_k: int = 20, query_instruction: str | None = None) -> list[dict[str, Any]]:
    top_k = _positive_int(top_k, "top_k")
    manifest, vectors, rows = _load_dense(Path(dense_manifest_path))
    return _dense_candidates_from_loaded(
        manifest,
        vectors,
        rows,
        query_vector,
        top_k=top_k,
        query_instruction=query_instruction,
    )


def _dense_candidates_from_loaded(
    manifest: Mapping[str, Any],
    vectors: Any,
    rows: Sequence[Mapping[str, Any]],
    query_vector: Any,
    *,
    top_k: int,
    query_instruction: str | None,
) -> list[dict[str, Any]]:
    """Score one query against a previously validated Dense artifact."""

    import numpy as np
    query = np.asarray(query_vector, dtype=np.float32)
    if query.ndim != 1 or query.shape[0] != vectors.shape[1] or not np.isfinite(query).all():
        raise CandidateRetrievalError("Dense query vector dimension is invalid")
    norm = float(np.linalg.norm(query))
    if norm == 0:
        raise CandidateRetrievalError("Dense query vector must be non-zero")
    query = query / norm
    scores = vectors @ query
    ranked = sorted(((float(score), str(row["unit_id"])) for score, row in zip(scores, rows)), key=lambda item: (-item[0], item[1]))
    query_payload: dict[str, Any] = {"model_name": manifest.get("model_name"), "model_revision": manifest.get("model_revision"), "dimension": int(vectors.shape[1]), "normalization": "L2", "top_k": top_k}
    if query_instruction is not None:
        query_payload["instruction"] = query_instruction
    query_identity = sha256_json(query_payload)
    return [{"unit_id": uid, "rank": rank, "retrieval": {"mode": "dense", "score": score, "arm_build_identity": manifest["arm_build_identity"], "query_config_identity": query_identity}} for rank, (score, uid) in enumerate(ranked[:top_k], 1)]


def load_dense_query_model(model_dir: Path) -> Any:
    """Construct the pinned local CPU query encoder without download fallback."""
    try:
        from sentence_transformers import SentenceTransformer
        return SentenceTransformer(str(Path(model_dir)), local_files_only=True, device="cpu")
    except Exception as exc:
        raise CandidateRetrievalError("local Dense query model/runtime unavailable") from exc


def encode_dense_query(
    model_dir: Path,
    query: str,
    *,
    instruction: str = "为这个句子生成表示以用于检索相关文章：",
    model: Any | None = None,
) -> Any:
    """Encode one query with the pinned local model; never downloads or falls back."""
    try:
        import numpy as np
        encoder = load_dense_query_model(model_dir) if model is None else model
        value = encoder.encode([f"{instruction}{query}" if instruction else query], convert_to_numpy=True, normalize_embeddings=True, show_progress_bar=False)
        vector = np.asarray(value, dtype=np.float32)
        if vector.ndim != 2 or vector.shape[0] != 1 or not np.isfinite(vector).all():
            raise CandidateRetrievalError("Dense query encoding returned an invalid vector")
        return vector[0]
    except CandidateRetrievalError:
        raise
    except Exception as exc:
        raise CandidateRetrievalError("local Dense query model/runtime unavailable") from exc


def _validate_local_dense_binding(manifest: Mapping[str, Any], model_dir: Path, *, model_revision: str | None = None) -> None:
    """Verify the local model is the vector space recorded by a Dense artifact."""
    expected_sha = manifest.get("model_sha256")
    expected_revision = manifest.get("model_revision")
    if not isinstance(expected_sha, str) or not expected_sha or not isinstance(expected_revision, str) or not expected_revision:
        raise CandidateRetrievalError("Dense artifact lacks verifiable model binding")
    if model_revision is not None and model_revision != expected_revision:
        raise CandidateRetrievalError("Dense model revision does not match artifact")
    model_file = Path(model_dir) / "model.safetensors"
    try:
        actual_sha = sha256(model_file.read_bytes()).hexdigest()
    except OSError as exc:
        raise CandidateRetrievalError("Dense model weights are unavailable") from exc
    if actual_sha != expected_sha:
        raise CandidateRetrievalError("Dense model SHA-256 does not match artifact")
    if manifest.get("dtype") != "float32" or manifest.get("normalization") != "L2":
        raise CandidateRetrievalError("Dense artifact vector-space contract is unsupported")


def dense_candidates_local(
    dense_manifest_path: Path,
    model_dir: Path,
    query: str,
    *,
    top_k: int = 20,
    instruction: str = "为这个句子生成表示以用于检索相关文章：",
    model_revision: str | None = DEFAULT_DENSE_MODEL_REVISION,
) -> list[dict[str, Any]]:
    """Encode and retrieve with a locally pinned model bound to the artifact."""
    manifest, vectors, _ = _load_dense(Path(dense_manifest_path))
    _validate_local_dense_binding(manifest, Path(model_dir), model_revision=model_revision)
    vector = encode_dense_query(Path(model_dir), query, instruction=instruction)
    if int(vector.shape[0]) != int(vectors.shape[1]):
        raise CandidateRetrievalError("Dense query model dimension does not match artifact")
    return dense_candidates(dense_manifest_path, vector, top_k=top_k, query_instruction=instruction)


def hybrid_candidates(lexical: Sequence[Mapping[str, Any]], dense: Sequence[Mapping[str, Any]], *, lexical_build_identity: str, dense_build_identity: str, top_k: int = 20, rrf_k: int = 60) -> list[dict[str, Any]]:
    top_k, rrf_k = _positive_int(top_k, "top_k"), _positive_int(rrf_k, "rrf_k")
    components: dict[str, dict[str, Any]] = {}
    for mode, rows in (("lexical", lexical), ("dense", dense)):
        for row in rows:
            unit_id = row.get("unit_id")
            rank = row.get("rank")
            if not isinstance(unit_id, str) or not isinstance(rank, int) or rank <= 0:
                raise CandidateRetrievalError("invalid arm candidate row")
            item = components.setdefault(unit_id, {})
            retrieval = _mapping(row.get("retrieval", {}), "candidate retrieval")
            expected_identity = lexical_build_identity if mode == "lexical" else dense_build_identity
            if retrieval.get("arm_build_identity") != expected_identity:
                raise CandidateRetrievalError(f"{mode} candidate arm identity mismatch")
            item[mode] = {"rank": rank, "score": retrieval.get("score")}
    scored = []
    for unit_id, item in components.items():
        value = sum(1.0 / (rrf_k + int(item[mode]["rank"])) for mode in item)
        scored.append((value, unit_id, item))
    scored.sort(key=lambda x: (-x[0], x[1]))
    fusion_identity = sha256_json({"method": "rrf", "version": RRF_FUSION_VERSION, "rrf_k": rrf_k, "top_k": top_k})
    return [{"unit_id": uid, "rank": rank, "retrieval": {"mode": "hybrid", "score": value, "arm_build_identities": {"lexical": lexical_build_identity, "dense": dense_build_identity}, "fusion": {"method": "rrf", "version": RRF_FUSION_VERSION, "config_identity": fusion_identity}, "components": item}} for rank, (value, uid, item) in enumerate(scored[:top_k], 1)]


@dataclass(frozen=True)
class BatchCandidateRetriever:
    """One Measure batch's already-validated lexical and Dense index state."""

    lexical_manifest: Mapping[str, Any]
    lexical_rows: Sequence[Mapping[str, Any]]
    dense_manifest: Mapping[str, Any]
    dense_vectors: Any
    dense_rows: Sequence[Mapping[str, Any]]

    def candidates_for_query(
        self,
        query: str,
        query_vector: Any,
        *,
        instruction: str,
        top_k: int = 20,
        k1: float = 1.2,
        b: float = 0.75,
        rrf_k: int = 60,
    ) -> dict[str, list[dict[str, Any]]]:
        """Compute each arm once and fuse those exact rows with existing RRF."""

        top_k = _positive_int(top_k, "top_k")
        k1, b = _finite_float(k1, "k1"), _finite_float(b, "b")
        if k1 < 0 or not 0 <= b <= 1:
            raise CandidateRetrievalError("invalid BM25 parameters")
        lexical = _lexical_candidates_from_loaded(
            self.lexical_manifest,
            self.lexical_rows,
            query,
            top_k=top_k,
            k1=k1,
            b=b,
            analyzer=analyze,
        )
        dense = _dense_candidates_from_loaded(
            self.dense_manifest,
            self.dense_vectors,
            self.dense_rows,
            query_vector,
            top_k=top_k,
            query_instruction=instruction,
        )
        hybrid = hybrid_candidates(
            lexical,
            dense,
            lexical_build_identity=str(self.lexical_manifest["arm_build_identity"]),
            dense_build_identity=str(self.dense_manifest["arm_build_identity"]),
            top_k=top_k,
            rrf_k=rrf_k,
        )
        return {"lexical": lexical, "dense": dense, "hybrid": hybrid}


def load_batch_candidate_retriever(
    lexical_manifest_path: Path,
    dense_manifest_path: Path,
) -> BatchCandidateRetriever:
    """Load and fully validate both existing production indexes once per batch."""

    lexical_manifest, lexical_rows = _load_lexical(Path(lexical_manifest_path))
    dense_manifest, dense_vectors, dense_rows = _load_dense(Path(dense_manifest_path))
    return BatchCandidateRetriever(
        lexical_manifest=lexical_manifest,
        lexical_rows=lexical_rows,
        dense_manifest=dense_manifest,
        dense_vectors=dense_vectors,
        dense_rows=dense_rows,
    )


def retrieve_candidates(mode: str, *, lexical_manifest_path: Path | None = None, dense_manifest_path: Path | None = None, model_dir: Path | None = None, query: str = "", query_vector: Any | None = None, instruction: str = "为这个句子生成表示以用于检索相关文章：", top_k: int = 20, rrf_k: int = 60) -> list[dict[str, Any]]:
    if mode == "lexical":
        if lexical_manifest_path is None:
            raise CandidateRetrievalError("lexical manifest is required")
        return lexical_candidates(lexical_manifest_path, query, top_k=top_k)
    if mode == "dense":
        if dense_manifest_path is None:
            raise CandidateRetrievalError("Dense manifest is required")
        if model_dir is not None:
            if query_vector is not None:
                raise CandidateRetrievalError("precomputed Dense query vectors cannot be combined with a local model path")
            return dense_candidates_local(dense_manifest_path, model_dir, query, top_k=top_k, instruction=instruction)
        if query_vector is None:
            raise CandidateRetrievalError("Dense query vector or local model is required")
        return dense_candidates(dense_manifest_path, query_vector, top_k=top_k, query_instruction=instruction)
    if mode == "hybrid":
        if lexical_manifest_path is None or dense_manifest_path is None:
            raise CandidateRetrievalError("both arm manifests are required")
        lexical_rows = lexical_candidates(lexical_manifest_path, query, top_k=top_k)
        if model_dir is not None:
            if query_vector is not None:
                raise CandidateRetrievalError("precomputed Dense query vectors cannot be combined with a local model path")
            dense_rows = dense_candidates_local(dense_manifest_path, model_dir, query, top_k=top_k, instruction=instruction)
        elif query_vector is not None:
            dense_rows = dense_candidates(dense_manifest_path, query_vector, top_k=top_k, query_instruction=instruction)
        else:
            raise CandidateRetrievalError("Dense query vector or local model is required")
        lexical_manifest, _ = _load_lexical(Path(lexical_manifest_path))
        dense_manifest, _, _ = _load_dense(Path(dense_manifest_path))
        return hybrid_candidates(lexical_rows, dense_rows, lexical_build_identity=str(lexical_manifest["arm_build_identity"]), dense_build_identity=str(dense_manifest["arm_build_identity"]), top_k=top_k, rrf_k=rrf_k)
    raise CandidateRetrievalError(f"unknown retrieval mode: {mode}")
