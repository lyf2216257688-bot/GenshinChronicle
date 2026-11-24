"""Shared OBC response and versioned Raw input contracts."""
import json
from pathlib import Path

from .config import DEFAULT_BASE_URL
from .storage import sha256


SOURCE = "mihoyo_obc"
SCHEMA = "gc-raw-2"
REQUEST_CONTRACT = "mihoyo-obc-phase-01-v2"
ENDPOINTS = {
    "map": "/common/blackboard/ys_obc/v1/home/map",
    "listings": "/common/blackboard/ys_obc/v1/home/content/list",
    "details": "/hoyowiki/genshin/wapi/entry_page",
}
DETAIL_HEADERS = {"x-rpc-wiki_app": "genshin"}
PAGINATION_KEYS = {
    "page", "page_size", "page_num", "total", "total_count", "has_more",
    "next", "next_cursor", "cursor", "offset",
}


class RawIntegrityError(ValueError):
    """The run cannot be safely opened under the requested contract."""


class ResponseError(ValueError):
    """The received bytes cannot be accepted as a successful observation."""


def json_bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False).encode("utf-8")


def object_at(path):
    try:
        value = json.loads(path.read_bytes())
    except (ValueError, OSError) as exc:
        raise RawIntegrityError(f"Cannot read Raw record {path}") from exc
    if not isinstance(value, dict):
        raise RawIntegrityError(f"Expected a Raw object at {path}")
    return value


def relative_path(root, value, *, prefix="responses"):
    if not isinstance(value, str) or "\\" in value or ":" in value:
        raise RawIntegrityError(f"Invalid run-relative Raw path {value!r}")
    parts = value.split("/")
    if len(parts) < 2 or parts[0] != prefix or any(part in ("", ".", "..") for part in parts):
        raise RawIntegrityError(f"Invalid run-relative Raw path {value!r}")
    root = Path(root).resolve()
    path = root.joinpath(*parts)
    if path.resolve() != path:
        raise RawIntegrityError(f"Raw path uses a filesystem alias: {value}")
    return path


def content_key(value):
    if type(value) is int:
        return True
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _pagination_signals(value, path, *, nodes=False):
    signals = [f"{path}.{key}" for key in value if key.lower() in PAGINATION_KEYS]
    if nodes:
        for field in ("list", "children"):
            children = value.get(field)
            if isinstance(children, list):
                for index, child in enumerate(children):
                    if isinstance(child, dict) and "id" in child and "content_id" not in child:
                        signals.extend(_pagination_signals(child, f"{path}.{field}[{index}]", nodes=True))
    return signals


def channel_nodes(nodes, *, is_map=False):
    """Walk verified channel containers. Preview rows remain in the Raw body."""
    if not isinstance(nodes, list):
        raise ResponseError("channel nodes must be an array")
    for node in nodes:
        if not isinstance(node, dict) or not content_key(node.get("id")):
            raise ResponseError("channel node identity missing or invalid")
        if is_map and (not isinstance(node.get("name"), str) or not content_key(node.get("parent_id"))):
            raise ResponseError("map channel name or parent identity missing or invalid")
        rows = node.get("list")
        if not isinstance(rows, list) or any(
            not isinstance(row, dict) or not content_key(row.get("content_id")) for row in rows
        ):
            raise ResponseError("listing rows invalid or missing content_id")
        yield node
        yield from channel_nodes(node.get("children"), is_map=is_map)


