import copy
import json
from unittest.mock import Mock
from urllib.parse import urlencode

import pytest

from keyword_searcher.apify import ApifyGoogle, ApifyRecoveryRequired
from keyword_searcher.apify_errors import SEARCH_ERROR
from keyword_searcher.export import export_results
from keyword_searcher.http import Response
from keyword_searcher.models import DiscoveryError, Resolution
from keyword_searcher.pipeline import run
from keyword_searcher.recheck import recheck_saved
from keyword_searcher.store import Store

OBSERVED_QUERY = "startup brasileira diabetes saúde digital"


def failure(term=OBSERVED_QUERY, page=19):
    # Shape supplied from Actor 0.0.466; stacks are deliberately not retained.
    return {
        "#debug": {
            "requestId": "j6YfcTpkQUsSwmq",
            "url": "http://www.google.com.br/search?"
            + urlencode(
                {
                    "q": term,
                    "hl": "pt-BR",
                    "start": (page - 1) * 10,
                }
            ),
            "method": "GET",
            "retryCount": 3,
            "errorMessages": [
                "Error: Got empty search results page (0 organic + paid results)",
                "Error: 500 - Internal Server Error: Google query failed: External proxy provider returned invalid result",
                "synthetic secret apify_api_do_not_save_me",
            ],
        },
        "#error": True,
    }


def record(term, page, host="page"):
    return {
        "searchQuery": {"term": term, "page": page},
        "organicResults": [{"url": f"https://{host}{page}.example/"}],
    }


def setup(tmp_path, queries, records, maximum=50, **evidence_changes):
    store = Store(tmp_path / "run/state.sqlite", {})
    batch = store.apify_plan_batch(queries, len(queries) * maximum, 2800)
    store.apify_set_run(batch, "existingRun")
    evidence = dict(
        dataset_id="dataset123",
        run_id="existingRun",
        status="SUCCEEDED",
        exit_code=0,
        cost_usd=1.6,
        cost_limit_usd=4.5425,
        pending_requests=0,
        handled_requests=len(records),
    )
    evidence.update(evidence_changes)
    transport = Mock()
    transport.start.side_effect = AssertionError("Existing paid run must never be recreated")
    transport.completed.return_value = evidence
    transport.items.return_value = records
    provider = ApifyGoogle(transport, store, queries, "br", "pt", maximum, 2800)
    return store, provider, transport


def resolver():
    result = Mock()
    result.resolve.side_effect = lambda r: Resolution("confirmed", "Supplier", r.url)
    return result


def test_337_record_regression_recovers_336_pages_and_existing_paid_run(tmp_path):
    queries = [OBSERVED_QUERY] + [f"query {index}" for index in range(19)]
    records = [record(OBSERVED_QUERY, page, "observed") for page in range(1, 19)]
    for index, query in enumerate(queries[1:]):
        records.extend(
            record(query, page, f"source{index}p")
            for page in range(1, (17 if index < 14 else 16) + 1)
        )
    records.insert(335, failure())
    assert len(records) == 337
    store, provider, transport = setup(tmp_path, queries, records)
    sites = resolver()
    try:
        run(queries, provider, sites, store, 50, workers=8)
        report = export_results(store, tmp_path / "output")
        assert report["apify_pages"] == 336
        assert report["exported_rows"] == 336
        assert report["query_status_counts"] == {"incomplete": 1, "complete": 19}
        coverage = report["apify_coverage"][OBSERVED_QUERY]
        assert coverage["pages"] == 18 and coverage["last_page"] == 19
        assert coverage["failed_pages"] == [19] and coverage["reason"] == SEARCH_ERROR
        assert report["apify_runs"][0]["dataset_records"] == 337
        assert report["apify_runs"][0]["search_errors"] == [
            {
                "query": OBSERVED_QUERY,
                "page": 19,
                "reason": SEARCH_ERROR,
                "retry_count": 3,
                "request_id": "j6YfcTpkQUsSwmq",
            }
        ]
        assert store.apify_open_batch() is None
        assert store.apify_reserved_pages() == 1000
        metadata = json.loads((tmp_path / "output/handoff.metadata.json").read_text())
        assert metadata["coverage"]["partial"]
        before = sites.resolve.call_count
        run(queries, provider, sites, store, 50, workers=8)
        assert sites.resolve.call_count == before == 336
        transport.completed.assert_called_once_with("existingRun")
        transport.start.assert_not_called()
        assert "apify_api_do_not_save_me" not in json.dumps(report)
    finally:
        store.close()


@pytest.mark.parametrize("failed_page", [1, 2, 3])
def test_first_middle_or_last_error_does_not_hide_later_valid_pages(tmp_path, failed_page):
    records = [
        failure("q", page) if page == failed_page else record("q", page) for page in range(1, 4)
    ]
    store, provider, transport = setup(tmp_path, ["q"], records)
    sites = resolver()
    try:
        run(["q"], provider, sites, store, 50)
        assert sites.resolve.call_count == 2
        assert store.apify_pages() == 2
        assert len(store.saved_pages("q")) == 3
        assert store.export_progress() == [
            dict(query="q", status="incomplete", reason=SEARCH_ERROR)
        ]
        transport.start.assert_not_called()
    finally:
        store.close()


def test_error_only_query_is_incomplete_and_does_not_poison_other_query(tmp_path):
    records = [failure("q", 1), record("other", 1)]
    store, provider, transport = setup(tmp_path, ["q", "other"], records)
    sites = resolver()
    try:
        run(["q", "other"], provider, sites, store, 50)
        assert sites.resolve.call_count == 1
        assert store.apify_query_coverage("q")["pages"] == 0
        assert store.apify_query_coverage("other")["status"] == "complete"
        assert store.apify_pages() == 1
        transport.start.assert_not_called()
    finally:
        store.close()


