import threading
from io import StringIO
from unittest.mock import Mock

import pytest

from keyword_searcher.apify import ApifyGoogle
from keyword_searcher.models import DiscoveryError, Resolution
from keyword_searcher.pipeline import recheck_sites, run
from keyword_searcher.progress import TerminalProgress
from keyword_searcher.store import Store
from keyword_searcher.workers import ResolutionWorkers, WorkerFailure


def page(urls, has_next=False):
    return dict(
        provider="apify-google",
        organicResults=[{"url": url} for url in urls],
        has_next=has_next,
        end_status="complete",
        end_reason="",
    )


def provider():
    result = Mock()
    result.parse = ApifyGoogle.parse
    result.search.side_effect = AssertionError("Saved corpus must not start a search")
    return result


@pytest.mark.parametrize("mode", ["search", "recheck"])
@pytest.mark.parametrize("workers,query_count,page_count", [(20, 4, 5), (100, 10, 10)])
def test_workers_overlap_across_pages_and_queries(tmp_path, mode, workers, query_count, page_count):
    store = Store(tmp_path / "state.sqlite", {})
    queries = [f"q{q}" for q in range(query_count)]
    for q, query in enumerate(queries):
        for p in range(page_count):
            store.save_page(
                query, p * 10, page([f"https://host{q}-{p}.example/"], has_next=p < page_count - 1)
            )
        store.progress(query, "limited", "max_pages")
    original_progress = store.export_progress()
    barrier = threading.Barrier(workers)
    lock = threading.Lock()
    active = peak = 0
    thread_ids = set()

    def resolve(result):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(active, peak)
            thread_ids.add(threading.get_ident())
        try:
            barrier.wait(timeout=10)
            return Resolution("confirmed", result.url, result.url)
        finally:
            with lock:
                active -= 1

    sites = Mock(resolve=resolve)
    search = provider()
    try:
        with TerminalProgress(query_count, page_count, enabled=False) as display:
            if mode == "search":
                run(queries, search, sites, store, page_count, progress=display, workers=workers)
                assert all(q["status"] == "complete" for q in store.export_progress())
            else:
                recheck_sites(queries, search, sites, store, display, workers=workers)
                assert store.export_progress() == original_progress
        assert peak == workers and len(thread_ids) == workers
        assert len(list(store.export_resolutions())) == workers
        assert display.queries_done == query_count
        assert display.pages_done == workers
        assert display.results_total == display.results_done == workers
        assert not display.active_workers and display.queued_sites == 0
        search.search.assert_not_called()
    finally:
        store.close()


def test_later_query_finishes_before_earlier_slow_page_and_writes_stay_central(tmp_path):
    slow, fast, later = [f"https://{host}.example/" for host in ("slow", "fast", "later")]
    store = Store(tmp_path / "state.sqlite", {})
    store.save_page("first", 0, page([slow], True))
    store.save_page("first", 10, page([fast]))
    store.save_page("second", 0, page([later]))
    release, entered = threading.Event(), threading.Event()
    writes = []
    main = threading.get_ident()
    original_save = store.save_resolution

    def resolve(result):
        if result.url == slow:
            entered.set()
            assert release.wait(5), "Later pages/queries never entered the pool"
        else:
            assert entered.wait(5)
        return Resolution("confirmed", result.url, result.url)

    def save(query, url, resolution):
        assert threading.get_ident() == main
        if url != slow:
            assert {q["query"]: q["status"] for q in store.export_progress()}["first"] == "running"
        original_save(query, url, resolution)
        writes.append(url)
        if url == later:
            release.set()

    store.save_resolution = save
    try:
        run(["first", "second"], provider(), Mock(resolve=resolve), store, 2, workers=2)
        assert writes == [fast, later, slow]
        assert all(q["status"] == "complete" for q in store.export_progress())
    finally:
        release.set()
        store.close()


def test_host_waiting_jobs_do_not_occupy_slots_needed_by_other_hosts(tmp_path):
    urls = [f"https://a.example/{i}" for i in range(4)] + ["https://b.example/"]
    store = Store(tmp_path / "state.sqlite", {})
    for index, url in enumerate(urls):
        store.save_page("q", index * 10, page([url], index < len(urls) - 1))
    release = threading.Event()
    lock = threading.Lock()
    active_a = peak_a = 0
    original_save = store.save_resolution

    def resolve(result):
        nonlocal active_a, peak_a
        is_a = "a.example" in result.url
        if is_a:
            with lock:
                active_a += 1
                peak_a = max(peak_a, active_a)
        try:
            if result.url.endswith("/0"):
                assert release.wait(5), "Same-host jobs occupied all slots"
            return Resolution("confirmed", result.url, result.url)
        finally:
            if is_a:
                with lock:
                    active_a -= 1

    def save(query, url, resolution):
        original_save(query, url, resolution)
        if "b.example" in url:
            release.set()

    store.save_resolution = save
    try:
        run(["q"], provider(), Mock(resolve=resolve), store, 5, workers=2)
        assert release.is_set() and peak_a == 1
        assert len(list(store.export_resolutions())) == 5
    finally:
        release.set()
        store.close()


