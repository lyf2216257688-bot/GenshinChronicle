import math
import os
from pathlib import Path
import re
from urllib.parse import urlsplit, urlunsplit

from ..model_calls import locked
from .access import AccessPaused, SourceAccess
from .config import CollectorConfig
from .contract import (
    DETAIL_HEADERS, ENDPOINTS, REQUEST_CONTRACT, SCHEMA, SOURCE, RawIntegrityError,
    channel_nodes, content_key, full_request, json_bytes, object_at,
)
from .run_store import RawRunStore, now
from .storage import sha256
from .transport import HttpTransport, MAX_RETRY_DELAY, RequestError, USER_AGENT, request_with_retries


def _component(value, label):
    reserved = {"CON", "PRN", "AUX", "NUL"} | {f"{name}{i}" for name in ("COM", "LPT") for i in range(1, 10)}
    if (not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", value)
            or value.endswith(".") or value.split(".")[0].upper() in reserved):
        raise ValueError(f"Invalid {label} path component")
    return value


def _base_url(value):
    try:
        if not isinstance(value, str) or any(ord(c) <= 32 or c == "\\" for c in value) or "?" in value or "#" in value:
            raise ValueError
        split = urlsplit(value)
        if split.scheme not in ("http", "https") or not split.hostname or split.username is not None:
            raise ValueError
        port = split.port
    except (TypeError, ValueError):
        raise ValueError("Invalid Raw base URL. Use an HTTP(S) address without credentials, query or fragment.") from None
    hostname = split.hostname
    host = f"[{hostname}]" if ":" in hostname else hostname
    if port is not None and port != (443 if split.scheme == "https" else 80):
        host = f"{host}:{port}"
    return urlunsplit((split.scheme, host, split.path.rstrip("/"), "", ""))


