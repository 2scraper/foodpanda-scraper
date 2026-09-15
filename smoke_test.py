#!/usr/bin/env python3
"""foodpanda-scraper — offline smoke tests.

One file of plain functions with fixtures loaded from
`fixtures_generated.json`, no pytest required. `tests/test_smoke.py` wraps it
as a single pytest test so `pytest` works as an entry point without a second
copy of the checks.

    python3 smoke_test.py

It MUST pass with no engine library installed at all: every
`import playwright_scraper` / `selenium_scraper` / `puppeteer_scraper` is
guarded and the skip is REPORTED, because "skipped, engine absent" reads
exactly like a passing run. CI's engine-smoke job installs each engine in its
own venv and fails if that skip list is non-empty.

The fixtures are cut from real captures by `make_fixtures.py`, which proves
each one parses IDENTICALLY to its untrimmed original, column for column,
and scrubs the refusal pages' site keys. Do not hand-edit them.

WHAT THIS SUITE IS FOR, beyond the obvious
------------------------------------------
Most of these checks exist because of a specific failure, in this repo or in
a sibling. The ones worth knowing about before you change anything:

  * `test_a_refused_page_is_not_an_empty_one` pins the bug THIS repo's first
    live run found. Page 2 of a three-page run was refused on every attempt,
    fell through to the parse, produced 0 rows, and the run loop read that as
    "this page added no sku we had not already seen" — the end of the
    listing. The run reported status `complete` and exit 0 while holding a
    third of the catalogue. `no_new_products` is a COMPLETE stop reason, so
    nothing downstream could tell.

  * `test_the_challenge_is_not_solvable` pins the other one. PerimeterX's
    denial page renders what looks like a reCAPTCHA v2 checkbox, and on that
    evidence this repo first called it a solvable `challenge`. The loader
    beside it is `recaptcha/enterprise.js`, which `captcha_solver.py` does
    not implement — so a solve would have been charged for and would have
    bought a token the site rejects.

  * `test_values_on_real_fixtures` asserts VALUES, not coverage. A column can
    be 100% populated and entirely wrong: every home-page row here came out
    with `category = "From 25 min"` until the info rows were read by meaning
    rather than by position.

  * `test_markers_that_match_every_page` is §18's rule as an assertion. This
    site ships `_pxAppId` and the string "reCAPTCHA" on EVERY page it serves,
    so either as a marker would report the whole catalogue as blocked.

  * `test_engine_parity` binds every shared-module call in every engine
    against the callee's REAL signature. Two engines in a sibling repo called
    `classify(html, url=...)` where the parameter is positional, both crashed
    on their first fetch, and nothing short of a live run saw it.
"""

import ast
import builtins
import contextlib
import csv as csv_module
import importlib
import inspect
import io
import json
import os
import re
import sys
import tempfile
from dataclasses import asdict, fields

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import env_config
import page_flow
import product_parser
from diff_runs import HOURS_FIELDS, TRACKED_FIELDS, diff_products
from output_writer import (COMPLETE_STOP_REASONS, EXIT_BLOCKED,
                           EXIT_NO_PRODUCTS, EXIT_PARTIAL,
                           LIST_CSV_SEPARATOR, Product, ROW_CLASS_BY_MODE,
                           SOURCE_DEFAULT, UNIQUE_BY_SKU_MODES, dedupe_by_key,
                           finish_run, run_meta, save, write_csv)
from product_parser import (BOT_CHALLENGE_MARKERS, BLOCK_MARKERS,
                            CLOUDFLARE_MARKERS, COUNTRY_BY_HOST, HOSTS,
                            NEXT_PAGE_SELECTOR, NO_RESULTS_MARKERS,
                            PAGE_CAP, PAGINATES_BY_URL, REFUSED_HOSTS,
                            SELECTORS, THIN_PAGE_FLOOR, TYPICAL_PAGE_SIZE,
                            UNSOLVABLE_CHALLENGE_MARKERS,
                            WOULD_BE_SOLVABLE_MARKERS, city_area_from_url,
                            currency_in, detect_block_marker,
                            detect_bot_challenge, detect_page_state, int_in,
                            is_no_results, is_supported_host,
                            jsonld_vendor_names, listing_kind,
                            listing_page_kind, page_of_url, page_url,
                            paginates_by_url, parse_products,
                            paginates_by_url as _paginates, results_range,
                            served_by_foodpanda, site_host, sku_from_url,
                            slug_from_url, source_of, strip_tracking,
                            total_results, unsolvable_challenge,
                            unsupported_reason)
from proxy_pool import ProxyPool, mask, split_credentials, to_playwright

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
ENGINES = ("playwright_scraper", "puppeteer_scraper", "selenium_scraper")
SHARED_MODULES = {"page_flow": page_flow, "product_parser": product_parser}

_failures = []
_total_checks = 0


def check(label, condition):
    """Print and record one check. Returns the condition so callers can
    accumulate with `ok &= check(...)`."""
    global _total_checks
    _total_checks += 1
    if condition:
        print("  PASS  %s" % label)
    else:
        print("  FAIL  %s" % label)
        _failures.append(label)
    return bool(condition)


def group(title):
    print("\n== %s" % title)


def _raises(fn):
    """True if `fn()` raises. Used where refusing is the correct behaviour."""
    try:
        fn()
    except Exception:
        return True
    return False


_FIXTURE_PATH = os.path.join(REPO_ROOT, "fixtures_generated.json")
if not os.path.exists(_FIXTURE_PATH):
    # Said in words rather than as a bare FileNotFoundError, because the first
    # time this happens it is usually not missing from the disk — it is
    # missing from the COMMIT. `.gitignore` carries a blanket `*.json` (a
    # scraper's own output is large and stale by the time anyone reads it),
    # which swallows it silently: the whole suite is green locally and every
    # CI job dies at import. `test_required_files_are_committed` catches that
    # case directly.
    raise SystemExit(
        f"fixtures_generated.json is missing from {REPO_ROOT}.\n"
        f"If you are in a clean checkout, it should have been committed — "
        f"check that .gitignore's `*.json` rule still carries the "
        f"`!fixtures_generated.json` exception.\n"
        f"If you are regenerating fixtures, run: python3 make_fixtures.py")
with open(_FIXTURE_PATH, encoding="utf-8") as _f:
    FIXTURES = json.load(_f)

LISTING_FIXTURES = ("LISTING_PK_CITY", "LISTING_PK_AREA2", "LISTING_SG_CITY",
                    "LISTING_PK_HOME")
BLOCK_FIXTURES = ("BLOCK_PX_RECAPTCHA", "BLOCK_PX_RECAPTCHA_SG",
                  "BLOCK_PX_PLAIN", "BLOCK_CLOUDFLARE")
INDEX_FIXTURES = ("INDEX_PK_CITY", "INDEX_PK_AREA")


def html_of(name):
    return FIXTURES[name]["html"]


def url_of(name):
    return FIXTURES[name]["url"]


def rows_of(name, page=1):
    return parse_products(html_of(name), url_of(name), page=page)


def by_sku(name, page=1):
    return {r.sku: r for r in rows_of(name, page)}


# ---------------------------------------------------------------------------
def test_numbers_and_currencies():
    group("Numbers and currencies")
    ok = True

    # §10's amazon-scraper bug in this repo's shape: the review count is
    # printed as "(1,234)", and `re.search(r"\d+", ...)` returns 1.
    ok &= check("int_in('(1,234)') == 1234", int_in("(1,234)") == 1234)
    ok &= check("int_in('(57)') == 57", int_in("(57)") == 57)
    ok &= check("int_in('(100+)') == 100", int_in("(100+)") == 100)
    ok &= check("int_in('') is None", int_in("") is None)
    ok &= check("int_in('no digits') is None", int_in("no digits") is None)

    # §4's three grouping conventions, and the no-break space variants a
    # rendered page really uses.
    norm = product_parser._normalize_amount
    ok &= check("1,234.56 -> 1234.56", norm("1,234.56") == 1234.56)
    ok &= check("1.234,56 -> 1234.56", norm("1.234,56") == 1234.56)
    ok &= check("1 234,56 (NBSP) -> 1234.56", norm("1 234,56") == 1234.56)
    ok &= check("1 234,56 (narrow NBSP) -> 1234.56",
                norm("1 234,56") == 1234.56)
    # Exactly three trailing digits is a THOUSANDS grouping: no currency in
    # this brand's range has a three-digit subunit.
    ok &= check("1,234 -> 1234 (not 1.234)", norm("1,234") == 1234.0)
    ok &= check("3.69 -> 3.69 (two decimals stay decimals)", norm("3.69") == 3.69)
    ok &= check("69 -> 69", norm("69") == 69.0)

    # Currency from the SYMBOL the tile prints, longest-first so a prefix is
    # not swallowed by the bare symbol inside it.
    ok &= check("'Rs.69' -> PKR", currency_in("Rs.69") == "PKR")
    ok &= check("'S$3.69' -> SGD", currency_in("S$3.69") == "SGD")
    ok &= check("'S$ 15' -> SGD not USD", currency_in("15% off S$ 15") == "SGD")
    ok &= check("'RM4.50' -> MYR", currency_in("RM4.50") == "MYR")
    ok &= check("'NT$60' -> TWD not USD", currency_in("NT$60") == "TWD")
    ok &= check("'HK$20' -> HKD not USD", currency_in("HK$20") == "HKD")
    # §8: a missing currency is null, never a defaulted one.
    ok &= check("no symbol -> None", currency_in("Free delivery") is None)
    ok &= check("empty -> None", currency_in("") is None)
    return ok


