"""Synthetic regressions for patterns observed in the organoid audit."""

import json
from unittest.mock import Mock

import pytest

from keyword_searcher.http import Response
from keyword_searcher.models import DiscoveryError, SearchResult
from keyword_searcher.resolve import WebsiteResolver

ROOT = "https://supplier.example.org/"
OFFER = '<nav><a href="/services">Services</a></nav>'


def schema(kind="Organization", name="Synthetic Supplier", **extra):
    return (
        '<script type="application/ld+json">'
        + json.dumps({"@type": kind, "name": name, "url": ROOT, **extra})
        + "</script>"
    )


def resolver(pages):
    client = Mock()

    def get(url):
        value = pages.get(url)
        if value is None:
            raise DiscoveryError("not_available_in_fixture")
        if isinstance(value, tuple):
            status, body = value
        else:
            status, body = 200, value
        return Response(url, status, body, "text/html")

    client.get.side_effect = get
    return WebsiteResolver(client), client


@pytest.mark.parametrize("path", ["product", "news/product", "blog/technical-report"])
def test_own_article_resolves_verified_provider_home(path):
    url = ROOT + path
    site, client = resolver({url: schema() + schema("Article"), ROOT: schema() + OFFER})
    result = site.resolve(SearchResult("", url))
    assert (result.status, result.name, result.url) == ("confirmed", "Synthetic Supplier", ROOT)
    assert json.loads(result.evidence)["source_page_kind"] == "editorial"
    assert client.get.call_count == 2


def test_article_schema_on_home_does_not_override_provider_identity():
    site, _ = resolver({ROOT: schema() + schema("Article") + OFFER})
    assert site.resolve(SearchResult("", ROOT)).status == "confirmed"


def test_fragmented_copyright_rights_notice_is_not_an_owner_conflict():
    site, _ = resolver(
        {
            ROOT: schema()
            + OFFER
            + "<footer>© 2026 Synthetic Supplier. All Rights<br>Reserved</footer>"
        }
    )
    assert site.resolve(SearchResult("", ROOT)).status == "confirmed"


@pytest.mark.parametrize(
    "host",
    [
        "pubmed.ncbi.nlm.nih.gov",
        "eurekalert.org",
        "link.springer.com",
        "findaphd.com",
        "arpa-h.gov",
        "anr.fr",
        "azolifesciences.com",
        "asiaresearchnews.com",
        "ioplus.nl",
        "uscnucleus.org",
        "energiainteligenteufjf.com.br",
        "pure.johnshopkins.edu",
        "research.monash.edu",
        "edspace.american.edu",
        "open-neuromorphic.org",
    ],
)
def test_recognized_source_skipped_before_access(host):
    site, client = resolver({})
    outcome = site.resolve(SearchResult("Supplier", f"https://{host}/paper"))
    assert (outcome.status, outcome.reason) == ("skipped", "non_institutional_source")
    client.get.assert_not_called()
    assert json.loads(outcome.evidence)["source_kind"]


def test_host_rules_do_not_match_suffix_spoof():
    site, client = resolver({})
    result = site.resolve(SearchResult("", "https://eurekalert.org.example.org/"))
    assert result.status == "pending"
    client.get.assert_called_once()


def test_research_core_with_own_services_is_eligible():
    site, _ = resolver({ROOT: schema(name="Synthetic Research Institute") + OFFER})
    assert site.resolve(SearchResult("", ROOT)).status == "confirmed"


def test_academic_identity_without_offer_is_a_source():
    site, _ = resolver({ROOT: schema("EducationalOrganization", "Synthetic University")})
    assert site.resolve(SearchResult("", ROOT)).status == "skipped"


def test_generic_organization_without_offer_stays_pending():
    site, _ = resolver({ROOT: schema()})
    assert site.resolve(SearchResult("", ROOT)).reason == "provider_offer_unverified"


def test_unknown_publisher_with_products_menu_is_not_a_provider():
    site, _ = resolver({ROOT: schema() + schema("NewsMediaOrganization") + OFFER})
    assert site.resolve(SearchResult("", ROOT)).status == "skipped"


