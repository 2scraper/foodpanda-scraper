"""page_flow.py — what to do with the page foodpanda just gave us.

foodpanda answers a request five ways, and four of them want a different
response, which is why this module exists rather than the same triage being
written three times inside three engines and drifting apart (§1):

    content    the vendor grid is in the document
    empty      the address does not exist — the site's own 404 document,
               served out of its own assets and therefore indistinguishable
               from a page still painting unless you look for its title
    shell      served, built out of the site's own assets, grid not there
               yet. Wants a WAIT, not a refetch
    challenge  a refusal carrying something this repo can solve, which on
               this site is PerimeterX's denial page when it renders its
               reCAPTCHA Enterprise widget. Measured: solved in ~55s for
               $0.00299, after which the page came back with its grid, while
               a plain reload cleared the same block 0 of 8 times
    blocked    a refusal with nothing solvable on it, which here means
               PerimeterX's widget-less stub. Cloudflare's managed challenge
               is NOT in this state: its Turnstile is intercepted and solved
               (measured: 11s, $0.00145, and the page came back)

The policy lives in `STATE_POLICY` as DATA, so an engine cannot quietly
disagree with its twins about whether a page is worth retrying or worth
paying for.

Everything here is pure or driven through small callables, so each engine
passes its own driver's primitives and keeps its browser plumbing to itself:

    count(selector) -> int              how many elements match
    scroll_page() -> None               scroll the document down
    page_height() -> Optional[int]      its scroll height
    sleep(ms) -> None                   wait

No JavaScript crosses that boundary in either direction (§1): Selenium's
`execute_script` takes a function BODY with an explicit `return` where
Playwright and pyppeteer take `() => expr`, so this module names the
OPERATION and each engine spells it in its own driver's dialect.

The grid is SERVER-RENDERED, which decides most of this file
------------------------------------------------------------
§18 says which page kind server-renders its grid decides what "unknown"
means, and the answer here was measured by reading the RESPONSE BODY rather
than the settled DOM:

    /city/lahore/area/gulberg?page=3   918 KB   44 tiles in the raw response,
                                                44 at DOMContentLoaded,
                                                44 after ten seconds
    /city/lahore                       978 KB   48 / 48 / 48

Every tile is in the first response. Nothing arrives over XHR, nothing paints
late, and `shell` is therefore a state this repo has never observed on a
listing — it is kept because a refusal must not be able to masquerade as one,
not because a listing is expected to reach it.

Two things follow, and both are the opposite of a sibling repo:

  * The readiness wait is nearly free and almost always resolves on its first
    poll. It stays, because "almost always" is not "always" and the cost of
    being wrong is a run holding part of a page.
  * A browser is needed to get PAST THE BOT CHECK, not to render the page.
    That is worth knowing before paying for browser infrastructure.

Why there is almost no scrolling here, unlike a sibling repo
------------------------------------------------------------
An area listing DOES scroll infinitely — a capture of one, scrolled to
exhaustion, holds 1,904 tiles and 6.9 MB of DOM. This repo deliberately does
not do that, because the site publishes a page ADDRESS for every one of those
batches (`?page=N`, see product_parser) and fetching the address is strictly
better: it is restartable, it parallelises, each response is 1 MB instead of
17, and every tile arrives already attributed to its page. Scrolling would
get the same rows more slowly, in one un-resumable document, and would make
`page` a guess.

The one listing with no addresses is the HOME page, which is a curated set
with no pagination of any kind. That one is scrolled, bounded, and that is
the only place the scroll loop below is used.
"""

from __future__ import annotations

import logging
from typing import Callable, Dict, List, Optional
from urllib.parse import urlsplit

from product_parser import (SELECTORS, NEXT_PAGE_SELECTOR, PAGE_CAP,
                            unsolvable_challenge,
                            TYPICAL_PAGE_SIZE, THIN_PAGE_FLOOR,
                            detect_block_marker, detect_bot_challenge,
                            detect_page_state, listing_kind, page_of_url,
                            page_url, paginates_by_url, results_range,
                            served_by_foodpanda, strip_tracking)

