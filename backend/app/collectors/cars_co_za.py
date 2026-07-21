"""Cars.co.za collector."""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import urlencode

from bs4 import BeautifulSoup

from app.collectors.base import BaseCollector, CollectorError
from app.collectors.browser import fetch_rendered_html, playwright_available
from app.schemas.listings import ListingPayload

logger = logging.getLogger(__name__)


class CarsCoZaCollector(BaseCollector):
    source = "cars_co_za"
    category = "marketplace"

    SEARCH_URL = "https://www.cars.co.za/usedcars/Toyota/Fortuner/"

    def build_search_params(self) -> dict[str, Any]:
        return {
            "price_to": self.settings.stretch_price_zar,
            "mileage_to": self.settings.stretch_mileage_km,
        }

    def search(self) -> list[ListingPayload]:
        url = f"{self.SEARCH_URL}?{urlencode(self.build_search_params())}"
        listings: list[ListingPayload] = []
        html = ""

        try:
            html = self.fetch_text(url)
            self.snapshot_raw("search", html)
            listings = self.parse_search_html(html)
        except Exception:
            logger.exception("Cars.co.za HTTP search failed")

        if not listings and playwright_available() and self.settings.use_playwright:
            try:
                html = fetch_rendered_html(
                    url,
                    wait_selector="a[href*='/for-sale/'], .vehicle-card, article",
                )
                self.snapshot_raw("search_rendered", html)
                listings = self.parse_search_html(html)
            except Exception:
                logger.exception("Cars.co.za Playwright search failed")

        if not listings:
            raise CollectorError("Cars.co.za: no listings parsed", parser_broken=True)
        return listings

    def parse_search_html(self, html: str) -> list[ListingPayload]:
        soup = BeautifulSoup(html, "html.parser")
        cards = soup.select(".vehicle-card, .result-item, article.listing, [data-vehicle-id], .js-vehicle")
        if not cards:
            cards = soup.select("a[href*='/for-sale/']")
        results: list[ListingPayload] = []
        seen: set[str] = set()
        for card in cards:
            try:
                if card.name == "a":
                    link = card
                    container = card.parent
                else:
                    link = card.select_one("a[href]")
                    container = card
                href = link["href"] if link and link.has_attr("href") else None
                if not href:
                    continue
                if href.startswith("/"):
                    href = f"https://www.cars.co.za{href}"
                vid = None
                if hasattr(container, "get"):
                    vid = container.get("data-vehicle-id") or container.get("data-id")
                listing_id = str(vid) if vid else self._id_from_url(href)
                if listing_id in seen:
                    continue
                seen.add(listing_id)
                title_el = container.select_one("h2, h3, .vehicle-title, .title") if hasattr(container, "select_one") else None
                price_el = container.select_one(".price, .vehicle-price") if hasattr(container, "select_one") else None
                text = container.get_text(" ", strip=True) if container is not None else link.get_text(" ", strip=True)
                if "fortuner" not in text.lower() and "fortuner" not in href.lower():
                    continue
                img = container.select_one("img") if hasattr(container, "select_one") else None
                results.append(
                    ListingPayload(
                        source=self.source,
                        source_listing_id=listing_id,
                        url=href,
                        title=title_el.get_text(strip=True) if title_el else link.get_text(strip=True),
                        variant_raw=title_el.get_text(strip=True) if title_el else None,
                        price_zar=self._price(price_el.get_text() if price_el else text),
                        mileage_km=self._mileage(text),
                        year=self._year(text),
                        dealer_name=self._text(container, ".dealer-name, .seller"),
                        dealer_location=self._text(container, ".location, .area"),
                        image_urls=[img["src"]] if img and img.has_attr("src") else [],
                        make="Toyota",
                        model="Fortuner",
                    )
                )
            except Exception:
                logger.exception("Failed parsing Cars.co.za card")
        return results

    @staticmethod
    def _text(card: Any, selector: str) -> str | None:
        if not hasattr(card, "select_one"):
            return None
        el = card.select_one(selector)
        return el.get_text(strip=True) if el else None

    @staticmethod
    def _id_from_url(url: str) -> str:
        m = re.search(r"/(\d{5,})", url)
        return m.group(1) if m else url.rstrip("/").split("/")[-1]

    @staticmethod
    def _year(text: str) -> int | None:
        m = re.search(r"\b(20[0-2]\d)\b", text)
        return int(m.group(1)) if m else None

    @staticmethod
    def _mileage(text: str) -> int | None:
        m = re.search(r"([\d\s,]+)\s*km", text, re.I)
        return int(re.sub(r"[^\d]", "", m.group(1))) if m else None

    @staticmethod
    def _price(text: str) -> int | None:
        m = re.search(r"R\s*([\d\s,]+)", text, re.I)
        return int(re.sub(r"[^\d]", "", m.group(1))) if m else None
