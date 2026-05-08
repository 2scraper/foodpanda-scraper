"""
Foodpanda Scraper — Selenium (fixed)

Strategy 0 — Direct HTTP API (fastest, no browser)
Strategy 1 — undetected-chromedriver (bypasses PerimeterX bot detection)
Strategy 2 — __NEXT_DATA__ / window state extraction
Strategy 3 — CSS selector + link fallback

Usage:
    pip install -r requirements.txt
    python scraper_selenium.py --city singapore --pages 3
    python scraper_selenium.py --city singapore --headed
    python scraper_selenium.py --menu --restaurant-url "https://www.foodpanda.sg/restaurant/..."
    python scraper_selenium.py --city singapore --proxy "http://user:pass@proxy.2prx.com:8080"
    python scraper_selenium.py --city singapore --debug
"""

import argparse
import json
import csv
import random
import sys
import time
from datetime import datetime

try:
    import requests as req_lib
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

# Prefer undetected-chromedriver to bypass PerimeterX
try:
    import undetected_chromedriver as uc
    HAS_UC = True
except ImportError:
    HAS_UC = False
    print("[WARN] undetected-chromedriver not installed. Run: pip install undetected-chromedriver")
    print("[WARN] Without it PerimeterX will likely block headless mode.\n")

try:
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.common.exceptions import NoSuchElementException, TimeoutException
except ImportError:
    print("[ERROR] selenium not installed. Run: pip install selenium")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

TWOCAPTCHA_API_KEY = "YOUR_2CAPTCHA_API_KEY"
PROXY_URL          = ""

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
    budget   = v.get("budget")          if isinstance(v.get("budget"),          dict) else {}
    rating   = v.get("rating")          if isinstance(v.get("rating"),          dict) else {}
    chars    = v.get("characteristics") if isinstance(v.get("characteristics"), dict) else {}
    address  = v.get("address")         if isinstance(v.get("address"),         dict) else {}
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
    pv = variations[0] if isinstance(variations, list) and variations else {}
    pv = pv if isinstance(pv, dict) else {}
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
    """Recursively walk any JSON structure to find vendor objects."""
    results = []
    if isinstance(data, dict):
        if (data.get("name") and
                (data.get("id") or data.get("code")) and
                "cuisines" in data):
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
    """Recursively find menu sections with products."""
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
# 2captcha — PerimeterX solver
# ---------------------------------------------------------------------------

def solve_perimeterx(api_key: str, site_url: str, debug: bool = False) -> str | None:
    if not HAS_REQUESTS:
        print("[WARN] requests required for 2captcha. pip install requests")
        return None
    if debug:
        print(f"[2captcha] Submitting PerimeterX for {site_url}")
    r = req_lib.post("https://2captcha.com/in.php", data={
        "key": api_key, "method": "perimeterx", "pageurl": site_url, "json": 1
    })
    data = r.json()
    if data.get("status") != 1:
        print(f"[2captcha] Error: {data}")
        return None
    task_id = data["request"]
    for _ in range(36):
        time.sleep(5)
        r = req_lib.get(f"https://2captcha.com/res.php?key={api_key}&action=get&id={task_id}&json=1")
        result = r.json()
        if result.get("status") == 1:
            if debug:
                print("[2captcha] PerimeterX solved ✓")
            return result["request"]
        if result.get("request") != "CAPCHA_NOT_READY":
            print(f"[2captcha] Error: {result}")
            return None
    return None


# ---------------------------------------------------------------------------
# Strategy 0: Direct HTTP API
# ---------------------------------------------------------------------------

def api_fetch_vendors(country_key: str, lat: float, lng: float,
                       pages: int, proxy: str = "", debug: bool = False) -> list[dict]:
    if not HAS_REQUESTS:
        return []

    _, prefix, _, _ = COUNTRIES.get(country_key, COUNTRIES["singapore"])
    base    = f"https://{prefix}.fd-api.com"
    ua      = random.choice(USER_AGENTS)
    headers = {
        "User-Agent":      ua,
        "Accept":          "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer":         COUNTRIES[country_key][0] + "/",
        "Origin":          COUNTRIES[country_key][0],
        "x-fp-api-key":    "volo",
    }
    proxies = {"http": proxy, "https": proxy} if proxy else None
    results = []
    seen    = set()

    for page_num in range(pages):
        offset = page_num * 48
        params = {
            "latitude": lat, "longitude": lng,
            "language_id": 1, "include": "characteristics",
            "dynamic_pricing": 0, "configuration": "Variant1",
            "budgets": "", "cuisine": "", "sort": "",
            "food_characteristic": "", "use_free_delivery_label": "false",
            "vertical": "restaurants", "limit": 48, "offset": offset,
        }
        try:
            r = req_lib.get(f"{base}/api/v5/vendors", params=params,
                            headers=headers, proxies=proxies, timeout=20)
            if debug:
                print(f"[API] {r.status_code} {r.url}")
            if r.status_code != 200:
                print(f"[API] HTTP {r.status_code} — stopping")
                break
            data  = r.json()
            found = extract_vendors_from_blob(data, seen)
            results.extend(found)
            print(f"[API] Page {page_num+1}: {len(found)} vendors (total {len(results)})")
            if not found:
                break
            time.sleep(random.uniform(0.8, 1.5))
        except Exception as e:
            print(f"[API] Error: {e}")
            break

    return results


