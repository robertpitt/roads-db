const {test,beforeEach}=require('node:test');
const assert=require('node:assert/strict'),crypto=require('node:crypto'),zlib=require('node:zlib');
const Storage=require('../demo/storage.js');
let cachesByName,requests;
const hash=b=>crypto.createHash('sha256').update(b).digest('hex');
const raw=[Buffer.from('first block'),Buffer.from('second block')],packed=raw.map(b=>zlib.gzipSync(b));
const manifest={id:'test-version',files:{'gb.rdb':{bytes:raw.reduce((n,b)=>n+b.length,0),parts:packed.map((b,i)=>({name:`gb.rdb.${i}.gz`,bytes:raw[i].length,download_bytes:b.length,sha256:hash(b)}))}}};
beforeEach(()=>{
  cachesByName=new Map();requests=[];
  global.caches={async open(name){if(!cachesByName.has(name))cachesByName.set(name,new Map());const data=cachesByName.get(name);return {
    async match(url){return data.get(String(url))?.clone();},async put(url,response){data.set(String(url),response.clone());},async delete(url){return data.delete(String(url));}
  };},async delete(name){return cachesByName.delete(name);}};
  global.fetch=async url=>{requests.push(String(url));const i=Number(String(url).match(/\.(\d)\.gz$/)?.[1]);return new Response(packed[i],{status:200});};
});
test('national chunks are verified, expanded and reused without a second download',async()=>{
  assert.equal((await Storage.inspect('gb',manifest)).complete,false);
  const first=await Storage.load('gb',manifest);
  assert.deepEqual(Buffer.from(first.files['gb.rdb']),Buffer.concat(raw));assert.equal(requests.length,2);
  assert.equal((await Storage.inspect('gb',manifest)).complete,true);
  global.fetch=()=>{throw new Error('Network must not be used');};
  const second=await Storage.load('gb',manifest);assert.equal(second.downloaded,0);
  assert.deepEqual(Buffer.from(second.files['gb.rdb']),Buffer.concat(raw));
});
test('an interrupted download retains verified chunks and resumes only missing parts',async()=>{
  const fetch=global.fetch;global.fetch=async url=>{if(String(url).includes('.1.gz'))throw new Error('Disconnected');return fetch(url);};
  await assert.rejects(Storage.load('gb',manifest),/Disconnected/);
  assert.equal((await Storage.inspect('gb',manifest)).cached,packed[0].length);
  requests=[];global.fetch=fetch;await Storage.load('gb',manifest);
  assert.equal(requests.length,1);assert.ok(requests[0].endsWith('.1.gz'));
});
test('bad network and cached chunks are rejected, removed and can be retried',async()=>{
  const fetch=global.fetch;global.fetch=async()=>new Response('corrupt');
  await assert.rejects(Storage.load('gb',manifest),/Invalid download/);
  assert.equal((await Storage.inspect('gb',manifest)).cached,0);
  global.fetch=fetch;await Storage.load('gb',manifest);
  const cache=await caches.open(Storage.cacheName(manifest));await cache.put('https://localhost/data/gb/gb.rdb.1.gz',new Response('corrupt'));
  await assert.rejects(Storage.load('gb',manifest),/Invalid download/);
  requests=[];await Storage.load('gb',manifest);assert.equal(requests.length,1);
});
test('versions and datasets remain independent; eviction and removal allow re-download',async()=>{
  await Storage.load('gb',manifest);
  const london={id:'london-version',files:{'london.rdb':{bytes:raw[0].length,sha256:hash(raw[0])}}};
  global.fetch=async()=>new Response(raw[0]);await Storage.load('london',london);
  await Storage.remove('gb',manifest);
  assert.equal((await Storage.inspect('london',london)).complete,true);
  assert.equal((await Storage.inspect('gb',manifest)).complete,false);
  assert.equal((await Storage.inspect('gb',{...manifest,id:'new-version'})).complete,false);
});
test('quota failures are actionable and never report a complete saved dataset',async()=>{
  const open=caches.open;caches.open=async name=>{const c=await open(name);c.put=async()=>{const e=new Error('Quota');e.name='QuotaExceededError';throw e;};return c;};
  await assert.rejects(Storage.load('gb',manifest),/Browser storage is full/);
  assert.equal((await Storage.inspect('gb',manifest)).complete,false);
});
