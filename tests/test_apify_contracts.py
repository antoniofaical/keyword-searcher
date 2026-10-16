import json
from io import BytesIO
from pathlib import Path
from unittest.mock import Mock
from urllib.error import HTTPError, URLError

import pytest

from keyword_searcher.apify import ApifyGoogle, ApifyRecoveryRequired, ApifyTransport
from keyword_searcher.models import DiscoveryError, Resolution
from keyword_searcher.pipeline import run
from keyword_searcher.store import Store


def record(page=1, count=10):
    return {
        "searchQuery": {"term": "q", "page": page},
        "organicResults": [{"url": f"https://example.org/{page}/{i}"} for i in range(count)],
    }


def transport_for(records, **changes):
    transport = Mock()
    transport.start.return_value = "run123"
    evidence = dict(
        dataset_id="dataset123",
        run_id="run123",
        status="SUCCEEDED",
        exit_code=0,
        cost_usd=0.01,
        cost_limit_usd=1,
        pending_requests=0,
        handled_requests=len(records),
    )
    evidence.update(changes)
    transport.completed.return_value = evidence
    transport.items.return_value = records
    return transport


@pytest.mark.parametrize(
    "records,max_pages,status,reason",
    [
        ([record()], 3, "complete", ""),
        ([record(count=0)], 3, "complete", ""),
        ([record(), record(2, 3)], 3, "complete", ""),
        ([record(), record(2, 3)], 2, "limited", "max_pages"),
    ],
)
def test_dataset_pagination_never_launches_a_second_actor(
    tmp_path, records, max_pages, status, reason
):
    store = Store(tmp_path / "state.sqlite", {})
    transport = transport_for(records)
    provider = ApifyGoogle(transport, store, ["q"], "br", "pt", max_pages, 900)
    resolver = Mock()
    resolver.resolve.return_value = Resolution("pending")
    run(["q"], provider, resolver, store, max_pages)
    run(["q"], provider, resolver, store, max_pages)
    transport.start.assert_called_once()
    assert store.export_progress() == [dict(query="q", status=status, reason=reason)]
    assert store.apify_query_coverage("q")["pages"] == len(records)
    # Even explicitly asking for a missing continuation cannot re-launch a paid run.
    with pytest.raises(DiscoveryError, match="continuation_unverified"):
        provider.search("q", len(records) * 10)
    transport.start.assert_called_once()
    store.close()


@pytest.mark.parametrize(
    "changes,reason",
    [
        ({"cost_usd": 1}, "apify_cost_limit"),
        ({"cost_usd": None}, "apify_completion_unverified"),
        ({"cost_usd": float("nan")}, "apify_completion_unverified"),
        ({"cost_limit_usd": None}, "apify_completion_unverified"),
        ({"pending_requests": 1}, "apify_completion_unverified"),
        ({"handled_requests": 2}, "apify_completion_unverified"),
        ({"exit_code": 1}, "apify_completion_unverified"),
    ],
)
def test_uncertain_or_truncated_run_never_reports_complete(tmp_path, changes, reason):
    store = Store(tmp_path / "state.sqlite", {})
    transport = transport_for([record()], **changes)
    provider = ApifyGoogle(transport, store, ["q"], "br", "pt", 3, 900)
    resolver = Mock()
    resolver.resolve.return_value = Resolution("pending")
    run(["q"], provider, resolver, store, 3)
    assert store.export_progress()[0]["status"] == "incomplete"
    assert store.export_progress()[0]["reason"] == reason
    assert store.page("q", 0) is not None
    assert store.apify_open_batch() is None
    store.close()


@pytest.mark.parametrize(
    "records,error",
    [
        ([record(), record()], "duplicate"),
        ([record(), record(3)], "intermediate"),
        ([record(2)], "omitted"),
        ([{"searchQuery": {"term": [], "page": 1}, "organicResults": []}], "unexpected"),
        ([{"searchQuery": {"term": "q", "page": True}, "organicResults": []}], "unexpected"),
        ([{"searchQuery": {"term": "q", "page": 1}, "organicResults": [{}]}], "organic_result"),
    ],
)
def test_invalid_dataset_preserves_run_for_recovery_without_partial_import(
    tmp_path, records, error
):
    store = Store(tmp_path / "state.sqlite", {})
    transport = transport_for(records)
    provider = ApifyGoogle(transport, store, ["q"], "br", "pt", 3, 900)
    with pytest.raises(DiscoveryError, match=error):
        provider.search("q", 0)
    assert store.apify_open_batch()[2] == "run123"
    assert store.saved_pages("q") == []
    with pytest.raises(DiscoveryError, match=error):
        provider.search("q", 0)
    transport.start.assert_called_once()
    store.close()


