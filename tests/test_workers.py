import json
import threading
from concurrent.futures import CancelledError, ThreadPoolExecutor
from io import StringIO
from unittest.mock import Mock

import pytest

from keyword_searcher import cli
from keyword_searcher import workers as worker_module
from keyword_searcher.export import export_results
from keyword_searcher.http import HttpClient, Response
from keyword_searcher.models import DiscoveryError, Resolution
from keyword_searcher.pipeline import recheck_sites, run
from keyword_searcher.progress import TerminalProgress
from keyword_searcher.resolve import WebsiteResolver
from keyword_searcher.search import parse_legacy
from keyword_searcher.store import Store
from keyword_searcher.workers import ResolutionWorkers, interleave_hosts


def page(urls):
    return {
        "search_metadata": {"status": "Success"},
        "organic_results": [{"link": url, "title": "Synthetic"} for url in urls],
    }


def provider_for(urls):
    provider = Mock()
    provider.parse = parse_legacy
    provider.search.return_value = page(urls)
    return provider


def site(url):
    name = url.split("//")[1].split(".")[0].title()
    return Response(
        url,
        200,
        f'<meta property="og:site_name" content="{name}"><a href="/products">Our products</a>',
        "text/html",
    )


def test_workers_overlap_and_save_fast_site_before_slow_site_on_main_thread(tmp_path):
    slow = "https://slow.example/"
    fast = "https://fast.example/"
    entered = threading.Event()
    release = threading.Event()
    main_thread = threading.get_ident()
    calls, writes = [], []

    def resolve(result):
        calls.append(threading.get_ident())
        if result.url == slow:
            entered.set()
            assert release.wait(3), "Fast site was blocked by the slow site"
        else:
            assert entered.wait(3)
        return Resolution("confirmed", result.url.split("//")[1], result.url)

    store = Store(tmp_path / "run/state.sqlite", {})
    original_save = store.save_resolution

    def save(query, url, resolution):
        assert threading.get_ident() == main_thread
        writes.append(url)
        original_save(query, url, resolution)
        if url == fast:
            release.set()

    store.save_resolution = save
    try:
        run(["q"], provider_for([slow, fast]), Mock(resolve=resolve), store, 1, workers=2)
        assert writes == [fast, slow]
        assert main_thread not in calls and len(set(calls)) == 2
    finally:
        release.set()
        store.close()


def test_worker_queue_is_bounded_and_cancels_unsubmitted_sites(tmp_path, monkeypatch):
    release = threading.Event()
    started = threading.Barrier(3)
    submitted = []
    real_executor = ThreadPoolExecutor

    class InstrumentedExecutor(real_executor):
        def submit(self, fn, result):
            submitted.append(result.url)
            return super().submit(fn, result)

    def resolve(result):
        started.wait(timeout=3)
        assert release.wait(3)
        return Resolution("pending")

    original_wait = worker_module.wait
    interrupted = False

    def interrupt_wait(futures, *args, **kwargs):
        nonlocal interrupted
        if not interrupted:
            interrupted = True
            started.wait(timeout=3)
            raise KeyboardInterrupt()
        return original_wait(futures, *args, **kwargs)

    monkeypatch.setattr("keyword_searcher.workers.ThreadPoolExecutor", InstrumentedExecutor)
    monkeypatch.setattr("keyword_searcher.workers.wait", interrupt_wait)
    store = Store(tmp_path / "run/state.sqlite", {})
    try:
        urls = [f"https://site{index}.example/" for index in range(50)]
        with pytest.raises(KeyboardInterrupt):
            run(
                ["q"],
                provider_for(urls),
                Mock(resolve=resolve, cancel=release.set),
                store,
                1,
                workers=2,
            )
        assert submitted == urls[:2]
        assert len(list(store.export_resolutions())) == 2
    finally:
        release.set()
        store.close()


def test_sequential_and_parallel_keep_identical_decisions_exports_and_resume(tmp_path):
    urls = ["https://acme.example/p", "https://other.example/", "https://linkedin.com/test"]
    outputs = []
    for workers in (1, 8):
        store = Store(tmp_path / f"run{workers}/state.sqlite", {})
        http = Mock(get=Mock(side_effect=site))
        resolver = WebsiteResolver(http)
        provider = provider_for(urls)
        output = tmp_path / f"output{workers}"
        try:
            run(["q", "second"], provider, resolver, store, 1, workers=workers)
            decisions = list(store.export_resolutions())
            assert len(decisions) == 6
            assert sum(r.status == "confirmed" for _q, _url, r in decisions) == 4
            export_results(store, output)
            outputs.append(
                (
                    decisions,
                    {
                        name: (output / name).read_bytes()
                        for name in (
                            "companies.csv",
                            "pending.json",
                            "handoff.json",
                            "handoff.metadata.json",
                        )
                    },
                )
            )
            calls = http.get.call_count
            run(["q", "second"], provider, resolver, store, 1, workers=workers)
            assert http.get.call_count == calls
            assert provider.search.call_count == 2
        finally:
            store.close()
    assert outputs[0] == outputs[1]