logger = logging.getLogger("page_flow")


# ---------------------------------------------------------------------------
# Readiness
# ---------------------------------------------------------------------------
READY_SELECTOR = SELECTORS["item_card"]
# The family's name for it, so an engine written against a sibling reads the
# same.
READY_SELECTOR_LISTING = READY_SELECTOR

# Above 1, per §5: waiting for a single match resolves on an unrelated node
# long before the grid paints. Two rather than a full page, so a genuinely
# short last page is not made to spend the whole timeout — `min_matches`
# clamps it further when the caller knows the page is short.
MIN_CARD_MATCHES = 2

# Generous against a measured first paint of zero — the grid is in the first
# response — because a residential exit and a cold cache are both slower than
# a laptop on a home connection, and the cost of waiting too long is latency
# where the cost of waiting too little is a run holding part of a page.
CONTENT_TIMEOUT_MS = 25_000


def ready_selector(mode: str = "listing") -> str:
    return READY_SELECTOR


def min_matches(mode: str = "listing", expected: Optional[int] = None) -> int:
    """How many matches mean "painted"."""
    if expected is None or expected <= 0:
        return MIN_CARD_MATCHES
    return max(1, min(MIN_CARD_MATCHES, expected))


def content_timeout_ms(mode: str = "listing") -> int:
    return CONTENT_TIMEOUT_MS


def expected_cards(html: Optional[str]) -> Optional[int]:
    """How many tiles this page's own counter says it holds.

    Always None on this site: foodpanda publishes no counter anywhere in the
    document — no "1-48 of 1,904", no total. Kept so the engines' call sites
    are identical to their siblings', and so the absence is stated in code
    rather than discovered by someone looking for the counter.
    """
    return results_range(html or "").expected_on_page


def wait_for_count(count, sleep, selector: str, want: int,
                   timeout_ms: int, poll_ms: int = 500) -> int:
    """Poll until `count(selector) > want`, or the timeout. Returns the count.

    A POLL rather than the driver's own wait-for-predicate, and this is not a
    style choice: Playwright's `wait_for_function` hands the browser a STRING
    to evaluate, so on a site whose CSP lacks `unsafe-eval` it dies with

        EvalError: Evaluating a string as JavaScript violates the following
        Content Security Policy directive …

    which took a sibling repo's live run down with exit 1 — a crash, on that
    site's most obvious URL (§18). foodpanda's own CSP was not audited page
    kind by page kind here, which is exactly why the question is not being
    asked: counting elements goes over CDP instead (`querySelectorAll`
    through the protocol, not through eval), so it works under any CSP and
    spells the same in all three drivers.

    The count is returned rather than a bool so a caller can say how close it
    got, and a timeout is not an error: a listing with genuinely nothing on
    it never reaches `want`, and that is exit 4 rather than a fault.

    On this site it almost always returns on its FIRST poll, because the grid
    is in the first response (see the module docstring). It stays because
    "almost always" is not "always".
    """
    waited = 0
    found = 0
    while True:
        try:
            found = count(selector)
        except Exception as exc:                    # a driver-level fault
            logger.warning("could not count %r: %s", selector, exc)
            return found
        if found > want:
            return found
        if waited >= timeout_ms:
            return found
        sleep(poll_ms)
        waited += poll_ms


# ---------------------------------------------------------------------------
# The scroll, used on the home page and nowhere else
# ---------------------------------------------------------------------------
# Three stable rounds, not one: the next batch takes longer to arrive than a
# single pause (§8).
SCROLL_STABLE_ROUNDS = 3
# The home page is a curated set, not a catalogue — 47 to 83 vendor links
# across the ten country sites probed — so a handful of rounds reaches the
# bottom of it. The cap exists so a page that grows forever cannot hang a
# run, which on THIS site is not hypothetical: the same scroll loop pointed
# at an area listing ran to 1,904 tiles and 6.9 MB.
SCROLL_MAX_ROUNDS = 12
SCROLL_PAUSE_MS = 1_600


