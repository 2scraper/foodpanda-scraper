"""
product_parser.py
------------------
Everything this project knows about foodpanda's markup lives here. The
engines know how to drive a browser; this module knows what a vendor tile
looks like, which URLs are listings, how the site paginates, and how to tell
a page it served from a page it refused.

There is no JSON-LD path, and that is a measurement
---------------------------------------------------
§4 says to count the `application/ld+json` blocks before writing either path.
Counted, on every capture in `captures/`:

    /                          1 block   WebSite
    /city                      1 block   BreadcrumbList
    /city/lahore               3 blocks  BreadcrumbList, CollectionPage, FAQPage
    /city/lahore/area/gulberg  2 blocks  BreadcrumbList, CollectionPage

The `CollectionPage` looks promising and is not: its `mainEntity` is an
`ItemList` holding SIX `ListItem`s — a name and a URL each, no rating, no
cuisine, no image — on a page whose DOM carries FORTY-EIGHT vendor tiles. It
is an SEO snippet for the first row of the grid, not a description of the
page. Building the primary path on it would silently return 6 rows out of 48
and report success, which is this codebase's most common historical bug class
(§8).

So the primary path is the site's OWN DATA ATTRIBUTE — every tile is a
`<li data-testid="{vendor_code}">` — with the `/restaurant/{code}/{slug}` URL
pattern as the fallback, exactly as §4 prescribes for a site with no usable
structured data. The JSON-LD is still read, for one thing only: the six names
it publishes are used to cross-check the first six parsed titles in the
offline suite, which is free and catches a tile-scoping regression.

Classes here are NOT build hashes
---------------------------------
Unusually for this family, foodpanda ships stable semantic BEM class names
(`bds-c-vendor-tile__name`, `bds-c-rating__label-primary`) rather than
generated hashes. They are still not the primary anchor — `data-testid` is,
because the site puts the vendor's own id in it — but where a class is the
only handle, it is matched as a SUBSTRING (`[class*="bds-c-vendor-tile__name"]`)
so a design-system version bump that appends a modifier does not break it.

Tile scoping
------------
§4's "junk-link data theft" failure cannot happen here, and the reason is
worth stating rather than assuming: a vendor tile is a single `<li>` carrying
the vendor's code in `data-testid`, and every field is read from INSIDE that
element. There is no widening walk from an anchor to its tile at all, so
there is no level to stop one short of or one past. The one thing that does
need care is the reverse — a tile with no link in it (measured: 1 of 1,904) —
and the code is read from the `<li>` for exactly that case.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

from bs4 import BeautifulSoup

from output_writer import Product, SOURCE_DEFAULT

logger = logging.getLogger("product_parser")


# ---------------------------------------------------------------------------
# Which sites this understands
# ---------------------------------------------------------------------------
# foodpanda publishes no cross-country `<link rel="alternate" hreflang>` set —
# checked on three captures, and each carries exactly one self-referencing
# alternate — so §5's "take the host table from the site's own hreflang" has
# nothing to take. The list below is therefore the brand's own country
# operations, and every entry was then PROBED with a real browser rather than
# trusted; `live/host_probe.json` holds the raw result and the README the
# table. An entry that is in the brand's list and does not serve a listing is
# refused WITH THE REASON, because "is not a foodpanda site" would be false
# and would send the reader hunting for a typo.
HOSTS: Tuple[str, ...] = (
    "foodpanda.sg",
    "foodpanda.my",
    "foodpanda.co.th",
    "foodpanda.ph",
    "foodpanda.com.tw",
    "foodpanda.hk",
    "foodpanda.pk",
    "foodpanda.com.bd",
    "foodpanda.com.kh",
    "foodpanda.la",
    "foodpanda.com.mm",
)

# The country each host serves, for the README table and for error messages
# that can say WHICH site the reader asked for.
COUNTRY_BY_HOST: Dict[str, str] = {
    "foodpanda.sg": "Singapore",
    "foodpanda.my": "Malaysia",
    "foodpanda.co.th": "Thailand",
    "foodpanda.ph": "Philippines",
    "foodpanda.com.tw": "Taiwan",
    "foodpanda.hk": "Hong Kong",
    "foodpanda.pk": "Pakistan",
    "foodpanda.com.bd": "Bangladesh",
    "foodpanda.com.kh": "Cambodia",
    "foodpanda.la": "Laos",
    "foodpanda.com.mm": "Myanmar",
}

# Hosts that ARE foodpanda and are still refused, each with the reason.
# `foodpanda.com` is the brand's global landing page: it resolves, it serves
# HTTP 200, and it holds no vendor tiles and no /city tree at all — it is a
# country picker. Refusing it as "not a foodpanda site" would be a lie.
REFUSED_HOSTS: Dict[str, str] = {
    "foodpanda.com": (
        "foodpanda.com is the brand's global landing page, not a storefront: "
        "it serves a country picker with no vendor tiles and no /city tree. "
        "Pick a country site — foodpanda.pk, foodpanda.sg, foodpanda.co.th "
        "and the rest are listed in the README"
    ),
}


def site_host(url: str) -> Optional[str]:
    """The supported host this URL is on, or None.

    Matches with or without `www.`, because §5's lesson from a sibling repo
    is that a URL rebuilt as `https://www.{host}{path}` never matches the
    page's own on a site that answers bare. All eleven foodpanda country
    sites were probed both ways; see `live/host_probe.json`.
    """
    netloc = urlsplit(url).netloc.lower().split("@")[-1].split(":")[0]
    bare = netloc[4:] if netloc.startswith("www.") else netloc
    return bare if bare in HOSTS else None


def unsupported_reason(url: str) -> Optional[str]:
    """Why this URL cannot be scraped, or None if it can."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        return f"{url!r} is not an http(s) URL"
    netloc = parts.netloc.lower().split("@")[-1].split(":")[0]
    bare = netloc[4:] if netloc.startswith("www.") else netloc
    if bare in REFUSED_HOSTS:
        return REFUSED_HOSTS[bare]
    if bare not in HOSTS:
        return (f"{netloc!r} is not a foodpanda country site. Supported: "
                + ", ".join(HOSTS))
    if listing_kind(url) == "vendor":
        return (
            "a /restaurant/{code}/{slug} page is not supported. Measured "
            "2026-09-15: eight cold navigations and one click-through from a "
            "listing the same browser had just been served all came back HTTP "
            "403 with PerimeterX's denial page, on foodpanda.pk and "
            "foodpanda.sg alike, while listing pages answered 200 in the same "
            "sessions. No page was ever captured, so no parser was written "
            "for one. See the README, \"What a vendor page does\"")
    if listing_kind(url) == "unknown":
        return (
            f"{parts.path!r} is not a foodpanda listing. This reads vendor "
            f"listings: / , /city/{{city}} and /city/{{city}}/area/{{area}}")
    return None


