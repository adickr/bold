"""WeBuyCars collector — public elastic search API with proof-of-work.

Primary path posts to:
  https://appgateway.webuycars.co.za/website-elastic-backend/api/search

using the same PoW flow as the website (challenge → solve → validate →
x-proof-of-work-token). Filters mirror the live buy-a-car URL:

  /buy-a-car?km_min=0&km_max=100000&km=0&km=100000&axle=4X4
    &province=Western+Cape&q=Toyota+Fortuner

Playwright intercept remains a fallback.
"""

from __future__ import annotations

import hashlib
import logging
import random
import re
import time
import uuid
from typing import Any
from urllib.parse import urlencode

from bs4 import BeautifulSoup

from app.collectors.base import BaseCollector, CollectorError
from app.collectors.browser import fetch_json_from_responses, fetch_rendered_html, playwright_available
from app.schemas.listings import ListingPayload

logger = logging.getLogger(__name__)

API_BASE = "https://appgateway.webuycars.co.za"
SEARCH_API = f"{API_BASE}/website-elastic-backend/api/search"
POW_CHALLENGE = f"{API_BASE}/website-nest-backend/api/v1/proof-of-work/challenge"
POW_VALIDATE = f"{API_BASE}/website-nest-backend/api/v1/proof-of-work/validate"


def _write_uint32(buf: list[int], val: int, n: int) -> int:
    buf[n] = (val >> 24) & 255
    buf[n + 1] = (val >> 16) & 255
    buf[n + 2] = (val >> 8) & 255
    buf[n + 3] = val & 255
    return n + 4


