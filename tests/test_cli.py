import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import roads_rdb
from examples.create_demo_gpkg import create_demo_gpkg


class CliReleaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.gpkg = self.root / "demo #1.gpkg"
        self.index = self.root / "demo.rdb"
        self.lookup = self.root / "lookup.sqlite"
        create_demo_gpkg(self.gpkg)

    def cli(self, *args, success=True):
        env = os.environ.copy()
        # Child processes use the same source or installed package under test.
        env["PYTHONPATH"] = str(Path(roads_rdb.__file__).resolve().parent.parent)
        result = subprocess.run(
            [sys.executable, "-m", "roads_rdb", *map(str, args)],
            cwd=self.root, env=env, capture_output=True, text=True, timeout=30,
        )
        if success:
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        return result

    def build(self, *args):
        return self.cli("build", "--input", self.gpkg, "--output", self.index, *args)

    def query(self, origin="530000,180000", target="530200,180000", success=True):
        return self.cli("query", "--index", self.index, "--from", origin,
                        "--to", target, "--snap-radius", "1", "--snap-candidates", "1",
                        "--with-edges", success=success)

    def test_build_inspect_query_batch_and_lookup(self):
        summary = self.build("--lookup", self.lookup)
        self.assertEqual(summary["nodes"], 6)
        self.assertEqual(summary["directed_edges"], 10)
        info = self.cli("inspect", "--index", self.index)
        self.assertEqual(info["format_version"], 1)
        self.assertEqual(info["components"], 2)
        route = self.query()
        self.assertEqual(route["nodes"], [0, 1, 2])
        self.assertEqual(len(route["edges"]), 2)
        self.assertEqual(route["cost_m"], 200)
        batch = self.cli("batch", "--index", self.index, "--from", "530000,180000",
                         "--to", "530100,180000", "--to", "530200,180000",
                         "--snap-radius", "1", "--snap-candidates", "1")
        self.assertEqual([r["cost_m"] for r in batch["routes"]], [100, 200])
        with sqlite3.connect(self.lookup) as conn:
            self.addCleanup(conn.close)
            self.assertEqual(conn.execute("SELECT count(*) FROM edges").fetchone()[0], 10)
            self.assertEqual(conn.execute("SELECT source_node_id FROM nodes WHERE node_id=0")
                             .fetchone()[0], "n1")

    def test_bbox_build_and_reverse_route(self):
        summary = self.build("--bbox", "529999,179999,530201,180001")
        self.assertEqual(summary["snap_geometries"], 4)
        self.assertEqual(self.query("530200,180000", "530000,180000")["cost_m"], 200)

    def test_partial_edge_snaps(self):
        self.build()
        self.assertEqual(self.query("530025,180000", "530175,180000")["cost_m"], 150)

    def test_disconnected_route_is_a_useful_error(self):
        self.build()
        result = self.query(target="531100,181000", success=False)
        self.assertIn("same component", result.stderr)

    def test_missing_input_does_not_create_a_database(self):
        missing = self.root / "missing.gpkg"
        result = self.cli("build", "--input", missing, "--output", self.index, success=False)
        self.assertIn("GeoPackage not found", result.stderr)
        self.assertFalse(missing.exists())
        self.assertFalse(self.index.exists())

    def test_missing_index_and_bad_coordinates_are_useful_errors(self):
        self.cli("inspect", "--index", self.index, success=False)
        self.build()
        self.query(origin="not-a-coordinate", success=False)


if __name__ == "__main__":
    unittest.main()
