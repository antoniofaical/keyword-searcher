import csv
import json
import runpy
from pathlib import Path
from unittest.mock import Mock

import pytest

from keyword_searcher.export import export_results, main
from keyword_searcher.handoff import build_handoff
from keyword_searcher.models import DiscoveryError, Resolution
from keyword_searcher.store import Store

select_sites = runpy.run_path(str(Path(__file__).parent / "fixtures/adherence_contract.py"))[
    "select_sites"
]


def confirmed(query, source, name="Acme", url="https://acme.example/"):
    return query, source, Resolution("confirmed", name, url, "verified", '{"proof":"saved"}')


def complete(*queries):
    return [dict(query=q, status="complete", reason="") for q in queries]


def test_deduplicates_across_queries_with_every_source_and_original_evidence():
    rows = [
        confirmed("b", "https://acme.example/news"),
        confirmed("a", "https://acme.example/product"),
        confirmed("a", "https://acme.example/article"),
        ("a", "https://blocked.example/", Resolution("pending", reason="site_http_403")),
        ("a", "https://news.example/", Resolution("skipped", reason="non_institutional_source")),
    ]
    sites, metadata = build_handoff(rows, complete("a", "b"))
    assert sites == [{"name": "Acme", "url": "https://acme.example/"}]
    assert select_sites(sites, None) == sites
    assert metadata["counts"] == {
        "sites": 1,
        "confirmed_resolutions": 3,
        "duplicates": 2,
        "pending_resolutions": 1,
        "skipped_resolutions": 1,
    }
    sources = metadata["sites"][0]["sources"]
    assert [s["query"] for s in sources] == ["a", "a", "b"]
    assert {s["discovered_url"] for s in sources} == {r[1] for r in rows[:3]}
    assert all(s["resolution"]["evidence"] == rows[0][2].evidence for s in sources)
    assert metadata["coverage"]["search_complete"]
    assert metadata["coverage"]["partial"]  # unresolved website, even with complete search
    assert build_handoff(list(reversed(rows)), complete("b", "a")) == (sites, metadata)


@pytest.mark.parametrize(
    "url",
    [
        "http://www.acme.example/",
        "https://acme.example/?tenant=1#top",
        "https://acme.example",
        "https://acme.example////",
        "https://acme.example/;session=2",
    ],
)
def test_url_variants_match_real_receiver_and_preserve_raw_alternative(url):
    rows = [confirmed("a", "1"), confirmed("b", "2", "Alias", url)]
    raw = [dict(name=r[2].name, url=r[2].url) for r in rows]
    sites, metadata = build_handoff(rows, complete("a", "b"))
    assert sites == select_sites(raw, None)
    assert len(sites) == 1
    assert metadata["sites"][0]["alternatives"] == [raw[1]]


def test_subdomains_and_paths_are_not_fused_but_same_name_keeps_first():
    rows = [
        confirmed("a", "1", "Same", "https://sub.acme.example/"),
        confirmed("b", "2", "Same", "https://acme.example/pt/"),
        confirmed("c", "3", "Independent", "https://acme.example/pt/"),
        confirmed("d", "4", "Other Subdomain", "https://other.acme.example/"),
        confirmed("e", "5", "Other Path", "https://acme.example/en/"),
    ]
    raw = [dict(name=r[2].name, url=r[2].url) for r in rows]
    sites, metadata = build_handoff(rows, complete("a", "b", "c", "d", "e"))
    assert sites == select_sites(raw, None)
    assert len(sites) == 4
    assert metadata["sites"][0]["alternatives"] == [raw[1]]
    assert sum(len(e["sources"]) for e in metadata["sites"]) == 5


