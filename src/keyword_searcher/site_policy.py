"""Source evidence, independent of query relevance and company age."""

import re
from urllib.parse import parse_qs, urlsplit

# Named platforms are sources, not a negative list of candidate suppliers.
SOURCE_HOSTS = {
    "pubmed.ncbi.nlm.nih.gov": "research_source",
    "pmc.ncbi.nlm.nih.gov": "research_source",
    "arxiv.org": "research_source",
    "biorxiv.org": "research_source",
    "medrxiv.org": "research_source",
    "scholar.google.com": "research_source",
    "springer.com": "editorial_source",
    "nature.com": "editorial_source",
    "sciencedirect.com": "editorial_source",
    "wiley.com": "editorial_source",
    "frontiersin.org": "editorial_source",
    "eurekalert.org": "editorial_source",
    "news-medical.net": "editorial_source",
    "azolifesciences.com": "editorial_source",
    "azonano.com": "editorial_source",
    "azom.com": "editorial_source",
    "unite.ai": "editorial_source",
    "asiaresearchnews.com": "editorial_source",
    "theconversation.com": "editorial_source",
    "globo.com": "editorial_source",
    "innovationorigins.com": "editorial_source",
    "ioplus.nl": "editorial_source",
    "uscnucleus.org": "editorial_source",
    "energiainteligenteufjf.com.br": "editorial_source",
    "pure.johnshopkins.edu": "research_source",
    "research.monash.edu": "research_source",
    "edspace.american.edu": "research_source",
    "open-neuromorphic.org": "community_source",
    "findaphd.com": "listing_source",
    "arpa-h.gov": "funding_source",
    "anr.fr": "funding_source",
    "independent.co.uk": "editorial_source",
    "artecult.com": "editorial_source",
    "springernature.com": "editorial_source",
    "developers.google.com": "documentation_source",
}

OFFER = re.compile(
    r"\b(our (?:products?|services?|solutions?|platforms?)|"
    r"products? (?:and|&) services?|subscription plans|request (?:a )?quote|"
    r"buy now|shop now|book (?:a )?demo|request (?:a )?demo|"
    r"nossos (?:produtos|serviços|soluções)|solicite (?:uma )?(?:cotação|demonstração))\b",
    re.I,
)
OFFER_LABEL = re.compile(
    r"^(?:shop|buy (?:the |our )?[\w -]{2,60}|products?|services?|solutions?|platforms?|produtos|serviços|soluções)"
    r"(?:\s+(?:and|&|e)\s+(?:products?|services?|solutions?|produtos|serviços|soluções))?$",
    re.I,
)
INSTITUTION = re.compile(
    r"\b(university|universidade|université|academic resource|research institute|"
    r"funding agency|grant agency|news publisher|editorial standards|trade union)\b",
    re.I,
)


def known_source(url):
    host = urlsplit(url).hostname or ""
    return next(
        (
            kind
            for domain, kind in SOURCE_HOSTS.items()
            if host == domain or host.endswith("." + domain)
        ),
        None,
    )


def visible_text(soup, *, exclude=()):
    """Do not treat scripts, templates or structured metadata as visible offers."""
    return " ".join(
        str(text).strip()
        for text in soup.find_all(string=True)
        if text.parent.name not in {"script", "style", "noscript", "template", "title", *exclude}
        and not any(
            p.name in {"script", "style", "noscript", "template", *exclude} for p in text.parents
        )
        and not any(
            re.search(
                r"cookie-banner|cookie-consent|onetrust|didomi|cmplz",
                " ".join([p.get("id", ""), *p.get("class", [])]),
                re.I,
            )
            for p in text.parents
        )
    )


def declared_source_kind(soup):
    """Explicit portal/publishing identity takes precedence over navigation menus."""
    names = [t.get("content", "") for t in soup.select('meta[property="og:site_name"]')]
    if soup.title:
        names.append(soup.title.get_text(" ", strip=True))
    if any(
        re.search(
            r"(?:^|[|])\s*(?:research (?:portal|communities)\b|publication repository\b)", n, re.I
        )
        for n in names
    ):
        return "research_source"
    if any(re.search(r"today['’]s headlines|latest breaking news", n, re.I) for n in names):
        return "editorial_source"
    return None


def own_offer(soup, base):
    from urllib.parse import urljoin

    from .models import DiscoveryError
    from .urls import normalize_url, same_host

    text = visible_text(soup, exclude={"footer", "aside", "blockquote"})
    match = OFFER.search(text)
    if match:
        return match.group(0)
    for anchor in soup.select("a[href]"):
        label = anchor.get_text(" ", strip=True)
        if not OFFER_LABEL.fullmatch(label):
            continue
        try:
            target = normalize_url(urljoin(base, anchor["href"]))
        except DiscoveryError:
            continue
        if same_host(base, target):
            return label
    return None


def interstitial(url, soup):
    errors = parse_qs(urlsplit(url).query)
    if any(key.lower() == "error" and values for key, values in errors.items()):
        return "site_error_page"
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    headings = [h.get_text(" ", strip=True) for h in soup.select("h1, main h2, body > h2")[:5]]
    if any(
        re.search(
            r"^(?:just a moment|access denied|attention required|verify (?:you are|you're) human|"
            r"cookies? (?:are )?(?:not supported|required)|sign in to continue|"
            r"log in to continue|service unavailable|page not found|403 forbidden)",
            text.strip(),
            re.I,
        )
        for text in [title, *headings]
    ) or soup.select_one("#challenge-form, #cf-challenge-running, form[action*='/challenge/']"):
        return "site_interstitial"
    return None
