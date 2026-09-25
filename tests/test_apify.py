import json
from unittest.mock import Mock

import pytest

from keyword_searcher.apify import ApifyGoogle, ApifyRecoveryRequired, ApifyTransport
from keyword_searcher.models import BudgetExceeded, DiscoveryError, Resolution
from keyword_searcher.pipeline import run
from keyword_searcher.store import Store


def record(query, page=1, count=2):
    return {
        "searchQuery": {"term": query, "page": page},
        "organicResults": [
            {"url": f"https://{query}.example.org/{page}-{index}", "title": query}
            for index in range(count)
        ],
    }


def setup(tmp_path, queries, transport, pages=2, limit=900, batch_size=20):
    store = Store(tmp_path / "state.sqlite", {})
    provider = ApifyGoogle(transport, store, queries, "br", "pt", pages, limit, batch_size)
    return store, provider


def test_apify_batch_reuses_serpapi_pages_and_exports(tmp_path):
    queries = ["old", "new", "later"]
    transport = Mock()
    transport.start.return_value = "run123"
    transport.completed.return_value = "dataset123"
    transport.items.return_value = [record("new"), record("later")]
    store, provider = setup(tmp_path, queries, transport)
    store.save_page(
        "old",
        0,
        {
            "search_metadata": {"status": "Success"},
            "organic_results": [],
        },
    )
    resolver = Mock()
    resolver.resolve.return_value = Resolution("confirmed", "Example", "https://example.org/")
    run(queries, provider, resolver, store, 2)
    assert transport.start.call_count == 1
    assert transport.start.call_args.args[:4] == (["new", "later"], 2, "br", "pt")
    assert store.requests() == 0
    report = store.export(tmp_path)
    assert report["apify_pages"] == 2
    assert report["apify_reserved_pages"] == 4
    assert report["exported_rows"] == 2
    assert {row["query"]: row["status"] for row in report["queries"]} == {
        "old": "complete",
        "new": "complete",
        "later": "complete",
    }
    store.close()


def test_apify_resumes_existing_run_without_starting_another(tmp_path):
    transport = Mock()
    transport.start.return_value = "run123"
    transport.completed.side_effect = [ApifyRecoveryRequired("temporary"), "dataset123"]
    transport.items.return_value = [record("q")]
    store, provider = setup(tmp_path, ["q"], transport)
    with pytest.raises(ApifyRecoveryRequired):
        provider.search("q", 0)
    assert store.apify_open_batch()[2] == "run123"
    resumed = ApifyGoogle(transport, store, ["q"], "br", "pt", 2, 900)
    assert resumed.search("q", 0)["provider"] == "apify-google"
    transport.start.assert_called_once()
    assert store.apify_open_batch() is None
    store.close()


def test_unconfirmed_start_requires_manual_run_id(tmp_path):
    transport = Mock()
    transport.start.side_effect = DiscoveryError("network_failure")
    store, provider = setup(tmp_path, ["q"], transport)
    with pytest.raises(ApifyRecoveryRequired, match="not confirmed"):
        provider.search("q", 0)
    with pytest.raises(ApifyRecoveryRequired, match="unconfirmed"):
        provider.search("q", 0)
    assert transport.start.call_count == 1
    transport.completed.return_value = "dataset123"
    transport.items.return_value = [record("q")]
    store.apify_recover_run("run123")
    assert provider.search("q", 0)["provider"] == "apify-google"
    store.close()


def test_apify_page_budget_is_cumulative(tmp_path):
    transport = Mock()
    transport.start.return_value = "run123"
    transport.completed.return_value = "dataset123"
    transport.items.return_value = [record("first")]
    store, provider = setup(tmp_path, ["first", "second"], transport, limit=2, batch_size=1)
    provider.search("first", 0)
    with pytest.raises(BudgetExceeded, match="apify_page_limit"):
        provider.search("second", 0)
    store.close()


def test_missing_query_does_not_silently_launch_another_actor(tmp_path):
    transport = Mock()
    transport.start.return_value = "run123"
    transport.completed.return_value = "dataset123"
    transport.items.return_value = [record("first")]
    store, provider = setup(tmp_path, ["first", "second"], transport)
    with pytest.raises(ApifyRecoveryRequired, match="omitted"):
        provider.search("first", 0)
    assert store.apify_open_batch()[2] == "run123"
    transport.start.assert_called_once()
    store.close()


def test_apify_pagination_and_validation(tmp_path):
    transport = Mock()
    transport.start.return_value = "run123"
    transport.completed.return_value = "dataset123"
    transport.items.return_value = [record("q", 1, 10), record("q", 2, 3)]
    store, provider = setup(tmp_path, ["q"], transport)
    assert provider.parse(provider.search("q", 0), 0).next_start == 10
    assert provider.parse(store.page("q", 10), 10).next_start is None
    store.close()


def test_apify_transport_uses_bearer_token_and_cost_cap(monkeypatch):
    captured = []

    class Reply:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def read(self, _limit):
            return json.dumps({"data": {"id": "run123"}}).encode()

    def fake_urlopen(request, timeout):
        captured.append(request)
        return Reply()

    monkeypatch.setattr("keyword_searcher.apify.urlopen", fake_urlopen)
    transport = ApifyTransport("fake-token")
    assert transport.start(["q"], 2, "br", "pt", 0.5) == "run123"
    req = captured[0]
    assert req.get_header("Authorization") == "Bearer fake-token"
    assert "fake-token" not in req.full_url
    assert "maxTotalChargeUsd=0.5" in req.full_url
    body = json.loads(req.data)
    assert body["maxPagesPerQuery"] == 2
    assert body["websiteContentScraper"]["enable"] is False
