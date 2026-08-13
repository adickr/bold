"""AutoTrader South Africa collector."""

from __future__ import annotations

import json
import logging
import re
from typing import Any
from urllib.parse import urlencode, urlparse

from bs4 import BeautifulSoup

from app.collectors.base import BaseCollector, CollectorError
from app.collectors.browser import browser_page, playwright_available
from app.schemas.listings import ListingPayload
from app.services.normalise import detect_drivetrain

logger = logging.getLogger(__name__)

# Real detail URLs look like:
# /car-for-sale/toyota/fortuner/2.8gd-6-4x4-vx/28096596
DETAIL_PATH_RE = re.compile(
    r"^/car-for-sale/(?:[^/]+/){1,12}(?P<id>\d{6,})/?$",
    re.I,
)

# Suburb/city fragments that often appear on AutoTrader cards without "Western Cape"
_LOCATION_HINT_RE = re.compile(
    r"(?i)\b("
    r"western\s*cape|cape\s*town|stellenbosch|paarl|somerset\s*west|"
    r"brackenfell|bellville|george|knysna|mossel\s*bay|worcester|strand|"
    r"hermanus|durbanville|milnerton|table\s*view|claremont|goodwood|"
    r"parow|blouberg|parklands|rondebosch|newlands|kuils\s*river|"
    r"century\s*city|tyger\s*valley|montague\s*gardens|melkbos|"
    r"fish\s*hoek|hout\s*bay|atlantis|caledon|swellendam|oudtshoorn|"
    r"plettenberg|beaufort\s*west"
    r")\b"
)


