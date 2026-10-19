#!/usr/bin/env python3
from __future__ import annotations

import argparse
import concurrent.futures
import json
import math
import os
import random
import statistics
import sys
import threading
import time
from array import array
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from roads_rdb.graph import QueryWorkspace, route_nodes
from roads_rdb.rdb import read_rdb


DEFAULT_PAIRS_PER_BAND = 100
DEFAULT_PROGRESS_EVERY = 25
DEFAULT_THREADS = max(1, os.cpu_count() or 1)


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


@dataclass(slots=True)
class RouteSample:
    route_ms: float
    node_count: int
    edge_count: int
    cost_m: float
    los_m: float


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Benchmark route-node generation by line-of-sight distance bands using only an RDB file."
    )
    parser.add_argument("--index", default="build/full_gb.rdb")
    parser.add_argument("--distances-km", default="10,50,100,200")
    parser.add_argument(
        "--pairs",
        type=int,
        default=DEFAULT_PAIRS_PER_BAND,
        help="successful routed pairs per distance band",
    )
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--cell-size-m", type=float, default=5000.0)
    parser.add_argument("--tolerance-ratio", type=float, default=0.10)
    parser.add_argument("--min-tolerance-m", type=float, default=1000.0)
    parser.add_argument("--max-sample-attempts", type=int, default=50000)
    parser.add_argument("--snap-radius-m", type=float, default=50.0)
    parser.add_argument("--snap-candidates", type=int, default=8)
    parser.add_argument(
        "--threads",
        type=int,
        default=DEFAULT_THREADS,
        help="route query worker threads; defaults to detected CPU count; use 1 for serial timing",
    )
    parser.add_argument(
        "--progress-every",
        type=int,
        default=DEFAULT_PROGRESS_EVERY,
        help="print progress to stderr after this many successful routes per band; use 0 to disable",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if args.threads < 1:
        parser.error("--threads must be at least 1")

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
                progress_every=args.progress_every,
                threads=args.threads,
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
        "threads": args.threads,
        "bands": bands,
    }

    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"Loaded RDB in {payload['load_s']}s; built node sampler in {payload['node_index_s']}s")
        print(
            f"Nodes: {payload['node_count']:,}; sampler cell size: {args.cell_size_m:g} m; "
            f"route threads: {args.threads}"
        )
        print()
        for band in bands:
            print(
                f"{band['target_km']:>6.0f} km: "
                f"{band['successes']}/{band['requested_pairs']} routes, "
                f"sample attempts {band['sample_attempts']}, "
                f"{band['routes_per_s']} routes/s, "
                f"median {band['route_ms']['median']} ms, "
                f"p95 {band['route_ms']['p95']} ms, "
                f"p99 {band['route_ms']['p99']} ms, "
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
    progress_every: int,
    threads: int,
) -> dict:
    if threads <= 1:
        return run_band_serial(
            graph,
            node_index,
            rng,
            distance_m=distance_m,
            requested_pairs=requested_pairs,
            tolerance_m=tolerance_m,
            max_sample_attempts=max_sample_attempts,
            snap_radius_m=snap_radius_m,
            snap_candidates=snap_candidates,
            progress_every=progress_every,
        )
    return run_band_threaded(
        graph,
        node_index,
        rng,
        distance_m=distance_m,
        requested_pairs=requested_pairs,
        tolerance_m=tolerance_m,
        max_sample_attempts=max_sample_attempts,
        snap_radius_m=snap_radius_m,
        snap_candidates=snap_candidates,
        progress_every=progress_every,
        threads=threads,
    )


def run_band_serial(
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
    progress_every: int,
) -> dict:
    workspace = QueryWorkspace(graph.node_count)
    route_times: list[float] = []
    node_counts: list[int] = []
    edge_counts: list[int] = []
    costs_m: list[float] = []
    los_values: list[float] = []
    ratios: list[float] = []
    failures = 0
    sample_attempts = 0
    started_band = time.perf_counter()
    last_progress_successes = 0

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
                workspace=workspace,
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
        if progress_every > 0 and len(route_times) - last_progress_successes >= progress_every:
            last_progress_successes = len(route_times)
            elapsed_s = time.perf_counter() - started_band
            print(
                f"{distance_m / 1000:g} km: {len(route_times)}/{requested_pairs} routes "
                f"in {elapsed_s:.1f}s",
                file=sys.stderr,
            )

    elapsed_s = time.perf_counter() - started_band

    return build_band_payload(
        distance_m=distance_m,
        tolerance_m=tolerance_m,
        requested_pairs=requested_pairs,
        threads=1,
        successes=len(route_times),
        failures=failures,
        sample_attempts=sample_attempts,
        elapsed_s=elapsed_s,
        route_times=route_times,
        node_counts=node_counts,
        edge_counts=edge_counts,
        costs_m=costs_m,
        los_values=los_values,
        ratios=ratios,
    )


