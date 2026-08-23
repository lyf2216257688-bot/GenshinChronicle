import random
import time
import urllib.error
import urllib.parse
import urllib.request
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Mapping, Optional


@dataclass
class Response:
    status: int
    body: bytes
    headers: Mapping[str, str]
    url: str


class RequestError(Exception):
    def __init__(self, message: str, *, status: Optional[int] = None, retryable: bool = False):
        super().__init__(message)
        self.status = status
        self.retryable = retryable


class HttpTransport:
    def __init__(self, *, timeout: float = 20.0, max_retries: int = 3, backoff_base: float = .25,
                 user_agent: str = "genshin-corpus-collector/0.1", max_retry_delay: float = 60.0):
        self.timeout, self.max_retries, self.backoff_base = timeout, max_retries, backoff_base
        self.max_retry_delay = max(0.0, max_retry_delay)
        self.user_agent = user_agent

    def get(self, url: str, *, params: Mapping[str, str] = (), headers: Mapping[str, str] = ()) -> Response:
        query = urllib.parse.urlencode(params)
        target = url + (("&" if "?" in url else "?") + query if query else "")
        request_headers = {"User-Agent": self.user_agent, **dict(headers)}
        for attempt in range(self.max_retries + 1):
            try:
                req = urllib.request.Request(target, headers=request_headers, method="GET")
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    return Response(resp.status, resp.read(), dict(resp.headers.items()), target)
            except urllib.error.HTTPError as exc:
                if exc.code == 429:
                    retry_after = exc.headers.get("Retry-After")
                    if attempt < self.max_retries:
                        self._sleep(retry_after, attempt)
                        continue
                retryable = exc.code >= 500
                if retryable and attempt < self.max_retries:
                    self._sleep(None, attempt)
                    continue
                raise RequestError(f"HTTP {exc.code}", status=exc.code, retryable=retryable) from exc
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                if attempt < self.max_retries:
                    self._sleep(None, attempt)
                    continue
                raise RequestError(str(exc), retryable=True) from exc
        raise RequestError("request exhausted", retryable=True)

    def _sleep(self, retry_after: Optional[str], attempt: int) -> None:
        if retry_after:
            try:
                delay = max(0.0, float(retry_after))
            except ValueError:
                try:
                    target = parsedate_to_datetime(retry_after)
                    if target.tzinfo is None:
                        target = target.replace(tzinfo=timezone.utc)
                    delay = max(0.0, (target - datetime.now(timezone.utc)).total_seconds())
                except (TypeError, ValueError, OverflowError):
                    delay = self.backoff_base * (2 ** attempt)
        else:
            delay = self.backoff_base * (2 ** attempt)
        delay = min(delay, self.max_retry_delay)
        time.sleep(delay + random.uniform(0, min(self.backoff_base, delay * .1)))