def test_interrupt_marks_each_unfinished_query_and_resume_reuses_completed_sites(
    tmp_path, monkeypatch
):
    store = Store(tmp_path / "state.sqlite", {})
    queries = [f"q{i}" for i in range(4)]
    urls = [f"https://host{i}.example/" for i in range(4)]
    for query, url in zip(queries, urls):
        store.save_page(query, 0, page([url]))
    stop, entered = threading.Event(), threading.Event()
    fed = threading.Event()
    close_query = ResolutionWorkers.close_query

    def close(self, query, *args, **kwargs):
        close_query(self, query, *args, **kwargs)
        if query == "q3":
            fed.set()

    monkeypatch.setattr(ResolutionWorkers, "close_query", close)

    def resolve(result):
        if result.url == urls[0]:
            assert entered.wait(5)
            assert fed.wait(5)
            raise KeyboardInterrupt()
        entered.set()
        assert stop.wait(5)
        return Resolution("confirmed", "Finished", result.url)

    search = provider()
    try:
        with pytest.raises(KeyboardInterrupt):
            run(queries, search, Mock(resolve=resolve, cancel=stop.set), store, 1, workers=2)
        states = {q["query"]: q["status"] for q in store.export_progress()}
        assert states == {
            "q0": "interrupted",
            "q1": "complete",
            "q2": "interrupted",
            "q3": "interrupted",
        }
        assert len(list(store.export_resolutions())) == 1
        resumed = Mock()
        resumed.resolve.side_effect = lambda result: Resolution("confirmed", "Finished", result.url)
        run(queries, search, resumed, store, 1, workers=4)
        assert resumed.resolve.call_count == 3
        assert all(q["status"] == "complete" for q in store.export_progress())
        search.search.assert_not_called()
    finally:
        stop.set()
        store.close()


def test_worker_failure_is_attributed_to_original_query(tmp_path):
    store = Store(tmp_path / "state.sqlite", {})
    for index in range(3):
        store.save_page(f"q{index}", 0, page([f"https://host{index}.example/"]))
    stop, entered = threading.Event(), threading.Event()

    def resolve(result):
        if "host0" in result.url:
            assert entered.wait(5)
            raise DiscoveryError("synthetic resolver failure")
        entered.set()
        assert stop.wait(5)
        return Resolution("confirmed", "Finished", result.url)

    try:
        with pytest.raises(WorkerFailure):
            run(
                ["q0", "q1", "q2"],
                provider(),
                Mock(resolve=resolve, cancel=stop.set),
                store,
                1,
                workers=2,
            )
        states = {q["query"]: q for q in store.export_progress()}
        assert states["q0"]["status"] == "failed"
        assert states["q0"]["reason"] == "site_worker_failed"
        assert states["q1"]["status"] == "complete"
        assert states["q2"]["status"] != "complete"
    finally:
        stop.set()
        store.close()


def test_pending_decisions_are_persisted_before_waiting_for_next_actor(tmp_path):
    store = Store(tmp_path / "state.sqlite", {})
    store.save_page("first", 0, page(["https://first.example/"]))
    sites = Mock()
    sites.resolve.side_effect = lambda result: Resolution("confirmed", "Finished", result.url)
    search = provider()
    main = threading.get_ident()

    def start(query, offset):
        assert threading.get_ident() == main
        assert query == "next" and offset == 0
        assert store.resolution("first", "https://first.example/") is not None
        assert store.export_progress()[0]["status"] == "complete"
        return page(["https://next.example/"])

    search.search.side_effect = start
    try:
        run(["first", "next"], search, sites, store, 1, workers=2)
        search.search.assert_called_once()
    finally:
        store.close()


def test_global_progress_does_not_reset_between_pages_or_queries():
    stream = StringIO()
    with TerminalProgress(2, 3, stream=stream) as progress:
        progress.configure_workers(100, global_mode=True)
        progress.begin_query(1, "first")
        progress.page_ready(7)
        progress.end_result(Resolution("pending"))
        progress.begin_query(2, "second")
        progress.page_ready(9)
        progress.queue_changed(12, ready_hosts=7)
        progress.finish_query("complete", index=1)
        assert progress.pages_done == 2
        assert progress.results_total == 16 and progress.results_done == 1
        assert progress.queries_done == 1
        assert "queued=12" in stream.getvalue()
        assert "ready_hosts=7" in stream.getvalue()
        assert "Query 1/2: complete" in stream.getvalue()
