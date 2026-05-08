"""
Foodpanda Scraper — Playwright (PerimeterX-aware)

The site uses PerimeterX bot protection. This scraper handles it via:
  Strategy 0 — Direct HTTP API (no browser, fastest, works when session cookies available)
  Strategy 1 — playwright-stealth (patches 50+ fingerprint vectors PerimeterX checks)
  Strategy 2 — 2captcha PerimeterX/DataDome challenge solver
  Strategy 3 — __NEXT_DATA__ / window state extraction
  Strategy 4 — CSS + link fallback

Usage:
    pip install playwright-stealth aiohttp
    playwright install chromium

    python scraper_playwright.py --city singapore --pages 3
    python scraper_playwright.py --city singapore --headed        # most reliable, real browser
    python scraper_playwright.py --city singapore --debug
    python scraper_playwright.py --menu --restaurant-url "https://www.foodpanda.sg/restaurant/..."
    python scraper_playwright.py --city singapore --proxy "http://user:pass@proxy.2prx.com:8080"
"""

import asyncio
import argparse
import json
import csv
import random
import sys
import time
from datetime import datetime

try:
    from playwright.async_api import async_playwright
except ImportError:
    print("[ERROR] playwright not installed. Run: pip install playwright && playwright install chromium")
    sys.exit(1)

try:
    from playwright_stealth import stealth_async
    HAS_STEALTH = True
except ImportError:
    HAS_STEALTH = False
    print("[WARN] playwright-stealth not installed. Run: pip install playwright-stealth")
    print("[WARN] Without it PerimeterX will likely block headless mode.\n")

try:
    import aiohttp
    HAS_AIOHTTP = True
except ImportError:
    HAS_AIOHTTP = False

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

TWOCAPTCHA_API_KEY = "YOUR_2CAPTCHA_API_KEY"   # https://2captcha.com
PROXY_URL          = ""                          # http://user:pass@proxy.2prx.com:8080

# Country config: domain + fd-api subdomain + lat/lng of capital city
COUNTRIES = {
    "singapore":   ("https://www.foodpanda.sg",     "tw-sg",  1.3521,  103.8198),
    "hong_kong":   ("https://www.foodpanda.hk",     "tw-hk",  22.3193, 114.1694),
    "thailand":    ("https://www.foodpanda.co.th",  "tw-th",  13.7563, 100.5018),
    "malaysia":    ("https://www.foodpanda.my",     "tw-my",  3.1390,  101.6869),
    "philippines": ("https://www.foodpanda.ph",     "tw-ph",  14.5995, 120.9842),
    "pakistan":    ("https://www.foodpanda.pk",     "tw-pk",  33.6844,  73.0479),
    "bangladesh":  ("https://www.foodpanda.com.bd", "tw-bd",  23.8103,  90.4125),
    "taiwan":      ("https://www.foodpanda.com.tw", "tw-tw",  25.0330, 121.5654),
    "cambodia":    ("https://www.foodpanda.com.kh", "tw-kh",  11.5564, 104.9282),
    "myanmar":     ("https://www.foodpanda.com.mm", "tw-mm",  16.8661,  96.1951),
    "laos":        ("https://www.foodpanda.la",     "tw-la",  17.9757, 102.6331),
    "romania":     ("https://www.foodpanda.ro",     "tw-ro",  44.4268,  26.1025),
    "bulgaria":    ("https://www.foodpanda.bg",     "tw-bg",  42.6977,  23.3219),
    "slovakia":    ("https://www.foodpanda.sk",     "tw-sk",  48.1486,  17.1077),
    "czechia":     ("https://www.foodpanda.cz",     "tw-cz",  50.0755,  14.4378),
}

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]

# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------

def _safe(d, *keys, default=""):
    for k in keys:
        if isinstance(d, dict):
            d = d.get(k)
            if d is None:
                return default
        else:
            return default
    return d if d is not None else default


