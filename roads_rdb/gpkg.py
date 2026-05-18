"""Small GeoPackage helpers for OS Open Roads feature tables."""

from __future__ import annotations

from dataclasses import dataclass
import sqlite3
import struct
from typing import Iterator


@dataclass(frozen=True, slots=True)
class Point:
    x: float
    y: float


@dataclass(frozen=True, slots=True)
class LineString:
    points: tuple[Point, ...]


Geometry = Point | LineString
BBox = tuple[float, float, float, float]

_GPKG_MAGIC = b"GP"
_WKB_LITTLE_ENDIAN = 1
_WKB_POINT = 1
_WKB_LINESTRING = 2
_GPKG_ENVELOPE_BYTES = {
    0: 0,
    1: 32,  # minx, maxx, miny, maxy
    2: 48,  # minx, maxx, miny, maxy, minz, maxz
    3: 48,  # minx, maxx, miny, maxy, minm, maxm
    4: 64,  # minx, maxx, miny, maxy, minz, maxz, minm, maxm
}


def decode_gpkg_geometry(blob: bytes) -> Geometry:
    """Decode a GeoPackageBinary POINT or LINESTRING with little-endian WKB."""

    view = memoryview(blob)
    offset = _gpkg_wkb_offset(view)

    if len(view) < offset + 5:
        raise ValueError("GeoPackage geometry is missing a WKB header")

    byte_order = view[offset]
    if byte_order != _WKB_LITTLE_ENDIAN:
        raise ValueError("only little-endian WKB geometries are supported")

    geometry_type = struct.unpack_from("<I", view, offset + 1)[0]
    position = offset + 5

    if geometry_type == _WKB_POINT:
        point, position = _read_point(view, position)
        _reject_trailing_bytes(view, position)
        return point

    if geometry_type == _WKB_LINESTRING:
        if len(view) < position + 4:
            raise ValueError("LINESTRING geometry is missing its point count")

        point_count = struct.unpack_from("<I", view, position)[0]
        position += 4
        points: list[Point] = []
        for _ in range(point_count):
            point, position = _read_point(view, position)
            points.append(point)

        _reject_trailing_bytes(view, position)
        return LineString(tuple(points))

    raise ValueError(f"unsupported WKB geometry type: {geometry_type}")


def iter_nodes(conn: sqlite3.Connection, bbox=None, limit=None) -> Iterator[dict]:
    """Yield road node records as simple dictionaries.

    ``bbox`` is ``(minx, miny, maxx, maxy)`` in British National Grid metres.
    """

    bbox = _normalise_bbox(bbox)
    limit = _normalise_limit(limit)
    if limit == 0:
        return

    yielded = 0
    if bbox is not None and _table_exists(conn, "rtree_road_node_geometry"):
        sql = (
            'SELECT n.fid, n.geometry, n.id, n.form_of_road_node '
            'FROM "road_node" AS n '
            'JOIN "rtree_road_node_geometry" AS r ON r.id = n.fid '
            "WHERE r.maxx >= ? AND r.minx <= ? AND r.maxy >= ? AND r.miny <= ? "
            "ORDER BY n.fid"
        )
        params: list[object] = [bbox[0], bbox[2], bbox[1], bbox[3]]
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)

        for row in conn.execute(sql, params):
            yield _node_dict(row)
        return

    sql = (
        'SELECT fid, geometry, id, form_of_road_node '
        'FROM "road_node" '
        "ORDER BY fid"
    )
    params = []
    if bbox is None and limit is not None:
        sql += " LIMIT ?"
        params.append(limit)

    for row in conn.execute(sql, params):
        point = _expect_point(decode_gpkg_geometry(row[1]))
        if bbox is not None and not _bbox_intersects(_geometry_bbox(point), bbox):
            continue

        yield {
            "fid": row[0],
            "id": row[2],
            "x": point.x,
            "y": point.y,
            "form": row[3],
        }
        yielded += 1
        if limit is not None and yielded >= limit:
            return


def iter_links(conn: sqlite3.Connection, bbox=None, limit=None) -> Iterator[dict]:
    """Yield road link records as simple dictionaries."""

    bbox = _normalise_bbox(bbox)
    limit = _normalise_limit(limit)
    if limit == 0:
        return

    columns = (
        "fid, geometry, id, start_node, end_node, length, road_classification, "
        "road_function, primary_route, trunk_road"
    )
    yielded = 0

    if bbox is not None and _table_exists(conn, "rtree_road_link_geometry"):
        sql = (
            f'SELECT l.{columns.replace(", ", ", l.")} '
            'FROM "road_link" AS l '
            'JOIN "rtree_road_link_geometry" AS r ON r.id = l.fid '
            "WHERE r.maxx >= ? AND r.minx <= ? AND r.maxy >= ? AND r.miny <= ? "
            "ORDER BY l.fid"
        )
        params: list[object] = [bbox[0], bbox[2], bbox[1], bbox[3]]
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)

        for row in conn.execute(sql, params):
            yield _link_dict(row)
        return

    sql = f'SELECT {columns} FROM "road_link" ORDER BY fid'
    params = []
    if bbox is None and limit is not None:
        sql += " LIMIT ?"
        params.append(limit)

    for row in conn.execute(sql, params):
        geometry = _expect_linestring(decode_gpkg_geometry(row[1]))
        if bbox is not None and not _bbox_intersects(_geometry_bbox(geometry), bbox):
            continue

        yield _link_dict(row, geometry)
        yielded += 1
        if limit is not None and yielded >= limit:
            return


