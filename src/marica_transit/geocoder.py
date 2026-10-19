"""Stop-name geocoding with Nominatim (OpenStreetMap) and/or the Google Geocoding API.

Network access is **opt-in** (``--force-regeocode``). Google needs ``GOOGLE_MAPS_API_KEY``
in the environment (see ``.env.example``). Requires the ``fetch`` extra.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Literal

import pandas as pd

from marica_transit.scraper import require_fetch_extra

log = logging.getLogger(__name__)

Service = Literal["nominatim", "google", "both"]

CITY, STATE, COUNTRY, COUNTRY_CODE = "Maricá", "Rio de Janeiro", "Brasil", "BR"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
GOOGLE_URL = "https://maps.googleapis.com/maps/api/geocode/json"


@dataclass(frozen=True, slots=True)
class GeocodeResult:
    lat: float | None
    lon: float | None
    address: str | None
    source: str


def clean_stop_name(name: str) -> str:
    """Normalise a Moovit stop name into a geocodable query.

    The original project defined this cleaning step but never called it.
    """
    query = name.replace("Lot ", "Loteamento ")
    if "Ponto Final - " in query and len(query.split(" - ")[1]) > 5:
        query = query.split(" - ")[1]
    return query.strip()


class Geocoder:
    def __init__(self, contact: str | None = None, timeout_s: float = 10) -> None:
        self._requests, _ = require_fetch_extra()
        self.timeout_s = timeout_s
        self.google_key = os.environ.get("GOOGLE_MAPS_API_KEY")
        agent = "marica-transit/1.0" + (f" ({contact})" if contact else "")
        self.headers = {"User-Agent": agent}
        self._last_nominatim = 0.0

    def nominatim(self, query: str) -> GeocodeResult:
        # Nominatim usage policy: at most one request per second.
        wait = 1.1 - (time.monotonic() - self._last_nominatim)
        if wait > 0:
            time.sleep(wait)
        self._last_nominatim = time.monotonic()
        params = {
            "q": f"{query}, {CITY}, {STATE}, {COUNTRY}",
            "format": "jsonv2",
            "addressdetails": 1,
            "limit": 1,
        }
        try:
            resp = self._requests.get(
                NOMINATIM_URL, params=params, headers=self.headers, timeout=self.timeout_s
            )
            resp.raise_for_status()
            hits = resp.json()
        except (self._requests.RequestException, ValueError) as exc:
            return GeocodeResult(None, None, f"Nominatim error: {exc}", "nominatim_failed")
        if not hits:
            return GeocodeResult(None, None, None, "nominatim_failed")
        hit = hits[0]
        addr = hit.get("address", {})
        city = addr.get("city") or addr.get("town") or addr.get("village") or ""
        if CITY.lower() not in city.lower():
            return GeocodeResult(
                None, None, f"outside {CITY}: {hit.get('display_name')}", "nominatim_failed"
            )
        return GeocodeResult(
            float(hit["lat"]), float(hit["lon"]), hit.get("display_name"), "nominatim"
        )

    def google(self, query: str) -> GeocodeResult:
        if not self.google_key:
            return GeocodeResult(None, None, "GOOGLE_MAPS_API_KEY not set", "google_skipped")
        params = {
            "key": self.google_key,
            "address": query,
            "components": f"country:{COUNTRY_CODE}|administrative_area:{STATE}|locality:{CITY}",
        }
        try:
            resp = self._requests.get(GOOGLE_URL, params=params, timeout=self.timeout_s)
            resp.raise_for_status()
            data = resp.json()
        except (self._requests.RequestException, ValueError) as exc:
            return GeocodeResult(None, None, f"Google error: {exc}", "google_failed")
        if data.get("status") != "OK" or not data.get("results"):
            msg = data.get("error_message", data.get("status"))
            return GeocodeResult(None, None, f"Google API: {msg}", "google_failed")
        first = data["results"][0]
        loc = first["geometry"]["location"]
        return GeocodeResult(loc["lat"], loc["lng"], first.get("formatted_address"), "google")

    def geocode(self, name: str, service: Service = "both") -> GeocodeResult:
        query = clean_stop_name(name)
        if service == "google":
            return self.google(query)
        result = self.nominatim(query)
        if service == "nominatim" or result.lat is not None:
            return result
        return self.google(query)

    def geocode_stops(self, stops: pd.DataFrame, service: Service = "both") -> pd.DataFrame:
        """Geocode each *unique* stop name once and map results back onto every row."""
        names = stops["stop_name"].astype(str).unique()
        log.info("geocoding %d unique stop names (%d rows) via %s", len(names), len(stops), service)
        results: dict[str, GeocodeResult] = {}
        for i, name in enumerate(names, 1):
            results[name] = self.geocode(name, service)
            log.debug("[%d/%d] %s -> %s", i, len(names), name, results[name].source)
        out = stops.copy()
        out["lat"] = out["stop_name"].map(lambda n: results[n].lat)
        out["lon"] = out["stop_name"].map(lambda n: results[n].lon)
        out["geocoded_address"] = out["stop_name"].map(lambda n: results[n].address)
        out["geocoding_source"] = out["stop_name"].map(lambda n: results[n].source)
        failed = sum(1 for r in results.values() if r.lat is None)
        log.info("geocoding done: %d/%d unique names failed", failed, len(names))
        return out
