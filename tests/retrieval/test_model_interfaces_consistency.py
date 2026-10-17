from pathlib import Path
import sys
import unittest

# Ensure src root is on sys.path if invoked without PYTHONPATH set
_SRC_DIR = Path(__file__).resolve().parents[2] / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from genshin_corpus.rag.model_interfaces_catalog import (
    build_model_interfaces_catalog,
    check_model_interfaces_documentation,
    generate_markdown_summary,
)


class ModelInterfacesConsistencyTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parents[2]
        self.doc_path = self.root / "docs" / "model-interfaces.md"
        self.current_phase_path = self.root / "docs" / "current-phase.md"
        self.readme_path = self.root / "README.md"

    def test_catalog_builds_without_error_and_covers_all_categories(self):
        catalog = build_model_interfaces_catalog()
        self.assertTrue(len(catalog) >= 8)
        categories = {entry.category for entry in catalog}
        self.assertIn("phase04_production_qa", categories)
        self.assertIn("phase05_navi_e2", categories)
        self.assertIn("phase05_retired_semantic_sdk", categories)
        self.assertIn("deprecated_historical", categories)

    def test_e2_routes_in_catalog_match_navi_e2_routes_code(self):
        from genshin_corpus.rag.navi_e2_routes import E2_ROUTES
        catalog = build_model_interfaces_catalog()
        e2_entries = [e for e in catalog if e.category == "phase05_navi_e2"]
        catalog_pairs = {(e.logical_model, e.route_name) for e in e2_entries}
        self.assertEqual(catalog_pairs, set(E2_ROUTES.keys()))

    def test_model_interfaces_doc_is_consistent_with_code(self):
        errors = check_model_interfaces_documentation(self.doc_path)
        self.assertEqual(errors, [], f"Documentation consistency errors: {errors}")

    def test_current_phase_router_links_to_model_interfaces_doc(self):
        self.assertTrue(self.current_phase_path.exists())
        content = self.current_phase_path.read_text(encoding="utf-8")
        self.assertIn("model-interfaces.md", content,
                      "docs/current-phase.md Documentation Router must link to docs/model-interfaces.md")

    def test_readme_references_model_interfaces_doc(self):
        self.assertTrue(self.readme_path.exists())
        content = self.readme_path.read_text(encoding="utf-8")
        self.assertIn("model-interfaces.md", content,
                      "README.md must reference docs/model-interfaces.md")

    def test_markdown_summary_generates_valid_table_structure(self):
        summary = generate_markdown_summary()
        self.assertIn("Phase 04", summary)
        self.assertIn("Phase 05 Navi E2", summary)
        self.assertIn("gpt-6-luna", summary)
        self.assertIn("TOKENMETRO_API_KEY", summary)
        self.assertIn("qwen3.7-plus-2026-05-26", summary)