class Reply:
    def __init__(self, value):
        self.value = value

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def read(self, _limit):
        return json.dumps(self.value).encode()


@pytest.mark.parametrize(
    "failure",
    [
        URLError("secret"),
        HTTPError("secret", 429, "", {}, None),
        HTTPError("secret", 503, "", {}, None),
    ],
)
def test_read_requests_retry_without_exposing_credentials(monkeypatch, failure):
    urlopen = Mock(side_effect=[failure, Reply([])])
    monkeypatch.setattr("keyword_searcher.apify.urlopen", urlopen)
    monkeypatch.setattr("keyword_searcher.apify.time.sleep", lambda _: None)
    assert ApifyTransport("secret").request("/datasets/test/items") == []
    assert urlopen.call_count == 2


@pytest.mark.parametrize("code", [401, 403])
def test_authentication_errors_are_not_retried(monkeypatch, code):
    urlopen = Mock(side_effect=HTTPError("secret", code, "secret", {}, None))
    monkeypatch.setattr("keyword_searcher.apify.urlopen", urlopen)
    with pytest.raises(DiscoveryError, match=f"apify_http_{code}") as exc:
        ApifyTransport("secret").request("/actor-runs/run123")
    assert "secret" not in str(exc.value)
    urlopen.assert_called_once()


def test_lost_post_response_never_retries_start(monkeypatch):
    urlopen = Mock(side_effect=URLError("secret"))
    monkeypatch.setattr("keyword_searcher.apify.urlopen", urlopen)
    with pytest.raises(DiscoveryError, match="network_failure"):
        ApifyTransport("secret").start(["q"], 3, "br", "pt", 1)
    urlopen.assert_called_once()


@pytest.mark.parametrize(
    "country,language,expected",
    [
        ("br", "pt", "pt-BR"),
        ("pt", "pt", "pt-PT"),
        ("us", "pt", "pt-BR"),
        ("us", "en", "en"),
        ("br", "pt-PT", "pt-PT"),
        ("pt", "pt-BR", "pt-BR"),
    ],
)
def test_portuguese_input_matches_public_actor_language_contract(country, language, expected):
    fixture = Path(__file__).parent / "fixtures/apify_language_contract.json"
    accepted = json.loads(fixture.read_text(encoding="utf-8"))["languageCode"]["enum"]
    # These values come from the public Actor build, independent of our adapter.
    assert "pt" not in accepted
    transport = ApifyTransport("secret")
    transport.request = Mock(return_value={"data": {"id": "run123"}})
    assert transport.start(["q"], 5, country, language, 2) == "run123"
    sent = transport.request.call_args.args[1]
    assert sent["languageCode"] == expected
    assert sent["languageCode"] in accepted


def test_post_rejection_retains_api_reason_and_redacts_credentials(monkeypatch):
    token = "apify_api_test_secret"
    body = json.dumps(
        {"error": {"type": "invalid-input", "message": f"languageCode invalid {token}\n\x1b"}}
    ).encode()
    failure = HTTPError("secret-url", 400, "secret-title", {}, BytesIO(body))
    urlopen = Mock(side_effect=failure)
    monkeypatch.setattr("keyword_searcher.apify.urlopen", urlopen)
    with pytest.raises(DiscoveryError, match="apify_http_400: invalid-input") as exc:
        ApifyTransport(token).start(["q"], 5, "br", "pt", 2)
    assert "languageCode invalid" in str(exc.value)
    assert token not in str(exc.value)
    assert "\x1b" not in str(exc.value)
    assert "\n" not in str(exc.value)
    urlopen.assert_called_once()


