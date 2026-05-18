from __future__ import annotations

import sqlite3

from .graph import RoadGraph


def write_lookup_db(path: str, graph: RoadGraph) -> None:
    """Write the companion lookup database keyed by dense node/edge IDs."""

    from pathlib import Path

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.executescript(
            """
            drop table if exists nodes;
            drop table if exists links;
            drop table if exists edges;
            drop table if exists snap_geometries;
            create table nodes (
              node_id integer primary key,
              source_node_id text not null,
              x real not null,
              y real not null
            );
            create table links (
              link_id integer primary key,
              source_link_id text not null
            );
            create table edges (
              edge_id integer primary key,
              source_link_id text not null,
              from_node integer not null,
              to_node integer not null,
              length_m real not null
            );
            create table snap_geometries (
              geom_id integer primary key,
              source_link_id text not null,
              start_node integer not null,
              end_node integer not null,
              forward_edge integer not null,
              reverse_edge integer
            );
            """
        )
        conn.executemany(
            "insert into nodes(node_id, source_node_id, x, y) values (?, ?, ?, ?)",
            (
                (idx, source_id, graph.node_xy(idx)[0], graph.node_xy(idx)[1])
                for idx, source_id in enumerate(graph.source_node_ids)
            ),
        )
        conn.executemany(
            "insert into links(link_id, source_link_id) values (?, ?)",
            enumerate(graph.source_link_ids),
        )
        conn.executemany(
            "insert into edges(edge_id, source_link_id, from_node, to_node, length_m) values (?, ?, ?, ?, ?)",
            (
                (
                    edge_id,
                    graph.source_link_ids[graph.edge_source_link[edge_id]],
                    graph.edge_from[edge_id],
                    graph.edge_to[edge_id],
                    graph.edge_length[edge_id] / graph.length_scale,
                )
                for edge_id in range(graph.edge_count)
            ),
        )
        conn.executemany(
            """
            insert into snap_geometries(
              geom_id, source_link_id, start_node, end_node, forward_edge, reverse_edge
            ) values (?, ?, ?, ?, ?, ?)
            """,
            (
                (
                    geom_id,
                    graph.source_link_ids[graph.edge_source_link[geom.forward_edge]],
                    geom.start_node,
                    geom.end_node,
                    geom.forward_edge,
                    geom.reverse_edge,
                )
                for geom_id, geom in enumerate(graph.snap_geometries)
            ),
        )
        conn.commit()
    finally:
        conn.close()
