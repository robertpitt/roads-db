from __future__ import annotations

import json
import math
import mmap
import struct
from bisect import bisect_right
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, Callable, Iterable, Mapping

from .gpkg import Point
from .graph import RoadGraph, SnapCandidate, SnapGeometry, _snap_endpoint_costs, _snap_endpoint_costs_units


MAGIC = b"RDB1"
FORMAT_VERSION = 1
UINT32_NONE = 0xFFFFFFFF
SECTION_ALIGNMENT = 64

_HEADER = struct.Struct("<4sII")
_SECTION = struct.Struct("<32sQQ")
_U32 = struct.Struct("<I")
_NODE = struct.Struct("<II")
_GEOM = struct.Struct("<IIIIII")
_BBOX = struct.Struct("<IIII")
_TILE = struct.Struct("<IIII")
_COMPONENT_META = struct.Struct("<IIIIII")

_REQUIRED_SECTIONS = {
    "metadata",
    "node_x",
    "node_y",
    "edge_to",
    "edge_length",
    "edge_geom",
    "offsets",
    "snap_geom_records",
    "snap_geom_bboxes",
    "snap_geom_offsets",
    "snap_geom_points",
    "snap_tiles",
    "snap_tile_geoms",
    "components",
    "component_metadata",
}


@dataclass(frozen=True, slots=True)
class Section:
    offset: int
    size: int


