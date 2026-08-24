import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .config import CollectorConfig
from .storage import RunStore, write_json
from .transport import HttpTransport, RequestError, Response


_PAGINATION_SIGNAL_KEYS = {
    "page",
    "page_size",
    "page_num",
    "total",
    "total_count",
    "has_more",
    "next",
    "next_cursor",
    "cursor",
    "offset",
}

_LISTING_CONTAINER_HINT_KEYS = {
    "id",
    "channel_id",
    "parent_id",
    "depth",
    "children",
    "layout",
    "entry_limit",
    "hidden",
    "shortcut",
    "name",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Collector:
    def __init__(self, config: CollectorConfig, transport=None):
        self.config = config
        self.transport = transport or HttpTransport(timeout=config.timeout, max_retries=config.max_retries,
                                                    backoff_base=config.backoff_base)
        self.run_id = config.resolved_run_id()
        self.store = RunStore(config.output_root, "mihoyo_obc", config.locale, self.run_id)
        self.inventory: dict[str, dict[str, Any]] = {}
        self._listing_contract_blocked = False
        self.manifest: dict[str, Any] = self._blank_manifest()
        self._checkpoint()

    def _blank_manifest(self) -> dict[str, Any]:
        return {
            "source_system": "mihoyo_obc",
            "locale": self.config.locale,
            "run_id": self.run_id,
            "status": "partial",
            "started_at": _now(),
            "ended_at": None,
            "request_scope": {"app_sn": self.config.app_sn, "locale": self.config.locale},
            "channels": [],
            "recognized_channel_count": 0,
            "details": [],
            "detail_selection": {"limit": None, "strategy": "inventory_order", "selected_content_ids": []},
            "failures": [],
            "listing_responses_saved": 0,
            "listing_records_discovered": 0,
            "unique_detail_responses_expected": 0,
            "successful_detail_fetches": 0,
            "failed_detail_fetches": 0,
            "cross_channel_duplicate_memberships": 0,
            "unknown_unhandled_structures": [],
            "retry_summary": {"attempts": 0, "retryable_failures": 0, "retry_after_seen": 0},
            "paths": {},
        }

    def _checkpoint(self) -> None:
        write_json(self.store.manifest_path, self.manifest)

    @staticmethod
    def _json(body: bytes) -> Any:
        return json.loads(body.decode("utf-8"))

    @staticmethod
    def _sanity_json(body: bytes) -> bool:
        try:
            json.loads(body.decode("utf-8"))
            return True
        except Exception:
            return False

    def _record_failure(self, kind: str, key: str, url: str, status: int | None, error: str, *, context=None) -> None:
        failure = {"kind": kind, "key": key, "url": url, "status": status, "error": error, "recorded_at": _now()}
        if context is not None:
            failure["context"] = context
        self.store.failure(failure)
        self.manifest["failures"].append(failure)
        self.manifest["status"] = "partial"
        self._checkpoint()

    def _get(self, kind: str, key: str, endpoint: str, params: Mapping[str, str], *, headers=(), context=None) -> bytes | None:
        record = self.store.valid_response(kind, key, self._sanity_json)
        if record is not None:
            self.manifest["paths"][f"{kind}:{key}"] = {"raw": record["raw_path"], "metadata": record["metadata_path"], "sha256": record["sha256"]}
            return Path(record["raw_path"]).read_bytes()
        url = self.config.base_url.rstrip("/") + endpoint
        try:
            response: Response = self.transport.get(url, params=params, headers=headers)
            self.manifest["retry_summary"]["attempts"] += 1
            if response.status != 200:
                raise RequestError(f"HTTP {response.status}", status=response.status)
            record = self.store.write_response(kind, key, response.body, {"url": response.url, "status": response.status, "context": context})
            self.manifest["paths"][f"{kind}:{key}"] = {"raw": record["raw_path"], "metadata": record["metadata_path"], "sha256": record["sha256"]}
            self._checkpoint()
            return response.body
        except RequestError as exc:
            if exc.retryable:
                self.manifest["retry_summary"]["retryable_failures"] += 1
            if exc.status == 429:
                self.manifest["retry_summary"]["retry_after_seen"] += 1
            self._record_failure(kind, key, url, exc.status, str(exc), context=context)
            return None

    def discover_map(self) -> Any | None:
        body = self._get("map", "home-map", "/common/blackboard/ys_obc/v1/home/map", {"app_sn": self.config.app_sn})
        return self._json(body) if body else None

    @staticmethod
    def _channel_identity(value: dict[str, Any]):
        if "channel_id" in value:
            return value["channel_id"]
        if "id" in value and any(key in value for key in ("parent_id", "depth", "children", "list")):
            return value["id"]
        return None

    def _nodes(self, value: Any):
        if isinstance(value, dict):
            if self._channel_identity(value) is not None:
                yield value
            for child in value.values():
                yield from self._nodes(child)
        elif isinstance(value, list):
            for child in value:
                yield from self._nodes(child)

    def _record_unknown(self, context: dict[str, Any], note: str) -> None:
        self.store.failure({
            "kind": "listing_item_anomaly",
            "key": str(context.get("channel_id", "unknown")),
            "url": "",
            "status": None,
            "error": note,
            "context": context,
            "recorded_at": _now(),
        })
        self.manifest["unknown_unhandled_structures"].append({"context": context, "note": note, "recorded_at": _now()})
        self.manifest["failures"].append({
            "kind": "listing_item_anomaly",
            "key": str(context.get("channel_id", "unknown")),
            "url": "",
            "status": None,
            "error": note,
            "context": context,
            "recorded_at": _now(),
        })
        self.manifest["status"] = "partial"
        self._checkpoint()

    @staticmethod
    def _listing_pagination_signals(value: Any) -> list[str]:
        """Inspect only the response envelope and recognized listing containers."""
        signals = []

        def inspect_dict(node: dict[str, Any], path: str, *, descend_list: bool = False) -> None:
            for key, child in node.items():
                child_path = f"{path}.{key}"
                if key.lower() in _PAGINATION_SIGNAL_KEYS:
                    signals.append(child_path)
                if key == "list" and isinstance(child, list) and descend_list:
                    for index, item in enumerate(child):
                        if (
                            isinstance(item, dict)
                            and "content_id" not in item
                            and isinstance(item.get("list"), list)
                            and any(hint in item for hint in _LISTING_CONTAINER_HINT_KEYS)
                        ):
                            inspect_dict(item, f"{child_path}[{index}]", descend_list=True)

        if not isinstance(value, dict):
            return signals
        inspect_dict(value, "root")
        data = value.get("data")
        if isinstance(data, dict):
            inspect_dict(data, "root.data", descend_list=True)
        return signals

    def fetch_listings(self, map_payload: Any, *, channel_ids=None) -> int:
        selected_channels = None if channel_ids is None else {str(channel_id) for channel_id in channel_ids}
        seen = set()
        recognized_channels = 0
        for node in self._nodes(map_payload):
            channel_id = str(self._channel_identity(node))
            if channel_id in seen:
                continue
            seen.add(channel_id)
            recognized_channels += 1
            if selected_channels is not None and channel_id not in selected_channels:
                continue
            body = self._get("listings", channel_id, "/common/blackboard/ys_obc/v1/home/content/list",
                             {"app_sn": self.config.app_sn, "channel_id": channel_id, **self.config.listing_params},
                             context={"channel_id": channel_id})
            channel_record = {"channel_id": channel_id, "status": "completed" if body else "failed",
                              "pagination": "single_response_verified" if body else "UNKNOWN",
                              "attempted": True, "completed": bool(body), "failed": not bool(body)}
            self.manifest["channels"].append(channel_record)
            self._checkpoint()
            if not body:
                continue
            payload = self._json(body)
            pagination_signals = self._listing_pagination_signals(payload)
            if pagination_signals:
                channel_record["pagination"] = "pagination_signal_detected"
                self._listing_contract_blocked = True
                self._record_failure(
                    "listing_pagination_contract",
                    channel_id,
                    self.config.base_url.rstrip("/") + "/common/blackboard/ys_obc/v1/home/content/list",
                    None,
                    "listing response contains unhandled pagination signals",
                    context={"channel_id": channel_id, "signals": pagination_signals},
                )
                break
            self.manifest["listing_responses_saved"] += 1
            for item in self._items(payload, context={"channel_id": channel_id}):
                content_id = item.get("content_id")
                if content_id is None:
                    self._record_unknown({"kind": "listing_item", "channel_id": channel_id, "item": item},
                                         "listing item missing content_id")
                    continue
                self.manifest["listing_records_discovered"] += 1
                entry = self.inventory.setdefault(str(content_id), {"content_id": str(content_id), "channels": []})
                if channel_id not in entry["channels"]:
                    if entry["channels"]:
                        self.manifest["cross_channel_duplicate_memberships"] += 1
                    entry["channels"].append(channel_id)
            self._checkpoint()
        return recognized_channels

    def _items(self, value: Any, context: dict[str, Any] | None = None):
        if isinstance(value, dict):
            if isinstance(value.get("list"), list):
                for x in value["list"]:
                    if isinstance(x, dict) and "content_id" in x:
                        yield x
                    elif (
                        isinstance(x, dict)
                        and isinstance(x.get("list"), list)
                        and any(key in x for key in ("id", "parent_id", "depth", "children"))
                    ):
                        # Channel/list containers are traversed below; they are not content records.
                        continue
                    elif isinstance(x, dict):
                        yield x
                    else:
                        self._record_unknown({**(context or {}), "item": x}, "non-dict listing item")
            for child in value.values():
                yield from self._items(child, context=context)
        elif isinstance(value, list):
            for child in value:
                yield from self._items(child, context=context)

    def _select_details(self, detail_limit):
        if detail_limit is None:
            self.manifest["detail_selection"] = {
                "limit": None,
                "strategy": "inventory_order",
                "selected_content_ids": list(self.inventory),
            }
            return list(self.inventory.items())
        channel_order = [entry["channel_id"] for entry in self.manifest["channels"]]
        if len(channel_order) <= 1:
            selected = list(self.inventory.items())[:detail_limit]
            strategy = "inventory_order"
        else:
            buckets = {
                channel_id: [
                    (content_id, membership)
                    for content_id, membership in self.inventory.items()
                    if channel_id in membership["channels"]
                ]
                for channel_id in channel_order
            }
            selected = []
            selected_ids = set()
            while len(selected) < detail_limit:
                made_progress = False
                for channel_id in channel_order:
                    candidate = next((item for item in buckets[channel_id] if item[0] not in selected_ids), None)
                    if candidate is None:
                        continue
                    selected.append(candidate)
                    selected_ids.add(candidate[0])
                    made_progress = True
                    if len(selected) >= detail_limit:
                        break
                if not made_progress:
                    break
            strategy = "channel_round_robin"
        self.manifest["detail_selection"] = {
            "limit": detail_limit,
            "strategy": strategy,
            "selected_content_ids": [content_id for content_id, _ in selected],
        }
        return selected

    def fetch_details(self, *, detail_limit=None) -> None:
        if detail_limit is not None and detail_limit < 0:
            raise ValueError("detail_limit must be non-negative")
        write_json(self.store.metadata / "inventory.json", list(self.inventory.values()))
        self.manifest["unique_detail_responses_expected"] = len(self.inventory)
        self._checkpoint()
        details = self._select_details(detail_limit)
        for content_id, membership in details:
            body = self._get("details", content_id, "/hoyowiki/genshin/wapi/entry_page",
                             {"app_sn": self.config.app_sn, "entry_page_id": content_id, "lang": self.config.locale},
                             headers={"x-rpc-wiki_app": "genshin"}, context={"content_id": content_id})
            self.manifest["details"].append({"content_id": content_id, "channels": membership["channels"],
                                              "status": "completed" if body else "failed"})
            if body:
                self.manifest["successful_detail_fetches"] += 1
            else:
                self.manifest["failed_detail_fetches"] += 1
            self._checkpoint()

    def _finalize(self) -> dict[str, Any]:
        self.manifest["inventory_count"] = len(self.inventory)
        self.manifest["ended_at"] = _now()
        self.manifest["status"] = "complete" if self.manifest["failed_detail_fetches"] == 0 and not self.manifest["failures"] else "partial"
        self._checkpoint()
        return self.manifest

    def run(self, *, fetch_details: bool = True, channel_ids=None, detail_limit=None) -> dict:
        payload = self.discover_map()
        if payload is not None:
            recognized_channels = self.fetch_listings(payload, channel_ids=channel_ids)
            self.manifest["recognized_channel_count"] = recognized_channels
            if recognized_channels == 0:
                self._record_failure(
                    "map_schema",
                    "home-map",
                    self.config.base_url.rstrip("/") + "/common/blackboard/ys_obc/v1/home/map",
                    None,
                    "map response contained no recognizable channel nodes",
                )
            if fetch_details and not self._listing_contract_blocked:
                self.fetch_details(detail_limit=detail_limit)
        return self._finalize()
