# Contributing

Use Python 3.11 or newer. From the checkout:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
python -m unittest discover -s tests -v
```

For Windows activation, use `.venv\Scripts\Activate.ps1` in PowerShell.
Runtime code uses the Python standard library. Keep changes focused, explain
the affected behavior, and add tests for routing, file-format, or API changes.
Synthetic test data should be generated locally; do not commit downloaded
GeoPackages, generated indexes, lookup databases, or machine-specific files.

`README.md` describes the supported user workflow. `SPEC.md` describes the
binary layout, and `NOTES.md` records implementation details and historical
benchmarks. `Idea.md` and `PROCESS.md` are design proposals and include future
work. Changes to the file layout must address versioning and compatibility.
Dense IDs must remain consistent within each matching RDB/lookup build.

Before submitting a change, run the source tests and release checks:

```bash
python -m pip install build twine
python -m build
python -m twine check --strict dist/*
python scripts/check_release.py
```

Use an empty `dist/` directory when changing versions. `python -m build` builds
the wheel through the source distribution, and the release checker installs it
in a fresh virtual environment, copies tests outside the checkout, and runs
the CLI, routing, and local HTTP tests. These tests bind only to `127.0.0.1`
using an ephemeral port; the environment must allow loopback sockets.

When reporting a bug, include the Python/OS versions, command, error output,
data edition, whether the build was full or bounded, and the smallest synthetic
reproduction possible. Check shared output for private coordinates or local
paths before posting it. Contributions are provided under the [MIT licence](LICENSE).