def parse_vendor(v: dict) -> dict:
    cuisines = [c.get("name", "") for c in (v.get("cuisines") or []) if isinstance(c, dict)]
    budget   = v.get("budget")  if isinstance(v.get("budget"),  dict) else {}
    rating   = v.get("rating")  if isinstance(v.get("rating"),  dict) else {}
    chars    = v.get("characteristics") if isinstance(v.get("characteristics"), dict) else {}
    address  = v.get("address") if isinstance(v.get("address"), dict) else {}
    return {
        "id":            v.get("id", ""),
        "code":          v.get("code", ""),
        "name":          v.get("name", ""),
        "url":           v.get("web_path", ""),
        "cuisines":      ", ".join(filter(None, cuisines)),
        "rating":        _safe(rating, "average"),
        "rating_count":  _safe(rating, "total_ratings"),
        "delivery_time": chars.get("delivery_time_label", ""),
        "delivery_fee":  budget.get("delivery_fee_label", ""),
        "min_order":     budget.get("minimum_delivery_fee_label", ""),
        "price_range":   budget.get("price_range_label", ""),
        "city":          _safe(address, "city"),
        "latitude":      _safe(address, "latitude"),
        "longitude":     _safe(address, "longitude"),
        "is_promoted":   v.get("is_promoted", False),
        "hero_image":    v.get("hero_image", ""),
        "scraped_at":    datetime.utcnow().isoformat(),
    }


def parse_menu_item(item: dict, category: str) -> dict:
    variations = item.get("product_variations") or [{}]
    pv = variations[0] if variations else {}
    return {
        "id":             item.get("id", ""),
        "name":           item.get("name", ""),
        "description":    item.get("description", ""),
        "category":       category,
        "price":          pv.get("price", ""),
        "original_price": pv.get("original_price", ""),
        "currency":       item.get("currency", ""),
        "is_available":   item.get("is_available", True),
        "image_url":      item.get("image_url", ""),
        "scraped_at":     datetime.utcnow().isoformat(),
    }


def extract_vendors_from_blob(data, seen: set) -> list[dict]:
    results = []
    if isinstance(data, dict):
        if data.get("name") and (data.get("id") or data.get("code")) and "cuisines" in data:
            vid = str(data.get("id") or data.get("code"))
            if vid not in seen:
                seen.add(vid)
                results.append(parse_vendor(data))
            return results
        for val in data.values():
            results.extend(extract_vendors_from_blob(val, seen))
    elif isinstance(data, list):
        for item in data:
            results.extend(extract_vendors_from_blob(item, seen))
    return results


def extract_menu_from_blob(data) -> list[dict]:
    items = []
    if isinstance(data, dict):
        if "products" in data and isinstance(data["products"], list):
            category = data.get("name", "")
            for p in data["products"]:
                if isinstance(p, dict) and p.get("name"):
                    items.append(parse_menu_item(p, category))
            return items
        for val in data.values():
            items.extend(extract_menu_from_blob(val))
    elif isinstance(data, list):
        for item in data:
            items.extend(extract_menu_from_blob(item))
    return items


# ---------------------------------------------------------------------------
# 2captcha — PerimeterX / generic challenge solver
# ---------------------------------------------------------------------------

async def solve_perimeterx(api_key: str, site_url: str, debug: bool = False) -> str | None:
    """
    Submit a PerimeterX challenge to 2captcha.
    Returns the solved token, or None on failure.
    See: https://2captcha.com/api-docs/perimeterx
    """
    if not HAS_AIOHTTP:
        print("[WARN] aiohttp required for 2captcha. pip install aiohttp")
        return None
    if debug:
        print(f"[2captcha] Submitting PerimeterX challenge for {site_url}")
    async with aiohttp.ClientSession() as session:
        async with session.post("https://2captcha.com/in.php", data={
            "key":     api_key,
            "method":  "perimeterx",
            "pageurl": site_url,
            "json":    1,
        }) as r:
            data = await r.json()
        if data.get("status") != 1:
            print(f"[2captcha] Submit error: {data}")
            return None
        task_id = data["request"]
        if debug:
            print(f"[2captcha] Task {task_id} submitted")
        for _ in range(36):
            await asyncio.sleep(5)
            async with session.get(
                f"https://2captcha.com/res.php?key={api_key}&action=get&id={task_id}&json=1"
            ) as r:
                result = await r.json()
            if result.get("status") == 1:
                if debug:
                    print("[2captcha] PerimeterX solved ✓")
                return result["request"]
            if result.get("request") != "CAPCHA_NOT_READY":
                print(f"[2captcha] Error: {result}")
                return None
    return None


