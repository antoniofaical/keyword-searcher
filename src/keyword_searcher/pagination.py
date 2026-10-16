"""A duplicate Apify page does not exhaust an already imported page chain."""


def stop_on_repeated_page(query, payload, page, store):
    # Legacy/live pagination retains its guard against repeated paid requests.
    if payload.get("provider") != "apify-google":
        return True
    # A terminal Apify page carries the imported batch coverage, even if repeated.
    # A continuation is usable only when its payload is already saved.
    return page.next_start is not None and store.page(query, page.next_start) is None
