import json
from unittest.mock import Mock

import pytest

from keyword_searcher import cli
from keyword_searcher.apify import ApifyGoogle
from keyword_searcher.apify_errors import SEARCH_ERROR
from keyword_searcher.export import export_results
from keyword_searcher.models import Resolution
from keyword_searcher.pipeline import run
from keyword_searcher.store import Store


def page(urls, *, next_page=False, status="complete", reason="", error=False):
    result = dict(
        provider="apify-google",
        organicResults=[{"url": url} for url in urls],
        has_next=next_page,
        end_status=status,
        end_reason=reason,
    )
    if error:
        result["search_error"] = True
    return result


def provider(store, queries):
    transport = Mock()
    for method in ("start", "completed", "items", "request"):
        getattr(transport, method).side_effect = AssertionError("No Apify requests allowed")
    search = ApifyGoogle(transport, store, queries, "br", "pt", 50, 2800)
    return search, transport


def sites():
    resolver = Mock()
    resolver.resolve.side_effect = lambda result: Resolution("confirmed", "Supplier", result.url)
    return resolver


@pytest.mark.parametrize("workers", [1, 8])
def test_duplicate_middle_pages_do_not_hide_later_new_urls_and_terminal_coverage(tmp_path, workers):
    store = Store(tmp_path / "state.sqlite", {})
    a, b = "https://a.example/", "https://b.example/"
    for index, urls in enumerate(([a], [a], [a], [b], [b])):
        store.save_page("q", index * 10, page(urls, next_page=index < 4))
    search, transport = provider(store, ["q"])
    resolver = sites()
    try:
        run(["q"], search, resolver, store, 50, workers=workers)
        assert {call.args[0].url for call in resolver.resolve.call_args_list} == {a, b}
        assert resolver.resolve.call_count == 2
        assert store.export_progress() == [dict(query="q", status="complete", reason="")]
        assert len(store.saved_pages("q")) == 5
        transport.start.assert_not_called()
        transport.completed.assert_not_called()
    finally:
        store.close()


@pytest.mark.parametrize("workers", [1, 8])
def test_repeated_terminal_page_preserves_incomplete_provider_evidence(tmp_path, workers):
    store = Store(tmp_path / "state.sqlite", {})
    url = "https://a.example/"
    for offset in (0, 10):
        store.save_page(
            "q",
            offset,
            page([url], next_page=offset == 0, status="incomplete", reason="apify_cost_limit"),
        )
    search, _ = provider(store, ["q"])
    try:
        run(["q"], search, sites(), store, 50, workers=workers)
        assert store.export_progress() == [
            dict(query="q", status="incomplete", reason="apify_cost_limit")
        ]
    finally:
        store.close()


@pytest.mark.parametrize("workers", [1, 8])
def test_duplicate_pages_do_not_hide_a_later_explicit_search_error(tmp_path, workers):
    store = Store(tmp_path / "state.sqlite", {})
    url = "https://a.example/"
    for offset in (0, 10):
        store.save_page(
            "q", offset, page([url], next_page=True, status="incomplete", reason=SEARCH_ERROR)
        )
    store.save_page("q", 20, page([], status="incomplete", reason=SEARCH_ERROR, error=True))
    search, _ = provider(store, ["q"])
    try:
        run(["q"], search, sites(), store, 50, workers=workers)
        assert store.export_progress() == [
            dict(query="q", status="incomplete", reason=SEARCH_ERROR)
        ]
    finally:
        store.close()


@pytest.mark.parametrize("workers", [1, 8])
def test_repeated_page_cannot_fetch_an_absent_successor(tmp_path, workers):
    store = Store(tmp_path / "state.sqlite", {})
    for offset in (0, 10):
        store.save_page("q", offset, page(["https://a.example/"], next_page=True))
    search, _ = provider(store, ["q"])
    search.plan_search = Mock(
        side_effect=AssertionError("Missing continuation must not be searched")
    )
    try:
        run(["q"], search, sites(), store, 50, workers=workers)
        assert store.export_progress() == [
            dict(query="q", status="incomplete", reason="repeated_results")
        ]
        search.plan_search.assert_not_called()
    finally:
        store.close()


@pytest.mark.parametrize("workers", [1, 8])
def test_saved_only_missing_pages_never_contact_apify_or_modify_open_run(tmp_path, workers):
    store = Store(tmp_path / "state.sqlite", {})
    queries = ["missing", "gap", "cached"]
    store.save_page("gap", 0, page(["https://gap.example/"], next_page=True))
    store.save_page("cached", 0, page(["https://cached.example/"]))
    batch = store.apify_plan_batch(["missing"], 50, 2800)
    store.apify_set_run(batch, "existingRun")
    original_batch = store.apify_open_batch()
    search, transport = provider(store, queries)
    search.plan_search = Mock(side_effect=AssertionError("Saved-only must not plan a search"))
    try:
        run(queries, search, sites(), store, 50, workers=workers, saved_searches_only=True)
        assert {
            row["query"]: (row["status"], row["reason"]) for row in store.export_progress()
        } == {
            "missing": ("not_started", "saved_search_page_missing"),
            "gap": ("incomplete", "saved_search_page_missing"),
            "cached": ("complete", ""),
        }
        assert store.apify_open_batch() == original_batch
        assert store.apify_reserved_pages() == 50
        assert len(list(store.export_resolutions())) == 2
        search.plan_search.assert_not_called()
        transport.start.assert_not_called()
    finally:
        store.close()


