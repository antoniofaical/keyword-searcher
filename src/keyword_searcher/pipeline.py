import json

from .models import BudgetExceeded, DiscoveryError


def run(queries, provider, resolver, store, max_pages, retry_pending=False, progress=None):
    store.ensure_queries(queries)
    budget_exhausted = False
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
                    store.progress(query, status, "search_request_limit")
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
                for result in new_results:
                    cached = store.resolution(query, result.url)
                    reused = cached is not None and not (
                        retry_pending and cached.status == "pending"
                    )
                    if progress:
                        progress.begin_result(result.url, reused)
                    if cached is None or (retry_pending and cached.status == "pending"):
                        cached = resolver.resolve(result)
                        store.save_resolution(query, result.url, cached)
                    seen.add(result.url)
                    if progress:
                        progress.end_result(cached)
                if page.next_start is None:
                    status = "complete"
                    store.progress(query, status)
                    break
                if not page.results:
                    status = "incomplete"
                    store.progress(query, status, "empty_page_with_next")
                    break
                start = page.next_start
            else:
                status = "limited"
                store.progress(query, status, "max_pages")
        except BudgetExceeded:
            budget_exhausted = True
            status = "incomplete" if store.page(query, 0) is not None else "not_started"
            store.progress(query, status, "search_request_limit")
            # Other queries can still be processed entirely from stored responses.
            continue
        except DiscoveryError as exc:
            store.progress(query, status, str(exc))
        except KeyboardInterrupt:
            status = "interrupted"
            store.progress(query, status)
            raise
        finally:
            if progress:
                progress.finish_query(status)


def recheck_sites(queries, provider, resolver, store, progress=None):
    """Re-resolve stored organic results, making no search provider requests."""
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
            for result in results:
                if progress:
                    progress.begin_result(result.url, False)
                resolution = resolver.resolve(result)
                store.save_resolution(query, result.url, resolution)
                seen.add(result.url)
                if progress:
                    progress.end_result(resolution)
        if progress:
            progress.finish_query("rechecked")
