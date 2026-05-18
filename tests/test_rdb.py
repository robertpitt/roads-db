import tempfile
import unittest
from pathlib import Path

from roads_rdb.gpkg import Point
from roads_rdb.graph import QueryWorkspace, RoadGraph, SnapGeometry, route_nodes
from roads_rdb.rdb import read_rdb, write_rdb


class RdbRoundTripTests(unittest.TestCase):
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
            finally:
                loaded.close()

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
