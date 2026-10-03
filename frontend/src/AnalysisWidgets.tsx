import {useEffect,useState,lazy,Suspense} from 'react';
import Viewport from './Viewport';
const ComparisonViewer=lazy(()=>import('./RunPanel').then(m=>({default:m.RunViewer})));
import {api,post,uid,number,statusLabel} from './api';

export function ProjectThumbnail({project}:{project:any}){
 const [missing,setMissing]=useState(false);
 useEffect(()=>setMissing(false),[project?.id,project?.model_id]);
 if(!project?.model_id)return null;
 return missing?<span className="project-thumbnail-missing">模型预览不可用</span>:<img className="project-thumbnail" key={project.id+':'+project.model_id} src={`/api/v1/projects/${project.id}/thumbnail`} alt={`${project.name} · 实际 CAD 预览`} loading="lazy" onError={()=>setMissing(true)}/>;
}

const analysisNames:Record<string,string>={static:'结构静力',thermal_steady:'稳态热',thermal_transient:'瞬态热',fatigue:'高周疲劳',contact_cycle:'周期接触',contact_fatigue:'接触疲劳与磨损',thermo_mechanical:'热结构耦合'};
const metricInfo:Record<string,[string,number]>={maximum_temperature_C:['最高温度 / °C',1],minimum_temperature_C:['最低温度 / °C',1],maximum_temperature_rise_K:['最大温升 / K',1],maximum_von_mises_Pa:['最大原始等效应力 / MPa',1e-6],maximum_displacement_m:['最大位移 / mm',1000],maximum_contact_pressure_Pa:['最大接触压力 / MPa',1e-6],maximum_wear_depth_m:['最大磨损深度 / μm',1e6],minimum_life_cycles:['最低有限寿命 / 次',1],maximum_damage:['最大损伤',1],minimum_safety_factor:['最低安全系数',1],strain_energy_J:['应变能 / J',1]};

const exportStatusNames:Record<string,string>={queued:'等待导出',running:'正在导出',partial:'部分导出完成',interrupted:'导出已中断',cancelled:'导出已取消',completed:'导出完成',failed:'导出失败'};
const exportStatusLabel=(status:string)=>exportStatusNames[status]||'状态待更新';

export function ExportActions({run,onUpdated}:{run:any;onUpdated?:(v:any)=>void}){
 const [scope,setScope]=useState('report'),[busy,setBusy]=useState(false),[cancelling,setCancelling]=useState(false),[message,setMessage]=useState('');
 const task=run?.latest_export,exporting=task&&!['completed','partial','failed','cancelled','interrupted'].includes(task.status);
 useEffect(()=>{if(!exporting)return;let stale=false;const timer=setInterval(()=>api('/analysis-runs/'+run.id).then(v=>{if(!stale)onUpdated?.(v)}).catch(e=>setMessage(String(e))),1800);return()=>{stale=true;clearInterval(timer)}},[run?.id,exporting,onUpdated]);
 if(!run||!['completed','partial','failed','interrupted','cancelled'].includes(run.status))return null;
 return <div className="export-actions">
  {task&&<small>导出状态：{exportStatusLabel(task.status)} · {task.detail}</small>}
  <label>仅重新导出<select aria-label="重新导出范围" value={scope} onChange={e=>setScope(e.target.value)} disabled={busy||cancelling||!!exporting}><option value="report">报告与清单</option><option value="video">视频</option><option value="all">报告与视频</option></select></label>
  <button disabled={busy||exporting||cancelling} onClick={async()=>{setBusy(true);setMessage('');try{const value=await post(`/analysis-runs/${run.id}/exports`,{scope,request_id:uid()});onUpdated?.({...run,latest_export:value});setMessage('已提交后台导出，沿用已有原始结果。');}catch(e){setMessage(String(e))}finally{setBusy(false)}}}>{busy?'正在提交…':'重新生成'}</button>
  {exporting&&<button disabled={busy||cancelling} onClick={async()=>{setCancelling(true);setMessage('');try{const value=await post(`/analysis-runs/${run.id}/exports/${task.id}/cancel`);onUpdated?.({...run,latest_export:value});setMessage(exportStatusLabel(value.status)+'，上一版本交付保留。');onUpdated?.(await api('/analysis-runs/'+run.id));}catch(e){setMessage(String(e))}finally{setCancelling(false)}}}>{cancelling?'正在取消…':'取消本次导出'}</button>}
  {task&&['failed','partial','interrupted'].includes(task.status)&&<a href={`/api/v1/analysis-runs/${run.id}/exports/${task.id}/log`} target="_blank" rel="noreferrer">查看导出诊断日志</a>}
  {message&&<small role="status">{message}</small>}
 </div>;
}

