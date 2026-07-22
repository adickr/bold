"""Cars.co.za collector.

Uses the same filtered search URL as the live 4x4 board, e.g.:

https://www.cars.co.za/usedcars/?make_model_variant=Toyota[Fortuner]
  &sort=sort_rank&price_type=listing_price
  &vfs_area=Western%20Cape&vfs_mileage=0-99999
  &vehicle_axle_config=4X4&P=1

Do NOT use the /usedcars/Western-Cape/Toyota/Fortuner/ SEO path — it does
not reliably keep the axle filter and leaks 4x2 / Raised Body stock.

Scrapes **search listing cards only** (price, km, year, location, detail href).
Never opens individual vehicle detail pages — that is unnecessary and multiplies
Cloudflare challenges.

Cars.co.za is often behind Cloudflare; Playwright (with challenge wait)
is preferred when available. Use one headed persistent Chrome session so the
checkbox is needed at most once per collect.
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
    click_next_if_present,
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
    HOME_URL = "https://www.cars.co.za/"

    def build_search_params(self, page: int = 1) -> dict[str, Any]:
        """Exact filter set from the live WC · 4x4 · ≤100k Fortuner board."""
        mileage_hi = max(0, int(self.settings.max_mileage_km) - 1)
        preferred = (self.settings.preferred_province or "").strip() or "Western Cape"
        params: dict[str, Any] = {
            "make_model_variant": "Toyota[Fortuner]",
            "sort": "sort_rank",
            "price_type": "listing_price",
            "vfs_area": preferred,
            "vfs_mileage": f"0-{mileage_hi}",
            "vehicle_axle_config": "4X4",
            "P": page,
        }
        if self.settings.enforce_max_price:
            params["price_to"] = self.settings.stretch_price_zar
        return params

    def search_url(self, page: int = 1) -> str:
        return f"{self.SEARCH_URL}?{urlencode(self.build_search_params(page))}"

    def search_urls(self, page: int = 1) -> list[str]:
        """Only the query-string 4x4 board — never the SEO path that leaks 4x2s."""
        return [self.search_url(page)]

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
                "Cars.co.za: Cloudflare blocked the listing page. "
                "Either (1) use PLAYWRIGHT_HEADED=true with a fresh "
                "./data/chrome-profile-cars and wait for 'Verifying…' to finish "
                "(no automation banner), or (2) start normal Chrome with "
                "--remote-debugging-port=9222 and set PLAYWRIGHT_CDP_URL="
                "http://127.0.0.1:9222 — see README. AutoTrader/WeBuyCars still work.",
                parser_broken=True,
            )
        return listings

    def _search_single_browser_session(self) -> list[ListingPayload]:
        """One Chrome window; scrape search/listing cards only — never open detail pages.

        Only navigate to ONE search URL. Alternate shapes / extra pages re-trigger
        Cloudflare and cause the endless 'Verifying you are human' loop.
        """
        all_listings: list[ListingPayload] = []
        seen: set[str] = set()
        # Prefer one solid page over CF loops; expand only via in-page Next
        max_pages = max(1, min(self.settings.collector_max_pages, 4))
        total_hint: int | None = None
        api_payloads: list[Any] = []

        with browser_page() as page:

            def _on_response(response) -> None:  # type: ignore[no-untyped-def]
                try:
                    url = response.url or ""
                    if response.status >= 400:
                        return
                    if not any(
                        x in url.lower()
                        for x in ("/api/", "graphql", "search", "vehicle", "listing", "usedcars")
                    ):
                        return
                    ctype = (response.headers.get("content-type") or "").lower()
                    if "json" not in ctype and "javascript" not in ctype:
                        return
                    data = response.json()
                    api_payloads.append(data)
                except Exception:
                    return

            page.on("response", _on_response)

            # Single entry URL only — the live query-string 4x4 board (not SEO path)
            entry_url = self.search_url(1)
            logger.info(
                "Cars.co.za: one search listing page (cards only): %s",
                entry_url,
            )
            html = goto_and_wait(
                page,
                entry_url,
                wait_selector="a[href*='/for-sale/used/']",
                wait_through_challenge=True,
                timeout_ms=90000,
            )
            if is_cloudflare_challenge(page):
                logger.warning(
                    "Cars.co.za still on Cloudflare after wait — aborting (will not retry URLs)"
                )
                return []

            self.snapshot_raw("search_p1", html)
            page_listings = self._merge_page_results(html, api_payloads)
            if not page_listings:
                logger.info("Cars.co.za: no listing cards on first search page")
                return []

            total_hint = self._parse_total(html)
            self._accumulate(all_listings, seen, page_listings)
            logger.info(
                "Cars.co.za page 1: parsed %s (unique %s, site_total=%s)",
                len(page_listings),
                len(all_listings),
                total_hint,
            )

            for page_num in range(2, max_pages + 1):
                if total_hint is not None and len(all_listings) >= total_hint:
                    break
                if len(page_listings) < 8:
                    break

                before_api = len(api_payloads)
                # Never soft_goto extra pages — that re-triggers Cloudflare loops.
                # Only advance if an in-page Next control exists.
                if not click_next_if_present(page):
                    logger.info(
                        "Cars.co.za: no in-page Next (or CF) — keeping %s listings from page 1+",
                        len(all_listings),
                    )
                    break
                html = page.content()
                logger.info("Cars.co.za: advanced via in-page Next → page %s", page_num)

                if is_cloudflare_challenge(page):
                    logger.warning(
                        "Cars.co.za challenged on page %s — keeping earlier results only",
                        page_num,
                    )
                    break

                self.snapshot_raw(f"search_p{page_num}", html)
                new_api = api_payloads[before_api:]
                page_listings = self._merge_page_results(html, new_api or api_payloads)
                if not page_listings:
                    logger.info("Cars.co.za page %s returned 0 — stopping", page_num)
                    break

                if total_hint is None:
                    total_hint = self._parse_total(html)
                gained = self._accumulate(all_listings, seen, page_listings)
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

        return self._annotate(all_listings)

    def _merge_page_results(
        self, html: str, api_payloads: list[Any] | None = None
    ) -> list[ListingPayload]:
        rows = list(self.parse_search_html(html))
        for payload in api_payloads or []:
            try:
                rows.extend(self.parse_api_json(payload))
                rows.extend(self.parse_api_json(self._find_list_in_obj(payload)))
            except Exception:
                logger.debug("Cars.co.za API payload parse skipped", exc_info=True)
        return self._valid_vehicle_listings(rows)

    @staticmethod
    def _accumulate(
        all_listings: list[ListingPayload],
        seen: set[str],
        page_listings: list[ListingPayload],
    ) -> int:
        gained = 0
        for item in page_listings:
            if item.source_listing_id in seen:
                continue
            seen.add(item.source_listing_id)
            all_listings.append(item)
            gained += 1
        return gained

    def _search_http_only(self) -> list[ListingPayload]:
        all_listings: list[ListingPayload] = []
        seen: set[str] = set()
        for page in range(1, max(1, min(self.settings.collector_max_pages, 6)) + 1):
            found = False
            for url in self.search_urls(page):
                try:
                    html = self.fetch_text(url)
                    self.snapshot_raw("search_http", html)
                    if "just a moment" in html.lower() or "cf-turnstile" in html.lower():
                        logger.warning("Cars.co.za HTTP hit Cloudflare for %s", url)
                        continue
                    rows = self._valid_vehicle_listings(self.parse_search_html(html))
                    if self._accumulate(all_listings, seen, rows):
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
        by_id: dict[str, ListingPayload] = {}
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
            existing = by_id.get(item.source_listing_id)
            if existing:
                by_id[item.source_listing_id] = self._enrich_listing(existing, item)
                continue
            by_id[item.source_listing_id] = item
            out.append(item)
        # Keep original order, with enriched objects
        return [by_id[i.source_listing_id] for i in out]

    @staticmethod
    def _enrich_listing(primary: ListingPayload, extra: ListingPayload) -> ListingPayload:
        """Fill missing fields from a duplicate parse of the same listing id."""
        updates: dict[str, Any] = {}
        for field in (
            "colour",
            "dealer_name",
            "dealer_location",
            "price_zar",
            "mileage_km",
            "year",
            "title",
            "variant_raw",
            "drivetrain",
        ):
            if getattr(primary, field) in (None, "", []) and getattr(extra, field) not in (None, "", []):
                updates[field] = getattr(extra, field)
        if not primary.image_urls and extra.image_urls:
            updates["image_urls"] = extra.image_urls
        if not updates:
            return primary
        return primary.model_copy(update=updates)

    @staticmethod
    def _drivetrain_from_text(*parts: str | None) -> str | None:
        """Read axle from URL/title. Cars.co.za 4x4 SEO slugs usually include 4x4.

        Do NOT assume 4x4 from the search filter alone — 4x2 demos (e.g. mHev Auto)
        sometimes leak into axle-filtered results.
        """
        blob = " ".join(p for p in parts if p).lower().replace(" ", "")
        # Toyota Raised Body = 4x2 (Tokai etc. slugs say Raised-Body, not 4x2)
        if "raisedbody" in blob or "raised-body" in blob:
            return "4x2"
        if re.search(r"(?:^|[-_/])4x2(?:[-_/]|$)", blob) or "4x2" in blob:
            return "4x2"
        if re.search(r"(?:^|[-_/])4x4(?:[-_/]|$)", blob) or "4x4" in blob or "4wd" in blob:
            return "4x4"
        return None

    def _annotate(self, listings: list[ListingPayload]) -> list[ListingPayload]:
        """Keep only cards with an explicit 4x4 signal in URL/title.

        The live axle-filtered board should already be 4x4-only; this is the
        hard gate so SEO-path leaks / Raised Body / mHev demos never land.
        """
        preferred = (self.settings.preferred_province or "").strip() or None
        out: list[ListingPayload] = []
        dropped = 0
        for item in listings:
            data = item.model_dump()
            detected = self._drivetrain_from_text(
                data.get("url"), data.get("title"), data.get("variant_raw")
            )
            if detected != "4x4":
                dropped += 1
                continue
            data["drivetrain"] = "4x4"
            if preferred and not data.get("dealer_location"):
                data["dealer_location"] = preferred
            elif preferred and preferred.lower() not in (data.get("dealer_location") or "").lower():
                data["dealer_location"] = f"{data.get('dealer_location')}, {preferred}".strip(", ")
            out.append(ListingPayload.model_validate(data))
        if dropped:
            logger.info("Cars.co.za: dropped %s non-explicit-4x4 card(s)", dropped)
        return out

    def parse_api_json(self, data: Any) -> list[ListingPayload]:
        items = self._listing_dicts_from_payload(data)

        results: list[ListingPayload] = []
        for row in items:
            if not isinstance(row, dict):
                continue
            row = self._flatten_api_row(row)
            title = str(row.get("title") or row.get("name") or row.get("heading") or "")
            blob = f"{title} {row.get('model') or ''} {row.get('variant') or ''}"
            if "fortuner" not in blob.lower() and str(row.get("model") or "").lower() != "fortuner":
                continue
            listing_id = str(
                row.get("id")
                or row.get("vehicle_id")
                or row.get("vehicleId")
                or row.get("listing_id")
                or row.get("code")
                or ""
            )
            url = (
                row.get("website_url")
                or row.get("url")
                or row.get("permalink")
                or row.get("link")
            )
            if not listing_id and url:
                listing_id = self._id_from_url(str(url))
            if not listing_id or not listing_id.isdigit():
                # Ignore opaque codes; require numeric listing ids
                if url:
                    listing_id = self._id_from_url(str(url))
                if not listing_id or not str(listing_id).isdigit():
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
            price_zar = None
            if price not in (None, ""):
                try:
                    price_zar = int(price)
                except (TypeError, ValueError):
                    price_zar = self._price(str(price))
            mileage_km = None
            if mileage not in (None, ""):
                try:
                    mileage_km = int(mileage)
                except (TypeError, ValueError):
                    mileage_km = self._mileage(str(mileage))
            if price_zar is None and mileage_km is None:
                continue
            location = (
                row.get("location")
                or row.get("area")
                or row.get("province")
                or row.get("city")
                or row.get("agent_locality")
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
            elif isinstance(images, dict):
                # Cars.co.za image descriptor object — skip for now
                images = []
            colour = row.get("colour") or row.get("color") or row.get("Colour")
            if colour is not None:
                colour = str(colour).strip() or None
            results.append(
                ListingPayload(
                    source=self.source,
                    source_listing_id=str(listing_id),
                    url=str(url),
                    title=title or f"Toyota Fortuner {listing_id}",
                    variant_raw=str(row.get("variant") or title or ""),
                    year=int(year) if year not in (None, "") else None,
                    price_zar=price_zar,
                    mileage_km=mileage_km,
                    colour=colour,
                    dealer_name=row.get("dealer")
                    or row.get("dealer_name")
                    or row.get("seller")
                    or row.get("agent_name"),
                    dealer_location=str(location) if location else None,
                    image_urls=[str(x) for x in images[:12] if x],
                    drivetrain=self._drivetrain_from_text(
                        str(url),
                        title,
                        str(row.get("variant") or ""),
                        str(row.get("vehicle_axle_config") or ""),
                    ),
                    make="Toyota",
                    model="Fortuner",
                    raw_payload=row,
                )
            )
        return results

    @classmethod
    def _listing_dicts_from_payload(cls, data: Any) -> list[Any]:
        """Collect listing row dicts from flat API shapes or Cars.co.za NEXT_DATA."""
        if isinstance(data, list):
            return [x for x in data if isinstance(x, dict)]
        if not isinstance(data, dict):
            return []

        for key in ("results", "listings", "vehicles", "data", "hits", "items"):
            val = data.get(key)
            if isinstance(val, list) and val and isinstance(val[0], dict):
                return val
            if isinstance(val, dict):
                nested = val.get("results") or val.get("listings") or val.get("hits") or val.get("data")
                if isinstance(nested, list) and nested and isinstance(nested[0], dict):
                    return nested

        # __NEXT_DATA__ → props.initialState.searchCarReducer.searchResults
        try:
            search = (
                data.get("props", {})
                .get("initialState", {})
                .get("searchCarReducer", {})
                .get("searchResults")
            )
        except AttributeError:
            search = None
        if isinstance(search, dict):
            rows: list[Any] = []
            primary = search.get("data")
            if isinstance(primary, list):
                rows.extend(primary)
            featured = (
                (search.get("meta") or {}).get("featured_listings", {}).get("data")
                if isinstance(search.get("meta"), dict)
                else None
            )
            if isinstance(featured, list):
                rows.extend(featured)
            if rows:
                return [x for x in rows if isinstance(x, dict)]

        found = cls._find_list_in_obj(data)
        return [x for x in found if isinstance(x, dict)] if isinstance(found, list) else []

    @staticmethod
    def _flatten_api_row(row: dict[str, Any]) -> dict[str, Any]:
        """Unwrap JSON:API {id, attributes:{...}} rows into a flat dict."""
        attrs = row.get("attributes")
        if isinstance(attrs, dict):
            flat = dict(attrs)
            if row.get("id") is not None:
                flat.setdefault("id", row["id"])
            return flat
        return row

    def parse_search_html(self, html: str) -> list[ListingPayload]:
        if "just a moment" in html.lower() and "/for-sale/" not in html.lower():
            return []
        soup = BeautifulSoup(html, "html.parser")
        results: list[ListingPayload] = []
        seen: set[str] = set()

        # Next.js / embedded JSON blobs — prefer these (include colour)
        for script in soup.select("script#__NEXT_DATA__, script[type='application/json']"):
            try:
                data = json.loads(script.string or "")
            except Exception:
                continue
            for item in self.parse_api_json(data):
                if item.source_listing_id in seen:
                    continue
                seen.add(item.source_listing_id)
                results.append(item)

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
                        drivetrain=self._drivetrain_from_text(href, title, text),
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
                    drivetrain=self._drivetrain_from_text(href, title, text),
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
        for key in (
            "props",
            "pageProps",
            "initialState",
            "searchCarReducer",
            "searchResults",
            "fallback",
            "search",
            "vehicles",
            "results",
            "data",
            "featured_listings",
        ):
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
        """Extract odometer km from card text; avoid swallowing adjacent prices."""
        candidates: list[int] = []
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
            # Price + km glued: "61999541000 Km" → keep trailing odometer-sized chunk
            if val > 500_000 and len(digits) >= 7:
                for n in (6, 5, 4, 3):
                    rest = int(digits[-n:])
                    if 500 < rest <= 500_000:
                        val = rest
                        break
            if 0 < val <= 500_000:
                candidates.append(val)
        return candidates[-1] if candidates else None

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
