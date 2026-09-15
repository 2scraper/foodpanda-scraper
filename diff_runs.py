#!/usr/bin/env python3
"""
diff_runs.py
-------------
Compares two output files from this project (JSON, as written by
output_writer.save) and reports what changed between them, keyed on `sku` —
the identifier the README already tells people to diff on for price
monitoring and assortment tracking, but that nothing in this repo actually
computed.

    python3 diff_runs.py --old watches.2026-09-01.json \\
                          --new watches.2026-09-07.json

Typical use is a scheduled re-run of one of the four scraper engines, kept
under a dated filename, diffed against the previous one:

    python3 playwright_scraper.py --url "$URL" --out "girls_$(date +%F)"
    python3 diff_runs.py --old "girls_$(ls -t girls_*.json | sed -n 2p)" \\
                          --new "girls_$(date +%F).json" --out diff.json

Four buckets, each keyed on sku:

  added          — sku present in --new, absent from --old
  removed        — sku present in --old, absent from --new (delisted, or just
                   off this particular page/area run)
  changed        — sku present in both, with a different rating, review
                   count, deal label, discount, cuisine set or name
  hours_changed  — sku present in both, and the ONLY thing that moved is
                   whether the vendor is open and when it opens next. On this
                   site that is the loudest signal in the file and the least
                   interesting one: a third of the tiles in every measured
                   capture were closed, so two runs a few hours apart
                   disagree about hundreds of rows purely because time
                   passed. Reported separately, and --fail-on-change
                   deliberately ignores it — a monitor that fired on this
                   would fire every night.
  source_changed — always empty here, and emitted anyway so a consumer
                   written against the family's diff shape does not have to
                   branch. It exists for siblings that reconcile a structured
                   price against a rendered one; a foodpanda tile has no
                   price to reconcile (see output_writer's docstring).

A product this project's parser could not recover a sku for (None) cannot be
matched across runs at all, so it is counted and reported separately rather
than silently folded into "added"/"removed", which would be wrong on its face.
"""

import argparse
import json
import pathlib
import re
import sys
from typing import Dict, List, Optional, Tuple

from output_writer import UNIQUE_BY_SKU_MODES

# What is worth watching on a vendor tile, and the two flags that stop the
# numbers beside them from lying.
#
# `review_count_is_floor` is tracked WITH `review_count` and not as an
# afterthought: the site caps the printed figure at "(100+)", "(500+)",
# "(1000+)", so a vendor crossing 100 reviews moves from 99 to a cap, and a
# monitor watching the number alone would report every busy vendor as frozen
# forever and every crossing as a one-off jump. The flag is what makes the
# pair readable.
#
# `title` IS tracked, which this family usually avoids: a vendor renaming
# itself is a real event here ("Slap" -> "Slap - Girja Chowk"), `slug` moves
# with it, and no other column would show it.
#
# `is_open` and `opens_at` are tracked but routed to their own bucket — see
# `hours_changed` in diff_products.
#
# NO price fields, because this site's listing has none: 0 price nodes across
# 1,952 tiles on two country sites (see output_writer's docstring). So
# `PRICE_FIELDS` is empty, `--price-tolerance-pct` is inert, and the
# `within_tolerance` bucket is always empty. All three are kept so the
# family's diff shape and CLI stay identical across repos.
TRACKED_FIELDS = ("rating", "review_count", "review_count_is_floor",
                  "discount_pct", "discount_label", "discount_is_upper_bound",
                  "tags", "free_delivery", "min_order", "category",
                  "is_super_vendor", "title", "slug", "is_open", "opens_at")

# The columns whose comparability depends on the two runs having read the
# same node. Empty here: there is no price and therefore no `price_source`.
PRICE_FIELDS = ()

# The pair that moves with the clock rather than with the catalogue.
HOURS_FIELDS = ("is_open", "opens_at")


def _load(path: str) -> List[dict]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _by_sku(products: List[dict]) -> Tuple[Dict[str, dict], int]:
    indexed = {}
    unmatchable = 0
    for p in products:
        sku = p.get("sku")
        if sku is None:
            unmatchable += 1
            continue
        # A run's own output can already hold a duplicate sku (two rows in the
        # same category, or a rerun of dedupe_by_sku's job on older output
        # written before it existed) — keep the first and count the rest as
        # unmatchable rather than letting one clobber the other silently.
        if sku in indexed:
            unmatchable += 1
            continue
        indexed[sku] = p
    return indexed, unmatchable