def should_scroll(url: str) -> bool:
    """Whether this listing needs scrolling at all.

    Only the home page. Every other listing has per-page addresses, and
    fetching those is better than scrolling in every way that matters — see
    the module docstring.
    """
    return listing_kind(url) == "listing" and not paginates_by_url(url)


def scroll_until_settled(count, page_height, scroll_to_bottom, sleep,
                         selector: str = READY_SELECTOR_LISTING,
                         rounds: int = SCROLL_MAX_ROUNDS,
                         pause_ms: int = SCROLL_PAUSE_MS,
                         stable_rounds: int = SCROLL_STABLE_ROUNDS) -> dict:
    """Scroll a listing until it stops growing. Returns what happened.

    Pure policy: every browser operation arrives as a callable, so this runs
    identically under all three drivers and is testable with the browser
    stubbed out.

    Scrolls to `document.body.scrollHeight` rather than wheeling a fixed
    distance — §8's rule, earned when a 2,400px wheel stopped three rounds
    short of the bottom of a sibling repo's 7,600px grid and a run took 30 of
    50 cards while looking settled. Requires the count AND the height to hold
    still for THREE rounds, because the next batch takes longer to arrive
    than a single pause.

    The returned dict is meant for the sidecar. `settled` False means the
    round budget ran out with the page still growing — the run is PARTIAL and
    must say so, because a listing that was still loading when we stopped is
    not an exhausted one.
    """
    seen_counts: List[int] = []
    stable = 0
    prev = None
    for i in range(rounds):
        sleep(pause_ms)
        try:
            found = count(selector)
        except Exception as exc:                    # a driver-level fault
            logger.warning("scroll round %d could not count: %s", i, exc)
            break
        height = page_height()
        seen_counts.append(found)
        same = prev is not None and found == prev[0] and height == prev[1]
        stable = stable + 1 if same else 0
        logger.info("scroll round %d: %d tiles, height %s, stable %d",
                    i, found, height, stable)
        prev = (found, height)
        if stable >= stable_rounds and found >= MIN_CARD_MATCHES:
            return {"settled": True, "rounds": i + 1, "cards": found,
                    "height": height, "counts": seen_counts}
        scroll_to_bottom()
    return {"settled": False, "rounds": len(seen_counts),
            "cards": prev[0] if prev else 0,
            "height": prev[1] if prev else None, "counts": seen_counts}


# ---------------------------------------------------------------------------
# Classification and the policy that follows from it
# ---------------------------------------------------------------------------
def classify(html: Optional[str], status: Optional[int] = None,
             url: str = "") -> str:
    """Which of the five states this response is.

    `status` is positional and comes SECOND, matching `detect_page_state`.
    Getting that wrong is not a style question: a sibling repo shipped two of
    three engines calling this as `classify(html, url=...)`, both crashed on
    their first fetch, and nothing short of a live run or a signature-binding
    check saw it (§17). This repo's smoke suite binds every shared-module
    call in every engine for that reason.
    """
    if html is None:
        return "blocked"
    return detect_page_state(html, status, url)


# The retry/solve/blocked decision as DATA rather than as three copies of an
# if-chain in three engines (§1).
#
#   parse    is there anything on this page worth writing down?
#   retry    would fetching it again, later or from a different exit,
#            plausibly help?
#   solve    is there something to pay a solver for?
#   blocked  does this count towards exit 3?
STATE_POLICY: Dict[str, Dict[str, bool]] = {
    "content":   {"parse": True,  "retry": False, "solve": False, "blocked": False},
    # The address does not exist. The site answered the question; asking it
    # again gets the same 404.
    "empty":     {"parse": False, "retry": False, "solve": False, "blocked": False},
    # Served and still painting. Wants the readiness wait, not another fetch:
    # refetching a shell buys another shell (§18). Parsed because by the time
    # an engine asks, the wait has already run.
    "shell":     {"parse": True,  "retry": False, "solve": False, "blocked": False},
    "challenge": {"parse": False, "retry": True,  "solve": True,  "blocked": False},
    "blocked":   {"parse": False, "retry": True,  "solve": False, "blocked": True},
}


