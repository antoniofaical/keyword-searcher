import json
import re
from collections import OrderedDict
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from .identity import identity, name_key
from .models import DiscoveryError, Resolution
from .site_policy import (
    INSTITUTION,
    declared_source_kind,
    interstitial,
    known_source,
    own_offer,
    visible_text,
)
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
NON_INSTITUTIONAL_REASONS = {"non_institutional_source", "editorial_or_listing_page"}
POLICY_VERSION = 3


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
    return identity(soup, base, nodes, types, ORG_TYPES)[0]


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
        self.cache = OrderedDict()

    def page(self, url):
        if url in self.cache:
            self.cache.move_to_end(url)
            value = self.cache[url]
            if isinstance(value, str):
                raise DiscoveryError(value)
            return value[:2]
        try:
            value = self._page(url)
        except DiscoveryError as exc:
            # Never retain exception tracebacks (which hold HTTP bodies/frames).
            self.cache[url] = str(exc)
            self.trim_cache()
            raise
        self.cache[url] = value
        self.trim_cache()
        return value[:2]

    def trim_cache(self):
        while (
            len(self.cache) > 128
            or sum(
                len(value) if isinstance(value, str) else value[2] for value in self.cache.values()
            )
            > 8 * 1024 * 1024
        ):
            self.cache.popitem(last=False)

    def _page(self, url):
        response = self.http.get(url)
        final = normalize_url(response.url)
        if excluded(final) or known_source(final):
            raise DiscoveryError("non_institutional_source")
        if response.status != 200:
            raise DiscoveryError(f"site_http_{response.status}")
        if response.content_type not in {"text/html", "application/xhtml+xml"}:
            raise DiscoveryError("site_not_html")
        soup = BeautifulSoup(response.body, "html.parser")
        problem = interstitial(final, soup)
        if problem:
            raise DiscoveryError(problem)
        if declared_source_kind(soup):
            raise DiscoveryError("non_institutional_source")
        return final, soup, len(response.body.encode("utf-8"))

    def resolve(self, result):
        inspected = []
        details = {
            "policy_version": POLICY_VERSION,
            "source_url": result.url,
            "inspected": inspected,
        }

        def decision(status, reason, name="", url=""):
            return Resolution(status, name, url, reason, json.dumps(details, ensure_ascii=False))

        def inspect(url):
            if url not in inspected:
                if len(inspected) >= 5:
                    raise DiscoveryError("site_inspection_limit")
                inspected.append(url)
            return self.page(url)

        try:
            original = normalize_url(result.url)
            # Functional error parameters remain in the URL and cannot be exported.
            if interstitial(original, BeautifulSoup("", "html.parser")):
                return decision("pending", "site_error_page")
            source_kind = known_source(original)
            if excluded(original) or source_kind:
                details["source_kind"] = source_kind or "intermediary"
                return decision("skipped", "non_institutional_source")
            access_error = None
            try:
                base, soup = inspect(original)
                details["source_final_url"] = base
            except DiscoveryError as exc:
                access_error = str(exc)
                # A homepage attempt can recover an inaccessible deep page, not a
                # challenge, explicit error, unsafe destination or foreign redirect.
                if access_error not in {
                    "site_http_403",
                    "site_http_429",
                    "site_http_202",
                    "site_http_203",
                    "network_failure",
                    "site_http_503",
                    "response_too_large",
                    "site_not_html",
                }:
                    raise
                base, soup = original, BeautifulSoup("", "html.parser")
                details["source_access_error"] = access_error
            candidates = evidence(soup, base)
            source_name, source_signals, source_conflict = identity(
                soup, base, nodes, types, ORG_TYPES
            )
            structured_identity = any(
                s["context"] in {"organization", "website", "site_name"} for s in source_signals
            )
            if source_conflict and structured_identity:
                details["identity_signals"] = source_signals
                return decision("pending", "conflicting_company_identity")
            expected_name, declared = sorted(candidates)[0] if candidates else (None, None)
            if structured_identity:
                expected_name = source_name
                details["source_identity_signals"] = source_signals
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
            mismatch = False
            unknown_offer = False
            editorial = bool(EDITORIAL.search(urlsplit(base).path)) or any(
                types(n) & EDITORIAL_TYPES for n in nodes(soup)
            )
            offer = own_offer(soup, base)
            failures = {}
            details["source_page_kind"] = "editorial" if editorial else "other"
            checked = set()
            for url in homes:
                if url in checked or not same_host(url, base):
                    continue
                checked.add(url)
                try:
                    final, home = inspect(url)
                except DiscoveryError as exc:
                    failures[url] = str(exc)
                    details["access_failures"] = failures
                    if str(exc) in NON_INSTITUTIONAL_REASONS:
                        return decision("skipped", str(exc))
                    continue
                # A redirect must still lead to the same institutional host.
                if not same_host(final, base):
                    mismatch = True
                    continue
                name, signals, conflict = identity(home, final, nodes, types, ORG_TYPES)
                details["identity_signals"] = signals
                if conflict:
                    return decision("pending", "conflicting_company_identity")
                home_types = set().union(*(types(n) for n in nodes(home)))
                if "NewsMediaOrganization" in home_types or re.search(
                    r"editorial standards|independent news (?:site|publisher)",
                    visible_text(home),
                    re.I,
                ):
                    return decision("skipped", "non_institutional_source")
                home_offer = own_offer(home, final)
                usable_offer = home_offer or (offer if not editorial or expected_name else None)
                offer_url = final if home_offer else base
                if not usable_offer and (
                    home_types
                    & {"CollegeOrUniversity", "EducationalOrganization", "GovernmentOrganization"}
                    or (name and INSTITUTION.search(name))
                ):
                    return decision("skipped", "non_provider_institution")
                # At most one same-host About/contact page supplements weak metadata.
                if not name:
                    for anchor in home.select("a[href]"):
                        label = anchor.get_text(" ", strip=True)
                        if not re.match(
                            r"^(about(?: [\w -]{1,60})?|company|sobre(?: nós)?|quem somos|contact(?: us)?)$",
                            label,
                            re.I,
                        ):
                            continue
                        try:
                            about_url = urljoin(final, anchor["href"])
                            about_url = normalize_url(about_url)
                            if not same_host(about_url, final) or about_url in inspected:
                                continue
                            about_final, about = inspect(about_url)
                            if not same_host(about_final, final):
                                break
                            about_name, about_signals, about_conflict = identity(
                                about, about_final, nodes, types, ORG_TYPES
                            )
                            if about_conflict:
                                return decision("pending", "conflicting_company_identity")
                            if about_name and any(
                                name_key(s["name"]) == name_key(about_name) for s in signals
                            ):
                                name = about_name
                                details["identity_signals"] = signals + about_signals
                                details["identity_support_url"] = about_final
                                about_offer = own_offer(about, about_final)
                                usable_offer = about_offer or usable_offer
                                if about_offer:
                                    offer_url = about_final
                        except DiscoveryError as exc:
                            failures[about_url] = str(exc)
                            details["access_failures"] = failures
                        break
                if not name:
                    continue
                expected_keys = {name_key(expected_name)} if expected_name else set()
                for signal in source_signals:
                    if signal["context"] in {"organization", "website", "site_name"}:
                        expected_keys.update(name_key(a) for a in signal["aliases"])
                home_keys = {name_key(name)}
                for signal in signals:
                    home_keys.update(name_key(a) for a in signal["aliases"])
                if expected_name and not expected_keys & home_keys:
                    mismatch = True
                    continue
                if not usable_offer:
                    unknown_offer = True
                    continue
                details.update(
                    name=name,
                    entrypoint=final,
                    declared_url=declared,
                    source_kind="own_provider_site",
                    offer_evidence=usable_offer,
                    offer_source_url=offer_url,
                )
                return decision("confirmed", "homepage_identity_verified", name, final)
            if mismatch:
                return decision("pending", "entrypoint_identity_mismatch")
            if access_error:
                return decision("pending", access_error)
            if failures:
                return decision("pending", next(iter(failures.values())))
            if editorial and not offer and not unknown_offer:
                return decision("skipped", "editorial_or_listing_page")
            return decision(
                "pending",
                "provider_offer_unverified"
                if unknown_offer
                else "missing_or_ambiguous_company_identity",
            )
        except DiscoveryError as exc:
            reason = str(exc)
            status = "skipped" if reason in NON_INSTITUTIONAL_REASONS else "pending"
            return decision(status, reason)
