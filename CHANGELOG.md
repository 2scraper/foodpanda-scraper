# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the versions follow [Semantic Versioning](https://semver.org/) as closely
as a command-line tool can. **A patch release means "fixes", not "no flag ever
changes"**: where a default changes behaviour for an existing user, the
release notes lead with it in a blockquote, so nobody discovers it from a bill
or from a half-empty output file.

---

## [0.1.0] — 2026-09-15

The first release on this scraper family's architecture. It replaces the
April 2026 prototype entirely: three monolithic scripts with their own copies
of everything become a parser, a page policy, a shared output contract and
three engines that must agree.

> **If you used the prototype**, nothing about the command line or the output
> carries over. The row schema, the exit codes and the flags are the family's
> now, and the prototype's `foodpanda_*.json` files are not comparable with
> this one's.

### Added

* **`product_parser.py`** — everything this project knows about foodpanda's
  markup, in one file. The primary anchor is the site's own `data-testid`,
  which carries the vendor code, with the `/restaurant/{code}/{slug}` URL
  pattern as the fallback. There is deliberately no JSON-LD path: the one
  `CollectionPage` block on a listing holds SIX names on a page of
  forty-eight, so building on it would return an eighth of the page and
  report success.
* **`page_flow.py`** — the retry/solve/blocked decision as DATA, so three
  engines cannot quietly disagree about whether a page is worth retrying or
  worth paying for.
* **Three engines** — Playwright (primary), pyppeteer and Selenium, agreeing
  on exit codes, run status, flags and named constants, with the offline
  suite asserting that they do.
* **Eleven country sites**, ten of them verified live on 2026-09-15:
  foodpanda.pk, .sg, .my, .ph, .com.tw, .hk, .com.bd, .com.kh, .la and
  .com.mm. foodpanda.co.th is supported and was refused on all three probe
  attempts in that window, so it is listed as unverified rather than as
  working.
* **Real pagination.** The site publishes `?page=N` links between batches
  itself, and a cold fetch of `?page=2` returned 48 tiles sharing no vendor
  code with page 1 — so pages are fetched independently and `--concurrency`
  above 1 is genuinely available on the `/city` tree.
* **`diff_runs.py`** with an `hours_changed` bucket: a vendor opening or
  closing follows the clock rather than the catalogue, and on this site that
  moves a third of the rows between any two runs.
* **`make_fixtures.py`** — cuts the offline suite's fixtures out of real
  captures and refuses to write unless each one parses identically to its
  untrimmed original, column for column.
* **Over 400 offline checks**, a Docker image built and run in CI, and a
  daily canary that SKIPS with a notice rather than failing when no proxy
  secret is set.

### Fixed — found by running it against the live site

Three defects that no amount of reading found, and one inherited from the
family core. Every one of them was invisible to a green offline suite.

* **A refused page was reported as an exhausted listing.** Page 2 of a
  three-page run was refused on every attempt, fell through to the parse,
  produced 0 rows, and the run loop read that as "this page added no sku we
  had not already seen" — the end of the catalogue. The run reported
  `status: complete`, `stop_reason: no_new_products`, `pages_failed: []` and
  exit 0 while holding 48 of roughly 144 rows. A refused page is now a FAILED
  page: the run exits 6 and names the page number.
* **The challenge is reCAPTCHA Enterprise, and unsolvable here.**
  PerimeterX's denial page renders a v2-shaped `g-recaptcha` container, and
  on that evidence this repo first called it a solvable `challenge`. The
  loader beside it is `recaptcha/enterprise.js` — measured on four denial
  documents across two country sites, with zero occurrences of
  `recaptcha/api.js` on any of them. A solve would have been charged for and
  would have bought a token the site rejects. Such a page is now `blocked`,
  and nothing is attempted or billed.
* **The retry that could not work.** Without a proxy pool the inherited loop
  re-fetched through the SAME browser session. On this site the refusal is a
  property of the session, so that re-confirms it: 0 of 4 same-session
  re-fetches were served, against a page that came back with 48 rows on the
  second attempt once the browser was relaunched. A local run now relaunches
  between block attempts; a `--cdp-endpoint` run still does not, because a
  Scraping Browser profile allows one live connection.
* **The two secondary engines crashed on a blocked page with no proxy.**
  Inherited verbatim: the block-retry branch read `pool.current` with no null
  check, while `pool` is None whenever neither `--proxy` nor `--proxy-file`
  was given — and the retry budget is non-zero in exactly that case. The
  first refused page of any pool-less run would have died with
  `AttributeError` instead of reporting exit 3.

### Fixed — found while pinning fixture values

* **Every home-page row had the delivery time in its `category` column.** A
  home-page tile carries FIVE info rows where a city-listing tile carries
  one, and the parser took the first. The rows are now read by what they ARE
  — the site labels each one for screen readers — which also turned three of
  them into real columns: `delivery_time`, `delivery_fee` with its
  `currency`, and `price_level`. They are the only money a foodpanda tile
  ever carries.

### Known limitations, stated rather than worked around

* **No vendor-page mode.** A `/restaurant/{code}/{slug}` page is where a
  menu, a delivery fee and a minimum order live, and it could not be
  captured: eight cold navigations and one click-through from a listing the
  same browser had just been served all came back HTTP 403, on two country
  sites. A parser written against markup nobody has seen is a guess with a
  docstring, so the mode is absent rather than broken. Such a URL is refused
  with that reason.
* **The 2Captcha paths are NOT live-verified.** No funded key was available
  while this was written, so the solver, the Scraping Browser endpoint and
  the fingerprint client are implemented and exercised offline and have never
  been run end to end against this site. This is stated here, in the README
  and in `.env.example` rather than left for a reader to discover from a bill.
  Note separately that the solver would not help against the enterprise
  widget anyway — see above.
* **foodpanda.co.th is unverified.** It is in the supported host list on the
  brand's own country footprint and was refused on all three probe attempts
  in the measurement window.

[0.1.0]: https://github.com/2scraper/foodpanda-scraper/releases/tag/v0.1.0