def _gpkg_wkb_offset(blob: memoryview) -> int:
    if len(blob) < 8:
        raise ValueError("GeoPackage geometry is too short")

    if blob[:2].tobytes() != _GPKG_MAGIC:
        raise ValueError("GeoPackage geometry has an invalid magic header")

    version = blob[2]
    if version != 0:
        raise ValueError(f"unsupported GeoPackageBinary version: {version}")

    flags = blob[3]
    if flags & 0xC0:
        raise ValueError("GeoPackage geometry uses reserved flag bits")
    if flags & 0x20:
        raise ValueError("extended GeoPackageBinary geometries are not supported")
    if flags & 0x10:
        raise ValueError("empty GeoPackage geometries are not supported")

    envelope_code = (flags >> 1) & 0x07
    try:
        envelope_bytes = _GPKG_ENVELOPE_BYTES[envelope_code]
    except KeyError as exc:
        raise ValueError(f"unsupported GeoPackage envelope code: {envelope_code}") from exc

    header_byte_order = "<" if flags & 0x01 else ">"
    struct.unpack_from(f"{header_byte_order}i", blob, 4)

    offset = 8 + envelope_bytes
    if len(blob) < offset:
        raise ValueError("GeoPackage geometry is shorter than its declared header")

    return offset


def _read_point(blob: memoryview, offset: int) -> tuple[Point, int]:
    if len(blob) < offset + 16:
        raise ValueError("POINT coordinate data is truncated")

    x, y = struct.unpack_from("<dd", blob, offset)
    return Point(x, y), offset + 16


def _reject_trailing_bytes(blob: memoryview, offset: int) -> None:
    if offset != len(blob):
        raise ValueError("geometry contains trailing bytes")


def _node_dict(row) -> dict:
    point = _expect_point(decode_gpkg_geometry(row[1]))
    return {
        "fid": row[0],
        "id": row[2],
        "x": point.x,
        "y": point.y,
        "form": row[3],
    }


def _link_dict(row, geometry: LineString | None = None) -> dict:
    if geometry is None:
        geometry = _expect_linestring(decode_gpkg_geometry(row[1]))

    return {
        "fid": row[0],
        "id": row[2],
        "start_node": row[3],
        "end_node": row[4],
        "length": row[5],
        "road_classification": row[6],
        "road_function": row[7],
        "primary_route": row[8],
        "trunk_road": row[9],
        "geometry": geometry,
    }


def _expect_point(geometry: Geometry) -> Point:
    if not isinstance(geometry, Point):
        raise ValueError("expected POINT geometry")
    return geometry


def _expect_linestring(geometry: Geometry) -> LineString:
    if not isinstance(geometry, LineString):
        raise ValueError("expected LINESTRING geometry")
    return geometry


def _geometry_bbox(geometry: Geometry) -> BBox | None:
    if isinstance(geometry, Point):
        return geometry.x, geometry.y, geometry.x, geometry.y

    if not geometry.points:
        return None

    xs = [point.x for point in geometry.points]
    ys = [point.y for point in geometry.points]
    return min(xs), min(ys), max(xs), max(ys)


def _bbox_intersects(left: BBox | None, right: BBox) -> bool:
    if left is None:
        return False

    return (
        left[2] >= right[0]
        and left[0] <= right[2]
        and left[3] >= right[1]
        and left[1] <= right[3]
    )


def _normalise_bbox(bbox) -> BBox | None:
    if bbox is None:
        return None

    try:
        minx, miny, maxx, maxy = bbox
    except (TypeError, ValueError) as exc:
        raise ValueError("bbox must be a 4-item (minx, miny, maxx, maxy) tuple") from exc

    if minx > maxx or miny > maxy:
        raise ValueError("bbox minimums must be less than or equal to maximums")

    return minx, miny, maxx, maxy


def _normalise_limit(limit) -> int | None:
    if limit is None:
        return None

    if limit < 0:
        raise ValueError("limit must be greater than or equal to zero")

    return limit


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


__all__ = ["Point", "LineString", "decode_gpkg_geometry", "iter_nodes", "iter_links"]
