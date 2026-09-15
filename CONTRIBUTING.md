# Contributing

Bug reports, site-change reports and pull requests are all welcome. This file
covers the few things specific to a scraper, which are not the usual ones.

## Before you open anything

Run the offline suite. It needs no network, no browser and no API key, and takes
about a second:

```bash
pip install -r requirements.txt
python3 smoke_test.py
```

It prints its own check count, and lists any group it had to skip because an
engine library is absent.

**The suite must pass with no engine installed at all.** CI installs only
`beautifulsoup4` and `requests`, so any import of `playwright_scraper`,
`puppeteer_scraper` or `selenium_scraper` in a test has to sit inside
`try/except ImportError` with the skip recorded. This is easy to get wrong
locally, where you almost certainly have an engine installed and an unguarded
import passes.

If the suite fails on a clean clone, that is itself the bug — say so.

## Never commit a credential

`.env` is in `.gitignore`. Keep it there.

The scrapers mask `user:pass@` in their own log lines, but three things are **not**
masked: raw HTML dumps, the Scraper API's `x-debug` response header, and your
shell history. Before pasting any output into an issue or a PR, replace keys,
proxy passwords and full `ws://user:pass@host:9222` endpoints with `***`.

CI fails the build if something that looks like a credential is committed. That
check is a backstop, not a review — a leaked key has to be rotated whether or
not the check caught it.

## Reporting a site change

foodpanda changing its markup is the normal way this stops working, and it
has its own issue template. The detail that saves the most time is WHICH
anchor broke, because on this site there is no usable structured data on a
listing page to fall back on — the one `CollectionPage` JSON-LD block holds
SIX names on a page of forty-eight, so it is a cross-check rather than a
path.

1. **The tile.** `li[class*="bds-c-vendor-tile"]`, whose `data-testid` IS the
   vendor code. If this moves the run reports 0 rows and exit 4, which is
   loud.
2. **The link.** `a[href^="/restaurant/"]` — the fallback anchor, and the one
   §4 calls a contract with search engines. A tile with no link at all does
   exist (1 of 1,904 measured), which is why the code is read from the `<li>`.
3. **The info rows**, `[data-testid="bds-c-vendor-tile__info-row-text"]`, and
   the screen-reader labels beside them. These are read by MEANING rather
   than by position, because a home-page tile carries five of them and a city
   tile carries one — reading the first as the cuisines is a bug this repo
   already shipped and fixed.
4. **The rating block**, `[data-testid="review-and-rating"]`, whose two spans
   are the value and the parenthesised count in that order.
5. **The page separators**, `[data-testid="pageNumber"]`, which carry the
   page number in their `id` and are how a scrolled document attributes a
   tile to a page.

There is no detail-page path to fall back on: a `/restaurant/{code}/{slug}`
page is refused to this repo on every attempt (see the README), so no mode
reads one.

A second thing can break without any anchor failing: the **page attribution**.
`position` restarts at 1 on every page, so if the page number stops reaching
the parser the rows still look healthy while two of them silently claim the
same place in the listing. Every run checks that `(page, position)` is unique
across its output and says so loudly if it is not, and the canary asserts the
same thing.

A third: the **info-row classification**. Those rows are read by meaning, so a
change in how the site labels them shows up as cuisines full of delivery
times rather than as an empty column. If you are reporting a change, include
what `category` and `cuisines` actually contain.

`--dump-html PATH` writes the exact bytes the parser was given, on success as
well as failure, and a run that finds nothing writes a dump and a screenshot
next to the output on its own.

## Before this repository goes public

One item cannot be undone later, so it belongs on a checklist rather than in
someone's head. **A commit on top cannot reach what a published tag and a
merged PR's refs already hold** — those stay attached to the PR and cannot be
deleted from it. Afterwards, only a fresh repository removes anything.

```bash
python3 .github/ci_checks.py --history-check
```

That applies the same credential rules CI enforces to **every blob that has
ever existed**, not just the working tree. It is deliberately not part of
`--all` and not run by CI: it shells out to git once per object, and a dirty
history needs a decision, not a red check on every push.

Then the rest of the presentation, in the order that matters:

1. `python3 smoke_test.py` green, and the canary dispatched at least once —
   including its SKIP branch, which is what runs when no `FOODPANDA_PROXY`
   secret is set. This canary needs a secret to do real work: foodpanda
   refuses a large share of cold navigations even from a residential address
   (10 of 30 served, measured 2026-09-15), and a GitHub runner's datacentre
   address is a worse starting position that this repo has NOT measured. So
   without the secret the job goes green with a `::notice::` saying in words
   that nothing was tested — a check that is always red teaches everyone to
   ignore checks. With the secret, a block is a failure, because then it
   means something.
