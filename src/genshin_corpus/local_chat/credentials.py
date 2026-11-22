"""Windows user-scoped storage for the verified local-chat model route."""
import base64
from contextlib import contextmanager
import ctypes
from ctypes import wintypes
from dataclasses import dataclass, field
import json
import os
from uuid import uuid4

from ..evidence_io import read, strict_json, write
from ..generation.clean_chat import DEFAULT_ROUTE, ROUTES
from .store import Problem

OFFICIAL_ENDPOINT = ROUTES[DEFAULT_ROUTE]["endpoint"]
KEY_ENV = ROUTES[DEFAULT_ROUTE]["key_env"]
OFFICIAL_BASE = OFFICIAL_ENDPOINT.removesuffix("/chat/completions")
FILE_NAME = "live-settings.json"


@dataclass(frozen=True)
class Credential:
    source: str
    revision: str | None = None
    key: str | None = field(default=None, repr=False)


def _valid_key(value):
    return bool(value) and all("!" <= char <= "~" for char in value)


def _dpapi(value, *, encrypt):
    if os.name != "nt":
        raise OSError("Windows credential protection unavailable")

    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]

    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32")
    transform = crypt32.CryptProtectData if encrypt else crypt32.CryptUnprotectData
    description = wintypes.LPCWSTR if encrypt else ctypes.POINTER(wintypes.LPWSTR)
    transform.argtypes = [ctypes.POINTER(Blob), description, ctypes.POINTER(Blob),
                          ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    transform.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
    kernel32.LocalFree.restype = wintypes.HLOCAL

    data = (ctypes.c_ubyte * len(value)).from_buffer_copy(value)
    source, result = Blob(len(value), data), Blob()
    try:
        # User scope is the default; flag 1 only suppresses native dialogs.
        if not transform(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result)):
            raise ctypes.WinError(ctypes.get_last_error())
        return ctypes.string_at(result.data, result.size)
    finally:
        ctypes.memset(data, 0, len(value))
        if result.data:
            if not encrypt:
                ctypes.memset(result.data, 0, result.size)
            kernel32.LocalFree(ctypes.cast(result.data, wintypes.HLOCAL))


def read_credential(root):
    try:
        record = read(root / FILE_NAME)
    except FileNotFoundError:
        key = os.environ.get(KEY_ENV, "").strip()
        return Credential("environment", "environment", key) if _valid_key(key) else Credential("missing")
    except (OSError, ValueError):
        return Credential("unavailable")
    try:
        if record["version"] != 1 or record["endpoint"] != OFFICIAL_ENDPOINT:
            return Credential("unavailable")
        protected = base64.b64decode(record["protected_key"], validate=True)
        payload = strict_json(_dpapi(protected, encrypt=False).decode("utf8"))
        revision, key = record["revision"], payload["key"]
        if not isinstance(revision, str) or not revision or payload["revision"] != revision:
            return Credential("unavailable")
        if not isinstance(key, str) or not _valid_key(key):
            return Credential("unavailable")
        return Credential("local", revision, key)
    except (OSError, TypeError, ValueError, KeyError):
        return Credential("unavailable")


def save_credential(root, endpoint, key):
    if endpoint.strip().rstrip("/") not in {
        OFFICIAL_BASE, OFFICIAL_BASE + "/v1", OFFICIAL_ENDPOINT, OFFICIAL_BASE + "/v1/chat/completions",
    }:
        raise Problem("unsupported_endpoint", "请输入 DeepSeek 官方地址 https://api.deepseek.com。", 422)
    key = key.strip()
    if not _valid_key(key):
        raise Problem("invalid_key", "API Key 不能为空或包含空格、换行等字符。", 422)
    if os.name != "nt":
        raise Problem("credential_storage_unavailable", "此运行环境无法使用 Windows 账号加密。")
    current = read_credential(root)
    if current.source == "local" and current.key == key:
        return
    revision = uuid4().hex
    data = json.dumps({"revision": revision, "key": key}).encode("utf8")
    try:
        protected = _dpapi(data, encrypt=True)
    except OSError:
        raise Problem("credential_storage_unavailable", "Windows 凭据加密失败，API Key 未保存。") from None
    temporary = root / (".live-settings-" + revision + ".tmp")
    try:
        write(temporary, {"version": 1, "endpoint": OFFICIAL_ENDPOINT, "revision": revision,
                          "protected_key": base64.b64encode(protected).decode("ascii")})
        os.replace(temporary, root / FILE_NAME)
    finally:
        temporary.unlink(missing_ok=True)


def clear_credential(root):
    (root / FILE_NAME).unlink(missing_ok=True)


@contextmanager
def worker_credential(root, revision):
    credential = read_credential(root)
    if not credential.key or credential.revision != revision:
        raise ValueError("LIVE_CREDENTIAL_BINDING_CHANGED")
    # G5 reads its established slot. Only the per-turn process receives this key.
    previous = os.environ.get(KEY_ENV)
    os.environ[KEY_ENV] = credential.key
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(KEY_ENV, None)
        else:
            os.environ[KEY_ENV] = previous
