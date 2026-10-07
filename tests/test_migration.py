import csv
import json
import sqlite3
from pathlib import Path
from unittest.mock import Mock

import pytest

from keyword_searcher.apify import ApifyGoogle
from keyword_searcher.cli import main
from keyword_searcher.export import export_results, output_directory
from keyword_searcher.models import DiscoveryError, Resolution
from keyword_searcher.store import Store


def config():
    return dict(
        version="0.2.0",
        provider="apify-google",
        queries=["q"],
        country="br",
        language="pt",
        location="",
        max_pages=3,
    )


def legacy_database(path):
    old = config()
    old.update(version="0.1.0", provider="serpapi-google")
    store = Store(path, old)
    store.save_page("q", 0, {"search_metadata": {"status": "Success"}, "organic_results": []})
    store.reserve(10)
    store.save_resolution(
        "q",
        "https://example.org/p",
        Resolution("confirmed", "A; B", "https://example.org/", evidence="preserved"),
    )
    batch = store.apify_plan_batch(["q"], 3, 10)
    store.apify_set_run(batch, "run123")
    # Restore exactly the schema-1 shape rather than pretending the new writer is old.
    with store.db:
        store.db.execute("DELETE FROM meta WHERE key='schema_version'")
        store.db.execute("DROP TABLE apify_coverage")
        store.db.execute("DROP TABLE apify_run_results")
    store.close()
    return old, batch


def test_migration_preserves_pages_ids_decisions_and_counters(tmp_path):
    path = tmp_path / "state.sqlite"
    old, batch = legacy_database(path)
    store = Store(path, config())
    assert store.requests() == 1
    assert store.apify_reserved_pages() == 3
    assert store.apify_open_batch() == (batch, ["q"], "run123", "running")
    assert store.resolution("q", "https://example.org/p").evidence == "preserved"
    assert store.page("q", 0)["organic_results"] == []
    meta = dict(store.db.execute("SELECT key, value FROM meta"))
    assert json.loads(meta["legacy_config"]) == old
    assert json.loads(meta["migration"])["to"] == 2
    assert json.loads(meta["config"])["provider"] == "apify-google"
    store.close()
    # Software upgrades do not change the semantic run identity.
    updated = config()
    updated["version"] = "0.3.0"
    Store(path, updated).close()


@pytest.mark.parametrize(
    "field,value",
    [
        ("queries", ["other"]),
        ("country", "us"),
        ("language", "en"),
        ("location", "elsewhere"),
        ("max_pages", 2),
    ],
)
def test_incompatible_migration_leaves_legacy_config_untouched(tmp_path, field, value):
    path = tmp_path / "state.sqlite"
    old, _ = legacy_database(path)
    updated = config()
    updated[field] = value
    with pytest.raises(DiscoveryError, match="configuration"):
        Store(path, updated)
    with sqlite3.connect(path) as db:
        assert (
            json.loads(db.execute("SELECT value FROM meta WHERE key='config'").fetchone()[0]) == old
        )
        assert db.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone() is None
        assert (
            db.execute("SELECT name FROM sqlite_master WHERE name='apify_coverage'").fetchone()
            is None
        )


def test_future_schema_is_rejected(tmp_path):
    path = tmp_path / "state.sqlite"
    store = Store(path, config())
    with store.db:
        store.db.execute("UPDATE meta SET value='999' WHERE key='schema_version'")
    store.close()
    with pytest.raises(DiscoveryError, match="schema"):
        Store(path, config())


def test_export_external_new_path_preserves_csv_and_state(tmp_path):
    store = Store(tmp_path / "state/state.sqlite", {})
    store.ensure_queries(["gestão; estoque"])
    store.save_resolution(
        "gestão; estoque",
        "https://example.org/p",
        Resolution("confirmed", "A; B", "https://example.org/"),
    )
    output = tmp_path / "outside with spaces/nested"
    report = export_results(store, output)
    raw = (output / "companies.csv").read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    with (output / "companies.csv").open(encoding="utf-8-sig", newline="") as file:
        assert list(csv.reader(file, delimiter=";")) == [
            ["CompanyName", "URL", "SearchQuery"],
            ["A; B", "https://example.org/", "gestão; estoque"],
        ]
    assert report["exported_rows"] == 1
    assert not (output / "state.sqlite").exists()
    assert not list(output.glob("*.tmp"))
    assert export_results(store, tmp_path / "other")["exported_rows"] == 1
    store.close()


def test_export_rejects_checkout_before_creating_files():
    root = Path(__file__).resolve().parents[1]
    with pytest.raises(DiscoveryError, match="outside"):
        output_directory(root / "forbidden-output")
    assert not (root / "forbidden-output").exists()


def test_export_rejects_installed_package_without_checkout(tmp_path, monkeypatch):
    package = tmp_path / "site-packages/keyword_searcher"
    package.mkdir(parents=True)
    monkeypatch.setattr("keyword_searcher.export.__file__", str(package / "export.py"))
    monkeypatch.chdir(tmp_path)
    with pytest.raises(DiscoveryError, match="installed package"):
        output_directory(package / "output")
    assert output_directory(tmp_path / "external") == tmp_path / "external"


