"""Recheck a recoverable copy of saved search state, without an Apify transport."""

import argparse
import json
import sqlite3
from collections import Counter
from dataclasses import asdict
from pathlib import Path

from .apify import ApifyGoogle
from .export import export_results, output_directory
from .http import HttpClient
from .models import DiscoveryError
from .pipeline import recheck_sites
from .progress import TerminalProgress
from .resolve import WebsiteResolver
from .store import Store


def recheck_saved(run_dir, directory, *, http=None, progress=True):
    directory = output_directory(directory)
    if directory.exists():
        raise DiscoveryError("Recheck output already exists; choose a new directory")
    source = Store.open_existing(Path(run_dir) / "state.sqlite")
    try:
        # Validate every stored page before creating output or visiting any site.
        queries = [
            row[0] for row in source.db.execute("SELECT DISTINCT query FROM pages ORDER BY query")
        ]
        if not queries:
            raise DiscoveryError("No saved search pages to recheck")
        maximum = 1
        for query in queries:
            pages = source.saved_pages(query)
            maximum = max(maximum, len(pages))
            for start, raw in pages:
                ApifyGoogle.parse(json.loads(raw), start)
        directory.mkdir(parents=True, exist_ok=False)
        backup = sqlite3.connect(directory / "state-before.sqlite")
        try:
            source.db.backup(backup)
            working = sqlite3.connect(directory / "state.sqlite")
            try:
                backup.backup(working)
            finally:
                working.close()
        finally:
            backup.close()
    finally:
        source.close()
    store = Store.open_existing(directory / "state.sqlite", readonly=False)
    try:
        before = {(q, url): asdict(item) for q, url, item in store.export_resolutions()}
        interrupted = False
        try:
            with TerminalProgress(len(queries), maximum, enabled=progress) as display:
                recheck_sites(
                    queries, ApifyGoogle, WebsiteResolver(http or HttpClient()), store, display
                )
        except KeyboardInterrupt:
            interrupted = True
        finally:
            report = export_results(store, directory)
        after = {(q, url): asdict(item) for q, url, item in store.export_resolutions()}
        fields = ("status", "name", "url", "reason")
        changes = [
            dict(query=q, source_url=url, before=before.get((q, url)), after=item)
            for (q, url), item in after.items()
            if any(before.get((q, url), {}).get(field) != item[field] for field in fields)
        ]
        summary = dict(
            mode="saved_sites_recheck",
            policy_version=2,
            interrupted=interrupted,
            before=dict(Counter(x["status"] for x in before.values())),
            after=dict(Counter(x["status"] for x in after.values())),
            changed=len(changes),
            exported_rows=report["exported_rows"],
            source_state=str((Path(run_dir) / "state.sqlite").resolve()),
            original_state_unchanged=True,
            backup="state-before.sqlite",
            rechecked_state="state.sqlite",
            notes=[
                "Only public website GETs; no Apify requests or searches.",
                "Search pages, configuration, progress, run IDs and reservations are preserved.",
                "Partial results are exported on interruption; recheck this copied state in a new directory.",
            ],
        )
        for filename, value in (
            ("recheck_summary.json", summary),
            ("recheck_changes.json", changes),
        ):
            (directory / filename).write_text(
                json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8"
            )
        return summary
    finally:
        store.close()


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Recheck copied search state without queries or Apify token"
    )
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--no-progress", action="store_true")
    args = parser.parse_args(argv)
    try:
        summary = recheck_saved(args.run_dir, args.output_dir, progress=not args.no_progress)
    except (DiscoveryError, OSError, sqlite3.Error, ValueError) as exc:
        parser.exit(1, f"Error: {exc}\n")
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    print(f"Recheck output: {args.output_dir.resolve()}")
    return 130 if summary["interrupted"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
