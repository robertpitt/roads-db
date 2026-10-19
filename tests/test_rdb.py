import struct
import tempfile
import unittest
from pathlib import Path

from roads_rdb.gpkg import Point
from roads_rdb.graph import QueryWorkspace, RoadGraph, SnapGeometry, route_nodes, route_nodes_batch
from roads_rdb.rdb import read_rdb, write_rdb


class RdbRoundTripTests(unittest.TestCase):
    def test_rejects_invalid_headers_and_truncated_directory(self):
        cases = [
            (b"", ""),
            (b"RDB1", "too small"),
            (struct.pack("<4sII", b"BAD!", 1, 0), "invalid RDB magic"),
            (struct.pack("<4sII", b"RDB1", 2, 0), "unsupported RDB version"),
            (struct.pack("<4sII", b"RDB1", 1, 1), "directory extends beyond"),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "invalid.rdb"
            for data, error in cases:
                with self.subTest(data=data):
                    path.write_bytes(data)
                    with self.assertRaisesRegex(ValueError, error):
                        read_rdb(path)
                    # Failed reads must release the file, including on Windows.
                    path.unlink()

    def test_round_trips_graph_and_routes(self):
        graph = RoadGraph(
            origin_x_m=0,
            origin_y_m=0,
            coord_scale=10,
            length_scale=10,
            node_x=[0, 100, 200],
            node_y=[0, 0, 0],
            edge_from=[0, 1, 1, 2],
            edge_to=[1, 0, 2, 1],
            edge_length=[100, 100, 100, 100],
            reverse_edge=[1, 0, 3, 2],
            edge_geom=[0, 0, 1, 1],
            offsets=[0, 1, 3, 4],
            snap_geometries=[
                SnapGeometry(
                    points=(Point(0, 0), Point(10, 0)),
                    start_node=0,
                    end_node=1,
                    forward_edge=0,
                    reverse_edge=1,
                    length_m=10,
                    bbox=(0, 0, 10, 0),
                ),
                SnapGeometry(
                    points=(Point(10, 0), Point(20, 0)),
                    start_node=1,
                    end_node=2,
                    forward_edge=2,
                    reverse_edge=3,
                    length_m=10,
                    bbox=(10, 0, 20, 0),
                ),
            ],
            snap_tile_size_m=256,
            snap_tiles={(0, 0): [0, 1]},
            edge_source_link=[0, 0, 1, 1],
        )

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tiny.rdb"
            write_rdb(path, graph)
            loaded = read_rdb(path)
            try:
                self.assertEqual(loaded.node_count, 3)
                self.assertEqual(loaded.edge_count, 4)
                self.assertEqual(loaded.snap_geometry_count, 2)
                self.assertEqual(loaded.component_count, 1)
                self.assertEqual(loaded.metadata["format_version"], 1)
                self.assertEqual(loaded.sections["node_x"].size, 3 * 4)
                self.assertEqual(loaded.sections["node_y"].size, 3 * 4)
                self.assertEqual(loaded.sections["edge_to"].size, 4 * 4)
                self.assertEqual(loaded.sections["edge_length"].size, 4 * 4)
                self.assertEqual(loaded.sections["edge_geom"].size, 4 * 4)
                self.assertNotIn("nodes", loaded.sections)
                self.assertNotIn("edges", loaded.sections)
                self.assertEqual(loaded.sections["snap_geom_bboxes"].size, 2 * 16)
                for section in loaded.sections.values():
                    self.assertEqual(section.offset % 64, 0)
                self.assertNotIn("edge_reverse", loaded.sections)
                self.assertNotIn("edge_source_links", loaded.sections)
                self.assertEqual(loaded.edge_geom_id(2), 1)
                self.assertTrue(loaded.snap_geom_may_intersect_radius(0, 0, 0, 5))
                self.assertFalse(loaded.snap_geom_may_intersect_radius(1, 0, 0, 5))

                workspace = QueryWorkspace(loaded.node_count)
                result = route_nodes(
                    loaded,
                    (0, 0),
                    (20, 0),
                    snap_radius_m=5,
                    workspace=workspace,
                )
                self.assertEqual(result.node_ids, [0, 1, 2])
                self.assertEqual(result.edge_ids, [0, 2])
                self.assertAlmostEqual(result.cost_m, 20)

                batch_results = route_nodes_batch(
                    loaded,
                    (0, 0),
                    [(10, 0), (20, 0)],
                    snap_radius_m=5,
                    workspace=workspace,
                )
                self.assertEqual([result.node_ids for result in batch_results], [[0, 1], [0, 1, 2]])
                self.assertEqual([result.edge_ids for result in batch_results], [[0], [0, 2]])
            finally:
                loaded.close()

            sidecar_path = Path(tmp) / "tiny_sidecars.rdb"
            write_rdb(sidecar_path, graph, include_edge_reverse=True, include_edge_source_links=True)
            loaded = read_rdb(sidecar_path)
            try:
                self.assertIn("edge_reverse", loaded.sections)
                self.assertIn("edge_source_links", loaded.sections)
                self.assertEqual(loaded.edge_reverse_id(2), 3)
                self.assertEqual(loaded.edge_source_link_id(2), 1)
            finally:
                loaded.close()

            with path.open("r+b") as fp:
                fp.seek(4)
                fp.write(struct.pack("<I", 5))
            with self.assertRaisesRegex(ValueError, "unsupported RDB version"):
                read_rdb(path)

    def test_rejects_routes_across_components(self):
        graph = RoadGraph(
            origin_x_m=0,
            origin_y_m=0,
            coord_scale=10,
            length_scale=10,
            node_x=[0, 100, 1000, 1100],
            node_y=[0, 0, 0, 0],
            edge_from=[0, 1, 2, 3],
            edge_to=[1, 0, 3, 2],
            edge_length=[100, 100, 100, 100],
            reverse_edge=[1, 0, 3, 2],
            edge_geom=[0, 0, 1, 1],
            offsets=[0, 1, 2, 3, 4],
            snap_geometries=[
                SnapGeometry(
                    points=(Point(0, 0), Point(10, 0)),
                    start_node=0,
                    end_node=1,
                    forward_edge=0,
                    reverse_edge=1,
                    length_m=10,
                    bbox=(0, 0, 10, 0),
                ),
                SnapGeometry(
                    points=(Point(100, 0), Point(110, 0)),
                    start_node=2,
                    end_node=3,
                    forward_edge=2,
                    reverse_edge=3,
                    length_m=10,
                    bbox=(100, 0, 110, 0),
                ),
            ],
            snap_tile_size_m=256,
            snap_tiles={(0, 0): [0, 1]},
            edge_source_link=[0, 0, 1, 1],
        )

        self.assertEqual(graph.component_count, 2)
        with self.assertRaisesRegex(ValueError, "same component"):
            route_nodes(graph, (0, 0), (110, 0), snap_radius_m=5)


if __name__ == "__main__":
    unittest.main()
