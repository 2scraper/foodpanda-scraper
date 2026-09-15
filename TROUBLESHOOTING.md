# Troubleshooting

Every symptom below was seen while building this repo, and the answer is what
actually fixed it. Numbers are measurements, not estimates — where something
has not been measured, it says so.

---

## "Access to this page has been denied" / exit 3

This is **PerimeterX**, foodpanda's bot manager. It is the single most common
thing you will meet, and the first thing to know is that it is **not
permanent and not about your address alone**.

Measured 2026-09-15, one residential exit, fourteen foodpanda addresses, up
to three attempts each with a fresh browser and roughly half a minute
between: **10 of 30 navigations were served.** The successes were spread
across attempt numbers — four on the first, one on the second, five on the
third. The same URL that answered 403 answered 200 two attempts later, with
no proxy and no solve.

So, in order:

1. **Let `--retries` do its work, and give it room.** Every retry launches a
   FRESH browser, because the refusal is a property of the session. A
   three-page run with an 8-second backoff had page 2 refused on all five
   attempts; the same run with `--retry-delay 30` was served on the second.
2. **Raise `--delay`.** The refusal rate is a direct function of request
   rate. Thirty navigations in twenty minutes from one address is what
   produced the two-in-three refusal rate above.
3. **Use `--headful`.** Every measurement in this repo was taken with a real
   window. A headless window is one more thing a bot manager can key on, and
   this site is already refusing a third to two-thirds of cold requests.
4. **Then, and only then, buy an exit.** `--proxy` or `--proxy-file` with a
   residential exit, preferably in the country site's own country.
   `ap.proxy.2captcha.com` is the regional gateway for the Asian sites this
   brand operates.

**A 2Captcha key does NOT help here.** See the next section.

---

## "Why doesn't my 2Captcha key get me past the block?"

Because the challenge is **reCAPTCHA Enterprise**, and this project
implements reCAPTCHA v2 and v3.

PerimeterX's denial page renders what looks like an ordinary v2 checkbox:

```html
<div id="px-captcha">
  <div class="g-recaptcha" data-sitekey="6Lc…" data-callback="handleCaptcha">
```

and reading only that, this repo first classified a refusal as a solvable
challenge. The **loader** beside it says otherwise, and the loader wins:

```html
<script src="https://www.google.com/recaptcha/enterprise.js?hl=en-US">
```

Measured on four denial documents — foodpanda.pk and foodpanda.sg, a vendor
page and a listing page, captures hours apart: 3, 3, 3 and 2 occurrences of
`recaptcha/enterprise`, and **zero** occurrences of `recaptcha/api.js` on any
of them. The runtime detector agrees independently: `___grecaptcha_cfg`
reports `enterprise: true`.

So `product_parser.detect_bot_challenge` returns `None` for these pages, the
state is `blocked` rather than `challenge`, **no solve is attempted and your
key is not charged**. That is deliberate: a solve here would buy a token the
site rejects.

The day foodpanda swaps the enterprise loader for the ordinary one, one line
in `product_parser.py` changes — see `WOULD_BE_SOLVABLE_MARKERS`.

---

## "Just a moment..." instead of the site

This is **Cloudflare's managed challenge**, and it means something different
from the section above: your client did not look like a browser at all, so it
never reached the application.

Measured with `curl` against all ten country sites: every one returned
HTTP 403 and this document, while a real browser on the same address was
served normally seconds later.

If you are seeing it **from a browser engine**, something has stripped the
context. Prefer `playwright_scraper.py`, which is the engine every
measurement here was taken with.

---

## A run reported `complete` but I only got one page

It should not any more — this was a real bug, found by this repo's first live
run, and it is pinned by a test now.

What happened: page 2 was refused on every attempt, fell through to the
parse, produced 0 rows, and the run loop read that as "this page added no sku
we had not already seen" — the end of the listing. `no_new_products` is a
COMPLETE stop reason, so the sidecar said `status: complete`, `pages_failed:
[]`, exit 0, holding 48 of roughly 144 rows.

