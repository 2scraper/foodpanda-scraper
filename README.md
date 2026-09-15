# foodpanda-scraper

[![release](https://img.shields.io/github/v/release/2scraper/foodpanda-scraper?sort=semver)](https://github.com/2scraper/foodpanda-scraper/releases)
[![tests](https://github.com/2scraper/foodpanda-scraper/actions/workflows/tests.yml/badge.svg)](https://github.com/2scraper/foodpanda-scraper/actions/workflows/tests.yml)
[![python](https://img.shields.io/badge/python-3.9%20%E2%80%93%203.13-blue)](pyproject.toml)
[![licence](https://img.shields.io/badge/licence-MIT-green)](LICENSE)
[![engines](https://img.shields.io/badge/engines-Playwright%20%C2%B7%20Selenium%20%C2%B7%20pyppeteer%20%C2%B7%20CDP-lightgrey)](#engines)
[![runs without an account](https://img.shields.io/badge/runs-without%20an%20account-brightgreen)](#do-i-need-to-buy-anything)

Scrapes **foodpanda vendor listings** — every restaurant and shop tile the
site puts on a city, area or home page, with its rating, review count,
cuisines, deal labels, delivery data and whether it is open right now. JSON
and CSV, one row per vendor, the same schema across all ten country sites.

Four ways to fetch: **Playwright** (recommended), **Selenium**, **pyppeteer**,
or a remote browser over **CDP** — the 2Captcha Scraping Browser API, or any
browser that exposes a CDP endpoint.

```bash
pip install -r requirements.txt -r requirements-playwright.txt
playwright install chromium

python3 playwright_scraper.py \
  --url "https://www.foodpanda.pk/city/lahore/area/gulberg" \
  --pages 3 --delay 45 --retry-delay 30 \
  --out gulberg --format both
```

```
[+] Saved 143 products -> gulberg.json
[+] Saved 143 products -> gulberg.csv
[+] Wrote run metadata -> gulberg.meta.json (status=complete)
```

That is a real run, 2026-09-15, from an ordinary residential address with no
proxy, no key and no account. See [sample_output.json](sample_output.json).

---

## Do I need to buy anything?

**No, and that is measured.** A plain headful Chromium on a residential
address was served by **ten of the ten country sites** on 2026-09-15, and
a three-page run of a real area listing returned 143 vendors with
`status: complete`.

What you do need is **patience**, because this site's refusal is a matter of
degree:

> A sweep of fourteen foodpanda addresses from one residential exit, each
> navigated up to three times with a fresh browser and roughly half a minute
> between attempts, was served on **10 of 30 navigations**. The successes were
> spread across attempt numbers — four on the first, one on the second, five
> on the third.

The refusal is a property of the **session and the moment**, not of your
address alone: the same URL that answers 403 answers 200 two attempts later.
So the instruments that work here, in order, are `--retries` (each attempt
launches a fresh browser), `--retry-delay` and `--delay`. Only after those
does buying an exit help.

### `--fingerprint` makes this site WORSE — do not use it here

Measured on one URL within five minutes, so the hour and the address fall on
every arm equally:

| Run | Served |
|---|---|
| no fingerprint | 48 rows, **first attempt** |
| no fingerprint, five minutes later | 48 rows, **first attempt** |
| `--fingerprint --fp-country pk` | **refused 4 of 4** |
| `--fingerprint --fp-country de` — matching the exit | **refused 4 of 4** |

The last row is what makes this conclusive. A fingerprint whose country
contradicts the exit is a known mismatch, so the obvious reading of the third
row is "wrong country" — and the fourth row removes it: a fingerprint that
matched the exit exactly was refused just the same.

What is left is the mismatch the flag cannot avoid. The fingerprint describes
a Windows machine; it is injected into whatever Chromium you are running. The
overrides say one thing while everything they do not cover — the real GPU
strings, the font stack, the TLS handshake, the JS engine's own behaviour —
says another, and PerimeterX reads that as worse than an honest browser.

The flag stays because it is part of this family's contract and the 2Captcha
fingerprint product genuinely works on sites where the local browser is the
thing being scored. On foodpanda it is a way to get refused. Use
`--cdp-endpoint` instead, which supplies a coherent identity rather than a
painted-on one — and which the scraper already refuses to stack a fingerprint
on top of.

### Headless is refused — so `--headful` is the default

Unusually for this family, this scraper runs **with a window** unless you ask
otherwise. One URL, arms alternating, a fresh browser per navigation, 45
seconds apart so the hour and the address fall on both equally:

| | Served | Tiles |
|---|---|---|
| `--headless` | **0 of 4** | 0, HTTP 403 every time |
| `--headful` | **4 of 4** | 47, 48, 45, 48 |

`--headless` is still there and is what the Docker image passes, because a
container has no display. That is the reason an image can be refused where
your laptop is not.

**What the paid products actually buy on this site** — measured, not
guessed. The Scraping Browser API turns this site from "patience required"
into "just works":

| | Refusals | Retries | Wall clock |
|---|---|---|---|
| Residential address, no proxy | page 3 refused 4 times | 5 attempts | ~7 min |
| `--cdp-endpoint` (Scraping Browser) | **none** | **none** | **29 s** |

Both runs took the same three pages of the same listing and returned 143 and
144 vendors with `status: complete`. If you are doing more than a page or
two, that is the difference the money buys.

> **The endpoint expires.** A Scraping Browser profile lives about a day;
> after that it answers HTTP 401 `deny_no_user` and you mint new credentials.
> So it is the best path for a run you are watching and the wrong thing to
> put in anything scheduled. This repo runs nothing live on a timer, so it
> is not a problem here — but if you wire one up yourself, use a proxy for
> the schedule and keep the endpoint for runs you are watching.

**What a 2Captcha key does NOT buy here**: a way past the block. See
[the challenge is Enterprise](#the-challenge-is-recaptcha-enterprise-and-this-repo-cannot-solve-it).

---

## What it reads

| URL | What you get | Paginates |
|---|---|---|
| `https://www.foodpanda.pk/` | The country home page — a curated set of vendors, **with delivery time, delivery fee and price level** | No pagination of any kind; scrolled instead |
| `/city/{city}` | A city's listing, 48 vendor tiles | `?page=N` |
| `/city/{city}/area/{area}` | An area's listing — the big one | `?page=N`, up to 24 numbered pages on the largest measured |

Refused, each **with the reason**:

| URL | Why |
|---|---|
| `/restaurant/{code}/{slug}` | [A vendor page cannot be reached](#what-a-vendor-page-does) |
| `/city`, `/city/{city}/area` | Directories of links, not listings of vendors — warned about, and the run honestly reports 0 rows |
| `foodpanda.com` | The brand's global landing page: a country picker with no vendor tiles and no `/city` tree |

### The ten country sites

Taken from the brand's own country footprint — foodpanda publishes no
cross-country `hreflang` set, checked on three captures — and then **probed
one at a time with a real browser** rather than trusted.

| Host | Country | Probed 2026-09-15 |
|---|---|---|
| `foodpanda.pk` | Pakistan | served, 62 vendor links on the home page |
| `foodpanda.sg` | Singapore | served, 67 |
| `foodpanda.my` | Malaysia | served, 83 |
| `foodpanda.ph` | Philippines | served, 56 |
| `foodpanda.com.tw` | Taiwan | served, 75 |
| `foodpanda.hk` | Hong Kong | served, 60 |
| `foodpanda.com.bd` | Bangladesh | served, 57 |
| `foodpanda.com.kh` | Cambodia | served, 47 |
| `foodpanda.la` | Laos | served, 50 |
| `foodpanda.com.mm` | Myanmar | served, 50 |

**`foodpanda.co.th` was dropped**, and it is the most surprising thing this
repo found. It answers HTTP 200 and redirects to **robinhood.co.th** — a
different company — with zero vendor tiles, zero `deliveryhero` references
and zero `/restaurant/` links on it. foodpanda has left Thailand. The scraper
refuses that host with that reason rather than reporting a block, because a
block would send you looking for a proxy problem that does not exist.

Each row is one to three navigations with a real browser; the raw probe
output is not committed (it is a working artefact, and stale within days).
Bare hosts redirect to their `www.` form and both are accepted.

The country **is** the host, so there is no `--country` flag to disagree with
the URL.

---

## The output

One row per vendor tile. The family's column prefix comes first, then this
site's own. `sample_output.json` is cut from the run at the top of this page.

| Column | Notes |
|---|---|
| `source` | The country site: `foodpanda.pk`, `foodpanda.sg`, … |
| `url`, `sku`, `slug` | The vendor's page, its four-character code, and its URL slug. The code is read from the tile's own `data-testid` and checked against its href — the two agreed on **1,904 of 1,904** measured tiles |
| `title` | From the `title` attribute, not the text node, which CSS truncates with an ellipsis |
| `rating` | Out of **five**. 69–78% of tiles carry one; the rest are vendors with no reviews yet |
| `review_count` + `review_count_is_floor` | **Read the flag.** The site caps the printed figure at `(100+)`, `(500+)`, `(1000+)`, so 100 with the flag set means "at least 100" |
| `discount_pct` + `discount_is_upper_bound` + `discount_label` | **Read the flag.** `20% off` is 20; `Up to 35% off` is a CEILING, and 21% of measured labels are that shape |
| `min_order` + `min_order_currency` | The minimum spend a voucher attaches to its percentage: `20% off Rs. 300`, `15% off S$ 30` |
| `currency` | ISO 4217, read from the symbol the tile prints. Null where the tile carries no money at all, which is every row of a city or area listing |
| `category`, `cuisines` | The site's own categorisation — "Pakistani", "Fast Food", "Cakes & Bakery". 98% of tiles |
| `is_open` + `opens_at` | **The most volatile column in the schema**: a point-in-time reading, 35–38% closed in the measured captures. `opens_at` is verbatim ("Sat 10:20") |
| `tags` | Every deal label, verbatim. The overflow chip (`+1`) is excluded — it is a count of hidden labels, not a label |
| `free_delivery`, `is_super_vendor` | Site badges. `free_delivery` is matched case-insensitively: Pakistan writes "Free Delivery" and Singapore "Free delivery" |
| `delivery_time`, `delivery_fee`, `price_level` | **Home page only** — see below |
| `page_kind` | `home`, `city` or `area`. The reason the three columns above can be null |
| `page`, `position` | The pair is unique across a run, and the canary asserts it |
| `image_url` | Recognised POSITIVELY by foodpanda's own media hosts, so a placeholder cannot fill the column |
| `city`, `area` | From the LISTING's URL. A vendor tile publishes no address of its own |

**Five family columns are absent**, and each absence is a measurement rather
than an oversight: `price`, `original_price`, `price_source`, `brand`,
`in_stock`. A foodpanda vendor tile carries no price — the thing on sale is a
restaurant, not an item — and 0 price nodes were found across 1,952 tiles on
two country sites. The reasoning is written out at the top of
[`output_writer.py`](output_writer.py).

### The home page publishes more than the listings do

This surprised us, and it is worth knowing before you pick a URL. A home-page
tile carries **five** labelled info rows where a city tile carries one:

```
city listing   Desserts
home page      From 25 min · $ · Biryani · Rs.69 · Free for first order
               ^delivery time  ^price level  ^cuisines  ^delivery fee
```

So `delivery_time`, `delivery_fee` (with its `currency`) and `price_level`
are populated on `--url https://www.foodpanda.pk/` and null on a `/city` or
`/area` run. The home page is the one listing foodpanda renders with a
delivery address already in play.

The rows are read by **what they are**, not by where they sit: the site
labels each one for screen readers, and the classification falls back to the
row's shape so it does not depend on the label being in English.

---

## What a vendor page does

Nothing, for this repo. `/restaurant/{code}/{slug}` is where a menu, a
delivery fee and a minimum order live, and it is refused:

> Eight cold navigations and one click-through from a listing **the same
> browser had just been served** all came back HTTP 403 with PerimeterX's
> denial page, on foodpanda.pk and foodpanda.sg alike, while listing pages
> answered 200 in those same sessions.

No page was ever captured, so no parser was written and **no mode ships**. A
parser written against markup nobody has seen is a guess with a docstring.
The scraper refuses such a URL with that reason rather than half-working.

---

## The two refusals, and why they want opposite responses

### PerimeterX — `Access to this page has been denied`

The common one. Per-session, and it clears — see
[Do I need to buy anything?](#do-i-need-to-buy-anything). Two shapes, both
captured: a 10 KB page with a reCAPTCHA widget on it, and a 4.7 KB page with
no widget at all.

### Cloudflare — `Just a moment...`

What a client that does not look like a browser gets, **before it ever
reaches the application**. Measured with `curl` against all eleven hosts
this repo probed (the ten storefronts and foodpanda.com): every one returned
HTTP 403 and this document, while a real browser on the same address was
served normally seconds later.

If you see this from a browser engine, something has stripped the context —
that is not a proxy problem and a proxy will not fix it.

### The challenge is reCAPTCHA **Enterprise**, and this repo cannot solve it

PerimeterX's denial page renders what looks like an ordinary reCAPTCHA v2
checkbox:

```html
<div id="px-captcha">
  <div class="g-recaptcha" data-sitekey="6Lc…" data-callback="handleCaptcha">
```

and on that evidence this repo first classified a refusal as a **solvable**
challenge. The loader beside it says otherwise, and the loader wins:

```html
<script src="https://www.google.com/recaptcha/enterprise.js?hl=en-US">
```

Measured on four denial documents — two country sites, two page kinds,
captures hours apart: **3, 3, 3 and 2** occurrences of `recaptcha/enterprise`,
and **zero** occurrences of `recaptcha/api.js` on any of them. The runtime
detector agrees independently: `___grecaptcha_cfg` reports `enterprise: true`.

`captcha_solver.py` implements reCAPTCHA v2 and v3 and **not** the enterprise
method. So such a page is reported as `blocked` rather than `challenge`,
**no solve is attempted and your key is not charged** — a solve would have
bought a token the site rejects.

The day foodpanda swaps the enterprise loader for the ordinary one, one line
in `product_parser.py` changes.

---

## Pagination

Unusually for this family, the site **publishes the page addresses itself**.
An area listing renders its own numbered links between batches of tiles:

```html
<span class="page-number" id="2" data-testid="pageNumber">
  <a href="https://www.foodpanda.pk/city/lahore/area/gulberg?page=2">2</a>
</span>
```

and a **cold fetch** of that address returned 48 tiles sharing no vendor code
with page 1. So pages are fetched independently, `--concurrency` above 1 is
genuinely available on the `/city` tree, and the run stops on a data
condition — "this page added no sku we had not already seen" — rather than on
a selector.

Three things the code will tell you rather than guess:

* **The home page has no pagination at all.** `--pages 5` there scrolls the
  one page it has, and says so. `--concurrency` is refused with that reason.
* **A refused page is not an exhausted listing.** This was a real bug, found
  by this repo's first live run: page 2 was refused on every attempt, the
  parse produced 0 rows, and the run read that as the end of the catalogue —
  reporting `status: complete` and exit 0 while holding a third of the data.
  A refused page is now a FAILED page, and the run exits **6** with the page
  number in `pages_failed`.
* **Page size is not constant.** Three consecutive cold fetches of one
  listing returned 48, 48 and 44 tiles, so nothing in this repo concludes
  completeness from a count.

---

## Engines

| | Playwright | Selenium | pyppeteer | Scraping Browser (CDP) |
|---|---|---|---|---|
| Recommended | **yes** | works | needs `--chromium-path` | for volume |
| `--concurrency` > 1 | yes | no | no | refused (one connection per profile) |
| Authenticated proxy | yes | **no** | yes | n/a |
| Authenticated CDP endpoint | yes | **no** | yes | yes |
| `--fingerprint` | yes | yes | **no** | not applicable |

Every measurement in this README was taken with the **Playwright** engine and
Playwright's own bundled Chromium — no special channel needed there.

**The pyppeteer engine is refused out of the box on this site, and the reason
is its browser rather than its driver.** Measured on one URL in one window,
arms alternating:

| | Served |
|---|---|
| pyppeteer + Playwright's Chromium 148 | **2 of 2**, full grid |
| pyppeteer + its own bundled Chromium 117 | **0 of 2**, HTTP 403 |
| Playwright | **2 of 2**, full grid |

pyppeteer pins a Chromium that reports `117.0.5938.0` — three years stale in
September 2026. Point the engine at a browser you already have and it works:

```bash
python3 puppeteer_scraper.py --chromium-path /usr/bin/google-chrome ...
```

The Selenium engine does not have this problem, because chromedriver drives
the Chrome already installed on the machine. It was served first try in a
live run.

Two Selenium limits are real and are reported loudly rather than
half-working: `--proxy-server` takes an address with nowhere to put a
password, and chromedriver's `debuggerAddress` is a bare `host:port`. Use
Playwright or pyppeteer for either. pyppeteer is also effectively unmaintained
and its own README points at Playwright.

Install exactly **one** engine: playwright and pyppeteer pin incompatible
`pyee` versions, and pyppeteer and selenium collide on `urllib3`.

---

## Exit codes

| | |
|---|---|
| `0` | ok |
| `1` | crash |
| `2` | bad usage |
| `3` | blocked |
| `4` | ran fine, zero vendors |
| `5` | remote API error |
| `6` | partial — some pages were never read |

**A run that finds nothing writes nothing**, so last night's good output is
never replaced by `[]`. `--allow-empty` is the opt-out. Every run writes
`<out>.meta.json` with the status, the stop reason and **which** pages failed,
by number — a failed run writes none, because a `"failed"` sidecar beside good
data would contradict it.

---

## Comparing two runs

```bash
python3 diff_runs.py --old gulberg.2026-09-14.json --new gulberg.2026-09-15.json
```

Keyed on `sku`, with one bucket this site needs and its siblings do not:
**`hours_changed`**. A vendor opening or closing follows the clock rather than
the catalogue — 735 of 1,904 tiles were closed in one measured capture — so
rows whose ONLY change is the opening pair are reported separately and
`--fail-on-change` ignores them.

It **refuses** to diff two runs that are not both `complete`, and two runs
from different country sites: foodpanda issues vendor codes per country, so
two rows can share a `sku` and be unrelated vendors.

---

## Configuration

Credentials go in `.env` next to the scripts, never on a command line.

```bash
cp .env.example .env
python3 env_config.py     # prints what was picked up, WITHOUT secrets
```

Precedence, highest first: an explicit flag → an exported environment
variable → `.env` → the default. Anything still carrying `{...}` braces is
treated as unset, so a copied example is never sent to an API as if it were a
key.

`TWOCAPTCHA_KEY`, `FOODPANDA_PROXY`, `FOODPANDA_CDP_ENDPOINT` and
`FOODPANDA_URL` are the four variables the code reads, and a test asserts
`.env.example` documents exactly those, in both directions.

> **Three of the four 2Captcha paths were run end to end against this site
> on 2026-09-15.** The fourth — the captcha solver — is not untested but
> INAPPLICABLE: the only challenge foodpanda renders is reCAPTCHA Enterprise,
> which this project does not implement, so there is nothing here to spend a
> solve on and your key is never charged for one. See
> [the challenge is Enterprise](#the-challenge-is-recaptcha-enterprise-and-this-repo-cannot-solve-it).

---

## Tests

```bash
python3 smoke_test.py      # the offline suite, no engine library required
pytest                     # the same checks, wrapped as one pytest test
python3 make_fixtures.py   # regenerate the fixtures from your own captures
```

Over **400 offline checks**, with fixtures cut from real captures by
`make_fixtures.py`, which refuses to write unless every trimmed fixture parses
identically to its untrimmed original, column for column.

The checks worth knowing about are listed at the top of `smoke_test.py`. Most
of them exist because of a specific failure.

CI runs the offline suite on the oldest and newest supported Python, installs
each engine in **its own virtualenv** (their pins are mutually unsatisfiable),
builds and runs the Docker image, and greps for committed credentials through
one implementation that both the workflow and the suite invoke.

There is also a **canary** workflow that takes three real pages off the live
site and asserts the things only a real run can show — that pagination
happened, that `(page, position)` is unique, that an area row carries no
delivery data. It is **dispatch-only, with no schedule**: nothing here runs
live on its own, and there is no badge claiming it does. Point it at a
`FOODPANDA_CDP_ENDPOINT` or `FOODPANDA_PROXY` secret and run it by hand when
you want that check; without one it skips with a notice.

---

## Traps that look like bugs

Read [TROUBLESHOOTING.md](TROUBLESHOOTING.md) before filing one. The short
list:

* `review_count` of 100 on a plainly busier vendor — read
  `review_count_is_floor`.
* `discount_pct` of 35 on a vendor that is not 35% off — read
  `discount_is_upper_bound`.
* Two runs disagreeing about a third of the rows — that is `is_open`, and the
  clock.
* `delivery_fee` null on every row — that is a `/city` run; those columns are
  the home page's, and `page_kind` says which you have.
* `--category` not appearing in the `category` column — by design. That
  column holds the vendor's cuisines; `--category` labels the run, in the
  sidecar.
* A Docker run being refused where your laptop is not — there is no display in
  a container, so it runs headless.

---

## Licence

MIT. See [LICENSE](LICENSE).

Captcha solving, the Scraping Browser API, proxies and fingerprints are four
separately billed [2Captcha](https://2captcha.com) products behind one key.
