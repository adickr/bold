"""AutoTrader South Africa collector."""

from __future__ import annotations

import json
import logging
import re
from typing import Any
from urllib.parse import urlencode

from bs4 import BeautifulSoup

from app.collectors.base import BaseCollector, CollectorError
from app.collectors.browser import fetch_rendered_html, playwright_available
from app.schemas.listings import ListingPayload

logger = logging.getLogger(__name__)


class AutoTraderCollector(BaseCollector):
    source = "autotrader"
    category = "marketplace"

    SEARCH_URL = "https://www.autotrader.co.za/cars-for-sale/toyota/fortuner"

    def build_search_params(self) -> dict[str, Any]:
        return {
            "price_to": self.settings.stretch_price_zar,
            "mileage_to": self.settings.stretch_mileage_km,
            "rcp": 50,
        }

    def search(self) -> list[ListingPayload]:
        url = f"{self.SEARCH_URL}?{urlencode(self.build_search_params())}"
        html = ""
        listings: list[ListingPayload] = []

        try:
            html = self.fetch_text(url)
            self.snapshot_raw("search", html)
            listings = self.parse_all(html)
        except Exception:
            logger.exception("AutoTrader HTTP search failed")

        if not listings and playwright_available() and self.settings.use_playwright:
            try:
                html = fetch_rendered_html(
                    url,
                    wait_selector="a[href*='/car-for-sale/'], a[href*='/cars-for-sale/']",
                )
                self.snapshot_raw("search_rendered", html)
                listings = self.parse_all(html)
            except Exception:
                logger.exception("AutoTrader Playwright search failed")

        if not listings:
            raise CollectorError("AutoTrader: no listings parsed", parser_broken=True)
        return listings

    def parse_all(self, html: str) -> list[ListingPayload]:
        listings = self.parse_search_html(html)
        if not listings:
            listings = self.parse_vehicle_data_blobs(html)
        if not listings:
            listings = self.parse_embedded_json(html)
        return listings

    def parse_search_html(self, html: str) -> list[ListingPayload]:
        soup = BeautifulSoup(html, "html.parser")
        cards = soup.select(
            "[data-testid='listing-card'], article.listing-card, .result-card, "
            "[data-listing-id], li[data-testid*='result'], div[data-vehicle-id]"
        )
        # Broader fallback: any anchor to a listing detail page
        if not cards:
            cards = soup.select("a[href*='/car-for-sale/'], a[href*='/cars-for-sale/']")
        results: list[ListingPayload] = []
        seen: set[str] = set()
        for card in cards:
            try:
                if card.name == "a":
                    link = card
                    container = card.parent
                else:
                    link = card.select_one("a[href*='/car-for-sale/'], a[href*='/cars-for-sale/']")
                    container = card
                href = link["href"] if link and link.has_attr("href") else None
                if not href:
                    continue
                if href.startswith("/"):
                    href = f"https://www.autotrader.co.za{href}"
                listing_id = self._extract_id(href, container if container is not None else card)
                if listing_id in seen:
                    continue
                seen.add(listing_id)
                title_el = None
                if hasattr(container, "select_one"):
                    title_el = container.select_one("h2, h3, .title, [data-testid='listing-title']")
                meta = container.get_text(" ", strip=True) if container is not None else link.get_text(" ", strip=True)
                price_el = container.select_one(".price, [data-testid='price']") if hasattr(container, "select_one") else None
                location_el = container.select_one(".location, [data-testid='location']") if hasattr(container, "select_one") else None
                dealer_el = container.select_one(".dealer, [data-testid='dealer']") if hasattr(container, "select_one") else None
                img = container.select_one("img") if hasattr(container, "select_one") else None
                title = title_el.get_text(strip=True) if title_el else (link.get_text(strip=True) or None)
                if title and "fortuner" not in title.lower() and "fortuner" not in meta.lower():
                    continue
                results.append(
                    ListingPayload(
                        source=self.source,
                        source_listing_id=listing_id,
                        url=href,
                        title=title,
                        variant_raw=title,
                        price_zar=self._extract_price(price_el.get_text() if price_el else meta),
                        mileage_km=self._extract_mileage(meta),
                        year=self._extract_year(meta),
                        dealer_location=location_el.get_text(strip=True) if location_el else None,
                        dealer_name=dealer_el.get_text(strip=True) if dealer_el else None,
                        image_urls=[img["src"]] if img and img.has_attr("src") else [],
                        make="Toyota",
                        model="Fortuner",
                    )
                )
            except Exception:
                logger.exception("Failed parsing AutoTrader card")
        return results

    def parse_vehicle_data_blobs(self, html: str) -> list[ListingPayload]:
        """Parse embedded vehicle_data JSON objects used by AutoTrader pages."""
        results: list[ListingPayload] = []
        for match in re.finditer(r"vehicle_data\s*=\s*(\{.*?\});", html, flags=re.S):
            try:
                data = json.loads(match.group(1))
            except Exception:
                continue
            results.extend(self._from_vehicle_data(data))
        # Also scan for JSON script tags containing listing arrays
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
        if not listing_id:
            return []
        if title and "fortuner" not in str(title).lower() and data.get("model") != "Fortuner":
            return []
        price = data.get("price") or data.get("priceZar") or data.get("askingPrice")
        mileage = data.get("mileage") or data.get("odometer") or data.get("km")
        return [
            ListingPayload(
                source=self.source,
                source_listing_id=listing_id,
                url=data.get("url") or f"https://www.autotrader.co.za/car-for-sale/{listing_id}",
                title=str(title) if title else None,
                variant_raw=str(title) if title else None,
                year=data.get("year") or data.get("modelYear"),
                price_zar=int(price) if price not in (None, "") else None,
                mileage_km=int(mileage) if mileage not in (None, "") else None,
                colour=data.get("colour") or data.get("color"),
                dealer_name=(data.get("dealer") or {}).get("name") if isinstance(data.get("dealer"), dict) else data.get("dealerName"),
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
        matches = re.findall(
            r'\{\s*"id"\s*:\s*"?(?P<id>\d+)"?.*?"title"\s*:\s*"(?P<title>[^"]*Fortuner[^"]*)".*?"price"\s*:\s*(?P<price>\d+)',
            html,
            flags=re.I | re.S,
        )
        results: list[ListingPayload] = []
        for listing_id, title, price in matches[:80]:
            results.append(
                ListingPayload(
                    source=self.source,
                    source_listing_id=str(listing_id),
                    url=f"https://www.autotrader.co.za/car-for-sale/{listing_id}",
                    title=title,
                    variant_raw=title,
                    price_zar=int(price),
                    make="Toyota",
                    model="Fortuner",
                )
            )
        return results

    def parse_fixture_html(self, html: str) -> list[ListingPayload]:
        return self.parse_search_html(html)

    @staticmethod
    def _extract_id(url: str, card: Any) -> str:
        data_id = None
        if hasattr(card, "get"):
            data_id = card.get("data-listing-id") or card.get("data-id") or card.get("data-vehicle-id")
        if data_id:
            return str(data_id)
        m = re.search(r"/(\d{5,})", url)
        return m.group(1) if m else url.rstrip("/").split("/")[-1]

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
