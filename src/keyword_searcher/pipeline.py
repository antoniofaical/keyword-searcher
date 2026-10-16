import json

from .apify import ApifyGoogle, ApifyRecoveryRequired
from .models import BudgetExceeded, DiscoveryError
from .pagination import stop_on_repeated_page
from .parallel_pipeline import run_parallel
from .workers import ResolutionWorkers, WorkerFailure


def run(
    queries,
    provider,
    resolver,
    store,
    max_pages,
    retry_pending=False,
    progress=None,
    workers=1,
    *,
    saved_searches_only=False,
):
    with ResolutionWorkers(resolver, workers, progress) as pool:
        execute = run_parallel if workers > 1 and isinstance(provider, ApifyGoogle) else _run
        execute(
            queries,
            provider,
            pool,
            store,
            max_pages,
            retry_pending,
            progress,
            saved_searches_only=saved_searches_only,
        )


def _run(
    queries, provider, pool, store, max_pages, retry_pending, progress, *, saved_searches_only=False
):
    store.ensure_queries(queries)
    budget_exhausted = False
    budget_reason = "apify_page_limit"
    for query_index, query in enumerate(queries, start=1):
        if budget_exhausted and store.page(query, 0) is None:
            continue
        if progress:
            progress.begin_query(query_index, query)
        store.progress(query, "running")
        start = 0
        seen = set()
        status = "failed"
        reason = ""
        try:
            for page_index in range(max_pages):
                payload = store.page(query, start)
                if payload is None and saved_searches_only:
                    status = "incomplete" if page_index else "not_started"
                    reason = "saved_search_page_missing"
                    break
                if budget_exhausted and payload is None:
                    status = "incomplete" if page_index else "not_started"
                    reason = budget_reason
                    break
                if payload is None:
                    # Finish the available site work before blocking on a paid batch.
                    pool.drain()
                if progress:
                    progress.begin_page(page_index + 1, payload is not None)
                if payload is None:
                    payload = provider.search(query, start)
                    store.save_page(query, start, payload)
                page = provider.parse(payload, start)
                new_results = [item for item in page.results if item.url not in seen]
                if progress:
                    progress.page_ready(len(new_results))
                if (
                    page.results
                    and not new_results
                    and stop_on_repeated_page(query, payload, page, store)
                ):
                    status = "incomplete"
                    reason = "repeated_results"
                    break
                pool.process(query, new_results, store, retry_pending=retry_pending)
                seen.update(result.url for result in new_results)
                if page.next_start is None:
                    status = page.end_status
                    reason = page.end_reason
                    break
                if not page.results and not page.search_error:
                    status = "incomplete"
                    reason = "empty_page_with_next"
                    break
                start = page.next_start
            else:
                status = "limited"
                reason = "max_pages"
        except BudgetExceeded as exc:
            budget_exhausted = True
            budget_reason = str(exc)
            status = "incomplete" if store.page(query, 0) is not None else "not_started"
            reason = budget_reason
            # Other queries can still be processed entirely from stored responses.
            continue
        except ApifyRecoveryRequired:
            reason = "apify_run_needs_recovery"
            raise
        except WorkerFailure:
            status, reason = "interrupted", ""
            raise
        except DiscoveryError as exc:
            status = "incomplete" if store.page(query, 0) is not None else "failed"
            reason = str(exc)
        except KeyboardInterrupt:
            status = "interrupted"
            raise
        finally:
            pool.close_query(query, query_index, status, reason, store)


def recheck_sites(queries, provider, resolver, store, progress=None, workers=1):
    """Re-resolve stored organic results, making no search provider requests."""
    with ResolutionWorkers(resolver, workers, progress) as pool:
        _recheck_sites(queries, provider, pool, store, progress)


def _recheck_sites(queries, provider, pool, store, progress):
    for index, query in enumerate(store.saved_queries(queries), start=1):
        if progress:
            progress.begin_query(index, query)
        seen = set()
        try:
            for page_number, (start, raw) in enumerate(store.saved_pages(query), start=1):
                if progress:
                    progress.begin_page(page_number, True)
                page = provider.parse(json.loads(raw), start)
                results = [result for result in page.results if result.url not in seen]
                if progress:
                    progress.page_ready(len(results))
                pool.process(query, results, store, recheck=True)
                seen.update(result.url for result in results)
        finally:
            pool.close_query(query, index, "rechecked", "", store, persist=False)