If you see this on an older checkout, update. On a current one, a refused page
is reported as a FAILED page and the run exits **6 (partial)** with the page
number in `pages_failed`.

**Always read the sidecar.** `<out>.meta.json` names the status, the stop
reason and which pages failed, by number.

---

## Exit 4: "ran fine and parsed 0 rows"

Three ordinary causes, in order of likelihood:

* **You pointed it at a directory, not a listing.** `/city` lists cities and
  `/city/{city}/area` lists areas — neither has a vendor tile on it. The
  scraper warns about this before the run. The listings are one level down:
  `/city/{city}` and `/city/{city}/area/{area}`.
* **You asked for a page past the end of the listing.** That is a correct
  answer.
* **The tile anchor moved.** Re-run with `--dump-html` and grep the snapshot
  for `bds-c-vendor-tile`. If it is absent, the site's design system has
  changed and `SELECTORS["item_card"]` in `product_parser.py` needs updating.

---

## "This URL is not supported"

Three URLs are refused **with the reason**, because "not a foodpanda site"
would be false in all three cases:

| URL | Why |
|---|---|
| `foodpanda.com` | The brand's global landing page — a country picker with no vendor tiles and no `/city` tree. Pick a country site. |
| `/restaurant/{code}/{slug}` | A vendor page. Eight cold navigations and one click-through from a listing the same browser had just been served all came back HTTP 403, on two country sites. No page was ever captured, so no parser was written and no mode ships. |
| anything else | Not a listing this repo understands. |

---

## `--pages 5` on the home page only fetched one

That is correct, and it says so in the log. The foodpanda **home page has no
pagination of any kind** — no page links, no page parameter, no next link —
so there is no page 2 to fetch. The run scrolls the one page it has instead.

For real pages, point `--url` at `/city/{city}` or `/city/{city}/area/{area}`:
the site publishes `?page=N` links between batches itself, and a cold fetch of
`?page=2` returns 48 tiles sharing no vendor code with page 1.

---

## `--concurrency` was ignored

It is refused, with the reason, for any URL that has no per-page addresses:
the home page, a `/city` or `/area` directory, and a vendor page. It is also
refused with `--cdp-endpoint`, because a Scraping Browser profile allows one
live connection and workers would collide (`profile_locked`) — use several
`pid`s, one run each.

Where it **is** available (the `/city` tree), remember what it costs on this
site: N workers is N times the request rate from one address, and the refusal
rate here is a direct function of exactly that. Raise `--delay` before
raising `--concurrency`, and pass `--proxy-file` if you raise both.

---

## The `category` column has cuisines in it, and `--category` did nothing to it

By design. On this site `category` holds the **vendor's own cuisines**, read
off the tile — "Pakistani", "Fast Food", "Cakes & Bakery". Letting a
command-line label write into it would mix your string with the site's data
in one column with no way to tell them apart.

`--category` labels the **run** instead: it appears as `run_label` in
`<out>.meta.json`.

---

## `delivery_fee`, `delivery_time` and `price_level` are null on every row

Then you ran a `/city` or `/area` listing, and those three columns belong to
the **home page** — the one listing foodpanda renders with a delivery address
already in play. The `page_kind` column says which you have: `home`, `city`
or `area`.

This is why `page_kind` exists. Diffing a home run against a city run without
it would read those three columns emptying as the vendors changing.

---

## `review_count` says 100 for a vendor that plainly has more

Read `review_count_is_floor` beside it. The site caps the printed figure at
round numbers — `(100+)`, `(500+)`, `(1000+)` — so 100 with the flag set means
"at least 100", not "exactly 100". 6% of tiles on one country site and 27% on
another are capped this way.

A monitor watching the number alone would report every busy vendor as frozen
at exactly 100 forever.

---

## `discount_pct` says 35 but the vendor is not 35% off