# ---------------------------------------------------------------------------
def test_values_on_real_fixtures():
    group("Values on real fixtures, not coverage")
    ok = True

    pk = by_sku("LISTING_PK_CITY")
    ok &= check("PK city listing parses 6 tiles", len(pk) == 6)
    slap = pk.get("pedb")
    ok &= check("pedb is present", slap is not None)
    if slap:
        ok &= check("pedb title is the vendor's own name",
                    slap.title == "Slap - Girja Chowk")
        ok &= check("pedb rating is 5.0 out of five", slap.rating == 5.0)
        ok &= check("pedb review_count is 57, not 5", slap.review_count == 57)
        ok &= check("pedb review_count is not a floor",
                    slap.review_count_is_floor is False)
        ok &= check("pedb discount is 20% and not an upper bound",
                    slap.discount_pct == 20.0
                    and slap.discount_is_upper_bound is False)
        ok &= check("pedb keeps the label it read the discount from",
                    slap.discount_label == "20% off")
        ok &= check("pedb cuisines come from the site's own row",
                    slap.cuisines == ["Desserts"])
        ok &= check("pedb category is the cuisines joined",
                    slap.category == "Desserts")
        ok &= check("pedb sku matches the code in its URL",
                    slap.sku == sku_from_url(slap.url) == "pedb")
        ok &= check("pedb slug is the URL's second segment",
                    slap.slug == "slap-girja-chowk")
        ok &= check("pedb city comes from the LISTING url", slap.city == "lahore")
        ok &= check("pedb has no price columns at all",
                    not hasattr(slap, "price"))
        ok &= check("pedb carries no currency (a city tile has no money)",
                    slap.currency is None)

    # "Up to N% off" is a CEILING, and 21% of the measured labels are that
    # shape. Reporting it as a discount would be a guess presented as a fact.
    desi = pk.get("m3d7")
    if desi:
        ok &= check("'Up to 10% off' -> 10.0 flagged as an upper bound",
                    desi.discount_pct == 10.0
                    and desi.discount_is_upper_bound is True)

    sg = by_sku("LISTING_SG_CITY")
    ok &= check("SG city listing parses 6 tiles", len(sg) == 6)
    # The case difference that a case-sensitive match got wrong: PK writes
    # "Free Delivery" and SG writes "Free delivery".
    ok &= check("SG free-delivery label is matched case-insensitively",
                all(r.free_delivery for r in sg.values()
                    if any(t.lower() == "free delivery" for t in r.tags)))
    qin = sg.get("mk3o")
    if qin:
        ok &= check("'15% off S$ 30' -> 15% with a 30 SGD minimum spend",
                    qin.discount_pct == 15.0 and qin.min_order == 30.0
                    and qin.min_order_currency == "SGD")
        ok &= check("the minimum spend is NOT read as the discount",
                    qin.discount_pct != 30.0)
    hakka = sg.get("aah1")
    if hakka:
        # "(100+)" is the site's CAP, not a count. Without the flag a monitor
        # would report every busy vendor as frozen at exactly 100 forever.
        ok &= check("'(100+)' is 100 AND flagged as a floor",
                    hakka.review_count == 100
                    and hakka.review_count_is_floor is True)
    ok &= check("a CJK vendor name survives verbatim",
                sg.get("lwow") is not None
                and "珍味小厨" in (sg["lwow"].title or ""))

    # The home page is a different listing kind and publishes three columns
    # the others do not. This is the block that was 100% populated and wrong.
    home = by_sku("LISTING_PK_HOME")
    ok &= check("home listing parses 4 tiles", len(home) == 4)
    suadish = home.get("m05i")
    if suadish:
        ok &= check("home cuisines are the CUISINES row, not the time row",
                    suadish.cuisines == ["Biryani"]
                    and suadish.category == "Biryani")
        ok &= check("home delivery_time is kept verbatim",
                    suadish.delivery_time == "From 25 min")
        ok &= check("home delivery_fee is 69.0 PKR",
                    suadish.delivery_fee == 69.0 and suadish.currency == "PKR")
        ok &= check("home price_level is counted from the site's own symbols",
                    suadish.price_level == 1)
        ok &= check("home page_kind says which listing this came off",
                    suadish.page_kind == "home")
        ok &= check("'(500+)' is 500 AND flagged as a floor",
                    suadish.review_count == 500
                    and suadish.review_count_is_floor is True)
        ok &= check("the super-vendor badge is read", suadish.is_super_vendor is True)
        ok &= check("an unclaimed info row is kept verbatim",
                    "Free for first order" in suadish.info_notes)

    # page_kind must differ between the three listing kinds, because three
    # columns are populated on exactly one of them.
    ok &= check("city rows say page_kind='city'",
                all(r.page_kind == "city" for r in pk.values()))
    ok &= check("area rows say page_kind='area'",
                all(r.page_kind == "area"
                    for r in by_sku("LISTING_PK_AREA2").values()))
    ok &= check("a city tile has no delivery data at all",
                all(r.delivery_fee is None and r.delivery_time is None
                    and r.price_level is None for r in pk.values()))

    # Every fixture, every row: the columns the site publishes on all of them.
    for name in LISTING_FIXTURES:
        rows = rows_of(name)
        ok &= check(f"{name}: every row has a sku",
                    all(r.sku for r in rows))
        ok &= check(f"{name}: every row has a title",
                    all(r.title for r in rows))
        ok &= check(f"{name}: every image_url is on a foodpanda media host",
                    all(r.image_url and
                        ("deliveryhero.io" in r.image_url
                         or "dhmedia.io" in r.image_url) for r in rows))
        ok &= check(f"{name}: every rating is within 0-5",
                    all(r.rating is None or 0.0 <= r.rating <= 5.0
                        for r in rows))
        ok &= check(f"{name}: source is the country host",
                    all(r.source == site_host(url_of(name)) for r in rows))
        ok &= check(f"{name}: no overflow chip ('+1') reached the tags",
                    all(not re.match(r"^\+\s*\d+$", t)
                        for r in rows for t in r.tags))
    return ok


# ---------------------------------------------------------------------------
def test_urls_and_hosts():
    group("URLs, hosts and what is refused")
    ok = True

    ok &= check("eleven country sites are supported", len(HOSTS) == 11)
    ok &= check("every supported host has a country name",
                set(COUNTRY_BY_HOST) == set(HOSTS))
    ok &= check("www. and bare hosts both resolve",
                site_host("https://www.foodpanda.pk/city/lahore") == "foodpanda.pk"
                and site_host("https://foodpanda.pk/city/lahore") == "foodpanda.pk")
    ok &= check("an unrelated host is not supported",
                not is_supported_host("https://www.deliveroo.co.uk/"))

    # §5: a host that IS the brand's and is still refused must be refused
    # WITH THE REASON — "is not a foodpanda site" would be false and would
    # send the reader hunting for a typo.
    why = unsupported_reason("https://www.foodpanda.com/")
    ok &= check("foodpanda.com is refused", why is not None)
    ok &= check("...and NOT as 'not a foodpanda site'",
                why is not None and "not a foodpanda" not in why)
    ok &= check("...naming what it actually is",
                why is not None and "landing" in why.lower())
    ok &= check("foodpanda.com is in REFUSED_HOSTS with a reason",
                all(isinstance(v, str) and len(v) > 40
                    for v in REFUSED_HOSTS.values()))

    vendor = "https://www.foodpanda.pk/restaurant/pedb/slap-girja-chowk"
    ok &= check("a vendor page is refused", unsupported_reason(vendor) is not None)
    ok &= check("...with the measurement, not a shrug",
                "403" in (unsupported_reason(vendor) or ""))
    ok &= check("a vendor URL is recognised as 'vendor'",
                listing_kind(vendor) == "vendor")
    ok &= check("its code and slug are recoverable from the URL",
                sku_from_url(vendor) == "pedb"
                and slug_from_url(vendor) == "slap-girja-chowk")

    kinds = {
        "https://www.foodpanda.pk/": "listing",
        "https://www.foodpanda.pk/city/lahore": "listing",
        "https://www.foodpanda.pk/city/lahore/area/gulberg": "listing",
        "https://www.foodpanda.pk/city": "index",
        "https://www.foodpanda.pk/city/lahore/area": "index",
        "https://www.foodpanda.pk/restaurant/pedb/slap": "vendor",
        "https://www.foodpanda.pk/contents/privacy.htm": "unknown",
    }
    for url, want in kinds.items():
        ok &= check(f"listing_kind({url.split('.pk')[1] or '/'!r}) == {want!r}",
                    listing_kind(url) == want)

    page_kinds = {
        "https://www.foodpanda.pk/": "home",
        "https://www.foodpanda.sg/city/singapore": "city",
        "https://www.foodpanda.pk/city/lahore/area/gulberg": "area",
        "https://www.foodpanda.pk/city": None,
    }
    for url, want in page_kinds.items():
        ok &= check(f"listing_page_kind -> {want!r}",
                    listing_page_kind(url) == want)

    ok &= check("city and area come off the listing URL",
                city_area_from_url(
                    "https://www.foodpanda.pk/city/lahore/area/gulberg")
                == ("lahore", "gulberg"))
    ok &= check("a city listing has no area",
                city_area_from_url("https://www.foodpanda.pk/city/lahore")
                == ("lahore", None))
    ok &= check("the home page has neither",
                city_area_from_url("https://www.foodpanda.pk/") == (None, None))

    ok &= check("tracking parameters are stripped",
                strip_tracking("https://www.foodpanda.pk/city/lahore?utm_source=x")
                == "https://www.foodpanda.pk/city/lahore")
    ok &= check("...but ?page= is NOT (it identifies the page)",
                "page=2" in strip_tracking(
                    "https://www.foodpanda.pk/city/lahore/area/g?page=2&fbclid=1"))
    ok &= check("source_of falls back to the default, not to a crash",
                source_of("https://example.com/") == SOURCE_DEFAULT)
    return ok


