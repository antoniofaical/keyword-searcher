import csv
import json
import sqlite3
import zipfile
from dataclasses import asdict
from unittest.mock import Mock

import pytest

from keyword_searcher.audit import generate_audit, main, sample_cases
from keyword_searcher.models import DiscoveryError, Resolution
from keyword_searcher.store import Store


def state(tmp_path):
    directory = tmp_path / "run"
    store = Store(directory / "state.sqlite", {"language": "pt"})
    store.ensure_queries(["q"])
    rows = [
        ("company", Resolution("confirmed", "Company", "https://company.example.org/")),
        ("article", Resolution("pending", reason="editorial_or_listing_page")),
        ("blocked", Resolution("pending", reason="site_http_403")),
        ("missing", Resolution("pending", reason="missing_or_ambiguous_company_identity")),
        ("proxy", Resolution("pending", reason="site_http_203")),
        ("directory", Resolution("skipped", reason="non_institutional_source")),
    ]
    for host, item in rows:
        store.save_resolution("q", f"https://{host}.example.org/", item)
    store.save_page(
        "q",
        0,
        {
            "provider": "apify-google",
            "organicResults": [
                {
                    "url": "https://missing.example.org/",
                    "title": "=untrusted title",
                    "description": "Text",
                }
            ],
        },
    )
    store.progress("q", "limited", "max_pages")
    store.apify_plan_batch(["q"], 5, 100)
    return directory, store


def test_readonly_audit_does_not_change_state_or_make_network_requests(tmp_path, monkeypatch):
    directory, store = state(tmp_path)
    store.close()
    before = (directory / "state.sqlite").read_bytes()
    network = Mock(side_effect=AssertionError("Offline audit must not access the network"))
    monkeypatch.setattr("keyword_searcher.http.HttpClient.get", network)
    monkeypatch.setattr("keyword_searcher.apify.ApifyTransport.start", network)
    output = tmp_path / "audit"
    summary = generate_audit(directory, output)
    assert (directory / "state.sqlite").read_bytes() == before
    assert summary["before"] == summary["after"]
    assert summary["reclassified"] == 0
    assert summary["exported_rows"] == 1
    assert not (output / "state-before.sqlite").exists()
    with (output / "audit_cases.csv").open(encoding="utf-8-sig", newline="") as file:
        cases = list(csv.DictReader(file, delimiter=";"))
    assert len(cases) == 4
    assert all(row["ReviewOutcome"] == row["ReviewerNotes"] == "" for row in cases)
    missing = next(row for row in cases if row["Reason"] == "missing_or_ambiguous_company_identity")
    assert json.loads(missing["SearchTitles"]) == ["=untrusted title"]
    network.assert_not_called()
    with zipfile.ZipFile(output / "audit_bundle.zip") as bundle:
        assert set(bundle.namelist()) == {
            "audit_cases.csv",
            "audit_summary.json",
            "companies.csv",
            "pending.json",
            "report.json",
            "handoff.json",
            "handoff.metadata.json",
        }


def test_reclassification_has_consistent_backup_and_preserves_other_decisions(tmp_path):
    directory, store = state(tmp_path)
    before = {url: asdict(item) for _, url, item in store.export_resolutions()}
    metadata = store.export_metadata()
    progress = store.export_progress()
    store.close()
    summary = generate_audit(directory, tmp_path / "audit", reclassify=True)
    assert summary["reclassified"] == 1
    assert summary["before"] == {"confirmed": 1, "pending": 4, "skipped": 1}
    assert summary["after"] == {"confirmed": 1, "pending": 3, "skipped": 2}
    backup = Store.open_existing(tmp_path / "audit/state-before.sqlite")
    assert {url: asdict(item) for _, url, item in backup.export_resolutions()} == before
    backup.close()
    current = Store.open_existing(directory / "state.sqlite")
    assert current.export_metadata() == metadata
    assert current.export_progress() == progress
    for _, url, item in current.export_resolutions():
        expected = dict(before[url])
        if url == "https://article.example.org/":
            expected["status"] = "skipped"
        assert asdict(item) == expected
    current.close()
    second = generate_audit(directory, tmp_path / "second", reclassify=True)
    assert second["reclassified"] == 0
    assert (tmp_path / "second/companies.csv").read_bytes() == (
        tmp_path / "audit/companies.csv"
    ).read_bytes()


def test_samples_group_queries_and_cover_different_hosts_deterministically():
    rows = [
        (
            f"q{index}",
            f"https://same.example.org/{index}",
            Resolution("pending", reason="site_http_403"),
        )
        for index in range(10)
    ]
    rows.extend(
        [
            ("q", "https://different.example.org/", Resolution("pending", reason="site_http_403")),
            ("q2", "https://different.example.org/", Resolution("pending", reason="site_http_403")),
        ]
    )
    cases = sample_cases(rows, 2, 42)
    assert cases == sample_cases(list(reversed(rows)), 2, 42)
    assert len(cases) == 2
    different = next(row for row in cases if row["url"] == "https://different.example.org/")
    assert different["queries"] == {"q", "q2"}


def test_existing_audit_keeps_human_review_fields_and_does_not_reclassify(tmp_path):
    directory, store = state(tmp_path)
    store.close()
    output = tmp_path / "audit"
    output.mkdir()
    reviewed = output / "audit_cases.csv"
    reviewed.write_text("human review", encoding="utf-8")
    with pytest.raises(DiscoveryError, match="already exists"):
        generate_audit(directory, output, reclassify=True)
    assert reviewed.read_text(encoding="utf-8") == "human review"
    current = Store.open_existing(directory / "state.sqlite")
    assert current.resolution("q", "https://article.example.org/").status == "pending"
    current.close()


def test_missing_state_is_not_created_and_future_schema_is_not_migrated(tmp_path):
    missing = tmp_path / "absent/state.sqlite"
    with pytest.raises(sqlite3.Error):
        Store.open_existing(missing)
    assert not missing.exists()
    directory, store = state(tmp_path)
    with store.db:
        store.db.execute("UPDATE meta SET value='999' WHERE key='schema_version'")
    store.close()
    with pytest.raises(DiscoveryError, match="schema 2"):
        generate_audit(directory, tmp_path / "audit", reclassify=True)
    assert not (tmp_path / "audit").exists()


def test_audit_cli_reads_existing_state_without_queries_or_token(tmp_path, monkeypatch, capsys):
    directory, store = state(tmp_path)
    store.close()
    monkeypatch.delenv("APIFY_API_TOKEN", raising=False)
    assert (
        main(
            [
                "--run-dir",
                str(directory),
                "--output-dir",
                str(tmp_path / "audit"),
                "--sample-size",
                "1",
                "--reclassify-editorial",
            ]
        )
        == 0
    )
    assert "audit_bundle.zip" in capsys.readouterr().out
