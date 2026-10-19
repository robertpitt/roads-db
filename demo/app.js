import {createRoadsClient} from "./api.js";

const $=id=>document.getElementById(id);
const defaults={london:{from:"51.5010,-0.1416",to:"51.5055,-0.0754"},gb:{from:"53.4808,-2.2426",to:"53.8008,-1.5491"}};
const pointsByDataset=new Map();
const map=L.map("map",{zoomControl:false,preferCanvas:true}).setView([51.51,-0.12],13);
L.control.zoom({position:"bottomright"}).addTo(map);
const markers=L.layerGroup().addTo(map),routeLayer=L.layerGroup().addTo(map),networkLayer=L.layerGroup().addTo(map),boundsLayer=L.layerGroup().addTo(map);
const tiles=L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png",{maxZoom:19,attribution:'&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</a>'});
let client,manifest,activeDataset,selection=0,sequence=0,networkSequence=0,cameraRevision=0;
let ready=false,loading=false,routing=false,activePoint=null,lastResponse=null,offlineReady=false;
const distance=m=>m>=1000?`${(m/1000).toFixed(2)} km`:`${Math.round(m)} m`;
const mb=bytes=>`${(bytes/1e6).toFixed(1)} MB`;
const icon=name=>{const svg=document.createElementNS("http://www.w3.org/2000/svg","svg"),use=document.createElementNS("http://www.w3.org/2000/svg","use");svg.classList.add("icon");svg.setAttribute("aria-hidden","true");use.setAttribute("href",`#i-${name}`);svg.append(use);return svg;};

function parsePoint(id){
  const parts=$(id).value.trim().split(",");
  const label=id==="from"?"Starting point":"Destination";
  if(parts.length!==2||parts.some(v=>!v.trim()))throw new Error(`${label}: enter latitude, longitude, or choose a point on the map.`);
  const point=parts.map(Number);
  if(!point.every(Number.isFinite)||Math.abs(point[0])>90||Math.abs(point[1])>180)throw new Error(`${label}: latitude must be −90 to 90 and longitude −180 to 180.`);
  return point;
}
function requestPoints(){return {from:parsePoint("from"),to:parsePoint("to")};}
function validPoints(){try{requestPoints();return true;}catch{return false;}}
function updateControls(){
  $("routeButton").disabled=!ready||routing||!validPoints();
  $("routeButton").classList.toggle("busy",routing);
  $("routeButtonLabel").textContent=routing?"Finding route…":"Find route";
  $("pickFrom").disabled=$("pickTo").disabled=$("swapButton").disabled=!ready;
  $("exampleButton").disabled=!ready;
  $("fitRoute").disabled=!lastResponse;
  $("copyButton").disabled=!lastResponse;
}
function setPick(point){
  activePoint=ready?point:null;
  document.body.classList.toggle("picking",!!activePoint);
  for(const id of ["from","to"]){const selected=activePoint===id;$(id+"Field").classList.toggle("active",selected);$(id==="from"?"pickFrom":"pickTo").setAttribute("aria-pressed",String(selected));}
  $("mapPrompt").hidden=!activePoint;
  $("mapNote").textContent=activePoint==="from"?"Click to place your starting point":"Click to place your destination";
}
function clearError(){
  $("routeError").hidden=true;
  for(const id of ["from","to"]){$(id).setAttribute("aria-invalid","false");$(id+"Field").classList.remove("invalid");}
}
function showError(error){
  $("routeError").textContent=error.message;$("routeError").hidden=false;
  for(const id of ["from","to"]){try{parsePoint(id);}catch{$(id).setAttribute("aria-invalid","true");$(id+"Field").classList.add("invalid");}}
}
function emptyState(title="A route starts with two points",description="Set a start and destination to see your directions here."){
  $("emptySteps").hidden=false;$("emptyTitle").textContent=title;$("emptyDescription").textContent=description;
  $("emptySteps").classList.toggle("busy",routing);
}
function clearResult(){
  sequence++;routing=false;lastResponse=null;routeLayer.clearLayers();$("steps").replaceChildren();
  $("routeSummary").hidden=true;$("distance").textContent=$("timing").textContent=$("edgeCount").textContent="—";
  $("copyLabel").textContent="Copy JSON";$("apiResult").textContent="Choose a route to inspect its response.";
  $("routeAnnouncement").textContent="";clearError();emptyState();updateControls();
}
function placeMarkers(){
  markers.clearLayers();
  for(const [index,key]of ["from","to"].entries()){
    let point;try{point=parsePoint(key);}catch{continue;}
    const marker=L.marker(point,{draggable:true,keyboard:true,title:index?"Destination B; drag to adjust":"Starting point A; drag to adjust",icon:L.divIcon({className:"",html:`<div class="point-marker ${index?"b":""}">${index?"B":"A"}</div>`,iconSize:[32,32],iconAnchor:[16,16]})}).addTo(markers);
    marker.on("dragstart",()=>{setPick(null);clearResult();});
    marker.on("dragend",()=>{const p=marker.getLatLng();$(key).value=`${p.lat.toFixed(6)},${p.lng.toFixed(6)}`;commitPoints();});
  }
}
function commitPoints(){
  clearResult();placeMarkers();
  try{requestPoints();if(ready)void route();}catch(error){if($("from").value.trim()&&$("to").value.trim())showError(error);}
  updateControls();
}
function cameraPadding(){
  return matchMedia("(min-width: 901px)").matches?{paddingTopLeft:[$("dataset").closest("aside").getBoundingClientRect().width+48,84],paddingBottomRight:[70,70]}:{paddingTopLeft:[35,65],paddingBottomRight:[45,45]};
}
function frame(bounds){map.fitBounds(bounds,{...cameraPadding(),maxZoom:15,animate:false});}
// Camera changes are explicit. Routing, point placement, typing and pin dragging
// never frame the map, even when a delayed result arrives after a manual pan.
function showRoute(){if(lastResponse){setPick(null);frame(L.latLngBounds(lastResponse.coordinates));}}
$("fitRoute").onclick=$("showRoute").onclick=showRoute;
$("viewCoverage").onclick=()=>{if(manifest){setPick(null);frame(manifest.bounds);}};
map.on("movestart",()=>cameraRevision++);