class MmapRoadGraph:
    """RDB graph view backed by mmap and section offsets."""

    def __init__(
        self,
        path: str | Path,
        fp: BinaryIO,
        mm: mmap.mmap,
        metadata: Mapping[str, Any],
        sections: Mapping[str, Section],
    ) -> None:
        self.path = Path(path)
        self._fp = fp
        self._mmap = mm
        self.metadata = dict(metadata)
        self.sections = dict(sections)
        self.origin_x_m = int(metadata["origin_x_m"])
        self.origin_y_m = int(metadata["origin_y_m"])
        self.coord_scale = int(metadata["coord_scale"])
        self.length_scale = int(metadata["length_scale"])
        self.snap_tile_size_m = float(metadata["snap_tile_size_m"])
        self._node_count = int(metadata["node_count"])
        self._edge_count = int(metadata["directed_edge_count"])
        self._snap_geometry_count = int(metadata["snap_geometry_count"])
        self._snap_tile_count = int(metadata["snap_tile_count"])
        self._snap_geom_point_count = int(metadata["snap_geom_point_count"])
        self._snap_tile_geom_count = int(metadata["snap_tile_geom_count"])
        self._component_count = int(metadata["component_count"])
        self._section_u32_views: dict[str, memoryview] = {}
        self._validate()
        self._node_x_u32 = self._u32_view("node_x")
        self._node_y_u32 = self._u32_view("node_y")
        self._edge_to_u32 = self._u32_view("edge_to")
        self._edge_length_u32 = self._u32_view("edge_length")
        self._edge_geom_u32 = self._u32_view("edge_geom")
        self._offsets_u32 = self._u32_view("offsets")
        self._snap_geom_records_u32 = self._u32_view("snap_geom_records")
        self._snap_geom_bboxes_u32 = self._u32_view("snap_geom_bboxes")
        self._snap_geom_offsets_u32 = self._u32_view("snap_geom_offsets")
        self._snap_geom_points_u32 = self._u32_view("snap_geom_points")
        self._snap_tiles_u32 = self._u32_view("snap_tiles")
        self._snap_tile_geoms_u32 = self._u32_view("snap_tile_geoms")
        self._components_u32 = self._u32_view("components")
        self._edge_reverse_u32 = self._u32_view("edge_reverse") if "edge_reverse" in self.sections else None
        self._edge_source_links_u32 = (
            self._u32_view("edge_source_links") if "edge_source_links" in self.sections else None
        )

    @property
    def node_count(self) -> int:
        return self._node_count

    @property
    def edge_count(self) -> int:
        return self._edge_count

    @property
    def snap_geometry_count(self) -> int:
        return self._snap_geometry_count

    @property
    def snap_tile_count(self) -> int:
        return self._snap_tile_count

    @property
    def component_count(self) -> int:
        return self._component_count

    @property
    def has_components(self) -> bool:
        return True

    def close(self) -> None:
        for view in self._section_u32_views.values():
            view.release()
        self._section_u32_views.clear()
        self._mmap.close()
        self._fp.close()

    def node_xy(self, node_id: int) -> tuple[float, float]:
        x, y = self._node_record(node_id)
        return self.origin_x_m + x / self.coord_scale, self.origin_y_m + y / self.coord_scale

    def node_units(self, node_id: int) -> tuple[int, int]:
        return self._node_record(node_id)

    def outgoing_edges(self, node_id: int) -> range:
        start = self._offsets_u32[node_id]
        end = self._offsets_u32[node_id + 1]
        return range(start, end)

    def edge_target_and_length_m(self, edge_id: int) -> tuple[int, float]:
        return self._edge_to_u32[edge_id], self._edge_length_u32[edge_id] / self.length_scale

    def edge_target_and_length_units(self, edge_id: int) -> tuple[int, int]:
        return self._edge_to_u32[edge_id], self._edge_length_u32[edge_id]

    def edge_geom_id(self, edge_id: int) -> int:
        return self._edge_geom_u32[edge_id]

    def edge_reverse_id(self, edge_id: int) -> int:
        if self._edge_reverse_u32 is not None:
            return self._edge_reverse_u32[edge_id]
        return UINT32_NONE

    def edge_source_link_id(self, edge_id: int) -> int:
        if self._edge_source_links_u32 is not None:
            return self._edge_source_links_u32[edge_id]
        return UINT32_NONE

    def component_id(self, node_id: int) -> int:
        return self._components_u32[node_id]

    def tile_geom_ids(self, tile_x: int, tile_y: int) -> Iterable[int]:
        record = self._find_tile(tile_x, tile_y)
        if record is None:
            return ()
        _tx, _ty, first, count = record
        tile_geoms = self._snap_tile_geoms_u32
        return (tile_geoms[idx] for idx in range(first, first + count))

    def snap_distance(self, geom_id: int, x: float, y: float) -> tuple[float, float, float, float]:
        start = self._snap_geom_offsets_u32[geom_id]
        end = self._snap_geom_offsets_u32[geom_id + 1]
        if end <= start:
            raise ValueError(f"snap geometry {geom_id} has no points")

        point_base = self.sections["snap_geom_points"].offset
        prev_x, prev_y = self._snap_point_xy(point_base, start)
        best_dist_sq = math.inf
        best_along = 0.0
        best_x = prev_x
        best_y = prev_y
        travelled = 0.0

        for point_idx in range(start + 1, end):
            next_x, next_y = self._snap_point_xy(point_base, point_idx)
            dx = next_x - prev_x
            dy = next_y - prev_y
            seg_len_sq = dx * dx + dy * dy
            if seg_len_sq:
                t = ((x - prev_x) * dx + (y - prev_y) * dy) / seg_len_sq
                t = min(1.0, max(0.0, t))
                projected_x = prev_x + t * dx
                projected_y = prev_y + t * dy
                dist_sq = (x - projected_x) ** 2 + (y - projected_y) ** 2
                seg_len = math.sqrt(seg_len_sq)
                if dist_sq < best_dist_sq:
                    best_dist_sq = dist_sq
                    best_along = travelled + t * seg_len
                    best_x = projected_x
                    best_y = projected_y
                travelled += seg_len
            prev_x = next_x
            prev_y = next_y

        return math.sqrt(best_dist_sq), best_along, best_x, best_y

    def snap_geom_may_intersect_radius(self, geom_id: int, x: float, y: float, radius_m: float) -> bool:
        bboxes = self._snap_geom_bboxes_u32
        bbox_base = geom_id * 4
        min_x = bboxes[bbox_base]
        min_y = bboxes[bbox_base + 1]
        max_x = bboxes[bbox_base + 2]
        max_y = bboxes[bbox_base + 3]
        x_units = (x - self.origin_x_m) * self.coord_scale
        y_units = (y - self.origin_y_m) * self.coord_scale
        radius_units = radius_m * self.coord_scale
        return not (
            x_units < min_x - radius_units
            or x_units > max_x + radius_units
            or y_units < min_y - radius_units
            or y_units > max_y + radius_units
        )

    def snap_endpoint_costs(self, geom_id: int, snap: SnapCandidate) -> tuple[tuple[int, float], ...]:
        geom_base = geom_id * 6
        start_node = self._snap_geom_records_u32[geom_base]
        end_node = self._snap_geom_records_u32[geom_base + 1]
        length_units = self._snap_geom_records_u32[geom_base + 4]
        return _snap_endpoint_costs(start_node, end_node, length_units / self.length_scale, snap)

    def snap_endpoint_cost_units(self, geom_id: int, snap: SnapCandidate) -> tuple[tuple[int, int], ...]:
        geom_base = geom_id * 6
        start_node = self._snap_geom_records_u32[geom_base]
        end_node = self._snap_geom_records_u32[geom_base + 1]
        length_units = self._snap_geom_records_u32[geom_base + 4]
        return _snap_endpoint_costs_units(start_node, end_node, length_units, self.length_scale, snap)

    def bounds(self) -> tuple[float, float, float, float]:
        min_x = min_y = float("inf")
        max_x = max_y = float("-inf")
        for node_id in range(self.node_count):
            x, y = self.node_xy(node_id)
            min_x = min(min_x, x)
            min_y = min(min_y, y)
            max_x = max(max_x, x)
            max_y = max(max_y, y)
        return min_x, min_y, max_x, max_y

    def _node_record(self, node_id: int) -> tuple[int, int]:
        if node_id < 0 or node_id >= self.node_count:
            raise IndexError(node_id)
        return self._node_x_u32[node_id], self._node_y_u32[node_id]

    def _edge_record(self, edge_id: int) -> tuple[int, int, int, int, int, int]:
        if edge_id < 0 or edge_id >= self.edge_count:
            raise IndexError(edge_id)
        return (
            self._edge_from_node(edge_id),
            self._edge_to_u32[edge_id],
            self._edge_length_u32[edge_id],
            self.edge_reverse_id(edge_id),
            self._edge_geom_u32[edge_id],
            self.edge_source_link_id(edge_id),
        )

    def _edge_from_node(self, edge_id: int) -> int:
        if edge_id < 0 or edge_id >= self.edge_count:
            raise IndexError(edge_id)
        return bisect_right(self._offsets_u32, edge_id) - 1

    def _geom_record(self, geom_id: int) -> tuple[int, int, int, int, int, int]:
        if geom_id < 0 or geom_id >= self.snap_geometry_count:
            raise IndexError(geom_id)
        geom_base = geom_id * 6
        geoms = self._snap_geom_records_u32
        return (
            geoms[geom_base],
            geoms[geom_base + 1],
            geoms[geom_base + 2],
            geoms[geom_base + 3],
            geoms[geom_base + 4],
            geoms[geom_base + 5],
        )

    def _tile_record(self, tile_idx: int) -> tuple[int, int, int, int]:
        tile_base = tile_idx * 4
        tiles = self._snap_tiles_u32
        return tiles[tile_base], tiles[tile_base + 1], tiles[tile_base + 2], tiles[tile_base + 3]

    def _snap_points(self, geom_id: int) -> Iterable[Point]:
        start = self._snap_geom_offsets_u32[geom_id]
        end = self._snap_geom_offsets_u32[geom_id + 1]
        point_base = self.sections["snap_geom_points"].offset
        for point_idx in range(start, end):
            x, y = self._snap_point_xy(point_base, point_idx)
            yield Point(x, y)

    def _snap_point_xy(self, _point_base: int, point_idx: int) -> tuple[float, float]:
        point_offset = point_idx * 2
        x = self._snap_geom_points_u32[point_offset]
        y = self._snap_geom_points_u32[point_offset + 1]
        return self.origin_x_m + x / self.coord_scale, self.origin_y_m + y / self.coord_scale

    def _u32(self, section_name: str, index: int) -> int:
        return self._u32_view(section_name)[index]

    def _u32_view(self, section_name: str) -> memoryview:
        view = self._section_u32_views.get(section_name)
        if view is not None:
            return view
        section = self.sections[section_name]
        view = memoryview(self._mmap)[section.offset : section.offset + section.size].cast("I")
        self._section_u32_views[section_name] = view
        return view

    def _find_tile(self, tile_x: int, tile_y: int) -> tuple[int, int, int, int] | None:
        lo = 0
        hi = self.snap_tile_count
        while lo < hi:
            mid = (lo + hi) // 2
            record = self._tile_record(mid)
            key = (record[0], record[1])
            if key < (tile_x, tile_y):
                lo = mid + 1
            elif key > (tile_x, tile_y):
                hi = mid
            else:
                return record
        return None

    def _validate(self) -> None:
        _expect_section_size(self.sections["node_x"], self.node_count, _U32.size)
        _expect_section_size(self.sections["node_y"], self.node_count, _U32.size)
        _expect_section_size(self.sections["edge_to"], self.edge_count, _U32.size)
        _expect_section_size(self.sections["edge_length"], self.edge_count, _U32.size)
        _expect_section_size(self.sections["edge_geom"], self.edge_count, _U32.size)
        _expect_section_size(self.sections["offsets"], self.node_count + 1, _U32.size)
        _expect_section_size(self.sections["snap_geom_records"], self.snap_geometry_count, _GEOM.size)
        _expect_section_size(self.sections["snap_geom_bboxes"], self.snap_geometry_count, _BBOX.size)
        _expect_section_size(self.sections["snap_geom_offsets"], self.snap_geometry_count + 1, _U32.size)
        _expect_section_size(self.sections["snap_geom_points"], self._snap_geom_point_count, _NODE.size)
        _expect_section_size(self.sections["snap_tiles"], self.snap_tile_count, _TILE.size)
        _expect_section_size(self.sections["snap_tile_geoms"], self._snap_tile_geom_count, _U32.size)
        _expect_section_size(self.sections["components"], self.node_count, _U32.size)
        _expect_section_size(self.sections["component_metadata"], self.component_count, _COMPONENT_META.size)
        if "edge_reverse" in self.sections:
            _expect_section_size(self.sections["edge_reverse"], self.edge_count, _U32.size)
        if "edge_source_links" in self.sections:
            _expect_section_size(self.sections["edge_source_links"], self.edge_count, _U32.size)


