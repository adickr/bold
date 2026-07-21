"""WeBuyCars collector — prefers intercepted public search JSON via Playwright."""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import urlencode

from bs4 import BeautifulSoup

from app.collectors.base import BaseCollector, CollectorError
from app.collectors.browser import fetch_json_from_responses, fetch_rendered_html, playwright_available
from app.schemas.listings import ListingPayload

logger = logging.getLogger(__name__)


class WeBuyCarsCollector(BaseCollector):
    source = "webuycars"
    category = "marketplace"

    SEARCH_URL = "https://www.webuycars.co.za/buy-a-car"

    def build_search_params(self) -> dict[str, Any]:
        # SPA expects JSON-array query values, e.g. Make=["Toyota"]
        params: dict[str, Any] = {
            "Make": '["Toyota"]',
            "Model": '["Fortuner"]',
        }
        preferred = (self.settings.preferred_province or "").strip().lower()
        if preferred in {"western cape", "wc", "western-cape"}:
            params["Provinces"] = '["Western Cape"]'
        return params

    def search(self) -> list[ListingPayload]:
        url = f"{self.SEARCH_URL}?{urlencode(self.build_search_params())}"
        listings: list[ListingPayload] = []

        if playwright_available() and self.settings.use_playwright:
            try:
                payloads = fetch_json_from_responses(
                    url,
                    url_substring="website-elastic-backend/api/search",
                    settle_ms=2500,
                )
                self.snapshot_raw(
                    "search_api_intercept",
                    {"responses": len(payloads), "sample": payloads[:1]},
                )
                for payload in payloads:
                    listings.extend(self.parse_api_json(payload))
                if listings:
                    return self._dedupe(listings)
            except Exception:
                logger.exception("WeBuyCars Playwright intercept failed")

            try:
                html = fetch_rendered_html(url)
                self.snapshot_raw("search_rendered", html)
                listings = self.parse_search_html(html)
                listings.extend(self.parse_json_ld(html))
                if listings:
                    return self._dedupe(listings)
            except Exception:
                logger.exception("WeBuyCars Playwright HTML failed")

        # Plain HTTP rarely works (SPA shell only), but try for completeness.
        try:
            html = self.fetch_text(url)
            self.snapshot_raw("search", html)
            listings = self.parse_search_html(html)
            listings.extend(self.parse_json_ld(html))
        except Exception:
            logger.exception("WeBuyCars HTTP search failed")

        if not listings:
            raise CollectorError("WeBuyCars: no listings parsed", parser_broken=True)
        return self._dedupe(listings)

    def parse_api_json(self, data: Any) -> list[ListingPayload]:
        items: list[Any] = []
        if isinstance(data, dict):
            for key in ("data", "items", "results", "vehicles", "Hits", "hits"):
                val = data.get(key)
                if isinstance(val, list):
                    items = val
                    break
                if isinstance(val, dict):
                    nested = val.get("items") or val.get("hits") or val.get("results")
                    if isinstance(nested, list):
                        items = nested
                        break
            if not items and ("StockNumber" in data or "stockNumber" in data):
                items = [data]
        elif isinstance(data, list):
            items = data

        results: list[ListingPayload] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            src = item.get("_source")
            row = src if isinstance(src, dict) else item
            model = str(row.get("Model") or row.get("model") or "")
            title = (
                row.get("OnlineDescription")
                or row.get("Description")
                or row.get("title")
                or row.get("name")
                or row.get("modelDescription")
            )
            blob = f"{title} {model} {row.get('Variant') or row.get('variant') or ''}"
            if "fortuner" not in blob.lower() and model.lower() != "fortuner":
                continue
            stock = str(
                row.get("StockNumber")
                or row.get("stockNumber")
                or row.get("id")
                or row.get("vehicleId")
                or ""
            )
            if not stock:
                continue
            price = row.get("Price") or row.get("BuyNowPrice") or row.get("price")
            mileage = row.get("Mileage") or row.get("mileage") or row.get("odo")
            year = row.get("Year") or row.get("year")
            colour = row.get("Colour") or row.get("colour") or row.get("color")
            province = row.get("Province") or row.get("province")
            branch = (
                row.get("BranchName")
                or row.get("branchName")
                or row.get("branch")
                or row.get("DealerKey")
            )
            if branch and province:
                dealer_location = f"{branch}, {province}"
            elif province:
                dealer_location = str(province)
            elif branch:
                dealer_location = str(branch)
            else:
                dealer_location = None
            image_urls = self._extract_images(row)
            variant = row.get("Variant") or row.get("variant") or title
            axle = str(row.get("AxleConfiguration") or row.get("drivetrain") or "")
            drivetrain = None
            if axle:
                if "4X4" in axle.upper() or "4x4" in axle:
                    drivetrain = "4x4"
                elif "4X2" in axle.upper() or "4x2" in axle:
                    drivetrain = "4x2"
            results.append(
                ListingPayload(
                    source=self.source,
                    source_listing_id=stock,
                    url=f"https://www.webuycars.co.za/buy-a-car/{stock}",
                    title=str(title) if title else f"Toyota Fortuner {stock}",
                    description=str(row.get("ServiceHistory") or ""),
                    variant_raw=str(variant or ""),
                    year=int(year) if year not in (None, "") else None,
                    price_zar=int(price) if price not in (None, "") else None,
                    mileage_km=int(mileage) if mileage not in (None, "") else None,
                    colour=str(colour) if colour else None,
                    dealer_name="WeBuyCars",
                    dealer_location=dealer_location,
                    dealer_stock_number=stock,
                    vin=row.get("VIN") or row.get("vin"),
                    image_urls=image_urls,
                    transmission=row.get("Gearbox") or row.get("transmission"),
                    fuel_type=row.get("FuelType") or row.get("fuelType"),
                    drivetrain=drivetrain,
                    make="Toyota",
                    model="Fortuner",
                    raw_payload=row,
                )
            )
        return results

    @staticmethod
    def _extract_images(row: dict[str, Any]) -> list[str]:
        raw = row.get("Images")
        if raw in (None, "", [], {}):
            raw = row.get("ImagePaths")
        if raw in (None, "", [], {}):
            raw = row.get("images")
        urls: list[str] = []
        if isinstance(raw, dict):
            candidates = list(raw.values())
        elif isinstance(raw, (list, tuple)):
            candidates = list(raw)
        elif raw:
            candidates = [raw]
        else:
            candidates = []
        for item in candidates:
            if isinstance(item, dict):
                url = item.get("url") or item.get("Url") or item.get("path") or item.get("Path")
                if url:
                    urls.append(str(url))
            elif item:
                urls.append(str(item))
        return urls[:12]

    def parse_json_ld(self, html: str) -> list[ListingPayload]:
        from bs4 import BeautifulSoup
        import json

        soup = BeautifulSoup(html, "html.parser")
        results: list[ListingPayload] = []
        for tag in soup.select('script[type="application/ld+json"]'):
            try:
                data = json.loads(tag.string or "")
            except Exception:
                continue
            blocks = data if isinstance(data, list) else [data]
            for block in blocks:
                if not isinstance(block, dict):
                    continue
                elements = []
                if block.get("@type") == "ItemList":
                    elements = block.get("itemListElement") or []
                for el in elements:
                    item = el.get("item") if isinstance(el, dict) else None
                    if not isinstance(item, dict):
                        continue
                    if "fortuner" not in str(item.get("name", "")).lower() and item.get("model") != "Fortuner":
                        continue
                    stock = str(item.get("description") or "")
                    offer = item.get("offers") or {}
                    mileage = None
                    odo = item.get("mileageFromOdometer") or {}
                    if isinstance(odo, dict):
                        mileage = odo.get("value")
                    results.append(
                        ListingPayload(
                            source=self.source,
                            source_listing_id=stock or str(item.get("name")),
                            url=el.get("url") or f"https://www.webuycars.co.za/buy-a-car/{stock}",
                            title=item.get("name"),
                            variant_raw=item.get("name"),
                            year=item.get("vehicleModelDate"),
                            price_zar=int(offer["price"]) if offer.get("price") else None,
                            mileage_km=int(mileage) if mileage else None,
                            image_urls=[item["image"]] if item.get("image") else [],
                            dealer_name="WeBuyCars",
                            make="Toyota",
                            model="Fortuner",
                            raw_payload=item,
                        )
                    )
        return results

    def parse_search_html(self, html: str) -> list[ListingPayload]:
        soup = BeautifulSoup(html, "html.parser")
        cards = soup.select("[data-stock], .vehicle-card, .car-card, article, a[href*='/buy-a-car/']")
        results: list[ListingPayload] = []
        seen: set[str] = set()
        for card in cards:
            try:
                href = None
                if card.name == "a" and card.has_attr("href"):
                    href = card["href"]
                else:
                    link = card.select_one("a[href*='/buy-a-car/']")
                    href = link["href"] if link else None
                if not href:
                    continue
                if href.startswith("/"):
                    href = f"https://www.webuycars.co.za{href}"
                stock = card.get("data-stock") or card.get("data-id")
                if not stock:
                    m = re.search(r"/buy-a-car/([A-Z0-9]{6,})", href, re.I)
                    stock = m.group(1) if m else None
                if not stock or stock in seen:
                    continue
                text = card.get_text(" ", strip=True)
                if "fortuner" not in text.lower() and "Fortuner" not in (card.get("data-model") or ""):
                    # detail cards sometimes only show stock in href; keep if stock-like URL
                    if "/Toyota/" in href or "fortuner" in href.lower():
                        pass
                    elif "fortuner" not in href.lower():
                        # still allow stock pages
                        if not re.search(r"/buy-a-car/[A-Z0-9]{6,}", href, re.I):
                            continue
                seen.add(str(stock))
                title_el = card.select_one("h2, h3, .title") if hasattr(card, "select_one") else None
                img = card.select_one("img") if hasattr(card, "select_one") else None
                results.append(
                    ListingPayload(
                        source=self.source,
                        source_listing_id=str(stock),
                        url=href,
                        title=title_el.get_text(strip=True) if title_el else text[:120] or None,
                        variant_raw=title_el.get_text(strip=True) if title_el else None,
                        price_zar=self._price(text),
                        mileage_km=self._mileage(text),
                        year=self._year(text),
                        dealer_name="WeBuyCars",
                        dealer_stock_number=str(stock),
                        image_urls=[img["src"]] if img is not None and img.has_attr("src") else [],
                        make="Toyota",
                        model="Fortuner",
                    )
                )
            except Exception:
                logger.exception("Failed parsing WeBuyCars card")
        return results

    @staticmethod
    def _dedupe(listings: list[ListingPayload]) -> list[ListingPayload]:
        out: dict[str, ListingPayload] = {}
        for item in listings:
            out[item.source_listing_id] = item
        return list(out.values())

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
