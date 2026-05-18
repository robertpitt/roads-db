import sqlite3
import struct
import unittest

from roads_rdb.gpkg import LineString, Point, decode_gpkg_geometry, iter_links, iter_nodes


def _gpkg_header(envelope=None):
    flags = 0x01
    envelope_blob = b""
    if envelope is not None:
        flags |= 0x02
        minx, miny, maxx, maxy = envelope
        envelope_blob = struct.pack("<dddd", minx, maxx, miny, maxy)

    return b"GP" + bytes([0, flags]) + struct.pack("<i", 27700) + envelope_blob


def _point_blob(x, y, envelope=None):
    return _gpkg_header(envelope) + struct.pack("<BIdd", 1, 1, x, y)


def _linestring_blob(points, envelope=None):
    blob = _gpkg_header(envelope) + struct.pack("<BII", 1, 2, len(points))
    for x, y in points:
        blob += struct.pack("<dd", x, y)
    return blob


class DecodeGeoPackageGeometryTests(unittest.TestCase):
    def test_decodes_point(self):
        self.assertEqual(
            decode_gpkg_geometry(_point_blob(12.5, -3.25)),
            Point(12.5, -3.25),
        )

    def test_decodes_linestring_after_geopackage_envelope(self):
        self.assertEqual(
            decode_gpkg_geometry(
                _linestring_blob(
                    [(1.0, 2.0), (3.0, 4.0), (5.5, 6.5)],
                    envelope=(1.0, 2.0, 5.5, 6.5),
                )
            ),
            LineString((Point(1.0, 2.0), Point(3.0, 4.0), Point(5.5, 6.5))),
        )

    def test_rejects_invalid_geopackage_magic(self):
        with self.assertRaisesRegex(ValueError, "invalid magic"):
            decode_gpkg_geometry(b"xx\x00\x01" + struct.pack("<i", 27700))

    def test_rejects_unsupported_wkb_type(self):
        blob = _gpkg_header() + struct.pack("<BI", 1, 3)
        with self.assertRaisesRegex(ValueError, "unsupported WKB geometry type"):
            decode_gpkg_geometry(blob)


class GeoPackageIteratorTests(unittest.TestCase):
    def test_iter_nodes_filters_bbox_without_rtree_and_limits_after_filtering(self):
        conn = sqlite3.connect(":memory:")
        conn.execute(
            """
            CREATE TABLE road_node (
              fid INTEGER PRIMARY KEY,
              geometry BLOB,
              id TEXT,
              form_of_road_node TEXT
            )
            """
        )
        conn.executemany(
            "INSERT INTO road_node VALUES (?, ?, ?, ?)",
            [
                (1, _point_blob(1.0, 2.0), "n1", "junction"),
                (2, _point_blob(100.0, 100.0), "n2", "road end"),
                (3, _point_blob(5.0, 6.0), "n3", "pseudo node"),
            ],
        )

        self.assertEqual(
            list(iter_nodes(conn, bbox=(0.0, 0.0, 10.0, 10.0), limit=2)),
            [
                {"fid": 1, "id": "n1", "x": 1.0, "y": 2.0, "form": "junction"},
                {"fid": 3, "id": "n3", "x": 5.0, "y": 6.0, "form": "pseudo node"},
            ],
        )

    def test_iter_links_decodes_geometry_and_filters_with_rtree(self):
        conn = sqlite3.connect(":memory:")
        conn.execute(
            """
            CREATE TABLE road_link (
              fid INTEGER PRIMARY KEY,
              geometry BLOB,
              id TEXT,
              start_node TEXT,
              end_node TEXT,
              length REAL,
              road_classification TEXT,
              road_function TEXT,
              primary_route BOOLEAN,
              trunk_road BOOLEAN
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE rtree_road_link_geometry (
              id INTEGER PRIMARY KEY,
              minx REAL,
              maxx REAL,
              miny REAL,
              maxy REAL
            )
            """
        )
        conn.executemany(
            "INSERT INTO road_link VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    1,
                    _linestring_blob([(100.0, 100.0), (200.0, 200.0)]),
                    "l1",
                    "n1",
                    "n2",
                    140.0,
                    "Unclassified",
                    "Local Road",
                    0,
                    0,
                ),
                (
                    2,
                    _linestring_blob([(5.0, 5.0), (20.0, 20.0)]),
                    "l2",
                    "n2",
                    "n3",
                    21.0,
                    "A Road",
                    "A Road",
                    1,
                    0,
                ),
            ],
        )
        conn.executemany(
            "INSERT INTO rtree_road_link_geometry VALUES (?, ?, ?, ?, ?)",
            [
                (1, 100.0, 200.0, 100.0, 200.0),
                (2, 5.0, 20.0, 5.0, 20.0),
            ],
        )

        self.assertEqual(
            list(iter_links(conn, bbox=(0.0, 0.0, 10.0, 10.0), limit=1)),
            [
                {
                    "fid": 2,
                    "id": "l2",
                    "start_node": "n2",
                    "end_node": "n3",
                    "length": 21.0,
                    "road_classification": "A Road",
                    "road_function": "A Road",
                    "primary_route": 1,
                    "trunk_road": 0,
                    "geometry": LineString((Point(5.0, 5.0), Point(20.0, 20.0))),
                }
            ],
        )

    def test_iter_links_filters_bbox_without_rtree(self):
        conn = sqlite3.connect(":memory:")
        conn.execute(
            """
            CREATE TABLE road_link (
              fid INTEGER PRIMARY KEY,
              geometry BLOB,
              id TEXT,
              start_node TEXT,
              end_node TEXT,
              length REAL,
              road_classification TEXT,
              road_function TEXT,
              primary_route BOOLEAN,
              trunk_road BOOLEAN
            )
            """
        )
        conn.executemany(
            "INSERT INTO road_link VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    1,
                    _linestring_blob([(100.0, 100.0), (200.0, 200.0)]),
                    "l1",
                    "n1",
                    "n2",
                    140.0,
                    "Unclassified",
                    "Local Road",
                    0,
                    0,
                ),
                (
                    2,
                    _linestring_blob([(2.0, 2.0), (3.0, 3.0)]),
                    "l2",
                    "n2",
                    "n3",
                    1.5,
                    "B Road",
                    "B Road",
                    0,
                    1,
                ),
            ],
        )

        rows = list(iter_links(conn, bbox=(0.0, 0.0, 10.0, 10.0)))
        self.assertEqual([row["fid"] for row in rows], [2])
        self.assertEqual(rows[0]["geometry"], LineString((Point(2.0, 2.0), Point(3.0, 3.0))))


if __name__ == "__main__":
    unittest.main()