def validate_response(kind, key, body):
    try:
        payload = json.loads(body.decode("utf-8"))
    except (ValueError, RecursionError) as exc:
        raise ResponseError("response is not valid UTF-8 JSON") from exc
    if not isinstance(payload, dict) or type(payload.get("retcode")) is not int:
        raise ResponseError("response lacks an integer retcode")
    if payload["retcode"] != 0:
        raise ResponseError(f"API retcode {payload['retcode']}")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise ResponseError("response data must be an object")
    if kind == "details":
        page = data.get("page")
        if not isinstance(page, dict) or not content_key(page.get("id")) or str(page["id"]) != key:
            raise ResponseError("detail page.id does not match requested content_id")
        return data
    nodes = data.get("list")
    try:
        if kind == "listings":
            signals = _pagination_signals(payload, "root") + _pagination_signals(data, "root.data", nodes=True)
            if signals:
                raise ResponseError(f"unhandled listing pagination signals: {', '.join(signals)}")
        ids = [str(node["id"]) for node in channel_nodes(nodes, is_map=kind == "map")]
    except RecursionError as exc:
        raise ResponseError("channel tree exceeds supported nesting") from exc
    if kind == "map" and (not ids or len(ids) != len(set(ids))):
        raise ResponseError("map has no channel nodes or repeats channel identity")
    if kind == "listings" and (not ids or len(nodes) != 1 or ids[0] != key):
        raise ResponseError("listing root does not match requested channel_id")
    return data


def full_request(scope):
    return (
        scope.get("request_contract") == REQUEST_CONTRACT
        and scope.get("base_url") == DEFAULT_BASE_URL
        and scope.get("app_sn") == "ys_obc"
        and scope.get("listing_param_names") == []
        and scope.get("listing_params_sha256") == sha256(json_bytes({}))
        and "channel_ids" in scope and scope["channel_ids"] is None
        and scope.get("fetch_details") is True
        and "detail_limit" in scope and scope["detail_limit"] is None
    )


def is_versioned(manifest):
    if "schema_version" not in manifest:
        return False
    if manifest["schema_version"] != SCHEMA:
        raise RawIntegrityError("Unsupported Raw manifest schema")
    return True


def _checked_discovery(root, kind, key, entry):
    body = relative_path(root, entry["raw"]).read_bytes()
    if sha256(body) != entry["sha256"]:
        raise RawIntegrityError(f"Raw discovery binding is invalid for {kind}:{key}")
    try:
        return validate_response(kind, key, body)
    except ResponseError as exc:
        raise RawIntegrityError(f"Raw discovery fails business validation for {kind}:{key}: {exc}") from exc


