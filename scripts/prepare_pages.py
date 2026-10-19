"""Validate and stage the self-contained Pages site, without downloading data."""
from pathlib import Path
import hashlib
import json
import shutil

root=Path(__file__).resolve().parents[1]
source=root/"demo"
manifest=json.loads((source/"data/manifest.json").read_text())
for name,info in manifest["files"].items():
    path=source/"data"/name
    assert path.stat().st_size==info["bytes"] and path.stat().st_size<40*1024*1024, name
    assert hashlib.sha256(path.read_bytes()).hexdigest()==info["sha256"],name
destination=root/"build/pages"
if destination.exists():shutil.rmtree(destination)
shutil.copytree(source,destination,ignore=shutil.ignore_patterns(".DS_Store"))
digest=hashlib.sha256()
for path in sorted(destination.rglob("*")):
    if path.is_file():digest.update(path.relative_to(destination).as_posix().encode());digest.update(path.read_bytes())
sw=destination/"sw.js"
sw.write_text(sw.read_text().replace("__VERSION__",digest.hexdigest()[:16]))
(destination/".nojekyll").touch()
print(f"Pages site ready: {destination}; data {sum(i['bytes'] for i in manifest['files'].values())/1e6:.2f} MB")