def is_supported_host(url: str) -> bool:
    return site_host(url) is not None


def source_of(url: str) -> str:
    """Which country site a row came from — the `source` column."""
    return site_host(url) or SOURCE_DEFAULT


# ---------------------------------------------------------------------------
# URL shapes
# ---------------------------------------------------------------------------
_HOME_RE = re.compile(r"^/?$")
_CITY_INDEX_RE = re.compile(r"^/city/?$", re.I)
_CITY_RE = re.compile(r"^/city/(?P<city>[^/]+)/?$", re.I)
_AREA_INDEX_RE = re.compile(r"^/city/(?P<city>[^/]+)/area/?$", re.I)
_AREA_RE = re.compile(r"^/city/(?P<city>[^/]+)/area/(?P<area>[^/]+)/?$", re.I)
# The vendor code is the site's own id space: four lowercase alphanumerics on
# every one of the 1,952 tiles measured. Deliberately not pinned to exactly
# four — the site's own config template is `restaurant/%vendor-code%/
# %vendor-url-key%` with no length stated, and a longer code appearing later
# should widen the id, not break the URL.
_VENDOR_RE = re.compile(r"^/restaurant/(?P<code>[a-z0-9]{2,12})/(?P<slug>[^/]+)/?$",
                        re.I)

# The id recovery §5 asks for. JSON-LD carries no vendor code at all — its six
# ListItems have a `url` and a `name` and nothing else — so the URL is the
# only structured source, and the tile's `data-testid` is the primary one.
_SKU_IN_URL_RE = re.compile(r"/restaurant/([a-z0-9]{2,12})/", re.I)

# Query parameters the site's own links carry for analytics and that are not
# part of a page's identity. Stripped before a URL is used as a key, so the
# same vendor reached from two placements dedupes to one row.
TRACKING_PARAMS = frozenset("""
    utm_source utm_medium utm_campaign utm_term utm_content
    gclid fbclid msclkid ttclid
    src source_id click_id aid
""".split())


def strip_tracking(url: str) -> str:
    parts = urlsplit(url)
    kept = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
            if k.lower() not in TRACKING_PARAMS]
    return urlunsplit((parts.scheme, parts.netloc, parts.path,
                       urlencode(kept), ""))


def listing_kind(url: str) -> str:
    """Which kind of page this URL is.

        listing      a page of vendor tiles: /, /city/{city},
                     /city/{city}/area/{area}
        index        a directory of links rather than of vendors: /city and
                     /city/{city}/area. Named rather than folded into
                     `unknown` so the refusal can say what it actually is —
                     an index is a reasonable thing to have asked for and the
                     message should point at the pages below it.
        vendor       /restaurant/{code}/{slug}
        unknown      anything else
    """
    path = urlsplit(url).path or "/"
    if _HOME_RE.match(path):
        return "listing"
    if _AREA_RE.match(path) or _CITY_RE.match(path):
        return "listing"
    if _CITY_INDEX_RE.match(path) or _AREA_INDEX_RE.match(path):
        return "index"
    if _VENDOR_RE.match(path):
        return "vendor"
    return "unknown"


def sku_from_url(url: str) -> Optional[str]:
    match = _SKU_IN_URL_RE.search(urlsplit(url).path or "")
    return match.group(1).lower() if match else None


def slug_from_url(url: str) -> Optional[str]:
    match = _VENDOR_RE.match(urlsplit(url).path or "")
    return match.group("slug") if match else None


