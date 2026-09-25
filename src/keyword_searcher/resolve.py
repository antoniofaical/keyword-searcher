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
    "sourceforge.net",
    "slideshare.net",
    "scribd.com",
    "play.google.com",
    "pinterest.com",
    "mementodatabase.com",
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


def home_name(soup, base):
    """Read self-declared identity on a homepage; conflict stays unresolved."""
    organizations = {name for name, _ in evidence(soup, base)}
    if len(organizations) > 1:
        return None
    if organizations:
        return next(iter(organizations))
    names = set()
    for node in nodes(soup):
        if "WebSite" in types(node) and isinstance(node.get("name"), str):
            url = node.get("url")
            if isinstance(url, str):
                try:
                    if same_host(normalize_url(urljoin(base, url)), base):
                        names.add(" ".join(node["name"].split()))
                except DiscoveryError:
                    pass
    for tag in soup.select('meta[property="og:site_name"][content]'):
        names.add(" ".join(tag["content"].split()))
    names.discard("")
    return next(iter(names)) if len({name.casefold() for name in names}) == 1 else None


def home_links(soup, base):
    homes = set()
    for anchor in soup.select("a[href]"):
        label = (
            (anchor.get("aria-label", "") + " " + anchor.get_text(" ", strip=True)).strip().lower()
        )
        is_home = label in {"home", "homepage", "início", "inicio", "página inicial"}
        is_logo = bool(anchor.find("img", alt=re.compile("logo", re.IGNORECASE)))
        if is_home or is_logo:
            try:
                target = normalize_url(urljoin(base, anchor["href"]))
            except DiscoveryError:
                continue
            if same_host(base, target):
                homes.add(target)
    return homes


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
            if len(candidates) > 1:
                return Resolution("pending", reason="missing_or_ambiguous_company_identity")
            expected_name, declared = next(iter(candidates)) if candidates else (None, None)
            root = normalize_url(f"{urlsplit(base).scheme}://{urlsplit(base).netloc}/")
            # A declared locale root can be an institutional entrypoint; a product
            # page declaring itself is not a homepage, even with Organization JSON-LD.
            homes = []
            if declared and re.fullmatch(
                r"/(?:[a-z]{2}(?:-[a-z]{2})?)?/?", urlsplit(declared).path, re.IGNORECASE
            ):
                homes.append(declared)
            homes.extend(sorted(home_links(soup, base), key=len))
            homes.append(root)
            checked = set()
            mismatch = False
            for url in homes:
                if url in checked or not same_host(url, base):
                    continue
                checked.add(url)
                try:
                    final, home = (base, soup) if url == base else self.page(url)
                except DiscoveryError:
                    continue
                # A redirect must still lead to the same institutional host.
                if not same_host(final, base):
                    mismatch = True
                    continue
                name = home_name(home, final)
                if not name:
                    continue
                if expected_name and name.casefold() != expected_name.casefold():
                    mismatch = True
                    continue
                return Resolution(
                    "confirmed",
                    name,
                    final,
                    "homepage_identity_verified",
                    json.dumps(
                        {
                            "name": name,
                            "source_url": original,
                            "declared_url": declared,
                            "entrypoint": final,
                        },
                        ensure_ascii=False,
                    ),
                )
            return Resolution(
                "pending",
                reason="entrypoint_identity_mismatch"
                if mismatch
                else "missing_or_ambiguous_company_identity",
            )
        except DiscoveryError as exc:
            return Resolution("pending", reason=str(exc))
