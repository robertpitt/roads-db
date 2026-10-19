# Road RDB Fast Routing Specification

Status: Draft

Spec version: 1.0

Binary format: `RDB1` format version 1

Scope: A language-agnostic, memory-mapped road routing kernel that returns dense
route node IDs from projected coordinates with the smallest practical query
client.

## 1. Normative Language

The key words `MUST`, `MUST NOT`, `REQUIRED`, `SHOULD`, `SHOULD NOT`, `MAY`, and
`OPTIONAL` are to be interpreted as described in RFC 2119.

`Implementation-defined` means an implementation may choose its own behavior,
but the choice must be documented and must not violate this specification.

## 2. Purpose

An RDB file is a prebuilt binary index for very fast road routing. The file is
opened once, memory-mapped directly into the process, and queried without SQL,
UUID lookups, string records, source metadata, or per-record object
materialization on the hot path.

The core operations are:

```text
routeNodes(origin_coordinate, target_coordinate) -> ordered dense node IDs
routeBatch(origin_coordinate, target_coordinates[]) -> routeNodes result per target
snap(point) -> nearby routable snap candidates
```

Routing means path generation over the stored graph. It does not mean road-name
lookup, turn-by-turn instruction generation, display geometry enrichment, live
traffic, access rules, or metadata retrieval.

## 3. Design Goals

- Optimize the binary layout for direct `mmap` access by a small query client.
- Keep the route hot path integer-array based.
- Store enough snap data in the RDB file to resolve coordinates to graph nodes
  without a source database.
- Return dense node IDs, and optionally dense directed edge IDs, with no source
  IDs required.
- Support single origin-target routing and origin-to-many batch routing.
- Make the baseline route algorithm exact over the stored integer graph costs.
- Keep metadata, source IDs, road names, display geometry, directions, and
  reverse-edge lookup outside the required hot path.
- Permit optional exact acceleration sections without changing required v1
  section semantics.

## 4. Non-Goals

- RDB is not a general-purpose road information database.
- RDB v1 does not store source node UUIDs, source link UUIDs, road names, road
  numbers, turn instruction text, or display-only metadata.
- RDB v1 does not define turn restrictions, one-way inference from source data,
  legal access rules, closures, traffic, or speed profiles.
- RDB v1 does not require shortcut graphs, contraction hierarchies, overlays, or
  corridor compression. Those are optional exact accelerators.
- RDB v1 does not require dense IDs to remain stable across rebuilds.

## 5. Runtime Model

An implementation has two roles:

- Builder: converts a richer road source into one immutable RDB file.
- Query client: opens the RDB file, prepares typed views over sections, snaps
  query coordinates, runs routing, and returns dense IDs.

The query client MUST be able to run without the source dataset and without a
companion lookup database.

An opened dataset is immutable. Implementations MAY share it across threads when
the host language can safely share immutable memory. Mutable query state MUST be
per request, per worker, or synchronized.

## 6. Primitive Encoding

All fixed-width integer fields MUST be little-endian.

Primitive types:

```text
u32: unsigned 32-bit integer
u64: unsigned 64-bit integer
bytes[N]: exactly N bytes
```

The sentinel value `UINT32_NONE` is:

```text
0xffffffff
```

Writers MUST fail instead of wrapping if any count, dense ID, coordinate unit,
or length unit cannot fit in `u32`.

## 7. Units

Coordinates are projected metre coordinates encoded as non-negative integer
offsets from the file origin:

```text
x_m = origin_x_m + x_units / coord_scale
y_m = origin_y_m + y_units / coord_scale
```

Lengths and route costs are encoded as integer length units:

```text
length_m = length_units / length_scale
```

The query engine SHOULD keep endpoint costs, edge costs, tentative distances,
heap priorities, and best route totals in integer length units. Conversion to
metres SHOULD happen only at API boundaries.

## 8. Dense IDs

All public IDs returned by the query engine are zero-based dense indexes:

```text
node_id:      index into node_x and node_y
edge_id:      index into edge_to, edge_length, and edge_geom
geom_id:      index into snap_geom_records and snap_geom_bboxes
component_id: value stored in components
```

The builder MUST use one consistent dense ID assignment across all sections in
one file.

## 9. File Layout

An RDB file is:

```text
header
section directory
section data
```

Readers MUST use section directory offsets. Readers MUST NOT infer section
locations from section order.

### 9.1 Header

The header starts at byte offset `0` and is exactly 12 bytes:

```text
magic:          bytes[4]  ASCII "RDB1"
format_version: u32       current version is 1
section_count:  u32       number of directory entries
```

### 9.2 Section Directory

The directory starts immediately after the header. Each entry is 48 bytes:

```text
name:   bytes[32]  ASCII section name, NUL-padded
offset: u64        absolute byte offset from start of file
size:   u64        section byte length
```

Section names MUST be unique non-empty ASCII strings of at most 32 bytes.
Section ranges MUST be fully contained in the file and MUST NOT overlap.

V1 writers MUST align every section offset to at least 64 bytes. Readers MUST
honor the directory offsets. Files with any other `format_version` are
unsupported and MUST be rebuilt.

## 10. Required Sections

A conforming v1 file MUST contain exactly the data needed for baseline snapping,
component rejection, and exact route search:

```text
metadata
node_x
node_y
edge_to
edge_length
edge_geom
offsets
snap_geom_records
snap_geom_bboxes
snap_geom_offsets
snap_geom_points
snap_tiles
snap_tile_geoms
components
component_metadata
```

Readers MUST ignore unknown optional sections unless those sections are required
by a future format version the reader claims to support.

## 11. Section Schemas

### 11.1 `metadata`

The `metadata` section is a UTF-8 JSON object. Required keys:

```text
format_version:          integer, must be 1
origin_x_m:              integer projected metres
origin_y_m:              integer projected metres
coord_scale:             integer coordinate units per metre
length_scale:            integer length units per metre
snap_tile_size_m:        number
node_count:              integer
directed_edge_count:     integer
snap_geometry_count:     integer
snap_tile_count:         integer
snap_geom_point_count:   integer
snap_tile_geom_count:    integer
component_count:         integer
```

Optional metadata keys MAY describe dataset identity, source fingerprint,
builder version, coordinate reference, and build timestamp. Unknown metadata keys
MUST be ignored by readers.

### 11.2 `node_x` and `node_y`

The `node_x` section contains `node_count` `u32` x-coordinate offsets. The
`node_y` section contains `node_count` `u32` y-coordinate offsets.

```text
node_x[node_id] = x_units
node_y[node_id] = y_units
```

Splitting coordinates into separate arrays keeps the A* heuristic loop on the
smallest required memory streams.

### 11.3 `edge_to`, `edge_length`, and `edge_geom`

The required directed-edge hot path is stored in split arrays:

```text
edge_to[edge_id]     = target node_id
edge_length[edge_id] = integer length units
edge_geom[edge_id]   = snap geometry id
```

The `from_node` is implied by `offsets`. Edge records MUST be grouped by
`from_node`.

For node `n`, outgoing edges are:

```text
edge_to[offsets[n] ... offsets[n + 1] - 1]
edge_length[offsets[n] ... offsets[n + 1] - 1]
edge_geom[offsets[n] ... offsets[n + 1] - 1]
```

### 11.4 `offsets`

The `offsets` section contains `node_count + 1` `u32` values:

```text
offsets[0] = 0
offsets[node_count] = directed_edge_count
offsets[i] <= offsets[i + 1]
```

### 11.5 `snap_geom_records`

Each snap geometry record is 24 bytes:

```text
start_node:   u32
end_node:     u32
forward_edge: u32
reverse_edge: u32
length:       u32
point_count:  u32
```

`reverse_edge` is `UINT32_NONE` when no distinct reverse directed edge exists.

### 11.6 `snap_geom_bboxes`

Each snap geometry bounding box record is 16 bytes:

```text
min_x_units: u32
min_y_units: u32
max_x_units: u32
max_y_units: u32
```

The record index is the `geom_id`. Query clients SHOULD test this box before
loading the geometry point range for exact point-to-polyline projection.

### 11.7 `snap_geom_offsets`

The section contains `snap_geometry_count + 1` `u32` values and indexes into
`snap_geom_points`:

```text
points for geom g =
  snap_geom_points[snap_geom_offsets[g] ... snap_geom_offsets[g + 1] - 1]
```

### 11.8 `snap_geom_points`

Each point record is 8 bytes:

```text
x_units: u32
y_units: u32
```

Points MUST be stored in source geometry order. A routing snap geometry SHOULD
contain at least two points.

### 11.9 `snap_tiles`

Each tile record is 16 bytes:

```text
tile_x:     u32
tile_y:     u32
first_geom: u32
geom_count: u32
```

Tiles MUST be sorted lexicographically by `(tile_x, tile_y)`.

Tile coordinates are computed in projected metres:

```text
tile_x = floor(x_m / snap_tile_size_m)
tile_y = floor(y_m / snap_tile_size_m)
```

### 11.10 `snap_tile_geoms`

The section contains `snap_tile_geom_count` `u32` geometry IDs. For one tile
record, assigned geometry IDs are:

```text
snap_tile_geoms[first_geom ... first_geom + geom_count - 1]
```

Geometry IDs within a tile SHOULD be sorted ascending.

### 11.11 `components`

The section contains `node_count` `u32` values. The value at `node_id` is the
node's `component_id`.

### 11.12 `component_metadata`

Each component metadata record is 24 bytes:

```text
node_count:  u32
edge_count:  u32
min_x_units: u32
min_y_units: u32
max_x_units: u32
max_y_units: u32
```

## 12. Optional Sections

Optional sections are outside the required route hot path. Common optional
sections:

```text
edge_reverse:      directed_edge_count u32 values
edge_source_links: directed_edge_count u32 values
```

Acceleration sections MAY add exact shortcuts, overlays, landmark potentials,
or unpack tables. Such sections MUST NOT change required v1 section semantics.

## 13. Builder Procedure

The builder input is implementation-defined, but it MUST provide routable source
nodes, source links, link endpoint IDs, projected coordinates, link lengths, and
link polylines.

The builder MUST:

1. Load routable source nodes and links.
2. Reject or skip links without at least two geometry points.
3. Assign deterministic dense `node_id` values.
4. Choose `origin_x_m` and `origin_y_m` so all stored coordinate offsets are
   non-negative.
5. Quantize node coordinates, geometry points, and lengths.
6. Create one directed edge per routable direction.
7. Reorder edges into compressed-sparse-row adjacency order.
8. Build snap geometry records, bboxes, point offsets, and tile ranges.
9. Compute weakly connected components over the directed edge endpoints.
10. Write aligned sections and the section directory.

When rebuild-stable dense IDs are not required, builders SHOULD order nodes by
component and then by spatial locality, such as Morton or Hilbert order, to
improve cache behavior during graph search.

## 14. Snap Query

Inputs:

```text
point: projected x/y metres
radius_m: default 250
limit: default 8
```

Procedure:

```text
tile = floor(point / snap_tile_size_m)
tile_radius = max(1, ceil(radius_m / snap_tile_size_m))
geom_ids = union of geometry IDs in all covered tiles

for each geom_id:
    reject if snap_geom_bboxes[geom_id] is farther than radius_m
    project point onto every non-zero segment in the polyline
    keep candidates whose exact distance <= radius_m

return closest candidates by distance, limited by limit
```

Implementations SHOULD deduplicate geometry IDs before exact projection.

## 15. Route Query

`routeNodes(origin, target)` returns:

```text
node_ids:    ordered dense node IDs
edge_ids:    optional ordered dense directed edge IDs
cost_m:      optional total cost in metres
origin_snap: optional snap candidate used at origin
target_snap: optional snap candidate used at target
```

Baseline routing is exact shortest path over directed `edge_length` values plus
integer endpoint costs from snap positions.

Endpoint costs for one snap candidate:

```text
along_units = clamp(round(along_m * length_scale), 0, geom.length)

if along_units == 0:
    start_node cost 0
else if along_units == geom.length:
    end_node cost 0
else:
    start_node cost along_units
    end_node cost geom.length - along_units
```

If origin and target endpoint candidates share no component, the query MUST fail
before graph search.

The baseline graph search is multi-source, multi-target A* over directed edges.
The heuristic MUST be admissible in integer length units. A conforming heuristic
is:

```text
min over target endpoint t:
    floor(euclidean_distance_units(node, t) * length_scale / coord_scale)
    + target_endpoint_cost_units(t)
```

Implementations MAY use Dijkstra by returning zero heuristic values.

## 16. Batch Query

`routeBatch(origin, targets[])` routes from one coordinate to many candidate
target coordinates.

The query client SHOULD snap the origin once, compute origin endpoint costs once,
and reuse one query workspace sequentially across target searches. Each target
MUST still be snapped independently.

Batch results MUST be equivalent to calling `routeNodes(origin, target)` for
each target in order. Implementations MAY either fail the whole batch on the
first per-target error or expose per-target errors, but the behavior MUST be
documented.

## 17. Query Workspace

For large datasets, route queries MUST NOT initialize `node_count` distance,
predecessor, or settled arrays on every request.

Implementations SHOULD use reusable per-worker workspaces with:

```text
dist_units[node_id]: integer length units or infinity sentinel
predecessor_node[node_id]
predecessor_edge[node_id]
settled[node_id]
touched_nodes[]
priority_queue
```

Only nodes listed in `touched_nodes` SHOULD be reset between queries.

## 18. Reader Validation

Readers MUST validate at open:

- magic and supported format version;
- section directory shape, uniqueness, bounds, and non-overlap;
- presence of all required v1 sections;
- metadata version and required counts;
- required section sizes from metadata counts.

Readers MAY defer expensive cross-reference checks to debug or conformance mode.

## 19. Failure Model

Implementations SHOULD expose errors in these categories:

```text
invalid_file
unsupported_version
missing_section
corrupt_section
invalid_options
snap_not_found
no_shared_component
no_route
```

Error messages SHOULD identify the failed stage.

## 20. Performance Contract

The format is designed for low query latency, but exact timings depend on graph
size, hardware, implementation language, and optional accelerators.

Conforming implementations SHOULD publish benchmark results for:

- cold open time;
- mmap reader memory overhead after open;
- snap latency;
- single-pair route p50, p95, and p99 latency by distance band;
- origin-to-many batch throughput;
- allocations per query when a workspace is reused.

Benchmarks SHOULD use only the RDB file, not the source dataset.

## 21. Validation Matrix

Implementations SHOULD test:

- header and directory rejection paths;
- required v1 section sizes and 64-byte writer alignment;
- v1 hot-path arrays are exactly `node_count * 4` or
  `directed_edge_count * 4` bytes as applicable;
- `snap_geom_bboxes` reject distant geometries before point projection;
- snap returns expected candidates sorted by distance;
- component filtering rejects disconnected endpoints;
- route search returns expected node and edge sequences for known graphs;
- route cost equals integer endpoint costs plus traversed edge lengths;
- reusable query workspaces reset only touched nodes correctly;
- batch routing preserves target order and matches repeated single queries;
- files produced by one implementation can be read by another.

## 22. Definition of Done

An implementation can claim baseline RDB v1 compatibility when it can:

1. Build an `RDB1` v1 file with all required sections.
2. Open the file using mmap or equivalent immutable byte views.
3. Decode required records without per-record object materialization.
4. Run bbox-pruned snap queries from the RDB file alone.
5. Run exact route-node queries with integer internal costs.
6. Run origin-to-many batch queries with reused origin snap work and workspace.
7. Return ordered dense node IDs, and edge IDs when requested.
8. Pass the validation matrix.