def city_area_from_url(url: str) -> Tuple[Optional[str], Optional[str]]:
    """The city and area this LISTING is for, from its own address.

    A vendor tile publishes no address of its own — no street, no
    neighbourhood, not even a district — so the listing's URL is the only
    location a row has, and it is the location of the LISTING rather than of
    the vendor. `/city/lahore/area/gulberg` -> ("lahore", "gulberg");
    `/city/lahore` -> ("lahore", None); `/` -> (None, None).
    """
    path = urlsplit(url).path or "/"
    match = _AREA_RE.match(path)
    if match:
        return match.group("city").lower(), match.group("area").lower()
    match = _CITY_RE.match(path)
    if match:
        return match.group("city").lower(), None
    match = _AREA_INDEX_RE.match(path)
    if match:
        return match.group("city").lower(), None
    return None, None


# ---------------------------------------------------------------------------
# Pagination — an address the site publishes itself
# ---------------------------------------------------------------------------
# This site paginates by URL, and unusually for this family there is no
# guesswork in the claim: an area listing renders a page separator between
# each batch of tiles, and the separator IS a link the site wrote —
#
#   <span class="page-number" id="2" data-testid="pageNumber">
#     <a href="https://www.foodpanda.pk/city/lahore/area/gulberg?page=2"
#        class="page-number-link">2</a></span>
#
# — so `?page=N` is the site's own contract rather than this repo's
# convention. §7's "verify first" was done the only way that counts: a cold
# fetch of `?page=2` returned 48 tiles whose codes did not overlap page 1's.
#
# Because the addresses are real, `--concurrency` above 1 is allowed here
# (unlike two sibling repos), and page 1 is still fetched alone because its
# content is what decides the rest.
PAGINATES_BY_URL = True
PAGE_PARAM = "page"

# `link[rel=next]` FIRST, per §5 — standards-based signals before build
# artefacts — even though foodpanda publishes none today: 0 occurrences
# across every capture. It leads the list so that if the site ever adds one,
# it is preferred over the testid without an edit. The second entry is the
# site's own page-number link, which is what actually matches.
NEXT_PAGE_SELECTOR = ("link[rel='next'], a[rel='next'], "
                      "[data-testid='pageNumber'] a.page-number-link")

# The separator element itself, used to attribute a tile to a page when a
# whole scrolled listing arrives in one document (see `parse_products`).
PAGE_SEPARATOR_SELECTOR = "[data-testid='pageNumber']"

# A hard stop so a malformed or infinite listing cannot run forever. The
# largest listing measured was Gulberg, Lahore, which the site itself
# numbered to page 25; 60 leaves a wide margin over that and still ends.
PAGE_CAP = 60


def paginates_by_url(url: str) -> bool:
    """Whether page N of this listing has an address of its own.

    True for the /city tree, where the site publishes the `?page=N` links
    itself. FALSE for the home page, which is a curated set of tiles with no
    pagination of any kind on it — no separators, no next link, no page
    parameter — so asking for page 2 of `/` would fetch the same 53 tiles
    again, find no new sku, and call the listing exhausted. That is the right
    answer arrived at by luck; refusing it up front is the right answer
    arrived at on purpose (§7).
    """
    if listing_kind(url) != "listing":
        return False
    return not _HOME_RE.match(urlsplit(url).path or "/")


def page_url(url: str, page: int) -> str:
    """This listing's address for page `page`.

    REPLACES an existing `page` parameter rather than appending a second one,
    and preserves every other parameter — §5's rule, and the site's own links
    are built the same way. Page 1 is the bare URL with the parameter
    removed, because that is the address the site's canonical link uses and a
    `?page=1` row would otherwise dedupe against a bare-URL row as two
    different pages.
    """
    parts = urlsplit(url)
    kept = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
            if k.lower() != PAGE_PARAM]
    if page > 1:
        kept.append((PAGE_PARAM, str(page)))
    return urlunsplit((parts.scheme, parts.netloc, parts.path,
                       urlencode(kept), parts.fragment))


def page_of_url(url: str) -> int:
    """Which page this URL addresses. 1 when it says nothing."""
    for key, value in parse_qsl(urlsplit(url).query):
        if key.lower() == PAGE_PARAM:
            try:
                return max(1, int(value))
            except ValueError:
                return 1
    return 1


# ---------------------------------------------------------------------------
# Selectors
# ---------------------------------------------------------------------------
SELECTORS: Dict[str, str] = {
    # THE anchor. Every vendor tile is an `<li>` whose class names it and
    # whose `data-testid` IS the vendor code. Matched on the class as a
    # substring so `--large`, `--small` and any future modifier all match.
    "item_card": "li[class*='bds-c-vendor-tile']",
    # The fallback anchor, and the one §4 calls a contract with search
    # engines: the product URL pattern.
    "item_link": "a[href^='/restaurant/']",
    "name": "[class*='bds-c-vendor-tile__name']",
    "rating": "[data-testid='review-and-rating']",
    "rating_value": "[class*='bds-c-rating__label-primary']",
    "rating_count": "[class*='bds-c-rating__label-secondary']",
    "image": "[data-testid='bds-c-vendor-tile__vendor-image-actual']",
    "info_row": "[data-testid='bds-c-vendor-tile__info-row-text']",
    "tag_label": "[class*='bds-c-tag__label']",
    "closed_overlay": "[data-testid='bds-c-vendor-tile__vendor-image-overlay']",
    "super_badge": "[data-testid='super-restaurant-badge']",
    "page_separator": PAGE_SEPARATOR_SELECTOR,
}


