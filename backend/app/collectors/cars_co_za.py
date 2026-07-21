"""Cars.co.za collector."""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import urlencode

from bs4 import BeautifulSoup

from app.collectors.base import BaseCollector, CollectorError
from app.schemas.listings import ListingPayload

logger = logging.getLogger(__name__)


class CarsCoZaCollector(BaseCollector):
    source = "cars_co_za"
    category = "marketplace"

    SEARCH_URL = "https://www.cars.co.za/searchVehicle.php"

    def build_search_params(self) -> dict[str, Any]:
        return {
            "make_model": "Toyota Fortuner",
            "price_to": self.settings.stretch_price_zar,
            "mileage_to": self.settings.stretch_mileage_km,
            "vehicle_type": "used",
        }

    def search(self) -> list[ListingPayload]:
        url = f"{self.SEARCH_URL}?{urlencode(self.build_search_params())}"
        html = self.fetch_text(url)
        self.snapshot_raw("search", html)
        listings = self.parse_search_html(html)
        if not listings:
            raise CollectorError("Cars.co.za: no listings parsed", parser_broken=True)
        return listings

    def parse_search_html(self, html: str) -> list[ListingPayload]:
        soup = BeautifulSoup(html, "html.parser")
        cards = soup.select(".vehicle-card, .result-item, article.listing, [data-vehicle-id]")
        results: list[ListingPayload] = []
        for card in cards:
            try:
                vid = card.get("data-vehicle-id") or card.get("data-id")
                link = card.select_one("a[href]")
                href = link["href"] if link else None
                if not href:
                    continue
                if href.startswith("/"):
                    href = f"https://www.cars.co.za{href}"
                listing_id = str(vid) if vid else self._id_from_url(href)
                title_el = card.select_one("h2, h3, .vehicle-title, .title")
                price_el = card.select_one(".price, .vehicle-price")
                text = card.get_text(" ", strip=True)
                img = card.select_one("img")
                results.append(
                    ListingPayload(
                        source=self.source,
                        source_listing_id=listing_id,
                        url=href,
                        title=title_el.get_text(strip=True) if title_el else None,
                        variant_raw=title_el.get_text(strip=True) if title_el else None,
                        price_zar=self._price(price_el.get_text() if price_el else text),
                        mileage_km=self._mileage(text),
                        year=self._year(text),
                        dealer_name=self._text(card, ".dealer-name, .seller"),
                        dealer_location=self._text(card, ".location, .area"),
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