def _within_tolerance(before: dict, after: dict, changes: dict,
                      tolerance_pct: float) -> bool:
    """True if every differing price field moved by less than `tolerance_pct`.

    INERT ON THIS SITE, and kept rather than deleted so the family's CLI is
    the same everywhere. `PRICE_FIELDS` is empty because a foodpanda vendor
    tile carries no price at all, so this function has nothing to measure and
    returns False on the first `if` for the default tolerance of zero.

    A sibling repo needs it: that site converts prices for a cross-border
    visitor and the exchange rate ticks between two runs of the same command.
    Nothing equivalent exists here — the only money anywhere near a tile is a
    voucher's minimum spend, which is a fixed condition and not a converted
    amount, and `min_order` is compared exactly.

    A move is judged on the LARGEST relative change among the price fields,
    so a genuine 0.5% cut is not hidden by a 0.04% tolerance applied
    field-by-field.
    """
    if tolerance_pct <= 0:
        return False
    for field in PRICE_FIELDS:
        if field not in changes:
            continue
        was, now = before.get(field), after.get(field)
        if not isinstance(was, (int, float)) or not isinstance(now, (int, float)):
            return False  # a None appearing or disappearing is a real change
        if was == 0:
            return False
        if abs(now - was) / abs(was) * 100.0 > tolerance_pct:
            return False
    return True


def diff_products(old: List[dict], new: List[dict],
                  price_tolerance_pct: float = 0.0) -> dict:
    old_by_sku, old_unmatchable = _by_sku(old)
    new_by_sku, new_unmatchable = _by_sku(new)

    added = [new_by_sku[sku] for sku in new_by_sku.keys() - old_by_sku.keys()]
    removed = [old_by_sku[sku] for sku in old_by_sku.keys() - new_by_sku.keys()]

    changed, source_changed, within_tolerance, lifecycle = [], [], [], []
    hours_changed = []
    for sku in old_by_sku.keys() & new_by_sku.keys():
        before, after = old_by_sku[sku], new_by_sku[sku]
        field_changes = {
            field: {"old": before.get(field), "new": after.get(field)}
            for field in TRACKED_FIELDS
            if before.get(field) != after.get(field)
        }
        if not field_changes:
            continue

        # THE CLOCK MOVED, which is not a change to the listing.
        #
        # `is_open` is a point-in-time reading of whether the vendor is
        # accepting orders, and it is the most volatile column in the schema
        # by a wide margin: 735 of 1,904 tiles on one measured capture and 17
        # of 48 on another were closed, and which ones they are depends
        # entirely on the hour the run happened to execute. Reporting that as
        # a change to the catalogue would drown every real signal — a diff of
        # two runs six hours apart would call a third of the file `changed`
        # and none of it would mean anything.
        #
        # So a row whose ONLY differences are the opening pair goes here
        # instead, and `--fail-on-change` ignores this bucket for the same
        # reason it ignores `source_changed`: it says something about when
        # the two runs ran, not about what the site published. A row where
        # the hours moved AND the rating moved keeps its rating change in
        # `changed`, where it belongs.
        if field_changes and all(f in HOURS_FIELDS for f in field_changes):
            hours_changed.append({
                "sku": sku, "title": after.get("title"),
                "changes": field_changes,
            })
            continue

        # A row whose price_source differs between runs is not comparable
        # on price. UNREACHABLE HERE and kept deliberately: `PRICE_FIELDS` is
        # empty, so the `any(...)` below is always False and this bucket is
        # always empty. It stays because the family's diff shape is part of
        # the output contract (§9) — a consumer that reads `source_changed`
        # on four sibling repos should not have to special-case the fifth —
        # and because a future page kind on this site with a price on it
        # would want exactly this branch rather than a reinvention of it.
        sources = (before.get("price_source"), after.get("price_source"))
        if sources[0] != sources[1] and any(f in field_changes for f in PRICE_FIELDS):
            price_part = {f: v for f, v in field_changes.items() if f in PRICE_FIELDS}
            other_part = {f: v for f, v in field_changes.items() if f not in PRICE_FIELDS}
            source_changed.append({
                "sku": sku, "title": after.get("title"),
                "price_source": {"old": sources[0], "new": sources[1]},
                "changes": price_part,
            })
            field_changes = other_part
            if not field_changes:
                continue

        # There is no lifecycle bucket on this site, and its absence is a
        # measurement rather than an omission. A sibling repo needs one
        # because an auction closing moves `bid_kind` and the amount beside
        # it in one event. The nearest thing here is a vendor opening and
        # closing, which happens twice a day, every day, to most of the
        # catalogue — that is the clock, not a lifecycle, and it has its own
        # bucket above. The `lifecycle` key is still emitted, always empty,
        # so a consumer written against the family's diff shape does not have
        # to branch.
        # An FX tick rather than a real move — see _within_tolerance. Always
        # False here, since `PRICE_FIELDS` is empty.
        if (all(f in PRICE_FIELDS for f in field_changes)
                and _within_tolerance(before, after, field_changes,
                                      price_tolerance_pct)):
            within_tolerance.append({"sku": sku, "title": after.get("title"),
                                     "changes": field_changes})
            continue

        changed.append({"sku": sku, "title": after.get("title"),
                        "changes": field_changes})

    return {
        "added": added,
        "removed": removed,
        "changed": changed,
        "hours_changed": hours_changed,
        "source_changed": source_changed,
        "within_tolerance": within_tolerance,
        "lifecycle": lifecycle,
        "unmatchable_old": old_unmatchable,
        "unmatchable_new": new_unmatchable,
    }


