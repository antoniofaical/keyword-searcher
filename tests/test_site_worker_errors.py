import hashlib
import json
import sqlite3
from email.message import Message
from http.client import HTTPConnection
from io import BytesIO
from unittest.mock import Mock
from urllib.parse import urlsplit

import pytest

from keyword_searcher import cli, recheck
from keyword_searcher.http import HttpClient
from keyword_searcher.models import DiscoveryError, Resolution
from keyword_searcher.pipeline import run
from keyword_searcher.progress import TerminalProgress
from keyword_searcher.resolve import WebsiteResolver
from keyword_searcher.search import parse_legacy
from keyword_searcher.store import Store
from keyword_searcher.urls import normalize_url
from keyword_searcher.worker_diagnostics import failure_exit_message
from keyword_searcher.workers import WorkerFailure


def page(url):
    return {"search_metadata": {"status": "Success"}, "organic_results": [{"link": url}]}


def search():
    provider = Mock()
    provider.parse = parse_legacy
    provider.search.side_effect = AssertionError("No paid searches")
    return provider


@pytest.mark.parametrize(
    "source, expected",
    [
        ("https://example.org/saúde/atenção", "https://example.org/sa%C3%BAde/aten%C3%A7%C3%A3o"),
        ("https://example.org/quem somos", "https://example.org/quem%20somos"),
        ("https://example.org/東京", "https://example.org/%E6%9D%B1%E4%BA%AC"),
        ("https://example.org/a%2Fb/%C3%A7", "https://example.org/a%2Fb/%C3%A7"),
        ("https://example.org/a:b;c,@x!$&'()*+=", "https://example.org/a:b;c,@x!$&'()*+="),
    ],
)
def test_paths_are_ascii_for_http_without_double_encoding(source, expected):
    target = normalize_url(source)
    assert target == expected
    assert normalize_url(target) == target
    # Exercises the exact stdlib operation that failed on the VPS, with no socket.
    connection = HTTPConnection("example.org")
    try:
        connection.putrequest("GET", urlsplit(target).path)
    finally:
        connection.close()


def test_unencodable_path_is_an_expected_invalid_url():
    with pytest.raises(DiscoveryError, match="invalid_url"):
        normalize_url("https://example.org/\ud800")


@pytest.mark.parametrize("workers", [1, 20])
def test_accented_about_link_does_not_crash_site_resolution(tmp_path, monkeypatch, workers):
    store = Store(tmp_path / "state.sqlite", {})
    source = "https://example.org/melhor-sistema-para-gest%C3%A3o-assistencial-do-home-care"
    store.save_page("q", 0, page(source))
    requested = []
    client = HttpClient()
    monkeypatch.setattr("keyword_searcher.http.require_public_url", lambda _: None)

    class Reply(BytesIO):
        code = 200
        headers = Message()
        headers["Content-Type"] = "text/html; charset=utf-8"

    def open_request(request, timeout):
        url = request.full_url
        requested.append(url)
        connection = HTTPConnection("example.org")
        try:
            connection.putrequest("GET", request.selector)
        finally:
            connection.close()
        if url.endswith("/quem%20somos/aten%C3%A7%C3%A3o"):
            html = '<meta property="og:site_name" content="Example">'
        else:
            # Title is a weak identity; About supplies an independent signal.
            html = '<title>Example</title><a href="/quem somos/atenção">About</a>'
        return Reply(html.encode())

    client.opener = Mock(open=open_request)
    try:
        run(["q"], search(), WebsiteResolver(client), store, 1, workers=workers)
        assert "https://example.org/quem%20somos/aten%C3%A7%C3%A3o" in requested
        assert len(list(store.export_resolutions())) == 1
        assert store.export_progress()[0]["status"] == "complete"
    finally:
        store.close()