@pytest.mark.parametrize("workers", [1, 8])
def test_aps_like_completed_work_repairs_seven_statuses_without_revisiting_any_site(
    tmp_path, workers
):
    store = Store(tmp_path / "state.sqlite", {})
    queries = [f"repeated{i}" for i in range(7)] + ["failedPage"]
    for i, query in enumerate(queries):
        url = f"https://site{i}.example/"
        store.save_page(
            query,
            0,
            page(
                [url],
                next_page=True,
                status="incomplete" if query == "failedPage" else "complete",
                reason=SEARCH_ERROR if query == "failedPage" else "",
            ),
        )
        store.save_resolution(query, url, Resolution("confirmed", f"Supplier {i}", url))
        if query == "failedPage":
            store.save_page(
                query, 10, page([], status="incomplete", reason=SEARCH_ERROR, error=True)
            )
        else:
            store.save_page(query, 10, page([url]))
        store.progress(
            query, "incomplete", SEARCH_ERROR if query == "failedPage" else "repeated_results"
        )
    original_pages = {q: store.saved_pages(q) for q in queries}
    original_resolutions = list(store.export_resolutions())
    search, transport = provider(store, queries)
    resolver = Mock(
        resolve=Mock(side_effect=AssertionError("All saved URLs already have decisions"))
    )
    try:
        run(queries, search, resolver, store, 50, workers=workers, saved_searches_only=True)
        statuses = store.export_progress()
        assert sum(row["status"] == "complete" for row in statuses) == 7
        assert (
            next(row for row in statuses if row["query"] == "failedPage")["reason"] == SEARCH_ERROR
        )
        assert list(store.export_resolutions()) == original_resolutions
        assert {q: store.saved_pages(q) for q in queries} == original_pages
        resolver.resolve.assert_not_called()
        transport.start.assert_not_called()
    finally:
        store.close()


def test_cli_saved_only_needs_no_token_and_changes_only_progress_and_regenerated_outputs(
    tmp_path, monkeypatch
):
    queries = tmp_path / "queries.txt"
    queries.write_text("q\n", encoding="utf-8")
    run_dir, output = tmp_path / "run", tmp_path / "output"
    config = dict(queries=["q"], country="br", language="pt", location="", max_pages=50)
    store = Store(run_dir / "state.sqlite", config)
    url = "https://a.example/"
    store.save_page("q", 0, page([url], next_page=True))
    store.save_page("q", 10, page([url]))
    store.save_resolution("q", url, Resolution("confirmed", "Supplier", url))
    store.progress("q", "incomplete", "repeated_results")
    before = export_results(store, output)
    original_handoff = (output / "handoff.json").read_bytes()
    store.close()
    monkeypatch.delenv("APIFY_API_TOKEN", raising=False)
    monkeypatch.setattr(
        "keyword_searcher.apify.ApifyTransport.request", Mock(side_effect=AssertionError("No API"))
    )
    resolver = Mock(resolve=Mock(side_effect=AssertionError("No revisits")))
    monkeypatch.setattr(cli, "WebsiteResolver", Mock(return_value=resolver))
    assert (
        cli.main(
            [
                "--queries",
                str(queries),
                "--run-dir",
                str(run_dir),
                "--output-dir",
                str(output),
                "--max-pages",
                "50",
                "--workers",
                "8",
                "--saved-searches-only",
                "--no-progress",
            ]
        )
        == 0
    )
    after = json.loads((output / "report.json").read_text())
    assert before["query_status_counts"] == {"incomplete": 1}
    assert after["query_status_counts"] == {"complete": 1}
    assert (output / "handoff.json").read_bytes() == original_handoff
    assert after["handoff"]["partial"] is False


@pytest.mark.parametrize(
    "recovery", [[], ["--apify-abandon-batch"], ["--apify-recover-run-id", "existingRun"]]
)
def test_cli_saved_only_refuses_new_state_or_run_recovery_without_creating_files(
    tmp_path, recovery
):
    queries = tmp_path / "queries.txt"
    queries.write_text("q\n", encoding="utf-8")
    run_dir = tmp_path / "missing"
    with pytest.raises(SystemExit) as caught:
        cli.main(
            [
                "--queries",
                str(queries),
                "--run-dir",
                str(run_dir),
                "--output-dir",
                str(tmp_path / "out"),
                "--saved-searches-only",
                *recovery,
            ]
        )
    assert caught.value.code == 1
    assert not run_dir.exists()
