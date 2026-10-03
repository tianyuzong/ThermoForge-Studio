import {useEffect,useState} from 'react';
import {FileText,Download,Loader2} from 'lucide-react';
import {api} from './api';
import {Modal} from './Editors';

function caption(name:string){
 const titles:Record<string,string>={'01-model.png':'模型与组件','02-mesh.png':'实际网格','03-boundary.png':'支撑与载荷','04-displacement.png':'总位移','05-elastic.png':'弹性变形','06-stress.png':'等效应力','08-safety.png':'屈服安全系数','09-contact.png':'离散接触压力'};
 if(titles[name])return titles[name];
 if(name.startsWith('07-hotspot-'))return '局部应力 · 组件 '+name.replace('07-hotspot-','').replace('.png','');
 if(name.startsWith('10-contact-pair-'))return '接触采样 · 对 '+name.replace('10-contact-pair-','').replace('.png','');
 if(name.startsWith('11-region-'))return '指定选区应力 · '+name.replace('11-region-','').replace('.png','');
 return '多工况对照';
}

export default function ReportGallery({job,close}:{job:any,close:()=>void}){
 const [data,setData]=useState<any>(null),[selected,setSelected]=useState(0),[error,setError]=useState('');
 useEffect(()=>{let disposed=false;api('/jobs/'+job.id+'/report').then(d=>{if(!disposed)setData(d);}).catch(e=>{if(!disposed)setError(String(e));});return()=>{disposed=true;};},[job.id]);
 const figure=data?.figures?.[selected];
 return <Modal title="图文分析报告" onClose={close}>
  <div className="report-gallery">
   <div className="report-gallery-actions"><a className="download-button" href={`/api/v1/jobs/${job.id}/files/report.pdf`} target="_blank" rel="noreferrer"><FileText size={16}/>下载中文报告</a><a className="download-button" href={`/api/v1/jobs/${job.id}/files/metrics.csv`}><Download size={16}/>下载指标表格</a></div>
   {error?<p className="warning-strip">{error}</p>:!data?<p><Loader2 size={16} className="spin"/> 正在读取报告图表…</p>:!data.available?<p>此历史作业尚未生成新版图表，可先查看原报告。</p>:<>
    <p className="report-validation-note"><b>参考商业软件同条件数值对照：尚未验证</b><br/>云图来自实际结果；积分点强度、节点平滑显示与接触压力分别统计。</p>
    <div className="report-figure-tabs">{data.figures.map((f:any,i:number)=><button key={f.name} className={i===selected?'active':''} onClick={()=>setSelected(i)}>{caption(f.name)}</button>)}</div>
    {figure&&<figure><a href={figure.url} target="_blank" rel="noreferrer"><img src={figure.url} alt={caption(figure.name)}/></a><figcaption>{caption(figure.name)} · 点击查看原图。色标、单位和局部评估范围见报告图注。</figcaption></figure>}
    {job.summary?.contact&&<p className="muted">齿根弯曲、接触疲劳和运行干涉尚未评估；局部应力图不能替代这些专项校核。</p>}
   </>}
  </div>
 </Modal>;
}
