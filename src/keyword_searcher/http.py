import threading
from concurrent.futures import CancelledError
from contextlib import contextmanager
from dataclasses import dataclass
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .models import DiscoveryError
from .urls import normalize_url, require_public_url


@dataclass(frozen=True)
class Response:
    url: str
    status: int
    body: str
    content_type: str


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class HttpClient:
    def __init__(self, timeout: float = 20, max_bytes: int = 2_000_000):
        self.timeout = timeout
        self.max_bytes = max_bytes
        self._local = threading.local()
        self._opener_override = None
        self._hosts = set()
        self._condition = threading.Condition()
        self._cancelled = threading.Event()

    @property
    def opener(self):
        if self._opener_override is not None:
            return self._opener_override
        if not hasattr(self._local, "opener"):
            self._local.opener = build_opener(NoRedirect())
        return self._local.opener

    @opener.setter
    def opener(self, value):
        self._opener_override = value

    def cancel(self):
        self._cancelled.set()
        with self._condition:
            self._condition.notify_all()

    @contextmanager
    def host_slot(self, url):
        host = urlsplit(url).hostname.removeprefix("www.")
        with self._condition:
            while host in self._hosts and not self._cancelled.is_set():
                self._condition.wait(timeout=0.1)
            if self._cancelled.is_set():
                raise CancelledError()
            self._hosts.add(host)
        try:
            yield
        finally:
            with self._condition:
                self._hosts.remove(host)
                self._condition.notify_all()

    def get(self, url: str) -> Response:
        for _ in range(6):
            url = normalize_url(url)
            # Release this host before following a redirect and acquiring the next.
            with self.host_slot(url):
                response = self._get_once(url)
            if isinstance(response, Response):
                return response
            url = response
        raise DiscoveryError("redirect_limit")

    def _get_once(self, url):
        require_public_url(url)
        request = Request(url, headers={"User-Agent": "KeywordSearcher/0.1"})
        try:
            stream = self.opener.open(request, timeout=self.timeout)
        except HTTPError as exc:
            stream = exc
        except (URLError, OSError, TimeoutError, HTTPException) as exc:
            raise DiscoveryError("network_failure") from exc
        with stream:
            status = stream.code
            if status in {301, 302, 303, 307, 308}:
                location = stream.headers.get("Location")
                if not location:
                    raise DiscoveryError("redirect_without_location")
                return urljoin(url, location)
            try:
                data = stream.read(self.max_bytes + 1)
            except (OSError, TimeoutError, HTTPException) as exc:
                raise DiscoveryError("network_failure") from exc
            if len(data) > self.max_bytes:
                raise DiscoveryError("response_too_large")
            charset = stream.headers.get_content_charset() or "utf-8"
            try:
                body = data.decode(charset, errors="replace")
            except LookupError:
                body = data.decode("utf-8", errors="replace")
            return Response(url, status, body, stream.headers.get_content_type())