# ---------------------------------------------------------------------------
def test_pagination():
    group("Pagination — an address the site publishes itself")
    ok = True
    area = "https://www.foodpanda.pk/city/lahore/area/gulberg"
    city = "https://www.foodpanda.sg/city/singapore"
    home = "https://www.foodpanda.pk/"

    ok &= check("this site paginates by URL", PAGINATES_BY_URL is True)
    ok &= check("an area listing paginates by URL", paginates_by_url(area))
    ok &= check("a city listing paginates by URL", paginates_by_url(city))
    # §7 in its sharpest form: the home page has no pagination of any kind,
    # so asking for its page 2 would refetch page 1, add no new sku, and be
    # reported as an exhausted listing.
    ok &= check("the HOME page does NOT paginate by URL",
                not paginates_by_url(home))
    ok &= check("...so it offers no next-page candidates",
                page_flow.next_page_candidates(home) == [])
    ok &= check("...and refuses --concurrency with that reason",
                (page_flow.concurrency_refusal(home) or "").find("home page") >= 0)
    ok &= check("an area listing allows concurrency",
                page_flow.concurrency_refusal(area) is None)
    ok &= check("a vendor URL refuses concurrency",
                page_flow.concurrency_refusal(
                    "https://www.foodpanda.pk/restaurant/pedb/x") is not None)
    ok &= check("an index URL refuses concurrency",
                page_flow.concurrency_refusal(
                    "https://www.foodpanda.pk/city") is not None)

    ok &= check("page 2 is ?page=2", page_url(area, 2) == area + "?page=2")
    # Page 1 is the BARE url: a `?page=1` row would otherwise dedupe against a
    # bare-URL row as two different pages.
    ok &= check("page 1 is the bare URL", page_url(area, 1) == area)
    ok &= check("an existing page parameter is REPLACED, not duplicated",
                page_url(area + "?page=7", 3) == area + "?page=3")
    ok &= check("other parameters survive",
                page_url(area + "?vertical=restaurants", 2)
                == area + "?vertical=restaurants&page=2")
    ok &= check("page_of_url reads it back", page_of_url(area + "?page=9") == 9)
    ok &= check("page_of_url defaults to 1", page_of_url(area) == 1)
    ok &= check("a non-numeric page parameter does not crash",
                page_of_url(area + "?page=abc") == 1)

    # §7's "verify first": the convention is checked against the site's own
    # advertised link where there is one.
    ok &= check("the convention agrees with the site's own page-2 link",
                page_flow.pagination_is_addressable(area, [area + "?page=2"]))
    ok &= check("a cursor the convention cannot build answers False",
                not page_flow.pagination_is_addressable(
                    area, [area + "?cursor=eyJhIjoxfQ"]))
    ok &= check("no advertised link falls back to the convention",
                page_flow.pagination_is_addressable(area, []))
    ok &= check("a link to a DIFFERENT listing is not followed",
                page_flow.next_page_candidates(
                    area, ["https://www.foodpanda.pk/city/lahore/area/other?page=2"])
                == [area + "?page=2"])

    ok &= check("PAGE_CAP is a real stop", PAGE_CAP > 0
                and page_flow.page_cap_reached(PAGE_CAP))
    ok &= check("link[rel=next] leads the selector list (§5)",
                NEXT_PAGE_SELECTOR.strip().startswith("link[rel='next']"))

    # The site's own page separators, from the scrolled capture: a document
    # holding many pages must attribute each tile to the page it came from.
    rows = rows_of("SCROLLED_PK_AREA")
    pages = sorted({r.page for r in rows})
    ok &= check("the scrolled fixture spans more than one page", len(pages) > 1)
    ok &= check("its pages are the ids the site printed", pages == [1, 2, 3, 4])
    pairs = [(r.page, r.position) for r in rows]
    # §18's arithmetic bug: `position` restarts at 1 on every page, so without
    # `page` threaded through, rows silently share a place in the listing.
    ok &= check("(page, position) is unique across a multi-page document",
                len(set(pairs)) == len(pairs))
    ok &= check("position restarts at 1 on each page",
                all(1 in [p for pg, p in pairs if pg == page] for page in pages))
    ok &= check("every sku in the scrolled fixture is distinct",
                len({r.sku for r in rows}) == len(rows))

    # A single-page fetch has no separators and must use the page it was told.
    ok &= check("a cold page-2 fetch labels every row page 2",
                all(r.page == 2 for r in rows_of("LISTING_PK_AREA2", page=2)))
    return ok


# ---------------------------------------------------------------------------
def test_page_state():
    group("Page state — ordered by what each signal proves")
    ok = True

    for name in LISTING_FIXTURES:
        ok &= check(f"{name} is content",
                    detect_page_state(html_of(name), 200, url_of(name)) == "content")
        ok &= check(f"{name} is served by foodpanda",
                    served_by_foodpanda(html_of(name)))
        ok &= check(f"{name} carries no block marker",
                    detect_block_marker(html_of(name)) is None)

    for name in BLOCK_FIXTURES:
        ok &= check(f"{name} is blocked",
                    detect_page_state(html_of(name), 403, url_of(name)) == "blocked")
        ok &= check(f"{name} is NOT served by foodpanda",
                    not served_by_foodpanda(html_of(name)))
        ok &= check(f"{name} names its vendor",
                    detect_block_marker(html_of(name)) in ("PerimeterX",
                                                           "Cloudflare"))
    ok &= check("PerimeterX and Cloudflare are told apart",
                detect_block_marker(html_of("BLOCK_PX_PLAIN")) == "PerimeterX"
                and detect_block_marker(html_of("BLOCK_CLOUDFLARE")) == "Cloudflare")

    # §17's classification-order trap: a page the site plainly served, with
    # no tiles on it, must not come back `blocked` just because it is lean.
    for name in INDEX_FIXTURES:
        state = detect_page_state(html_of(name), 200, url_of(name))
        ok &= check(f"{name} is not reported as blocked", state != "blocked")
        ok &= check(f"{name} is recognised as served", served_by_foodpanda(html_of(name)))

    # An unambiguous positive beats a status code: a 403 on a document that
    # holds the grid is the site's CDN being odd, not a refusal.
    ok &= check("tiles in the document win over a 403 status",
                detect_page_state(html_of("LISTING_PK_CITY"), 403,
                                  url_of("LISTING_PK_CITY")) == "content")
    ok &= check("no html at all is blocked", detect_page_state(None) == "blocked")
    ok &= check("an empty string with a 403 is blocked",
                detect_page_state("", 403) == "blocked")

    # The site's own 404 furniture. NO_RESULTS_MARKERS is empty on purpose —
    # §18: a marker set written from imagination matches nothing.
    ok &= check("NO_RESULTS_MARKERS is empty, not invented",
                NO_RESULTS_MARKERS == ())
    ok &= check("the site's 404 title is recognised as empty",
                is_no_results("<html><head><title>404 Ooops!</title></head></html>"))
    ok &= check("a served listing is NOT 'no results'",
                not is_no_results(html_of("LISTING_PK_CITY")))
    return ok