def test_interrupt_drains_completed_work_and_resumes_saved_page(tmp_path):
    stop = threading.Event()
    active = threading.Event()
    urls = ["https://interrupt.example/", "https://late.example/", "https://next.example/"]

    def resolve(result):
        if result.url == urls[0]:
            assert active.wait(3)
            raise KeyboardInterrupt()
        active.set()
        assert stop.wait(3)
        return Resolution("confirmed", "Late Supplier", result.url)

    resolver = Mock(resolve=resolve, cancel=stop.set)
    provider = provider_for(urls)
    store = Store(tmp_path / "run/state.sqlite", {})
    try:
        with pytest.raises(KeyboardInterrupt):
            run(["q"], provider, resolver, store, 1, workers=2)
        assert stop.is_set()
        assert store.resolution("q", urls[1]).status == "confirmed"
        assert store.resolution("q", urls[2]) is None
        assert store.export_progress()[0]["status"] == "interrupted"
        export_results(store, tmp_path / "partial", interrupted=True)
        metadata = json.loads((tmp_path / "partial/handoff.metadata.json").read_text())
        assert metadata["coverage"]["interrupted"]
        finished = Mock()
        finished.resolve.side_effect = lambda r: Resolution("confirmed", "Finished", r.url)
        run(["q"], provider, finished, store, 1, workers=2)
        provider.search.assert_called_once()
        assert finished.resolve.call_count == 2
        assert store.export_progress()[0]["status"] == "complete"
    finally:
        stop.set()
        store.close()


@pytest.mark.parametrize("workers", [1, 8])
def test_retry_pending_and_recheck_preserve_search_state(tmp_path, workers):
    urls = ["https://old.example/", "https://pending.example/"]
    provider = provider_for(urls)
    store = Store(tmp_path / "run/state.sqlite", {})
    store.save_page("q", 0, page(urls))
    store.save_resolution("q", urls[0], Resolution("confirmed", "Old", urls[0]))
    store.save_resolution("q", urls[1], Resolution("pending", reason="network_failure"))
    resolver = Mock()
    resolver.resolve.side_effect = lambda r: Resolution("confirmed", "New", r.url)
    try:
        run(["q"], provider, resolver, store, 1, retry_pending=True, workers=workers)
        assert resolver.resolve.call_count == 1
        assert store.resolution("q", urls[0]).name == "Old"
        progress_before = store.export_progress()
        recheck_sites(["q"], provider, resolver, store, workers=workers)
        assert resolver.resolve.call_count == 3
        assert store.resolution("q", urls[0]).name == "New"
        assert store.export_progress() == progress_before
        provider.search.assert_not_called()
    finally:
        store.close()


@pytest.mark.parametrize("failure", [False, True])
def test_simultaneous_cache_misses_fetch_once_and_share_errors(failure):
    barrier = threading.Barrier(8)
    fetched = threading.Event()
    release = threading.Event()
    url = "https://shared.example/"

    def get(url):
        fetched.set()
        assert release.wait(3)
        if failure:
            raise DiscoveryError("network_failure")
        return site(url)

    http = Mock(get=Mock(side_effect=get))
    resolver = WebsiteResolver(http)

    def read():
        barrier.wait(timeout=3)
        try:
            return resolver.page(url)[0]
        except DiscoveryError as exc:
            return str(exc)

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(read) for _ in range(8)]
        try:
            assert fetched.wait(3)
        finally:
            release.set()
        assert [future.result(timeout=3) for future in futures] == [
            "network_failure" if failure else url
        ] * 8
    http.get.assert_called_once()
    assert resolver._loading == {}
    if failure:
        assert resolver.cache[url] == "network_failure"


