## Project idea: **Open Roads Fast Lookup Pyramid**

Build a small routing-index project that takes an OS Open Roads `.gpkg` file and produces a derived, pre-calculated, multi-layer dataset for very fast network-distance lookup.

The spirit is exactly the transcript’s idea: do the expensive work before the user asks, store shortcuts / layers, then make each lookup touch only a small part of the graph rather than the whole road network. The transcript describes this as pre-processing, shortcut construction, optional reweighting, and tiny live searches instead of brute-force Dijkstra over the full graph. 

For OS Open Roads, this is a good fit because the dataset is already a topologically structured link-and-node road network for Great Britain, available as GeoPackage, with RoadLinks and RoadNodes rather than just disconnected line drawings. ([OS Docs][1])

---

## Important scope decision

I would design this as a **fast shortest-distance / approximate travel-time lookup engine**, not a production navigation engine.

OS Open Roads gives you useful network structure: RoadLinks have `start_node`, `end_node`, `length`, road classification, road function, road name fields, `primary_route`, and `trunk_road`. ([OS Docs][2]) RoadNodes represent link endpoints or connectivity points. ([OS Docs][3])

But Open Roads is not the fully routable OS product with live-style restrictions, turn costs, speed limits, hazards, width/height restrictions, and similar routing details. OS’s more detailed Routing and Asset Management Information product is the one described as fully routable and including restrictions, average speeds, indicative speeds, hazards, and turn/time/height/weight/width stipulations. ([Ordnance Survey][4])

So the MVP should answer:

> “Given two points or two road nodes, return the shortest road-network distance, optionally with a simple estimated travel time based on road class.”

That keeps the project principled and achievable.

---

# First-principles design

The core question is:

> What can be precomputed because it rarely changes, and what must remain query-time?

For Open Roads:

**Mostly stable:**

* node/link topology
* geometry
* road classifications
* node-to-node connectivity
* spatial location of links
* multi-level road/cell hierarchy

**Potentially changeable:**

* edge weights
* speed assumptions
* avoid-road-class preferences
* temporary penalties
* user-specific profiles

So the project should separate:

1. **Topology index** — built rarely.
2. **Weight/customization layer** — rebuilt cheaply when assumptions change.
3. **Query layer** — runs extremely fast.

That mirrors the three-stage idea from the transcript: heavy pre-processing, cheaper reweighting/customisation, and tiny live query. 

---

# Recommended architecture

## Input

Original file:

```text
open_roads.gpkg
```

Expected layers:

```text
RoadLink
RoadNode
MotorwayJunction
```

OS documents GeoPackage as an OGC standard, single-file, platform-independent format that can contain large and complex GIS data. ([OS Docs][5])

---

## Output

Create a new derived dataset:

```text
open_roads_fast_index.gpkg
```

or:

```text
open_roads_fast_index.sqlite
open_roads_fast_index.bin
```

The cleanest small-project output is probably a **SQLite / GeoPackage-derived file** with extra tables. GeoPackage itself is SQLite-based, so this keeps the project inspectable.

---

# The multi-layer dataset

## Layer 0 — raw graph layer

This is the direct transformation of RoadLinks and RoadNodes into graph tables.

```text
nodes
-----
node_id_int
source_node_id
x
y
degree
cell_l1
cell_l2
cell_l3

edges
-----
edge_id_int
source_link_id
from_node
to_node
length_m
road_classification
road_function
primary_route
trunk_road
base_cost_m
base_cost_s
reverse_edge_id
geometry_ref
```

Principle:

> Convert geographic objects into a compact integer graph.

Do not run queries directly on string GUIDs and geometries. Convert all node and edge IDs into dense integers. That makes Dijkstra/A*/overlay queries much faster.

---

## Layer 1 — spatial snap layer

Before routing, a user usually gives coordinates, not node IDs.

So build a lookup layer:

```text
edge_spatial_index
------------------
edge_id_int
minx
miny
maxx
maxy

snap_candidates
---------------
tile_id
edge_id_int
```

