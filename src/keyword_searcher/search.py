import json
import time
from urllib.parse import parse_qs, urlencode, urlsplit

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


class SerpApi:
    """Only organic Google results; pagination offsets come from provider metadata."""

    def __init__(self, http, api_key, country, language, location, reserve, retries=2):
        self.http = http
        self.api_key = api_key
        self.country = country
        self.language = language
        self.location = location
        self.reserve = reserve
        self.retries = retries

    def search(self, query: str, start: int) -> dict:
        if not self.api_key:
            raise DiscoveryError("SERPAPI_API_KEY is required for uncached searches")
        params = dict(
            engine="google",
            q=query,
            start=start,
            gl=self.country,
            hl=self.language,
            api_key=self.api_key,
        )
        if self.location:
            params["location"] = self.location
        for attempt in range(self.retries + 1):
            self.reserve()  # Commit before sending: interrupted attempts still count.
            try:
                response = self.http.get("https://serpapi.com/search.json?" + urlencode(params))
            except DiscoveryError:
                if attempt == self.retries:
                    raise
            else:
                if response.status == 200:
                    try:
                        payload = json.loads(response.body)
                    except ValueError as exc:
                        raise DiscoveryError("provider_invalid_json") from exc
                    if not isinstance(payload, dict):
                        raise DiscoveryError("provider_invalid_payload")
                    # Successful no-results responses may contain an error message.
                    if payload.get("error") and metadata(payload).get("status") != "Success":
                        raise DiscoveryError("provider_error_check_account_or_query")
                    # Never persist echoed API keys or provider URLs containing them.
                    return json.loads(json.dumps(payload).replace(self.api_key, "[REDACTED]"))
                if response.status not in {429, 500, 502, 503, 504}:
                    raise DiscoveryError(f"provider_http_{response.status}")
                if attempt == self.retries:
                    raise DiscoveryError(f"provider_http_{response.status}")
            time.sleep(min(2**attempt, 4))
        raise DiscoveryError("provider_retry_exhausted")

    @staticmethod
    def parse(payload: dict, start: int) -> SearchPage:
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
