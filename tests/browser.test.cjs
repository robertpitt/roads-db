const {test,before,after}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),os=require('node:os'),crypto=require('node:crypto');
const {execFileSync}=require('node:child_process');
const Graph=require('../demo/engine.js');
const root=path.resolve(__dirname,'..');let fixture;
function readGraph(file){const data=fs.readFileSync(file);return new Graph(data.buffer.slice(data.byteOffset,data.byteOffset+data.byteLength));}
before(()=>{
  fixture=fs.mkdtempSync(path.join(os.tmpdir(),'roads-browser-'));
  execFileSync(process.env.ROADS_PYTHON||'python',['-c',`
from pathlib import Path
import sqlite3,sys
from examples.create_demo_gpkg import create_demo_gpkg
from scripts.build_pages_demo import build_demo
p=Path(sys.argv[1]); source=p/'demo.gpkg';create_demo_gpkg(source)
conn=sqlite3.connect(source);conn.execute('UPDATE road_link SET fid=fid+100');conn.commit();conn.close()
build_demo(source,p/'data',(529999,179999,531101,181001),source_date='synthetic',attribution='Synthetic test fixture')
`,fixture],{cwd:root,env:{...process.env,PYTHONDONTWRITEBYTECODE:'1'}});
});
after(()=>{if(fixture)fs.rmSync(fixture,{recursive:true,force:true});});
test('London data hashes, sizes, and graph counts match the manifest',()=>{
  const manifest=JSON.parse(fs.readFileSync(path.join(root,'demo/data/manifest.json')));
  for(const [name,meta]of Object.entries(manifest.files)){
    const file=fs.readFileSync(path.join(root,'demo/data',name));
    assert.equal(file.length,meta.bytes);assert.equal(crypto.createHash('sha256').update(file).digest('hex'),meta.sha256);
    assert.ok(file.length<40*1024*1024);
  }
  const graph=readGraph(path.join(root,'demo/data/london.rdb'));
  assert.equal(graph.meta.node_count,manifest.nodes);assert.equal(graph.meta.snap_geometry_count,manifest.roads);
});
test('browser A* matches twenty Python routes on the real London extract',()=>{
  const graph=readGraph(path.join(root,'demo/data/london.rdb'));
  for(const route of JSON.parse(fs.readFileSync(path.join(root,'demo/data/parity.json')))){
    const result=graph.route(route.from_bng,route.to_bng,{radius:1,candidates:8});
    assert.equal(result.cost_m,route.cost_m,JSON.stringify(route));
  }
});
test('shortest path, reverse route, partial snaps, and workspace reuse',()=>{
  const graph=readGraph(path.join(fixture,'data/london.rdb'));
  for(let i=0;i<3;i++){
    assert.equal(graph.route([530000,180000],[530200,180000],{radius:1,candidates:1}).cost_m,200);
    assert.equal(graph.route([530200,180000],[530000,180000],{radius:1,candidates:1}).cost_m,200);
    assert.equal(graph.route([530025,180000],[530175,180000],{radius:1,candidates:1}).cost_m,150);
  }
  assert.equal(graph.route([530025,180000],[530075,180000],{radius:1,candidates:1}).cost_m,50);
  assert.throws(()=>graph.route([530000,180000],[531100,181000],{radius:1,candidates:1}),/disconnected/);
  assert.throws(()=>graph.route([0,0],[530000,180000]),/snap/);
});
test('truncated and unsupported RDB files are rejected',()=>{
  assert.throws(()=>new Graph(new ArrayBuffer(4)),/truncated/);
  const data=fs.readFileSync(path.join(fixture,'data/london.rdb'));
  data.writeUInt32LE(2,4);assert.throws(()=>new Graph(data.buffer.slice(data.byteOffset,data.byteOffset+data.byteLength)),/Unsupported/);
});
test('SQLite metadata uses explicit source IDs for bounded builds',async()=>{
  const SQL=await require('../demo/vendor/sql-wasm.js')({locateFile:name=>path.join(root,'demo/vendor',name)});
  const db=new SQL.Database(fs.readFileSync(path.join(fixture,'data/london.sqlite')));
  try{
    assert.deepEqual(db.exec('SELECT geom_id,source_fid,name FROM roads ORDER BY geom_id LIMIT 2')[0].values,
      [[0,101,'Demo Road'],[1,102,'Demo Road']]);
  }finally{db.close();}
});
test('browser geographic conversion agrees with the Python London reference',()=>{
  const proj4=require('../demo/vendor/proj4.js');
  const bng='+proj=tmerc +lat_0=49 +lon_0=-2 +k=0.9996012717 +x_0=400000 +y_0=-100000 +ellps=airy +towgs84=446.448,-125.157,542.060,0.1502,0.2470,0.8421,-20.4894 +units=m +no_defs';
  const geo=proj4(bng,'EPSG:4326',[530000,180000]);
  assert.ok(Math.abs(geo[0]-(-0.1283539785))<0.00001);
  assert.ok(Math.abs(geo[1]-51.5039908254)<0.00001);
});
test('tile binary search and bounded viewport rendering cover the graph',()=>{
  const graph=readGraph(path.join(root,'demo/data/london.rdb'));
  for(let i=0;i<graph.meta.snap_tile_count;i++)assert.equal(graph.tile(graph.snap_tiles[i*4],graph.snap_tiles[i*4+1]),i*4);
  assert.equal(graph.tile(999999,999999),undefined);
  assert.equal(graph.networkIds([0,0,1,1]).length,0);
  const box=[520000,174000,540000,188000],ids=graph.networkIds(box,1000);
  assert.ok(ids.length>0&&ids.length<=1000);
  for(const id of ids)assert.ok(id<graph.meta.snap_geometry_count);
});
test('optional full GB data matches Python local and intercity routing', {skip:!process.env.ROADS_NATIONAL_DATA},()=>{
  const data=process.env.ROADS_NATIONAL_DATA,graph=readGraph(path.join(data,'gb.rdb'));
  const manifest=JSON.parse(fs.readFileSync(path.join(data,'manifest.json')));
  assert.equal(graph.meta.node_count,manifest.nodes);assert.equal(graph.meta.snap_geometry_count,manifest.roads);
  for(const route of JSON.parse(fs.readFileSync(path.join(data,'parity.json')))){
    assert.equal(graph.route(route.from_bng,route.to_bng,{radius:250,candidates:8}).cost_m,route.cost_m);
  }
  assert.ok(graph.networkIds(manifest.bbox_bng,10000).length<=10000);
});
