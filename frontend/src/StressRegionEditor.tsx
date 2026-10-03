import {Plus,Trash2} from 'lucide-react';

export function StressRegionEditor({value,change,geometry}:{value:any[];change:(v:any[])=>void;geometry:any}){
 const bodies=geometry.components||[];
 const update=(i:number,patch:any)=>change(value.map((r,j)=>i===j?{...r,...patch}:r));
 return <>
  <p className="muted">先在三维视图选中真实齿根面并保存命名选区，再为各组件添加评估项。最大主应力或指定方向正应力从选面相邻单元的原始积分点提取，报告自动生成局部放大图和数值表。</p>
  <p className="warning-strip">这是局部应力提取，尚未分离截面膜应力与弯曲分量，不能据此判定齿根弯曲或疲劳强度合格。</p>
  {value.map((region,i)=>{
   const body=bodies.find((b:any)=>b.id===region.selection.body_id);
   const valid=new Set(body?.face_ids||[]);
   const available=Object.entries({...geometry.named_selections,...body?.named_selections}).filter(([,ids])=>Array.isArray(ids)&&ids.some((id:number)=>valid.has(id))).map(([name])=>name);
   return <section className="edit-card" key={i}>
    <div className="row"><label className="form-field">评估名称<input aria-label={`局部应力名称 ${i+1}`} value={region.name} onChange={e=>update(i,{name:e.target.value})}/></label><button className="icon-button" aria-label={`删除局部应力 ${i+1}`} onClick={()=>change(value.filter((_,j)=>j!==i))}><Trash2 size={16}/></button></div>
    <div className="form-grid">
     <label className="form-field">组件<select aria-label={`局部应力组件 ${i+1}`} value={region.selection.body_id??''} onChange={e=>update(i,{selection:{kind:'named',name:'',body_id:e.target.value?Number(e.target.value):null,face_ids:[]}})}><option value="">请选择组件</option>{bodies.map((b:any)=><option key={b.id} value={b.id}>{b.name}</option>)}</select></label>
     <label className="form-field">评估选区<select aria-label={`局部应力选区 ${i+1}`} disabled={!body} value={region.selection.kind==='faces'?'__faces':region.selection.name||''} onChange={e=>update(i,{selection:{...region.selection,kind:'named',name:e.target.value,face_ids:[]}})}><option value="">请选择真实评估面</option>{region.selection.kind==='faces'&&<option value="__faces">已绑定面 {region.selection.face_ids.join(', ')}</option>}{available.map(name=><option key={name}>{name}</option>)}</select></label>
     <label className="form-field full">应力指标<select aria-label={`局部应力指标 ${i+1}`} value={region.measure} onChange={e=>update(i,{measure:e.target.value,direction:e.target.value==='normal'?[0,0,0]:null})}><option value="principal_max">最大主应力（拉正压负）</option><option value="normal">指定方向正应力（拉正压负）</option></select></label>
    </div>
    {region.measure==='normal'&&<><p className="muted">输入正应力方向的全局向量。程序归一化后计算 nᵀσn；零向量不能提交。</p><div className="vector">{['X','Y','Z'].map((axis,k)=><label key={axis}><span>{axis}</span><input aria-label={`应力方向 ${i+1} ${axis}`} type="number" step="any" value={region.direction?.[k]??0} onChange={e=>update(i,{direction:(region.direction||[0,0,0]).map((v:number,j:number)=>j===k?Number(e.target.value):v)})}/></label>)}</div></>}
   </section>;
  })}
  <button className="text-button" disabled={value.length>=12} onClick={()=>change([...value,{name:'局部应力 '+(value.length+1),selection:{kind:'named',name:'',body_id:null,face_ids:[]},measure:'principal_max',direction:null}])}><Plus size={15}/>添加局部应力评估</button>
 </>;
}
