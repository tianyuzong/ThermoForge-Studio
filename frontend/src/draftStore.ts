import {api} from './api';
export const draftStorageKey=(project:any,kind:string)=>`studio-draft:${project?.id}:${project?.model_id}:${kind}`;
export function chooseDraft(server:any,local:any,modelId:string){
 if(!local?.case||local.model_id!==modelId||!local.dirty)return {chosen:server,conflict:false};
 return {chosen:local,conflict:!local.adopted&&(local.revision||0)!==(server.revision||0)};
}
/** Commit the latest local edit before a new prompt reads the server's current case. */
export async function flushPendingDraft(project:any){
 const prefix=`studio-draft:${project.id}:${project.model_id}:`,pending:any[]=[];
 for(let i=0;i<localStorage.length;i++){const key=localStorage.key(i);if(!key?.startsWith(prefix))continue;try{const value=JSON.parse(localStorage.getItem(key)||'null');if(value?.dirty)pending.push({...value,key})}catch{}}
 pending.sort((a,b)=>(b.edited_at||0)-(a.edited_at||0));const draft=pending[0];if(!draft)return;
 const kind=draft.case.analysis_type,server=await api(`/projects/${project.id}/analysis-draft?analysis_type=${kind}`);
 if(chooseDraft(server,draft,project.model_id).conflict)throw Error('当前草稿在其他页面已更新；请打开多物理场研究，选择保留本机或服务器版本后再提交指令。');
 const saved=await api(`/projects/${project.id}/analysis-draft?analysis_type=${kind}`,{method:'PUT',body:JSON.stringify({case:draft.case,expected_revision:server.revision,base_case_id:draft.base_case_id||null,parameter_sources:draft.parameter_sources||{}})});
 const current=JSON.parse(localStorage.getItem(draft.key)||'{}');if(current.edited_at===draft.edited_at)localStorage.setItem(draft.key,JSON.stringify({...saved,dirty:false}));
}