def test_article_customer_and_quoted_offer_do_not_identify_the_publisher():
    url = ROOT + "article"
    article = schema(
        "Article", publisher={"@type": "Organization", "name": "Customer", "url": ROOT}
    )
    site, _ = resolver(
        {
            url: article + "<blockquote>Our products</blockquote>",
            ROOT: schema(name="Synthetic Publisher"),
        }
    )
    assert site.resolve(SearchResult("Customer", url)).status != "confirmed"


def test_website_brand_and_developer_organization_conflict():
    site, _ = resolver(
        {
            ROOT: schema(name="Synthetic Web Agency LLC")
            + schema("WebSite", "Synthetic Supplier")
            + OFFER
        }
    )
    result = site.resolve(SearchResult("", ROOT))
    assert (result.status, result.reason) == ("pending", "conflicting_company_identity")
    assert not result.name


def test_copyright_owner_conflict_is_not_masked_by_single_organization():
    site, _ = resolver(
        {
            ROOT: schema(name="Synthetic Agency")
            + "<footer>© 2026 Synthetic Supplier</footer>"
            + OFFER
        }
    )
    assert site.resolve(SearchResult("", ROOT)).reason == "conflicting_company_identity"


def test_explicit_brand_legal_alias_and_legal_suffix_are_compatible():
    site, _ = resolver(
        {
            ROOT: schema(name="Synthetic Holdings Ltd", alternateName="Synthetic Supplier")
            + schema("WebSite")
            + OFFER
        }
    )
    assert site.resolve(SearchResult("", ROOT)).status == "confirmed"
    site, _ = resolver(
        {
            ROOT: schema(name="Synthetic Supplier S.L.")
            + schema("WebSite", "syntheticsupplier.com")
            + OFFER
        }
    )
    assert site.resolve(SearchResult("", ROOT)).status == "confirmed"


def test_explicit_alias_survives_product_to_home_verification():
    url = ROOT + "product"
    site, _ = resolver(
        {
            url: schema(name="Synthetic Holdings Ltd", alternateName="Synthetic Supplier"),
            ROOT: schema("WebSite") + OFFER,
        }
    )
    assert site.resolve(SearchResult("", url)).status == "confirmed"


@pytest.mark.parametrize(
    "metadata",
    [
        "<title>Synthetic Supplier</title><footer>© 2026 Synthetic Supplier Pte Ltd</footer>",
        "<title>Synthetic Supplier | Instruments</title><p>At Synthetic Supplier, we develop instruments.</p>",
        '<img src="/logo.svg" alt="Synthetic Supplier Logo"><footer>Copyright © 2026 Synthetic Supplier. All rights reserved.</footer>',
    ],
)
def test_visible_identity_requires_agreement_across_contexts(metadata):
    site, _ = resolver({ROOT: metadata + OFFER})
    result = site.resolve(SearchResult("Unrelated search title", ROOT))
    assert (result.status, result.name) == ("confirmed", "Synthetic Supplier")
    assert len(json.loads(result.evidence)["identity_signals"]) >= 2


@pytest.mark.parametrize(
    "metadata",
    [
        "<title>Synthetic Supplier</title>",
        '<title>Synthetic Supplier</title><meta property="og:title" content="Synthetic Supplier">',
        "<title>Synthetic Supplier</title><footer>Website by Synthetic Supplier</footer>",
        '<title>Synthetic Supplier</title><img alt="Unrelated Logo" src="logo.svg">',
    ],
)
def test_domain_search_title_and_head_duplicates_do_not_supply_identity(metadata):
    site, _ = resolver({ROOT: metadata + OFFER})
    assert site.resolve(SearchResult("Synthetic Supplier", ROOT)).status == "pending"


def test_about_page_supports_root_identity_with_same_host():
    site, client = resolver(
        {
            ROOT: '<title>Synthetic Supplier</title><a href="/about">About us</a>' + OFFER,
            ROOT + "about": schema(),
        }
    )
    result = site.resolve(SearchResult("", ROOT))
    assert (result.status, result.url) == ("confirmed", ROOT)
    assert json.loads(result.evidence)["identity_support_url"] == ROOT + "about"
    assert client.get.call_count == 2


def test_foreign_about_link_not_followed():
    site, client = resolver(
        {
            ROOT: '<title>Synthetic Supplier</title><a href="https://foreign.example.org/about">About us</a>'
            + OFFER
        }
    )
    assert site.resolve(SearchResult("", ROOT)).status == "pending"
    client.get.assert_called_once()


