/* Local-only routing worker: no routing service or API key. */
importScripts("vendor/proj4.js", "vendor/sql-wasm.js", "engine.js", "storage.js");
proj4.defs("EPSG:27700", "+proj=tmerc +lat_0=49 +lon_0=-2 +k=0.9996012717 +x_0=400000 +y_0=-100000 +ellps=airy +towgs84=446.448,-125.157,542.060,0.1502,0.2470,0.8421,-20.4894 +units=m +no_defs");
const projection=proj4("EPSG:4326","EPSG:27700");
let graph,db,manifest,majorIds;
const geographic=p=>{const [lon,lat]=projection.inverse(p);return [lat,lon];};

async function initialise(dataset,progress) {
  manifest=await RoadsStorage.manifest(dataset);
  const {files,downloaded}=await RoadsStorage.load(dataset,manifest,progress);
  progress({stage:"Opening routing graph and SQLite"});
  graph=new RoadsGraph(files[dataset==="gb"?"gb.rdb":"london.rdb"]);
  const SQL=await initSqlJs({locateFile:name=>new URL(`vendor/${name}`,self.location).href});
  db=new SQL.Database(new Uint8Array(files[dataset==="gb"?"gb.sqlite":"london.sqlite"]));
  const table=manifest.metadata_schema==="dictionary-v1"?"road_labels":"roads";
  if(db.exec(`SELECT count(*) FROM ${table}`)[0].values[0][0]!==manifest.roads || graph.meta.node_count!==manifest.nodes)
    throw new Error("Mismatched dataset files");
  return {...manifest,downloaded_bytes:downloaded};
}

function directions(request) {
  const started=performance.now();
  for(const key of ["from","to"]){
    if(!Array.isArray(request[key])||request[key].length!==2||!request[key].every(Number.isFinite))throw new Error(`${key} must be [latitude, longitude]`);
  }
  const from=projection.forward([request.from[1],request.from[0]]),to=projection.forward([request.to[1],request.to[0]]);
  const bounds=manifest.bbox_bng;
  if([from,to].some(p=>p[0]<bounds[0]||p[0]>bounds[2]||p[1]<bounds[1]||p[1]>bounds[3]))throw new Error("Both points must be inside the selected dataset");
  const route=graph.route(from,to,{radius:250,candidates:8});
  const routingMs=performance.now()-started,segments=[];
  function partial(snap,node,reverse=false) {
    const g=snap.id*6,points=graph.geometry(snap.id);
    let travelled=0;const before=[],after=[];
    for(let i=0;i<points.length;i++) {
      if(i)travelled+=Math.hypot(points[i][0]-points[i-1][0],points[i][1]-points[i-1][1]);
      (travelled<snap.along?before:after).push(points[i]);
    }
    const toStart=node===graph.snap_geom_records[g];
    const line=[snap.point,...(toStart?before.reverse():after)];
    if(reverse)line.reverse();
    const length=graph.snap_geom_records[g+4]/graph.meta.length_scale;
    return {id:snap.id,points:line,distance:Math.max(0,toStart?snap.along:length-snap.along)};
  }
  if(route.direct){
    const a=route.origin,b=route.target,points=graph.geometry(a.id),middle=[];
    let travelled=0;
    for(let i=0;i<points.length;i++){
      if(i)travelled+=Math.hypot(points[i][0]-points[i-1][0],points[i][1]-points[i-1][1]);
      if(travelled>Math.min(a.along,b.along)&&travelled<Math.max(a.along,b.along))middle.push(points[i]);
    }
    if(a.along>b.along)middle.reverse();
    segments.push({id:a.id,points:[a.point,...middle,b.point],distance:route.cost_m});
  }else{
  segments.push(partial(route.origin,route.nodes[0]));
  route.edges.forEach((edge,i)=>{
    const id=graph.edge_geom[edge],points=graph.geometry(id);
    if(graph.snap_geom_records[id*6]!==route.nodes[i])points.reverse();
    segments.push({id,points,distance:graph.edge_length[edge]/graph.meta.length_scale});
  });
  segments.push(partial(route.target,route.nodes.at(-1),true));
  }
  const coordinates=[],steps=[],statement=db.prepare("SELECT name,number,classification FROM roads WHERE geom_id=?");
  let lastBearing=null;
  try {
    for(const segment of segments) {
      for(const point of segment.points) {
        const geo=geographic(point),last=coordinates.at(-1);
        if(!last||Math.abs(last[0]-geo[0])+Math.abs(last[1]-geo[1])>1e-10)coordinates.push(geo);
      }
      if(segment.distance<0.05||segment.points.length<2)continue;
      statement.bind([segment.id]);const info=statement.step()?statement.getAsObject():{};statement.reset();
      const road=info.name||info.number||info.classification||"Unnamed road";
      const a=segment.points[0],b=segment.points.at(-1),bearing=Math.atan2(b[0]-a[0],b[1]-a[1])*180/Math.PI;
      if(steps.at(-1)?.road===road)steps.at(-1).distance_m+=segment.distance;
      else {
        const angle=lastBearing===null?0:((bearing-lastBearing+540)%360)-180;
        const turn=Math.abs(angle)<25?"Continue":Math.abs(angle)<60?`Bear ${angle>0?"right":"left"}`:`Turn ${angle>0?"right":"left"}`;
        steps.push({road,distance_m:segment.distance,instruction:`${lastBearing===null?"Start":turn} on ${road}`});
      }
      lastBearing=bearing;
    }
  }finally{statement.free();}
  for(const step of steps)step.distance_m=Math.round(step.distance_m*10)/10;
  return {cost_m:route.cost_m,elapsed_ms:Math.round((performance.now()-started)*10)/10,
    routing_ms:Math.round(routingMs*10)/10,node_count:route.nodes.length,edge_count:route.edges.length,
    visited_nodes:route.visited,origin_snap:geographic(route.origin.point),target_snap:geographic(route.target.point),
    origin_snap_m:Math.round(route.origin.distance*10)/10,target_snap_m:Math.round(route.target.distance*10)/10,
    coordinates,steps,dataset:manifest.id,computed_in:"browser"};
}

