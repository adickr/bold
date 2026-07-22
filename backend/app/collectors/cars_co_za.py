"""Cars.co.za collector.

Uses the same filtered search URL shape as the live site, e.g.:

https://www.cars.co.za/usedcars/?make_model_variant=Toyota[Fortuner]
  &sort=sort_rank&price_type=listing_price
  &vfs_mileage=0-99999&vfs_area=Western%20Cape
  &vehicle_axle_config=4X4&P=1

Cars.co.za is often behind Cloudflare; Playwright (with challenge wait)
is preferred when available.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any
from urllib.parse import urlencode

from bs4 import BeautifulSoup

from app.collectors.base import BaseCollector, CollectorError
from app.collectors.browser import (
    browser_page,
    goto_and_wait,
    is_cloudflare_challenge,
    playwright_available,
)
from app.schemas.listings import ListingPayload

logger = logging.getLogger(__name__)

DETAIL_HREF_RE = re.compile(
    r"/for-sale/used/[^\"'\s>]*/(?P<id>\d{5,})/?",
    re.I,
)
RESULT_COUNT_RE = re.compile(r"(\d+)\s*-\s*(\d+)\s*of\s*(\d+)", re.I)
NEWS_TITLE_RE = re.compile(
    r"(?i)\b(spotted|buyer.?s guide|price & specs|vs\b|head-to-head|review|news)\b"
)


class CarsCoZaCollector(BaseCollector):
    source = "cars_co_za"
    category = "marketplace"

    SEARCH_URL = "https://www.cars.co.za/usedcars/"
    # Path-shaped SEO URL (often lighter CF / same results as query filters)
    SEARCH_PATH_WC = "https://www.cars.co.za/usedcars/Western-Cape/Toyota/Fortuner/"
    HOME_URL = "https://www.cars.co.za/"

    def build_search_params(self, page: int = 1, *, path_mode: bool = False) -> dict[str, Any]:
        # Match the site's own filter query string (see user WC 4x4 ≤100k URL)
        mileage_hi = max(0, int(self.settings.max_mileage_km) - 1)
        params: dict[str, Any] = {
            "sort": "price_asc",
            "price_type": "listing_price",
            "vfs_mileage": f"0-{mileage_hi}",
            "vehicle_axle_config": "4X4",
            "P": page,
        }
        if not path_mode:
            params["make_model_variant"] = "Toyota[Fortuner]"
            preferred = (self.settings.preferred_province or "").strip()
            if preferred:
                params["vfs_area"] = preferred
        if self.settings.enforce_max_price:
            params["price_to"] = self.settings.stretch_price_zar
        return params

    def search_url(self, page: int = 1) -> str:
        return f"{self.SEARCH_URL}?{urlencode(self.build_search_params(page))}"

    def search_urls(self, page: int = 1) -> list[str]:
        """Prefer path URL for WC, then query-string form as fallback."""
        urls: list[str] = []
        preferred = (self.settings.preferred_province or "").strip().lower()
        if preferred in {"western cape", "wc", "western-cape"}:
            urls.append(
                f"{self.SEARCH_PATH_WC}?{urlencode(self.build_search_params(page, path_mode=True))}"
            )
        urls.append(self.search_url(page))
        return urls

    def search(self) -> list[ListingPayload]:
        if playwright_available() and self.settings.use_playwright:
            try:
                listings = self._search_single_browser_session()
                if listings:
                    return listings
            except Exception:
                logger.exception("Cars.co.za single-session Playwright search failed")

        # Last resort: plain HTTP (usually CF-blocked)
        listings = self._search_http_only()
        if not listings:
            raise CollectorError(
                "Cars.co.za: no listings parsed — Cloudflare is blocking. "
                "Restart with PLAYWRIGHT_HEADED=true and "
                "PLAYWRIGHT_USER_DATA_DIR=./data/chrome-profile, click the checkbox "
                "ONCE in the Chrome window, leave that profile alone, then re-collect.",
                parser_broken=True,
            )
        return listings

    def _search_single_browser_session(self) -> list[ListingPayload]:
        """One Chrome window for the whole scan — CF checkbox at most once."""
        all_listings: list[ListingPayload] = []
        seen: set[str] = set()
        max_pages = max(1, self.settings.collector_max_pages)
        total_hint: int | None = None
        url_builder = None

        with browser_page() as page:
            # Warm the domain so Turnstile cookies attach before search URLs
            logger.info("Cars.co.za: warming homepage in persistent Chrome…")
            goto_and_wait(
                page,
                self.HOME_URL,
                wait_through_challenge=True,
                timeout_ms=180000,
            )

            for page_num in range(1, max_pages + 1):
                urls = (
                    [url_builder(page_num)]
                    if url_builder is not None
                    else self.search_urls(page_num)
                )
                page_listings: list[ListingPayload] = []
                page_total: int | None = None
                for url in urls:
                    html = goto_and_wait(
                        page,
                        url,
                        wait_selector="a[href*='/for-sale/used/']",
                        wait_through_challenge=True,
                        timeout_ms=180000,
                    )
                    if is_cloudflare_challenge(page):
                        logger.warning("Cars.co.za still challenged after wait: %s", url)
                        continue
                    self.snapshot_raw(f"search_p{page_num}", html)
                    page_listings = self._valid_vehicle_listings(self.parse_search_html(html))
                    page_total = self._parse_total(html)
                    if page_listings:
                        if url_builder is None:
                            if "Western-Cape/Toyota/Fortuner" in url:
                                url_builder = lambda p: (
                                    f"{self.SEARCH_PATH_WC}?"
                                    f"{urlencode(self.build_search_params(p, path_mode=True))}"
                                )
                            else:
                                url_builder = self.search_url
                        break

                if total_hint is None and page_total is not None:
                    total_hint = page_total
                if not page_listings:
                    logger.info("Cars.co.za page %s returned 0 — stopping", page_num)
                    break

                gained = 0
                for item in page_listings:
                    if item.source_listing_id in seen:
                        continue
                    seen.add(item.source_listing_id)
                    all_listings.append(item)
                    gained += 1
                logger.info(
                    "Cars.co.za page %s: parsed %s (unique total %s, +%s, site_total=%s)",
                    page_num,
                    len(page_listings),
                    len(all_listings),
                    gained,
                    total_hint,
                )
                if gained == 0:
                    break
                if total_hint is not None and len(all_listings) >= total_hint:
                    break
                if len(page_listings) < 8:
                    break

        return self._annotate(all_listings)

    def _search_http_only(self) -> list[ListingPayload]:
        all_listings: list[ListingPayload] = []
        seen: set[str] = set()
        for page in range(1, max(1, self.settings.collector_max_pages) + 1):
            found = False
            for url in self.search_urls(page):
                try:
                    html = self.fetch_text(url)
                    self.snapshot_raw("search_http", html)
                    if "just a moment" in html.lower() or "cf-turnstile" in html.lower():
                        logger.warning("Cars.co.za HTTP hit Cloudflare for %s", url)
                        continue
                    rows = self._valid_vehicle_listings(self.parse_search_html(html))
                    for item in rows:
                        if item.source_listing_id in seen:
                            continue
                        seen.add(item.source_listing_id)
                        all_listings.append(item)
                        found = True
                    if rows:
                        break
                except Exception:
                    logger.exception("Cars.co.za HTTP failed for %s", url)
            if not found:
                break
        return self._annotate(all_listings)

    def _valid_vehicle_listings(self, listings: list[ListingPayload]) -> list[ListingPayload]:
        """Drop editorial/news hits and require a real used-car detail URL."""
        out: list[ListingPayload] = []
        seen: set[str] = set()
        for item in listings:
            href = (item.url or "").lower()
            title = item.title or ""
            if "/for-sale/used/" not in href and not DETAIL_HREF_RE.search(href):
                # Allow API rows that only have an id — rebuild used URL later only if priced
                if not (item.price_zar and item.source_listing_id):
                    continue
            if NEWS_TITLE_RE.search(title) and not item.price_zar:
                continue
            # Real stock almost always has a price or mileage on the card/API
            if item.price_zar is None and item.mileage_km is None:
                # URL-only slug with year+Fortuner is still ok (SSR sometimes omits text)
                if not re.search(r"/20[0-2]\d-.*fortuner", href, re.I):
                    continue
            if item.source_listing_id in seen:
                continue
            seen.add(item.source_listing_id)
            out.append(item)
        return out

    def _annotate(self, listings: list[ListingPayload]) -> list[ListingPayload]:
        """Search is already 4x4 + area filtered — fill gaps on parsed rows."""
        preferred = (self.settings.preferred_province or "").strip() or None
        out: list[ListingPayload] = []
        for item in listings:
            data = item.model_dump()
            if not data.get("drivetrain"):
                data["drivetrain"] = "4x4"
            if preferred and not data.get("dealer_location"):
                data["dealer_location"] = preferred
            elif preferred and preferred.lower() not in (data.get("dealer_location") or "").lower():
                data["dealer_location"] = f"{data.get('dealer_location')}, {preferred}".strip(", ")
            out.append(ListingPayload.model_validate(data))
        return out

    def parse_api_json(self, data: Any) -> list[ListingPayload]:
        items: list[Any] = []
        if isinstance(data, dict):
            for key in ("results", "listings", "vehicles", "data", "hits", "items"):
                val = data.get(key)
                if isinstance(val, list):
                    items = val
                    break
                if isinstance(val, dict):
                    nested = val.get("results") or val.get("listings") or val.get("hits")
                    if isinstance(nested, list):
                        items = nested
                        break
        elif isinstance(data, list):
            items = data

        results: list[ListingPayload] = []
        for row in items:
            if not isinstance(row, dict):
                continue
            title = str(row.get("title") or row.get("name") or row.get("heading") or "")
            blob = f"{title} {row.get('model') or ''} {row.get('variant') or ''}"
            if "fortuner" not in blob.lower() and str(row.get("model") or "").lower() != "fortuner":
                continue
            listing_id = str(
                row.get("id")
                or row.get("vehicle_id")
                or row.get("vehicleId")
                or row.get("listing_id")
                or ""
            )
            url = row.get("url") or row.get("permalink") or row.get("link")
            if not listing_id and url:
                listing_id = self._id_from_url(str(url))
            if not listing_id:
                continue
            if url and str(url).startswith("/"):
                url = f"https://www.cars.co.za{url}"
            if not url:
                url = f"https://www.cars.co.za/for-sale/used/toyota-fortuner/{listing_id}"
            if "/for-sale/used/" not in str(url).lower():
                continue
            price = row.get("price") or row.get("price_zar") or row.get("asking_price")
            mileage = row.get("mileage") or row.get("mileage_km") or row.get("odometer")
            year = row.get("year") or row.get("model_year")
            if price in (None, "") and mileage in (None, ""):
                continue
            location = (
                row.get("location")
                or row.get("area")
                or row.get("province")
                or row.get("city")
            )
            if isinstance(location, dict):
                location = ", ".join(
                    str(location.get(k))
                    for k in ("suburb", "city", "province", "area")
                    if location.get(k)
                )
            images = row.get("images") or row.get("image_urls") or []
            if isinstance(images, str):
                images = [images]
            results.append(
                ListingPayload(
                    source=self.source,
                    source_listing_id=listing_id,
                    url=str(url),
                    title=title or f"Toyota Fortuner {listing_id}",
                    variant_raw=str(row.get("variant") or title or ""),
                    year=int(year) if year not in (None, "") else None,
                    price_zar=int(price) if price not in (None, "") else None,
                    mileage_km=int(mileage) if mileage not in (None, "") else None,
                    dealer_name=row.get("dealer") or row.get("dealer_name") or row.get("seller"),
                    dealer_location=str(location) if location else None,
                    image_urls=[str(x) for x in images[:12]],
                    drivetrain="4x4",
                    make="Toyota",
                    model="Fortuner",
                    raw_payload=row,
                )
            )
        return results

    def parse_search_html(self, html: str) -> list[ListingPayload]:
        if "just a moment" in html.lower() and "/for-sale/" not in html.lower():
            return []
        soup = BeautifulSoup(html, "html.parser")
        results: list[ListingPayload] = []
        seen: set[str] = set()

        # Next.js / embedded JSON blobs
        for script in soup.select("script#__NEXT_DATA__, script[type='application/json']"):
            try:
                data = json.loads(script.string or "")
            except Exception:
                continue
            results.extend(self.parse_api_json(data))
            results.extend(self.parse_api_json(self._find_list_in_obj(data)))

        cards = soup.select(
            ".vehicle-card, .result-item, article.listing, [data-vehicle-id], "
            ".js-vehicle, .vehicle-list-item, li.vehicle"
        )
        for card in cards:
            try:
                link = (
                    card.select_one("a[href*='/for-sale/used/']")
                    if hasattr(card, "select_one")
                    else None
                )
                href = link["href"] if link and link.has_attr("href") else None
                if not href:
                    continue
                if href.startswith("/"):
                    href = f"https://www.cars.co.za{href}"
                listing_id = (
                    card.get("data-vehicle-id")
                    or card.get("data-id")
                    or self._id_from_url(href)
                )
                if not listing_id or listing_id in seen:
                    continue
                text = card.get_text(" ", strip=True)
                if "fortuner" not in text.lower() and "fortuner" not in href.lower():
                    continue
                seen.add(str(listing_id))
                title_el = card.select_one("h2, h3, .vehicle-title, .title")
                price_el = card.select_one(".price, .vehicle-price")
                img = card.select_one("img")
                title = (
                    title_el.get_text(strip=True)
                    if title_el
                    else (link.get_text(strip=True) if link else None)
                )
                if not title or len(title) < 12 or "fair deal" in title.lower() or title.lower().startswith("r "):
                    title = self._title_from_url(href) or title
                results.append(
                    ListingPayload(
                        source=self.source,
                        source_listing_id=str(listing_id),
                        url=href.split("?")[0],
                        title=title or f"Toyota Fortuner {listing_id}",
                        variant_raw=title,
                        price_zar=self._price(price_el.get_text() if price_el else text),
                        mileage_km=self._mileage(text),
                        year=self._year(text) or self._year_from_url(href),
                        dealer_name=self._text(card, ".dealer-name, .seller, .dealer"),
                        dealer_location=self._text(card, ".location, .area, .province")
                        or self._location_from_url(href),
                        image_urls=self._img_urls(img),
                        drivetrain="4x4",
                        make="Toyota",
                        model="Fortuner",
                    )
                )
            except Exception:
                logger.exception("Failed parsing Cars.co.za card")

        # Fallback: every used-car detail anchor on the page
        for link in soup.select("a[href*='/for-sale/used/']"):
            href = link.get("href") or ""
            if href.startswith("/"):
                href = f"https://www.cars.co.za{href}"
            m = DETAIL_HREF_RE.search(href)
            if not m:
                continue
            listing_id = m.group("id")
            if listing_id in seen:
                continue
            if "fortuner" not in href.lower() and "fortuner" not in link.get_text(" ", strip=True).lower():
                continue
            seen.add(listing_id)
            container = link.find_parent(["article", "li", "div"]) or link.parent
            text = container.get_text(" ", strip=True) if container is not None else link.get_text(" ", strip=True)
            img = container.select_one("img") if hasattr(container, "select_one") else None
            title = self._title_from_url(href) or link.get_text(strip=True) or f"Toyota Fortuner {listing_id}"
            results.append(
                ListingPayload(
                    source=self.source,
                    source_listing_id=listing_id,
                    url=href.split("?")[0],
                    title=title,
                    variant_raw=title,
                    price_zar=self._price(text),
                    mileage_km=self._mileage(text),
                    year=self._year(text) or self._year_from_url(href),
                    dealer_location=self._location_from_url(href),
                    image_urls=self._img_urls(img),
                    drivetrain="4x4",
                    make="Toyota",
                    model="Fortuner",
                )
            )
        return results

    @staticmethod
    def _find_list_in_obj(data: Any) -> Any:
        if isinstance(data, list):
            return data
        if not isinstance(data, dict):
            return []
        for key in ("props", "pageProps", "fallback", "search", "vehicles", "results"):
            if key in data:
                found = CarsCoZaCollector._find_list_in_obj(data[key])
                if found:
                    return found
        return []

    @staticmethod
    def _parse_total(html: str) -> int | None:
        m = RESULT_COUNT_RE.search(html)
        if m:
            return int(m.group(3))
        return None

    @staticmethod
    def _text(card: Any, selector: str) -> str | None:
        if not hasattr(card, "select_one"):
            return None
        el = card.select_one(selector)
        return el.get_text(strip=True) if el else None

    @staticmethod
    def _img_urls(img: Any) -> list[str]:
        if img is None:
            return []
        for attr in ("src", "data-src", "data-lazy-src"):
            if img.has_attr(attr) and img.get(attr):
                url = img.get(attr)
                if url.startswith("//"):
                    url = f"https:{url}"
                return [url]
        return []

    @staticmethod
    def _id_from_url(url: str) -> str:
        m = DETAIL_HREF_RE.search(url)
        if m:
            return m.group("id")
        m = re.search(r"/(\d{5,})", url)
        return m.group(1) if m else url.rstrip("/").split("/")[-1]

    @staticmethod
    def _year_from_url(url: str) -> int | None:
        m = re.search(r"/(20[0-2]\d)-", url)
        return int(m.group(1)) if m else None

    @staticmethod
    def _title_from_url(url: str) -> str | None:
        m = re.search(r"/for-sale/used/([^/]+)/\d+", url or "", re.I)
        if not m:
            return None
        slug = m.group(1).replace("-", " ").strip()
        return slug or None

    @staticmethod
    def _location_from_url(url: str) -> str | None:
        # ...-Western-Cape-Rondebosch/11014602/
        m = re.search(r"Western-Cape-([^/]+)/\d+", url, re.I)
        if m:
            suburb = m.group(1).replace("-", " ")
            return f"{suburb}, Western Cape"
        if re.search(r"Western-Cape", url, re.I):
            return "Western Cape"
        return None

    @staticmethod
    def _year(text: str) -> int | None:
        m = re.search(r"\b(20[0-2]\d)\b", text)
        return int(m.group(1)) if m else None

    @staticmethod
    def _mileage(text: str) -> int | None:
        for m in re.finditer(r"([\d\s,]+)\s*[Kk]m\b", text or ""):
            digits = re.sub(r"[^\d]", "", m.group(1))
            if not digits:
                continue
            val = int(digits)
            # get_text often glues year+km → 202370098; peel a leading 20xx
            if val > 500_000 and len(digits) >= 8 and digits[:2] in {"19", "20"}:
                rest = int(digits[4:])
                if 0 < rest <= 500_000:
                    val = rest
            if 0 < val <= 500_000:
                return val
        return None

    @staticmethod
    def _price(text: str) -> int | None:
        """Pick cash asking price; skip finance 'R x p/m' amounts."""
        for m in re.finditer(r"R\s*([\d\s,]+)", text or "", re.I):
            tail = (text or "")[m.end() : m.end() + 8].lower()
            if "p/m" in tail or "/month" in tail:
                continue
            digits = re.sub(r"[^\d]", "", m.group(1))
            if not digits:
                continue
            val = int(digits)
            if 50_000 <= val <= 5_000_000:
                return val
        return None