def should_parse(state: str) -> bool:
    return STATE_POLICY.get(state, STATE_POLICY["blocked"])["parse"]


def should_retry(state: str) -> bool:
    return STATE_POLICY.get(state, STATE_POLICY["blocked"])["retry"]


def should_solve(state: str) -> bool:
    return STATE_POLICY.get(state, STATE_POLICY["blocked"])["solve"]


def counts_as_blocked(state: str) -> bool:
    return STATE_POLICY.get(state, STATE_POLICY["blocked"])["blocked"]


def is_unpainted(state: str, html: Optional[str]) -> bool:
    """Whether this page is served but has not painted its grid yet."""
    if state != "shell":
        return False
    return served_by_foodpanda(html or "")


# RETRYING A REFUSAL WORKS HERE, and the number behind that is the most
# useful measurement in this repo.
#
# A sweep of fourteen foodpanda addresses from one residential exit, each
# navigated up to three times with a fresh browser and roughly half a minute
# between attempts, was SERVED on 10 of 30 navigations — and the successes
# were spread across the attempt numbers: four came on the first attempt, one
# on the second, five on the third. The same address that answered 403
# answered 200 two attempts later, with no proxy, no solve and no change but
# a new browser and a pause.
#
# So the refusal is a property of the SESSION and the moment, not of the
# address — which is why a fresh browser is what re-rolls it (§8: "a rotation
# is a fresh browser") and why the budget below is non-zero. It is larger
# with a pool because a different exit is a stronger re-roll than a different
# session from the same address.
#
# Four attempts against a measured per-attempt success of about a third puts
# a page's chance of being served near 80%; five puts it near 87%. The
# engines READ these constants rather than computing their own budget — a
# policy constant nothing consults is the same defect as dead code (§17), and
# the smoke suite asserts each of them has a consumer outside this module.
RETRY_ON_BLOCKED = True
BLOCK_RETRIES_WITHOUT_POOL = 4
BLOCK_RETRIES_WITH_POOL = 5

# One solve per page. Detection is broad on purpose, but a second solve on
# the same page has never been the answer to the first one failing.
SOLVES_PER_PAGE = 1


def block_advice(html: Optional[str], headless: bool, has_pool: bool) -> str:
    """What a reader should actually DO about this block.

    Exists because the two refusals on this site want OPPOSITE first moves,
    and a message that says only "blocked" sends half the readers to buy a
    proxy they did not need.
    """
    marker = detect_block_marker(html or "")
    if marker == "Cloudflare":
        return (
            "blocked (Cloudflare's managed challenge). Every non-browser "
            "client gets this — all eleven hosts probed answer a plain HTTP "
            "request with it, measured — and foodpanda.com serves one to a "
            "real browser too. It is a Turnstile, so --solve-captcha clears "
            "it (11s, $0.00145 measured); without a key, --retries is the "
            "instrument, since every attempt launches a fresh browser.")
    hints = [
        "this refusal is per-session and it clears: the same address was "
        "served on a later attempt in 10 of 30 measured navigations, with no "
        "proxy and no solve — so let --retries do its work before buying "
        "anything",
        "raise --delay: the refusal rate climbs with how fast the requests "
        "come, and 30 navigations in 20 minutes from one address is what "
        "produced that two-in-three refusal rate",
    ]
    if headless:
        hints.append("try --headful: a headless window was refused on every "
                     "one of four attempts where a headful one was served")
    if not has_pool:
        hints.append("spread the load with --proxy-file, and prefer an exit "
                     "in the site's own country")
    if detect_bot_challenge(html or ""):
        hints.append(
            "this page rendered a solvable widget — `--solve-captcha always` "
            "with a funded key gets through it (measured: ~55s, $0.003), "
            "though a fresh session usually clears the same refusal for "
            "nothing")
    elif unsolvable_challenge(html or ""):
        # Said OUT LOUD, because the obvious next move on seeing a captcha is
        # to buy a solver, and on THIS variant there is nothing to buy.
        hints.append(
            f"this page carries {unsolvable_challenge(html or '')} — a solve "
            f"was not attempted and nothing was charged")
    lead = "blocked (PerimeterX)" if marker else "blocked (no vendor marker)"
    return lead + ". " + "; ".join(hints) + "."