function renderSteps(steps){
  $("steps").replaceChildren();
  for(const [index,step]of [...steps,{instruction:"Arrive at your destination",arrival:true}].entries()){
    const row=document.createElement("li"),symbol=document.createElement("span"),content=document.createElement("span"),length=document.createElement("span");
    symbol.className="step-icon";const arrow=icon(step.arrival?"check":index===0?"route":"arrow");
    if(index&&!step.arrival){const text=step.instruction.toLowerCase();arrow.style.transform=`rotate(${text.includes("bear right")?-45:text.includes("bear left")?-135:text.includes("right")?0:text.includes("left")?180:-90}deg)`;}
    symbol.append(arrow);content.textContent=step.instruction;length.className="metres";length.textContent=step.arrival?"":distance(step.distance_m);
    row.append(symbol,content,length);$("steps").append(row);
  }
}
async function route(){
  if(!ready)return;
  let request;try{request=requestPoints();}catch(error){clearResult();showError(error);return;}
  clearResult();placeMarkers();const requestId=sequence,current=client;
  routing=true;updateControls();emptyState("Finding your route…","Calculating entirely on your device.");
  $("routeAnnouncement").textContent="Calculating route.";
  try{
    const result=await current.route(request);if(requestId!==sequence||current!==client)return;
    lastResponse=result;routing=false;
    L.polyline(result.coordinates,{color:"#fff",weight:9,opacity:.96,interactive:false}).addTo(routeLayer);
    L.polyline(result.coordinates,{color:"#2563eb",weight:5,opacity:1,interactive:false}).addTo(routeLayer);
    for(const [key,snap]of [["from",result.origin_snap],["to",result.target_snap]])L.polyline([request[key],snap],{color:"#7d93b1",weight:2,dashArray:"3 6",interactive:false}).addTo(routeLayer);
    $("distance").textContent=distance(result.cost_m);$("timing").textContent=`${result.elapsed_ms} ms`;$("edgeCount").textContent=result.edge_count.toLocaleString();
    $("routeSummary").hidden=false;$("emptySteps").hidden=true;renderSteps(result.steps);
    $("apiExample").textContent=`await roadsDB.route(${JSON.stringify(request,null,2)})`;
    $("apiResult").textContent=JSON.stringify(result,null,2);
    $("routeAnnouncement").textContent=`Route ready. ${distance(result.cost_m)}, calculated on your device. Use Show route to fit the map.`;
  }catch(error){
    if(requestId!==sequence||current!==client)return;routing=false;showError(error);
    emptyState("No route found","Adjust your points and try again. Both points need to be near connected roads in this dataset.");
    $("apiResult").textContent=JSON.stringify({error:error.message},null,2);
  }finally{if(requestId===sequence&&current===client)updateControls();}
}
$("routeForm").onsubmit=event=>{event.preventDefault();setPick(null);void route();};
$("pickFrom").onclick=()=>setPick(activePoint==="from"?null:"from");
$("pickTo").onclick=()=>setPick(activePoint==="to"?null:"to");
$("cancelPick").onclick=()=>setPick(null);
window.addEventListener("keydown",event=>{if(event.key==="Escape")setPick(null);});
$("swapButton").onclick=()=>{setPick(null);[$("from").value,$("to").value]=[$("to").value,$("from").value];commitPoints();};
$("resetButton").onclick=()=>{cameraRevision++;$("from").value=$("to").value="";clearResult();markers.clearLayers();setPick("from");};
$("exampleButton").onclick=()=>{setPick(null);const preset=defaults[$("dataset").value];$("from").value=preset.from;$("to").value=preset.to;commitPoints();};
for(const id of ["from","to"]){
  $(id).addEventListener("input",()=>{cameraRevision++;clearResult();updateControls();});
  $(id).addEventListener("change",commitPoints);
}
map.on("click",event=>{
  if(!ready||!activePoint)return;
  const key=activePoint;$(key).value=`${event.latlng.lat.toFixed(6)},${event.latlng.lng.toFixed(6)}`;
  setPick(key==="from"&&!$("to").value.trim()?"to":null);commitPoints();
});
$("tiles").onchange=()=>$("tiles").checked?tiles.addTo(map):map.removeLayer(tiles);
function selectTab(active,focus=false){
  for(const key of ["steps","api"]){const selected=key===active;$(key+"Tab").setAttribute("aria-selected",String(selected));$(key+"Tab").tabIndex=selected?0:-1;$(key+"Panel").hidden=!selected;}
  if(focus)$(active+"Tab").focus();
}
for(const active of ["steps","api"]){
  $(active+"Tab").onclick=()=>selectTab(active);
  $(active+"Tab").onkeydown=event=>{if(["ArrowLeft","ArrowRight","Home","End"].includes(event.key)){event.preventDefault();selectTab(event.key==="Home"?"steps":event.key==="End"?"api":active==="steps"?"api":"steps",true);}};
}
$("copyButton").onclick=async()=>{
  if(!lastResponse)return;const current=sequence;
  try{await navigator.clipboard.writeText(JSON.stringify(lastResponse,null,2));if(current===sequence){$("copyLabel").textContent="Copied";setTimeout(()=>{if(current===sequence)$("copyLabel").textContent="Copy JSON";},2500);}}
  catch{$("copyLabel").textContent="Select JSON to copy";}
};

