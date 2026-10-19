const CACHE="roads-db-shell:__VERSION__";
const ASSETS=["./","index.html","style.css","app.js","api.js","worker.js","engine.js","storage.js",
  "vendor/leaflet.js","vendor/leaflet.css","vendor/proj4.js","vendor/sql-wasm.js","vendor/sql-wasm.wasm",
  "data/manifest.json","data/NOTICE.txt","data/gb/manifest.json","data/gb/NOTICE.txt"];
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
    // Dataset bytes belong to the worker's versioned cache. Never satisfy a new
    // data version from an older cache with the same URL.
    if(url.pathname.includes("/data/")&&!url.pathname.endsWith("/manifest.json")&&!url.pathname.endsWith("/NOTICE.txt"))return fetch(event.request);
    return await caches.match(event.request)||fetch(event.request);
  })());
});
