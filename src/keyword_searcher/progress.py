"""Thread-safe terminal feedback for discovery and concurrent site resolution."""

import os
import shutil
import sys
import threading
import time
from urllib.parse import urlsplit

COLORS = {"queries": "\033[36m", "pages": "\033[34m", "results": "\033[32m"}
RESET = "\033[0m"


class TerminalProgress:
    def __init__(self, query_count, max_pages, *, stream=None, enabled=True):
        self.stream = stream if stream is not None else sys.stderr
        self.enabled = enabled
        self.live = enabled and bool(getattr(self.stream, "isatty", lambda: False)())
        self.color = self.live and "NO_COLOR" not in os.environ
        self.block = "#"
        try:
            "\u2588".encode(getattr(self.stream, "encoding", None) or "utf-8")
        except (UnicodeError, LookupError):
            pass
        else:
            self.block = "\u2588"
        self.total_queries = query_count
        self.max_pages = max_pages
        self.queries_done = 0
        self.pages_done = 0
        self.results_done = 0
        self.results_total = 0
        self.confirmed = 0
        self.pending = 0
        self.skipped = 0
        self.query_index = 0
        self.activity = "Starting"
        self.started = time.monotonic()
        self._lines = 0
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._heartbeat = None
        self.worker_limit = None
        self.active_workers = {}
        self.global_mode = False
        self.queued_sites = 0

    def configure_workers(self, workers, *, global_mode=False):
        with self._lock:
            self.worker_limit = workers
            self.global_mode = global_mode

    def queue_changed(self, queued):
        with self._lock:
            self.queued_sites = queued
            self._draw_locked()

    def begin_worker(self, url):
        token = object()
        with self._lock:
            self.active_workers[token] = (urlsplit(url).hostname or url, time.monotonic())
            self._draw_locked()
        return token

    def end_worker(self, token):
        with self._lock:
            self.active_workers.pop(token, None)
            self._draw_locked()

    def _write(self, message):
        encoding = getattr(self.stream, "encoding", None)
        if encoding:
            message = message.encode(encoding, errors="backslashreplace").decode(encoding)
        self.stream.write(message)

    def __enter__(self):
        if self.enabled:
            self.started = time.monotonic()
            self._draw()
            if self.live:
                self._heartbeat = threading.Thread(target=self._tick, daemon=True)
                self._heartbeat.start()
        return self

    def __exit__(self, _type, _value, _traceback):
        self._stop.set()
        if self._heartbeat is not None:
            self._heartbeat.join(timeout=2)
        with self._lock:
            if self.live:
                self._draw_locked()
                self._write("\n")
                self.stream.flush()

    def _tick(self):
        while not self._stop.wait(1):
            self._draw()

    def begin_query(self, index, query):
        with self._lock:
            self.query_index = index
            if not self.global_mode:
                self.pages_done = 0
                self.results_done = 0
                self.results_total = 0
            self.activity = f"Query {index}/{self.total_queries}: {query}"
            self._draw_locked()

    def begin_page(self, page_number, cached):
        with self._lock:
            source = "stored" if cached else "waiting for Apify batch"
            self.activity = f"Page {page_number}/{self.max_pages}: {source}"
            self._draw_locked()

    def page_ready(self, results_total):
        with self._lock:
            self.pages_done += 1
            if self.global_mode:
                self.results_total += results_total
            else:
                self.results_total = results_total
                self.results_done = 0
            self.activity = f"Page loaded: {results_total} new links"
            self._draw_locked()

    def begin_result(self, url, cached):
        with self._lock:
            source = "stored" if cached else "checking"
            host = urlsplit(url).hostname or url
            self.activity = f"Link {self.results_done + 1}/{self.results_total}: {source} {host}"
            self._draw_locked()

    def end_result(self, resolution):
        with self._lock:
            self.results_done += 1
            if resolution.status == "confirmed":
                self.confirmed += 1
            elif resolution.status == "pending":
                self.pending += 1
            elif resolution.status == "skipped":
                self.skipped += 1
            self.activity = f"Last link: {resolution.status}"
            self._draw_locked()

    def finish_query(self, status, *, index=None):
        with self._lock:
            self.queries_done += 1
            self.activity = f"Query {index or self.query_index}/{self.total_queries}: {status}"
            self._draw_locked()

    def _bar(self, name, done, total, extra, width):
        label = name.capitalize()
        details = f" {done}/{total} {extra}"
        size = min(30, max(0, width - len(label) - len(details) - 5))
        if size < 8:
            return f"{label}: {details.strip()}"
        filled = round(size * done / max(total, 1))
        segment = self.block * filled
        if self.color and segment:
            segment = COLORS[name] + segment + RESET
        return f"{label} [{segment}{'-' * (size - filled)}]{details}"

    def _draw(self):
        with self._lock:
            self._draw_locked()

    def _draw_locked(self):
        if not self.enabled:
            return
        elapsed = int(time.monotonic() - self.started)
        activity = self.activity
        workers = ""
        if self.worker_limit is not None:
            completed = self.confirmed + self.pending + self.skipped
            workers = f"Workers {len(self.active_workers)}/{self.worker_limit} active; sites_done={completed}"
            if self.global_mode:
                workers += f" queued={self.queued_sites}"
        if self.active_workers:
            host, started = min(self.active_workers.values(), key=lambda item: item[1])
            activity = f"Checking {host} site_elapsed={int(time.monotonic() - started)}s"
        if not self.live:
            # Redirected output remains readable without terminal escape sequences.
            message = f"[total_elapsed={elapsed}s] {activity}"
            if workers:
                message += f" | {workers}"
            self._write(message + "\n")
            self.stream.flush()
            return
        width = shutil.get_terminal_size(fallback=(100, 24)).columns
        lines = [
            self._bar(
                "queries",
                self.queries_done,
                self.total_queries,
                f"confirmed={self.confirmed} pending={self.pending} skipped={self.skipped}",
                width,
            ),
            self._bar(
                "pages",
                self.pages_done,
                self.max_pages * self.total_queries if self.global_mode else self.max_pages,
                "global loaded / limit" if self.global_mode else "current query / limit",
                width,
            ),
            self._bar(
                "results",
                self.results_done,
                self.results_total,
                "global loaded" if self.global_mode else "current page",
                width,
            ),
        ]
        if workers:
            lines.append(workers[: max(1, width - 1)])
        lines.append(f"{activity} total_elapsed={elapsed}s"[: max(1, width - 1)])
        if self._lines:
            self._write("\r\033[2K")
            for _ in range(self._lines - 1):
                self._write("\033[1A\r\033[2K")
        self._write("\n".join(lines))
        self.stream.flush()
        self._lines = len(lines)
