"""Install a built wheel in isolation and exercise the shipped CLI and map.

Run after `python -m build`. Tests and the synthetic example are copied outside
the source tree so missing package files cannot be masked by the checkout.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
import venv
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    wheels = list(args.dist.resolve().glob("roads_rdb-*.whl"))
    if len(wheels) != 1:
        parser.error("expected exactly one roads-rdb wheel; use a clean dist directory")
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    env["PYTHONNOUSERSITE"] = "1"
    with tempfile.TemporaryDirectory(prefix="roads-release-") as tmp:
        work = Path(tmp)
        venv.EnvBuilder(with_pip=True).create(work / "venv")
        bin_dir = work / "venv" / ("Scripts" if os.name == "nt" else "bin")
        python = bin_dir / ("python.exe" if os.name == "nt" else "python")

        def run(*command: str | Path) -> None:
            subprocess.run([str(part) for part in command], cwd=work, env=env, check=True)

        run(python, "-m", "pip", "install", "--no-index", "--no-deps", wheels[0])
        run(python, "-c", "from importlib.metadata import distribution; from pathlib import Path; import sys; "
            "import roads_rdb; from roads_rdb.map_server import STATIC_DIR; "
            "d = distribution('roads-rdb'); "
            "assert d.version == roads_rdb.__version__ == sys.argv[1]; "
            "assert d.metadata['License-Expression'] == sys.argv[2]; "
            "assert (STATIC_DIR / 'route_map.html').is_file(); "
            "licenses = {Path(p).name for p in d.files if '/licenses/' in str(p)}; "
            "assert {'LICENSE', 'NOTICE'} <= licenses", project["version"], project["license"])
        for command in ("roads-rdb", "roads-rdb-map"):
            executable = command + (".exe" if os.name == "nt" else "")
            run(bin_dir / executable, "--help")
        for directory in ("tests", "examples"):
            shutil.copytree(ROOT / directory, work / directory,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        run(python, "-m", "unittest", "discover", "-s", "tests", "-v")
    print("Release checks passed: isolated wheel, CLI, routing, and map.")


if __name__ == "__main__":
    main()
