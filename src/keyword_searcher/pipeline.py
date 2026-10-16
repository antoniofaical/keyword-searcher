import json

from .apify import ApifyRecoveryRequired
from .models import BudgetExceeded, DiscoveryError
from .workers import ResolutionWorkers


def run(
    queries, provider, resolver, store, max_pages, retry_pending=False, progress=None, workers=1
):
    with ResolutionWorkers(resolver, workers, progress) as pool:
        _run(queries, provider, pool, store, max_pages, retry_pending, progress)


def _run(queries, provider, pool, store, max_pages, retry_pending, progress):
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
        try:
            for page_index in range(max_pages):
                payload = store.page(query, start)
                if budget_exhausted and payload is None:
                    status = "incomplete" if page_index else "not_started"
                    store.progress(query, status, budget_reason)
                    break
                if progress:
                    progress.begin_page(page_index + 1, payload is not None)
                if payload is None:
                    payload = provider.search(query, start)
                    store.save_page(query, start, payload)
                page = provider.parse(payload, start)
                new_results = [item for item in page.results if item.url not in seen]
                if progress:
                    progress.page_ready(len(new_results))
                if page.results and not new_results:
                    status = "incomplete"
                    store.progress(query, status, "repeated_results")
                    break
                pool.process(query, new_results, store, retry_pending=retry_pending)
                seen.update(result.url for result in new_results)
                if page.next_start is None:
                    status = page.end_status
                    store.progress(query, status, page.end_reason)
                    break
                if not page.results and not page.search_error:
                    status = "incomplete"
                    store.progress(query, status, "empty_page_with_next")
                    break
                start = page.next_start
            else:
                status = "limited"
                store.progress(query, status, "max_pages")
        except BudgetExceeded as exc:
            budget_exhausted = True
            budget_reason = str(exc)
            status = "incomplete" if store.page(query, 0) is not None else "not_started"
            store.progress(
                query,
                status,
                budget_reason,
            )
            # Other queries can still be processed entirely from stored responses.
            continue
        except ApifyRecoveryRequired:
            store.progress(query, status, "apify_run_needs_recovery")
            raise
        except DiscoveryError as exc:
            status = "incomplete" if store.page(query, 0) is not None else "failed"
            store.progress(query, status, str(exc))
        except KeyboardInterrupt:
            status = "interrupted"
            store.progress(query, status)
            raise
        finally:
            if progress:
                progress.finish_query(status)


def recheck_sites(queries, provider, resolver, store, progress=None, workers=1):
    """Re-resolve stored organic results, making no search provider requests."""
    with ResolutionWorkers(resolver, workers, progress) as pool:
        _recheck_sites(queries, provider, pool, store, progress)


def _recheck_sites(queries, provider, pool, store, progress):
    for index, query in enumerate(store.saved_queries(queries), start=1):
        if progress:
            progress.begin_query(index, query)
        seen = set()
        for page_number, (start, raw) in enumerate(store.saved_pages(query), start=1):
            if progress:
                progress.begin_page(page_number, True)
            page = provider.parse(json.loads(raw), start)
            results = [result for result in page.results if result.url not in seen]
            if progress:
                progress.page_ready(len(results))
            pool.process(query, results, store, recheck=True)
            seen.update(result.url for result in results)
        if progress:
            progress.finish_query("rechecked")