# ---------------------------------------------------------------------------
def test_markers_that_match_every_page():
    group("§18 — a marker that matches every page is worse than no marker")
    ok = True
    # Both of these are on EVERY page foodpanda serves. Either one used as a
    # marker would report the whole catalogue as blocked.
    for probe in ("_pxAppId", "PERIMETERX_APP_ID", "reCAPTCHA"):
        on_good = sum(1 for n in LISTING_FIXTURES if probe in html_of(n))
        ok &= check(f"{probe!r} is on every served fixture "
                    f"({on_good}/{len(LISTING_FIXTURES)})",
                    on_good == len(LISTING_FIXTURES))
        ok &= check(f"...and is NOT used as a block marker",
                    not any(probe.lower() == m.lower() for m in BLOCK_MARKERS))
        ok &= check(f"...and is NOT used as a challenge marker",
                    not any(probe.lower() in m.lower()
                            for m in BOT_CHALLENGE_MARKERS))

    # Every marker this repo DOES use must score zero on every served page.
    for marker in BLOCK_MARKERS + CLOUDFLARE_MARKERS:
        hits = sum(1 for n in LISTING_FIXTURES if marker in html_of(n))
        ok &= check(f"block marker {marker!r} is absent from every served page",
                    hits == 0)
    for marker in BLOCK_MARKERS:
        hits = sum(1 for n in ("BLOCK_PX_PLAIN", "BLOCK_PX_RECAPTCHA")
                   if marker in html_of(n))
        ok &= check(f"...and present on a real refusal ({marker!r})", hits > 0)
    return ok


# ---------------------------------------------------------------------------
def test_the_challenge_is_not_solvable():
    group("The challenge is reCAPTCHA Enterprise, and unsolvable here")
    ok = True
    # The bug this pins: the denial page's container looks like a v2 checkbox
    # and the LOADER is enterprise.js. Reading the container alone called it a
    # solvable `challenge` and would have paid for a token the site rejects.
    for name in ("BLOCK_PX_RECAPTCHA", "BLOCK_PX_RECAPTCHA_SG"):
        html = html_of(name)
        ok &= check(f"{name} carries the v2-shaped container",
                    'class="g-recaptcha"' in html)
        ok &= check(f"{name} loads recaptcha/enterprise.js",
                    "recaptcha/enterprise" in html)
        ok &= check(f"{name} does NOT load the ordinary api.js",
                    "recaptcha/api.js" not in html)
        ok &= check(f"{name} is reported as UNSOLVABLE",
                    detect_bot_challenge(html) is None)
        ok &= check(f"{name} names what it is",
                    unsolvable_challenge(html) == "reCAPTCHA Enterprise")
        ok &= check(f"{name} is therefore 'blocked', so nothing is billed",
                    detect_page_state(html, 403, url_of(name)) == "blocked")

    ok &= check("the solvable-marker set is empty, and that is the measurement",
                BOT_CHALLENGE_MARKERS == ())
    ok &= check("enterprise leads the unsolvable set",
                UNSOLVABLE_CHALLENGE_MARKERS[0] == "recaptcha/enterprise")
    # The set that documents what WOULD be solvable is deliberately not
    # consulted — a marker set that is checked but can never match is dead
    # code wearing a policy's clothes (§17).
    ok &= check("WOULD_BE_SOLVABLE_MARKERS names the ordinary loaders",
                "recaptcha/api.js" in WOULD_BE_SOLVABLE_MARKERS)
    ok &= check("...and is not wired into detect_bot_challenge",
                "WOULD_BE_SOLVABLE_MARKERS" not in inspect.getsource(
                    detect_bot_challenge))
    ok &= check("a served page is never reported as a challenge",
                all(detect_bot_challenge(html_of(n)) is None
                    for n in LISTING_FIXTURES))
    return ok


# ---------------------------------------------------------------------------
def test_page_flow_policy():
    group("page_flow — the policy as data")
    ok = True
    states = set(page_flow.STATE_POLICY)
    ok &= check("five states, and no more",
                states == {"content", "empty", "shell", "challenge", "blocked"})
    for state, policy in page_flow.STATE_POLICY.items():
        ok &= check(f"{state}: policy names all four decisions",
                    set(policy) == {"parse", "retry", "solve", "blocked"})
    ok &= check("content is parsed and not retried",
                page_flow.should_parse("content")
                and not page_flow.should_retry("content"))
    ok &= check("empty is neither parsed nor retried",
                not page_flow.should_parse("empty")
                and not page_flow.should_retry("empty"))
    ok &= check("empty does NOT count as blocked",
                not page_flow.counts_as_blocked("empty"))
    ok &= check("shell is parsed, not refetched (refetching a shell buys a shell)",
                page_flow.should_parse("shell")
                and not page_flow.should_retry("shell"))
    ok &= check("blocked is retried and counts as blocked",
                page_flow.should_retry("blocked")
                and page_flow.counts_as_blocked("blocked"))
    ok &= check("blocked never buys a solve",
                not page_flow.should_solve("blocked"))
    ok &= check("an unknown state defaults to the blocked policy",
                page_flow.should_retry("something-new")
                and page_flow.counts_as_blocked("something-new"))

    # §17: a policy constant nothing consults is the same defect as dead code.
    engine_sources = {}
    for name in ENGINES:
        path = os.path.join(REPO_ROOT, f"{name}.py")
        engine_sources[name] = open(path, encoding="utf-8").read()
    for constant in ("RETRY_ON_BLOCKED", "BLOCK_RETRIES_WITHOUT_POOL",
                     "SOLVES_PER_PAGE"):
        consumers = [n for n, s in engine_sources.items() if constant in s]
        ok &= check(f"{constant} is read by all three engines",
                    len(consumers) == 3)
    ok &= check("the block-retry budget is non-zero (a fresh session clears it)",
                page_flow.RETRY_ON_BLOCKED
                and page_flow.BLOCK_RETRIES_WITHOUT_POOL > 0)
    ok &= check("a pool buys more attempts than no pool",
                page_flow.BLOCK_RETRIES_WITH_POOL
                > page_flow.BLOCK_RETRIES_WITHOUT_POOL)

    # The advice must name the vendor, because the two refusals want
    # opposite first moves.
    cf = page_flow.block_advice(html_of("BLOCK_CLOUDFLARE"), headless=True,
                               has_pool=False)
    px = page_flow.block_advice(html_of("BLOCK_PX_PLAIN"), headless=True,
                                has_pool=False)
    ok &= check("Cloudflare advice names Cloudflare", "Cloudflare" in cf)
    ok &= check("PerimeterX advice names PerimeterX", "PerimeterX" in px)
    ok &= check("the two differ", cf != px)
    ok &= check("PerimeterX advice mentions --retries", "--retries" in px)
    ok &= check("headless advice suggests --headful", "--headful" in px)

    ok &= check("only the home page is scrolled",
                page_flow.should_scroll("https://www.foodpanda.pk/")
                and not page_flow.should_scroll(
                    "https://www.foodpanda.pk/city/lahore/area/gulberg"))
    ok &= check("there is no counter on this site to do arithmetic with",
                page_flow.expected_cards(html_of("LISTING_PK_CITY")) is None
                and page_flow.page_gap(html_of("LISTING_PK_CITY"), 48) is None
                and total_results(html_of("LISTING_PK_CITY")) is None
                and results_range(html_of("LISTING_PK_CITY")).expected_on_page
                is None)
    ok &= check("a thin page is a warning, not a completeness test",
                page_flow.is_thin_page(1)
                and not page_flow.is_thin_page(TYPICAL_PAGE_SIZE)
                and not page_flow.is_thin_page(TYPICAL_PAGE_SIZE - 4))
    ok &= check("the thin floor sits well under a full page",
                THIN_PAGE_FLOOR < TYPICAL_PAGE_SIZE)
    return ok


# ---------------------------------------------------------------------------
def test_scroll_loop():
    group("The scroll loop, with the browser stubbed out")
    ok = True

    # Grows for two rounds, then holds still. A loop that stopped at the first
    # unchanged round would stop at 20 (§8: three stable rounds, not one).
    counts = iter([10, 20, 48, 48, 48, 48, 48, 48])
    heights = iter([1000, 2000, 5000, 5000, 5000, 5000, 5000, 5000])
    scrolls = []
    result = page_flow.scroll_until_settled(
        count=lambda sel: next(counts, 48),
        page_height=lambda: next(heights, 5000),
        scroll_to_bottom=lambda: scrolls.append(1),
        sleep=lambda ms: None)
    ok &= check("the scroll settles", result["settled"] is True)
    ok &= check("it reaches the full count", result["cards"] == 48)
    ok &= check("it scrolled more than once", len(scrolls) >= 2)

    # Still growing when the budget runs out: that page is PARTIAL and must
    # say so, or the missing tail reads as delisted vendors.
    growing = iter(range(10, 500, 10))
    tall = iter(range(1000, 50000, 1000))
    result = page_flow.scroll_until_settled(
        count=lambda sel: next(growing, 500),
        page_height=lambda: next(tall, 50000),
        scroll_to_bottom=lambda: None,
        sleep=lambda ms: None, rounds=5)
    ok &= check("a page still growing is reported as unsettled",
                result["settled"] is False)
    ok &= check("...with the rounds it managed", result["rounds"] == 5)

    # The count alone is not enough: a batch can be in flight with the count
    # unchanged and the height already moving.
    steady_count = iter([30, 30, 30, 30, 30, 30, 30, 30])
    moving_height = iter([1000, 2000, 3000, 4000, 5000, 6000, 7000, 8000])
    result = page_flow.scroll_until_settled(
        count=lambda sel: next(steady_count, 30),
        page_height=lambda: next(moving_height, 8000),
        scroll_to_bottom=lambda: None, sleep=lambda ms: None, rounds=6)
    ok &= check("a steady count with a growing height is NOT settled",
                result["settled"] is False)

    # The readiness poll: returns the count it reached, and a driver fault is
    # reported rather than raised.
    seen = iter([0, 1, 2, 5])
    ok &= check("wait_for_count returns once the threshold is passed",
                page_flow.wait_for_count(lambda sel: next(seen, 5),
                                         lambda ms: None, "sel", 2, 5000) == 5)
    ok &= check("a timeout is not an error, it is a count",
                page_flow.wait_for_count(lambda sel: 0, lambda ms: None,
                                         "sel", 2, 20, poll_ms=10) == 0)

    def boom(sel):
        raise RuntimeError("driver went away")

    ok &= check("a driver fault ends the wait instead of the run",
                page_flow.wait_for_count(boom, lambda ms: None, "s", 2, 100) == 0)
    return ok


