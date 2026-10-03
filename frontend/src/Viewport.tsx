import {useEffect,useRef,useState,forwardRef,useImperativeHandle} from 'react';
import * as THREE from 'three';
import {OrbitControls} from 'three/addons/controls/OrbitControls.js';
import {fields,number} from './api';
import {deformationCycle,updateDeformedPositions} from './visualization';

export interface Display{topology_id?:string;bounds_points?:number[][];points:number[][];triangles:number[][];surface_ids?:number[];displacements?:number[][];fields?:Record<string,(number|null)[]>;node_ids?:number[];note?:string}
export type ViewHandle={view:(name:string)=>void;focus:(point:number[])=>void;fit:()=>void;orbit:(angle:number)=>void};
const ramp=[new THREE.Color('#164b65'),new THREE.Color('#167e8a'),new THREE.Color('#54b4a4'),new THREE.Color('#ebcf70'),new THREE.Color('#e58a49'),new THREE.Color('#b93638')];
function color(value:number){const x=Math.max(0,Math.min(.99999,value))*(ramp.length-1),i=Math.floor(x);return ramp[i].clone().lerp(ramp[i+1],x-i);}

const Viewport=forwardRef<ViewHandle,{data:Display|null;field:string;selected:number[];onSelect:(ids:number[])=>void;deformation:number;animate:boolean;showOriginal:boolean;autoRotate:boolean;meshVisible:boolean;geometry:any;caseConfig:any;showMarkers:boolean;slice:Display|null;sliceAxis:number;slicePosition:number;peak:number[]|null;range?:[number,number];clipEnabled?:boolean;onProbePick?:(p:{node_id:number;point_m:number[]})=>void}>((props,ref)=>{
 const host=useRef<HTMLDivElement>(null),runtime=useRef<any>(null),latest=useRef(props),[probe,setProbe]=useState<any>(null),[error,setError]=useState('');latest.current=props;
 const fitCamera=(r:any,direction?:THREE.Vector3)=>{if(!r)return;const halfFov=Math.atan(Math.tan(THREE.MathUtils.degToRad(r.camera.fov/2))*Math.min(1,r.camera.aspect));const distance=r.span*.5/Math.sin(halfFov)*1.22;const dir=direction||r.camera.position.clone().sub(r.controls.target).normalize();r.camera.position.copy(r.center).addScaledVector(dir,distance);r.controls.target.copy(r.center);r.controls.update();};
 useImperativeHandle(ref,()=>({orbit(angle){const r=runtime.current;if(r){fitCamera(r,new THREE.Vector3(Math.cos(angle),Math.sin(angle),.55).normalize());r.renderer.render(r.scene,r.camera);}},view(name){const r=runtime.current;if(!r)return;const v:Record<string,number[]>={iso:[1.6,-2,1.4],front:[0,-2,0],top:[0,0,2],right:[2,0,0]};r.camera.up.set(...(name==='top'?[0,1,0]:[0,0,1]) as [number,number,number]);fitCamera(r,new THREE.Vector3(...(v[name]||v.iso)).normalize());},fit(){fitCamera(runtime.current);},focus(point){const r=runtime.current;if(!r)return;const target=new THREE.Vector3(...point),direction=r.camera.position.clone().sub(r.controls.target).normalize();r.camera.position.copy(target).addScaledVector(direction,r.span*.20);r.controls.target.copy(target);r.controls.update();}}));
 useEffect(()=>{
  if(!host.current)return;let renderer:THREE.WebGLRenderer;
  try{renderer=new THREE.WebGLRenderer({antialias:true,alpha:true});}catch(e){setError('浏览器无法创建 WebGL 视图，请启用硬件加速。');return;}
  renderer.setPixelRatio(Math.min(devicePixelRatio,2));renderer.localClippingEnabled=true;renderer.setClearColor('#edf2f4',0);renderer.outputColorSpace=THREE.SRGBColorSpace;host.current.appendChild(renderer.domElement);
  const scene=new THREE.Scene(),camera=new THREE.PerspectiveCamera(36,1,.00001,1000);camera.up.set(0,0,1);
  const controls=new OrbitControls(camera,renderer.domElement);controls.enableDamping=true;controls.dampingFactor=.12;
  scene.add(new THREE.HemisphereLight('#ffffff','#637b89',2.1));const light=new THREE.DirectionalLight('#ffffff',3);light.position.set(.4,-.5,.8);scene.add(light);
  const group=new THREE.Group(),markers=new THREE.Group();scene.add(group,markers);const ray=new THREE.Raycaster(),mouse=new THREE.Vector2();
  const r:any={renderer,scene,camera,controls,group,markers,mesh:null,center:new THREE.Vector3(),span:.1,boundsKey:'',sliceMesh:null,deformTargets:[],lastFactor:NaN,animationStart:null};runtime.current=r;
  const resize=()=>{const element=host.current;if(!element)return;const rect=element.getBoundingClientRect();renderer.setSize(Math.max(1,rect.width),Math.max(1,rect.height));camera.aspect=rect.width/Math.max(1,rect.height);camera.updateProjectionMatrix();};resize();const observer=new ResizeObserver(resize);observer.observe(host.current);
  let frame=0,lastFrame=0;const render=(now=0)=>{frame=requestAnimationFrame(render);if(now-lastFrame<1000/30)return;lastFrame=now;
   const p=latest.current;controls.autoRotate=p.autoRotate;controls.autoRotateSpeed=1.2;
   if(p.animate&&r.animationStart==null)r.animationStart=now;if(!p.animate)r.animationStart=null;
   const factor=p.deformation*(p.animate?deformationCycle((now-r.animationStart)/1000):1);
   if(factor!==r.lastFactor){for(const item of r.deformTargets){updateDeformedPositions(item.geometry.attributes.position.array,item.rest,item.displacement,factor);item.geometry.attributes.position.needsUpdate=true;item.geometry.computeBoundingSphere();}r.lastFactor=factor;}
   controls.update(1/30);renderer.render(scene,camera);
  };render();
  const hit=(e:PointerEvent)=>{const rect=renderer.domElement.getBoundingClientRect();mouse.set((e.clientX-rect.left)/rect.width*2-1,-(e.clientY-rect.top)/rect.height*2+1);ray.setFromCamera(mouse,camera);return ray.intersectObjects([r.sliceMesh,r.mesh].filter(Boolean)).find(h=>!r.clip||h.object===r.sliceMesh||r.clip.distanceToPoint(h.point)>=-1e-9);};
  let start=[0,0];const down=(e:PointerEvent)=>{start=[e.clientX,e.clientY];};
  const click=(e:PointerEvent)=>{if(Math.hypot(e.clientX-start[0],e.clientY-start[1])>4)return;const h=hit(e);if(h&&h.object===r.mesh&&h.faceIndex!=null){if(e.shiftKey&&latest.current.onProbePick){const d=latest.current.data!;const tri=d.triangles[h.faceIndex];const position=r.mesh.geometry.attributes.position;const local=tri.map((idx:number,k:number)=>({idx,distance:new THREE.Vector3().fromBufferAttribute(position,h.faceIndex!*3+k).distanceToSquared(h.point)})).sort((a:any,b:any)=>a.distance-b.distance)[0].idx;latest.current.onProbePick({node_id:d.node_ids?.[local]??local+1,point_m:d.points[local]});return;}const id=latest.current.data?.surface_ids?.[h.faceIndex];if(id!=null){const ids=latest.current.selected;latest.current.onSelect(ids.includes(id)?ids.filter(x=>x!==id):[...ids,id]);}}};
  const move=(e:PointerEvent)=>{const h=hit(e);if(!h||h.faceIndex==null){setProbe(null);return;}const data=h.object===r.sliceMesh?latest.current.slice:latest.current.data;if(!data)return;const fi=h.faceIndex,tri=data.triangles[fi],values=data.fields?.[latest.current.field];
   const pos=(h.object as THREE.Mesh).geometry.attributes.position;const a=new THREE.Vector3().fromBufferAttribute(pos,fi*3),b=new THREE.Vector3().fromBufferAttribute(pos,fi*3+1),c=new THREE.Vector3().fromBufferAttribute(pos,fi*3+2),w=new THREE.Vector3();THREE.Triangle.getBarycoord(h.point,a,b,c,w);
   setProbe({id:data.surface_ids?.[fi],point:h.point.toArray(),value:values&&tri.every(i=>values[i]!=null)?tri.reduce((sum,i,k)=>sum+(values[i] as number)*w.getComponent(k),0):null});};
  renderer.domElement.addEventListener('pointerdown',down);renderer.domElement.addEventListener('pointerup',click);renderer.domElement.addEventListener('pointermove',move);
  return()=>{cancelAnimationFrame(frame);observer.disconnect();controls.dispose();renderer.dispose();renderer.domElement.remove();scene.traverse((x:any)=>{x.geometry?.dispose();if(x.material)Array.isArray(x.material)?x.material.forEach((m:any)=>m.dispose()):x.material.dispose();});runtime.current=null;};
 },[]);
 useEffect(()=>{
  const r=runtime.current;if(!r||!props.data)return;const data=props.data;
  const previous=r.previousProps;
  const canUpdate=previous&&r.mesh&&previous.data?.triangles===data.triangles&&previous.data?.points===data.points&&previous.geometry===props.geometry&&previous.selected===props.selected&&previous.showOriginal===props.showOriginal&&previous.meshVisible===props.meshVisible&&previous.caseConfig===props.caseConfig&&previous.showMarkers===props.showMarkers&&previous.slice===props.slice&&previous.clipEnabled===props.clipEnabled&&previous.sliceAxis===props.sliceAxis&&previous.slicePosition===props.slicePosition&&previous.peak===props.peak&&!!previous.data?.fields?.[previous.field]===!!data.fields?.[props.field]&&!props.slice;
  if(canUpdate&&data.fields?.[props.field]){
   const values=data.fields[props.field];let lo=Infinity,hi=-Infinity;for(const value of values)if(value!=null&&Number.isFinite(value)){lo=Math.min(lo,value);hi=Math.max(hi,value)}if(props.range)[lo,hi]=props.range;if(!Number.isFinite(lo)||!Number.isFinite(hi)){lo=0;hi=1}if(props.field==='safety_factor')hi=Math.min(hi,10);
   const colors=r.mesh.geometry.attributes.color.array;const displacement=r.deformTargets[0]?.displacement;
   data.triangles.forEach((tri,fi)=>tri.forEach((idx,k)=>{const c=values[idx]==null||!Number.isFinite(values[idx])?new THREE.Color('#b8c6cc'):color(((values[idx] as number)-lo)/Math.max(hi-lo,1e-20));const offset=(fi*3+k)*3;colors[offset]=c.r;colors[offset+1]=c.g;colors[offset+2]=c.b;if(displacement){const u=data.displacements?.[idx]||[0,0,0];displacement[offset]=u[0];displacement[offset+1]=u[1];displacement[offset+2]=u[2];}}));
   r.mesh.geometry.attributes.color.needsUpdate=true;r.lastFactor=NaN;r.previousProps=props;setProbe(null);return;
  }
  r.previousProps=props;
  const clear=(group:THREE.Group)=>{for(const obj of [...group.children]){group.remove(obj);obj.traverse((x:any)=>{x.geometry?.dispose();if(x.material)Array.isArray(x.material)?x.material.forEach((m:any)=>m.dispose()):x.material.dispose();});}};clear(r.group);clear(r.markers);r.deformTargets=[];r.lastFactor=NaN;setProbe(null);
  const box=new THREE.Box3().setFromPoints((data.bounds_points?.length?data.bounds_points:data.points).map(p=>new THREE.Vector3(...p)));r.span=Math.max(box.getSize(new THREE.Vector3()).length(),1e-6);r.center=box.getCenter(new THREE.Vector3());
  const key=JSON.stringify([box.min.toArray().map(x=>x.toFixed(5)),box.max.toArray().map(x=>x.toFixed(5))]);
  if(r.boundsKey!==key){r.boundsKey=key;fitCamera(r,new THREE.Vector3(1.5,-1.8,1.2).normalize());r.camera.near=r.span/1000;r.camera.far=r.span*100;r.camera.updateProjectionMatrix();r.controls.target.copy(r.center);r.controls.minDistance=r.span*.1;r.controls.maxDistance=r.span*20;}
  const grid=new THREE.GridHelper(r.span*2,24,'#b6c8ce','#d7e1e5');grid.rotation.x=Math.PI/2;grid.position.set(r.center.x,r.center.y,box.min.z-r.span*.035);r.group.add(grid);
  const axes=new THREE.AxesHelper(r.span*.18);axes.position.copy(box.min).addScalar(-r.span*.12);r.group.add(axes);
  const vals=data.fields?.[props.field];let low=Infinity,high=-Infinity;if(vals)for(const v of vals){if(v!=null&&Number.isFinite(v)){if(v<low)low=v;if(v>high)high=v;}}if(!Number.isFinite(low)||!Number.isFinite(high)){low=0;high=1;}if(props.range)[low,high]=props.range;
  if(props.field==='safety_factor'&&vals)high=Math.min(high,10);
  r.clip=(props.slice||props.clipEnabled)?new THREE.Plane(new THREE.Vector3().setComponent(props.sliceAxis,-1),props.slicePosition):null;
  const makeMesh=(d:Display,slice=false)=>{
   const points:number[]=[],displacements:number[]=[],colors:number[]=[],fv=d.fields?.[props.field];
   d.triangles.forEach((t,fi)=>{for(const idx of t){const p=d.points[idx],u=d.displacements?.[idx]||[0,0,0];points.push(...p);displacements.push(...u);
     let c=fv?(fv[idx]==null?new THREE.Color('#b8c6cc'):color(((fv[idx] as number)-low)/Math.max(high-low,1e-20))):new THREE.Color(['#beced5','#a5c9bd','#d9bfa5','#afaecd'][(props.geometry?.components?.findIndex((b:any)=>b.face_ids.includes(d.surface_ids?.[fi]))||0)%4]||'#beced5');if(!slice&&props.selected.includes(d.surface_ids?.[fi]??-1))c=new THREE.Color('#ecad55');colors.push(c.r,c.g,c.b);}});
   const g=new THREE.BufferGeometry();g.setAttribute('position',new THREE.Float32BufferAttribute(points,3));g.setAttribute('color',new THREE.Float32BufferAttribute(colors,3));g.computeVertexNormals();
   const material=fv?new THREE.MeshBasicMaterial({vertexColors:true,side:THREE.DoubleSide}):new THREE.MeshStandardMaterial({vertexColors:true,metalness:.18,roughness:.64,side:THREE.DoubleSide});
   if(!slice&&r.clip)material.clippingPlanes=[r.clip];const mesh=new THREE.Mesh(g,material);r.group.add(mesh);if(d.displacements){r.deformTargets.push({geometry:g,rest:new Float32Array(points),displacement:new Float32Array(displacements)});}
   if(props.meshVisible){const edges=new THREE.Mesh(g,new THREE.MeshBasicMaterial({wireframe:true,color:'#425c69',transparent:true,opacity:.19,clippingPlanes:r.clip&&!slice?[r.clip]:[]}));r.group.add(edges);}
   else if(!fv){const edges=new THREE.LineSegments(new THREE.EdgesGeometry(g,25),new THREE.LineBasicMaterial({color:'#44636e',transparent:true,opacity:.48}));r.group.add(edges);}
   return mesh;
  };
  r.mesh=makeMesh(data);r.sliceMesh=props.slice?makeMesh(props.slice,true):null;
  if(props.showOriginal&&data.displacements){const ghost=new THREE.LineSegments(new THREE.EdgesGeometry(r.mesh.geometry,24),new THREE.LineBasicMaterial({color:'#667e8a',transparent:true,opacity:.48,depthWrite:false,clippingPlanes:r.clip?[r.clip]:[]}));r.group.add(ghost);}

  const surfaces=props.geometry?.surfaces||[],named=props.geometry?.named_selections||{};
  const idsOf=(s:any)=>{const body=props.geometry?.components?.find((b:any)=>b.id===s?.body_id),scope=body?surfaces.filter((f:any)=>body.face_ids.includes(f.id)):surfaces,local=body?.named_selections||named;if(s?.kind==='direction'){const a='xyz'.indexOf(s.direction?.slice(-1)),sign=s.direction?.[0]==='+'?1:-1;const found=scope.filter((f:any)=>f.normal[a]*sign>.98),extreme=Math.max(...found.map((f:any)=>f.centroid_m[a]*sign));return found.filter((f:any)=>Math.abs(f.centroid_m[a]*sign-extreme)<1e-7).map((f:any)=>f.id);}return (s?.kind==='named'?local[s.name]||named[s.name]||[]:s?.face_ids||[]).filter((id:number)=>!body||body.face_ids.includes(id));};
  const centerOf=(s:any)=>{const ids=idsOf(s);const fs=surfaces.filter((f:any)=>ids.includes(f.id));if(!fs.length)return r.center.clone();return new THREE.Vector3(...fs.reduce((sum:number[],f:any)=>sum.map((v,a)=>v+f.centroid_m[a]/fs.length),[0,0,0]));};
  if(props.showMarkers&&props.caseConfig){
   for(const hub of props.caseConfig.gear_contact?.hubs||[]){
    const origin=new THREE.Vector3(...hub.center_m),axis=new THREE.ArrowHelper(new THREE.Vector3(0,0,1),origin,r.span*.13,hub.rotation_locked?0x2e829b:0xd07938,r.span*.02,r.span*.01);r.markers.add(axis);
    if(hub.torque_Nm){const arc=new THREE.EllipseCurve(0,0,r.span*.05,r.span*.05,0,Math.PI*1.6,hub.torque_Nm<0,0),pts=arc.getPoints(40).map(p=>new THREE.Vector3(p.x+origin.x,p.y+origin.y,origin.z+r.span*.11));r.markers.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts),new THREE.LineBasicMaterial({color:0xd07938,depthTest:false})));const dir=pts.at(-1)!.clone().sub(pts.at(-2)!).normalize();r.markers.add(new THREE.ArrowHelper(dir,pts.at(-1)!,r.span*.025,0xd07938,r.span*.018,r.span*.008));}
   }
   for(const load of props.caseConfig.loads||[]){const origin=centerOf(load.selection||{kind:'named',name:'全部表面',body_id:load.body_id});let dir=new THREE.Vector3(...load.vector);if(load.kind==='pressure'){const ids=idsOf(load.selection);const face=surfaces.find((s:any)=>ids.includes(s.id));dir=new THREE.Vector3(...(face?.normal||[0,0,1])).multiplyScalar(-Math.sign(load.pressure_Pa));}if(dir.length()>0){dir.normalize();const len=r.span*.23;const arrow=new THREE.ArrowHelper(dir,origin.clone().addScaledVector(dir,-len),len,0xd07938,r.span*.045,r.span*.022);r.markers.add(arrow);}}
   for(const support of props.caseConfig.supports||[]){const ids=idsOf(support.selection);for(const s of surfaces.filter((f:any)=>ids.includes(f.id))){const marker=new THREE.Mesh(new THREE.OctahedronGeometry(r.span*.018),new THREE.MeshBasicMaterial({color:'#2e829b',depthTest:false}));marker.position.set(...s.centroid_m as [number,number,number]);r.markers.add(marker);}}
  }
  if(props.peak){const marker=new THREE.Mesh(new THREE.SphereGeometry(r.span*.009,20,14),new THREE.MeshBasicMaterial({color:'#d03639',depthTest:false}));marker.position.set(...props.peak as [number,number,number]);r.markers.add(marker);}
 },[props.data,props.field,props.selected,props.showOriginal,props.meshVisible,props.geometry,props.caseConfig,props.showMarkers,props.slice,props.clipEnabled,props.sliceAxis,props.slicePosition,props.peak,props.range]);
 return <div className="viewport-wrap"><div className="canvas" ref={host}/>{error&&<div className="empty">{error}</div>}
  <div className="probe">{probe?<><b>{probe.id?'面 '+probe.id:'剖面探针'}</b><span>{props.deformation>0?'显示坐标：':''}{probe.point.map((x:number)=>number(x*1000)).join(' / ')} mm</span>{probe.value!=null&&<strong>{number(probe.value*(fields[props.field]?.factor||1))} {fields[props.field]?.unit}</strong>}</>:<span>拖动旋转 · 滚轮缩放 · 右键平移 · {props.onProbePick?'Shift + 点击取节点':'点击选面'}</span>}</div>
 </div>;
});
export default Viewport;