Use this to answer:

```text
nearest road link to point
nearest graph node to point
nearest N candidate links
```

Principle:

> The first lookup should be spatial, not graph-based.

For ultra-fast lookup, the system should not scan every RoadLink to find the nearest segment. Use an R-tree or tile index.

---

## Layer 2 — corridor compression layer

Many road graphs contain chains of degree-2 nodes:

```text
A -- n1 -- n2 -- n3 -- B
```

For many lookup tasks, this can become:

```text
A ======= B
```

with a stored unpacking path:

```text
shortcut A-B = [A-n1, n1-n2, n2-n3, n3-B]
```

Derived table:

```text
corridor_edges
--------------
corridor_id
from_node
to_node
total_length_m
total_cost_s
edge_count
first_edge
last_edge

corridor_unpack
---------------
corridor_id
position
child_edge_id
```

Principle:

> Collapse detail that cannot affect a decision.

A degree-2 chain has no branch choice inside it. The query algorithm does not need to “think” at every intermediate point. It only needs to know the accumulated cost and how to unpack the final geometry.

This is the easiest useful shortcut layer to implement.

---

## Layer 3 — cell / tile overlay layer

Partition the graph into cells, for example:

```text
Level 1: 1 km grid cells
Level 2: 5 km grid cells
Level 3: 25 km grid cells
```

For each cell, identify **boundary nodes**: nodes connected to edges that leave the cell.

Then precompute shortest paths between boundary nodes inside the cell.

```text
cells
-----
level
cell_id
parent_cell_id
bbox

cell_nodes
----------
level
cell_id
node_id

cell_boundary_nodes
-------------------
level
cell_id
node_id

cell_shortcuts
--------------
level
cell_id
from_boundary_node
to_boundary_node
cost_m
cost_s
unpack_ref
```

Principle:

> Long routes should not repeatedly solve local neighbourhoods.

For example, suppose a route goes from Bristol to Cambridge. The live query should not examine every local road inside every city and village along the way. It should:

1. search locally near Bristol,
2. jump through precomputed cell shortcuts,
3. search locally near Cambridge.

This is a small-project version of the “pyramid” idea from the transcript.

This also resembles **Customizable Route Planning**, where a graph is partitioned into multiple levels, overlay graphs are built, and shortcut lengths are computed during customization. ([Microsoft][6])

---

## Layer 4 — high-importance skeleton layer

Create a top-level graph containing the most important roads and connectors.

Use OS attributes as hints:

```text
trunk_road = true
primary_route = true
road_classification in ('Motorway', 'A Road')
```

But do not treat this as a perfect ranking. Road class is not the same thing as graph importance.

A better ranking formula:

```text
importance =
    road_class_score
  + primary_route_score
  + trunk_road_score
  + node_degree_score
  + cell_boundary_score
  + sampled_betweenness_score
```

Derived table:

```text
node_rank
---------
node_id
rank
importance_score
reason
```

Principle:

> “Important” means useful for many shortest paths, not merely visually major.

The transcript’s highway-pyramid intuition is right, but the engineering version should be based on connectivity and shortcut usefulness, not just road labels. 

---

# Query flow

A lookup from point A to point B should work like this.

## Step 1 — snap

```text
origin point  -> nearest road edge(s)
target point  -> nearest road edge(s)
```

Return a few candidates, not just one, because the nearest geometry is not always the best network connection.

## Step 2 — local search

Search only within the origin’s local cell to reach boundary nodes.

```text
origin candidate nodes -> local boundary nodes
```

## Step 3 — overlay search

Search the higher-level shortcut graph.

```text
origin boundary -> cell shortcuts -> high-level skeleton -> target boundary
```

## Step 4 — local descent

Search inside the destination cell.

```text
target boundary nodes -> target candidate nodes
```

## Step 5 — unpack

If the user wants the actual route geometry, unpack:

