"""Batch Google searches through Apify; never re-launch an uncertain paid run."""

import json
import math
import re
import sys
import time
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .models import BudgetExceeded, DiscoveryError, SearchPage, SearchResult
from .search import parse_legacy

API = "https://api.apify.com/v2"
ACTOR = "apify~google-search-scraper"
TERMINAL = {"SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"}
TRANSITIONAL = {"READY", "RUNNING", "TIMING-OUT", "ABORTING"}


class ApifyRecoveryRequired(DiscoveryError):
    """Stop before another Actor can be charged for an uncertain batch."""


def valid_id(value):
    return isinstance(value, str) and re.fullmatch(r"[a-zA-Z0-9]+", value)


class ApifyTransport:
    def __init__(self, token, retries=2):
        self.token = token
        self.retries = retries

    def request(self, path, body=None):
        if not self.token:
            raise DiscoveryError("APIFY_API_TOKEN is required for uncached Apify searches")
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = Request(
            API + path,
            data=data,
            headers={
                "Authorization": "Bearer " + self.token,
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        )
        # GET retries are safe. A lost POST response must never create a duplicate run.
        attempts = 1 if body is not None else self.retries + 1
        for attempt in range(attempts):
            try:
                with urlopen(request, timeout=60) as response:
                    raw = response.read(20_000_001)
                    if len(raw) > 20_000_000:
                        raise DiscoveryError("apify_response_too_large")
                break
            except HTTPError as exc:
                if exc.code not in {429, 500, 502, 503, 504} or attempt == attempts - 1:
                    raise DiscoveryError(f"apify_http_{exc.code}") from exc
            except (URLError, OSError, TimeoutError, HTTPException) as exc:
                if attempt == attempts - 1:
                    raise DiscoveryError("apify_network_failure") from exc
            time.sleep(min(2**attempt, 4))
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
            "verifyLeadsEnrichmentEmails": False,
            "aiOverview": {"scrapeFullAiOverview": False},
            "aiModeSearch": {"enableAiMode": False},
            "geminiSearch": {"enableGemini": False},
            "perplexitySearch": {"enablePerplexity": False},
            "chatGptSearch": {"enableChatGpt": False},
            "copilotSearch": {"enableCopilot": False},
            "websiteContentScraper": {"enable": False},
        }
        result = self.request(
            f"/actors/{ACTOR}/runs?" + urlencode({"maxTotalChargeUsd": cost_limit}),
            input_data,
        )
        run = result.get("data", {}) if isinstance(result, dict) else {}
        if not isinstance(run, dict) or not valid_id(run.get("id")):
            raise DiscoveryError("apify_missing_run_id")
        return run["id"]

    def completed(self, run_id):
        while True:
            result = self.request(f"/actor-runs/{run_id}")
            run = result.get("data") if isinstance(result, dict) else None
            if not isinstance(run, dict):
                raise ApifyRecoveryRequired("apify_invalid_run")
            status = run.get("status")
            if not isinstance(status, str):
                raise ApifyRecoveryRequired("apify_invalid_run_status")
            if status in TERMINAL:
                if status != "SUCCEEDED":
                    raise ApifyRecoveryRequired(f"Apify run {run_id} ended with {status}")
                # Run stats and costs can still be preliminary at first completion.
                time.sleep(10)
                result = self.request(f"/actor-runs/{run_id}")
                run = result.get("data") if isinstance(result, dict) else None
                if not isinstance(run, dict) or run.get("status") != "SUCCEEDED":
                    raise ApifyRecoveryRequired("apify_unstable_terminal_run")
                if not valid_id(run.get("defaultDatasetId")):
                    raise ApifyRecoveryRequired(f"Apify run {run_id} has no dataset ID")
                queue = {}
                queue_id = run.get("defaultRequestQueueId")
                if valid_id(queue_id):
                    result = self.request(f"/request-queues/{queue_id}")
                    queue = result.get("data", {}) if isinstance(result, dict) else {}
                    if not isinstance(queue, dict):
                        queue = {}
                options = run.get("options", {})
                options = options if isinstance(options, dict) else {}
                # Whitelist evidence: do not persist echoed credentials or arbitrary messages.
                return {
                    "run_id": run_id,
                    "dataset_id": run["defaultDatasetId"],
                    "status": run["status"],
                    "exit_code": run.get("exitCode"),
                    "cost_usd": run.get("usageTotalUsd"),
                    "cost_limit_usd": options.get("maxTotalChargeUsd"),
                    "pending_requests": queue.get("pendingRequestCount"),
                    "handled_requests": queue.get("handledRequestCount"),
                }
            if status not in TRANSITIONAL:
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


def completion_reason(evidence, record_count):
    """Conservative evidence of exhaustion, independent of organic link count."""
    cost, cap = evidence.get("cost_usd"), evidence.get("cost_limit_usd")
    numbers = all(type(v) in {int, float} and math.isfinite(v) for v in (cost, cap))
    if numbers and cap > 0 and cost >= cap:
        return "apify_cost_limit"
    if (
        not numbers
        or cost < 0
        or cap <= 0
        or evidence.get("status") != "SUCCEEDED"
        or evidence.get("exit_code") != 0
        or type(evidence.get("pending_requests")) is not int
        or evidence["pending_requests"] != 0
        or type(evidence.get("handled_requests")) is not int
        or evidence["handled_requests"] != record_count
    ):
        return "apify_completion_unverified"
    return ""


