"""Helpers for listing media and outbound links."""

from __future__ import annotations

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
