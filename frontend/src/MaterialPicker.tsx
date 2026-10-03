import {useState} from 'react';

export type MaterialCard={id:string;builtin?:boolean;category?:string;aliases?:string[];condition?:string;notes?:string[];references?:{title:string;url:string}[];material:any};

/** Selection copies a card; it never edits the library or commits the form. */
export function MaterialPicker({cards,value,onChange,label='选择材料卡'}:{cards:MaterialCard[];value:any;onChange:(m:any)=>void;label?:string}){
 const [category,setCategory]=useState('全部'),[search,setSearch]=useState('');
 const group=(c:MaterialCard)=>c.category||(c.builtin?'教学演示':'我的材料');
 const groups=[...new Set(cards.map(group))];
 const query=search.trim().toLocaleLowerCase();
 const visible=cards.filter(c=>(category==='全部'||group(c)===category)&&[c.material.name,group(c),...(c.aliases||[])].join(' ').toLocaleLowerCase().includes(query));
 const selected=cards.find(c=>c.material.name===value?.name);
 return <section className="material-picker" aria-label={label+'浏览器'}>
  <div className="material-filters"><label className="form-field">材料分类<select aria-label={label+'分类'} value={category} onChange={e=>setCategory(e.target.value)}><option>全部</option>{groups.map(g=><option key={g}>{g}</option>)}</select></label>
   <label className="form-field">搜索牌号或名称<input type="search" aria-label={label+'搜索'} placeholder="例如 ABS、尼龙、304、6061" value={search} onChange={e=>setSearch(e.target.value)}/></label></div>
  <label className="form-field">从材料库选择 <span className="muted">{visible.length} / {cards.length} 张材料卡</span><select aria-label={label} value="" onChange={e=>{const card=cards.find(c=>c.id===e.target.value);if(card)onChange(structuredClone(card.material));}}><option value="">{visible.length?'选择预设或已保存材料…':'没有匹配的材料，可清除搜索或自定义'}</option>{groups.map(g=>{const rows=visible.filter(c=>group(c)===g);return rows.length?<optgroup key={g} label={g}>{rows.map(c=><option key={c.id} value={c.id}>{c.material.name}</option>)}</optgroup>:null;})}</select></label>
  {selected&&<div className="material-reference"><strong>{selected.builtin?'预设参考':'我的材料'} · {selected.material.name}</strong>{selected.condition&&<p>{selected.condition}</p>}{selected.notes?.map((n,i)=><p key={i}>{n}</p>)}{selected.references?.map(r=><a key={r.url} href={r.url} target="_blank" rel="noreferrer">查看来源：{r.title} ↗</a>)}</div>}
 </section>;
}
