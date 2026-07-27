"""Helpers for listing media and outbound links."""

from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse


SOURCE_LABELS = {
    "autotrader": "AutoTrader",
    "cars_co_za": "Cars.co.za",
    "webuycars": "WeBuyCars",
}

SOURCE_ORIGINS = {
    "autotrader": "https://www.autotrader.co.za",
    "cars_co_za": "https://www.cars.co.za",
    "webuycars": "https://www.webuycars.co.za",
}

_AT_DETAIL_RE = re.compile(
    r"^/car-for-sale/(?:[^/]+/){1,12}(?P<id>\d{6,})/?$",
    re.I,
)
_CARS_USED_RE = re.compile(
    r"/for-sale/used/[^\"'\s>]*/(?P<id>\d{5,})/?",
    re.I,
)


def source_label(source: str | None) -> str:
    if not source:
        return "Source"
    return SOURCE_LABELS.get(source, source.replace("_", " ").title())


def absolute_url(url: str | None, *, source: str | None = None) -> str | None:
    if not url:
        return None
    value = url.strip()
    if not value or value.startswith("data:"):
        return None
    if value.startswith("//"):
        return "https:" + value
    if value.startswith("http://") or value.startswith("https://"):
        return value
    origin = SOURCE_ORIGINS.get(source or "", "")
    if value.startswith("/") and origin:
        return urljoin(origin, value)
    # Relative CDN paths sometimes omit scheme/host incorrectly
    if origin and not urlparse(value).scheme:
        return urljoin(origin + "/", value.lstrip("/"))
    return value


def _slugify(text: str | None) -> str:
    raw = (text or "").lower()
    raw = re.sub(r"toyota|fortuner", " ", raw)
    raw = re.sub(r"[^a-z0-9.]+", "-", raw).strip("-")
    return raw[:80] or "4x4"


def rebuild_autotrader_url(
    listing_id: str | None,
    *,
    title: str | None = None,
    variant: str | None = None,
) -> str | None:
    """Rebuild an AutoTrader detail URL from listing id + variant.

    Preserves engine dots (``2.8gd-6``). Wrong hyphenated engines like ``2-4gd-6``
    historically 503'd — never emit those.
    """
    if not listing_id or not str(listing_id).isdigit():
        return None
    from app.collectors.autotrader import AutoTraderCollector

    slug = AutoTraderCollector.slug_from_variant(variant) or AutoTraderCollector.slug_from_variant(
        title
    )
    if not slug:
        return None
    # Refuse the old broken engine shape
    if re.search(r"/\d-\d|^\d-\d", slug):
        return None
    return f"https://www.autotrader.co.za/car-for-sale/toyota/fortuner/{slug}/{listing_id}"


def looks_like_invented_autotrader_url(url: str | None) -> bool:
    """True for previously rebuilt paths that AutoTrader rejects (dot→hyphen engines)."""
    if not url:
        return False
    path = urlparse(url).path.lower()
    # Our old slugify turned "2.4gd-6" into "2-4gd-6"
    if re.search(r"/fortuner/\d-\d", path):
        return True
    # Bare /car-for-sale/{id}
    if re.match(r"^/car-for-sale/\d{6,}/?$", path):
        return True
    return False


def improve_stored_autotrader_url(
    url: str | None,
    *,
    listing_id: str | None = None,
    title: str | None = None,
    variant: str | None = None,
) -> str | None:
    """Upgrade short AT SEO paths using stored variant text for outbound links."""
    from app.collectors.autotrader import AutoTraderCollector

    abs_url = absolute_url(url, source="autotrader")
    if abs_url and looks_like_invented_autotrader_url(abs_url):
        abs_url = None
    improved = AutoTraderCollector.improve_detail_url(
        abs_url,
        variant=variant,
        title=title,
        listing_id=listing_id,
    )
    if improved and is_valid_marketplace_url("autotrader", improved):
        return improved.split("?")[0]
    if abs_url and is_valid_marketplace_url("autotrader", abs_url):
        return abs_url.split("?")[0]
    rebuilt = rebuild_autotrader_url(listing_id, title=title, variant=variant)
    if rebuilt and is_valid_marketplace_url("autotrader", rebuilt):
        return rebuilt
    return None


def rebuild_webuycars_url(listing_id: str | None) -> str | None:
    if not listing_id:
        return None
    stock = str(listing_id).strip()
    if not stock:
        return None
    return f"https://www.webuycars.co.za/buy-a-car/{stock}"


def rebuild_cars_co_za_url(
    listing_id: str | None,
    *,
    title: str | None = None,
    year: int | None = None,
) -> str | None:
    if not listing_id or not str(listing_id).isdigit():
        return None
    slug = _slugify(title) or "toyota-fortuner"
    if year and not str(year) in slug:
        slug = f"{year}-toyota-fortuner-{slug}"
    elif "toyota" not in slug:
        slug = f"toyota-fortuner-{slug}"
    return f"https://www.cars.co.za/for-sale/used/{slug}/{listing_id}/"


def is_valid_marketplace_url(source: str | None, url: str | None) -> bool:
    if not source or not url:
        return False
    abs_url = absolute_url(url, source=source)
    if not abs_url:
        return False
    if source == "autotrader" and looks_like_invented_autotrader_url(abs_url):
        return False
    path = urlparse(abs_url).path
    if source == "autotrader":
        return bool(_AT_DETAIL_RE.match(path))
    if source == "cars_co_za":
        return bool(_CARS_USED_RE.search(path))
    if source == "webuycars":
        lower = path.lower()
        return "/buy-a-car/" in lower and not lower.rstrip("/").endswith("/buy-a-car")
    return abs_url.startswith("http")


def normalise_listing_url(
    source: str | None,
    url: str | None,
    *,
    listing_id: str | None = None,
    title: str | None = None,
    variant: str | None = None,
    year: int | None = None,
) -> str | None:
    """Absolutize and repair common broken marketplace URL shapes.

    AutoTrader: upgrade short SEO slugs using variant text; never emit ``2-4gd-6``.
    """
    abs_url = absolute_url(url, source=source)
    if source == "autotrader":
        return improve_stored_autotrader_url(
            abs_url,
            listing_id=listing_id,
            title=title,
            variant=variant,
        )
    if source == "cars_co_za":
        if abs_url and is_valid_marketplace_url("cars_co_za", abs_url):
            return abs_url.split("?")[0]
        return rebuild_cars_co_za_url(listing_id, title=title, year=year)
    if source == "webuycars":
        if abs_url and is_valid_marketplace_url("webuycars", abs_url):
            return abs_url.split("?")[0]
        return rebuild_webuycars_url(listing_id)
    return abs_url


def normalise_image_urls(urls: list[str] | None, *, source: str | None = None) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in urls or []:
        abs_url = absolute_url(raw, source=source)
        if not abs_url or abs_url in seen:
            continue
        # Skip tiny tracking pixels / placeholders when obvious
        lower = abs_url.lower()
        if any(x in lower for x in ("1x1", "pixel.gif", "spacer.", "placeholder")):
            continue
        seen.add(abs_url)
        out.append(abs_url)
    return out