function connectivity(){
  if(!ready)return;
  $("connection").className="connection ready";$("connectionText").textContent=navigator.onLine?"On-device routing":"Offline routing";
  $("loadStatus").className="load-status ready";
  $("loadStatus").textContent=offlineReady?"App and data available offline":"Data saved · preparing offline app…";
}
window.addEventListener("online",connectivity);window.addEventListener("offline",connectivity);
if("serviceWorker" in navigator)navigator.serviceWorker.register("sw.js").then(()=>navigator.serviceWorker.ready).then(()=>{offlineReady=true;connectivity();}).catch(()=>{});
async function storagePolicy(){
  let persistent=false;try{persistent=await navigator.storage?.persisted?.()||false;}catch{}
  $("storagePolicy").textContent=persistent?"Storage protected from automatic eviction.":"Storage is saved on this device; this browser may reclaim it when space is needed.";
}
async function drawNetwork(){
  if(!ready)return;const current=client,id=++networkSequence,b=map.getBounds();
  try{
    const lines=await current.network({bounds:[b.getSouth(),b.getNorth()],longitudes:[b.getWest(),b.getEast()],zoom:map.getZoom()});
    if(id!==networkSequence||current!==client)return;
    networkLayer.clearLayers();L.polyline(lines,{color:"#a8b9cb",weight:1.15,opacity:.78,interactive:false,smoothFactor:1.5}).addTo(networkLayer).bringToBack();
  }catch{}
}
let moveTimer;map.on("moveend",()=>{clearTimeout(moveTimer);moveTimer=setTimeout(drawNetwork,180);});
function datasetDescription(saved){
  const gb=activeDataset==="gb",count=manifest.roads.toLocaleString();
  $("datasetInfo").textContent=saved.complete?`${count} roads · Saved on this device`:gb?`${mb(saved.total)} download · Desktop recommended. Great Britain excludes Northern Ireland.`:`${count} roads · ${mb(saved.total)} download`;
  $("datasetFootprint").textContent=`${gb?"England, Scotland and Wales; excludes Northern Ireland. ":"Central London. "}${mb(saved.total)} download, ${mb(Object.values(manifest.files).reduce((sum,f)=>sum+f.bytes,0))} expanded.${gb?" Desktop recommended; allow about 1.5 GB of memory.":""}`;
}
async function chooseDataset({auto=true,frameMap=true,preservePoints=false}={}){
  const current=++selection,dataset=$("dataset").value,revision=cameraRevision;
  if(activeDataset&&!preservePoints)pointsByDataset.set(activeDataset,{from:$("from").value,to:$("to").value});
  const changed=activeDataset!==dataset;activeDataset=dataset;
  if(changed&&!preservePoints){const points=pointsByDataset.get(dataset)||defaults[dataset];$("from").value=points.from;$("to").value=points.to;}
  networkSequence++;ready=false;loading=false;client?.close();client=null;manifest=null;window.roadsDB=null;setPick(null);clearResult();
  markers.clearLayers();networkLayer.clearLayers();boundsLayer.clearLayers();
  $("loadDataset").hidden=$("cancelDownload").hidden=$("forgetDataset").hidden=$("downloadProgress").hidden=true;$("viewCoverage").disabled=true;
  $("datasetInfo").textContent="Checking saved data…";$("loadStatus").className="load-status";$("loadStatus").textContent="";
  $("connection").className="connection";$("connectionText").textContent="Preparing data";
  $("dataNotice").href=dataset==="gb"?"data/gb/NOTICE.txt":"data/NOTICE.txt";
  $("datasetCaption").textContent=dataset==="gb"?"Great Britain":"Central London";$("networkStats").textContent="Local road network";
  $("exampleLabel").textContent=dataset==="gb"?"Manchester → Leeds":"Palace → Tower Bridge";
  try{
    const meta=await RoadsStorage.manifest(dataset);if(current!==selection)return;
    const saved=await RoadsStorage.inspect(dataset,meta);if(current!==selection)return;
    manifest=meta;datasetDescription(saved);$("connectionText").textContent=saved.complete?"Saved dataset":saved.cached?"Download paused":"Download needed";$("networkStats").textContent=`${meta.roads.toLocaleString()} roads · Local data`;
    $("viewCoverage").disabled=false;
    // Do not interrupt someone who moved the map while the manifest was loading.
    if(frameMap&&cameraRevision===revision)frame(dataset==="london"&&validPoints()?L.latLngBounds([parsePoint("from"),parsePoint("to")]):meta.bounds);
    $("forgetDataset").hidden=!saved.cached;
    $("downloadLabel").textContent=saved.complete?"Open saved dataset":saved.cached?`Resume · ${mb(saved.total-saved.cached)} remaining`:`Download ${dataset==="gb"?"Great Britain":"London"} · ${mb(saved.total)}`;
    $("loadStatus").textContent=saved.complete?"Saved locally · no download needed":saved.cached?`${mb(saved.cached)} saved. Continue where you left off.`:"Download once to start routing offline.";
    $("loadDataset").hidden=false;
    if(auto&&(saved.complete||dataset==="london"))await loadDataset(current);
  }catch(error){if(current===selection){$("loadStatus").textContent=error.message;$("loadStatus").className="load-status error";$("downloadLabel").textContent="Retry loading data";$("loadDataset").hidden=false;$("connection").className="connection error";$("connectionText").textContent="Dataset unavailable";}}
}
async function loadDataset(current=selection){
  if(current!==selection||loading)return;
  if(!manifest){await chooseDataset({frameMap:false,preservePoints:true});return;}
  const dataset=activeDataset;loading=true;ready=false;updateControls();
  $("loadDataset").hidden=true;$("cancelDownload").hidden=false;$("downloadProgress").hidden=false;$("downloadProgress").value=0;$("loadStatus").className="load-status";
  $("loadStatus").textContent="Preparing your dataset…";$("connection").className="connection";$("connectionText").textContent="Preparing data";
  try{
    // Storage protection is best effort; a refusal must not block routing.
    try{await navigator.storage?.persist?.();}catch{}await storagePolicy();
    const saved=await RoadsStorage.inspect(dataset,manifest),estimate=await navigator.storage?.estimate?.();if(current!==selection)return;
    $("cancelDownload").textContent=saved.complete?"Cancel":"Pause";
    if(estimate?.quota&&estimate.quota-(estimate.usage||0)<(saved.total-saved.cached)*1.1)throw new Error("Not enough browser storage. Free space or choose London.");
    client=createRoadsClient({dataset,onProgress:progress=>{
      if(current!==selection)return;
      $("loadStatus").textContent=progress.total?`${progress.stage} · ${mb(progress.complete)} / ${mb(progress.total)}`:progress.stage;
      if(progress.total)$("downloadProgress").value=progress.complete/progress.total;
    }});window.roadsDB=client;
    const meta=await client.ready;if(current!==selection)return;
    ready=true;loading=false;try{localStorage.setItem("roads-db-dataset",dataset);}catch{}
    $("downloadProgress").hidden=$("cancelDownload").hidden=true;$("forgetDataset").hidden=false;
    datasetDescription({...saved,complete:true});
    $("datasetFootprint").textContent+=meta.downloaded_bytes===0?" Loaded from browser storage; 0 MB downloaded this session.":` ${mb(meta.downloaded_bytes)} downloaded and saved this session.`;
    map.attributionControl.addAttribution(meta.attribution);
    L.rectangle(meta.bounds,{color:"#94a9c3",weight:1,dashArray:"5 6",fill:false,interactive:false}).addTo(boundsLayer);
    connectivity();updateControls();placeMarkers();if(validPoints())await route();
    if(current===selection)void drawNetwork();
  }catch(error){
    if(current!==selection)return;
    loading=false;ready=false;client?.close();client=null;window.roadsDB=null;updateControls();
    $("loadStatus").textContent=error.message;$("loadStatus").className="load-status error";
    $("connection").className="connection error";$("connectionText").textContent="Dataset unavailable";
    $("downloadLabel").textContent="Retry / resume";$("loadDataset").hidden=false;$("cancelDownload").hidden=$("downloadProgress").hidden=true;
  }
}
$("dataset").onchange=()=>chooseDataset();
$("loadDataset").onclick=()=>loadDataset();
$("cancelDownload").onclick=async()=>{const operation=selection+1;await chooseDataset({auto:false,frameMap:false,preservePoints:true});if(selection===operation)$("loadStatus").textContent="Loading paused. Saved chunks will be reused when you resume.";};
$("forgetDataset").onclick=()=>{$("removeDescription").textContent=`Remove ${manifest.name} from this browser? You will need to download it again. Other saved datasets will be kept.`;$("removeDialog").returnValue="cancel";$("removeDialog").showModal();};
$("removeDialog").addEventListener("close",async()=>{
  if($("removeDialog").returnValue!=="remove")return;
  const dataset=activeDataset,meta=manifest,current=++selection;ready=false;loading=false;client?.close();client=null;window.roadsDB=null;setPick(null);clearResult();
  try{await RoadsStorage.remove(dataset,meta);if(current!==selection)return;await chooseDataset({auto:false,frameMap:false,preservePoints:true});}catch(error){if(current===selection){$("loadStatus").textContent=error.message;$("loadStatus").className="load-status error";}}
});
try{const saved=localStorage.getItem("roads-db-dataset");if(["london","gb"].includes(saved))$("dataset").value=saved;}catch{}
const params=new URLSearchParams(location.search);
if(["london","gb"].includes(params.get("dataset")))$("dataset").value=params.get("dataset");
if(params.has("from")||params.has("to"))pointsByDataset.set($("dataset").value,{from:params.get("from")??defaults[$("dataset").value].from,to:params.get("to")??defaults[$("dataset").value].to});
updateControls();await storagePolicy();await chooseDataset();
