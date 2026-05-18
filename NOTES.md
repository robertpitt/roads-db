# Road RDB Handoff Notes

This project is an experimental OS Open Roads routing-kernel prototype. The
current goal is not navigation-grade routing. The goal is to evaluate a compact
single binary file that can be memory-mapped, queried quickly, and return an
ordered path as dense node IDs plus optional edge IDs.

## Current Artifacts

Main source data:

```text
oproad_gpkg_gb/Data/oproad_gb.gpkg
```

Current full routing binary:

```text
build/full_gb.rdb
```

Full build stats from the current prototype:

```text
file size:         560 MB
nodes:             3,346,499
directed edges:    7,897,102
snap geometries:   3,961,077
snap tiles:        2,253,083
build time:        ~92 seconds
build peak RSS:    ~7.7 GB
```

The full companion lookup SQLite DB has not been built yet. Small lookup DBs
have been built for bbox experiments.

## Code Layout

```text
roads_rdb/gpkg.py       GeoPackageBinary decoding and road_node/road_link iterators
roads_rdb/geo.py        WGS84 lat/lon -> British National Grid conversion
roads_rdb/graph.py      Graph model, snap index access, Dijkstra route query
roads_rdb/rdb.py        RDB writer and mmap-backed RDB reader
roads_rdb/lookup.py     Companion SQLite lookup writer for bbox builds
roads_rdb/cli.py        build/query/inspect/bench CLI

scripts/evaluate_directions.py        RDB route + GeoPackage SQL enrichment
scripts/benchmark_distance_bands.py   RDB-only benchmark by straight-line distance band

tests/test_gpkg.py      GeoPackage geometry tests
tests/test_rdb.py       RDB round-trip/query test
```

Run tests:

```bash
python3 -m unittest discover -s tests
```

## File Design

The RDB file is a simple sectioned binary format:

```text
header
section directory
metadata JSON
nodes
edges
offsets
snap_geom_records
snap_geom_offsets
snap_geom_points
snap_tiles
snap_tile_geoms
```

Header:

```text
magic:          RDB1
format_version: 1
section_count:  u32
```

Section directory entries:

```text
name:   32-byte nul-padded ASCII
offset: u64
size:   u64
```

Core records:

```text
node:              x_units:u32, y_units:u32
edge:              from:u32, to:u32, length_dm:u32, reverse:u32, geom_id:u32, source_link:u32
adjacency offset:  u32
snap geom record:  start_node:u32, end_node:u32, forward_edge:u32, reverse_edge:u32,
                   length_dm:u32, point_count:u32
snap geom point:   x_units:u32, y_units:u32
snap tile:         tile_x:u32, tile_y:u32, first_geom:u32, geom_count:u32
snap tile geom:    geom_id:u32
```

Coordinates are EPSG:27700 British National Grid metres. Packed node and snap
geometry points are decimetre offsets from file origin:

```text
x = origin_x_m + x_units / coord_scale
y = origin_y_m + y_units / coord_scale
coord_scale = 10
```

The current reader in `roads_rdb/rdb.py` is mmap-backed. It does not materialize
the graph on load. `inspect` on the full GB file is roughly:

```text
wall time: ~0.05s
peak RSS:  ~14 MB
```

## Build Pipeline

Build a bounded extract:

```bash
python3 -m roads_rdb build \
  --input oproad_gpkg_gb/Data/oproad_gb.gpkg \
  --bbox 530000,180000,531000,181000 \
  --output build/tiny.rdb \
  --lookup build/tiny_lookup.sqlite
```

Build full GB:

```bash
python3 -m roads_rdb build \
  --input oproad_gpkg_gb/Data/oproad_gb.gpkg \
  --output build/full_gb.rdb
```

Current build behavior:

```text
1. Read road_node records.
2. Assign dense node IDs in source fid order.
3. Iterate road_link records.
4. Decode GeoPackageBinary LINESTRING geometry.
5. Create bidirectional directed edges, except loops become self-edges.
6. Build adjacency offsets.
7. Build fixed-grid snap tiles.
8. Stream RDB sections to disk.
```

For full builds, source UUIDs are not retained unless `--lookup` is provided.
This avoids holding millions of strings in memory.

The writer now streams sections directly. It no longer constructs a giant
dictionary of section byte blobs before writing.

Remaining build memory pressure:

```text
road_node UUID -> node_id map
decoded snap geometries as Python objects
snap tile dictionary
edge arrays before writing
```

The next build-side reduction should target snap geometry and snap tile
construction first.

## Query Process

Query command:

```bash
python3 -m roads_rdb query \
  --index build/full_gb.rdb \
  --from 528500,179500 \
  --to 531500,181500 \
  --with-edges
```

Query flow:

```text
1. Open RDB through mmap.
2. Use snap tile index to find nearby road geometries.
3. Compute point-to-polyline distance for snap candidates.
4. Convert snap candidates to graph endpoint costs.
5. Run multi-source Dijkstra over directed edges.
6. Stop when a target candidate endpoint is finalized.
7. Reconstruct ordered node IDs and optional edge IDs.
```