# ---------------------------------------------------------------------------
def test_a_refused_page_is_not_an_empty_one():
    group("A refused page is not an exhausted listing (the live-run bug)")
    ok = True
    # `no_new_products` is a COMPLETE stop reason, which is correct for a
    # page the site served with nothing new on it — and catastrophic for a
    # page that was never served at all. The engines now mark any state that
    # must not be parsed (other than `empty`) as a FAILED page.
    ok &= check("'no_new_products' is a COMPLETE stop reason",
                "no_new_products" in COMPLETE_STOP_REASONS)
    ok &= check("blocked must NOT be parsed",
                not page_flow.should_parse("blocked"))
    ok &= check("challenge must NOT be parsed",
                not page_flow.should_parse("challenge"))
    ok &= check("empty IS allowed to end a listing",
                not page_flow.counts_as_blocked("empty"))

    guard = ('if state != "empty" and not page_flow.should_parse(state):')
    for name in ENGINES:
        source = open(os.path.join(REPO_ROOT, f"{name}.py"),
                      encoding="utf-8").read()
        ok &= check(f"{name} guards the parse against an unread page",
                    guard in source)
        ok &= check(f"{name} marks such a page blocked_by",
                    source.count("outcome.blocked_by") >= 2)

    # And the end-to-end shape: a partial run must not claim to be complete.
    with tempfile.TemporaryDirectory() as tmp:
        prefix = os.path.join(tmp, "run")
        rows = rows_of("LISTING_PK_CITY")
        code = finish_run(rows, prefix, "json", allow_empty=False,
                          blocked=True, stop_reason="blocked_PerimeterX",
                          pages_requested=3, pages_completed=1,
                          pages_failed=[2], mode="listing",
                          source="foodpanda.pk",
                          start_url=url_of("LISTING_PK_CITY"),
                          final_url=url_of("LISTING_PK_CITY"))
        meta = json.load(open(prefix + ".meta.json", encoding="utf-8"))
        ok &= check("a run with a refused page exits PARTIAL",
                    code == EXIT_PARTIAL)
        ok &= check("...with status 'partial', not 'complete'",
                    meta["status"] == "partial")
        ok &= check("...naming WHICH page failed, by number",
                    meta["pages_failed"] == [2])
        ok &= check("...and keeping the rows it did get",
                    meta["products"] == len(rows))
    return ok


# ---------------------------------------------------------------------------
def test_output_contract():
    group("The output contract")
    ok = True
    names = [f.name for f in fields(Product)]

    # §9: the family prefix, in order. Columns this site does not have are
    # ABSENT rather than null-on-every-row, and each absence is a measurement
    # written down in output_writer's docstring.
    family_present = ["source", "scraped_at", "url", "sku", "title", "currency",
                      "discount_pct", "rating", "review_count", "image_url",
                      "category", "page", "position"]
    ok &= check("the family prefix is present and in order",
                names[:len(family_present)] == family_present)
    for absent in ("price", "original_price", "price_source", "brand", "in_stock"):
        ok &= check(f"{absent!r} is absent, and the reason is written down",
                    absent not in names)
    ok &= check("the docstring explains every absence",
                all(word in (__import__("output_writer").__doc__ or "")
                    for word in ("price", "brand", "in_stock")))

    ok &= check("one mode, and it is listing",
                set(ROW_CLASS_BY_MODE) == {"listing"}
                and UNIQUE_BY_SKU_MODES == ("listing",))
    ok &= check("exit codes are the family's",
                (EXIT_BLOCKED, EXIT_NO_PRODUCTS, EXIT_PARTIAL) == (3, 4, 6))

    # Dedupe is by sku and preserves the first occurrence's order.
    seen = set()
    rows = rows_of("LISTING_PK_CITY")
    first = dedupe_by_key(rows, seen)
    second = dedupe_by_key(rows, seen)
    ok &= check("dedupe keeps every row the first time", len(first) == len(rows))
    ok &= check("...and none the second", second == [])

    # An empty CSV still carries its header, so a consumer reads a table with
    # no rows instead of failing on a zero-byte file.
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "empty.csv")
        write_csv([], path)
        with open(path, encoding="utf-8") as f:
            header = next(csv_module.reader(f))
        ok &= check("an empty CSV still has the full header",
                    header == names)

        # Lists join with the family separator and JSON keeps the structure.
        path = os.path.join(tmp, "rows.csv")
        write_csv(rows, path)
        with open(path, encoding="utf-8") as f:
            table = list(csv_module.DictReader(f))
        tagged = next((r for r in table if r["tags"]), None)
        ok &= check("CSV joins list columns with the family separator",
                    tagged is not None
                    and (LIST_CSV_SEPARATOR in tagged["tags"]
                         or "," not in tagged["tags"]))
        ok &= check("CSV column order matches the dataclass",
                    list(table[0]) == names)
    return ok


# ---------------------------------------------------------------------------
def test_writers_and_finish_run():
    group("Writers, exit codes and the sidecar")
    ok = True
    rows = rows_of("LISTING_PK_CITY")
    with tempfile.TemporaryDirectory() as tmp:
        prefix = os.path.join(tmp, "run")

        # A run that finds nothing writes NOTHING: never replace last night's
        # good output with [].
        save(rows, prefix, "json", allow_empty=False)
        before = open(prefix + ".json", encoding="utf-8").read()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            save([], prefix, "json", allow_empty=False)
        after = open(prefix + ".json", encoding="utf-8").read()
        ok &= check("an empty result does not overwrite good output",
                    before == after)
        ok &= check("...and says so", "0 product" in buf.getvalue()
                    or "allow-empty" in buf.getvalue())

        with contextlib.redirect_stdout(io.StringIO()):
            save([], prefix, "json", allow_empty=True)
        ok &= check("--allow-empty is the opt-out",
                    json.load(open(prefix + ".json", encoding="utf-8")) == [])

        # A FAILED run writes no sidecar: a "failed" sidecar beside good data
        # would contradict it.
        prefix2 = os.path.join(tmp, "failed")
        with contextlib.redirect_stdout(io.StringIO()):
            code = finish_run([], prefix2, "json", allow_empty=False,
                              blocked=True, stop_reason="blocked_PerimeterX",
                              pages_requested=1, pages_completed=0,
                              pages_failed=[1], mode="listing",
                              source="foodpanda.pk",
                              start_url="https://www.foodpanda.pk/city/lahore",
                              final_url="https://www.foodpanda.pk/city/lahore")
        ok &= check("a blocked run with no rows exits 3", code == EXIT_BLOCKED)
        ok &= check("...and writes no sidecar",
                    not os.path.exists(prefix2 + ".meta.json"))

        prefix3 = os.path.join(tmp, "empty_ok")
        with contextlib.redirect_stdout(io.StringIO()):
            code = finish_run([], prefix3, "json", allow_empty=False,
                              blocked=False, stop_reason="completed",
                              pages_requested=1, pages_completed=1,
                              pages_failed=[], mode="listing",
                              source="foodpanda.pk",
                              start_url="https://www.foodpanda.pk/city/lahore",
                              final_url="https://www.foodpanda.pk/city/lahore")
        ok &= check("a genuinely empty listing exits 4, not 3",
                    code == EXIT_NO_PRODUCTS)

    meta = run_meta("complete", "completed", 3, 3,
                    "https://www.foodpanda.pk/city/lahore",
                    "https://www.foodpanda.pk/city/lahore?page=3", 144,
                    pages_failed=[], mode="listing", source="foodpanda.pk")
    ok &= check("the sidecar records which pages failed, by number",
                meta["pages_failed"] == [])
    ok &= check("...the mode", meta["mode"] == "listing")
    ok &= check("...and the country site", meta["source"] == "foodpanda.pk")
    return ok