def _print_summary(result: dict) -> None:
    print(f"[+] {len(result['added'])} added, {len(result['removed'])} removed, "
          f"{len(result['changed'])} changed, "
          f"{len(result.get('hours_changed', []))} opened or closed, "
          f"{len(result['source_changed'])} not comparable, "
          f"{len(result.get('within_tolerance', []))} within the price "
          f"tolerance.")
    for p in result["added"]:
        print(f"  + {p.get('sku')}  {p.get('title')}  "
              f"{p.get('rating')} ({p.get('review_count')})")
    for p in result["removed"]:
        print(f"  - {p.get('sku')}  {p.get('title')}  "
              f"{p.get('rating')} ({p.get('review_count')})")
    for c in result["changed"]:
        deltas = ", ".join(f"{f}: {v['old']!r} -> {v['new']!r}" for f, v in c["changes"].items())
        print(f"  ~ {c['sku']}  {c['title']}  {deltas}")
    for c in result.get("hours_changed", []):
        deltas = ", ".join(f"{f}: {v['old']!r} -> {v['new']!r}"
                           for f, v in c["changes"].items())
        print(f"  * {c['sku']}  {c['title']}  {deltas}  "
              f"[the vendor's opening state, which follows the clock rather "
              f"than the listing — not a change to the catalogue]")
    for c in result.get("within_tolerance", []):
        moves = ", ".join(
            f"{f}: {v['old']} -> {v['new']}" for f, v in c["changes"].items())
        print(f"  ~ {c['sku']}  {c['title']}  {moves}  [within --price-"
              f"tolerance-pct]")
    for c in result["source_changed"]:
        src_pair = c["price_source"]
        deltas = ", ".join(f"{f}: {v['old']!r} -> {v['new']!r}" for f, v in c["changes"].items())
        print(f"  ? {c['sku']}  {c['title']}  {deltas}  "
              f"[price_source {src_pair['old']!r} -> {src_pair['new']!r}]")
    unmatchable = result["unmatchable_old"] + result["unmatchable_new"]
    if unmatchable:
        print(f"[!] {unmatchable} row(s) across both files had no sku or a "
              f"duplicate sku, and could not be matched across runs.")


def _run_status(path: str) -> Tuple[Optional[str], Optional[dict]]:
    """Read the `<out>.meta.json` sidecar beside a run's JSON output.

    Returns (status, meta), or (None, None) when there is no sidecar — which
    is the normal case for output written before run metadata existed, or by
    `scraper_api_client.py` (single fetch, no pagination to cut short).
    """
    meta_path = re.sub(r"\.json$", "", path) + ".meta.json"
    try:
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None, None
    return meta.get("status"), meta


