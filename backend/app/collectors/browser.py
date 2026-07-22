"""Optional Playwright helpers for JS-rendered marketplace pages.

Used only when normal HTTP parsing is insufficient. Does not bypass CAPTCHAs
or authentication — it loads public pages in a real browser context and waits
for Cloudflare interstitial pages to clear when possible.

For Cars.co.za on a local Mac (recommended):
  PLAYWRIGHT_HEADED=true
  PLAYWRIGHT_USER_DATA_DIR=./data/chrome-profile-cars

If Cloudflare still loops on \"Verifying you are human\", connect to a normal
Chrome you control (no automation banner):

  /Applications/Google\\ Chrome.app/Contents/MacOS/Google\\ Chrome \\
    --remote-debugging-port=9222 \\
    --user-data-dir=\"$PWD/data/chrome-cdp\"
  # open cars.co.za once, pass the check, then:
  PLAYWRIGHT_CDP_URL=http://127.0.0.1:9222

Use ONE browser session per source collect so Cloudflare is only cleared once.
"""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

logger = logging.getLogger(__name__)

_STEALTH_INIT = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
window.chrome = window.chrome || { runtime: {} };
Object.defineProperty(navigator, 'languages', { get: () => ['en-ZA', 'en-GB', 'en'] });
Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
"""


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


def _cdp_url() -> str | None:
    raw = (os.environ.get("PLAYWRIGHT_CDP_URL") or "").strip()
    return raw or None


def _storage_state_path() -> Path | None:
    base = _user_data_dir()
    if not base:
        return None
    return Path(base) / "cars-co-za-storage.json"


def _launch_args() -> list[str]:
    return [
        "--disable-blink-features=AutomationControlled",
        "--disable-dev-shm-usage",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-infobars",
        "--exclude-switches=enable-automation",
    ]


def _attach_stealth(context: Any) -> None:
    try:
        context.add_init_script(_STEALTH_INIT)
    except Exception:
        logger.debug("Could not attach stealth init script", exc_info=True)


@contextmanager
def browser_page(*, headless: bool | None = None) -> Iterator[Any]:
    """Yield a single Playwright page. Prefer CDP → persistent Chrome → launch."""
    from playwright.sync_api import sync_playwright

    headed = _headed() if headless is None else (not headless)
    headless = not headed
    user_data = _user_data_dir()
    storage_path = _storage_state_path()
    cdp = _cdp_url()

    with sync_playwright() as p:
        # 1) Connect to a real user-controlled Chrome (best for Cloudflare)
        if cdp:
            browser = p.chromium.connect_over_cdp(cdp)
            context = browser.contexts[0] if browser.contexts else browser.new_context()
            _attach_stealth(context)
            page = context.pages[0] if context.pages else context.new_page()
            logger.info("Playwright connected over CDP: %s", cdp)
            try:
                yield page
            finally:
                # Do not close the user's Chrome — only disconnect
                try:
                    browser.close()
                except Exception:
                    pass
            return

        context = None
        browser = None
        launch_kwargs: dict[str, Any] = {
            "headless": headless,
            "args": _launch_args(),
            # Removes the yellow "Chrome is being controlled by automated test software" bar
            # that Cloudflare uses as a strong bot signal.
            "ignore_default_args": ["--enable-automation"],
        }

        # 2) Persistent profile: stable fingerprint + CF cookies across collects
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
                    _attach_stealth(context)
                    logger.info(
                        "Playwright persistent context channel=%s headed=%s dir=%s stealth=1",
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

        # 3) Ephemeral launch
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
        _attach_stealth(context)
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
        body = (page.inner_text("body") or "").lower()[:2000]
    except Exception:
        body = ""
    if "verifying you are human" in body or "confirm you are human" in body:
        return True
    if "security service to protect against malicious bots" in body:
        return True
    try:
        return bool(
            page.query_selector(
                "#challenge-running, #cf-challenge-running, .cf-turnstile, "
                "iframe[src*='challenges.cloudflare'], #challenge-stage"
            )
        )
    except Exception:
        return False


def _wait_through_challenge(page: Any, timeout_ms: int = 120000) -> bool:
    """Wait until Cloudflare clears. Returns True if page looks clear."""
    if not is_cloudflare_challenge(page):
        for selector in (
            "a[href*='/for-sale/used/']",
            "a[href*='/for-sale/']",
            "a[href*='/car-for-sale/']",
            "[data-vehicle-id]",
        ):
            try:
                page.wait_for_selector(selector, timeout=min(8000, timeout_ms))
                return True
            except Exception:
                continue
        return True

    headed = _headed()
    if headed:
        logger.warning(
            "Cloudflare challenge — if a checkbox appears, click it once. "
            "If it only says 'Verifying…' and loops, close this window and use "
            "PLAYWRIGHT_CDP_URL with a normal Chrome (see README). Waiting up to %ss…",
            timeout_ms // 1000,
        )
    else:
        logger.warning(
            "Cloudflare challenge in headless mode (often never clears). Waiting %ss…",
            timeout_ms // 1000,
        )

    try:
        page.wait_for_function(
            """() => {
              const t = (document.title || '').toLowerCase();
              if (t.includes('just a moment') || t.includes('attention required')) return false;
              if (document.querySelector('#challenge-running, #cf-challenge-running')) return false;
              const body = (document.body && document.body.innerText || '').toLowerCase();
              if (body.includes('verifying you are human')) return false;
              if (body.includes('confirm you are human')) return false;
              return true;
            }""",
            timeout=timeout_ms,
        )
    except Exception:
        logger.warning(
            "Challenge wait timed out (title=%s) — giving up on this navigation",
            page.title(),
        )
        return False

    for selector in (
        "a[href*='/for-sale/used/']",
        "a[href*='/for-sale/']",
        "a[href*='/car-for-sale/']",
        "[data-vehicle-id]",
    ):
        try:
            page.wait_for_selector(selector, timeout=min(30000, timeout_ms))
            page.wait_for_timeout(1000)
            return True
        except Exception:
            continue
    logger.warning("No listing selectors after challenge wait (title=%s)", page.title())
    return not is_cloudflare_challenge(page)


def goto_and_wait(
    page: Any,
    url: str,
    *,
    wait_selector: str | None = None,
    timeout_ms: int = 120000,
    wait_through_challenge: bool = False,
) -> str:
    """Navigate an existing page and return HTML (keeps CF cookies in-session)."""
    page.goto(url, wait_until="domcontentloaded", timeout=min(timeout_ms, 90000))
    if wait_through_challenge or is_cloudflare_challenge(page):
        cleared = _wait_through_challenge(page, timeout_ms=timeout_ms)
        if not cleared:
            return page.content()
    try:
        page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 20000))
    except Exception:
        logger.debug("networkidle timeout for %s", url)
    if wait_selector and not is_cloudflare_challenge(page):
        try:
            page.wait_for_selector(wait_selector, timeout=min(timeout_ms, 15000))
        except Exception:
            logger.debug("Timed out waiting for selector %s on %s", wait_selector, url)
    page.wait_for_timeout(600)
    return page.content()


def soft_goto(
    page: Any,
    url: str,
    *,
    wait_selector: str | None = None,
    timeout_ms: int = 45000,
) -> str:
    """Same-session navigation after CF is already cleared — only wait if challenged again."""
    if is_cloudflare_challenge(page):
        logger.warning("Skipping soft goto — still on Cloudflare challenge")
        return page.content()
    page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
    if is_cloudflare_challenge(page):
        logger.warning("Cloudflare reappeared on soft navigation to %s — not looping wait", url)
        # Short wait only; do not hang the collect job
        _wait_through_challenge(page, timeout_ms=min(45000, timeout_ms))
    try:
        page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 15000))
    except Exception:
        logger.debug("networkidle timeout for soft goto %s", url)
    if wait_selector and not is_cloudflare_challenge(page):
        try:
            page.wait_for_selector(wait_selector, timeout=min(timeout_ms, 12000))
        except Exception:
            logger.debug("Timed out waiting for selector %s on %s", wait_selector, url)
    page.wait_for_timeout(500)
    return page.content()


def click_next_if_present(page: Any) -> bool:
    """Advance search results via in-page Next control (avoids a full new-document CF)."""
    if is_cloudflare_challenge(page):
        return False
    for selector in (
        "a[rel='next']",
        "button[aria-label*='next' i]",
        "a[aria-label*='next' i]",
        "a:has-text('Next')",
        "button:has-text('Next')",
        ".pagination a:has-text('›')",
        ".pagination a:has-text('>')",
    ):
        try:
            loc = page.locator(selector).first
            if not loc.count() or not loc.is_visible():
                continue
            loc.click(timeout=2500)
            try:
                page.wait_for_load_state("domcontentloaded", timeout=15000)
            except Exception:
                pass
            page.wait_for_timeout(800)
            if is_cloudflare_challenge(page):
                logger.warning("Cloudflare after Next click — stopping pagination")
                return False
            return True
        except Exception:
            continue
    return False


def fetch_rendered_html(
    url: str,
    *,
    wait_selector: str | None = None,
    timeout_ms: int = 120000,
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
            if is_cloudflare_challenge(page):
                break
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
