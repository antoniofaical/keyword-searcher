"""Offline, reproducible audit samples and explicit repair of stored source statuses."""

import argparse
import csv
import hashlib
import json
import random
import sqlite3
import zipfile
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import urlsplit

from .export import export_results, output_directory
from .models import DiscoveryError
from .store import Store

REASONS = (
    "missing_or_ambiguous_company_identity",
    "site_http_403",
    "editorial_or_listing_page",
    "site_http_203",
    "non_institutional_source",
)
FIELDS = (
    "CaseID",
    "Reason",
    "SourceURL",
    "Host",
    "Queries",
    "SearchTitles",
    "SearchSnippets",
    "StoredStatuses",
    "UpdatedStatuses",
    "ReviewOutcome",
    "ReviewerNotes",
)


def sample_cases(resolutions, size, seed):
    """Group repeated source URLs and prefer distinct hosts before filling each stratum."""
    groups = {}
    for query, url, resolution in resolutions:
        if resolution.reason not in REASONS or resolution.status != "pending":
            continue
        key = (resolution.reason, url)
        item = groups.setdefault(key, dict(reason=resolution.reason, url=url, queries=set()))
        item["queries"].add(query)
    strata = defaultdict(list)
    for key in sorted(groups):
        strata[key[0]].append(groups[key])
    selected = []
    randomizer = random.Random(seed)
    for reason in REASONS:
        candidates = strata[reason]
        randomizer.shuffle(candidates)
        chosen, remainder, hosts = [], [], set()
        for item in candidates:
            host = (urlsplit(item["url"]).hostname or "").removeprefix("www.").lower()
            if host not in hosts:
                chosen.append(item)
                hosts.add(host)
            else:
                remainder.append(item)
        selected.extend((chosen + remainder)[:size])
    return selected


def search_details(store):
    details = defaultdict(set)
    for query, raw in store.db.execute("SELECT query,payload FROM pages ORDER BY query,start"):
        payload = json.loads(raw)
        apify = payload.get("provider") == "apify-google"
        for row in payload.get("organicResults" if apify else "organic_results", []):
            url = row.get("url" if apify else "link")
            if isinstance(url, str):
                title = row.get("title", "")
                snippet = row.get("description" if apify else "snippet", "")
                details[query, url].add(
                    (
                        title if isinstance(title, str) else "",
                        snippet if isinstance(snippet, str) else "",
                    )
                )
    return details


def encode(values):
    return json.dumps(sorted(set(values)), ensure_ascii=False)


def generate_audit(run_dir, directory, *, size=20, seed=42, reclassify=False):
    directory = output_directory(directory)
    if directory.exists():
        raise DiscoveryError("Audit output directory already exists; choose a new directory")
    store = Store.open_existing(Path(run_dir) / "state.sqlite", readonly=not reclassify)
    try:
        before = list(store.export_resolutions())
        selected = sample_cases(before, size, seed)
        details = search_details(store)
        directory.mkdir(parents=True, exist_ok=False)
        changed = 0
        if reclassify:
            # SQLite's backup API captures a consistent recoverable copy before any update.
            backup = sqlite3.connect(directory / "state-before.sqlite")
            try:
                store.db.backup(backup)
            finally:
                backup.close()
            changed = store.reclassify_non_institutional()
        report = export_results(store, directory)
        rows = []
        original = {(query, url): item.status for query, url, item in before}
        current = {(query, url): item.status for query, url, item in store.export_resolutions()}
        for case in selected:
            query_urls = [(query, case["url"]) for query in sorted(case["queries"])]
            metadata = {item for key in query_urls for item in details[key]}
            identifier = hashlib.sha256((case["reason"] + "\0" + case["url"]).encode()).hexdigest()
            rows.append(
                dict(
                    CaseID="case-" + identifier[:16],
                    Reason=case["reason"],
                    SourceURL=case["url"],
                    Host=urlsplit(case["url"]).hostname or "",
                    Queries=encode(case["queries"]),
                    SearchTitles=encode(title for title, _ in metadata if title),
                    SearchSnippets=encode(snippet for _, snippet in metadata if snippet),
                    StoredStatuses=encode(original[key] for key in query_urls),
                    UpdatedStatuses=encode(current[key] for key in query_urls),
                    ReviewOutcome="",
                    ReviewerNotes="",
                )
            )
        with (directory / "audit_cases.csv").open("w", encoding="utf-8-sig", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=FIELDS, delimiter=";")
            writer.writeheader()
            writer.writerows(rows)
        summary = dict(
            mode="offline_reclassification" if reclassify else "readonly",
            seed=seed,
            sample_size_per_reason=size,
            sampled=dict(Counter(case["reason"] for case in selected)),
            before=dict(Counter(item.status for _, _, item in before)),
            after=dict(Counter(item.status for _, _, item in store.export_resolutions())),
            pending_reasons_before=dict(
                Counter(item.reason for _, _, item in before if item.status == "pending")
            ),
            reclassified=changed,
            exported_rows=report["exported_rows"],
            backup="state-before.sqlite" if reclassify else None,
            notes=[
                "No network requests or Apify searches were performed.",
                "Samples are grouped by reason and source URL; query occurrences are retained.",
                "Distinct hosts are preferred; this is a diagnostic sample, not a prevalence estimate.",
                "ReviewOutcome and ReviewerNotes are reserved for human review.",
                "The ZIP excludes SQLite state and backup files.",
            ],
        )
        (directory / "audit_summary.json").write_text(
            json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        with zipfile.ZipFile(directory / "audit_bundle.zip", "x", zipfile.ZIP_DEFLATED) as bundle:
            for name in (
                "audit_cases.csv",
                "audit_summary.json",
                "companies.csv",
                "pending.json",
                "report.json",
            ):
                bundle.write(directory / name, arcname=name)
        return summary
    finally:
        store.close()


def positive(value):
    size = int(value)
    if size < 1:
        raise argparse.ArgumentTypeError("sample size must be positive")
    return size


def main(argv=None):
    parser = argparse.ArgumentParser(description="Offline audit of stored website resolutions")
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--sample-size", type=positive, default=20)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--reclassify-editorial", action="store_true")
    args = parser.parse_args(argv)
    try:
        summary = generate_audit(
            args.run_dir,
            args.output_dir,
            size=args.sample_size,
            seed=args.seed,
            reclassify=args.reclassify_editorial,
        )
    except (DiscoveryError, OSError, sqlite3.Error, ValueError) as exc:
        parser.exit(1, f"Error: {exc}\n")
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    print(f"Audit bundle: {args.output_dir.resolve() / 'audit_bundle.zip'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
