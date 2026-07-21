"""WeBuyCars collector (third MVP source)."""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import urlencode

from bs4 import BeautifulSoup

from app.collectors.base import BaseCollector, CollectorError
from app.schemas.listings import ListingPayload

logger = logging.getLogger(__name__)


class WeBuyCarsCollector(BaseCollector):
    source = "webuycars"
    category = "marketplace"

    SEARCH_URL = "https://www.webuycars.co.za/buy-a-car"

    def build_search_params(self) -> dict[str, Any]:
        return {
            "make": "Toyota",
            "model": "Fortuner",
            "maxPrice": self.settings.stretch_price_zar,
            "maxMileage": self.settings.stretch_mileage_km,
        }

    def search(self) -> list[ListingPayload]:
        url = f"{self.SEARCH_URL}?{urlencode(self.build_search_params())}"
        html = self.fetch_text(url)
        self.snapshot_raw("search", html)
        listings = self.parse_search_html(html)
        if not listings:
            # WeBuyCars often exposes a public search API shape; try JSON endpoint pattern
            try:
                api_url = (
                    "https://api.webuycars.co.za/api/vehicles/search?"
                    + urlencode(self.build_search_params())
                )
                data = self.fetch_json(api_url)
                self.snapshot_raw("search_api", data if isinstance(data, dict) else {"data": data})
                listings = self.parse_api_json(data)
            except Exception:
                logger.exception("WeBuyCars API fallback failed")
        if not listings:
            raise CollectorError("WeBuyCars: no listings parsed", parser_broken=True)
        return listings

    def parse_api_json(self, data: Any) -> list[ListingPayload]:
        items = []
        if isinstance(data, dict):
            items = data.get("items") or data.get("results") or data.get("data") or []
        elif isinstance(data, list):
            items = data
        results: list[ListingPayload] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            title = item.get("title") or item.get("name") or item.get("modelDescription")
            if title and "fortuner" not in str(title).lower() and item.get("model") != "Fortuner":
                continue
            stock = str(item.get("stockNumber") or item.get("id") or item.get("vehicleId"))
            results.append(
                ListingPayload(
                    source=self.source,
                    source_listing_id=stock,
                    url=item.get("url")
                    or f"https://www.webuycars.co.za/vehicle/{stock}",
                    title=str(title) if title else f"Toyota Fortuner {stock}",
                    variant_raw=str(item.get("variant") or title or ""),
                    year=item.get("year"),
                    price_zar=int(item["price"]) if item.get("price") else None,
                    mileage_km=int(item["mileage"]) if item.get("mileage") else None,
                    colour=item.get("colour") or item.get("color"),
                    dealer_name="WeBuyCars",
                    dealer_location=item.get("branch") or item.get("location"),
                    dealer_stock_number=stock,
                    vin=item.get("vin"),
                    image_urls=list(item.get("images") or item.get("imageUrls") or []),
                    transmission=item.get("transmission"),
                    fuel_type=item.get("fuelType") or item.get("fuel"),
                    drivetrain=item.get("drivetrain") or item.get("driveTrain"),
                    make="Toyota",
                    model="Fortuner",
                    raw_payload=item,
                )
            )
        return results

    def parse_search_html(self, html: str) -> list[ListingPayload]:
        soup = BeautifulSoup(html, "html.parser")
        cards = soup.select("[data-stock], .vehicle-card, .car-card, article")
        results: list[ListingPayload] = []
        for card in cards:
            try:
                stock = card.get("data-stock") or card.get("data-id")
                link = card.select_one("a[href]")
                href = link["href"] if link else None
                if not href and not stock:
                    continue
                if href and href.startswith("/"):
                    href = f"https://www.webuycars.co.za{href}"
                text = card.get_text(" ", strip=True)
                if "fortuner" not in text.lower() and "Fortuner" not in (card.get("data-model") or ""):
                    continue
                listing_id = str(stock) if stock else self._id_from_url(href or text)
                title_el = card.select_one("h2, h3, .title")
                img = card.select_one("img")
                results.append(
                    ListingPayload(
                        source=self.source,
                        source_listing_id=listing_id,
                        url=href or f"https://www.webuycars.co.za/vehicle/{listing_id}",
                        title=title_el.get_text(strip=True) if title_el else None,
                        variant_raw=title_el.get_text(strip=True) if title_el else None,
                        price_zar=self._price(text),
                        mileage_km=self._mileage(text),
                        year=self._year(text),
                        dealer_name="WeBuyCars",
                        dealer_stock_number=listing_id,
                        image_urls=[img["src"]] if img and img.has_attr("src") else [],
                        make="Toyota",
                        model="Fortuner",
                    )
                )
            except Exception:
                logger.exception("Failed parsing WeBuyCars card")
        return results

    @staticmethod
    def _id_from_url(url: str) -> str:
        m = re.search(r"/(\d{4,}|[A-Z0-9-]{5,})", url)
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
