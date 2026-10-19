from __future__ import annotations

import argparse
import json
import random
import sqlite3
import statistics
import time
from pathlib import Path

from .graph import QueryWorkspace, build_graph_from_gpkg, parse_bbox, parse_point, route_nodes, route_nodes_batch
from .lookup import write_lookup_db
from .rdb import read_rdb, write_rdb


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="roads-rdb")
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("build", help="build a dataset.rdb from a GeoPackage")
    build.add_argument("--input", required=True, help="path to OS Open Roads GeoPackage")
    build.add_argument("--bbox", help="optional BNG bbox minx,miny,maxx,maxy")
    build.add_argument("--output", required=True, help="output dataset.rdb path")
    build.add_argument("--lookup", help="optional companion lookup sqlite path")
    build.add_argument("--tile-size", type=float, default=256.0)
    build.add_argument("--limit-links", type=int)

    query = sub.add_parser("query", help="route between two BNG coordinates")
    query.add_argument("--index", required=True)
    query.add_argument("--from", dest="origin", required=True, help="BNG x,y")
    query.add_argument("--to", dest="target", required=True, help="BNG x,y")
    query.add_argument("--snap-radius", type=float, default=250.0)
    query.add_argument("--snap-candidates", type=int, default=8)
    query.add_argument("--with-edges", action="store_true")

    batch = sub.add_parser("batch", help="route from one BNG coordinate to multiple targets")
    batch.add_argument("--index", required=True)
    batch.add_argument("--from", dest="origin", required=True, help="BNG x,y")
    batch.add_argument("--to", dest="targets", action="append", required=True, help="target BNG x,y; repeatable")
    batch.add_argument("--snap-radius", type=float, default=250.0)
    batch.add_argument("--snap-candidates", type=int, default=8)
    batch.add_argument("--with-edges", action="store_true")

    inspect = sub.add_parser("inspect", help="print dataset summary")
    inspect.add_argument("--index", required=True)

    bench = sub.add_parser("bench", help="run random coordinate-pair route benchmarks")
    bench.add_argument("--index", required=True)
    bench.add_argument("--pairs", type=int, default=25)
    bench.add_argument("--seed", type=int, default=1)
    bench.add_argument("--sample", choices=["nodes", "bbox"], default="nodes")

    args = parser.parse_args(argv)
    try:
        if args.command == "build":
            return _build(args)
        if args.command == "query":
            return _query(args)
        if args.command == "batch":
            return _batch(args)
        if args.command == "inspect":
            return _inspect(args)
        if args.command == "bench":
            return _bench(args)
    except (OSError, ValueError, sqlite3.Error) as exc:
        parser.exit(2, f"{parser.prog}: error: {exc}\n")
    raise AssertionError(args.command)


def _build(args: argparse.Namespace) -> int:
    started = time.perf_counter()
    graph = build_graph_from_gpkg(
        args.input,
        bbox=parse_bbox(args.bbox) if args.bbox else None,
        link_limit=args.limit_links,
        tile_size_m=args.tile_size,
        keep_source_ids=bool(args.lookup),
    )
    write_rdb(args.output, graph)
    if args.lookup:
        write_lookup_db(args.lookup, graph)

    elapsed = time.perf_counter() - started
    summary = {
        "output": str(Path(args.output)),
        "lookup": str(Path(args.lookup)) if args.lookup else None,
        "nodes": graph.node_count,
        "directed_edges": graph.edge_count,
        "snap_geometries": len(graph.snap_geometries),
        "snap_tiles": len(graph.snap_tiles),
        "elapsed_s": round(elapsed, 3),
    }
    print(json.dumps(summary, indent=2))
    return 0


def _query(args: argparse.Namespace) -> int:
    graph = read_rdb(args.index)
    try:
        result = route_nodes(
            graph,
            parse_point(args.origin),
            parse_point(args.target),
            snap_radius_m=args.snap_radius,
            snap_candidates=args.snap_candidates,
        )
        payload = _route_payload(result, with_edges=args.with_edges)
        print(json.dumps(payload, indent=2))
        return 0
    finally:
        graph.close()


