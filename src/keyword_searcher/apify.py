"""Batch Google searches through Apify; never re-launch an uncertain paid run."""

import json
import re
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .models import DiscoveryError, SearchPage, SearchResult
from .search import SerpApi

API = "https://api.apify.com/v2"
ACTOR = "apify~google-search-scraper"
TERMINAL = {"SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"}


class ApifyRecoveryRequired(DiscoveryError):
    """Stop before another Actor can be charged for an uncertain batch."""


class ApifyTransport:
    def __init__(self, token):
        self.token = token

    def request(self, path, body=None):
        if not self.token:
            raise DiscoveryError("APIFY_API_TOKEN is required for uncached Apify searches")
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = Request(
            API + path,
            data=data,
            headers={
                "Authorization": "Bearer " + self.token,
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        )
        try:
            with urlopen(req, timeout=60) as response:
                raw = response.read(20_000_001)
                if len(raw) > 20_000_000:
                    raise DiscoveryError("apify_response_too_large")
        except HTTPError as exc:
            raise DiscoveryError(f"apify_http_{exc.code}") from exc
        except (URLError, OSError, TimeoutError) as exc:
            raise DiscoveryError("apify_network_failure") from exc
        try:
            return json.loads(raw)
        except (ValueError, TypeError) as exc:
            raise DiscoveryError("apify_invalid_json") from exc

    def start(self, queries, pages, country, language, cost_limit):
        input_data = {
            "queries": "\n".join(queries),
            "maxPagesPerQuery": pages,
            "countryCode": country,
            "languageCode": language,
            "focusOnPaidAds": False,
            "maximumLeadsEnrichmentRecords": 0,
            "aiOverview": {"scrapeFullAiOverview": False},
            "aiModeSearch": {"enableAiMode": False},
            "websiteContentScraper": {"enable": False},
        }
        result = self.request(
            f"/actors/{ACTOR}/runs?" + urlencode({"maxTotalChargeUsd": cost_limit}),
            input_data,
        )
        run = result.get("data", {}) if isinstance(result, dict) else {}
        run_id = run.get("id")
        if not isinstance(run_id, str) or not re.fullmatch(r"[a-zA-Z0-9]+", run_id):
            raise DiscoveryError("apify_missing_run_id")
        return run_id

    def completed(self, run_id):
        while True:
            result = self.request(f"/actor-runs/{run_id}")
            run = result.get("data", {}) if isinstance(result, dict) else {}
            status = run.get("status")
            if status in TERMINAL:
                if status != "SUCCEEDED":
                    raise ApifyRecoveryRequired(f"Apify run {run_id} ended with {status}")
                dataset_id = run.get("defaultDatasetId")
                if not isinstance(dataset_id, str) or not re.fullmatch(r"[a-zA-Z0-9]+", dataset_id):
                    raise ApifyRecoveryRequired(f"Apify run {run_id} has no dataset ID")
                return dataset_id
            if status not in {"READY", "RUNNING"}:
                raise ApifyRecoveryRequired(f"Apify run {run_id} has unknown status")
            time.sleep(5)

    def items(self, dataset_id):
        offset = 0
        while True:
            rows = self.request(
                f"/datasets/{dataset_id}/items?" + urlencode({"offset": offset, "limit": 50})
            )
            if not isinstance(rows, list):
                raise ApifyRecoveryRequired("apify_invalid_dataset")
            yield from rows
            offset += len(rows)
            if len(rows) < 50:
                return


class ApifyGoogle:
    """Fetch missing pages in bounded batches, using the existing page cache and resolver."""

    def __init__(
        self,
        transport,
        store,
        queries,
        country,
        language,
        max_pages,
        max_apify_pages,
        batch_size=20,
        cost_limit=1.0,
    ):
        self.transport = transport
        self.store = store
        self.queries = queries
        self.country = country
        self.language = language
        self.max_pages = max_pages
        self.max_apify_pages = max_apify_pages
        self.batch_size = batch_size
        self.cost_limit = cost_limit

    @staticmethod
    def parse(payload, start):
        if payload.get("provider") != "apify-google":
            return SerpApi.parse(payload, start)
        rows = payload.get("organicResults")
        if not isinstance(rows, list):
            raise DiscoveryError("apify_missing_organic_results")
        results = []
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("url"), str):
                raise DiscoveryError("apify_invalid_organic_result")
            results.append(
                SearchResult(str(row.get("title", "")), row["url"], str(row.get("description", "")))
            )
        return SearchPage(results, start + 10 if payload.get("has_next") else None)

    def search(self, query, start):
        open_batch = self.store.apify_open_batch()
        if open_batch:
            batch_id, batch_queries, run_id, status = open_batch
            if status == "planned":
                raise ApifyRecoveryRequired(
                    "Apify batch is unconfirmed: check Apify Console for its run ID, "
                    "then use --apify-recover-run-id ID; no new run was started"
                )
        else:
            if isinstance(self.transport, ApifyTransport) and not self.transport.token:
                raise DiscoveryError("APIFY_API_TOKEN is required for uncached Apify searches")
            remaining = self.queries[self.queries.index(query) :]
            batch_queries = [q for q in remaining if self.store.page(q, 0) is None or q == query][
                : self.batch_size
            ]
            if query not in batch_queries:
                raise DiscoveryError("apify_batch_missing_query")
            batch_id = self.store.apify_plan_batch(
                batch_queries, len(batch_queries) * self.max_pages, self.max_apify_pages
            )
            print(
                f"Apify: starting batch {batch_id} ({len(batch_queries)} queries)", file=sys.stderr
            )
            try:
                run_id = self.transport.start(
                    batch_queries, self.max_pages, self.country, self.language, self.cost_limit
                )
            except DiscoveryError as exc:
                raise ApifyRecoveryRequired(
                    "Apify start not confirmed; check Apify Console before resuming "
                    "to avoid a duplicate paid run"
                ) from exc
            self.store.apify_set_run(batch_id, run_id)
        print(f"Apify: waiting for run {run_id} (batch {batch_id})", file=sys.stderr)
        dataset_id = self.transport.completed(run_id)
        discovered = {}
        allowed = set(batch_queries)
        for item in self.transport.items(dataset_id):
            if not isinstance(item, dict):
                raise ApifyRecoveryRequired("apify_invalid_dataset_item")
            search = item.get("searchQuery")
            if not isinstance(search, dict):
                raise ApifyRecoveryRequired("apify_missing_search_query")
            term, page = search.get("term"), search.get("page")
            if term not in allowed or type(page) is not int or not 1 <= page <= self.max_pages:
                raise ApifyRecoveryRequired("apify_unexpected_query_or_page")
            if not isinstance(item.get("organicResults"), list):
                raise ApifyRecoveryRequired("apify_missing_organic_results")
            if (term, page) in discovered:
                raise ApifyRecoveryRequired("apify_duplicate_query_page")
            discovered[term, page] = item
        if not discovered:
            raise ApifyRecoveryRequired("apify_empty_dataset")
        if any((term, 1) not in discovered for term in batch_queries):
            raise ApifyRecoveryRequired(
                f"Apify run {run_id} omitted one or more queries; inspect its dataset"
            )
        for (term, page), item in discovered.items():
            rows = item["organicResults"]
            has_next = (term, page + 1) in discovered or (len(rows) >= 10)
            payload = {
                "provider": "apify-google",
                "organicResults": rows,
                "has_next": has_next,
                "searchQuery": {"term": term, "page": page},
            }
            self.parse(payload, (page - 1) * 10)  # Validate before storing.
            if self.store.page(term, (page - 1) * 10) is None:
                self.store.save_page(term, (page - 1) * 10, payload)
        self.store.apify_finish_batch(batch_id)
        result = self.store.page(query, start)
        if result is None:
            raise DiscoveryError("apify_missing_requested_page")
        return result
