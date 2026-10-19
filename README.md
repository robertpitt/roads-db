# roads-rdb

An experimental Python builder and evaluator for compact, memory-mapped
OS Open Roads routing indexes. `v0.1.0` provides A* distance queries, batch
routing, optional metadata lookup, and a local click-to-route map.

**Experimental: routes are paths through a simplified graph.** Road links are
treated as bidirectional. One-way streets, legal access, turn restrictions,
speed profiles, and traffic are not modelled. The directions are approximate;
this release is intended for research and distance experiments.

**[Try the local-first browser demo →](https://robertpitt.github.io/roads-db/)**
Choose central London (11.53 MB) or Great Britain (399.09 MB compressed). Routes
run in a Web Worker; verified downloads stay in browser storage for offline reuse.
Great Britain covers England, Scotland and Wales, excluding Northern Ireland. See the
[demo guide and browser API](docs/PAGES_DEMO.md).

The MVP builds:

```text
dataset.rdb          # compact path-planning binary
road_lookup.sqlite   # companion metadata lookup keyed by dense IDs
```

The binary runtime accepts British National Grid coordinates and returns ordered
dense node IDs, with optional edge IDs for exact enrichment.

## Install

Use Python **3.11 or newer**. The package has no third-party Python runtime
dependencies. CI covers Python 3.11–3.14 on Linux, plus Python 3.13 on macOS and
Windows. Clone the repository, then install it in a virtual environment. Skip
the first two commands if you already have a checkout or extracted source
distribution:

```bash
git clone https://github.com/robertpitt/roads-db.git
cd roads-db
python3 -m venv .venv
source .venv/bin/activate
python -m pip install .
roads-rdb --help
roads-rdb-map --help
```

On Windows, use `py -3 -m venv .venv` and `.venv\Scripts\Activate.ps1` in
PowerShell, then run the same `python -m pip install .` command. The examples
below use Bash line continuations; in PowerShell enter each command on one line.

Python module entry points also work: `python -m roads_rdb` and
`python -m roads_rdb.map_server`.

## Try It Without Downloading Data

Create a tiny synthetic input and query its 200 metre shortest path:

```bash
python examples/create_demo_gpkg.py build/demo.gpkg
roads-rdb build --input build/demo.gpkg --output build/demo.rdb --lookup build/demo_lookup.sqlite
roads-rdb inspect --index build/demo.rdb
roads-rdb query --index build/demo.rdb --from 530000,180000 --to 530200,180000 --snap-radius 1 --with-edges
roads-rdb-map --index build/demo.rdb --no-directions
```

The query returns `nodes: [0, 1, 2]` and `cost_m: 200.0`. Open
`http://127.0.0.1:8765/?from=51.50399,-0.12835&to=51.50394,-0.12547` to see
the demo area. The generated network is fictional; its input is a small SQLite
fixture with the tables and GeoPackage geometry blobs the reader uses, rather
than a complete GIS export. The generator refuses to overwrite an existing file.

Routing works offline. The map background and Leaflet assets require an internet
connection and use OpenStreetMap tiles and unpkg.com respectively.

## Get OS Open Roads Data

1. Open the official [OS Open Roads product page](https://www.ordnancesurvey.co.uk/products/os-open-roads)
   and follow **Access this product** to download the Great Britain GeoPackage.
2. Extract the archive locally. The commands below assume its data file is at
   `oproad_gpkg_gb/Data/oproad_gb.gpkg`; use your extracted path if it differs.
3. Keep the supplied licence acknowledgements with the download and any derived
   datasets. See [NOTICE](NOTICE) for the acknowledgement supplied with the 2026 data.

National source data and generated databases are excluded from Git, with the
bounded `demo/data/` extract as an explicit exception. The Python distributions
do not bundle the demo data. The reader expects `road_node` and `road_link` tables and
EPSG:27700 coordinates; see [database_shape.md](database_shape.md) for the
observed source schema.

## Build A Small Extract

```bash
python3 -m roads_rdb build \
  --input oproad_gpkg_gb/Data/oproad_gb.gpkg \
  --bbox 530000,180000,531000,181000 \
  --output build/tiny.rdb \
  --lookup build/tiny_lookup.sqlite
```

## Query

```bash
python3 -m roads_rdb query \
  --index build/tiny.rdb \
  --from 530100,180100 \
  --to 530900,180900 \
  --with-edges
```

Routing uses A* with a British National Grid Euclidean lower-bound heuristic.
The query loop keeps route costs in integer RDB length units and converts to
metres only in the returned payload.

Origin-to-many batch routing reuses the origin snap work and one query
workspace:

```bash
python3 -m roads_rdb batch \
  --index build/tiny.rdb \
  --from 530100,180100 \
  --to 530900,180900 \
  --to 530700,180500
```

## Benchmark

```bash
python3 -m roads_rdb bench --index build/tiny.rdb --pairs 25
```

The benchmark samples real graph nodes by default. Use `--sample bbox` to sample
random points inside the dataset bounds instead.

Distance-band benchmark using only the RDB file:

```bash
python3 scripts/benchmark_distance_bands.py \
  --index build/full_gb.rdb \
  --distances-km 10,50,100,200 \
  --pairs 100 \
  --json
```

This builds an in-memory node-coordinate sampler from the RDB, picks random
node-coordinate pairs at each straight-line distance band, runs `route_nodes`,
and reports route timing/path stats including mean, median, p90, p95, and p99.
It does not query the source GeoPackage.

## Build The Full GB Routing Binary

Omit `--bbox`:

```bash
python3 -m roads_rdb build \
  --input oproad_gpkg_gb/Data/oproad_gb.gpkg \
  --output build/full_gb.rdb
```

The pure-Python builder materializes graph data into Python objects, then the
runtime reads the result through `mmap`. Allow substantial RAM for national
builds. A historical development run on the 2026-05 GeoPackage produced a
560 MB RDB in about 92 seconds with roughly 7.7 GB peak memory. These are
historical measurements, not a fresh v0.1.0 benchmark or hardware-independent
performance guarantee.

Full-file inspect now uses the mmap section directory and does not materialize
the graph:

```text
inspect wall time: ~0.05s
inspect peak RSS:  ~14 MB
```

The route query uses reusable distance/predecessor arrays; server-style callers
can reuse a `QueryWorkspace` to avoid repeated full-graph allocation. RDB v1
builds split the route hot path into `node_x`, `node_y`, `edge_to`,
`edge_length`, and `edge_geom` arrays, with connected-component IDs,
64-byte-aligned sections, and snap geometry bounding boxes for cheaper candidate
pruning. Reverse-edge and source-link mappings are optional sidecar data outside
the route hot path.

## Evaluate Directions

Use the RDB for routing and the original GeoPackage SQLite tables for road names:

```bash
python3 scripts/evaluate_directions.py \
  --index build/full_gb.rdb \
  --gpkg oproad_gpkg_gb/Data/oproad_gb.gpkg \
  --from 52.591905,-1.9786927 \
  --to 52.5934361,-1.9675859
```

This is a simple evaluator: it groups route edges by road name/number and derives
turn labels from node bearings. It is not yet a navigation-grade instruction
engine with turn restrictions.

## Route Map

Run a local click-to-route map against an RDB:

```bash
python3 -m roads_rdb.map_server \
  --index build/full_gb.rdb \
  --gpkg oproad_gpkg_gb/Data/oproad_gb.gpkg
```

Then open `http://127.0.0.1:8765/`. The map accepts WGS84 lat/lon clicks,
converts them to British National Grid for routing, and draws the returned route
node path back on a Leaflet map. When the GeoPackage is available, the UI also
shows turn-by-turn steps by fetching `road_link` metadata for the route edge
geometry IDs.

You can pre-seed a route for testing:

```text
http://127.0.0.1:8765/?from=52.591905,-1.9786927&to=52.5934361,-1.9675859
```

## Current Scope

Implemented:

```text
GeoPackageBinary POINT/LINESTRING decoding
bbox extract using GeoPackage RTree when available
dense node and directed-edge graph construction
fixed tile snap index
single-file RDB writer/reader
memory-mapped section reader
route-node query using A*
connected-component route rejection
reusable query workspace
origin-to-many batch route queries
companion SQLite lookup database
WGS84 <-> BNG conversion
local click-to-route web map
unit tests and real-data smoke benchmarks
```

Not implemented yet:

```text
cell overlays
corridor compression
shortcut unpack layers
one-way, legal access, or turn restrictions
navigation-grade instructions, speed profiles, or traffic
```

Earlier prototype binaries are intentionally unsupported. Rebuild the RDB to
emit the v1 split-array hot-path layout, snap bounding boxes, and required
component sections.

Only RDB format version 1 is accepted. Package version `0.1.0` and binary format
version `1` are separate version numbers. Dense IDs are local to one build and
must not be mixed across indexes or lookup databases. See [SPEC.md](SPEC.md) for
the format and [NOTES.md](NOTES.md) for implementation notes.

The map draws the returned node path rather than the full road geometry. Its
directions lookup assumes a full build using the same GeoPackage with contiguous
road-link FIDs and no skipped geometries. For bbox extracts or other inputs, use
`--no-directions` to avoid incorrect road-name mappings. WGS84/BNG conversion uses
an approximate Helmert transform; survey-grade coordinate accuracy is outside
the scope of this release. The HTTP server is a local development demo.

## Development And Release Checks

```bash
python -m unittest discover -s tests -v
python -m pip install build twine
python -m build
python -m twine check --strict dist/*
python scripts/check_release.py
```

The release checker installs the wheel in a temporary environment outside the
checkout and repeats the routing and HTTP tests. It verifies the CLI entry
points and packaged HTML asset. No downloaded data is needed. See
[CONTRIBUTING.md](CONTRIBUTING.md), [CHANGELOG.md](CHANGELOG.md), and the
[release checklist](docs/RELEASING.md).

## Licence And Attribution

Code and synthetic examples: [MIT](LICENSE). OS Open Roads has separate
[Open Government Licence terms](https://os.uk/opendata/licence); see [NOTICE](NOTICE).
The map displays the 2026 OS acknowledgement and OpenStreetMap attribution.
Update the acknowledgement for a different data vintage, and retain applicable
attribution when sharing datasets, maps, or screenshots.
