// Display observable actions and returned summaries; never invent a model trace.
export function workflowActivity(w) {
  if(w.activity?.length)return w.activity;
  const rows=[];
  if(w.prompt)rows.push({id:'saved-request',actor:'user',text:'已提交仿真目标',legacy:true});
  for(const c of w.cases||[]){
    if(c.agent_prompt)rows.push({id:'saved-'+c.key,actor:'agent',legacy:true,case_key:c.key,
      text:c.name+'：'+(c.status==='needs_input'?'有待补充的问题':'已有Agent配置记录'),
      details:[...(c.changes||[]),...(c.questions||[]),...(c.warnings||[])]});
  }
  if(w.report_ready)rows.push({id:'saved-report',actor:'host',text:'已保存综合报告与计算结果',legacy:true});
  return rows;
}

export function workflowStages(w) {
  const phase=w?.phase,cases=w?.planned_cases||w?.cases||[],configured=!!w?.plan_complete;
  return [
    {label:'Agent拆分工况',state:cases.length||configured?'done':phase==='planning'?'active':'pending'},
    {label:'Agent配置校验',state:configured?'done':phase==='planning'&&cases.length?'active':phase==='needs_input'?'attention':'pending'},
    {label:'执行与结果校核',state:phase==='partial'?'attention':['reporting','completed'].includes(phase)?'done':phase==='running'?'active':'pending'},
    {label:'报告交付',state:w?.report_ready&&['completed','partial'].includes(phase)?'done':phase==='reporting'?'active':'pending'}
  ];
}