Read `discount_is_upper_bound`. The site writes two shapes, and 21% of the
measured labels are the second:

| Label | `discount_pct` | `discount_is_upper_bound` |
|---|---|---|
| `20% off` | 20.0 | `False` |
| `Up to 35% off` | 35.0 | `True` |

`discount_label` keeps the text verbatim so a wrong parse is visible rather
than silent.

---

## Two runs disagree about a third of the rows

Almost certainly `is_open`. It is a **point-in-time** reading of whether the
vendor is accepting orders, and it is the most volatile column in the schema:
735 of 1,904 tiles on one measured capture and 17 of 48 on another were
closed, and which ones depends entirely on the hour.

`diff_runs.py` routes rows whose ONLY change is the opening pair to a
separate `hours_changed` bucket, and `--fail-on-change` ignores it. If you are
diffing by hand, do the same.

---

## `diff_runs.py` refused to compare two runs

Three reasons it will refuse, each with the reason printed:

* **One of them is not `complete`.** A run cut short is missing every vendor
  on the pages it never fetched, and diffing it against a full run reports
  them all as `removed`. Pass `--force` if you genuinely want that.
* **They are different country sites.** foodpanda issues vendor codes
  **per country**, so two rows can share a `sku` and be unrelated vendors —
  a cross-country diff would match them and report the difference as a change.
* **One run holds more than one country site.** That run was redirected
  mid-way and its own rows are not safely keyed against each other.

---

## Selenium can't use my proxy or my Scraping Browser endpoint

Both are real limits of the driver, reported loudly rather than half-working:

* **`--proxy` with credentials**: chromedriver's `--proxy-server` takes an
  address with nowhere to put a password, and there is no Selenium equivalent
  of pyppeteer's `page.authenticate`. The credentials are stripped and you
  are warned. 2Captcha's IP-whitelist mode is the way around it: whitelist
  your address and you get `host:port` connections with no credentials in
  them at all.
* **`--cdp-endpoint`**: chromedriver's `debuggerAddress` is a bare
  `host:port`. Playwright and pyppeteer authenticate on the WebSocket
  upgrade and work fine.

Use `playwright_scraper.py` or `puppeteer_scraper.py` for either. Note also
that the **pyppeteer engine has no fingerprint flags** anywhere in this
scraper family — `--fingerprint` is wired into the Playwright and Selenium
engines only.

---

## Every page load times out through my proxy, but `curl` works fine

The gateway is almost certainly not answering `407`.

Chromium does not send proxy credentials on its first `CONNECT`. It sends an
unauthenticated one, waits for a `407 Proxy Authentication Required`
challenge, and only then retries with the credentials. An HTTP client like
`requests` sends `Proxy-Authorization` PREEMPTIVELY, so it never needs the
challenge — which is why the same credential can work perfectly from a script
and hang every browser.

Measured on a real 2Captcha custom-zone credential, 2026-09-15:

| | Result |
|---|---|
| `requests` -> foodpanda.pk | HTTP 403 (PerimeterX) in **5s** — the route works |
| raw `CONNECT` with `Proxy-Authorization` | `HTTP/1.1 200 OK` in **3s** |
| raw `CONNECT` with NO credentials | **no answer at all**, stalled 35s |
| Chromium -> example.com | `ERR_TIMED_OUT` at **31s** |
| Chromium -> foodpanda.pk | `ERR_TIMED_OUT` at **31s** |

Note the fourth row: the browser could not reach `example.com` either, so
this is nothing to do with the site.

**How to tell it apart from a block**: a block arrives as a PAGE (exit 3, and
the dump names PerimeterX or Cloudflare). This arrives as `ERR_TIMED_OUT`
with no document at all, at almost exactly the same number of seconds every
time, to every destination.

**What to do:**

