# OP Road GeoPackage database shape

This document describes the structure of `oproad_gpkg_gb/Data/oproad_gb.gpkg` so processing tools can be designed without opening the GeoPackage for schema discovery.

Generated from SQLite/GeoPackage metadata and aggregate checks on 2026-05-18.

## File container

| Property | Value |
| --- | --- |
| File | `oproad_gpkg_gb/Data/oproad_gb.gpkg` |
| Container format | OGC GeoPackage, stored as SQLite 3 |
| File size | Approximately 2.0 GB |
| SQLite encoding | UTF-8 |
| SQLite page size | 4096 bytes |
| SQLite page count | 523,453 |
| SQLite `application_id` | `0x47504B47` (`GPKG`) |
| SQLite `user_version` | `10200` |
| Feature CRS | EPSG:27700, OSGB 1936 / British National Grid |
| Coordinate units | Metres |
| Geometry dimensions | XY only, no Z or M |
| Tile tables | Present as empty GeoPackage metadata tables only; no tile rows |

The GeoPackage contains three feature layers:

| Layer | Geometry | Rows | Feature `id` values | Extent min x | Extent min y | Extent max x | Extent max y | Last change |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `road_link` | `LINESTRING` | 3,961,077 | 3,961,077 distinct, no nulls | 9,123.00 | 8,045.93 | 655,563.00 | 1,216,649.00 | `2026-04-07T12:03:02.137Z` |
| `road_node` | `POINT` | 3,346,499 | 3,346,499 distinct, no nulls | 9,242.00 | 8,064.94 | 655,550.00 | 1,216,649.00 | `2026-04-07T12:07:34.588Z` |
| `motorway_junction` | `POINT` | 669 | 669 distinct, no nulls | 240,371.15 | 87,858.46 | 621,043.67 | 722,244.73 | `2026-04-07T11:55:47.128Z` |

## Logical model

`road_link` is the central layer. Each row is a road line feature with a start and end node.

`road_node` contains the point nodes referenced by `road_link.start_node` and `road_link.end_node`.

`motorway_junction` contains labelled motorway junction point features. There are no declared database relationships from this layer to `road_link` or `road_node`.

The database does not declare foreign keys, but these relationships were checked:

| Relationship | Result |
| --- | ---: |
| `road_link.start_node` exists in `road_node.id` | 3,961,077 of 3,961,077 |
| `road_link.end_node` exists in `road_node.id` | 3,961,077 of 3,961,077 |
| `road_link.start_node = road_link.end_node` | 25,052 rows |

`road_number_toid` and `road_name_toid` look like external Ordnance Survey TOID references. There are no target tables for them inside this GeoPackage.

## Implementation record shapes

The feature `id`, `start_node`, and `end_node` values are UUID-shaped text identifiers. `road_number_toid` and `road_name_toid` are TOID-shaped strings such as `osgb4000000015865010`.

Boolean fields are stored as SQLite integer booleans with observed values `0` and `1`.

```ts
type UUIDText = string;
type TOIDText = string;
type BooleanInt = 0 | 1;
type Metres = number;

interface GeoPackageGeometry {
  // SQLite BLOB in GeoPackageBinary format.
  // Use a GeoPackage-aware reader, or strip the GeoPackageBinary header
  // before passing the WKB payload to a raw WKB parser.
  bytes: Uint8Array;
  srsId: 27700;
}

interface RoadLinkRecord {
  fid: number;
  geometry: GeoPackageGeometry; // LINESTRING, EPSG:27700
  id: UUIDText;
  fictitious: BooleanInt;
  road_classification:
    | "Unclassified"
    | "Unknown"
    | "Not Classified"
    | "Classified Unnumbered"
    | "A Road"
    | "B Road"
    | "Motorway";
  road_function:
    | "Local Road"
    | "Restricted Local Access Road"
    | "Minor Road"
    | "A Road"
    | "B Road"
    | "Secondary Access Road"
    | "Local Access Road"
    | "Motorway";
  form_of_way:
    | "Single Carriageway"
    | "Collapsed Dual Carriageway"
    | "Roundabout"
    | "Slip Road"
    | "Dual Carriageway"
    | "Shared Use Carriageway"
    | "Guided Busway";
  road_classification_number: string | null;
  name_1: string | null;
  name_1_lang: "cym" | "gla" | null;
  name_2: string | null;
  name_2_lang: "eng" | null;
  road_structure: "Road In Tunnel" | null;
  length: Metres;
  length_uom: "m";
  loop: BooleanInt;
  primary_route: BooleanInt;
  trunk_road: BooleanInt;
  start_node: UUIDText;
  end_node: UUIDText;
  road_number_toid: TOIDText | null;
  road_name_toid: TOIDText | null;
}

interface RoadNodeRecord {
  fid: number;
  geometry: GeoPackageGeometry; // POINT, EPSG:27700
  id: UUIDText;
  form_of_road_node: "junction" | "road end" | "pseudo node" | "roundabout";
}

interface MotorwayJunctionRecord {
  fid: number;
  geometry: GeoPackageGeometry; // POINT, EPSG:27700
  id: UUIDText;
  junction_number: string;
}
```

