"""Offline contracts over a small checked-in source fixture."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import socket
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from genshin_corpus.clean import CleanRef, clean_obc_detail, resolve_clean_ref
from genshin_corpus.chunking import ChunkProfile, adapt_record, chunk_parent
from genshin_corpus.evidence_io import digest, read, sha, write
from genshin_corpus.indexing import (
    ANALYZER_VERSION, LEXICAL_FIELDS, ChunkInputSource, analyze_fields,
    iter_inputs, prepare_inputs, representation_id,
)
from genshin_corpus.indexing.schema import project_chunk
from genshin_corpus.indexing.validation import validate_readback


FIXTURE = Path(__file__).parent / "fixtures" / "source.json"


def build_source(root, *, long_story=False):
    fixture = read(FIXTURE)
    if long_story:
        fixture["records"][0]["modules"][0]["components"][0]["data"]["rich_text"] += "<p>完整长句。</p>" * 80
    clean_root, chunk_root = root / "clean", root / "chunks"
    entries, parents, chunks, graphs = [], [], [], []
    for page in fixture["records"]:
        for module in page["modules"]:
            for component in module["components"]:
                component["data"] = json.dumps(component["data"], ensure_ascii=False)
        body = json.dumps({"retcode": 0, "data": {"page": page}}, ensure_ascii=False).encode("utf-8")
        record = clean_obc_detail(body, artifact_path="fixture.json", artifact_sha256=sha256(body).hexdigest(),
                                  locale=fixture["locale"], raw_run_id="fixture-run", content_id=page["id"])
        path = "records/" + record["observation_id"] + ".json"
        write(clean_root / path, record)
        entries.append({"content_id": page["id"], "observation_id": record["observation_id"],
                        "record_path": path, "record_sha256": sha(clean_root / path), "status": "complete"})
        adapted = adapt_record(record)
        parents.extend(adapted["parents"])
        graphs.extend(adapted["graphs"])
        for parent in adapted["parents"]:
            chunks.extend(chunk_parent(parent, len, ChunkProfile("fixture", 60, 100, 180, 250)))
    clean_manifest = clean_root / "metadata" / "manifest.json"
    write(clean_manifest, {"schema_version": "gc-clean-1", "status": "complete", "source": fixture["source"],
                           "locale": fixture["locale"], "records": entries})
    chunk_root.mkdir()
    for name, values in (("parents.jsonl", parents), ("graphs.jsonl", graphs), ("chunks-balanced.jsonl", chunks)):
        with (chunk_root / name).open("w", encoding="utf-8", newline="\n") as handle:
            for value in values:
                handle.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")
    write(chunk_root / "report.json", {"status": "complete", "record_count": len(entries),
          "policy_version": "gc-story-admission-pilot-4", "parents": len(parents), "graphs": len(graphs),
          "profiles": {"balanced": {"chunks": len(chunks)}}})
    write(chunk_root / "manifest.json", {"status": "complete", "schema_version": "gc-chunking-pilot-2",
          "clean_manifest": str(clean_manifest), "clean_manifest_sha256": sha(clean_manifest),
          "selection": {"content_ids": [entry["content_id"] for entry in entries]}, "sources": entries,
          "artifacts": {name: sha(chunk_root / name) for name in
                        ("parents.jsonl", "graphs.jsonl", "chunks-balanced.jsonl", "report.json")}})
    return chunk_root / "manifest.json"


def dense_config():
    return {"model": "fixture-model", "revision": "pinned-revision", "tokenizer_sha256": "a" * 64,
            "dimensions": 8, "pooling": "mean", "normalization": "l2", "document_prefix": "",
            "query_prefix": "query instruction", "max_tokens": 2048}


class IndexInputTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        # Every test, including source readback, must be network independent.
        network = patch.object(socket, "socket", side_effect=AssertionError("network forbidden"))
        network.start()
        self.addCleanup(network.stop)

    def source(self, **kwargs):
        return ChunkInputSource(build_source(self.root, **kwargs))

    def test_shared_identity_dense_equality_and_unicode_readback(self):
        source = self.source()
        rows = list(source.iter_documents())
        self.assertTrue(any(row["granularities"] == ["micro", "macro"] for row in rows))
        for row in rows:
            chunk = source.read_chunk(row)
            self.assertEqual(chunk["retrieval_text"], row["dense_text"])
            self.assertEqual(chunk["context"], row["source_context"])
            self.assertEqual(row["source_item"]["locale"], "fixture-locale")
            record = source.read_clean(row)
            for part in chunk["parts"]:
                self.assertEqual(resolve_clean_ref(CleanRef(**part["clean_ref"]), record=record)["text"], part["text"])
            self.assertIsNotNone(source.read_natural_parent(row))
        self.assertEqual(len({row["chunk_id"] for row in rows}), len(rows))

    def test_macro_order_need_not_follow_physical_row_order(self):
        source = self.source(long_story=True)
        rows = list(source.iter_documents())
        self.assertTrue(any(row["granularities"] == ["micro"] for row in rows))
        self.assertTrue(any(row["granularities"] == ["macro"] for row in rows))
        # Reverse the file to exercise the actual source contract independently of row order.
        path = source.root / "chunks-balanced.jsonl"
        path.write_bytes(b"".join(reversed(path.read_bytes().splitlines(keepends=True))))
        manifest = read(source.manifest_path)
        manifest["artifacts"][path.name] = sha(path)
        source.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        reordered = list(ChunkInputSource(source.manifest_path).iter_documents())
        self.assertEqual([row["chunk_id"] for row in reordered], list(reversed([row["chunk_id"] for row in rows])))

    def test_conditions_graph_and_original_context_are_preserved(self):
        source = self.source()
        row = next(row for row in source.iter_documents() if row["conditions"])
        chunk = source.read_chunk(row)
        self.assertEqual(row["conditions"][0]["sources"], chunk["parts"][0]["condition_sources"])
        self.assertIn("白天", row["lexical_fields"]["conditions"])
        self.assertEqual(source.read_graph(row)["parent_id"], row["parent_id"])
        parent = source.read_parent(row)
        mutated = deepcopy(chunk)
        mutated["parts"][0]["condition_status"] = "unresolved"
        projected = project_chunk(mutated, parent)
        self.assertEqual(projected["conditions"][0]["status"], "unresolved")
        self.assertEqual(projected["dense_text"], chunk["retrieval_text"])

    def test_unknown_provenance_and_empty_document_stay_explicit(self):
        source = self.source()
        rows = list(source.iter_documents())
        self.assertEqual({row["source_context"]["source_provenance"]["state"] for row in rows}, {"unknown"})
        self.assertEqual(list(source.iter_documents(content_ids=["empty"])), [])
        with self.assertRaisesRegex(ValueError, "selection"):
            list(source.iter_documents(content_ids=["missing"]))

    def test_same_text_different_occurrences_are_not_collapsed(self):
        source = self.source()
        row = next(row for row in source.iter_documents() if row["source_item"]["content_id"] == "story"
                   and row["lexical_fields"]["body"][0].count("相同原句") == 2)
        chunk = source.read_chunk(row)
        repeated = [part for part in chunk["parts"] if "相同原句" in part["text"]]
        self.assertEqual(len(repeated), 2)
        self.assertNotEqual(repeated[0]["clean_ref"], repeated[1]["clean_ref"])

    def test_create_only_deterministic_bundles_and_route_readers(self):
        source = self.source()
        first = prepare_inputs(source, self.root / "first")
        second = prepare_inputs(source, self.root / "second")
        self.assertEqual(first, second)
        self.assertNotIn("projection_code_sha256", first["binding"])
        self.assertEqual(first["projection_code_sha256"], source.projection_code_sha256)
        rows = list(iter_inputs(self.root / "first" / "manifest.json"))
        micro = list(iter_inputs(self.root / "first" / "manifest.json", granularity="micro"))
        macro = list(iter_inputs(self.root / "first" / "manifest.json", granularity="macro"))
        self.assertEqual(rows, micro)
        self.assertEqual(rows, macro)
        with self.assertRaises(FileExistsError):
            prepare_inputs(source, self.root / "first")
        subset = prepare_inputs(source, self.root / "subset", content_ids=["story"])
        self.assertEqual(first["input_snapshot_id"], subset["input_snapshot_id"])
        self.assertNotEqual(first["input_bundle_id"], subset["input_bundle_id"])

    def test_failure_never_publishes_complete_manifest(self):
        source = self.source()
        with patch.object(source, "verify_unchanged", side_effect=ValueError("source changed")):
            with self.assertRaises(ValueError):
                prepare_inputs(source, self.root / "failed")
        self.assertFalse((self.root / "failed" / "manifest.json").exists())
        with self.assertRaises(FileNotFoundError):
            list(iter_inputs(self.root / "failed" / "manifest.json"))

    def test_prepared_bundle_source_readback(self):
        source = self.source()
        prepare_inputs(source, self.root / "bundle")
        result = validate_readback(source, self.root / "bundle" / "manifest.json")
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["clean_records_read"], 2)
        self.assertEqual(result["original_graphs_checked"], 1)
        self.assertGreater(result["counts"]["body_refs"], 0)

    def test_corrupt_bundle_or_source_fails_closed(self):
        source = self.source()
        prepare_inputs(source, self.root / "bundle")
        with (self.root / "bundle" / "inputs.jsonl").open("ab") as handle:
            handle.write(b"{}\n")
        with self.assertRaisesRegex(ValueError, "checksum"):
            list(iter_inputs(self.root / "bundle" / "manifest.json"))
        with (source.root / "parents.jsonl").open("ab") as handle:
            handle.write(b" ")
        with self.assertRaises(ValueError):
            ChunkInputSource(source.manifest_path)

    def test_partial_manifest_and_clean_binding_mismatch_are_rejected(self):
        path = build_source(self.root)
        manifest = read(path)
        manifest["status"] = "partial"
        path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "complete"):
            ChunkInputSource(path)
        manifest["status"] = "complete"
        manifest["clean_manifest_sha256"] = "b" * 64
        path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "checksum"):
            ChunkInputSource(path)

    def test_readback_rejects_foreign_locator_or_modified_clean(self):
        source = self.source()
        row = next(source.iter_documents())
        other = deepcopy(row)
        other["input_snapshot_id"] = "foreign"
        with self.assertRaisesRegex(ValueError, "snapshot"):
            source.read_chunk(other)
        row["parent_locator"]["offset"] += 1
        with self.assertRaises(ValueError):
            source.read_parent(row)
        clean_path = source.clean_root / row["clean_record"]["record_path"]
        clean_path.write_bytes(clean_path.read_bytes() + b" ")
        with self.assertRaisesRegex(ValueError, "checksum"):
            source.read_clean(row)

    def test_resealed_route_mismatch_is_rejected(self):
        source = self.source()
        output = self.root / "bundle"
        manifest = prepare_inputs(source, output)
        path = output / "micro.jsonl"
        lines = path.read_text(encoding="utf-8").splitlines()
        route = json.loads(lines[0])
        route["row_number"] = 99
        lines[0] = json.dumps(route)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        manifest["artifacts"][path.name] = sha(path)
        manifest["input_bundle_id"] = digest({key: manifest[key] for key in
                                             ("input_snapshot_id", "selection", "counts", "artifacts")})
        (output / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Route mapping"):
            list(iter_inputs(output / "manifest.json"))

    def test_duplicate_identity_and_dangling_overlap_fail_with_matching_hashes(self):
        source = self.source()
        path = source.root / "chunks-balanced.jsonl"
        lines = path.read_bytes().splitlines(keepends=True)
        for changed, message in ((lines + lines[:1], "Duplicate Chunk"),
                                 ([json.dumps({**json.loads(lines[0]), "overlapping_chunk_ids": ["missing"]},
                                              ensure_ascii=False).encode("utf-8") + b"\n"] + lines[1:], "overlap target")):
            path.write_bytes(b"".join(changed))
            manifest = read(source.manifest_path)
            manifest["artifacts"][path.name] = sha(path)
            source.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                list(ChunkInputSource(source.manifest_path).iter_documents())

    def test_projection_rejects_unknown_critical_structure(self):
        source = self.source()
        row = next(source.iter_documents())
        chunk, parent = source.read_chunk(row), source.read_parent(row)
        for key, value in (("schema_version", "unknown"), ("granularities", ["future"]),
                           ("order_semantics", "timeline"), ("body_text", "rewritten")):
            modified = {**chunk, key: value}
            with self.subTest(key=key), self.assertRaises(ValueError):
                project_chunk(modified, parent)

    def test_independent_representation_bindings(self):
        config = dense_config()
        base = representation_id("bundle", "micro", "dense", config)
        self.assertEqual(base, representation_id("bundle", "micro", "dense", deepcopy(config)))
        for key, value in (("model", "other"), ("revision", "next"), ("query_prefix", "changed"),
                           ("tokenizer_sha256", "b" * 64), ("dimensions", 16), ("normalization", "none")):
            self.assertNotEqual(base, representation_id("bundle", "micro", "dense", {**config, key: value}))
        self.assertNotEqual(base, representation_id("other-bundle", "micro", "dense", config))
        self.assertNotEqual(base, representation_id("bundle", "macro", "dense", config))
        with self.assertRaisesRegex(ValueError, "Incomplete"):
            representation_id("bundle", "micro", "dense", {"dimensions": 8})


class AnalyzerTests(unittest.TestCase):
    def fields(self, values):
        return {field: values if field == "body" else [] for field in LEXICAL_FIELDS}

    def test_multivalue_boundaries_and_non_han_words(self):
        config = {"version": ANALYZER_VERSION, "names": ["han_unigram", "han_bigram"]}
        terms = analyze_fields(self.fields(["雷电将军，须弥 Qwen3😀", "璃月"]), config)["body"]
        self.assertEqual(terms["han_unigram"], ["雷", "电", "将", "军", "须", "弥", "qwen3", "璃", "月"])
        self.assertEqual(terms["han_bigram"], ["雷电", "电将", "将军", "须弥", "qwen3", "璃月"])

    def test_word_segmenter_requires_explicit_binding_and_implementation(self):
        config = {"version": ANALYZER_VERSION, "names": ["word"]}
        with self.assertRaisesRegex(ValueError, "bindings"):
            analyze_fields(self.fields(["雷电将军"]), config)
        config["segmenter"] = {"implementation": "fixture", "version": "1", "configuration_sha256": "a" * 64,
                               "dictionary_sha256": "b" * 64}
        with self.assertRaisesRegex(ValueError, "explicit segmenter"):
            analyze_fields(self.fields(["雷电将军"]), config)
        terms = analyze_fields(self.fields(["雷电将军"]), config, segmenter=lambda text: [text])
        self.assertEqual(terms["body"]["word"], ["雷电将军"])
        lexical = {"field_weights": {"body": 1.0}, "field_b": {"body": 0.75}, "k1": 1.2, "analyzers": config}
        first = representation_id("bundle", "micro", "bm25f", lexical)
        changed = deepcopy(lexical)
        changed["analyzers"]["segmenter"]["dictionary_sha256"] = "c" * 64
        self.assertNotEqual(first, representation_id("bundle", "micro", "bm25f", changed))


if __name__ == "__main__":
    unittest.main()
