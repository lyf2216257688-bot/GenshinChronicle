import http.client
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Mapping

from .access import AccessPaused, SourceAccess


USER_AGENT = "genshin-corpus-collector/0.1"
MAX_RETRY_DELAY = 60.0


@dataclass
class Response:
    status: int
    body: bytes
    headers: Mapping[str, str]
    url: str


class RequestError(Exception):
    def __init__(self, message, *, status=None, retryable=False, response=None):
        super().__init__(message)
        self.status = status
        self.retryable = retryable
        self.response = response


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # A redirect has no reviewed OBC request contract of its own.
        return None


class HttpTransport:
    def __init__(self, *, timeout=20.0, max_retries=3, backoff_base=.25,
                 user_agent=USER_AGENT, max_retry_delay=MAX_RETRY_DELAY):
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self.user_agent = user_agent
        self.max_retry_delay = max_retry_delay
        self.opener = urllib.request.build_opener(_NoRedirect())

    def request_once(self, url, *, params, headers, observe):
        query = urllib.parse.urlencode(params)
        target = f"{url}?{query}" if query else url
        req = urllib.request.Request(target, headers={"User-Agent": self.user_agent, **dict(headers)}, method="GET")
        try:
            reply = self.opener.open(req, timeout=self.timeout)
        except urllib.error.HTTPError as exc:
            reply, status = exc, exc.code
        except (OSError, http.client.HTTPException) as exc:
            # Exception text can include query values or local network configuration.
            raise RequestError(type(exc).__name__, retryable=True) from exc
        else:
            status = reply.status
        with reply:
            headers = dict(reply.headers.items()) if reply.headers is not None else {}
            observe(status, headers)
            try:
                body = reply.read()
            except (OSError, http.client.HTTPException) as exc:
                partial = getattr(exc, "partial", None)
                response = Response(status, partial, headers, url) if isinstance(partial, bytes) else None
                raise RequestError(f"incomplete HTTP body ({type(exc).__name__})", status=status,
                                   retryable=status == 200 or status >= 500, response=response) from exc
        return Response(status, body, headers, url)

    def get(self, url, *, params=(), headers=(), access=None):
        """Bounded GET for the verified OBC transport."""
        access = access if access is not None else SourceAccess()
        return request_with_retries(
            lambda observe: self.request_once(url, params=params, headers=headers, observe=observe),
            access=access, request={"url": urllib.parse.urlsplit(url).path}, on_attempt=lambda *args: None,
            max_retries=self.max_retries, backoff_base=self.backoff_base, wait_cap=self.max_retry_delay,
        )


def request_with_retries(send, *, access, request, on_attempt, max_retries, backoff_base, wait_cap=MAX_RETRY_DELAY):
    backoff = min(backoff_base, wait_cap)
    cooldown_wait_left = wait_cap
    for index in range(max_retries + 1):
        restriction = None
        observed = False
        retry_after_seen = False
        with access.attempt(backoff, request) as observe_status:
            def observe(status, headers):
                nonlocal restriction, observed, retry_after_seen
                restriction = observe_status(status, headers)
                observed = True
                retry_after_seen = status == 429 and any(key.lower() == "retry-after" for key in headers)

            error = None
            try:
                response = send(observe)
            except RequestError as exc:
                error, response = exc, exc.response
            if not observed:
                observe(response.status if response is not None else error.status,
                        response.headers if response is not None else {})
            on_attempt(response, error, retry_after_seen, restriction)
        if restriction is not None:
            if "not_before" in restriction and index < max_retries:
                wait = max(0.0, restriction["not_before"] - access.clock())
                if wait <= cooldown_wait_left:
                    time.sleep(wait)
                    cooldown_wait_left -= wait
                    continue
            raise AccessPaused(restriction)
        if error is None and response.status < 500:
            return response
        if index == max_retries or (error is not None and not error.retryable):
            if error is not None:
                raise error
            return response
        time.sleep(backoff + random.uniform(0, min(backoff_base, backoff * .1)))
        backoff = min(backoff * 2, wait_cap)
    raise AssertionError("negative retry budget")