## Feature tables

### `road_link`

DDL:

```sql
CREATE TABLE IF NOT EXISTS "road_link" (
  "fid" INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
  "geometry" LINESTRING,
  "id" TEXT,
  "fictitious" BOOLEAN,
  "road_classification" TEXT,
  "road_function" TEXT,
  "form_of_way" TEXT,
  "road_classification_number" TEXT,
  "name_1" TEXT,
  "name_1_lang" TEXT,
  "name_2" TEXT,
  "name_2_lang" TEXT,
  "road_structure" TEXT,
  "length" REAL,
  "length_uom" TEXT,
  "loop" BOOLEAN,
  "primary_route" BOOLEAN,
  "trunk_road" BOOLEAN,
  "start_node" TEXT,
  "end_node" TEXT,
  "road_number_toid" TEXT,
  "road_name_toid" TEXT
);
```

Column shape:

| Column | SQLite type | Observed nulls | Observed distinct values | Notes |
| --- | --- | ---: | ---: | --- |
| `fid` | `INTEGER` | 0 | 3,961,077 | Primary key, autoincrement |
| `geometry` | `LINESTRING` | 0 | Not counted | GeoPackageBinary `LINESTRING`, EPSG:27700 |
| `id` | `TEXT` | 0 | 3,961,077 | UUID-shaped feature id |
| `fictitious` | `BOOLEAN` | 0 | 1 | Always `0` in this file |
| `road_classification` | `TEXT` | 0 | 7 | See domain table below |
| `road_function` | `TEXT` | 0 | 8 | See domain table below |
| `form_of_way` | `TEXT` | 0 | 7 | See domain table below |
| `road_classification_number` | `TEXT` | 3,498,042 | 5,109 | Road number, for example `A6` |
| `name_1` | `TEXT` | 1,380,477 | 348,788 | Primary road name |
| `name_1_lang` | `TEXT` | 3,947,950 | 2 | `cym` or `gla` where present |
| `name_2` | `TEXT` | 3,947,950 | 3,044 | Secondary road name |
| `name_2_lang` | `TEXT` | 3,947,950 | 1 | `eng` where present |
| `road_structure` | `TEXT` | 3,960,539 | 1 | `Road In Tunnel` where present |
| `length` | `REAL` | 0 | Not counted | Metres, min 1.0, max 19,190.0, avg 146.466 |
| `length_uom` | `TEXT` | 0 | 1 | Always `m` |
| `loop` | `BOOLEAN` | 0 | 2 | `1` when `start_node = end_node` |
| `primary_route` | `BOOLEAN` | 0 | 2 | `1` for primary route links |
| `trunk_road` | `BOOLEAN` | 0 | 2 | `1` for trunk road links |
| `start_node` | `TEXT` | 0 | 2,559,832 | References `road_node.id` |
| `end_node` | `TEXT` | 0 | 2,964,326 | References `road_node.id` |
| `road_number_toid` | `TEXT` | 3,498,042 | 5,161 | External road number TOID |
| `road_name_toid` | `TEXT` | 1,380,477 | 823,193 | External road name TOID |

No empty strings were observed in the text columns checked. Missing optional text is represented as SQL `NULL`.

`length` summary:

| Metric | Value |
| --- | ---: |
| Minimum | 1.0 m |
| Maximum | 19,190.0 m |
| Average | 146.466 m |
| Total | 580,164,663.0 m |

`road_classification` domain:

| Value | Count |
| --- | ---: |
| `Unclassified` | 1,852,327 |
| `Unknown` | 888,353 |
| `Not Classified` | 405,159 |
| `Classified Unnumbered` | 352,203 |
| `A Road` | 291,483 |
| `B Road` | 164,344 |
| `Motorway` | 7,208 |