# ---------------------------------------------------------------------------
# Pagination — an address the site publishes itself
# ---------------------------------------------------------------------------
# Unusually for this family there is a `page_url()` to call, and §7's "verify
# first" was satisfied the only way that counts: the site renders its own
# `?page=N` links between batches, and a cold fetch of `?page=2` returned 48
# tiles sharing no vendor code with page 1. See product_parser.
#
# The three layers §7 asks for, weakest last:
#   1. `link[rel=next]` leads NEXT_PAGE_SELECTOR (foodpanda publishes none
#      today; it leads so that the day it does, nothing needs editing).
#   2. `page_url()` reconstructing `?page=N` — what actually works.
#   3. The terminating condition is DATA: an engine stops when a page adds no
#      new sku. A missing link is a property of markup; an exhausted listing
#      is a property of the catalogue.
NEXT_PAGE_TIMEOUT_MS = 30_000
NEXT_PAGE_POLL_MS = 500


def next_page_selector(page_num: int = 1) -> str:
    """The selector for the site's own next-page link.

    Takes `page_num` and ignores it, which is deliberate: the signature
    matches the family's, and a site that numbered its link differently per
    page would be handled here rather than in three engines.
    """
    return NEXT_PAGE_SELECTOR


def pagination_is_addressable(page1_url: str,
                              advertised_hrefs: Optional[List[str]] = None
                              ) -> bool:
    """Whether page N of this listing can be fetched without fetching N-1.

    §7's "plan page URLs up front, but VERIFY first". The convention is
    `?page=N`, and it is verified two ways depending on what page 1 gave us:

      * Page 1 advertised its own page links — which a SCROLLED listing does,
        because the site renders `<a href=".../gulberg?page=2">` between
        batches. Then the convention is checked against the site's own
        address, and a cursor or token the convention could not reproduce
        would answer False.
      * Page 1 advertised nothing — which a COLD fetch does, measured: zero
        page-number links on a page holding 48 tiles. Then the convention is
        all there is, and it stands on the direct verification in
        product_parser: a cold `?page=2` returned 48 tiles sharing no vendor
        code with page 1.

    An engine that gets False here must not plan page URLs and must not run
    workers concurrently.
    """
    if not paginates_by_url(page1_url):
        return False
    if not advertised_hrefs:
        return True
    built = page_url(page1_url, 2)
    want = strip_tracking(built)
    return any(strip_tracking(href) == want for href in advertised_hrefs if href)


def pagination_agrees(current_url: str, page_num: int,
                      advertised_hrefs: Optional[List[str]] = None) -> bool:
    """Whether the site's own next-page link matches what we would build."""
    if not advertised_hrefs:
        return True
    want = strip_tracking(page_url(current_url, page_num + 1))
    return any(strip_tracking(href) == want for href in advertised_hrefs if href)


