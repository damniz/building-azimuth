# building-azimuth

Find the compass direction a building's roof *faces* from its address — the
azimuth that matters for solar panel siting — as a Streamlit web app and as a
JSON/PNG API, both served from the same app.

Azimuth convention: **0° = North, 90° = East, 180° = South, 270° = West**. The
app first estimates the roof's *ridge* line (see "How it works" below), then
reports the azimuth of the roof *slope* perpendicular to that ridge — a solar
panel mounted on the roof faces along the slope, not along the ridge itself. A
ridge has two slopes facing opposite directions; the app always reports
whichever of the two is closer to due south (180°), the more solar-relevant
choice in the Northern Hemisphere.

## How it works

1. **Geocode** the address to coordinates (OpenStreetMap).
2. **Look up the building footprint** near that point (OpenStreetMap).
3. **Estimate the roof ridge** from the footprint's longest edge, refined by
   image-based ridge detection when a satellite image is fetched (the picture
   and the image API endpoint; the plain JSON endpoint skips this for speed).
4. **Convert the ridge to the reported azimuth**: rotate 90° to the roof-slope
   direction, then pick whichever of the two resulting compass directions is
   closer to south. The picture always shows the ridge itself, not this
   rotated value.

You can also tell the app what you already know about the roof instead of relying
on the estimate — see **Roof ridge options** below.

### Known limitations

- The footprint-edge heuristic is an approximation. It's wrong whenever a roof's
  ridge doesn't run along the building's longest wall (common for e.g. some
  terraced/row houses) — use the manual override in that case.
- Ridge-line detection is best-effort and requires the two roof slopes to
  actually look different in the image (a lighting or material difference
  across the ridge) — a uniformly colored/lit roof, or low-resolution imagery,
  can still mean nothing is found. It falls back to the footprint heuristic
  rather than guess.
- Building-footprint coverage and accuracy varies by region, though it's
  generally good across Europe and particularly strong in Belgium.
- Underlying map-data services are occasionally slow or unavailable; the app
  automatically retries against alternative sources where possible, but if all
  are down you'll see a clear error rather than a result.
- Picking "whichever slope faces closer to south" is a simplification: the
  footprint and ridge line don't reveal which of the two slopes actually has
  usable roof area or panels, only which one is more solar-relevant if both
  are viable.

### Roadmap

- Report multiple azimuths with roof-area fractions for complex buildings,
  not just the single dominant one (the API's `azimuths` field already has
  the groundwork for this).
- Improve ridge detection and orientation estimation — still best-effort and
  often finds nothing on typical small roofs.

## Setup

Requires [uv](https://docs.astral.sh/uv/) and Python 3.11+.

```bash
uv sync
```

This creates `.venv/` and installs everything from `pyproject.toml` / `uv.lock`.

## Running the app

```bash
uv run streamlit run asgi_app.py
```

This serves **both** the web UI and the API from the same port (default
`http://localhost:8501`) — `asgi_app.py` wraps the Streamlit script
(`streamlit_app.py`) in an ASGI app with the API routes mounted alongside it.

## Web UI

Open the app, enter an address, and optionally set **Roof ridge**. These options
describe the *ridge*, not the reported azimuth directly — the displayed number
is always the perpendicular, south-preferring conversion described above:

| Option | Effect on the estimated ridge |
|---|---|
| **Ridge detection** (default) | Footprint-edge ridge, refined by image-based ridge detection when confident. |
| **Ridge along the long side** | Forces the footprint-heuristic ridge as-is (no rotation). |
| **Ridge along the short side** | Forces the footprint-heuristic ridge rotated 90°. |
| **Flat roof** | Footprint-heuristic ridge, but skips ridge detection entirely (there's nothing to detect). |

A manual choice always wins over automatic detection — it reflects what you
actually know about the building.

## API user guide

Two read-only `GET` endpoints, served on the same host/port as the web UI.

### `GET /api/azimuth` — JSON

Returns the numeric azimuth. Fast: it never fetches or analyzes imagery, so it
always reflects the footprint heuristic (or your manual override) — **it does
not run ridge detection**, unlike the image endpoint and the web UI.

**Query parameters**

| Name | Required | Values | Description |
|---|---|---|---|
| `address` | yes | any string | Address to look up. |
| `roof_orientation` | no | `along`, `across`, `flat` | Manual override; omit for the footprint-heuristic default. |

**Example**

```bash
curl -G "http://localhost:8501/api/azimuth" \
  --data-urlencode "address=Grote Markt 1, Antwerp, Belgium"
```

```json
{
  "address": "Grote Markt 1, Antwerp, Belgium",
  "resolved_address": "Stadhuis Antwerpen, 1, Grote Markt, ..., Antwerpen, ..., 2000, België / Belgique / Belgien",
  "lat": 51.2213112,
  "lon": 4.3991737,
  "azimuth_deg": 110.37,
  "roof_orientation_hint": null,
  "azimuths": [
    {"azimuth_deg": 110.35, "length_fraction": 0.697},
    {"azimuth_deg": 201.21, "length_fraction": 0.303}
  ]
}
```

Field notes:
- `azimuth_deg` — the roof-slope-facing azimuth, full compass range `[0, 360)`:
  the estimated ridge (see "How it works") rotated 90° to the slope direction,
  resolved to whichever of the two possible directions is closer to south.
- `roof_orientation_hint` — `"along"`, `"across"`, or `"flat"` if `roof_orientation`
  was supplied and applied; otherwise `null`. Describes what was done to the
  *ridge* estimate, not the reported azimuth directly (see "Web UI" above).
- `azimuths` — every footprint edge direction grouped by similar bearing, each
  already converted the same way (rotated 90°, resolved toward south), with its
  share of the total perimeter (`length_fraction`). Useful for complex
  footprints with more than one significant wall direction; `azimuth_deg` above
  is just the top entry (or the manual override, if given).

With an override:

```bash
curl -G "http://localhost:8501/api/azimuth" \
  --data-urlencode "address=8 avenue du Monde, 1400 Nivelles" \
  --data-urlencode "roof_orientation=across"
```

### `GET /api/azimuth/image` — PNG

Returns a satellite image (`image/png`) with the footprint outline and the
estimated *ridge* line drawn on it (not the reported azimuth directly — see
"How it works" above for why those differ by 90°). This endpoint attempts
ridge detection when no `roof_orientation` override is given, so its result
can occasionally differ slightly from the plain JSON endpoint's for the same
address.

Same query parameters as above (`address` required, `roof_orientation` optional).

```bash
curl -G "http://localhost:8501/api/azimuth/image" \
  --data-urlencode "address=Grote Markt 1, Antwerp, Belgium" \
  -o azimuth.png
```

### Errors

Errors are JSON: `{"error": "..."}`, with a status code indicating the cause:

| Status | Cause |
|---|---|
| 400 | Missing/empty `address`, or the address couldn't be geocoded. |
| 404 | No building footprint found near the geocoded location. |
| 502 | The satellite imagery service couldn't be reached (image endpoint only). |

### Rate limits and etiquette

Results are cached server-side for 24 hours per address, so repeat lookups of
the same address are effectively free. This app is sized for personal/
low-volume use, not bulk querying.

## Attribution

- Building data and geocoding: © OpenStreetMap contributors (Nominatim, Overpass).
- Satellite imagery: Esri, Maxar, Earthstar Geographics, and the GIS User Community
  (elsewhere), or NGI (Nationaal Geografisch Instituut / Institut Géographique
  National) for Belgian addresses.