@pytest.mark.parametrize("names", [("A B", "A-B"), ("Acme", "ACME"), ("Células", "Celulas")])
def test_directory_collisions_are_reported_instead_of_renaming_or_merging(names):
    rows = [confirmed("a", "1", names[0]), confirmed("b", "2", names[1], "https://other.example/")]
    with pytest.raises(ValueError, match="collide on disk"):
        select_sites([dict(name=r[2].name, url=r[2].url) for r in rows], None)
    with pytest.raises(DiscoveryError, match="collide on disk"):
        build_handoff(rows, complete("a", "b"))


@pytest.mark.parametrize(
    "name,url",
    [("", "https://acme.example/"), ("Acme", "acme.example"), ("!!!", "https://acme.example/")],
)
def test_invalid_confirmed_input_does_not_replace_previous_outputs(tmp_path, name, url):
    store = Store(tmp_path / "run/state.sqlite", {})
    output = tmp_path / "output"
    output.mkdir()
    old = output / "companies.csv"
    old.write_bytes(b"previous CSV")
    store.save_resolution("q", "https://source.example/", Resolution("confirmed", name, url))
    try:
        with pytest.raises(DiscoveryError):
            export_results(store, output)
    finally:
        store.close()
    assert old.read_bytes() == b"previous CSV"
    assert not (output / "handoff.json").exists()
    assert not list(output.glob("*.tmp"))


@pytest.mark.parametrize(
    "status", ["limited", "failed", "interrupted", "not_started", "incomplete"]
)
def test_partial_search_and_explicit_interruption_are_visible(status):
    _, metadata = build_handoff(
        [confirmed("q", "1")], [dict(query="q", status=status, reason="saved")]
    )
    assert metadata["coverage"]["partial"]
    assert not metadata["coverage"]["search_complete"]
    _, interrupted = build_handoff([confirmed("q", "1")], complete("q"), interrupted=True)
    assert interrupted["coverage"]["interrupted"] and interrupted["coverage"]["partial"]


def test_missing_progress_and_zero_confirmed_are_not_claimed_complete():
    _, metadata = build_handoff([confirmed("q", "1")], [])
    assert metadata["coverage"]["queries"][0]["status"] == "unknown"
    assert metadata["coverage"]["partial"]
    sites, metadata = build_handoff([], complete("q"))
    assert sites == select_sites([], None) == []
    assert metadata["counts"]["sites"] == 0
    assert not metadata["coverage"]["partial"]


def test_export_unicode_atomic_resume_and_offline_cli_preserve_database(tmp_path, monkeypatch):
    network = Mock(side_effect=AssertionError("Offline export must not access any network"))
    for target in (
        "keyword_searcher.http.HttpClient.get",
        "keyword_searcher.apify.ApifyTransport.start",
        "keyword_searcher.apify.ApifyTransport.request",
    ):
        monkeypatch.setattr(target, network)
    monkeypatch.delenv("APIFY_API_TOKEN", raising=False)
    run = tmp_path / "run"
    output = tmp_path / "output with spaces"
    store = Store(run / "state.sqlite", {})
    for query, source in (
        ("a", "https://acme.example/1"),
        ("a", "https://acme.example/2"),
        ("b", "https://acme.example/3"),
    ):
        store.save_resolution(query, source, confirmed(query, source, "Órgãos; Research")[2])
        store.progress(query, "complete")
    report = export_results(store, output)
    assert report["exported_rows"] == 2 and report["handoff"]["sites"] == 1
    assert not report["handoff"]["partial"]
    with (output / "companies.csv").open(encoding="utf-8-sig", newline="") as f:
        csv_rows = list(csv.DictReader(f, delimiter=";"))
    assert len(csv_rows) == 2 and csv_rows[0]["CompanyName"] == "Órgãos; Research"
    assert not (output / "handoff.json").read_bytes().startswith(b"\xef\xbb\xbf")
    first = {p.name: p.read_bytes() for p in output.iterdir()}
    store.close()
    state_before = (run / "state.sqlite").read_bytes()
    assert main(["--run-dir", str(run), "--output-dir", str(output)]) == 0
    assert {p.name: p.read_bytes() for p in output.iterdir()} == first
    assert (run / "state.sqlite").read_bytes() == state_before
    network.assert_not_called()
    # A later resolution replaces stale handoff and keeps human-review files intact.
    human = output / "audit_cases.csv"
    human.write_text("CaseID;ReviewOutcome\nexisting;approved", encoding="utf-8")
    store = Store.open_existing(run / "state.sqlite", readonly=False)
    store.save_resolution(
        "c", "https://other.example/", Resolution("confirmed", "New", "https://other.example/")
    )
    store.progress("c", "complete")
    export_results(store, output)
    store.close()
    sites = json.loads((output / "handoff.json").read_text(encoding="utf-8"))
    assert select_sites(sites, None) == sites and len(sites) == 2
    assert human.read_text(encoding="utf-8").endswith("existing;approved")
    assert not list(output.glob("*.tmp"))