def _gen_nonce(buf: list[int]) -> None:
    ts = int(time.time() * 1000)
    n = _write_uint32(buf, (ts // 4294967296) & 0xFFFFFFFF, 0)
    n = _write_uint32(buf, ts & 0xFFFFFFFF, n)
    end = n + 4 * ((len(buf) - n) // 4)
    while n < end:
        _write_uint32(buf, int(4294967296 * random.random()) & 0xFFFFFFFF, n)
        n += 4
    while n < len(buf):
        buf[n] = int(256 * random.random()) & 255
        n += 1


def _check_complexity(digest: bytes, complexity: int) -> bool:
    if complexity >= 8 * len(digest):
        return False
    n = 0
    o = 0
    while n <= complexity - 8:
        if digest[o] != 0:
            return False
        n += 8
        o += 1
    mask = (255 << (8 + n - complexity)) & 255
    return (digest[o] & mask) == 0


def solve_proof_of_work(challenge_hex: str, difficulty: int) -> str:
    """Match the website Solver: SHA256(challenge || nonce) with leading zero bits."""
    prefix = bytes.fromhex(challenge_hex)
    nonce = [0] * 16
    while True:
        _gen_nonce(nonce)
        digest = hashlib.sha256(prefix + bytes(nonce)).digest()
        if _check_complexity(digest, int(difficulty)):
            return ",".join(str(b) for b in nonce)


class WeBuyCarsCollector(BaseCollector):
    source = "webuycars"
    category = "marketplace"

    SEARCH_URL = "https://www.webuycars.co.za/buy-a-car"
    PAGE_SIZE = 24

    def build_search_params(self) -> list[tuple[str, Any]]:
        """Browser URL params matching the live buy-a-car filters.

        Example:
          /buy-a-car?km_min=0&km_max=100000&km=0&km=100000&axle=4X4
            &province=Western+Cape&q=Toyota+Fortuner
        """
        max_km = int(self.settings.max_mileage_km or 100_000)
        params: list[tuple[str, Any]] = [
            ("km_min", 0),
            ("km_max", max_km),
            ("km", 0),
            ("km", max_km),
            ("q", "Toyota Fortuner"),
        ]
        req = (self.settings.required_drivetrain or "").strip().lower().replace(" ", "")
        if req in {"4x4", "4wd", "awd"}:
            params.insert(-1, ("axle", "4X4"))
        elif req in {"4x2", "2wd"}:
            params.insert(-1, ("axle", "4X2"))
        preferred = (self.settings.preferred_province or "").strip()
        if preferred:
            # Live site uses plain province=Western+Cape (not a JSON array)
            params.insert(-1, ("province", preferred))
        return params

    def build_search_url(self) -> str:
        return f"{self.SEARCH_URL}?{urlencode(self.build_search_params())}"

    def build_api_body(self, *, offset: int, size: int) -> dict[str, Any]:
        max_km = int(self.settings.max_mileage_km or 100_000)
        preferred = (self.settings.preferred_province or "").strip()
        province: list[str] | None = [preferred] if preferred else None
        req = (self.settings.required_drivetrain or "").strip().lower().replace(" ", "")
        axle: list[str] | None = None
        if req in {"4x4", "4wd", "awd"}:
            axle = ["4X4"]
        elif req in {"4x2", "2wd"}:
            axle = ["4X2"]
        body: dict[str, Any] = {
            "to": offset,
            "size": size,
            "type": "Vehicle",
            "filter_type": "all",
            "subcategory": None,
            "q": "Toyota Fortuner",
            "Make": ["Toyota"],
            "Model": ["Fortuner"],
            "Roadworthy": None,
            "Auctions": [],
            "Variant": None,
            "DealerKey": None,
            "FuelType": None,
            "BodyType": None,
            "Gearbox": None,
            "AxleConfiguration": axle,
            "Colour": None,
            "FinanceGrade": None,
            "Priced_Amount_Gte": 0,
            "Priced_Amount_Lte": 0,
            "MonthlyInstallment_Amount_Gte": 0,
            "MonthlyInstallment_Amount_Lte": 0,
            "auctionDate": None,
            "auctionEndDate": None,
            "auctionDurationInSeconds": None,
            "Kilometers_Gte": 0,
            # Match live buy-a-car km_max / km range
            "Kilometers_Lte": max_km,
            "Priced_Amount_Sort": "asc",
            "Bid_Amount_Sort": "",
            "Kilometers_Sort": "",
            "Year_Sort": "",
            "Auction_Date_Sort": "",
            "Auction_Lot_Sort": "",
            "Year": [],
            "Price_Update_Date_Sort": "",
            "Online_Auction_Date_Sort": "",
            "Online_Auction_In_Progress": "",
            # Match live province=Western+Cape → API Province: ["Western Cape"]
            "Province": province,
        }
        return body

    def search(self) -> list[ListingPayload]:
        listings: list[ListingPayload] = []

        try:
            listings = self.search_via_api()
            if listings:
                return self._dedupe(listings)
        except Exception:
            logger.exception("WeBuyCars API search failed")

        url = self.build_search_url()
        if playwright_available() and self.settings.use_playwright:
            try:
                payloads = fetch_json_from_responses(
                    url,
                    url_substring="website-elastic-backend/api/search",
                    settle_ms=2500,
                    scroll_rounds=max(4, self.settings.collector_max_pages // 2),
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

    def obtain_pow_token(self) -> str:
        fingerprint = f"fortuner-agent-{uuid.uuid4().hex}"
        challenge_resp = self.client.post(
            f"{POW_CHALLENGE}?fingerprintId={fingerprint}",
            json={"fingerprintId": fingerprint},
            headers=self._api_headers(),
        )
        challenge_resp.raise_for_status()
        challenge = challenge_resp.json()
        challenge_hex = challenge["challenge"]
        difficulty = int(challenge["difficulty"])
        solution = solve_proof_of_work(challenge_hex, difficulty)
        validate_resp = self.client.post(
            f"{POW_VALIDATE}?fingerprintId={fingerprint}",
            json={
                "fingerprintId": fingerprint,
                "token": solution,
                "challenge": challenge_hex,
            },
            headers=self._api_headers(),
        )
        validate_resp.raise_for_status()
        token = (validate_resp.json() or {}).get("token")
        if not token:
            raise CollectorError("WeBuyCars: PoW validate returned no token")
        return str(token)

    def _api_headers(self, pow_token: str | None = None) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Origin": "https://www.webuycars.co.za",
            "Referer": self.build_search_url(),
            "User-Agent": self.settings.user_agent,
        }
        if pow_token:
            headers["x-proof-of-work-token"] = pow_token
        return headers

    def search_via_api(self) -> list[ListingPayload]:
        """Paginate Fortuner 4x4 results via the public elastic API."""
        pow_token = self.obtain_pow_token()
        page_size = self.PAGE_SIZE
        max_pages = max(1, self.settings.collector_max_pages)
        max_results = max_pages * page_size

        all_listings: list[ListingPayload] = []
        seen: set[str] = set()
        offset = 0
        total: int | None = None
        pages = 0
        while pages < max_pages and len(seen) < max_results:
            body = self.build_api_body(offset=offset, size=page_size)
            self.throttle()
            resp = self.client.post(
                SEARCH_API,
                json=body,
                headers=self._api_headers(pow_token),
            )
            if resp.status_code in {401, 403}:
                pow_token = self.obtain_pow_token()
                resp = self.client.post(
                    SEARCH_API,
                    json=body,
                    headers=self._api_headers(pow_token),
                )
            resp.raise_for_status()
            payload = resp.json()
            if pages == 0:
                self.snapshot_raw(
                    "search_api",
                    {
                        "total": payload.get("total"),
                        "sample": (payload.get("data") or [])[:2],
                    },
                )
            batch = self.parse_api_json(payload)
            if total is None:
                total_obj = payload.get("total") or {}
                total = int(total_obj.get("value") or 0) if isinstance(total_obj, dict) else None
            gained = 0
            for item in batch:
                if item.source_listing_id in seen:
                    continue
                seen.add(item.source_listing_id)
                all_listings.append(item)
                gained += 1
            logger.info(
                "WeBuyCars API offset=%s batch=%s gained=%s unique=%s total=%s",
                offset,
                len(batch),
                gained,
                len(seen),
                total,
            )
            pages += 1
            if not batch:
                break
            offset += len(batch)
            if total is not None and offset >= total:
                break
            if len(batch) < page_size:
                break

        return all_listings

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
                or row.get("DealerKey")
                or row.get("branch")
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
            availability = self._sale_availability(row)
            if availability != "available":
                logger.info(
                    "WeBuyCars %s marked %s (Status=%s) — will deactivate",
                    stock,
                    availability,
                    row.get("Status"),
                )
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
                    availability=availability,
                    make="Toyota",
                    model="Fortuner",
                    raw_payload=row,
                )
            )
        return results

    @staticmethod
    def _sale_availability(row: dict[str, Any]) -> str:
        """WeBuyCars 'Sale in progress' maps to API Status=Reserved."""
        status = str(row.get("Status") or row.get("status") or "").strip().lower()
        if status in {"reserved", "sold", "sale in progress", "pending", "withdrawn", "inactive"}:
            return "unavailable"
        if status in {"for sale", "available"}:
            return "available"
        # Unknown — treat as available only when clearly stocked for sale
        stock_status = str(row.get("StockStatus") or "").strip().lower()
        if stock_status in {"sold", "reserved"}:
            return "unavailable"
        return "available"

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
        # Normalise relative CDN paths
        normalised: list[str] = []
        for url in urls[:12]:
            if url.startswith("//"):
                normalised.append(f"https:{url}")
            elif url.startswith("/"):
                normalised.append(f"https://www.webuycars.co.za{url}")
            else:
                normalised.append(url)
        return normalised

    def parse_json_ld(self, html: str) -> list[ListingPayload]:
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
                availability = "available"
                if re.search(r"sale\s+in\s+progress|\breserved\b|\bsold\b", text, re.I):
                    availability = "unavailable"
                if "fortuner" not in text.lower() and "Fortuner" not in (card.get("data-model") or ""):
                    if "/Toyota/" in href or "fortuner" in href.lower():
                        pass
                    elif "fortuner" not in href.lower():
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
                        availability=availability,
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
