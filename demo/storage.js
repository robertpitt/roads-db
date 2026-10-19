/* Versioned, checksum-verified dataset cache shared by the page and worker. */
(function(root){
  const base=new URL('.',root.location?.href||'https://localhost/');
  const manifestPath=dataset=>dataset==='gb'?'data/gb/manifest.json':'data/manifest.json';
  const cacheName=manifest=>`roads-db-data:${base.pathname}:${manifest.id}`;
  const entries=manifest=>Object.entries(manifest.files).flatMap(([name,file])=>file.parts||[{name,bytes:file.bytes,download_bytes:file.bytes,sha256:file.sha256}]);
  const directory=dataset=>new URL(dataset==='gb'?'data/gb/':'data/',base);
  async function manifest(dataset){
    if(!['london','gb'].includes(dataset))throw new Error('Unknown dataset');
    const response=await fetch(new URL(manifestPath(dataset),base),{cache:'no-cache'});
    if(!response.ok)throw new Error('Dataset manifest is unavailable');
    return response.json();
  }
  async function inspect(dataset,m){
    const cache=await caches.open(cacheName(m));let cached=0,total=0;
    for(const part of entries(m)){total+=part.download_bytes;if(await cache.match(new URL(part.name,directory(dataset))))cached+=part.download_bytes;}
    return {cached,total,complete:cached===total};
  }
  async function remove(dataset,m){await caches.delete(cacheName(m));}
  async function load(dataset,m,progress=()=>{}){
    const cache=await caches.open(cacheName(m)),state=await inspect(dataset,m);let complete=0,downloaded=0;
    const files={};
    for(const [name,file]of Object.entries(m.files)){
      const output=new Uint8Array(file.bytes);let offset=0;
      for(const part of file.parts||[{name,bytes:file.bytes,download_bytes:file.bytes,sha256:file.sha256}]){
        const url=new URL(part.name,directory(dataset));let response=await cache.match(url),hit=!!response;
        if(!response){response=await fetch(url);if(!response.ok)throw new Error(`Download failed: ${part.name}. Retry to resume.`);}
        const bytes=new Uint8Array(await response.arrayBuffer());
        const hash=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes)),x=>x.toString(16).padStart(2,'0')).join('');
        if(bytes.byteLength!==part.download_bytes||hash!==part.sha256){await cache.delete(url);throw new Error(`Invalid download: ${part.name}. Retry to resume.`);}
        if(!hit){
          try{await cache.put(url,new Response(bytes));}catch(error){throw new Error(error.name==='QuotaExceededError'?'Browser storage is full. Free space and retry to resume.':`Unable to save the dataset: ${error.message}`);}
          downloaded+=bytes.byteLength;
        }
        const decoded=file.parts?new Uint8Array(await new Response(new Blob([bytes]).stream().pipeThrough(new DecompressionStream('gzip'))).arrayBuffer()):bytes;
        if(decoded.byteLength!==part.bytes||offset+decoded.byteLength>output.length){await cache.delete(url);throw new Error('Invalid dataset chunk size');}
        output.set(decoded,offset);offset+=decoded.length;complete+=part.download_bytes;
        progress({stage:hit?'Loading saved data':'Downloading and saving',complete,total:state.total,downloaded});
      }
      if(offset!==file.bytes)throw new Error('Incomplete dataset');
      files[name]=output.buffer;
    }
    return {files,downloaded};
  }
  const api={manifest,inspect,remove,load,entries,cacheName,manifestPath};root.RoadsStorage=api;
  if(typeof module!=='undefined')module.exports=api;
})(globalThis);