Returned shape:

```text
node_ids: ordered dense node IDs
edge_ids: optional ordered dense edge IDs
cost_m:   distance cost in metres
origin_snap / target_snap metadata
```

The current Dijkstra state uses compact arrays:

```text
dist:      array("d")
prev_node: array("i")
prev_edge: array("i")
```

This reduced full-query RSS substantially compared with Python lists.

## Direction Evaluation

Directions evaluator:

```bash
python3 scripts/evaluate_directions.py \
  --index build/full_gb.rdb \
  --gpkg oproad_gpkg_gb/Data/oproad_gb.gpkg \
  --from 52.591905,-1.9786927 \
  --to 52.5934361,-1.9675859
```

This script:

```text
1. Converts WGS84 lat/lon to BNG.
2. Routes through build/full_gb.rdb.
3. Maps route edge geom IDs back to road_link.fid.
4. Queries the original GeoPackage for road names/numbers/classes.
5. Groups adjacent edges by road label.
6. Derives rough turn labels from node bearings.
```

Example output for the above route:

```text
Route: 636.7 m, 10 nodes, 9 edges

1. Start on Butts Road (248 m)
2. Slight left onto Lichfield Street (41 m)
3. Turn right onto Lichfield Road (5 m)
4. Slight left onto A461 (26 m)
5. Continue onto Mellish Road (92 m)
6. Turn left onto Mellish Court (49 m)
7. Arrive at destination (0 m)
```

Important caveat: this is not navigation-grade turn-by-turn routing. It has no
turn restrictions, lane data, one-way rules, or authoritative manoeuvre model.

## Benchmarks

Small London 4 km x 3 km extract:

```text
nodes:          2,599
directed edges: 7,026
RDB size:       444 KB
```

Full RDB inspect:

```text
wall time: ~0.05s
peak RSS:  ~14 MB
```

Full RDB local London route:

```text
wall time: ~0.08s
peak RSS:  ~68 MB
route:     66 nodes
```

Distance-band benchmark command:

```bash
python3 scripts/benchmark_distance_bands.py \
  --index build/full_gb.rdb \
  --distances-km 10,50,100,200 \
  --pairs 2 \
  --json
```

Latest sampled results:

```text
RDB load:            0.0066s
node sampler build:  1.7334s
peak RSS:            ~508 MB

10 km:   median 34.645 ms,   p95 42.833 ms,   median nodes 93.5
50 km:   median 301.793 ms,  p95 517.801 ms,  median nodes 227
100 km:  median 960.637 ms,  p95 1482.102 ms, median nodes 471.5
200 km:  median 3863.816 ms, p95 4067.254 ms, median nodes 1015
```

These are raw Dijkstra numbers. They are not expected to be final. The purpose
of the next graph layers is to make 100-200 km routes avoid scanning large
parts of the national graph.

## Known Limitations

Routing limitations:

```text
No one-way restrictions.
No turn restrictions.
No traffic/speed profile yet.
No road access restrictions.
No component index in the RDB yet.
No A* heuristic yet.
No corridor compression yet.
No cell overlays or shortcut hierarchy yet.
```

Data/model limitations:

```text
Open Roads is not a fully routable navigation product.
Directions are inferred from names and bearings only.
WGS84->BNG conversion is implemented locally and should be validated against pyproj.
Full lookup DB has not been produced.
```

Implementation limitations:

```text
Full build still peaks around 7.7 GB RSS.
Snap geometries are stored as fixed u32 point pairs, not delta/varint encoded.
Snap tile IDs are sparse records, searched by binary search.
Route query still allocates full-graph Dijkstra arrays per query.
No reusable query workspace/pool yet.
No bidirectional Dijkstra.
No mmap-backed priority queue or native extension.
```

## Recommended Next Steps

High-value next steps:

```text
1. Add connected component IDs to RDB and reject impossible routes before search.
2. Add A* with Euclidean lower-bound heuristic.
3. Add bidirectional Dijkstra/A* for long routes.
4. Add corridor compression for degree-2 chains.
5. Add shortcut unpacking so route output still returns original node IDs.
6. Add 1 km / 5 km / 25 km cell overlays.
7. Replace the benchmark node sampler with an mmap/grid-backed sampler to reduce RSS.
8. Build the full companion lookup DB or a compact link metadata binary.
9. Split snap geometry from route geometry and test geometry simplification.
10. Validate WGS84->BNG conversion against `pyproj` when available.
```

For the immediate next iteration, the best target is probably:

```text
A* + connected components + bidirectional search
```

This should improve long routes without changing the file format as much as
cell overlays or contraction-style shortcuts.

## Design Direction

Keep `dataset.rdb` as the routing kernel:

```text
coords -> snap -> graph search -> node IDs / edge IDs
```

Keep rich metadata elsewhere:

```text
edge ID / source link ID -> road names, numbers, display geometry, directions text
```

The RDB should remain sectioned and mmap-friendly. Avoid adding string metadata,
source UUIDs, or verbose geometry to the hot routing file unless benchmarks show
it is necessary.

