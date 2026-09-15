"""Cut the offline suite's fixtures out of real captures, and PROVE they
parse the same.

Its output is `fixtures_generated.json`, which `smoke_test.py` loads. This
script is shipped because two files point at it — `smoke_test.py`'s own
docstring and TROUBLESHOOTING.md — and an instruction pointing at a file that
does not exist is worse than no instruction.

WHAT YOU NEED TO RUN IT
-----------------------
Your own captures, in `../../captures/` relative to the repo, named as
`SOURCES` below expects. They are deliberately NOT in the repository: a
single listing capture of this site is 1.0 to 1.7 MB, and a scrolled one is
16 MB.

Take them with a real browser — `--dump-html` on any engine writes exactly
the bytes the parser was given — and remember that this site refuses a
headless window far more often than a headful one, so pass `--headful`.

WHAT IT ENFORCES, and why each rule is here
-------------------------------------------
  * every fixture is CUT from a real capture, never hand-written. §18's
    lesson in this repo's shape: the one marker set in this family that WAS
    written from imagination matched none of the real strings, and an empty
    page came back as `shell` and spent a 25-second readiness wait on an
    answer the site had already given;
  * each one is verified to parse IDENTICALLY to the untrimmed original for
    the tiles it keeps — every column, not just a count;
  * the refusal fixtures' reCAPTCHA site key is replaced with an obvious
    placeholder BEFORE anything is written, and guarded by a PATTERN rather
    than by the literal one capture happened to contain, so the next capture
    is caught too. That key is PerimeterX's own public one rather than
    anybody's secret, but a 40-character base64-ish string in a public repo
    reads as a live credential to every scanner that looks, including this
    repo's own CI grep (§10).
"""
import json
import os
import pathlib
import re
import sys
from dataclasses import asdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from bs4 import BeautifulSoup  # noqa: E402

import product_parser as P  # noqa: E402

HERE = pathlib.Path(__file__).parent
# ../../captures relative to a repo checked out at <site>/repo/<repo-name>.
CAPTURES = pathlib.Path(
    os.environ.get("FOODPANDA_CAPTURES", HERE.parent.parent / "captures"))
OUT = HERE / "fixtures_generated.json"

# name -> (capture file, the URL it was fetched from, how many tiles to keep)
#
# Four listing fixtures and not one, because §15's "run a SECOND country
# site" applies to fixtures too: every bug this repo found that a single
# locale could not show came from comparing two. `pk` and `sg` differ in
# language, currency inside a deal label ("Rs. 300" against "S$ 15") and in
# the CASE of the free-delivery label, which a case-sensitive match got
# wrong.
SOURCES = {
    "LISTING_PK_CITY":  ("fp_pk_city_lahore.html",
                         "https://www.foodpanda.pk/city/lahore", 6),
    "LISTING_PK_AREA2": ("fp_pk_area_gulberg_p2.html",
                         "https://www.foodpanda.pk/city/lahore/area/gulberg?page=2", 5),
    "LISTING_SG_CITY":  ("fp_sg_city_singapore.html",
                         "https://www.foodpanda.sg/city/singapore", 6),
    "LISTING_PK_HOME":  ("fp_pk_home.html",
                         "https://www.foodpanda.pk/", 4),
}

# A page the site serves that holds NO vendor tiles: the /city directory. It
# is the case §17's classification-order trap is about — built out of the
# site's own assets, so a threshold-first classifier calls it blocked.
INDEX_SOURCES = {
    "INDEX_PK_CITY": ("fp_pk_city_index.html", "https://www.foodpanda.pk/city"),
    "INDEX_PK_AREA": ("fp_pk_area_index.html",
                      "https://www.foodpanda.pk/city/lahore/area"),
}

# The three refusals, which are three different documents and must not be
# collapsed into one fixture:
#   px_recaptcha  PerimeterX's denial page WITH a reCAPTCHA Enterprise widget
#   px_plain      PerimeterX's denial page with NO widget at all
#   cloudflare    Cloudflare's managed challenge, which is what a non-browser
#                 client gets before it ever reaches the application
BLOCK_SOURCES = {
    "BLOCK_PX_RECAPTCHA": ("fp_block_px_recaptcha.html",
                           "https://www.foodpanda.pk/restaurant/pedb/slap-girja-chowk"),
    "BLOCK_PX_RECAPTCHA_SG": ("fp_block_px_recaptcha_sg.html",
                              "https://www.foodpanda.sg/city"),
    "BLOCK_PX_PLAIN": ("fp_block_px_plain.html",
                       "https://www.foodpanda.pk/city/lahore/area/gulberg?page=2"),
    "BLOCK_CLOUDFLARE": ("fp_block_cloudflare.html",
                         "https://www.foodpanda.com/"),
}

# The scrolled capture, kept for ONE purpose: it is the only document that
# carries the site's own `<span data-testid="pageNumber" id="N">` separators,
# which is how `parse_products` attributes a tile to a page when many pages
# arrive in one document. Trimmed hard — the original is 16 MB.
SCROLLED_SOURCE = ("fp_pk_area_gulberg_scrolled.html",
                   "https://www.foodpanda.pk/city/lahore/area/gulberg")

