"""Read legacy SerpApi payloads; never make remote searches."""

from urllib.parse import parse_qs, urlsplit

from .models import DiscoveryError, SearchPage, SearchResult


def metadata(payload):
    value = payload.get("search_metadata")
    if not isinstance(value, dict):
        raise DiscoveryError("provider_invalid_metadata")
    return value


def information(payload):
    value = payload.get("search_information", {})
    if not isinstance(value, dict):
        raise DiscoveryError("provider_invalid_search_information")
    return value


def parse_legacy(payload: dict, start: int) -> SearchPage:
    if not isinstance(payload, dict):
        raise DiscoveryError("provider_invalid_payload")
    if metadata(payload).get("status") != "Success":
        raise DiscoveryError("provider_search_not_successful")
    rows = payload.get("organic_results")
    if rows is None and information(payload).get("organic_results_state") == "Fully empty":
        rows = []
    if not isinstance(rows, list):
        raise DiscoveryError("provider_missing_organic_results")
    results = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("link"), str):
            raise DiscoveryError("provider_invalid_organic_result")
        results.append(
            SearchResult(str(row.get("title", "")), row["link"], str(row.get("snippet", "")))
        )
    pagination = payload.get("serpapi_pagination", {})
    if not isinstance(pagination, dict):
        raise DiscoveryError("provider_invalid_pagination")
    next_url = pagination.get("next") or pagination.get("next_link")
    next_start = None
    if next_url:
        try:
            next_start = int(parse_qs(urlsplit(next_url).query)["start"][0])
            if next_start <= start:
                raise ValueError()
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise DiscoveryError("provider_invalid_next_offset") from exc
    return SearchPage(results, next_start)
