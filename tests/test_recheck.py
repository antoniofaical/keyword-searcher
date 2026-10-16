import json
import sqlite3
from unittest.mock import Mock

import pytest

from keyword_searcher.http import Response
from keyword_searcher.models import DiscoveryError, Resolution
from keyword_searcher.recheck import main, recheck_saved
from keyword_searcher.store import Store


def saved_state(tmp_path):
    directory = tmp_path / "original-run"
    store = Store(
        directory / "state.sqlite", {"queries": ["q"], "language": "pt", "version": "0.2.2"}
    )
    rows = [
        ("old", Resolution("confirmed", "Wrong", "https://old.example.org/")),
        ("article", Resolution("skipped", reason="editorial_or_listing_page")),
        ("missing", Resolution("pending", reason="missing_or_ambiguous_company_identity")),
    ]
    store.save_page(
        "q",
        0,
        {
            "provider": "apify-google",
            "has_next": False,
            "organicResults": [
                {"title": "Synthetic", "url": f"https://{host}.example.org/"} for host, _ in rows
            ],
        },
    )
    for host, item in rows:
        store.save_resolution("q", f"https://{host}.example.org/", item)
    store.progress("q", "limited", "max_pages")
    batch = store.apify_plan_batch(["q"], 5, 100)
    with store.db:
        store.db.execute(
            "UPDATE apify_batches SET run_id='original-run-id',status='complete' WHERE id=?",
            (batch,),
        )
    store.close()
    return directory


def site_http():
    def get(url):
        if "old." in url:
            return Response(
                url,
                200,
                '<script type="application/ld+json">{"@type":"NewsMediaOrganization"}</script>',
                "text/html",
            )
        body = '<meta property="og:site_name" content="Synthetic Supplier"><a href="/products">Products</a>'
        return Response(url, 200, body, "text/html")

    return Mock(get=Mock(side_effect=get))


def test_recheck_all_statuses_on_copy_with_backup_and_no_apify(tmp_path, monkeypatch):
    original = saved_state(tmp_path)
    source_bytes = (original / "state.sqlite").read_bytes()
    apify = Mock(side_effect=AssertionError("Recheck must never request Apify"))
    monkeypatch.setattr("keyword_searcher.apify.ApifyTransport.start", apify)
    monkeypatch.setattr("keyword_searcher.apify.ApifyTransport.request", apify)
    monkeypatch.delenv("APIFY_API_TOKEN", raising=False)
    output = tmp_path / "recheck"
    summary = recheck_saved(original, output, http=site_http(), progress=False)
    assert summary["before"] == {"confirmed": 1, "skipped": 1, "pending": 1}
    assert summary["after"] == {"confirmed": 2, "skipped": 1}
    assert summary["changed"] == 3
    assert summary["exported_rows"] == 2
    assert not summary["interrupted"]
    assert (original / "state.sqlite").read_bytes() == source_bytes
    backup = Store.open_existing(output / "state-before.sqlite")
    working = Store.open_existing(output / "state.sqlite")
    source = Store.open_existing(original / "state.sqlite")
    try:
        for table in (
            "meta",
            "pages",
            "progress",
            "apify_batches",
            "apify_coverage",
            "apify_run_results",
        ):
            expected = source.db.execute(f"SELECT * FROM {table}").fetchall()
            assert backup.db.execute(f"SELECT * FROM {table}").fetchall() == expected
            assert working.db.execute(f"SELECT * FROM {table}").fetchall() == expected
        assert list(backup.export_resolutions()) == list(source.export_resolutions())
    finally:
        for store in (backup, working, source):
            store.close()
    apify.assert_not_called()
    changes = json.loads((output / "recheck_changes.json").read_text(encoding="utf-8"))
    assert {c["before"]["status"] for c in changes} == {"confirmed", "skipped", "pending"}
    assert (output / "companies.csv").read_text(encoding="utf-8-sig").splitlines()[
        0
    ] == "CompanyName;URL;SearchQuery"


def test_existing_directory_preserves_human_review_and_state(tmp_path):
    original = saved_state(tmp_path)
    before = (original / "state.sqlite").read_bytes()
    output = tmp_path / "reviewed"
    output.mkdir()
    review = output / "audit_cases.csv"
    review.write_text(
        "CaseID;ReviewOutcome;ReviewerNotes\nexisting;approved;human", encoding="utf-8"
    )
    http = site_http()
    with pytest.raises(DiscoveryError, match="already exists"):
        recheck_saved(original, output, http=http)
    assert review.read_text(encoding="utf-8").endswith("existing;approved;human")
    assert (original / "state.sqlite").read_bytes() == before
    http.get.assert_not_called()


def test_interrupt_exports_partial_copy_with_recoverable_state(tmp_path):
    original = saved_state(tmp_path)
    output = tmp_path / "partial"
    http = site_http()
    response = Response("https://old.example.org/", 403, "", "text/html")
    http.get.side_effect = [response, KeyboardInterrupt()]
    summary = recheck_saved(original, output, http=http, progress=False)
    assert summary["interrupted"]
    assert (output / "state-before.sqlite").is_file()
    assert (output / "state.sqlite").is_file()
    assert (output / "companies.csv").is_file()
    assert (output / "recheck_summary.json").is_file()
    # A fresh directory can recheck the partial working copy without a search.
    finished = recheck_saved(output, tmp_path / "resumed", http=site_http(), progress=False)
    assert not finished["interrupted"]


def test_malformed_page_rejected_before_site_access_or_output(tmp_path):
    original = saved_state(tmp_path)
    store = Store.open_existing(original / "state.sqlite", readonly=False)
    store.save_page("q", 0, {"provider": "apify-google", "organicResults": "bad"})
    store.close()
    http = site_http()
    with pytest.raises(DiscoveryError):
        recheck_saved(original, tmp_path / "invalid", http=http)
    assert not (tmp_path / "invalid").exists()
    http.get.assert_not_called()


def test_missing_state_and_empty_state_do_not_create_outputs(tmp_path):
    with pytest.raises(sqlite3.Error):
        recheck_saved(tmp_path / "missing", tmp_path / "output")
    assert not (tmp_path / "missing").exists()
    store = Store(tmp_path / "empty/state.sqlite", {})
    store.close()
    with pytest.raises(DiscoveryError, match="No saved"):
        recheck_saved(tmp_path / "empty", tmp_path / "output")
    assert not (tmp_path / "output").exists()


def test_cli_needs_no_queries_token_or_matching_search_options(tmp_path, monkeypatch, capsys):
    original = saved_state(tmp_path)
    monkeypatch.delenv("APIFY_API_TOKEN", raising=False)
    monkeypatch.setattr("keyword_searcher.recheck.HttpClient", site_http)
    assert (
        main(
            ["--run-dir", str(original), "--output-dir", str(tmp_path / "output"), "--no-progress"]
        )
        == 0
    )
    assert "original_state_unchanged" in capsys.readouterr().out
