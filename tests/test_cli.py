import json
import shutil
from pathlib import Path

import networkx as nx
import pytest

from marica_transit.cli import main

FIXTURE = Path(__file__).parent / "fixtures" / "tiny_stops.csv"


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    target = tmp_path / "data" / "processed" / "moovit_stops_geocoded.csv"
    target.parent.mkdir(parents=True)
    shutil.copy(FIXTURE, target)
    return tmp_path / "data"


def test_build_graph_writes_graphml(data_dir: Path, tmp_path: Path) -> None:
    out = tmp_path / "g.graphml"
    main(["build-graph", "--data-dir", str(data_dir), "--graphml", str(out)])
    assert nx.read_graphml(out).number_of_nodes() == 7


def test_analyze_json(data_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "stats.json"
    main(["analyze", "--data-dir", str(data_dir), "--region", "marica", "--json", str(out)])
    report = json.loads(out.read_text())
    assert report["networks"][0]["stops"] == 7
    assert report["geocode_quality"]["out_of_area"] == 1
    assert "strongly connected components" in capsys.readouterr().out


def test_route_by_name_writes_map(
    data_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "route.html"
    main(
        [
            "route",
            "--data-dir",
            str(data_dir),
            "--from",
            "Beta",
            "--to",
            "Alpha",
            "--out",
            str(out),
        ]
    )
    text = capsys.readouterr().out
    assert "over 4 stops" in text
    assert "(1 transfer(s))" in text
    assert "Shortest route" in out.read_text()


def test_route_unknown_stop_exits(data_dir: Path) -> None:
    with pytest.raises(SystemExit, match="not in the selected network"):
        main(["route", "--data-dir", str(data_dir), "--from", "Nowhere", "--to", "Alpha"])


def test_map_and_coverage_outputs(data_dir: Path, tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    png = docs / "preview.png"
    main(["map", "--data-dir", str(data_dir), "--out-dir", str(docs), "--png", str(png)])
    assert (docs / "index.html").stat().st_size > 0
    assert (docs / "itaipuacu.html").stat().st_size > 0
    assert png.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    cov = tmp_path / "cov.html"
    main(["coverage", "--data-dir", str(data_dir), "--street", "Alpha St", "--out", str(cov)])
    assert "Stop spacing" in cov.read_text()