* **Use IP whitelisting instead of user:pass.** 2Captcha's proxy dashboard
  will whitelist your address and
  `/proxy/generate_white_list_connections` then returns one `host:port` per
  exit with no credentials in them at all. With nothing to authenticate,
  there is no `407` handshake to stall on. This also happens to be the only
  way the Selenium engine can use a proxy (see above).
* Or ask for a gateway/zone that answers `407` on an unauthenticated
  `CONNECT`.
* Or skip proxies and use `--cdp-endpoint` — the Scraping Browser API brings
  its own browser and its own exit.

**A separate trap on the same credential**: check whether the zone rotates
per request. Measured on the same one — four requests, four different exit
addresses. A browser pulls dozens of resources for one listing, and it cannot
do that from a different address each time. 2Captcha pins the exit with a
`-session-{id}-sessTime-{minutes}` segment in the USERNAME:

```
http://{user}-zone-custom-region-pk-session-myrun1-sessTime-30:{password}@...
```

With the session in place, four requests came from one address. (That was
necessary but not sufficient here — the `407` problem above is separate.)

---

## I turned on `--fingerprint` and now everything is blocked

That is the expected result on this site, and it is measured. Four runs of one
URL within five minutes:

| Run | Served |
|---|---|
| no fingerprint | 48 rows, first attempt |
| no fingerprint, five minutes later | 48 rows, first attempt |
| `--fingerprint --fp-country pk` | refused 4 of 4 |
| `--fingerprint --fp-country de` — matching the exit | refused 4 of 4 |

It is not a country mismatch: the last arm matched the exit exactly. It is
that a fingerprint describing a Windows machine, injected into the Chromium
you actually have, contradicts everything it does not cover — real GPU
strings, fonts, TLS, JS engine behaviour. An honest browser scores better.

**Drop the flag.** If you want a different identity, use `--cdp-endpoint`:
the Scraping Browser supplies a coherent one, and this scraper deliberately
refuses to stack a fingerprint on top of it.

The fingerprint client itself is fine and was verified live — fingerprint
5581439 (PK) came back with its user agent, locale `ur-PK` and timezone
`Asia/Karachi` all applied correctly. The problem is what the site makes of
it, not what the API returns.

---

## The pyppeteer engine is refused where the others are not

Its browser, not its driver. Measured on one URL in one window, arms
alternating so the address and the hour fall on all three equally:

| | Served |
|---|---|
| pyppeteer + Playwright's Chromium 148 | **2 of 2**, full grid |
| pyppeteer + its own bundled Chromium 117 | **0 of 2**, HTTP 403 |
| Playwright | **2 of 2**, full grid |

pyppeteer pins Chromium revision 1181205, which reports `117.0.5938.0` —
three years stale in September 2026, and a bot manager needs no other signal.

```bash
python3 puppeteer_scraper.py --chromium-path /usr/bin/google-chrome ...
python3 puppeteer_scraper.py --chromium-path "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" ...
```

Turning off pyppeteer's automation flags does NOT help — `--enable-automation`
removed, `--disable-blink-features=AutomationControlled` added, and both
together were each refused on every attempt with the bundled browser.

---

## Regenerating the offline suite's fixtures

```
python3 make_fixtures.py
```

It expects your own captures in `../../captures/` relative to the repo, named
as `SOURCES` in that file. They are not committed: a single listing capture
is 1.0 to 1.7 MB and a scrolled one is 16 MB.

Take them with `--dump-html` on any engine, which writes exactly the bytes
the parser was given, and pass `--headful`.

The script refuses to write anything unless every trimmed fixture parses
**identically** to its untrimmed original, column for column, and unless
nothing credential-shaped survives the scrub.

---

## The Docker image gets refused where my laptop does not

Expected. There is no display in a container, so the engine runs headless
there, and headless is the window this site is least generous with. Every
measurement in the README was taken with `--headful` on a desktop.

The levers, in order: raise `--delay`, pass a residential `--proxy` in the
site's own country, or point `--cdp-endpoint` at the Scraping Browser API,
which brings its own browser and its own exit.
