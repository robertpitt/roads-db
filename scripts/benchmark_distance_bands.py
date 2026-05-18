#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import random
import statistics
import sys
import time
from array import array
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from roads_rdb.graph import route_nodes
from roads_rdb.rdb import read_rdb


@dataclass(slots=True)
class NodeIndex:
    xs: array
    ys: array
    grid: dict[tuple[int, int], list[int]]
    cell_size_m: float

    def xy(self, node_id: int) -> tuple[float, float]:
        return self.xs[node_id], self.ys[node_id]


@dataclass(slots=True)
class Pair:
    origin_node: int
    target_node: int
    origin: tuple[float, float]
    target: tuple[float, float]
    los_m: float


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Benchmark route-node generation by line-of-sight distance bands using only an RDB file."
    )
    parser.add_argument("--index", default="build/full_gb.rdb")
    parser.add_argument("--distances-km", default="10,50,100,200")
    parser.add_argument("--pairs", type=int, default=3, help="successful routed pairs per distance band")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--cell-size-m", type=float, default=5000.0)
    parser.add_argument("--tolerance-ratio", type=float, default=0.10)
    parser.add_argument("--min-tolerance-m", type=float, default=1000.0)
    parser.add_argument("--max-sample-attempts", type=int, default=50000)
    parser.add_argument("--snap-radius-m", type=float, default=50.0)
    parser.add_argument("--snap-candidates", type=int, default=8)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    distances_m = [float(part.strip()) * 1000 for part in args.distances_km.split(",") if part.strip()]
    rng = random.Random(args.seed)

    started_load = time.perf_counter()
    graph = read_rdb(args.index)
    try:
        started_index = time.perf_counter()
        node_index = build_node_index(graph, args.cell_size_m)
        index_s = time.perf_counter() - started_index
        load_s = started_index - started_load

        bands = []
        for distance_m in distances_m:
            band = run_band(
                graph,
                node_index,
                rng,
                distance_m=distance_m,
                requested_pairs=args.pairs,
                tolerance_m=max(args.min_tolerance_m, distance_m * args.tolerance_ratio),
                max_sample_attempts=args.max_sample_attempts,
                snap_radius_m=args.snap_radius_m,
                snap_candidates=args.snap_candidates,
            )
            bands.append(band)
    finally:
        graph.close()

    payload = {
        "index": args.index,
        "node_count": graph.node_count,
        "load_s": round(load_s, 4),
        "node_index_s": round(index_s, 4),
        "cell_size_m": args.cell_size_m,
        "bands": bands,
    }

    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"Loaded RDB in {payload['load_s']}s; built node sampler in {payload['node_index_s']}s")
        print(f"Nodes: {payload['node_count']:,}; sampler cell size: {args.cell_size_m:g} m")
        print()
        for band in bands:
            print(
                f"{band['target_km']:>6.0f} km: "
                f"{band['successes']}/{band['requested_pairs']} routes, "
                f"sample attempts {band['sample_attempts']}, "
                f"median {band['route_ms']['median']} ms, "
                f"p95 {band['route_ms']['p95']} ms, "
                f"median nodes {band['nodes']['median']}, "
                f"median route/LOS {band['route_to_los_ratio']['median']}"
            )
    return 0


def build_node_index(graph, cell_size_m: float) -> NodeIndex:
    xs = array("f")
    ys = array("f")
    grid: dict[tuple[int, int], list[int]] = defaultdict(list)
    for node_id in range(graph.node_count):
        x_units, y_units = graph.node_units(node_id)
        x = graph.origin_x_m + x_units / graph.coord_scale
        y = graph.origin_y_m + y_units / graph.coord_scale
        xs.append(x)
        ys.append(y)
        grid[(math.floor(x / cell_size_m), math.floor(y / cell_size_m))].append(node_id)
    return NodeIndex(xs=xs, ys=ys, grid=dict(grid), cell_size_m=cell_size_m)


