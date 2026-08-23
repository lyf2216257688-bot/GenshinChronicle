import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .config import CollectorConfig
from .storage import RunStore, write_json
from .transport import HttpTransport, RequestError, Response


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
            "details": [],
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

    def _nodes(self, value: Any):
        if isinstance(value, dict):
            if "channel_id" in value:
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

    def fetch_listings(self, map_payload: Any) -> None:
        seen = set()
        for node in self._nodes(map_payload):
            channel_id = str(node.get("channel_id"))
            if channel_id in seen:
                continue
            seen.add(channel_id)
            body = self._get("listings", channel_id, "/common/blackboard/ys_obc/v1/home/content/list",
                             {"app_sn": self.config.app_sn, "channel_id": channel_id, **self.config.listing_params},
                             context={"channel_id": channel_id})
            self.manifest["channels"].append({"channel_id": channel_id, "status": "completed" if body else "failed",
                                               "pagination": "UNKNOWN", "attempted": True, "completed": bool(body),
                                               "failed": not bool(body)})
            self._checkpoint()
            if not body:
                continue
            self.manifest["listing_responses_saved"] += 1
            payload = self._json(body)
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

    def _items(self, value: Any, context: dict[str, Any] | None = None):
        if isinstance(value, dict):
            if isinstance(value.get("list"), list):
                for x in value["list"]:
                    if isinstance(x, dict):
                        yield x
                    else:
                        self._record_unknown({**(context or {}), "item": x}, "non-dict listing item")
            for child in value.values():
                yield from self._items(child, context=context)
        elif isinstance(value, list):
            for child in value:
                yield from self._items(child, context=context)

    def fetch_details(self) -> None:
        write_json(self.store.metadata / "inventory.json", list(self.inventory.values()))
        self.manifest["unique_detail_responses_expected"] = len(self.inventory)
        self._checkpoint()
        for content_id, membership in self.inventory.items():
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

    def run(self, *, fetch_details: bool = True) -> dict:
        payload = self.discover_map()
        if payload is not None:
            self.fetch_listings(payload)
            if fetch_details:
                self.fetch_details()
        return self._finalize()
