from __future__ import annotations

import heapq
import math
import sqlite3
from array import array
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from .gpkg import LineString, Point, decode_gpkg_geometry, iter_links, iter_nodes


DEFAULT_TILE_SIZE_M = 256.0
DEFAULT_SNAP_RADIUS_M = 250.0
UINT32_NONE = 0xFFFFFFFF
MAX_COST_UNITS = 0xFFFFFFFFFFFFFFFF


@dataclass(slots=True)
class SnapGeometry:
    points: tuple[Point, ...]
    start_node: int
    end_node: int
    forward_edge: int
    reverse_edge: int | None
    length_m: float
    bbox: tuple[float, float, float, float]


@dataclass(slots=True)
class RoadGraph:
    origin_x_m: int
    origin_y_m: int
    coord_scale: int
    length_scale: int
    node_x: list[int]
    node_y: list[int]
    edge_from: list[int]
    edge_to: list[int]
    edge_length: list[int]
    reverse_edge: list[int]
    edge_geom: list[int]
    offsets: list[int]
    snap_geometries: list[SnapGeometry]
    snap_tile_size_m: float
    snap_tiles: dict[tuple[int, int], list[int]]
    source_node_ids: list[str] = field(default_factory=list)
    source_link_ids: list[str] = field(default_factory=list)
    edge_source_link: list[int] = field(default_factory=list)
    component_ids: list[int] = field(default_factory=list)
    component_node_counts: list[int] = field(default_factory=list)
    component_edge_counts: list[int] = field(default_factory=list)
    component_bboxes: list[tuple[int, int, int, int]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.component_ids:
            (
                self.component_ids,
                self.component_node_counts,
                self.component_edge_counts,
                self.component_bboxes,
            ) = _compute_components(
                self.node_count,
                self.node_x,
                self.node_y,
                self.offsets,
                self.edge_to,
            )

    @property
    def node_count(self) -> int:
        return len(self.node_x)

    @property
    def edge_count(self) -> int:
        return len(self.edge_to)

    def node_xy(self, node_id: int) -> tuple[float, float]:
        return (
            self.origin_x_m + self.node_x[node_id] / self.coord_scale,
            self.origin_y_m + self.node_y[node_id] / self.coord_scale,
        )

    def node_units(self, node_id: int) -> tuple[int, int]:
        return self.node_x[node_id], self.node_y[node_id]

    def outgoing_edges(self, node_id: int) -> range:
        return range(self.offsets[node_id], self.offsets[node_id + 1])

    def bounds(self) -> tuple[float, float, float, float]:
        xs = [self.origin_x_m + value / self.coord_scale for value in self.node_x]
        ys = [self.origin_y_m + value / self.coord_scale for value in self.node_y]
        return min(xs), min(ys), max(xs), max(ys)

    def edge_target_and_length_m(self, edge_id: int) -> tuple[int, float]:
        return self.edge_to[edge_id], self.edge_length[edge_id] / self.length_scale

    def edge_target_and_length_units(self, edge_id: int) -> tuple[int, int]:
        return self.edge_to[edge_id], self.edge_length[edge_id]

    def edge_geom_id(self, edge_id: int) -> int:
        return self.edge_geom[edge_id]

    def component_id(self, node_id: int) -> int:
        return self.component_ids[node_id] if self.component_ids else 0

    @property
    def component_count(self) -> int:
        return len(self.component_node_counts) if self.component_node_counts else 1

    @property
    def has_components(self) -> bool:
        return bool(self.component_ids)

    def tile_geom_ids(self, tile_x: int, tile_y: int) -> Iterable[int]:
        return self.snap_tiles.get((tile_x, tile_y), ())

    def snap_distance(self, geom_id: int, x: float, y: float) -> tuple[float, float, float, float]:
        return _point_to_polyline(x, y, self.snap_geometries[geom_id].points)

    def snap_geom_may_intersect_radius(self, geom_id: int, x: float, y: float, radius_m: float) -> bool:
        min_x, min_y, max_x, max_y = self.snap_geometries[geom_id].bbox
        return not (
            x < min_x - radius_m
            or x > max_x + radius_m
            or y < min_y - radius_m
            or y > max_y + radius_m
        )

    def snap_endpoint_costs(self, geom_id: int, snap: SnapCandidate) -> tuple[tuple[int, float], ...]:
        geom = self.snap_geometries[geom_id]
        return _snap_endpoint_costs(geom.start_node, geom.end_node, geom.length_m, snap)

    def snap_endpoint_cost_units(self, geom_id: int, snap: SnapCandidate) -> tuple[tuple[int, int], ...]:
        geom = self.snap_geometries[geom_id]
        length_units = round(geom.length_m * self.length_scale)
        return _snap_endpoint_costs_units(geom.start_node, geom.end_node, length_units, self.length_scale, snap)


@dataclass(slots=True)
class SnapCandidate:
    geom_id: int
    distance_m: float
    along_m: float
    projected_x: float
    projected_y: float

    @property
    def fraction(self) -> float:
        geom_len = getattr(self, "_geom_len", None)
        if not geom_len:
            return 0.0
        return min(1.0, max(0.0, self.along_m / geom_len))


@dataclass(slots=True)
class RouteResult:
    node_ids: list[int]
    edge_ids: list[int]
    cost_m: float
    origin_snap: SnapCandidate
    target_snap: SnapCandidate


class QueryWorkspace:
    """Reusable per-thread route query state.

    The workspace still owns full-size arrays, but they are allocated once and
    only nodes touched by the previous query are reset. Distances are stored in
    integer length units so the search loop avoids per-edge metre conversion.
    """

    def __init__(self, node_count: int) -> None:
        self.node_count = node_count
        self.dist = array("Q", [MAX_COST_UNITS]) * node_count
        self.prev_node = array("i", [-1]) * node_count
        self.prev_edge = array("i", [-1]) * node_count
        self.settled = array("b", [0]) * node_count
        self.touched: list[int] = []
        self.heap: list[tuple[float, float, int]] = []

    @classmethod
    def for_graph(cls, graph: RoadGraph) -> "QueryWorkspace":
        return cls(graph.node_count)

    def reset(self) -> None:
        for node_id in self.touched:
            self.dist[node_id] = MAX_COST_UNITS
            self.prev_node[node_id] = -1
            self.prev_edge[node_id] = -1
            self.settled[node_id] = 0
        self.touched.clear()
        self.heap.clear()

    def set_forward(self, node_id: int, cost: int, prev_node: int = -1, prev_edge: int = -1) -> None:
        if self.dist[node_id] == MAX_COST_UNITS:
            self.touched.append(node_id)
        self.dist[node_id] = cost
        self.prev_node[node_id] = prev_node
        self.prev_edge[node_id] = prev_edge


def parse_bbox(text: str) -> tuple[float, float, float, float]:
    parts = [float(part.strip()) for part in text.split(",")]
    if len(parts) != 4:
        raise ValueError("bbox must be minx,miny,maxx,maxy")
    minx, miny, maxx, maxy = parts
    if minx >= maxx or miny >= maxy:
        raise ValueError("bbox min values must be lower than max values")
    return minx, miny, maxx, maxy


def parse_point(text: str) -> tuple[float, float]:
    parts = [float(part.strip()) for part in text.split(",")]
    if len(parts) != 2:
        raise ValueError("point must be x,y in EPSG:27700 metres")
    return parts[0], parts[1]


def build_graph_from_gpkg(
    gpkg_path: str,
    *,
    bbox: tuple[float, float, float, float] | None,
    link_limit: int | None = None,
    tile_size_m: float = DEFAULT_TILE_SIZE_M,
    coord_scale: int = 10,
    length_scale: int = 10,
    keep_source_ids: bool = True,
) -> RoadGraph:
    """Build a compact in-memory graph from links intersecting a BNG bbox.

    When ``bbox`` is ``None`` this builds from the whole GeoPackage. The whole
    build uses two link scans to avoid retaining millions of raw link dicts.
    """

    source = Path(gpkg_path)
    if not source.is_file():
        raise FileNotFoundError(f"GeoPackage not found: {source}")
    conn = sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        if bbox is None:
            source_nodes = {
                row["id"]: (int(row["fid"]), float(row["x"]), float(row["y"]), row["form"])
                for row in iter_nodes(conn, bbox=None)
            }
            needed_node_ids: set[str] | None = None
        else:
            needed_node_ids = set()
            link_count = 0
            for link in iter_links(conn, bbox=bbox, limit=link_limit):
                needed_node_ids.add(link["start_node"])
                needed_node_ids.add(link["end_node"])
                link_count += 1
            if link_count == 0:
                raise ValueError("bbox did not select any road_link records")
            source_nodes = _fetch_nodes_by_source_id(conn, needed_node_ids)

        graph = _build_graph_from_connection(
            conn,
            source_nodes=source_nodes,
            needed_node_ids=needed_node_ids,
            bbox=bbox,
            link_limit=link_limit,
            tile_size_m=tile_size_m,
            coord_scale=coord_scale,
            length_scale=length_scale,
            keep_source_ids=keep_source_ids,
        )
    finally:
        conn.close()
    return graph


def _build_graph_from_connection(
    conn: sqlite3.Connection,
    *,
    source_nodes: dict[str, tuple[int, float, float, str]],
    needed_node_ids: set[str] | None,
    bbox: tuple[float, float, float, float] | None,
    link_limit: int | None,
    tile_size_m: float,
    coord_scale: int,
    length_scale: int,
    keep_source_ids: bool,
) -> RoadGraph:
    if not source_nodes:
        raise ValueError("no source nodes were loaded")

    if needed_node_ids is not None and (missing := needed_node_ids.difference(source_nodes)):
        sample = ", ".join(sorted(missing)[:5])
        raise ValueError(f"{len(missing)} link endpoint nodes were not found: {sample}")

    ordered_source_nodes = sorted(source_nodes.items(), key=lambda item: item[1][0])
    node_id_by_source = {source_id: idx for idx, (source_id, _row) in enumerate(ordered_source_nodes)}
    source_node_ids = [source_id for source_id, _row in ordered_source_nodes] if keep_source_ids else []

    min_x = min(row[1] for _source_id, row in ordered_source_nodes)
    min_y = min(row[2] for _source_id, row in ordered_source_nodes)
    node_count = len(ordered_source_nodes)

    source_link_ids: list[str] = [] if keep_source_ids else []
    old_from = array("I")
    old_to = array("I")
    old_length = array("I")
    old_reverse = array("I")
    old_geom = array("I")
    old_source_link = array("I")
    snap_geoms_pending: list[tuple[LineString, int, int, float, int, int | None]] = []

    for link in iter_links(conn, bbox=bbox, limit=link_limit):
        geom = link["geometry"]
        if not isinstance(geom, LineString) or len(geom.points) < 2:
            continue
        geom_min_x, geom_min_y, _geom_max_x, _geom_max_y = _points_bbox(geom.points)
        min_x = min(min_x, geom_min_x)
        min_y = min(min_y, geom_min_y)

        start_node = node_id_by_source[link["start_node"]]
        end_node = node_id_by_source[link["end_node"]]
        length_m = float(link["length"])
        if keep_source_ids:
            source_link_ids.append(link["id"])
            source_link_idx = len(source_link_ids) - 1
        else:
            source_link_idx = 0xFFFFFFFF

        if start_node == end_node:
            edge_idx = len(old_from)
            _append_edge(
                old_from,
                old_to,
                old_length,
                old_reverse,
                old_geom,
                old_source_link,
                start_node,
                end_node,
                round(length_m * length_scale),
                edge_idx,
                len(snap_geoms_pending),
                source_link_idx,
            )
            snap_geoms_pending.append((geom, start_node, end_node, length_m, edge_idx, None))
            continue

        forward_idx = len(old_from)
        reverse_idx = forward_idx + 1
        geom_id = len(snap_geoms_pending)
        length_units = round(length_m * length_scale)
        _append_edge(
            old_from,
            old_to,
            old_length,
            old_reverse,
            old_geom,
            old_source_link,
            start_node,
            end_node,
            length_units,
            reverse_idx,
            geom_id,
            source_link_idx,
        )
        _append_edge(
            old_from,
            old_to,
            old_length,
            old_reverse,
            old_geom,
            old_source_link,
            end_node,
            start_node,
            length_units,
            forward_idx,
            geom_id,
            source_link_idx,
        )
        snap_geoms_pending.append((geom, start_node, end_node, length_m, forward_idx, reverse_idx))

    if not old_from:
        raise ValueError("no routable road_link geometries were loaded")

    origin_x_m = math.floor(min_x)
    origin_y_m = math.floor(min_y)
    node_x = array("I", (round((row[1] - origin_x_m) * coord_scale) for _source_id, row in ordered_source_nodes))
    node_y = array("I", (round((row[2] - origin_y_m) * coord_scale) for _source_id, row in ordered_source_nodes))

    edge_count = len(old_from)
    offsets = array("I", [0]) * (node_count + 1)
    for from_node in old_from:
        offsets[from_node + 1] += 1
    for idx in range(1, len(offsets)):
        offsets[idx] += offsets[idx - 1]

    cursor = array("I", offsets)
    old_to_new = array("I", [0]) * edge_count
    edge_from = array("I", [0]) * edge_count
    edge_to = array("I", [0]) * edge_count
    edge_length = array("I", [0]) * edge_count
    reverse_old_order = array("I", [0]) * edge_count
    reverse_edge = array("I", [0]) * edge_count
    edge_geom = array("I", [0]) * edge_count
    edge_source_link = array("I", [0]) * edge_count

    for old_idx, from_node in enumerate(old_from):
        new_idx = cursor[from_node]
        cursor[from_node] += 1
        old_to_new[old_idx] = new_idx
        edge_from[new_idx] = from_node
        edge_to[new_idx] = old_to[old_idx]
        edge_length[new_idx] = old_length[old_idx]
        reverse_old_order[new_idx] = old_reverse[old_idx]
        edge_geom[new_idx] = old_geom[old_idx]
        edge_source_link[new_idx] = old_source_link[old_idx]

    for new_idx, reverse_old_idx in enumerate(reverse_old_order):
        reverse_edge[new_idx] = old_to_new[reverse_old_idx]

    snap_geometries: list[SnapGeometry] = []
    for geom, start_node, end_node, length_m, forward_old, reverse_old in snap_geoms_pending:
        snap_geometries.append(
            SnapGeometry(
                points=geom.points,
                start_node=start_node,
                end_node=end_node,
                forward_edge=old_to_new[forward_old],
                reverse_edge=old_to_new[reverse_old] if reverse_old is not None else None,
                length_m=length_m,
                bbox=_points_bbox(geom.points),
            )
        )

    graph = RoadGraph(
        origin_x_m=origin_x_m,
        origin_y_m=origin_y_m,
        coord_scale=coord_scale,
        length_scale=length_scale,
        node_x=node_x,
        node_y=node_y,
        edge_from=edge_from,
        edge_to=edge_to,
        edge_length=edge_length,
        reverse_edge=reverse_edge,
        edge_geom=edge_geom,
        offsets=offsets,
        snap_geometries=snap_geometries,
        snap_tile_size_m=tile_size_m,
        snap_tiles={},
        source_node_ids=source_node_ids,
        source_link_ids=source_link_ids,
        edge_source_link=edge_source_link,
    )
    graph.snap_tiles = build_snap_tiles(graph)
    return graph


def _compute_components(
    node_count: int,
    node_x: list[int],
    node_y: list[int],
    offsets: list[int],
    edge_to: list[int],
) -> tuple[array, array, array, list[tuple[int, int, int, int]]]:
    component_ids = array("I", [UINT32_NONE]) * node_count
    component_node_counts = array("I")
    component_edge_counts = array("I")
    component_bboxes: list[tuple[int, int, int, int]] = []

    component_id = 0
    for start_node in range(node_count):
        if component_ids[start_node] != UINT32_NONE:
            continue

        stack = [start_node]
        component_ids[start_node] = component_id
        node_total = 0
        edge_total = 0
        min_x = min_y = UINT32_NONE
        max_x = max_y = 0

        while stack:
            node_id = stack.pop()
            node_total += 1
            edge_total += offsets[node_id + 1] - offsets[node_id]
            x = node_x[node_id]
            y = node_y[node_id]
            min_x = min(min_x, x)
            min_y = min(min_y, y)
            max_x = max(max_x, x)
            max_y = max(max_y, y)

            for edge_id in range(offsets[node_id], offsets[node_id + 1]):
                next_node = edge_to[edge_id]
                if component_ids[next_node] == UINT32_NONE:
                    component_ids[next_node] = component_id
                    stack.append(next_node)

        component_node_counts.append(node_total)
        component_edge_counts.append(edge_total)
        component_bboxes.append((min_x, min_y, max_x, max_y))
        component_id += 1

    return component_ids, component_node_counts, component_edge_counts, component_bboxes


def _append_edge(
    edge_from: array,
    edge_to: array,
    edge_length: array,
    reverse_edge: array,
    edge_geom: array,
    edge_source_link: array,
    from_node: int,
    to_node: int,
    length_units: int,
    reverse: int,
    geom_id: int,
    source_link_idx: int,
) -> None:
    edge_from.append(from_node)
    edge_to.append(to_node)
    edge_length.append(length_units)
    reverse_edge.append(reverse)
    edge_geom.append(geom_id)
    edge_source_link.append(source_link_idx)


def build_snap_tiles(graph: RoadGraph, *, margin_m: float = 2.0) -> dict[tuple[int, int], list[int]]:
    tiles: dict[tuple[int, int], list[int]] = {}
    for geom_id, geom in enumerate(graph.snap_geometries):
        minx, miny, maxx, maxy = geom.bbox
        min_tx = math.floor((minx - margin_m) / graph.snap_tile_size_m)
        max_tx = math.floor((maxx + margin_m) / graph.snap_tile_size_m)
        min_ty = math.floor((miny - margin_m) / graph.snap_tile_size_m)
        max_ty = math.floor((maxy + margin_m) / graph.snap_tile_size_m)
        for tx in range(min_tx, max_tx + 1):
            for ty in range(min_ty, max_ty + 1):
                tiles.setdefault((tx, ty), []).append(geom_id)
    for geom_ids in tiles.values():
        geom_ids.sort()
    return tiles


def snap_point(
    graph: RoadGraph,
    x: float,
    y: float,
    *,
    radius_m: float = DEFAULT_SNAP_RADIUS_M,
    limit: int = 8,
) -> list[SnapCandidate]:
    tile_x = math.floor(x / graph.snap_tile_size_m)
    tile_y = math.floor(y / graph.snap_tile_size_m)
    tile_radius = max(1, math.ceil(radius_m / graph.snap_tile_size_m))
    geom_ids: set[int] = set()
    for tx in range(tile_x - tile_radius, tile_x + tile_radius + 1):
        for ty in range(tile_y - tile_radius, tile_y + tile_radius + 1):
            geom_ids.update(graph.tile_geom_ids(tx, ty))

    candidates: list[SnapCandidate] = []
    for geom_id in geom_ids:
        if not graph.snap_geom_may_intersect_radius(geom_id, x, y, radius_m):
            continue
        distance_m, along_m, px, py = graph.snap_distance(geom_id, x, y)
        if distance_m <= radius_m:
            candidates.append(SnapCandidate(geom_id, distance_m, along_m, px, py))

    candidates.sort(key=lambda item: item.distance_m)
    return candidates[:limit]


def route_nodes(
    graph: RoadGraph,
    origin: tuple[float, float],
    target: tuple[float, float],
    *,
    snap_radius_m: float = DEFAULT_SNAP_RADIUS_M,
    snap_candidates: int = 8,
    workspace: QueryWorkspace | None = None,
) -> RouteResult:
    return route_nodes_batch(
        graph,
        origin,
        [target],
        snap_radius_m=snap_radius_m,
        snap_candidates=snap_candidates,
        workspace=workspace,
    )[0]


def route_nodes_batch(
    graph: RoadGraph,
    origin: tuple[float, float],
    targets: Iterable[tuple[float, float]],
    *,
    snap_radius_m: float = DEFAULT_SNAP_RADIUS_M,
    snap_candidates: int = 8,
    workspace: QueryWorkspace | None = None,
) -> list[RouteResult]:
    """Route from one origin to targets in order.

    The origin is snapped once and reused. This strict batch helper raises on
    the first target that cannot be snapped or routed.
    """

    target_list = list(targets)
    if not target_list:
        return []

    origin_snaps = snap_point(graph, origin[0], origin[1], radius_m=snap_radius_m, limit=snap_candidates)
    if not origin_snaps:
        raise ValueError("origin did not snap to any road link")

    start_costs = _endpoint_costs(graph, origin_snaps)

    query_workspace = workspace or QueryWorkspace(graph.node_count)
    if query_workspace.node_count != graph.node_count:
        raise ValueError("query workspace node count does not match graph")

    results: list[RouteResult] = []
    for target in target_list:
        target_snaps = snap_point(graph, target[0], target[1], radius_m=snap_radius_m, limit=snap_candidates)
        if not target_snaps:
            raise ValueError("target did not snap to any road link")
        target_costs = _endpoint_costs(graph, target_snaps)
        filtered_start_costs, filtered_target_costs = _filter_endpoint_costs_by_component(
            graph,
            start_costs,
            target_costs,
        )
        query_workspace.reset()
        results.append(
            _search_astar(
                graph,
                filtered_start_costs,
                filtered_target_costs,
                query_workspace,
            )
        )
    return results


def _endpoint_costs(
    graph: RoadGraph,
    snaps: Iterable[SnapCandidate],
) -> dict[int, tuple[int, SnapCandidate]]:
    costs: dict[int, tuple[int, SnapCandidate]] = {}
    for snap in snaps:
        for node_id, cost in graph.snap_endpoint_cost_units(snap.geom_id, snap):
            current = costs.get(node_id)
            if current is None or cost < current[0]:
                costs[node_id] = (cost, snap)
    return costs


def _filter_endpoint_costs_by_component(
    graph: RoadGraph,
    start_costs: dict[int, tuple[int, SnapCandidate]],
    target_costs: dict[int, tuple[int, SnapCandidate]],
) -> tuple[dict[int, tuple[int, SnapCandidate]], dict[int, tuple[int, SnapCandidate]]]:
    if not getattr(graph, "has_components", False):
        return start_costs, target_costs
    start_components = {graph.component_id(node_id) for node_id in start_costs}
    target_components = {graph.component_id(node_id) for node_id in target_costs}
    shared_components = start_components.intersection(target_components)
    if not shared_components:
        raise ValueError("no route found between snapped candidates in the same component")
    if len(shared_components) == len(start_components) == len(target_components):
        return start_costs, target_costs
    return (
        {
            node_id: entry
            for node_id, entry in start_costs.items()
            if graph.component_id(node_id) in shared_components
        },
        {
            node_id: entry
            for node_id, entry in target_costs.items()
            if graph.component_id(node_id) in shared_components
        },
    )


def _search_astar(
    graph: RoadGraph,
    start_costs: dict[int, tuple[int, SnapCandidate]],
    target_costs: dict[int, tuple[int, SnapCandidate]],
    workspace: QueryWorkspace,
) -> RouteResult:
    heuristic = _target_heuristic_units(graph, target_costs)
    split_edge_to = getattr(graph, "_edge_to_u32", None)
    split_edge_length = getattr(graph, "_edge_length_u32", None)
    split_offsets = getattr(graph, "_offsets_u32", None)
    if split_edge_to is not None and split_edge_length is not None and split_offsets is not None:
        return _search_astar_split(
            graph,
            start_costs,
            target_costs,
            workspace,
            heuristic,
            split_edge_to,
            split_edge_length,
            split_offsets,
        )

    dist = workspace.dist
    prev_node = workspace.prev_node
    prev_edge = workspace.prev_edge
    settled = workspace.settled
    touched = workspace.touched
    heap = workspace.heap
    inf = MAX_COST_UNITS
    heappush = heapq.heappush
    heappop = heapq.heappop
    outgoing_edges = graph.outgoing_edges
    edge_target_and_length_units = graph.edge_target_and_length_units

    for node_id, (cost, _snap) in start_costs.items():
        if dist[node_id] == inf:
            touched.append(node_id)
        dist[node_id] = cost
        prev_node[node_id] = -1
        prev_edge[node_id] = -1
        heappush(heap, (cost + heuristic(node_id), cost, node_id))

    best_target = -1
    best_total = MAX_COST_UNITS
    best_target_snap: SnapCandidate | None = None

    while heap:
        priority, cost, node_id = heappop(heap)
        if cost != dist[node_id] or settled[node_id]:
            continue
        if priority >= best_total:
            break

        settled[node_id] = 1
        target_entry = target_costs.get(node_id)
        if target_entry is not None:
            total = cost + target_entry[0]
            if total < best_total:
                best_total = total
                best_target = node_id
                best_target_snap = target_entry[1]

        for edge_id in outgoing_edges(node_id):
            next_node, edge_length_units = edge_target_and_length_units(edge_id)
            if settled[next_node]:
                continue
            next_cost = cost + edge_length_units
            if next_cost < dist[next_node]:
                if dist[next_node] == inf:
                    touched.append(next_node)
                dist[next_node] = next_cost
                prev_node[next_node] = node_id
                prev_edge[next_node] = edge_id
                heappush(heap, (next_cost + heuristic(next_node), next_cost, next_node))

    if best_target < 0 or best_target_snap is None:
        raise ValueError("no route found between snapped candidates")

    path_nodes, path_edges = _reconstruct_forward_path(workspace, best_target)
    origin_snap = start_costs[path_nodes[0]][1]
    return RouteResult(
        path_nodes,
        path_edges,
        best_total / graph.length_scale,
        origin_snap,
        best_target_snap,
    )


def _search_astar_split(
    graph: RoadGraph,
    start_costs: dict[int, tuple[int, SnapCandidate]],
    target_costs: dict[int, tuple[int, SnapCandidate]],
    workspace: QueryWorkspace,
    heuristic,
    edge_to,
    edge_length,
    offsets,
) -> RouteResult:
    dist = workspace.dist
    prev_node = workspace.prev_node
    prev_edge = workspace.prev_edge
    settled = workspace.settled
    touched = workspace.touched
    heap = workspace.heap
    inf = MAX_COST_UNITS
    heappush = heapq.heappush
    heappop = heapq.heappop

    for node_id, (cost, _snap) in start_costs.items():
        if dist[node_id] == inf:
            touched.append(node_id)
        dist[node_id] = cost
        prev_node[node_id] = -1
        prev_edge[node_id] = -1
        heappush(heap, (cost + heuristic(node_id), cost, node_id))

    best_target = -1
    best_total = MAX_COST_UNITS
    best_target_snap: SnapCandidate | None = None

    while heap:
        priority, cost, node_id = heappop(heap)
        if cost != dist[node_id] or settled[node_id]:
            continue
        if priority >= best_total:
            break

        settled[node_id] = 1
        target_entry = target_costs.get(node_id)
        if target_entry is not None:
            total = cost + target_entry[0]
            if total < best_total:
                best_total = total
                best_target = node_id
                best_target_snap = target_entry[1]

        for edge_id in range(offsets[node_id], offsets[node_id + 1]):
            next_node = edge_to[edge_id]
            if settled[next_node]:
                continue
            next_cost = cost + edge_length[edge_id]
            if next_cost < dist[next_node]:
                if dist[next_node] == inf:
                    touched.append(next_node)
                dist[next_node] = next_cost
                prev_node[next_node] = node_id
                prev_edge[next_node] = edge_id
                heappush(heap, (next_cost + heuristic(next_node), next_cost, next_node))

    if best_target < 0 or best_target_snap is None:
        raise ValueError("no route found between snapped candidates")

    path_nodes, path_edges = _reconstruct_forward_path(workspace, best_target)
    origin_snap = start_costs[path_nodes[0]][1]
    return RouteResult(
        path_nodes,
        path_edges,
        best_total / graph.length_scale,
        origin_snap,
        best_target_snap,
    )


def _reconstruct_forward_path(workspace: QueryWorkspace, target_node: int) -> tuple[list[int], list[int]]:
    path_nodes: list[int] = []
    path_edges: list[int] = []
    cursor = target_node
    while cursor >= 0:
        path_nodes.append(cursor)
        edge_id = workspace.prev_edge[cursor]
        if edge_id >= 0:
            path_edges.append(edge_id)
        cursor = workspace.prev_node[cursor]
    path_nodes.reverse()
    path_edges.reverse()
    return path_nodes, path_edges


def _target_heuristic_units(
    graph: RoadGraph,
    target_costs: dict[int, tuple[int, SnapCandidate]],
):
    coord_to_length_units = graph.length_scale / graph.coord_scale
    hypot = math.hypot
    node_x = getattr(graph, "_node_x_u32", None)
    node_y = getattr(graph, "_node_y_u32", None)

    if node_x is not None and node_y is not None:
        target_refs = [
            (
                node_x[node_id],
                node_y[node_id],
                endpoint_cost,
            )
            for node_id, (endpoint_cost, _snap) in target_costs.items()
        ]

        if len(target_refs) == 1:
            tx, ty, endpoint_cost = target_refs[0]

            def heuristic(node_id: int) -> int:
                return int(hypot(node_x[node_id] - tx, node_y[node_id] - ty) * coord_to_length_units) + endpoint_cost

            return heuristic

        if len(target_refs) == 2:
            tx0, ty0, endpoint_cost0 = target_refs[0]
            tx1, ty1, endpoint_cost1 = target_refs[1]

            def heuristic(node_id: int) -> int:
                x = node_x[node_id]
                y = node_y[node_id]
                estimate0 = int(hypot(x - tx0, y - ty0) * coord_to_length_units) + endpoint_cost0
                estimate1 = int(hypot(x - tx1, y - ty1) * coord_to_length_units) + endpoint_cost1
                return estimate0 if estimate0 <= estimate1 else estimate1

            return heuristic

        def heuristic(node_id: int) -> int:
            x = node_x[node_id]
            y = node_y[node_id]
            best = MAX_COST_UNITS
            for tx, ty, endpoint_cost in target_refs:
                estimate = int(hypot(x - tx, y - ty) * coord_to_length_units) + endpoint_cost
                if estimate < best:
                    best = estimate
            return best

        return heuristic

    node_units = graph.node_units
    target_refs = [
        (*node_units(node_id), endpoint_cost)
        for node_id, (endpoint_cost, _snap) in target_costs.items()
    ]

    if len(target_refs) == 1:
        tx, ty, endpoint_cost = target_refs[0]

        def heuristic(node_id: int) -> int:
            x, y = node_units(node_id)
            return int(hypot(x - tx, y - ty) * coord_to_length_units) + endpoint_cost

        return heuristic

    if len(target_refs) == 2:
        tx0, ty0, endpoint_cost0 = target_refs[0]
        tx1, ty1, endpoint_cost1 = target_refs[1]

        def heuristic(node_id: int) -> int:
            x, y = node_units(node_id)
            estimate0 = int(hypot(x - tx0, y - ty0) * coord_to_length_units) + endpoint_cost0
            estimate1 = int(hypot(x - tx1, y - ty1) * coord_to_length_units) + endpoint_cost1
            return estimate0 if estimate0 <= estimate1 else estimate1

        return heuristic

    def heuristic(node_id: int) -> int:
        x, y = node_units(node_id)
        best = MAX_COST_UNITS
        for tx, ty, endpoint_cost in target_refs:
            estimate = int(hypot(x - tx, y - ty) * coord_to_length_units) + endpoint_cost
            if estimate < best:
                best = estimate
        return best

    return heuristic


def _fetch_nodes_by_source_id(
    conn: sqlite3.Connection,
    source_ids: Iterable[str],
) -> dict[str, tuple[int, float, float, str]]:
    ids = sorted(source_ids)
    if not ids:
        return {}

    out: dict[str, tuple[int, float, float, str]] = {}
    conn.execute("drop table if exists temp.rdb_needed_nodes")
    conn.execute("create temp table rdb_needed_nodes(id text primary key)")
    conn.executemany("insert into temp.rdb_needed_nodes(id) values (?)", ((source_id,) for source_id in ids))
    sql = """
        select n.fid, n.id, n.geometry, n.form_of_road_node
        from road_node as n
        join temp.rdb_needed_nodes as needed on needed.id = n.id
        order by n.fid
    """
    for fid, source_id, blob, form in conn.execute(sql):
        point = decode_gpkg_geometry(blob)
        if not isinstance(point, Point):
            raise ValueError(f"road_node {source_id} geometry was not a point")
        out[source_id] = (int(fid), point.x, point.y, form)
    return out


def _snap_endpoint_costs(
    start_node: int,
    end_node: int,
    length_m: float,
    snap: SnapCandidate,
) -> tuple[tuple[int, float], ...]:
    along = min(max(snap.along_m, 0.0), length_m)
    if along <= 1e-6:
        return ((start_node, 0.0),)
    if length_m - along <= 1e-6:
        return ((end_node, 0.0),)
    return (
        (start_node, along),
        (end_node, max(0.0, length_m - along)),
    )


def _snap_endpoint_costs_units(
    start_node: int,
    end_node: int,
    length_units: int,
    length_scale: int,
    snap: SnapCandidate,
) -> tuple[tuple[int, int], ...]:
    along_units = min(max(round(snap.along_m * length_scale), 0), length_units)
    if along_units <= 0:
        return ((start_node, 0),)
    remaining_units = length_units - along_units
    if remaining_units <= 0:
        return ((end_node, 0),)
    return (
        (start_node, along_units),
        (end_node, remaining_units),
    )


def _points_bbox(points: tuple[Point, ...]) -> tuple[float, float, float, float]:
    xs = [point.x for point in points]
    ys = [point.y for point in points]
    return min(xs), min(ys), max(xs), max(ys)


def _point_to_polyline(x: float, y: float, points: tuple[Point, ...]) -> tuple[float, float, float, float]:
    best_dist_sq = math.inf
    best_along = 0.0
    best_x = points[0].x
    best_y = points[0].y
    travelled = 0.0
    for start, end in zip(points, points[1:]):
        dx = end.x - start.x
        dy = end.y - start.y
        seg_len_sq = dx * dx + dy * dy
        if seg_len_sq == 0:
            continue
        t = ((x - start.x) * dx + (y - start.y) * dy) / seg_len_sq
        t = min(1.0, max(0.0, t))
        px = start.x + t * dx
        py = start.y + t * dy
        dist_sq = (x - px) ** 2 + (y - py) ** 2
        seg_len = math.sqrt(seg_len_sq)
        if dist_sq < best_dist_sq:
            best_dist_sq = dist_sq
            best_along = travelled + t * seg_len
            best_x = px
            best_y = py
        travelled += seg_len
    return math.sqrt(best_dist_sq), best_along, best_x, best_y
