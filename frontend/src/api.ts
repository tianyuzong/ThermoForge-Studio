export async function api<T=any>(path:string,init:RequestInit={}):Promise<T>{
 const r=await fetch('/api/v1'+path,{...init,headers:{...(init.body instanceof FormData?{}:{'Content-Type':'application/json'}),...init.headers}});
 if(!r.ok){const j=await r.json().catch(()=>({detail:r.statusText}));throw new Error(typeof j.detail==='string'?j.detail:JSON.stringify(j.detail));}
 return r.json();
}
export const post=(path:string,body:any={})=>api(path,{method:'POST',body:JSON.stringify(body)});
export const uid=()=>crypto.randomUUID();
export const statusLabel:Record<string,string>={parsing:'理解指令',needs_input:'等待补充',validating:'检查工况',queued:'排队中',meshing:'生成网格',solving:'求解中',verifying:'检查结果',analyzing:'分析结果',reporting:'生成报告',completed:'已完成',partial:'部分完成',failed:'未完成',cancelled:'已取消',interrupted:'已中断',draft:'配置草案'};
export const settled=new Set(['completed','partial','failed','cancelled','interrupted','draft','needs_input']);
export const fields:Record<string,{label:string,unit:string,factor:number,group?:string}>={contact_pressure:{label:'接触压力（离散显示）',unit:'MPa',factor:1e-6},elastic_displacement:{label:'弹性变形（不含轴转角）',unit:'mm',factor:1000},stress:{label:'等效应力',unit:'MPa',factor:1e-6},displacement:{label:'总位移',unit:'mm',factor:1000},principal_max:{label:'最大主应力',unit:'MPa',factor:1e-6},principal_min:{label:'最小主应力',unit:'MPa',factor:1e-6},strain:{label:'最大绝对正应变',unit:'%',factor:100},safety_factor:{label:'屈服安全系数',unit:'',factor:1}};
export function number(v:number|undefined|null,digits=4){if(v==null||!Number.isFinite(v))return '—';if(v!==0&&(Math.abs(v)<1e-4||Math.abs(v)>=1e7))return v.toExponential(digits-1);return Number(v.toPrecision(digits)).toLocaleString('en-US',{maximumSignificantDigits:digits});}

export async function loadFieldCatalog(){const catalog=await api('/results/catalog');for(const f of catalog.fields)fields[f.key]={label:f.label,unit:f.display_unit,factor:f.factor,group:f.group};}