# ---------------------------------------------------------------------------
def test_diff():
    group("diff_runs — the clock is not the catalogue")
    ok = True
    ok &= check("no price field is tracked (this site has none)",
                not any(f in TRACKED_FIELDS for f in
                        ("price", "original_price", "price_source")))
    ok &= check("the review-count FLOOR is tracked beside the count",
                "review_count" in TRACKED_FIELDS
                and "review_count_is_floor" in TRACKED_FIELDS)
    ok &= check("the opening pair has its own bucket",
                HOURS_FIELDS == ("is_open", "opens_at"))

    base = [asdict(r) for r in rows_of("LISTING_PK_CITY")]

    # Only the hours moved: that is the clock, and it must not read as a
    # change to the catalogue.
    later = [dict(r) for r in base]
    for row in later[:3]:
        row["is_open"] = False
        row["opens_at"] = "Sat 10:20"
    result = diff_products(base, later)
    ok &= check("an open/closed flip lands in hours_changed",
                len(result["hours_changed"]) == 3)
    ok &= check("...and NOT in changed", result["changed"] == [])
    ok &= check("...with nothing added or removed",
                result["added"] == [] and result["removed"] == [])

    # A rating move alongside the hours keeps its own bucket.
    later2 = [dict(r) for r in base]
    later2[0]["is_open"] = False
    later2[0]["rating"] = 4.1
    result = diff_products(base, later2)
    ok &= check("a real change alongside the clock stays in changed",
                len(result["changed"]) == 1)
    ok &= check("...and carries the rating, not the hours",
                "rating" in result["changed"][0]["changes"])

    # Added and removed.
    result = diff_products(base[:-1], base)
    ok &= check("a new vendor is 'added'", len(result["added"]) == 1)
    result = diff_products(base, base[:-1])
    ok &= check("a vanished vendor is 'removed'", len(result["removed"]) == 1)

    # The family's shape is kept even where a bucket can never fill.
    result = diff_products(base, base)
    for bucket in ("added", "removed", "changed", "hours_changed",
                   "source_changed", "within_tolerance", "lifecycle"):
        ok &= check(f"the diff always emits {bucket!r}", bucket in result)
    ok &= check("an identical pair changes nothing",
                all(result[b] == [] for b in ("added", "removed", "changed",
                                              "hours_changed")))
    return ok


# ---------------------------------------------------------------------------
def test_env_config():
    group("env_config — a copied example must read as unset")
    ok = True
    ok &= check("the keys are this site's",
                set(env_config.ENV_KEYS) == {"TWOCAPTCHA_KEY",
                                             "FOODPANDA_CDP_ENDPOINT",
                                             "FOODPANDA_PROXY",
                                             "FOODPANDA_URL"})
    # §3: .env.example must document exactly the variables the code reads,
    # in both directions.
    documented = set()
    example = open(os.path.join(REPO_ROOT, ".env.example"), encoding="utf-8")
    for line in example:
        match = re.match(r"^([A-Z][A-Z0-9_]*)=", line.strip())
        if match:
            documented.add(match.group(1))
    example.close()
    ok &= check("every documented variable is read", documented <= set(env_config.ENV_KEYS))
    ok &= check("every read variable is documented", set(env_config.ENV_KEYS) <= documented)

    # §17: a copied .env.example must round-trip through the real loader as
    # UNSET for every credential. The braced placeholders are the case a
    # literal-only check missed, and it produced a 401 a long way from its
    # cause.
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, ".env")
        with open(os.path.join(REPO_ROOT, ".env.example"), encoding="utf-8") as f:
            open(path, "w", encoding="utf-8").write(f.read())
        # `load_env` writes into os.environ, so the environment is saved and
        # restored around it: a test that leaves TWOCAPTCHA_KEY set is a test
        # that changes what the next one measures.
        saved = {k: os.environ.get(k) for k in env_config.ENV_KEYS}
        for key in env_config.ENV_KEYS:
            os.environ.pop(key, None)
        try:
            env_config.load_env(path, override=True)
            for key in ("TWOCAPTCHA_KEY", "FOODPANDA_CDP_ENDPOINT",
                        "FOODPANDA_PROXY"):
                ok &= check(f"a copied {key} reads as unset",
                            not env_config.env_value(key))
            # The non-credential default is still usable, so the example is
            # not simply inert.
            url = env_config.env_value("FOODPANDA_URL") or ""
            ok &= check("the example's URL is usable as-is",
                        is_supported_host(url))
            ok &= check("...and it is a listing this repo supports",
                        unsupported_reason(url) is None)
            ok &= check("an unknown key in .env is reported, not ignored",
                        env_config.unknown_keys(path) == [])
            # And the braced placeholders really are what makes them unset —
            # a literal-only check reported both credentialled URLs as
            # CONFIGURED (§17).
            ok &= check("a braced value is treated as a placeholder",
                        not env_config.env_value("FOODPANDA_PROXY"))
        finally:
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
    return ok


