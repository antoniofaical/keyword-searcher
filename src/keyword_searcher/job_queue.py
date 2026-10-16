"""Main-thread, disk-backed backlog; blocked hosts cannot hide ready hosts."""

import json
import sqlite3
from dataclasses import asdict

from .models import SearchResult


class JobQueue:
    def __init__(self):
        # Empty filename creates a private temporary file, removed on close.
        self.db = sqlite3.connect("")
        self.db.executescript("""
            CREATE TABLE jobs (id INTEGER PRIMARY KEY, query TEXT, host TEXT, payload TEXT);
            CREATE INDEX jobs_host ON jobs(host, id);
            CREATE TABLE heads (host TEXT PRIMARY KEY, first_id INTEGER);
            CREATE TABLE busy (host TEXT PRIMARY KEY);
        """)
        self.size = 0

    def __len__(self):
        return self.size

    def append(self, job):
        cursor = self.db.execute(
            "INSERT INTO jobs(query, host, payload) VALUES (?, ?, ?)",
            (job.query, job.host, json.dumps(asdict(job.result))),
        )
        self.db.execute("INSERT OR IGNORE INTO heads VALUES (?, ?)", (job.host, cursor.lastrowid))
        self.size += 1

    def pop(self, *, ready=True):
        condition = (
            "WHERE NOT EXISTS (SELECT 1 FROM busy WHERE busy.host=heads.host)" if ready else ""
        )
        row = self.db.execute(
            "SELECT jobs.id, jobs.query, jobs.host, jobs.payload FROM heads "
            f"JOIN jobs ON jobs.id=heads.first_id {condition} ORDER BY jobs.id LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        ident, query, host, payload = row
        self.db.execute("DELETE FROM jobs WHERE id=?", (ident,))
        following = self.db.execute("SELECT MIN(id) FROM jobs WHERE host=?", (host,)).fetchone()[0]
        if following is None:
            self.db.execute("DELETE FROM heads WHERE host=?", (host,))
        else:
            self.db.execute("UPDATE heads SET first_id=? WHERE host=?", (following, host))
        if ready:
            self.db.execute("INSERT INTO busy VALUES (?)", (host,))
        self.size -= 1
        return query, SearchResult(**json.loads(payload))

    def release(self, host):
        self.db.execute("DELETE FROM busy WHERE host=?", (host,))

    def ready_hosts(self):
        return self.db.execute(
            "SELECT COUNT(*) FROM heads WHERE NOT EXISTS "
            "(SELECT 1 FROM busy WHERE busy.host=heads.host)"
        ).fetchone()[0]

    def close(self):
        self.db.close()