class ApifyGoogle:
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
        if not isinstance(payload, dict):
            raise DiscoveryError("provider_invalid_payload")
        if payload.get("provider") != "apify-google":
            return parse_legacy(payload, start)
        rows = payload.get("organicResults")
        if not isinstance(rows, list):
            raise DiscoveryError("apify_missing_organic_results")
        results = []
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("url"), str):
                raise DiscoveryError("apify_invalid_organic_result")
            results.append(
                SearchResult(
                    str(row.get("title", "")),
                    row["url"],
                    str(row.get("description", "")),
                )
            )
        if type(payload.get("has_next")) is not bool:
            raise DiscoveryError("apify_invalid_pagination")
        status = payload.get("end_status", "incomplete")
        reason = payload.get("end_reason", "apify_legacy_completion_unverified")
        if (
            not isinstance(status, str)
            or status not in {"complete", "limited", "incomplete"}
            or not isinstance(reason, str)
        ):
            raise DiscoveryError("apify_invalid_coverage")
        return SearchPage(results, start + 10 if payload["has_next"] else None, status, reason)

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
            # Cached continuations can use legacy variable offsets or uncertain Apify
            # coverage. Never translate these into an automatic paid replacement.
            if self.store.saved_pages(query):
                raise DiscoveryError("cached_continuation_unverified_use_new_run")
            if isinstance(self.transport, ApifyTransport) and not self.transport.token:
                raise DiscoveryError("APIFY_API_TOKEN is required for uncached Apify searches")
            remaining = self.queries[self.queries.index(query) :]
            capacity = min(
                self.batch_size,
                (self.max_apify_pages - self.store.apify_reserved_pages()) // self.max_pages,
            )
            if capacity < 1:
                raise BudgetExceeded("apify_page_limit")
            batch_queries = [q for q in remaining if not self.store.saved_pages(q)][:capacity]
            batch_id = self.store.apify_plan_batch(
                batch_queries,
                len(batch_queries) * self.max_pages,
                self.max_apify_pages,
            )
            print(
                f"Apify: starting batch {batch_id} ({len(batch_queries)} queries)", file=sys.stderr
            )
            try:
                run_id = self.transport.start(
                    batch_queries,
                    self.max_pages,
                    self.country,
                    self.language,
                    self.cost_limit,
                )
            except DiscoveryError as exc:
                raise ApifyRecoveryRequired(
                    "Apify start not confirmed; check Apify Console before resuming "
                    "to avoid a duplicate paid run"
                ) from exc
            self.store.apify_set_run(batch_id, run_id)
        print(f"Apify: waiting for run {run_id} (batch {batch_id})", file=sys.stderr)
        evidence = self.transport.completed(run_id)
        if not isinstance(evidence, dict) or not valid_id(evidence.get("dataset_id")):
            raise ApifyRecoveryRequired("apify_missing_completion_evidence")
        discovered = {}
        allowed = set(batch_queries)
        for item in self.transport.items(evidence["dataset_id"]):
            if not isinstance(item, dict) or not isinstance(item.get("searchQuery"), dict):
                raise ApifyRecoveryRequired("apify_missing_search_query")
            term, page = item["searchQuery"].get("term"), item["searchQuery"].get("page")
            if (
                not isinstance(term, str)
                or term not in allowed
                or type(page) is not int
                or not 1 <= page <= self.max_pages
            ):
                raise ApifyRecoveryRequired("apify_unexpected_query_or_page")
            if not isinstance(item.get("organicResults"), list):
                raise ApifyRecoveryRequired("apify_missing_organic_results")
            if (term, page) in discovered:
                raise ApifyRecoveryRequired("apify_duplicate_query_page")
            discovered[term, page] = item
        if not discovered:
            raise ApifyRecoveryRequired("apify_empty_dataset")
        if any((term, 1) not in discovered for term in batch_queries):
            raise ApifyRecoveryRequired(f"Apify run {run_id} omitted queries; inspect its dataset")
        for term in batch_queries:
            pages = sorted(page for q, page in discovered if q == term)
            if pages != list(range(1, pages[-1] + 1)):
                raise ApifyRecoveryRequired("apify_missing_intermediate_page")
            # Old mixed runs have no reliable offset mapping.
            if any(
                json.loads(raw).get("provider") != "apify-google"
                for _, raw in self.store.saved_pages(term)
            ):
                raise ApifyRecoveryRequired("apify_legacy_offset_mapping_unverified")
        reason = completion_reason(evidence, len(discovered))
        coverage = {}
        for term in batch_queries:
            last = max(page for q, page in discovered if q == term)
            if reason:
                status, end_reason = "incomplete", reason
            elif last == self.max_pages:
                status, end_reason = "limited", "max_pages"
            else:
                status, end_reason = "complete", ""
            coverage[term] = {
                "pages": last,
                "status": status,
                "reason": end_reason,
                "run_id": run_id,
            }
        pages = []
        for (term, page), item in sorted(discovered.items()):
            summary = coverage[term]
            payload = {
                "provider": "apify-google",
                "organicResults": item["organicResults"],
                "has_next": (term, page + 1) in discovered,
                "end_status": summary["status"],
                "end_reason": summary["reason"],
                "searchQuery": {"term": term, "page": page},
            }
            self.parse(payload, (page - 1) * 10)
            pages.append((term, (page - 1) * 10, payload))
        self.store.apify_import_batch(batch_id, pages, coverage, evidence)
        result = self.store.page(query, start)
        if result is None:
            raise DiscoveryError("apify_missing_requested_page")
        return result
