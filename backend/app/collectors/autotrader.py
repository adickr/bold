"""AutoTrader South Africa collector.

Prefers publicly accessible search JSON / structured listing cards.
Live scraping is conservative and optional; fixtures power local/demo runs.
"""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import urlencode

from bs4 import BeautifulSoup

from app.collectors.base import BaseCollector, CollectorError
from app.schemas.listings import ListingPayload

logger = logging.getLogger(__name__)


class AutoTraderCollector(BaseCollector):
    source = "autotrader"
    category = "marketplace"

    SEARCH_URL = "https://www.autotrader.co.za/cars-for-sale"

    def build_search_params(self) -> dict[str, Any]:
        return {
            "make": "Toyota",
            "model": "Fortuner",
            "price_to": self.settings.stretch_price_zar,
            "mileage_to": self.settings.stretch_mileage_km,
            "body_type": "SUV",
        }

    def search(self) -> list[ListingPayload]:
        url = f"{self.SEARCH_URL}?{urlencode(self.build_search_params())}"
        html = self.fetch_text(url)
        self.snapshot_raw("search", html)
        listings = self.parse_search_html(html)
        if not listings:
            # Attempt embedded JSON-LD / __NEXT_DATA__ style payloads
            listings = self.parse_embedded_json(html)
        if not listings:
            raise CollectorError("AutoTrader: no listings parsed", parser_broken=True)
        return listings

    def parse_search_html(self, html: str) -> list[ListingPayload]:
        soup = BeautifulSoup(html, "html.parser")
        cards = soup.select("[data-testid='listing-card'], article.listing-card, .result-card")
        results: list[ListingPayload] = []
        for card in cards:
            try:
                link = card.select_one("a[href*='/car-for-sale/'], a[href*='/cars-for-sale/']")
                href = link["href"] if link and link.has_attr("href") else None
                if not href:
                    continue
                if href.startswith("/"):
                    href = f"https://www.autotrader.co.za{href}"
                listing_id = self._extract_id(href, card)
                title_el = card.select_one("h2, h3, .title, [data-testid='listing-title']")
                price_el = card.select_one(".price, [data-testid='price']")
                meta = card.get_text(" ", strip=True)
                year = self._extract_year(meta)
                mileage = self._extract_mileage(meta)
                price = self._extract_price(price_el.get_text() if price_el else meta)
                location_el = card.select_one(".location, [data-testid='location']")
                dealer_el = card.select_one(".dealer, [data-testid='dealer']")
                img = card.select_one("img")
                results.append(
                    ListingPayload(
                        source=self.source,
                        source_listing_id=listing_id,
                        url=href,
                        title=title_el.get_text(strip=True) if title_el else None,
                        variant_raw=title_el.get_text(strip=True) if title_el else None,
                        price_zar=price,
                        mileage_km=mileage,
                        year=year,
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

    def parse_embedded_json(self, html: str) -> list[ListingPayload]:
        # Lightweight extraction of listing-like JSON blobs without executing JS
        matches = re.findall(
            r'\{\s*"id"\s*:\s*"?(?P<id>\d+)"?.*?"title"\s*:\s*"(?P<title>[^"]*Fortuner[^"]*)".*?"price"\s*:\s*(?P<price>\d+)',
            html,
            flags=re.I | re.S,
        )
        results: list[ListingPayload] = []
        for listing_id, title, price in matches[:50]:
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
        data_id = card.get("data-listing-id") or card.get("data-id")
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
