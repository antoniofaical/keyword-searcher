"""Feed saved queries fairly while a single Apify batch runs ahead."""

import time
from dataclasses import dataclass, field

from .apify import ApifyRecoveryRequired
from .models import BudgetExceeded, DiscoveryError
from .search_ahead import SearchAhead
from .workers import WorkerFailure


@dataclass
class Cursor:
    query: str
    index: int
    start: int = 0
    pages: int = 0
    seen: set = field(default_factory=set)
    started: bool = False
    closed: bool = False


def run_parallel(queries, provider, pool, store, max_pages, retry_pending, progress):
    store.ensure_queries(queries)
    cursors = [Cursor(query, index) for index, query in enumerate(queries, 1)]
    by_query = {cursor.query: cursor for cursor in cursors}
    search = SearchAhead(provider, progress)
    exhausted = False

    def begin(cursor):
        if not cursor.started:
            cursor.started = True
            store.progress(cursor.query, "running")
        if progress:
            progress.begin_query(cursor.index, cursor.query)

    def finish(cursor, status, reason=""):
        cursor.closed = True
        pool.close_query(cursor.query, cursor.index, status, reason, store)

    try:
        while any(not cursor.closed for cursor in cursors):
            changed = False
            if search.plan is not None:
                cursor = by_query[search.plan.query]
                try:
                    changed = search.poll()
                except ApifyRecoveryRequired:
                    finish(
                        cursor,
                        "incomplete" if cursor.pages else "failed",
                        "apify_run_needs_recovery",
                    )
                    raise
                except DiscoveryError as exc:
                    finish(cursor, "incomplete" if cursor.pages else "failed", str(exc))
                    search.clear()
                    changed = True
            pool.poll()
            for cursor in cursors:
                if cursor.closed:
                    continue
                payload = store.page(cursor.query, cursor.start)
                if payload is None:
                    if exhausted:
                        finish(
                            cursor,
                            "incomplete" if cursor.pages else "not_started",
                            "apify_page_limit",
                        )
                        changed = True
                    elif search.plan is None:
                        begin(cursor)
                        try:
                            search.begin(cursor.query, cursor.start)
                        except BudgetExceeded:
                            exhausted = True
                        except ApifyRecoveryRequired:
                            finish(
                                cursor,
                                "incomplete" if cursor.pages else "failed",
                                "apify_run_needs_recovery",
                            )
                            raise
                        except DiscoveryError as exc:
                            finish(cursor, "incomplete" if cursor.pages else "failed", str(exc))
                        changed = True
                    continue
                begin(cursor)
                if progress:
                    progress.begin_page(cursor.pages + 1, True)
                try:
                    page = provider.parse(payload, cursor.start)
                    results = [result for result in page.results if result.url not in cursor.seen]
                    cursor.pages += 1
                    if progress:
                        progress.page_ready(len(results))
                    if page.results and not results:
                        finish(cursor, "incomplete", "repeated_results")
                        continue
                    pool.process(cursor.query, results, store, retry_pending=retry_pending)
                    cursor.seen.update(result.url for result in results)
                    if page.next_start is None:
                        finish(cursor, page.end_status, page.end_reason)
                    elif not page.results and not page.search_error:
                        finish(cursor, "incomplete", "empty_page_with_next")
                    elif cursor.pages >= max_pages:
                        finish(cursor, "limited", "max_pages")
                    else:
                        cursor.start = page.next_start
                except WorkerFailure:
                    raise
                except DiscoveryError as exc:
                    finish(cursor, "incomplete", str(exc))
                changed = True
            if not changed:
                # Keep accepting site results throughout the remote Actor wait.
                if pool.pending or pool.queued:
                    pool._wait_one()
                else:
                    time.sleep(0.05)
    finally:
        try:
            search.close()
        finally:
            for cursor in cursors:
                if cursor.started and not cursor.closed:
                    finish(cursor, "interrupted")