class AutoTraderCollector(BaseCollector):
    source = "autotrader"
    category = "marketplace"

    SEARCH_URL = "https://www.autotrader.co.za/cars-for-sale/toyota/fortuner"
    # Province URLs need the province id segment (p-9 = Western Cape)
    SEARCH_URL_WC = "https://www.autotrader.co.za/cars-for-sale/western-cape/p-9/toyota/fortuner"

    def search_base_url(self) -> str:
        preferred = (self.settings.preferred_province or "").strip().lower()
        if preferred in {"western cape", "wc", "western-cape"}:
            return self.SEARCH_URL_WC
        return self.SEARCH_URL

    def build_search_params(self, page: int = 1) -> dict[str, Any]:
        """Match the live site filter shape that returns the full WC 4x4 set.

        Example (65 results):
        /cars-for-sale/western-cape/p-9/toyota/fortuner
          ?mileage=less-than-100000&transmissiondrive=4x4
        """
        mileage_cap = max(1, int(self.settings.max_mileage_km))
        params: dict[str, Any] = {
            "mileage": f"less-than-{mileage_cap}",
        }
        req = (self.settings.required_drivetrain or "").strip().lower().replace(" ", "")
        if req in {"4x4", "4wd", "awd"}:
            params["transmissiondrive"] = "4x4"
        elif req in {"4x2", "2wd"}:
            params["transmissiondrive"] = "4x2"
        if page > 1:
            params["pagenumber"] = page
        return params

    def search(self) -> list[ListingPayload]:
        all_listings: list[ListingPayload] = []
        max_pages = max(1, self.settings.collector_max_pages)
        result_count: int | None = None
        page_count: int | None = None
        site_unavailable = False

        # Prefer HTTP with the correct filter URL; fall back to Playwright when
        # HTTP is blocked or only returns a thin SSR slice of the result set.
        http_ok = False
        for page in range(1, max_pages + 1):
            if page_count is not None and page > page_count:
                break
            url = f"{self.search_base_url()}?{urlencode(self.build_search_params(page))}"
            page_listings, meta_rc, meta_pc, unavailable = self._search_one_page_http_meta(url)
            site_unavailable = site_unavailable or unavailable
            if meta_rc is not None:
                result_count = meta_rc
            if meta_pc is not None:
                page_count = meta_pc
            if not page_listings:
                if page == 1:
                    break
                logger.info("AutoTrader page %s returned 0 listings — stopping", page)
                break
            http_ok = True
            before = len(all_listings)
            all_listings.extend(page_listings)
            all_listings = self._dedupe(all_listings)
            gained = len(all_listings) - before
            logger.info(
                "AutoTrader page %s: parsed %s (unique total %s, +%s, site_total=%s, page_count=%s)",
                page,
                len(page_listings),
                len(all_listings),
                gained,
                result_count,
                page_count,
            )
            if gained == 0:
                break
            if result_count is not None and len(all_listings) >= result_count:
                break
            # Do not stop just because a page has fewer than 8 cards — AT pages vary

        thin_http = False
        if http_ok:
            if result_count is not None and len(all_listings) < max(8, int(result_count * 0.5)):
                thin_http = True
            elif result_count is None and len(all_listings) < 8:
                thin_http = True
        need_playwright = (not http_ok or thin_http) and playwright_available() and self.settings.use_playwright
        if need_playwright:
            reason = "blocked/empty" if not http_ok else f"thin HTTP ({len(all_listings)}/{result_count})"
            logger.info("AutoTrader escalating to Playwright (%s)", reason)
            try:
                pw_listings, pw_unavailable = self._search_all_pages_playwright(max_pages)
                site_unavailable = site_unavailable or pw_unavailable
                if len(pw_listings) > len(all_listings):
                    all_listings = pw_listings
            except Exception:
                logger.exception("AutoTrader Playwright search failed")

        listings = [x for x in all_listings if self.is_detail_url(x.url)]
        listings = [
            ListingPayload.model_validate(
                {
                    **item.model_dump(),
                    "url": self.improve_detail_url(
                        item.url,
                        variant=item.variant_raw,
                        title=item.title,
                        listing_id=item.source_listing_id,
                    )
                    or item.url,
                }
            )
            for item in listings
        ]
        listings = [x for x in listings if self._is_plausible_card(x)]
        listings = self._annotate_search_scope(listings)
        if not listings:
            if site_unavailable:
                raise CollectorError(
                    "AutoTrader: site unavailable (HTTP 503 / IP blocked). "
                    "Same Wi‑Fi as this app is blocked — try a phone hotspot or VPN, "
                    "confirm https://www.autotrader.co.za loads in a browser on that network, "
                    "then Collect again. (CDP/headed Chrome will not help if the IP itself is blocked.)",
                    parser_broken=False,
                )
            raise CollectorError("AutoTrader: no listings parsed", parser_broken=True)
        logger.info("AutoTrader collect finished with %s plausible listings", len(listings))
        return listings

    @staticmethod
    def _looks_unavailable(html: str | None, *, status: int | None = None) -> bool:
        if status is not None and status >= 500:
            return True
        text = (html or "").lower()
        if not text:
            return False
        if "server unavailable" in text:
            return True
        if "please check back later" in text and "autotrader" in text:
            return True
        if len(text) < 2000 and "your ip address is:" in text:
            return True
        return False

    def _search_one_page_http(self, url: str) -> list[ListingPayload]:
        rows, _, _, _ = self._search_one_page_http_meta(url)
        return rows

    def _search_one_page_http_meta(
        self, url: str
    ) -> tuple[list[ListingPayload], int | None, int | None, bool]:
        try:
            self.throttle()
            response = self.client.get(url)
            html = response.text or ""
            self.snapshot_raw("search", html)
            if self._looks_unavailable(html, status=response.status_code):
                logger.warning(
                    "AutoTrader HTTP unavailable (status=%s) for %s",
                    response.status_code,
                    url,
                )
                return [], None, None, True
            if response.status_code >= 400:
                logger.warning(
                    "AutoTrader HTTP status %s for %s", response.status_code, url
                )
                return [], None, None, response.status_code >= 500
            if "just a moment" in html.lower() or len(html) < 5000:
                logger.warning("AutoTrader HTTP page looks blocked/empty for %s", url)
                return [], None, None, False
            rc, pc = self._parse_result_meta(html)
            return [x for x in self.parse_all(html) if self.is_detail_url(x.url)], rc, pc, False
        except Exception:
            logger.exception("AutoTrader HTTP search failed for %s", url)
            return [], None, None, False

    def _search_all_pages_playwright(self, max_pages: int) -> tuple[list[ListingPayload], bool]:
        """One browser session for all AutoTrader search pages (cards / embedded JSON)."""
        from app.collectors.browser import soft_goto

        all_listings: list[ListingPayload] = []
        result_count: int | None = None
        page_count: int | None = None
        unavailable = False
        with browser_page() as page:
            for page_num in range(1, max_pages + 1):
                if page_count is not None and page_num > page_count:
                    break
                url = f"{self.search_base_url()}?{urlencode(self.build_search_params(page_num))}"
                if page_num == 1:
                    resp = page.goto(url, wait_until="domcontentloaded", timeout=60000)
                    status = resp.status if resp else None
                    try:
                        page.wait_for_load_state("networkidle", timeout=45000)
                    except Exception:
                        pass
                    page.wait_for_timeout(2000)
                else:
                    soft_goto(page, url, timeout_ms=60000)
                    status = None
                page.mouse.wheel(0, 3500)
                page.wait_for_timeout(800)
                html = page.content()
                self.snapshot_raw(f"search_rendered_p{page_num}", html)
                if self._looks_unavailable(html, status=status):
                    unavailable = True
                    logger.warning("AutoTrader Playwright page looks unavailable (status=%s)", status)
                    break
                rc, pc = self._parse_result_meta(html)
                if rc is not None:
                    result_count = rc
                if pc is not None:
                    page_count = pc
                page_listings = self._listings_from_playwright_page(page, html)
                if not page_listings:
                    break
                before = len(all_listings)
                all_listings.extend(page_listings)
                all_listings = self._dedupe(all_listings)
                logger.info(
                    "AutoTrader Playwright page %s: +%s (unique %s / site_total=%s)",
                    page_num,
                    len(all_listings) - before,
                    len(all_listings),
                    result_count,
                )
                if len(all_listings) == before:
                    break
                if result_count is not None and len(all_listings) >= result_count:
                    break
        return all_listings, unavailable

    def _listings_from_playwright_page(self, page: Any, html: str) -> list[ListingPayload]:
        listings = [x for x in self.parse_all(html) if self.is_detail_url(x.url)]

        # Browser card hrefs usually have the full SEO slug; JSON canonicalUrl is often
        # the short `/2.8gd-6/{id}` form that 503s in a new tab.
        dom_hrefs: dict[str, str] = {}
        try:
            rows = page.eval_on_selector_all(
                "a[href*='/car-for-sale/']",
                """els => els.map(a => ({ href: a.href || a.getAttribute('href') || '' }))""",
            )
            for row in rows or []:
                href = ((row or {}).get("href") or "").split("?")[0]
                if not self.is_detail_url(href):
                    continue
                lid = self.listing_id_from_url(href)
                if not lid:
                    continue
                prev = dom_hrefs.get(lid)
                if prev is None or self._url_slug_score(href) > self._url_slug_score(prev):
                    dom_hrefs[lid] = href
        except Exception:
            logger.exception("AutoTrader DOM href scrape failed")

        if listings:
            enriched: list[ListingPayload] = []
            for item in listings:
                href = dom_hrefs.get(item.source_listing_id)
                data = item.model_dump()
                if href and self._url_slug_score(href) >= self._url_slug_score(item.url or ""):
                    data["url"] = href
                data["url"] = self.improve_detail_url(
                    data.get("url"),
                    variant=data.get("variant_raw"),
                    title=data.get("title"),
                    listing_id=data.get("source_listing_id"),
                ) or data.get("url")
                enriched.append(ListingPayload.model_validate(data))
            return [x for x in enriched if self._is_plausible_card(x)]

        rows = page.eval_on_selector_all(
            "a[href*='/car-for-sale/']",
            """els => els.map(a => {
              let card = a.closest('article, li, [data-testid*="listing"], [data-testid*="result"]');
              if (!card) {
                // Walk up until a single detail-id card, never a multi-listing wrapper
                let node = a.parentElement;
                while (node && node !== document.body) {
                  const hrefs = [...node.querySelectorAll("a[href*='/car-for-sale/']")]
                    .map(x => (x.getAttribute('href') || ''))
                    .filter(h => /\\/\\d{6,}\\/?$/.test(h.split('?')[0]));
                  const ids = new Set(hrefs.map(h => (h.match(/(\\d{6,})\\/?$/) || [])[1]).filter(Boolean));
                  if (ids.size === 1) { card = node; break; }
                  if (ids.size > 1) break;
                  node = node.parentElement;
                }
              }
              if (!card) card = a.parentElement;
              let text = card ? card.innerText : a.innerText;
              if (card) {
                const hrefs = [...card.querySelectorAll("a[href*='/car-for-sale/']")]
                  .map(x => (x.getAttribute('href') || ''));
                const ids = new Set(hrefs.map(h => (h.match(/(\\d{6,})\\/?$/) || [])[1]).filter(Boolean));
                if (ids.size > 1) text = a.innerText || '';
              }
              const img = card ? card.querySelector('img') : null;
              return {
                href: a.href,
                text: text || '',
                img: img ? (img.currentSrc || img.src || img.getAttribute('data-src') || '') : ''
              };
            })""",
        )
        results: list[ListingPayload] = []
        seen: set[str] = set()
        for row in rows or []:
            href = (row or {}).get("href") or ""
            if not self.is_detail_url(href):
                continue
            listing_id = self.listing_id_from_url(href)
            if not listing_id or listing_id in seen:
                continue
            seen.add(listing_id)
            text = row.get("text") or ""
            if "fortuner" not in text.lower() and "fortuner" not in href.lower():
                continue
            # Prefer link-local title; never trust giant ancestor text for drivetrain
            title = self._resolve_title(self._title_from_text(text), href, text)
            compact = " ".join(
                (self._title_from_text(text) or "", title, href)
            )[:200]
            url = self.improve_detail_url(
                href.split("?")[0],
                variant=title,
                title=title,
                listing_id=listing_id,
            ) or href.split("?")[0]
            results.append(
                ListingPayload(
                    source=self.source,
                    source_listing_id=listing_id,
                    url=url,
                    title=title,
                    variant_raw=title,
                    price_zar=self._extract_price(text) if text.strip() else None,
                    mileage_km=self._extract_mileage(text) if text.strip() else None,
                    year=self._extract_year(text) or self._extract_year(href),
                    dealer_location=self._location_from_text(text) or self._location_from_url(href),
                    drivetrain=self._resolve_drivetrain(href, compact, title),
                    image_urls=[row["img"]] if row.get("img") else [],
                    make="Toyota",
                    model="Fortuner",
                )
            )
        return [x for x in results if self._is_plausible_card(x)]

    def search_with_playwright(self, url: str) -> list[ListingPayload]:
        """Legacy single-URL helper. Prefer _search_all_pages_playwright for collects."""
        with browser_page() as page:
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            try:
                page.wait_for_load_state("networkidle", timeout=45000)
            except Exception:
                pass
            page.wait_for_timeout(2500)
            page.mouse.wheel(0, 3500)
            page.wait_for_timeout(1500)
            html = page.content()
            self.snapshot_raw("search_rendered", html)
            return self._listings_from_playwright_page(page, html)

    @staticmethod
    def _merge_listing_payloads(primary: ListingPayload, secondary: ListingPayload) -> ListingPayload:
        """Prefer richer fields; keep the more specific detail URL."""
        data = primary.model_dump()
        other = secondary.model_dump()
        for key in (
            "title",
            "variant_raw",
            "price_zar",
            "mileage_km",
            "year",
            "dealer_name",
            "dealer_location",
            "drivetrain",
            "transmission",
            "fuel_type",
            "colour",
            "image_urls",
            "raw_payload",
        ):
            cur = data.get(key)
            nxt = other.get(key)
            if key == "image_urls":
                if not cur and nxt:
                    data[key] = nxt
                continue
            if cur in (None, "", [], {}) and nxt not in (None, "", [], {}):
                data[key] = nxt
            elif key in {"title", "variant_raw"} and nxt and len(str(nxt)) > len(str(cur or "")):
                data[key] = nxt
        # Prefer the more specific SEO slug (short /2.8gd-6/{id} often 503s)
        primary_url = data.get("url") or ""
        secondary_url = other.get("url") or ""
        if AutoTraderCollector._url_slug_score(secondary_url) > AutoTraderCollector._url_slug_score(
            primary_url
        ):
            data["url"] = secondary_url
        return ListingPayload.model_validate(data)

    def parse_all(self, html: str) -> list[ListingPayload]:
        """Union JSON + HTML parsers so dropped JSON tiles can still be recovered."""
        by_id: dict[str, ListingPayload] = {}
        for parser in (
            self.parse_search_results_json,
            self.parse_search_html,
            self.parse_vehicle_data_blobs,
            self.parse_embedded_json,
        ):
            for item in parser(html):
                if not self.is_detail_url(item.url):
                    continue
                lid = item.source_listing_id
                existing = by_id.get(lid)
                if existing is None:
                    by_id[lid] = item
                else:
                    # Embedded JSON is richest when present — prefer its fields
                    if existing.raw_payload and not item.raw_payload:
                        by_id[lid] = self._merge_listing_payloads(existing, item)
                    elif item.raw_payload and not existing.raw_payload:
                        by_id[lid] = self._merge_listing_payloads(item, existing)
                    else:
                        by_id[lid] = self._merge_listing_payloads(existing, item)
        return list(by_id.values())

    @staticmethod
    def _balanced_json_object(html: str, start: int) -> str | None:
        if start < 0 or start >= len(html) or html[start] != "{":
            return None
        depth = 0
        in_string = False
        escape = False
        for i, ch in enumerate(html[start:], start):
            if in_string:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_string = False
                continue
            if ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return html[start : i + 1]
        return None

    def _json_object_for_listing_id(self, html: str, needle_start: int, listing_id: str) -> dict[str, Any] | None:
        """Walk brace opens backward until we get the object whose listingId matches."""
        pos = needle_start + 1
        while True:
            start = html.rfind("{", 0, pos)
            if start < 0:
                return None
            blob = self._balanced_json_object(html, start)
            pos = start
            if not blob:
                continue
            try:
                data = json.loads(blob)
            except Exception:
                continue
            if not isinstance(data, dict):
                continue
            if str(data.get("listingId") or "") == listing_id:
                return data

    def parse_search_results_json(self, html: str) -> list[ListingPayload]:
        """Parse AutoTrader's embedded results.featuredTiles / listing JSON blobs."""
        results: list[ListingPayload] = []
        seen: set[str] = set()
        for match in re.finditer(r'"listingId"\s*:\s*(\d+)', html or ""):
            listing_id = match.group(1)
            if listing_id in seen:
                continue
            data = self._json_object_for_listing_id(html or "", match.start(), listing_id)
            if not data:
                continue
            path = data.get("canonicalUrl") or ""
            if not path:
                continue
            if path.startswith("/"):
                url = f"https://www.autotrader.co.za{path}"
            else:
                url = str(path)
            url = url.split("?")[0]
            if not self.is_detail_url(url):
                continue
            if str(data.get("make") or data.get("makeModel") or "").lower().find("toyota") < 0 and "fortuner" not in url.lower():
                # Still allow when model says Fortuner
                if str(data.get("model") or "").lower() != "fortuner":
                    continue
            if str(data.get("model") or "").lower() not in {"", "fortuner"} and "fortuner" not in (
                str(data.get("makeModel") or "") + " " + url
            ).lower():
                continue

            variant = str(data.get("variant") or data.get("makeModelLongVariant") or "")
            title = str(data.get("makeModelLongVariant") or "").strip()
            if not title:
                title = f"Toyota Fortuner {variant}".strip() or self._resolve_title(None, url)
            price = self._extract_price(str(data.get("price") or ""))
            mileage = None
            transmission = None
            fuel = None
            for icon in data.get("summaryIcons") or []:
                if not isinstance(icon, dict):
                    continue
                text = str(icon.get("text") or "")
                url_icon = str(icon.get("url") or "").lower()
                if "mileage" in url_icon or re.search(r"\bkm\b", text, re.I):
                    mileage = self._extract_mileage(text) or mileage
                elif "transmission" in url_icon:
                    low = text.lower()
                    if "auto" in low:
                        transmission = "automatic"
                    elif "manual" in low:
                        transmission = "manual"
                elif "diesel" in url_icon or "petrol" in url_icon or text.lower() in {"diesel", "petrol"}:
                    low = text.lower()
                    if "diesel" in low:
                        fuel = "diesel"
                    elif "petrol" in low:
                        fuel = "petrol"

            suburb = data.get("dealerSuburbName") or data.get("dealerCityName")
            loc = None
            if suburb:
                loc = f"{suburb}, Western Cape" if self._is_wc_search() else str(suburb)
            drivetrain = (
                self.drivetrain_from_url(url)
                or detect_drivetrain(variant)
                or detect_drivetrain(title)
            )
            image = data.get("imageUrl") or data.get("featuredImageUrl")
            seen.add(listing_id)
            results.append(
                ListingPayload(
                    source=self.source,
                    source_listing_id=listing_id,
                    url=url,
                    title=title,
                    variant_raw=variant or title,
                    price_zar=price,
                    mileage_km=mileage,
                    year=self._extract_year(title) or self._extract_year(url),
                    dealer_name=data.get("dealerName"),
                    dealer_location=loc,
                    drivetrain=drivetrain,
                    transmission=transmission,
                    fuel_type=fuel,
                    image_urls=[str(image)] if image else [],
                    make="Toyota",
                    model="Fortuner",
                    raw_payload=data,
                )
            )
        return results

    def _card_container_for_link(self, link) -> Any:
        """Pick the tightest ancestor that contains only this listing's detail link."""
        listing_id = self.listing_id_from_url(link.get("href") or "")
        best = link
        for parent in link.parents:
            if getattr(parent, "name", None) not in {"div", "article", "li", "section"}:
                continue
            if not hasattr(parent, "select"):
                break
            ids: set[str] = set()
            for anchor in parent.select("a[href*='/car-for-sale/']"):
                href = anchor.get("href") or ""
                if href.startswith("/"):
                    href = f"https://www.autotrader.co.za{href}"
                lid = self.listing_id_from_url(href.split("?")[0])
                if lid:
                    ids.add(lid)
            if listing_id and listing_id in ids and len(ids) == 1:
                best = parent
                if parent.name in {"article", "li"}:
                    break
                continue
            if len(ids) > 1:
                break
        return best

    def parse_search_html(self, html: str) -> list[ListingPayload]:
        soup = BeautifulSoup(html, "html.parser")
        # Only singular /car-for-sale/.../{id} detail links — never /cars-for-sale/ search links
        anchors = soup.select("a[href*='/car-for-sale/']")
        results: list[ListingPayload] = []
        seen: set[str] = set()
        for link in anchors:
            try:
                href = link.get("href")
                if not href:
                    continue
                if href.startswith("/"):
                    href = f"https://www.autotrader.co.za{href}"
                href = href.split("?")[0]
                if not self.is_detail_url(href):
                    continue
                listing_id = self.listing_id_from_url(href)
                if not listing_id or listing_id in seen:
                    continue
                seen.add(listing_id)
                container = self._card_container_for_link(link)
                title_el = None
                if hasattr(container, "select_one"):
                    title_el = container.select_one("h2, h3, .title, [data-testid='listing-title']")
                meta = (
                    container.get_text(" ", strip=True)
                    if container is not None and container is not link
                    else link.get_text(" ", strip=True)
                )
                # Cap meta so page-level filter chips don't dominate
                if len(meta) > 500:
                    meta = meta[:500]
                # If the chosen container still looks multi-card, fall back to link text only
                if hasattr(container, "select"):
                    sibling_ids = set()
                    for anchor in container.select("a[href*='/car-for-sale/']"):
                        ah = anchor.get("href") or ""
                        if ah.startswith("/"):
                            ah = f"https://www.autotrader.co.za{ah}"
                        lid = self.listing_id_from_url(ah.split("?")[0])
                        if lid:
                            sibling_ids.add(lid)
                    if len(sibling_ids) > 1:
                        meta = link.get_text(" ", strip=True)
                        title_el = None
                price_el = (
                    container.select_one(".price, [data-testid='price']")
                    if hasattr(container, "select_one") and meta
                    else None
                )
                location_el = (
                    container.select_one(".location, [data-testid='location']")
                    if hasattr(container, "select_one")
                    else None
                )
                dealer_el = (
                    container.select_one(".dealer, [data-testid='dealer']")
                    if hasattr(container, "select_one")
                    else None
                )
                img = container.select_one("img") if hasattr(container, "select_one") else None
                image_urls = []
                if img is not None:
                    for attr in ("src", "data-src", "data-lazy-src", "data-original"):
                        if img.has_attr(attr) and img.get(attr):
                            image_urls.append(img.get(attr))
                            break
                    if not image_urls and img.has_attr("srcset"):
                        image_urls.append(img.get("srcset").split(",")[0].strip().split(" ")[0])
                raw_title = title_el.get_text(strip=True) if title_el else (link.get_text(strip=True) or None)
                if (
                    "fortuner" not in (raw_title or "").lower()
                    and "fortuner" not in meta.lower()
                    and "fortuner" not in href.lower()
                ):
                    continue
                title = self._resolve_title(raw_title, href, meta)
                loc = location_el.get_text(strip=True) if location_el else None
                loc = loc or self._location_from_text(meta) or self._location_from_url(href)
                compact = f"{title} {raw_title or ''} {href}"
                results.append(
                    ListingPayload(
                        source=self.source,
                        source_listing_id=listing_id,
                        url=href,
                        title=title,
                        variant_raw=title,
                        price_zar=self._extract_price(price_el.get_text() if price_el else meta),
                        mileage_km=self._extract_mileage(meta),
                        year=self._extract_year(meta) or self._extract_year(href),
                        dealer_location=loc,
                        dealer_name=dealer_el.get_text(strip=True) if dealer_el else None,
                        drivetrain=self._resolve_drivetrain(href, compact, title),
                        image_urls=image_urls,
                        make="Toyota",
                        model="Fortuner",
                    )
                )
            except Exception:
                logger.exception("Failed parsing AutoTrader card")
        return [x for x in results if self._is_plausible_card(x)]

    def parse_vehicle_data_blobs(self, html: str) -> list[ListingPayload]:
        results: list[ListingPayload] = []
        for match in re.finditer(r"vehicle_data\s*=\s*(\{.*?\});", html, flags=re.S):
            try:
                data = json.loads(match.group(1))
            except Exception:
                continue
            results.extend(self._from_vehicle_data(data))
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup.select("script"):
            text = tag.string or ""
            if "Fortuner" not in text or "price" not in text.lower():
                continue
            for blob in re.findall(r"\{[^{}]{0,40}\"id\"[^{}]{0,200}Fortuner[^{}]{0,200}\}", text):
                try:
                    data = json.loads(blob)
                except Exception:
                    continue
                results.extend(self._from_vehicle_data(data))
        return results

    def _from_vehicle_data(self, data: dict[str, Any]) -> list[ListingPayload]:
        listing_id = str(data.get("id") or data.get("listingId") or data.get("advertId") or "")
        title = data.get("title") or data.get("name") or data.get("derivative")
        raw_url = data.get("url") or data.get("listingUrl") or data.get("seoUrl")
        if raw_url and raw_url.startswith("/"):
            raw_url = f"https://www.autotrader.co.za{raw_url}"
        if raw_url and not self.is_detail_url(raw_url):
            raw_url = None
        if not listing_id and raw_url:
            listing_id = self.listing_id_from_url(raw_url) or ""
        if not listing_id:
            return []
        if title and "fortuner" not in str(title).lower() and data.get("model") != "Fortuner":
            return []
        # Only emit if we have a real detail URL — never invent short /car-for-sale/{id}
        if not raw_url:
            return []
        price = data.get("price") or data.get("priceZar") or data.get("askingPrice")
        mileage = data.get("mileage") or data.get("odometer") or data.get("km")
        loc = data.get("suburb") or data.get("city") or data.get("province")
        if isinstance(loc, dict):
            loc = loc.get("name") or loc.get("city") or loc.get("province")
        blob = f"{title or ''} {raw_url} {loc or ''}"
        drivetrain = (
            self.drivetrain_from_url(raw_url)
            or data.get("drivetrain")
            or data.get("driveType")
            or detect_drivetrain(blob)
        )
        resolved_title = self._resolve_title(str(title) if title else None, raw_url)
        return [
            ListingPayload(
                source=self.source,
                source_listing_id=listing_id,
                url=raw_url.split("?")[0],
                title=resolved_title,
                variant_raw=resolved_title,
                year=data.get("year") or data.get("modelYear"),
                price_zar=int(price) if price not in (None, "") else None,
                mileage_km=int(mileage) if mileage not in (None, "") else None,
                colour=data.get("colour") or data.get("color"),
                dealer_name=(data.get("dealer") or {}).get("name")
                if isinstance(data.get("dealer"), dict)
                else data.get("dealerName"),
                dealer_location=str(loc) if loc else self._location_from_url(raw_url),
                image_urls=list(data.get("images") or data.get("imageUrls") or []),
                make="Toyota",
                model="Fortuner",
                drivetrain=drivetrain,
                transmission=data.get("transmission"),
                fuel_type=data.get("fuel") or data.get("fuelType"),
                raw_payload=data,
            )
        ]

    def parse_embedded_json(self, html: str) -> list[ListingPayload]:
        # Prefer explicit URL captures over inventing paths
        results: list[ListingPayload] = []
        seen: set[str] = set()
        for match in re.finditer(
            r"https://www\.autotrader\.co\.za/car-for-sale/toyota/fortuner/[^\"'\s>]+/\d{6,}",
            html,
            flags=re.I,
        ):
            href = match.group(0).split("?")[0]
            listing_id = self.listing_id_from_url(href)
            if not listing_id or listing_id in seen:
                continue
            seen.add(listing_id)
            results.append(
                ListingPayload(
                    source=self.source,
                    source_listing_id=listing_id,
                    url=href,
                    title=f"Toyota Fortuner {listing_id}",
                    make="Toyota",
                    model="Fortuner",
                )
            )
        return results

    def parse_fixture_html(self, html: str) -> list[ListingPayload]:
        return self._annotate_search_scope(self.parse_search_html(html))

    def _annotate_search_scope(self, listings: list[ListingPayload]) -> list[ListingPayload]:
        """Keep cards that match the active drivetrain requirement.

        Live AutoTrader SEO slugs often omit `4x4` (e.g. `/2.8gd-6/{id}`) even when
        the search was filtered with `transmissiondrive=4x4`. Trust that marketplace
        filter unless the URL/title explicitly says 4x2.
        """
        preferred = (self.settings.preferred_province or "").strip()
        is_wc = preferred.lower() in {"western cape", "wc", "western-cape"}
        req = (self.settings.required_drivetrain or "").strip().lower().replace(" ", "")
        require_4x4 = req in {"4x4", "4wd", "awd"}
        require_4x2 = req in {"4x2", "2wd"}
        # We only add transmissiondrive=… when require_* is set (see build_search_params)
        search_asserted_4x4 = require_4x4
        search_asserted_4x2 = require_4x2

        out: list[ListingPayload] = []
        dropped = 0
        for item in listings:
            data = item.model_dump()
            url_dt = self.drivetrain_from_url(data.get("url"))
            # Prefer compact variant_raw (embedded JSON) over noisy HTML titles
            compact = " ".join(
                filter(None, [data.get("variant_raw"), data.get("title")])
            )
            text_dt = None
            if compact and not self._is_chip_title(compact):
                text_dt = detect_drivetrain(compact)
            elif compact:
                # Chip-only strings are not evidence of axle
                text_dt = None

            detected = url_dt or text_dt
            if require_4x4:
                if url_dt == "4x2" or text_dt == "4x2":
                    dropped += 1
                    continue
                if url_dt == "4x4" or text_dt == "4x4":
                    data["drivetrain"] = "4x4"
                elif search_asserted_4x4:
                    # Marketplace already filtered axle — keep the card
                    data["drivetrain"] = "4x4"
                else:
                    dropped += 1
                    continue
            elif require_4x2:
                if url_dt == "4x4" or text_dt == "4x4":
                    dropped += 1
                    continue
                if detected == "4x2" or search_asserted_4x2:
                    data["drivetrain"] = "4x2"
                else:
                    dropped += 1
                    continue
            elif detected:
                data["drivetrain"] = detected
            if self._is_chip_title(data.get("title")):
                data["title"] = self._resolve_title(data.get("title"), data.get("url"), compact)
                data["variant_raw"] = data.get("variant_raw") or data["title"]
            if is_wc:
                loc = (data.get("dealer_location") or "").strip()
                if not loc:
                    data["dealer_location"] = self._location_from_url(data.get("url") or "") or preferred
                elif "western cape" not in loc.lower() and not _LOCATION_HINT_RE.search(loc):
                    data["dealer_location"] = f"{loc}, {preferred}"
            out.append(ListingPayload.model_validate(data))
        if dropped:
            logger.info(
                "AutoTrader: dropped %s card(s) outside drivetrain filter",
                dropped,
            )
        return [x for x in out if self._is_plausible_card(x)]

    def _is_wc_search(self) -> bool:
        preferred = (self.settings.preferred_province or "").strip().lower()
        return preferred in {"western cape", "wc", "western-cape"}

    @staticmethod
    def _parse_result_meta(html: str) -> tuple[int | None, int | None]:
        rc = re.search(r'"resultCount"\s*:\s*(\d+)', html or "")
        pc = re.search(r'"pageCount"\s*:\s*(\d+)', html or "")
        return (
            int(rc.group(1)) if rc else None,
            int(pc.group(1)) if pc else None,
        )

    @staticmethod
    def _location_from_text(text: str) -> str | None:
        m = _LOCATION_HINT_RE.search(text or "")
        if not m:
            return None
        found = re.sub(r"\s+", " ", m.group(1)).strip().title()
        if "western cape" in found.lower():
            return "Western Cape"
        if "cape town" in found.lower():
            return "Cape Town, Western Cape"
        return f"{found}, Western Cape"

    @staticmethod
    def _location_from_url(url: str) -> str | None:
        # .../car-for-sale/toyota/fortuner/.../western-cape/.../{id}
        if re.search(r"western[\-_]?cape", url or "", re.I):
            return "Western Cape"
        return None

    @classmethod
    def is_detail_url(cls, url: str | None) -> bool:
        if not url:
            return False
        path = urlparse(url).path
        return bool(DETAIL_PATH_RE.match(path))

    @classmethod
    def listing_id_from_url(cls, url: str) -> str | None:
        path = urlparse(url).path
        m = DETAIL_PATH_RE.match(path)
        if m:
            return m.group("id")
        # Last resort: final numeric segment only on /car-for-sale/ paths
        if "/car-for-sale/" not in path:
            return None
        tail = path.rstrip("/").split("/")[-1]
        return tail if tail.isdigit() and len(tail) >= 6 else None

    @staticmethod
    def _slug_has_engine(slug: str | None) -> bool:
        return bool(re.search(r"\d+\.\d+gd-?6|\bgd-?6\b|\d+\.\d+", (slug or "").lower()))

    @classmethod
    def _slug_from_url(cls, url: str | None) -> str | None:
        path = urlparse(url or "").path.lower().rstrip("/")
        m = re.search(r"/car-for-sale/toyota/fortuner/([^/]+)/(\d{6,})$", path)
        return m.group(1) if m else None

    @classmethod
    def _merge_seo_slugs(cls, base: str | None, extra: str | None) -> str | None:
        """Keep engine tokens from ``base``; append useful trim/axle bits from ``extra``."""
        base = (base or "").strip("-").lower()
        extra = (extra or "").strip("-").lower()
        if not base and not extra:
            return None
        if not base:
            return extra or None
        if not extra:
            return base
        if not cls._slug_has_engine(extra) and cls._slug_has_engine(base):
            # e.g. base=2.8gd-6 + extra=4x4-auto → 2.8gd-6-4x4-auto
            parts = [base]
            for token in extra.split("-"):
                if not token or token in base:
                    continue
                if token in {"toyota", "fortuner"}:
                    continue
                parts.append(token)
            return "-".join(parts)[:90]
        # Prefer the candidate that keeps engine + more specificity
        if cls._slug_has_engine(extra):
            if cls._slug_has_engine(base) and len(base) > len(extra) and all(
                t in base for t in extra.split("-") if t
            ):
                return base
            return extra
        return base

    @classmethod
    def _url_slug_score(cls, url: str | None) -> int:
        """Higher = more specific SEO slug. Short `/2.8gd-6/{id}` paths often 503."""
        path = urlparse(url or "").path.lower().rstrip("/")
        m = re.search(r"/car-for-sale/toyota/fortuner/([^/]+)/(\d{6,})$", path)
        if not m:
            return 0
        slug = m.group(1)
        score = len(slug)
        for token in ("4x4", "4x2", "vx", "gr-s", "gr-sport", "legend", "raised-body", "auto"):
            if token in slug:
                score += 8
        if cls._slug_has_engine(slug):
            score += 20
        else:
            # Drivetrain/trim-only slugs must never beat a short engine slug
            score -= 40
        # Bare engine-only slugs are weaker than engine+axle/trim
        if re.fullmatch(r"2[.\-]?[48]gd-?6", slug):
            score -= 20
        return score

    @classmethod
    def slug_from_variant(cls, text: str | None) -> str | None:
        """Build an AutoTrader SEO slug while preserving engine dots (2.8 not 2-8)."""
        if not text:
            return None
        raw = text.lower()
        raw = re.sub(r"\btoyota\b|\bfortuner\b", " ", raw)
        # Years belong in titles, not AT SEO slugs
        raw = re.sub(r"\b20[0-2]\d\b", " ", raw)
        raw = re.sub(r"\bgr\s*sport\b", "gr-sport", raw)
        raw = re.sub(r"\bgr\s*s\b", "gr-s", raw)
        raw = re.sub(r"\braised\s*body\b", "raised-body", raw)
        raw = re.sub(r"\bgd\s*[- ]?\s*6\b", "gd-6", raw)
        raw = re.sub(r"\bautomatic\b|\bauto\b", "auto", raw)
        # AutoTrader slugs use 2.8gd-6 (no hyphen between engine and gd-6)
        raw = re.sub(r"(\d+\.\d+)\s*-?\s*gd-6", r"\1gd-6", raw)
        # Protect decimal engines before hyphenating (2.8 → keep dot)
        engines: list[str] = []

        def _protect_engine(match: re.Match[str]) -> str:
            engines.append(match.group(0))
            return f"engine{len(engines) - 1}"

        # Protect full engine+gd token when present
        raw = re.sub(r"\d+\.\d+gd-6", _protect_engine, raw)
        raw = re.sub(r"\d+\.\d+", _protect_engine, raw)
        raw = re.sub(r"[^a-z0-9]+", "-", raw)
        for idx, engine in enumerate(engines):
            raw = raw.replace(f"engine{idx}", engine)
        raw = re.sub(r"-{2,}", "-", raw).strip("-")
        if not raw or raw.isdigit():
            return None
        if not re.search(r"\d\.\d|gd-6|4x[24]|vx|gr|legend", raw):
            return None
        return raw[:90]

    @classmethod
    def improve_detail_url(
        cls,
        url: str | None,
        *,
        variant: str | None = None,
        title: str | None = None,
        listing_id: str | None = None,
    ) -> str | None:
        """Upgrade short/broken AutoTrader slugs using variant text when available.

        Never replace an engine-bearing slug with a drivetrain-only slug (that used
        to make every card fail ``_is_plausible_card`` after collect).
        """
        if not url:
            return None
        abs_url = url.split("?")[0]
        if not cls.is_detail_url(abs_url):
            return None
        lid = listing_id or cls.listing_id_from_url(abs_url)
        if not lid:
            return abs_url
        current_slug = cls._slug_from_url(abs_url)
        current_score = cls._url_slug_score(abs_url)
        # Already specific enough
        if current_score >= 40:
            return abs_url
        variant_slug = cls.slug_from_variant(variant) or cls.slug_from_variant(title)
        slug = cls._merge_seo_slugs(current_slug, variant_slug)
        if not slug or slug == current_slug:
            return abs_url
        # Refuse to drop engine evidence already present in the URL
        if cls._slug_has_engine(current_slug) and not cls._slug_has_engine(slug):
            return abs_url
        improved = f"https://www.autotrader.co.za/car-for-sale/toyota/fortuner/{slug}/{lid}"
        if cls._url_slug_score(improved) > current_score:
            return improved
        return abs_url

    @classmethod
    def drivetrain_from_url(cls, url: str | None) -> str | None:
        """Prefer SEO slug over noisy card text (filters inject '4x4' / 'AT')."""
        path = (urlparse(url or "").path or "").lower()
        # Explicit 4x2 in slug wins even if page filters mention 4x4
        if re.search(r"(?:^|[-_/])4x2(?:[-_/]|$)", path) or re.search(r"(?:^|[-_/])4-x-2(?:[-_/]|$)", path):
            return "4x2"
        if re.search(r"(?:^|[-_/])4x4(?:[-_/]|$)", path) or re.search(r"(?:^|[-_/])4-x-4(?:[-_/]|$)", path):
            return "4x4"
        if re.search(r"(?:^|[-_/])4wd(?:[-_/]|$)", path):
            return "4x4"
        return None

    @classmethod
    def title_from_url(cls, url: str | None) -> str | None:
        path = urlparse(url or "").path
        m = re.search(r"/car-for-sale/toyota/fortuner/([^/]+)/\d{6,}", path or "", re.I)
        if not m:
            return None
        slug = m.group(1).replace("-", " ").strip()
        if not slug or slug.isdigit():
            return None
        return f"Toyota Fortuner {slug}"

    @classmethod
    def _is_chip_title(cls, title: str | None) -> bool:
        t = (title or "").strip()
        if not t:
            return True
        if re.fullmatch(r"(?i)(?:toyota\s+)?(?:fortuner\s+)?4x[24](?:\s*(?:a/?t|m/?t|auto(?:matic)?))?", t):
            return True
        if "fortuner" not in t.lower() and len(t) < 20:
            return True
        return False

    @classmethod
    def _is_plausible_card(cls, item: ListingPayload) -> bool:
        """Drop rows contaminated by search filter chips (fake 100k km / '4x4 AT')."""
        url = item.url or ""
        title = item.title or ""
        path = urlparse(url).path or ""
        slug_part = re.sub(r"/\d{6,}/?$", "", path)
        blob = f"{title} {item.variant_raw or ''} {slug_part}"
        if not cls.is_detail_url(url):
            return False
        if "fortuner" not in blob.lower():
            return False
        if cls.drivetrain_from_url(url) == "4x2":
            return False
        # Chip titles only OK when the SEO slug itself has engine/year/trim evidence
        engineish = re.search(
            r"\b20[0-2]\d\b|\b2[.\s-][48]\b|\b2[48]gd\b|gd-?6|\bvx\b|gr-?s|legend",
            blob,
            re.I,
        )
        if cls._is_chip_title(title) and not engineish:
            return False
        if not engineish:
            return False
        return True

    @classmethod
    def _resolve_title(cls, title: str | None, url: str | None, meta: str | None = None) -> str:
        if title and not cls._is_chip_title(title) and "fortuner" in title.lower():
            return title
        from_meta = cls._title_from_text(meta or "")
        if from_meta and not cls._is_chip_title(from_meta):
            return from_meta
        from_url = cls.title_from_url(url)
        if from_url:
            return from_url
        listing_id = cls.listing_id_from_url(url or "") or "unknown"
        return f"Toyota Fortuner {listing_id}"

    @classmethod
    def _resolve_drivetrain(cls, url: str | None, *text_bits: str | None) -> str | None:
        from_url = cls.drivetrain_from_url(url)
        if from_url:
            return from_url
        # Only trust compact title/variant — not giant card ancestors with filter chips
        for bit in text_bits:
            if not bit or len(bit) > 180:
                continue
            dt = detect_drivetrain(bit)
            if dt:
                return dt
        return None

    @staticmethod
    def _dedupe(listings: list[ListingPayload]) -> list[ListingPayload]:
        out: dict[str, ListingPayload] = {}
        for item in listings:
            out[item.source_listing_id] = item
        return list(out.values())

    @staticmethod
    def _title_from_text(text: str) -> str | None:
        for line in (text or "").splitlines():
            line = line.strip()
            if "fortuner" in line.lower() and len(line) < 120:
                return line
        return None

    @staticmethod
    def _normalise_spaces(text: str | None) -> str:
        # AutoTrader JSON uses NBSP (\u00A0) inside "R 629 000" / "92 000 km"
        return re.sub(r"[\u00A0\u202F\u2007]", " ", text or "")

    @staticmethod
    def _extract_year(text: str) -> int | None:
        m = re.search(r"\b(20[0-2]\d)\b", AutoTraderCollector._normalise_spaces(text))
        return int(m.group(1)) if m else None

    @staticmethod
    def _extract_mileage(text: str) -> int | None:
        """Prefer card odometer; ignore filter chips like 'Up to 100 000 km'."""
        cleaned = AutoTraderCollector._normalise_spaces(text)
        cleaned = re.sub(
            r"(?i)(up\s*to|mileage\s*to|max(?:imum)?|under|below|less\s*than)\s*[\d\s,]+\s*km",
            " ",
            cleaned,
        )
        # Range chips: "0 - 100 000 km" / "0–99999 km"
        cleaned = re.sub(
            r"(?i)\b\d{1,3}(?:[ \t]\d{3})?\s*[-–—to]+\s*[\d\s,]+\s*km\b",
            " ",
            cleaned,
        )
        candidates: list[int] = []
        for m in re.finditer(
            r"(?<!\d)(\d{1,3}(?:[ \t]\d{3}){0,2}|\d{4,6})[ \t]*km\b",
            cleaned,
            re.I,
        ):
            digits = re.sub(r"[^\d]", "", m.group(1))
            if not digits:
                continue
            val = int(digits)
            if 1_000 <= val <= 500_000:
                candidates.append(val)
        if not candidates:
            return None
        # Exact buyer-filter ceilings are almost always chips when alone
        if len(candidates) == 1 and candidates[0] in {100_000, 99_999, 110_000, 150_000, 200_000}:
            return None
        return candidates[-1]

    @staticmethod
    def _extract_price(text: str) -> int | None:
        # Do not use re.I — trailing 'r' in "Fortuner" must not match as currency.
        # Prefer grouped amounts like "R 539 900" and stop before mileage digits.
        cleaned = AutoTraderCollector._normalise_spaces(text)
        for m in re.finditer(
            r"(?<![A-Za-z])R[ \t]*(\d{1,3}(?:[ \t]\d{3}){1,3}|\d{5,7})\b",
            cleaned,
        ):
            tail = cleaned[m.end() : m.end() + 8].lower()
            if "p/m" in tail or "/month" in tail:
                continue
            digits = re.sub(r"[^\d]", "", m.group(1))
            if not digits:
                continue
            val = int(digits)
            if 50_000 <= val <= 5_000_000:
                return val
        return None
