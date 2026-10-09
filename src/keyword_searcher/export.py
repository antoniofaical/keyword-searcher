"""Export regenerable results to an explicit destination outside source checkouts."""

import argparse
import csv
import json
import os
import sqlite3
from collections import Counter
from dataclasses import asdict
from pathlib import Path

from .handoff import build_handoff
from .models import DiscoveryError


def checkout_roots(path):
    for root in (path, *path.parents):
        if (root / ".git").exists():
            yield root
        elif (root / "pyproject.toml").is_file() and (root / "src/keyword_searcher").is_dir():
            yield root


def output_directory(path):
    target = Path(path).expanduser().resolve()
    package = Path(__file__).resolve().parent
    roots = {package, *checkout_roots(package), *checkout_roots(Path.cwd().resolve())}
    for root in roots:
        if target.is_relative_to(root):
            raise DiscoveryError(
                "--output-dir must be outside the repository and installed package"
            )
    return target


def export_results(store, directory, *, interrupted=False):
    directory = output_directory(directory)
    rows = list(store.export_resolutions())
    statuses = store.export_progress()
    # Validate the consumer contract before replacing any existing output.
    sites, handoff_metadata = build_handoff(rows, statuses, interrupted=interrupted)
    directory.mkdir(parents=True, exist_ok=True)
    temp = directory / "companies.csv.tmp"
    seen = set()
    pending = []
    try:
        with temp.open("w", encoding="utf-8-sig", newline="") as file:
            writer = csv.writer(file, delimiter=";")
            writer.writerow(["CompanyName", "URL", "SearchQuery"])
            for query, source, item in rows:
                if item.status == "confirmed":
                    key = (item.url, query)
                    if key not in seen:
                        writer.writerow([item.name, item.url, query])
                        seen.add(key)
                else:
                    pending.append(dict(query=query, discovered_url=source, **asdict(item)))
        os.replace(temp, directory / "companies.csv")
        report = dict(
            **store.export_metadata(),
            exported_rows=len(seen),
            query_status_counts=dict(Counter(q["status"] for q in statuses)),
            queries=statuses,
            pending=sum(x["status"] == "pending" for x in pending),
            skipped=sum(x["status"] == "skipped" for x in pending),
            handoff=dict(
                sites=len(sites),
                duplicates=handoff_metadata["counts"]["duplicates"],
                partial=handoff_metadata["coverage"]["partial"],
                sites_file="handoff.json",
                metadata_file="handoff.metadata.json",
            ),
        )
        for name, data in [
            ("handoff.json", sites),
            ("handoff.metadata.json", handoff_metadata),
            ("pending.json", pending),
            ("report.json", report),
        ]:
            temp = directory / (name + ".tmp")
            temp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            os.replace(temp, directory / name)
        return report
    finally:
        temp.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Export saved state offline, including classifier handoff"
    )
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        directory = output_directory(args.output_dir)
        from .store import Store

        store = Store.open_existing(args.run_dir / "state.sqlite")
        try:
            report = export_results(store, directory)
        finally:
            store.close()
    except (DiscoveryError, OSError, sqlite3.Error) as exc:
        parser.exit(1, f"Error: {exc}\n")
    print(json.dumps(report["handoff"], indent=2, ensure_ascii=False))
    print(f"Handoff: {directory / 'handoff.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
