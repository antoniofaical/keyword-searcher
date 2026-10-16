import threading
from concurrent.futures import CancelledError
from io import StringIO
from unittest.mock import Mock

import pytest

from keyword_searcher.apify import ApifyGoogle, ApifyRecoveryRequired, ApifyTransport
from keyword_searcher.models import DiscoveryError, Resolution
from keyword_searcher.pipeline import recheck_sites, run
from keyword_searcher.progress import TerminalProgress
from keyword_searcher.store import Store
from keyword_searcher.workers import ResolutionWorkers


def saved(urls, has_next=False):
    return dict(
        provider="apify-google",
        organicResults=[{"url": url, "title": url} for url in urls],
        has_next=has_next,
        end_status="complete",
        end_reason="",
    )


def record(query):
    return {
        "searchQuery": {"term": query, "page": 1},
        "organicResults": [{"url": f"https://{query}.example/"}],
    }


def evidence(run_id="run123", count=1):
    return dict(
        run_id=run_id,
        dataset_id=run_id,
        status="SUCCEEDED",
        exit_code=0,
        cost_usd=0.01,
        cost_limit_usd=1,
        pending_requests=0,
        handled_requests=count,
    )


def provider(store, queries, transport, *, limit=100, batch_size=20):
    return ApifyGoogle(transport, store, queries, "br", "pt", 1, limit, batch_size, 7)


def confirmed(result):
    return Resolution("confirmed", result.title, result.url)


def test_next_batch_and_new_query_run_before_previous_slow_site_finishes(tmp_path):
    store = Store(tmp_path / "state.sqlite", {})
    store.save_page("old", 0, saved(["https://old.example/"]))
    old_entered, new_saved, run_saved = (threading.Event() for _ in range(3))
    main = threading.get_ident()
    transport = Mock()

    def start(*args):
        assert threading.get_ident() != main
        assert old_entered.wait(5), "Previous sites were drained before the next batch"
        assert not new_saved.is_set()
        assert args == (["new"], 1, "br", "pt", 7)
        return "run123"

    def completed(run_id):
        assert threading.get_ident() != main
        assert run_saved.is_set(), "Run ID must be durable before remote polling"
        return evidence(run_id)

    transport.start.side_effect = start
    transport.completed.side_effect = completed
    transport.items.return_value = [record("new")]
    original_set_run, original_save = store.apify_set_run, store.save_resolution

    def set_run(*args):
        assert threading.get_ident() == main
        original_set_run(*args)
        run_saved.set()

    def save(query, url, resolution):
        assert threading.get_ident() == main
        original_save(query, url, resolution)
        if query == "new":
            new_saved.set()

    store.apify_set_run, store.save_resolution = set_run, save

    def resolve(result):
        if "old.example" in result.url:
            old_entered.set()
            assert new_saved.wait(5), "New query was held behind the old query"
        return confirmed(result)

    try:
        with TerminalProgress(2, 1, stream=StringIO()) as display:
            run(
                ["old", "new"],
                provider(store, ["old", "new"], transport),
                Mock(resolve=resolve),
                store,
                1,
                workers=2,
                progress=display,
            )
        assert new_saved.is_set()
        assert display.results_done == 2 and display.search_activity == "idle"
        assert {row["query"]: row["status"] for row in store.export_progress()} == {
            "old": "complete",
            "new": "limited",
        }
        transport.start.assert_called_once()
    finally:
        new_saved.set()
        store.close()


def test_remote_wait_does_not_hide_later_saved_queries_or_delay_their_persistence(tmp_path):
    store = Store(tmp_path / "state.sqlite", {})
    store.save_page("cached", 0, saved(["https://cached.example/"]))
    persisted = threading.Event()
    transport = Mock(start=Mock(return_value="run123"))

    def completed(run_id):
        assert persisted.wait(5), "Coordinator blocked on Apify instead of processing saved work"
        return evidence(run_id)

    transport.completed.side_effect = completed
    transport.items.return_value = [record("missing")]
    original_save = store.save_resolution

    def save(*args):
        original_save(*args)
        if args[0] == "cached":
            persisted.set()

    store.save_resolution = save
    try:
        run(
            ["missing", "cached"],
            provider(store, ["missing", "cached"], transport),
            Mock(resolve=confirmed),
            store,
            1,
            workers=2,
        )
        assert len(list(store.export_resolutions())) == 2
    finally:
        persisted.set()
        store.close()


