import argparse
import json
import math
import os
import sqlite3
from pathlib import Path

from . import __version__
from .apify import ApifyGoogle, ApifyTransport
from .export import export_results, output_directory
from .http import HttpClient
from .models import DiscoveryError
from .pipeline import recheck_sites, run
from .progress import TerminalProgress
from .resolve import WebsiteResolver
from .store import Store


def positive(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return number


def positive_float(value):
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("must be a positive finite number")
    return number


def read_queries(path):
    return list(
        dict.fromkeys(
            line.strip()
            for line in path.read_text(encoding="utf-8-sig").splitlines()
            if line.strip()
        )
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description="Google queries to institutional entrypoints")
    parser.add_argument(
        "--queries", required=True, type=Path, help="UTF-8 text, one query per line"
    )
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument(
        "--output-dir", required=True, type=Path, help="CSV/JSON destination outside the repository"
    )
    parser.add_argument("--provider", choices=["apify"], default="apify")
    parser.add_argument("--max-pages", type=positive, default=3)
    parser.add_argument("--country", default="br")
    parser.add_argument("--language", default="pt")
    parser.add_argument("--location", default="")
    parser.add_argument("--retry-pending", action="store_true")
    parser.add_argument(
        "--max-apify-pages",
        type=positive,
        default=900,
        help="Cumulative page reservations across Apify batches (default: 900)",
    )
    parser.add_argument("--apify-batch-size", type=positive, default=20)
    parser.add_argument(
        "--apify-run-cost-limit-usd",
        type=positive_float,
        default=1.0,
        help="Maximum charge of each Apify Actor run (default: USD 1)",
    )
    parser.add_argument(
        "--apify-recover-run-id",
        default="",
        help="Attach the run ID from Apify Console to an unconfirmed batch",
    )
    parser.add_argument(
        "--apify-abandon-batch",
        action="store_true",
        help="Abandon an unfinished Apify batch after checking its run in Apify Console",
    )
    parser.add_argument(
        "--recheck-sites",
        action="store_true",
        help="Revisit saved result sites without any new Google searches",
    )
    parser.add_argument("--no-progress", action="store_true")
    args = parser.parse_args(argv)
    store = None
    try:
        args.output_dir = output_directory(args.output_dir)
        args.run_dir = args.run_dir.expanduser().resolve()
        queries = read_queries(args.queries)
        if not queries:
            raise DiscoveryError("The queries file is empty")
        if args.apify_abandon_batch and args.apify_recover_run_id:
            raise DiscoveryError("Choose either --apify-abandon-batch or --apify-recover-run-id")
        config = dict(
            version=__version__,
            provider="apify-google",
            queries=queries,
            country=args.country,
            language=args.language,
            location=args.location,
            max_pages=args.max_pages,
        )
        store = Store(args.run_dir / "state.sqlite", config)
        store.ensure_queries(queries)
        if args.apify_recover_run_id:
            store.apify_recover_run(args.apify_recover_run_id)
        if args.apify_abandon_batch:
            store.apify_abandon_batch()
        http = HttpClient()
        provider = ApifyGoogle(
            ApifyTransport(os.environ.get("APIFY_API_TOKEN", "")),
            store,
            queries,
            args.country,
            args.language,
            args.max_pages,
            args.max_apify_pages,
            args.apify_batch_size,
            args.apify_run_cost_limit_usd,
        )
        if args.location and not args.recheck_sites:
            raise DiscoveryError("Apify does not support --location; cached recheck is available")
        interrupted = False
        try:
            visible_queries = (
                len(store.saved_queries(queries)) if args.recheck_sites else len(queries)
            )
            with TerminalProgress(
                visible_queries, args.max_pages, enabled=not args.no_progress
            ) as progress:
                resolver = WebsiteResolver(http)
                if args.recheck_sites:
                    recheck_sites(queries, provider, resolver, store, progress)
                else:
                    run(
                        queries,
                        provider,
                        resolver,
                        store,
                        args.max_pages,
                        args.retry_pending,
                        progress,
                    )
        except KeyboardInterrupt:
            interrupted = True
        finally:
            report = export_results(store, args.output_dir)
        print(
            json.dumps(
                {key: value for key, value in report.items() if key != "queries"},
                ensure_ascii=True,
                indent=2,
            )
        )
        print(f"State: {args.run_dir / 'state.sqlite'}")
        print(f"Detailed coverage: {args.output_dir / 'report.json'}")
        if interrupted:
            return 130
        incomplete = any(q["status"] != "complete" for q in report["queries"])
        return 2 if incomplete or report["pending"] else 0
    except (DiscoveryError, OSError, sqlite3.Error) as exc:
        parser.exit(1, f"Error: {exc}\n")
    finally:
        if store:
            store.close()
