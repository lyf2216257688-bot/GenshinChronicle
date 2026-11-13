from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from genshin_corpus.evidence_io import digest, read, seal, sha, write
from genshin_corpus.runtime_compatibility import index_space, VERSION


class CompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        self.old = {"python": "3.12.10", "packages": {"torch": "2.11.0+cu128"}, "model": "fixed"}
        self.new = {**self.old, "python": "3.14.5"}
        self.manifest = {"configuration": self.old, "space_id": digest(self.old)}

    def certificate(self):
        roots = {}
        for name in ("baseline", "candidate"):
            root = self.repo / name
            write(root / "witness.json", {"name": name})
            seal(root)
            roots[name] = {"path": str(root), "complete_sha256": sha(root / "complete.json")}
        report = {"version": VERSION, "status": "PASS", "checks": {k: True for k in
            ("tokens", "dense", "sparse", "rankings", "official_sparse_kernel")}, "code": {},
            "spaces": {"dense": {"source": digest(self.old), "target": digest(self.new),
                                    "manifest_digest": digest(self.manifest)}}, **roots}
        write(self.repo / "certificate/report.json", report)
        seal(self.repo / "certificate")
        return {"runtime_compatibility": "certificate"}

    def test_existing_space_needs_no_exception(self):
        self.assertEqual(index_space(self.repo, {}, self.manifest, self.old), digest(self.old))

    def test_different_runtime_needs_sealed_evidence(self):
        with self.assertRaises(ValueError): index_space(self.repo, {}, self.manifest, self.new)

    def test_validated_mapping_keeps_source_and_actual_id_distinct(self):
        config = self.certificate()
        with patch("genshin_corpus.runtime_compatibility.compatibility_code", return_value={}):
            self.assertEqual(index_space(self.repo, config, self.manifest, self.new), digest(self.old))
            self.assertNotEqual(digest(self.new), digest(self.old))

    def test_changed_witness_code_manifest_or_dependency_is_rejected(self):
        config = self.certificate()
        with patch("genshin_corpus.runtime_compatibility.compatibility_code", return_value={}):
            changed = {**self.manifest, "other": "different"}
            with self.assertRaises(ValueError): index_space(self.repo, config, changed, self.new)
            with self.assertRaises(ValueError): index_space(self.repo, config, self.manifest, {**self.new, "model": "other"})
            (self.repo / "baseline/witness.json").write_text("{}", encoding="utf-8")
            with self.assertRaises(ValueError): index_space(self.repo, config, self.manifest, self.new)
        with patch("genshin_corpus.runtime_compatibility.compatibility_code", return_value={"changed": "hash"}):
            with self.assertRaises(ValueError): index_space(self.repo, config, self.manifest, self.new)


if __name__ == "__main__": unittest.main()
