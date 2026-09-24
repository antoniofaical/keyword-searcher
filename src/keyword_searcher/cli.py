import argparse
import json
import os
from pathlib import Path

from . import __version__
from .http import HttpClient
from .models import DiscoveryError
from .pipeline import run
from .progress import TerminalProgress
from .resolve import WebsiteResolver
from .search import SerpApi
from .store import Store


def positive(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be greater than zero")
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
    parser.add_argument("--max-pages", type=positive, default=3)
    parser.add_argument(
        "--max-search-requests",
        type=positive,
        default=20,
        help="Cumulative API attempt ceiling for this run, including retries",
    )
    parser.add_argument("--country", default="br")
    parser.add_argument("--language", default="pt")
    parser.add_argument("--location", default="")
    parser.add_argument("--retry-pending", action="store_true")
    parser.add_argument("--no-progress", action="store_true")
    args = parser.parse_args(argv)
    store = None
    try:
        queries = read_queries(args.queries)
        if not queries:
            raise DiscoveryError("The queries file is empty")
        config = dict(
            version=__version__,
            provider="serpapi-google",
            queries=queries,
            country=args.country,
            language=args.language,
            location=args.location,
            max_pages=args.max_pages,
        )
        store = Store(args.run_dir / "state.sqlite", config)
        http = HttpClient()
        provider = SerpApi(
            http,
            os.environ.get("SERPAPI_API_KEY", ""),
            args.country,
            args.language,
            args.location,
            lambda: store.reserve(args.max_search_requests),
        )
        interrupted = False
        try:
            with TerminalProgress(
                len(queries), args.max_pages, enabled=not args.no_progress
            ) as progress:
                run(
                    queries,
                    provider,
                    WebsiteResolver(http),
                    store,
                    args.max_pages,
                    args.retry_pending,
                    progress,
                )
        except KeyboardInterrupt:
            interrupted = True
        finally:
            report = store.export(args.run_dir)
        print(json.dumps(report, ensure_ascii=True, indent=2))
        if interrupted:
            return 130
        incomplete = any(q["status"] != "complete" for q in report["queries"])
        return 2 if incomplete or report["pending"] else 0
    except (DiscoveryError, OSError) as exc:
        parser.exit(1, f"Error: {exc}\n")
    finally:
        if store:
            store.close()