self.onmessage=async({data:{id,method,payload}})=>{
  try{
    let result;
    if(method==="init")result=await initialise(payload.dataset,progress=>postMessage({type:"progress",...progress}));
    else if(!graph)throw new Error("Dataset is still loading");
    else if(method==="route")result=directions(payload);
    else if(method==="network"){
      const corners=payload.bounds.flatMap(lat=>payload.longitudes.map(lon=>projection.forward([lon,lat])));
      const bounds=[Math.min(...corners.map(p=>p[0])),Math.min(...corners.map(p=>p[1])),Math.max(...corners.map(p=>p[0])),Math.max(...corners.map(p=>p[1]))];
      let ids;
      if(manifest.metadata_schema==="dictionary-v1"&&payload.zoom<12){
        if(!majorIds)majorIds=db.exec("SELECT geom_id FROM roads WHERE classification IN ('Motorway','A Road')")[0]?.values.map(row=>row[0])||[];
        const m=graph.meta,b=graph.snap_geom_bboxes,box=bounds.map((v,i)=>(v-(i%2?m.origin_y_m:m.origin_x_m))*m.coord_scale);
        ids=majorIds.filter(id=>{const i=id*4;return b[i]<=box[2]&&b[i+2]>=box[0]&&b[i+1]<=box[3]&&b[i+3]>=box[1];});
        const stride=Math.max(1,Math.ceil(ids.length/30000));ids=ids.filter((_,i)=>i%stride===0);
      }else ids=graph.networkIds(bounds,30000);
      result=ids.map(id=>{const points=graph.geometry(id);return (payload.zoom<10?[points[0],points.at(-1)]:points).map(geographic);});
    }
    else throw new Error("Unknown API method");
    postMessage({id,result});
  }catch(error){postMessage({id,error:error.message});}
};
