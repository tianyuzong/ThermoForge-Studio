export function installThermalAnimation({api,post,stopPlayback}){
 const $=id=>document.getElementById(id);
 const dialog=$('thermal-animation-dialog'),view=$('thermal-animation-frame');
 $('thermal-animation-export').title='按默认双视角导出：15秒过程，加2秒末帧停留';
 let context=null,generation=0,poll=null;
 const setStatus=text=>$('thermal-animation-status').textContent=text;
 function close(){generation++;clearTimeout(poll);view.src='about:blank';if(dialog.open)dialog.close();}
 function showExport(state){
  const running=['pending','rendering','encoding'].includes(state.phase);
  $('thermal-animation-export').disabled=running;
  $('thermal-animation-export').textContent=running?'正在导出…':state.phase==='completed'?'重新检查视频':'导出 MP4';
  const ready=state.phase==='completed';$('thermal-animation-video').hidden=!ready;
  if(ready)$('thermal-animation-video').href='/api/jobs/'+encodeURIComponent(context.id)+'/animation/video';
  setStatus((state.detail||'')+(running?' · '+Math.round(state.progress||0)+'%':''));
 }
 async function checkExport(token){
  try{
   const state=await api('/jobs/'+encodeURIComponent(context.id)+'/animation/status');
   if(token!==generation||!dialog.open)return;
   showExport(state);
   if(['pending','rendering','encoding'].includes(state.phase))poll=setTimeout(()=>checkExport(token),1000);
  }catch(error){if(token===generation)setStatus(error.message);}
 }
 $('open-thermal-animation').onclick=async()=>{
  if(!context||context.mode==='steady'||context.frames<2)return;
  stopPlayback();const token=++generation;clearTimeout(poll);
  $('thermal-animation-title').textContent='热扩散动画 · '+context.name;
  $('thermal-animation-export').disabled=true;$('thermal-animation-video').hidden=true;
  $('thermal-animation-html').hidden=true;setStatus('正在读取真实温度场并准备双视角动画…');dialog.showModal();
  try{
   const prefix='/jobs/'+encodeURIComponent(context.id)+'/animation';
   await api(prefix);
   if(token!==generation||!dialog.open)return;
   view.src='/api'+prefix+'/view';
   $('thermal-animation-html').href='/api'+prefix+'/html';$('thermal-animation-html').hidden=false;
   await checkExport(token);
  }catch(error){if(token===generation)setStatus('动画未能载入：'+error.message);}
 };
 $('thermal-animation-export').onclick=async()=>{
  if(!context)return;const token=generation;
  $('thermal-animation-export').disabled=true;setStatus('正在提交视频导出任务…');
  try{
   const state=await post('/jobs/'+encodeURIComponent(context.id)+'/animation/video',{});
   if(token!==generation)return;showExport(state);clearTimeout(poll);
   if(['pending','rendering','encoding'].includes(state.phase))poll=setTimeout(()=>checkExport(token),700);
  }catch(error){if(token===generation){setStatus(error.message);$('thermal-animation-export').disabled=false;}}
 };
 $('thermal-animation-close').onclick=close;
 dialog.addEventListener('cancel',event=>{event.preventDefault();close();});
 dialog.addEventListener('close',()=>{clearTimeout(poll);view.src='about:blank';});
 view.addEventListener('load',()=>{
  // Keyboard events inside the iframe do not bubble to the host dialog.
  view.contentDocument?.addEventListener('keydown',event=>{if(event.key==='Escape'){event.preventDefault();close();}});
 });
 window.addEventListener('message',event=>{if(event.source===view.contentWindow&&event.origin===location.origin&&event.data?.type==='thermal-animation-error')setStatus('动画未能载入：'+event.data.message);});
 return value=>{
  if(context?.id!==value.id&&dialog.open)close();context=value;
  const enabled=value.mode!=='steady'&&value.frames>1;
  $('open-thermal-animation').disabled=!enabled;
  $('open-thermal-animation').title=enabled?'双视角动画、温度曲线与MP4导出':'稳态结果只有平衡状态，不生成时间动画';
 };
}