def write_rdb(
    path: str | Path,
    graph: RoadGraph,
    *,
    include_edge_reverse: bool = False,
    include_edge_source_links: bool = False,
) -> None:
    """Write the concrete MVP routing-kernel binary with streamed sections."""

    geom_point_count = sum(len(geom.points) for geom in graph.snap_geometries)
    tile_geom_count = sum(len(geom_ids) for geom_ids in graph.snap_tiles.values())
    metadata = {
        "format_version": FORMAT_VERSION,
        "origin_x_m": graph.origin_x_m,
        "origin_y_m": graph.origin_y_m,
        "coord_scale": graph.coord_scale,
        "length_scale": graph.length_scale,
        "snap_tile_size_m": graph.snap_tile_size_m,
        "node_count": graph.node_count,
        "directed_edge_count": graph.edge_count,
        "snap_geometry_count": len(graph.snap_geometries),
        "snap_tile_count": len(graph.snap_tiles),
        "snap_geom_point_count": geom_point_count,
        "snap_tile_geom_count": tile_geom_count,
        "component_count": graph.component_count,
    }
    metadata_bytes = json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode("utf-8")

    sizes = {
        "metadata": len(metadata_bytes),
        "node_x": graph.node_count * _U32.size,
        "node_y": graph.node_count * _U32.size,
        "edge_to": graph.edge_count * _U32.size,
        "edge_length": graph.edge_count * _U32.size,
        "edge_geom": graph.edge_count * _U32.size,
        "offsets": len(graph.offsets) * _U32.size,
        "snap_geom_records": len(graph.snap_geometries) * _GEOM.size,
        "snap_geom_bboxes": len(graph.snap_geometries) * _BBOX.size,
        "snap_geom_offsets": (len(graph.snap_geometries) + 1) * _U32.size,
        "snap_geom_points": geom_point_count * _NODE.size,
        "snap_tiles": len(graph.snap_tiles) * _TILE.size,
        "snap_tile_geoms": tile_geom_count * _U32.size,
        "components": len(graph.component_ids) * _U32.size,
        "component_metadata": len(graph.component_node_counts) * _COMPONENT_META.size,
    }
    if include_edge_reverse:
        sizes["edge_reverse"] = graph.edge_count * _U32.size
    if include_edge_source_links:
        sizes["edge_source_links"] = graph.edge_count * _U32.size

    names = list(sizes)
    directory_size = len(names) * _SECTION.size
    cursor = _align(_HEADER.size + directory_size)
    directory: list[tuple[str, int, int]] = []
    for name in names:
        cursor = _align(cursor)
        directory.append((name, cursor, sizes[name]))
        cursor += sizes[name]

    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("wb") as fp:
        fp.write(_HEADER.pack(MAGIC, FORMAT_VERSION, len(directory)))
        for name, offset, size in directory:
            _write_section_entry(fp, name, offset, size)

        writers: dict[str, Callable[[BinaryIO], None]] = {
            "metadata": lambda f: f.write(metadata_bytes),
            "node_x": lambda f: _write_u32_iter(f, graph.node_x),
            "node_y": lambda f: _write_u32_iter(f, graph.node_y),
            "edge_to": lambda f: _write_u32_iter(f, graph.edge_to),
            "edge_length": lambda f: _write_u32_iter(f, graph.edge_length),
            "edge_geom": lambda f: _write_u32_iter(f, graph.edge_geom),
            "offsets": lambda f: _write_u32_iter(f, graph.offsets),
            "snap_geom_records": lambda f: _write_snap_geom_records(f, graph),
            "snap_geom_bboxes": lambda f: _write_snap_geom_bboxes(f, graph),
            "snap_geom_offsets": lambda f: _write_snap_geom_offsets(f, graph),
            "snap_geom_points": lambda f: _write_snap_geom_points(f, graph),
            "snap_tiles": lambda f: _write_snap_tiles(f, graph),
            "snap_tile_geoms": lambda f: _write_snap_tile_geoms(f, graph),
            "components": lambda f: _write_u32_iter(f, graph.component_ids),
            "component_metadata": lambda f: _write_component_metadata(f, graph),
            "edge_reverse": lambda f: _write_u32_iter(f, graph.reverse_edge),
            "edge_source_links": lambda f: _write_edge_source_links(f, graph),
        }
        for name, offset, expected_size in directory:
            _pad_until(fp, offset)
            before = fp.tell()
            writers[name](fp)
            actual_size = fp.tell() - before
            if actual_size != expected_size:
                raise ValueError(f"section {name} wrote {actual_size} bytes, expected {expected_size}")