# ---------------------------------------------------------------------------
# Numbers
# ---------------------------------------------------------------------------
# The grouping conventions §4 lists, including the no-break variants: a
# rendered page uses NBSP or a narrow NBSP so a number does not wrap, and
# missing them parses "1 234" as 234.
_GROUP_SPACES = "    "
_AMOUNT = (r"\d{1,3}(?:[.,%s]\d{3})+(?:[.,]\d{1,2})?"
           r"|\d+(?:[.,]\d{1,2})?" % _GROUP_SPACES)

# Currency as foodpanda's deal labels write it, longest-first so a prefix is
# not swallowed by the bare symbol it contains (§4). Every one of these was
# read off a real label or the site's own configuration, not from a list of
# currencies the countries use: "S$ 15" and "Rs. 300" are what the tiles say.
CURRENCY_SYMBOLS: Tuple[Tuple[str, str], ...] = (
    ("S$", "SGD"),
    ("RM", "MYR"),
    ("Rs.", "PKR"),
    ("Rs", "PKR"),
    ("Tk", "BDT"),
    ("NT$", "TWD"),
    ("HK$", "HKD"),
    ("₱", "PHP"),
    ("฿", "THB"),
    ("৳", "BDT"),
    ("$", "USD"),
)

_PERCENT_RE = re.compile(r"(?P<upper>up\s+to\s+)?-?\s*(?P<pct>\d{1,3}(?:[.,]\d+)?)\s*%",
                         re.I)


def _normalize_amount(raw: str) -> Optional[float]:
    """Turn a rendered number into a float, or None.

    Implements §4's three grouping conventions and its two disambiguation
    rules: when both a dot and a comma appear the LAST one is the decimal
    point, and when only one appears, exactly three trailing digits means a
    thousands grouping (no currency in this family's range has a three-digit
    subunit).
    """
    text = raw.strip()
    if not text:
        return None
    for space in _GROUP_SPACES[1:]:
        text = text.replace(space, " ")
    text = text.replace(" ", "")
    has_dot, has_comma = "." in text, "," in text
    if has_dot and has_comma:
        decimal = "." if text.rfind(".") > text.rfind(",") else ","
        grouping = "," if decimal == "." else "."
        text = text.replace(grouping, "").replace(decimal, ".")
    elif has_comma or has_dot:
        separator = "," if has_comma else "."
        tail = text.split(separator)[-1]
        if len(tail) == 3 and text.count(separator) >= 1:
            text = text.replace(separator, "")
        else:
            text = text.replace(separator, ".")
    try:
        return float(text)
    except ValueError:
        return None


def int_in(text: str) -> Optional[int]:
    """The first whole number in `text`, grouping separators included.

    "(1,234)" -> 1234, and NOT 1 — the amazon-scraper review-count bug (§10)
    is exactly this function written as `re.search(r"\\d+", ...)`.
    """
    match = re.search(_AMOUNT, text or "")
    if not match:
        return None
    value = _normalize_amount(match.group(0))
    return int(value) if value is not None else None


def currency_in(text: str) -> Optional[str]:
    """The currency a deal label's amount is written in, or None.

    Longest symbol first. Returns None rather than a defaulted currency when
    the label states no symbol — §8, a missing currency is null.
    """
    if not text:
        return None
    for symbol, code in CURRENCY_SYMBOLS:
        if symbol in text:
            return code
    return None


# ---------------------------------------------------------------------------
# Detection: served, refused, or challenged
# ---------------------------------------------------------------------------
# §18's rule, and this site proves it twice over. BOTH of these are on EVERY
# page foodpanda serves, and either one as a marker would report the whole
# catalogue as blocked:
#
#   window._pxAppId = 'PXlJuB4eTB'       the PerimeterX sensor bootstrap,
#   "PERIMETERX_APP_ID":"lJuB4eTB"       1 occurrence on all 7 good captures
#
#   "Captcha_Modal_Warning_ErrorMessage":
#       "Failed to verify with the reCAPTCHA server"
#                                        the page's own i18n bundle, so the
#                                        string "reCAPTCHA" is on every page
#
# So `perimeterx` and `recaptcha` are NOT markers here. What separates a
# refusal from a page is the denial document's own furniture, measured at 0
# occurrences on seven served captures and non-zero on every refusal:
BLOCK_MARKERS: Tuple[str, ...] = (
    "px-captcha",                 # 42 occurrences on every denial page
    "Access to this page has been denied",
    "Please confirm you are a human",
    "window._pxUuid",
    "_pxJsClientSrc",
    "_pxHostUrl",
)

# The OTHER refusal, and it is a different vendor: a request that does not
# look like a browser at all never reaches PerimeterX, because Cloudflare
# answers first with a managed challenge. Measured with curl against all
# eleven country sites — every one of them returned HTTP 403 and this
# document, while a real browser on the same address was served normally.
CLOUDFLARE_MARKERS: Tuple[str, ...] = (
    "challenges.cloudflare.com",
    "__cf_chl",
    "Just a moment...",
)