# ---------------------------------------------------------------------------
# CDP response capture for Selenium
# ---------------------------------------------------------------------------

class CDPCapture:
    """Capture XHR JSON responses via Chrome DevTools Protocol."""
    def __init__(self, driver):
        self.driver   = driver
        self.captured: list[dict] = []

    def enable(self):
        self.driver.execute_cdp_cmd("Network.enable", {})

    def harvest(self) -> list[dict]:
        """Pull all JSON responses from Chrome performance log."""
        new_entries = []
        try:
            logs = self.driver.get_log("performance")
        except Exception:
            return new_entries

        for entry in logs:
            try:
                msg = json.loads(entry["message"])["message"]
                if msg.get("method") != "Network.responseReceived":
                    continue
                url = msg["params"]["response"]["url"]
                ct  = msg["params"]["response"].get("headers", {}).get("content-type", "")
                if "json" not in ct.lower():
                    continue
                req_id = msg["params"]["requestId"]
                try:
                    body_obj = self.driver.execute_cdp_cmd(
                        "Network.getResponseBody", {"requestId": req_id}
                    )
                    body = json.loads(body_obj.get("body", "{}"))
                    new_entries.append({"url": url, "data": body})
                    self.captured.append({"url": url, "data": body})
                except Exception:
                    pass
            except Exception:
                pass
        return new_entries


# ---------------------------------------------------------------------------
# Scraper
# ---------------------------------------------------------------------------

