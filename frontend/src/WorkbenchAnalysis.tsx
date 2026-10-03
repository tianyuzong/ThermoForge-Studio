import {useEffect,useRef,useState} from 'react';
import {api,post,uid,statusLabel} from './api';
import {RunViewer,PROMPT_TEMPLATE} from './RunPanel';
import Viewport from './Viewport';
import {ExportActions,QueueStatus} from './AnalysisWidgets';
import './workbench-analysis.css';
import {flushPendingDraft} from './draftStore';

const terminal=new Set(['completed','partial','failed','cancelled','interrupted']);
const planning=new Set(['queued','planning']);
const names:Record<string,string>={...statusLabel,ready:'配置就绪',submitted:'已提交',planning:'解析指令',pending:'待执行',running:'进行中',blocked:'依赖未完成',exporting:'正在导出'};
export const analysisStatus=(status:string)=>names[status]||status;
const storageKey=(project:any)=>`studio-workbench-analysis:${project.id}:${project.model_id}`;
export const hasMultiphysicsIntent=(text:string)=>/全套分析|力学热学全仿|一键全仿|多物理场|热分析|热学|热结构|稳态|瞬态|疲劳|周期接触|磨损|视频|运行已保存研究/.test(text);

/** Main-workbench state; plans and runs are persisted by the existing server APIs. */
export function useWorkbenchAnalysis(project:any,onShow:()=>void){
 const [plans,setPlans]=useState<any[]>([]),[runs,setRuns]=useState<any[]>([]);
 const [plan,setPlan]=useState<any>(null),[run,setRun]=useState<any>(null);
 const [enabled,setEnabled]=useState(true),[selected,setSelected]=useState('');
 const [busy,setBusy]=useState(false),[error,setError]=useState('');
 const identity=project?.id+':'+project?.model_id;
 const current=useRef(identity);current.current=identity;
 const show=useRef(onShow);show.current=onShow;
 const request=useRef({text:'',id:uid()}),submitting=useRef(false),selection=useRef('');
 const planRef=useRef<any>(null),runRef=useRef<any>(null);planRef.current=plan;runRef.current=run;
 const remember=(value:any)=>{if(project?.model_id)localStorage.setItem(storageKey(project),JSON.stringify(value));};
 const validPlan=(p:any)=>p?.project_id===project?.id&&p?.model_id===project?.model_id;
 const validRun=(r:any)=>r?.origin_project_id===project?.id&&r.nodes?.some((n:any)=>n.model_id===project?.model_id);
 function selectNode(key:string,r=run,p=plan){setSelected(key);selection.current=key;remember({plan:p?.id,run:r?.id,node:key});show.current();}
 function acceptRun(r:any,p=plan,activate=true){
  setRun(r);setEnabled(true);setRuns(old=>[r,...old.filter(x=>x.id!==r.id)]);
  const key=r.nodes.some((n:any)=>n.key===selection.current)?selection.current:r.nodes[0]?.key||'';
  setSelected(key);selection.current=key;remember({plan:p?.id,run:r.id,node:key});if(activate)show.current();
 }
 async function choosePlan(p:any){
  try{
  if(!validPlan(p))throw Error('该指令属于其他项目或几何版本');
  const scope=current.current;setEnabled(true);setPlan(p);setRun(null);setError('');
  remember({plan:p.id});show.current();
  if(p.run_id){const r=await api('/analysis-runs/'+p.run_id);if(current.current===scope&&planRef.current?.id===p.id)acceptRun(r,p);}
  }catch(e){setError(String(e));}
 }
 async function chooseRun(r:any){
  try{
  if(!validRun(r))throw Error('该批次不属于当前项目和模型');
  const scope=current.current;setPlan(null);acceptRun(r,null);
  if(r.plan_id){const p=await api('/analysis-plans/'+r.plan_id);if(current.current===scope&&runRef.current?.id===r.id)setPlan(p);}
  }catch(e){setError(String(e));}
 }
 function clear(fresh=true){setEnabled(fresh);setPlan(null);setRun(null);setError('');remember({mode:fresh?'new':'legacy'});request.current={text:'',id:uid()};}
 useEffect(()=>{
  const scope=identity;let stale=false;setPlans([]);setRuns([]);setPlan(null);setRun(null);setEnabled(true);setSelected('');selection.current='';setError('');
  if(!project?.model_id)return;
  (async()=>{
   const [ps,rs]=await Promise.all([api('/projects/'+project.id+'/analysis-plans?limit=1000'),api('/analysis-runs?project_id='+project.id+'&limit=1000')]);
   if(stale||current.current!==scope)return;
   const availablePlans=ps.filter(validPlan),availableRuns=rs.filter(validRun);setPlans(availablePlans);setRuns(availableRuns);
   let saved:any={};try{saved=JSON.parse(localStorage.getItem(storageKey(project))||'{}');}catch{}
   if(saved.mode){setEnabled(saved.mode==='new');return;}
   const p=availablePlans.find((v:any)=>v.id===saved.plan)||availablePlans[0];
   const r=availableRuns.find((v:any)=>v.id===(saved.run||p?.run_id))||(!p?availableRuns[0]:null);
   selection.current=saved.node||'';
   if(p){setPlan(p);setEnabled(true);}if(r)acceptRun(r,p);else if(p)show.current();
  })().catch(e=>{if(!stale)setError(String(e));});
  return()=>{stale=true;};
 },[project?.id,project?.model_id]);
 useEffect(()=>{
  if(!plan||!planning.has(plan.status))return;
  const scope=identity;let stale=false,running=false;
  const timer=setInterval(async()=>{
   if(running)return;running=true;
   try{const p=await api('/analysis-plans/'+plan.id);
    const r=p.run_id?await api('/analysis-runs/'+p.run_id):null;
    if(stale||current.current!==scope)return;
    setPlan(p);setPlans(old=>[p,...old.filter(x=>x.id!==p.id)]);
    if(r)acceptRun(r,p);
   }catch(e){if(!stale)setError(String(e));}finally{running=false;}
  },1600);return()=>{stale=true;clearInterval(timer);};
 },[plan?.id,plan?.status,identity]);
 useEffect(()=>{
  if(!run||terminal.has(run.status))return;
  const scope=identity;let stale=false,running=false;
  const timer=setInterval(async()=>{if(running)return;running=true;try{const r=await api('/analysis-runs/'+run.id);if(!stale&&current.current===scope){setRun(r);setRuns(old=>[r,...old.filter(x=>x.id!==r.id)]);}}catch(e){if(!stale)setError(String(e));}finally{running=false;}},2000);
  return()=>{stale=true;clearInterval(timer);};
 },[run?.id,run?.status,identity]);
 async function submit(text:string,provider:string,draft:boolean){
  if(submitting.current||!text.trim()||!project?.model_id)return false;
  const scope=identity;submitting.current=true;setBusy(true);setError('');
  const signature=JSON.stringify([scope,text,provider,draft,plan?.status==='needs_input'?plan.id:null]);
  if(signature!==request.current.text)request.current={text:signature,id:uid()};
  try{await flushPendingDraft(project);const p=await post(`/projects/${project.id}/analysis-plans`,{request_id:request.current.id,prompt:text,provider,execute:!draft,force:false,previous_plan_id:plan?.status==='needs_input'?plan.id:null});
   if(current.current!==scope)return true;setPlan(p);setPlans(old=>[p,...old.filter(x=>x.id!==p.id)]);setRun(null);setEnabled(true);remember({plan:p.id});show.current();
   if(p.run_id){const r=await api('/analysis-runs/'+p.run_id);if(current.current===scope)acceptRun(r,p);}
   return true;
  }catch(e){if(current.current===scope)setError(String(e));return false;}finally{submitting.current=false;setBusy(false);}
 }
 async function action(kind:'start'|'resume'|'cancel'){
  const scope=identity;setBusy(true);setError('');
  try{
   if(kind==='start'&&plan){const r=await post('/analysis-runs',{request_id:'workbench-plan:'+plan.id,project_id:project.id,plan_id:plan.id});if(current.current===scope)acceptRun(r);}
   else if(run){const r=await post(`/analysis-runs/${run.id}/${kind}`);if(current.current===scope)setRun(r);}
   else if(plan&&kind==='cancel'){const p=await post(`/analysis-plans/${plan.id}/cancel`);if(current.current===scope)setPlan(p);}
  }catch(e){if(current.current===scope)setError(String(e));}finally{setBusy(false);}
 }
 const active=busy||!!(plan&&planning.has(plan.status))||!!(run&&!terminal.has(run.status));
 const node=run?.nodes?.find((n:any)=>n.key===selected)||run?.nodes?.[0];
 return {plan,run,plans,runs,node,enabled,active,busy,error,dismissError:()=>setError(''),selectNode,choosePlan,chooseRun,clear,submit,action,setRun};
}
type Controller=ReturnType<typeof useWorkbenchAnalysis>;

