"""Offline input contract for startup-adherence; no scoring or network calls."""

import re
import unicodedata
from dataclasses import asdict
from urllib.parse import urlparse

from . import __version__
from .models import DiscoveryError

RECEIVER = "antoniofaical/startup-theme-adherence-classifier-jev"
RECEIVER_COMMIT = "dbd6c4cc4fb35bb820205b500b2bdf67eb84b34b"


def site_keys(name, url):
    """Match receiver equivalence, including its disk-directory collision rule."""
    if not isinstance(name, str) or not name or not isinstance(url, str) or not url:
        raise DiscoveryError("Handoff requires nonempty site name and HTTP(S) URL")
    try:
        parsed = urlparse(url.strip())
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
            raise ValueError
        host = (parsed.hostname or "").lower().removeprefix("www.")
        path = re.sub(r"/{2,}", "/", parsed.path or "/")
        # Receiver drops URL params, fragments and queries before comparing.
        path = re.sub(r"%[0-9a-fA-F]{2}", lambda m: m.group(0).upper(), path)
        key = (host, parsed.port, path.rstrip("/") or "/")
        ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
        directory = re.sub(r"[^A-Za-z0-9._-]+", "-", ascii_name).strip(".-_").casefold()
        if not directory:
            raise ValueError
    except ValueError as exc:
        raise DiscoveryError("Handoff site URL or evidence directory name is invalid") from exc
    return key, directory


def build_handoff(rows, progress, *, interrupted=False):
    """First entry wins in stored query/source order; retain every confirmed source."""
    sites, entries = [], []
    by_name, by_url, directories = {}, {}, set()
    counts = {"confirmed": 0, "pending": 0, "skipped": 0}
    queries = {p["query"]: dict(p) for p in progress}
    for query, source, item in sorted(rows, key=lambda row: (row[0], row[1])):
        counts[item.status] = counts.get(item.status, 0) + 1
        queries.setdefault(query, dict(query=query, status="unknown", reason="missing_progress"))
        if item.status != "confirmed":
            continue
        key, directory = site_keys(item.name, item.url)
        index = by_name.get(item.name)
        if index is None:
            index = by_url.get(key)
        if index is None:
            if directory in directories:
                raise DiscoveryError(
                    "Handoff site names collide on disk; review the saved identities"
                )
            index = len(sites)
            selected = dict(name=item.name, url=item.url)
            sites.append(selected)
            entries.append(dict(**selected, alternatives=[], sources=[]))
            by_name[item.name] = index
            by_url[key] = index
            directories.add(directory)
        else:
            # Mirror select_sites: an alias selects the first occurrence, but an
            # ignored alternative URL does not become a new equivalence key.
            by_name[item.name] = index
        entry = entries[index]
        alternative = dict(name=item.name, url=item.url)
        if alternative != sites[index] and alternative not in entry["alternatives"]:
            entry["alternatives"].append(alternative)
        entry["sources"].append(dict(query=query, discovered_url=source, resolution=asdict(item)))
    progress = [queries[q] for q in sorted(queries)]
    search_complete = bool(progress) and all(p["status"] == "complete" for p in progress)
    metadata = dict(
        handoff_schema_version=1,
        producer=dict(name="keyword-searcher", version=__version__),
        receiver=dict(repository=RECEIVER, contract_commit=RECEIVER_COMMIT),
        counts=dict(
            sites=len(sites),
            confirmed_resolutions=counts["confirmed"],
            duplicates=counts["confirmed"] - len(sites),
            pending_resolutions=counts["pending"],
            skipped_resolutions=counts["skipped"],
        ),
        coverage=dict(
            interrupted=interrupted,
            search_complete=search_complete,
            partial=interrupted or not search_complete or bool(counts["pending"]),
            queries=progress,
        ),
        sites=entries,
    )
    return sites, metadata
