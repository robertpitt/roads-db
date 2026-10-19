"""Build the complete GB browser dataset and independently verifiable gzip chunks.

Generated binaries stay outside Git; publish the .gz files as data release assets.
"""
from __future__ import annotations
import argparse, gc, gzip, hashlib, json, sqlite3, sys
from contextlib import closing
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from roads_rdb.graph import build_graph_from_gpkg, route_nodes
from roads_rdb.rdb import write_rdb, read_rdb


def build(source, output, date, md5):
    output.mkdir(parents=True, exist_ok=True)
    print('Building full Great Britain graph…', flush=True)
    graph = build_graph_from_gpkg(source, bbox=None, keep_source_ids=True)
    write_rdb(output/'gb.rdb', graph)
    print('Exporting matching names…', flush=True)
    database=output/'gb.sqlite'; database.unlink(missing_ok=True)
    with closing(sqlite3.connect(Path(source).resolve().as_uri()+'?mode=ro', uri=True)) as src, closing(sqlite3.connect(database)) as db:
        src.execute('CREATE TEMP TABLE selected(geom_id INTEGER, source_id TEXT)')
        src.executemany('INSERT INTO selected VALUES (?, ?)', ((i,graph.source_link_ids[graph.edge_source_link[g.forward_edge]]) for i,g in enumerate(graph.snap_geometries)))
        # National metadata omits UUIDs, retaining every road name and classification.
        # A dictionary avoids repeating names across millions of segments.
        db.executescript('''CREATE TABLE labels(label_id INTEGER PRIMARY KEY,name TEXT,number TEXT,classification TEXT);
          CREATE TABLE road_labels(geom_id INTEGER PRIMARY KEY,label_id INTEGER NOT NULL);
          CREATE VIEW roads AS SELECT geom_id,name,number,classification FROM road_labels JOIN labels USING(label_id);''')
        labels={}; count=0
        for geom_id,name,number,classification in src.execute('''SELECT s.geom_id,l.name_1,l.road_classification_number,l.road_classification
            FROM selected s JOIN road_link l ON l.id=s.source_id ORDER BY s.geom_id'''):
            key=(name,number,classification)
            if key not in labels:
                labels[key]=len(labels); db.execute('INSERT INTO labels VALUES (?,?,?,?)',(labels[key],*key))
            db.execute('INSERT INTO road_labels VALUES (?,?)',(geom_id,labels[key]));count+=1
        assert count==len(graph.snap_geometries), 'Metadata and graph mismatch'
        db.commit();db.execute('VACUUM')
    manifest={'name':'Great Britain','format_version':1,'bbox_bng':[0,0,700000,1300000],
      'bounds':[[49.8,-7.6],[60.9,1.9]],'source_date':date,
      'source_url':'https://api.os.uk/downloads/v1/products/OpenRoads/downloads', 'source_archive_md5':md5,
      'attribution':'Contains OS data © Crown Copyright and database rights 2026.',
      'coverage':'England, Scotland and Wales. Northern Ireland is not included in OS Open Roads.',
      'nodes':graph.node_count,'directed_edges':graph.edge_count,'roads':count,'components':graph.component_count,
      'metadata_schema':'dictionary-v1','files':{},'release':f'data-gb-{date}'}
    del graph;gc.collect()
    print('Compressing 16 MiB chunks…',flush=True)
    for name in ['gb.rdb','gb.sqlite']:
        path=output/name; parts=[]
        with path.open('rb') as f:
            while block:=f.read(16*1024*1024):
                packed=gzip.compress(block,compresslevel=9,mtime=0);part=f'{name}.{len(parts):03}.gz'
                (output/part).write_bytes(packed)
                parts.append({'name':part,'bytes':len(block),'download_bytes':len(packed),'sha256':hashlib.sha256(packed).hexdigest()})
        manifest['files'][name]={'bytes':path.stat().st_size,'sha256':hashlib.file_digest(path.open('rb'),'sha256').hexdigest(),'parts':parts}
    manifest['id']=''.join(f['sha256'][:16] for f in manifest['files'].values())
    (output/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    (output/'NOTICE.txt').write_text(manifest['attribution']+'\nOS Open Roads, Open Government Licence v3.0.\nhttps://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/\n'+manifest['coverage']+'\n')
    print('Generating Python parity routes…',flush=True)
    checks=[]
    with closing(read_rdb(output/'gb.rdb')) as graph:
        # Real city pairs: local, intercity and Scottish mainland coverage.
        for a,b in [((530000,180000),(532000,181000)),((325500,673500),(326000,674000)),((383000,398000),(430000,434000)),((530000,180000),(325500,673500))]:
            result=route_nodes(graph,a,b,snap_radius_m=250,snap_candidates=8)
            checks.append({'from_bng':a,'to_bng':b,'cost_m':result.cost_m})
    (output/'parity.json').write_text(json.dumps(checks)+'\n')
    print(json.dumps({k:v for k,v in manifest.items() if k!='files'},indent=2),flush=True)
    print('Download MB:',sum(p['download_bytes'] for f in manifest['files'].values() for p in f['parts'])/1e6,flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--input',required=True);p.add_argument('--output',type=Path,default=Path('build/national'));p.add_argument('--source-date',required=True);p.add_argument('--source-md5',default='')
    a=p.parse_args();build(a.input,a.output,a.source_date,a.source_md5)
