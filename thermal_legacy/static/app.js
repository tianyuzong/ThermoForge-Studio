import * as THREE from 'three';
import {OrbitControls} from '/vendor/OrbitControls.js';
import {installWorkflows} from '/workflow-ui.js';
import {installThermalAnimation} from '/thermal-animation-ui.js';
const $=id=>document.getElementById(id),$$=s=>Array.from(document.querySelectorAll(s));
const clone=x=>JSON.parse(JSON.stringify(x));
// The workflow host owns sequencing; each physics configuration still comes from Agent.
installWorkflows({getContext:()=>({model_id:model?.id,config:clone(cfg||{})}),openJob:(id)=>openJob(id),refreshLibrary:()=>refreshLibrary()});
const updateThermalAnimation=installThermalAnimation({api,post:(...args)=>post(...args),stopPlayback:()=>stopPlay()});
let boot,model,display,cfg,agentConfig=null,agentRunning=false,tab='model',activeRegion=-1,activeHeat=0,activeCooling=0,action='orbit',view='setup',dirty=false,jobId=null,busyId=null,result=null,temperatures=null,frame=0,slice=null,playing=false,playTimer=null,toastTimer=null,loadGeneration=0,recoveredTimer=null,uniformResult=false;
let agentConversation=null,agentRequestGeneration=0;
let points,faces,centers,normals,areas,neighbors,solid,geometry,creases,boxGroup=new THREE.Group(),sliceMesh,axes,partSpan=1;
let surfaceComponent='all',surfaceVertexComponents=null,surfaceRange={low:25,high:25.5};
let cpuMode=false,cpuResult=null,cpuGroup=new THREE.Group(),cpuSurfaceMesh=null,cpuBoardMesh=null,cpuCoolerMesh=null,cpuFanMesh=null;
const materialsColors=['#bf8056','#57a6c2','#8b97a9','#906fbc','#69a791','#cc9866'];
const scene=new THREE.Scene(),camera=new THREE.PerspectiveCamera(34,1,.00001,10000);camera.up.set(0,0,1);
const renderer=new THREE.WebGLRenderer({antialias:true,alpha:true});renderer.setPixelRatio(Math.min(devicePixelRatio,2));renderer.localClippingEnabled=true;
$('viewport').append(renderer.domElement);
const controls=new OrbitControls(camera,renderer.domElement);controls.enableDamping=false;controls.addEventListener('change',()=>render());
scene.add(new THREE.HemisphereLight(0xf1f6ff,0xa6b1c0,2.4));const light=new THREE.DirectionalLight(0xffffff,3);light.position.set(3,-4,6);scene.add(light);scene.add(boxGroup);const heatMarkers=new THREE.Group();scene.add(heatMarkers);scene.add(cpuGroup);cpuGroup.visible=false;
const setupMaterial=new THREE.MeshStandardMaterial({vertexColors:true,side:THREE.DoubleSide,metalness:.08,roughness:.7});
const resultMaterial=new THREE.MeshBasicMaterial({vertexColors:true,side:THREE.DoubleSide,toneMapped:false});
const raycaster=new THREE.Raycaster(),pointer=new THREE.Vector2();
const ramp=['#3158bf','#21a5c3','#f4d06d','#ec6b31','#bb283b'].map(c=>new THREE.Color(c));
function render(){renderer.render(scene,camera);}
function resize(){const r=$('viewport').getBoundingClientRect();if(!r.width||!r.height)return;renderer.setSize(r.width,r.height);camera.aspect=r.width/r.height;camera.updateProjectionMatrix();render();}
new ResizeObserver(resize).observe($('viewport'));
function toast(message,error=false){$('toast').textContent=message;$('toast').classList.toggle('error',error);$('toast').hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('toast').hidden=true,error?10000:4500);}
function clearCpuScene(){while(cpuGroup.children.length){const child=cpuGroup.children.pop();child.geometry?.dispose();if(child.material?.dispose)child.material.dispose();}cpuSurfaceMesh=cpuBoardMesh=cpuCoolerMesh=cpuFanMesh=null;}
function createCpuScene(){clearCpuScene();const boardMat=new THREE.MeshStandardMaterial({color:0x1f684d,roughness:.8,metalness:.05});cpuBoardMesh=new THREE.Mesh(new THREE.BoxGeometry(.24,.18,.008),boardMat);cpuBoardMesh.position.z=0;cpuGroup.add(cpuBoardMesh);const socket=new THREE.Mesh(new THREE.BoxGeometry(.075,.075,.012),new THREE.MeshStandardMaterial({color:0x222b36,roughness:.5}));socket.position.z=.01;cpuGroup.add(socket);const cpuBase=new THREE.Mesh(new THREE.BoxGeometry(.062,.062,.012),new THREE.MeshStandardMaterial({color:0x9da7b2,roughness:.45,metalness:.35}));cpuBase.position.z=.02;cpuGroup.add(cpuBase);const nx=14,ny=14,w=.058,h=.058,z=.027,positions=[],indices=[];for(let j=0;j<=ny;j++)for(let i=0;i<=nx;i++)positions.push(-w/2+w*i/nx,-h/2+h*j/ny,z);for(let j=0;j<ny;j++)for(let i=0;i<nx;i++){const a=j*(nx+1)+i,b=a+1,c=a+nx+1,d=c+1;indices.push(a,c,b,b,c,d);}const g=new THREE.BufferGeometry();g.setAttribute('position',new THREE.Float32BufferAttribute(positions,3));g.setIndex(indices);g.setAttribute('color',new THREE.Float32BufferAttribute(new Float32Array((nx+1)*(ny+1)*3),3));cpuSurfaceMesh=new THREE.Mesh(g,new THREE.MeshBasicMaterial({vertexColors:true,side:THREE.DoubleSide,toneMapped:false}));cpuSurfaceMesh.userData.grid={nx,ny,w,h};cpuGroup.add(cpuSurfaceMesh);cpuCoolerMesh=new THREE.Mesh(new THREE.BoxGeometry(.072,.072,.014),new THREE.MeshStandardMaterial({color:0x8c96a1,roughness:.32,metalness:.6,transparent:true,opacity:.92}));cpuCoolerMesh.position.z=.038;cpuGroup.add(cpuCoolerMesh);cpuFanMesh=new THREE.Mesh(new THREE.BoxGeometry(.082,.082,.024),new THREE.MeshStandardMaterial({color:0x364b66,roughness:.55,transparent:true,opacity:.9}));cpuFanMesh.position.z=.058;cpuGroup.add(cpuFanMesh);const center=new THREE.Vector3(0,0,.025);controls.target.copy(center);camera.position.copy(center).add(new THREE.Vector3(.26,-.34,.26));camera.near=.001;camera.far=10;camera.updateProjectionMatrix();controls.minDistance=.12;controls.maxDistance=2;controls.update();render();}
function setCpuMode(enabled){cpuMode=enabled;cpuGroup.visible=enabled;if(solid)solid.visible=!enabled;if(creases)creases.visible=!enabled;if(enabled){if(!cpuGroup.children.length)createCpuScene();$('view-title').textContent='CPU 热仿真 · 主板与散热器';$('view-subtitle').textContent='旋转查看 · CPU 表面颜色表示温度';$('result-panel').hidden=true;render();}else{if(solid)solid.visible=true;if(creases)creases.visible=true;$('view-title').textContent=view==='temperature'?'仿真结果 · '+(cfg?.name||''):model?.name||'几何模型';$('view-subtitle').textContent=view==='temperature'?'旋转查看 · 指向模型读取当前结果场':'拖动旋转 · 滚轮缩放 · 右键平移';render();}}
function updateCpuFrame(index){if(!cpuResult||!cpuSurfaceMesh)return;const cpu=cpuResult.cpu_C[index],ambient=Number($('cpu-ambient').value),low=Math.min(ambient,...cpuResult.cpu_C,...cpuResult.board_C),high=Math.max(...cpuResult.cpu_C);const span=high-low||1;const colors=cpuSurfaceMesh.geometry.attributes.color.array,{nx,ny}=cpuSurfaceMesh.userData.grid;for(let j=0;j<=ny;j++)for(let i=0;i<=nx;i++){const dx=i/nx-.5,dy=j/ny-.5,r=Math.min(1,Math.sqrt(dx*dx+dy*dy)*2);const local=cpu+(1-r)*Math.max(0,(cpu-ambient)*.16+1);heatColor((local-low)/span).toArray(colors,(j*(nx+1)+i)*3);}cpuSurfaceMesh.geometry.attributes.color.needsUpdate=true;const coolerTemp=cpuResult.cooler_C[index],boardTemp=cpuResult.board_C[index];cpuCoolerMesh.material.color.set(heatColor((coolerTemp-low)/span));cpuFanMesh.material.color.set($('cpu-cooler-type').value==='water'?0x2c9ab7:0x364b66);$('cpu-time-label').textContent=cpuResult.times_s[index].toFixed(0)+' s';$('cpu-timeline').value=index;$('cpu-peak').textContent=cpuResult.maximum_cpu_C.toFixed(2);$('cpu-final').textContent=cpu.toFixed(2);$('cpu-cooler-temp').textContent=coolerTemp.toFixed(2);$('cpu-board-temp').textContent=boardTemp.toFixed(2);render();}
const agentStages=['理解仿真目标','识别模型与热源位置','生成参数配置','校验边界条件','提交有限元求解','展示温度场结果'];
function agentTraceReset(){const list=$('agent-trace-list');list.replaceChildren();agentStages.forEach(label=>{const li=document.createElement('li'),body=document.createElement('div'),title=document.createElement('strong'),detail=document.createElement('small');title.textContent=label;body.append(title,detail);li.append(body);list.append(li);});$('agent-trace-summary').textContent='准备开始';$('agent-trace-detail').textContent='';$('agent-trace').hidden=false;}
function agentTraceSet(index,state,detail=''){const items=$('agent-trace-list').children;for(let i=0;i<items.length;i++){items[i].classList.toggle('active',i===index&&state==='active');items[i].classList.toggle('done',i===index&&state==='done');items[i].classList.toggle('error',i===index&&state==='error');if(i===index)items[i].querySelector('small').textContent=detail;}const label=agentStages[index]||'';$('agent-trace-summary').textContent=(state==='done'?'已完成 · ':state==='error'?'需要处理 · ':state==='active'?'正在进行 · ':'')+label;$('agent-trace-detail').textContent=detail;}
async function api(path,options={}){const response=await fetch('/api'+path,options);if(!response.ok){let body;try{body=await response.json();}catch{body={detail:response.statusText};}let detail=body.detail;if(Array.isArray(detail))detail=detail.map(x=>x.loc.slice(1).join('.')+'：'+x.msg).join('\n');throw new Error(typeof detail==='string'?detail:JSON.stringify(detail));}return response.json();}
const post=(path,data)=>api(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
async function binary(path,Type){const r=await fetch(path);if(!r.ok)throw new Error('结果数据读取失败');return new Type(await r.arrayBuffer());}
function protect(fn){return async(...args)=>{try{await fn(...args);}catch(error){toast(error.message,true);}};}
const number=id=>Number($(id).value);
function material(){return activeRegion<0?cfg.base_material:cfg.regions[activeRegion].material;}
function group(){return tab==='heat'?cfg.heat_sources[activeHeat]:tab==='cooling'?cfg.cooling[activeCooling]:null;}
function changed(){dirty=true;$('dirty-note').hidden=!result||view!=='temperature';drawHeatMarkers();recolor();}
function setTab(value){tab=value;$$('[data-tab]').forEach(b=>b.classList.toggle('active',b.dataset.tab===value));$$('[data-panel]').forEach(p=>p.hidden=p.dataset.panel!==value);if(value==='material'||value==='heat'||value==='cooling')setView('setup');$('brushbar').hidden=!['heat','cooling'].includes(value)||view!=='setup';setAction('orbit');refreshEntities();drawBoxes();drawHeatMarkers();recolor();}
function setView(value){if(value==='setup')resetDeformation();if(value==='temperature'&&!result)return;view=value;$('dirty-note').hidden=!dirty||!result||view!=='temperature';$$('[data-view]').forEach(b=>b.classList.toggle('selected',b.dataset.view===value));$('result-panel').hidden=value!=='temperature';$('brushbar').hidden=value!=='setup'||!['heat','cooling'].includes(tab);boxGroup.visible=value==='setup'&&tab==='material';if(solid)solid.material=value==='temperature'?resultMaterial:setupMaterial;setAction('orbit');applyClip();drawHeatMarkers();recolor();$('view-title').textContent=value==='temperature'?'仿真结果 · '+(cfg?.name||''):model?.name||'几何模型';$('view-subtitle').textContent=value==='temperature'?'旋转查看 · 指向模型读取当前结果场':'拖动旋转 · 滚轮缩放 · 右键平移';}
function setAction(value){action=value;$$('[data-action]').forEach(b=>b.classList.toggle('selected',b.dataset.action===value));controls.enabled=value==='orbit';renderer.domElement.style.cursor=value==='orbit'?'grab':'crosshair';}
function buildAdjacency(){centers=new Float32Array(faces.length);normals=new Float32Array(faces.length);areas=new Float64Array(faces.length/3);neighbors=Array.from({length:areas.length},()=>[]);const edges=new Map();for(let f=0;f<areas.length;f++){const p=[0,1,2].map(j=>new THREE.Vector3().fromArray(points,faces[f*3+j]*3));const cross=p[1].clone().sub(p[0]).cross(p[2].clone().sub(p[0]));areas[f]=cross.length()/2;cross.normalize().toArray(normals,f*3);p[0].clone().add(p[1]).add(p[2]).multiplyScalar(1/3).toArray(centers,f*3);for(let j=0;j<3;j++){const a=faces[f*3+j],b=faces[f*3+(j+1)%3],key=Math.min(a,b)+','+Math.max(a,b);const list=edges.get(key)||[];for(const neighbor of list){neighbors[f].push(neighbor);neighbors[neighbor].push(f);}list.push(f);edges.set(key,list);}}}
function clearObject(obj){if(!obj)return;scene.remove(obj);obj.geometry?.dispose();if(obj!==solid)obj.material?.dispose();}
function fit(front=true){if(!model)return;const bounds=model.bounds_m,center=new THREE.Vector3(...bounds[0]).add(new THREE.Vector3(...bounds[1])).multiplyScalar(.5);partSpan=Math.max(...model.dimensions_m);const distance=partSpan*1.9;controls.target.copy(center);if(front)camera.position.copy(center).add(new THREE.Vector3(.24,-1,.14).normalize().multiplyScalar(distance));else camera.position.sub(controls.target).normalize().multiplyScalar(distance).add(center);camera.near=partSpan/1000;camera.far=partSpan*100;camera.updateProjectionMatrix();controls.minDistance=partSpan*.4;controls.maxDistance=partSpan*15;controls.update();render();}
function loadMesh(data){display=data;points=new Float32Array(data.points);faces=new Uint32Array(data.faces);clearObject(solid);clearObject(creases);clearObject(sliceMesh);sliceMesh=null;slice=null;geometry=new THREE.BufferGeometry();geometry.setAttribute('position',new THREE.BufferAttribute(new Float32Array(points),3));geometry.setIndex(new THREE.BufferAttribute(faces,1));geometry.setAttribute('color',new THREE.BufferAttribute(new Float32Array(points.length),3));geometry.computeVertexNormals();solid=new THREE.Mesh(geometry,setupMaterial);scene.add(solid);creases=new THREE.LineSegments(new THREE.EdgesGeometry(geometry,38),new THREE.LineBasicMaterial({color:0x455a76,transparent:true,opacity:.12,depthWrite:false}));scene.add(creases);buildAdjacency();buildSurfaceComponents();fit();refreshInfo();drawBoxes();drawHeatMarkers();recolor();}
function buildSurfaceComponents(){surfaceVertexComponents=null;surfaceComponent='all';const records=(model?.components||[]).filter(c=>Array.isArray(c.bounds_m)&&c.bounds_m.length===2);const select=$('surface-component');if(select)select.replaceChildren(new Option('整个模型','all'));if(!records.length||!centers?.length)return;const boxes=records.map(c=>({id:Number(c.component_id),name:c.name||('组件 '+(Number(c.component_id)+1)),lo:new THREE.Vector3(...c.bounds_m[0]),hi:new THREE.Vector3(...c.bounds_m[1]),center:new THREE.Vector3(...c.bounds_m[0]).add(new THREE.Vector3(...c.bounds_m[1])).multiplyScalar(.5)}));const faceIds=new Int32Array(areas.length);for(let f=0;f<areas.length;f++){const p=new THREE.Vector3().fromArray(centers,f*3);let best=boxes.find(b=>p.x>=b.lo.x-1e-7&&p.x<=b.hi.x+1e-7&&p.y>=b.lo.y-1e-7&&p.y<=b.hi.y+1e-7&&p.z>=b.lo.z-1e-7&&p.z<=b.hi.z+1e-7);if(!best)best=boxes.reduce((a,b)=>p.distanceToSquared(a.center)<p.distanceToSquared(b.center)?a:b);faceIds[f]=best.id;}const votes=Array.from({length:points.length/3},()=>new Map());for(let f=0;f<faceIds.length;f++)for(let j=0;j<3;j++){const v=faces[f*3+j],map=votes[v];map.set(faceIds[f],(map.get(faceIds[f])||0)+1);}surfaceVertexComponents=new Int32Array(points.length/3);for(let v=0;v<votes.length;v++){let best=-1,count=-1;for(const [id,n] of votes[v])if(n>count){best=id;count=n;}surfaceVertexComponents[v]=best;}if(select)for(const b of boxes)select.append(new Option(b.name,String(b.id)));}
function newConfig(m){return {model_id:m.id,name:m.name.replace(/\.[^.]+$/,'')+' · 热仿真',base_material:clone(boot.materials[0]),regions:[],component_materials:[],heat_sources:[],cooling:[],analysis_mode:'transient',initial_C:25,ambient_C:25,radiation_ambient_C:25,default_h:10,heat_convection:false,radiation_enabled:false,emissivity:.8,air_gap_enabled:true,air_gap_k_W_mK:.026,air_gap_max_m:.05,contact_resistance_m2K_W:0,duration_s:3600,dt_s:15,save_s:15,mesh_size_m:Math.max(...m.dimensions_m)/30};}
async function loadModel(id,configuration=null){const generation=++loadGeneration;resetAgentConversation();stopPlay();const loadedModel=await api('/models/'+id);if(loadedModel.state!=='ready')throw new Error('模型尚未准备完成');const mesh=await api('/models/'+id+'/display');if(generation!==loadGeneration)return false;model=loadedModel;cfg=configuration?Object.assign(newConfig(model),clone(configuration)):newConfig(model);cfg.base_material=Object.assign(clone(boot.materials[0]),cfg.base_material);cfg.regions=(cfg.regions||[]).map(r=>({...r,material:Object.assign(clone(boot.materials[0]),r.material)}));cfg.component_materials=cfg.component_materials||[];cfg.heat_sources=(cfg.heat_sources||[]).map(h=>({...h,power_profile:h.power_profile||[],thermostat:h.thermostat||null}));cfg.cooling=(cfg.cooling||[]).map(c=>({...c,radiation:!!c.radiation,emissivity:c.emissivity??.85}));result=null;temperatures=null;jobId=null;dirty=false;activeRegion=-1;activeHeat=0;activeCooling=0;view='setup';$('temperature-view').disabled=true;$('dirty-note').hidden=true;$('show-slice').checked=false;setupMaterial.clippingPlanes=[];resultMaterial.clippingPlanes=[];resetAgentConversation();loadMesh(mesh);$('model-select').value=id;syncFields();setView('setup');return true;}
function refreshInfo(){const dl=$('model-info');dl.replaceChildren();const components=Array.isArray(model.components)?model.components:[],openComponents=components.filter(c=>c.closed===false);const componentText=components.length?`${components.length} 个（${openComponents.length} 个开口）`:'未提供组件信息';for(const [k,v] of [['尺寸 · cm',model.dimensions_m.map(x=>(x*100).toFixed(2)).join(' × ')],['表面三角形',model.triangles.toLocaleString()],['内部空腔面',model.cavity_triangles.toLocaleString()],['组件',componentText],['格式',model.kind.toUpperCase()]]){const dt=document.createElement('dt'),dd=document.createElement('dd');dt.textContent=k;dd.textContent=v;dl.append(dt,dd);}const warnings=[];if(model.warning)warnings.push(model.warning);if(openComponents.length)warnings.push(`检测到 ${openComponents.length} 个开口组件，无法可靠生成体积网格；请修复 STL 后再运行。`);$('model-warning').textContent=warnings.join(' ');$('model-warning').hidden=!warnings.length;$('model-count').textContent=model.vertices.toLocaleString()+' 个表面顶点';$('brush-radius').value=+(Math.max(...model.dimensions_m)*5).toFixed(2);}
function setOptions(select,items,value){select.replaceChildren();for(const item of items){const option=document.createElement('option');option.value=item.id;option.textContent=item.name;select.append(option);}if(value!==undefined)select.value=value;}
async function refreshLibrary(){boot=await api('/bootstrap');setOptions($('model-select'),boot.models.filter(m=>m.state==='ready'),model?.id);setOptions($('project-select'),[{id:'',name:'选择保存的算例…'},...boot.projects]);setOptions($('material-preset'),boot.materials.map((m,i)=>({id:String(i),name:materialPresetName(m)})));renderHistory();}
const historyDropdown=$('run-history');
function positionRunHistory(){
 if(!historyDropdown.open)return;
 const anchor=historyDropdown.getBoundingClientRect(),panel=historyDropdown.querySelector('.run-history-content');
 const width=Math.min(380,innerWidth-32),top=anchor.bottom+8;
 panel.style.left=Math.max(16,Math.min(anchor.left,innerWidth-width-16))+'px';
 panel.style.top=top+'px';
 $('history').style.maxHeight=Math.max(80,Math.min(420,innerHeight*.55,innerHeight-top-60))+'px';
}
historyDropdown.addEventListener('toggle',positionRunHistory);
window.addEventListener('resize',positionRunHistory);
window.addEventListener('scroll',positionRunHistory,{passive:true});
document.addEventListener('click',event=>{if(!historyDropdown.contains(event.target))historyDropdown.open=false;});
document.addEventListener('keydown',event=>{if(event.key==='Escape'&&historyDropdown.open){historyDropdown.open=false;historyDropdown.querySelector('summary').focus();}});
function renderHistory(){
 const list=$('history');list.replaceChildren();
 $('history-count').textContent=boot.jobs.length+' 条';
 for(const job of boot.jobs){
  const button=document.createElement('button'),label=document.createElement('span'),small=document.createElement('small');
  button.dataset.jobId=job.id;label.textContent=job.name;
  small.textContent=({completed:'已完成 · 点击查看',running:'正在计算',pending:'等待计算',failed:'运行失败',cancelled:'已取消'})[job.status.phase]||job.status.phase;
  button.append(label,small);
  button.onclick=protect(async()=>{if(job.status.phase==='completed'){await openJob(job.id);historyDropdown.open=false;}else toast(job.status.detail,job.status.phase==='failed');});
  list.append(button);
 }
 if(!boot.jobs.length)list.textContent='尚无计算记录';
}
function showRunProgress(job,status){$('run-progress').hidden=false;$('run-progress').dataset.recovered='true';$('progress-title').textContent=job.name;$('progress').value=status.progress||0;$('progress-percent').textContent=(status.progress||0)+'%';$('progress-detail').textContent=status.detail||'等待计算';$('run').disabled=true;}
async function recoverRunningJob(job){busyId=job.id;showRunProgress(job,job.status);const poll=async()=>{const status=await api('/jobs/'+job.id);if(busyId!==job.id)return;showRunProgress(job,status);if(status.phase==='completed'||status.phase==='failed'||status.phase==='cancelled'){clearInterval(recoveredTimer);recoveredTimer=null;busyId=null;$('run').disabled=false;$('run-progress').hidden=true;delete $('run-progress').dataset.recovered;await refreshLibrary();if(status.phase==='completed'){toast('后台仿真已完成');if(model?.id===job.model_id)await openJob(job.id);}else if(status.phase==='cancelled')toast('后台仿真已取消');else toast(status.detail,true);}};await poll();if(busyId===job.id)recoveredTimer=setInterval(()=>poll().catch(error=>toast(error.message,true)),1000);}
function syncFields(){if(!cfg)return;$('project-name').value=cfg.name;for(const [id,key] of [['initial','initial_C'],['ambient','ambient_C'],['default-h','default_h'],['duration','duration_s'],['dt','dt_s'],['save-interval','save_s'],['analysis-mode','analysis_mode'],['radiation-ambient','radiation_ambient_C'],['emissivity','emissivity'],['contact-resistance','contact_resistance_m2K_W']])$(id).value=cfg[key];$('mesh-size').value=+(cfg.mesh_size_m*1000).toFixed(5);$('heat-convection').checked=cfg.heat_convection;$('radiation-enabled').checked=cfg.radiation_enabled;$('air-gap-enabled').checked=cfg.air_gap_enabled!==false;$('air-gap-max').value=+(cfg.air_gap_max_m*1000).toFixed(5);$('air-gap-k').value=cfg.air_gap_k_W_mK??.026;refreshEntities();}
function entityButton(name,subtitle,color,selected,onClick){const b=document.createElement('button');b.className='entity'+(selected?' selected':'');const s=document.createElement('span');s.className='swatch';s.style.background=color;const t=document.createElement('span');t.textContent=name;const small=document.createElement('small');small.textContent=subtitle;b.append(s,t,small);b.onclick=onClick;return b;}
const materialCategories={metal:'金属',polymer:'高分子',glass:'玻璃',ceramic:'陶瓷',elastomer:'弹性体',composite:'复合材料',other:'其他'};
function materialPresetName(m){return (materialCategories[m.category]||'其他')+' · '+m.name;}
function renderMaterialEngineering(m){
 const target=$('material-engineering');if(!target)return;target.replaceChildren();
 const title=document.createElement('strong');title.textContent=(materialCategories[m.category]||'未分类')+' · 力学与温区';target.append(title);
 const data=[['弹性模量 (GPa)',m.young_modulus_Pa==null?null:m.young_modulus_Pa/1e9],['泊松比',m.poisson_ratio],['屈服强度 (MPa)',m.yield_strength_Pa==null?null:m.yield_strength_Pa/1e6],['抗拉强度 (MPa)',m.tensile_strength_Pa==null?null:m.tensile_strength_Pa/1e6],['抗压强度 (MPa)',m.compressive_strength_Pa==null?null:m.compressive_strength_Pa/1e6],['物性参考温度 (°C)',m.reference_temperature_C],['物性有效最低温度 (°C)',m.valid_min_C],['物性有效最高温度 (°C)',m.valid_max_C],['材料最低使用温度 (°C)',m.service_min_C],['材料最高使用温度 (°C)',m.service_max_C],['玻璃化转变 Tg (°C)',m.glass_transition_C]];
 const list=document.createElement('dl');list.className='info-list';for(const [label,value] of data){const dt=document.createElement('dt'),dd=document.createElement('dd');dt.textContent=label;dd.textContent=value==null?'未提供':Number(value).toPrecision(5).replace(/\.?0+$/,'');list.append(dt,dd);}target.append(list);
 const criterion=document.createElement('p');criterion.textContent='强度判据：'+({von_mises:'von Mises / 屈服',principal:'主拉/主压应力',none:'不评估'}[m.strength_criterion||'von_mises']);target.append(criterion);
 if(m.property_notes){const note=document.createElement('p');note.className='hint';note.textContent=m.property_notes;target.append(note);}
 if(m.data_source){const note=document.createElement('p');note.className='hint';note.textContent='数据来源：'+m.data_source;target.append(note);}
}
function syncPhaseFields(){const p=material().phase_change;$('phase-enabled').value=p?'on':'off';$('phase-fields').hidden=!p;if(p){$('phase-melting').value=p.melting_C;$('phase-mushy').value=p.mushy_C;$('phase-latent').value=p.latent_J_kg;}}
function refreshEntities(){if(!cfg)return;const regions=$('region-list');regions.replaceChildren(entityButton('基础实体',cfg.base_material.name,materialsColors[0],activeRegion<0,()=>{activeRegion=-1;refreshEntities();drawBoxes();}));cfg.regions.forEach((r,i)=>regions.append(entityButton(r.name,r.material.name,materialsColors[(i+1)%materialsColors.length],activeRegion===i,()=>{activeRegion=i;refreshEntities();drawBoxes();})));
 const componentList=$('component-list');componentList.replaceChildren();(model.components||[]).forEach(component=>{const row=document.createElement('label');row.className='field';const title=document.createElement('span');title.className='component-title';const id=Number(component.component_id);const name=component.name||('组件 '+(id+1));const status=component.closed===false?'开口':component.closed===true?'封闭':'';const triangleText=Number.isFinite(Number(component.triangles))?` · ${Number(component.triangles).toLocaleString()} 面`:'';title.textContent=name+triangleText+(status?' · '+status:'');if(Array.isArray(component.bounds_m)&&component.bounds_m.length===2){const size=component.bounds_m[1].map((v,i)=>(Number(v)-Number(component.bounds_m[0][i]))*1000);title.title='尺寸 '+size.map(v=>v.toFixed(2)).join(' × ')+' mm';}const select=document.createElement('select');const inherited=document.createElement('option');inherited.value='';inherited.textContent='跟随基础材料';select.append(inherited);boot.materials.forEach((preset,index)=>{const option=document.createElement('option');option.value=String(index);option.textContent=materialPresetName(preset);select.append(option);});const assigned=(cfg.component_materials||[]).find(item=>item.component_id===component.component_id);if(assigned){const index=boot.materials.findIndex(item=>item.name===assigned.material.name);if(index>=0)select.value=String(index);}select.onchange=()=>{cfg.component_materials=(cfg.component_materials||[]).filter(item=>item.component_id!==component.component_id);if(select.value!=='')cfg.component_materials.push({component_id:component.component_id,material:clone(boot.materials[Number(select.value)])});changed();};row.append(title,select);componentList.append(row);});
 $('region-box').hidden=activeRegion<0;if(activeRegion>=0){const r=cfg.regions[activeRegion];$('region-name').value=r.name;for(let i=0;i<3;i++){const axis='xyz'[i];$(axis+'min').value=+(r.min_m[i]*100).toFixed(5);$(axis+'max').value=+(r.max_m[i]*100).toFixed(5);}}
 const m=material();renderMaterialEngineering(m);$('material-name').value=m.name;$('mat-k').value=m.k;$('mat-rho').value=m.rho;$('mat-cp').value=m.cp;$('mat-cte').value=m.thermal_expansion_CTE_per_K??0;$('material-preset').value=String(boot.materials.findIndex(p=>p.name===m.name));syncPhaseFields();
 for(const kind of ['heat','cooling']){const isHeat=kind==='heat',items=isHeat?cfg.heat_sources:cfg.cooling,active=isHeat?activeHeat:activeCooling;const list=$(kind+'-list');list.replaceChildren();items.forEach((g,i)=>list.append(entityButton(g.name,isHeat?((g.source_type==='point'?'点':'面')+' · '+g.power_W+' W'):'h = '+g.h,isHeat?'#eb7b32':'#1b9dab',active===i,()=>{if(isHeat)activeHeat=i;else activeCooling=i;refreshEntities();drawHeatMarkers();recolor();})));$(kind+'-empty').hidden=!!items.length;$(kind+'-fields').hidden=!items.length;const g=items[active];if(!g)continue;$(kind+'-name').value=g.name;if(isHeat){g.source_type=g.source_type||'surface';g.placement=g.placement||'surface';g.faces=g.faces||[];$('heat-source-type').value=g.source_type;$('heat-placement').value=g.placement;$('heat-power').value=g.power_W;$('heat-start').value=g.start_s;$('heat-end').value=g.end_s;$('heat-radius').value=(g.radius_m||.001)*1000;const p=heatPosition(g).toArray();for(let i=0;i<3;i++)$('heat-position-'+'xyz'[i]).value=(g.position_m?.[i]??p[i])*1000;$('heat-profile').value=(g.power_profile||[]).map(p=>p.time_s+':'+p.power_W).join(', ');$('heat-thermostat').checked=!!g.thermostat;$('thermostat-fields').hidden=!g.thermostat;if(g.thermostat){$('thermostat-target').value=g.thermostat.target_C;$('thermostat-hysteresis').value=g.thermostat.hysteresis_C;$('thermostat-max').value=g.thermostat.max_power_W;}}else{$('cooling-h').value=g.h;$('cooling-ambient').value=g.ambient_C;$('cooling-radiation').checked=!!g.radiation;$('cooling-emissivity').value=g.emissivity??.85;}selectionText(kind,g);}}
function selectionText(kind,g){if(kind==='heat'&&(g.source_type==='point'||g.placement==='embedded')){const p=heatPosition(g);$('heat-selection').textContent='点位置 · '+p.toArray().map(x=>(x*1000).toFixed(2)).join(' / ')+' mm · 半径 '+((g.radius_m||.001)*1000).toFixed(2)+' mm';return;}const selected=g.faces||[],area=selected.reduce((a,f)=>a+(areas?.[f]||0),0);$(kind+'-selection').textContent=selected.length.toLocaleString()+' 个三角面 · '+area.toPrecision(4)+' m²';}
function drawBoxes(){while(boxGroup.children.length){const c=boxGroup.children.pop();c.geometry?.dispose();c.material?.dispose();}if(!cfg)return;cfg.regions.forEach((r,i)=>{const b=new THREE.Box3(new THREE.Vector3(...r.min_m),new THREE.Vector3(...r.max_m));const h=new THREE.Box3Helper(b,new THREE.Color(materialsColors[(i+1)%materialsColors.length]));h.material.transparent=true;h.material.opacity=i===activeRegion?1:.35;boxGroup.add(h);if(i===activeRegion){const size=b.getSize(new THREE.Vector3()),center=b.getCenter(new THREE.Vector3());const fill=new THREE.Mesh(new THREE.BoxGeometry(size.x,size.y,size.z),new THREE.MeshBasicMaterial({color:materialsColors[(i+1)%materialsColors.length],transparent:true,opacity:.08,depthWrite:false}));fill.position.copy(center);boxGroup.add(fill);}});boxGroup.visible=tab==='material'&&view==='setup';render();}
function heatPosition(g){if(g.position_m?.length===3)return new THREE.Vector3(...g.position_m);if(g.faces?.length){const p=new THREE.Vector3();for(const f of g.faces)p.add(new THREE.Vector3().fromArray(centers,f*3));return p.multiplyScalar(1/g.faces.length);}return new THREE.Vector3(...model.bounds_m[0]).add(new THREE.Vector3(...model.bounds_m[1])).multiplyScalar(.5);}
function drawHeatMarkers(){while(heatMarkers.children.length){const marker=heatMarkers.children.pop();marker.geometry?.dispose();marker.material?.dispose();}if(!cfg||!model||tab!=='heat'||view!=='setup')return;const size=Math.max(partSpan*.025,1e-5);cfg.heat_sources.forEach((g,i)=>{const marker=new THREE.Mesh(new THREE.SphereGeometry(size,16,10),new THREE.MeshBasicMaterial({color:i===activeHeat?0xf05b2b:0xf4a261,depthTest:false}));marker.position.copy(heatPosition(g));marker.userData.heatIndex=i;marker.renderOrder=5;heatMarkers.add(marker);});render();}
function heatColor(t){const x=Math.max(0,Math.min(.999999,t))*(ramp.length-1),i=Math.floor(x);return ramp[i].clone().lerp(ramp[i+1],x-i);}
function updateSurfaceRange(){if(!result||!temperatures){surfaceRange={low:result?.minimum_C??25,high:result?.maximum_C??25.5};return;}const selected=surfaceComponent==='all'?null:Number(surfaceComponent);let low=Infinity,high=-Infinity;for(let f=0;f<result.frames;f++)for(let v=0;v<result.vertices;v++)if(selected===null||!surfaceVertexComponents||surfaceVertexComponents[v]===selected){const value=temperatures[f*result.vertices+v];if(value<low)low=value;if(value>high)high=value;}if(!Number.isFinite(low)){low=result.minimum_C;high=result.maximum_C;}if(high-low<1e-8){low-=.5;high+=.5;}surfaceRange={low,high};$('scale-low').textContent=low.toFixed(2)+fieldUnit();$('scale-high').textContent=high.toFixed(2)+fieldUnit();}
function surfaceStats(frameIndex){if(resultField!=='temperature')return engineeringSurfaceStats(frameIndex);const selected=surfaceComponent==='all'?null:Number(surfaceComponent);if(selected===null||!surfaceVertexComponents)return result.stats[frameIndex];let low=Infinity,high=-Infinity,sum=0,count=0;for(let v=0;v<result.vertices;v++)if(surfaceVertexComponents[v]===selected){const value=temperatures[frameIndex*result.vertices+v];low=Math.min(low,value);high=Math.max(high,value);sum+=value;count++;}return count?{minimum_C:low,maximum_C:high,average_C:sum/count}:result.stats[frameIndex];}
function recolor(){if(!geometry||!cfg)return;const colors=geometry.attributes.color.array;const legend=$('model-legend');legend.replaceChildren();
 if(view==='temperature'&&result){const span=surfaceRange.high-surfaceRange.low||1;const selected=surfaceComponent==='all'?null:Number(surfaceComponent);for(let i=0;i<points.length/3;i++){const visible=selected===null||!surfaceVertexComponents||surfaceVertexComponents[i]===selected;if(!visible||uniformResult)new THREE.Color('#aeb9c8').toArray(colors,i*3);else heatColor((temperatures[frame*result.vertices+i]-surfaceRange.low)/span).toArray(colors,i*3);}if(sliceMesh&&slice){const c=sliceMesh.geometry.attributes.color.array;for(let i=0;i<slice.vertices;i++){if(uniformResult)new THREE.Color('#aeb9c8').toArray(c,i*3);else heatColor((slice.values[frame*slice.vertices+i]-surfaceRange.low)/span).toArray(c,i*3);}sliceMesh.geometry.attributes.color.needsUpdate=true;}}
 else{for(let i=0;i<points.length/3;i++){let c=new THREE.Color(materialsColors[0]);if(tab==='material'){for(let j=0;j<cfg.regions.length;j++){const r=cfg.regions[j];if([0,1,2].every(k=>points[i*3+k]>=r.min_m[k]&&points[i*3+k]<=r.max_m[k]))c.set(materialsColors[(j+1)%materialsColors.length]);}}else if(tab==='heat'||tab==='cooling')c.set('#c7d1df');c.toArray(colors,i*3);}if(tab==='heat'||tab==='cooling'){const items=tab==='heat'?cfg.heat_sources:cfg.cooling,active=tab==='heat'?activeHeat:activeCooling;items.forEach((g,i)=>{const c=new THREE.Color(tab==='heat'?'#ef8034':'#16a2b1');if(i!==active)c.lerp(new THREE.Color('#c7d1df'),.55);for(const f of (g.faces||[]))for(let j=0;j<3;j++)c.toArray(colors,faces[f*3+j]*3);});}const items=tab==='material'?[cfg.base_material,...cfg.regions.map(r=>r.material)]:tab==='heat'?cfg.heat_sources:tab==='cooling'?cfg.cooling:[cfg.base_material];items.forEach((g,i)=>{const span=document.createElement('span');span.textContent=g.name;span.style.setProperty('--swatch',tab==='heat'?'#ef8034':tab==='cooling'?'#16a2b1':materialsColors[i%materialsColors.length]);legend.append(span);});}
 geometry.attributes.color.needsUpdate=true;render();}
function setRay(event){const r=renderer.domElement.getBoundingClientRect();pointer.set((event.clientX-r.left)/r.width*2-1,-(event.clientY-r.top)/r.height*2+1);raycaster.setFromCamera(pointer,camera);}
function hitAt(event){setRay(event);const objects=sliceMesh?.visible?[solid,sliceMesh]:[solid];return raycaster.intersectObjects(objects.filter(Boolean)).find(hit=>!sliceMesh?.visible||hit.object===sliceMesh||clipPlane.distanceToPoint(hit.point)>=-1e-7);}
let draggingHeat=null,dragPlane=new THREE.Plane();
function moveHeatMarker(event){if(!draggingHeat)return;setRay(event);const source=cfg.heat_sources[draggingHeat.index],surfaceHit=hitAt(event),point=new THREE.Vector3();if(source.source_type==='surface'&&source.placement!=='embedded'&&surfaceHit?.object===solid){point.copy(surfaceHit.point);source.faces=[surfaceHit.faceIndex];}else if(!raycaster.ray.intersectPlane(dragPlane,point))return;const lo=new THREE.Vector3(...model.bounds_m[0]),hi=new THREE.Vector3(...model.bounds_m[1]),span=new THREE.Vector3(...model.dimensions_m),pad=source.placement==='external'?span.multiplyScalar(.5):new THREE.Vector3();point.max(lo.sub(pad)).min(hi.add(pad));source.position_m=point.toArray();drawHeatMarkers();selectionText('heat',source);changed();}
let painting=false,lastPaint=0;
function paint(hit){const g=group();if(!g||hit.object!==solid)return;const selected=new Set(g.faces),radius=number('brush-radius')/100,start=hit.faceIndex,queue=[start],visited=new Set([start]),reference=new THREE.Vector3().fromArray(normals,start*3);while(queue.length){const f=queue.pop(),c=new THREE.Vector3().fromArray(centers,f*3),normal=new THREE.Vector3().fromArray(normals,f*3);if(f!==start&&(c.distanceTo(hit.point)>radius||normal.dot(reference)<.1))continue;if(action==='erase')selected.delete(f);else selected.add(f);for(const next of neighbors[f])if(!visited.has(next)){visited.add(next);queue.push(next);}}g.faces=[...selected].sort((a,b)=>a-b);selectionText(tab,g);changed();}
renderer.domElement.addEventListener('pointerdown',event=>{if(view==='setup'&&tab==='heat'){setRay(event);const marker=raycaster.intersectObjects(heatMarkers.children,false)[0];if(marker){activeHeat=marker.object.userData.heatIndex;refreshEntities();const current=heatMarkers.children[activeHeat];draggingHeat={index:activeHeat,marker:current};dragPlane.setFromNormalAndCoplanarPoint(camera.getWorldDirection(new THREE.Vector3()),current.position);renderer.domElement.setPointerCapture(event.pointerId);controls.enabled=false;return;}}if(view==='setup'&&action!=='orbit'){const hit=hitAt(event);if(hit){painting=true;renderer.domElement.setPointerCapture(event.pointerId);paint(hit);}}});
renderer.domElement.addEventListener('pointerup',()=>{painting=false;if(draggingHeat){draggingHeat=null;controls.enabled=action==='orbit';}});renderer.domElement.addEventListener('pointercancel',()=>{painting=false;draggingHeat=null;controls.enabled=action==='orbit';});
renderer.domElement.addEventListener('pointermove',event=>{if(!solid)return;if(draggingHeat){moveHeatMarker(event);return;}const hit=hitAt(event);if(!hit)return;if(painting&&performance.now()-lastPaint>35){lastPaint=performance.now();paint(hit);}const xyz=[hit.point.x,hit.point.y,hit.point.z].map(x=>(x*100).toFixed(2));let text='X '+xyz[0]+' · Y '+xyz[1]+' · Z '+xyz[2]+' cm';if(view==='temperature'&&result){const g=hit.object.geometry,a=new THREE.Vector3().fromBufferAttribute(g.attributes.position,hit.face.a),b=new THREE.Vector3().fromBufferAttribute(g.attributes.position,hit.face.b),c=new THREE.Vector3().fromBufferAttribute(g.attributes.position,hit.face.c),w=THREE.Triangle.getBarycoord(hit.point,a,b,c,new THREE.Vector3());const values=hit.object===solid?temperatures:slice.values,n=hit.object===solid?result.vertices:slice.vertices;const T=w.x*values[frame*n+hit.face.a]+w.y*values[frame*n+hit.face.b]+w.z*values[frame*n+hit.face.c];text=T.toFixed(3)+' '+fieldUnit()+' · '+text;}$('point-readout').textContent=text;});
$$('[data-tab]').forEach(b=>b.onclick=()=>setTab(b.dataset.tab));$$('[data-view]').forEach(b=>b.onclick=()=>setView(b.dataset.view));$$('[data-action]').forEach(b=>b.onclick=()=>setAction(b.dataset.action));
$('fit').onclick=()=>fit();for(const [id,sign] of [['front',-1],['back',1]])$(id).onclick=()=>{const distance=camera.position.distanceTo(controls.target);camera.position.copy(controls.target).add(new THREE.Vector3(0,sign,.1).normalize().multiplyScalar(distance));controls.update();render();};
$('add-region').onclick=()=>{if(!cfg)return;const lo=model.bounds_m[0],hi=model.bounds_m[1];cfg.regions.push({name:'区域 '+(cfg.regions.length+1),min_m:lo.map((x,i)=>x+(hi[i]-x)*.25),max_m:lo.map((x,i)=>x+(hi[i]-x)*.75),material:clone(boot.materials[1])});activeRegion=cfg.regions.length-1;refreshEntities();drawBoxes();changed();};
$('remove-region').onclick=()=>{cfg.regions.splice(activeRegion,1);activeRegion=-1;refreshEntities();drawBoxes();changed();};
$('region-name').onchange=()=>{cfg.regions[activeRegion].name=$('region-name').value;refreshEntities();changed();};
for(let k=0;k<3;k++)for(const edge of ['min','max'])$('xyz'[k]+edge).onchange=()=>{const r=cfg.regions[activeRegion],value=number('xyz'[k]+edge)/100;if((edge==='min'&&value>=r.max_m[k])||(edge==='max'&&value<=r.min_m[k])){toast('最小坐标必须小于最大坐标',true);refreshEntities();return;}r[edge+'_m'][k]=value;drawBoxes();changed();};
$('material-preset').onchange=()=>{const value=clone(boot.materials[number('material-preset')]);if(activeRegion<0)cfg.base_material=value;else cfg.regions[activeRegion].material=value;refreshEntities();changed();};
for(const [id,key] of [['material-name','name'],['mat-k','k'],['mat-rho','rho'],['mat-cp','cp'],['mat-cte','thermal_expansion_CTE_per_K']])$(id).onchange=()=>{material()[key]=key==='name'?$(id).value:number(id);refreshEntities();changed();};
$('phase-enabled').onchange=()=>{const m=material();m.phase_change=$('phase-enabled').value==='on'?{melting_C:number('phase-melting'),mushy_C:number('phase-mushy'),latent_J_kg:number('phase-latent')}:null;syncPhaseFields();changed();};
for(const [id,key] of [['phase-melting','melting_C'],['phase-mushy','mushy_C'],['phase-latent','latent_J_kg']])$(id).onchange=()=>{const p=material().phase_change;if(p){p[key]=number(id);changed();}};
for(const kind of ['heat','cooling']){$('add-'+kind).onclick=()=>{if(!cfg)return;const isHeat=kind==='heat',list=isHeat?cfg.heat_sources:cfg.cooling;list.push(isHeat?{name:'热源 '+(list.length+1),source_type:'surface',placement:'surface',power_W:100,start_s:0,end_s:cfg.duration_s,faces:[],position_m:model.bounds_m[0].map((x,i)=>(x+model.bounds_m[1][i])/2),radius_m:Math.max(...model.dimensions_m)/30,power_profile:[],thermostat:null}:{name:'散热区 '+(list.length+1),h:25,ambient_C:cfg.ambient_C,faces:[],radiation:false,emissivity:.85});if(isHeat)activeHeat=list.length-1;else activeCooling=list.length-1;refreshEntities();setAction('paint');changed();};$('remove-'+kind).onclick=()=>{if(kind==='heat'){cfg.heat_sources.splice(activeHeat,1);activeHeat=Math.max(0,activeHeat-1);}else{cfg.cooling.splice(activeCooling,1);activeCooling=Math.max(0,activeCooling-1);}refreshEntities();changed();};}
for(const [id,key] of [['heat-name','name'],['heat-power','power_W'],['heat-start','start_s'],['heat-end','end_s'],['cooling-name','name'],['cooling-h','h'],['cooling-ambient','ambient_C']])$(id).onchange=()=>{const heat=id.startsWith('heat-'),g=heat?cfg.heat_sources[activeHeat]:cfg.cooling[activeCooling];if(!g)return;g[key]=key==='name'?$(id).value:number(id);refreshEntities();changed();};
$('heat-source-type').onchange=()=>{const g=cfg?.heat_sources[activeHeat];if(!g)return;g.source_type=$('heat-source-type').value;refreshEntities();changed();};$('heat-placement').onchange=()=>{const g=cfg?.heat_sources[activeHeat];if(!g)return;g.placement=$('heat-placement').value;refreshEntities();changed();};$('heat-radius').onchange=()=>{const g=cfg?.heat_sources[activeHeat];if(g){g.radius_m=number('heat-radius')/1000;changed();}};for(const axis of 'xyz')$('heat-position-'+axis).onchange=()=>{const g=cfg?.heat_sources[activeHeat];if(g){g.position_m='xyz'.split('').map(a=>number('heat-position-'+a)/1000);drawHeatMarkers();selectionText('heat',g);changed();}};
function parseProfile(text){return text.split(',').map(x=>x.trim()).filter(Boolean).map(x=>{const [time_s,power_W]=x.split(':').map(Number);if(!Number.isFinite(time_s)||!Number.isFinite(power_W)||time_s<0||power_W<0)throw new Error('功率曲线格式应为 时间:功率');return {time_s,power_W};}).sort((a,b)=>a.time_s-b.time_s);}
$('heat-profile').onchange=protect(()=>{const g=cfg?.heat_sources[activeHeat];if(g){g.power_profile=parseProfile($('heat-profile').value);changed();}});
$('heat-thermostat').onchange=()=>{const g=cfg?.heat_sources[activeHeat];if(!g)return;g.thermostat=$('heat-thermostat').checked?{target_C:number('thermostat-target'),hysteresis_C:number('thermostat-hysteresis'),min_power_W:0,max_power_W:number('thermostat-max')}:null;$('thermostat-fields').hidden=!g.thermostat;changed();};
for(const [id,key] of [['thermostat-target','target_C'],['thermostat-hysteresis','hysteresis_C'],['thermostat-max','max_power_W']])$(id).onchange=()=>{const g=cfg?.heat_sources[activeHeat];if(g?.thermostat){g.thermostat[key]=number(id);changed();}};
$('cooling-radiation').onchange=()=>{const g=cfg?.cooling[activeCooling];if(g){g.radiation=$('cooling-radiation').checked;changed();}};$('cooling-emissivity').onchange=()=>{const g=cfg?.cooling[activeCooling];if(g){g.emissivity=number('cooling-emissivity');changed();}};
$('clear-selection').onclick=()=>{const g=group();if(g){g.faces=[];selectionText(tab,g);changed();}};
$('direction').onchange=()=>{const value=$('direction').value,g=group();if(!value||!g){$('direction').value='';return;}const axis='xyz'.indexOf(value[1]),sign=value[0]==='+'?1:-1;g.faces=[];for(let i=0;i<areas.length;i++)if(display.is_outer[i]&&normals[i*3+axis]*sign>.65)g.faces.push(i);selectionText(tab,g);changed();$('direction').value='';};
for(const [id,key] of [['initial','initial_C'],['ambient','ambient_C'],['default-h','default_h'],['duration','duration_s'],['dt','dt_s'],['save-interval','save_s'],['analysis-mode','analysis_mode'],['radiation-ambient','radiation_ambient_C'],['emissivity','emissivity'],['contact-resistance','contact_resistance_m2K_W']])$(id).onchange=()=>{if(cfg){cfg[key]=id==='analysis-mode'?$(id).value:number(id);changed();if(id==='analysis-mode')$('solver-model-note').textContent=$(id).value==='steady'?'三维稳态导热 · 对流与辐射边界':'三维瞬态导热 · 对流与辐射边界';}};
$('air-gap-enabled').onchange=()=>{if(cfg){cfg.air_gap_enabled=$('air-gap-enabled').checked;changed();}};$('air-gap-max').onchange=()=>{if(cfg){cfg.air_gap_max_m=number('air-gap-max')/1000;changed();}};$('air-gap-k').onchange=()=>{if(cfg){cfg.air_gap_k_W_mK=number('air-gap-k');changed();}};
$('mesh-size').onchange=()=>{cfg.mesh_size_m=number('mesh-size')/1000;changed();};$('heat-convection').onchange=()=>{cfg.heat_convection=$('heat-convection').checked;changed();};$('radiation-enabled').onchange=()=>{cfg.radiation_enabled=$('radiation-enabled').checked;changed();};$('project-name').onchange=()=>{if(cfg){cfg.name=$('project-name').value;changed();}};
$('model-select').onchange=protect(()=>loadModel($('model-select').value));
$('project-select').onchange=protect(async()=>{const id=$('project-select').value;if(!id)return;const p=await api('/projects/'+id);await loadModel(p.model_id,p.config);toast('已加载算例：'+p.name);});
$('save-project').onclick=protect(async()=>{if(!cfg)throw new Error('请先导入模型');await post('/projects',cfg);await refreshLibrary();toast('算例已保存到本机');});
function resetAgentConversation(notice=''){
  agentRequestGeneration++;
  agentConversation=cfg?{base:clone(cfg),anchor:JSON.stringify(cfg),history:[],applied:0,busy:false}:null;
  agentConfig=null;
  $('agent-prompt').value='';
  $('agent-plan-box').hidden=true;
  $('agent-conversation-note').textContent=notice||'可以分次补充参数或回答追问。必需问题全部解决后，才能确认并仿真。';
  renderAgentHistory();
  syncAgentComposer();
}
function ensureAgentConversation(){
  if(!agentConversation||agentConversation.anchor!==JSON.stringify(cfg))
    resetAgentConversation(agentConversation?'主界面参数已更新，已基于当前参数开始新对话。':'');
}
function renderAgentHistory(messages=agentConversation?.history||[]){
  const list=$('agent-history');
  list.replaceChildren();list.hidden=!messages.length;
  for(const message of messages){
    const entry=document.createElement('article'),name=document.createElement('strong'),body=document.createElement('div');
    entry.className='agent-message '+message.role;
    name.textContent=message.role==='user'?'你':'仿真助手';body.textContent=message.content;
    entry.append(name,body);list.append(entry);
  }
  list.scrollTop=list.scrollHeight;
}
function syncAgentComposer(){
  const busy=!!agentConversation?.busy,continuing=!!agentConversation?.history.length;
  $('agent-plan').disabled=busy;
  $('agent-mode').disabled=busy;
  $('agent-prompt').disabled=busy;
  $('agent-plan').textContent=busy?'分析中…':continuing?'发送补充':'生成配置';
  $('agent-prompt-label').textContent=continuing?'继续补充或回答问题':'仿真目标';
  $('agent-prompt').placeholder=continuing?'直接回答上方问题，或描述要调整的参数；可分多次补充。':'例如：在模型内部中心新增一个 3000 W 点热源，仿真 180 秒。';
  const ready=!!agentConfig&&!busy&&!$('agent-prompt').value.trim();
  $('agent-apply').disabled=!ready;$('agent-confirm').disabled=!ready;
}
$('agent-open').onclick=()=>{if(!cfg){toast('请先导入或选择一个模型',true);return;}ensureAgentConversation();$('agent-dialog').hidden=false;$('agent-prompt').focus();};
$('agent-close').onclick=()=>$('agent-dialog').hidden=true;
$('agent-dialog').onclick=e=>{if(e.target===$('agent-dialog'))$('agent-dialog').hidden=true;};
$('agent-new').onclick=()=>{resetAgentConversation();$('agent-prompt').focus();};
$('agent-prompt').oninput=syncAgentComposer;
$('agent-prompt').onkeydown=e=>{if(e.key==='Enter'&&(e.ctrlKey||e.metaKey)&&!e.isComposing){e.preventDefault();if(!$('agent-plan').disabled)$('agent-plan').click();}};
$('agent-trace-close').onclick=()=>$('agent-trace').hidden=true;
const agentModeLabels={local:'本地规则解析器',codex:'Codex'};
function refreshAgentModeHint(){const mode=$('agent-mode')?.value||'local';$('agent-mode-hint').textContent=mode==='codex'?'Codex 可结合上下文理解简短回复，逐轮补全配置。默认调用本机官方 Codex CLI。':'本地模式支持明确参数的逐轮修改；涉及指代、选择或复杂追问时，请切换 Codex，已有对话会保留。';}
$('agent-mode').onchange=refreshAgentModeHint;
 $('agent-plan').onclick=protect(async()=>{
  if(!cfg)throw new Error('请先导入模型');
  if(agentConversation?.busy)return;
  const prompt=$('agent-prompt').value.trim();
  if(!prompt)throw new Error('请描述仿真目标或回答上方问题');
  ensureAgentConversation();
  $('agent-prompt').value=prompt;
  const state=agentConversation,mode=$('agent-mode')?.value||'local',requestConfig=clone(state.base);
  if(prompt.length>4000||state.history.length>=200||prompt.length+state.history.reduce((n,m)=>n+m.content.length,0)>120000)
    throw new Error('对话内容超过单次处理范围，请缩短输入，或整理未解决要求后开始新对话。');
  const requestGeneration=++agentRequestGeneration,modelGeneration=loadGeneration;
  const isCurrent=()=>agentConversation===state&&requestGeneration===agentRequestGeneration&&modelGeneration===loadGeneration&&cfg?.model_id===requestConfig.model_id;
  const started=Date.now(),userMessage={role:'user',content:prompt};
  agentConfig=null;state.busy=true;syncAgentComposer();
  $('agent-plan-box').hidden=true;
  renderAgentHistory([...state.history,userMessage]);
  agentTraceReset();
  const showWaiting=()=>{if(isCurrent())agentTraceSet(0,'active',(agentModeLabels[mode]||mode)+' 正在核对配置 · 已等待 '+Math.floor((Date.now()-started)/1000)+' 秒');};
  showWaiting();
  const waitingTimer=setInterval(showWaiting,1000);
  try{
    const plan=await post('/agent/plan',{model_id:requestConfig.model_id,prompt,config:requestConfig,mode,
      history:clone(state.history),applied_message_count:state.applied});
    if(!isCurrent())return;
    const questions=plan.questions||[];
    $('agent-plan-box').hidden=false;
    const detailLabel=(agentModeLabels[mode]||mode)+(plan.workflow==='thermal-config'?' · 配置 Skill':'')+(Number.isFinite(plan.metrics?.elapsed_s)?' · '+plan.metrics.elapsed_s.toFixed(1)+' 秒':'');
    $('agent-plan-mode').textContent=' · '+detailLabel;
    if(!plan.ok||questions.length){
      const detail=questions.join('\n')||'配置生成失败，请重试。';
      const clarification=['clarification','invalid_config'].includes(plan.error_code)||(plan.ok&&questions.length)||(!plan.error_code&&mode==='local');
      agentTraceSet(0,clarification?'active':'error',detail);
      $('agent-plan-text').textContent=clarification?'配置尚未完成，请在下方回答待解决问题。':'配置生成未完成，保留本条输入，可重试。';
      $('agent-plan-questions').textContent=detail;
      $('agent-plan-questions').classList.add('has-warning');
      if(clarification){
        state.history.push(userMessage,{role:'assistant',content:detail});
        $('agent-prompt').value='';
      }
      renderAgentHistory();
      return;
    }
    if(!plan.config?.heat_sources?.length&&!plan.config?.environment_only)throw new Error('Agent 草案没有生成热源。请补充热源或明确使用纯环境温变工况；本次不能确认运行。');
    if(plan.config.model_id!==requestConfig.model_id)throw new Error('Agent 草案属于其他模型，请为当前模型重新生成。');
    agentConfig=clone(plan.config);
    const lines=[...(plan.changes||[]).map(x=>'✓ '+x),...(plan.warnings||[]).map(x=>'! '+x)];
    const summary=lines.length?lines.join('\n'):'当前配置无需修改。';
    state.history.push(userMessage,{role:'assistant',content:summary+'\n必需参数已通过校验。可继续补充，或点击“确认并仿真”。'});
    state.base=clone(agentConfig);state.applied=state.history.length;
    $('agent-prompt').value='';
    renderAgentHistory();
    agentTraceSet(0,'done','已收到配置 · '+detailLabel);
    agentTraceSet(1,'done','模型 '+model.name+' · 请核对热源选区');
    agentTraceSet(2,'done',(plan.changes||[]).join(' · ')||'保持当前参数');
    agentTraceSet(3,'done','必需参数与几何校验通过，等待用户确认');
    $('agent-plan-text').textContent=summary;
    $('agent-plan-questions').textContent='参数已完整。可继续补充，或确认并仿真。';
    $('agent-plan-questions').classList.toggle('has-warning',false);
  }catch(error){
    if(!isCurrent())return;
    agentConfig=null;
    agentTraceSet(0,'error',error.message);
    $('agent-plan-box').hidden=false;
    $('agent-plan-mode').textContent=' · '+(agentModeLabels[mode]||mode);
    $('agent-plan-text').textContent='配置生成未完成，保留本条输入，可重试。';
    $('agent-plan-questions').textContent=error.message;
    $('agent-plan-questions').classList.add('has-warning');
    renderAgentHistory();
    throw error;
  }finally{
    clearInterval(waitingTimer);
    if(isCurrent()){state.busy=false;syncAgentComposer();$('agent-prompt').focus();}
  }
 });
 function applyAgentConfig(){if(agentConversation?.busy||$('agent-prompt').value.trim())throw new Error('请先发送补充并完成参数核对');if(!agentConfig)throw new Error('请先生成配置');if(!agentConfig.heat_sources?.length&&!agentConfig.environment_only)throw new Error('草案缺少热源，请重新生成配置。');if(agentConfig.model_id!==cfg.model_id)throw new Error('模型已改变，请重新生成配置。');cfg=clone(agentConfig);agentConversation.anchor=JSON.stringify(cfg);dirty=true;result=null;temperatures=null;jobId=null;activeRegion=-1;activeHeat=0;activeCooling=0;syncFields();setView('setup');}
 $('agent-apply').onclick=protect(async()=>{applyAgentConfig();agentRunning=false;agentTraceSet(3,'done','配置已应用，可在主界面修改后确认');$('agent-dialog').hidden=true;toast('Agent 配置已应用，请检查或修改参数后点击“运行仿真”');});
 $('agent-confirm').onclick=protect(async()=>{applyAgentConfig();agentRunning=true;agentTraceSet(4,'active','用户已确认，正在提交任务');$('agent-dialog').hidden=true;toast('已确认 Agent 配置，开始运行仿真');$('run').click();});
$('export-config').onclick=()=>{if(!cfg)return;const url=URL.createObjectURL(new Blob([JSON.stringify(cfg,null,2)],{type:'application/json'})),a=document.createElement('a');a.href=url;a.download='thermal-config.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
$('download-report-pdf').onclick=e=>{if(!jobId){e.preventDefault();return;}e.currentTarget.href='/api/jobs/'+jobId+'/files/report.pdf';e.currentTarget.download='thermal-report.pdf';};
function measurementList(text){return text.split(',').map(x=>x.trim()).filter(Boolean).map(x=>{const [time_s,temperature_C]=x.split(':').map(Number);if(!Number.isFinite(time_s)||!Number.isFinite(temperature_C))throw new Error('实测点格式应为 时间:温度');return {time_s,temperature_C};});}
$('fit-h').onclick=protect(async()=>{$('cal-result').textContent='计算中…';const r=await post('/analysis/calibrate',{mode:'fit_h',mass_kg:number('cal-mass'),cp_J_kgK:number('cal-cp'),area_m2:number('cal-area'),initial_C:number('cal-initial'),ambient_C:number('cal-ambient'),power_W:number('cal-power'),measurements:measurementList($('cal-measurements').value)});$('cal-result').textContent=JSON.stringify(r,null,2);});
$('fit-power').onclick=protect(async()=>{$('cal-result').textContent='计算中…';const r=await post('/analysis/calibrate',{mode:'power_limit',mass_kg:number('cal-mass'),cp_J_kgK:number('cal-cp'),area_m2:number('cal-area'),initial_C:number('cal-initial'),ambient_C:number('cal-ambient'),h_W_m2K:number('cal-h'),max_temperature_C:number('cal-limit'),duration_s:number('cal-duration'),power_W:number('cal-power'),measurements:[{time_s:0,temperature_C:number('cal-initial')},{time_s:number('cal-duration'),temperature_C:number('cal-limit')}]});$('cal-result').textContent=JSON.stringify(r,null,2);});
$('run-cycle').onclick=protect(async()=>{$('cycle-result').textContent='计算中…';const r=await post('/analysis/refrigeration',{refrigerant:$('cycle-refrigerant').value,evaporating_C:number('cycle-evap'),condensing_C:number('cycle-cond'),superheat_C:number('cycle-superheat'),subcool_C:number('cycle-subcool'),cooling_capacity_W:number('cycle-capacity'),compressor_efficiency:number('cycle-efficiency'),fan_pump_power_W:number('cycle-aux')});$('cycle-result').textContent=JSON.stringify(r,null,2);});
$('file').onchange=protect(async()=>{const file=$('file').files[0];if(!file)return;const form=new FormData();form.append('file',file);form.append('units',$('import-units').value);form.append('scale_factor',$('import-scale').value);const uploaded=await api('/models',{method:'POST',body:form});toast('正在导入 '+file.name);$('file').disabled=true;try{while(true){await new Promise(r=>setTimeout(r,1000));const value=await api('/models/'+uploaded.id);$('view-title').textContent=value.status.detail;if(value.status.phase==='failed')throw new Error(value.status.detail);if(value.state==='ready'){await refreshLibrary();await loadModel(value.id);toast('模型已导入，请设置材料和热源');break;}}}finally{$('file').disabled=false;$('file').value='';}});
async function openJob(id){const config=await api('/jobs/'+id+'/files/config.json');if(await loadModel(config.model_id,config))await loadResult(id);}
async function loadResult(id){stopPlay();const generation=loadGeneration;const data=await api('/jobs/'+id+'/result'),values=await binary('/api/jobs/'+id+'/files/surface.bin',Float32Array);if(generation!==loadGeneration)return;if(values.length!==data.frames*data.vertices||data.vertices!==points.length/3)throw new Error('结果与模型顶点数量不匹配');result=data;surfaceComponent='all';if($('surface-component'))$('surface-component').value='all';const warnings=data.summary.warnings||[],energy=data.summary.energy||{};const noHeat=!Number(energy.input_energy_J)&&Math.abs(data.maximum_C-data.minimum_C)<1e-8;uniformResult=noHeat||Math.abs(data.maximum_C-data.minimum_C)<1e-8;const excursion=row=>Math.max(Math.abs(row.maximum_C-cfg.initial_C),Math.abs(row.minimum_C-cfg.initial_C));const peakFrame=data.stats.reduce((best,row,index)=>excursion(row)>excursion(data.stats[best])?index:best,0);const peakNotice=peakFrame<data.stats.length-1&&data.stats[peakFrame].maximum_C>data.stats.at(-1).maximum_C+1e-4?['当前默认显示最高温度帧 t='+data.stats[peakFrame].time_s+' s，末帧温度为 '+data.stats.at(-1).average_C.toFixed(3)+' °C。']:[];$('numerical-notice').textContent=[...warnings,...(noHeat?['当前算例没有热源，温度场保持等温；请在“热源”中添加并刷选受热面。']:[]),...peakNotice].join(' ');$('numerical-notice').hidden=!warnings.length&&!noHeat&&!peakNotice.length;temperatures=values;await loadEngineeringResult(id,data,values);if(generation!==loadGeneration)return;jobId=id;dirty=false;$('temperature-view').disabled=false;$('dirty-note').hidden=true;$('timeline').max=result.frames-1;frame=peakFrame;$('timeline').value=frame;const lo=result.minimum_C,hi=result.maximum_C;if(hi-lo<1e-8){result.minimum_C=lo-.5;result.maximum_C=hi+.5;}updateSurfaceRange();$('download-csv').href='/api/jobs/'+id+'/files/history.csv';$('download-displacement').href='/api/jobs/'+id+'/files/displacement.bin';$('download-report').href='/api/jobs/'+id+'/files/report.md';$('download-report-pdf').href='/api/jobs/'+id+'/files/report.pdf';$('download-report-pdf').download='thermal-report.pdf';$('download-3d').href='/api/jobs/'+id+'/files/result.zip';const expansion=data.thermal_expansion||data.summary.thermal_expansion;const expansionText=expansion&&Number(expansion.max_displacement_m||expansion.maximum_displacement_m)>0?' · 热位移峰值 '+(Number(expansion.max_displacement_m||expansion.maximum_displacement_m)*1000).toFixed(3)+' mm':'';$('result-evidence').textContent=(result.summary.nodes||0).toLocaleString()+' 节点 · 能量残差 '+Number(energy.energy_balance_error_J).toExponential(1)+' J'+expansionText;updateThermalAnimation({id,name:cfg.name,mode:data.analysis_mode||cfg.analysis_mode||'transient',frames:data.frames});setView('temperature');showFrame(frame);}
function showFrame(i){
 const steady=(result.analysis_mode??=cfg.analysis_mode||'transient')==='steady';
 if(steady)i=result.frames-1;
 frame=i;updateDeformation();const s=surfaceStats(i);
 $('maximum').textContent=s.maximum_C.toFixed(3);$('average').textContent=s.average_C.toFixed(3);$('minimum').textContent=s.minimum_C.toFixed(3);
 const seconds=value=>Number(Number(value).toPrecision(8)).toString();
 $('time-label').textContent=steady?'稳态平衡结果':seconds(result.times_s[i])+' / '+seconds(result.times_s.at(-1))+' s · 第 '+(i+1)+' / '+result.frames+' 帧'+(i?' · 距上一帧 '+seconds(result.times_s[i]-result.times_s[i-1])+' s':' · 初始帧');
 $('playback-note').textContent=steady?'稳态结果表示平衡状态，不提供升温动画；仍可切换温度、位移和应力云图。':'逐保存帧播放温度、位移和应力；时间标签显示真实保存时刻，播放帧率只控制观看速度。';
 for(const id of ['play','timeline','playback-fps'])$(id).disabled=steady||result.frames<2;
 $('timeline').value=i;$('play').textContent=steady?'稳态结果':playing?'暂停':i===result.frames-1?'从头播放':'播放';recolor();
}
function stopPlay(){playing=false;clearInterval(playTimer);if(result)$('play').textContent=result.analysis_mode==='steady'?'稳态结果':frame===result.frames-1?'从头播放':'播放';}
function playbackInterval(){const fps=Math.min(60,Math.max(.1,Number($('playback-fps').value)||4));$('playback-fps').value=fps;return 1000/fps;}
function startPlaybackTimer(){clearInterval(playTimer);playTimer=setInterval(()=>{if(frame>=result.frames-1){stopPlay();return;}showFrame(frame+1);},playbackInterval());}
$('play').onclick=()=>{if(!result||result.analysis_mode==='steady'||result.frames<2)return;if(playing){stopPlay();return;}if(frame===result.frames-1)showFrame(0);playing=true;$('play').textContent='暂停';startPlaybackTimer();};$('playback-fps').oninput=()=>{const value=Number($('playback-fps').value);if(playing&&Number.isFinite(value)&&value>0)startPlaybackTimer();};$('playback-fps').onchange=()=>{playbackInterval();if(playing)startPlaybackTimer();};$('timeline').oninput=()=>{stopPlay();showFrame(number('timeline'));};
const clipPlane=new THREE.Plane(new THREE.Vector3(-1,0,0),0);
function applyClip(){const enabled=view==='temperature'&&$('show-slice').checked&&sliceMesh;const axis=number('slice-axis'),ratio=number('slice-position')/100,value=model?model.bounds_m[0][axis]*(1-ratio)+model.bounds_m[1][axis]*ratio:0;clipPlane.normal.set(0,0,0).setComponent(axis,-1);clipPlane.constant=value;resultMaterial.clippingPlanes=enabled?[clipPlane]:[];setupMaterial.clippingPlanes=[];if(creases)creases.material.clippingPlanes=enabled?[clipPlane]:[];if(sliceMesh)sliceMesh.visible=!!enabled;$('slice-value').textContent=(value*100).toFixed(2)+' cm';render();}
let sliceTimer,sliceGeneration=0;
async function loadSlice(){if(!result||!$('show-slice').checked){applyClip();return;}const token=++sliceGeneration,id=jobId,axis=number('slice-axis'),ratio=number('slice-position')/100,value=model.bounds_m[0][axis]*(1-ratio)+model.bounds_m[1][axis]*ratio;$('slice-value').textContent='计算截面…';const meta=await api('/jobs/'+id+'/slice?axis='+axis+'&value='+value);const base='/api/jobs/'+id+'/slices/'+meta.key+'/';const [pos,index,values]=await Promise.all([binary(base+'points.bin',Float32Array),binary(base+'faces.bin',Uint32Array),binary(base+'values.bin',Float32Array)]);if(token!==sliceGeneration||id!==jobId)return;clearObject(sliceMesh);const g=new THREE.BufferGeometry();g.setAttribute('position',new THREE.BufferAttribute(pos,3));g.setIndex(new THREE.BufferAttribute(index,1));g.setAttribute('color',new THREE.BufferAttribute(new Float32Array(pos.length),3));sliceMesh=new THREE.Mesh(g,new THREE.MeshBasicMaterial({vertexColors:true,side:THREE.DoubleSide,toneMapped:false}));scene.add(sliceMesh);slice={...meta,values};applyClip();recolor();}
$('show-slice').onchange=protect(async()=>{await loadSlice();if($('show-slice').checked){const axis=number('slice-axis'),dir=new THREE.Vector3(.7,-1,.3);dir.setComponent(axis,1.3);camera.position.copy(controls.target).add(dir.normalize().multiplyScalar(partSpan*1.9));controls.update();render();}});
$('slice-axis').onchange=protect(()=>loadSlice());$('slice-position').oninput=()=>{clearTimeout(sliceTimer);sliceTimer=setTimeout(protect(()=>loadSlice()),250);};
$('surface-component').onchange=()=>{surfaceComponent=$('surface-component').value;if(result){updateSurfaceRange();showFrame(frame);}else recolor();};
  $('run').onclick=protect(async()=>{if(!cfg)throw new Error('请先导入模型');if(!cfg.heat_sources?.length&&!cfg.environment_only)throw new Error('请先在“热源”中添加至少一个热源，并刷选受热面或指定点位置，再运行仿真。');for(const h of cfg.heat_sources){const pointLike=h.source_type==='point'||h.placement==='embedded';if(pointLike&&!h.position_m?.length)throw new Error('请为“'+h.name+'”指定三维位置');if(!pointLike&&!h.faces?.length)throw new Error('请为“'+h.name+'”刷选受热面');}for(const c of cfg.cooling)if(!c.faces.length)throw new Error('请先为“'+c.name+'”选取散热面');stopPlay();result=null;temperatures=null;jobId=null;$('temperature-view').disabled=true;setView('setup');let id;try{({id}=await post('/jobs',cfg));}catch(error){if(error.message.includes('已有算例正在计算')){await refreshLibrary();const running=boot.jobs.find(job=>job.status.phase==='running'||job.status.phase==='pending');if(running&&!busyId){if(agentRunning)agentTraceSet(4,'error','已有任务 '+running.id.slice(0,8)+' 正在计算');await recoverRunningJob(running);return;}}if(agentRunning)agentTraceSet(4,'error',error.message);throw error;}busyId=id;if(agentRunning)agentTraceSet(4,'active','任务 '+id.slice(0,8)+' · 求解器已启动');$('run').disabled=true;$('run-progress').hidden=false;$('progress-title').textContent=cfg.name;const snapshot=clone(cfg);try{while(true){const s=await api('/jobs/'+id);$('progress').value=s.progress;$('progress-percent').textContent=s.progress+'%';$('progress-detail').textContent=s.detail;if(agentRunning)$('agent-trace-detail').textContent=s.detail;if(s.phase==='completed'){await refreshLibrary();if(cfg.model_id===snapshot.model_id){const modified=JSON.stringify(cfg)!==JSON.stringify(snapshot);if(agentRunning)agentTraceSet(4,'done','有限元计算完成');if(agentRunning)agentTraceSet(5,'active','读取温度场并更新三维颜色');await loadResult(id);if(agentRunning)agentTraceSet(5,'done','温度场已展示，可播放时间轴');if(modified){dirty=true;$('dirty-note').hidden=false;}}toast('仿真完成');break;}if(s.phase==='failed'){if(agentRunning)agentTraceSet(4,'error',s.detail);throw new Error(s.detail);}if(s.phase==='cancelled'){if(agentRunning)agentTraceSet(4,'error','任务已取消');toast('已取消计算');break;}await new Promise(r=>setTimeout(r,1000));}}finally{busyId=null;$('run').disabled=false;$('run-progress').hidden=true;agentRunning=false;await refreshLibrary();}});
$('cancel-run').onclick=protect(async()=>{if(busyId)await post('/jobs/'+busyId+'/cancel',{});});

let resultField='temperature',temperatureFrames=null,surfaceDisplacement=null,stressFrames=null;
function fieldUnit(){return resultField==='stress'?'MPa':resultField==='displacement'?'mm':'°C';}
const assessmentWords={exceeded:'超出限值',within_limits:'在限值内',not_assessed:'未评估',review_required:'需补充评估',within_configured_limits:'在已设限值内'};
function engineeringSurfaceStats(f){
 const selected=surfaceComponent==='all'?null:Number(surfaceComponent);let lo=Infinity,hi=-Infinity,sum=0,n=0;
 for(let v=0;v<result.vertices;v++)if(selected===null||surfaceVertexComponents?.[v]===selected){const a=temperatures[f*result.vertices+v];lo=Math.min(lo,a);hi=Math.max(hi,a);sum+=a;n++;}
 return {minimum_C:n?lo:0,maximum_C:n?hi:0,average_C:n?sum/n:0};
}
function resetDeformation(){
 if(geometry&&points){geometry.attributes.position.array.set(points);geometry.attributes.position.needsUpdate=true;geometry.computeVertexNormals();if(creases)creases.visible=true;}
}
function updateDeformation(){
 resetDeformation();if(!result?.structural||!surfaceDisplacement||view!=='temperature')return;
 const scale=Math.min(1000,Math.max(0,Number($('deformation-scale').value)||0));if(!scale)return;
 const p=geometry.attributes.position.array;
 for(let i=0;i<p.length;i++)p[i]=points[i]+surfaceDisplacement[frame*p.length+i]*scale;
 geometry.attributes.position.needsUpdate=true;geometry.computeVertexNormals();if(creases)creases.visible=false;
}
async function loadEngineeringResult(id,data,temperatureValues){
 const generation=loadGeneration;
 let displacement=null,stress=null;
 if(data.structural){[displacement,stress]=await Promise.all([binary('/api/jobs/'+id+'/files/surface-displacement.bin',Float32Array),binary('/api/jobs/'+id+'/files/surface-von-mises.bin',Float32Array)]);
  if(displacement.length!==data.frames*data.vertices*3||stress.length!==data.frames*data.vertices)throw new Error('热应力/位移结果与模型不匹配');}
 if(generation!==loadGeneration)return;
 resultField='temperature';temperatureFrames=temperatureValues;surfaceDisplacement=displacement;stressFrames=stress;
 $('result-field').value='temperature';$('deformation-scale').value=0;resetDeformation();
 for(const option of $('result-field').options)option.disabled=option.value!=='temperature'&&!data.structural;
 $('deformation-scale').disabled=!data.structural;
 $$('div.temperature-stats small').forEach(el=>el.textContent='°C');
 $('show-slice').disabled=false;
 $('engineering-results').hidden=!data.assessment;
 if(!data.assessment)return;
 const a=data.assessment;$('assessment-status').textContent=assessmentWords[a.status]||a.status;$('assessment-status').dataset.status=a.status;
 $('download-assessment').href='/api/jobs/'+id+'/files/assessment.json';
 $('engineering-summary').textContent=data.structural?
  `三维热弹性 · ${data.structural.mode==='free'?'自由状态':'固定支撑'} · 峰值等效应力 ${(data.structural.maximum_von_mises_Pa/1e6).toFixed(3)} MPa · 最大位移 ${(data.structural.maximum_displacement_m*1000).toFixed(4)} mm`:
  '仅温度分析；尚未求解受约束热变形与热应力。';
 $('field-note').textContent='温度场：固定色标比较各时刻；内部剖切显示温度。';
 const contact=data.summary?.surface_evaluation;
 if(contact){const last=contact.rows.at(-1);
  $('engineering-summary').textContent+=` · 接触面末帧最高 ${last.maximum_C.toFixed(3)} °C · 面积平均 ${last.average_C.toFixed(3)} °C · 温差 ${last.delta_C.toFixed(3)} K`+
   (last.flatness_m==null?'':` · 接触面翘曲 ${(last.flatness_m*1000).toFixed(5)} mm`);}
 const body=$('assessment-table').querySelector('tbody');body.replaceChildren();
 for(const c of a.checks){const row=document.createElement('tr');
  const factor=c.unit==='Pa'?1e-6:c.unit==='m'?1000:1,unit=c.unit==='Pa'?'MPa':c.unit==='m'?'mm':c.unit||'';
  const fmt=v=>v==null?'未设置':(v*factor).toPrecision(4)+' '+unit;
  const labels=[c.name,c.value==null?'—':fmt(c.value),fmt(c.limit),c.time_s==null?'—':c.time_s+' s',assessmentWords[c.status]||c.status];
  for(const label of labels){const td=document.createElement('td');td.textContent=label;td.dataset.status=c.status;row.append(td);}body.append(row);}
}
$('result-field').onchange=()=>{
 if(!result)return;resultField=$('result-field').value;
 if(resultField==='temperature')temperatures=temperatureFrames;
 else if(resultField==='stress')temperatures=Float32Array.from(stressFrames,v=>v/1e6);
 else{temperatures=new Float32Array(result.frames*result.vertices);for(let i=0;i<temperatures.length;i++)temperatures[i]=Math.hypot(...surfaceDisplacement.subarray(i*3,i*3+3))*1000;}
 $('show-slice').checked=false;$('show-slice').disabled=resultField!=='temperature';applyClip();
 $$('div.temperature-stats small').forEach(el=>el.textContent=fieldUnit());
 let lo=Infinity,hi=-Infinity;for(const v of temperatures){lo=Math.min(lo,v);hi=Math.max(hi,v);}uniformResult=hi-lo<1e-8;
 $('field-note').textContent=resultField==='stress'?'等效应力为表面投影值；表内峰值依据原始单元应力，完整应力分量可导出。':resultField==='displacement'?'位移显示单位为毫米；表面统计为投影顶点统计，整体峰值见评估表。':'温度场：固定色标比较各时刻；内部剖切显示温度。';
 updateSurfaceRange();showFrame(frame);
};
$('deformation-scale').onchange=()=>{if(result){$('show-slice').checked=false;applyClip();updateDeformation();render();}};
document.addEventListener('visibilitychange',()=>{if(document.hidden)stopPlay();});
await protect(async()=>{await refreshLibrary();const running=boot.jobs.find(job=>job.status.phase==='running'||job.status.phase==='pending');if(running){await loadModel(running.model_id);await recoverRunningJob(running);}else if(boot.models.some(m=>m.id==='wukong'))await openJob('demo');else if(boot.models.some(m=>m.state==='ready'))await loadModel(boot.models.find(m=>m.state==='ready').id);resize();})();
