"""Concordant self-identification; never infer a name from the request/domain."""

import re
import unicodedata
from html import unescape
from urllib.parse import urljoin

from .models import DiscoveryError
from .site_policy import visible_text
from .urls import normalize_url, same_host


def name_key(name):
    name = unicodedata.normalize("NFKD", unescape(name)).casefold().strip(" ,.-")
    name = re.sub(r"\.(com|org|net)$", "", name)
    name = re.sub(
        r"(?:\s|,)+(?:pte\.?\s+|pty\.?\s+)?(?:ltd\.?|limited|inc\.?|incorporated|"
        r"llc|corp\.?|corporation|ag|gmbh|aps|as|ab|plc|srl|s\.?\s*l\.?|s\.?\s*a\.?)$",
        "",
        name,
    )
    return "".join(c for c in name if c.isalnum() and not unicodedata.combining(c))


def signal_keys(signal, anchors):
    keys = {name_key(signal["name"]), *(name_key(a) for a in signal["aliases"])}
    # A caption is removable only in presentation contexts and when its complete
    # prefix independently matches another declared name. Never shorten Organization.
    if signal["context"] in {"website", "site_name", "copyright"}:
        prefix = re.split(r"\s+[|–—-]\s+|\s*\|\s*", signal["name"], maxsplit=1)[0]
        if signal["context"] == "copyright":
            prefix = prefix.split(",", 1)[0]
        key = name_key(prefix)
        if key in anchors:
            keys.add(key)
    return keys - {""}


def copyright_names(soup):
    """Only ownership notices, not policy links, font licenses or image credits."""
    for element in soup.find_all(string=True):
        parents = list(element.parents)
        if any(
            p.name in {"script", "style", "noscript", "template", "svg", "title", "a"}
            for p in parents
        ):
            continue
        scoped = any(
            p.name == "footer"
            or p.get("role") == "contentinfo"
            or re.search(
                r"footer|copyright|copy-right",
                " ".join([p.get("id", ""), *p.get("class", [])]),
                re.I,
            )
            for p in parents
        )
        if not scoped:
            continue
        text = str(element).strip()
        if len(text) > 250:
            continue
        match = re.match(
            r"^(?:(?:©|ⓒ)\s*(?:copyright\s*)?|copyright\s*[:\-]?\s*(?:©|ⓒ)?\s*)"
            r"(?:\d{4}(?:\s*[-–]\s*\d{4})?\s*)?(?:by\s+)?([^\W_].*)$",
            text,
            re.I,
        )
        if not match:
            continue
        name = re.split(r"\ball\s+rights\b|\btodos os direitos\b|[|©]", match[1], flags=re.I)[
            0
        ].strip(" .-")
        if not any(char.isalpha() for char in name):
            continue
        if re.match(r"(?:notice|policy|permission|holder|is|statuses|terms)\b", name, re.I):
            continue
        if 2 <= len(name) <= 90 and not re.search(r"website by|designed by|powered by", name, re.I):
            yield name


def identity(soup, base, nodes, types, organization_types):
    signals = []
    for node in nodes(soup):
        kind = types(node)
        context = (
            "organization"
            if kind & organization_types
            else "website"
            if "WebSite" in kind
            else None
        )
        if not context:
            continue
        name, url = node.get("name"), node.get("url")
        if not isinstance(name, str) or not isinstance(url, str) or not name.strip():
            continue
        try:
            if not same_host(normalize_url(urljoin(base, url)), base):
                continue
        except DiscoveryError:
            continue
        # Explicit alternateName supports a brand/legal-name relationship.
        aliases = node.get("alternateName", [])
        aliases = aliases if isinstance(aliases, list) else [aliases]
        signals.append(
            {
                "context": context,
                "name": " ".join(name.split()),
                "aliases": [a for a in aliases if isinstance(a, str) and a.strip()],
            }
        )
    for tag in soup.select('meta[property="og:site_name"][content]'):
        if tag["content"].strip():
            signals.append({"context": "site_name", "name": tag["content"].strip(), "aliases": []})

    strong = list(signals)
    anchors = {name_key(s["name"]) for s in strong}
    anchors.update(name_key(a) for s in strong for a in s["aliases"])
    if strong:
        keys = [signal_keys(s, anchors) for s in strong]
        if not set.intersection(*keys):
            return None, signals, True

    # Fallback needs agreement across separate contexts. Titles alone never qualify.
    if soup.title:
        title = soup.title.get_text(" ", strip=True)
        parts = re.split(r"\s+[|–—-]\s+|\s*\|\s*", title)
        parts = [p.strip() for p in parts if p.strip().lower() not in {"home", "homepage"}]
        for part in parts[:3]:
            if 2 <= len(part) <= 90:
                signals.append({"context": "title", "name": part, "aliases": []})
    for image in soup.select("img[alt]"):
        if "logo" in " ".join([image.get("id", ""), image.get("src", ""), image["alt"]]).lower():
            name = re.sub(r"\blogo\b", "", image["alt"], flags=re.I).strip(" -|")
            if 2 <= len(name) <= 90:
                signals.append({"context": "logo", "name": name, "aliases": []})
    for name in copyright_names(soup):
        signals.append({"context": "copyright", "name": name, "aliases": []})
    # Explicit self-description, unlike duplicate head metadata, is independent.
    text = visible_text(soup)
    for signal in list(signals):
        if signal["context"] != "title":
            continue
        brand = signal["name"]
        if re.search(
            re.escape(brand) + r"\s+(?:is|are|provides|offers|é|oferece)\b", text, re.I
        ) or re.search(r"\bat\s+" + re.escape(brand) + r",?\s+we\b", text, re.I):
            signals.append({"context": "self_description", "name": brand, "aliases": []})
    if strong:
        allowed = set.union(*keys)
        if any(
            s["context"] == "copyright" and not signal_keys(s, allowed) & allowed for s in signals
        ):
            return None, signals, True
        shared = set.intersection(*keys)
        preferred = next(
            (s for s in strong if s["context"] == "organization"),
            next((s for s in strong if name_key(s["name"]) in shared), strong[0]),
        )
        return preferred["name"], signals, False
    grouped = {}
    for signal in signals:
        grouped.setdefault(name_key(signal["name"]), []).append(signal)
    matches = [
        group
        for key, group in grouped.items()
        if key
        and len({s["context"] for s in group}) >= 2
        and any(s["context"] != "title" for s in group)
    ]
    if len(matches) != 1:
        return None, signals, len(matches) > 1
    match = matches[0]
    # Conflicting copyright identifies the owner; do not accept a customer's logo.
    if any(
        s["context"] == "copyright"
        and name_key(match[0]["name"]) not in signal_keys(s, {name_key(match[0]["name"])})
        for s in signals
    ):
        return None, signals, True
    return match[0]["name"], signals, False