@pytest.mark.parametrize("mode", ["search", "recheck"])
def test_same_host_backlog_beyond_old_queue_limit_cannot_hide_next_query(tmp_path, mode):
    store = Store(tmp_path / "state.sqlite", {})
    store.save_page("blocked", 0, saved([f"https://blocked.example/{i}" for i in range(40)]))
    store.save_page("later", 0, saved(["https://later.example/"]))
    release = threading.Event()
    lock = threading.Lock()
    active = peak = 0

    def resolve(result):
        nonlocal active, peak
        is_blocked = "blocked.example" in result.url
        if is_blocked:
            with lock:
                active += 1
                peak = max(peak, active)
        try:
            if result.url.endswith("/0"):
                assert release.wait(5), "Busy host backlog hid a different query"
            if "later.example" in result.url:
                release.set()
            return confirmed(result)
        finally:
            if is_blocked:
                with lock:
                    active -= 1

    transport = Mock()
    search = provider(store, ["blocked", "later"], transport)
    try:
        if mode == "search":
            run(["blocked", "later"], search, Mock(resolve=resolve), store, 1, workers=2)
        else:
            recheck_sites(["blocked", "later"], search, Mock(resolve=resolve), store, workers=2)
        assert release.is_set() and peak == 1
        assert len(list(store.export_resolutions())) == 41
        transport.start.assert_not_called()
    finally:
        release.set()
        store.close()


def test_100_workers_can_reach_later_query_behind_500_same_host_jobs(tmp_path):
    store = Store(tmp_path / "state.sqlite", {})
    queries = ["blocked", "others"]
    store.save_page("blocked", 0, saved([f"https://blocked.example/{i}" for i in range(500)]))
    store.save_page("others", 0, saved([f"https://host{i}.example/" for i in range(99)]))
    barrier = threading.Barrier(100)
    lock = threading.Lock()
    active = peak = 0

    def resolve(result):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        try:
            if "blocked.example" not in result.url or result.url.endswith("/0"):
                barrier.wait(timeout=10)
            return confirmed(result)
        finally:
            with lock:
                active -= 1

    try:
        run(queries, provider(store, queries, Mock()), Mock(resolve=resolve), store, 1, workers=100)
        assert peak == 100
        assert len(list(store.export_resolutions())) == 599
    finally:
        barrier.abort()
        store.close()


def test_batches_remain_serial_and_preserve_budget_caps_and_exports(tmp_path):
    outputs = []
    for workers in (1, 4):
        store = Store(tmp_path / f"state{workers}.sqlite", {})
        transport = Mock()
        queries = ["one", "two", "three"]
        active = None
        starts = []

        def start(batch, pages, country, language, cost):
            nonlocal active
            assert active is None, "More than one remote batch in flight"
            active = batch[0]
            starts.append((batch, pages, cost))
            return active

        def completed(run_id):
            assert active == run_id
            return evidence(run_id)

        def items(run_id):
            nonlocal active
            assert active == run_id
            result = [record(run_id)]
            active = None
            return result

        transport.start.side_effect, transport.completed.side_effect = start, completed
        transport.items.side_effect = items
        search = provider(store, queries, transport, limit=2, batch_size=1)
        try:
            run(queries, search, Mock(resolve=confirmed), store, 1, workers=workers)
            outputs.append(list(store.export_resolutions()))
            assert starts == [(["one"], 1, 7), (["two"], 1, 7)]
            assert store.apify_reserved_pages() == 2
            assert {row["query"]: row["status"] for row in store.export_progress()} == {
                "one": "limited",
                "two": "limited",
                "three": "not_started",
            }
        finally:
            store.close()
    assert outputs[0] == outputs[1]


@pytest.mark.parametrize("phase", ["start", "fetch"])
def test_interrupt_preserves_background_run_id_and_resume_does_not_charge_again(
    tmp_path, monkeypatch, phase
):
    store = Store(tmp_path / "state.sqlite", {})
    entered, release = threading.Event(), threading.Event()
    transport = Mock()

    def start(*_):
        if phase == "start":
            entered.set()
            assert release.wait(5)
        return "run123"

    def completed(run_id):
        if phase == "fetch":
            entered.set()
            assert release.wait(5)
            raise CancelledError()
        return evidence(run_id)

    transport.start.side_effect, transport.completed.side_effect = start, completed
    transport.cancel.side_effect = release.set
    transport.items.return_value = [record("q")]
    poll = ResolutionWorkers.poll

    def interrupt(self):
        if entered.is_set():
            if phase == "start":
                release.set()
            raise KeyboardInterrupt()
        poll(self)

    monkeypatch.setattr(ResolutionWorkers, "poll", interrupt)
    try:
        with pytest.raises(KeyboardInterrupt):
            run(
                ["q"],
                provider(store, ["q"], transport),
                Mock(resolve=confirmed),
                store,
                1,
                workers=2,
            )
        assert store.apify_open_batch()[2] == "run123"
        assert store.apify_reserved_pages() == 1
        assert store.export_progress()[0]["status"] == "interrupted"
        monkeypatch.setattr(ResolutionWorkers, "poll", poll)
        transport.completed.side_effect = lambda run_id: evidence(run_id)
        run(["q"], provider(store, ["q"], transport), Mock(resolve=confirmed), store, 1, workers=2)
        transport.start.assert_called_once()
        assert store.export_progress()[0]["status"] == "limited"
    finally:
        release.set()
        store.close()