export function installAgentPresence(shell) {
  const $=id=>shell.querySelector('#'+id);
  const element=(tag,text,cls)=>{const n=document.createElement(tag);if(text)n.textContent=text;if(cls)n.className=cls;return n;};
  const heading=shell.querySelector('.agent-heading'),brand=heading.firstElementChild;
  brand.classList.add('workflow-brand');brand.prepend(element('b','A','workflow-avatar'));
  const badge=element('span','Agent待命','workflow-agent-badge');badge.id='workflow-agent-badge';heading.insertBefore(badge,$('workflow-close'));
  brand.querySelector('span').textContent='Agent规划与配置 · 宿主计算与交付';
  const prompt=$('workflow-prompt');prompt.rows=3;prompt.parentElement.firstChild.textContent='给Agent的仿真目标';
  $('workflow-plan').textContent='交给Agent规划';$('workflow-plan').classList.remove('wide');
  const submitRow=element('div',null,'workflow-submit-row'),hint=shell.querySelector('.workflow-sheet > p.hint');
  hint.textContent='先由Agent生成可检查的计划，整体确认后开始计算。';
  $('workflow-plan').before(submitRow);submitRow.append(hint,$('workflow-plan'));
  $('workflow-retry').textContent='让Agent继续校验';$('workflow-recheck').textContent='让Agent重新校验全部';
  const stages=element('ol',null,'workflow-stages');stages.id='workflow-stages';stages.setAttribute('aria-label','任务阶段');$('workflow-status').before(stages);
  const workbench=element('div',null,'workflow-workbench');
  const panel=element('section',null,'workflow-agent-panel');panel.setAttribute('aria-labelledby','workflow-log-title');
  const logHeading=element('div',null,'workflow-panel-heading'),logTitle=element('h3','Agent工作记录'),logCount=element('span','等待任务');logTitle.id='workflow-log-title';logHeading.append(logTitle,logCount);
  const log=element('div',null,'workflow-agent-log');log.id='workflow-agent-log';
  panel.append(logHeading,element('p','实际调用与返回摘要，计算和报告由宿主执行。','hint workflow-log-note'),log);
  const planPanel=element('section',null,'workflow-plan-panel'),planHeading=element('div',null,'workflow-panel-heading'),caseCount=element('span','尚未生成');
  planHeading.append(element('h3','Agent工况计划'),caseCount);planPanel.append(planHeading);
  $('workflow-cases').before(workbench);workbench.append(panel,planPanel);planPanel.append($('workflow-questions'),$('workflow-cases'));
  const evidence=element('details',null,'workflow-evidence-box');evidence.append(element('summary','模型依据与分析范围'));workbench.after(evidence);evidence.append($('workflow-evidence'));
  const limits=element('p',null,'hint');limits.id='workflow-limitations';evidence.append(limits);
  const footer=element('div',null,'workflow-footer');shell.querySelector('.workflow-actions').before(footer);footer.append(shell.querySelector('.workflow-actions'),$('workflow-files'));
  let signature='';
  function render(w){
    const active=['planning','running','reporting'].includes(w?.phase),events=w?workflowActivity(w):[];
    badge.textContent=({planning:'Agent工作中',needs_input:'等待你补充',ready:'等待整体确认',running:'宿主执行中',reporting:'正在交付报告',completed:'任务完成',partial:'部分完成',failed:'任务未完成',cancelled:'已取消',interrupted:'任务中断'})[w?.phase]||'Agent待命';badge.dataset.active=String(active);
    stages.replaceChildren();workflowStages(w).forEach((s,i)=>{const li=element('li',(s.state==='done'?'✓':i+1)+' '+s.label);li.dataset.state=s.state;stages.append(li);});
    const cases=w?.cases||[],ready=cases.filter(c=>['ready','completed'].includes(c.status)).length;
    const total=w?.planned_cases?.length||cases.length;
    caseCount.textContent=total?total+'个工况 · '+ready+'个就绪或完成':'等待Agent拆分';
    const questions=w?.questions||[];$('workflow-questions').textContent=questions.length?'Agent需要补充的信息：\n'+questions.join('\n'):'';$('workflow-questions').hidden=!questions.length;
    limits.textContent=(w?.limitations||[]).join('\n');
    const stamp=JSON.stringify([w?.id,w?.phase,events]);if(stamp===signature)return;signature=stamp;
    const following=log.scrollHeight-log.scrollTop-log.clientHeight<50,oldScroll=log.scrollTop;
    const expanded=new Set(Array.from(log.querySelectorAll('details[open]')).map(x=>x.dataset.id));log.replaceChildren();
    logCount.textContent=events.length?(events[0].legacy?'来自已保存的记录':events.length+'条实际记录'):'等待任务';
    if(!events.length){log.append(element('p','Agent会先拆分工况，再逐项配置和校验。参数改动、待补充问题与执行记录会显示在这里。','workflow-welcome'));return;}
    if(events[0].legacy)log.append(element('p','历史任务未记录逐步调用时间；以下摘要来自已保存的Agent配置。','workflow-history-note'));
    if(w.activity_omitted)log.append(element('p','显示最近200条记录，更早的活动已省略。','workflow-history-note'));
    for(const [index,event] of events.entries()){
      const article=element('article',null,'workflow-message');article.dataset.actor=event.actor;article.dataset.status=event.status||'completed';
      const header=element('div',null,'workflow-message-heading');
      let who={agent:'配置Agent',host:'仿真宿主',user:'你'}[event.actor]||'任务记录';if(event.actor==='agent'&&event.stage==='planning')who='规划Agent';
      header.append(element('strong',who),element('small',event.at?new Date(event.at*1000).toLocaleTimeString('zh-CN',{hour12:false}):'已保存记录'));
      article.append(header,element('p',event.text));
      article.classList.toggle('working',index===events.length-1&&active&&event.status==='started');
      if(event.details?.length){
        const detail=element('details'),summary=element('summary',event.status==='needs_input'?'查看待补充问题':'查看返回摘要（'+event.details.length+'）'),list=element('ul');
        detail.dataset.id=event.id;detail.open=expanded.has(event.id)||event.status==='needs_input';
        for(const text of event.details)list.append(element('li',text));detail.append(summary,list);article.append(detail);
      }
      log.append(article);
    }
    if(following)log.scrollTop=log.scrollHeight;else log.scrollTop=oldScroll;
  }
  render(null);return {render};
}
