import {createRoadsClient} from "./api.js";
const $=id=>document.getElementById(id),status=$("loadStatus");
const client=createRoadsClient({onProgress:text=>status.textContent=text});
window.roadsDB=client;
const map=L.map("map",{zoomControl:false,preferCanvas:true}).setView([51.51,-0.12],13);
L.control.zoom({position:"bottomright"}).addTo(map);
const markers=L.layerGroup().addTo(map),routeLayer=L.layerGroup().addTo(map);
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
$("exampleButton").onclick=()=>{$("from").value="51.5010,-0.1416";$("to").value="51.5055,-0.0754";route();};
map.on("click",event=>{if(!ready)return;sequence++;clearResult();$(next).value=`${event.latlng.lat.toFixed(6)},${event.latlng.lng.toFixed(6)}`;next=next==="from"?"to":"from";placeMarkers();$("mapNote").textContent=`Click the map to set ${next==="from"?"A":"B"}`;if($("from").value&&$("to").value)route();});
for(const active of["steps","api"])$(active+"Tab").onclick=()=>{for(const key of["steps","api"]){$(key+"Tab").setAttribute("aria-pressed",String(key===active));$(key+"Panel").hidden=key!==active;}};
$("copyButton").onclick=async()=>{if(!lastResponse)return;try{await navigator.clipboard.writeText(JSON.stringify(lastResponse,null,2));$("copyButton").textContent="Copied";}catch{$("copyButton").textContent="Select and copy the response below";}};
let offlineReady=false;
function connectivity(){if(ready){status.textContent=`${navigator.onLine?"On-device routing":"Offline routing"} · ${offlineReady?"saved for offline use":"saving offline app…"}`;status.className="load-status ready";}}
window.addEventListener("online",connectivity);window.addEventListener("offline",connectivity);
const offline="serviceWorker" in navigator?navigator.serviceWorker.register("sw.js").then(()=>navigator.serviceWorker.ready).then(()=>{offlineReady=true;connectivity();}).catch(()=>{}):Promise.resolve();
try{
  const manifest=await client.ready;
  map.attributionControl.addAttribution(manifest.attribution);
  map.fitBounds(manifest.bounds,{padding:[30,30]});
  L.rectangle(manifest.bounds,{color:"#80977d",weight:1,dashArray:"6 5",fill:false,interactive:false}).addTo(map);
  $("networkStats").textContent=`${manifest.roads.toLocaleString()} roads · ${(Object.values(manifest.files).reduce((s,f)=>s+f.bytes,0)/1e6).toFixed(1)} MB`;
  status.textContent="Drawing the local road network…";
  const network=await client.network();L.polyline(network,{color:"#abb6a4",weight:1.1,opacity:.85,interactive:false,smoothFactor:1.5}).addTo(map).bringToBack();
  for(const [label,point]of[["WESTMINSTER",[51.497,-.13]],["SOHO",[51.514,-.135]],["CITY OF LONDON",[51.515,-.09]],["SOUTHWARK",[51.499,-.087]],["CAMDEN",[51.539,-.143]]])L.marker(point,{interactive:false,icon:L.divIcon({className:"landmark",html:label,iconSize:[130,15]})}).addTo(map);
  ready=true;$("routeButton").disabled=false;connectivity();
  const params=new URLSearchParams(location.search);if(params.has("from"))$("from").value=params.get("from");if(params.has("to"))$("to").value=params.get("to");
  await route();
}catch(error){status.textContent=`Unable to load London: ${error.message}`;status.className="load-status error";$("mapNote").textContent="Reload to retry the dataset download";}
