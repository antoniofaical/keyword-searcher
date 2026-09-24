import json
import re
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from .models import DiscoveryError, Resolution
from .urls import normalize_url, same_host

# These are source types, never filters on company age, market, activity or adherence.
INTERMEDIARIES = {
    "linkedin.com",
    "facebook.com",
    "instagram.com",
    "youtube.com",
    "x.com",
    "crunchbase.com",
    "g2.com",
    "capterra.com",
    "getapp.com",
    "softwareadvice.com",
    "wikipedia.org",
    "amazon.com",
    "github.com",
    "reddit.com",
    "medium.com",
}
EDITORIAL = re.compile(
    r"/(blog|news|noticias|notícias|articles?|press|compare|reviews?)(/|$)", re.IGNORECASE
)
ORG_TYPES = {
    "Organization",
    "Corporation",
    "LocalBusiness",
    "OnlineBusiness",
    "ProfessionalService",
    "Store",
    "MedicalBusiness",
}
EDITORIAL_TYPES = {"NewsArticle", "Article", "BlogPosting", "Review", "ItemList"}


def nodes(soup):
    """Top-level organizations only: never lift a customer or article publisher."""
    result = []
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or script.get_text())
        except (ValueError, TypeError):
            continue
        queue = data if isinstance(data, list) else [data]
        for item in queue:
            if not isinstance(item, dict):
                continue
            result.append(item)
            graph = item.get("@graph", [])
            if isinstance(graph, list):
                result.extend(x for x in graph if isinstance(x, dict))
    return result


def types(node):
    value = node.get("@type", [])
    return {str(x).rsplit("/", 1)[-1] for x in (value if isinstance(value, list) else [value])}


def excluded(url):
    host = urlsplit(url).hostname or ""
    return any(host == item or host.endswith("." + item) for item in INTERMEDIARIES)


def evidence(soup, base):
    candidates = set()
    for node in nodes(soup):
        if not types(node) & ORG_TYPES:
            continue
        name, url = node.get("name"), node.get("url")
        if not isinstance(name, str) or not isinstance(url, str) or not name.strip():
            continue
        try:
            target = normalize_url(urljoin(base, url))
        except DiscoveryError:
            continue
        if same_host(target, base):
            candidates.add((" ".join(name.split()), target))
    return candidates


class WebsiteResolver:
    """Bounded identity inspection, not a crawler. Ambiguity stays pending."""

    def __init__(self, http):
        self.http = http

    def page(self, url):
        response = self.http.get(url)
        if response.status != 200:
            raise DiscoveryError(f"site_http_{response.status}")
        if response.content_type not in {"text/html", "application/xhtml+xml"}:
            raise DiscoveryError("site_not_html")
        if excluded(response.url) or EDITORIAL.search(urlsplit(response.url).path):
            raise DiscoveryError("non_institutional_source")
        soup = BeautifulSoup(response.body, "html.parser")
        if any(types(n) & EDITORIAL_TYPES for n in nodes(soup)):
            raise DiscoveryError("editorial_or_listing_page")
        return response.url, soup

    def resolve(self, result):
        try:
            original = normalize_url(result.url)
            if excluded(original) or EDITORIAL.search(urlsplit(original).path):
                return Resolution("skipped", reason="non_institutional_source")
            base, soup = self.page(original)
            candidates = evidence(soup, base)
            # Without structured identity, inspect only an explicit home/logo link.
            if not candidates:
                homes = set()
                for anchor in soup.select("a[href]"):
                    label = (
                        (anchor.get("aria-label", "") + " " + anchor.get_text(" ", strip=True))
                        .strip()
                        .lower()
                    )
                    is_home = label in {"home", "homepage", "início", "inicio", "página inicial"}
                    is_logo = bool(anchor.find("img", alt=re.compile("logo", re.IGNORECASE)))
                    if is_home or is_logo:
                        try:
                            target = normalize_url(urljoin(base, anchor["href"]))
                        except DiscoveryError:
                            continue
                        if same_host(base, target) and target != base:
                            homes.add(target)
                if len(homes) == 1:
                    base, soup = self.page(homes.pop())
                    candidates = evidence(soup, base)
            if len(candidates) != 1:
                return Resolution("pending", reason="missing_or_ambiguous_company_identity")
            name, target = next(iter(candidates))
            # Check declared home; its own identity must agree before exporting.
            if target != base:
                final, home = self.page(target)
                confirmed = evidence(home, final)
                if not any(
                    n.casefold() == name.casefold() and same_host(u, final) for n, u in confirmed
                ):
                    return Resolution("pending", reason="entrypoint_identity_mismatch")
                base = final
            return Resolution(
                "confirmed",
                name,
                base,
                "organization_url_verified",
                json.dumps(
                    {"name": name, "declared_url": target, "source_url": original},
                    ensure_ascii=False,
                ),
            )
        except DiscoveryError as exc:
            return Resolution("pending", reason=str(exc))