# What this repo can actually pay to have solved, and on this site the answer
# is the interesting one: PerimeterX's denial page renders a REAL reCAPTCHA
# v2 checkbox, and `captcha_solver.py` implements exactly that.
#
#   <div id="px-captcha">
#     <div class="g-recaptcha" data-sitekey="6Lc…" data-callback="handleCaptcha">
#
# Named `BOT_CHALLENGE_MARKERS` because that is what the family calls this
# set, and `scraper_api_client.py` imports it by that name.
#
# So a foodpanda block is a `challenge` rather than a dead end, and the
# 2Captcha integration in this repo is load-bearing rather than decorative.
# NOT LIVE-VERIFIED: no funded 2Captcha key was available while this was
# written, so the solve path is implemented and exercised offline against the
# captured denial page, and has never been run end to end against the site.
# That is stated here, in the README and in the CHANGELOG rather than left
# for a reader to discover from a bill (§13, §16).
BOT_CHALLENGE_MARKERS: Tuple[str, ...] = (
    'class="g-recaptcha"',
    "data-sitekey",
    "recaptcha/api2/anchor",
    "recaptcha/api2/bframe",
    "recaptcha/api.js",
)

# Forward-looking only: a vendor this repo could NOT solve, which would make
# the page `blocked` rather than `challenge` so nothing is attempted and
# nothing is charged. None of these has ever been seen on foodpanda.
UNBOT_CHALLENGE_MARKERS: Tuple[str, ...] = (
    "hcaptcha.com/captcha",
    "geo.captcha-delivery.com",
    "datadome",
)

_SITEKEY_RE = re.compile(r'data-sitekey="([^"]+)"')

# Positive detection: what every page this site serves is built out of, and
# what no interstitial and no browser error page is (§8, §18). foodpanda's
# own media hosts. This is the signal that answers correctly for Chromium's
# built-in network-error page, which carries the SITE'S hostname in its
# `<title>` and would pass any title check.
_ASSET_MARKER = re.compile(
    r"images\.deliveryhero\.io|foodpanda\.dhmedia\.io|\.dhmedia\.io"
    r"|static\.fd-api\.com", re.I)
# Measured: 22 to 257 matches on each of seven served captures — the leanest
# was the /city index, at 22 — and 0 on every refusal document, PerimeterX
# and Cloudflare alike. Three is a wide margin under the leanest real page,
# and it is checked AFTER the unambiguous positive signals rather than before
# them (§17's classification-order trap).
_ASSET_MIN_MATCHES = 3


def served_by_foodpanda(html: str) -> bool:
    """Whether this page was built out of the site's own assets."""
    return len(_ASSET_MARKER.findall(html or "")) >= _ASSET_MIN_MATCHES


def detect_block_marker(html: str) -> Optional[str]:
    """The refusal marker this page carries, or None.

    Names the VENDOR, because "blocked (PerimeterX)" tells a reader to slow
    down and change exit while "blocked (Cloudflare)" tells them their client
    did not look like a browser at all — and those are different afternoons.
    """
    text = html or ""
    for marker in BLOCK_MARKERS:
        if marker in text:
            return "PerimeterX"
    for marker in CLOUDFLARE_MARKERS:
        if marker in text:
            return "Cloudflare"
    return None


def challenge_sitekey(html: str) -> Optional[str]:
    """The reCAPTCHA site key the denial page rendered, or None."""
    match = _SITEKEY_RE.search(html or "")
    return match.group(1) if match else None


def detect_bot_challenge(html: str, url: str = "") -> Optional[str]:
    """The SOLVABLE challenge this page rendered, or None.

    Silent about a challenge this repo cannot solve, so no solve is attempted
    and nothing is charged for it (§8: detected ≠ blocking ≠ paying).

    Gated on the page NOT being one the site served: the markers below are
    generic, and running them against a good page is how a sibling repo
    reported exit 3 on a 1.8 MB page holding the full catalogue. A served
    page that genuinely renders a challenge inside itself is still caught,
    because a served page carrying `g-recaptcha` has one.
    """
    text = html or ""
    lowered = text.lower()
    for marker in UNBOT_CHALLENGE_MARKERS:
        if marker in lowered:
            return None
    for marker in BOT_CHALLENGE_MARKERS:
        if marker.lower() in lowered:
            return "recaptcha"
    return None


def is_challenge_page(html: str) -> bool:
    """Whether this is a refusal document rather than a page."""
    return detect_block_marker(html) is not None


# The site's own "nothing here" copy. EMPTY, and deliberately so: no capture
# in this repo has ever held one, because every listing address that exists
# has vendors on it and every address that does not exist is answered with a
# 404 document rather than an empty grid.
#
# §18's lesson is why this is a list and not a guess: a marker set written
# from imagination matches nothing and turns an empty answer into a
# twenty-five-second wait and the wrong exit code. So the list stays empty
# until somebody captures the sentence, and `detect_page_state` reaches
# `empty` through the site's 404 furniture instead — which IS captured.
NO_RESULTS_MARKERS: Tuple[str, ...] = ()

# What the site's 404 actually looks like. Its `<title>` is "404 Ooops!" on
# every country site checked, and the document is served out of foodpanda's
# own assets, so it passes `served_by_foodpanda` and would otherwise be
# classified `shell` — a page still painting — and waited on.
NOT_FOUND_MARKERS: Tuple[str, ...] = (
    "404 Ooops!",
    "<title>404",
)