def check_full_input(root, manifest, inventory_body):
    """Validate coverage and the promoted path set before derived output is created."""
    scope = manifest.get("request_scope")
    if (manifest.get("status") != "complete" or manifest.get("full_coverage") is not True
            or not isinstance(scope, dict) or not full_request(scope)
            or manifest.get("run_identity_sha256") != sha256(json_bytes(scope))
            or any(scope.get(field) != manifest.get(field) for field in ("source_system", "locale", "run_id"))):
        raise RawIntegrityError("Raw run does not qualify as a complete OBC input")
    if sha256(inventory_body) != manifest.get("inventory_sha256"):
        raise RawIntegrityError("Raw inventory checksum mismatch")
    try:
        rows = json.loads(inventory_body)
        inventory = {row["content_id"]: row["channels"] for row in rows}
        details = {row["content_id"]: row for row in manifest["details"]}
        channels = {row["channel_id"]: row for row in manifest["channels"]}
        coverage = manifest["coverage"]
        selection = manifest["detail_selection"]
        paths = manifest["paths"]
        if (not inventory or len(rows) != len(inventory) or len(details) != len(manifest["details"])
                or not channels or len(channels) != len(manifest["channels"])
                or set(details) != set(inventory) or list(inventory) != selection["selected_content_ids"]
                or selection["limit"] is not None
                or coverage["discovery_complete"] is not True or coverage["missing_channel_ids"]
                or set(coverage["target_channel_ids"]) != set(channels)
                or coverage["target_channel_ids"] != coverage["recognized_channel_ids"]
                or manifest["failures"] or manifest["failed_detail_fetches"] != 0
                or len(inventory) != manifest["inventory_count"]
                or len(inventory) != manifest["unique_detail_responses_expected"]
                or len(details) != manifest["successful_detail_fetches"]
                or len(channels) != manifest["recognized_channel_count"]
                or len(channels) != manifest["listing_responses_saved"]):
            raise RawIntegrityError("Raw full coverage is inconsistent")
        for channel in channels.values():
            if channel["status"] != "completed" or channel["pagination"] != "single_response_verified":
                raise RawIntegrityError("Raw listing coverage is incomplete")
        for content_id, detail in details.items():
            memberships = inventory[content_id]
            if (detail["status"] != "completed" or detail["channels"] != memberships
                    or not isinstance(memberships, list) or not memberships
                    or len(set(memberships)) != len(memberships) or not set(memberships) <= set(channels)):
                raise RawIntegrityError("Raw detail coverage is inconsistent")
        expected = {"map:home-map"} | {f"listings:{key}" for key in channels} | {f"details:{key}" for key in details}
        if set(paths) != expected:
            raise RawIntegrityError("Raw promoted response set does not match coverage")
        seen = set()
        for entry_key, entry in paths.items():
            raw_path, receipt_path = (relative_path(root, entry[field]) for field in ("raw", "metadata"))
            for path in (raw_path, receipt_path):
                if path in seen or not path.is_file():
                    raise RawIntegrityError(f"Raw promoted path missing or duplicated: {path}")
                seen.add(path)
            checksum = entry["sha256"]
            if not isinstance(checksum, str) or len(checksum) != 64 or any(c not in "0123456789abcdef" for c in checksum):
                raise RawIntegrityError("Raw promoted response lacks a valid checksum")
            kind, key = entry_key.split(":", 1)
            receipt = object_at(receipt_path)
            if (receipt.get("kind") != kind or receipt.get("key") != key
                    or receipt.get("ok") is not True or receipt.get("status") != 200
                    or receipt.get("raw_path") != entry["raw"] or receipt.get("sha256") != checksum
                    or receipt.get("run_identity_sha256") != manifest["run_identity_sha256"]):
                raise RawIntegrityError(f"Raw promoted receipt does not match the run for {entry_key}")
        map_data = _checked_discovery(root, "map", "home-map", paths["map:home-map"])
        map_ids = [str(node["id"]) for node in channel_nodes(map_data["list"], is_map=True)]
        if map_ids != coverage["recognized_channel_ids"]:
            raise RawIntegrityError("Raw coverage does not match the observed channel map")
        observed, listing_rows, duplicate_memberships = {}, 0, 0
        for key in map_ids:
            listing = _checked_discovery(root, "listings", key, paths[f"listings:{key}"])
            rows = [row for node in channel_nodes(listing["list"]) for row in node["list"]]
            if len(rows) != channels[key]["listing_record_count"]:
                raise RawIntegrityError(f"Raw listing record count differs for channel {key}")
            listing_rows += len(rows)
            for row in rows:
                memberships = observed.setdefault(str(row["content_id"]), [])
                if key not in memberships:
                    duplicate_memberships += bool(memberships)
                    memberships.append(key)
        if (list(observed.items()) != list(inventory.items())
                or listing_rows != manifest["listing_records_discovered"]
                or duplicate_memberships != manifest["cross_channel_duplicate_memberships"]):
            raise RawIntegrityError("Raw inventory does not match the observed channel listings")
    except RawIntegrityError:
        raise
    except (AttributeError, KeyError, TypeError, ValueError, OSError) as exc:
        raise RawIntegrityError("Raw full coverage fields are invalid") from exc


def has_legacy_coverage_gap(manifest, inventory=None):
    """Only reject gaps provable from the unversioned record by itself."""
    selection = manifest.get("detail_selection")
    if isinstance(selection, dict) and selection.get("limit") is not None:
        return True
    for expected, completed in (
        ("recognized_channel_count", "listing_responses_saved"),
        ("unique_detail_responses_expected", "successful_detail_fetches"),
        ("inventory_count", "successful_detail_fetches"),
    ):
        if expected in manifest and completed in manifest and manifest[expected] != manifest[completed]:
            return True
    if manifest.get("inventory_count") == 0 or not manifest.get("details"):
        return True
    if inventory is None:
        return False
    details = {str(row["content_id"]) for row in manifest["details"] if row.get("status") == "completed"}
    return not inventory or {str(row["content_id"]) for row in inventory} != details
