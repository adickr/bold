"""Collector registry."""

from __future__ import annotations

from app.collectors.autotrader import AutoTraderCollector
from app.collectors.base import BaseCollector
from app.collectors.cars_co_za import CarsCoZaCollector
from app.collectors.webuycars import WeBuyCarsCollector

COLLECTORS: dict[str, type[BaseCollector]] = {
    AutoTraderCollector.source: AutoTraderCollector,
    CarsCoZaCollector.source: CarsCoZaCollector,
    WeBuyCarsCollector.source: WeBuyCarsCollector,
}


def get_collector(source: str, **kwargs) -> BaseCollector:
    cls = COLLECTORS.get(source)
    if not cls:
        raise KeyError(f"Unknown collector source: {source}")
    return cls(**kwargs)


def all_collectors(**kwargs) -> list[BaseCollector]:
    return [cls(**kwargs) for cls in COLLECTORS.values()]
