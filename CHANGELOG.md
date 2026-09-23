# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the versions follow [Semantic Versioning](https://semver.org/) as closely
as a command-line tool can. **A patch release means "fixes", not "no flag ever
changes"**: where a default changes behaviour for an existing user, the
release notes lead with it in a blockquote, so nobody discovers it from a bill
or from a half-empty output file.

---

## [Unreleased]

### Fixed

- **`scraper_api_client.py --help` described two other sites.** Its
  description said the pages need JavaScript and a single fetch returns
  about 5 products, and `--url` told you to wait for a
  `lodging-card-responsive` element that arrives over GraphQL. Neither is
  true here: foodpanda server-renders its grid, and the docstring's own
  measurement is 48 of 48 vendors from one request. `--category` no longer
  claims to default to a URL segment — this client does not use it.
- **Issue templates described an auction site.** The bug-report hints talked
  about lots, reserve prices, a page-100 cap and `/en/` locales, and the
  site-change template's anchors were `__NEXT_DATA__` and lot cards.
  Rewritten from this repo's README, CONTRIBUTING and TROUBLESHOOTING:
  headless refusal, PerimeterX retries, directory URLs, the `(100+)` and
  `Up to` flags, home-page-only delivery columns.
- The pyppeteer and Selenium engines' docstrings listed a `--mode product`
  and gave a Tokopedia path as their `--url` example; both now describe the
  one `listing` mode and a real area listing. Their unreachable
  `--mode product` sidecar branch (a shop's id and slug, which nothing here
  produces) is gone; the listing branch is unchanged.
- Comments that told another site's story as this one's: `/p/<slug>` hubs,
  "hub category", a search grid over GraphQL, a `keyword=kopi` tracking tail
  and a detail page's buy box in the engines; an Akamai 394-byte refusal and
  auction lots in `captcha_solver.py`; a Tokopedia no-results string, an
  HTTP/2 stream reset, an Orlando search and `shop_rating` in
  `output_writer.py`. Replaced with what this repo measured, or attributed
  to the sibling where it happened. `captcha_solver.py` no longer points at
  a "No DataDome solver" section that does not exist.
- `SECURITY.md` said this project has no releases or tags; it has both.
- The access badge now says what the README measures: no account needed,
  from a residential IP.

## [0.2.2] — 2026-09-16

### Fixed

- **The README was missing its canary badge.** The workflow has been
  running all along; the badge that reports it was never added, so the
  one signal that says whether this scraper still works against the live
  site was invisible above the fold.
- **Fifteen lines of unreachable code removed from `playwright_scraper.py`.**
  A function's `def` line had been lost at some point before this repo's
  first commit, leaving its docstring and its `try: return page.content()`
  body indented into the end of `_mask_credentials`, where the control flow
  can never arrive. Nothing called it — `_content_when_settled` below it
  does the job — so no behaviour changes. The same fifteen lines, byte for
  byte, were in six repos of this family.

### Added

- **A check for a statement the control flow can never reach.** The
  undefined-name walk beside it cannot see this class by design: it pools
  every binding in a file rather than tracking scopes, so a name used inside
  dead code passes as long as anything else in the module binds it. The new
  one is narrow — a statement after a `return`/`raise`/`break`/`continue` in
  the SAME block — and measured across the eighteen repos of this family it
  found six real problems and zero false positives. Verified by control:
  appending `return 1` followed by a statement turns the suite red.

## [0.2.1] — 2026-09-16

0.2.0 implemented both of this site's captchas and corrected the README. It
did **not** correct `--help`, which is the surface a user reads first — so the
withdrawn claim went on shipping in the engines' own text.

> **`--help` was lying in three places.** Two engines said "NO challenge has
> ever been observed on this site … neither setting has anything to act on
> today, and neither helps with a refusal". Playwright's said Cloudflare's
> challenge "is reported as blocked instead, so no solve is attempted or
> billed for it". All three describe a repo that existed before 0.2.0.

### Fixed

* **Every user-facing claim that a captcha cannot be solved is gone**, from
  `--help`, from four docstrings and from `page_flow.block_advice`. What
  replaced them is what was measured: reCAPTCHA Enterprise ~55s / $0.00299,
  Turnstile 11s / $0.00145, and PerimeterX's widget-less stub as the one
  refusal nothing can buy.

* **`--help` named a host the parser refuses.** `.co.th` was dropped in 0.1.0
  — it redirects to robinhood.co.th, a different company — and
  `product_parser.HOSTS` has held ten entries ever since, while three engines
  and `output_writer.py` went on saying "eleven country sites" and
  playwright's `--url` help still listed `.co.th` among the supported ones.

* **Two documents disagreed about Cloudflare.** The README said a managed
  challenge is what a non-browser client gets and that a browser engine
  seeing one means "something has stripped the context" — three sections
  above the Turnstile section explaining that `foodpanda.com` serves one to a
  real browser, which is how this repo solved one. Same contradiction in
  TROUBLESHOOTING.md. Both now say the true thing: every non-browser client
  gets it, a browser can meet one too, and either way it is solved.

### Added

* **A flag reference in the README.** There was none: sixteen real flags were
  documented nowhere but `--help`. Four tables — core, proxies, 2Captcha, and
  the engine differences — with the default and what each one actually does.

* **Three checks, because prose does not fail a build on its own.** A
  banned-phrase check over every shipped `.py` and `.md` (CHANGELOG exempt —
  it quotes the withdrawn claim in order to withdraw it, and a released
  section is history); a check that the documented host count agrees with
  `len(HOSTS)` and that no user-facing text advertises a refused host; and a
  check that every flag an engine accepts appears in the README, that every
  in-page link resolves, and that each exit code is documented.

  Each was verified to FAIL when the fix is reverted. A check that cannot
  fail is not a check.

