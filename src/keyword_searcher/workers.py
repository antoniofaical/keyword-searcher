"""Bounded site resolution; only the caller accesses SQLite and saves decisions."""

from collections import deque
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from urllib.parse import urlsplit


def interleave_hosts(results):
    """Schedule different source hosts first so one host cannot fill the pool."""
    hosts = {}
    for result in results:
        host = (urlsplit(result.url).hostname or "").removeprefix("www.")
        hosts.setdefault(host, deque()).append(result)
    while hosts:
        for host in list(hosts):
            yield hosts[host].popleft()
            if not hosts[host]:
                del hosts[host]


class ResolutionWorkers:
    def __init__(self, resolver, workers=1, progress=None):
        if workers < 1:
            raise ValueError("workers must be greater than zero")
        self.resolver = resolver
        self.workers = workers
        self.progress = progress
        self.executor = None

    def __enter__(self):
        if self.workers > 1:
            self.executor = ThreadPoolExecutor(max_workers=self.workers, thread_name_prefix="site")
        if self.progress:
            self.progress.configure_workers(self.workers)
        return self

    def __exit__(self, *_):
        if self.executor:
            self.executor.shutdown(wait=True, cancel_futures=True)

    def _resolve(self, result):
        token = self.progress.begin_worker(result.url) if self.progress else None
        try:
            return self.resolver.resolve(result)
        finally:
            if self.progress:
                self.progress.end_worker(token)

    def process(self, query, results, store, *, retry_pending=False, recheck=False):
        pending = {}

        def save(result, resolution):
            store.save_resolution(query, result.url, resolution)
            if self.progress:
                self.progress.end_result(resolution)

        def collect(done):
            for future in done:
                result = pending.pop(future)
                save(result, future.result())

        try:
            for result in interleave_hosts(results) if self.executor else results:
                cached = None if recheck else store.resolution(query, result.url)
                reused = cached is not None and not (retry_pending and cached.status == "pending")
                if reused:
                    if self.progress:
                        self.progress.begin_result(result.url, True)
                        self.progress.end_result(cached)
                elif self.executor is None:
                    if self.progress:
                        self.progress.begin_result(result.url, False)
                    save(result, self._resolve(result))
                else:
                    # At most `workers` outstanding tasks; no unbounded executor queue.
                    while len(pending) >= self.workers:
                        done, _ = wait(pending, timeout=0.1, return_when=FIRST_COMPLETED)
                        collect(done)
                    pending[self.executor.submit(self._resolve, result)] = result
            while pending:
                done, _ = wait(pending, timeout=0.1, return_when=FIRST_COMPLETED)
                collect(done)
        except BaseException as exc:
            if isinstance(exc, KeyboardInterrupt):
                cancel = getattr(self.resolver, "cancel", None)
                if callable(cancel):
                    cancel()
            for future in pending:
                future.cancel()
            if pending:
                wait(pending)
            # Preserve successes that completed around the interruption/error.
            for future, result in pending.items():
                if not future.cancelled() and future.exception() is None:
                    save(result, future.result())
            raise
