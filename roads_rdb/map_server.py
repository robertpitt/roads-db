from __future__ import annotations

import argparse
import json
import mimetypes
import sqlite3
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from .directions import build_steps, fetch_link_infos_from_connection
from .geo import bng_to_wgs84, wgs84_to_bng
from .graph import QueryWorkspace, route_nodes
from .rdb import read_rdb


STATIC_DIR = Path(__file__).resolve().parent / "static"
MAX_REQUEST_BYTES = 16 * 1024


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="roads-rdb-map")
    parser.add_argument("--index", default="build/full_gb.rdb")
    parser.add_argument("--gpkg", default="oproad_gpkg_gb/Data/oproad_gb.gpkg")
    parser.add_argument("--no-directions", action="store_true")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--snap-radius", type=float, default=250.0)
    parser.add_argument("--snap-candidates", type=int, default=8)
    args = parser.parse_args(argv)

    graph = read_rdb(args.index)
    gpkg_path = None if args.no_directions else Path(args.gpkg)
    if gpkg_path is not None and not gpkg_path.exists():
        gpkg_path = None
    try:
        with RouteMapServer(
            (args.host, args.port),
            RouteMapHandler,
            graph=graph,
            gpkg_path=gpkg_path,
            snap_radius_m=args.snap_radius,
            snap_candidates=args.snap_candidates,
        ) as server:
            print(f"Route map: http://{args.host}:{server.server_port}/")
            if gpkg_path is not None:
                print(f"Directions: {gpkg_path}")
            else:
                print("Directions: disabled")
            server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        graph.close()
    return 0


class RouteMapServer(ThreadingHTTPServer):
    daemon_threads = True

    def process_request_thread(self, request, client_address) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            conn = getattr(self.thread_state, "direction_conn", None)
            if conn is not None:
                conn.close()

    def __init__(
        self,
        server_address,
        RequestHandlerClass,
        *,
        graph,
        gpkg_path: Path | None,
        snap_radius_m: float,
        snap_candidates: int,
    ) -> None:
        super().__init__(server_address, RequestHandlerClass)
        self.graph = graph
        self.gpkg_path = gpkg_path
        self.snap_radius_m = snap_radius_m
        self.snap_candidates = snap_candidates
        self.thread_state = threading.local()

    def workspace(self) -> QueryWorkspace:
        workspace = getattr(self.thread_state, "workspace", None)
        if workspace is None:
            workspace = QueryWorkspace(self.graph.node_count)
            self.thread_state.workspace = workspace
        return workspace

    def direction_connection(self) -> sqlite3.Connection | None:
        if self.gpkg_path is None:
            return None
        conn = getattr(self.thread_state, "direction_conn", None)
        if conn is None:
            uri = self.gpkg_path.resolve().as_uri() + "?mode=ro"
            conn = sqlite3.connect(uri, uri=True)
            self.thread_state.direction_conn = conn
        return conn


class RouteMapHandler(BaseHTTPRequestHandler):
    server: RouteMapServer

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path in ("", "/"):
            self._send_static("route_map.html")
            return
        if parsed.path.startswith("/static/"):
            self._send_static(parsed.path.removeprefix("/static/"))
            return
        if parsed.path == "/health":
            self._send_json({"ok": True})
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path != "/api/route":
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        try:
            body = self._read_json()
            origin = _lat_lon(body, "origin")
            target = _lat_lon(body, "target")
            payload = self._route(origin, target)
        except ValueError as exc:
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return
        self._send_json(payload)

    def log_message(self, format: str, *args) -> None:
        return

    def _route(self, origin_lat_lon: tuple[float, float], target_lat_lon: tuple[float, float]) -> dict:
        origin_bng = wgs84_to_bng(*origin_lat_lon)
        target_bng = wgs84_to_bng(*target_lat_lon)
        started = time.perf_counter()
        route = route_nodes(
            self.server.graph,
            origin_bng,
            target_bng,
            snap_radius_m=self.server.snap_radius_m,
            snap_candidates=self.server.snap_candidates,
            workspace=self.server.workspace(),
        )
        elapsed_ms = (time.perf_counter() - started) * 1000

        bng_path = (
            [(route.origin_snap.projected_x, route.origin_snap.projected_y)]
            + [self.server.graph.node_xy(node_id) for node_id in route.node_ids]
            + [(route.target_snap.projected_x, route.target_snap.projected_y)]
        )
        coordinates = [[lat, lon] for lat, lon in (bng_to_wgs84(x, y) for x, y in bng_path)]
        origin_snap = bng_to_wgs84(route.origin_snap.projected_x, route.origin_snap.projected_y)
        target_snap = bng_to_wgs84(route.target_snap.projected_x, route.target_snap.projected_y)
        geom_ids = [self.server.graph.edge_geom_id(edge_id) for edge_id in route.edge_ids]
        directions_error = None
        try:
            steps = self._steps_payload(route.node_ids, route.edge_ids, geom_ids)
        except sqlite3.Error as exc:
            steps = []
            directions_error = str(exc)
        return {
            "cost_m": round(route.cost_m, 1),
            "elapsed_ms": round(elapsed_ms, 1),
            "node_count": len(route.node_ids),
            "edge_count": len(route.edge_ids),
            "origin_snap_m": round(route.origin_snap.distance_m, 1),
            "target_snap_m": round(route.target_snap.distance_m, 1),
            "origin_bng": [round(origin_bng[0], 3), round(origin_bng[1], 3)],
            "target_bng": [round(target_bng[0], 3), round(target_bng[1], 3)],
            "origin_snap": [origin_snap[0], origin_snap[1]],
            "target_snap": [target_snap[0], target_snap[1]],
            "coordinates": coordinates,
            "directions_available": self.server.gpkg_path is not None,
            "directions_error": directions_error,
            "steps": steps,
        }

    def _steps_payload(self, node_ids: list[int], edge_ids: list[int], geom_ids: list[int]) -> list[dict]:
        conn = self.server.direction_connection()
        if conn is None:
            return []
        fids = sorted({geom_id + 1 for geom_id in geom_ids})
        links = fetch_link_infos_from_connection(conn, fids)
        steps = build_steps(self.server.graph, node_ids, edge_ids, geom_ids, links)
        return [step.to_payload() for step in steps]

    def _send_static(self, relative_path: str) -> None:
        path = (STATIC_DIR / relative_path).resolve()
        if not path.is_file() or STATIC_DIR not in path.parents:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        data = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _read_json(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("invalid content length") from exc
        if length <= 0:
            raise ValueError("request body is required")
        if length > MAX_REQUEST_BYTES:
            raise ValueError(f"request body must be at most {MAX_REQUEST_BYTES} bytes")
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError("request body must be JSON") from exc
        if not isinstance(payload, dict):
            raise ValueError("request body must be a JSON object")
        return payload

    def _send_json(self, payload: dict, *, status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def _lat_lon(payload: dict, key: str) -> tuple[float, float]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"{key} must be an object")
    try:
        lat = float(value["lat"])
        lon = float(value["lon"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"{key} must contain numeric lat and lon") from exc
    if not 49.0 <= lat <= 61.0 or not -9.0 <= lon <= 3.0:
        raise ValueError(f"{key} is outside the expected Great Britain bounds")
    return lat, lon


if __name__ == "__main__":
    raise SystemExit(main())
