# Case study: shortest route across Jardim Atlântico (Itaipuaçu)

Shortest bus route between the eastern and western ends of Jardim Atlântico, computed on the
Itaipuaçu subgraph with Dijkstra and with A\* (straight-line distance as an admissible
heuristic). Originally `script/tests/otimizacao/main.py` in the course project.

```bash
uv run marica-transit route --out docs/route-optimization.html
# or pick your own endpoints
uv run marica-transit route --from-coord=-22.9672,-42.9099 --to-coord=-22.9676,-42.9708
uv run marica-transit route --region marica --from "Terminal De Itaipuaçu" --to "Rodoviária Do Povo De Maricá"
```

Map: [`docs/route-optimization.html`](../../docs/route-optimization.html)

## Method

- The network is the **induced** subgraph of the stops inside the Itaipuaçu bounding box: a
  link is kept only if both of its stops are inside. Links follow line direction.
- Edge weight is the great-circle (haversine) distance between consecutive stops.
- A coordinate can match several stops (geocoders often return one street-level point for
  different house numbers), so every stop at the nearest coordinate is a candidate and the
  shortest route over all candidate pairs wins.
- The ride plan uses the fewest line changes possible along the chosen path (greedy
  intersection of the lines serving each leg, which is optimal for a fixed path).

## Result (default endpoints)

| | |
|---|---|
| Origin | Rua Cento E Vinte E Dois, 12 (−22.9672, −42.9099) |
| Destination | Rua Trinta E Seis, 84 (one of 3 stops at −22.9676, −42.9708) |
| Dijkstra | 6.62 km, 6 stops |
| A\* | 6.62 km, 6 stops (same path) |
| Ride plan | E20 for 2 stops, then E32 for 3 stops: 1 transfer |

Distances are straight lines between geocoded stops, not road distances, and inherit the
geocoding error described in the main README.

## What changed from the original script

- **Fake links removed.** The original rebuilt links from the rows that survived the
  bounding-box filter, so a line that left the area and came back was joined straight across
  the gap: 37 of its 267 links (14%) were not real consecutive stops. The subgraph is now
  induced from the full graph.
- **Nodes keyed by stop name, not coordinate.** Keying by coordinate merged the 33
  "Rodovia Amaral Peixoto, &lt;n&gt;" stops that share one street-level geocode into a single
  artificial hub, which the original reported as the most central stop (degree 40).
- Distances use haversine instead of geopy's geodesic (difference < 0.5%), so the route
  measures 6.62 km instead of the 6.63 km reported before.