`road_function` domain:

| Value | Count |
| --- | ---: |
| `Local Road` | 1,735,717 |
| `Restricted Local Access Road` | 887,188 |
| `Minor Road` | 692,656 |
| `A Road` | 291,483 |
| `B Road` | 164,344 |
| `Secondary Access Road` | 134,534 |
| `Local Access Road` | 47,947 |
| `Motorway` | 7,208 |

`form_of_way` domain:

| Value | Count |
| --- | ---: |
| `Single Carriageway` | 3,811,710 |
| `Collapsed Dual Carriageway` | 57,432 |
| `Roundabout` | 56,738 |
| `Slip Road` | 21,384 |
| `Dual Carriageway` | 10,387 |
| `Shared Use Carriageway` | 3,272 |
| `Guided Busway` | 154 |

Other `road_link` domains:

| Column | Value | Count |
| --- | --- | ---: |
| `fictitious` | `0` | 3,961,077 |
| `road_structure` | `NULL` | 3,960,539 |
| `road_structure` | `Road In Tunnel` | 538 |
| `length_uom` | `m` | 3,961,077 |
| `loop` | `0` | 3,936,025 |
| `loop` | `1` | 25,052 |
| `primary_route` | `0` | 3,850,583 |
| `primary_route` | `1` | 110,494 |
| `trunk_road` | `0` | 3,923,689 |
| `trunk_road` | `1` | 37,388 |
| `name_1_lang` | `NULL` | 3,947,950 |
| `name_1_lang` | `cym` | 12,757 |
| `name_1_lang` | `gla` | 370 |
| `name_2_lang` | `NULL` | 3,947,950 |
| `name_2_lang` | `eng` | 13,127 |

Most common `road_classification_number` values:

| Value | Count |
| --- | ---: |
| `A6` | 3,406 |
| `A38` | 3,129 |
| `A40` | 2,049 |
| `A5` | 1,995 |
| `A4` | 1,992 |
| `A34` | 1,823 |
| `A1` | 1,823 |
| `A41` | 1,817 |
| `A30` | 1,790 |
| `A61` | 1,766 |

Most common `name_1` values:

| Value | Count |
| --- | ---: |
| `High Street` | 17,565 |
| `Station Road` | 12,272 |
| `Main Street` | 8,163 |
| `London Road` | 7,816 |
| `Church Road` | 6,771 |
| `Church Lane` | 5,932 |
| `Church Street` | 5,791 |
| `Mill Lane` | 4,448 |
| `Main Road` | 4,328 |
| `New Road` | 4,100 |

### `road_node`

DDL:

```sql
CREATE TABLE IF NOT EXISTS "road_node" (
  "fid" INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
  "geometry" POINT,
  "id" TEXT,
  "form_of_road_node" TEXT
);
```

Column shape:

| Column | SQLite type | Observed nulls | Observed distinct values | Notes |
| --- | --- | ---: | ---: | --- |
| `fid` | `INTEGER` | 0 | 3,346,499 | Primary key, autoincrement |
| `geometry` | `POINT` | 0 | Not counted | GeoPackageBinary `POINT`, EPSG:27700 |
| `id` | `TEXT` | 0 | 3,346,499 | UUID-shaped node id |
| `form_of_road_node` | `TEXT` | 0 | 4 | See domain table below |

`form_of_road_node` domain:

| Value | Count |
| --- | ---: |
| `junction` | 2,071,714 |
| `road end` | 1,039,748 |
| `pseudo node` | 170,561 |
| `roundabout` | 64,476 |

### `motorway_junction`

DDL:

```sql
CREATE TABLE IF NOT EXISTS "motorway_junction" (
  "fid" INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
  "geometry" POINT,
  "id" TEXT,
  "junction_number" TEXT
);
```

Column shape:

| Column | SQLite type | Observed nulls | Observed distinct values | Notes |
| --- | --- | ---: | ---: | --- |
| `fid` | `INTEGER` | 0 | 669 | Primary key, autoincrement |
| `geometry` | `POINT` | 0 | Not counted | GeoPackageBinary `POINT`, EPSG:27700 |
| `id` | `TEXT` | 0 | 669 | UUID-shaped junction feature id |
| `junction_number` | `TEXT` | 0 | 668 | Label such as `A1(M) J45` |

One duplicate `junction_number` was observed: `M90 J1` appears twice. All other sampled top values occur once.

