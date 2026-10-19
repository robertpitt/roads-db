from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass
from pathlib import Path


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

    def to_payload(self) -> dict[str, str | float | int]:
        return {
            "instruction": self.instruction,
            "road": self.road,
            "distance_m": round(self.distance_m, 1),
            "edge_count": self.edge_count,
        }


def fetch_link_infos(gpkg_path: str | Path, geom_ids: list[int]) -> dict[int, LinkInfo]:
    # For a full build, snap geometry IDs are assigned in road_link fid order.
    # road_link.fid is 1-based, geom_id is 0-based.
    fids = sorted({geom_id + 1 for geom_id in geom_ids})
    conn = sqlite3.connect(gpkg_path)
    try:
        return fetch_link_infos_from_connection(conn, fids)
    finally:
        conn.close()


def fetch_link_infos_from_connection(conn: sqlite3.Connection, fids: list[int]) -> dict[int, LinkInfo]:
    out: dict[int, LinkInfo] = {}
    for chunk_start in range(0, len(fids), 900):
        chunk = fids[chunk_start : chunk_start + 900]
        if not chunk:
            continue
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