# ---------------------------------------------------------------------------
# Direct HTTP API  (Strategy 0 — no browser)
# ---------------------------------------------------------------------------

async def api_fetch_vendors(
    country_key: str,
    lat: float,
    lng: float,
    pages: int,
    proxy: str = "",
    debug: bool = False,
) -> list[dict]:
    """
    Call Foodpanda's internal REST API directly.
    Endpoint: https://{prefix}.fd-api.com/api/v5/vendors
    """
    if not HAS_AIOHTTP:
        return []

    _, prefix, _, _ = COUNTRIES.get(country_key, COUNTRIES["singapore"])
    base = f"https://{prefix}.fd-api.com"
    ua   = random.choice(USER_AGENTS)

    headers = {
        "User-Agent":      ua,
        "Accept":          "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer":         COUNTRIES[country_key][0] + "/",
        "Origin":          COUNTRIES[country_key][0],
        "x-fp-api-key":    "volo",
    }

    proxy_url = proxy or PROXY_URL or None
    connector = aiohttp.TCPConnector(ssl=False) if proxy_url else None

    results = []
    seen    = set()

    async with aiohttp.ClientSession(headers=headers, connector=connector) as session:
        for page_num in range(pages):
            offset = page_num * 48
            params = {
                "latitude":              lat,
                "longitude":             lng,
                "language_id":           1,
                "include":               "characteristics",
                "dynamic_pricing":       0,
                "configuration":         "Variant1",
                "budgets":               "",
                "cuisine":               "",
                "sort":                  "",
                "food_characteristic":   "",
                "use_free_delivery_label": "false",
                "vertical":              "restaurants",
                "limit":                 48,
                "offset":                offset,
            }
            url = f"{base}/api/v5/vendors"
            try:
                kw = {"params": params, "timeout": aiohttp.ClientTimeout(total=20)}
                if proxy_url:
                    kw["proxy"] = proxy_url
                async with session.get(url, **kw) as r:
                    if debug:
                        print(f"[API] {r.status} {str(r.url)[:120]}")
                    if r.status != 200:
                        print(f"[API] HTTP {r.status} — stopping pagination")
                        break
                    data = await r.json(content_type=None)
                    found = extract_vendors_from_blob(data, seen)
                    results.extend(found)
                    print(f"[API] Page {page_num+1}: {len(found)} vendors (total {len(results)})")
                    if not found:
                        break
                    await asyncio.sleep(random.uniform(0.8, 1.5))
            except Exception as e:
                print(f"[API] Error: {e}")
                break

    return results


# ---------------------------------------------------------------------------
# Browser scraper
# ---------------------------------------------------------------------------

