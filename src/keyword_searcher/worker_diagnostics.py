"""Bounded failure context without exception messages, locals or URL credentials."""

import hashlib
import json
import os
import re
import traceback
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from . import __version__
from .models import DiscoveryError


def safe_text(value, limit=500):
    value = str(value)
    token = os.environ.get("APIFY_API_TOKEN", "")
    if token:
        value = value.replace(token, "[REDACTED]")
    value = re.sub(r"apify_api_[a-zA-Z0-9_-]+", "[REDACTED]", value)
    return "".join(char if char.isprintable() else " " for char in value)[:limit]


def source_location(url):
    try:
        parts = urlsplit(url)
        # Query values, fragments and userinfo are unnecessary for locating a crash.
        return safe_text(urlunsplit((parts.scheme, parts.hostname or "", parts.path, "", "")))
    except ValueError:
        return "[invalid URL]"


def failure_details(job, exc, phase):
    frames = traceback.extract_tb(exc.__traceback__)[-12:]
    return dict(
        version=__version__,
        captured_at=datetime.now(timezone.utc).isoformat(),
        query=safe_text(job.query),
        source_location=source_location(job.result.url),
        source_url_sha256=hashlib.sha256(
            job.result.url.encode("utf-8", errors="replace")
        ).hexdigest(),
        phase=phase,
        exception_type=safe_text(type(exc).__name__),
        frames=[
            dict(
                file=safe_text(Path(frame.filename).name),
                line=frame.lineno,
                function=safe_text(frame.name),
            )
            for frame in frames
        ],
        notes=[
            "Exception messages, source lines, locals, URL query/fragment and userinfo are omitted.",
            "The fingerprint identifies the exact source URL in saved state.",
            "This records the last fatal worker error, not the current run status.",
        ],
    )


def failure_exit_message(error, directory):
    from .export import output_directory

    try:
        directory = output_directory(directory)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "worker_error.json"
        temp = path.with_suffix(".json.tmp")
        try:
            temp.write_text(
                json.dumps(error.details, indent=2, ensure_ascii=True), encoding="utf-8"
            )
            os.replace(temp, path)
        finally:
            temp.unlink(missing_ok=True)
    except (OSError, DiscoveryError) as exc:
        return f"Error: {error}\nUnable to write worker diagnostic ({type(exc).__name__}).\n"
    return f"Error: {error}\nWorker diagnostic: {path}\n"
