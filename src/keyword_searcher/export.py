"""Export regenerable results to an explicit destination outside source checkouts."""

import csv
import json
import os
from collections import Counter
from dataclasses import asdict
from pathlib import Path

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


def export_results(store, directory):
    directory = output_directory(directory)
    directory.mkdir(parents=True, exist_ok=True)
    temp = directory / "companies.csv.tmp"
    seen = set()
    pending = []
    try:
        with temp.open("w", encoding="utf-8-sig", newline="") as file:
            writer = csv.writer(file, delimiter=";")
            writer.writerow(["CompanyName", "URL", "SearchQuery"])
            for query, source, item in store.export_resolutions():
                if item.status == "confirmed":
                    key = (item.url, query)
                    if key not in seen:
                        writer.writerow([item.name, item.url, query])
                        seen.add(key)
                else:
                    pending.append(dict(query=query, discovered_url=source, **asdict(item)))
        os.replace(temp, directory / "companies.csv")
        statuses = store.export_progress()
        report = dict(
            **store.export_metadata(),
            exported_rows=len(seen),
            query_status_counts=dict(Counter(q["status"] for q in statuses)),
            queries=statuses,
            pending=sum(x["status"] == "pending" for x in pending),
            skipped=sum(x["status"] == "skipped" for x in pending),
        )
        for name, data in [("report.json", report), ("pending.json", pending)]:
            temp = directory / (name + ".tmp")
            temp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            os.replace(temp, directory / name)
        return report
    finally:
        temp.unlink(missing_ok=True)
