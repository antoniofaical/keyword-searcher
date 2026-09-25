import csv
import json
from unittest.mock import Mock

import pytest

from keyword_searcher.cli import main, read_queries
from keyword_searcher.http import Response
from keyword_searcher.models import BudgetExceeded, DiscoveryError, Resolution, SearchResult
from keyword_searcher.pipeline import recheck_sites, run
from keyword_searcher.resolve import WebsiteResolver
from keyword_searcher.search import SerpApi
from keyword_searcher.store import Store
from keyword_searcher.urls import normalize_url, require_public_url


def payload(urls=(), next_start=None):
    data = {
        "search_metadata": {"status": "Success"},
        "organic_results": [{"title": "Example", "link": u} for u in urls],
    }
    if next_start is not None:
        data["serpapi_pagination"] = {"next": f"https://serpapi.com/search?start={next_start}"}
    return data


def html(name="Example", url="https://example.org/", extra=""):
    return (
        '<script type="application/ld+json">'
        + json.dumps({"@type": "Organization", "name": name, "url": url})
        + "</script>"
        + extra
    )


def web(body, url="https://example.org/", status=200):
    return Response(url, status, body, "text/html")


def test_url_preserves_path_subdomain_query():
    assert (
        normalize_url("HTTPS://ACME.example.org/pt/brand/?tenant=2&utm_source=x#test")
        == "https://acme.example.org/pt/brand/?tenant=2"
    )


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "https://u:p@example.org",
        "javascript:alert(1)",
        "https://example.org:8000",
    ],
)
def test_invalid_urls(url):
    with pytest.raises(DiscoveryError):
        normalize_url(url)


def test_private_destination(monkeypatch):
    monkeypatch.setattr("socket.getaddrinfo", lambda *a, **k: [(0, 0, 0, "", ("127.0.0.1", 0))])
    with pytest.raises(DiscoveryError, match="non_public"):
        require_public_url("https://example.org")


def test_schema_home_and_redirect():
    client = Mock()
    client.get.side_effect = [
        web(html(url="https://example.org/pt/"), "https://example.org/pt/product"),
        web(html(url="https://example.org/pt/"), "https://example.org/pt/"),
    ]
    result = WebsiteResolver(client).resolve(SearchResult("Product", "https://old.example.org/p"))
    assert (result.status, result.name, result.url) == (
        "confirmed",
        "Example",
        "https://example.org/pt/",
    )
    assert client.get.call_count == 2


def test_no_guess_from_title_or_domain():
    client = Mock()
    client.get.return_value = web("<title>Acme - best software</title>")
    result = WebsiteResolver(client).resolve(SearchResult("Acme", "https://example.org"))
    assert result.status == "pending"
    assert not result.name


def test_home_link_fallback():
    client = Mock()
    client.get.side_effect = [
        web('<a href="/en/">Home</a>', "https://example.org/product"),
        web(html(url="https://example.org/en/"), "https://example.org/en/"),
    ]
    result = WebsiteResolver(client).resolve(SearchResult("Product", "https://example.org/product"))
    assert result.status == "confirmed"
    assert result.url == "https://example.org/en/"


def test_ambiguous_organizations():
    client = Mock()
    client.get.return_value = web(html() + html("Different"))
    assert (
        WebsiteResolver(client).resolve(SearchResult("", "https://example.org")).status == "pending"
    )


def test_publisher_not_company():
    client = Mock()
    client.get.return_value = web(
        '<script type="application/ld+json">'
        + json.dumps(
            {
                "@type": "Article",
                "publisher": {
                    "@type": "Organization",
                    "name": "Publisher",
                    "url": "https://example.org/",
                },
            }
        )
        + "</script>"
    )
    result = WebsiteResolver(client).resolve(SearchResult("", "https://example.org"))
    assert result.status != "confirmed"


@pytest.mark.parametrize(
    "url",
    [
        "https://g2.com/products/x",
        "https://www.linkedin.com/company/a",
        "https://example.org/news/new-product",
    ],
)
def test_non_institutional_not_fetched(url):
    client = Mock()
    assert WebsiteResolver(client).resolve(SearchResult("", url)).status == "skipped"
    client.get.assert_not_called()


def test_sourceforge_directory_is_not_exported():
    client = Mock()
    result = WebsiteResolver(client).resolve(
        SearchResult(
            "Hospital food service software",
            "https://sourceforge.net/software/food-service-management/windows/",
        )
    )
    assert result.status == "skipped"
    client.get.assert_not_called()


def test_deep_product_url_resolves_to_verified_homepage():
    client = Mock()
    product = "https://www.alphaebm.com/hospital-food-service-software-uae.html"
    client.get.side_effect = [
        web(html("Alpha EBM", product), product),
        web('<meta property="og:site_name" content="Alpha EBM">', "https://www.alphaebm.com/"),
    ]
    result = WebsiteResolver(client).resolve(SearchResult("Alpha EBM", product))
    assert (result.status, result.url) == ("confirmed", "https://www.alphaebm.com/")