def is_no_results(html: str) -> bool:
    """Whether the site said, in its own words, that there is nothing here.

    A POSITIVE signal only. A listing with an empty grid and none of this
    copy is a page still painting, not an empty catalogue, and the two want
    opposite responses (§18).
    """
    text = html or ""
    if any(marker in text for marker in NOT_FOUND_MARKERS):
        return True
    lowered = text.lower()
    return any(marker.lower() in lowered for marker in NO_RESULTS_MARKERS)


def detect_page_state(html: Optional[str], status: Optional[int] = None,
                      url: str = "") -> str:
    """Which of five states this response is.

    Ordered by how much each signal PROVES rather than by what is cheap to
    check (§17). The unambiguous positive — tiles in the document — comes
    first, so a page the site plainly served can never be reported as
    blocked; the site's own 404 copy comes before any threshold; and the
    asset count is last, because it is the only signal here that is a
    threshold rather than a marker.

    `status` is positional and SECOND, matching `page_flow.classify`. Two
    engines in a sibling repo called this with `status` as a keyword and both
    crashed on their first fetch, invisibly to every offline check (§17) —
    the smoke suite here binds every shared-module call in every engine for
    that reason.
    """
    if html is None:
        return "blocked"

    # 1. Unambiguous positive: the grid is in the document.
    if _has_tiles(html):
        return "content"

    # 2. The site's own answer that there is nothing at this address.
    if is_no_results(html):
        return "empty"

    # 3. A refusal document. Before the status check because it names the
    #    vendor, which the status does not, and before the asset threshold
    #    because a refusal carries none of the site's assets anyway.
    if is_challenge_page(html):
        return "challenge" if detect_bot_challenge(html) else "blocked"

    # 4. A refusal with no document worth reading.
    if status is not None and (status in (401, 403, 429) or status >= 500):
        return "blocked"

    # 5. A challenge rendered inside a page, if one ever is.
    if detect_bot_challenge(html):
        return "challenge"

    # 6. Not built out of this site's assets at all — a browser error page or
    #    somebody else's interstitial. §18: Chromium's own network-error page
    #    carries the site's hostname in its title and nothing else of the
    #    site's, and this is the check that answers it correctly.
    if not served_by_foodpanda(html):
        return "blocked"

    # 7. Served, ours, and the grid is not there yet. Wants the readiness
    #    wait, not a refetch: refetching a shell buys another shell (§18).
    return "shell"


def _has_tiles(html: str) -> bool:
    """Whether this document holds at least one vendor tile.

    A cheap string test before the parse, because `detect_page_state` runs on
    every response including a 16 MB scrolled listing, and building a full
    soup to answer "is there a grid" costs seconds there.
    """
    return "bds-c-vendor-tile" in (html or "")


# ---------------------------------------------------------------------------
# Rows
# ---------------------------------------------------------------------------
def _text(node) -> str:
    return node.get_text(" ", strip=True) if node is not None else ""


def _absolute(page_url_: str, href: str) -> str:
    if not href:
        return ""
    if href.startswith(("http://", "https://")):
        return href
    parts = urlsplit(page_url_)
    return urlunsplit((parts.scheme or "https", parts.netloc, href, "", ""))


def _rating(tile) -> Tuple[Optional[float], Optional[int], Optional[bool]]:
    """(rating, review_count, review_count_is_floor) off one tile.

    The site prints "5" and "(57)" in two sibling spans, and caps the count
    at round numbers: "(100+)", "(500+)", "(1000+)". The cap is NOT a count,
    and returning it as one would freeze every busy vendor at exactly 100
    reviews for the rest of the file's life — so the flag rides beside it.
    """
    block = tile.select_one(SELECTORS["rating"])
    if block is None:
        return None, None, None
    value_text = _text(block.select_one(SELECTORS["rating_value"]))
    count_text = _text(block.select_one(SELECTORS["rating_count"]))
    rating = _normalize_amount(value_text) if value_text else None
    # A rating out of five: anything outside that range is not a rating, and
    # silently writing 57.0 into the column because the two spans were read
    # in the wrong order is the kind of 100%-populated-and-wrong column §10
    # exists to catch.
    if rating is not None and not (0.0 <= rating <= 5.0):
        logger.warning("discarding out-of-range rating %r on tile %r",
                       value_text, tile.get("data-testid"))
        rating = None
    count = int_in(count_text) if count_text else None
    is_floor = ("+" in count_text) if count_text else None
    return rating, count, is_floor


# The overflow chip the site renders when a tile has more deal labels than it
# can fit. It is a COUNT of hidden labels, not a label, and leaving it in
# would put "+1" in a column of offers.
_OVERFLOW_TAG_RE = re.compile(r"^\+\s*\d+$")


def _tags(tile) -> List[str]:
    labels = []
    for node in tile.select(SELECTORS["tag_label"]):
        text = _text(node)
        if text and not _OVERFLOW_TAG_RE.match(text):
            labels.append(text)
    return labels


# "Free Delivery" on foodpanda.pk, "Free delivery" on foodpanda.sg. A
# case-sensitive match reports 0% free delivery on Singapore, where the
# measured figure is 96%.
_FREE_DELIVERY_RE = re.compile(r"^free\s+delivery$", re.I)