def run_band(
    graph,
    node_index: NodeIndex,
    rng: random.Random,
    *,
    distance_m: float,
    requested_pairs: int,
    tolerance_m: float,
    max_sample_attempts: int,
    snap_radius_m: float,
    snap_candidates: int,
) -> dict:
    route_times: list[float] = []
    node_counts: list[int] = []
    edge_counts: list[int] = []
    costs_m: list[float] = []
    los_values: list[float] = []
    ratios: list[float] = []
    failures = 0
    sample_attempts = 0

    while len(route_times) < requested_pairs and sample_attempts < max_sample_attempts:
        sample_attempts += 1
        pair = sample_pair(node_index, rng, distance_m=distance_m, tolerance_m=tolerance_m)
        if pair is None:
            continue

        started = time.perf_counter()
        try:
            route = route_nodes(
                graph,
                pair.origin,
                pair.target,
                snap_radius_m=snap_radius_m,
                snap_candidates=snap_candidates,
            )
        except ValueError:
            failures += 1
            continue
        elapsed_ms = (time.perf_counter() - started) * 1000
        route_times.append(elapsed_ms)
        node_counts.append(len(route.node_ids))
        edge_counts.append(len(route.edge_ids))
        costs_m.append(route.cost_m)
        los_values.append(pair.los_m)
        ratios.append(route.cost_m / pair.los_m if pair.los_m else math.nan)

    return {
        "target_km": distance_m / 1000,
        "tolerance_m": tolerance_m,
        "requested_pairs": requested_pairs,
        "successes": len(route_times),
        "route_failures": failures,
        "sample_attempts": sample_attempts,
        "line_of_sight_m": summarise(los_values),
        "route_cost_m": summarise(costs_m),
        "route_to_los_ratio": summarise(ratios),
        "route_ms": summarise(route_times),
        "nodes": summarise(node_counts),
        "edges": summarise(edge_counts),
    }


def sample_pair(
    node_index: NodeIndex,
    rng: random.Random,
    *,
    distance_m: float,
    tolerance_m: float,
) -> Pair | None:
    origin_node = rng.randrange(len(node_index.xs))
    ox, oy = node_index.xy(origin_node)
    angle = rng.random() * math.tau
    tx = ox + math.cos(angle) * distance_m
    ty = oy + math.sin(angle) * distance_m

    cell_radius = max(1, math.ceil(tolerance_m / node_index.cell_size_m))
    target_cell = (math.floor(tx / node_index.cell_size_m), math.floor(ty / node_index.cell_size_m))
    best: tuple[float, int] | None = None
    min_distance = max(0.0, distance_m - tolerance_m)
    max_distance = distance_m + tolerance_m

    for cx in range(target_cell[0] - cell_radius, target_cell[0] + cell_radius + 1):
        for cy in range(target_cell[1] - cell_radius, target_cell[1] + cell_radius + 1):
            for candidate in node_index.grid.get((cx, cy), ()):
                if candidate == origin_node:
                    continue
                cx_m, cy_m = node_index.xy(candidate)
                los = math.hypot(cx_m - ox, cy_m - oy)
                if min_distance <= los <= max_distance:
                    error = abs(los - distance_m)
                    if best is None or error < best[0]:
                        best = (error, candidate)

    if best is None:
        return None

    target_node = best[1]
    target = node_index.xy(target_node)
    los_m = math.hypot(target[0] - ox, target[1] - oy)
    return Pair(origin_node, target_node, (ox, oy), target, los_m)


def summarise(values: list[float] | list[int]) -> dict[str, float | int | None]:
    if not values:
        return {"min": None, "median": None, "p95": None, "max": None}
    sorted_values = sorted(values)
    return {
        "min": round(sorted_values[0], 3),
        "median": round(statistics.median(sorted_values), 3),
        "p95": round(percentile(sorted_values, 0.95), 3),
        "max": round(sorted_values[-1], 3),
    }


def percentile(sorted_values: list[float] | list[int], p: float) -> float:
    idx = min(len(sorted_values) - 1, max(0, int(round((len(sorted_values) - 1) * p))))
    return float(sorted_values[idx])


if __name__ == "__main__":
    raise SystemExit(main())