def test_site_name_on_homepage_suffices_without_schema():
    client = Mock()
    client.get.side_effect = [
        web("<title>Product</title>", "https://example.org/product"),
        web('<meta property="og:site_name" content="Example">'),
    ]
    result = WebsiteResolver(client).resolve(SearchResult("Product", "https://example.org/product"))
    assert (result.status, result.name, result.url) == (
        "confirmed",
        "Example",
        "https://example.org/",
    )


def test_mismatched_home_identity():
    client = Mock()
    client.get.side_effect = [web(html(), "https://example.org/product"), web(html("Different"))]
    result = WebsiteResolver(client).resolve(SearchResult("", "https://example.org/product"))
    assert result.reason == "entrypoint_identity_mismatch"


def test_provider_exact_query_and_redaction():
    client = Mock()
    data = payload(["https://example.org"])
    data["search_parameters"] = {"api_key": "secret123"}
    client.get.return_value = Response("", 200, json.dumps(data), "application/json")
    reserve = Mock()
    provider = SerpApi(client, "secret123", "br", "pt", "", reserve)
    result = provider.search('"inventory" OR estoque -jobs', 10)
    from urllib.parse import parse_qs, urlsplit

    params = parse_qs(urlsplit(client.get.call_args.args[0]).query)
    assert params["q"] == ['"inventory" OR estoque -jobs']
    assert params["start"] == ["10"]
    assert "secret123" not in json.dumps(result)
    reserve.assert_called_once()


def test_provider_retries_count_against_budget(monkeypatch):
    monkeypatch.setattr("keyword_searcher.search.time.sleep", lambda _: None)
    client = Mock()
    client.get.side_effect = [
        Response("", 429, "", ""),
        Response("", 200, json.dumps(payload()), ""),
    ]
    reserve = Mock()
    assert SerpApi(client, "secret", "br", "pt", "", reserve).search("q", 0)
    assert reserve.call_count == 2


def test_provider_auth_error_no_retry():
    client = Mock()
    client.get.return_value = Response("", 401, "secret", "")
    with pytest.raises(DiscoveryError, match="provider_http_401"):
        SerpApi(client, "secret", "br", "pt", "", Mock()).search("q", 0)
    assert client.get.call_count == 1


def test_parse_empty_vs_malformed():
    assert SerpApi.parse(payload(), 0).results == []
    data = {
        "search_metadata": {"status": "Success"},
        "search_information": {"organic_results_state": "Fully empty"},
        "error": "no results",
    }
    assert SerpApi.parse(data, 0).results == []
    with pytest.raises(DiscoveryError):
        SerpApi.parse({"search_metadata": {"status": "Success"}}, 0)


def test_pagination_metadata():
    assert SerpApi.parse(payload(["https://example.org"], 17), 0).next_start == 17
    with pytest.raises(DiscoveryError):
        SerpApi.parse(payload(["https://example.org"], 0), 0)


def test_pipeline_resume_csv_and_provenance(tmp_path):
    store = Store(tmp_path / "state.sqlite", {})
    provider = Mock()
    provider.parse = SerpApi.parse
    provider.search.side_effect = [
        payload(["https://example.org/p", "https://example.org/q"]),
        payload(["https://example.org/p"]),
    ]
    resolver = Mock()
    resolver.resolve.return_value = Resolution("confirmed", "A; B", "https://example.org/")
    queries = ["gestão; estoque", "inventory"]
    run(queries, provider, resolver, store, 3)
    first_count = resolver.resolve.call_count
    run(queries, provider, resolver, store, 3)
    assert provider.search.call_count == 2
    assert resolver.resolve.call_count == first_count
    report = store.export(tmp_path)
    assert report["exported_rows"] == 2
    with (tmp_path / "companies.csv").open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f, delimiter=";"))
    assert rows[0] == ["CompanyName", "URL", "SearchQuery"]
    assert rows[1] == ["A; B", "https://example.org/", "gestão; estoque"]
    store.close()


def test_budget_persists_across_restart(tmp_path):
    store = Store(tmp_path / "state.sqlite", {})
    store.reserve(1)
    store.close()
    store = Store(tmp_path / "state.sqlite", {})
    with pytest.raises(BudgetExceeded):
        store.reserve(1)
    store.reserve(2)
    assert store.requests() == 2
    store.close()


def test_config_change_rejected(tmp_path):
    Store(tmp_path / "state.sqlite", {"country": "br"}).close()
    with pytest.raises(DiscoveryError, match="configuration"):
        Store(tmp_path / "state.sqlite", {"country": "us"})


