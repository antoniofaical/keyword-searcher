import ipaddress
import socket
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .models import DiscoveryError

TRACKERS = {"gclid", "fbclid", "msclkid", "dclid"}


def normalize_url(url: str) -> str:
    try:
        parts = urlsplit(url.strip())
        if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
            raise ValueError()
        if parts.username or parts.password:
            raise ValueError()
        host = parts.hostname.encode("idna").decode().lower().rstrip(".")
        port = parts.port
        if port not in {None, 80, 443}:
            raise ValueError()
        if ":" in host:
            host = f"[{host}]"
        if port and (parts.scheme.lower(), port) not in {("http", 80), ("https", 443)}:
            host += f":{port}"
        query = [
            (k, v)
            for k, v in parse_qsl(parts.query, keep_blank_values=True)
            if not k.lower().startswith("utm_") and k.lower() not in TRACKERS
        ]
        return urlunsplit((parts.scheme.lower(), host, parts.path or "/", urlencode(query), ""))
    except (ValueError, UnicodeError, AttributeError) as exc:
        raise DiscoveryError("invalid_url") from exc


def same_host(left: str, right: str) -> bool:
    def host(url):
        value = urlsplit(url).hostname or ""
        return value.removeprefix("www.").lower()

    return host(left) == host(right)


def require_public_url(url: str) -> None:
    host = urlsplit(normalize_url(url)).hostname
    try:
        addresses = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise DiscoveryError("dns_failure") from exc
    if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
        raise DiscoveryError("non_public_destination")
