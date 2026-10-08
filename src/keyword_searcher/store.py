import json
import sqlite3
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from .models import BudgetExceeded, DiscoveryError, Resolution


class Store:
    @classmethod
    def open_existing(cls, path, *, readonly=True):
        """Open schema-2 state for an audit without creating or migrating it."""
        store = cls.__new__(cls)
        mode = "ro" if readonly else "rw"
        store.db = sqlite3.connect(Path(path).resolve().as_uri() + f"?mode={mode}", uri=True)
        try:
            schema = store.db.execute(
                "SELECT value FROM meta WHERE key='schema_version'"
            ).fetchone()
            if not schema or schema[0] != "2":
                raise DiscoveryError("Audit requires state schema 2; no migration was performed")
        except BaseException:
            store.db.close()
            raise
        return store

    def __init__(self, path: Path, config: dict):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS pages (
                query TEXT, start INTEGER, payload TEXT NOT NULL,
                PRIMARY KEY(query, start));
            CREATE TABLE IF NOT EXISTS resolutions (
                query TEXT, discovered_url TEXT, payload TEXT NOT NULL,
                PRIMARY KEY(query, discovered_url));
            CREATE TABLE IF NOT EXISTS progress (query TEXT PRIMARY KEY, status TEXT, reason TEXT);
            CREATE TABLE IF NOT EXISTS apify_batches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                queries TEXT NOT NULL,
                reserved_pages INTEGER NOT NULL,
                run_id TEXT,
                status TEXT NOT NULL);
        """)
        encoded = json.dumps(config, sort_keys=True, ensure_ascii=False)
        previous = self.db.execute("SELECT value FROM meta WHERE key='config'").fetchone()
        schema = self.db.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        try:
            if schema and schema[0] != "2":
                raise DiscoveryError("Unsupported state schema; use a compatible tool version")
            if previous:
                try:
                    old = json.loads(previous[0])
                except ValueError as exc:
                    raise DiscoveryError("Invalid stored run configuration") from exc
                if not isinstance(old, dict):
                    raise DiscoveryError("Invalid stored run configuration")
                if old.get("provider") not in (None, "serpapi-google", "apify-google"):
                    raise DiscoveryError("Unsupported legacy provider")
                if not schema and old.get("version") not in (None, "0.1.0"):
                    raise DiscoveryError("Unsupported legacy tool version")

                def semantic(data):
                    return {k: v for k, v in data.items() if k not in {"version", "provider"}}

                if semantic(old) != semantic(config):
                    raise DiscoveryError("Run configuration differs; use a new --run-dir")
            with self.db:
                self.db.execute("BEGIN IMMEDIATE")
                self.db.execute("""CREATE TABLE IF NOT EXISTS apify_coverage (
                    query TEXT PRIMARY KEY, batch_id INTEGER NOT NULL, payload TEXT NOT NULL)""")
                self.db.execute("""CREATE TABLE IF NOT EXISTS apify_run_results (
                    batch_id INTEGER PRIMARY KEY, payload TEXT NOT NULL)""")
                if previous and not schema:
                    self.db.execute("INSERT INTO meta VALUES ('legacy_config', ?)", (previous[0],))
                    self.db.execute(
                        "INSERT INTO meta VALUES ('migration', ?)",
                        (
                            json.dumps(
                                {
                                    "from": 1,
                                    "to": 2,
                                    "reason": "Apify-only searches; preserve legacy pages, IDs and counters",
                                    "at": datetime.now(timezone.utc).isoformat(),
                                    "effects": [
                                        "provider=apify-google",
                                        "schema version separate from software",
                                    ],
                                }
                            ),
                        ),
                    )
                self.db.execute("INSERT OR REPLACE INTO meta VALUES ('config', ?)", (encoded,))
                self.db.execute("INSERT OR REPLACE INTO meta VALUES ('schema_version', '2')")
                self.db.execute("INSERT OR IGNORE INTO meta VALUES ('requests', '0')")
        except BaseException:
            self.db.close()
            raise

    def close(self):
        self.db.close()

    def reserve(self, maximum):
        try:
            self.db.execute("BEGIN IMMEDIATE")
            count = self.requests()
            if count >= maximum:
                raise BudgetExceeded("search_request_limit")
            self.db.execute("UPDATE meta SET value=? WHERE key='requests'", (str(count + 1),))
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise

    def requests(self):
        return int(self.db.execute("SELECT value FROM meta WHERE key='requests'").fetchone()[0])

    def apify_reserved_pages(self):
        return self.db.execute(
            "SELECT COALESCE(SUM(reserved_pages),0) FROM apify_batches"
        ).fetchone()[0]

    def apify_pages(self):
        return self.db.execute(
            "SELECT COUNT(*) FROM pages WHERE json_extract(payload, '$.provider')='apify-google'"
        ).fetchone()[0]

    def apify_open_batch(self):
        row = self.db.execute(
            "SELECT id, queries, run_id, status FROM apify_batches "
            "WHERE status IN ('planned', 'running') ORDER BY id LIMIT 1"
        ).fetchone()
        return (row[0], json.loads(row[1]), row[2], row[3]) if row else None

    def apify_plan_batch(self, queries, pages, maximum):
        try:
            self.db.execute("BEGIN IMMEDIATE")
            if self.apify_reserved_pages() + pages > maximum:
                raise BudgetExceeded("apify_page_limit")
            cursor = self.db.execute(
                "INSERT INTO apify_batches (queries,reserved_pages,status) VALUES (?,?,'planned')",
                (json.dumps(queries, ensure_ascii=False), pages),
            )
            self.db.commit()
            return cursor.lastrowid
        except BaseException:
            self.db.rollback()
            raise

    def apify_set_run(self, batch_id, run_id):
        with self.db:
            self.db.execute(
                "UPDATE apify_batches SET run_id=?, status='running' WHERE id=? AND status='planned'",
                (run_id, batch_id),
            )

    def apify_recover_run(self, run_id):
        import re

        if not re.fullmatch(r"[a-zA-Z0-9]+", run_id):
            raise DiscoveryError("Invalid Apify run ID")
        batch = self.apify_open_batch()
        if not batch or batch[3] != "planned":
            raise DiscoveryError("No unconfirmed Apify batch to recover")
        self.apify_set_run(batch[0], run_id)

    def apify_finish_batch(self, batch_id):
        with self.db:
            self.db.execute("UPDATE apify_batches SET status='done' WHERE id=?", (batch_id,))

    def apify_import_batch(self, batch_id, pages, coverage, run_info):
        """Commit a validated dataset and its terminal coverage together."""
        with self.db:
            for query, start, payload in pages:
                self.db.execute(
                    "INSERT OR IGNORE INTO pages VALUES (?, ?, ?)",
                    (query, start, json.dumps(payload, ensure_ascii=False)),
                )
            for query, summary in coverage.items():
                self.db.execute(
                    "INSERT OR REPLACE INTO apify_coverage VALUES (?, ?, ?)",
                    (query, batch_id, json.dumps(summary)),
                )
            self.db.execute(
                "INSERT OR REPLACE INTO apify_run_results VALUES (?, ?)",
                (batch_id, json.dumps(run_info)),
            )
            self.db.execute("UPDATE apify_batches SET status='done' WHERE id=?", (batch_id,))

    def apify_query_coverage(self, query):
        row = self.db.execute(
            "SELECT payload FROM apify_coverage WHERE query=?", (query,)
        ).fetchone()
        return json.loads(row[0]) if row else None

    def apify_abandon_batch(self):
        batch = self.apify_open_batch()
        if not batch:
            raise DiscoveryError("No unfinished Apify batch to abandon")
        with self.db:
            self.db.execute("UPDATE apify_batches SET status='abandoned' WHERE id=?", (batch[0],))

    def page(self, query, start):
        row = self.db.execute(
            "SELECT payload FROM pages WHERE query=? AND start=?", (query, start)
        ).fetchone()
        return json.loads(row[0]) if row else None

    def save_page(self, query, start, payload):
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO pages VALUES (?, ?, ?)",
                (query, start, json.dumps(payload, ensure_ascii=False)),
            )

    def resolution(self, query, url):
        row = self.db.execute(
            "SELECT payload FROM resolutions WHERE query=? AND discovered_url=?", (query, url)
        ).fetchone()
        return Resolution(**json.loads(row[0])) if row else None

    def save_resolution(self, query, url, resolution):
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO resolutions VALUES (?, ?, ?)",
                (query, url, json.dumps(asdict(resolution), ensure_ascii=False)),
            )

    def reclassify_non_institutional(self):
        """Change only stored pending decisions with an existing excluded-source reason."""
        from .resolve import NON_INSTITUTIONAL_REASONS

        reasons = sorted(NON_INSTITUTIONAL_REASONS)
        placeholders = ",".join("?" for _ in reasons)
        with self.db:
            cursor = self.db.execute(
                "UPDATE resolutions SET payload=json_set(payload, '$.status', 'skipped') "
                "WHERE json_extract(payload, '$.status')='pending' "
                f"AND json_extract(payload, '$.reason') IN ({placeholders})",
                reasons,
            )
        return cursor.rowcount

    def progress(self, query, status, reason=""):
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO progress VALUES (?, ?, ?)", (query, status, reason)
            )

    def ensure_queries(self, queries):
        with self.db:
            for query in queries:
                self.db.execute(
                    "INSERT OR IGNORE INTO progress VALUES (?, 'not_started', '')", (query,)
                )
            # Repair old runs that called untouched queries incomplete.
            self.db.execute("""
                UPDATE progress SET status='not_started'
                WHERE status='incomplete' AND reason='search_request_limit'
                  AND NOT EXISTS (SELECT 1 FROM pages WHERE pages.query=progress.query)
            """)

    def saved_queries(self, queries):
        available = {row[0] for row in self.db.execute("SELECT DISTINCT query FROM pages")}
        return [query for query in queries if query in available]

    def saved_pages(self, query):
        return self.db.execute(
            "SELECT start, payload FROM pages WHERE query=? ORDER BY start", (query,)
        ).fetchall()

    def export_resolutions(self):
        for query, source, raw in self.db.execute(
            "SELECT query, discovered_url, payload FROM resolutions ORDER BY query, discovered_url"
        ):
            yield query, source, Resolution(**json.loads(raw))

    def export_progress(self):
        return [
            dict(query=q, status=s, reason=r)
            for q, s, r in self.db.execute(
                "SELECT query, status, reason FROM progress ORDER BY query"
            )
        ]

    def export_metadata(self):
        return dict(
            search_requests=self.requests(),
            apify_pages=self.apify_pages(),
            apify_reserved_pages=self.apify_reserved_pages(),
            apify_coverage={
                q: json.loads(raw)
                for q, raw in self.db.execute(
                    "SELECT query, payload FROM apify_coverage ORDER BY query"
                )
            },
            apify_runs=[
                json.loads(raw)
                for (raw,) in self.db.execute(
                    "SELECT payload FROM apify_run_results ORDER BY batch_id"
                )
            ],
        )

    def export(self, directory):
        """Compatibility wrapper: all file writing belongs to export.py."""
        from .export import export_results

        return export_results(self, directory)
