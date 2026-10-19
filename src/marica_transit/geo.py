"""Geographic helpers: great-circle distance and bounding boxes."""

from __future__ import annotations

import math
from dataclasses import dataclass

EARTH_RADIUS_KM = 6371.0088  # IUGG mean Earth radius


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in kilometres between two (lat, lon) points in degrees.

    Haversine differs from the ellipsoidal (Vincenty/Karney) distance by well under 0.5%,
    which is far below the positional error of geocoded bus stops.
    """
    if any(math.isnan(v) for v in (lat1, lon1, lat2, lon2)):
        raise ValueError("coordinates must not be NaN")
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = phi2 - phi1
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlmb / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(a)))


@dataclass(frozen=True, slots=True)
class BoundingBox:
    """Axis-aligned latitude/longitude box (inclusive bounds)."""

    name: str
    lat_min: float
    lat_max: float
    lon_min: float
    lon_max: float
    label: str = ""

    @property
    def display_name(self) -> str:
        return self.label or self.name

    def __post_init__(self) -> None:
        if self.lat_min > self.lat_max or self.lon_min > self.lon_max:
            raise ValueError(f"invalid bounding box {self}")

    def contains(self, lat: float, lon: float) -> bool:
        return self.lat_min <= lat <= self.lat_max and self.lon_min <= lon <= self.lon_max


# Generous envelope around the municipality of Maricá; geocodes outside it are errors
# (e.g. "Entrada De Bambuí", in Maricá's Bambuí neighbourhood, was resolved to the town of
# Bambuí in Minas Gerais, ~460 km away).
MARICA = BoundingBox(
    "marica", lat_min=-23.02, lat_max=-22.78, lon_min=-43.06, lon_max=-42.60, label="Maricá"
)

# Itaipuaçu district study area used in the original course project
# (Recanto de Itaipuaçu to Restinga de Maricá, up to São José do Imbassaí and Inoã).
ITAIPUACU = BoundingBox(
    "itaipuacu",
    lat_min=-22.990,
    lat_max=-22.900,
    lon_min=-43.030,
    lon_max=-42.870,
    label="Itaipuaçu",
)

REGIONS: dict[str, BoundingBox] = {box.name: box for box in (MARICA, ITAIPUACU)}