# ---------------------------------------------------------------------------
def test_proxy_pool_and_credentials():
    group("Credentials never reach a log")
    ok = True
    # Built by CONCATENATION on purpose, so no line of this file holds a
    # complete `scheme://user:pass@host` literal. That keeps
    # .github/ci_checks.py's credential scan fully LIVE on this file — the one
    # place a real credential is most likely to be pasted while debugging —
    # instead of switching it off here with an allowlist entry (§17).
    _user, _secret = "user123", "sekrit" + "password"
    url = "http://" + _user + ":" + _secret + "@ap.proxy.2captcha.com:2334"
    masked = mask(url)
    ok &= check("the password is masked", _secret not in masked)
    ok &= check("the username is masked", _user not in masked)
    # §8: keep host and port — which exit a run used is the point of the log
    # and is not the secret.
    ok &= check("the host survives", "ap.proxy.2captcha.com" in masked)
    ok &= check("the port survives", "2334" in masked)
    ok &= check("None is handled", isinstance(mask(None), str))

    scrubbed, credentials = split_credentials(url)
    ok &= check("split_credentials strips the credentials from the address",
                _secret not in scrubbed and _user not in scrubbed)
    ok &= check("...and returns them separately",
                credentials == (_user, _secret))
    playwright_proxy = to_playwright(url)
    ok &= check("Playwright gets them as fields, never in the server string",
                _secret not in playwright_proxy["server"]
                and playwright_proxy["password"] == _secret)

    second = "http://" + "a" + ":" + "b" + "@eu.proxy.2captcha.com:2334"
    pool = ProxyPool([url, second])
    ok &= check("a pool of two has two exits", len(pool) == 2)
    ok &= check("the pool's repr leaks nothing",
                _secret not in repr(pool))
    before = pool.current
    pool.advance("blocked on page 2")
    ok &= check("advance moves to another exit", pool.current != before)

    # §8's hardest lesson: an EXCEPTION MESSAGE is a log. Nothing in this repo
    # may put a key in a URL's query string.
    for name in ENGINES + ("captcha_solver", "fingerprint_client",
                           "scraper_api_client", "proxy_pool", "env_config"):
        source = open(os.path.join(REPO_ROOT, f"{name}.py"),
                      encoding="utf-8").read()
        for line_no, line in enumerate(source.splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            bad = re.search(r"[?&](?:client)?key=|[?&]token=|[?&]api[_-]?key=",
                            line)
            if bad and "redact" not in line.lower() and "mask" not in line.lower():
                ok &= check(f"{name}.py:{line_no} puts a key in a query string",
                            False)
    ok &= check("no engine puts a credential in a query string", True)
    return ok


# ---------------------------------------------------------------------------
def test_engine_parity(skips):
    group("The three engines must agree")
    ok = True
    modules = {}
    for name in ENGINES:
        try:
            modules[name] = importlib.import_module(name)
        except ImportError as exc:
            skips.append(f"{name}: {exc}")

    # §10: each engine must import its driver at MODULE level, or the skip
    # above never happens and CI's engine-smoke job cannot catch a broken
    # import.
    driver_imports = {"playwright_scraper": "playwright",
                      "puppeteer_scraper": "pyppeteer",
                      "selenium_scraper": "selenium"}
    for name, driver in driver_imports.items():
        tree = ast.parse(open(os.path.join(REPO_ROOT, f"{name}.py"),
                              encoding="utf-8").read())
        top_level = [n for n in tree.body
                     if isinstance(n, (ast.Import, ast.ImportFrom))]
        imported = set()
        for node in top_level:
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
            elif isinstance(node, ast.Import):
                imported.update(a.name.split(".")[0] for a in node.names)
        ok &= check(f"{name} imports {driver} at module level",
                    driver in imported)

    # The flag contract (§9), asserted against the contract AND between the
    # engines, in both directions.
    contract = {"--url", "--pages", "--category", "--format", "--out",
                "--delay", "--retries", "--retry-delay", "--concurrency",
                "--proxy", "--proxy-file", "--proxy-rotate", "--proxy-shuffle",
                "--proxy-block-retries", "--twocaptcha-key", "--captcha-api",
                "--solve-captcha", "--min-score", "--cdp-endpoint",
                "--allow-empty", "--dump-html", "--headless", "--headful",
                "--mode", "--fingerprint", "--fp-tags", "--fp-country"}
    flag_sets = {}
    for name in ENGINES:
        source = open(os.path.join(REPO_ROOT, f"{name}.py"),
                      encoding="utf-8").read()
        flag_sets[name] = set(re.findall(r'add_argument\(\s*"(--[a-z0-9-]+)"',
                                         source))
    for name, flags in flag_sets.items():
        missing = contract - flags
        # Two documented engine limits, both in the README:
        #   selenium   cannot attach to an authenticated remote CDP endpoint
        #   pyppeteer  carries no fingerprint flags anywhere in this scraper
        #              family — the 2Captcha fingerprint client is wired into
        #              the Playwright and Selenium engines only
        allowed_missing = set()
        if name == "selenium_scraper":
            allowed_missing = {"--cdp-endpoint"}
        elif name == "puppeteer_scraper":
            allowed_missing = {"--fingerprint", "--fp-tags", "--fp-country"}
        ok &= check(f"{name} carries the contract's flags "
                    f"(missing: {sorted(missing - allowed_missing)})",
                    not (missing - allowed_missing))
    shared = set.intersection(*flag_sets.values()) if flag_sets else set()
    for name, flags in flag_sets.items():
        extra = flags - shared
        documented_differences = {"--cdp-endpoint", "--chromium-path",
                                  "--browser-channel", "--locale",
                                  "--chromedriver", "--binary",
                                  "--fingerprint", "--fp-tags", "--fp-country",
                                  "--no-sandbox", "--disable-dev-shm-usage"}
        ok &= check(f"{name}'s extra flags are documented differences "
                    f"({sorted(extra - documented_differences)})",
                    not (extra - documented_differences))

    # The named constants §1 allows an engine to hold must be identical.
    for constant in ("TITLE_FLOOR", "IMAGE_FLOOR", "THIN_PAGE_SHARE"):
        values = {}
        for name in ENGINES:
            source = open(os.path.join(REPO_ROOT, f"{name}.py"),
                          encoding="utf-8").read()
            match = re.search(rf"^{constant} = (.+)$", source, re.M)
            if match:
                values[name] = match.group(1).strip()
        ok &= check(f"all three engines agree on {constant} ({values})",
                    len(set(values.values())) == 1 and len(values) == 3)

    # §17.1: bind every shared-module call in every engine against the
    # callee's REAL signature.
    binding_failures = []
    for name in ENGINES:
        tree = ast.parse(open(os.path.join(REPO_ROOT, f"{name}.py"),
                              encoding="utf-8").read())
        imported = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module in SHARED_MODULES:
                for alias in node.names:
                    imported[alias.asname or alias.name] = (node.module,
                                                            alias.name)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            target, label = None, None
            func = node.func
            if (isinstance(func, ast.Attribute)
                    and isinstance(func.value, ast.Name)
                    and func.value.id in SHARED_MODULES):
                target = getattr(SHARED_MODULES[func.value.id], func.attr, None)
                label = f"{func.value.id}.{func.attr}"
            elif isinstance(func, ast.Name) and func.id in imported:
                module, original = imported[func.id]
                target = getattr(SHARED_MODULES[module], original, None)
                label = f"{module}.{original}"
            if target is None or not callable(target):
                continue
            try:
                signature = inspect.signature(target)
            except (TypeError, ValueError):
                continue
            args = [inspect.Parameter.empty] * len(node.args)
            kwargs = {k.arg: None for k in node.keywords if k.arg}
            try:
                signature.bind(*args, **kwargs)
            except TypeError as exc:
                binding_failures.append(f"{name}:{node.lineno} {label}(): {exc}")
    ok &= check(f"every shared-module call binds ({len(binding_failures)} "
                f"failure(s))", not binding_failures)
    for failure in binding_failures:
        print(f"        {failure}")

    # A removed flag stays removed, scoped to the ENGINES: `--country` on a
    # scraper could disagree with the URL, and is legitimate on
    # fingerprint_client.py where it picks a fingerprint locale.
    for name in ENGINES:
        source = open(os.path.join(REPO_ROOT, f"{name}.py"),
                      encoding="utf-8").read()
        ok &= check(f"{name} has no --country (the host IS the country)",
                    'add_argument("--country"' not in source)
        ok &= check(f"{name} offers only --mode listing",
                    'choices=["listing"]' in source)
    return ok


# ---------------------------------------------------------------------------
def test_no_undefined_names():
    group("Names that resolve, not just parse")
    ok = True
    # §10: compileall proves a file PARSES, not that its names RESOLVE. Kept
    # coarse — one bag of bindings, no scope tracking — so it under-reports
    # rather than inventing problems.
    known = set(dir(builtins)) | {"__file__", "__name__", "__doc__"}
    for filename in sorted(os.listdir(REPO_ROOT)):
        if not filename.endswith(".py"):
            continue
        tree = ast.parse(open(os.path.join(REPO_ROOT, filename),
                              encoding="utf-8").read())
        bound = set(known)
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and isinstance(node.ctx,
                                                         (ast.Store, ast.Del)):
                bound.add(node.id)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                                   ast.ClassDef)):
                bound.add(node.name)
                args = getattr(node, "args", None)
                if args:
                    for arg in (args.args + args.kwonlyargs
                                + getattr(args, "posonlyargs", [])):
                        bound.add(arg.arg)
                    if args.vararg:
                        bound.add(args.vararg.arg)
                    if args.kwarg:
                        bound.add(args.kwarg.arg)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    bound.add(alias.asname or alias.name.split(".")[0])
            elif isinstance(node, ast.ExceptHandler) and node.name:
                bound.add(node.name)
            elif isinstance(node, ast.Lambda):
                args = node.args
                for arg in (args.args + args.kwonlyargs
                            + getattr(args, "posonlyargs", [])):
                    bound.add(arg.arg)
            elif isinstance(node, (ast.Global, ast.Nonlocal)):
                bound.update(node.names)
        undefined = sorted({n.id for n in ast.walk(tree)
                            if isinstance(n, ast.Name)
                            and isinstance(n.ctx, ast.Load)
                            and n.id not in bound})
        ok &= check(f"{filename}: no undefined names ({undefined})",
                    not undefined)
    return ok


# ---------------------------------------------------------------------------
def test_dockerfile_matches_its_entrypoint():
    group("The image carries every module its entrypoint imports")
    ok = True
    dockerfile = os.path.join(REPO_ROOT, "Dockerfile")
    ok &= check("there is a Dockerfile", os.path.exists(dockerfile))
    if not os.path.exists(dockerfile):
        return ok
    text = open(dockerfile, encoding="utf-8").read()
    copied = set(re.findall(r"([a-z_]+\.py)", text))

    # The entrypoint's transitive first-party import graph.
    entry = "playwright_scraper"
    needed, queue = set(), [entry]
    local = {f[:-3] for f in os.listdir(REPO_ROOT) if f.endswith(".py")}
    while queue:
        module = queue.pop()
        if module in needed:
            continue
        needed.add(module)
        tree = ast.parse(open(os.path.join(REPO_ROOT, f"{module}.py"),
                              encoding="utf-8").read())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module in local:
                queue.append(node.module)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in local:
                        queue.append(alias.name)
    missing = {f"{m}.py" for m in needed} - copied
    # Three repos in this family shipped an image missing proxy_pool.py and
    # died with ModuleNotFoundError on every invocation, `--help` included.
    ok &= check(f"the COPY list covers the import graph (missing: "
                f"{sorted(missing)})", not missing)
    # What matters is whether a COPY line brings one in — the file's own
    # comment mentions mounting a .env at runtime, which is the safe way.
    # A .env baked into an image is a credential published to everyone who
    # can pull it (§11).
    copy_lines = [l for l in text.splitlines()
                  if l.strip().upper().startswith("COPY")]
    ok &= check("no COPY line brings in a .env",
                not any(".env" in l for l in copy_lines))
    ok &= check("no COPY line brings in the test suite",
                not any("smoke_test" in l or "fixtures_generated" in l
                        for l in copy_lines))
    ok &= check("the entrypoint is the Playwright engine",
                "playwright_scraper.py" in text)
    return ok


# ---------------------------------------------------------------------------
def test_wording():
    group("Wording enforced by a test (§12)")
    ok = True
    banned = {
        "cloud browser": "Scraping Browser API",
        "antidetect browser": "Scraping Browser API",
        "anti-detect browser": "Scraping Browser API",
        "gate.2prx.com": "2captcha.com/proxy",
        "--antidetect": "removed",
        "ANTIDETECT_LOCAL_API": "removed",
    }
    # This file is excluded: it holds every banned phrase as TEST DATA, and
    # a check that fails on its own fixtures is a check nobody can read (§17).
    shipped = [f for f in os.listdir(REPO_ROOT)
               if f.endswith((".py", ".md", ".txt", ".toml", ".yml"))
               and f != "smoke_test.py"]
    for filename in sorted(shipped):
        text = open(os.path.join(REPO_ROOT, filename),
                    encoding="utf-8", errors="replace").read().lower()
        for phrase, instead in banned.items():
            if phrase.lower() in text:
                ok &= check(f"{filename} uses {phrase!r} — write {instead!r}",
                            False)
    ok &= check("no banned wording in any shipped file", True)

    # A competitor must never be integrated (§12).
    for filename in sorted(f for f in os.listdir(REPO_ROOT)
                           if f.endswith(".py") and f != "smoke_test.py"):
        text = open(os.path.join(REPO_ROOT, filename), encoding="utf-8").read()
        for competitor in ("anti-captcha.com", "capmonster", "deathbycaptcha",
                           "brightdata", "oxylabs", "smartproxy"):
            if competitor in text.lower():
                ok &= check(f"{filename} references {competitor!r}", False)
    ok &= check("no competitor is integrated", True)
    return ok