def next_page_candidates(current_url: str,
                         advertised_hrefs: Optional[List[str]] = None
                         ) -> List[str]:
    """Addresses worth trying for the next page, best first.

    Empty for the home page, which paginates in no way whatsoever — asking
    for its page 2 would fetch the same tiles again, add no new sku, and be
    reported as an exhausted listing. Correct by luck; refusing it here makes
    it correct on purpose.
    """
    if not paginates_by_url(current_url):
        return []
    out = [page_url(current_url, page_of_url(current_url) + 1)]
    for href in advertised_hrefs or []:
        if href and _same_listing(current_url, href) and href not in out:
            out.append(href)
    return out


def _same_listing(current_url: str, candidate: str) -> bool:
    """Whether a candidate href is another page of THIS listing.

    Guards against following a link out of the listing — a neighbouring area,
    a promo card, the city index in the breadcrumb — which would silently
    replace the run's subject with someone else's catalogue.
    """
    a, b = urlsplit(current_url), urlsplit(candidate)
    if b.netloc and a.netloc and b.netloc.lower() != a.netloc.lower():
        return False
    return a.path.rstrip("/") == b.path.rstrip("/")


def page_cap_reached(page_num: int) -> bool:
    """Whether the hard page cap has been hit.

    A backstop, not a policy: the largest listing measured on this site is one
    the site itself numbered to page 25, and PAGE_CAP sits well above that so
    a malformed or genuinely enormous listing ends rather than running
    forever.
    """
    return page_num >= PAGE_CAP


def comparable(url: str) -> str:
    """A URL reduced to what identifies the page, for dedupe and comparison."""
    return strip_tracking(url)


# ---------------------------------------------------------------------------
# Completeness
# ---------------------------------------------------------------------------
def page_gap(html: Optional[str], parsed: int) -> Optional[int]:
    """How many tiles the page's own counter says are missing, or None.

    ALWAYS None on this site, because there is no counter to do the
    arithmetic with (§8's rank-gap trick needs the site to publish ranks, and
    foodpanda publishes none). None rather than 0 is the point: an unknown
    gap is not a gap of zero, and the two must not read the same in a
    sidecar.
    """
    expected = expected_cards(html)
    if expected is None:
        return None
    return max(0, expected - parsed)


def is_thin_page(parsed: int) -> bool:
    """Whether a page came back suspiciously short.

    A WARNING and never a completeness test, and the threshold is low on
    purpose. Page size is NOT constant on this site — measured 48, 48 and 44
    tiles on three cold fetches of consecutive pages of one listing — so a
    threshold at the typical size would fire on ordinary pages, and the last
    page of any listing is short by definition. `THIN_PAGE_FLOOR` is half the
    typical page, which no ordinary page has ever come back below.
    """
    return 0 < parsed < THIN_PAGE_FLOOR


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------
def concurrency_limit(url: str) -> Optional[int]:
    """The highest `--concurrency` this URL can honestly support.

    None — no limit of this module's making — for a listing with per-page
    addresses, which is the /city tree. 1 for everything else.

    A caveat that belongs with the number rather than in the README alone:
    concurrency here buys throughput at the cost of REFUSALS. N workers is N
    times the request rate from one address, and the refusal rate on this
    site is a function of exactly that. The engines warn when concurrency is
    raised without a pool (§7) and this is why.
    """
    return None if paginates_by_url(url) else 1


def concurrency_refusal(url: str) -> Optional[str]:
    """Why concurrency above 1 is refused for this URL, or None.

    Refused WITH the reason rather than silently running one worker, which
    would look like the flag did something.
    """
    kind = listing_kind(url)
    if kind == "vendor":
        return ("a vendor page is a single page; --concurrency above 1 has "
                "nothing to fetch")
    if kind == "index":
        return ("this is a directory of links rather than a listing of "
                "vendors; there are no pages to hand a second worker")
    if not paginates_by_url(url):
        return ("the foodpanda home page has no pagination of any kind — no "
                "page links, no page parameter, no next link — so there is no "
                "page 2 to hand a second worker. Point --url at a "
                "/city/{city} or /city/{city}/area/{area} listing, which do "
                "publish per-page addresses")
    return None
