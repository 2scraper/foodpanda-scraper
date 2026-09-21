"""
output_writer.py
-----------------
Shared row models + JSON/CSV writers used by all three scrapers.

One mode, one row shape
-----------------------
    --mode listing    a foodpanda vendor listing -> Product, one row per
                      vendor tile

There is deliberately no second mode. A `/restaurant/{code}/{slug}` page is
the obvious candidate — it is where a menu, a delivery fee and a minimum
order live — and it is REFUSED to this repo: eight cold navigations and one
click-through from a listing the same browser had just been served all came
back HTTP 403 with PerimeterX's denial page, on two country sites, while
listing pages on the same addresses and the same sessions answered 200. A
parser written against markup nobody has captured is a guess with a
docstring, so the mode is absent rather than broken. See README, "What a
vendor page does".

Six family columns are NOT here, and each absence is a measurement rather
than an oversight (§9: a column that is null on every row of every run should
not exist, and removing it needs the number written down):

    price            A foodpanda vendor tile carries no price. It cannot:
    currency         the thing on sale is a restaurant, not an item, and the
    original_price   menu behind it is on the page this repo cannot reach.
    price_source     Measured across 2,000 tiles on two country sites — PK
                     and SG — with every price-shaped anchor the family
                     knows: `.bds-c-price`, "Rs.", "S$", "Min. order",
                     "Delivery fee", a bare currency symbol outside a deal
                     label: zero occurrences on a tile. The one exception is
                     a MINIMUM SPEND inside a deal label ("20% off Rs. 300",
                     "15% off S$ 15"), which is a condition of a voucher and
                     not the vendor's price — it has its own two columns
                     below, under its own name.

                     So §4's tile-price overlay and §9's `price_source`
                     provenance are NOT ported. Dead code that looks
                     load-bearing is worse than no code.

    brand            A tile publishes no chain. Many vendors plainly ARE
                     chains ("KFC - Gulberg", "Pizza Hut - Jurong Point"),
                     but the chain is only ever the prefix of a free-text
                     name, and splitting on " - " would invent a brand for
                     every vendor whose name happens to contain a dash —
                     including "Slap - Girja Chowk", where the second half
                     is a neighbourhood. A guess presented as a fact (§8).

    in_stock         Renamed rather than dropped: what a tile states is
                     whether the vendor is ACCEPTING ORDERS RIGHT NOW, which
                     is `is_open` below. `in_stock` on a restaurant would be
                     a column about nothing.

`discount_pct` IS here, because the tile states it — and it comes with two
companions that stop it from lying. See the field.
"""

import csv
import json
from dataclasses import dataclass, asdict, field, fields
from datetime import datetime, timezone
from typing import Optional, List, Set, Sequence, Any, Type


# Which country site a row came from. This genuinely varies: foodpanda runs
# eleven of them on one front end, a Lahore vendor is on foodpanda.pk and
# nowhere else, and the deal labels are written in the local currency. Filled
# from the URL by `product_parser.source_of`; this is the fallback for a row
# built without one.
SOURCE_DEFAULT = "foodpanda.pk"


