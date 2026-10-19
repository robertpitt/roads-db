/* RDB v1 browser reader and A* runtime. MIT; see repository LICENSE. */
(function (root) {
  "use strict";
  const NONE = 0xffffffff;
  const roundEven = x => { const n = Math.floor(x), f = x - n; return f === 0.5 ? n + (n % 2) : Math.round(x); };

  class Heap {
    constructor() { this.items = []; }
    push(item) {
      const a = this.items; a.push(item); let i = a.length - 1;
      while (i) { const p = (i - 1) >> 1; if (Heap.compare(a[p], item) <= 0) break; a[i] = a[p]; i = p; }
      a[i] = item;
    }
    pop() {
      const a = this.items, first = a[0], last = a.pop();
      if (a.length) {
        let i = 0;
        while (i * 2 + 1 < a.length) {
          let c = i * 2 + 1;
          if (c + 1 < a.length && Heap.compare(a[c + 1], a[c]) < 0) c++;
          if (Heap.compare(last, a[c]) <= 0) break;
          a[i] = a[c]; i = c;
        }
        a[i] = last;
      }
      return first;
    }
    static compare(a, b) { return a[0] - b[0] || a[1] - b[1] || a[2] - b[2]; }
  }

  class RoadsGraph {
    constructor(buffer) {
      const view = new DataView(buffer), decoder = new TextDecoder();
      if (buffer.byteLength < 12 || decoder.decode(new Uint8Array(buffer, 0, 4)) !== "RDB1" || view.getUint32(4, true) !== 1)
        throw new Error("Unsupported or truncated RDB; expected format 1");
      const count = view.getUint32(8, true), sections = new Map();
      if (12 + count * 48 > buffer.byteLength) throw new Error("Truncated RDB directory");
      const ranges = [];
      for (let i = 0; i < count; i++) {
        const at = 12 + i * 48;
        const name = decoder.decode(new Uint8Array(buffer, at, 32)).split("\0")[0];
        const offset = Number(view.getBigUint64(at + 32, true)), bytes = Number(view.getBigUint64(at + 40, true));
        if (!name || sections.has(name) || !Number.isSafeInteger(offset + bytes) || offset < 12 + count * 48 || offset + bytes > buffer.byteLength)
          throw new Error("Invalid RDB section");
        sections.set(name, {offset, bytes}); ranges.push([offset, offset + bytes]);
      }
      ranges.sort((a, b) => a[0] - b[0]);
      for (let i = 1; i < ranges.length; i++) if (ranges[i][0] < ranges[i-1][1]) throw new Error("Overlapping RDB sections");
      const meta = sections.get("metadata");
      if (!meta) throw new Error("Missing RDB metadata");
      this.meta = JSON.parse(decoder.decode(new Uint8Array(buffer, meta.offset, meta.bytes)));
      if (this.meta.format_version !== 1) throw new Error("Invalid metadata version");
      const m = this.meta;
      const sizes = {node_x:m.node_count,node_y:m.node_count,edge_to:m.directed_edge_count,
        edge_length:m.directed_edge_count,edge_geom:m.directed_edge_count,offsets:m.node_count+1,
        components:m.node_count,snap_geom_records:m.snap_geometry_count*6,
        snap_geom_bboxes:m.snap_geometry_count*4,snap_geom_offsets:m.snap_geometry_count+1,
        snap_geom_points:m.snap_geom_point_count*2,snap_tiles:m.snap_tile_count*4,
        snap_tile_geoms:m.snap_tile_geom_count,component_metadata:m.component_count*6};
      if (new Uint8Array(new Uint32Array([1]).buffer)[0] !== 1) throw new Error("Little-endian browser required");
      for (const [name, length] of Object.entries(sizes)) {
        const s = sections.get(name);
        if (!Number.isSafeInteger(length) || length < 0 || !s || s.bytes !== length*4 || s.offset%4) throw new Error(`Invalid section: ${name}`);
        this[name] = new Uint32Array(buffer, s.offset, length);
      }
      this.dist = new Float64Array(m.node_count); this.dist.fill(Infinity);
      this.prev = new Int32Array(m.node_count); this.prev.fill(-1);
      this.prevEdge = new Int32Array(m.node_count); this.prevEdge.fill(-1);
      this.settled = new Uint8Array(m.node_count); this.touched = [];
      this.tiles = new Map();
      for (let i = 0; i < m.snap_tile_count; i++) this.tiles.set(`${this.snap_tiles[i*4]},${this.snap_tiles[i*4+1]}`, i*4);
    }
    xy(node) { return [this.meta.origin_x_m + this.node_x[node]/this.meta.coord_scale,
      this.meta.origin_y_m + this.node_y[node]/this.meta.coord_scale]; }
    geometry(id) {
      const out = [], m = this.meta;
      for (let i=this.snap_geom_offsets[id];i<this.snap_geom_offsets[id+1];i++)
        out.push([m.origin_x_m+this.snap_geom_points[i*2]/m.coord_scale,m.origin_y_m+this.snap_geom_points[i*2+1]/m.coord_scale]);
      return out;
    }
    snap(point, radius=250, limit=8) {
      const m = this.meta, tx=Math.floor(point[0]/m.snap_tile_size_m), ty=Math.floor(point[1]/m.snap_tile_size_m);
      const r=Math.max(1,Math.ceil(radius/m.snap_tile_size_m)), ids=new Set(), candidates=[];
      for(let x=tx-r;x<=tx+r;x++) for(let y=ty-r;y<=ty+r;y++) {
        const tile=this.tiles.get(`${x},${y}`); if(tile===undefined) continue;
        const first=this.snap_tiles[tile+2], count=this.snap_tiles[tile+3];
        for(let i=first;i<first+count;i++) ids.add(this.snap_tile_geoms[i]);
      }
      for(const id of [...ids].sort((a,b)=>a-b)) {
        const points=this.geometry(id); let best=Infinity,along=0,projected=points[0],travelled=0;
        for(let i=1;i<points.length;i++) {
          const a=points[i-1], b=points[i], dx=b[0]-a[0],dy=b[1]-a[1], sq=dx*dx+dy*dy;
          if(!sq) continue;
          const t=Math.max(0,Math.min(1,((point[0]-a[0])*dx+(point[1]-a[1])*dy)/sq));
          const p=[a[0]+t*dx,a[1]+t*dy],distance=(point[0]-p[0])**2+(point[1]-p[1])**2,len=Math.sqrt(sq);
          if(distance<best) { best=distance;along=travelled+t*len;projected=p; }
          travelled+=len;
        }
        if(best<=radius*radius) candidates.push({id,distance:Math.sqrt(best),along,point:projected});
      }
      candidates.sort((a,b)=>a.distance-b.distance || a.id-b.id);
      return candidates.slice(0,limit);
    }
    endpoints(snaps) {
      const out=new Map();
      for(const snap of snaps) {
        const g=snap.id*6, length=this.snap_geom_records[g+4];
        const along=Math.max(0,Math.min(length,roundEven(snap.along*this.meta.length_scale)));
        const refs=along===0 ? [[this.snap_geom_records[g],0]] : along===length ? [[this.snap_geom_records[g+1],0]] :
          [[this.snap_geom_records[g],along],[this.snap_geom_records[g+1],length-along]];
        for(const [node,cost] of refs) if(!out.has(node)||cost<out.get(node).cost) out.set(node,{cost,snap});
      }
      return out;
    }
    route(origin,target,{radius=250,candidates=8}={}) {
      if(![...origin,...target].every(Number.isFinite)||radius<=0||radius>1000||candidates<1||candidates>32) throw new Error("Invalid route coordinates or snap options");
      const originSnaps=this.snap(origin,radius,candidates),targetSnaps=this.snap(target,radius,candidates);
      const starts=this.endpoints(originSnaps),ends=this.endpoints(targetSnaps);
      let direct=null;
      for(const a of originSnaps)for(const b of targetSnaps)if(a.id===b.id){
        const length=this.snap_geom_records[a.id*6+4];
        const cost=Math.abs(Math.min(length,roundEven(a.along*this.meta.length_scale))-Math.min(length,roundEven(b.along*this.meta.length_scale)));
        if(!direct||cost<direct.cost)direct={cost,origin:a,target:b};
      }
      if(!starts.size||!ends.size) throw new Error("Point did not snap to a road. Choose a point inside the London extract.");
      const components=new Set([...starts.keys()].map(n=>this.components[n]));
      const shared=new Set([...ends.keys()].map(n=>this.components[n]).filter(c=>components.has(c)));
      if(!shared.size) throw new Error("No route between these disconnected roads");
      for(const n of starts.keys()) if(!shared.has(this.components[n])) starts.delete(n);
      for(const n of ends.keys()) if(!shared.has(this.components[n])) ends.delete(n);
      for(const n of this.touched) {this.dist[n]=Infinity;this.prev[n]=-1;this.prevEdge[n]=-1;this.settled[n]=0;}
      this.touched=[];
      const heap=new Heap(), refs=[...ends.entries()], scale=this.meta.length_scale/this.meta.coord_scale;
      const heuristic=n=>{let best=Infinity;for(const[t,e]of refs){const estimate=Math.floor(Math.hypot(this.node_x[n]-this.node_x[t],this.node_y[n]-this.node_y[t])*scale)+e.cost;if(estimate<best)best=estimate;}return best;};
      for(const [n,e] of starts) {this.touched.push(n);this.dist[n]=e.cost;heap.push([e.cost+heuristic(n),e.cost,n]);}
      let best=Infinity,finish=-1,visited=0;
      while(heap.items.length) {
        const [priority,cost,n]=heap.pop();
        if(cost!==this.dist[n]||this.settled[n]) continue;
        if(priority>=best) break;
        this.settled[n]=1;visited++;
        if(ends.has(n)&&cost+ends.get(n).cost<best) {best=cost+ends.get(n).cost;finish=n;}
        for(let e=this.offsets[n];e<this.offsets[n+1];e++) {
          const next=this.edge_to[e],distance=cost+this.edge_length[e];
          if(!this.settled[next]&&distance<this.dist[next]) {
            if(this.dist[next]===Infinity)this.touched.push(next);
            this.dist[next]=distance;this.prev[next]=n;this.prevEdge[next]=e;
            heap.push([distance+heuristic(next),distance,next]);
          }
        }
      }
      if(finish<0)throw new Error("No route found");
      if(direct&&direct.cost<=best)return{nodes:[],edges:[],direct:true,cost_m:direct.cost/this.meta.length_scale,origin:direct.origin,target:direct.target,visited};
      const nodes=[],edges=[]; let n=finish;
      while(n>=0){nodes.push(n);if(this.prevEdge[n]>=0)edges.push(this.prevEdge[n]);n=this.prev[n];}
      nodes.reverse();edges.reverse();
      return {nodes,edges,cost_m:best/this.meta.length_scale,origin:starts.get(nodes[0]).snap,target:ends.get(finish).snap,visited};
    }
  }
  root.RoadsGraph=RoadsGraph;
  if(typeof module!=="undefined")module.exports=RoadsGraph;
})(globalThis);