```text
top shortcut
  -> cell shortcut
    -> corridor shortcut
      -> original RoadLinks
```

This means you can have two query modes:

```text
fast_cost_only(origin, target)
```

returns just distance/time.

```text
full_route(origin, target)
```

returns distance/time plus geometry.

The cost-only query can be extremely fast because it never reconstructs every geometry segment.

---

# Suggested derived schema

```text
metadata
--------
index_version
source_product
source_date
build_time
weight_profile
notes

nodes
-----
node_id_int
source_node_id
x
y
degree
rank
cell_l1
cell_l2
cell_l3

edges
-----
edge_id_int
source_link_id
from_node
to_node
length_m
cost_m
cost_s
road_classification
road_function
primary_route
trunk_road
geom_id

edge_geometries
---------------
geom_id
geometry

corridor_edges
--------------
corridor_id
from_node
to_node
cost_m
cost_s
edge_count

corridor_unpack
---------------
corridor_id
position
edge_id_int

cells
-----
level
cell_id
parent_cell_id
minx
miny
maxx
maxy

cell_boundary_nodes
-------------------
level
cell_id
node_id

cell_shortcuts
--------------
shortcut_id
level
cell_id
from_node
to_node
cost_m
cost_s
unpack_type
unpack_ref

shortcut_unpack
---------------
shortcut_id
position
child_type
child_id

snap_index
----------
tile_id
edge_id_int
minx
miny
maxx
maxy
```

---

# Build pipeline

## Phase 1 — ingest

Read `RoadLink` and `RoadNode`.

Validate that every RoadLink has a start and end node. RoadLink has `start_node` and `end_node` in GeoPackage form, and the feature docs define these as the node coincident with the first and last vertex of the geometry. ([OS Docs][2])

## Phase 2 — normalize

Convert source IDs to integer IDs.

```text
GUID -> int
```

Store the mapping.

## Phase 3 — build graph

For every RoadLink:

```text
edge from start_node to end_node
edge from end_node to start_node
```

Because Open Roads does not provide one-way restrictions in the basic RoadLink schema, the MVP should treat links as bidirectional unless you add another data source.

## Phase 4 — assign weights

Distance-only profile:

```text
cost_m = length_m
```

Approximate time profile:

```text
cost_s = length_m / inferred_speed_mps
```

Example speed profile:

```text
Motorway: 100 km/h
A Road:    80 km/h
B Road:    60 km/h
Minor:     40 km/h
Local:     30 km/h
```

Label this clearly as **estimated**, not authoritative.

## Phase 5 — create corridor shortcuts

Collapse non-branching chains.

This gives you a first measurable speedup without needing full contraction hierarchies.

## Phase 6 — partition into cells

Start simple:

```text
1 km grid
5 km grid
25 km grid
```

Later, replace this with graph partitioning if needed.

## Phase 7 — precompute cell shortcuts

For each cell:

```text
for each boundary node B:
    run Dijkstra within the cell
    store shortest paths from B to other boundary nodes
```

## Phase 8 — build top-level skeleton

Use boundary nodes, trunk roads, primary routes, high-degree nodes, and important connectors.

## Phase 9 — benchmark

Compare:

```text
vanilla Dijkstra on raw graph
corridor graph only
cell overlay graph
cell overlay + skeleton
```

Measure:

```text
median query time
p95 query time
number of nodes visited
index size
build time
route distance error, if approximate
```

---

# Exact vs approximate design choice

You should decide this early.

## Exact version

Use complete cell-boundary shortcuts.

Pros:

* results match raw Dijkstra for the chosen weight profile
* easier to test correctness
* more intellectually satisfying

Cons:

* bigger shortcut tables
* more preprocessing

## Approximate version

Only store selected portal shortcuts and prefer high-importance roads.

Pros:

* smaller index
* faster
* more like a practical “lookup” system

Cons:

* route may not be exactly shortest
* you need an error metric

