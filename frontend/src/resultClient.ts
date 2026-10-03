import {api} from './api';

/** Immutable topology is shared by all frames. Values use a bounded per-study cache. */
const topologies = new Map<string, any>();
const frames = new Map<string, any>();
const versions = new Map<string,string>();
const MAX_FRAMES = 16;
export function registerResultVersion(id:string,sha256:string){
 if(versions.get(id)!==sha256){for(const key of frames.keys())if(key.startsWith(id+':'))frames.delete(key);topologies.delete(id);versions.set(id,sha256);}
}
export async function resultTopology(id:string, signal?:AbortSignal) {
  if(topologies.has(id)) return topologies.get(id);
  const value=await api(`/studies/${id}/topology`,{signal});
  topologies.set(id,value);
  if(topologies.size>4) topologies.delete(topologies.keys().next().value!);
  return value;
}
export async function resultFrame(id:string,index:number,signal?:AbortSignal,requestedFields?:string[]) {
  const topology=await resultTopology(id,signal);
  const selection=(requestedFields||[]).join(',');
  const key=`${id}:${versions.get(id)||''}:${topology.topology_id}:${index}:${selection}`;
  if(frames.has(key)){const value=frames.get(key);frames.delete(key);frames.set(key,value);return value;}
  const value=await api(`/studies/${id}/frames/${index}/values${selection?'?fields='+encodeURIComponent(selection):''}`,{signal});
  if(value.topology_id!==topology.topology_id){topologies.delete(id);throw Error('结果网格版本已变化，请重新打开研究。');}
  const merged={...topology,...value};
  frames.set(key,merged);
  if(frames.size>MAX_FRAMES)frames.delete(frames.keys().next().value!);
  return merged;
}
export function prefetchFrames(id:string,index:number,count:number,signal:AbortSignal,requestedFields?:string[]){
  for(const i of [index+1,index-1])if(i>=0&&i<count)resultFrame(id,i,signal,requestedFields).catch(()=>{});
}
export function frameDelay(times:number[],index:number,playbackSeconds=15){
  if(times.length<2)return 500;
  if(index===times.length-1)return 1500;
  return Math.max(24,playbackSeconds*1000*(times[index+1]-times[index])/Math.max(times.at(-1)!-times[0],1e-12));
}