def test_all_error_dataset_is_recorded_without_claiming_empty_success(tmp_path):
    store, provider, transport = setup(tmp_path, ["q"], [failure("q", 1)])
    sites = resolver()
    try:
        run(["q"], provider, sites, store, 50)
        assert store.apify_pages() == 0
        assert store.export_progress()[0]["status"] == "incomplete"
        assert store.apify_open_batch() is None
        sites.resolve.assert_not_called()
        run(["q"], provider, sites, store, 50)
        transport.completed.assert_called_once()
        transport.start.assert_not_called()
    finally:
        store.close()


@pytest.mark.parametrize(
    "change",
    [
        {"#debug": None},
        {"#debug": {"url": "http://www.google.com.br/search?q=q&start=10", "method": "POST"}},
        {"#debug": {"url": "http://evil.example/search?q=q&start=10", "method": "GET"}},
        {"#debug": {"url": "http://www.google.com.br.evil/search?q=q&start=10", "method": "GET"}},
        {"#debug": {"url": "http://www.google.com.br/search?q=other&start=10", "method": "GET"}},
        {"#debug": {"url": "http://www.google.com.br/search?q=q&start=11", "method": "GET"}},
        {"#debug": {"url": "http://www.google.com.br/search?q=q&start=-10", "method": "GET"}},
        {"#debug": {"url": "http://www.google.com.br/search?q=q&q=q&start=10", "method": "GET"}},
        {
            "#debug": {
                "url": "http://www.google.com.br/search?q=q&start=10&start=20",
                "method": "GET",
            }
        },
        {"#debug": {"url": "http://www.google.com.br/search?q=q&start=500", "method": "GET"}},
        {"#debug": {"url": "http://user:secret@www.google.com/search?q=q", "method": "GET"}},
        {"searchQuery": {"term": "q", "page": True}},
        {"organicResults": [{"url": "https://partial.example/"}]},
    ],
)
def test_unmapped_or_contradictory_error_preserves_run_and_blocks_partial_import(tmp_path, change):
    item = failure("q", 2)
    item.update(copy.deepcopy(change))
    store, provider, transport = setup(tmp_path, ["q"], [record("q", 1), item])
    try:
        with pytest.raises(ApifyRecoveryRequired, match="unmapped_search_error"):
            provider.search("q", 0)
        assert store.saved_pages("q") == []
        assert store.apify_open_batch()[2] == "existingRun"
        transport.start.assert_not_called()
    finally:
        store.close()


def test_unexplained_gap_still_blocks_whole_dataset(tmp_path):
    records = [record("q", 1), failure("q", 3)]
    store, provider, _transport = setup(tmp_path, ["q"], records)
    try:
        with pytest.raises(ApifyRecoveryRequired, match="missing_intermediate"):
            provider.search("q", 0)
        assert store.saved_pages("q") == []
    finally:
        store.close()


def test_success_for_failed_page_is_kept_but_error_still_disclosed(tmp_path):
    records = [record("q", 1), failure("q", 1)]
    store, provider, _transport = setup(tmp_path, ["q"], records)
    try:
        run(["q"], provider, resolver(), store, 50)
        assert store.apify_pages() == 1
        assert not store.page("q", 0).get("search_error")
        assert store.apify_query_coverage("q")["failed_pages"] == [1]
        assert store.export_progress()[0]["status"] == "incomplete"
    finally:
        store.close()


def test_unknown_queue_counts_remain_unverified_even_with_mapped_error(tmp_path):
    records = [record("q", 1), failure("q", 2), record("other", 1)]
    store, provider, _transport = setup(tmp_path, ["q", "other"], records, handled_requests=2)
    try:
        run(["q", "other"], provider, resolver(), store, 50)
        assert store.apify_query_coverage("other")["reason"] == "apify_completion_unverified"
        assert store.apify_query_coverage("q")["reason"] == SEARCH_ERROR
        assert store.apify_pages() == 2
    finally:
        store.close()


def test_recheck_copy_reads_error_markers_and_preserves_original(tmp_path):
    records = [record("q", 1), failure("q", 2), record("q", 3)]
    store, provider, _transport = setup(tmp_path, ["q"], records)
    run(["q"], provider, resolver(), store, 50)
    store.close()
    source = tmp_path / "run/state.sqlite"
    before = source.read_bytes()
    http = Mock()
    http.get.side_effect = lambda url: Response(
        url,
        200,
        '<meta property="og:site_name" content="Supplier"><a href="/products">Products</a>',
        "text/html",
    )
    summary = recheck_saved(source.parent, tmp_path / "recheck", http=http, progress=False)
    assert source.read_bytes() == before
    assert summary["exported_rows"] == 2
    assert http.get.call_count == 2
    report = json.loads((tmp_path / "recheck/report.json").read_text())
    assert report["apify_pages"] == 2
    assert report["query_status_counts"] == {"incomplete": 1}


def test_fake_error_marker_cannot_hide_results_or_claim_success():
    payload = dict(
        provider="apify-google",
        organicResults=[{"url": "https://a.example/"}],
        has_next=True,
        end_status="incomplete",
        end_reason=SEARCH_ERROR,
        search_error=True,
    )
    with pytest.raises(DiscoveryError, match="invalid_search_error"):
        ApifyGoogle.parse(payload, 0)