For a small project, I would build the **exact distance version first**, then add approximate time profiles later.

---

# Why I would not start with full contraction hierarchies

Classic contraction hierarchies are powerful: nodes are ordered by importance, low-importance nodes are contracted, and shortcuts are added so shortest paths are preserved; queries then run over the hierarchy rather than the full graph. ([ae.iti.kit.edu][7])

But full CH has tricky implementation details:

* choosing a good contraction order,
* witness searches,
* shortcut explosion,
* unpacking paths,
* handling changed weights,
* avoiding memory blow-up.

For a small project, a **multi-level cell overlay** is more transparent. It still teaches the same core lesson from the transcript: build an expensive road-network index once, then answer lookups by touching a much smaller graph. 

A good stretch goal would be to implement a “CH-lite” version after the cell overlay works.

---

# MVP deliverable

I would define the project as:

> Build a derived Open Roads lookup index for one region of Great Britain that can answer shortest-network-distance queries between two coordinates faster than raw Dijkstra, using precomputed corridor and cell-level shortcuts.

## MVP area

Pick a bounded region first, for example:

```text
Greater London
Bristol + surrounding area
West Midlands
one county
```

Avoid starting with all of Great Britain.

## MVP commands

```bash
openroads-index build \
  --input open_roads.gpkg \
  --bbox xmin,ymin,xmax,ymax \
  --levels 1000,5000,25000 \
  --profile distance \
  --output open_roads_fast_index.gpkg
```

```bash
openroads-index query \
  --index open_roads_fast_index.gpkg \
  --from lon,lat \
  --to lon,lat \
  --mode cost-only
```

```bash
openroads-index query \
  --index open_roads_fast_index.gpkg \
  --from lon,lat \
  --to lon,lat \
  --mode full-route
```

---

# Success criteria

A good small-project target:

```text
Input size:       one city/county
Build time:       under 30 minutes
Cost-only query:  under 10 ms median
Full route query: under 100 ms median
Correctness:      exact match to raw Dijkstra for distance profile
Benchmark:        at least 10x fewer visited nodes than raw Dijkstra
```

For the full GB dataset, the targets can be relaxed while you learn.

---

# The clean conceptual model

Think of the final dataset as four nested views of the same road network:

```text
L0: every original road link
L1: compressed corridors
L2: local cell shortcuts
L3: regional / national skeleton
```

The query should climb:

```text
origin -> local graph -> cell overlay -> skeleton
```

then descend:

```text
skeleton -> cell overlay -> local graph -> destination
```

That is the essence of the transcript’s “pay the cost in the dark, then answer instantly” idea, applied to OS Open Roads in a small, buildable way.

[1]: https://docs.os.uk/os-downloads/products/transport-network-portfolio/os-open-roads/os-open-roads-technical-specification "OS Open Roads Technical Specification | OS Download Products' Documentation"
[2]: https://docs.os.uk/os-downloads/products/transport-network-portfolio/os-open-roads/os-open-roads-technical-specification/geopackage-overview/roadlink "RoadLink | OS Download Products' Documentation"
[3]: https://docs.os.uk/os-downloads/products/transport-network-portfolio/os-open-roads/os-open-roads-technical-specification/feature-types/roadnode "RoadNode | OS Download Products' Documentation"
[4]: https://www.ordnancesurvey.co.uk/products/os-routing-asset-management-information "OS Routing and Asset Management Information | Data Products | OS"
[5]: https://docs.os.uk/os-downloads/products/transport-network-portfolio/os-open-roads/os-open-roads-technical-specification/geopackage-overview "GeoPackage overview | OS Download Products' Documentation"
[6]: https://www.microsoft.com/en-us/research/wp-content/uploads/2013/01/crp_web_130724.pdf?utm_source=chatgpt.com "Customizable Route Planning in Road Networks?"
[7]: https://ae.iti.kit.edu/1640.php?utm_source=chatgpt.com "Faster and Simpler Hierarchical Routing in Road Networks"