export function WorkbenchAnalysisResults({analysis,geometry,modelData,onModel}:{analysis:Controller;geometry:any;modelData:any;onModel:()=>void}){
 const {run,plan,node}=analysis;
 return <div className="workbench-analysis-result" aria-label="主界面多物理场结果">
  <div className="workbench-analysis-heading"><div><small>统一仿真工作台</small><h1>{run?.title||plan?.decision?.title||'配置与计算进度'}</h1></div><button onClick={onModel}>模型与选区</button></div>
  {run&&<label className="workbench-case-selector">当前工况<select aria-label="主界面分析工况" value={node?.key||''} onChange={e=>analysis.selectNode(e.target.value)}>{run.nodes.map((n:any)=><option key={n.key} value={n.key}>{n.label} · {analysisStatus(n.status)}{n.reused?' · 已核验复用':''}</option>)}</select></label>}
  {run&&node?.study_id?<RunViewer key={node.study_id} run={run} node={node}/>:<><p className="workbench-stage">{node?.detail||run?.detail||plan?.message||'在右侧描述分析目标和边界条件，条件齐全后自动执行。'}</p>{geometry&&modelData&&<div className="workbench-pending-model"><Viewport data={modelData} field="stress" selected={[]} onSelect={()=>{}} deformation={0} animate={false} showOriginal={false} autoRotate={false} meshVisible={false} geometry={geometry} caseConfig={null} showMarkers={false} slice={null} sliceAxis={2} slicePosition={0} peak={null}/></div>}</>}
 </div>;
}

