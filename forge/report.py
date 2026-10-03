"""Readable Chinese engineering reports, sourced exclusively from saved raw fields."""
from pathlib import Path
from xml.sax.saxutils import escape
import json
import numpy as np
import h5py
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import SimpleDocTemplate,Paragraph,Spacer,Table,TableStyle,Image,KeepTogether
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.colors import HexColor,white
from reportlab.lib.pagesizes import A4

TYPES={'static':'结构静力','thermal_steady':'稳态热分析','thermal_transient':'瞬态热分析','fatigue':'高周疲劳','contact_cycle':'周期接触','contact_fatigue':'接触疲劳与磨损','thermo_mechanical':'热结构装配'}


def selection_description(selection):
    if selection is None:return '全部指定组件'
    body='全模型选区' if selection.body_id is None else f'组件 {selection.body_id}'
    region={'direction':f'朝向 {selection.direction} 的面','holes':'孔壁','box':f'空间范围 {selection.bounds_m} m','named':f'命名选区 {selection.name}','faces':f'面 {selection.face_ids}'}.get(selection.kind,selection.kind)
    return body+'，'+region


def report_pdf(folder,case,summary,source=None):
    """Single-study and run reports share chapters, figures and automatic TOC."""
    from .reporting.document import Report
    folder=Path(folder);source=Path(source or folder)
    config=json.loads((source/'config.json').read_text(encoding='utf-8'))
    node={'key':'study','study_id':source.name,'model_id':config.get('model_id',''),
          'case':case.model_dump(),'label':case.name,'status':'partial' if summary.get('solve_failure') else 'completed'}
    verification=source/'run-step-validation.json'
    if verification.is_file():node['validation']=json.loads(verification.read_text(encoding='utf-8'))
    run={'id':folder.name,'title':case.name,'suite_version':'single-study','nodes':[node]}
    return Report(run,folder,sources={source.name:source},filename='report.pdf').build()


def history_chart(folder,fontfile):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.font_manager import FontProperties
    with h5py.File(folder/'fields.h5') as f:
        times=f['times_s'][:]
        if len(times)<2:return None
        fig,ax=plt.subplots(figsize=(7.5,3.4));font=FontProperties(fname=str(fontfile)) if fontfile.exists() else None
        if 'temperature_C' in f:
            values=f['temperature_C'];ax.plot(times,[x.max() for x in values],color='#bd623b',label='最高温度');ax.plot(times,[x.min() for x in values],color='#197786',label='最低温度');unit='温度 / °C'
        else:
            from strength.fem import stress_invariants
            values=f['stresses'];ax.plot(times,[stress_invariants(x)[0].max()/1e6 for x in values],color='#197786',label='原始积分点最大等效应力');unit='应力 / MPa'
        ax.set_xlabel('时间 / s',fontproperties=font);ax.set_ylabel(unit,fontproperties=font);ax.grid(alpha=.18);ax.legend(prop=font,frameon=False);fig.tight_layout()
        path=folder/'history.png';fig.savefig(path,dpi=180);plt.close(fig);return path


def contact_history_chart(folder,fontfile):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.font_manager import FontProperties
    with h5py.File(folder/'fields.h5') as f:
        if 'contact' not in f or len(f['times_s'])<2 or not any(len(v) for v in f['contact'].values()):return None
        times=f['times_s'][:];pressure=[];gap=[]
        for i in range(len(times)):
            pairs=f['contact'].get(str(i),{})
            pressure.append(max((float(np.max(p['pressure_Pa'])) for p in pairs.values() if len(p['pressure_Pa'])),default=0)/1e6)
            gap.append(min((float(np.min(p['gap_m'])) for p in pairs.values() if len(p['gap_m'])),default=float('nan'))*1e6)
        font=FontProperties(fname=str(fontfile)) if fontfile.exists() else None
        fig,ax=plt.subplots(figsize=(7.5,3.4));second=ax.twinx()
        ax.plot(times,pressure,color='#197786');second.plot(times,gap,color='#bd623b')
        ax.set_xlabel('时间 / s',fontproperties=font);ax.set_ylabel('从面平均压力最大值 / MPa',fontproperties=font,color='#197786')
        second.set_ylabel('最小法向间隙 / μm',fontproperties=font,color='#bd623b');ax.grid(alpha=.18);fig.tight_layout()
        path=folder/'contact-history.png';fig.savefig(path,dpi=180);plt.close(fig);return path
