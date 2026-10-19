"""Create a tiny, synthetic input using OS Open Roads tables and geometry blobs.

This is a reader fixture, not a complete GIS GeoPackage export. No Ordnance
Survey data is copied. Coordinates are EPSG:27700 metres near central London.
"""

from __future__ import annotations

import argparse
import sqlite3
import struct
from contextlib import closing
from pathlib import Path


def _geometry(kind: int, points: list[tuple[float, float]]) -> bytes:
    header = b"GP" + bytes([0, 1]) + struct.pack("<i", 27700)
    wkb = struct.pack("<BI", 1, kind)
    if kind == 2:
        wkb += struct.pack("<I", len(points))
    return header + wkb + b"".join(struct.pack("<dd", *point) for point in points)


def create_demo_gpkg(path: str | Path) -> None:
    path = Path(path)
    if path.exists():
        raise FileExistsError(f"refusing to overwrite {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    nodes = [
        (530000, 180000), (530100, 180000), (530200, 180000),
        (530100, 180100), (531000, 181000), (531100, 181000),
    ]
    # Two alternative routes between nodes 1 and 3, plus a disconnected road.
    links = [
        (1, 2, 100.0, "Demo Road"), (2, 3, 100.0, "Demo Road"),
        (1, 4, 141.4, "Long Way"), (4, 3, 141.4, "Long Way"),
        (5, 6, 100.0, "Island Road"),
    ]
    with closing(sqlite3.connect(path)) as conn:
        with conn:
            conn.executescript("""
                CREATE TABLE road_node (
                    fid INTEGER PRIMARY KEY, geometry BLOB NOT NULL,
                    id TEXT NOT NULL, form_of_road_node TEXT
                );
                CREATE TABLE road_link (
                    fid INTEGER PRIMARY KEY, geometry BLOB NOT NULL,
                    id TEXT NOT NULL, start_node TEXT, end_node TEXT,
                    length REAL, road_classification TEXT, road_function TEXT,
                    primary_route BOOLEAN, trunk_road BOOLEAN,
                    name_1 TEXT, road_classification_number TEXT, form_of_way TEXT
                );
            """)
            conn.executemany(
                "INSERT INTO road_node VALUES (?, ?, ?, ?)",
                [(i, _geometry(1, [point]), f"n{i}", "junction")
                 for i, point in enumerate(nodes, 1)],
            )
            conn.executemany(
                "INSERT INTO road_link VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [(i, _geometry(2, [nodes[start - 1], nodes[end - 1]]),
                  f"l{i}", f"n{start}", f"n{end}", length,
                  "Unclassified", "Local Road", 0, 0, name, None, "Single Carriageway")
                 for i, (start, end, length, name) in enumerate(links, 1)],
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", nargs="?", default="build/demo.gpkg")
    args = parser.parse_args()
    try:
        create_demo_gpkg(args.output)
    except OSError as exc:
        parser.exit(2, f"{parser.prog}: error: {exc}\n")
    print(f"Created synthetic input: {args.output}")


if __name__ == "__main__":
    main()