export function WorkbenchAnalysisAgent({analysis,panel,onTemplate}:{analysis:Controller;panel:string;onTemplate:()=>void}){
 const {plan,run}=analysis;
 if(panel==='history')return <div className="history-list"><h3>多物理场指令与批次</h3>{analysis.plans.map(p=><button key={p.id} onClick={()=>analysis.choosePlan(p)}><span>{p.decision?.title||p.prompt.slice(0,70)}</span><small>{analysisStatus(p.status)} · {new Date(p.created_at*1000).toLocaleString()}</small></button>)}{analysis.runs.filter(r=>!analysis.plans.some(p=>p.run_id===r.id)).map(r=><button key={r.id} onClick={()=>analysis.chooseRun(r)}><span>{r.title}</span><small>{analysisStatus(r.status)}</small></button>)}{!analysis.plans.length&&!analysis.runs.length&&<p>当前项目还没有多物理场记录。</p>}</div>;
 if(panel==='activity')return <div className="workbench-analysis-log"><h3>执行记录</h3>{plan&&<p>指令 {plan.id.slice(0,8)} · {analysisStatus(plan.status)}<br/>{plan.message}</p>}{run&&<><p>批次 {run.id.slice(0,8)} · {analysisStatus(run.status)}<br/>{run.detail}</p>{run.nodes.map((n:any)=><article key={n.key}><b>{n.label} · {analysisStatus(n.status)}</b><p>{n.detail||''}</p><small>{n.reused?'已核验复用':'本批次求解'} · {n.study_id||'尚未建立研究'}</small></article>)}{run.exports?.map((e:any)=><p key={e.key}>导出 {run.nodes.find((n:any)=>n.key===e.key)?.label||e.key} · {analysisStatus(e.status)} {e.detail||''}</p>)}</>}{!run&&!plan&&<p>解析、求解、导出及失败诊断会记录在此处。</p>}</div>;
 return <div className="workbench-analysis-chat">
  {!plan&&!run?<div className="agent-intro"><span className="eyebrow">ONE WORKSPACE</span><h3>描述完整工况，<br/>在这里完成仿真。</h3><p>支持力学、热学、疲劳和接触耦合。任务进度在右侧，模型、云图和时间播放保留在中央。</p><button className="prompt-example" onClick={onTemplate}>填入通用仿真指令模板</button><p>采用当前项目的真实材料、选区和保存工况；缺少条件时集中列出。</p></div>:<>
  <div className="workbench-analysis-state" role="status"><b>{analysisStatus(run?.status||plan?.status)}</b><p>{run?.detail||plan?.message}</p>{run&&<small>{run.nodes.filter((n:any)=>n.status==='completed').length} / {run.nodes.length} 项分析 · {run.exports?.filter((e:any)=>e.status==='completed').length||0} / {run.nodes.length} 项导出</small>}</div>
  <details className="workbench-user-prompt"><summary>你 · {(plan?.prompt||run?.prompt||'').slice(0,70)}</summary><div>{plan?.prompt||run?.prompt}</div></details>
  {plan&&<details className="workbench-plan-notes" open={!run}><summary>建模说明与适用范围</summary><div className="message assistant"><div className="message-label">THERMOFORGE AGENT</div><div className="message-content">{plan.decision?.message||plan.message}</div></div></details>}

  {plan?.questions?.length>0&&<div className="questions">{plan.questions.map((q:string,i:number)=><p key={i}>{q}</p>)}</div>}
  {plan?.nodes?.length>0&&<details className="case-summary"><summary>查看已解析的 {plan.nodes.length} 项工况</summary>{plan.nodes.map((n:any)=><details key={n.key}><summary>{n.label}</summary><p>继承来源：{n.source_case_id||'完整新工况'}</p>{n.changes?.length>0&&<div className="plan-diff"><b>有效配置变更</b>{n.changes.map((change:any,i:number)=><p key={i}><code>{change.path}</code><span>{JSON.stringify(change.before)??'未设置'} → {JSON.stringify(change.after)??'未设置'}</span></p>)}</div>}<pre>{JSON.stringify(n.case,null,2)}</pre></details>)}</details>}
  <QueueStatus/><div className="workbench-analysis-actions">{plan?.status==='ready'&&!run&&<button disabled={analysis.busy} onClick={()=>analysis.action('start')}>按此方案开始计算</button>}{analysis.active&&<button disabled={analysis.busy} onClick={()=>analysis.action('cancel')}>取消当前任务</button>}{run&&terminal.has(run.status)&&run.status!=='completed'&&<button disabled={analysis.busy} onClick={()=>analysis.action('resume')}>继续未完成任务 / 重试导出</button>}</div>
  <ExportActions run={run} onUpdated={analysis.setRun}/>
  {(run?.artifacts||run?.export_generation)&&<div className="task-downloads"><a href={`/api/v1/analysis-runs/${run.id}/files/summary.pdf`} target="_blank" rel="noreferrer">详细图文报告</a><a href={`/api/v1/analysis-runs/${run.id}/files/bundle.zip`}>下载全部结果 ZIP</a><a href={`/api/v1/analysis-runs/${run.id}/files/metrics.csv`}>汇总指标 CSV</a></div>}
  {run?.nodes?.map((n:any)=><button className="workbench-result-link" key={n.key} onClick={()=>analysis.selectNode(n.key)}><span>{n.label}</span><small>{analysisStatus(n.status)} · 查看云图与动画</small></button>)}
  <p className="agent-note">计算和导出在后台执行，关闭网页后仍会继续。复用来源、材料假设和验证范围随报告保存。</p>
  </>}
 </div>;
}
export {PROMPT_TEMPLATE};