# ---------------------------------------------------------------------------
def test_no_capture_leaks():
    group("The fixtures carry nothing that reads as a credential")
    ok = True
    blob = json.dumps(FIXTURES, ensure_ascii=False)
    # Guarded by PATTERNS, not by the literals one capture happened to hold,
    # so the next capture is caught too (§10).
    patterns = {
        r'data-sitekey="(?!SITEKEY-SCRUBBED)[^"]{20,}"': "an unscrubbed site key",
        r"\b[0-9a-f]{32}\b": "a 32-hex string (reads as an API key)",
        r"://[^/\s\"']+:[^/\s\"'@]+@": "a credentialled URL",
        r"\banti-csrftoken\b": "a CSRF token",
        r'"sessionId"\s*:\s*"[^"]+"': "a session id",
    }
    for pattern, what in patterns.items():
        hit = re.search(pattern, blob)
        ok &= check(f"no {what} in the fixtures"
                    + (f" (found {hit.group(0)[:40]!r})" if hit else ""),
                    hit is None)
    ok &= check("the site keys ARE scrubbed, visibly",
                "SITEKEY-SCRUBBED-BY-make_fixtures" in blob)
    # Every fixture says which capture it came from, so a reader can tell
    # what is real.
    ok &= check("every fixture names its source capture",
                all("capture" in f for f in FIXTURES.values()))
    return ok


# ---------------------------------------------------------------------------
def test_ci_checks_is_wired_up():
    group("One implementation of the credential grep, invoked from both")
    ok = True
    # §17: `.github/ci_checks.py` was in three repos and invoked by nothing,
    # while the workflow reimplemented a narrower version inline. Two sources
    # of truth, one dead and one holed.
    script = os.path.join(REPO_ROOT, ".github", "ci_checks.py")
    ok &= check("ci_checks.py exists", os.path.exists(script))
    workflow = os.path.join(REPO_ROOT, ".github", "workflows", "tests.yml")
    ok &= check("tests.yml exists", os.path.exists(workflow))
    if os.path.exists(workflow):
        text = open(workflow, encoding="utf-8").read()
        ok &= check("the workflow CALLS ci_checks.py rather than reimplementing it",
                    "ci_checks.py" in text)
    if os.path.exists(script):
        # It must pass on its own repository. A check that fails on its own
        # repo is a check nobody can read.
        import subprocess
        result = subprocess.run(
            [sys.executable, script, "--secret-check"],
            cwd=REPO_ROOT, capture_output=True, text=True)
        ok &= check(f"ci_checks.py --secret-check passes on this repo "
                    f"({result.stdout.strip()[-120:] or result.stderr.strip()[-120:]})",
                    result.returncode == 0)
    return ok


# ---------------------------------------------------------------------------
def test_sample_output():
    group("sample_output is cut from a real run")
    ok = True
    for name in ("sample_output.json", "sample_output.csv"):
        path = os.path.join(REPO_ROOT, name)
        ok &= check(f"{name} is committed", os.path.exists(path))
    json_path = os.path.join(REPO_ROOT, "sample_output.json")
    if os.path.exists(json_path):
        rows = json.load(open(json_path, encoding="utf-8"))
        ok &= check("it holds rows", len(rows) > 0)
        names = {f.name for f in fields(Product)}
        ok &= check("its columns are exactly Product's",
                    all(set(r) == names for r in rows))
        ok &= check("every row names a real foodpanda host",
                    all(r["source"] in HOSTS for r in rows))
        ok &= check("every url is a /restaurant/ URL",
                    all("/restaurant/" in (r["url"] or "") for r in rows))
        # CI greps for fabrication markers; so does this.
        blob = json.dumps(rows)
        for marker in ("example.com", "lorem ipsum", "Example Vendor",
                       "TODO", "FIXME", "XXXX"):
            ok &= check(f"no {marker!r} in the sample",
                        marker.lower() not in blob.lower())
    csv_path = os.path.join(REPO_ROOT, "sample_output.csv")
    if os.path.exists(csv_path):
        with open(csv_path, encoding="utf-8") as f:
            header = next(csv_module.reader(f))
        ok &= check("the CSV header matches the dataclass",
                    header == [f.name for f in fields(Product)])
    return ok


# ---------------------------------------------------------------------------
def test_required_files_are_committed():
    group("A clean checkout has what it needs")
    ok = True
    for name in ("fixtures_generated.json", "sample_output.json",
                 "sample_output.csv", ".env.example", "requirements.txt",
                 "requirements-playwright.txt", "requirements-puppeteer.txt",
                 "requirements-selenium.txt", "pyproject.toml", "Dockerfile",
                 "README.md", "CHANGELOG.md", "TROUBLESHOOTING.md",
                 "make_fixtures.py"):
        ok &= check(f"{name} is present", os.path.exists(os.path.join(REPO_ROOT, name)))

    # The exceptions that keep `*.json` from swallowing the committed files.
    gitignore = open(os.path.join(REPO_ROOT, ".gitignore"), encoding="utf-8").read()
    for exception in ("!fixtures_generated.json", "!sample_output.json",
                      "!sample_output.csv"):
        ok &= check(f".gitignore keeps {exception[1:]}", exception in gitignore)
    ok &= check(".gitignore excludes .env", re.search(r"^\.env$", gitignore, re.M))
    return ok


# ---------------------------------------------------------------------------
def test_readme_claims():
    group("Every number in the README is one this repo can show")
    ok = True
    path = os.path.join(REPO_ROOT, "README.md")
    if not os.path.exists(path):
        return check("README.md exists", False)
    text = open(path, encoding="utf-8").read()
    ok &= check("the README names the eleven country sites",
                all(host in text for host in HOSTS))
    ok &= check("it states that a vendor page is not supported",
                "/restaurant/" in text and "403" in text)
    ok &= check("it states the challenge is reCAPTCHA Enterprise",
                "Enterprise" in text)
    ok &= check("it says the solve path was NOT live-verified",
                re.search(r"not (been )?(live-)?verified", text, re.I) is not None)
    # Compared with thousands separators stripped: the README writes "1,904"
    # because that is the readable form, and a check that forced "1904" would
    # be a check about typography rather than about the claim.
    ungrouped = text.replace(",", "")
    ok &= check("the tile count it quotes matches the fixtures' originals",
                str(FIXTURES["SCROLLED_PK_AREA"]["tiles_in_original"])
                in ungrouped)
    ok &= check("the page-size figure it quotes is the measured one",
                str(TYPICAL_PAGE_SIZE) in ungrouped)
    ok &= check("it documents the exit codes", "exit 6" in text or "`6`" in text)
    return ok


# The floor the README and CHANGELOG quote. Deliberately a FLOOR rather than
# an exact number: an exact count goes stale the moment a check is added, and
# a stale number in a README is worse than no number (§17). Raise it when it
# is comfortably passed; it can only ever be an under-claim.
CLAIMED_CHECK_FLOOR = 400


def main() -> int:
    ok = True
    skips = []

    ok &= test_numbers_and_currencies()
    ok &= test_values_on_real_fixtures()
    ok &= test_urls_and_hosts()
    ok &= test_pagination()
    ok &= test_page_state()
    ok &= test_markers_that_match_every_page()
    ok &= test_the_challenge_is_not_solvable()
    ok &= test_page_flow_policy()
    ok &= test_scroll_loop()
    ok &= test_a_refused_page_is_not_an_empty_one()
    ok &= test_output_contract()
    ok &= test_writers_and_finish_run()
    ok &= test_diff()
    ok &= test_env_config()
    ok &= test_proxy_pool_and_credentials()
    ok &= test_engine_parity(skips)
    ok &= test_no_undefined_names()
    ok &= test_dockerfile_matches_its_entrypoint()
    ok &= test_wording()
    ok &= test_no_capture_leaks()
    ok &= test_ci_checks_is_wired_up()
    ok &= test_sample_output()
    ok &= test_required_files_are_committed()
    ok &= test_readme_claims()

    passed = _total_checks - len(_failures)
    if passed < CLAIMED_CHECK_FLOOR:
        ok = False
        _failures.append(
            f"the README and CHANGELOG claim over {CLAIMED_CHECK_FLOOR} "
            f"checks and only {passed} ran — either checks were removed or "
            f"the claim needs lowering")

    print()
    if _failures:
        print("%d check(s) FAILED:" % len(_failures))
        for failure in _failures:
            print("  - %s" % failure)
    if skips:
        print("%d engine group(s) SKIPPED — an optional engine library is "
              "absent. CI's engine-smoke job installs each engine in its own "
              "venv and fails if this list is non-empty, because a skip reads "
              "exactly like a passing run:" % len(skips))
        for skip in skips:
            print("  - %s" % skip)
    print("%d check(s) ran." % _total_checks)
    print("smoke_test: %s" % ("OK" if ok else "FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