# The site key PerimeterX's denial page renders. Scrubbed by PATTERN, so a
# future capture with a different key is caught too (§10).
_SITEKEY_RE = re.compile(r'(data-sitekey=")[^"]{20,}(")')
_SITEKEY_PLACEHOLDER = r"\1SITEKEY-SCRUBBED-BY-make_fixtures\2"
# PerimeterX stamps a per-request uuid/vid into its denial document. Neither
# is a credential and both identify the exact request that was refused, so
# they are replaced for the same reason.
_UUID_RE = re.compile(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})")
_UUID_PLACEHOLDER = "00000000-0000-0000-0000-000000000000"


def scrub(html: str) -> str:
    html = _SITEKEY_RE.sub(_SITEKEY_PLACEHOLDER, html)
    return _UUID_RE.sub(_UUID_PLACEHOLDER, html)


def read(name: str) -> str:
    path = CAPTURES / name
    if not path.is_file():
        sys.exit(f"missing capture: {path}\n"
                 f"See this file's docstring for how to take one.")
    return path.read_text(encoding="utf-8", errors="replace")


# A vendor tile in the RAW string. Matched on the raw bytes rather than
# through BeautifulSoup because the fixture has to BE the real markup: `str()`
# on a parsed node re-serialises it — entities normalised, attribute order
# settled — and a re-serialised document is a different document that happens
# to look similar.
_TILE_OPEN_RE = re.compile(r"<li\b[^>]*class=\"[^\"]*bds-c-vendor-tile[^\"]*\"[^>]*>")


def trim_tiles(html: str, keep: int) -> str:
    """Keep the first `keep` vendor tiles and the page's head, drop the rest.

    Whitespace and attribute order are preserved verbatim, because the point
    of a fixture cut from a real capture is that it IS the real markup.
    """
    opens = [m.start() for m in _TILE_OPEN_RE.finditer(html)]
    if len(opens) <= keep:
        return html
    start = opens[0]
    # The end of the last kept tile: the `</li>` that closes it. Tiles do not
    # nest on this site — a tile is a leaf `<li>` in one `<ul>` — so the first
    # `</li>` after the last kept tile's opening tag is its own.
    end = html.find("</li>", opens[keep - 1])
    if start < 0 or end <= start:
        sys.exit("could not locate the tiles in the original string")
    end += len("</li>")
    head_end = html.find("</head>")
    head = html[:head_end + 7] if head_end > 0 else ""
    return head + "<body><ul>" + html[start:end] + "</ul></body></html>"


# What a slimmed `<head>` must keep. Each entry is evidence some assertion in
# the offline suite depends on; everything else in a 145 KB inline bundle is
# not evidence about anything.
_KEEP_IN_SCRIPT = (
    "application/ld+json",     # jsonld_vendor_names reads these
    "_pxAppId",                # the §18 trap: on EVERY served page
    "PERIMETERX_APP_ID",
    "Captcha_Modal_Warning",   # the i18n bundle's reCAPTCHA strings, ditto
    "images.deliveryhero.io",  # the positive asset signal
    "dhmedia.io",
)
_SCRIPT_RE = re.compile(r"<script\b[^>]*>.*?</script>", re.S | re.I)
_STYLE_RE = re.compile(r"<style\b[^>]*>.*?</style>", re.S | re.I)


def slim(html: str) -> str:
    """Drop script and style BODIES that carry no evidence, keeping the tags.

    The tags stay so the document's shape is unchanged; only the bytes nobody
    reads go. `slim` is applied to LISTING fixtures only — a refusal document
    is a few kilobytes and is kept verbatim, because its script bodies are
    exactly what the block markers are matched against.
    """
    def keep_script(match):
        body = match.group(0)
        if any(marker in body for marker in _KEEP_IN_SCRIPT):
            return body
        opening = body[:body.find(">") + 1]
        return opening + "/* body dropped by make_fixtures.slim */</script>"

    html = _SCRIPT_RE.sub(keep_script, html)
    return _STYLE_RE.sub(
        lambda m: m.group(0)[:m.group(0).find(">") + 1]
        + "/* dropped */</style>", html)


def parses_identically(original: str, trimmed: str, url: str, keep: int) -> bool:
    """Every column of every kept row must match the untrimmed original."""
    before = [asdict(r) for r in P.parse_products(original, url)][:keep]
    after = [asdict(r) for r in P.parse_products(trimmed, url)]
    if len(after) != len(before):
        print(f"    row count differs: {len(before)} -> {len(after)}")
        return False
    for a, b in zip(before, after):
        a.pop("scraped_at", None)
        b.pop("scraped_at", None)
        if a != b:
            diff = {k: (a.get(k), b.get(k)) for k in set(a) | set(b)
                    if a.get(k) != b.get(k)}
            print(f"    row {a.get('sku')} differs: {diff}")
            return False
    return True