def read_rdb(path: str | Path) -> MmapRoadGraph:
    """Open an RDB file as an mmap-backed graph view."""

    source = Path(path)
    fp = source.open("rb")
    try:
        mm = mmap.mmap(fp.fileno(), 0, access=mmap.ACCESS_READ)
    except Exception:
        fp.close()
        raise
    try:
        if len(mm) < _HEADER.size:
            raise ValueError("RDB file is too small")

        magic, version, section_count = _HEADER.unpack_from(mm, 0)
        if magic != MAGIC:
            raise ValueError(f"invalid RDB magic: {magic!r}")
        if version != FORMAT_VERSION:
            raise ValueError(f"unsupported RDB version: {version}")

        sections = _read_directory(mm, section_count)
        missing = sorted(_REQUIRED_SECTIONS.difference(sections))
        if missing:
            raise ValueError(f"missing RDB sections: {', '.join(missing)}")
        metadata = json.loads(_section_bytes(mm, sections, "metadata").decode("utf-8"))
        metadata_version = int(metadata.get("format_version", version))
        if metadata_version != FORMAT_VERSION:
            raise ValueError(f"RDB metadata version {metadata_version} is unsupported")
        return MmapRoadGraph(source, fp, mm, metadata, sections)
    except Exception:
        mm.close()
        fp.close()
        raise


