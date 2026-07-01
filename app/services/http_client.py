from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any


@dataclass
class HttpResponse:
    status: int
    data: Any
    text: str


class ExternalAPIError(RuntimeError):
    def __init__(self, message: str, *, status: int | None = None, body: str | None = None):
        super().__init__(message)
        self.status = status
        self.body = body


def request_json(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    payload: Any = None,
    query: dict[str, str] | None = None,
    timeout: int = 30,
    retries: int = 2,
) -> HttpResponse:
    if query:
        separator = "&" if "?" in url else "?"
        url = url + separator + urllib.parse.urlencode(query)

    body: bytes | None = None
    req_headers = {"User-Agent": "Nocturne-MVP/0.1", **(headers or {})}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        req_headers.setdefault("Content-Type", "application/json")

    last_error: ExternalAPIError | None = None
    for attempt in range(retries + 1):
        request = urllib.request.Request(url, data=body, method=method.upper(), headers=req_headers)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read().decode("utf-8")
                return HttpResponse(response.status, _decode(raw), raw)
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            if exc.code in {408, 409, 425, 429, 500, 502, 503, 504} and attempt < retries:
                time.sleep(0.6 * (2**attempt))
                continue
            raise ExternalAPIError(f"HTTP {exc.code} from {url}", status=exc.code, body=raw) from exc
        except urllib.error.URLError as exc:
            last_error = ExternalAPIError(f"network error calling {url}: {exc.reason}")
            if attempt < retries:
                time.sleep(0.6 * (2**attempt))
                continue
            raise last_error from exc

    assert last_error is not None
    raise last_error


def _decode(raw: str) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw
