# Road DB Build And Query Process

This document defines an end-to-end process for turning the OS Open Roads
GeoPackage into a compact, memory-loadable `dataset.rdb` routing kernel.

The binary file has one main job:

```text
coordinates A and B -> ordered route node IDs
```

Almost everything else should be resolved outside the binary from a companion
database keyed by the same node and edge IDs. That external database can provide
road names, source UUIDs, full geometry, per-link distances, display metadata,
and debugging information after `dataset.rdb` has produced the path.

The project borrows the main lesson from `postcode-db`: do not store a generic
database and then ask it questions at runtime. Instead, exploit the structure of
the domain, normalize it into dense integer indexes, pack it into predictable
binary sections, and make the runtime query path touch as little memory as
possible.

Related context:

```text
database_shape.md
Idea.md
https://raw.githubusercontent.com/robertpitt/postcode-db/refs/heads/main/README.md
```

## Goal

Build a derived road-network dataset that can be loaded into memory by a server
and used to answer near-realtime route-node queries such as:

```text
Given coordinate A and coordinate B, what ordered node IDs form the route?
```

The primary output is:

```text
dataset.rdb
```

The runtime should be able to open this file as a single binary blob, create
typed views over its sections, and perform lookups without SQL, JSON parsing, or
string-heavy object allocation.

The preferred minimal runtime response is:

```ts
interface RouteNodeResult {
  nodeIds: Uint32Array;
  edgeIds?: Uint32Array;
  cost?: number;
  profile: "distance_m" | "estimated_time_s";
  originSnap?: SnapRef;
  targetSnap?: SnapRef;
}
```

`originSnap` and `targetSnap` are optional but useful when the input coordinates
fall midway along a road link. If the project chooses to return strictly only
nodes, the runtime should snap each coordinate to a real graph node and accept
the small loss of precision.

`edgeIds` is optional but important for exact enrichment. Road names, road
numbers, and per-link distances are link properties. If the service returns only
nodes, the external database must infer the edge for each adjacent node pair.
That is usually fine when there is only one edge between two nodes, but a compact
edge ID list removes ambiguity in parallel-edge or loop cases.

## Non-Goals

The first version is not a production navigation engine.

OS Open Roads provides road links, road nodes, geometry, length, road
classification, road function, road names, trunk-road flags, and primary-route
flags. It does not provide the full routing restrictions needed for authoritative
turn-by-turn navigation, such as live closures, turn restrictions, one-way
restrictions, height restrictions, weight restrictions, legal access rules, or
traffic conditions.

The first exact target is therefore:

```text
Shortest route-node sequence for a chosen static weight profile.
```

Estimated time can be added as a profile, but it must be labelled as an estimate
derived from road attributes, not as authoritative travel time.

## Source Data Constraints

The input described in [database_shape.md](database_shape.md) is:

```text
oproad_gpkg_gb/Data/oproad_gb.gpkg
```

Important facts:

```text
Format:       GeoPackage, SQLite 3 container
CRS:          EPSG:27700, British National Grid
Units:        metres
road_link:    3,961,077 LINESTRING rows
road_node:    3,346,499 POINT rows
junctions:    669 motorway junction POINT rows
```

The useful topology is already present:

```text
road_link.start_node -> road_node.id
road_link.end_node   -> road_node.id
road_link.length     -> metres
```

Every observed `road_link.start_node` and `road_link.end_node` has a matching
`road_node.id`. There are 25,052 loop links where `start_node = end_node`.

Important implementation constraints:

```text
Source IDs are UUID-shaped text.
Coordinates are British National Grid metres.
Geometries are GeoPackageBinary, not bare WKB.
road_link geometries include an XY envelope in the GeoPackageBinary header.
road_node geometries have an 8-byte GeoPackageBinary header.
There are no ordinary attribute indexes on id/start_node/end_node.
```

The builder should treat the GeoPackage as a source artifact only. It should not
be part of the runtime lookup path.

## Design Principles From postcode-db

`postcode-db` works because the binary format matches the problem:

```text
postcode string -> structured code -> bitmap/list presence -> packed coordinate
```

For roads, the equivalent shape is:

```text
coordinate -> snap index -> graph node/edge -> shortcut hierarchy -> node IDs
```

The transferable principles are:

1. Convert external identifiers into dense integer IDs.
2. Keep the hot runtime path string-free.
3. Quantize coordinates into integers.
4. Store offsets, counts, and packed arrays instead of nested objects.
5. Sort records so lookup can be direct, binary searched, or range scanned.
6. Separate hot sections from cold sections.
7. Make the binary file self-describing with a header and section directory.
8. Validate every record during build, then trust the packed structure at
   runtime.
9. Benchmark the packed client against a simple baseline.
10. Keep the file memory-mappable and dependency-light.

The main difference is that postcodes are exact key-value lookups, while road
routing is graph search. The process therefore needs precomputed graph layers,
not just a direct index.

## First-Principles Model

A road network is a weighted graph:

```text
node = road junction, road end, pseudo node, or roundabout node
edge = traversable road link between two nodes
weight = distance or estimated travel cost
```

The slow query-time operations are:

```text
1. Find the nearest useful road edge to each coordinate.
2. Search a graph with millions of nodes and edges.
3. Expand shortcut results back into ordered original graph node IDs.
```

The build process should move as much work as possible out of those three steps:

```text
1. Prebuild a spatial snap index.
2. Compress non-decision corridors.
3. Partition the graph into cells.
4. Precompute shortest paths between cell boundary nodes.
5. Build higher-level shortcut graphs.
6. Store unpack tables so shortcut routes can be converted back to node IDs.
```

The runtime query should touch the original full graph only near the origin and
destination, and should use precomputed shortcuts for the middle of longer
routes.

## Routing-Kernel Boundary

`dataset.rdb` is not the road information database. It is the fast path planner.

Keep this inside `dataset.rdb`:

```text
node coordinates needed for routing and snapping
adjacency arrays
weight arrays
snap index
shortcut hierarchy
shortcut-to-node unpack data
component IDs
small build/profile metadata
```

Keep this outside `dataset.rdb`, in a normal database or separate lookup store:

```text
source road_node.id UUIDs
source road_link.id UUIDs
road names
road numbers
road classifications for display
full road-link geometry
per-link display distances
turn-by-turn text
debug and provenance records
```

The builder should emit both artifacts from the same normalized ID assignment:

```text
dataset.rdb              # fast routing kernel
road_lookup.sqlite       # or another DB keyed by node_id_int / edge_id_int
```

Both files should carry the same `dataset_id` or build hash so a service cannot
accidentally use a route-node list from one build against a metadata database
from another build.

## Runtime Output Contract

The smallest useful output is:

```text
node_ids
```

The most robust small output is:

```text
node_ids
edge_ids
origin_snap_ref, optional
target_snap_ref, optional
cost, optional
```

Use `node_ids` as the public route shape. Keep `edge_ids` as an optional exact
join key for the companion lookup database. That keeps the binary focused on
path planning while avoiding ambiguity when two road links connect the same
nodes.

## Output Dataset Shape

`dataset.rdb` should be one binary file with a stable, versioned layout.

Recommended top-level structure:

```text
+-----------------------------+
| RDB header                  |
+-----------------------------+
| section directory           |
+-----------------------------+
| metadata                    |
| nodes                       |
| directed_edges              |
| adjacency_index             |
| weight_profiles             |
| snap_geometry               |
| snap_tiles                  |
| snap_tile_edges             |
| corridor_edges              |
| corridor_unpack             |
| cells                       |
| cell_membership             |
| cell_boundary_nodes         |
| shortcut_edges              |
| shortcut_unpack             |
| checksum                    |
+-----------------------------+
```

The search-hot runtime sections are:

```text
nodes
directed_edges
adjacency_index
weight_profiles
snap_geometry
snap_tiles
snap_tile_edges
corridor_edges
cells
cell_boundary_nodes
shortcut_edges
```

The post-search route reconstruction sections are:

```text
corridor_unpack
shortcut_unpack
```

They do not need to be touched during the graph search itself, but they are
required when shortcuts are enabled and the response must contain original
ordered node IDs.

Runtime queries should not need source UUIDs, road names, full route geometry, or
display metadata.

## Binary Format Rules

Use these rules unless benchmarking proves a better local choice:

```text
Endianness:       little-endian
Offsets:          absolute u64 file offsets in the section directory
Counts:           u32 where possible, u64 only for byte sizes and file offsets
Coordinates:      integer British National Grid offsets
Lengths:          integer decimetres or centimetres, chosen once per file
Weights:          profile-specific integer cost units
Strings:          dictionary encoded, never inline in hot records
UUIDs:            omitted from dataset.rdb; stored in companion lookup DB
Geometry:         minimal snap geometry only, not display geometry
Compression:      section-local, only where random access is preserved
```

Coordinates should stay in EPSG:27700 internally. Convert WGS84 longitude and
latitude to British National Grid only at API boundaries.

For compact coordinates, store a dataset origin in the header:

```text
origin_x_m = floor(min_x)
origin_y_m = floor(min_y)
```

Then store node coordinates as unsigned integer offsets from that origin:

```text
x_units = round((x_m - origin_x_m) * coordinate_scale)
y_units = round((y_m - origin_y_m) * coordinate_scale)
```

Start with decimetres:

```text
coordinate_scale = 10
```

This gives sub-metre snapping while keeping coordinates within 32-bit unsigned
integers for Great Britain.

## Runtime Record Sketches

The exact bit layout can evolve, but the logical records should stay close to
this.

### Node Record

```text
node_id:         implicit array index
x:               u32 coordinate units from dataset origin
y:               u32 coordinate units from dataset origin
first_edge:      u32 offset into directed_edges
edge_count:      u16
flags:           u16
rank:            u16
component_id:    u32
```

Node source UUIDs do not belong in the hot routing binary. They should be stored
in the companion lookup database:

```text
node_id_int -> source road_node.id
```

### Directed Edge Record

Store each traversable direction as its own edge. For the MVP, Open Roads links
are treated as bidirectional, so one source `road_link` usually becomes two
directed edges.

```text
edge_id:         implicit array index
to_node:         u32
reverse_edge:    u32
length_units:    u32
snap_geom_id:    u32
```

The source link ID and road name data belong in the companion lookup database:

```text
edge_id_int -> source road_link.id
```

### Routing Attribute Record

Avoid display attributes in `dataset.rdb`. If an attribute does not affect route
planning or shortcut construction, keep it in the external lookup database.

The routing binary may still need a small internal attribute record if a weight
profile depends on road class or flags:

```text
road_classification:        enum u8, optional
road_function:              enum u8, optional
primary_route:              bit, optional
trunk_road:                 bit, optional
reserved:                   bits
```

If all packed weights are precomputed, even these attributes can be omitted from
the runtime file after build.

### Weight Profile Section

Weights should be separate from topology so alternate static profiles can be
rebuilt without repacking geometry.

```text
profile_id
edge_weight_units[directed_edge_count]
corridor_weight_units[corridor_edge_count]
shortcut_weight_units[shortcut_edge_count]
```

Initial profiles:

```text
distance_m
estimated_time_s
```

`distance_m` is exact for the source `length` field after quantization.
`estimated_time_s` is derived from a documented speed table and is not
authoritative.

## Logical Layers

The dataset should contain multiple views of the same graph.

### L0: Canonical Graph

Purpose:

```text
Represent the original road network as compact integer arrays.
```

Contains:

```text
nodes
directed_edges
adjacency_index
weight_profiles
snap_geometry
```

L0 is the correctness source. Every optimized layer must be testable against
L0 Dijkstra for the same weight profile.

### L1: Snap Index

Purpose:

```text
Map an input coordinate to nearby candidate road edges quickly.
```

Use a fixed British National Grid tile index at first. A tile size between
128 m and 512 m is a reasonable starting point; choose by benchmark.

Store:

```text
tile_id -> contiguous range of edge IDs
```

Each edge should be assigned to every tile touched by its bounding box, with a
small expansion margin to avoid missing close candidates at tile borders.

Runtime snap flow:

```text
1. Convert input point to BNG.
2. Find tile.
3. Read candidate edge IDs for tile and neighbouring tiles.
4. Compute exact point-to-polyline distance for the candidate geometries.
5. Return the best N candidates, not just one.
```

Returning multiple candidates matters because the nearest geometry is not always
the best network connection.

### L2: Corridor Compression

Purpose:

```text
Collapse chains of nodes where no route decision can be made.
```

A typical chain:

```text
A -- n1 -- n2 -- n3 -- B
```

can be represented as:

```text
A ======= B
```

with unpack data:

```text
corridor_id -> [edge_1, edge_2, edge_3, edge_4]
```

Do not cross nodes where attributes indicate a meaningful decision or where
degree is not 2. Treat loop edges carefully and keep them in the base graph
until the corridor rules are proven.

This layer is the first optimization milestone because it is easier to validate
than full contraction hierarchies and should reduce node visits substantially.

### L3: Cell Overlay

Purpose:

```text
Precompute travel across local areas so long routes avoid repeated local search.
```

Start with grid cells in BNG metres:

```text
level 0: 1 km
level 1: 5 km
level 2: 25 km
```

For each cell:

```text
1. Identify member nodes.
2. Identify boundary nodes that connect to another cell.
3. Run bounded Dijkstra inside the cell from each boundary node.
4. Store shortest paths to other boundary nodes in the same cell.
5. Store unpack references to reconstruct the path.
```

The exact overlay version stores enough boundary-to-boundary shortcuts to match
raw Dijkstra for the chosen weight profile.

### L4: Skeleton Graph

Purpose:

```text
Provide a small high-importance graph for regional and national routes.
```

Use these signals:

```text
road_classification = Motorway or A Road
primary_route = 1
trunk_road = 1
high node degree
cell boundary role
sampled shortest-path frequency
```

Road class alone is not enough. Importance should mean "useful for many shortest
paths", not just "visually major".

The skeleton layer is a later milestone. Build exact corridor and cell overlays
first.

### Unpack Layer

Purpose:

```text
Convert shortcut paths back to ordered original graph node IDs.
```

The unpack layer should be recursive:

```text
shortcut edge -> child shortcuts or corridor paths
corridor path -> original directed edge IDs
directed edge IDs -> ordered node IDs
```

The returned node IDs should be dense `node_id_int` values. The external lookup
database can translate them to source node UUIDs, names, display geometries, or
other records if needed.

Internally, searches should track predecessor edge IDs, not just predecessor
nodes. The final node list can be derived from the edge path, and the optional
edge list can be returned when exact metadata enrichment is required.

## Build Pipeline

The builder should be deterministic: the same input file, build config, and
profile config should produce byte-identical output except for timestamps if
timestamps are included.

### Phase 1: Source Intake

Read:

```text
road_node(fid, geometry, id, form_of_road_node)
road_link(fid, geometry, id, attributes..., length, start_node, end_node)
motorway_junction(fid, geometry, id, junction_number)
```

Requirements:

```text
Use a GeoPackage-aware reader, or correctly strip the GeoPackageBinary header.
Read coordinates as EPSG:27700 metres.
Create temporary indexes or in-memory maps for id/start_node/end_node joins.
Stream rows where possible.
Avoid retaining raw SQLite row objects.
```

Outputs:

```text
raw_nodes.tmp
raw_links.tmp
raw_junctions.tmp
source_metadata.json
```

### Phase 2: Validation

Validate before packing:

```text
Every road_node.id is unique.
Every road_link.id is unique.
Every road_link.start_node exists.
Every road_link.end_node exists.
Every geometry CRS is EPSG:27700.
Every road_link.length is positive.
Every non-loop road_link geometry has at least two points.
The first and last geometry vertices are close to the declared start/end nodes.
Domain values match known enums or are captured as unknown enum values.
```

Validation should produce a report:

```text
build/validation.json
```

This report should count warnings separately from hard failures.

### Phase 3: ID Normalization

Assign dense integer IDs:

```text
road_node.id -> node_id_int
road_link.id -> link_id_int
directed edge IDs are generated during graph construction
```

Recommended order:

```text
Sort nodes by source fid or stable source id.
Sort links by source fid or stable source id.
```

The runtime should never compare UUID strings.

Outputs:

```text
node_id_map
link_id_map
```

These maps are used during packing and should also be written to the companion
lookup database. They should not be placed in `dataset.rdb` unless a debug build
explicitly asks for them.

### Phase 4: Coordinate And Geometry Encoding

Compute global bounds:

```text
min_x, min_y, max_x, max_y
```

Choose:

```text
coordinate_scale = 10       # decimetres
length_scale = 10           # decimetres
```

Encode node coordinates as `u32` offsets from the dataset origin.

Encode minimal snap geometries as:

```text
snap_geom_id
point_count
first point absolute x/y offset
subsequent points as zigzag varint dx/dy
```

Snap geometry is needed because the API accepts coordinates, not node IDs. It
should be just detailed enough to choose a good nearest road candidate. Full
display geometry belongs in the companion lookup database.

```text
snap_geometry: simplified enough for nearest-edge calculation
route_geometry: external lookup database only
```

### Phase 5: Canonical Graph Construction

For every source `road_link`:

```text
from = node_id_map[start_node]
to   = node_id_map[end_node]
```

For the MVP, create two directed edges for non-loop links:

```text
from -> to
to   -> from
```

For loop links, create a single self-directed edge and keep it out of early
compression rules:

```text
preserve the edge, but exclude it from simple corridor compression initially
```

Build adjacency in compressed sparse row form:

```text
adjacency_index[node_id]     = first outgoing directed edge
adjacency_index[node_id + 1] = end of outgoing directed edge range
directed_edges[first:end]    = outgoing edges
```

This makes neighbour iteration a tight array scan.

### Phase 6: Weight Profiles

The first profile must be exact distance:

```text
distance_weight = quantized road_link.length
```

The second profile can be estimated travel time:

```text
estimated_time = length_m / inferred_speed_mps
```

Example initial speed table:

```text
Motorway:                   100 km/h
A Road:                      80 km/h
B Road:                      60 km/h
Classified Unnumbered:       40 km/h
Unclassified:                35 km/h
Local Road function:         30 km/h
Restricted Local Access:     20 km/h
Unknown or Not Classified:   30 km/h
```

The exact values should live in a profile config file and be copied into
small `dataset.rdb` metadata. The source classification fields used to derive
the values do not need to remain in the runtime file unless runtime profile
switching requires them.

### Phase 7: Connected Components

Compute connected components on the canonical graph.

Store:

```text
component_id per node
component size
largest component id
```

At runtime, if origin and target snap candidates are in different components,
the query can fail quickly or try alternate snap candidates.

### Phase 8: Snap Index Build

Choose tile size:

```text
snap_tile_size_m = 256    # benchmark against 128 and 512
```

For each source link geometry:

```text
1. Compute bounding box.
2. Expand by snap_search_margin_m.
3. Assign edge/link to every touched tile.
4. Sort tile entries by tile_id.
5. Deduplicate entries.
```

Store:

```text
snap_tiles[tile_id] -> start/count in snap_tile_edges
snap_tile_edges[]   -> link or directed edge references
```

Prefer storing source link references for snap candidates, then map to the two
directed edges after the best side/endpoints are known.

### Phase 9: Corridor Compression

Build a corridor graph from L0.

A node is corridor-internal when:

```text
undirected_degree == 2
not a cell boundary
not a protected motorway-junction-adjacent node
not a roundabout boundary requiring special handling
not a loop-only node
not explicitly protected by debug config
```

`motorway_junction` points are not directly linked to `road_node`, so protection
requires a preprocessing step that snaps each junction point to nearby nodes or
links.

For each maximal chain:

```text
create corridor_edge
store total distance/profile cost
store endpoint nodes
store unpack list of original directed edges or node IDs
```

Validation:

```text
For random endpoint pairs, L0 shortest distance == corridor graph distance.
Every original directed edge appears in zero or one corridor unpack chain.
Protected edges remain queryable.
```

### Phase 10: Cell Partitioning

Start with geometric grid cells because they are simple, deterministic, and
match the coordinate system.

Initial levels:

```text
L0 cells: 1 km
L1 cells: 5 km
L2 cells: 25 km
```

For each node:

```text
cell_id(level, x, y)
```

For each edge:

```text
if from_cell != to_cell:
    mark both endpoint nodes as boundary nodes for that level
```

Later, replace or supplement grid cells with graph partitioning if grid cells
produce too many boundary nodes in dense urban areas.

### Phase 11: Cell Shortcut Precomputation

For every cell and every profile:

```text
1. Gather nodes and internal edges for the cell.
2. Gather boundary nodes.
3. For each boundary node, run Dijkstra constrained to the cell.
4. Store shortest paths to other boundary nodes.
5. Store predecessor/unpack data needed to recover ordered node IDs.
```

Control shortcut growth:

```text
Skip isolated or tiny cells.
Use upper limits and report cells with excessive boundary counts.
Benchmark complete boundary cliques before adding approximation.
```

The exact version should be implemented first. Approximate portal pruning can be
introduced only after exact correctness and size are measured.

### Phase 12: Higher-Level Overlay And Skeleton

Build overlay graphs from:

```text
cell shortcuts
inter-cell crossing edges
high-rank original/corridor edges
```

For a long route, the query should climb from local graph to overlay graph,
cross most of the distance using shortcuts, then descend near the target.

Skeleton ranking can use:

```text
road class score
primary route flag
trunk road flag
node degree
boundary node frequency
sampled betweenness from random route probes
```

This phase should be treated as an optimization layer, not as a requirement for
the first exact implementation.

### Phase 13: Pack dataset.rdb

Packing steps:

```text
1. Reserve header.
2. Reserve section directory.
3. Write each section aligned to 8 or 16 bytes.
4. Record section offset, byte length, item count, encoding, and checksum.
5. Write final directory.
6. Write final header.
7. Write whole-file checksum or footer checksum.
```

Header fields:

```text
magic:             "RDB1"
format_version:    u16
flags:             u16
header_size:        u32
section_count:      u32
file_size:          u64
source_fingerprint: bytes
build_config_hash:  bytes
dataset_id:         bytes
origin_x_m:         i32
origin_y_m:         i32
coordinate_scale:   u16
length_scale:       u16
node_count:         u32
directed_edge_count: u32
profile_count:      u16
reserved:           bytes
```

Every section should be independently checkable so corrupt or incompatible files
fail during open, not halfway through a query.

### Phase 14: Runtime Client

The runtime client should expose a small API:

```ts
RoadDb.open(buffer: ArrayBuffer | Buffer): RoadDb

db.snap(point: LonLat | BngPoint, options?): SnapCandidate[]

db.routeNodes(
  from: LonLat | BngPoint,
  to: LonLat | BngPoint,
  options?: RouteOptions
): RouteNodeResult
```

Open should:

```text
validate magic/version
validate section directory
validate checksums if enabled
create typed array/DataView readers
avoid materializing per-node or per-edge JS objects
```

Query should:

```text
1. Convert input points to BNG.
2. Snap each point to N candidate links.
3. Reject candidate pairs in different components.
4. If points are local, run bounded L0 or corridor search.
5. If points are distant, use local search -> overlay -> local search.
6. Unpack shortcuts into original ordered node IDs.
7. Return the node ID sequence, plus optional edge IDs, cost, and snap refs.
```

### Phase 15: Verification And Benchmarks

Build a repeatable benchmark suite before optimizing aggressively.

Correctness tests:

```text
Known node-to-node shortest paths against raw Dijkstra.
Random node pairs inside the same component.
Random coordinate pairs through snap + routeNodes.
Corridor graph distances against L0.
Cell shortcut distances against constrained L0.
Unpacked node path cost equals reported route cost.
All unpacked node paths connect end-to-end through known edges.
```

Performance tests:

```text
raw L0 Dijkstra
corridor graph only
cell overlay
cell overlay plus skeleton
route-node query
snap-only query
```

Metrics:

```text
dataset.rdb file size
hot section byte size
cold section byte size
build time
open time
median query time
p95 query time
p99 query time
visited node count
visited edge count
shortcut count
route distance error, if approximate mode is enabled
```

The benchmark output should be written as machine-readable JSON and a short
human-readable report.

## Query Strategies

### Route-Node Query

Route-node mode is the main product path:

```text
coordinate A
  -> snap candidates
  -> local/corridor entry nodes
  -> overlay shortcuts
  -> local/corridor exit nodes
  -> coordinate B
  -> ordered node IDs
```

It should not decode source UUIDs, road names, or full route geometry.

The result should use internal dense node IDs:

```text
node_ids = [u32, u32, u32, ...]
```

When exact enrichment is required, include edge IDs:

```text
edge_ids = [u32, u32, u32, ...]   # length = node_ids.length - 1
```

The companion lookup database is responsible for turning those IDs into richer
records.

### Minimal Snap Output

When A or B is located midway along a road link, a pure node list cannot describe
the exact fractional entry or exit point. There are two reasonable modes:

```text
strict_nodes:
    snap A and B to real graph nodes
    return only node_ids

node_path_with_snap_refs:
    return node_ids plus originSnap/targetSnap edge references and fractions
```

The second mode is still small, but it lets the external lookup database recover
the partial first and last road segments.

### Local Query Shortcut

If both snapped points are in the same small cell or nearby cells, the overlay
may be unnecessary. The runtime should support a local fast path:

```text
if same 1 km or 5 km neighbourhood:
    run bounded Dijkstra on L0/corridor graph
else:
    use overlay query
```

This avoids using national-level shortcuts for short urban routes.

## Memory Strategy

Design the file so deployments can choose between:

```text
load whole file into memory
memory-map the file
load search-hot sections and lazily read unpack sections
```

Hot-path memory should exclude:

```text
source UUIDs
road names
full-resolution geometry
debug metadata
validation reports
```

A server that only answers route-node queries should be able to run with a much
smaller resident set than a server that also enriches routes from a metadata
database.

## Suggested Project Milestones

### Milestone 0: Baseline Reader

Deliver:

```text
GeoPackage reader
GeoPackageBinary decoder
node/link validation report
raw Dijkstra over one bounded region
companion lookup database keyed by dense IDs
```

Success:

```text
Can route between dense node IDs in a small bbox.
Can compare route distance to a simple reference implementation.
Can translate route node IDs through the lookup database.
```

### Milestone 1: Minimal RDB

Deliver:

```text
dataset.rdb header
section directory
nodes
directed_edges
adjacency_index
distance profile
runtime client
```

Success:

```text
Can load dataset.rdb and return node-to-node route node IDs.
```

### Milestone 2: Coordinate Queries

Deliver:

```text
snap geometry encoding
snap tile index
coordinate-to-route-node query
```

Success:

```text
Can ask from lon/lat or BNG point to lon/lat or BNG point and receive node IDs.
```

### Milestone 3: Corridor Compression

Deliver:

```text
corridor graph
corridor-to-node unpack
benchmarks against L0
```

Success:

```text
Distance matches L0 for tested pairs.
Visited node count is materially lower than raw Dijkstra.
```

### Milestone 4: Cell Overlay

Deliver:

```text
1 km / 5 km / 25 km cells
boundary node detection
cell shortcuts
overlay query
```

Success:

```text
Longer routes avoid scanning large parts of the base graph.
Distance still matches L0 for exact profile tests.
```

### Milestone 5: Full GB Build

Deliver:

```text
complete GB dataset.rdb
build report
benchmark report
memory report
```

Success targets should be based on measured regional results, not guessed
upfront. Initial rough targets:

```text
open time:          under 1 second for memory-loaded hot sections
route-node median:  under 10 ms for regional queries
route-node p95:     under 50 ms for regional queries
lookup enrichment:  measured separately outside dataset.rdb
```

### Milestone 6: Optional Approximation

Only after exact routing works:

```text
portal pruning
skeleton-only long-distance mode
profile customization
CH-lite experiments
```

Success:

```text
Error is measured, bounded, and explicit.
File size and query time improve enough to justify the approximation.
```

## Open Design Decisions

These should be answered by prototypes and benchmarks:

```text
Coordinate precision: decimetres vs centimetres.
Snap tile size: 128 m vs 256 m vs 512 m.
How simplified snap geometry can be before snapping quality suffers.
Whether cell partitioning should remain grid-based or use graph partitioning.
Whether estimated-time weights should be packed with the first release.
Whether strict node-only output is enough or snap refs are required.
Whether all shortcut-to-node unpack data should be bundled or emitted as an optional file.
Whether the runtime should support memory mapping directly in the target language.
```

## Recommended First Implementation Path

Start smaller than Great Britain.

Use one bounded region from the GeoPackage, then implement in this order:

```text
1. Decode nodes and links.
2. Normalize UUIDs to integer IDs.
3. Build the companion lookup database keyed by dense IDs.
4. Build bidirectional adjacency arrays.
5. Pack a minimal dataset.rdb.
6. Load it and return node-to-node route node IDs.
7. Add snap tiles and coordinate route-node queries.
8. Add corridor compression.
9. Add cell overlays.
10. Expand to larger regions.
11. Build and benchmark all of Great Britain.
```

This sequence keeps correctness visible. Each optimization layer must prove that
it gives the same answer as the layer below it before it becomes part of the
default query path.
