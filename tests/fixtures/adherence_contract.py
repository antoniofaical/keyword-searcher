"""Unchanged receiver input functions (AST behavior) for offline conformance.

Source: antoniofaical/startup-theme-adherence-classifier-jev
Commit: dbd6c4cc4fb35bb820205b500b2bdf67eb84b34b
Paths: src/startup_adherence/cli.py and domain/urls.py
Only the input validator is snapshotted; no crawler, API client or scoring code.
"""

import re
import sys
import unicodedata
from urllib.parse import parse_qsl, urldefrag, urlencode, urlparse, urlunparse

TRACKING_KEYS = {"fbclid", "gclid", "mc_cid", "mc_eid"}


def evidence_directory_name(site_name: str) -> str:
    """Return a filesystem-safe directory name while preserving the display name."""
    ascii_name = unicodedata.normalize("NFKD", site_name).encode("ascii", "ignore").decode()
    directory_name = re.sub(r"[^A-Za-z0-9._-]+", "-", ascii_name).strip(".-_")
    if not directory_name:
        raise ValueError("site_name must contain at least one letter or number")
    return directory_name


def canonicalize(url: str, *, keep_query: bool) -> str:
    url, _ = urldefrag(url.strip())
    parsed = urlparse(url)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return ""
    query = ""
    if keep_query and parsed.query:
        pairs = [
            (key, value)
            for key, value in parse_qsl(parsed.query, keep_blank_values=True)
            if not key.lower().startswith("utm_") and key.lower() not in TRACKING_KEYS
        ]
        query = urlencode(sorted(pairs))
    path = re.sub(r"/{2,}", "/", parsed.path or "/")
    path = re.sub(
        r"%[0-9a-fA-F]{2}",
        lambda match: match.group(0).upper(),
        path,
    )
    return urlunparse((parsed.scheme.lower(), parsed.netloc.lower(), path, "", query, ""))


def host(url: str) -> str:
    return (urlparse(url).hostname or "").lower().removeprefix("www.")


def select_sites(configured: list[dict[str, str]], names: list[str] | None) -> list[dict[str, str]]:
    if not isinstance(configured, list):
        raise TypeError("Sites file must contain a JSON list")
    unique: list[dict[str, str]] = []
    by_name: dict[str, dict[str, str]] = {}
    by_url: dict[tuple[str, int | None, str], dict[str, str]] = {}
    by_directory: dict[str, dict[str, str]] = {}
    skipped = 0
    for site in configured:
        if (
            not isinstance(site, dict)
            or not isinstance(site.get("name"), str)
            or not isinstance(site.get("url"), str)
            or not site["name"]
            or not site["url"]
        ):
            raise ValueError("Each site needs name and URL")
        url = canonicalize(site["url"], keep_query=False)
        if not url:
            raise ValueError(f"Invalid site URL for {site['name']}: {site['url']}")
        parsed = urlparse(url)
        # Crawls ignore query strings; www, scheme and trailing slash variants
        # refer to the same site input for this batch.
        url_key = (host(url), parsed.port, parsed.path.rstrip("/") or "/")
        directory = evidence_directory_name(site["name"]).casefold()
        original = by_name.get(site["name"]) or by_url.get(url_key)
        if original is not None:
            by_name[site["name"]] = original
            skipped += 1
            continue
        if directory in by_directory:
            raise ValueError("Site names collide on disk")
        by_name[site["name"]] = site
        by_url[url_key] = site
        by_directory[directory] = site
        unique.append(site)
    if skipped:
        print(
            f"Skipped {skipped} duplicate site entries (first occurrence kept).",
            file=sys.stderr,
        )
    if not names or names == ["all"]:
        return unique
    unknown = set(names) - by_name.keys()
    if unknown:
        raise ValueError(f"Unknown sites: {', '.join(sorted(unknown))}")
    selected = {by_name[name]["name"] for name in names}
    return [site for site in unique if site["name"] in selected]