@pytest.mark.parametrize("phase", ["resolve", "persist", "progress"])
def test_worker_failure_identifies_phase_and_source_without_secrets(tmp_path, monkeypatch, phase):
    token = "apify_api_SYNTHETICsecret"
    monkeypatch.setenv("APIFY_API_TOKEN", token)
    source = f"https://user:password@example.org/private/path?key={token}#FRAGMENT_SECRET"
    store = Store(tmp_path / "state.sqlite", {})
    store.save_page("q", 0, page(source))
    resolver = Mock(resolve=Mock(return_value=Resolution("pending", reason="test")))
    progress = Mock(spec=TerminalProgress)
    error_type = {
        "resolve": ValueError,
        "persist": sqlite3.OperationalError,
        "progress": RuntimeError,
    }[phase]
    original = error_type(f"private exception message with {token}")
    if phase == "resolve":
        resolver.resolve.side_effect = original
    elif phase == "persist":
        store.save_resolution = Mock(side_effect=original)
    else:
        progress.end_result.side_effect = original
    try:
        with pytest.raises(WorkerFailure) as caught:
            run(["q"], search(), resolver, store, 1, workers=2, progress=progress)
        error = caught.value
        details = error.details
        assert error.__cause__ is original
        assert details["phase"] == phase and details["exception_type"] == error_type.__name__
        assert details["source_location"] == "https://example.org/private/path"
        assert details["source_url_sha256"] == hashlib.sha256(source.encode()).hexdigest()
        assert details["frames"] and all("/" not in f["file"] for f in details["frames"])
        serialized = json.dumps(details) + str(error)
        for omitted in (token, "password", "FRAGMENT_SECRET", "private exception message"):
            assert omitted not in serialized
        assert store.export_progress()[0]["reason"] == "site_worker_failed"
        # A progress failure must not undo a result that was already saved.
        assert (store.resolution("q", source) is not None) == (phase == "progress")
    finally:
        store.close()


@pytest.mark.parametrize("mode", ["search", "recheck"])
def test_cli_exports_partial_results_and_worker_diagnostic(tmp_path, monkeypatch, capsys, mode):
    queries = tmp_path / "queries.txt"
    queries.write_text("q", encoding="utf-8")
    run_dir, output = tmp_path / "run", tmp_path / "out"
    config = dict(queries=["q"], country="br", language="pt", location="", max_pages=1)
    store = Store(run_dir / "state.sqlite", config)
    store.save_page("q", 0, page("https://example.org/path"))
    store.close()
    before = (run_dir / "state.sqlite").read_bytes()
    resolver = Mock(resolve=Mock(side_effect=ValueError("not part of diagnostics")))
    target = cli if mode == "search" else recheck
    monkeypatch.setattr(target, "WebsiteResolver", Mock(return_value=resolver))
    args = [
        "--run-dir",
        str(run_dir),
        "--output-dir",
        str(output),
        "--workers",
        "2",
        "--no-progress",
    ]
    if mode == "search":
        args += ["--queries", str(queries), "--max-pages", "1"]
    with pytest.raises(SystemExit) as caught:
        target.main(args)
    assert caught.value.code == 1
    assert (output / "report.json").is_file() and (output / "handoff.json").is_file()
    details = json.loads((output / "worker_error.json").read_text(encoding="utf-8"))
    assert details["phase"] == "resolve" and details["exception_type"] == "ValueError"
    assert details["source_location"] == "https://example.org/path"
    stderr = capsys.readouterr().err
    assert "ValueError" in stderr and "worker_error.json" in stderr
    assert "not part of diagnostics" not in stderr
    if mode == "recheck":
        assert (run_dir / "state.sqlite").read_bytes() == before


def test_diagnostic_write_failure_keeps_primary_error_visible(tmp_path, monkeypatch):
    error = WorkerFailure(
        dict(exception_type="TypeError", phase="resolve", source_location="https://example.org/")
    )
    monkeypatch.setattr(
        "pathlib.Path.write_text", Mock(side_effect=PermissionError("private path"))
    )
    message = failure_exit_message(error, tmp_path / "out")
    assert "TypeError" in message and "PermissionError" in message
    assert "private path" not in message
