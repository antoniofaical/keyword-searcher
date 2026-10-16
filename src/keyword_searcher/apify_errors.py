"""Map explicit Actor failure rows to queries/pages without importing debug traces."""

import re
from urllib.parse import parse_qs, urlsplit

from .models import DiscoveryError

SEARCH_ERROR = "apify_search_page_failed"


def mapped_error(item, allowed, max_pages):
    debug = item.get("#debug")
    try:
        if not isinstance(debug, dict) or debug.get("method") != "GET":
            raise ValueError()
        if item.get("organicResults") not in (None, []):
            raise ValueError()
        url = urlsplit(debug["url"])
        if (
            url.scheme not in {"http", "https"}
            or url.username is not None
            or url.password is not None
            or url.port not in {None, 80, 443}
            or url.path != "/search"
            or not re.fullmatch(
                r"(?:www\.)?google\.(?:com|[a-z]{2,3}|(?:com|co)\.[a-z]{2})", url.hostname or ""
            )
        ):
            raise ValueError()
        params = parse_qs(url.query, keep_blank_values=True)
        terms, offsets = params.get("q", []), params.get("start", ["0"])
        if len(terms) != 1 or terms[0] not in allowed or len(offsets) != 1:
            raise ValueError()
        if not re.fullmatch(r"0|[1-9][0-9]{0,8}", offsets[0]):
            raise ValueError()
        offset = int(offsets[0])
        page = offset // 10 + 1
        if offset % 10 or page > max_pages:
            raise ValueError()
        term = terms[0]
        if "searchQuery" in item:
            query = item["searchQuery"]
            if (
                not isinstance(query, dict)
                or query.get("term") != term
                or type(query.get("page")) is not int
                or query["page"] != page
            ):
                raise ValueError()
        result = {"query": term, "page": page, "reason": SEARCH_ERROR}
        retries = debug.get("retryCount")
        if type(retries) is int and retries >= 0:
            result["retry_count"] = retries
        request_id = debug.get("requestId")
        if isinstance(request_id, str) and re.fullmatch(r"[a-zA-Z0-9]{1,128}", request_id):
            result["request_id"] = request_id
        return result
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise DiscoveryError("apify_unmapped_search_error") from exc
