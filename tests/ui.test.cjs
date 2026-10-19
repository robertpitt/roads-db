/* Planner interaction contract, with DOM/map/worker boundaries replaced by fakes.
 * Camera assertions execute the complete application, including async results.
 */
const {test}=require('node:test');
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const flush=()=>new Promise(resolve=>setImmediate(resolve));
class Element{
  constructor(){this.value='';this.textContent='';this.hidden=false;this.disabled=false;this.style={};this.attributes={};this.children=[];this.listeners={};this.classes=new Set();this.classList={add:x=>this.classes.add(x),remove:x=>this.classes.delete(x),toggle:(x,on)=>on?this.classes.add(x):this.classes.delete(x)};}
  setAttribute(key,value){this.attributes[key]=value;}
  append(...items){this.children.push(...items);}
  replaceChildren(...items){this.children=items;}
  addEventListener(name,fn){(this.listeners[name]??=[]).push(fn);}
  async emit(name,event={}){await Promise.all((this.listeners[name]||[]).map(fn=>fn(event)));}
  closest(){return {getBoundingClientRect:()=>({width:380})};}
  focus(){}showModal(){this.open=true;}
}
async function application(){
  const html=fs.readFileSync(require.resolve('../demo/index.html'),'utf8');
  const elements=Object.fromEntries([...html.matchAll(/\bid="([^"]+)"/g)].map(m=>[m[1],new Element()]));
  elements.dataset.value='london';elements.from.value='51.5010,-0.1416';elements.to.value='51.5055,-0.0754';
  const listeners={},markerList=[],frames=[],requests=[],pending=[],removals=[];let deferred=false;
  const result=(request,cost=5000)=>({coordinates:[request.from,request.to],cost_m:cost,elapsed_ms:3,edge_count:2,visited_nodes:4,origin_snap:request.from,target_snap:request.to,steps:[{road:'Test Road',instruction:'Start on Test Road',distance_m:cost}]});
  const map={setView(){return this;},on(name,fn){(listeners[name]??=[]).push(fn);return this;},fitBounds(bounds){frames.push(bounds);this.emit('movestart');return this;},emit(name,event={}){for(const fn of listeners[name]||[])fn(event);},getBounds:()=>({getSouth:()=>51.45,getNorth:()=>51.57,getWest:()=>-.27,getEast:()=>.02}),getZoom:()=>13,removeLayer(){},attributionControl:{addAttribution(){}}};
  function layer(){return {addTo(){return this;},clearLayers(){},bringToBack(){}};}
  const metadata={name:'Central London',bounds:[[51.45,-.27],[51.57,.02]],roads:10,attribution:'Fixture',files:{'london.rdb':{bytes:1000}}};
  const context={document:{body:new Element(),getElementById:id=>{assert.ok(elements[id],`Known UI element: ${id}`);return elements[id];},createElement:()=>new Element(),createElementNS:()=>new Element()},navigator:{onLine:true,storage:{persisted:async()=>true,persist:async()=>true,estimate:async()=>({quota:1e9,usage:0})}},localStorage:{getItem:()=>null,setItem(){}},location:{search:''},URLSearchParams,matchMedia:()=>({matches:true}),setTimeout:()=>0,clearTimeout(){},console,
    L:{map:()=>map,control:{zoom:()=>layer()},layerGroup:layer,tileLayer:layer,polyline:layer,rectangle:layer,latLngBounds:x=>x,divIcon:x=>x,marker(point){const marker={...layer(),handlers:{},on(name,fn){this.handlers[name]=fn;return this;},getLatLng:()=>({lat:point[0]+.001,lng:point[1]+.001})};markerList.push(marker);return marker;}},
    RoadsStorage:{manifest:async()=>metadata,inspect:async()=>({complete:true,cached:1000,total:1000}),remove:async dataset=>{removals.push(dataset);}},
    createRoadsClient:()=>({ready:Promise.resolve({...metadata,downloaded_bytes:0}),close(){},network:async()=>[],route(request){requests.push(request);if(deferred)return new Promise((resolve,reject)=>pending.push({resolve,reject,request}));return Promise.resolve(result(request));}}),addEventListener(name,fn){(listeners[`window:${name}`]??=[]).push(fn);}};
  context.window=context;
  const source=fs.readFileSync(require.resolve('../demo/app.js'),'utf8').replace(/^import[^\n]+\n/,'');
  await vm.runInNewContext(`(async()=>{${source}\n})()`,context);await flush();
  return {elements,map,frames,requests,markerList,pending,result,removals,defer(){deferred=true;},key(key){map.emit('window:keydown',{key});}};
}
test('placing A/B, swapping, typing and dragging never move the camera; framing is explicit',async()=>{
  const a=await application(),e=a.elements,initial=a.frames.length;
  assert.equal(initial,1,'only the initial dataset is framed');
  a.map.emit('movestart'); // User manually pans before editing their route.
  e.pickFrom.onclick();a.map.emit('click',{latlng:{lat:51.502,lng:-.13}});await flush();
  e.pickTo.onclick();a.map.emit('click',{latlng:{lat:51.51,lng:-.08}});await flush();
  e.swapButton.onclick();await flush();
  e.from.value='51.503,-0.12';await e.from.emit('input');await e.from.emit('change');await flush();
  const marker=a.markerList.at(-1);marker.handlers.dragstart();marker.handlers.dragend();await flush();
  assert.ok(a.requests.length>=5);assert.equal(a.frames.length,initial);
  e.showRoute.onclick();assert.equal(a.frames.length,initial+1);
  e.viewCoverage.onclick();assert.equal(a.frames.length,initial+2);
});
test('out-of-order routing results cannot replace newer routes or reframe a manual pan',async()=>{
  const a=await application(),e=a.elements,initial=a.frames.length;a.defer();
  e.routeForm.onsubmit({preventDefault(){}});await flush();
  e.from.value='51.502,-0.13';await e.from.emit('input');await e.from.emit('change');await flush();
  assert.equal(a.pending.length,2);a.map.emit('movestart');
  a.pending[1].resolve(a.result(a.pending[1].request,2222));await flush();assert.equal(e.distance.textContent,'2.22 km');
  a.pending[0].resolve(a.result(a.pending[0].request,1111));await flush();
  assert.equal(e.distance.textContent,'2.22 km');assert.equal(a.frames.length,initial);
});
test('point picking is explicit, clear guides A then B, Escape cancels, and malformed coordinates do not query',async()=>{
  const a=await application(),e=a.elements;
  const original=e.from.value;a.map.emit('click',{latlng:{lat:50,lng:0}});assert.equal(e.from.value,original);
  e.resetButton.onclick();assert.equal(e.pickFrom.attributes['aria-pressed'],'true');
  a.map.emit('click',{latlng:{lat:51.5,lng:-.1}});assert.equal(e.pickTo.attributes['aria-pressed'],'true');
  a.map.emit('click',{latlng:{lat:51.51,lng:-.11}});await flush();assert.equal(e.pickTo.attributes['aria-pressed'],'false');
  const before=e.to.value;a.map.emit('click',{latlng:{lat:50,lng:0}});assert.equal(e.to.value,before);
  e.pickTo.onclick();a.key('Escape');assert.equal(e.pickTo.attributes['aria-pressed'],'false');
  const count=a.requests.length;
  for(const value of [',-0.1','91,0','51,181','NaN,0','51,']){e.from.value=value;await e.from.emit('input');await e.from.emit('change');e.routeForm.onsubmit({preventDefault(){}});await flush();assert.equal(a.requests.length,count);assert.equal(e.routeButton.disabled,true);}
});

test('removal requires an explicit choice and does not automatically download again',async()=>{
  const a=await application(),e=a.elements,count=a.requests.length;
  e.forgetDataset.onclick();assert.equal(e.removeDialog.returnValue,'cancel');
  await e.removeDialog.emit('close');assert.deepEqual(a.removals,[]);
  e.forgetDataset.onclick();e.removeDialog.returnValue='remove';await e.removeDialog.emit('close');
  assert.deepEqual(a.removals,['london']);assert.equal(a.requests.length,count);assert.equal(e.loadDataset.hidden,false);
  e.forgetDataset.onclick();assert.equal(e.removeDialog.returnValue,'cancel','Escape must not reuse the previous remove decision');
  await e.removeDialog.emit('close');assert.equal(a.removals.length,1);
});