class FoodpandaScraper:
    def __init__(self, args):
        self.args       = args
        self.debug      = args.debug
        self.results:   list[dict] = []
        self._captured: list[dict] = []
        self._cookies:  list[dict] = []   # saved after warm-up

    def log(self, msg):
        if self.debug:
            print(f"[DEBUG] {msg}")

    # ── launch ───────────────────────────────────────────────────────────────

    async def _launch(self, playwright):
        opts = {
            "headless": not self.args.headed,
            "args": [
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--disable-features=IsolateOrigins,site-per-process",
                "--disable-web-security",
            ],
        }
        proxy = self.args.proxy or PROXY_URL
        if proxy:
            opts["proxy"] = {"server": proxy}
            print(f"[*] Proxy: {proxy}")

        browser = await playwright.chromium.launch(**opts)
        ctx = await browser.new_context(
            user_agent=random.choice(USER_AGENTS),
            viewport={"width": random.randint(1366, 1920), "height": random.randint(768, 1080)},
            locale="en-US",
            timezone_id="Asia/Singapore",
            extra_http_headers={
                "Accept-Language": "en-US,en;q=0.9",
                "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
                "sec-ch-ua-mobile": "?0",
                "sec-ch-ua-platform": '"Windows"',
            },
        )
        return browser, ctx

    async def _new_stealthy_page(self, ctx):
        page = await ctx.new_page()
        if HAS_STEALTH:
            await stealth_async(page)
            self.log("playwright-stealth applied ✓")
        else:
            # Manual minimal patch
            await page.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
                window.chrome = {runtime: {}};
            """)
        return page

    # ── PerimeterX warm-up ───────────────────────────────────────────────────

    async def _warmup(self, page, base_url: str) -> bool:
        """
        Load the homepage first, behave like a human, wait for
        PerimeterX to issue its cookie (_pxhd / _px3).
        Returns True if we passed bot detection.
        """
        print("[*] Warming up (loading homepage to pass PerimeterX)...")
        try:
            await page.goto(base_url, wait_until="domcontentloaded", timeout=30000)
        except Exception:
            pass
        await asyncio.sleep(random.uniform(2, 3))

        # Simulate human: random mouse movements
        for _ in range(random.randint(4, 8)):
            await page.mouse.move(
                random.randint(100, 1200),
                random.randint(100, 700),
                steps=random.randint(5, 15),
            )
            await asyncio.sleep(random.uniform(0.1, 0.4))

        # Scroll a bit
        for _ in range(3):
            await page.evaluate(f"window.scrollBy(0, {random.randint(100, 400)})")
            await asyncio.sleep(random.uniform(0.3, 0.7))

        title = await page.title()
        self.log(f"Warmup page title: {title}")

        if "denied" in title.lower() or "blocked" in title.lower() or "access" in title.lower():
            print("[!] PerimeterX still blocking after warm-up.")

            # Try 2captcha PerimeterX solver if key is set
            if TWOCAPTCHA_API_KEY and TWOCAPTCHA_API_KEY != "YOUR_2CAPTCHA_API_KEY":
                token = await solve_perimeterx(TWOCAPTCHA_API_KEY, base_url, self.debug)
                if token:
                    # Inject the solved token and reload
                    await page.evaluate(f"""
                        if (window._pxOnChallengeResolved) {{
                            window._pxOnChallengeResolved('{token}');
                        }}
                    """)
                    await asyncio.sleep(3)
                    return True
            return False

        return True

    # ── network listener ─────────────────────────────────────────────────────

    def _attach_listener(self, page):
        async def handle(response):
            ct = (response.headers.get("content-type") or "").lower()
            if "json" not in ct:
                return
            try:
                body = await response.body()
                data = json.loads(body)
                self._captured.append({"url": response.url, "data": data})
                self.log(f"JSON: {response.url[:100]}")
            except Exception:
                pass
        page.on("response", handle)

    # ── scroll ────────────────────────────────────────────────────────────────

    async def _scroll(self, page, cycles: int):
        for i in range(cycles):
            print(f"[*] Scroll cycle {i+1}/{cycles}...")
            prev_h = await page.evaluate("document.body.scrollHeight")
            for _ in range(6):
                await page.evaluate("window.scrollBy(0, window.innerHeight)")
                await asyncio.sleep(random.uniform(0.5, 1.0))
            for sel in ['button[data-testid="load-more"]', 'button:has-text("Load more")', '[class*="load-more"]']:
                try:
                    btn = await page.query_selector(sel)
                    if btn and await btn.is_visible():
                        await btn.click()
                        await asyncio.sleep(2)
                        break
                except Exception:
                    pass
            await asyncio.sleep(1.5)
            new_h = await page.evaluate("document.body.scrollHeight")
            if new_h == prev_h and i > 0:
                self.log("No new content — stopping scroll")
                break

    # ── extract ───────────────────────────────────────────────────────────────

    def _from_xhr(self) -> list[dict]:
        seen = set()
        results = []
        for entry in self._captured:
            found = extract_vendors_from_blob(entry["data"], seen)
            if found:
                results.extend(found)
                self.log(f"  {len(found)} vendors from {entry['url'][:100]}")
        return results

    async def _from_next_data(self, page) -> list[dict]:
        try:
            raw = await page.evaluate(
                "() => { const e = document.getElementById('__NEXT_DATA__'); return e ? e.textContent : null; }"
            )
            if raw:
                data = json.loads(raw)
                seen = set()
                vendors = extract_vendors_from_blob(data, seen)
                if vendors:
                    print(f"[+] __NEXT_DATA__: {len(vendors)} vendors")
                return vendors
        except Exception as e:
            self.log(f"__NEXT_DATA__ error: {e}")
        return []

    async def _from_css(self, page, base_url: str) -> list[dict]:
        self.log("CSS fallback...")
        for card_sel, name_sel in [
            ('[data-testid="vendor-list-item"]', '[data-testid="vendor-name"]'),
            ('[class*="vendor-card"]',           '[class*="vendor-name"], h3'),
            ('[class*="VendorCard"]',            'h3, h2'),
            ('li[class*="listing"]',             'h3, h2'),
        ]:
            try:
                cards = await page.query_selector_all(card_sel)
                if not cards:
                    continue
                self.log(f"  {len(cards)} cards with '{card_sel}'")
                results = []
                for card in cards:
                    try:
                        name_el = await card.query_selector(name_sel)
                        name = (await name_el.inner_text()).strip() if name_el else ""
                        if not name:
                            continue
                        a_el = await card.query_selector("a")
                        href = (await a_el.get_attribute("href") or "") if a_el else ""
                        if href and not href.startswith("http"):
                            href = base_url.rstrip("/") + href
                        results.append({"name": name, "url": href, "scraped_at": datetime.utcnow().isoformat()})
                    except Exception:
                        continue
                if results:
                    return results
            except Exception as e:
                self.log(f"CSS error: {e}")

        # Link fallback
        try:
            links = await page.evaluate("""
                () => [...document.querySelectorAll('a[href*="/restaurant/"]')].map(a => ({
                    name: a.innerText.trim().split('\\n')[0].trim(),
                    url:  a.href
                })).filter(x => x.name && x.name.length > 1)
            """)
            seen_urls = set()
            results = []
            for lnk in links:
                if lnk["url"] not in seen_urls:
                    seen_urls.add(lnk["url"])
                    results.append({**lnk, "scraped_at": datetime.utcnow().isoformat()})
            if results:
                print(f"[+] Link fallback: {len(results)} restaurants")
            return results
        except Exception:
            pass
        return []

    # ── public: listing ──────────────────────────────────────────────────────

    async def scrape_listing(self, country_key: str, pages: int):
        domain, prefix, lat, lng = COUNTRIES.get(country_key, COUNTRIES["singapore"])
        base_url = domain

        # ── Strategy 0: direct API (fast, no browser) ──
        if HAS_AIOHTTP:
            print("[*] Strategy 0: Direct HTTP API...")
            api_results = await api_fetch_vendors(
                country_key, lat, lng, pages,
                proxy=self.args.proxy, debug=self.debug,
            )
            if api_results:
                self.results = api_results
                return
            print("[*] Direct API returned 0 — falling back to browser...")

        # ── Strategy 1-4: browser ──
        async with async_playwright() as pw:
            browser, ctx = await self._launch(pw)
            try:
                page = await self._new_stealthy_page(ctx)
                self._attach_listener(page)

                # Warm up on homepage to get PerimeterX cookies
                passed = await self._warmup(page, base_url)
                if not passed:
                    print("[!] PerimeterX blocked. Options:")
                    print("    • Run with --headed (real-looking Chrome window helps)")
                    print("    • Use --proxy with a residential IP from 2prx.com")
                    print("    • Set TWOCAPTCHA_API_KEY for automatic PerimeterX bypass")
                    print("    • pip install playwright-stealth (if not installed)")

                # Now navigate to the actual listing
                listing_url = self.args.url or f"{base_url}/restaurants/new"
                print(f"[*] Loading listing: {listing_url}")
                try:
                    await page.goto(listing_url, wait_until="networkidle", timeout=45000)
                except Exception:
                    self.log("networkidle timeout — continuing")

                await asyncio.sleep(3)
                title = await page.title()
                self.log(f"Listing page title: {title}")

                if "denied" in title.lower():
                    print("[!] Still blocked on listing page.")

                await self._scroll(page, pages)
                await asyncio.sleep(2)

                restaurants = self._from_xhr()

                if not restaurants:
                    self.log("XHR gave 0 — trying __NEXT_DATA__")
                    restaurants = await self._from_next_data(page)

                if not restaurants:
                    self.log("Trying CSS...")
                    restaurants = await self._from_css(page, base_url)

                self.results = restaurants
                print(f"[+] Total: {len(restaurants)} restaurants")

            finally:
                await browser.close()

    # ── public: menu ─────────────────────────────────────────────────────────

    async def scrape_menu(self, url: str):
        async with async_playwright() as pw:
            browser, ctx = await self._launch(pw)
            try:
                page = await self._new_stealthy_page(ctx)
                self._attach_listener(page)

                # Warm up on homepage first
                base_url = "/".join(url.split("/")[:3])
                await self._warmup(page, base_url)

                print(f"[*] Loading menu: {url}")
                try:
                    await page.goto(url, wait_until="networkidle", timeout=45000)
                except Exception:
                    self.log("networkidle timeout")
                await asyncio.sleep(3)

                for _ in range(15):
                    await page.evaluate("window.scrollBy(0, window.innerHeight)")
                    await asyncio.sleep(0.5)
                await asyncio.sleep(2)

                menu_items = []
                for entry in self._captured:
                    menu_items.extend(extract_menu_from_blob(entry["data"]))

                if not menu_items:
                    try:
                        raw = await page.evaluate(
                            "() => { const e = document.getElementById('__NEXT_DATA__'); return e ? e.textContent : null; }"
                        )
                        if raw:
                            menu_items = extract_menu_from_blob(json.loads(raw))
                    except Exception as e:
                        self.log(f"Menu NEXT_DATA: {e}")

                self.results = menu_items
                print(f"[+] Found {len(menu_items)} menu items")

            finally:
                await browser.close()

    # ── save ─────────────────────────────────────────────────────────────────

    def save(self):
        if not self.results:
            print("[!] No data to save.")
            return
        fmt = self.args.format.lower()
        out = self.args.output or f"foodpanda_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.{fmt}"
        if fmt == "json":
            with open(out, "w", encoding="utf-8") as f:
                json.dump(self.results, f, ensure_ascii=False, indent=2)
        else:
            keys = list(self.results[0].keys())
            with open(out, "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=keys)
                w.writeheader()
                w.writerows(self.results)
        print(f"[+] Saved {len(self.results)} records → {out}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args():
    p = argparse.ArgumentParser(description="Foodpanda Scraper — Playwright + PerimeterX bypass")
    p.add_argument("--city",    default="singapore",
                   help=f"Country key: {', '.join(COUNTRIES)}")
    p.add_argument("--url",     help="Custom listing URL (overrides --city)")
    p.add_argument("--restaurant-url", dest="restaurant_url",
                   help="Restaurant URL for menu scraping")
    p.add_argument("--menu",    action="store_true",  help="Scrape menu items")
    p.add_argument("--pages",   type=int, default=3,  help="Pagination cycles (default 3)")
    p.add_argument("--format",  choices=["json", "csv"], default="json")
    p.add_argument("--output",  help="Output file path")
    p.add_argument("--proxy",   default="", help="http://user:pass@proxy.2prx.com:8080")
    p.add_argument("--headed",  action="store_true",  help="Show browser window")
    p.add_argument("--debug",   action="store_true",  help="Verbose logging")
    return p.parse_args()


async def main():
    args = parse_args()
    scraper = FoodpandaScraper(args)

    if args.menu:
        url = args.restaurant_url or args.url
        if not url:
            print("[ERROR] --menu requires --restaurant-url or --url")
            sys.exit(1)
        await scraper.scrape_menu(url)
    else:
        city = args.city if args.city in COUNTRIES else "singapore"
        await scraper.scrape_listing(city, args.pages)

    scraper.save()


if __name__ == "__main__":
    asyncio.run(main())
