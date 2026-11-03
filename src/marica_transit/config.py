"""Project paths and source URLs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# Moovit index of the EPT (Empresa Pública de Transportes de Maricá) bus lines.
EPT_LINES_URL = (
    "https://moovitapp.com/index/pt-br/transporte_p%C3%BAblico-lines-Rio_de_Janeiro-322-1036555"
)

# Origin/destination of the course project's route case study: the eastern and western
# ends of Jardim Atlântico, Itaipuaçu. The nearest graph stop to each point is used.
DEFAULT_ORIGIN = (-22.9672379, -42.9099213)
DEFAULT_DESTINATION = (-22.9675662, -42.9707889)


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
    def explorer_data(self) -> Path:
        """Data bundle read by the interactive explorer in docs/."""
        return self.docs_dir / "data" / "network.json"

    @property
    def folium_dir(self) -> Path:
        """Standalone Folium maps (not published; the explorer replaces them)."""
        return self.output_dir / "maps"

    @property
    def stats_json(self) -> Path:
        return self.output_dir / "network_stats.json"
