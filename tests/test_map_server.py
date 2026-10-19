import http.client
import json
import tempfile
import threading
import unittest
from contextlib import closing
from pathlib import Path

from examples.create_demo_gpkg import create_demo_gpkg
from roads_rdb.geo import bng_to_wgs84
from roads_rdb.graph import build_graph_from_gpkg
from roads_rdb.map_server import MAX_REQUEST_BYTES, RouteMapHandler, RouteMapServer
from roads_rdb.rdb import read_rdb, write_rdb


class MapReleaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        source = Path(self.tmp.name) / "demo #1.gpkg"
        index = Path(self.tmp.name) / "demo.rdb"
        create_demo_gpkg(source)
        write_rdb(index, build_graph_from_gpkg(str(source), bbox=None))
        graph = read_rdb(index)
        self.addCleanup(graph.close)
        self.server = RouteMapServer(
            ("127.0.0.1", 0), RouteMapHandler, graph=graph, gpkg_path=source,
            snap_radius_m=5, snap_candidates=1,
        )
        self.addCleanup(self.server.server_close)
        thread = threading.Thread(target=self.server.serve_forever,
                                  kwargs={"poll_interval": 0.01}, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(self.server.shutdown)

    def request(self, method, path, body=None, headers=None):
        with closing(http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)) as conn:
            conn.request(method, path, body=body, headers=headers or {})
            response = conn.getresponse()
            return response.status, response.getheader("Content-Type"), response.read()

    def route_body(self):
        origin = bng_to_wgs84(530000, 180000)
        target = bng_to_wgs84(530200, 180000)
        return json.dumps({"origin": dict(zip(("lat", "lon"), origin)),
                           "target": dict(zip(("lat", "lon"), target))}).encode()

    def test_root_serves_packaged_map_and_attribution(self):
        status, content_type, body = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", content_type)
        self.assertIn(b"Roads RDB Map", body)
        self.assertIn(b"Contains OS data", body)
        self.assertIn(b"OpenStreetMap", body)

    def test_health_and_static_paths(self):
        self.assertEqual(json.loads(self.request("GET", "/health")[2]), {"ok": True})
        self.assertEqual(self.request("GET", "/static/route_map.html")[0], 200)
        self.assertEqual(self.request("GET", "/static/../rdb.py")[0], 404)
        self.assertEqual(self.request("GET", "/missing")[0], 404)

    def test_route_with_named_steps(self):
        status, _, body = self.request("POST", "/api/route", self.route_body())
        self.assertEqual(status, 200, body)
        route = json.loads(body)
        self.assertAlmostEqual(route["cost_m"], 200, delta=0.5)
        self.assertEqual(route["edge_count"], 2)
        self.assertEqual(route["steps"][0]["road"], "Demo Road")
        self.assertTrue(route["directions_available"])
        self.assertIsNone(route["directions_error"])
        self.assertGreaterEqual(len(route["coordinates"]), 3)

    def test_route_without_source_metadata(self):
        self.server.gpkg_path = None
        status, _, body = self.request("POST", "/api/route", self.route_body())
        self.assertEqual(status, 200, body)
        route = json.loads(body)
        self.assertFalse(route["directions_available"])
        self.assertEqual(route["steps"], [])

    def test_malformed_json_returns_400(self):
        for raw in (b"", b"{", b"[]", b"null", b'"text"', b"123", b"\xff"):
            with self.subTest(body=raw):
                status, content_type, body = self.request("POST", "/api/route", raw)
                self.assertEqual(status, 400, body)
                self.assertEqual(content_type, "application/json")
                self.assertIn("error", json.loads(body))

    def test_invalid_coordinates_return_400(self):
        for origin in (None, [], {}, {"lat": "x", "lon": 0},
                       {"lat": 0, "lon": 0}, {"lat": float("nan"), "lon": 0}):
            with self.subTest(origin=origin):
                raw = json.dumps({"origin": origin, "target": {"lat": 52, "lon": -2}})
                status, _, body = self.request("POST", "/api/route", raw.encode())
                self.assertEqual(status, 400, body)
                self.assertIn("error", json.loads(body))

    def test_invalid_and_oversized_lengths_return_400(self):
        for length in ("invalid", "-1", str(MAX_REQUEST_BYTES + 1)):
            with self.subTest(length=length):
                status, _, body = self.request("POST", "/api/route", b"{}",
                                               {"Content-Length": length})
                self.assertEqual(status, 400, body)
                self.assertIn("error", json.loads(body))


if __name__ == "__main__":
    unittest.main()
