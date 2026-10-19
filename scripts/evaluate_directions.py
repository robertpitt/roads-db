#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from roads_rdb.directions import build_steps, fetch_link_infos
from roads_rdb.geo import parse_lat_lon, wgs84_to_bng
from roads_rdb.graph import route_nodes
from roads_rdb.rdb import read_rdb


DEFAULT_FROM = "52.591905,-1.9786927"
DEFAULT_TO = "52.5934361,-1.9675859"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--index", default="build/full_gb.rdb")
    parser.add_argument("--gpkg", default="oproad_gpkg_gb/Data/oproad_gb.gpkg")
    parser.add_argument("--from", dest="origin", default=DEFAULT_FROM, help="WGS84 lat,lon")
    parser.add_argument("--to", dest="target", default=DEFAULT_TO, help="WGS84 lat,lon")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    origin_latlon = parse_lat_lon(args.origin)
    target_latlon = parse_lat_lon(args.target)
    origin_bng = wgs84_to_bng(*origin_latlon)
    target_bng = wgs84_to_bng(*target_latlon)

    graph = read_rdb(args.index)
    try:
        route = route_nodes(graph, origin_bng, target_bng, snap_radius_m=300, snap_candidates=12)
        geom_ids = [graph.edge_geom_id(edge_id) for edge_id in route.edge_ids]
        link_infos = fetch_link_infos(args.gpkg, geom_ids)
        steps = build_steps(graph, route.node_ids, route.edge_ids, geom_ids, link_infos)
    finally:
        graph.close()

    payload = {
        "from": {"lat": origin_latlon[0], "lon": origin_latlon[1], "bng": [round(origin_bng[0], 3), round(origin_bng[1], 3)]},
        "to": {"lat": target_latlon[0], "lon": target_latlon[1], "bng": [round(target_bng[0], 3), round(target_bng[1], 3)]},
        "route": {
            "node_count": len(route.node_ids),
            "edge_count": len(route.edge_ids),
            "cost_m": round(route.cost_m, 1),
            "origin_snap_m": round(route.origin_snap.distance_m, 1),
            "target_snap_m": round(route.target_snap.distance_m, 1),
        },
        "steps": [
            step.to_payload()
            for step in steps
        ],
    }

    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"Origin BNG: {payload['from']['bng']}")
        print(f"Target BNG: {payload['to']['bng']}")
        print(
            f"Route: {payload['route']['cost_m']} m, "
            f"{payload['route']['node_count']} nodes, {payload['route']['edge_count']} edges"
        )
        print(
            f"Snap: origin {payload['route']['origin_snap_m']} m, "
            f"target {payload['route']['target_snap_m']} m"
        )
        print()
        for idx, step in enumerate(steps, start=1):
            print(f"{idx}. {step.instruction} ({step.distance_m:.0f} m)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