@dataclass
class Product:
    source: str = SOURCE_DEFAULT
    scraped_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    url: str = ""
    # The vendor code, four characters of the site's own id space ("pedb",
    # "c48s", "yqcc"). Read from the tile's OWN `data-testid` and checked
    # against the code in its href — the two agreed on 1,904 of 1,904 tiles,
    # and where a tile carries no link at all (one of those 1,904) the
    # attribute is still there and is what keeps the row identified.
    sku: Optional[str] = None
    # The vendor name as the tile prints it, from the `title` attribute
    # rather than the text node: the text node is truncated with an ellipsis
    # by CSS on a long name, and `title` is not. 1,904/1,904.
    title: Optional[str] = None
    # The currency the tile's money is written in, as an ISO 4217 code read
    # from the SYMBOL the tile prints — "Rs.69" -> PKR, "S$3.69" -> SGD. Null
    # rather than defaulted from the hostname where the tile carries no money
    # at all, which is every row of a city or area listing (§8: never present
    # a guess as a fact).
    currency: Optional[str] = None
    # From the tile's own deal label, and ONLY where that label states a
    # plain percentage: "20% off" -> 20.0. Two companion columns below carry
    # what a bare number would otherwise lose — `discount_label` keeps the
    # text verbatim, and `discount_is_upper_bound` is True for "Up to 35%
    # off", which is a ceiling and not a discount. Reading that as 35 with
    # nothing beside it would be a guess presented as a fact (§8), and 21%
    # of the labels measured are that shape.
    discount_pct: Optional[float] = None
    # Out of FIVE, and written as the site writes it: "5" and not "5.0".
    # 1,487 of 1,904 tiles on PK and 35 of 48 on SG — the tiles without one
    # are vendors with no reviews yet, which is a fact about the vendor and
    # not a parsing failure.
    rating: Optional[float] = None
    # The number in parentheses beside the rating. READ `review_count_is_floor`
    # BEFORE COMPARING TWO OF THESE: the site caps the printed figure at
    # round numbers — "(100+)", "(500+)", "(1000+)" — and 100+ is not 100.
    # A run that treated the cap as a count would report every busy vendor
    # as frozen at exactly 100 reviews forever.
    review_count: Optional[int] = None
    # The tile's image, from the site's own media hosts
    # (images.deliveryhero.io, foodpanda.dhmedia.io) and recognised
    # POSITIVELY by those hosts, so a future placeholder cannot fill the
    # column with something that is not a photo of the vendor. 1,904/1,904.
    image_url: Optional[str] = None
    # The vendor's cuisines as ONE string, which is the site's own
    # categorisation: "Pakistani", "Fast Food", "Cakes & Bakery". The list
    # form is in `cuisines` below; this is the family column and joins them
    # with ", " so one column name works across the family. 98% of tiles.
    category: Optional[str] = None
    # Which listing page this row came from (1-based) and its place in that
    # page as the site ordered it. Without `page`, `position` is ambiguous —
    # it restarts at 1 on every page, and a sibling repo shipped 60 of 119
    # rows silently claiming a position another row already held (§18). The
    # offline suite asserts the pair is unique across a multi-page run.
    page: Optional[int] = None
    position: Optional[int] = None

    # ---- foodpanda-specific, appended after the family prefix (§9) ----
    # The three columns the HOME page publishes and the city and area
    # listings do not, because the home page is the one listing the site
    # renders with a delivery address already in play. All three are null on
    # a /city or /city/../area row, and that is a property of the page kind
    # rather than of the vendor — `page_kind` below says which you have.
    #
    # The delivery estimate verbatim: "From 25 min", "From 50 min". NOT
    # parsed into minutes: the string is the site's own hedge ("From"), and a
    # bare 25 would present a floor as a duration.
    delivery_time: Optional[str] = None
    # The delivery fee as a number, with `currency` above beside it. 69.0 PKR
    # and 3.69 SGD in the measured captures. This is the only money a
    # foodpanda tile carries that is genuinely a price of something.
    delivery_fee: Optional[float] = None
    # The site's own price level, 1 to 4, printed as "$" to "$$$$". Counted
    # from the symbols rather than mapped from the screen-reader phrase
    # ("Inexpensive price range"), which is localised.
    price_level: Optional[int] = None
    # Which listing kind this row came off — "home", "city" or "area".
    # Recorded because three of the columns above are populated on exactly
    # one of them, and a consumer diffing a home run against a city run would
    # otherwise read the difference as the vendor changing.
    page_kind: Optional[str] = None
    # Any info row the tile published that none of the columns above claimed,
    # verbatim: "Free for first order", "In-Store Price", "Islandwide",
    # "Organic". A list rather than a guess at which of them deserves a
    # column of its own.
    info_notes: List[str] = field(default_factory=list)
    # Whether the vendor is accepting orders at the moment of the fetch. A
    # closed vendor gets an overlay on its image reading "Closed until Sat
    # 10:20"; an open one gets no overlay at all. 38% of PK tiles and 35% of
    # SG tiles were closed in the measured captures, which is what an
    # afternoon looks like, not a bug.
    #
    # This is a POINT-IN-TIME reading and the single most volatile column in
    # the schema: two runs an hour apart will disagree about a third of the
    # rows. diff_runs.py reports it, and it is the reason `--fail-on-change`
    # is not something to point at this column.
    is_open: Optional[bool] = None
    # The overlay's own words for when it opens again — "Sat 10:20",
    # "Tue 17:00". Verbatim and NOT parsed into a timestamp: the string
    # carries a weekday name in the site's locale and no date and no time
    # zone, so any conversion would be this machine's calendar presented as
    # the site's fact. Null on an open vendor.
    opens_at: Optional[str] = None
    # The cuisines as a list, in the site's own order, with the first of them
    # being what the site leads with. CSV joins with " | ", JSON keeps the
    # structure.
    cuisines: List[str] = field(default_factory=list)
    # Every deal label on the tile, verbatim and in the site's order:
    # "Free Delivery", "20% off", "Up to 15% off", "20% off Rs. 300",
    # "Voucher HC50", "New". The overflow chip the site renders when there
    # are more than it can fit ("+1", "+2") is EXCLUDED — it is a count of
    # hidden labels, not a label, and leaving it in would put "+1" in a
    # column of offers.
    tags: List[str] = field(default_factory=list)
    # The label `discount_pct` was read from, verbatim, so a wrong parse is
    # visible rather than silent.
    discount_label: Optional[str] = None
    # True where the label said "Up to N% off" — N is then a CEILING. False
    # where it said "N% off". Null where there is no percentage at all.
    discount_is_upper_bound: Optional[bool] = None
    # The minimum spend a deal label attaches to its percentage, and the
    # currency that spend is written in: "20% off Rs. 300" -> 300.0, "PKR";
    # "15% off S$ 15" -> 15.0, "SGD". This is the ONLY money on a tile and it
    # is a condition of a voucher, which is why it is not called `price`.
    # Sparse by nature — 1.4% of PK labels, 25% of SG labels.
    min_order: Optional[float] = None
    min_order_currency: Optional[str] = None
    # Whether the tile carries the site's free-delivery label. Case-folded on
    # purpose: PK writes "Free Delivery" and SG writes "Free delivery", and a
    # case-sensitive match would report 0% free delivery on Singapore.
    free_delivery: Optional[bool] = None
    # True where `review_count` is the site's cap rather than a count —
    # "(100+)", "(1000+)". 6% of PK tiles and 27% of SG tiles.
    review_count_is_floor: Optional[bool] = None
    # The site's own "super vendor" badge. Rare and unevenly distributed —
    # 24 of 1,904 on PK, 0 of 48 on SG — and kept because where it is present
    # it is a fact the site publishes, not an inference.
    is_super_vendor: Optional[bool] = None
    # The vendor's URL slug, the second segment of /restaurant/{code}/{slug}.
    # Kept beside `sku` because the slug changes when a vendor renames itself
    # and the code does not, so a diff on both says which of the two moved.
    slug: Optional[str] = None
    # Where this listing was, from the LISTING URL rather than from the tile:
    # /city/lahore/area/gulberg -> "lahore", "gulberg". A vendor tile states
    # no address of its own, so this is the only location a listing row has,
    # and it is the listing's location rather than the vendor's.
    city: Optional[str] = None
    area: Optional[str] = None