def test_repeated_results_not_complete(tmp_path):
    store = Store(tmp_path / "state.sqlite", {})
    provider = Mock()
    provider.parse = SerpApi.parse
    provider.search.side_effect = [
        payload(["https://example.org"], 10),
        payload(["https://example.org"], 20),
    ]
    resolver = Mock()
    resolver.resolve.return_value = Resolution("pending", reason="ambiguous")
    run(["q"], provider, resolver, store, 3)
    report = store.export(tmp_path)
    assert report["queries"][0]["reason"] == "repeated_results"
    store.close()


def test_interrupt_keeps_search_for_resume(tmp_path):
    store = Store(tmp_path / "state.sqlite", {})
    provider = Mock()
    provider.parse = SerpApi.parse
    provider.search.return_value = payload(["https://example.org"])
    resolver = Mock()
    resolver.resolve.side_effect = KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        run(["q"], provider, resolver, store, 3)
    resolver.resolve.side_effect = None
    resolver.resolve.return_value = Resolution("confirmed", "Example", "https://example.org/")
    run(["q"], provider, resolver, store, 3)
    assert provider.search.call_count == 1
    assert store.export(tmp_path)["queries"][0]["status"] == "complete"
    store.close()


def test_limits_distinct_from_complete(tmp_path):
    store = Store(tmp_path / "state.sqlite", {})
    provider = Mock()
    provider.parse = SerpApi.parse
    provider.search.return_value = payload(["https://example.org"], 10)
    resolver = Mock()
    resolver.resolve.return_value = Resolution("pending")
    run(["q"], provider, resolver, store, 1)
    assert store.export(tmp_path)["queries"][0]["status"] == "limited"
    store.close()


def test_queries_bom_unicode_and_duplicates(tmp_path):
    file = tmp_path / "q.txt"
    file.write_text('gestão\n\ngestão\n"stock" -jobs\n', encoding="utf-8-sig")
    assert read_queries(file) == ["gestão", '"stock" -jobs']


def test_cli_missing_key_reports_failure_not_empty_success(tmp_path, monkeypatch):
    monkeypatch.delenv("SERPAPI_API_KEY", raising=False)
    q = tmp_path / "q.txt"
    q.write_text("inventory")
    assert main(["--queries", str(q), "--run-dir", str(tmp_path / "run")]) == 2
    report = json.loads((tmp_path / "run/report.json").read_text())
    assert report["queries"][0]["status"] == "failed"
    assert report["search_requests"] == 0


def test_budget_does_not_skip_later_cached_query(tmp_path):
    store = Store(tmp_path / "state.sqlite", {})
    store.save_page("cached", 0, payload())
    provider = Mock()
    provider.parse = SerpApi.parse
    provider.search.side_effect = BudgetExceeded("search_request_limit")
    run(["uncached", "cached"], provider, Mock(), store, 3)
    report = store.export(tmp_path)
    by_query = {q["query"]: q["status"] for q in report["queries"]}
    assert by_query == {"uncached": "not_started", "cached": "complete"}
    store.close()


def test_budget_marks_untouched_queries_and_repairs_legacy_status(tmp_path):
    store = Store(tmp_path / "state.sqlite", {})
    store.save_page("partial", 0, payload(next_start=10))
    store.progress("untouched", "incomplete", "search_request_limit")
    store.ensure_queries(["partial", "untouched", "never_attempted"])
    provider = Mock()
    provider.parse = SerpApi.parse
    provider.search.side_effect = BudgetExceeded("search_request_limit")
    run(["partial", "untouched", "never_attempted"], provider, Mock(), store, 2)
    statuses = {q["query"]: q["status"] for q in store.export(tmp_path)["queries"]}
    assert statuses == {
        "partial": "incomplete",
        "untouched": "not_started",
        "never_attempted": "not_started",
    }
    provider.search.assert_called_once()
    store.close()


def test_recheck_saved_pages_without_search_requests(tmp_path):
    store = Store(tmp_path / "state.sqlite", {})
    store.save_page("q", 0, payload(["https://example.org/product"]))
    store.save_resolution("q", "https://example.org/product", Resolution("pending"))
    provider = Mock()
    provider.parse = SerpApi.parse
    resolver = Mock()
    resolver.resolve.return_value = Resolution("confirmed", "Example", "https://example.org/")
    before = store.requests()
    recheck_sites(["q", "untouched"], provider, resolver, store)
    provider.search.assert_not_called()
    assert store.requests() == before
    assert store.export(tmp_path)["exported_rows"] == 1
    store.close()


