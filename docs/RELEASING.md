# Releasing roads-rdb

The source repository is [robertpitt/roads-db](https://github.com/robertpitt/roads-db).
The v0.1.0 release configuration is prepared. Nothing in the CI workflow
publishes a GitHub release, pushes Git tags, or uploads to PyPI. Publishing a
versioned release is a separate maintainer action from pushing source commits.
The separate Pages workflow publishes the London / Great Britain browser demo on pushes to main;
see `PAGES_DEMO.md` for dataset and deployment checks. National datasets use separate
`data-gb-*` releases; these do not publish the v0.1.0 software version.

## Local release gate

1. Review the complete diff, including pre-existing staged work. Confirm that
   the intended package version in `pyproject.toml` and `roads_rdb/__init__.py`
   is `0.1.0`. RDB binary format version 1 is independent of the package version.
2. Use Python 3.11 or newer in a virtual environment. Run:

   ```bash
   python -m pip install build twine
   python -m unittest discover -s tests -v
   python -m build
   python -m twine check --strict dist/*
   python scripts/check_release.py
   ```

   Start with an empty `dist/` when building a different version. The build
   creates a source archive and a wheel from that source archive. The release
   check installs the wheel outside the repository, verifies MIT/version/HTML
   metadata, exercises both console commands, and repeats the synthetic tests.
3. Follow the README's synthetic quick start, including opening the local map.
   The generator intentionally refuses to overwrite its input. Use another
   output path if a demo file already exists.
4. If real data is available, build a small extract and exercise a route. For
   national artifacts, record source edition, hardware, build memory/time, RDB
   format, and checksums. Keep results separate from historical measurements.
   A full-GB build is not required for the source-only experimental release.
5. Inspect the wheel/source archive: include code, map HTML, licence, and NOTICE;
   include examples/tests/docs in the source archive. Exclude source datasets,
   generated RDB/SQLite files, bytecode, environment files, and credentials.
6. Review Git history and tracked files for secrets and unintended data. Existing
   prototype commits contain bytecode; its removal from the release tree does
   not remove it from history. Any history rewrite is a separate deliberate step.
7. Review `CHANGELOG.md`, supported behavior, known limitations, and attribution.
   Source data and generated route artifacts retain their own data terms.

## When publication is authorized

- Commit the reviewed source, push to GitHub, and confirm every CI matrix job
  passes. CI builds packages and retains them as workflow artifacts only.
- Require passing CI for changes to the default branch.
- Replace the unpublished changelog heading with the publication date, create
  the annotated `v0.1.0` tag from the approved commit, and create the GitHub
  release using the changelog entry. Attach the checked wheel and source archive.
- Distribute OS-derived datasets separately only with their edition, format,
  checksum, build details, and required attribution. Code licensing does not
  replace the OS data licence.

PyPI publication is optional and is not configured by this release preparation.
