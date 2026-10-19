# London browser demo

Live URL after Pages deployment: https://robertpitt.github.io/roads-db/

The demo is a static app in `demo/`. A dedicated Web Worker reads the same RDB
format-1 arrays as Python, runs A*, and uses SQLite WebAssembly for road names.
Routing requests stay on the device. There is no hosted routing backend or API
key. A browser JavaScript API is available in the demo's console:

```javascript
const result = await roadsDB.route({
  from: [51.5010, -0.1416], // latitude, longitude
  to: [51.5055, -0.0754]
});
console.log(result.cost_m, result.steps, result.coordinates);
```

To integrate it in another page on the same origin, serve the `demo/` assets and
import `createRoadsClient` from `api.js`. Await `client.ready`, call
`client.route({from, to})`, and call `client.close()` when finished. The ready
promise returns dataset metadata. `onProgress` can display loading status.
This is an in-browser API, not an HTTP endpoint for curl or other servers.
For a local HTTP endpoint, use the existing Python `roads-rdb-map` server.

The response includes distance in metres, full road geometry as `[lat, lon]`
pairs, approximate instructions, snap distances, query timing, explored-node
count, and a dataset ID. Timing includes local geometry/metadata enrichment;
`routing_ms` reports graph query time alone. It excludes initial downloads.

## Data footprint and coverage

- BNG bbox: `520000,174000,540000,188000` (20 × 14 km, central London).
- 43,241 nodes, 112,478 directed edges, 56,392 road geometries.
- Routing binary: 7,212,896 bytes; SQLite names: 4,321,280 bytes.
- Combined data: 11.53 MB before HTTP compression; vendored browser dependencies
  add about 1 MB. Individual data files are capped at 40 MiB by build checks.
- Source: the official OS Open Roads GeoPackage downloaded 2026-10-19, verified
  against archive MD5 `2ee5d30899c4a44df321e5e1a66989ac`.

`demo/data/manifest.json` records bounds, hashes, sizes, attribution, and dataset
identity. Only the London RDB and SQLite are exceptions to the repository's
database ignore rules. The national GeoPackage/archive remain ignored locally.
The Python wheel/source package does not bundle the browser dataset or vendors.

Road names are joined using actual source IDs and stored with dense geometry
IDs. This avoids assuming `source_fid == geom_id + 1` for a bounded build.
The boundary can cut off routes that would travel outside this area. Routes
are experimental distance paths: one-way, legal access, turn restrictions,
traffic, and travel-time profiles are not modelled. Instructions use approximate
bearings and source road names. Dense IDs and sidecars must come from one build.

## Local preview and checks

Requires Python 3.11+ and Node.js 24+; no npm install is needed:

```bash
node --test tests/browser.test.cjs
python scripts/prepare_pages.py
python -m http.server 8766 --bind 127.0.0.1 --directory build/pages
```

Open `http://127.0.0.1:8766/`. Use HTTPS or localhost for workers, Cache Storage,
and service workers; opening `index.html` as a file is unsupported. The tests
compare 20 real London routes with Python using the serialized RDB, and cover
partial snaps, same-road routes, disconnected components, reader validation,
workspace reuse, hashes, and explicit SQLite source-ID mappings.

Once the app says it is saved for offline use, the shell and dataset are cached.
Reloads and new queries can run offline while browser storage is retained.
The initial visit needs a download, and clearing site storage removes it. The
default basemap is the local road network. Optional OpenStreetMap labels need
internet; tiles are not bulk downloaded or included in the offline cache.

`prepare_pages.py` validates hashes, stages `build/pages`, and stamps a cache
version derived from all site files. The service worker checks the manifest
online and falls back to its cached copy offline. A newly deployed app shell
may require a second reload after its service worker installs. Old immutable
dataset caches can be removed through the browser's site-storage controls.

## Rebuild the extract

Download and unzip the official GeoPackage via the
[OS Downloads API](https://docs.os.uk/os-apis/accessing-os-apis/os-downloads-api/getting-started/automating-os-opendata-downloads)
or OS Data Hub. Check its supplied licence and archive checksum. Then:

```bash
python scripts/build_pages_demo.py \
  --input oproad_gpkg_gb/Data/oproad_gb.gpkg \
  --bbox 520000,174000,540000,188000 \
  --source-date 2026-10-19 \
  --source-md5 2ee5d30899c4a44df321e5e1a66989ac
node --test tests/browser.test.cjs
python scripts/prepare_pages.py
```

Use the actual download date/checksum and update the attribution for a new data
edition. The exporter emits both files, manifest, attribution, and parity cases
together. Commit the entire `demo/data/` set after reviewing its size and licence.
Python costs are generated from the written, quantized RDB, not the unquantized
builder graph. Tests use eight snap candidates so junction ties include their
connected roads rather than depending on language-specific set iteration order.

## GitHub Pages deployment

Set the repository's Pages build source to **GitHub Actions**. On a push to
`main`, `.github/workflows/pages.yml` checks the engine and committed dataset,
stages the static site, uploads it, and deploys through the `github-pages`
environment. Pull requests run checks without publishing. CI does not download
the national dataset. See [GitHub's custom-workflow documentation](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages).

Third-party code is pinned and vendored, with licences in `demo/vendor/`:
Leaflet 1.9.4 (BSD-2-Clause), sql.js 1.15.1 (MIT), and Proj4js 2.22.0 (MIT).
Data is covered by the separate OGL notice in `demo/data/NOTICE.txt`.