2. The repo description, homepage and topics set (see the family notes on
   what those should say).
3. Only then the row in the org profile README — and check it with an
   ANONYMOUS request rather than your own logged-in browser. A row pointing
   at a private repo is a 404 for every visitor, which costs more trust than
   the missing row.

## Pull requests

**Add a test for the behaviour you are changing.** `smoke_test.py` is a single
file of plain functions with inline HTML/JSON fixtures — no pytest, no
conftest, no fixtures directory. Copy the nearest existing check and edit it.

Six properties in this repo exist because they were once absent and cost
real time. Tests pin all six, so a PR that breaks one will fail rather than
silently regress:

- **A refused page is not an exhausted listing.** This repo's first live run
  reported `status: complete` and exit 0 while holding a third of the
  catalogue, because a page refused on every attempt fell through to the
  parse, produced 0 rows, and the loop read that as "no new sku". Any state
  that must not be parsed, other than `empty`, is a FAILED page.

- **The challenge is reCAPTCHA Enterprise.** PerimeterX's denial page renders
  a v2-shaped `g-recaptcha` container, and the loader beside it is
  `recaptcha/enterprise.js` — measured on four denial documents, with zero
  occurrences of `recaptcha/api.js` on any of them. Reading the container
  alone called it solvable, which would have paid for a token the site
  rejects. `detect_bot_challenge` returns None for those pages, deliberately.

- **A marker that matches every page is worse than no marker.** foodpanda
  ships `window._pxAppId` and the string "reCAPTCHA" on EVERY page it serves —
  the sensor bootstrap and the i18n bundle. Either as a marker would report
  the whole catalogue as blocked. A test asserts both are on every served
  fixture AND that neither is in any marker set.

- **The info rows are read by meaning, not by position.** A home-page tile
  carries five of them and a city tile carries one, so taking the first as the
  cuisines put "From 25 min" in the `category` column of every home-page row —
  100% populated and entirely wrong.

- **A capped count is not a count.** The site prints `(100+)`, `(500+)`,
  `(1000+)`, so `review_count_is_floor` rides beside `review_count`. Without
  it a monitor reports every busy vendor as frozen at exactly 100 forever.
  The same shape applies to `discount_is_upper_bound`: "Up to 35% off" is a
  ceiling, and 21% of measured labels are that shape.

- **The clock is not the catalogue.** `is_open` is a point-in-time reading and
  a third of the tiles flip between any two runs, so `diff_runs.py` routes
  rows whose only change is the opening pair to `hours_changed` and
  `--fail-on-change` ignores it.

### Style

- **Match the file you are editing.** No formatter is enforced.
- **Comments explain *why*.** What the code does is visible; why it does it that
  way, especially where the obvious version is wrong, is not.
- **A timeout on every remote call.** Every browser library used here has needed
  an explicit timeout its own API does not provide, and each has needed its own
  route out of the runtime — reporting a timeout is not the same as exiting on
  one. If you add a call to a remote browser or API, bound it.
- **Fail loudly.** A function that returns an empty list on error, or logs
  success without checking that the thing it wanted actually happened, is the
  single most common bug class in this codebase's history. A selector that
  matches the *wrong* element is worse than one that matches nothing, because
  the second one tells you.

### If your change needs a live run

Most do not — the suite covers the parser, the writers, the captcha classifier
and the CLI contract against inline fixtures. If yours genuinely needs
a live site, say in the PR what you ran, which URL and page kind, from
which exit, and what you got — including the name, image and rating coverage
percentages the run prints, and the status and `pages_failed` from the
sidecar.

Note what is NOT a finding on this site: a single refused fetch. The refusal
is per-session and probabilistic — 10 of 30 navigations served from one
residential exit — so "it was blocked" from one attempt says nothing. Give it
`--retries` with a real `--retry-delay`, and say how many attempts each page
took. A three-page run that needed five attempts on page 3 and then returned
143 vendors is a normal good result here.

**Run more than the primary engine.** "Mirror them exactly" is a design rule,
not a verification: the first live run of the pyppeteer engine crashed on its
FIRST fetch on a signature mismatch that four separate offline checks and 400
green assertions had not caught.

Do not add anything that submits the registration form. This project
deliberately never does, and a captcha token proved valid by creating a real
account is not a result worth having.

## Scope

This repo scrapes **public pages** on foodpanda: the country home page and
the city and area vendor listings, exactly as an anonymous visitor is served
them.
Out of scope: anything behind a login, anything that submits a form, and
anything that defeats a protection rather than passing it the way an ordinary
browser does.

## Licence

MIT. By opening a pull request you agree your contribution ships under it.
