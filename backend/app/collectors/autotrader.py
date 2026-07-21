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

logger = logging.getLogger(__name__)

# Real detail URLs look like:
# /car-for-sale/toyota/fortuner/2.8gd-6-4x4-vx/28096596
DETAIL_PATH_RE = re.compile(
    r"^/car-for-sale/(?:[^/]+/){1,6}(?P<id>\d{6,})/?$",
    re.I,
)


class AutoTraderCollector(BaseCollector):
    source = "autotrader"
    category = "marketplace"

    SEARCH_URL = "https://www.autotrader.co.za/cars-for-sale/toyota/fortuner"
    SEARCH_URL_WC = "https://www.autotrader.co.za/cars-for-sale/western-cape/toyota/fortuner"

    def search_base_url(self) -> str:
        preferred = (self.settings.preferred_province or "").strip().lower()
        if preferred in {"western cape", "wc", "western-cape"}:
            return self.SEARCH_URL_WC
        return self.SEARCH_URL

    def build_search_params(self) -> dict[str, Any]:
        params: dict[str, Any] = {
            "mileage_to": self.settings.stretch_mileage_km,
            "rcp": 50,
        }
        # Price is open — sort/score prefer cheaper; optional comfort cap only if enabled
        if self.settings.enforce_max_price:
            params["price_to"] = self.settings.stretch_price_zar
        return params

    def search(self) -> list[ListingPayload]:
        url = f"{self.search_base_url()}?{urlencode(self.build_search_params())}"
        listings: list[ListingPayload] = []

        try:
            html = self.fetch_text(url)
            self.snapshot_raw("search", html)
            listings = self.parse_all(html)
        except Exception:
            logger.exception("AutoTrader HTTP search failed")

        if (not listings) and playwright_available() and self.settings.use_playwright:
            try:
                listings = self.search_with_playwright(url)
            except Exception:
                logger.exception("AutoTrader Playwright search failed")

        listings = [x for x in listings if self.is_detail_url(x.url)]
        if not listings:
            raise CollectorError("AutoTrader: no listings parsed", parser_broken=True)
        return self._dedupe(listings)

    def search_with_playwright(self, url: str) -> list[ListingPayload]:
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
            listings = self.parse_all(html)
            if listings:
                return listings

            # Direct DOM extraction of detail anchors (most reliable)
            rows = page.eval_on_selector_all(
                "a[href*='/car-for-sale/']",
                """els => els.map(a => {
                  const card = a.closest('article, li, div') || a.parentElement;
                  const text = card ? card.innerText : a.innerText;
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
                results.append(
                    ListingPayload(
                        source=self.source,
                        source_listing_id=listing_id,
                        url=href.split("?")[0],
                        title=self._title_from_text(text) or f"Toyota Fortuner {listing_id}",
                        variant_raw=self._title_from_text(text),
                        price_zar=self._extract_price(text),
                        mileage_km=self._extract_mileage(text),
                        year=self._extract_year(text),
                        image_urls=[row["img"]] if row.get("img") else [],
                        make="Toyota",
                        model="Fortuner",
                    )
                )
            return results

    def parse_all(self, html: str) -> list[ListingPayload]:
        listings = self.parse_search_html(html)
        if not listings:
            listings = self.parse_vehicle_data_blobs(html)
        if not listings:
            listings = self.parse_embedded_json(html)
        return [x for x in listings if self.is_detail_url(x.url)]

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
                container = link.find_parent(["article", "li", "div"]) or link.parent
                title_el = None
                if hasattr(container, "select_one"):
                    title_el = container.select_one("h2, h3, .title, [data-testid='listing-title']")
                meta = (
                    container.get_text(" ", strip=True)
                    if container is not None
                    else link.get_text(" ", strip=True)
                )
                price_el = (
                    container.select_one(".price, [data-testid='price']")
                    if hasattr(container, "select_one")
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
                title = title_el.get_text(strip=True) if title_el else (link.get_text(strip=True) or None)
                if "fortuner" not in (title or "").lower() and "fortuner" not in meta.lower() and "fortuner" not in href.lower():
                    continue
                results.append(
                    ListingPayload(
                        source=self.source,
                        source_listing_id=listing_id,
                        url=href,
                        title=title or f"Toyota Fortuner {listing_id}",
                        variant_raw=title,
                        price_zar=self._extract_price(price_el.get_text() if price_el else meta),
                        mileage_km=self._extract_mileage(meta),
                        year=self._extract_year(meta),
                        dealer_location=location_el.get_text(strip=True) if location_el else None,
                        dealer_name=dealer_el.get_text(strip=True) if dealer_el else None,
                        image_urls=image_urls,
                        make="Toyota",
                        model="Fortuner",
                    )
                )
            except Exception:
                logger.exception("Failed parsing AutoTrader card")
        return results

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
        return [
            ListingPayload(
                source=self.source,
                source_listing_id=listing_id,
                url=raw_url.split("?")[0],
                title=str(title) if title else None,
                variant_raw=str(title) if title else None,
                year=data.get("year") or data.get("modelYear"),
                price_zar=int(price) if price not in (None, "") else None,
                mileage_km=int(mileage) if mileage not in (None, "") else None,
                colour=data.get("colour") or data.get("color"),
                dealer_name=(data.get("dealer") or {}).get("name")
                if isinstance(data.get("dealer"), dict)
                else data.get("dealerName"),
                dealer_location=data.get("suburb") or data.get("city") or data.get("province"),
                image_urls=list(data.get("images") or data.get("imageUrls") or []),
                make="Toyota",
                model="Fortuner",
                drivetrain=data.get("drivetrain") or data.get("driveType"),
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
        return self.parse_search_html(html)

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
    def _extract_year(text: str) -> int | None:
        m = re.search(r"\b(20[0-2]\d)\b", text)
        return int(m.group(1)) if m else None

    @staticmethod
    def _extract_mileage(text: str) -> int | None:
        m = re.search(r"([\d\s]+)\s*km", text, re.I)
        if not m:
            return None
        return int(re.sub(r"\s+", "", m.group(1)))

    @staticmethod
    def _extract_price(text: str) -> int | None:
        m = re.search(r"R\s*([\d\s,]+)", text, re.I)
        if not m:
            return None
        return int(re.sub(r"[^\d]", "", m.group(1)))