def test_403_deep_page_can_recover_home_and_retains_original_error():
    url = ROOT + "product"
    site, client = resolver({url: (403, ""), ROOT: schema() + OFFER})
    result = site.resolve(SearchResult("", url))
    assert (result.status, result.url) == ("confirmed", ROOT)
    assert json.loads(result.evidence)["source_access_error"] == "site_http_403"
    assert client.get.call_count == 2


def test_403_without_verified_home_remains_access_failure():
    url = ROOT + "product"
    site, _ = resolver({url: (403, ""), ROOT: "<title>Supplier</title>"})
    assert site.resolve(SearchResult("", url)).reason == "site_http_403"


def test_article_with_inaccessible_home_is_pending_not_discarded():
    url = ROOT + "news/product"
    site, _ = resolver({url: schema("Article"), ROOT: (403, "")})
    result = site.resolve(SearchResult("", url))
    assert (result.status, result.reason) == ("pending", "site_http_403")
    assert json.loads(result.evidence)["access_failures"][ROOT] == "site_http_403"


def test_website_identity_must_agree_between_source_and_home():
    url = ROOT + "product"
    site, _ = resolver(
        {
            url: schema("WebSite", "Synthetic Alpha"),
            ROOT: schema("WebSite", "Synthetic Beta") + OFFER,
        }
    )
    assert site.resolve(SearchResult("", url)).reason == "entrypoint_identity_mismatch"


@pytest.mark.parametrize(
    "body",
    [
        "<title>Just a moment...</title>",
        "<title>Synthetic Supplier</title><h1>Verify you are human</h1>",
        "<title>Sign in to continue</title>",
    ],
)
def test_interstitial_cannot_confirm_even_with_brand_and_offer(body):
    site, _ = resolver({ROOT: body + schema() + OFFER})
    assert site.resolve(SearchResult("", ROOT)).reason == "site_interstitial"


def test_error_parameter_is_retained_as_evidence_and_not_fetched():
    site, client = resolver({})
    url = ROOT + "?error=cookies_not_supported&code=example"
    result = site.resolve(SearchResult("", url))
    assert (result.status, result.reason) == ("pending", "site_error_page")
    assert json.loads(result.evidence)["source_url"] == url
    client.get.assert_not_called()


def test_cookie_banner_and_functional_query_do_not_block_valid_home():
    url = ROOT + "?tenant=2"
    site, _ = resolver(
        {url: schema(url=url) + OFFER + "<aside>We use cookies. Accept cookies.</aside>"}
    )
    result = site.resolve(SearchResult("", url))
    assert (result.status, result.url) == ("confirmed", url)


def test_scripts_cannot_invent_visible_identity_or_offer():
    site, _ = resolver(
        {
            ROOT: '<title>Synthetic Supplier</title><script>"At Synthetic Supplier, we provide services. Our products"</script>'
        }
    )
    assert site.resolve(SearchResult("", ROOT)).status == "pending"


def test_multiple_home_links_cannot_exceed_inspection_budget():
    links = "".join(f'<a href="/home{i}">Home</a>' for i in range(10))
    pages = {
        ROOT + "product": links,
        **{ROOT + f"home{i}": "" for i in range(10)},
        ROOT: schema() + OFFER,
    }
    site, client = resolver(pages)
    assert site.resolve(SearchResult("", ROOT + "product")).status == "pending"
    assert client.get.call_count <= 5


def test_cache_reuses_home_and_has_bounded_size():
    site, client = resolver({ROOT: schema() + OFFER})
    for _ in range(3):
        assert site.resolve(SearchResult("", ROOT)).status == "confirmed"
    client.get.assert_called_once()
    for i in range(150):
        site.resolve(SearchResult("", ROOT + f"unknown{i}"))
    assert len(site.cache) <= 128


def test_cache_also_bounds_content_size_and_does_not_retain_tracebacks():
    body = "<title>Page</title>" + "x" * (1024 * 1024)
    site, _ = resolver({ROOT + str(i): body for i in range(10)})
    for i in range(10):
        site.page(ROOT + str(i))
    assert sum(value[2] for value in site.cache.values()) <= 8 * 1024 * 1024
    with pytest.raises(DiscoveryError):
        site.page(ROOT + "missing")
    assert isinstance(site.cache[ROOT + "missing"], str)
