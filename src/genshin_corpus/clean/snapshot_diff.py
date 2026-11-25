"""Compare complete Raw observations without modifying either archive."""

import argparse
from collections import Counter
from pathlib import Path

from ..collector.contract import SOURCE, has_legacy_coverage_gap, validate_response
from ..collector.run_store import create_json
from ..evidence_io import digest, require, sha
from .models import SourceNumber, pointer_get, source_json
from .pipeline import RawRun


VERSION = "gc-raw-snapshot-diff-1"
LEGACY_BASELINE_SHA = "b7b519c94fd5001cc3b7128ea59bd8117d9e3a88b7a26db71431349010f26978"
LEGACY_INVENTORY_SHA = "2ef3fac14b367ace730e01d62fb41b6786cc49b432e0fde2bc6d4489cf366d73"


def ordered_value(value):
    """Preserve consumed source types, object order and exact number tokens."""
    if isinstance(value, SourceNumber):
        return [value.source_type, str(value)]
    if isinstance(value, dict):
        return ["object", [[key, ordered_value(child)] for key, child in value.items()]]
    if isinstance(value, list):
        return ["array", [ordered_value(child) for child in value]]
    return [type(value).__name__, value]


def _location(artifact, pointer):
    return {"path": artifact["path"], "sha256": artifact["sha256"], "pointer": pointer}


class Snapshot:
    def __init__(self, path, *, allow_legacy=False):
        self.raw = RawRun(path)
        manifest = self.raw.manifest
        if not self.raw.versioned:
            require(allow_legacy and self.raw.manifest_sha256 == LEGACY_BASELINE_SHA,
                    "An unversioned Raw input must be the reviewed historical baseline")
            require(self.raw.inventory_artifact["sha256"] == LEGACY_INVENTORY_SHA, "Historical inventory hash changed")
            require(not has_legacy_coverage_gap(manifest, self.raw.inventory) and not manifest.get("failures"),
                    "The historical baseline has incomplete coverage")
        require(self.raw.items and set(self.raw.items) == set(self.raw.details), "Incomplete Raw inventory")
        self.values = {name: source_json(artifact["body"].decode("utf-8"))
                       for name, artifact in self.raw.discovery_artifacts.items()}
        self.topology = {key: value["parent_id"] for key, value in self.raw.channel_nodes.items()}
        require(self.topology, "No Raw channel coverage")
        scope = manifest["request_scope"]
        self.coverage = {"source": SOURCE, "locale": manifest["locale"], "app_sn": scope["app_sn"]}
        self.binding = {"run_id": manifest["run_id"], "manifest_sha256": self.raw.manifest_sha256,
                        "started_at": manifest["started_at"], "ended_at": manifest["ended_at"],
                        "coverage": self.coverage, "input_schema": manifest.get("schema_version", "reviewed-legacy")}
        self.request_shape = {k: v for k, v in scope.items() if k not in {"run_id", "output_root", "collection_root"}}

    def _observed(self, row):
        name, pointer = row["artifact_name"], row["pointer"]
        return pointer_get(self.values[name], pointer), _location(self.raw.discovery_artifacts[name], pointer)

    def observation(self, key):
        detail = self.raw.response("details:" + key)
        validate_response("details", key, detail["body"])
        discovery = self.raw.discovery(key)
        locators = {"detail": _location(detail, ""), "memberships": [], "listings": [], "channels": {}}
        memberships, listings, topology = [], [], {}
        for row in discovery["memberships"]:
            actual, locator = self._observed(row)
            require(str(actual) == row["channel_id"], "Membership locator identity changed")
            memberships.append(ordered_value(actual))
            locators["memberships"].append(locator)
        for row in discovery["listings"]:
            actual, locator = self._observed(row)
            require(str(actual["content_id"]) == key, "Listing locator identity changed")
            context = {field: row[field] for field in ("channel_id", "requested_channel_id", "discovery_kind")}
            listings.append({"context": context, "value": ordered_value(actual)})
            locators["listings"].append({**locator, **context})
        for channel, row in discovery["channel_nodes"].items():
            actual, locator = self._observed(row)
            require((str(actual["id"]), actual["name"], str(actual["parent_id"])) ==
                    (channel, row["name"], row["parent_id"]), "Channel locator identity changed")
            topology[channel] = [ordered_value(actual[field]) for field in ("id", "name", "parent_id")]
            locators["channels"][channel] = locator
        parts = {"detail": detail["sha256"], "memberships": digest(memberships),
                 "listings": digest(listings), "channel_structure": digest(topology)}
        source = [self.coverage["source"], self.coverage["locale"], key]
        return {"run_id": self.binding["run_id"], "manifest_sha256": self.binding["manifest_sha256"],
                "observation_id": digest([VERSION, source, self.binding["manifest_sha256"], locators]),
                "consumed_source_sha256": digest([VERSION, source, parts]), "parts": parts, "locators": locators}

    def verify_binding(self):
        require(sha(self.raw.root / "metadata/manifest.json") == self.raw.manifest_sha256,
                "Raw manifest changed while comparing V1 observations")
        for artifact in self.raw.discovery_artifacts.values():
            require(sha(self.raw.root / artifact["path"]) == artifact["sha256"],
                    f"Raw discovery changed while comparing: {artifact['path']}")


def compare_snapshots(previous_path, current_path):
    previous = Snapshot(previous_path, allow_legacy=True)
    current = Snapshot(current_path)
    require(previous.coverage == current.coverage, "Raw coverage is not comparable")
    if previous.raw.versioned:
        require(previous.request_shape == current.request_shape, "Raw request contracts differ")
    require(previous.binding["run_id"] != current.binding["run_id"], "Two distinct observation runs are required")
    old_ids, new_ids = set(previous.raw.items), set(current.raw.items)
    changed_channels = sorted(key for key in previous.topology.keys() | current.topology.keys()
                              if previous.topology.get(key) != current.topology.get(key))
    rows = []
    for key in sorted(old_ids | new_ids):
        before = previous.observation(key) if key in old_ids else None
        after = current.observation(key) if key in new_ids else None
        changes = ([part for part in before["parts"] if before["parts"][part] != after["parts"][part]]
                   if before is not None and after is not None else [])
        if before is None:
            status = "added"
        elif after is None:
            status = "unresolved" if changed_channels else "removed"
        else:
            status = "modified" if changes else "unchanged"
        rows.append({"source_item": {"source": SOURCE, "locale": current.coverage["locale"], "content_id": key},
                     "status": status, "changed_parts": changes, "previous_observation": before, "current_observation": after,
                     "locator_changed": before is not None and after is not None and before["locators"] != after["locators"]})
    previous.verify_binding()
    current.verify_binding()
    return {"version": VERSION, "previous": previous.binding, "current": current.binding,
            "status": "coverage_review_required" if changed_channels else "complete",
            "changed_channels": changed_channels, "counts": dict(Counter(row["status"] for row in rows)), "records": rows}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--previous", required=True, type=Path)
    parser.add_argument("--current", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    require(args.output.resolve() == args.output.absolute(), "Diff output must not use a filesystem alias")
    args.output = args.output.resolve()
    require(all(not args.output.is_relative_to(path.resolve()) for path in (args.previous, args.current)),
            "Diff output must be outside the Raw archives")
    require(not args.output.exists() and not args.output.is_symlink(), "Diff output already exists")
    result = compare_snapshots(args.previous, args.current)
    create_json(args.output, result)
    return 0 if result["status"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