def _batch(args: argparse.Namespace) -> int:
    graph = read_rdb(args.index)
    try:
        workspace = QueryWorkspace(graph.node_count)
        results = route_nodes_batch(
            graph,
            parse_point(args.origin),
            [parse_point(target) for target in args.targets],
            snap_radius_m=args.snap_radius,
            snap_candidates=args.snap_candidates,
            workspace=workspace,
        )
        payload = {
            "origin": parse_point(args.origin),
            "target_count": len(args.targets),
            "routes": [_route_payload(result, with_edges=args.with_edges) for result in results],
        }
        print(json.dumps(payload, indent=2))
        return 0
    finally:
        graph.close()


def _inspect(args: argparse.Namespace) -> int:
    graph = read_rdb(args.index)
    payload = {
        "format_version": graph.metadata.get("format_version"),
        "nodes": graph.node_count,
        "directed_edges": graph.edge_count,
        "snap_geometries": _snap_geometry_count(graph),
        "snap_tiles": _snap_tile_count(graph),
        "components": getattr(graph, "component_count", None) if getattr(graph, "has_components", False) else None,
        "component_index": getattr(graph, "has_components", False),
        "origin_x_m": graph.origin_x_m,
        "origin_y_m": graph.origin_y_m,
        "coord_scale": graph.coord_scale,
        "length_scale": graph.length_scale,
        "tile_size_m": graph.snap_tile_size_m,
    }
    print(json.dumps(payload, indent=2))
    return 0


def _bench(args: argparse.Namespace) -> int:
    graph = read_rdb(args.index)
    rng = random.Random(args.seed)
    bounds = graph.bounds() if args.sample == "bbox" else None
    workspace = QueryWorkspace(graph.node_count)
    timings_ms: list[float] = []
    successes = 0
    failures = 0
    for _ in range(args.pairs):
        if args.sample == "nodes":
            origin_idx = rng.randrange(graph.node_count)
            target_idx = rng.randrange(graph.node_count)
            origin = graph.node_xy(origin_idx)
            target = graph.node_xy(target_idx)
        else:
            assert bounds is not None
            origin = (rng.uniform(bounds[0], bounds[2]), rng.uniform(bounds[1], bounds[3]))
            target = (rng.uniform(bounds[0], bounds[2]), rng.uniform(bounds[1], bounds[3]))
        started = time.perf_counter()
        try:
            route_nodes(graph, origin, target, workspace=workspace)
        except ValueError:
            failures += 1
            continue
        timings_ms.append((time.perf_counter() - started) * 1000)
        successes += 1

    payload = {
        "pairs": args.pairs,
        "sample": args.sample,
        "successes": successes,
        "failures": failures,
        "median_ms": round(statistics.median(timings_ms), 3) if timings_ms else None,
        "p95_ms": round(_percentile(timings_ms, 0.95), 3) if timings_ms else None,
    }
    print(json.dumps(payload, indent=2))
    return 0


def _snap_payload(snap) -> dict[str, float | int]:
    return {
        "geom_id": snap.geom_id,
        "distance_m": round(snap.distance_m, 3),
        "along_m": round(snap.along_m, 3),
        "x": round(snap.projected_x, 3),
        "y": round(snap.projected_y, 3),
    }


def _route_payload(result, *, with_edges: bool) -> dict[str, object]:
    payload: dict[str, object] = {
        "node_count": len(result.node_ids),
        "nodes": result.node_ids,
        "cost_m": round(result.cost_m, 3),
        "origin_snap": _snap_payload(result.origin_snap),
        "target_snap": _snap_payload(result.target_snap),
    }
    if with_edges:
        payload["edges"] = result.edge_ids
    return payload


def _snap_geometry_count(graph) -> int:
    return getattr(graph, "snap_geometry_count", len(getattr(graph, "snap_geometries", ())))


def _snap_tile_count(graph) -> int:
    return getattr(graph, "snap_tile_count", len(getattr(graph, "snap_tiles", ())))


def _percentile(values: list[float], p: float) -> float:
    values = sorted(values)
    if not values:
        raise ValueError("empty values")
    idx = min(len(values) - 1, max(0, int(round((len(values) - 1) * p))))
    return values[idx]
