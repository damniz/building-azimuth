# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

Setup and running:

```bash
uv sync                                  # install/update the environment from pyproject.toml / uv.lock
uv run streamlit run asgi_app.py         # serves BOTH the web UI and the API on the same port (default 8501)
```

Always run `asgi_app.py`, not `streamlit_app.py` directly — running the plain script serves the UI but silently
drops the `/api/azimuth*` routes (they're only mounted by the `st.App` wrapper in `asgi_app.py`). When testing
manually, bind to localhost explicitly (`--server.address localhost`) rather than the default, which listens on
all interfaces.

There is no automated test suite yet. Verification during development has been ad hoc:

- **Isolated component checks**, bypassing Nominatim/Overpass (both are external, and the public Overpass
  instance is frequently slow, rate-limited, or down — this is expected/environmental, not a bug to "fix" with
  retries): call `azimuth.imagery` / `azimuth.wms` / `azimuth.orientation` / `azimuth.ridge` functions directly
  against known lat/lon coordinates or synthetic footprint rings in a one-off script.
- **End-to-end**: run the server, then hit the API directly, e.g.
  `curl -G "http://localhost:8501/api/azimuth" --data-urlencode "address=..."` and
  `curl -G "http://localhost:8501/api/azimuth/image" --data-urlencode "address=..." -o out.png`.
- `azimuth.footprint.find_building`/`fetch_candidate_buildings` already fall back through
  `DEFAULT_OVERPASS_URLS` (main instance → private.coffee → maps.mail.ru, one attempt each) since the main
  instance being down is routine, not exceptional. For one-off testing against a single specific instance,
  pass `overpass_urls=[...]` with just that URL.
- After editing any module imported by `azimuth/api.py` or `asgi_app.py` (not the Streamlit script itself), the
  running server process must be **restarted** to pick up the change — Streamlit's autoreload re-executes the
  top-level script on each rerun, but custom ASGI routes are bound once at process start and don't get that
  treatment.

## Architecture

```
streamlit_app.py     Streamlit UI (thin script, calls azimuth.pipeline)
asgi_app.py           ASGI entry point: mounts the UI + API routes (st.App)
azimuth/
  geocode.py          Address -> coordinates (Nominatim)
  footprint.py        Coordinates -> building footprint (Overpass/OSM)
  geometry.py          Shared planar-geometry primitives
  orientation.py       Footprint -> azimuth (edge bearings, grouping, manual override)
  ridge.py              Image-based ridge-line detection (best-effort refinement)
  imagery.py             Satellite tile fetch + stitch (Esri World Imagery)
  wms.py                  Higher-res Belgian orthophoto (NGI WMS), preferred in-region
  render.py               Draws the footprint outline + ridge line
  pipeline.py              Orchestrates the above, with caching
  api.py                    Starlette routes for the JSON/PNG API
```

**Two surfaces, one pipeline.** `streamlit_app.py` (the UI) and `azimuth/api.py` (JSON/PNG routes) are both thin
callers of `azimuth/pipeline.py`, mounted together by `asgi_app.py` via `st.App(routes=..., exception_handlers=...)`.
Neither surface has its own logic beyond input handling and formatting.

**The pipeline has two cached entry points with intentionally different cost/accuracy tradeoffs**, sharing one
cached `_locate_building()` step (geocode → footprint → footprint-heuristic azimuth):
- `compute_orientation()` — footprint-only, never fetches imagery. Backs the fast JSON endpoint.
- `compute_building_azimuth()` — also fetches a satellite image and runs ridge detection. Backs the image
  endpoint and the UI.

This means the JSON endpoint's `azimuth_deg` can legitimately differ slightly from what the image endpoint draws
for the same address — that's by design (see README's API section), not a bug to reconcile.

**Azimuth resolution order** (each stage only runs if the previous one didn't produce a value):
1. Explicit user override (`roof_orientation=along|across|flat`) — always wins if given.
2. Image-based ridge-line detection (`ridge.py`), only attempted in `compute_building_azimuth()` and only when
   no override was given.
3. Footprint-edge heuristic (`orientation.py`) — the longest edge of the OSM building polygon, as a proxy for
   the roof ridge. This is the default and the ultimate fallback.

OSM's own `roof:orientation`/`roof:shape` tags are deliberately **not** auto-applied to correct step 3 — that was
tried and found unreliable (real roofs don't consistently follow the tag's along/across convention). Only an
explicit user-supplied value changes the result from the plain longest-edge default.

**Azimuths are lines, not vectors.** A footprint edge or roof ridge has no inherent forward/backward direction,
so bearings are normalized to `[0, 180)` throughout `orientation.py`, `ridge.py`, and `render.py`. Bearing math
consistently uses `atan2(dx, -dy)` where dx=east, dy=south (pixel-space convention: y increases southward,
matching Web Mercator) — any new geometry code should follow the same convention rather than reintroducing a
`atan2(dy, dx)`-style formula that would rotate everything 90°.

**Imagery is a source waterfall, not a single provider**: `pipeline.py` tries `azimuth/wms.py` (Belgium's
national NGI orthophoto WMS — much higher resolution than Esri there) first for in-Belgium coordinates, falling
back to `azimuth/imagery.py` (Esri World Imagery XYZ tiles) elsewhere or on any failure. Both sources implement
the same defense: a pixel-variance check that rejects placeholder/blank images. This exists because **both**
providers were found, during development, to return HTTP 200 with a non-error blank/"no data" image rather than
an error status — trusting the HTTP status code alone silently produces garbage input to ridge detection. Any
future imagery source must implement the same check rather than relying on status codes.

**`ridge.py`'s detection is deliberately conservative.** A candidate line must have both endpoints inside the
footprint (not just its midpoint), pass near the centroid, not run parallel to the footprint's own boundary
within a proximity tolerance, and either be corroborated by other similar-bearing segments or span almost the
entire footprint alone. Returning `None` (no detection) is the common, correct outcome for small/typical roofs —
not a failure to fix by loosening thresholds, since a naive first version was shown (via manual testing) to
confidently pick the wrong line on small buildings.

**Local geometry uses a flat-earth approximation** (`geometry.py`'s `project_to_local_meters`), valid only at
building scale (tens of meters) around a per-building origin — it is not a general-purpose projection and
shouldn't be reused for anything spanning a larger area.

## Workflow

Before committing new changes, always propose a corresponding update to `README.md` — or explicitly explain why
the change doesn't warrant one (e.g. a pure refactor, an internal bugfix with no user-visible or API-visible
effect). Don't skip this step silently.

At the start of a new session in this repo, check the README's "Roadmap" section for outstanding TODOs and
proactively propose tackling one, rather than waiting to be asked.

Always use the `developing-with-streamlit` skill for any work touching `streamlit_app.py` or Streamlit usage
elsewhere in this repo (new widgets, styling, layout, performance, or `st.App`/ASGI changes) — load it before
editing, not just when something looks visually off.