class Collector:
    def __init__(self, config: CollectorConfig, transport=None, *, access=None):
        self.config = config
        self.run_id = _component(config.resolved_run_id(), "run ID")
        locale = _component(config.locale, "locale")
        output = Path(config.output_root).resolve()
        root = output / SOURCE / locale / self.run_id
        if root.resolve() != root:
            raise RawIntegrityError("Raw run root uses a filesystem alias")
        self.store = RawRunStore(root)
        self.base_url = _base_url(config.base_url)
        self.listing_params = dict(config.listing_params)
        if not isinstance(config.app_sn, str) or not config.app_sn.strip():
            raise ValueError("app_sn must be nonempty")
        if any(not isinstance(k, str) or not k.strip() or not isinstance(v, str) for k, v in self.listing_params.items()):
            raise ValueError("listing_params must map nonempty names to strings")
        if any(key.lower() in {"app_sn", "channel_id"} for key in self.listing_params):
            raise ValueError("listing_params may not override app_sn or channel_id")
        if (type(config.max_retries) is not int or config.max_retries < 0
                or type(config.timeout) not in (int, float) or not math.isfinite(config.timeout) or config.timeout <= 0
                or type(config.backoff_base) not in (int, float) or not math.isfinite(config.backoff_base)
                or config.backoff_base < 0):
            raise ValueError("Invalid timeout or retry policy")
        self.transport = transport if transport is not None else HttpTransport(timeout=config.timeout)
        self.user_agent = self.transport.user_agent if isinstance(self.transport, HttpTransport) else USER_AGENT
        self.access = access if access is not None else SourceAccess()

    def _scope(self, fetch_details, channel_ids, detail_limit):
        if type(fetch_details) is not bool:
            raise ValueError("fetch_details must be a boolean")
        if detail_limit is not None and (not fetch_details or type(detail_limit) is not int or detail_limit < 0):
            raise ValueError("A non-negative detail limit requires detail collection")
        if channel_ids is not None:
            if isinstance(channel_ids, (str, bytes)):
                raise ValueError("channel_ids must be an iterable of channel identifiers")
            channel_ids = list(channel_ids)
            if not channel_ids or any(not content_key(key) for key in channel_ids):
                raise ValueError("channel_ids must contain nonempty channel identifiers")
            channel_ids = sorted({str(key) for key in channel_ids})
        return {
            "source_system": SOURCE, "locale": self.config.locale, "run_id": self.run_id,
            "collection_root": os.path.normcase(str(self.store.root)),
            "base_url": self.base_url, "app_sn": self.config.app_sn,
            "user_agent": self.user_agent,
            "request_contract": REQUEST_CONTRACT, "channel_ids": channel_ids,
            "listing_param_names": sorted(self.listing_params),
            "listing_params_sha256": sha256(json_bytes(self.listing_params)),
            "fetch_details": fetch_details, "detail_limit": detail_limit,
        }

    def _request(self, kind, key):
        params, headers = {"app_sn": self.config.app_sn}, {}
        if kind == "listings":
            params.update({"channel_id": key, **dict(sorted(self.listing_params.items()))})
        elif kind == "details":
            params.update({"entry_page_id": key, "lang": self.config.locale})
            headers = DETAIL_HEADERS.copy()
        url = self.base_url + ENDPOINTS[kind]
        request = {"method": "GET", "url": url, "params": params, "headers": {"User-Agent": self.user_agent, **headers}}
        receipt = {"url": url, "request_sha256": sha256(json_bytes(request)),
                   "run_identity_sha256": self.identity["sha256"]}
        return {"url": url, "params": params, "headers": headers, "receipt": receipt}

    def _send(self, request, observe):
        options = {"params": request["params"], "headers": request["headers"]}
        if isinstance(self.transport, HttpTransport):
            return self.transport.request_once(request["url"], observe=observe, **options)
        return self.transport.get(request["url"], **options)

    def _begin(self, scope):
        self.manifest = {
            "schema_version": SCHEMA, "source_system": SOURCE, "locale": self.config.locale, "run_id": self.run_id,
            "status": "partial", "full_coverage": False, "started_at": self.identity["started_at"], "ended_at": None,
            "request_scope": scope, "run_identity_sha256": self.identity["sha256"],
            "execution": {"timeout": self.config.timeout, "max_retries": self.config.max_retries,
                          "backoff_base": self.config.backoff_base},
            "coverage": {"recognized_channel_ids": [], "target_channel_ids": [], "missing_channel_ids": [],
                         "discovery_complete": False},
            "channels": [], "details": [], "detail_selection": {"limit": scope["detail_limit"],
                                                               "strategy": "none", "selected_content_ids": []},
            "paths": {}, "failures": [], "recovery_issues": [],
            "recognized_channel_count": 0, "listing_responses_saved": 0, "listing_records_discovered": 0,
            "cross_channel_duplicate_memberships": 0, "inventory_count": 0, "unique_detail_responses_expected": 0,
            "successful_detail_fetches": 0, "failed_detail_fetches": 0, "http_responses_saved": 0,
            "retry_summary": {"attempts": 0, "retryable_failures": 0, "retry_after_seen": 0},
        }
        self.inventory = {}
        self.expected = {}
        self.discovery_blocked = False
        for path in (self.store.checkpoint_path, self.store.discovery_path):
            if not path.exists():
                continue
            prior = object_at(path)
            if prior.get("run_identity_sha256") != self.identity["sha256"] or not isinstance(prior.get("paths"), dict):
                raise RawIntegrityError(f"Raw recovery record has an invalid identity at {path}")
            if path == self.store.checkpoint_path and prior.get("status") != "partial":
                raise RawIntegrityError(f"Raw progress checkpoint has an unexpected status at {path}")
            for key, entry in prior["paths"].items():
                kind, separator, identifier = key.partition(":")
                if (not separator or not identifier or kind not in ENDPOINTS or not isinstance(entry, dict)
                        or (key in self.expected and self.expected[key] != entry)):
                    raise RawIntegrityError(f"Raw recovery records disagree for {key}")
                self.expected[key] = entry
        for key, entry in self.expected.items():
            kind, identifier = key.split(":", 1)
            self.store.accepted(kind, identifier, self._request(kind, identifier)["receipt"], expected=entry)
        for kind, identifier in self.store.accepted_keys():
            if kind not in ENDPOINTS:
                raise RawIntegrityError(f"Unknown accepted Raw kind: {kind}")
            key = f"{kind}:{identifier}"
            if key not in self.expected:
                entry, _ = self.store.accepted(kind, identifier, self._request(kind, identifier)["receipt"])
                self.expected[key] = entry

    def _get(self, kind, key, *, dispatch=True):
        request = self._request(kind, key)
        receipt = request["receipt"]
        entry_key = f"{kind}:{key}"
        accepted = self.store.accepted(kind, key, receipt, expected=self.expected.get(entry_key))
        records, issues, attempts = self.store.attempts(kind, key, receipt)
        if accepted is None:
            accepted = self.store.recover_attempt(kind, key, receipt, records, issues)
        if accepted is None and dispatch:
            try:
                request_with_retries(
                    lambda observe: self._send(request, observe), access=self.access, request=receipt,
                    on_attempt=lambda response, error, retry_after, access: self.store.record_attempt(
                        kind, key, receipt, response, error, retry_after_seen=retry_after, restriction=access),
                    max_retries=self.config.max_retries, backoff_base=self.config.backoff_base,
                    wait_cap=MAX_RETRY_DELAY,
                )
            except AccessPaused as exc:
                self.manifest["access_restriction"] = exc.restriction
            except RequestError:
                # The completed transport attempt already recorded the failure.
                pass
            records, latest_issues, attempts = self.store.attempts(kind, key, receipt)
            issues = list(dict.fromkeys(issues + latest_issues))
            accepted = self.store.recover_attempt(kind, key, receipt, records, issues)
        retry = self.manifest["retry_summary"]
        retry["attempts"] += attempts
        retry["retryable_failures"] += sum(record.get("retryable") is True for record in records)
        retry["retry_after_seen"] += sum(record.get("retry_after_seen") is True for record in records)
        self.manifest["http_responses_saved"] += sum("raw_path" in record for record in records)
        self.manifest["recovery_issues"].extend(issues)
        if accepted is not None:
            entry, data = accepted
            self.manifest["paths"][entry_key] = entry
            return data
        if not dispatch:
            return None
        record = records[-1] if records else {}
        restriction = self.manifest.get("access_restriction", {})
        error = restriction.get("reason") or record.get("error", "no usable Raw response")
        self.manifest["failures"].append({"kind": kind, "key": key, "url": request["url"],
                                          "status": restriction.get("status", record.get("status")), "error": error})
        if kind == "listings" and "pagination signals:" in error:
            self.discovery_blocked = True
        return None

    def _discover(self, scope):
        payload = self._get("map", "home-map")
        if payload is None:
            return False
        ids = [str(node["id"]) for node in channel_nodes(payload["list"], is_map=True)]
        requested = scope["channel_ids"]
        targets = ids if requested is None else [key for key in ids if key in requested]
        missing = [] if requested is None else sorted(set(requested) - set(ids))
        coverage = self.manifest["coverage"]
        coverage.update({"recognized_channel_ids": ids, "target_channel_ids": targets, "missing_channel_ids": missing})
        self.manifest["recognized_channel_count"] = len(ids)
        if missing:
            self.manifest["failures"].append({"kind": "channel_selection", "keys": missing,
                                              "error": "requested channel IDs absent from map"})
        for key in targets:
            channel = {"channel_id": key, "status": "not_attempted", "pagination": "UNKNOWN",
                       "listing_record_count": 0}
            self.manifest["channels"].append(channel)
            dispatch = not self.discovery_blocked and "access_restriction" not in self.manifest
            listing = self._get("listings", key, dispatch=dispatch)
            if listing is None:
                if dispatch:
                    channel["status"] = "failed"
                    if self.discovery_blocked:
                        channel["pagination"] = "pagination_signal_detected"
                continue
            self.manifest["listing_responses_saved"] += 1
            rows = [row for node in channel_nodes(listing["list"]) for row in node["list"]]
            channel.update({"status": "completed", "pagination": "single_response_verified",
                            "listing_record_count": len(rows)})
            self.manifest["listing_records_discovered"] += len(rows)
            for row in rows:
                content_id = str(row["content_id"])
                memberships = self.inventory.setdefault(content_id, {"content_id": content_id, "channels": []})["channels"]
                if key not in memberships:
                    self.manifest["cross_channel_duplicate_memberships"] += bool(memberships)
                    memberships.append(key)
        coverage["discovery_complete"] = bool(targets) and not self.manifest["failures"]
        return coverage["discovery_complete"]

    def _select_details(self, scope):
        limit = scope["detail_limit"]
        if not scope["fetch_details"]:
            return [], "none"
        if limit is None:
            return list(self.inventory), "inventory_order"
        channels = self.manifest["coverage"]["target_channel_ids"]
        if len(channels) <= 1:
            return list(self.inventory)[:limit], "inventory_order"
        buckets = {key: [] for key in channels}
        for content_id, member in self.inventory.items():
            for key in member["channels"]:
                buckets[key].append(content_id)
        iterators = [iter(bucket) for bucket in buckets.values()]
        selected, seen = [], set()
        while iterators and len(selected) < limit:
            active = []
            for bucket in iterators:
                for content_id in bucket:
                    if content_id not in seen:
                        selected.append(content_id)
                        seen.add(content_id)
                        active.append(bucket)
                        break
                if len(selected) == limit:
                    break
            iterators = active
        return selected, "channel_round_robin"

    def _details(self, selected):
        for key in selected:
            status = "not_attempted"
            dispatch = "access_restriction" not in self.manifest
            if self._get("details", key, dispatch=dispatch) is not None:
                status = "completed"
                self.manifest["successful_detail_fetches"] += 1
            elif dispatch:
                status = "failed"
                self.manifest["failed_detail_fetches"] += 1
            self.manifest["details"].append({**self.inventory[key], "status": status})

    def _collect(self, scope):
        self._begin(scope)
        if not self.store.checkpoint_path.exists():
            self.store.checkpoint(self.manifest)
        complete_discovery = self._discover(scope)
        self.manifest["inventory_count"] = len(self.inventory)
        self.manifest["unique_detail_responses_expected"] = len(self.inventory)
        if complete_discovery:
            selected, strategy = self._select_details(scope)
            selection = {"limit": scope["detail_limit"], "strategy": strategy, "selected_content_ids": selected}
            self.manifest["detail_selection"] = selection
            discovery = self.store.freeze_discovery(list(self.inventory.values()), {
                "run_identity_sha256": self.identity["sha256"], "paths": dict(self.manifest["paths"]),
                "coverage": self.manifest["coverage"], "detail_selection": selection,
            })
            self.manifest["inventory_sha256"] = discovery["inventory_sha256"]
            self.store.checkpoint(self.manifest)
            self._details(selected)
            if full_request(scope) and not self.inventory:
                self.manifest["failures"].append({"kind": "coverage", "error": "full OBC inventory is empty"})
        self.manifest["ended_at"] = now()
        if not complete_discovery or self.manifest["failures"]:
            self.store.checkpoint(self.manifest)
            return self.manifest
        self.manifest["full_coverage"] = full_request(scope)
        self.manifest["status"] = "complete" if self.manifest["full_coverage"] else "scoped_complete"
        self.store.prepare(self.manifest)
        return self.store.terminal(self.identity, request_for=self._request, publish=True)

    def run(self, *, fetch_details=True, channel_ids=None, detail_limit=None):
        scope = self._scope(fetch_details, channel_ids, detail_limit)
        self.identity = self.store.identity(scope)
        if self.identity is not None and self.store.manifest_path.exists():
            return self.store.terminal(self.identity, request_for=self._request)
        with locked(self.store.lock_path):
            self.identity = self.store.identity(scope, initialize=True)
            if self.store.manifest_path.exists() or self.store.prepared_path.exists():
                return self.store.terminal(self.identity, request_for=self._request, publish=True)
            return self._collect(scope)
