# roads-rdb

Small evaluator for a compact OS Open Roads routing-kernel binary.

The MVP builds:

```text
dataset.rdb          # compact path-planning binary
road_lookup.sqlite   # companion metadata lookup keyed by dense IDs
```

The binary runtime accepts British National Grid coordinates and returns ordered
dense node IDs, with optional edge IDs for exact enrichment.

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

The current pure-Python evaluator materializes the full file into Python objects
when building, but reads the result through `mmap`. On the 2026-05 Open Roads
GeoPackage, the full build produced a 560 MB `build/full_gb.rdb` in about 92
seconds and used roughly 7.7 GB peak memory.

Full-file inspect now uses the mmap section directory and does not materialize
the graph:

```text
inspect wall time: ~0.05s
inspect peak RSS:  ~14 MB
```

The raw Dijkstra query still allocates per-query distance/predecessor arrays;
server-style callers can now reuse a `QueryWorkspace` to avoid repeated
full-graph allocation. New builds also include connected-component IDs and
reverse adjacency sections used by `auto` routing.

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
companion SQLite lookup database
unit tests and real-data smoke benchmarks
```

Not implemented yet:

```text
cell overlays
corridor compression
shortcut unpack layers
lon/lat to BNG conversion
turn restrictions or navigation instructions
```

Existing RDB v1 files remain readable, but they do not contain component or
reverse-adjacency sections. Rebuild the RDB to enable those runtime paths.
