from .models import BudgetExceeded, DiscoveryError


def run(queries, provider, resolver, store, max_pages, retry_pending=False):
    for query in queries:
        store.progress(query, "not_started")
    for query in queries:
        store.progress(query, "running")
        start = 0
        seen = set()
        try:
            for page_index in range(max_pages):
                payload = store.page(query, start)
                if payload is None:
                    payload = provider.search(query, start)
                    store.save_page(query, start, payload)
                page = provider.parse(payload, start)
                new_results = [item for item in page.results if item.url not in seen]
                if page.results and not new_results:
                    store.progress(query, "incomplete", "repeated_results")
                    break
                for result in new_results:
                    cached = store.resolution(query, result.url)
                    if cached is None or (retry_pending and cached.status == "pending"):
                        store.save_resolution(query, result.url, resolver.resolve(result))
                    seen.add(result.url)
                if page.next_start is None:
                    store.progress(query, "complete")
                    break
                if not page.results:
                    store.progress(query, "incomplete", "empty_page_with_next")
                    break
                start = page.next_start
            else:
                store.progress(query, "limited", "max_pages")
        except BudgetExceeded:
            store.progress(query, "incomplete", "search_request_limit")
            # Other queries can still be processed entirely from stored responses.
            continue
        except DiscoveryError as exc:
            store.progress(query, "failed", str(exc))
        except KeyboardInterrupt:
            store.progress(query, "interrupted")
            raise