def test_missing_state_does_not_create_output_or_database(tmp_path):
    with pytest.raises(SystemExit) as exc:
        main(["--run-dir", str(tmp_path / "missing"), "--output-dir", str(tmp_path / "output")])
    assert exc.value.code == 1
    assert not (tmp_path / "missing").exists()
    assert not (tmp_path / "output").exists()


def test_cli_interruption_and_resume_automatically_export_handoff(tmp_path, monkeypatch):
    from keyword_searcher import cli

    network = Mock(side_effect=AssertionError("Synthetic CLI must not request any network"))
    monkeypatch.setattr("keyword_searcher.http.HttpClient.get", network)
    monkeypatch.setattr("keyword_searcher.apify.ApifyTransport.start", network)
    queries = tmp_path / "queries.txt"
    queries.write_text("q", encoding="utf-8")
    output = tmp_path / "output"
    argv = [
        "--queries",
        str(queries),
        "--run-dir",
        str(tmp_path / "run"),
        "--output-dir",
        str(output),
        "--no-progress",
    ]

    def interrupt(_queries, _provider, _resolver, store, *_args):
        store.save_resolution("q", "https://acme.example/product", confirmed("q", "source")[2])
        store.progress("q", "complete")
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "run", interrupt)
    assert cli.main(argv) == 130
    sites = json.loads((output / "handoff.json").read_text(encoding="utf-8"))
    assert select_sites(sites, None) == sites
    metadata = json.loads((output / "handoff.metadata.json").read_text(encoding="utf-8"))
    assert metadata["coverage"]["interrupted"] and metadata["coverage"]["partial"]
    monkeypatch.setattr(cli, "run", lambda *_args: None)
    assert cli.main(argv) == 0
    assert json.loads((output / "handoff.json").read_text(encoding="utf-8")) == sites
    metadata = json.loads((output / "handoff.metadata.json").read_text(encoding="utf-8"))
    assert not metadata["coverage"]["partial"]
    network.assert_not_called()


def test_handoff_replace_failure_keeps_previous_file_and_cleans_temp(tmp_path, monkeypatch):
    store = Store(tmp_path / "run/state.sqlite", {})
    store.save_resolution("q", "https://acme.example/product", confirmed("q", "source")[2])
    output = tmp_path / "output"
    export_results(store, output)
    before = (output / "handoff.json").read_bytes()
    store.save_resolution(
        "q",
        "https://other.example/product",
        confirmed("q", "source", "Other", "https://other.example/")[2],
    )
    import keyword_searcher.export as exporter

    replace = exporter.os.replace

    def fail(source, target):
        if target.name == "handoff.json":
            raise OSError("synthetic write failure")
        return replace(source, target)

    monkeypatch.setattr(exporter.os, "replace", fail)
    try:
        with pytest.raises(OSError, match="synthetic"):
            export_results(store, output)
    finally:
        store.close()
    assert (output / "handoff.json").read_bytes() == before
    assert not list(output.glob("*.tmp"))
