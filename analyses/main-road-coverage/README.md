# Case study: stop spacing along Avenida Carlos Marighella (Itaipuaçu)

Are the bus stops along Itaipuaçu's main road close enough together? Stops are matched by
street name, chained in order along the road, and every gap above 450 m gets a suggested
new stop at its midpoint. Originally `script/tests/principal-carlos-marighella/main.py`.

```bash
uv run marica-transit coverage --out docs/main-road-coverage.html
# another road, another threshold
uv run marica-transit coverage --region marica --street "Rodovia Amaral Peixoto" --max-gap-m 600
```

Map: [`docs/main-road-coverage.html`](../../docs/main-road-coverage.html)

## Method

- A stop matches if its Moovit name **or** geocoded address contains "Carlos Marighella",
  "Carlos Mariguella" (spelling used in the data) or "Vitória Régia" (the road's former
  name). Matching ignores accents and case.
- Stops sharing a coordinate are merged into one location.
- Ordering: start at the end of the road's principal axis, then repeatedly hop to the nearest
  unvisited location by great-circle distance.

## Result

| | |
|---|---|
| Stop locations on the road | 23 (1 with an approximate geocode) |
| Chained length | 8.96 km |
| Median spacing | 254 m |
| Spacing classes (22 segments) | 15 ≤ 400 m · 0 at 400–600 m · 2 at 600–800 m · 5 at 800–1200 m · 0 > 1200 m |
| Gaps > 450 m (suggested stops) | 7, the largest 1,009 m |

## What changed from the original script

- The chain used to start at an arbitrary stop and measure nearest neighbours in raw degrees
  (which overstates east-west distances by ~9% at this latitude), so it could start in the
  middle of the road and jump back. It now starts at one end and uses metres.
- Matching now also checks the Moovit stop name, not only the geocoded address.
