"""Optional Playwright helpers for JS-rendered marketplace pages.

Used only when normal HTTP parsing is insufficient. Does not bypass CAPTCHAs
or authentication — it loads public pages in a real browser context and waits
for Cloudflare interstitial pages to clear when possible.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Any, Iterator

logger = logging.getLogger(__name__)


def playwright_available() -> bool:
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401

        return True
    except Exception:
        return False


@contextmanager
def browser_page(*, headless: bool = True) -> Iterator[Any]:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        launch_kwargs: dict[str, Any] = {
            "headless": headless,
            "args": ["--disable-blink-features=AutomationControlled"],
        }
        browser = None
        for channel in (None, "chrome", "chromium"):
            try:
                kwargs = dict(launch_kwargs)
                if channel:
                    kwargs["channel"] = channel
                browser = p.chromium.launch(**kwargs)
                break
            except Exception as exc:
                logger.debug("Playwright launch failed channel=%s: %s", channel, exc)
        if browser is None:
            raise RuntimeError("Unable to launch Playwright Chromium")
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/126.0.0.0 Safari/537.36"
            ),
            locale="en-ZA",
            viewport={"width": 1440, "height": 1100},
        )
        context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )
        page = context.new_page()
        try:
            yield page
        finally:
            context.close()
            browser.close()


def _wait_through_challenge(page: Any, timeout_ms: int = 90000) -> None:
    """Wait until Cloudflare / bot interstitial clears, if present."""
    try:
        page.wait_for_function(
            """() => {
              const t = (document.title || '').toLowerCase();
              if (t.includes('just a moment') || t.includes('attention required')) return false;
              if (document.querySelector('#challenge-running, #cf-challenge-running')) return false;
              return true;
            }""",
            timeout=timeout_ms,
        )
    except Exception:
        logger.warning("Challenge wait timed out (title=%s)", page.title())
    # Extra settle after CF — listing markup often hydrates a beat later
    try:
        page.wait_for_selector(
            "a[href*='/for-sale/'], a[href*='/car-for-sale/']",
            timeout=min(20000, timeout_ms),
        )
    except Exception:
        pass


def fetch_rendered_html(
    url: str,
    *,
    wait_selector: str | None = None,
    timeout_ms: int = 90000,
    wait_through_challenge: bool = False,
) -> str:
    with browser_page() as page:
        page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        if wait_through_challenge:
            _wait_through_challenge(page, timeout_ms=timeout_ms)
        try:
            page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 45000))
        except Exception:
            logger.warning("networkidle timeout for %s", url)
        if wait_selector:
            try:
                page.wait_for_selector(wait_selector, timeout=min(timeout_ms, 30000))
            except Exception:
                logger.warning("Timed out waiting for selector %s on %s", wait_selector, url)
        else:
            page.wait_for_timeout(2000)
        return page.content()


def fetch_json_from_responses(
    url: str,
    *,
    url_substring: str,
    timeout_ms: int = 90000,
    settle_ms: int = 3000,
    scroll_rounds: int = 8,
    wait_through_challenge: bool = False,
    json_only: bool = False,
) -> list[Any]:
    """Open a page and capture JSON bodies from matching network responses."""
    captured: list[Any] = []

    with browser_page() as page:

        def _on_response(response) -> None:  # type: ignore[no-untyped-def]
            try:
                if url_substring not in response.url:
                    return
                if response.status >= 400:
                    return
                ctype = (response.headers or {}).get("content-type", "")
                if json_only and "json" not in ctype and "javascript" not in ctype:
                    # Still try .json() — some APIs omit content-type
                    pass
                data = response.json()
                captured.append(data)
            except Exception:
                return

        page.on("response", _on_response)
        page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        if wait_through_challenge:
            _wait_through_challenge(page, timeout_ms=timeout_ms)
        try:
            page.wait_for_load_state("networkidle", timeout=timeout_ms)
        except Exception:
            logger.warning("networkidle timeout while capturing %s", url_substring)
        page.wait_for_timeout(settle_ms)

        for _ in range(max(1, scroll_rounds)):
            page.mouse.wheel(0, 4200)
            page.wait_for_timeout(900)
            for selector in (
                "button:has-text('Load more')",
                "button:has-text('Show more')",
                "a:has-text('Next')",
                "button:has-text('Next')",
                "[aria-label='Next']",
                "button[aria-label*='next' i]",
            ):
                try:
                    loc = page.locator(selector).first
                    if loc.count() and loc.is_visible():
                        loc.click(timeout=1500)
                        page.wait_for_timeout(1200)
                        break
                except Exception:
                    continue
        page.wait_for_timeout(1000)
    return captured