def test_retry_pending_reuses_search(tmp_path):
    store = Store(tmp_path / "state.sqlite", {})
    store.save_page("q", 0, payload(["https://example.org"]))
    store.save_resolution("q", "https://example.org", Resolution("pending"))
    provider = Mock()
    provider.parse = SerpApi.parse
    resolver = Mock()
    resolver.resolve.return_value = Resolution("confirmed", "Example", "https://example.org/")
    run(["q"], provider, resolver, store, 3, retry_pending=True)
    provider.search.assert_not_called()
    assert store.export(tmp_path)["pending"] == 0
    store.close()


@pytest.mark.parametrize(
    "data",
    [
        {"search_metadata": None},
        {"search_metadata": {"status": "Success"}, "search_information": None},
        {"search_metadata": {"status": "Success"}, "organic_results": "invalid"},
        {"search_metadata": {"status": "Success"}, "organic_results": [{}]},
    ],
)
def test_malformed_provider_response_is_expected_failure(data):
    with pytest.raises(DiscoveryError):
        SerpApi.parse(data, 0)


def test_cli_end_to_end_with_synthetic_transport(tmp_path, monkeypatch):
    queries = tmp_path / "q.txt"
    queries.write_text('"estoque"\ninventory', encoding="utf-8")
    client = Mock()
    client.get.side_effect = [
        Response("", 200, json.dumps(payload(["https://example.org/product"])), "application/json"),
        web(html(), "https://example.org/product"),
        web(html()),
        Response("", 200, json.dumps(payload(["https://example.org/"])), "application/json"),
        web(html()),
    ]
    monkeypatch.setattr("keyword_searcher.cli.HttpClient", lambda: client)
    monkeypatch.setenv("SERPAPI_API_KEY", "fake-test-key")
    args = ["--queries", str(queries), "--run-dir", str(tmp_path / "run")]
    assert main(args) == 0
    calls = client.get.call_count
    assert main(args) == 0
    assert client.get.call_count == calls
    report = json.loads((tmp_path / "run/report.json").read_text())
    assert report["search_requests"] == 2
    assert report["exported_rows"] == 2


def test_http_redirect_checks_each_destination(monkeypatch):
    from email.message import Message
    from io import BytesIO

    from keyword_searcher.http import HttpClient

    headers = Message()
    headers["Location"] = "http://127.0.0.1/private"
    stream = BytesIO()
    stream.code = 302
    stream.headers = headers
    client = HttpClient()
    client.opener = Mock()
    client.opener.open.return_value = stream
    check = Mock(side_effect=[None, DiscoveryError("non_public_destination")])
    monkeypatch.setattr("keyword_searcher.http.require_public_url", check)
    with pytest.raises(DiscoveryError, match="non_public"):
        client.get("https://example.org/")
    assert client.opener.open.call_count == 1
    assert check.call_count == 2


def test_progress_counts_saved_results_and_incomplete_queries(tmp_path):
    from io import StringIO

    from keyword_searcher.progress import TerminalProgress

    store = Store(tmp_path / "state.sqlite", {})
    store.save_page("cached", 0, payload(["https://example.org/"]))
    store.save_resolution(
        "cached", "https://example.org/", Resolution("confirmed", "Example", "https://example.org/")
    )
    provider = Mock()
    provider.parse = SerpApi.parse
    provider.search.side_effect = BudgetExceeded("search_request_limit")
    stream = StringIO()
    with TerminalProgress(2, 2, stream=stream) as display:
        run(["uncached", "cached"], provider, Mock(), store, 2, progress=display)
    assert display.queries_done == 2
    assert display.confirmed == 1
    assert "Query 1/2: not_started" in stream.getvalue()
    assert "Link 1/1: stored example.org" in stream.getvalue()
    assert "\033[" not in stream.getvalue()
    store.close()


def test_live_progress_renders_colored_bars_and_no_progress_is_silent(monkeypatch):
    from io import StringIO

    from keyword_searcher.progress import TerminalProgress

    class Tty(StringIO):
        encoding = "utf-8"

        def isatty(self):
            return True

    monkeypatch.delenv("NO_COLOR", raising=False)
    output = Tty()
    with TerminalProgress(1, 1, stream=output) as display:
        display.begin_query(1, "inventory")
        display.begin_page(1, False)
        display.page_ready(1)
        display.begin_result("https://example.org/", False)
        display.end_result(Resolution("confirmed", "Example", "https://example.org/"))
        display.finish_query("complete")
    assert "\033[36m" in output.getvalue()
    assert "\033[34m" in output.getvalue()
    assert "\033[32m" in output.getvalue()
    assert "confirmed=1" in output.getvalue()
    assert output.getvalue().endswith("\n")

    silent = StringIO()
    with TerminalProgress(1, 1, stream=silent, enabled=False) as display:
        display.begin_query(1, "inventory")
        display.finish_query("complete")
    assert silent.getvalue() == ""