@pytest.mark.parametrize(
    "body",
    [b"<html>bad gateway</html>", b"[]", b'{"error": []}', b'{"error": {"message": []}}'],
)
def test_invalid_error_body_keeps_http_status_without_crashing(monkeypatch, body):
    failure = HTTPError("secret", 400, "secret", {}, BytesIO(body))
    monkeypatch.setattr("keyword_searcher.apify.urlopen", Mock(side_effect=failure))
    with pytest.raises(DiscoveryError, match="^apify_http_400$"):
        ApifyTransport("secret").start(["q"], 3, "br", "pt", 1)


def test_uncertain_start_shows_cause_and_remains_blocked(tmp_path, monkeypatch):
    body = json.dumps(
        {"error": {"type": "invalid-input", "message": "languageCode is invalid"}}
    ).encode()
    failure = HTTPError("secret", 400, "", {}, BytesIO(body))
    urlopen = Mock(side_effect=failure)
    monkeypatch.setattr("keyword_searcher.apify.urlopen", urlopen)
    store = Store(tmp_path / "state.sqlite", {})
    provider = ApifyGoogle(ApifyTransport("secret"), store, ["q"], "br", "pt", 5, 100)
    with pytest.raises(ApifyRecoveryRequired, match="apify_http_400: invalid-input"):
        provider.search("q", 0)
    assert store.apify_open_batch()[3] == "planned"
    assert store.apify_reserved_pages() == 5
    with pytest.raises(ApifyRecoveryRequired, match="unconfirmed"):
        provider.search("q", 0)
    urlopen.assert_called_once()
    store.close()


def test_completed_waits_through_transient_states_and_reads_stable_evidence(monkeypatch):
    final = dict(
        status="SUCCEEDED",
        defaultDatasetId="dataset123",
        defaultRequestQueueId="queue123",
        usageTotalUsd=0.1,
        options={"maxTotalChargeUsd": 1},
        exitCode=0,
    )
    transport = ApifyTransport("secret")
    transport.request = Mock(
        side_effect=[
            {"data": {"status": "RUNNING"}},
            {"data": {"status": "TIMING-OUT"}},
            {"data": {"status": "ABORTING"}},
            {"data": final},
            {"data": final},
            {"data": {"pendingRequestCount": 0, "handledRequestCount": 1}},
        ]
    )
    monkeypatch.setattr("keyword_searcher.apify.time.sleep", lambda _: None)
    evidence = transport.completed("run123")
    assert evidence["dataset_id"] == "dataset123"
    assert evidence["cost_usd"] == 0.1
    assert evidence["pending_requests"] == 0
    assert transport.request.call_count == 6


@pytest.mark.parametrize("status", ["FAILED", "TIMED-OUT", "ABORTED"])
def test_terminal_failure_requires_recovery(monkeypatch, status):
    transport = ApifyTransport("secret")
    transport.request = Mock(return_value={"data": {"status": status}})
    with pytest.raises(ApifyRecoveryRequired, match=status):
        transport.completed("run123")


def test_dataset_items_are_fetched_in_all_pages():
    transport = ApifyTransport("secret")
    transport.request = Mock(side_effect=[[record()] * 50, [record(2)]])
    assert len(list(transport.items("dataset123"))) == 51
    assert "offset=50" in transport.request.call_args.args[0]


def test_malformed_run_status_is_an_expected_failure():
    transport = ApifyTransport("secret")
    transport.request = Mock(return_value={"data": {"status": []}})
    with pytest.raises(ApifyRecoveryRequired, match="invalid_run_status"):
        transport.completed("run123")


def test_interrupt_during_poll_keeps_confirmed_run_id(tmp_path):
    store = Store(tmp_path / "state.sqlite", {})
    transport = transport_for([record()])
    evidence = transport.completed.return_value
    transport.completed.side_effect = [KeyboardInterrupt(), evidence]
    provider = ApifyGoogle(transport, store, ["q"], "br", "pt", 3, 900)
    with pytest.raises(KeyboardInterrupt):
        provider.search("q", 0)
    assert store.apify_open_batch()[2] == "run123"
    provider.search("q", 0)
    transport.start.assert_called_once()
    store.close()
