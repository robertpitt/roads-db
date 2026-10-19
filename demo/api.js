/** Browser API. Coordinates are [latitude, longitude]; calls stay in a worker. */
export function createRoadsClient({dataset="london",onProgress=()=>{}}={}) {
  const worker=new Worker(new URL("worker.js",import.meta.url));
  const pending=new Map();let sequence=0,disposed=false;
  function send(method,payload) {
    if(disposed)return Promise.reject(new Error("Routing client is closed"));
    return new Promise((resolve,reject)=>{const id=++sequence;pending.set(id,{resolve,reject});worker.postMessage({id,method,payload});});
  }
  worker.onmessage=({data})=>{
    if(data.type==="progress"){onProgress(data);return;}
    const item=pending.get(data.id);if(!item)return;pending.delete(data.id);
    data.error?item.reject(new Error(data.error)):item.resolve(data.result);
  };
  worker.onerror=()=>{for(const item of pending.values())item.reject(new Error("Routing worker failed to load"));pending.clear();};
  const ready=send("init",{dataset});
  return {ready,async route(request){await ready;return send("route",request);},
    async network(view){await ready;return send("network",view);},
    close(){disposed=true;worker.terminate();for(const item of pending.values())item.reject(new Error("Routing client is closed"));pending.clear();}};
}