def test_export_failure_preserves_previous_csv(tmp_path, monkeypatch):
    store = Store(tmp_path / "state.sqlite", {})
    destination = tmp_path / "output"
    export_results(store, destination)
    original = (destination / "companies.csv").read_bytes()

    def denied(*args):
        raise PermissionError("synthetic permission error")

    monkeypatch.setattr("keyword_searcher.export.os.replace", denied)
    with pytest.raises(PermissionError):
        export_results(store, destination)
    assert (destination / "companies.csv").read_bytes() == original
    assert not list(destination.glob("*.tmp"))
    store.close()


def test_cli_apify_resume_recheck_and_change_output_without_token(tmp_path, monkeypatch):
    query = tmp_path / "q.txt"
    query.write_text("q", encoding="utf-8")
    transport = Mock()
    transport.start.return_value = "run123"
    transport.completed.return_value = {
        "dataset_id": "dataset123",
        "run_id": "run123",
        "status": "SUCCEEDED",
        "exit_code": 0,
        "cost_usd": 0.01,
        "cost_limit_usd": 1,
        "pending_requests": 0,
        "handled_requests": 1,
    }
    transport.items.return_value = [
        {
            "searchQuery": {"term": "q", "page": 1},
            "organicResults": [{"url": "https://example.org/"}],
        }
    ]
    resolver = Mock()
    resolver.resolve.return_value = Resolution("confirmed", "Example", "https://example.org/")
    monkeypatch.setattr("keyword_searcher.cli.ApifyTransport", lambda token: transport)
    monkeypatch.setattr("keyword_searcher.cli.WebsiteResolver", lambda http: resolver)
    monkeypatch.delenv("SERPAPI_API_KEY", raising=False)
    monkeypatch.delenv("APIFY_API_TOKEN", raising=False)
    args = ["--queries", str(query), "--run-dir", str(tmp_path / "state"), "--no-progress"]
    assert main(args + ["--output-dir", str(tmp_path / "first")]) == 0
    assert main(args + ["--output-dir", str(tmp_path / "second")]) == 0
    transport.start.assert_called_once()
    resolver.resolve.assert_called_once()
    assert main(args + ["--output-dir", str(tmp_path / "third"), "--recheck-sites"]) == 0
    assert resolver.resolve.call_count == 2
    assert transport.start.call_count == 1
    report = json.loads((tmp_path / "third/report.json").read_text())
    assert report["search_requests"] == 0
    assert report["apify_reserved_pages"] == 3
    assert report["queries"][0]["status"] == "complete"
    assert not (tmp_path / "state/companies.csv").exists()


def test_cli_rechecks_mixed_payloads_without_remote_calls(tmp_path, monkeypatch):
    queries = tmp_path / "q.txt"
    queries.write_text("q", encoding="utf-8")
    store = Store(tmp_path / "state/state.sqlite", config())
    store.save_page(
        "q",
        0,
        {
            "search_metadata": {"status": "Success"},
            "organic_results": [{"link": "https://one.example.org/"}],
        },
    )
    store.save_page(
        "q",
        17,
        {
            "provider": "apify-google",
            "has_next": False,
            "organicResults": [{"url": "https://two.example.org/"}],
        },
    )
    store.ensure_queries(["q"])
    store.close()
    transport, resolver = Mock(), Mock()
    resolver.resolve.return_value = Resolution("pending")
    monkeypatch.setattr("keyword_searcher.cli.ApifyTransport", lambda token: transport)
    monkeypatch.setattr("keyword_searcher.cli.WebsiteResolver", lambda http: resolver)
    assert (
        main(
            [
                "--queries",
                str(queries),
                "--run-dir",
                str(tmp_path / "state"),
                "--output-dir",
                str(tmp_path / "output"),
                "--recheck-sites",
            ]
        )
        == 2
    )
    transport.start.assert_not_called()
    assert resolver.resolve.call_count == 2


def test_cli_missing_output_and_removed_serpapi_options(tmp_path):
    for args in [[], ["--provider", "serpapi"], ["--max-search-requests", "1"]]:
        extra = ["--output-dir", str(tmp_path / "output")] if args else []
        with pytest.raises(SystemExit) as exc:
            main(["--queries", "q.txt", "--run-dir", str(tmp_path), *extra, *args])
        assert exc.value.code == 2


def test_legacy_offsets_do_not_start_paid_replacement(tmp_path):
    store = Store(tmp_path / "state.sqlite", {})
    store.save_page(
        "q",
        0,
        {
            "search_metadata": {"status": "Success"},
            "organic_results": [],
            "serpapi_pagination": {"next": "https://serpapi.com/search?start=17"},
        },
    )
    transport = Mock()
    provider = ApifyGoogle(transport, store, ["q"], "br", "pt", 3, 900)
    with pytest.raises(DiscoveryError, match="continuation_unverified"):
        provider.search("q", 17)
    transport.start.assert_not_called()
    assert store.apify_reserved_pages() == 0
    store.close()