def main() -> int:
    fixtures = {}
    failures = 0

    for name, (filename, url, keep) in SOURCES.items():
        original = read(filename)
        trimmed = slim(trim_tiles(original, keep))
        # The state must survive the slimming too: `detect_page_state` counts
        # the site's own asset references, and a slim that removed them would
        # turn a served page into a "blocked" one in every test that uses it.
        state = P.detect_page_state(trimmed, 200, url)
        if state != "content":
            print(f"[!] {name}: slimmed fixture classifies as {state!r}, not "
                  f"'content'")
            failures += 1
            continue
        if not parses_identically(original, trimmed, url, keep):
            print(f"[!] {name}: trimmed fixture does not parse identically")
            failures += 1
            continue
        fixtures[name] = {"html": scrub(trimmed), "url": url,
                          "tiles_in_original": len(
                              BeautifulSoup(original, "html.parser").select(
                                  P.SELECTORS["item_card"])),
                          "capture": filename}
        print(f"[+] {name}: {keep} tile(s), {len(trimmed)} bytes "
              f"(from {len(original)})")

    for name, (filename, url) in INDEX_SOURCES.items():
        original = read(filename)
        head_end = original.find("</head>")
        trimmed = slim(original[:head_end + 7] + "<body></body></html>"
                       if head_end > 0 else original[:4000])
        fixtures[name] = {"html": scrub(trimmed), "url": url,
                          "capture": filename}
        print(f"[+] {name}: index page head, {len(trimmed)} bytes")

    for name, (filename, url) in BLOCK_SOURCES.items():
        original = read(filename)
        fixtures[name] = {"html": scrub(original), "url": url,
                          "capture": filename}
        print(f"[+] {name}: {len(original)} bytes (verbatim, scrubbed)")

    # The scrolled capture, rebuilt rather than sliced: the original is 16 MB
    # and holds 1,904 tiles across 24 page separators, and the only thing the
    # page-attribution check needs is a few tiles on either side of a few
    # separators. So keep THREE tiles per segment for the first four
    # segments, with the site's own separators verbatim between them. Every
    # byte is still the site's; only the quantity changes.
    filename, url = SCROLLED_SOURCE
    original = read(filename)
    seps = list(re.finditer(
        r'<span class="page-number"[^>]*data-testid="pageNumber"[^>]*>.*?</span>',
        original, re.S))
    tiles = [m.start() for m in _TILE_OPEN_RE.finditer(original)]
    if len(seps) >= 3 and tiles:
        head_end = original.find("</head>")
        head = original[:head_end + 7] if head_end > 0 else ""
        boundaries = [0] + [m.start() for m in seps] + [len(original)]
        parts = []
        for index in range(min(4, len(boundaries) - 1)):
            lo, hi = boundaries[index], boundaries[index + 1]
            for tile_start in [t for t in tiles if lo <= t < hi][:3]:
                closing = original.find("</li>", tile_start)
                parts.append(original[tile_start:closing + 5])
            if index < len(seps):
                parts.append(seps[index].group(0))
        trimmed = slim(head + "<body><ul>" + "".join(parts)
                       + "</ul></body></html>")
        rows = P.parse_products(trimmed, url)
        pages = sorted({r.page for r in rows})
        fixtures["SCROLLED_PK_AREA"] = {
            "html": scrub(trimmed), "url": url, "capture": filename,
            "pages_present": pages,
            "separators_in_original": len(seps),
            "tiles_in_original": len(tiles)}
        print(f"[+] SCROLLED_PK_AREA: {len(rows)} tiles across pages {pages}, "
              f"{len(trimmed)} bytes (from {len(original)}; the original has "
              f"{len(tiles)} tiles and {len(seps)} separators)")
    else:
        print("[!] SCROLLED_PK_AREA: no page separators found in the capture")
        failures += 1

    # A last, cheap guard: nothing that looks like a live credential may
    # reach the file (§10). Checked on the SERIALISED output rather than per
    # fixture, so a key hiding in a field this script did not scrub is caught
    # too.
    blob = json.dumps(fixtures, ensure_ascii=False)
    for pattern, what in ((r'data-sitekey="(?!SITEKEY-SCRUBBED)[^"]{20,}"',
                           "an unscrubbed reCAPTCHA site key"),
                          (r"\b[0-9a-f]{32}\b", "a 32-hex string"),
                          (r"://[^/\s\"']+:[^/\s\"'@]+@", "a credentialled URL")):
        hit = re.search(pattern, blob)
        if hit:
            print(f"[!] refusing to write: {what} at {hit.group(0)[:40]!r}")
            failures += 1

    if failures:
        print(f"\n{failures} problem(s) — nothing written.")
        return 1

    OUT.write_text(json.dumps(fixtures, indent=1, ensure_ascii=False),
                   encoding="utf-8")
    print(f"\nWrote {OUT} ({OUT.stat().st_size} bytes, "
          f"{len(fixtures)} fixtures).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