export function QueueStatus(){
 const [queue,setQueue]=useState<any>(null);
 useEffect(()=>{let stale=false;const load=()=>api('/execution/queue').then(v=>{if(!stale)setQueue(v)}).catch(()=>{});load();const timer=setInterval(load,5000);return()=>{stale=true;clearInterval(timer)}},[]);
 if(!queue)return null;
 const items=queue.entries||queue.jobs||queue.items||[];
 return <details className="queue-status"><summary>后台队列 · {items.length} 项 · CPU {queue.cpu_threads||6} 线程</summary>{items.length?items.map((item:any,i:number)=><div key={item.id||i}><b>{item.queue_position?`#${item.queue_position} `:''}{item.name||item.label||item.id?.slice(0,8)} · {item.kind==='export_task'?exportStatusLabel(item.status):statusLabel[item.status]||item.status}</b><p>{item.detail||item.stage}</p>{item.last_progress_at&&<small>最近更新 {new Date(item.last_progress_at*1000).toLocaleString()}</small>}</div>):<p>当前没有排队或运行中的求解。</p>}{queue.free_disk_bytes!=null&&<small>可用磁盘 {number(queue.free_disk_bytes/2**30)} GiB</small>}</details>;
}

export function PreservedStages({study}:{study:any}){
 if(!study?.checkpoints?.length)return null;
 return <div className="preserved-stages"><b>已保存的收敛物理步</b>{study.checkpoints.map((checkpoint:any)=><p key={checkpoint.kind}>{checkpoint.kind==='coupling'?'热结构耦合':'热场'} · 最后收敛时刻 {number(checkpoint.last_converged_time_s)} s · 步编号 {checkpoint.last_committed_index}<br/>{checkpoint.download_url?<a href={checkpoint.download_url}>下载阶段 HDF5</a>:<span>计算停止后可下载阶段 HDF5</span>}</p>)}<small>仅包含已提交的物理状态，不表示整项计算完成。恢复时由任务流程检查可复用阶段。</small></div>;
}

export function NativeComparison({projectId,onLegacy}:{projectId:string;onLegacy?:(id:string)=>void}){
 const [data,setData]=useState<any>(null),[selected,setSelected]=useState<string[]>([]),[error,setError]=useState(''),[clouds,setClouds]=useState(false);
 useEffect(()=>{let stale=false;api(`/projects/${projectId}/analysis-comparison`).then(v=>{if(!stale){setData(v);setSelected(v.entries.filter((x:any)=>x.status==='completed').slice(0,2).map((x:any)=>x.id));}}).catch(e=>setError(String(e)));return()=>{stale=true}},[projectId]);
 const entries=(data?.entries||[]).filter((x:any)=>selected.includes(x.id));
 const hashes=new Set(entries.map((x:any)=>x.geometry_sha256||x.model_id));
 const types=new Set(entries.map((x:any)=>x.analysis_type));
 const keys=[...new Set<string>(entries.flatMap((x:any)=>Object.keys(x.metrics||{})))];
 return <div className="native-comparison"><h1>工况比较</h1><p>原静力与多物理场研究均保留来源。指标按相同单位对照，不同物理过程不做等价判断。</p>{error&&<p role="alert">{error}</p>}<div className="comparison-picker">{data?.entries?.map((entry:any)=><label key={entry.id}><input type="checkbox" checked={selected.includes(entry.id)} onChange={()=>setSelected(old=>old.includes(entry.id)?old.filter(x=>x!==entry.id):[...old,entry.id])}/>{entry.name} <small>{analysisNames[entry.analysis_type]||entry.analysis_type} · {entry.id.slice(0,8)} · {statusLabel[entry.status]||entry.status}</small></label>)}</div>{hashes.size>1&&<p className="study-error">所选结果来自不同几何，空间位置和云图不可直接对应。</p>}{types.size>1&&<p className="study-note">包含不同分析类型。只对照各自定义明确的指标，不表示相同载荷下的差异。</p>}<div className="comparison-scroll"><table><thead><tr><th>指标</th>{entries.map((entry:any)=><th key={entry.id}>{entry.name}<small>{entry.kind==='job'?'旧静力记录':'研究结果'}</small></th>)}</tr></thead><tbody>{keys.map(key=><tr key={key}><td>{data?.metric_labels?.[key]||metricLabel(key)}</td>{entries.map((entry:any)=><td key={entry.id}>{formatMetric(entry.metrics?.[key],key)}</td>)}</tr>)}<tr><td>真实保存时刻</td>{entries.map((entry:any)=><td key={entry.id}>{entry.times_s?.length>1?`${entry.times_s.length} 帧 / ${number(entry.times_s.at(-1))} s`:'非瞬态结果'}</td>)}</tr><tr><td>原始数据与报告</td>{entries.map((entry:any)=><td key={entry.id}>{entry.kind==='job'?<><button onClick={()=>onLegacy?.(entry.id)}>查看云图</button><a href={`/api/v1/jobs/${entry.id}/files/report.pdf`}>报告</a></>:<><a href={`/api/v1/studies/${entry.id}/files/report.pdf`}>报告</a><a href={`/api/v1/studies/${entry.id}/files/fields.h5`}>HDF5</a></>}</td>)}</tr></tbody></table></div><label className="comparison-cloud-switch"><input type="checkbox" checked={clouds} onChange={e=>setClouds(e.target.checked)}/>并排查看云图（各自色标与真实时间轴）</label>{clouds&&<div className="comparison-clouds">{entries.slice(0,2).map((entry:any)=><div key={entry.id}>{entry.kind==='job'?<LegacyCloud entry={entry}/>:<Suspense fallback={<p>正在加载结果…</p>}><ComparisonViewer node={{study_id:entry.id,model_id:entry.model_id,key:entry.id,label:entry.name,status:entry.status}}/></Suspense>}</div>)}</div>}</div>;
}
function formatMetric(v:any,key:string){if(v==null)return '—';if(typeof v==='number')return number(v*(metricInfo[key]?.[1]||1));if(typeof v==='object'&&'value'in v)return `${number(v.value)} ${v.unit||''}`;return String(v)}
function metricLabel(key:string){return metricInfo[key]?.[0]||key}

