#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from roads_rdb.geo import parse_lat_lon, wgs84_to_bng
from roads_rdb.graph import route_nodes
from roads_rdb.rdb import read_rdb


DEFAULT_FROM = "52.591905,-1.9786927"
DEFAULT_TO = "52.5934361,-1.9675859"


@dataclass(slots=True)
class LinkInfo:
    fid: int
    name: str | None
    number: str | None
    classification: str
    function: str
    form: str
    length_m: float

    @property
    def label(self) -> str:
        if self.name:
            return self.name
        if self.number:
            return self.number
        return self.classification or self.function or "unnamed road"


@dataclass(slots=True)
class Step:
    instruction: str
    road: str
    distance_m: float
    edge_count: int


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
            {
                "instruction": step.instruction,
                "road": step.road,
                "distance_m": round(step.distance_m, 1),
                "edge_count": step.edge_count,
            }
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


def fetch_link_infos(gpkg_path: str, geom_ids: list[int]) -> dict[int, LinkInfo]:
    # For a full build, snap geometry IDs are assigned in road_link fid order.
    # road_link.fid is 1-based, geom_id is 0-based.
    fids = sorted({geom_id + 1 for geom_id in geom_ids})
    conn = sqlite3.connect(gpkg_path)
    try:
        out: dict[int, LinkInfo] = {}
        for chunk_start in range(0, len(fids), 900):
            chunk = fids[chunk_start : chunk_start + 900]
            placeholders = ",".join("?" for _ in chunk)
            sql = f"""
                select fid, name_1, road_classification_number, road_classification,
                       road_function, form_of_way, length
                from road_link
                where fid in ({placeholders})
            """
            for row in conn.execute(sql, chunk):
                fid, name, number, classification, function, form, length = row
                out[fid] = LinkInfo(
                    fid=fid,
                    name=name,
                    number=number,
                    classification=classification,
                    function=function,
                    form=form,
                    length_m=float(length),
                )
        return out
    finally:
        conn.close()


def build_steps(graph, node_ids: list[int], edge_ids: list[int], geom_ids: list[int], links: dict[int, LinkInfo]) -> list[Step]:
    groups: list[dict] = []
    for edge_index, (edge_id, geom_id) in enumerate(zip(edge_ids, geom_ids)):
        link = links.get(geom_id + 1)
        road = link.label if link else "unknown road"
        _target, distance_m = graph.edge_target_and_length_m(edge_id)
        if groups and groups[-1]["road"] == road:
            groups[-1]["distance_m"] += distance_m
            groups[-1]["edge_count"] += 1
            groups[-1]["end_edge_index"] = edge_index
        else:
            groups.append(
                {
                    "road": road,
                    "distance_m": distance_m,
                    "edge_count": 1,
                    "start_edge_index": edge_index,
                    "end_edge_index": edge_index,
                }
            )

    steps: list[Step] = []
    for group_index, group in enumerate(groups):
        road = group["road"]
        if group_index == 0:
            instruction = f"Start on {road}"
        else:
            turn = describe_turn(graph, node_ids, group["start_edge_index"])
            if turn == "continue":
                instruction = f"Continue onto {road}"
            else:
                instruction = f"{turn.capitalize()} onto {road}"
        steps.append(Step(instruction, road, group["distance_m"], group["edge_count"]))
    steps.append(Step("Arrive at destination", "destination", 0.0, 0))
    return steps


def describe_turn(graph, node_ids: list[int], start_edge_index: int) -> str:
    if start_edge_index <= 0 or start_edge_index + 1 >= len(node_ids):
        return "continue"
    prev_xy = graph.node_xy(node_ids[start_edge_index - 1])
    at_xy = graph.node_xy(node_ids[start_edge_index])
    next_xy = graph.node_xy(node_ids[start_edge_index + 1])
    incoming = _bearing(prev_xy, at_xy)
    outgoing = _bearing(at_xy, next_xy)
    delta = ((outgoing - incoming + 540) % 360) - 180
    abs_delta = abs(delta)
    if abs_delta < 25:
        return "continue"
    if abs_delta < 60:
        return "slight right" if delta > 0 else "slight left"
    if abs_delta < 135:
        return "turn right" if delta > 0 else "turn left"
    return "make a U-turn"


def _bearing(a: tuple[float, float], b: tuple[float, float]) -> float:
    dx = b[0] - a[0]
    dy = b[1] - a[1]
    return math.degrees(math.atan2(dx, dy)) % 360


if __name__ == "__main__":
    raise SystemExit(main())
