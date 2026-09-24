import csv
import json
import os
import sqlite3
from dataclasses import asdict
from pathlib import Path

from .models import BudgetExceeded, DiscoveryError, Resolution


class Store:
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
        """)
        encoded = json.dumps(config, sort_keys=True, ensure_ascii=False)
        previous = self.db.execute("SELECT value FROM meta WHERE key='config'").fetchone()
        if previous and previous[0] != encoded:
            self.db.close()
            raise DiscoveryError("Run configuration differs; use a new --run-dir")
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO meta VALUES ('config', ?)", (encoded,))
            self.db.execute("INSERT OR IGNORE INTO meta VALUES ('requests', '0')")

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

    def progress(self, query, status, reason=""):
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO progress VALUES (?, ?, ?)", (query, status, reason)
            )

    def export(self, directory):
        directory = Path(directory)
        temp = directory / "companies.csv.tmp"
        seen = set()
        pending = []
        with temp.open("w", encoding="utf-8-sig", newline="") as file:
            writer = csv.writer(file, delimiter=";")
            writer.writerow(["CompanyName", "URL", "SearchQuery"])
            for query, source, raw in self.db.execute(
                "SELECT query, discovered_url, payload FROM resolutions ORDER BY query, discovered_url"
            ):
                item = Resolution(**json.loads(raw))
                if item.status == "confirmed":
                    key = (item.url, query)
                    if key not in seen:
                        writer.writerow([item.name, item.url, query])
                        seen.add(key)
                else:
                    pending.append(dict(query=query, discovered_url=source, **asdict(item)))
        os.replace(temp, directory / "companies.csv")
        statuses = [
            dict(query=q, status=s, reason=r)
            for q, s, r in self.db.execute(
                "SELECT query, status, reason FROM progress ORDER BY query"
            )
        ]
        report = dict(
            search_requests=self.requests(),
            exported_rows=len(seen),
            queries=statuses,
            pending=sum(x["status"] == "pending" for x in pending),
            skipped=sum(x["status"] == "skipped" for x in pending),
        )
        for name, data in [("report.json", report), ("pending.json", pending)]:
            temp = directory / (name + ".tmp")
            temp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            os.replace(temp, directory / name)
        return report