export function ResultHistory({series,currentTime,factor=1,unit='',onFrame}:{series:any;currentTime:number;factor?:number;unit?:string;onFrame?:(i:number)=>void}){
 if(!series?.times_s?.length)return <p className="study-note">此结果没有可用的历程数据。</p>;
 const minimum=['contact_gap','fatigue_life'].includes(series.field);
 const values=(series.values||(minimum?series.minimum:series.maximum)||[]).map((v:any)=>v==null?null:Number(v)*factor),valid=values.filter((v:any)=>Number.isFinite(v));
 if(!valid.length)return <p className="study-note">该变量没有有效值；灰色区域不代表零。</p>;
 const min=Math.min(...valid),max=Math.max(...valid),times=series.times_s,t0=times[0],end=times.at(-1),x=(t:number)=>52+500*(t-t0)/Math.max(end-t0,1e-12),y=(v:number)=>130-100*(v-min)/Math.max(max-min,1e-12);
 const segments:string[]=[];let points:string[]=[];values.forEach((value:any,i:number)=>{if(value==null||!Number.isFinite(value)){if(points.length)segments.push(points.join(' '));points=[];}else points.push(`${x(times[i])},${y(value)}`)});if(points.length)segments.push(points.join(' '));
 return <figure className="result-history"><figcaption>{series.label||'场极值'} · {series.values?'节点历程':minimum?'每帧最小值':'每帧最大值'} / {unit} <small>只连接实际保存点</small></figcaption><svg viewBox="0 0 590 170" role="img" aria-label={`${series.label||'结果'}真实时间历程`}><path d="M52 20V130H552" fill="none" stroke="#9aafb7"/>{segments.map((p,i)=><polyline key={i} points={p} stroke="#147d84" strokeWidth="2" fill="none"/>)}<line x1={x(currentTime)} x2={x(currentTime)} y1="20" y2="130" stroke="#bf7140" strokeDasharray="4 3"/><text x="48" y="24" textAnchor="end">{number(max,3)}</text><text x="48" y="134" textAnchor="end">{number(min,3)}</text><text x="52" y="154">{number(t0)} s</text><text x="552" y="154" textAnchor="end">{number(end)} s</text>{values.map((v:any,i:number)=>v!=null&&Number.isFinite(v)?<circle key={i} cx={x(times[i])} cy={y(v)} r="4" fill="#147d84" tabIndex={0} role="button" aria-label={`查看 ${number(times[i])} 秒`} onClick={()=>onFrame?.(i)} onKeyDown={e=>{if(e.key==='Enter')onFrame?.(i)}}><title>{number(times[i])} s: {number(v)} {unit}</title></circle>:null)}</svg></figure>;
}

function LegacyCloud({entry}:{entry:any}){const [data,setData]=useState<any>(null),[geometry,setGeometry]=useState<any>(null),[error,setError]=useState('');useEffect(()=>{let stale=false;Promise.all([api(`/jobs/${entry.id}/display`),api(`/models/${entry.model_id}`)]).then(([d,g])=>{if(!stale){setData(d);setGeometry(g)}}).catch(e=>setError(String(e)));return()=>{stale=true}},[entry.id]);return <div className="run-viewer"><header><h2>{entry.name}</h2><small>原静力 · 非瞬态 · 应力 Pa · 未变形</small></header>{error&&<p>{error}</p>}<div className="run-canvas">{data&&geometry&&<Viewport data={data} geometry={geometry} field="stress" selected={[]} onSelect={()=>{}} deformation={0} animate={false} autoRotate={false} showOriginal={false} meshVisible={false} caseConfig={null} showMarkers={false} slice={null} sliceAxis={2} slicePosition={0} peak={null}/>}</div></div>}
