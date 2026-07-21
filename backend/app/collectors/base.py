"""Base collector adapter interface."""

from __future__ import annotations

import json
import logging
import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import Settings, get_settings
from app.schemas.listings import ListingPayload

logger = logging.getLogger(__name__)


class CollectorError(Exception):
    """Raised when a collector fails in a controlled way."""

    def __init__(self, message: str, *, parser_broken: bool = False):
        super().__init__(message)
        self.parser_broken = parser_broken


class BaseCollector(ABC):
    """Independent source adapter. Failures must not cascade."""

    source: str
    category: str = "marketplace"  # marketplace | toyota_dealer | independent

    def __init__(self, settings: Settings | None = None, client: httpx.Client | None = None):
        self.settings = settings or get_settings()
        self._client = client
        self._owns_client = client is None

    def __enter__(self) -> BaseCollector:
        if self._client is None:
            self._client = httpx.Client(
                timeout=self.settings.request_timeout_seconds,
                headers={"User-Agent": self.settings.user_agent},
                follow_redirects=True,
            )
        return self

    def __exit__(self, *args: Any) -> None:
        if self._owns_client and self._client is not None:
            self._client.close()
            self._client = None

    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            raise RuntimeError("Collector client not initialised; use as context manager")
        return self._client

    def throttle(self) -> None:
        time.sleep(self.settings.request_delay_seconds)

    def snapshot_raw(self, name: str, content: str | bytes | dict[str, Any]) -> str:
        root = Path(self.settings.raw_snapshot_dir) / self.source
        root.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = root / f"{ts}_{name}"
        if isinstance(content, dict):
            path = path.with_suffix(".json")
            path.write_text(json.dumps(content, indent=2, default=str), encoding="utf-8")
        elif isinstance(content, bytes):
            path = path.with_suffix(".bin")
            path.write_bytes(content)
        else:
            path = path.with_suffix(".html")
            path.write_text(content, encoding="utf-8")
        return str(path)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8))
    def fetch_text(self, url: str) -> str:
        self.throttle()
        response = self.client.get(url)
        response.raise_for_status()
        return response.text

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8))
    def fetch_json(self, url: str) -> Any:
        self.throttle()
        response = self.client.get(url)
        response.raise_for_status()
        return response.json()

    def fixture_path(self, name: str) -> Path:
        return Path(__file__).resolve().parents[2] / "tests" / "fixtures" / self.source / name

    def load_fixture_json(self, name: str) -> Any:
        path = self.fixture_path(name)
        return json.loads(path.read_text(encoding="utf-8"))

    def load_fixture_text(self, name: str) -> str:
        return self.fixture_path(name).read_text(encoding="utf-8")

    @abstractmethod
    def search(self) -> list[ListingPayload]:
        """Return summary listings matching Fortuner search criteria."""

    def fetch_detail(self, listing: ListingPayload) -> ListingPayload:
        """Optionally enrich a listing. Default: return as-is."""
        return listing

    def collect(self) -> list[ListingPayload]:
        mode = self.settings.collector_mode
        if mode == "fixture":
            listings = self.search_from_fixtures()
        elif mode == "hybrid":
            try:
                listings = self.search()
                if not listings:
                    logger.warning("%s returned empty live results; falling back to fixtures", self.source)
                    listings = self.search_from_fixtures()
            except Exception:
                logger.exception("%s live search failed; falling back to fixtures", self.source)
                listings = self.search_from_fixtures()
        else:
            listings = self.search()

        if not listings:
            raise CollectorError(
                f"{self.source}: empty result set — possible parser/structural change",
                parser_broken=True,
            )
        return listings

    def search_from_fixtures(self) -> list[ListingPayload]:
        """Default fixture loader expecting search.json."""
        data = self.load_fixture_json("search.json")
        return [ListingPayload.model_validate(item) for item in data]
