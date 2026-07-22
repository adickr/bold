"""Optional Playwright helpers for JS-rendered marketplace pages.

Used only when normal HTTP parsing is insufficient. Does not bypass CAPTCHAs
or authentication — it loads public pages in a real browser context and waits
for Cloudflare interstitial pages to clear when possible.

For Cars.co.za on a local Mac, prefer:
  PLAYWRIGHT_HEADED=true
  PLAYWRIGHT_USER_DATA_DIR=./data/chrome-profile

Use ONE browser session per source collect so Cloudflare is only cleared once.
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
    path = Path(raw).expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True)
    return str(path)


def _storage_state_path() -> Path | None:
    base = _user_data_dir()
    if not base:
        return None
    return Path(base) / "cars-co-za-storage.json"


@contextmanager
def browser_page(*, headless: bool | None = None) -> Iterator[Any]:
    """Yield a single Playwright page. Prefer persistent Chrome for CF cookies."""
    from playwright.sync_api import sync_playwright

    headed = _headed() if headless is None else (not headless)
    headless = not headed
    user_data = _user_data_dir()
    storage_path = _storage_state_path()

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

        # Persistent profile: do NOT override UA/viewport — keep a stable fingerprint
        # so Cloudflare cookies from a prior checkbox stay valid.
        if user_data:
            for channel in ("chrome", "chromium", None):
                try:
                    kwargs = dict(launch_kwargs)
                    if channel:
                        kwargs["channel"] = channel
                    context = p.chromium.launch_persistent_context(
                        user_data,
                        locale="en-ZA",
                        timezone_id="Africa/Johannesburg",
                        **kwargs,
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
                try:
                    if storage_path is not None:
                        context.storage_state(path=str(storage_path))
                except Exception:
                    logger.debug("Could not persist storage_state", exc_info=True)
                context.close()
            return

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
        if storage_path and storage_path.exists():
            context_kwargs["storage_state"] = str(storage_path)

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
            try:
                if storage_path is not None:
                    context.storage_state(path=str(storage_path))
            except Exception:
                logger.debug("Could not persist storage_state", exc_info=True)
            context.close()
            browser.close()


def is_cloudflare_challenge(page: Any) -> bool:
    try:
        title = (page.title() or "").lower()
    except Exception:
        title = ""
    if "just a moment" in title or "attention required" in title:
        return True
    try:
        return bool(page.query_selector("#challenge-running, #cf-challenge-running, .cf-turnstile"))
    except Exception:
        return False


def _wait_through_challenge(page: Any, timeout_ms: int = 180000) -> None:
    """Wait until Cloudflare clears. Fast-path when already cleared."""
    if not is_cloudflare_challenge(page):
        for selector in (
            "a[href*='/for-sale/used/']",
            "a[href*='/for-sale/']",
            "a[href*='/car-for-sale/']",
            "[data-vehicle-id]",
        ):
            try:
                page.wait_for_selector(selector, timeout=min(12000, timeout_ms))
                return
            except Exception:
                continue
        return

    headed = _headed()
    if headed:
        logger.warning(
            "Cloudflare challenge detected — complete the checkbox in the Chrome window "
            "(waiting up to %ss)…",
            timeout_ms // 1000,
        )

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
        logger.warning("Challenge title wait timed out (title=%s)", page.title())

    for selector in (
        "a[href*='/for-sale/used/']",
        "a[href*='/for-sale/']",
        "a[href*='/car-for-sale/']",
        "[data-vehicle-id]",
    ):
        try:
            page.wait_for_selector(selector, timeout=min(45000, timeout_ms))
            page.wait_for_timeout(1500)
            return
        except Exception:
            continue
    logger.warning("No listing selectors after challenge wait (title=%s)", page.title())


def goto_and_wait(
    page: Any,
    url: str,
    *,
    wait_selector: str | None = None,
    timeout_ms: int = 180000,
    wait_through_challenge: bool = False,
) -> str:
    """Navigate an existing page and return HTML (keeps CF cookies in-session)."""
    page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
    if wait_through_challenge:
        _wait_through_challenge(page, timeout_ms=timeout_ms)
    try:
        page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 30000))
    except Exception:
        logger.debug("networkidle timeout for %s", url)
    if wait_selector and not is_cloudflare_challenge(page):
        try:
            page.wait_for_selector(wait_selector, timeout=min(timeout_ms, 20000))
        except Exception:
            logger.debug("Timed out waiting for selector %s on %s", wait_selector, url)
    page.wait_for_timeout(1200)
    return page.content()


def fetch_rendered_html(
    url: str,
    *,
    wait_selector: str | None = None,
    timeout_ms: int = 180000,
    wait_through_challenge: bool = False,
) -> str:
    with browser_page() as page:
        return goto_and_wait(
            page,
            url,
            wait_selector=wait_selector,
            timeout_ms=timeout_ms,
            wait_through_challenge=wait_through_challenge,
        )


def fetch_json_from_responses(
    url: str,
    *,
    url_substring: str,
    timeout_ms: int = 180000,
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
                data = response.json()
                captured.append(data)
            except Exception:
                return

        page.on("response", _on_response)
        goto_and_wait(
            page,
            url,
            wait_through_challenge=wait_through_challenge,
            timeout_ms=timeout_ms,
        )
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
        page.wait_for_timeout(800)
    return captured
