"""Bounded site resolution; only the caller accesses SQLite and saves decisions."""

from collections import deque
from concurrent.futures import FIRST_COMPLETED, CancelledError, ThreadPoolExecutor, wait
from dataclasses import dataclass
from urllib.parse import urlsplit

from .job_queue import JobQueue
from .models import DiscoveryError


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


@dataclass(frozen=True)
class Job:
    query: str
    result: object

    @property
    def host(self):
        return (urlsplit(self.result.url).hostname or "").removeprefix("www.")


class WorkerFailure(DiscoveryError):
    pass


class ResolutionWorkers:
    def __init__(self, resolver, workers=1, progress=None):
        if workers < 1:
            raise ValueError("workers must be greater than zero")
        self.resolver = resolver
        self.workers = workers
        self.progress = progress
        self.executor = None
        self.store = None
        self.pending = {}
        self.queued = JobQueue()
        self.busy_hosts = set()
        self.counts = {}
        self.closed = {}
        self.finished = set()
        self.interrupted = set()
        self.failed = set()

    def __enter__(self):
        if self.workers > 1:
            self.executor = ThreadPoolExecutor(max_workers=self.workers, thread_name_prefix="site")
        if self.progress:
            self.progress.configure_workers(self.workers, global_mode=self.workers > 1)
        return self

    def __exit__(self, exc_type, *_):
        try:
            if exc_type is None:
                try:
                    self.drain()
                except BaseException:
                    self._abort()
                    raise
            else:
                self._abort()
        finally:
            if self.executor:
                self.executor.shutdown(wait=True, cancel_futures=True)
            self.queued.close()

    def _resolve(self, result):
        token = self.progress.begin_worker(result.url) if self.progress else None
        try:
            return self.resolver.resolve(result)
        finally:
            if self.progress:
                self.progress.end_worker(token)

    def process(self, query, results, store, *, retry_pending=False, recheck=False):
        self.store = store
        self._collect([future for future in self.pending if future.done()])
        if self.executor:
            self._schedule()
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
                self._save(Job(query, result), self._resolve(result))
            else:
                # Backlog is on disk: busy hosts cannot stop reading other queries.
                self.counts[query] = self.counts.get(query, 0) + 1
                self.queued.append(Job(query, result))
                self._schedule()

    def close_query(self, query, index, status, reason, store, *, persist=True):
        self.store = store
        self.closed[query] = (index, status, reason, persist)
        self._finish_query(query)

    def _finish_query(self, query):
        if query not in self.closed or query in self.finished or self.counts.get(query, 0):
            return
        index, status, reason, persist = self.closed[query]
        if query in self.failed:
            status, reason = "failed", "site_worker_failed"
        elif query in self.interrupted:
            status, reason = "interrupted", ""
        if persist:
            self.store.progress(query, status, reason)
        self.finished.add(query)
        if self.progress:
            self.progress.finish_query(status, index=index)

    def _save(self, job, resolution):
        self.store.save_resolution(job.query, job.result.url, resolution)
        if self.progress:
            self.progress.end_result(resolution)

    def _schedule(self):
        # Source hosts waiting on their turn stay outside the executor.
        while len(self.pending) < self.workers:
            item = self.queued.pop()
            if item is None:
                break
            job = Job(*item)
            self.busy_hosts.add(job.host)
            future = self.executor.submit(self._resolve, job.result)
            self.pending[future] = job
        if self.progress:
            self.progress.queue_changed(len(self.queued), ready_hosts=self.queued.ready_hosts())

    def _accept(self, future, job, *, aborting=False):
        self.busy_hosts.discard(job.host)
        self.queued.release(job.host)
        try:
            self._save(job, future.result())
        except (KeyboardInterrupt, CancelledError):
            self.interrupted.add(job.query)
            if not aborting:
                raise
        except BaseException as exc:
            self.failed.add(job.query)
            if not aborting:
                raise WorkerFailure("Site worker failed; completed results were preserved") from exc
        finally:
            self.counts[job.query] -= 1
            self._finish_query(job.query)

    def _collect(self, done):
        for future in done:
            self._accept(future, self.pending.pop(future))
            if self.executor:
                # Refill each freed slot while persisting the rest of a completed group.
                self._schedule()

    def _wait_one(self):
        self._schedule()
        if self.pending:
            done, _ = wait(self.pending, timeout=0.1, return_when=FIRST_COMPLETED)
            self._collect(done)
        self._schedule()

    def poll(self):
        self._collect([future for future in self.pending if future.done()])
        self._schedule()

    def drain(self):
        while self.pending or self.queued:
            self._wait_one()

    def _abort(self):
        cancel = getattr(self.resolver, "cancel", None)
        if callable(cancel):
            cancel()
        while self.queued:
            job = Job(*self.queued.pop(ready=False))
            self.interrupted.add(job.query)
            self.counts[job.query] -= 1
            self._finish_query(job.query)
        for future in self.pending:
            future.cancel()
        if self.pending:
            wait(self.pending)
        for future in list(self.pending):
            self._accept(future, self.pending.pop(future), aborting=True)
        if self.progress:
            self.progress.queue_changed(0, ready_hosts=0)