def _check_comparable(args) -> bool:
    """Refuse an assortment diff between runs that are not both complete.

    This is the failure mode the sidecar exists for: a run cut short on page
    3 of 10 is missing every product on pages 4-10, and diffing it against
    yesterday's full run reports all of them as `removed` — reading as "these
    products were delisted" when in fact they were simply never fetched.
    Prices of the SKUs both runs DID see are still comparable, which is why
    this is a refusal with a --force escape hatch rather than a hard error.
    """
    problems = []
    modes = {}
    for label, path in (("--old", args.old), ("--new", args.new)):
        status, meta = _run_status(path)
        if status is None:
            continue  # no sidecar: nothing to check, see _run_status
        mode = (meta or {}).get("mode")
        if mode:
            modes[label] = mode
        if mode and mode not in UNIQUE_BY_SKU_MODES:
            # This tool's whole premise is one row per `sku`, diffed on
            # price. A mode that produces many rows per sku would give a diff
            # whose every line is an artefact of two rows sharing an id, so
            # it is refused outright rather than answered. Both of this
            # repo's current modes qualify; the check is here so that adding
            # one that does not is caught rather than discovered.
            problems.append(
                f"{label} ({path}) is a {mode!r} run, which is not one row "
                f"per sku. This tool diffs one row per sku on price, so there "
                f"is nothing here it can compare.")
        if status != "complete":
            problems.append(
                f"{label} ({path}) was a {status!r} run — stopped after "
                f"{meta.get('pages_completed')} of {meta.get('pages_requested')} "
                f"page(s), reason {meta.get('stop_reason')!r}")
    if len(set(modes.values())) > 1:
        problems.append(
            f"the two runs are different modes ({modes}). A listing row and a "
            f"detail row carry different fields, so `added`/`removed` would "
            f"describe the mode change rather than the catalogue.")

    # TWO COUNTRY SITES, which on this site is worse than incomparable —
    # it is actively misleading, and the reason is the id space.
    #
    # A vendor code is four characters of the site's own alphabet and it is
    # scoped PER COUNTRY: `aah1` is a Hakka thunder-tea stall in Toa Payoh on
    # foodpanda.sg, and there is nothing stopping foodpanda.pk from issuing
    # the same four characters to a biryani house in Lahore. Diffing one
    # country against another would therefore not merely report every row as
    # added and removed — it would silently MATCH the rows whose codes
    # collide and report a Singaporean noodle shop as having renamed itself
    # and changed cuisine.
    #
    # `source` is the column that says which country site a row came from,
    # and a run holding more than one of them is a run that was redirected
    # mid-way.
    sources = {}
    for label, path in (("--old", args.old), ("--new", args.new)):
        try:
            rows = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        country_sites = {r.get("source") for r in rows if r.get("source")}
        if len(country_sites) == 1:
            sources[label] = country_sites.pop()
        elif len(country_sites) > 1:
            problems.append(
                f"{label} ({path}) holds rows from more than one country site "
                f"({sorted(country_sites)}) — that run was redirected "
                f"mid-way, and vendor codes are scoped per country, so its "
                f"own rows are not safely keyed against each other.")
    if len(set(sources.values())) > 1:
        problems.append(
            f"the two runs are different country sites ({sources}). foodpanda "
            f"issues vendor codes per country, so two rows can share a `sku` "
            f"and be unrelated vendors — a diff across country sites would "
            f"match them and report the difference as a change.")

    if not problems:
        return True

    # A generic headline, because the reasons below are no longer only about
    # completeness: a mode mismatch and a reviews run are refused too, and a
    # message naming the wrong reason sends the reader looking in the wrong
    # place.
    print("[!] Refusing to diff these two runs:")
    for line in problems:
        print(f"      {line}")
    print("    Re-run the incomplete side, or pass --force to compare anyway "
          "(added/removed will include products that were simply never "
          "fetched).")
    return False


def parse_args():
    p = argparse.ArgumentParser(
        description="Diff two foodpanda-scraper JSON outputs by sku.")
    p.add_argument("--old", required=True, help="Earlier run's JSON output.")
    p.add_argument("--new", required=True, help="Later run's JSON output.")
    p.add_argument("--out", default=None,
                   help="Write the full diff as JSON to this path too.")
    p.add_argument("--price-tolerance-pct", type=float, default=0.0,
                   metavar="PCT",
                   help="Treat a price move smaller than PCT%% as an exchange-"
                        "rate tick rather than a price change: reported "
                        "separately and ignored by --fail-on-change. INERT ON "
                        "THIS SITE — a foodpanda vendor tile carries no price, "
                        "so there is nothing for a tolerance to absorb. The "
                        "flag is inherited from this scraper family and kept "
                        "so one command line works across all of it.")
    p.add_argument("--fail-on-change", action="store_true",
                   help="Exit 1 if anything was added, removed or changed — "
                        "for a cron job that should only notify on a real diff.")
    p.add_argument("--force", action="store_true",
                   help="Diff even when a run's .meta.json says it was partial "
                        "or failed. Products never fetched by the short run will "
                        "appear as added/removed.")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    if not args.force and not _check_comparable(args):
        return 2

    try:
        old = _load(args.old)
        new = _load(args.new)
    except (OSError, json.JSONDecodeError) as e:
        print(f"[!] Could not read one of the input files: {e}")
        return 2

    result = diff_products(old, new, price_tolerance_pct=args.price_tolerance_pct)
    _print_summary(result)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"[+] Full diff written to {args.out}")

    # `hours_changed`, `source_changed` and `within_tolerance` are NOT
    # reasons to fail. The first means the clock moved — on this site that is
    # most of the file most nights; the second means our own two snapshots
    # rendered differently; the third means a tolerance absorbed a move. None
    # of the three says anything about the catalogue, and alerting on any of
    # them would train whoever reads the alert to ignore it.
    if args.fail_on_change and (result["added"] or result["removed"] or result["changed"]):
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(1)