ROW_CLASS_BY_MODE = {"listing": Product}

# Modes whose rows are one-per-sku, and therefore safe to dedupe on `sku` and
# to hand to diff_runs.py. A listing page names each vendor once.
UNIQUE_BY_SKU_MODES = ("listing",)


def dedupe_by_key(rows: Sequence[Any], seen: Set[str], key: str = "sku") -> List[Any]:
    """Drop rows whose key already appeared earlier in this same run.

    `seen` is mutated in place, so callers thread the same set across pages —
    a stale or repeating next-page link then re-parses a page without
    duplicating its rows into the final output. On this site a healthy
    two-page run drops NOTHING: page 1 and page 2 of one Orlando search
    shared 0 of 50 titles, measured. So any non-zero drop count here is worth
    reading — the likeliest cause is a next-press that did not take and
    re-parsed the page it was already on.

    A row with no key is always kept: there is nothing to check a duplicate
    against, and dropping it would be a silent data loss rather than a
    duplicate removal.

    Both of this repo's modes are one row per `sku`, so `key` is never
    overridden here — the parameter exists because the rest of the family
    shares this function and one of them needs it.
    """
    fresh = []
    for r in rows:
        val = getattr(r, key, None)
        if val is None or val not in seen:
            if val is not None:
                seen.add(val)
            fresh.append(r)
    return fresh


# Kept under its old name: the engines and smoke tests in this family all
# call it, and a listing run does dedupe by sku.
def dedupe_by_sku(rows: Sequence[Any], seen: Set[str]) -> List[Any]:
    return dedupe_by_key(rows, seen, key="sku")


# CSV cannot hold a list. Joining with " | " keeps the cell readable in a
# spreadsheet and round-trippable by splitting on the same separator; the
# JSON output keeps the real list, so nothing is lost for a consumer that
# wants structure. `repr()` of a Python list (the default if this is not
# handled) is neither readable nor parseable by anything but Python.
LIST_CSV_SEPARATOR = " | "


