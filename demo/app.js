import {createRoadsClient} from "./api.js";
const $=id=>document.getElementById(id),status=$("loadStatus");
let client,manifest,selection=0,networkSequence=0;
const map=L.map("map",{zoomControl:false,preferCanvas:true}).setView([51.51,-0.12],13);
L.control.zoom({position:"bottomright"}).addTo(map);
const markers=L.layerGroup().addTo(map),routeLayer=L.layerGroup().addTo(map),networkLayer=L.layerGroup().addTo(map),boundsLayer=L.layerGroup().addTo(map);
const tiles=L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png",{maxZoom:19,attribution:'&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</a>'});
$("tiles").onchange=()=>$("tiles").checked?tiles.addTo(map):map.removeLayer(tiles);
let ready=false,next="from",sequence=0,lastResponse=null;
const distance=m=>m>=1000?`${(m/1000).toFixed(2)} km`:`${Math.round(m)} m`;
function parse(id){const p=$(id).value.split(",").map(Number);if(p.length!==2||!p.every(Number.isFinite))throw new Error("Use latitude, longitude for each point");return p;}
function placeMarkers(){markers.clearLayers();for(const [i,key]of["from","to"].entries()){
  if(!$(key).value.trim())continue;let point;try{point=parse(key);}catch{continue;}
  const marker=L.marker(point,{draggable:true,icon:L.divIcon({className:"",html:`<div class="point-marker ${i?"b":""}">${i?"B":"A"}</div>`,iconSize:[32,32],iconAnchor:[16,16]})}).addTo(markers);
  marker.on("dragend",()=>{const p=marker.getLatLng();$(key).value=`${p.lat.toFixed(6)},${p.lng.toFixed(6)}`;route();});
}}
function clearResult(){routeLayer.clearLayers();$("steps").replaceChildren();$("emptySteps").hidden=false;$("emptySteps").textContent="Your route will appear here.";for(const id of["distance","timing","edgeCount"])$(id).textContent="—";lastResponse=null;$("copyButton").textContent="Copy response";$("apiResult").textContent="Choose a route to inspect its response.";}
async function route(){
  if(!ready)return;const requestId=++sequence;clearResult();placeMarkers();$("mapNote").textContent="Calculating on your device…";
  try{
    const result=await client.route({from:parse("from"),to:parse("to")});if(requestId!==sequence)return;
    lastResponse=result;
    L.polyline(result.coordinates,{color:"#fffaf1",weight:9,opacity:1}).addTo(routeLayer);
    L.polyline(result.coordinates,{color:"#de643c",weight:5,opacity:1}).addTo(routeLayer);
    for(const [key,snap]of[["from",result.origin_snap],["to",result.target_snap]])L.polyline([parse(key),snap],{color:"#153c36",weight:2,dashArray:"3 6"}).addTo(routeLayer);
    $("distance").textContent=distance(result.cost_m);$("timing").textContent=`${result.elapsed_ms} ms`;$("edgeCount").textContent=result.edge_count;
    $("mapNote").textContent=`Computed locally · ${result.visited_nodes.toLocaleString()} nodes explored`;
    $("emptySteps").hidden=true;
    result.steps.forEach((step,i)=>{const row=document.createElement("li"),number=document.createElement("span"),instruction=document.createElement("span"),length=document.createElement("span");number.className="num";number.textContent=String(i+1).padStart(2,"0");instruction.textContent=step.instruction;length.className="metres";length.textContent=distance(step.distance_m);row.append(number,instruction,length);$("steps").append(row);});
    $("apiResult").textContent=JSON.stringify(result,null,2);
    map.fitBounds(L.latLngBounds(result.coordinates),{padding:[55,90],maxZoom:15});
  }catch(error){if(requestId!==sequence)return;$("mapNote").textContent=error.message;$("emptySteps").textContent=error.message;$("apiResult").textContent=JSON.stringify({error:error.message},null,2);}
}
$("routeForm").onsubmit=event=>{event.preventDefault();route();};
$("swapButton").onclick=()=>{[$("from").value,$("to").value]=[$("to").value,$("from").value];route();};
$("resetButton").onclick=()=>{sequence++;clearResult();markers.clearLayers();$("from").value="";$("to").value="";next="from";$("emptySteps").textContent="Your route will appear here.";$("mapNote").textContent="Click the map to set A";};
$("exampleButton").onclick=()=>{
  const gb=$("dataset").value==="gb";
  $("from").value=gb?"53.4808,-2.2426":"51.5010,-0.1416";$("to").value=gb?"53.8008,-1.5491":"51.5055,-0.0754";route();
};
map.on("click",event=>{if(!ready)return;sequence++;clearResult();$(next).value=`${event.latlng.lat.toFixed(6)},${event.latlng.lng.toFixed(6)}`;next=next==="from"?"to":"from";placeMarkers();$("mapNote").textContent=`Click the map to set ${next==="from"?"A":"B"}`;if($("from").value&&$("to").value)route();});
for(const active of["steps","api"])$(active+"Tab").onclick=()=>{for(const key of["steps","api"]){$(key+"Tab").setAttribute("aria-pressed",String(key===active));$(key+"Panel").hidden=key!==active;}};
$("copyButton").onclick=async()=>{if(!lastResponse)return;try{await navigator.clipboard.writeText(JSON.stringify(lastResponse,null,2));$("copyButton").textContent="Copied";}catch{$("copyButton").textContent="Select and copy the response below";}};
let offlineReady=false,storagePersistent=false;
const mb=bytes=>`${(bytes/1e6).toFixed(1)} MB`;
function connectivity(){if(ready){status.textContent=`${navigator.onLine?"On-device routing":"Offline routing"} · ${offlineReady?"app and data saved":"data saved · saving offline app…"}`;status.className="load-status ready";}}
window.addEventListener("online",connectivity);window.addEventListener("offline",connectivity);
if("serviceWorker" in navigator)navigator.serviceWorker.register("sw.js").then(()=>navigator.serviceWorker.ready).then(()=>{offlineReady=true;connectivity();}).catch(()=>{});
async function storagePolicy(){
  try{storagePersistent=await navigator.storage?.persisted?.()||false;}catch{storagePersistent=false;}
  $("storagePolicy").textContent=storagePersistent?"Protected browser storage":"Browser may reclaim storage";
}
async function drawNetwork(){
  if(!ready)return;const current=client,id=++networkSequence,b=map.getBounds();
  try{
    const lines=await current.network({bounds:[b.getSouth(),b.getNorth()],longitudes:[b.getWest(),b.getEast()],zoom:map.getZoom()});
    if(id!==networkSequence||current!==client)return;
    networkLayer.clearLayers();L.polyline(lines,{color:"#abb6a4",weight:1.1,opacity:.85,interactive:false,smoothFactor:1.5}).addTo(networkLayer).bringToBack();
  }catch{}
}
let moveTimer;map.on("moveend",()=>{clearTimeout(moveTimer);moveTimer=setTimeout(drawNetwork,180);});
async function chooseDataset(auto=true){
  const current=++selection,dataset=$("dataset").value;
  sequence++;networkSequence++;ready=false;client?.close();client=null;window.roadsDB=null;
  clearResult();markers.clearLayers();networkLayer.clearLayers();boundsLayer.clearLayers();
  $("routeButton").disabled=true;$("loadDataset").hidden=true;$("forgetDataset").hidden=true;$("downloadProgress").hidden=true;
  status.className="load-status";status.textContent="Checking saved data…";
  $("dataNotice").href=dataset==="gb"?"data/gb/NOTICE.txt":"data/NOTICE.txt";
  $("datasetCaption").textContent=dataset==="gb"?"GREAT BRITAIN":"CENTRAL LONDON";
  $("exampleButton").textContent=dataset==="gb"?"Manchester → Leeds":"Palace → Tower Bridge";
  try{
    const meta=await RoadsStorage.manifest(dataset);if(current!==selection)return;manifest=meta;
    const saved=await RoadsStorage.inspect(dataset,meta);if(current!==selection)return;
    $("datasetInfo").textContent=dataset==="gb"?`England, Scotland & Wales; excludes Northern Ireland. ${mb(saved.total)} download, ${mb(Object.values(meta.files).reduce((n,f)=>n+f.bytes,0))} expanded. Desktop recommended; allow about 1.5 GB of memory.`:`Central London · ${mb(saved.total)} download.`;
    $("networkStats").textContent=`${meta.roads.toLocaleString()} roads · ${mb(saved.total)}`;
    map.fitBounds(meta.bounds,{padding:[30,30]});
    $("forgetDataset").hidden=!saved.cached;
    $("loadDataset").textContent=saved.complete?"Load saved dataset":saved.cached?`Resume download (${mb(saved.total-saved.cached)} left)`:`Download ${meta.name} (${mb(saved.total)})`;
    $("mapNote").textContent=saved.complete?"Dataset saved on this device":"Download this dataset to start routing";
    status.textContent=saved.complete?"Saved locally · no data download needed":saved.cached?`${mb(saved.cached)} saved · ready to resume`:"Ready for first download";
    $("loadDataset").hidden=false;
    if(auto&&(saved.complete||dataset==="london"))await loadDataset(current);
  }catch(error){if(current===selection){status.textContent=error.message;status.className="load-status error";}}
}
async function loadDataset(current=selection){
  if(current!==selection||!manifest)return;
  const dataset=$("dataset").value;
  $("loadDataset").hidden=true;$("downloadProgress").hidden=false;$("downloadProgress").value=0;status.className="load-status";
  try{
    // Request protection on the page: persist() is unavailable in workers.
    await navigator.storage?.persist?.();await storagePolicy();
    const saved=await RoadsStorage.inspect(dataset,manifest),estimate=await navigator.storage?.estimate?.();
    if(estimate?.quota&&estimate.quota-estimate.usage<(saved.total-saved.cached)*1.1)throw new Error("Not enough browser storage. Free space or choose London.");
    if(current!==selection)return;
    client=createRoadsClient({dataset,onProgress:progress=>{
      if(current!==selection)return;
      status.textContent=progress.total?`${progress.stage} · ${mb(progress.complete)} / ${mb(progress.total)}`:progress.stage;
      if(progress.total)$("downloadProgress").value=progress.complete/progress.total;
    }});window.roadsDB=client;
    const meta=await client.ready;if(current!==selection)return;
    ready=true;try{localStorage.setItem("roads-db-dataset",dataset);}catch{}
    $("downloadProgress").hidden=true;$("forgetDataset").hidden=false;$("routeButton").disabled=false;
    $("datasetInfo").textContent+=(meta.downloaded_bytes===0?" Loaded from browser storage; 0 MB downloaded.":" Saved locally. Future visits reuse this download.");
    map.attributionControl.addAttribution(meta.attribution);
    L.rectangle(meta.bounds,{color:"#80977d",weight:1,dashArray:"6 5",fill:false,interactive:false}).addTo(boundsLayer);
    connectivity();await drawNetwork();if(current!==selection)return;
    const params=new URLSearchParams(location.search);
    $("from").value=params.get("from")||(dataset==="gb"?"53.4808,-2.2426":"51.5010,-0.1416");
    $("to").value=params.get("to")||(dataset==="gb"?"53.8008,-1.5491":"51.5055,-0.0754");
    await route();
  }catch(error){if(current===selection){status.textContent=error.message;status.className="load-status error";$("loadDataset").hidden=false;$("loadDataset").textContent="Retry / resume";$("downloadProgress").hidden=true;}}
}
$("dataset").onchange=()=>chooseDataset();
$("loadDataset").onclick=()=>loadDataset();
$("forgetDataset").onclick=async()=>{const dataset=$("dataset").value;selection++;sequence++;ready=false;client?.close();await RoadsStorage.remove(dataset,manifest);await chooseDataset(false);};
// London stays small and starts automatically. The national first download is opt-in.
try{const saved=localStorage.getItem("roads-db-dataset");if(["london","gb"].includes(saved))$("dataset").value=saved;}catch{}
await storagePolicy();await chooseDataset();
