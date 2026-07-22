"""Optional Playwright helpers for JS-rendered marketplace pages.

Used only when normal HTTP parsing is insufficient. Does not bypass CAPTCHAs
or authentication — it loads public pages in a real browser context and waits
for Cloudflare interstitial pages to clear when possible.

For Cars.co.za on a local Mac, prefer:
  PLAYWRIGHT_HEADED=true
  PLAYWRIGHT_USER_DATA_DIR=./data/chrome-profile

The first run may open a Chrome window so you can pass the Cloudflare check;
later runs reuse the saved cookies.
"""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

logger = logging.getLogger(__name__)


def playwright_available() -> bool:
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401

        return True
    except Exception:
        return False


def _headed() -> bool:
    return os.environ.get("PLAYWRIGHT_HEADED", "").strip().lower() in {"1", "true", "yes"}


def _user_data_dir() -> str | None:
    raw = (os.environ.get("PLAYWRIGHT_USER_DATA_DIR") or "").strip()
    if not raw:
        return None
    path = Path(raw).expanduser()
    path.mkdir(parents=True, exist_ok=True)
    return str(path)


@contextmanager
def browser_page(*, headless: bool | None = None) -> Iterator[Any]:
    from playwright.sync_api import sync_playwright

    headed = _headed() if headless is None else (not headless)
    headless = not headed
    user_data = _user_data_dir()

    with sync_playwright() as p:
        context = None
        browser = None
        launch_kwargs: dict[str, Any] = {
            "headless": headless,
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
            ],
        }
        context_kwargs: dict[str, Any] = {
            "user_agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/126.0.0.0 Safari/537.36"
            ),
            "locale": "en-ZA",
            "viewport": {"width": 1440, "height": 1100},
            "timezone_id": "Africa/Johannesburg",
        }

        # Persistent Chrome profile keeps Cloudflare cookies between collects
        if user_data:
            for channel in ("chrome", "chromium", None):
                try:
                    kwargs = dict(launch_kwargs)
                    if channel:
                        kwargs["channel"] = channel
                    context = p.chromium.launch_persistent_context(
                        user_data,
                        **kwargs,
                        **context_kwargs,
                    )
                    logger.info(
                        "Playwright persistent context channel=%s headed=%s dir=%s",
                        channel,
                        headed,
                        user_data,
                    )
                    break
                except Exception as exc:
                    logger.debug("Persistent launch failed channel=%s: %s", channel, exc)
                    context = None
            if context is None:
                raise RuntimeError("Unable to launch persistent Playwright Chromium")
            page = context.pages[0] if context.pages else context.new_page()
            try:
                yield page
            finally:
                context.close()
            return

        for channel in ("chrome", None, "chromium"):
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
        context = browser.new_context(**context_kwargs)
        context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )
        page = context.new_page()
        try:
            yield page
        finally:
            context.close()
            browser.close()


def _wait_through_challenge(page: Any, timeout_ms: int = 120000) -> None:
    """Wait until Cloudflare interstitial clears and used-listing markup appears."""
    deadline_chunk = max(15000, min(timeout_ms, 90000))
    try:
        page.wait_for_function(
            """() => {
              const t = (document.title || '').toLowerCase();
              if (t.includes('just a moment') || t.includes('attention required')) return false;
              if (document.querySelector('#challenge-running, #cf-challenge-running')) return false;
              return true;
            }""",
            timeout=deadline_chunk,
        )
    except Exception:
        logger.warning("Challenge title wait timed out (title=%s)", page.title())

    # CF often clears a few seconds after the title changes — keep polling for inventory
    listing_timeout = max(30000, timeout_ms - deadline_chunk)
    for selector in (
        "a[href*='/for-sale/used/']",
        "a[href*='/for-sale/']",
        "a[href*='/car-for-sale/']",
        "[data-vehicle-id]",
    ):
        try:
            page.wait_for_selector(selector, timeout=listing_timeout)
            page.wait_for_timeout(1500)
            return
        except Exception:
            continue
    logger.warning("No listing selectors after challenge wait (title=%s)", page.title())


def fetch_rendered_html(
    url: str,
    *,
    wait_selector: str | None = None,
    timeout_ms: int = 120000,
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
                page.wait_for_selector(wait_selector, timeout=min(timeout_ms, 45000))
            except Exception:
                logger.warning("Timed out waiting for selector %s on %s", wait_selector, url)
        else:
            page.wait_for_timeout(2000)
        # Extra settle — Cars.co.za hydrates cards after CF clear
        page.wait_for_timeout(2000)
        return page.content()


def fetch_json_from_responses(
    url: str,
    *,
    url_substring: str,
    timeout_ms: int = 120000,
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
            page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 45000))
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
