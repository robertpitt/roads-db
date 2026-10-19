const CACHE="roads-db-shell:__VERSION__";
const ASSETS=["./","index.html","style.css","app.js","api.js","worker.js","engine.js",
  "vendor/leaflet.js","vendor/leaflet.css","vendor/proj4.js","vendor/sql-wasm.js","vendor/sql-wasm.wasm",
  "data/manifest.json","data/NOTICE.txt"];
self.addEventListener("install",event=>event.waitUntil(caches.open(CACHE).then(cache=>
  cache.addAll(ASSETS.map(path=>new Request(new URL(path,self.location),{cache:"reload"})))).then(()=>self.skipWaiting())));
self.addEventListener("activate",event=>event.waitUntil((async()=>{
  for(const name of await caches.keys())if(name.startsWith("roads-db-shell:")&&name!==CACHE)await caches.delete(name);
  await self.clients.claim();
})()));
self.addEventListener("fetch",event=>{
  const url=new URL(event.request.url);
  if(event.request.method!=="GET"||url.origin!==self.location.origin||!url.pathname.startsWith(new URL(".",self.location).pathname))return;
  event.respondWith((async()=>{
    if(url.pathname.endsWith("/manifest.json")){
      try{const response=await fetch(event.request);if(response.ok){const cache=await caches.open(CACHE);await cache.put(event.request,response.clone());return response;}}catch{}
    }
    return await caches.match(event.request)||fetch(event.request);
  })());
});
