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

    SEARCH_URL = "https://www.autotrader.co.za/cars-for-sale/toyota/fortuner/4x4"
    # Province URLs need the province id segment (p-9 = Western Cape)
    SEARCH_URL_WC = "https://www.autotrader.co.za/cars-for-sale/western-cape/p-9/toyota/fortuner/4x4"

    def search_base_url(self) -> str:
        preferred = (self.settings.preferred_province or "").strip().lower()
        if preferred in {"western cape", "wc", "western-cape"}:
            return self.SEARCH_URL_WC
        return self.SEARCH_URL

    def build_search_params(self, page: int = 1) -> dict[str, Any]:
        params: dict[str, Any] = {
            "mileage_to": self.settings.stretch_mileage_km,
            "rcp": self.settings.collector_results_per_page,
        }
        if page > 1:
            params["pagenumber"] = page
        if self.settings.enforce_max_price:
            params["price_to"] = self.settings.stretch_price_zar
        return params

    def search(self) -> list[ListingPayload]:
        all_listings: list[ListingPayload] = []
        max_pages = max(1, self.settings.collector_max_pages)
        need_playwright = False

        for page in range(1, max_pages + 1):
            url = f"{self.search_base_url()}?{urlencode(self.build_search_params(page))}"
            page_listings = self._search_one_page_http(url)
            if not page_listings and page == 1:
                need_playwright = True
                break
            if not page_listings:
                logger.info("AutoTrader page %s returned 0 listings — stopping", page)
                break
            before = len(all_listings)
            all_listings.extend(page_listings)
            all_listings = self._dedupe(all_listings)
            gained = len(all_listings) - before
            logger.info(
                "AutoTrader page %s: parsed %s (unique total %s, +%s)",
                page,
                len(page_listings),
                len(all_listings),
                gained,
            )
            if gained == 0 or len(page_listings) < 8:
                break

        if need_playwright and playwright_available() and self.settings.use_playwright:
            try:
                all_listings = self._search_all_pages_playwright(max_pages)
            except Exception:
                logger.exception("AutoTrader Playwright search failed")

        listings = [x for x in all_listings if self.is_detail_url(x.url)]
        listings = [x for x in listings if self._is_plausible_card(x)]
        listings = self._annotate_search_scope(listings)
        if not listings:
            raise CollectorError("AutoTrader: no listings parsed", parser_broken=True)
        return listings

    def _search_one_page_http(self, url: str) -> list[ListingPayload]:
        try:
            html = self.fetch_text(url)
            self.snapshot_raw("search", html)
            return [x for x in self.parse_all(html) if self.is_detail_url(x.url)]
        except Exception:
            logger.exception("AutoTrader HTTP search failed for %s", url)
            return []

    def _search_all_pages_playwright(self, max_pages: int) -> list[ListingPayload]:
        """One browser session for all AutoTrader search pages (cards only)."""
        from app.collectors.browser import soft_goto

        all_listings: list[ListingPayload] = []
        with browser_page() as page:
            for page_num in range(1, max_pages + 1):
                url = f"{self.search_base_url()}?{urlencode(self.build_search_params(page_num))}"
                if page_num == 1:
                    page.goto(url, wait_until="domcontentloaded", timeout=60000)
                    try:
                        page.wait_for_load_state("networkidle", timeout=45000)
                    except Exception:
                        pass
                    page.wait_for_timeout(2000)
                else:
                    soft_goto(page, url, timeout_ms=60000)
                page.mouse.wheel(0, 3500)
                page.wait_for_timeout(800)
                html = page.content()
                self.snapshot_raw(f"search_rendered_p{page_num}", html)
                page_listings = self._listings_from_playwright_page(page, html)
                if not page_listings:
                    break
                before = len(all_listings)
                all_listings.extend(page_listings)
                all_listings = self._dedupe(all_listings)
                if len(all_listings) == before or len(page_listings) < 8:
                    break
        return all_listings

    def _listings_from_playwright_page(self, page: Any, html: str) -> list[ListingPayload]:
        listings = [x for x in self.parse_all(html) if self.is_detail_url(x.url)]
        if listings:
            return listings

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
            # Prefer link-local title; never trust giant ancestor text for drivetrain
            title = self._resolve_title(self._title_from_text(text), href, text)
            compact = " ".join(
                (self._title_from_text(text) or "", title, href)
            )[:200]
            results.append(
                ListingPayload(
                    source=self.source,
                    source_listing_id=listing_id,
                    url=href.split("?")[0],
                    title=title,
                    variant_raw=title,
                    price_zar=self._extract_price(text),
                    mileage_km=self._extract_mileage(text),
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
                container = link.find_parent(["article", "li"]) or link.find_parent("div") or link.parent
                # Prefer a compact card ancestor — huge wrappers include filter chips
                if hasattr(container, "get_text") and len(container.get_text(" ", strip=True)) > 800:
                    tighter = link.find_parent("article") or link.parent
                    if tighter is not None:
                        container = tighter
                title_el = None
                if hasattr(container, "select_one"):
                    title_el = container.select_one("h2, h3, .title, [data-testid='listing-title']")
                meta = (
                    container.get_text(" ", strip=True)
                    if container is not None
                    else link.get_text(" ", strip=True)
                )
                # Cap meta so page-level filter chips don't dominate
                if len(meta) > 500:
                    meta = meta[:500]
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
        """WC search pages often omit suburb/province on cards — tag so browse filters work.

        AutoTrader's Western Cape URL is already region-scoped; empty location must not
        cause the dashboard's province filter to hide every result.
        """
        preferred = (self.settings.preferred_province or "").strip()
        is_wc = preferred.lower() in {"western cape", "wc", "western-cape"}
        if not is_wc:
            return listings

        out: list[ListingPayload] = []
        for item in listings:
            data = item.model_dump()
            # Prefer SEO slug for drivetrain — never invent 4x4 from page filter chips
            url_dt = self.drivetrain_from_url(data.get("url"))
            if url_dt:
                data["drivetrain"] = url_dt
            elif not data.get("drivetrain"):
                compact = " ".join(
                    filter(None, [data.get("title"), data.get("variant_raw")])
                )
                data["drivetrain"] = detect_drivetrain(compact) if len(compact) < 180 else None
            if self._is_chip_title(data.get("title")):
                data["title"] = self._resolve_title(data.get("title"), data.get("url"))
                data["variant_raw"] = data["title"]
            loc = (data.get("dealer_location") or "").strip()
            if not loc:
                data["dealer_location"] = self._location_from_url(data.get("url") or "") or preferred
            elif "western cape" not in loc.lower() and not _LOCATION_HINT_RE.search(loc):
                data["dealer_location"] = f"{loc}, {preferred}"
            out.append(ListingPayload.model_validate(data))
        return [x for x in out if self._is_plausible_card(x) and x.drivetrain != "4x2"]

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
    def _extract_year(text: str) -> int | None:
        m = re.search(r"\b(20[0-2]\d)\b", text)
        return int(m.group(1)) if m else None

    @staticmethod
    def _extract_mileage(text: str) -> int | None:
        """Prefer card odometer; ignore filter chips like 'Up to 100 000 km'."""
        cleaned = re.sub(
            r"(?i)(up\s*to|mileage\s*to|max(?:imum)?|under|below|less\s*than)\s*[\d\s,]+\s*km",
            " ",
            text or "",
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
        for m in re.finditer(
            r"(?<![A-Za-z])R[ \t]*(\d{1,3}(?:[ \t]\d{3}){1,3}|\d{5,7})\b",
            text or "",
        ):
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