def read_rdb_materialized(path: str | Path) -> RoadGraph:
    """Load an RDB into Python objects. Useful for debugging, not production."""

    graph = read_rdb(path)
    try:
        node_x: list[int] = []
        node_y: list[int] = []
        for node_id in range(graph.node_count):
            x, y = graph._node_record(node_id)
            node_x.append(x)
            node_y.append(y)

        edge_from: list[int] = []
        edge_to: list[int] = []
        edge_length: list[int] = []
        reverse_edge: list[int] = []
        edge_geom: list[int] = []
        edge_source_link: list[int] = []
        for edge_id in range(graph.edge_count):
            from_node, to_node, length, reverse, geom_id, source_link = graph._edge_record(edge_id)
            edge_from.append(from_node)
            edge_to.append(to_node)
            edge_length.append(length)
            reverse_edge.append(reverse)
            edge_geom.append(geom_id)
            edge_source_link.append(source_link)

        offsets = [graph._u32("offsets", idx) for idx in range(graph.node_count + 1)]
        snap_geometries: list[SnapGeometry] = []
        for geom_id in range(graph.snap_geometry_count):
            start_node, end_node, forward_edge, reverse, length_units, _point_count = graph._geom_record(geom_id)
            points = tuple(graph._snap_points(geom_id))
            snap_geometries.append(
                SnapGeometry(
                    points=points,
                    start_node=start_node,
                    end_node=end_node,
                    forward_edge=forward_edge,
                    reverse_edge=None if reverse == UINT32_NONE else reverse,
                    length_m=length_units / graph.length_scale,
                    bbox=_bbox(points),
                )
            )

        tile_geoms = [
            graph._u32("snap_tile_geoms", idx)
            for idx in range(graph.sections["snap_tile_geoms"].size // _U32.size)
        ]
        snap_tiles: dict[tuple[int, int], list[int]] = {}
        for tile_idx in range(graph.snap_tile_count):
            tile_x, tile_y, first, count = graph._tile_record(tile_idx)
            snap_tiles[(tile_x, tile_y)] = tile_geoms[first : first + count]

        return RoadGraph(
            origin_x_m=graph.origin_x_m,
            origin_y_m=graph.origin_y_m,
            coord_scale=graph.coord_scale,
            length_scale=graph.length_scale,
            node_x=node_x,
            node_y=node_y,
            edge_from=edge_from,
            edge_to=edge_to,
            edge_length=edge_length,
            reverse_edge=reverse_edge,
            edge_geom=edge_geom,
            offsets=offsets,
            snap_geometries=snap_geometries,
            snap_tile_size_m=graph.snap_tile_size_m,
            snap_tiles=snap_tiles,
            edge_source_link=edge_source_link,
            component_ids=[
                graph._u32("components", idx)
                for idx in range(graph.node_count)
            ]
            if "components" in graph.sections
            else [],
            component_node_counts=[
                _COMPONENT_META.unpack_from(
                    graph._mmap,
                    graph.sections["component_metadata"].offset + idx * _COMPONENT_META.size,
                )[0]
                for idx in range(graph.component_count)
            ]
            if "component_metadata" in graph.sections
            else [],
            component_edge_counts=[
                _COMPONENT_META.unpack_from(
                    graph._mmap,
                    graph.sections["component_metadata"].offset + idx * _COMPONENT_META.size,
                )[1]
                for idx in range(graph.component_count)
            ]
            if "component_metadata" in graph.sections
            else [],
            component_bboxes=[
                _COMPONENT_META.unpack_from(
                    graph._mmap,
                    graph.sections["component_metadata"].offset + idx * _COMPONENT_META.size,
                )[2:6]
                for idx in range(graph.component_count)
            ]
            if "component_metadata" in graph.sections
            else [],
        )
    finally:
        graph.close()


def _write_section_entry(fp: BinaryIO, name: str, offset: int, size: int) -> None:
    encoded = name.encode("ascii")
    if len(encoded) > 32:
        raise ValueError(f"RDB section name is too long: {name}")
    fp.write(_SECTION.pack(encoded.ljust(32, b"\0"), offset, size))


def _align(offset: int, alignment: int = SECTION_ALIGNMENT) -> int:
    return ((offset + alignment - 1) // alignment) * alignment


def _pad_until(fp: BinaryIO, offset: int) -> None:
    current = fp.tell()
    if current > offset:
        raise ValueError(f"section writer passed expected offset {offset}")
    if current < offset:
        fp.write(b"\0" * (offset - current))


def _write_edge_source_links(fp: BinaryIO, graph: RoadGraph) -> None:
    for idx in range(graph.edge_count):
        value = graph.edge_source_link[idx] if idx < len(graph.edge_source_link) else UINT32_NONE
        fp.write(_U32.pack(value))


def _write_snap_geom_records(fp: BinaryIO, graph: RoadGraph) -> None:
    for geom in graph.snap_geometries:
        fp.write(
            _GEOM.pack(
                geom.start_node,
                geom.end_node,
                geom.forward_edge,
                geom.reverse_edge if geom.reverse_edge is not None else UINT32_NONE,
                round(geom.length_m * graph.length_scale),
                len(geom.points),
            )
        )


def _write_snap_geom_bboxes(fp: BinaryIO, graph: RoadGraph) -> None:
    for geom in graph.snap_geometries:
        min_x, min_y, max_x, max_y = geom.bbox
        fp.write(
            _BBOX.pack(
                math.floor((min_x - graph.origin_x_m) * graph.coord_scale),
                math.floor((min_y - graph.origin_y_m) * graph.coord_scale),
                math.ceil((max_x - graph.origin_x_m) * graph.coord_scale),
                math.ceil((max_y - graph.origin_y_m) * graph.coord_scale),
            )
        )


def _write_snap_geom_offsets(fp: BinaryIO, graph: RoadGraph) -> None:
    offset = 0
    fp.write(_U32.pack(offset))
    for geom in graph.snap_geometries:
        offset += len(geom.points)
        fp.write(_U32.pack(offset))


def _write_snap_geom_points(fp: BinaryIO, graph: RoadGraph) -> None:
    for geom in graph.snap_geometries:
        for point in geom.points:
            fp.write(
                _NODE.pack(
                    round((point.x - graph.origin_x_m) * graph.coord_scale),
                    round((point.y - graph.origin_y_m) * graph.coord_scale),
                )
            )


def _write_snap_tiles(fp: BinaryIO, graph: RoadGraph) -> None:
    cursor = 0
    for tile_x, tile_y in sorted(graph.snap_tiles):
        geom_ids = graph.snap_tiles[(tile_x, tile_y)]
        fp.write(_TILE.pack(tile_x, tile_y, cursor, len(geom_ids)))
        cursor += len(geom_ids)


def _write_snap_tile_geoms(fp: BinaryIO, graph: RoadGraph) -> None:
    for key in sorted(graph.snap_tiles):
        _write_u32_iter(fp, graph.snap_tiles[key])


def _write_component_metadata(fp: BinaryIO, graph: RoadGraph) -> None:
    for idx, node_count in enumerate(graph.component_node_counts):
        edge_count = graph.component_edge_counts[idx]
        min_x, min_y, max_x, max_y = graph.component_bboxes[idx]
        fp.write(_COMPONENT_META.pack(node_count, edge_count, min_x, min_y, max_x, max_y))


def _write_u32_iter(fp: BinaryIO, values: Iterable[int]) -> None:
    for value in values:
        fp.write(_U32.pack(value))


def _read_directory(data: mmap.mmap, section_count: int) -> dict[str, Section]:
    cursor = _HEADER.size
    sections: dict[str, Section] = {}
    for _ in range(section_count):
        if cursor + _SECTION.size > len(data):
            raise ValueError("RDB section directory extends beyond file")
        raw_name, offset, size = _SECTION.unpack_from(data, cursor)
        cursor += _SECTION.size
        name = raw_name.split(b"\0", 1)[0].decode("ascii")
        if not name:
            raise ValueError("RDB section has an empty name")
        if name in sections:
            raise ValueError(f"duplicate RDB section: {name}")
        if offset + size > len(data):
            raise ValueError(f"RDB section extends beyond file: {name}")
        sections[name] = Section(offset, size)
    _validate_section_ranges(sections)
    return sections


def _validate_section_ranges(sections: Mapping[str, Section]) -> None:
    ranges = sorted((section.offset, section.offset + section.size, name) for name, section in sections.items())
    for idx in range(1, len(ranges)):
        _previous_start, previous_end, previous_name = ranges[idx - 1]
        current_start, _current_end, current_name = ranges[idx]
        if current_start < previous_end:
            raise ValueError(f"RDB sections overlap: {previous_name}, {current_name}")


def _section_bytes(data: mmap.mmap, sections: Mapping[str, Section], name: str) -> bytes:
    section = sections[name]
    return data[section.offset : section.offset + section.size]


def _expect_section_size(section: Section, count: int, item_size: int) -> None:
    expected = count * item_size
    if section.size != expected:
        raise ValueError(f"RDB section size mismatch: expected {expected}, got {section.size}")


def _bbox(points: tuple[Point, ...]) -> tuple[float, float, float, float]:
    if not points:
        raise ValueError("snap geometry must contain at least one point")
    xs = [point.x for point in points]
    ys = [point.y for point in points]
    return min(xs), min(ys), max(xs), max(ys)