def _csv_value(v: Any) -> Any:
    if isinstance(v, (list, tuple)):
        return LIST_CSV_SEPARATOR.join(str(x) for x in v)
    return v


def write_json(rows: Sequence[Any], path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump([asdict(r) for r in rows], f, ensure_ascii=False, indent=2)


def write_csv(rows: Sequence[Any], path: str, row_cls: Type = Product) -> None:
    # An empty result still gets the header row. A zero-byte file makes a
    # consumer fail on read (no columns to parse) instead of reading a valid
    # table with zero rows — and "an empty result is still a well-formed
    # result" is the same principle as `save` refusing to overwrite good data.
    #
    # The header comes from `row_cls`, not from the first row, so an empty
    # run still writes the columns of the mode that produced it.
    fieldnames = [f.name for f in fields(row_cls)]
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in rows:
            writer.writerow({k: _csv_value(v) for k, v in asdict(r).items()})


# Exit code used when a run completes but produced nothing. Distinct from 1
# (crash) so a caller can tell "ran, found nothing" from "blew up".
EXIT_NO_PRODUCTS = 4

# Exit code for a run blocked by a bot-check/challenge page before parsing
# even started — distinct from EXIT_NO_PRODUCTS so a caller can tell "the
# search genuinely matched nothing" from "something stood between us and the
# content". See product_parser.detect_bot_challenge.
#
# On this site this code specifically does NOT cover the three ways to get a
# real page with no products on it: a `/p/<slug>` discovery hub, which
# answers 200 with banners and carousels and no grid; a search whose query
# matches nothing ("Oops, produk nggak ditemukan"); and one page past the
# end of a category listing. All three are EXIT_NO_PRODUCTS — the request
# was served exactly as asked and simply has no products on it. Reporting
# any of them as blocked would send a user hunting for a proxy problem that
# does not exist.
#
# What EXIT_BLOCKED means here is unusually literal: this site refuses a
# address it has scored NOTHING at all. No status code, no interstitial, no
# vendor marker — the HTTP/2 stream is reset and the run sees a connection
# error rather than a page.
EXIT_BLOCKED = 3

# Exit code for a run that gathered SOME rows and then stopped early — a
# page-load timeout, a 503 throttle, or a challenge on page 3 of 10. The
# output file is still written (throwing away three good pages would be
# worse), but it is not a complete picture, and a consumer that cannot tell
# the difference will read the pages that were never fetched as products that
# disappeared from the catalogue. See write_run_meta.
# A REMOTE service failed — the Scraping Browser refusing the connection
# (`profile_locked` is the common one: a profile allows a single live
# connection), or the Scraper API answering an error. Distinct from 1 (a
# crash in this code) and from 2 (bad usage) because it means "try again, or
# use a different profile", not "there is a bug here". Defined once, here,
# because the browser engines and scraper_api_client.py both return it and
# two definitions of the same code is exactly how a family's exit contract
# drifts.
EXIT_API_ERROR = 5

EXIT_PARTIAL = 6


# Exit code for a run that never GOT its pages: a navigation timeout, a dead
# or unauthenticated proxy, a DNS failure, or an edge answering with
# something that is not the page that was asked for.
#
# Distinct from EXIT_NO_PRODUCTS because those are opposite facts. Exit 4 is
# a statement about the CATALOGUE — "we asked, and the answer was nothing" —
# so handing it to a run that never reached the site tells a pipeline the
# listing is empty when nothing was read at all.
#
# 5 rather than a new number, and 5 rather than EXIT_PARTIAL:
#
#   * this family's contract already reserves 5 for a transport failure
#     (scraper_api_client has used it for a remote API error since it was
#     written), so this needs no new code and no per-repo table for a caller
#     driving more than one of these scrapers;
#   * EXIT_PARTIAL (6) means "some rows were gathered and the output is
#     incomplete". A run holding nothing writes no output at all, so a
#     consumer that reads the file on a 6 finds either nothing or the
#     PREVIOUS run's good data, which `save` deliberately does not
#     overwrite. Exit 5 promises no file.
#
# Deliberately NOT applied when rows WERE gathered: a timeout on page 7 of
# 10 is a partial run (exit 6, output written), which is already right. This
# decides only what a run holding nothing reports.
EXIT_FETCH_FAILED = 5


def write_run_meta(out_prefix: str, meta: dict) -> str:
    """Write a run-metadata sidecar next to the output, return its path.

    Deliberately a separate `<out>.meta.json` rather than columns on every
    row: this describes the RUN, not the product, and repeating it across
    every row would both bloat the output and change the schema every
    consumer of this project already parses.

    diff_runs.py reads it to refuse a comparison between runs that are not
    both complete, and between runs of different `mode`.
    """
    path = f"{out_prefix}.meta.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    print(f"[+] Wrote run metadata -> {path} (status={meta.get('status')})")
    return path


def run_meta(status: str, stop_reason: str, pages_requested: int,
             pages_completed: int, start_url: str, final_url: str,
             products: int, pages_failed: Optional[List[int]] = None,
             mode: str = "listing", source: str = SOURCE_DEFAULT,
             extra: Optional[dict] = None) -> dict:
    """Build the metadata dict for a finished run.

    `status` is the field a consumer branches on:
      complete — every requested page was fetched, or the site's own
                 pagination genuinely ran out (nothing more existed to get)
      partial  — rows were gathered, then the run stopped early
      failed   — nothing was gathered at all

    `mode` and `source` are both recorded. `mode` has one value here and
    is kept so the sidecar's shape matches the family's. `source` genuinely
    varies and matters more than usual: foodpanda runs ten country sites
    and issues vendor codes PER COUNTRY, so two rows from different sites can
    share a `sku` and be unrelated vendors. diff_runs.py refuses a pair whose
    sources differ for exactly that reason — a cross-country diff would match
    them and report the difference as a change.

    `extra` carries facts about the run that are not about any single row.
    This repo puts the listing's own counter there: `results_total`,
    `results_total_is_floor` and `cards_missing`, so a consumer can see that
    the site said "1 - 50 of 300+" and the run merged 40 rows off that page
    WITHOUT re-reading the HTML. That gap is arithmetic rather than a
    threshold (§8), and one measured run had it: page 2 read "51 - 100 of
    300+" and yielded 40 cards.

    `pages_failed` lists the pages that did not yield data, by number.
    `pages_completed` alone was enough only while pages were fetched strictly
    in order, where "3 of 10 completed" could only mean 1-2-3: a count is not
    a description once pages can be fetched independently and page 3 can fail
    while 4 and 5 succeed. Recording the numbers keeps the sidecar honest
    about WHICH part of the catalogue is missing, not just how much.
    """
    meta = {
        "source": source,
        "mode": mode,
        "status": status,
        "stop_reason": stop_reason,
        "pages_requested": pages_requested,
        "pages_completed": pages_completed,
        "pages_failed": pages_failed or [],
        "products": products,
        "start_url": start_url,
        "final_url": final_url,
        "finished_at": datetime.now(timezone.utc).isoformat(),
    }
    if extra:
        # Merged rather than nested under a key, so a consumer reads
        # `shop_rating` at the top level beside `products`. Run fields win a
        # name collision: a caller cannot accidentally overwrite `status`.
        meta.update({k: v for k, v in extra.items() if k not in meta})
    return meta


def save(rows: Sequence[Any], out_prefix: str, fmt: str,
         allow_empty: bool = False, row_cls: Type = Product) -> int:
    """Write JSON/CSV and return a process exit code.

    Returns 0 when rows were written, EXIT_NO_PRODUCTS when there were none.
    Callers are expected to exit with it.

    On zero rows, nothing is written at all unless `allow_empty`. Two reasons,
    and a live run demonstrated both. A page-load timeout produced
    `Saved 0 products -> out.json` and exit 0: a two-byte `[]` that a
    consuming pipeline reads as a successful run with no stock. Worse, if the
    file already held a good result from an earlier run, that result is now
    gone — the failure destroyed the last known good data. So an empty result
    leaves the previous file intact and says why.

    `allow_empty=True` is for the legitimate case: a filter that genuinely
    matches nothing, where an empty file is the answer.
    """
    if not rows and not allow_empty:
        print(f"[!] 0 products — refusing to write {out_prefix}.json/.csv, so an "
              f"earlier good result isn't overwritten with an empty one. "
              f"Pass --allow-empty if an empty result is the expected answer.")
        return EXIT_NO_PRODUCTS

    if fmt in ("json", "both"):
        write_json(rows, f"{out_prefix}.json")
        print(f"[+] Saved {len(rows)} products -> {out_prefix}.json")
    if fmt in ("csv", "both"):
        write_csv(rows, f"{out_prefix}.csv", row_cls=row_cls)
        print(f"[+] Saved {len(rows)} products -> {out_prefix}.csv")
    return 0 if rows else EXIT_NO_PRODUCTS


# Stop reasons that mean the run saw everything there was to see. Anything
# else ended the page loop early, so the result is only a partial view.
#
# "no_new_products" belongs here and "pagination_exhausted" beside it, and on
# this site the ordering between them is not a preference — it is the only
# thing that works.
#
# foodpanda's pagination is a real address the site publishes itself — an
# area listing renders `<a href=".../gulberg?page=2">` between batches — and a
# cold fetch of that address returned 48 tiles sharing no vendor code with
# page 1. So "no_new_products" really does mean the catalogue ran out here,
# and it belongs among the COMPLETE reasons.
#
# What it must NOT mean is "the page was refused". That was a real bug, found
# by this repo's first live run: a page refused on every attempt fell through
# to the parse, produced 0 rows, and the loop read that as the end of the
# listing — reporting COMPLETE while holding a third of the catalogue. The
# engines now mark any state that must not be parsed, other than `empty`, as
# a FAILED page before the loop ever sees a row count (§7, §18).
#
# "no new products" is therefore the data-side termination condition, and
# "pagination_exhausted" means the site's own button was gone or disabled —
# a property of the DOM, checked second.
#
# "single_page_mode" is complete by construction: --mode property reads one
# page because one page is all there is.
COMPLETE_STOP_REASONS = ("completed", "pagination_exhausted", "no_new_products",
                         "single_page_mode")


def finish_run(rows: Sequence[Any], out_prefix: str, fmt: str,
               allow_empty: bool, *, blocked: bool, stop_reason: str,
               pages_requested: int, pages_completed: int,
               start_url: str, final_url: str,
               pages_failed: Optional[List[int]] = None,
               mode: str = "listing", source: str = SOURCE_DEFAULT,
               extra: Optional[dict] = None) -> int:
    """Write output + the run-metadata sidecar; return the exit code.

    Shared by all three browser engines so the status/exit-code mapping
    cannot drift between them.

    The metadata sidecar is written ONLY when the row file was written.
    Otherwise a failed run would leave a "status": "failed" sidecar next to
    the previous run's still-intact good output (which `save` deliberately
    does not overwrite) — the two files would contradict each other, and
    diff_runs.py would refuse to compare data that is in fact fine.
    """
    complete = stop_reason in COMPLETE_STOP_REASONS
    row_cls = ROW_CLASS_BY_MODE.get(mode, Product)
    rc = save(rows, out_prefix, fmt, allow_empty=allow_empty, row_cls=row_cls)
    wrote_output = bool(rows) or allow_empty

    if wrote_output:
        status = "complete" if (rows and complete) else (
            "partial" if rows else "failed")
        write_run_meta(out_prefix, run_meta(
            status=status, stop_reason=stop_reason,
            pages_requested=pages_requested, pages_completed=pages_completed,
            pages_failed=pages_failed, mode=mode, source=source,
            start_url=start_url, final_url=final_url, products=len(rows),
            extra=extra))

    if not rows:
        # Nothing gathered at all, and WHY decides the code. Exit 4 is a
        # statement about the CATALOGUE — "the run finished and this listing
        # held nothing" — so it must not be handed to a run that never
        # reached the site.
        #
        # Found by a live run through a proxy the browser could not use:
        # every attempt on page 1 timed out, and the run reported exit 4,
        # which is indistinguishable from an area with no vendors in it.
        # That is §8's "blocked != empty != partial" collapsing at the one
        # point where nothing is written and there is no sidecar to read
        # instead.
        if blocked:
            # A challenge outranks the rest: it says something stood between
            # the run and the content.
            return EXIT_BLOCKED
        if not complete:
            print(f"[!] Nothing was gathered and the run did not finish "
                  f"({stop_reason}) — this is a FAILED run, not an empty "
                  f"listing. No output and no sidecar were written, so the "
                  f"log above is the only record.")
            return EXIT_FETCH_FAILED
        return rc
    if not complete:
        print(f"[!] Partial run: stopped after {pages_completed} of "
              f"{pages_requested} page(s) ({stop_reason}). The output holds "
              f"what was gathered, but it is NOT a complete view — see "
              f"{out_prefix}.meta.json.")
        return EXIT_PARTIAL
    return rc