def test_http_serializes_www_aliases_but_other_hosts_overlap():
    client = HttpClient()
    entered = threading.Event()
    other = threading.Event()
    release = threading.Event()
    alias = threading.Event()

    def get_once(url):
        if url == "https://a.example/":
            entered.set()
            assert release.wait(3)
        elif "www.a.example" in url:
            alias.set()
        else:
            other.set()
        return site(url)

    client._get_once = get_once
    with ThreadPoolExecutor(max_workers=3) as pool:
        first = pool.submit(client.get, "https://a.example/")
        try:
            assert entered.wait(3)
            second = pool.submit(client.get, "http://www.a.example/path")
            third = pool.submit(client.get, "https://b.example/")
            assert other.wait(3), "Different hosts must overlap"
            assert not alias.is_set(), "www alias must share the host limit"
        finally:
            release.set()
        for future in (first, second, third):
            future.result(timeout=3)
    assert alias.is_set() and client._hosts == set()


def test_cross_host_redirects_release_previous_slot_without_deadlock():
    client = HttpClient()
    barrier = threading.Barrier(2)

    def get_once(url):
        if url.endswith("/end"):
            return site(url)
        barrier.wait(timeout=3)
        return "https://b.example/end" if "a.example" in url else "https://a.example/end"

    client._get_once = get_once
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(client.get, f"https://{host}.example/") for host in ("a", "b")]
        assert {future.result(timeout=3).url for future in futures} == {
            "https://a.example/end",
            "https://b.example/end",
        }
    assert client._hosts == set()


def test_cancel_wakes_host_waiters_and_blocks_followup_requests():
    client = HttpClient()
    with client.host_slot("https://a.example/"):
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(client.get, "https://a.example/next")
            client.cancel()
            with pytest.raises(CancelledError):
                future.result(timeout=3)
    with pytest.raises(CancelledError):
        client.get("https://b.example/")
    assert client._hosts == set()


def test_http_openers_are_isolated_per_worker():
    client = HttpClient()
    barrier = threading.Barrier(2)

    def opener():
        value = client.opener
        barrier.wait(timeout=3)
        assert value is client.opener
        return value

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(opener) for _ in range(2)]
        assert futures[0].result(timeout=3) is not futures[1].result(timeout=3)


def test_worker_count_changes_without_changing_run_identity(tmp_path, monkeypatch):
    queries = tmp_path / "queries.txt"
    queries.write_text("q", encoding="utf-8")
    observed = []

    def process(*args, **kwargs):
        observed.append(args[-1])
        args[3].progress("q", "complete")

    monkeypatch.setattr(cli, "run", process)
    monkeypatch.setattr(cli, "recheck_sites", process)
    args = [
        "--queries",
        str(queries),
        "--run-dir",
        str(tmp_path / "run"),
        "--output-dir",
        str(tmp_path / "output"),
        "--no-progress",
    ]
    assert cli.main(args) == 0
    assert cli.main(args + ["--workers", "1"]) == 0
    assert cli.main(args + ["--recheck-sites"]) == 0
    assert observed == [8, 1, 8]


def test_invalid_pool_does_not_create_threads():
    with pytest.raises(ValueError):
        ResolutionWorkers(Mock(), workers=0)


def test_host_interleaving_prevents_one_host_filling_pool():
    urls = [
        "https://a.example/1",
        "https://www.a.example/2",
        "https://a.example/3",
        "https://b.example/1",
        "https://c.example/1",
    ]
    results = parse_legacy(page(urls), 0).results
    assert [result.url for result in interleave_hosts(results)] == [
        urls[0],
        urls[3],
        urls[4],
        urls[1],
        urls[2],
    ]


def test_progress_distinguishes_active_sites_and_total_elapsed(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr("keyword_searcher.progress.time.monotonic", lambda: clock[0])
    stream = StringIO()
    with TerminalProgress(1, 1, stream=stream) as display:
        display.configure_workers(8)
        clock[0] = 110
        token = display.begin_worker("https://example.org/")
        clock[0] = 115
        display._draw()
        assert "total_elapsed=15s" in stream.getvalue()
        assert "site_elapsed=5s" in stream.getvalue()
        assert "Workers 1/8 active" in stream.getvalue()
        display.end_worker(token)
        display.end_result(Resolution("pending"))
        assert not display.active_workers
        assert "sites_done=1" in stream.getvalue()


@pytest.mark.parametrize("limit", ["0", "-1"])
def test_cli_rejects_invalid_workers_before_creating_state(tmp_path, limit):
    with pytest.raises(SystemExit) as exc:
        cli.main(
            [
                "--queries",
                str(tmp_path / "q.txt"),
                "--run-dir",
                str(tmp_path / "run"),
                "--output-dir",
                str(tmp_path / "out"),
                "--workers",
                limit,
            ]
        )
    assert exc.value.code == 2
    assert not (tmp_path / "run").exists()
