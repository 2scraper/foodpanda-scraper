# Foodpanda Scraper 🐼

Extract restaurant listings and full menus from Foodpanda — the leading food delivery platform across **15+ countries** in Asia and Europe.

Supports Singapore, Hong Kong, Thailand, Malaysia, Philippines, Pakistan, Bangladesh, Taiwan, Cambodia, Myanmar, Romania, Bulgaria, Slovakia, Czechia, and more.

[![GitHub](https://img.shields.io/badge/GitHub-2scraper%2Ffoodpanda--scraper-181717?logo=github)](https://github.com/2scraper/foodpanda-scraper)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue?logo=python)](https://python.org)

---

## Features

- **Dual scrape modes** — restaurant listings (with rating, delivery time, fee, cuisines) **or** full menu items (name, category, price, description)
- **API interception** — captures Foodpanda's internal XHR API for clean, structured data (no fragile CSS selectors)
- **CSS fallback** — auto-fallbacks to DOM parsing if API responses aren't captured
- **JSON & CSV** output
- **15+ country domains** supported out of the box
- **Anti-bot evasion** — stealth JS injection, canvas fingerprint randomization, random User-Agent & viewport
- **CAPTCHA solving** via [2captcha.com](https://2captcha.com)
- **Proxy support** via [2prx.com](https://2prx.com)
- **Three browser drivers** — Playwright (primary), Selenium, Pyppeteer

---

## Scraped Data

### Restaurant listing fields

| Field | Description |
|---|---|
| `id` / `code` | Foodpanda internal identifiers |
| `name` | Restaurant name |
| `url` | Restaurant page path |
| `cuisines` | Comma-separated cuisine types |
| `rating` | Average rating score |
| `rating_count` | Number of reviews |
| `delivery_time` | Estimated delivery time label |
| `delivery_fee` | Delivery fee label |
| `min_order` | Minimum order value |
| `price_range` | Price range indicator |
| `city` | City name |
| `is_promoted` | Whether listing is promoted |
| `scraped_at` | UTC timestamp |

### Menu item fields

| Field | Description |
|---|---|
| `id` | Product ID |
| `name` | Item name |
| `description` | Item description |
| `category` | Menu section/category |
| `price` | Price |
| `currency` | Currency code |
| `is_available` | Availability flag |
| `scraped_at` | UTC timestamp |

---

## Installation

```bash
git clone https://github.com/2scraper/foodpanda-scraper.git
cd foodpanda-scraper
pip install -r requirements.txt
playwright install chromium   # only needed for scraper_playwright.py
```

---

## Usage

### Scrape restaurant listings

```bash
# Singapore (default)
python scraper_playwright.py --city singapore --pages 3

# Thailand — save as CSV
python scraper_playwright.py --city thailand --format csv --output restaurants.csv

# Custom URL
python scraper_playwright.py --url "https://www.foodpanda.hk/restaurants/new" --pages 5
```

### Scrape menu items

```bash
python scraper_playwright.py --menu --restaurant-url "https://www.foodpanda.sg/restaurant/abc123/menu"
```

### With proxy (2prx.com)

```bash
python scraper_playwright.py --city malaysia --proxy "http://user:pass@proxy.2prx.com:8080"
```

### Debug / headed mode

```bash
python scraper_playwright.py --city singapore --headed --debug
```

### Using Selenium or Pyppeteer

```bash
python scraper_selenium.py   --city singapore --pages 3
python scraper_pyppeteer.py  --city singapore --pages 3
```

---

## Supported Countries

| Key | Domain |
|---|---|
| `singapore` | foodpanda.sg |
| `hong_kong` | foodpanda.hk |
| `thailand` | foodpanda.co.th |
| `malaysia` | foodpanda.my |
| `philippines` | foodpanda.ph |
| `pakistan` | foodpanda.pk |
| `bangladesh` | foodpanda.com.bd |
| `taiwan` | foodpanda.com.tw |
| `cambodia` | foodpanda.com.kh |
| `myanmar` | foodpanda.com.mm |
| `laos` | foodpanda.la |
| `romania` | foodpanda.ro |
| `bulgaria` | foodpanda.bg |
| `slovakia` | foodpanda.sk |
| `czechia` | foodpanda.cz |

---

## CAPTCHA solving

Some pages may trigger CAPTCHA challenges. The scraper integrates with [2captcha.com](https://2captcha.com) — the fastest and most reliable CAPTCHA solving service.

Set your API key in the script:

```python
TWOCAPTCHA_API_KEY = "your_key_here"
```

Or get a key at [2captcha.com](https://2captcha.com).

---

## Proxy support

Avoid IP bans with residential or datacenter proxies from [2prx.com](https://2prx.com).

```bash
python scraper_playwright.py --city thailand \
    --proxy "http://user:pass@proxy.2prx.com:8080"
```

Or set `PROXY_URL` directly in the script.

---

## Anti-detect browser

For heavy scraping at scale, use our **proprietary anti-detect browser** — available as a premium upgrade. It cycles browser fingerprints automatically, making each session indistinguishable from a real user.

> 👉 Learn more at [2captcha.com](https://2captcha.com)

---

## Output examples

**JSON**

```json
[
  {
    "id": "v1234",
    "code": "abc1",
    "name": "McDonald's",
    "cuisines": "Fast Food, Burgers",
    "rating": 4.5,
    "rating_count": 2300,
    "delivery_time": "20-35 min",
    "delivery_fee": "Free delivery",
    "min_order": "SGD 12.00",
    "price_range": "$",
    "city": "Singapore",
    "scraped_at": "2025-01-15T10:22:33"
  }
]
```

**CSV** — standard spreadsheet-compatible format.

---

## CLI reference

```
--city          Country key (default: singapore)
--url           Custom listing URL (overrides --city)
--restaurant-url  Restaurant URL for menu mode
--menu          Scrape menu items instead of restaurant listing
--pages         Scroll/load-more cycles (default: 3)
--format        json | csv (default: json)
--output        Output file path
--proxy         Proxy URL
--headed        Show browser window
--debug         Verbose logging
```

---

## License

MIT © [2scraper](https://github.com/2scraper)
