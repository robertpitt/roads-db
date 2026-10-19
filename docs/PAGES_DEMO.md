# Browser demo: London and Great Britain

[Open the live demo](https://robertpitt.github.io/roads-db/). Choose **Central
London** or **Great Britain** in the dataset selector. London starts automatically;
the first national download requires clicking its download button. Both datasets
run locally in a dedicated Web Worker, using the Python-compatible RDB format-1
arrays for A* and SQLite WebAssembly for road names. There is no routing backend
or API key. The default basemap is drawn from the selected dataset. Optional
OpenStreetMap labels need an internet connection and are not cached offline.

**Great Britain is not the whole UK.** OS Open Roads covers England, Scotland
and Wales; it excludes Northern Ireland. All routes are experimental distance
paths with bidirectional links. One-way streets, access rules, turn restrictions,
traffic and travel-time profiles are not modelled. Instructions are approximate.

## Download sizes

| Dataset | Roads | Network download | Expanded RDB + SQLite |
| --- | ---: | ---: | ---: |
| Central London | 56,392 | 11.53 MB | 11.53 MB |
| Great Britain | 3,961,077 | 399.09 MB | 632.90 MB |

Sizes use decimal MB and exclude about 1 MB of browser libraries. Great Britain
has 3,346,499 nodes and 7,897,102 directed edges. Its RDB is 569,517,968 bytes;
its dictionary-compressed SQLite is 63,377,408 bytes. A desktop browser is
recommended; allow roughly 1.5 GB of memory. Expansion and worker startup happen
on each load, including when the download is already saved. Long-distance
queries can take longer than city routes.

London's BNG bbox is `520000,174000,540000,188000` (20 × 14 km). Boundary routes
may be cut off. National coverage includes disconnected islands; there is no
route between disconnected components and no inferred ferry connections.

Both datasets use the official OS Open Roads GeoPackage downloaded 2026-10-19,
archive MD5 `2ee5d30899c4a44df321e5e1a66989ac`. See each manifest for SHA-256
hashes, counts and attribution. Data is covered by OGL v3.0, separately from the
MIT code. The browser libraries and data are excluded from Python packages.

## How local caching works

- Each data edition has its own Cache Storage entry, identified by content hashes.
  Switching datasets terminates the old worker, releases its graph and SQLite
  memory, then opens the selected dataset. Both downloads remain saved on disk.
- The national files are split into independent gzip chunks with at most 16 MiB
  expanded per chunk. The worker checks each compressed chunk's SHA-256 before
  saving or using it. It expands chunks directly into the destination buffer,
  keeping temporary memory bounded. Cache hits make **no data network request**.
- Interrupted downloads keep completed chunks. **Retry / resume** downloads only
  missing chunks; corrupt chunks are removed and must be retried. Quota failures
  produce an actionable error. The download size and saved progress are visible.
- The page checks quota and requests persistent browser storage. The browser can
  refuse this request; the UI shows whether storage is protected or reclaimable.
  Clearing site storage, eviction, private browsing or a new data edition can
  require another download. Persistence cannot guarantee permanent retention.
- The service worker caches the app, libraries, WASM and dataset manifests. Once
  the status says **app and data saved**, cached datasets support offline reloads,
  dataset switching and new routes. Only manifest/app update checks need network
  access; these have offline fallbacks. An app update may take a second reload.
- **Remove saved dataset** removes that dataset's current download. It does not
  delete the other dataset. Older data editions can be removed using the browser's
  site-storage controls. Compressed national chunks occupy about 399 MB on disk.

At national zoom levels the map displays a bounded sample of major roads; at
street zoom it queries the visible area from the RDB tile index. Rendering is
limited independently of routing: queries always use the full loaded graph.

## Browser API

The active page exposes its selected client as `window.roadsDB` after loading:

```javascript
const result = await roadsDB.route({
  from: [53.4808, -2.2426], // latitude, longitude
  to: [53.8008, -1.5491]
});
console.log(result.cost_m, result.steps, result.coordinates);
```

Serve the demo assets from HTTPS or localhost to integrate the API elsewhere:

```javascript
import {createRoadsClient} from './api.js';
const client = createRoadsClient({
  dataset: 'gb', // or 'london' (default)
  onProgress: ({stage, complete, total}) => console.log(stage, complete, total)
});
const metadata = await client.ready; // includes downloaded_bytes for this load
const result = await client.route({from: [53.4808, -2.2426], to: [53.8008, -1.5491]});
client.close(); // terminate before switching datasets to release memory
```

Client creation starts loading immediately, so obtain first-download consent and
request persistent storage on the page first. The response includes distance,
full road geometry as `[lat, lon]`, approximate instructions, snaps, timings,
explored nodes and a dataset ID. `routing_ms` excludes geometry/name enrichment;
`elapsed_ms` includes it. Both exclude initial loading. This is an asynchronous
browser API; use `roads-rdb-map` for a local Python HTTP API.

## Local preview and tests

Requires Python 3.11+ and Node.js 24+; no npm install is needed:

```bash
node --test tests/*.test.cjs
python scripts/prepare_pages.py --download-national --materialize-national
ROADS_NATIONAL_DATA=build/national node --test tests/browser.test.cjs
python -m http.server 8766 --bind 127.0.0.1 --directory build/pages
```

Open `http://127.0.0.1:8766/`. The preparation command downloads the pinned data
release into ignored `build/national`, verifies all compressed and assembled
hashes, and stages the complete site. It reuses existing local chunks. The
optional materialization flag also reconstructs raw files for national tests.

Tests cover 20 London routes against Python, local/intercity national parity
(including London to Edinburgh), partial and same-road snaps, disconnected
components, reader validation, workspace reuse, source-ID mappings, binary tile
search, viewport bounds, cache reuse without network, interrupted downloads,
checksum failures, dataset isolation, eviction, removal and quota failure. Full
national parity runs in Pages CI after fetching the pinned data. Browser QA
also checks switching and offline routes with the preview server stopped.

## Rebuild and publish data

Download and verify the official GeoPackage through the
[OS Downloads API](https://docs.os.uk/os-apis/accessing-os-apis/os-downloads-api/getting-started/automating-os-opendata-downloads).
Keep its supplied licence. Rebuild the two datasets separately:

```bash
python scripts/build_pages_demo.py \
  --input oproad_gpkg_gb/Data/oproad_gb.gpkg \
  --bbox 520000,174000,540000,188000 \
  --source-date 2026-10-19 \
  --source-md5 2ee5d30899c4a44df321e5e1a66989ac
python scripts/build_national_demo.py \
  --input oproad_gpkg_gb/Data/oproad_gb.gpkg \
  --source-date 2026-10-19 \
  --source-md5 2ee5d30899c4a44df321e5e1a66989ac
```

Use the actual date, checksum and acknowledgement for a new edition. The full
export needs substantial build memory; this edition was built on a 64 GB Mac.
Both exporters join actual source IDs to dense geometry IDs rather than assuming
`fid == geom_id + 1`. The national names table uses a label dictionary and a
compatible `roads` view to keep the SQLite sidecar compact.

Commit London files and the national manifest, NOTICE and Python parity cases.
Keep the national binaries and source archive out of Git. Publish national gzip
chunks and metadata in a separate, immutable **data** release, with its tag in
`demo/data/gb/manifest.json`. This edition is
[data-gb-2026-10-19](https://github.com/robertpitt/roads-db/releases/tag/data-gb-2026-10-19).
To reconstruct raw files manually, concatenate the numerically ordered
`gb.rdb.*.gz` files and decompress all gzip members; repeat for `gb.sqlite`.
Do not replace assets under an existing data-edition tag.

The Pages workflow downloads the release assets during build and serves them
from the same origin as the app. Browsers do not fetch GitHub release URLs, so
cross-origin release-asset access is unnecessary. Staging enforces a 950 MB site
budget beneath [GitHub Pages' 1 GB limit](https://docs.github.com/en/pages/getting-started-with-github-pages/github-pages-limits).
The current site is about 412 MB; national first downloads also count toward
Pages' bandwidth limits. A busier demo may need dedicated data hosting.

On pushes to `main`, `.github/workflows/pages.yml` verifies data, runs national
parity, stages the site, and deploys to GitHub Pages. Pull requests run checks
without deployment. This workflow does not publish the v0.1.0 software release.
Dependencies are vendored with licences: Leaflet 1.9.4 (BSD-2-Clause), sql.js
1.15.1 (MIT), and Proj4js 2.22.0 (MIT).
