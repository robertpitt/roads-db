"""Verify and stage Pages; national chunks come from a pinned GitHub data release."""
from pathlib import Path
import argparse, gzip, hashlib, json, shutil, urllib.request

ROOT=Path(__file__).resolve().parents[1]

def verify(path, size, sha):
    if path.stat().st_size!=size or hashlib.file_digest(path.open('rb'),'sha256').hexdigest()!=sha:
        raise ValueError(f'Invalid dataset file: {path.name}')

def prepare(national_dir, download=False, materialize=False):
    source=ROOT/'demo';destination=ROOT/'build/pages'
    london=json.loads((source/'data/manifest.json').read_text())
    for name,info in london['files'].items():
        verify(source/'data'/name,info['bytes'],info['sha256'])
    national=json.loads((source/'data/gb/manifest.json').read_text())
    national_dir.mkdir(parents=True,exist_ok=True)
    for name,info in national['files'].items():
        digest=hashlib.sha256();size=0
        expanded=national_dir/(name+'.expanded')
        if materialize:expanded.write_bytes(b'')
        for part in info['parts']:
            path=national_dir/part['name']
            if not path.exists():
                if not download:raise FileNotFoundError(f'{path}: build the national dataset or pass --download-national')
                url=f"https://github.com/robertpitt/roads-db/releases/download/{national['release']}/{part['name']}"
                temporary=path.with_suffix('.download')
                print(f"Downloading {part['name']}",flush=True)
                try:
                    with urllib.request.urlopen(url,timeout=120) as response,temporary.open('wb') as f:shutil.copyfileobj(response,f)
                    verify(temporary,part['download_bytes'],part['sha256']);temporary.replace(path)
                finally:temporary.unlink(missing_ok=True)
            verify(path,part['download_bytes'],part['sha256'])
            with gzip.open(path,'rb') as f:
                block=f.read(part['bytes']+1)
            if len(block)!=part['bytes']:raise ValueError(f'Invalid expanded chunk: {path.name}')
            size+=len(block);digest.update(block)
            if materialize:
                with expanded.open('ab') as f:f.write(block)
        if size!=info['bytes'] or digest.hexdigest()!=info['sha256']:raise ValueError(f'Invalid assembled dataset: {name}')
        if materialize:expanded.replace(national_dir/name)
    if destination.exists():shutil.rmtree(destination)
    shutil.copytree(source,destination,ignore=shutil.ignore_patterns('.DS_Store'))
    for info in national['files'].values():
        for part in info['parts']:shutil.copyfile(national_dir/part['name'],destination/'data/gb'/part['name'])
    for name in ['manifest.json','parity.json','NOTICE.txt']:
        shutil.copyfile(source/'data/gb'/name,national_dir/name)
    digest=hashlib.sha256();total=0
    for path in sorted(destination.rglob('*')):
        if path.is_file():
            total+=path.stat().st_size;digest.update(path.relative_to(destination).as_posix().encode())
            with path.open('rb') as f:
                while block:=f.read(1024*1024):digest.update(block)
    if total>950_000_000:raise ValueError('Site exceeds the Pages data budget')
    sw=destination/'sw.js';sw.write_text(sw.read_text().replace('__VERSION__',digest.hexdigest()[:16]))
    (destination/'.nojekyll').touch()
    print(f'Pages site ready: {destination}; {total/1e6:.2f} MB')

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--national-dir',type=Path,default=ROOT/'build/national')
    parser.add_argument('--download-national',action='store_true')
    parser.add_argument('--materialize-national',action='store_true')
    args=parser.parse_args();prepare(args.national_dir,args.download_national,args.materialize_national)