def test_uncertain_batch_stops_without_importing_partial_results_or_starting_another(tmp_path):
    store = Store(tmp_path / "state.sqlite", {})
    transport = Mock(start=Mock(return_value="run123"))
    transport.completed.return_value = evidence(count=1)
    transport.items.return_value = [record("one")]
    try:
        with pytest.raises(ApifyRecoveryRequired, match="omitted"):
            run(
                ["one", "two"],
                provider(store, ["one", "two"], transport),
                Mock(resolve=confirmed),
                store,
                1,
                workers=4,
            )
        assert store.saved_queries(["one", "two"]) == []
        assert store.apify_open_batch()[2] == "run123"
        transport.start.assert_called_once()
    finally:
        store.close()


def test_cancelling_local_apify_wait_does_not_abort_remote_actor():
    transport = ApifyTransport("synthetic-token")
    transport.request = Mock(return_value={"data": {"status": "RUNNING"}})
    transport.cancel()
    with pytest.raises(CancelledError):
        transport.completed("run123")
    transport.request.assert_called_once_with("/actor-runs/run123")


def test_lost_background_post_response_is_never_retried_on_resume(tmp_path):
    store = Store(tmp_path / "state.sqlite", {})
    transport = Mock()
    transport.start.side_effect = DiscoveryError("lost response")
    try:
        for expected in ("not confirmed", "unconfirmed"):
            with pytest.raises(ApifyRecoveryRequired, match=expected):
                run(
                    ["q"],
                    provider(store, ["q"], transport),
                    Mock(resolve=confirmed),
                    store,
                    1,
                    workers=2,
                )
        transport.start.assert_called_once()
        assert store.apify_open_batch()[2] is None
        assert store.apify_reserved_pages() == 1
    finally:
        store.close()


def test_later_cached_query_is_processed_when_search_budget_is_exhausted(tmp_path):
    store = Store(tmp_path / "state.sqlite", {})
    store.save_page("cached", 0, saved(["https://cached.example/"]))
    transport = Mock()
    try:
        run(
            ["missing", "cached"],
            provider(store, ["missing", "cached"], transport, limit=0),
            Mock(resolve=confirmed),
            store,
            1,
            workers=4,
        )
        assert {row["query"]: row["status"] for row in store.export_progress()} == {
            "missing": "not_started",
            "cached": "complete",
        }
        assert store.resolution("cached", "https://cached.example/") is not None
        transport.start.assert_not_called()
    finally:
        store.close()


def test_interrupt_during_remote_wait_keeps_sites_already_persisted(tmp_path, monkeypatch):
    store = Store(tmp_path / "state.sqlite", {})
    store.save_page("cached", 0, saved(["https://cached.example/"]))
    entered, persisted, release = (threading.Event() for _ in range(3))
    transport = Mock(start=Mock(return_value="run123"))
    transport.cancel.side_effect = release.set
    original_save, original_poll = store.save_resolution, ResolutionWorkers.poll

    def completed(_):
        entered.set()
        assert release.wait(5)
        raise CancelledError()

    def save(*args):
        original_save(*args)
        persisted.set()

    def poll(self):
        original_poll(self)
        if entered.is_set() and persisted.is_set():
            raise KeyboardInterrupt()

    transport.completed.side_effect = completed
    store.save_resolution = save
    monkeypatch.setattr(ResolutionWorkers, "poll", poll)
    try:
        with pytest.raises(KeyboardInterrupt):
            run(
                ["missing", "cached"],
                provider(store, ["missing", "cached"], transport),
                Mock(resolve=confirmed),
                store,
                1,
                workers=4,
            )
        assert store.resolution("cached", "https://cached.example/") is not None
        assert {row["query"]: row["status"] for row in store.export_progress()} == {
            "missing": "interrupted",
            "cached": "complete",
        }
        assert store.apify_open_batch()[2] == "run123"
    finally:
        release.set()
        store.close()


@pytest.mark.parametrize("workers", [1, 4])
def test_import_uses_persisted_run_id_without_relying_on_echoed_evidence(tmp_path, workers):
    store = Store(tmp_path / "state.sqlite", {})
    transport = Mock(start=Mock(return_value="run123"))
    metadata = evidence()
    metadata.pop("run_id")
    transport.completed.return_value = metadata
    transport.items.return_value = [record("q")]
    try:
        run(
            ["q"],
            provider(store, ["q"], transport),
            Mock(resolve=confirmed),
            store,
            1,
            workers=workers,
        )
        assert store.apify_query_coverage("q")["run_id"] == "run123"
    finally:
        store.close()
