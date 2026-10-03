import {installAgentPresence} from './workflow-agent-ui.js';

export function installWorkflows({getContext,openJob,refreshLibrary}) {
  const shell=document.createElement('div');shell.className='agent-dialog';shell.hidden=true;
  shell.innerHTML=`<div class="agent-sheet workflow-sheet" role="dialog" aria-modal="true" aria-labelledby="workflow-title">
    <div class="agent-heading"><div><h2 id="workflow-title">多工况任务</h2><span>Agent配置 · 依赖续算 · 结果校核 · 综合报告</span></div><button id="workflow-close" aria-label="关闭多工况任务">×</button></div>
    <label class="field">历史任务<select id="workflow-history"><option value="">新任务</option></select></label>
    <label class="field">完整仿真目标<textarea id="workflow-prompt" rows="7" maxlength="24000" placeholder="使用当前模型，说明材料、热源选区、正常工况、故障或循环工况，以及报告指标。Agent将拆分并校验各工况。"></textarea></label>
    <p class="hint">使用当前已导入模型。生成计划后可检查每个工况的参数；整体确认后按依赖执行，配置相同的已完成结果会复用。未支持的物理分析会明确列出。</p>
    <button class="wide primary" id="workflow-plan">生成多工况计划</button>
    <p id="workflow-status" role="status" aria-live="polite"></p><div id="workflow-evidence" class="hint"></div>
    <div id="workflow-questions" class="agent-questions"></div><div id="workflow-cases"></div>
    <div class="workflow-actions"><button id="workflow-retry" disabled>继续校验计划</button><button id="workflow-recheck" disabled>重新校验全部</button><button id="workflow-start" class="primary" disabled>确认并执行全部工况</button><button id="workflow-cancel" disabled>取消任务</button></div>
    <div id="workflow-files" class="result-downloads"></div></div>`;
  document.body.append(shell);
  const agentView=installAgentPresence(shell);
  const $=id=>shell.querySelector('#'+id);
  const button=document.createElement('button');button.textContent='多工况任务';button.id='workflow-open';
  document.getElementById('agent-open').after(button);
  let id=null,timer=null,generation=0,busy=false,dirty=false,state=null;
  const labels={planning:'Agent正在规划与配置',needs_input:'Agent需要补充',ready:'Agent计划已校验',running:'宿主正在执行',reporting:'宿主正在汇总报告',completed:'计算及报告已完成',partial:'部分完成',failed:'执行失败',interrupted:'任务中断',cancelled:'已取消'};
  async function api(path,body){const r=await fetch('/api/workflows'+path,{method:body===undefined?'GET':'POST',headers:body===undefined?{}:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});const v=await r.json();if(!r.ok)throw Error(typeof v.detail==='string'?v.detail:JSON.stringify(v.detail));return v;}
  function message(text,error=false){$('workflow-status').textContent=text;$('workflow-status').classList.toggle('error',error);}
  function stop(){clearTimeout(timer);timer=null;}
  async function history(){const list=await api('');$('workflow-history').replaceChildren(new Option('新任务',''));for(const w of list)$('workflow-history').append(new Option(w.name+' · '+(labels[w.phase]||w.phase),w.id));$('workflow-history').value=id||'';}
  function link(label,url){const a=document.createElement('a');a.textContent=label;a.href=url;a.target='_blank';a.rel='noopener';return a;}
  function render(w){
    state=w;message((labels[w.phase]||w.phase)+'：'+w.detail);
    const option=Array.from($('workflow-history').options).find(x=>x.value===w.id);if(option)option.textContent=w.name+' · '+(labels[w.phase]||w.phase);
    $('workflow-evidence').textContent=w.geometry?`模型：${w.geometry.name} · 尺寸 ${w.geometry.dimensions_m.map(x=>(x*1000).toFixed(3)).join(' × ')} mm · 封闭表面：${w.geometry.topology.closed?'是':'否'} · 方向校正面数 ${w.geometry.topology.reoriented_faces}`:'';
    agentView.render(w);
    const expanded=new Set(Array.from($('workflow-cases').children).filter(x=>x.open).map(x=>x.dataset.key));
    const instructionsOpen=new Set(Array.from($('workflow-cases').children).filter(x=>x.querySelector('details')?.open).map(x=>x.dataset.key));
    const caseScroll=$('workflow-cases').scrollTop;
    $('workflow-cases').replaceChildren();
    const displayCases=w.planned_cases?.length?w.planned_cases.map(spec=>w.cases.find(c=>c.key===spec.key)||{...spec,status:'pending_review'}):w.cases;
    for(const c of displayCases){
      const card=document.createElement('details'),title=document.createElement('summary'),body=document.createElement('pre');
      card.dataset.key=c.key;card.open=expanded.has(c.key);
      const caseStatus={ready:'Agent已校验',pending_review:'等待Agent校验',needs_input:'Agent需要补充',queued:'排队中',running:'正在计算',verifying:'正在校核',completed:'已完成',failed:'未完成',skipped:'已跳过'};
      const parent=displayCases.find(x=>x.key===c.initial_from)?.name||c.initial_from;
      title.textContent=`${c.name} · ${caseStatus[c.status]||c.status}${c.reused?' · 复用结果':''}${c.initial_from?' · 温度场来自 '+parent:''}`;
      body.textContent=['Agent配置改动：',...(c.changes?.length?c.changes:[c.status==='pending_review'?'等待配置Agent返回。':'沿用配置，无额外参数改动。']),...(c.questions||[]),...(c.warnings||[]),c.detail||''].filter(Boolean).join('\n');card.append(title,body);
      const instruction=document.createElement('details'),instructionTitle=document.createElement('summary'),instructionBody=document.createElement('pre');instruction.open=instructionsOpen.has(c.key);instructionTitle.textContent='查看交给配置Agent的指令';instructionBody.textContent=c.agent_prompt||c.prompt;instruction.append(instructionTitle,instructionBody);card.append(instruction);
      if(c.status==='completed'&&c.job_id){const show=document.createElement('button');show.textContent='查看 '+c.name+' 结果';show.onclick=async()=>{try{await openJob(c.job_id);shell.hidden=true;stop();}catch(e){message(e.message,true);}};card.append(show);}
      $('workflow-cases').append(card);
    }
    $('workflow-cases').scrollTop=caseScroll;
    const active=['planning','running','reporting'].includes(w.phase);
    $('workflow-retry').disabled=dirty||active||w.plan_complete;
    $('workflow-recheck').disabled=dirty||active||!w.cases.length;
    $('workflow-start').disabled=dirty||!w.plan_complete||!['ready','failed','interrupted','cancelled','partial'].includes(w.phase);
    $('workflow-start').textContent=['failed','interrupted','cancelled','partial'].includes(w.phase)?'继续执行未完成工况':'确认并执行全部工况';
    $('workflow-cancel').disabled=!active;$('workflow-plan').disabled=busy||active;
    $('workflow-files').replaceChildren(link('计划及配置JSON','/api/workflows/'+id+'/files/workflow.json'));
    if(w.report_ready){for(const [label,name] of [['综合PDF','report.pdf'],['指标CSV','comparison.csv'],['报告与配置包','reports.zip'],['原始结果索引','manifest.json']])$('workflow-files').append(link(label,'/api/workflows/'+id+'/files/'+name));}
  }
  async function poll(token=generation){
    if(!id||token!==generation)return;
    try{const w=await api('/'+id);if(token!==generation)return;render(w);if(['planning','running','reporting'].includes(w.phase)&&!shell.hidden)timer=setTimeout(()=>poll(token),2500);else if(['completed','partial'].includes(w.phase))await refreshLibrary();}
    catch(e){if(token===generation)message(e.message,true);}
  }
  button.onclick=async()=>{shell.hidden=false;try{await history();if(id)await poll();}catch(e){message(e.message,true);}};
  $('workflow-close').onclick=()=>{shell.hidden=true;stop();};
  $('workflow-prompt').oninput=()=>{dirty=true;$('workflow-start').disabled=true;$('workflow-retry').disabled=true;$('workflow-recheck').disabled=true;};
  $('workflow-history').onchange=async()=>{stop();const token=++generation;id=$('workflow-history').value||null;dirty=false;state=null;
    if(!id){agentView.render(null);$('workflow-cases').replaceChildren();$('workflow-files').replaceChildren();$('workflow-evidence').textContent='';$('workflow-prompt').value='';for(const key of ['workflow-start','workflow-retry','workflow-recheck','workflow-cancel'])$(key).disabled=true;$('workflow-plan').disabled=busy;message('输入完整目标后交给Agent规划');return;}
    try{const w=await api('/'+id);if(token!==generation)return;$('workflow-prompt').value=w.prompt;render(w);await poll(token);}catch(e){if(token===generation)message(e.message,true);}};
  $('workflow-plan').onclick=async()=>{const prompt=$('workflow-prompt').value.trim();if(!prompt)return message('请填写完整仿真目标',true);let context;
    try{context=getContext();if(!context.model_id)throw Error('请先导入并选择模型');}catch(e){return message(e.message,true);}
    busy=true;$('workflow-plan').disabled=true;stop();const token=++generation;
    try{const created=await api('/plan',{...context,prompt});if(token!==generation)return;id=created.id;dirty=false;await history();await poll(token);}catch(e){message(e.message,true);}finally{busy=false;$('workflow-plan').disabled=state&&['planning','running','reporting'].includes(state.phase);}
  };
  $('workflow-start').onclick=async()=>{if(!id||dirty)return;$('workflow-start').disabled=true;try{await api('/'+id+'/start',{});await poll();}catch(e){message(e.message,true);$('workflow-start').disabled=false;}};
  $('workflow-retry').onclick=async()=>{if(!id||dirty)return;$('workflow-retry').disabled=true;try{await api('/'+id+'/retry-plan',{});await poll();}catch(e){message(e.message,true);}};
  $('workflow-recheck').onclick=async()=>{if(!id||dirty)return;$('workflow-recheck').disabled=true;try{await api('/'+id+'/recheck',{});await poll();}catch(e){message(e.message,true);}};
  $('workflow-cancel').onclick=async()=>{if(!id)return;try{await api('/'+id+'/cancel',{});await poll();}catch(e){message(e.message,true);}};
}
