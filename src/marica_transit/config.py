"""Project paths and source URLs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# Moovit index of the EPT (Empresa Pública de Transportes de Maricá) bus lines.
EPT_LINES_URL = (
    "https://moovitapp.com/index/pt-br/transporte_p%C3%BAblico-lines-Rio_de_Janeiro-322-1036555"
)


@dataclass(frozen=True, slots=True)
class Paths:
    """Locations of the committed data snapshot and generated artefacts."""

    data_dir: Path = Path("data")
    docs_dir: Path = Path("docs")
    output_dir: Path = Path("output")

    @property
    def raw_stops(self) -> Path:
        """Stop sequences as scraped from Moovit (no coordinates)."""
        return self.data_dir / "raw" / "moovit_stops_raw.csv"

    @property
    def geocoded_stops(self) -> Path:
        """Stop sequences enriched with latitude/longitude."""
        return self.data_dir / "processed" / "moovit_stops_geocoded.csv"

    @property
    def graphml(self) -> Path:
        return self.output_dir / "marica_network.graphml"

    @property
    def stats_json(self) -> Path:
        return self.output_dir / "network_stats.json"