def run_band_threaded(
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
    progress_every: int,
    threads: int,
) -> dict:
    route_times: list[float] = []
    node_counts: list[int] = []
    edge_counts: list[int] = []
    costs_m: list[float] = []
    los_values: list[float] = []
    ratios: list[float] = []
    failures = 0
    sample_attempts = 0
    started_band = time.perf_counter()
    last_progress_successes = 0
    worker_count = min(threads, max(1, requested_pairs))
    thread_state = threading.local()

    def route_pair(pair: Pair) -> RouteSample | None:
        workspace = getattr(thread_state, "workspace", None)
        if workspace is None:
            workspace = QueryWorkspace(graph.node_count)
            thread_state.workspace = workspace

        started = time.perf_counter()
        try:
            route = route_nodes(
                graph,
                pair.origin,
                pair.target,
                snap_radius_m=snap_radius_m,
                snap_candidates=snap_candidates,
                workspace=workspace,
            )
        except ValueError:
            return None
        return RouteSample(
            route_ms=(time.perf_counter() - started) * 1000,
            node_count=len(route.node_ids),
            edge_count=len(route.edge_ids),
            cost_m=route.cost_m,
            los_m=pair.los_m,
        )

    def submit_next(executor: concurrent.futures.ThreadPoolExecutor, pending: set) -> bool:
        nonlocal sample_attempts
        while sample_attempts < max_sample_attempts:
            sample_attempts += 1
            pair = sample_pair(node_index, rng, distance_m=distance_m, tolerance_m=tolerance_m)
            if pair is None:
                continue
            pending.add(executor.submit(route_pair, pair))
            return True
        return False

    with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as executor:
        pending: set[concurrent.futures.Future[RouteSample | None]] = set()
        while len(pending) < worker_count and len(route_times) + len(pending) < requested_pairs:
            if not submit_next(executor, pending):
                break

        while pending and len(route_times) < requested_pairs:
            done, pending = concurrent.futures.wait(
                pending,
                return_when=concurrent.futures.FIRST_COMPLETED,
            )
            for future in done:
                sample = future.result()
                if sample is None:
                    failures += 1
                    continue
                route_times.append(sample.route_ms)
                node_counts.append(sample.node_count)
                edge_counts.append(sample.edge_count)
                costs_m.append(sample.cost_m)
                los_values.append(sample.los_m)
                ratios.append(sample.cost_m / sample.los_m if sample.los_m else math.nan)

            if progress_every > 0 and len(route_times) - last_progress_successes >= progress_every:
                last_progress_successes = len(route_times)
                elapsed_s = time.perf_counter() - started_band
                print(
                    f"{distance_m / 1000:g} km: {len(route_times)}/{requested_pairs} routes "
                    f"in {elapsed_s:.1f}s using {worker_count} threads",
                    file=sys.stderr,
                )

            while len(pending) < worker_count and len(route_times) + len(pending) < requested_pairs:
                if not submit_next(executor, pending):
                    break

    elapsed_s = time.perf_counter() - started_band
    return build_band_payload(
        distance_m=distance_m,
        tolerance_m=tolerance_m,
        requested_pairs=requested_pairs,
        threads=worker_count,
        successes=len(route_times),
        failures=failures,
        sample_attempts=sample_attempts,
        elapsed_s=elapsed_s,
        route_times=route_times,
        node_counts=node_counts,
        edge_counts=edge_counts,
        costs_m=costs_m,
        los_values=los_values,
        ratios=ratios,
    )


def build_band_payload(
    *,
    distance_m: float,
    tolerance_m: float,
    requested_pairs: int,
    threads: int,
    successes: int,
    failures: int,
    sample_attempts: int,
    elapsed_s: float,
    route_times: list[float],
    node_counts: list[int],
    edge_counts: list[int],
    costs_m: list[float],
    los_values: list[float],
    ratios: list[float],
) -> dict:
    return {
        "target_km": distance_m / 1000,
        "tolerance_m": tolerance_m,
        "requested_pairs": requested_pairs,
        "threads": threads,
        "successes": successes,
        "route_failures": failures,
        "sample_attempts": sample_attempts,
        "elapsed_s": round(elapsed_s, 3),
        "routes_per_s": round(successes / elapsed_s, 3) if elapsed_s > 0 else None,
        "success_rate": round(successes / sample_attempts, 4) if sample_attempts else None,
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
        return {
            "count": 0,
            "min": None,
            "mean": None,
            "stdev": None,
            "p25": None,
            "median": None,
            "p75": None,
            "p90": None,
            "p95": None,
            "p99": None,
            "max": None,
        }
    sorted_values = sorted(values)
    return {
        "count": len(sorted_values),
        "min": round(sorted_values[0], 3),
        "mean": round(statistics.fmean(sorted_values), 3),
        "stdev": round(statistics.stdev(sorted_values), 3) if len(sorted_values) > 1 else 0.0,
        "p25": round(percentile(sorted_values, 0.25), 3),
        "median": round(statistics.median(sorted_values), 3),
        "p75": round(percentile(sorted_values, 0.75), 3),
        "p90": round(percentile(sorted_values, 0.90), 3),
        "p95": round(percentile(sorted_values, 0.95), 3),
        "p99": round(percentile(sorted_values, 0.99), 3),
        "max": round(sorted_values[-1], 3),
    }


def percentile(sorted_values: list[float] | list[int], p: float) -> float:
    idx = min(len(sorted_values) - 1, max(0, int(round((len(sorted_values) - 1) * p))))
    return float(sorted_values[idx])


if __name__ == "__main__":
    raise SystemExit(main())