**532 offline checks**, up from 493.

---

## [0.2.0] — 2026-09-16

Everything here is about the same thing: **what this scraper does when
foodpanda refuses it.** 0.1.0 shipped saying a 2Captcha key would not help.
That was wrong, and it was wrong in the way that matters most — it reported a
limit of THIS CODE as a limit of the product.

> **If you read 0.1.0's notes**, two claims in them are now false. A
> 2Captcha key DOES clear this site's challenges, both of them, and the
> canary is dispatch-only rather than daily.

### Added

* **reCAPTCHA Enterprise, solved end to end.** foodpanda's PerimeterX denial
  page renders a v2-shaped `g-recaptcha` container beside a
  `recaptcha/enterprise.js` loader. 2Captcha solves that
  (`RecaptchaV2EnterpriseTaskProxyless`); `captcha_solver.py` was simply
  building the non-enterprise task type. It now carries the page's own answer
  — the enterprise loader, and `window.grecaptcha.enterprise` — into the
  task. Measured on a live denial: **~55 seconds, $0.00299**, and the page
  came back with its full grid. Control, because a solve that coincides with
  a block expiring proves nothing: a plain same-session reload cleared the
  same block **0 of 8 times**. The token is what got in.

* **Cloudflare Turnstile, Challenge page included.** The hard half is that a
  Challenge page publishes no sitekey at all: Cloudflare calls
  `turnstile.render(container, params)` once and keeps nothing, so `cData`,
  `chlPageData` and `action` exist only inside that call. Every engine now
  installs an interception script on the context **before any page script
  runs** — `add_init_script` in Playwright, `evaluateOnNewDocument` in
  pyppeteer, CDP `Page.addScriptToEvaluateOnNewDocument` in Selenium, the one
  thing the three cannot share. Measured against `foodpanda.com`'s own "Just
  a moment…": all four parameters captured, `TurnstileTaskProxyless`, an
  837-character token in **11 seconds for $0.00145**, and the page came back
  as the real site.

  Against expectation, the user agent did **not** matter: the token was
  minted under a Windows UA while the browser was macOS and Cloudflare took
  it anyway. The mismatch is logged so it can be ruled out rather than
  guessed at.

* **`SOLVABLE_PRODUCTS`** — reCAPTCHA v2, v3, both Enterprise variants and
  Turnstile, in one place, so the answer to "would a key help here" is
  readable without tracing the marker sets.

### Changed

* **A page with a solvable widget on it is `challenge`, not `blocked`.**
  Both of this site's refusals now carry one, so the only thing left in
  `blocked` is PerimeterX's widget-less stub — 4.7 KB, 24 `px-captcha`
  references, nothing for any solver to work on at any price. Nothing is
  attempted or billed for that one.

* **The canary is dispatch-only: no schedule, no badge.** This repo runs
  nothing live on a timer. A green badge for a check that is not happening is
  as unreadable as one that is always red.

### Fixed

* **A wrong captcha variant, found by spending real money on the first
  attempt.** The heuristic had no rung for "challenge frame present, no
  `size`, no `render`", fell through to v3, bought a
  `RecaptchaV3TaskProxyless` and got `ERROR_CAPTCHA_UNSOLVABLE` after 87
  seconds — the exact "wrong variant buys a rejected token" failure
  `captcha_solver.py`'s own docstring warns about. v3 renders no challenge
  frame at all, so a frame present now settles the variant as a v2 checkbox.

* **The static detector could not see this site's widget.** It bailed unless
  the markup contained `grecaptcha`, and the denial page carries only the
  hyphenated class `g-recaptcha` — so anyone debugging from a `--dump-html`
  capture was told there was no captcha at all.

* **`cf-turnstile` was tried as a marker and thrown away**, which is the
  family's extension trap arriving for real. 2Captcha's Scraping Browser
  injects `chrome-extension://…/content/captcha/turnstile/hunter.js` with
  `data-ts-input="cf-turnstile-response"` into every page it loads, so the
  string appears once on a **served** Hong Kong listing and **zero** times on
  the real Cloudflare challenge — a marker that fires on good pages and
  misses the bad one, which is worse than no marker.
  `challenges.cloudflare.com` is the one that works: 0 on every served page,
  5 on the challenge. Caught by this repo's own check, not by review.

* **A sitekey-less Turnstile detection no longer builds a task.** It raises
  instead. Paying for a request 2Captcha will reject is worse than reporting
  the page unsolved.

### Tests

**493 offline checks**, up from 470. The new ones pin the Challenge-page task
shape, the standalone widget's task shape, the sitekey-less refusal, the
extension trap in both directions (present on a served page, absent from the
challenge) and the v1 API's refusal to half-try Turnstile.

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
  daily canary that SKIPS with a notice rather than failing when no proxy
  secret is set.

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
  run you are watching and the wrong secret for a scheduled job, so the daily
  canary prefers a durable `FOODPANDA_PROXY` and reports an expired endpoint
  as a SKIP with its cause named rather than as a failure. The engine's
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

[0.2.1]: https://github.com/2scraper/foodpanda-scraper/releases/tag/v0.2.1
[0.2.0]: https://github.com/2scraper/foodpanda-scraper/releases/tag/v0.2.0
[0.1.0]: https://github.com/2scraper/foodpanda-scraper/releases/tag/v0.1.0
