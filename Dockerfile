# Builds the Playwright engine (the one the README recommends) into a
# container.
#
#   docker build -t foodpanda-scraper .
#   docker run --rm -v "$PWD/out:/out" foodpanda-scraper \
#     --url "https://www.foodpanda.pk/city/lahore/area/gulberg" \
#     --pages 3 --out /out/gulberg
#
# Pass --proxy/--twocaptcha-key the same way as running locally, or mount a
# .env at /app/.env — nothing here bakes in a credential, and .dockerignore
# keeps one out of the build context. A .env baked into an image is a
# credential published to everyone who can pull it.
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt requirements-playwright.txt ./

# `playwright install chromium` is enough for this site, unlike one sibling
# repo where the bundled build is refused and real Chrome is required.
# Measured 2026-09-15: Playwright's own Chromium was served by ten of the
# eleven foodpanda country sites.
#
# `--with-deps` also pulls Chromium's shared-library dependencies through
# apt, which are not pip packages and so cannot ride in requirements.txt.
RUN pip install --no-cache-dir -r requirements.txt -r requirements-playwright.txt \
    && playwright install --with-deps chromium

# Every module playwright_scraper.py imports, transitively, plus diff_runs.py
# as a useful companion in the same image. smoke_test.py checks this list
# against the entrypoint's real import graph: three repos in this family
# shipped an image missing proxy_pool.py, which the engine imports at module
# level, so it died with ModuleNotFoundError on every invocation INCLUDING
# `--help` — a broken container that nothing in the repo would have noticed.
COPY captcha_solver.py env_config.py fingerprint_client.py output_writer.py \
     page_flow.py playwright_scraper.py product_parser.py proxy_pool.py \
     scraper_api_client.py diff_runs.py ./

# READ THIS BEFORE FILING A BUG ABOUT THE IMAGE BEING BLOCKED.
#
# There is no display in a container, so the engine runs headless here — and
# headless is the window this site is least generous with. Every measurement
# in this repo's README was taken with --headful on a desktop; a headless run
# is one more thing PerimeterX can key on, on a site whose refusal is already
# a matter of degree.
#
# So an image that gets refused where your laptop is not is behaving as
# expected. The levers, in the order worth trying: raise --delay, pass a
# residential --proxy (ideally in the site's own country), or point
# --cdp-endpoint at the 2Captcha Scraping Browser API, which brings its own
# browser and its own exit.
ENV FOODPANDA_DOCKER=1

ENTRYPOINT ["python3", "playwright_scraper.py", "--headless"]
CMD ["--help"]
