from __future__ import annotations

import hashlib
import unittest

from genshin_corpus.canonical.fingerprints import canonical_json_bytes
from genshin_corpus.retrieval.w7_sanitizer import (
    FAMILY_KEY_SCHEMA_VERSION,
    NORMALIZATION_VERSION,
    OCCURRENCE_KEY_SCHEMA_VERSION,
    _key,
    _normalise,
    _overlap_entry,
)


class W7SanitizerFocusedTests(unittest.TestCase):
    def test_c1_normalization_is_nfkc_whitespace_trim_and_casefold(self) -> None:
        self.assertEqual(_normalise("  Ａ\u2003B\n  "), "a b")

    def test_overlap_hashes_use_utf8_and_sorted_unique_windows_and_grams(self) -> None:
        query = "Abcdefghi"
        entry = _overlap_entry("legacy-v03-0001", query)
        normalized = "abcdefghi"
        expected_windows = sorted(
            hashlib.sha256(window.encode("utf-8")).hexdigest()
            for window in (normalized[index : index + 8] for index in range(2))
        )
        expected_grams = sorted(
            {hashlib.sha256(gram.encode("utf-8")).hexdigest() for gram in (normalized[index : index + 3] for index in range(7))}
        )
        self.assertEqual(entry["normalized_query_sha256"], hashlib.sha256(normalized.encode("utf-8")).hexdigest())
        self.assertEqual(entry["normalized_continuous_8char_window_sha256"], expected_windows)
        self.assertEqual(entry["normalized_unique_char_3gram_sha256"], expected_grams)
        self.assertEqual(entry["opaque_legacy_id"], "legacy-v03-0001")

    def test_structural_keys_hash_exact_canonical_json_address(self) -> None:
        address = {
            "record_id": "r",
            "section_ordinal": 1,
            "component_observation_key": "c",
            "unit_ordinal": 2,
            "lineage": {"parsed_json_pointer": "/x", "raw_refs": []},
        }
        expected = hashlib.sha256(canonical_json_bytes({"schema": OCCURRENCE_KEY_SCHEMA_VERSION, "address": address})).hexdigest()
        self.assertEqual(_key(OCCURRENCE_KEY_SCHEMA_VERSION, address), expected)
        self.assertNotEqual(_key(OCCURRENCE_KEY_SCHEMA_VERSION, address), _key(FAMILY_KEY_SCHEMA_VERSION, address))
        self.assertEqual(NORMALIZATION_VERSION, "c1-nfkc-whitespace-collapse-trim-casefold-v1")


if __name__ == "__main__":
    unittest.main()
