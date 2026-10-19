# Changelog

## Unreleased

- Add selectable Great Britain coverage, a 399.09 MB compressed national data
  edition, resumable verified downloads, per-dataset browser caching, persistent
  storage requests and controls, bounded map rendering, national routing parity
  and cache failure tests. Serve national chunks through Pages from a data release
  without adding them to Git history. OS coverage excludes Northern Ireland.

- Add a GitHub Pages London demo: 11.53 MB of real RDB/SQLite data, on-device
  routing, local road geometry, approximate directions, browser API, offline
  caching, and a Pages deployment workflow. Include browser/Python parity tests
  and reproducible bounded-data export with explicit source-ID metadata mappings.

## 0.1.0 — prepared, unpublished

Initial experimental release of the OS Open Roads routing-kernel builder and
evaluator. This entry becomes the release notes when v0.1.0 is published.

- Build an RDB format version 1 index from a full or bounded GeoPackage input.
- Query routes using A*, connected components, snapping, and reusable workspaces.
- Run origin-to-many batch queries and generate an optional SQLite metadata lookup.
- Inspect and benchmark memory-mapped indexes without the source GeoPackage.
- Display routes and approximate directions in a local web map, with the HTML
  included in installed Python packages.
- Return useful CLI errors for missing input and invalid coordinates; return
  HTTP 400 for invalid map requests, including non-object JSON and invalid UTF-8.
- Include synthetic examples, end-to-end routing and HTTP tests, isolated wheel
  checks, and GitHub Actions CI for Python 3.11–3.14 and major desktop platforms.
- License the code under MIT and document separate data and map attribution.

### Limitations

Road links are bidirectional. One-way streets, legal access, turn restrictions,
speed profiles, and traffic are not modelled. Directions and coordinate
conversions are approximate. Directions metadata assumes the same full source
dataset with contiguous FIDs and no skipped geometries. Earlier prototype RDB
layouts are unsupported; rebuild indexes from their source data.

Release checks use synthetic data. Historical national-network benchmarks are
development observations and have not been rerun for this release.