def _discount(tags: List[str]) -> Tuple[Optional[float], Optional[str],
                                        Optional[bool], Optional[float],
                                        Optional[str]]:
    """(discount_pct, discount_label, is_upper_bound, min_order, currency).

    Reads the FIRST label that states a percentage, and keeps three things a
    bare number would lose:

      * the label verbatim, so a wrong parse is visible;
      * whether it said "Up to", which makes the number a CEILING — 21% of
        the measured labels are that shape, and reporting "Up to 35% off" as
        a 35% discount is a guess presented as a fact (§8);
      * the minimum spend where the label attaches one ("20% off Rs. 300",
        "15% off S$ 15"), which is the only money on a tile and is a
        condition of a voucher rather than a price.

    §4's "strip percentages BEFORE matching" applies in reverse here: the
    percentage is what is wanted, so the MINIMUM SPEND is matched only in
    what remains after the percentage has been consumed. Without that,
    "20% off Rs. 300" yields a minimum order of 20.
    """
    for label in tags:
        match = _PERCENT_RE.search(label)
        if not match:
            continue
        pct = _normalize_amount(match.group("pct"))
        upper = bool(match.group("upper"))
        remainder = label[match.end():]
        currency = currency_in(remainder)
        amount = None
        if currency:
            amount_match = re.search(_AMOUNT, remainder)
            if amount_match:
                amount = _normalize_amount(amount_match.group(0))
        return pct, label, upper, amount, currency
    return None, None, None, None, None


# "Closed until Sat 10:20 Order for later" — the button's words come with it,
# so the time is taken as what sits between the site's own preposition and
# the call to action. Matched as "the first weekday-and-clock in the overlay"
# rather than by translating "Closed until", which is per-locale.
_OPENS_AT_RE = re.compile(
    r"([A-Za-z-￿]{2,12}\.?\s+\d{1,2}[:.]\d{2})")


def _closed(tile) -> Tuple[Optional[bool], Optional[str]]:
    """(is_open, opens_at) from the tile's image overlay.

    An open vendor has no overlay at all, so the absence IS the reading —
    which makes this one of the few columns where a missing element means
    True rather than None.
    """
    overlay = tile.select_one(SELECTORS["closed_overlay"])
    if overlay is None:
        return True, None
    text = _text(overlay)
    match = _OPENS_AT_RE.search(text)
    return False, match.group(1) if match else (text or None)


def _cuisines(tile) -> List[str]:
    """The tile's cuisine list, in the site's own order.

    Read from the FIRST info row only. A tile can carry a second one — "In-Store
    Price", "Islandwide", "Organic" — and those are shop attributes rather
    than cuisines; folding them in would put "In-Store Price" in a column of
    food categories on 16% of Pakistani rows.
    """
    rows = tile.select(SELECTORS["info_row"])
    if not rows:
        return []
    text = _text(rows[0])
    return [part.strip() for part in re.split(r"[,·•]", text) if part.strip()]


def _image_url(tile) -> Optional[str]:
    """The tile's image, recognised POSITIVELY by the site's media hosts.

    §4's rule in its narrow form: a positive host check means a future
    placeholder, a tracking pixel or a data-URI spinner cannot fill this
    column with something that is not a photo of the vendor.
    """
    node = tile.select_one(SELECTORS["image"])
    if node is None:
        return None
    src = node.get("src") or node.get("data-src") or ""
    return src if _ASSET_MARKER.search(src) else None


def parse_products(html: str, url: str, page: int = 1,
                   source: Optional[str] = None) -> List[Product]:
    """Every vendor tile in `html`, as rows.

    `page` is threaded in rather than inferred, and it matters: `position`
    restarts at 1 on every page, so without the page number 60 of 119 rows in
    a sibling repo's two-page run silently claimed a position another row
    already held (§18). Where the document holds a whole SCROLLED listing —
    which is what an infinite-scroll capture is — the site's own page
    separators are used instead, so each tile gets the page it really came
    from rather than all of them getting `page`.
    """
    soup = BeautifulSoup(html or "", "html.parser")
    tiles = soup.select(SELECTORS["item_card"])
    if not tiles:
        return []

    source = source or source_of(url)
    city, area = city_area_from_url(url)
    page_starts = _page_starts(soup, page)

    rows: List[Product] = []
    position_in_page: Dict[int, int] = {}
    for index, tile in enumerate(tiles):
        tile_page = page_starts(index)
        position_in_page[tile_page] = position_in_page.get(tile_page, 0) + 1

        link = tile.select_one(SELECTORS["item_link"])
        href = link.get("href") if link is not None else ""
        # The code from the tile's OWN attribute first. The href agreed on
        # 1,903 of 1,904 measured tiles and the odd one out has no link at
        # all — so reading the attribute is what keeps that row identified
        # rather than dropped.
        sku = (tile.get("data-testid") or "").strip().lower() or None
        if not sku and href:
            sku = sku_from_url(href)
        if not sku:
            continue

        name_node = tile.select_one(SELECTORS["name"])
        # `title` rather than the text node: CSS truncates a long name with
        # an ellipsis in the text and leaves the attribute whole.
        title = (name_node.get("title") if name_node is not None else None) \
            or _text(name_node) or None

        rating, review_count, count_is_floor = _rating(tile)
        tags = _tags(tile)
        pct, label, upper, min_order, min_currency = _discount(tags)
        is_open, opens_at = _closed(tile)
        cuisines = _cuisines(tile)

        rows.append(Product(
            source=source,
            url=_absolute(url, strip_tracking(href)) if href else "",
            sku=sku,
            title=title,
            discount_pct=pct,
            rating=rating,
            review_count=review_count,
            image_url=_image_url(tile),
            category=", ".join(cuisines) or None,
            page=tile_page,
            position=position_in_page[tile_page],
            is_open=is_open,
            opens_at=opens_at,
            cuisines=cuisines,
            tags=tags,
            discount_label=label,
            discount_is_upper_bound=upper,
            min_order=min_order,
            min_order_currency=min_currency,
            free_delivery=any(_FREE_DELIVERY_RE.match(t) for t in tags),
            review_count_is_floor=count_is_floor,
            is_super_vendor=tile.select_one(SELECTORS["super_badge"]) is not None,
            slug=slug_from_url(href) if href else None,
            city=city,
            area=area,
        ))
    return rows


