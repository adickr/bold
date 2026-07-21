"""Optional Playwright helpers for JS-rendered marketplace pages.

Used only when normal HTTP parsing is insufficient. Does not bypass CAPTCHAs
or authentication — it loads public pages in a real browser context.
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
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/126.0.0.0 Safari/537.36"
            ),
            locale="en-ZA",
            viewport={"width": 1440, "height": 1100},
        )
        page = context.new_page()
        try:
            yield page
        finally:
            context.close()
            browser.close()


def fetch_rendered_html(
    url: str, *, wait_selector: str | None = None, timeout_ms: int = 60000
) -> str:
    with browser_page() as page:
        page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        try:
            page.wait_for_load_state("networkidle", timeout=timeout_ms)
        except Exception:
            logger.warning("networkidle timeout for %s", url)
        if wait_selector:
            try:
                page.wait_for_selector(wait_selector, timeout=min(timeout_ms, 20000))
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
                data = response.json()
                captured.append(data)
            except Exception:
                return

        page.on("response", _on_response)
        page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        try:
            page.wait_for_load_state("networkidle", timeout=timeout_ms)
        except Exception:
            logger.warning("networkidle timeout while capturing %s", url_substring)
        page.wait_for_timeout(settle_ms)

        for _ in range(max(1, scroll_rounds)):
            page.mouse.wheel(0, 4200)
            page.wait_for_timeout(900)
            # Click common "next / load more" controls if present
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