## Geometry storage

The `geometry` column is stored as GeoPackageBinary, not bare WKB. Tools should prefer a GeoPackage-aware reader. When using raw SQLite reads, decode the GeoPackageBinary header first and then pass the remaining bytes to a WKB parser.

Observed prefixes:

| Layer | First geometry prefix | Meaning |
| --- | --- | --- |
| `road_link` | `47500003...` | `GP`, version 0, little-endian, XY envelope present |
| `road_node` | `47500001...` | `GP`, version 0, little-endian, no envelope |
| `motorway_junction` | `47500001...` | `GP`, version 0, little-endian, no envelope |

Header layout used here:

| Bytes | Meaning |
| --- | --- |
| 0-1 | Magic bytes `GP` |
| 2 | GeoPackageBinary version, observed `0` |
| 3 | Flags byte |
| 4-7 | Little-endian `srs_id`, observed `27700` |
| 8 onward | Optional envelope, then WKB payload |

For point layers in this file, the header is 8 bytes and the WKB payload begins at byte 8. The WKB prefix is `0101000000`, which is little-endian WKB `POINT`.

For `road_link`, the flags indicate an XY envelope. The header is 40 bytes: 8 bytes of base header plus four little-endian doubles for `minx`, `maxx`, `miny`, `maxy`. The WKB payload begins at byte 40. The WKB prefix is `0102000000`, which is little-endian WKB `LINESTRING`.

When implementing a generic reader, calculate the header length from the flags byte rather than hard-coding 8 or 40 bytes.

## GeoPackage metadata and indexes

Standard GeoPackage metadata tables present:

| Table | Rows | Purpose |
| --- | ---: | --- |
| `gpkg_contents` | 3 | Layer registry, bounds, CRS ids, timestamps |
| `gpkg_geometry_columns` | 3 | Geometry column type and CRS per feature table |
| `gpkg_spatial_ref_sys` | 4 | CRS definitions, including EPSG:27700 and EPSG:4326 |
| `gpkg_extensions` | 3 | RTree spatial index extension registrations |
| `gpkg_ogr_contents` | 3 | OGR-maintained feature counts |
| `gpkg_tile_matrix` | 0 | Empty |
| `gpkg_tile_matrix_set` | 0 | Empty |

Spatial index virtual tables are declared for all feature layers:

```sql
CREATE VIRTUAL TABLE "rtree_road_link_geometry" USING rtree(id, minx, maxx, miny, maxy);
CREATE VIRTUAL TABLE "rtree_road_node_geometry" USING rtree(id, minx, maxx, miny, maxy);
CREATE VIRTUAL TABLE "rtree_motorway_junction_geometry" USING rtree(id, minx, maxx, miny, maxy);
```

Each RTree has the usual SQLite shadow tables:

| Feature layer | RTree table | Shadow tables |
| --- | --- | --- |
| `road_link` | `rtree_road_link_geometry` | `_node`, `_parent`, `_rowid` |
| `road_node` | `rtree_road_node_geometry` | `_node`, `_parent`, `_rowid` |
| `motorway_junction` | `rtree_motorway_junction_geometry` | `_node`, `_parent`, `_rowid` |

There are RTree maintenance triggers for insert, update, and delete on each feature table. There are also feature-count triggers that update `gpkg_ogr_contents` when rows are inserted or deleted.

There are no ordinary attribute indexes declared on fields such as `road_link.id`, `road_link.start_node`, `road_link.end_node`, or `road_node.id`. If a processing workflow joins by these fields directly in SQLite, create temporary indexes in the working database or import the records into a store with explicit indexes.

## Processing notes

Use `fid` as the SQLite row key when addressing rows inside the GeoPackage. Use `id` as the stable feature identifier for interchange.

Read coordinates as British National Grid eastings and northings in metres. Reproject to WGS84 only at the boundary of a tool or API that requires longitude/latitude.

Do not assume SQLite schema nullability fully reflects the data contract. Only `fid` is declared `NOT NULL`, but aggregate checks showed all required-looking fields populated as documented above.

Do not rely on `junction_number` being unique in `motorway_junction`; `M90 J1` appears twice.

The local `sqlite3` executable used for this inspection could read the base tables and metadata but could not query the RTree virtual tables because its SQLite build lacks the `rtree` module. GeoPackage-aware GIS libraries or a SQLite build with RTree support should be able to use those indexes.