def _page_starts(soup, default_page: int):
    """A function from tile index to the page that tile came from.

    A cold fetch of `?page=N` holds one page and no separators, so every tile
    gets `default_page` and this costs nothing. A SCROLLED listing holds many
    pages in one document with the site's own `<span data-testid="pageNumber"
    id="N">` between them — measured: 24 separators and 1,904 tiles in one
    capture — and there the separator's own `id` is the page number, which is
    better evidence than anything this repo could count.
    """
    separators = soup.select(SELECTORS["page_separator"])
    if not separators:
        return lambda index: default_page

    tiles = soup.select(SELECTORS["item_card"])
    order = {id(node): position for position, node in enumerate(soup.find_all(True))}
    boundaries: List[Tuple[int, int]] = []
    for separator in separators:
        try:
            number = int(separator.get("id") or "")
        except ValueError:
            continue
        boundaries.append((order.get(id(separator), 0), number))
    boundaries.sort()
    tile_order = [order.get(id(tile), 0) for tile in tiles]

    def page_for(index: int) -> int:
        position = tile_order[index] if index < len(tile_order) else 0
        page = default_page
        for boundary, number in boundaries:
            if boundary < position:
                page = number
            else:
                break
        return page

    return page_for


# ---------------------------------------------------------------------------
# The JSON-LD that exists, used only as a cross-check
# ---------------------------------------------------------------------------
def jsonld_vendor_names(html: str) -> List[str]:
    """The names in the page's `CollectionPage` ItemList, in its order.

    SIX of them on a page of forty-eight, which is why this is not the
    primary path (see the module docstring). It is here because the offline
    suite compares it against the first six parsed titles: if a tile-scoping
    change ever made the parser read the wrong element, these six names are
    an independent statement by the site of what the top of the grid holds.
    """
    names: List[str] = []
    soup = BeautifulSoup(html or "", "html.parser")
    for block in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            data = json.loads(block.string or block.get_text() or "")
        except (ValueError, TypeError):
            continue
        for node in (data if isinstance(data, list) else [data]):
            if not isinstance(node, dict) or node.get("@type") != "CollectionPage":
                continue
            # `mainEntity` may legally be absent, null, a dict or a list —
            # §4's table, and a `.get()` default does NOT cover an explicit
            # null.
            main = node.get("mainEntity") or {}
            if isinstance(main, list):
                main = next((m for m in main if isinstance(m, dict)), {})
            if not isinstance(main, dict):
                continue
            for item in main.get("itemListElement") or []:
                if isinstance(item, dict) and item.get("name"):
                    names.append(item["name"])
    return names


# ---------------------------------------------------------------------------
# Completeness
# ---------------------------------------------------------------------------
class ResultsRange:
    """How many tiles this page says it holds, where it says anything.

    foodpanda publishes no counter — no "1-48 of 1,904", no total anywhere in
    the document — so `expected_on_page` is None on every page and the
    arithmetic §8 describes for a ranked grid is not available here. Stated
    as a measurement rather than left as an absent function, so the next
    reader does not go looking for a counter that is not there.

    What IS available is the site's own page separators, and a page usually
    holds 48 tiles. `TYPICAL_PAGE_SIZE` below is used only to warn about a
    THIN page, never to conclude one is complete.
    """

    def __init__(self, expected_on_page: Optional[int] = None,
                 total: Optional[int] = None):
        self.expected_on_page = expected_on_page
        self.total = total


# What a full page usually holds. NOT a constant the site guarantees, and the
# measurement is why: three cold fetches of consecutive pages of one listing
# returned 48, 48 and 44 tiles, while a scrolled capture of the same listing
# showed 48 in every one of its 24 labelled segments. So the site batches at
# 48 and delivers fewer when its own catalogue moves between requests.
#
# Used ONLY to describe a page in a log line and to set the thin-page warning
# below. Nothing decides completeness from it — the last page of any listing
# is short by definition, and a run that treated "fewer than 48" as failure
# would fail on every listing's final page.
TYPICAL_PAGE_SIZE = 48

# The warning threshold, at half the typical page. No ordinary page in any
# capture or live run has come back below it; a page that does is worth a
# line in the log even though it is not, on its own, an error.
THIN_PAGE_FLOOR = TYPICAL_PAGE_SIZE // 2


def results_range(html: str) -> ResultsRange:
    return ResultsRange()


def total_results(html: str) -> Optional[int]:
    return None
