"""One background Apify request at a time; all state changes stay on the caller."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace


class SearchAhead:
    def __init__(self, provider, progress=None):
        self.provider = provider
        self.progress = progress
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="apify")
        self.plan = None
        self.future = None
        self.starting = False

    def _status(self, text):
        if self.progress:
            self.progress.search_changed(text)

    def begin(self, query, start):
        assert self.plan is None
        self.plan = self.provider.plan_search(query, start)
        self.starting = self.plan.run_id is None
        if self.starting:
            self._status(f"starting batch {self.plan.batch_id}")
            self.future = self.executor.submit(self.provider.start_search, self.plan)
        else:
            self._fetch(self.plan.run_id)

    def _fetch(self, run_id):
        self.plan = replace(self.plan, run_id=run_id)
        self.starting = False
        self._status(f"waiting for run {run_id}")
        self.future = self.executor.submit(self.provider.fetch_search, run_id)

    def poll(self):
        if self.future is None or not self.future.done():
            return False
        if self.starting:
            run_id = self.future.result()
            # Persist the paid run ID before any further network operation.
            self.provider.store.apify_set_run(self.plan.batch_id, run_id)
            self._fetch(run_id)
            return True
        evidence, items = self.future.result()
        self.provider.import_search(self.plan, evidence, items)
        self.clear()
        return True

    def clear(self):
        self.plan = self.future = None
        self._status("idle")

    def close(self):
        try:
            if self.future is not None and self.starting:
                # Never abandon a successful POST response on Ctrl+C.
                try:
                    run_id = self.future.result()
                except BaseException:
                    pass  # The reserved batch remains unconfirmed, with no automatic retry.
                else:
                    self.provider.store.apify_set_run(self.plan.batch_id, run_id)
            if self.future is not None:
                cancel = getattr(self.provider.transport, "cancel", None)
                if callable(cancel) and not self.starting:
                    cancel()
                self.future.cancel()
        finally:
            self.executor.shutdown(wait=True, cancel_futures=True)
