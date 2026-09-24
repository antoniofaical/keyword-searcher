from dataclasses import dataclass
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
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
        self.opener = build_opener(NoRedirect())

    def get(self, url: str) -> Response:
        for _ in range(6):
            url = normalize_url(url)
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
                    url = urljoin(url, location)
                    continue
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
        raise DiscoveryError("redirect_limit")
