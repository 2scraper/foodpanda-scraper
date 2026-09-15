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
* **Ten country sites, every one verified live** on 2026-09-15:
  foodpanda.pk, .sg, .my, .ph, .com.tw, .hk, .com.bd, .com.kh, .la and
  .com.mm.
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
  dispatch-only canary that skips with a notice when no secret is set. It
  has no schedule and no badge: this repo runs nothing live on a timer, and a
  green badge for a check that is not happening is as unreadable as one that
  is always red.

### Fixed — found by running it against the live site

Five defects that no amount of reading found, two of them inherited from the
family core. Every one was invisible to a green offline suite, and two were
found only by running the SECONDARY engines rather than just the primary one
(§16).

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
* **The two secondary engines retried a block with no pause at all.** The
  Playwright engine waits `--retry-delay`, doubling; the other two relaunched
  and went straight back, so a five-attempt run finished in fourteen seconds
  and was refused every time. On a site whose refusal rate is a direct
  function of request rate, that spent the entire budget in the one window
  where it could not work — and it broke the family's "all three engines must
  agree" rule in the place it matters most.
* **The pyppeteer engine is refused with its own browser, and that is the
  browser rather than the driver.** Measured on one URL in one window, arms
  alternating: pyppeteer driving Playwright's Chromium 148 was served,
  pyppeteer driving its own bundled Chromium 117 was refused, and Playwright
  was served. pyppeteer pins a Chromium that is three years stale.
  `--chromium-path` is the answer, and the requirements file, the README and
  TROUBLESHOOTING all say so now. Removing pyppeteer's automation flags does
  not help — it is the version.

### Fixed — found while pinning fixture values

* **Every home-page row had the delivery time in its `category` column.** A
  home-page tile carries FIVE info rows where a city-listing tile carries
  one, and the parser took the first. The rows are now read by what they ARE
  — the site labels each one for screen readers — which also turned three of
  them into real columns: `delivery_time`, `delivery_fee` with its
  `currency`, and `price_level`. They are the only money a foodpanda tile
  ever carries.

### Verified live, 2026-09-15

* **Scraping Browser (`--cdp-endpoint`)** — three pages of a real area
  listing, 144 vendors, `status: complete`, **zero refusals and zero
  retries in 29 seconds**. The same three pages from an unproxied residential
  address needed five attempts on page 3 and about seven minutes.
* **Fingerprint client** — fingerprint 5581439 (PK) fetched and applied: the
  user agent out of `userAgent.userAgent`, locale `ur-PK` from
  `intl.contentLocale` (the fingerprint's OWN locale, not an invented
  `en-PK`), timezone `Asia/Karachi` from `intl.timeZone`, plus viewport and
  device scale factor. Those are the exact four keys §16 records as having
  been wrong in four sibling repos at once.
* **Scraper API** — HTTP 200, 965,862 bytes, **48 of 48 products from a
  single request** at $0.0005. That is a far better result than the sibling
  repo this client came from (3 of 50 cards) and the reason is structural:
  foodpanda server-renders its grid, so one shot gets the whole page.

* **`--fingerprint` is measured HARMFUL on this site.** Four runs of one URL
  within five minutes: no fingerprint served 48 rows on the first attempt
  twice, `--fp-country pk` was refused 4 of 4, and `--fp-country de` —
  matching the exit country exactly — was refused 4 of 4 as well. So it is
  not the country mismatch §8 warns about; it is that an injected Windows
  identity contradicts the real browser underneath it. The flag stays (it is
  the family's contract, and the product works elsewhere) and the README,
  TROUBLESHOOTING and this entry all say not to use it here.

* **A Scraping Browser profile expires in about a day**, after which the
  endpoint answers HTTP 401 `deny_no_user`. That makes it the best path for a
  run you are watching and the wrong secret for anything scheduled. The
  canary here is dispatch-only so it does not matter in practice, and an
  expired endpoint is reported as a SKIP with its cause named rather than as
  a failure. The engine's
  connect error now names both causes — 500 is a busy profile, 401 is a gone
  one — because telling a reader to wait for another run when the profile has
  expired is a wasted afternoon.

### Known limitations, stated rather than worked around

* **No vendor-page mode.** A `/restaurant/{code}/{slug}` page is where a
  menu, a delivery fee and a minimum order live, and it could not be
  captured: eight cold navigations and one click-through from a listing the
  same browser had just been served all came back HTTP 403, on two country
  sites. A parser written against markup nobody has seen is a guess with a
  docstring, so the mode is absent rather than broken. Such a URL is refused
  with that reason.
* **The captcha solver is inapplicable on this site**, which is not the
  same as untested: the only challenge foodpanda renders is reCAPTCHA
  Enterprise, which this project does not implement, so no solve is ever
  attempted and no key is ever charged. The other three paid paths WERE run
  end to end on 2026-09-15 — see "Verified live" below.
* **foodpanda.co.th is NOT a foodpanda site and is refused with that
  reason.** Measured 2026-09-15 through the Scraping Browser: it answers
  HTTP 200 and redirects to **robinhood.co.th**, a different company, with
  zero vendor tiles, zero `deliveryhero` references and zero `/restaurant/`
  links. foodpanda has left Thailand. Left in `HOSTS` it would have fetched a
  competitor's page, classified it `blocked` — correctly, it is not built out
  of foodpanda's assets — and reported exit 3, sending the reader after a
  proxy problem that does not exist (§5's `mediamarkt.lu` case).

[0.1.0]: https://github.com/2scraper/foodpanda-scraper/releases/tag/v0.1.0
