"""Write-once Raw observations and recoverable run publication."""
from base64 import b32encode
from datetime import datetime, timezone
import os
from pathlib import Path
import tempfile

from .contract import (
    ENDPOINTS, SCHEMA, RawIntegrityError, ResponseError, json_bytes, object_at, relative_path, validate_response,
)
from .storage import atomic_write, sha256


def now():
    return datetime.now(timezone.utc).isoformat()


def create_bytes(path, body):
    """Publish complete bytes atomically, failing if the destination exists."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(prefix=".tmp-", dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        try:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        except BaseException:
            # A partial staging file is not evidence or a publication.
            handle.close()
            temporary.unlink(missing_ok=True)
            raise
    try:
        if os.name == "nt":
            os.rename(temporary, path)  # Windows rename fails on an existing target.
        else:
            os.link(temporary, path)
    finally:
        if path.is_file() and temporary.exists() and os.path.samefile(temporary, path):
            temporary.unlink()


def create_json(path, value):
    create_bytes(path, json_bytes(value))


class RawRunStore:
    def __init__(self, root):
        self.root = Path(root)
        self.identity_path = self.path("metadata/identity.json")
        self.checkpoint_path = self.path("metadata/checkpoint.json")
        self.inventory_path = self.path("metadata/inventory.json")
        self.discovery_path = self.path("metadata/discovery.json")
        self.prepared_path = self.path("metadata/finalizing.json")
        self.manifest_path = self.path("metadata/manifest.json")

    def path(self, value):
        return relative_path(self.root, value, prefix=value.split("/", 1)[0])

    @property
    def lock_path(self):
        directory = self.root.parent / ".collector-locks"
        if directory.resolve() != directory:
            raise RawIntegrityError("Raw lock directory uses a filesystem alias")
        path = directory / f"{self.root.name}.lock"
        if path.resolve() != path:
            raise RawIntegrityError("Raw run lock uses a filesystem alias")
        return path

    def identity(self, scope, *, initialize=False):
        fingerprint = sha256(json_bytes(scope))
        if self.identity_path.exists():
            existing = object_at(self.identity_path)
            if (existing.get("schema_version") != SCHEMA or existing.get("request_scope") != scope
                    or existing.get("sha256") != fingerprint
                    or not isinstance(existing.get("started_at"), str) or not existing["started_at"]):
                raise RawIntegrityError("Raw run identity differs or is invalid. Use a new run ID for collection.")
            return existing
        if self.root.exists() and (not self.root.is_dir() or any(self.root.iterdir())):
            raise RawIntegrityError("Raw run lacks a verifiable collector identity. Use a new run ID for collection.")
        if initialize:
            identity = {"schema_version": SCHEMA, "request_scope": scope, "sha256": fingerprint, "started_at": now()}
            create_json(self.identity_path, identity)
            return identity
        return None

    def _bucket(self, kind, key):
        # Encode the full key digest compactly on case-insensitive Windows filesystems.
        digest = b32encode(bytes.fromhex(sha256(key.encode("utf-8")))).decode("ascii").rstrip("=").lower()
        return f"responses/{kind}/{digest}"

    def _attempt_paths(self, kind, key, number):
        base = f"{self._bucket(kind, key)}/{number:08d}"
        return self.path(f"{base}/response.json"), self.path(f"{base}/response.meta.json")

    def _accepted_path(self, kind, key):
        return self.path(f"{self._bucket(kind, key)}/accepted.json")

    def _numbers(self, kind, key):
        bucket = self.path(self._bucket(kind, key))
        if not bucket.exists():
            return []
        return sorted(int(path.name) for path in bucket.iterdir()
                      if len(path.name) == 8 and path.name.isascii() and path.name.isdecimal())

    def record_attempt(self, kind, key, request, response, error, *, retry_after_seen=False, restriction=None):
        numbers = self._numbers(kind, key)
        number = (numbers[-1] if numbers else 0) + 1
        raw, meta = self._attempt_paths(kind, key, number)
        record = {
            "kind": kind, "key": key, "attempt": number, **request, "observed_at": now(),
            "status": response.status if response is not None else error.status,
            "ok": False, "retryable": bool(error and error.retryable),
            "retry_after_seen": retry_after_seen,
        }
        if restriction is not None:
            record["access_restriction"] = restriction
        if response is not None:
            create_bytes(raw, response.body)
            record["raw_path"] = raw.relative_to(self.root).as_posix()
            record["sha256"] = sha256(response.body)
        if error is not None:
            record["error"] = str(error)
        elif response.status == 200:
            try:
                validate_response(kind, key, response.body)
            except ResponseError as exc:
                record["error"] = str(exc)
            else:
                record["ok"] = True
        else:
            record["error"] = f"HTTP {response.status}"
            record["retryable"] = response.status == 429 or response.status >= 500
        create_json(meta, record)

    def _attempt(self, kind, key, number, request, *, verify_body):
        raw, meta = self._attempt_paths(kind, key, number)
        record = object_at(meta)
        if (record.get("kind") != kind or record.get("key") != key or type(record.get("attempt")) is not int
                or record["attempt"] != number or any(record.get(field) != value for field, value in request.items())):
            raise RawIntegrityError(f"Raw attempt identity mismatch at {meta}")
        if "raw_path" in record or "sha256" in record:
            if (record.get("raw_path") != raw.relative_to(self.root).as_posix() or not raw.is_file()
                    or not isinstance(record.get("sha256"), str)):
                raise RawIntegrityError(f"Raw attempt path or digest is invalid at {meta}")
        elif raw.exists() or record.get("ok") is True:
            raise RawIntegrityError(f"Raw attempt lost its body binding at {meta}")
        if verify_body and record.get("ok") is True:
            body = raw.read_bytes()
            if record.get("status") != 200 or sha256(body) != record["sha256"]:
                raise RawIntegrityError(f"Raw attempt body checksum or HTTP state is invalid at {raw}")
            try:
                data = validate_response(kind, key, body)
            except ResponseError as exc:
                raise RawIntegrityError(f"Raw attempt business validation failed at {raw}: {exc}") from exc
            return record, data
        return record, None

    def accepted(self, kind, key, request, *, expected=None):
        path = self._accepted_path(kind, key)
        if not path.exists():
            if expected is not None:
                raise RawIntegrityError(f"Accepted Raw reference missing at {path}")
            return None
        pointer = object_at(path)
        number = pointer.get("attempt")
        if (type(number) is not int or number < 1
                or pointer.get("key") != key or pointer.get("kind") != kind):
            raise RawIntegrityError(f"Invalid accepted Raw reference at {path}")
        _, metadata = self._attempt_paths(kind, key, number)
        if not metadata.is_file() or sha256(metadata.read_bytes()) != pointer.get("receipt_sha256"):
            raise RawIntegrityError(f"Accepted Raw receipt missing or changed at {metadata}")
        record, data = self._attempt(kind, key, number, request, verify_body=True)
        if data is None:
            raise RawIntegrityError(f"Accepted Raw reference points to an unsuccessful attempt at {path}")
        entry = {"raw": record["raw_path"], "metadata": metadata.relative_to(self.root).as_posix(),
                 "sha256": record["sha256"]}
        if expected is not None and expected != entry:
            raise RawIntegrityError(f"Accepted Raw reference changed at {path}")
        return entry, data

    def accepted_keys(self):
        for path in (self.root / "responses").glob("*/*/accepted.json"):
            kind = path.parent.parent.name
            raw_reference = object_at(self.path(path.relative_to(self.root).as_posix()))
            key = raw_reference.get("key")
            if not isinstance(key, str) or path != self._accepted_path(kind, key):
                raise RawIntegrityError(f"Invalid accepted Raw reference at {path}")
            yield kind, key

    def attempts(self, kind, key, request):
        records, issues = [], []
        numbers = self._numbers(kind, key)
        for number in numbers:
            try:
                record, _ = self._attempt(kind, key, number, request, verify_body=False)
            except (RawIntegrityError, OSError) as exc:
                issues.append(str(exc))
            else:
                records.append(record)
        return records, issues, len(numbers)

    def recover_attempt(self, kind, key, request, records, issues):
        path = self._accepted_path(kind, key)
        for record in records:
            if record.get("ok") is not True:
                continue
            number = record["attempt"]
            try:
                _, data = self._attempt(kind, key, number, request, verify_body=True)
            except (RawIntegrityError, OSError) as exc:
                issues.append(str(exc))
                continue
            _, metadata = self._attempt_paths(kind, key, number)
            create_json(path, {"kind": kind, "key": key, "attempt": number,
                               "receipt_sha256": sha256(metadata.read_bytes())})
            entry = {"raw": record["raw_path"], "metadata": metadata.relative_to(self.root).as_posix(),
                     "sha256": record["sha256"]}
            return entry, data
        return None

    def checkpoint(self, value):
        # This is a replaceable report. Attempt receipts and accepted references own recovery.
        if value["status"] != "partial":
            raise RawIntegrityError("Only partial Raw progress may be checkpointed")
        atomic_write(self.path("metadata/checkpoint.json"), json_bytes(value))

    def freeze_discovery(self, inventory, discovery):
        body = json_bytes(inventory)
        discovery = {**discovery, "inventory_sha256": sha256(body)}
        for path, expected in (
            (self.inventory_path, body), (self.discovery_path, json_bytes(discovery))
        ):
            if path.exists():
                if path.read_bytes() != expected:
                    raise RawIntegrityError(f"Frozen Raw discovery differs at {path}")
            else:
                create_bytes(path, expected)
        return discovery

    def prepare(self, manifest):
        body = json_bytes(manifest)
        create_json(self.prepared_path, {"sha256": sha256(body), "manifest": manifest})

    def terminal(self, identity, *, request_for, publish=False):
        prepared = object_at(self.prepared_path)
        manifest = prepared.get("manifest")
        if not isinstance(manifest, dict):
            raise RawIntegrityError("Missing prepared Raw manifest")
        body = json_bytes(manifest)
        if (prepared.get("sha256") != sha256(body) or manifest.get("schema_version") != SCHEMA
                or manifest.get("status") not in ("complete", "scoped_complete")
                or manifest.get("request_scope") != identity["request_scope"]
                or manifest.get("started_at") != identity["started_at"]
                or manifest.get("run_identity_sha256") != identity["sha256"]):
            raise RawIntegrityError("Prepared Raw manifest or run identity is invalid")
        if self.manifest_path.exists():
            if self.manifest_path.read_bytes() != body:
                raise RawIntegrityError("Published Raw manifest differs from its prepared bytes")
        elif not publish:
            raise RawIntegrityError("Raw manifest has not been published")
        inventory = self.inventory_path.read_bytes()
        if sha256(inventory) != manifest.get("inventory_sha256"):
            raise RawIntegrityError("Published Raw inventory checksum mismatch")
        paths = manifest.get("paths")
        if not isinstance(paths, dict):
            raise RawIntegrityError("Published Raw response paths are invalid")
        for entry_key, entry in paths.items():
            kind, separator, key = entry_key.partition(":")
            if not separator or not key or kind not in ENDPOINTS or not isinstance(entry, dict):
                raise RawIntegrityError(f"Invalid published Raw response entry: {entry_key}")
            self.accepted(kind, key, request_for(kind, key)["receipt"], expected=entry)
        if not self.manifest_path.exists():
            create_bytes(self.manifest_path, body)
        return manifest