class FoodpandaScraperSelenium:
    def __init__(self, args):
        self.args    = args
        self.debug   = args.debug
        self.results: list[dict] = []

    def log(self, msg):
        if self.debug:
            print(f"[DEBUG] {msg}")

    # ── driver ───────────────────────────────────────────────────────────────

    def _build_driver(self):
        proxy = self.args.proxy or PROXY_URL

        if HAS_UC:
            # undetected-chromedriver: bypasses most PerimeterX checks
            opts = uc.ChromeOptions()
            if not self.args.headed:
                opts.add_argument("--headless=new")
            opts.add_argument("--no-sandbox")
            opts.add_argument("--disable-dev-shm-usage")
            opts.add_argument(f"--window-size={random.randint(1366,1920)},{random.randint(768,1080)}")
            if proxy:
                opts.add_argument(f"--proxy-server={proxy}")
                self.log(f"Proxy: {proxy}")
            # Enable performance logging for CDP capture
            opts.set_capability("goog:loggingPrefs", {"performance": "ALL"})
            driver = uc.Chrome(options=opts)
            self.log("Using undetected-chromedriver ✓")
        else:
            # Fallback: standard selenium with manual stealth patches
            opts = Options()
            if not self.args.headed:
                opts.add_argument("--headless=new")
            opts.add_argument("--no-sandbox")
            opts.add_argument("--disable-dev-shm-usage")
            opts.add_argument("--disable-blink-features=AutomationControlled")
            opts.add_experimental_option("excludeSwitches", ["enable-automation"])
            opts.add_experimental_option("useAutomationExtension", False)
            opts.add_argument(f"--user-agent={random.choice(USER_AGENTS)}")
            opts.add_argument(f"--window-size={random.randint(1366,1920)},{random.randint(768,1080)}")
            if proxy:
                opts.add_argument(f"--proxy-server={proxy}")
            opts.set_capability("goog:loggingPrefs", {"performance": "ALL"})
            driver = webdriver.Chrome(options=opts)
            driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
                "source": """
                    Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                    Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
                    window.chrome = {runtime: {}};
                """
            })
        return driver

    # ── PerimeterX warm-up ───────────────────────────────────────────────────

    def _warmup(self, driver, base_url: str) -> bool:
        print("[*] Warming up on homepage (PerimeterX cookie acquisition)...")
        try:
            driver.get(base_url)
        except Exception:
            pass
        time.sleep(random.uniform(2.5, 4))

        # Simulate human mouse movement via JS
        for _ in range(random.randint(3, 6)):
            x, y = random.randint(100, 1200), random.randint(100, 700)
            driver.execute_script(
                f"document.dispatchEvent(new MouseEvent('mousemove', {{clientX:{x}, clientY:{y}}}))"
            )
            time.sleep(random.uniform(0.1, 0.4))

        # Scroll a bit
        for _ in range(3):
            driver.execute_script(f"window.scrollBy(0, {random.randint(100, 400)})")
            time.sleep(random.uniform(0.3, 0.7))

        title = driver.title
        self.log(f"Warmup title: {title}")

        if any(w in title.lower() for w in ["denied", "blocked", "access"]):
            print("[!] PerimeterX blocking after warm-up.")
            if TWOCAPTCHA_API_KEY != "YOUR_2CAPTCHA_API_KEY":
                token = solve_perimeterx(TWOCAPTCHA_API_KEY, base_url, self.debug)
                if token:
                    driver.execute_script(
                        f"if(window._pxOnChallengeResolved) window._pxOnChallengeResolved('{token}');"
                    )
                    time.sleep(3)
                    return True
            return False
        return True

    # ── scroll ────────────────────────────────────────────────────────────────

    def _scroll(self, driver, cycles: int):
        for i in range(cycles):
            print(f"[*] Scroll cycle {i+1}/{cycles}...")
            prev_h = driver.execute_script("return document.body.scrollHeight")
            for _ in range(6):
                driver.execute_script("window.scrollBy(0, window.innerHeight)")
                time.sleep(random.uniform(0.5, 1.0))
            for sel in [
                '[data-testid="load-more"]', '.load-more-btn',
                "//button[contains(text(),'Load more')]",
            ]:
                try:
                    by = By.XPATH if sel.startswith("//") else By.CSS_SELECTOR
                    btn = driver.find_element(by, sel)
                    if btn.is_displayed():
                        btn.click()
                        time.sleep(2)
                        break
                except NoSuchElementException:
                    pass
                except Exception:
                    pass
            time.sleep(1.5)
            new_h = driver.execute_script("return document.body.scrollHeight")
            if new_h == prev_h and i > 0:
                self.log("No new content — stopping scroll early")
                break

    # ── extract ───────────────────────────────────────────────────────────────

    def _from_cdp(self, capture: CDPCapture) -> list[dict]:
        entries = capture.harvest()
        seen    = set()
        results = []
        for entry in entries:
            found = extract_vendors_from_blob(entry["data"], seen)
            if found:
                results.extend(found)
                self.log(f"  {len(found)} vendors from {entry['url'][:100]}")
        return results

    def _from_next_data(self, driver) -> list[dict]:
        try:
            raw = driver.execute_script(
                "const e = document.getElementById('__NEXT_DATA__'); return e ? e.textContent : null;"
            )
            if raw:
                data    = json.loads(raw)
                seen    = set()
                vendors = extract_vendors_from_blob(data, seen)
                if vendors:
                    print(f"[+] __NEXT_DATA__: {len(vendors)} vendors")
                return vendors
        except Exception as e:
            self.log(f"__NEXT_DATA__ error: {e}")
        return []

    def _from_window_state(self, driver) -> list[dict]:
        for expr in ["window.__INITIAL_STATE__", "window.__data__", "window.__PRELOADED_STATE__"]:
            try:
                raw = driver.execute_script(f"return JSON.stringify({expr} || null)")
                if raw and raw != "null":
                    data    = json.loads(raw)
                    seen    = set()
                    vendors = extract_vendors_from_blob(data, seen)
                    if vendors:
                        self.log(f"{expr}: {len(vendors)} vendors")
                        return vendors
            except Exception:
                pass
        return []

    def _from_css(self, driver, base_url: str) -> list[dict]:
        self.log("CSS fallback...")
        strategies = [
            ('[data-testid="vendor-list-item"]', '[data-testid="vendor-name"]'),
            ('[class*="vendor-card"]',           '[class*="vendor-name"], h3'),
            ('[class*="VendorCard"]',            'h3, h2'),
            ('li[class*="listing"]',             'h3, h2'),
        ]
        for card_sel, name_sel in strategies:
            try:
                cards = driver.find_elements(By.CSS_SELECTOR, card_sel)
                if not cards:
                    continue
                self.log(f"  {len(cards)} cards with '{card_sel}'")
                results = []
                for card in cards:
                    try:
                        name_el = card.find_element(By.CSS_SELECTOR, name_sel)
                        name = name_el.text.strip()
                    except Exception:
                        name = ""
                    if not name:
                        continue
                    try:
                        a_el = card.find_element(By.TAG_NAME, "a")
                        href = a_el.get_attribute("href") or ""
                    except Exception:
                        href = ""
                    if href and not href.startswith("http"):
                        href = base_url.rstrip("/") + href
                    results.append({
                        "name": name, "url": href,
                        "scraped_at": datetime.utcnow().isoformat()
                    })
                if results:
                    return results
            except Exception as e:
                self.log(f"CSS error: {e}")

        # Link fallback
        try:
            links = driver.execute_script("""
                return [...document.querySelectorAll('a[href*="/restaurant/"]')].map(a => ({
                    name: a.innerText.trim().split('\\n')[0].trim(),
                    url:  a.href
                })).filter(x => x.name && x.name.length > 1);
            """)
            seen_urls = set()
            results   = []
            for lnk in links:
                if lnk["url"] not in seen_urls:
                    seen_urls.add(lnk["url"])
                    results.append({**lnk, "scraped_at": datetime.utcnow().isoformat()})
            if results:
                print(f"[+] Link fallback: {len(results)} restaurants")
            return results
        except Exception as e:
            self.log(f"Link fallback error: {e}")
        return []

    # ── public: listing ──────────────────────────────────────────────────────

    def scrape_listing(self, country_key: str, pages: int):
        domain, prefix, lat, lng = COUNTRIES.get(country_key, COUNTRIES["singapore"])

        # Strategy 0: direct API
        if HAS_REQUESTS:
            print("[*] Strategy 0: Direct HTTP API...")
            api_results = api_fetch_vendors(
                country_key, lat, lng, pages,
                proxy=self.args.proxy, debug=self.debug
            )
            if api_results:
                self.results = api_results
                return
            print("[*] Direct API returned 0 — falling back to browser...")

        # Strategy 1-4: browser
        driver  = self._build_driver()
        capture = CDPCapture(driver)
        capture.enable()

        try:
            passed = self._warmup(driver, domain)
            if not passed:
                print("[!] PerimeterX blocked. Try --headed, --proxy, or set TWOCAPTCHA_API_KEY.")

            listing_url = self.args.url or f"{domain}/restaurants/new"
            print(f"[*] Loading listing: {listing_url}")
            driver.get(listing_url)
            time.sleep(random.uniform(3, 5))
            self.log(f"Title: {driver.title}")

            self._scroll(driver, pages)
            time.sleep(2)

            restaurants = self._from_cdp(capture)

            if not restaurants:
                self.log("CDP gave 0 — trying __NEXT_DATA__")
                restaurants = self._from_next_data(driver)

            if not restaurants:
                restaurants = self._from_window_state(driver)

            if not restaurants:
                restaurants = self._from_css(driver, domain)

            self.results = restaurants
            print(f"[+] Total: {len(restaurants)} restaurants")

        finally:
            driver.quit()

    # ── public: menu ─────────────────────────────────────────────────────────

    def scrape_menu(self, url: str):
        driver  = self._build_driver()
        capture = CDPCapture(driver)
        capture.enable()

        try:
            base_url = "/".join(url.split("/")[:3])
            self._warmup(driver, base_url)

            print(f"[*] Loading menu: {url}")
            driver.get(url)
            time.sleep(random.uniform(3, 5))

            for _ in range(15):
                driver.execute_script("window.scrollBy(0, window.innerHeight)")
                time.sleep(0.5)
            time.sleep(2)

            capture.harvest()
            menu_items = []
            for entry in capture.captured:
                menu_items.extend(extract_menu_from_blob(entry["data"]))

            if not menu_items:
                try:
                    raw = driver.execute_script(
                        "const e=document.getElementById('__NEXT_DATA__'); return e?e.textContent:null;"
                    )
                    if raw:
                        menu_items = extract_menu_from_blob(json.loads(raw))
                except Exception as e:
                    self.log(f"Menu NEXT_DATA: {e}")

            self.results = menu_items
            print(f"[+] Found {len(menu_items)} menu items")

        finally:
            driver.quit()

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
    p = argparse.ArgumentParser(description="Foodpanda Scraper — Selenium")
    p.add_argument("--city",    default="singapore",
                   help=f"Country key: {', '.join(COUNTRIES)}")
    p.add_argument("--url",     help="Custom listing URL")
    p.add_argument("--restaurant-url", dest="restaurant_url",
                   help="Restaurant URL for menu scraping")
    p.add_argument("--menu",    action="store_true")
    p.add_argument("--pages",   type=int, default=3)
    p.add_argument("--format",  choices=["json", "csv"], default="json")
    p.add_argument("--output")
    p.add_argument("--proxy",   default="", help="http://user:pass@proxy.2prx.com:8080")
    p.add_argument("--headed",  action="store_true")
    p.add_argument("--debug",   action="store_true")
    return p.parse_args()


def main():
    args    = parse_args()
    scraper = FoodpandaScraperSelenium(args)

    if args.menu:
        url = args.restaurant_url or args.url
        if not url:
            print("[ERROR] --menu requires --restaurant-url or --url")
            sys.exit(1)
        scraper.scrape_menu(url)
    else:
        city = args.city if args.city in COUNTRIES else "singapore"
        scraper.scrape_listing(city, args.pages)

    scraper.save()


if __name__ == "__main__":
    main()
