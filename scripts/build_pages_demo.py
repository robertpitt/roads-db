"""Build a bounded RDB and matching road-name SQLite file for the Pages demo."""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sqlite3
import sys
from collections import Counter
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from roads_rdb.geo import bng_to_wgs84
from roads_rdb.graph import build_graph_from_gpkg, parse_bbox, route_nodes
from roads_rdb.rdb import read_rdb, write_rdb


def build_demo(source: Path, output: Path, bbox: tuple, *, source_date: str,
               source_md5: str = "", attribution: str = "Contains OS data © Crown Copyright and database rights 2026.") -> dict:
    output.mkdir(parents=True, exist_ok=True)
    graph = build_graph_from_gpkg(str(source), bbox=bbox, keep_source_ids=True)
    index = output / "london.rdb"
    write_rdb(index, graph)
    database = output / "london.sqlite"
    database.unlink(missing_ok=True)
    # Join source IDs explicitly: bbox builds do not preserve fid = geom_id + 1.
    with closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)) as src:
        src.execute("CREATE TEMP TABLE selected(geom_id INTEGER, source_id TEXT)")
        src.executemany("INSERT INTO selected VALUES (?, ?)", (
            (i, graph.source_link_ids[graph.edge_source_link[g.forward_edge]])
            for i, g in enumerate(graph.snap_geometries)
        ))
        rows = src.execute("""SELECT s.geom_id, l.id, l.fid, l.name_1,
            l.road_classification_number, l.road_classification
            FROM selected s JOIN road_link l ON l.id=s.source_id ORDER BY s.geom_id""").fetchall()
    if len(rows) != len(graph.snap_geometries):
        raise ValueError("road metadata does not match the routing graph")
    with closing(sqlite3.connect(database)) as db:
        db.execute("""CREATE TABLE roads(geom_id INTEGER PRIMARY KEY,
            source_id TEXT NOT NULL, source_fid INTEGER NOT NULL, name TEXT,
            number TEXT, classification TEXT)""")
        db.executemany("INSERT INTO roads VALUES (?, ?, ?, ?, ?, ?)", rows)
        db.commit()
        db.execute("VACUUM")

    files = {}
    for path in (index, database):
        size = path.stat().st_size
        if size > 40 * 1024 * 1024:
            raise ValueError(f"{path.name} exceeds the demo's 40 MiB file budget")
        files[path.name] = {"bytes": size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    largest = Counter(graph.component_ids).most_common(1)[0][0]
    nodes = [i for i, c in enumerate(graph.component_ids) if c == largest]
    rng = random.Random(42)
    checks = []
    with closing(read_rdb(index)) as query_graph:
        for _ in range(min(20, len(nodes))):
            a, b = rng.sample(nodes, 2)
            start, end = graph.node_xy(a), graph.node_xy(b)
            route = route_nodes(query_graph, start, end, snap_radius_m=1, snap_candidates=8)
            checks.append({"from_bng": start, "to_bng": end, "cost_m": route.cost_m})
    (output / "parity.json").write_text(json.dumps(checks, separators=(",", ":")) + "\n")
    sw = bng_to_wgs84(bbox[0], bbox[1]); ne = bng_to_wgs84(bbox[2], bbox[3])
    manifest = {
        "name": "Central London", "format_version": 1,
        "bbox_bng": bbox, "bounds": [sw, ne], "source_date": source_date,
        "source_url": "https://api.os.uk/downloads/v1/products/OpenRoads/downloads",
        "source_archive_md5": source_md5, "attribution": attribution,
        "nodes": graph.node_count, "directed_edges": graph.edge_count,
        "roads": len(rows), "components": graph.component_count, "files": files,
    }
    manifest["id"] = files[index.name]["sha256"][:16] + files[database.name]["sha256"][:16]
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    (output / "NOTICE.txt").write_text(attribution + "\nOS Open Roads under the Open Government Licence v3.0.\n"
        "https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/\n"
        f"Downloaded {source_date}; bounded extract, not navigation data.\n", encoding="utf-8")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("demo/data"))
    parser.add_argument("--bbox", default="520000,174000,540000,188000")
    parser.add_argument("--source-date", required=True, help="date the source snapshot was downloaded")
    parser.add_argument("--source-md5", default="")
    args = parser.parse_args()
    print(json.dumps(build_demo(args.input, args.output, parse_bbox(args.bbox),
                               source_date=args.source_date, source_md5=args.source_md5), indent=2))


if __name__ == "__main__":
    main()
