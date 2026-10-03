"""Detailed, model-independent analysis report assembled from saved evidence."""
import csv
import json
from pathlib import Path
from xml.sax.saxutils import escape
import numpy as np
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import SimpleDocTemplate, Paragraph, Table, TableStyle, Spacer, PageBreak, Image, KeepTogether
from reportlab.platypus.tableofcontents import TableOfContents
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.colors import HexColor, white
from reportlab.lib.pagesizes import A4
from strength.config import DATA
from forge.provenance import file_hash, digest
from forge.report import TYPES
from . import VERSION
from ..version import VERSION as APP_VERSION
from .data import read_json, native_statistics, selection, validation_rows
from .figures import Figures, FONT

STATUS = {'completed':'完成','partial':'部分完成','failed':'失败','cancelled':'已取消',
          'interrupted':'中断','pending':'未开始','running':'进行中','queued':'排队中','blocked':'依赖阻止'}


def number(value, unit='', factor=1):
    return '未记录' if value is None else f'{value*factor:.6g} {unit}'.strip()


class Document(SimpleDocTemplate):
    def beforeDocument(self):
        # Multi-pass pagination must produce the same anchor names on every pass.
        self.outline_number=0
        self.toc_entries=[]

    def afterFlowable(self, flowable):
        if isinstance(flowable, Paragraph) and flowable.style.name in ('chapter','section'):
            self.outline_number+=1
            key=f'section-{self.outline_number}'
            level=0 if flowable.style.name=='chapter' else 1
            title=flowable.getPlainText()
            self.canv.bookmarkPage(key)
            self.canv.addOutlineEntry(title,key,level=level,closed=False)
            if getattr(flowable,'include_in_toc',True):
                self.notify('TOCEntry',(level,escape(title),self.page,key))
                self.toc_entries.append({'level':level,'title':title,'page':self.page,'anchor':key})


class Report:
    def __init__(self, run, root, sources=None, filename='summary.pdf'):
        self.run,self.root=run,Path(root);self.assets=self.root/'report-assets';self.assets.mkdir(exist_ok=True)
        self.sources=sources or {};self.filename=filename;self.errors=[];self.active_study=None
        self.font='DetailedCN';bold='DetailedCN-Bold';font_bold=Path('C:/Windows/Fonts/msyhbd.ttc')
        if FONT.exists():
            pdfmetrics.registerFont(TTFont(self.font,str(FONT),subfontIndex=0))
            pdfmetrics.registerFont(TTFont(bold,str(font_bold if font_bold.exists() else FONT),subfontIndex=0))
        else:
            self.font=bold='STSong-Light';pdfmetrics.registerFont(UnicodeCIDFont(self.font))
        self.body=ParagraphStyle('body',fontName=self.font,fontSize=9,leading=14.5,textColor=HexColor('#202b30'),wordWrap='CJK',spaceAfter=6)
        self.small=ParagraphStyle('small',parent=self.body,fontSize=8,leading=12,textColor=HexColor('#546872'))
        self.chapter=ParagraphStyle('chapter',parent=self.body,fontName=bold,fontSize=21,leading=29,spaceAfter=12,keepWithNext=True)
        self.section=ParagraphStyle('section',parent=self.body,fontName=bold,fontSize=13,leading=19,spaceBefore=12,spaceAfter=8,keepWithNext=True)
        self.table_style=ParagraphStyle('table-body',parent=self.body,fontSize=8,leading=12,spaceAfter=0)
        self.toc=TableOfContents(dotsMinLevel=0,levelStyles=[
            ParagraphStyle('toc-chapter',parent=self.body,fontName=bold,fontSize=10,leading=15,spaceBefore=7,spaceAfter=0,rightIndent=32),
            ParagraphStyle('toc-section',parent=self.body,fontSize=9,leading=13,spaceBefore=2,spaceAfter=0,leftIndent=14,rightIndent=32)])
        self.story=[];self.figure_number=0;self.records=[];self.loaded=[]

    def p(self,text,style=None):return Paragraph(escape(str(text)).replace('\n','<br/>'),style or self.body)

    def table(self,rows,widths=None):
        widths=widths or [510/len(rows[0])]*len(rows[0])
        result=Table([[self.p(c,self.table_style) for c in row] for row in rows],colWidths=widths,repeatRows=1,hAlign='LEFT')
        result.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),HexColor('#dcebed')),('ROWBACKGROUNDS',(0,1),(-1,-1),[white,HexColor('#f4f7f8')]),
            ('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),7),('RIGHTPADDING',(0,0),(-1,-1),7),
            ('TOPPADDING',(0,0),(-1,-1),6),('BOTTOMPADDING',(0,0),(-1,-1),6),('LINEBELOW',(0,0),(-1,0),.6,HexColor('#9cbcc1'))]))
        return result

    def page(self,title,include_in_toc=True):
        if self.story:self.story.append(PageBreak())
        heading=self.p(title,self.chapter);heading.include_in_toc=include_in_toc
        self.story.append(heading)

    def contents(self):
        self.story += [PageBreak(),self.p('目录',ParagraphStyle('contents-title',parent=self.chapter)),
            self.p('点击章节名称或页码可跳转。页码与 PDF 页面序号一致，由平台排版时自动计算。',self.small),
            Spacer(1,8),self.toc]

    def heading(self,title):self.story.append(self.p(title,self.section))
    def text(self,text):self.story.append(self.p(text))

    def figure(self,record,max_height=370):
        if not record:return
        from PIL import Image as PILImage
        self.figure_number+=1
        with PILImage.open(record['path']) as im:w,h=im.size
        width=min(510,max_height*w/h);height=width*h/w
        provenance=(' 研究 '+self.active_study+'；数据来自平台保存的工况与结果。') if self.active_study else ''
        self.story.append(KeepTogether([Image(str(record['path']),width=width,height=height),Spacer(1,4),
            self.p(f"图 {self.figure_number}  {record['caption']}"+provenance,self.small),Spacer(1,9)]))
        self.records.append({**record,'path':record['path'].relative_to(self.root).as_posix(),'figure':self.figure_number,'study_id':self.active_study})

    def load(self):
        for node in self.run['nodes']:
            source=Path(self.sources.get(node.get('study_id'),DATA/'studies'/str(node.get('study_id') or '')))
            if not (source/'fields.h5').is_file() or not (source/'summary.json').is_file():continue
            try:
                config=read_json(source/'config.json');summary=read_json(source/'summary.json')
                stats=native_statistics(source);audit=read_json(source/'audit.json',[])
                from forge.verification import read_verification
                verification=read_verification(source)
                node=dict(node)
                if verification and verification.get('status')=='invalid':node.pop('validation',None)
                aliases=config.get('component_aliases',{})
                if not aliases:
                    try:
                        from strength import store
                        project=store.get('project',config.get('project_id',''))
                        if project.get('component_aliases_model_id')==config['model_id']:aliases=project.get('component_aliases',{})
                    except KeyError:pass
                figures=Figures(source,self.assets/node['key'],stats,config['case'],summary)
                figures.aliases=aliases
                self.loaded.append({'node':node,'source':source,'config':config,'case':config['case'],'summary':summary,'stats':stats,'audit':audit,'figures':figures,'verification':verification,'aliases':aliases})
            except Exception as exc:self.errors.append({'study_id':node.get('study_id'),'stage':'read_saved_result','detail':str(exc)})

    def introduction(self):
        self.page('热学与力学分析\n详细图文报告' if all(x['case']['analysis_type'] in ('static','thermal_steady','thermal_transient','thermo_mechanical') for x in self.loaded) else '多物理场分析\n详细图文报告',include_in_toc=False)
        self.text(self.run.get('title','当前项目分析'))
        self.text('THERMOFORGE STUDIO  /  建模条件 · 真实场数据 · 数值诊断')
        rows=[['研究工况','计算状态','结果来源']]+[[n['label'],STATUS.get(n['status'],n['status']),
            '已核验历史结果' if n.get('reused') else '本批次计算'] for n in self.run['nodes']]
        self.story.append(self.table(rows,[270,90,150]));self.story.append(Spacer(1,12))
        if self.loaded:
            record=self.loaded[0]['figures'].geometry()
            from PIL import Image as PILImage
            with PILImage.open(record['path']) as im:w,h=im.size
            height=min(260,510*h/w);self.story.extend([Image(str(record['path']),width=height*w/h,height=height),self.p('封面模型视图：'+record['caption'],self.small)])
            self.records.append({**record,'path':record['path'].relative_to(self.root).as_posix(),'figure':None,'study_id':self.loaded[0]['node']['study_id']})
        self.text('阅读顺序：先核对模型、材料与边界，再查看整机与局部场图、原始极值和时间历程，最后检查平衡误差及收敛证据。使用后页目录或 PDF 左侧书签直达各章节。')
        self.text('本 PDF 由 ThermoForge Studio 仿真平台的报告模块自动生成。模型、材料、边界条件与计算设置读取本批次的工况快照；所有结果表、云图和曲线均由平台保存的原始 HDF5 场数据、计算摘要及求解诊断生成。')
        self.text('报告采用平台保存的计算记录。完整性、方程平衡与网格/步长验证分别列示，不能相互替代。材料来源、演示假设与数值验证范围按各工况记录保留；软件演示材料和等效热源不代表实物认证数据。')
        if self.errors:
            self.text('本报告为部分交付：以下内容生成失败，其余独立结果保留。')
            self.story.append(self.table([['研究 / 阶段','诊断']]+[[str(e.get('study_id') or '报告')+' / '+e['stage'],e['detail']] for e in self.errors],[175,335]))
        self.story.append(self.p(f"批次 {self.run['id']}\n平台 {APP_VERSION} / 报告 {VERSION} / 工况套件 {self.run['suite_version']}",self.small))
        self.contents()
        self.page('01  结果总览与阅读口径')
        rows=[['工况','温度峰值\n°C','原始应力\nMPa','最大位移\nmm','原生压力\nMPa']]
        for d in self.loaded:
            s=d['summary'];rows.append([d['node']['label'],number(s.get('maximum_temperature_C')),number(s.get('maximum_von_mises_Pa'),factor=1e-6),
                number(s.get('maximum_displacement_m'),factor=1000),number(s.get('maximum_contact_pressure_Pa'),factor=1e-6)])
        self.story.append(self.table(rows,[194,79,79,79,79]))
        self.heading('怎样理解这些结果')
        self.text('上表极值覆盖该工况已保存的完整历程，不保证同时发生。后文热点表给出对应组件、时间、节点或积分点及坐标；局部图用于看清整机外壳遮挡的区域。')
        self.text('温度使用节点值；应力统计使用原始体积分点值；原生接触压力使用求解器接触积分点值。云图沿用工作台的显示投影，投影后峰值可能较低，不能用云图颜色读数替代原始峰值。')
        kinds={d['case']['analysis_type']:d for d in self.loaded}
        if 'thermal_steady' in kinds and 'thermal_transient' in kinds:
            a,b=kinds['thermal_steady']['summary'],kinds['thermal_transient']['summary']
            self.text(f"稳态最高温度为 {number(a.get('maximum_temperature_C'),'°C')}；独立瞬态在 {b['times_s'][-1]:g} s 的计算历程最高温度为 {number(b.get('maximum_temperature_C'),'°C')}。它们分别代表平衡解和有限时长响应；瞬态初温及是否续算见各自边界表，不能把两者混为同一时刻。")
        if 'static' in kinds and 'thermo_mechanical' in kinds:
            self.text('静力与热结构工况的接触、轴向约束或网格可能不同。可比较各自响应，但不能把两者峰值简单相减后称为纯热应力。当前报告没有额外求解保持同一机械模型的零温升对照。')
        self.heading('本次证据范围')
        self.text('计算完成、方程平衡通过、网格/时间步独立性及实物验证是不同层级。后文逐项列明证据；未绑定通过记录的校核保持“未验证”。尖角、理想固定边缘及接触边缘的峰值尤其需要局部网格复核。')
        self.text('静力和稳态仅展示结果视角，没有虚构时间过程。瞬态图中的每个时刻来自已保存帧；图间连线不生成新的求解结果。未启用的疲劳、磨损或裂纹扩展不会在本报告中被推测出来。')

    def materials(self):
        self.page('02  模型、材料与网格')
        geometries={d['config']['model_id'] for d in self.loaded}
        for mid in sorted(geometries):
            geo=read_json(DATA/'models'/mid/'geometry.json',{})
            self.text(f"模型 {mid}；组件数 {len(geo.get('components',[]))}，几何面数 {len(geo.get('surfaces',[]))}。选区编号仅对本几何版本有效。")
            self.story.append(self.p('几何 SHA-256：'+str(geo.get('source_sha256') or self.loaded[0]['case'].get('geometry_sha256')),self.small))
            example=next(d for d in self.loaded if d['config']['model_id']==mid)
            self.active_study=example['node']['study_id']
            if example['aliases']:self.story.append(self.table([['稳定组件编号','显示别名']]+[[str(b),name] for b,name in sorted(example['aliases'].items())],[150,360]))
            for atlas in example['figures'].component_atlas():self.figure(atlas,450)
        groups={}
        for d in self.loaded:
            for body,material in (d['case'].get('component_materials') or {'全部':d['case']['material']}).items():
                key=digest(material);entry=groups.setdefault(key,{'material':material,'bodies':set(),'studies':set()})
                entry['bodies'].add(d['figures'].body_label(body));entry['studies'].add(d['node']['label'])
        self.page('材料卡与参数来源')
        for entry in groups.values():
            m=entry['material'];rows=[['材料 / 组件','已保存参数'],[m['name'],', '.join(sorted(entry['bodies']))],['适用工况','；'.join(sorted(entry['studies']))]]
            props=[]
            for k,label,u,factor in [('young_modulus_Pa','弹性模量','GPa',1e-9),('poisson_ratio','泊松比','',1),('density_kg_m3','密度','kg/m³',1),
                ('conductivity_W_mK','导热系数','W/(m·K)',1),('specific_heat_J_kgK','比热','J/(kg·K)',1),('thermal_expansion_per_K','热膨胀系数','1/K',1),
                ('yield_strength_Pa','屈服强度','MPa',1e-6),('tensile_strength_Pa','抗拉强度','MPa',1e-6),('hardness_Pa','硬度','MPa',1e-6),('wear_coefficient','Archard 磨损系数','',1)]:
                if m.get(k) is not None:props.append(label+' '+number(m[k],u,factor))
            rows.extend([['物性','；'.join(props)],['来源 / 适用温度',m.get('source','未提供')+'；'+str(m.get('valid_temperature_C') or '未提供温度适用区间')]])
            if m.get('phase_change'):rows.append(['相变',json.dumps(m['phase_change'],ensure_ascii=False)])
            self.story += [KeepTogether([self.table(rows,[133,377]),Spacer(1,10)])]
        self.heading('网格与离散')
        rows=[['工况','节点 / 单元','结构自由度','网格尺寸 / 最小 Jacobian']]
        for d in self.loaded:
            s=d['summary']['mesh'];rows.append([d['node']['label'],f"{s['nodes']:,} / {s['elements']:,}",str(s.get('dofs','未记录')),
                number(s.get('size_m'),'mm',1000)+' / '+number(s.get('min_jacobian'))])
        self.story.append(self.table(rows,[175,105,82,148]))
        self.text('结构采用 C3D10，热场采用匹配节点的二阶标量离散。表中 dofs 为存档结构自由度计数，不应理解为热场未知量数。局部网格图仅显示表面；完整体网格包含在 ZIP 的 mesh.npz 中。')

    def configuration(self,d,index):
        c,s,n=d['case'],d['summary'],d['node'];kind=c['analysis_type']
        self.page(f"{index:02d}  {n['label']}")
        self.text(TYPES.get(kind,kind)+'；状态：'+STATUS.get(n['status'],n['status'])+'；来源：'+('已核验历史结果复用' if n.get('reused') else '本批次计算'))
        self.story.append(self.p('研究编号 '+n['study_id'],self.small))
        provenance=d['config'].get('input_provenance')
        if provenance:
            self.text('配置基础：'+str(provenance.get('source_case_id') or '本次独立输入')+'；草稿版本 '+str(provenance.get('source_revision') if provenance.get('source_revision') is not None else '未记录')+'。')
            if provenance.get('parameter_sources'):
                self.heading('逐项参数来源')
                self.story.append(self.table([['参数路径（与工况快照一致）','来源说明']]+[[path,source] for path,source in sorted(provenance['parameter_sources'].items())],[240,270]))
        else:self.text('原记录未保存逐项参数来源；保留原材料来源及工况假设，不为历史结果补写来源。')
        resources=c.get('resources',{})
        self.text('保存的计算后端：'+str(s.get('compute_backend','未记录'))+f"；CPU 线程 {resources.get('cpu_threads','未记录')}，自由度上限 {resources.get('max_dofs','未记录')}，时间上限 {resources.get('time_limit_s','未记录')} s。")
        self.figure(d['figures'].boundaries(),235)
        rows=[['编号 / 边界','真实作用区域','值与单位']]
        if not kind.startswith('thermal'):
            for j,b in enumerate(c.get('supports',[]),1):rows.append([str(j)+' / '+b['name'],selection(b['selection']),
                '约束 '+', '.join(b['components'])+'；位移 '+str(b['displacement_m'])+' m'])
            for j,b in enumerate(c.get('loads',[]),len(c.get('supports',[]))+1):
                value='压力 '+number(b.get('pressure_Pa'),'Pa') if b['kind']=='pressure' else '力矩 '+str(b.get('torque_Nm'))+' N·m' if b['kind']=='torque' else str(b.get('vector'))+(' m/s²' if b['kind']=='gravity' else ' N')
                rows.append([str(j)+' / '+b['name'],selection(b.get('selection')) if b.get('selection') else '组件 '+str(b.get('body_id','全部')),value])
        if kind.startswith('thermal') or kind=='thermo_mechanical':
            self.heading('初始状态与热边界')
            t=c['thermal'];self.text(f"初始温度 {t['initial_C']:g} °C；无应力参考温度 {t['reference_C']:g} °C；续算来源 {t.get('initial_from_study') or '无，独立初始状态'}。")
            actual=s.get('initial_state')
            if actual:
                self.text('实际采用初始场：'+('研究 '+actual['study_id']+' 的 '+number(actual.get('source_time_s'),'s')+' 保存帧，完全一致节点映射' if actual.get('kind')=='saved_field' else '均匀温度场')+'；初温范围 '+number(actual.get('minimum_temperature_C'),'°C')+' 至 '+number(actual.get('maximum_temperature_C'),'°C')+'。')
            if kind!='thermal_steady':self.text(f"配置时长 {t['duration_s']:g} s，起始时间步 {t['dt_s']:g} s，保存间隔 {t['save_s']:g} s；实际保存 {s['frames']} 帧，范围 {s['times_s'][0]:g} 至 {s['times_s'][-1]:g} s。")
            else:self.text('稳态平衡求解，不使用配置对象中预留的瞬态时间参数。')
            for j,b in enumerate(t['boundaries'],1):
                label,unit={'body_power':('组件总功率','W'),'surface_power':('表面总功率','W'),'heat_flux':('热流密度','W/m²'),'convection':('对流系数','W/(m²·K)'),
                    'temperature':('指定温度','°C'),'radiation':('辐射发射率','')}[b['kind']]
                value=label+' '+number(b['value'],unit)
                if b['kind'] in ('convection','radiation'):value+='；环境 '+number(b['ambient_C'],'°C')
                for k in ('profile','ambient_profile','thermostat'):
                    if b.get(k):value+='；'+k+' '+json.dumps(b[k],ensure_ascii=False)
                rows.append([str(j)+' / '+b['name'],'组件 '+str(b['body_id']) if b.get('body_id') else selection(b.get('selection')),value])
        self.story.append(self.table(rows,[135,215,160]))
        self.heading('连接关系与建模假设')
        connections={}
        for key,label in [('bonded_connections','机械粘结'),('thermal_connections','热连接')]:
            if key=='bonded_connections' and kind.startswith('thermal'):continue
            if key=='thermal_connections' and not (kind.startswith('thermal') or kind=='thermo_mechanical'):continue
            for pair in c.get(key,[]):
                code=digest({k:pair.get(k) for k in ('name','slave','master','max_gap_m')})
                value=connections.setdefault(code,{'pair':pair,'kinds':[]});value['kinds'].append(label)
        if connections:
            rows=[['连接','从侧 → 主侧','容差 / 类型']]
            for value in connections.values():
                b=value['pair'];rows.append([b['name'],selection(b['slave'])+' → '+selection(b['master']),number(b.get('max_gap_m'),'μm',1e6)+'；'+', '.join(value['kinds'])])
            self.story.append(self.table(rows,[125,255,130]))
        if not connections and not c.get('contacts'):self.text('本工况未配置组件间粘结或开闭接触。')
        for a in c.get('assumptions',[]):self.text('• '+a)
        if c.get('contacts'):
            self.heading('开闭接触与界面传热参数')
            for pair in c['contacts']:
                rows=[['接触对',pair['name']],['从侧 / 主侧',selection(pair['slave'])+' → '+selection(pair['master'])],
                    ['机械接触',f"摩擦系数 {pair['friction']:g}；法向罚刚度 {pair['penalty_Pa_m']:.6g} Pa/m；切向刚度 {pair.get('tangential_stiffness_Pa_m')} Pa/m"],
                    ['搜索与初始偏置',number(pair['search_distance_m'],'mm',1000)+'；偏置 '+number(pair['initial_offset_m'],'μm',1e6)],
                    ['闭合界面热导','；'.join(f"p={v['pressure_Pa']/1e6:g} MPa → h={v['conductance_W_m2K']:g} W/(m²·K)"+(f"，T={v['temperature_C']:g} °C" if v.get('temperature_C') is not None else '') for v in pair.get('conductance',[])) if pair.get('thermal') else '未启用'],
                    ['张开界面',f"间隙导热系数 {pair.get('gap_conductivity_W_mK')} W/(m·K)；最小间隙 {number(pair.get('minimum_gap_m'),'μm',1e6)}；最大间隙 {number(pair.get('maximum_gap_m'),'mm',1000)}"],
                    ['磨损模型','启用' if pair.get('wear') else '未启用，本工况不输出磨损量']]
                self.story += [KeepTogether([self.table(rows,[125,385]),Spacer(1,8)])]
        if kind=='thermo_mechanical':
            from forge.schemas import coupling_mode
            feedback=s.get('coupling_mode',coupling_mode(c))=='two_way_contact'
            self.heading('双向迭代设置' if feedback else '热结构求解设置')
            cfg=c['coupling'];self.text('热场 → 热膨胀与机械接触 → 压力/间隙 → 界面传热 → 更新热场。' if feedback else '当前未配置有效的压力/间隙热反馈接触；采用单向热结构传递。')
            self.text(f"温度阈值 {cfg['temperature_tolerance_K']:g} K；位移相对阈值 {cfg['displacement_relative_tolerance']:g}；热流相对阈值 {cfg['heatflow_relative_tolerance']:g}；松弛 {cfg['relaxation']:g}；最多 {cfg['max_iterations']} 次迭代；最多 {cfg['max_cutbacks']} 次减步。")

    def results(self,d):
        s,c,st,fig=d['summary'],d['case'],d['stats'],d['figures'];last=len(st['frames'])-1;middle=last//2
        self.page(d['node']['label']+' / 结果与危险位置')
        rows=[['场量 / 原始数据口径','全历程极值','位置与对应时间']]
        for key,label,unit in [('temperature','最高节点温度','°C'),('stress','最大原始积分点等效应力','MPa'),('displacement','最大节点位移','mm')]:
            h=st['hotspots'].get(key)
            if h:
                stamp=f"t={h['time_s']:g} s" if last else '单帧结果，无物理时间轴'
                location=fig.body_label(h['body_id'])+f"；{stamp}；"+('节点 '+str(h['node']) if 'node' in h else f"单元 {h['element']} / 积分点 {h['integration_point']}")
                location+='\n坐标 mm：'+', '.join(f'{v:.4f}' for v in h['position_mm'])
                rows.append([label,number(h['value'],unit),location])
        if 'maximum_contact_pressure_Pa' in s:
            pressure_frames=[v for v in st['frames'] if v.get('native_pressure_max_MPa') is not None]
            pressure_peak=max(pressure_frames,key=lambda v:v['native_pressure_max_MPa']) if pressure_frames else None
            rows.append(['原生接触积分点压力',number(s['maximum_contact_pressure_Pa'],'MPa',1e-6),
                (f"已保存帧峰值发生于 {pressure_peak['time_s']:g} s；" if pressure_peak else '')+'与从面投影压力不同'])
        self.story.append(self.table(rows,[155,105,250]))
        self.text('坐标基于未变形全局坐标系；节点、单元和积分点编号从 1 起。原始应力危险点可能位于固定边缘或几何尖角，列出它不代表该局部峰值已经收敛。')
        if last and 'temperature_max_C' in st['frames'][-1]:
            before,end=st['frames'][-2:];change=end['temperature_max_C']-before['temperature_max_C']
            self.text(f"末两个保存时刻 {before['time_s']:g} 至 {end['time_s']:g} s，全模型最高温度由 {before['temperature_max_C']:.5g} 变为 {end['temperature_max_C']:.5g} °C，变化 {change:.5g} K。这段温度变化应结合温度曲线判断，报告没有将瞬态末帧自动判为热平衡。")
        if c['analysis_type']=='thermo_mechanical' and last and 'stress_max_MPa' in st['frames'][0]:
            begin,end=st['frames'][0],st['frames'][-1]
            self.text(f"同一耦合工况的初帧与末帧原始应力峰值分别为 {begin['stress_max_MPa']:.5g} 和 {end['stress_max_MPa']:.5g} MPa；最大位移分别为 {begin['displacement_max_mm']:.5g} 和 {end['displacement_max_mm']:.5g} mm。峰值位置可能变化，以上比较不是对某一个固定材料点的应力增量分解。")
        rows=[['组件','全历程最高温度 / °C','全历程应力峰值 / MPa','全历程最大位移 / mm']]
        for b,v in st['components'].items():rows.append([fig.body_label(b),number(v.get('temperature_max_C')),number(v.get('stress_max_MPa')),number(v.get('displacement_max_mm'))])
        self.heading('逐组件统计');self.story.append(self.table(rows,[65,148,149,148]))
        if c['analysis_type']=='static':
            view=read_json(self.root/d['node']['key']/'view.json',{})
            reaction=view.get('reactions')
            if reaction is None:
                try:
                    from forge.run_exports import static_support_reactions
                    reaction=static_support_reactions(d['source'])
                except (OSError,ValueError,KeyError) as exc:self.text('支反力恢复未完成：'+str(exc)+'；其他原始场结果仍列示。')
            if reaction:
                self.heading('支反力与外载核对')
                rows=[['作用组','X / N','Y / N','Z / N'],['外载合计',*[number(v) for v in reaction['applied_total_N']]],['支反力合计',*[number(v) for v in reaction['total_N']]]]
                for g in reaction['groups']:rows.append([g['name'],*[number(v) for v in g['force_N']]])
                self.story.append(self.table(rows,[210,100,100,100]));self.text(reaction['basis']+'。共享约束自由度在合计中只计一次。')
        if 'temperature' in st['hotspots']:
            body=st['hotspots']['temperature']['body_id'];heat_bodies=sorted({b['body_id'] for b in c['thermal']['boundaries'] if b.get('body_id')}) or [body]
            self.page(d['node']['label']+' / 温度与传热场')
            limits=[s['minimum_temperature_C'],s['maximum_temperature_C']]
            self.figure(fig.cloud('temperature',[(last,None,'整机'),(last,[body],f'热点组件 {body}')],'末帧温度 / 内部热点',
                f"末帧 {fig.times[last]:g} s；右图隐藏其他组件以显示内部热点。" if last else '稳态场或单帧结果，无瞬态时间含义。','temperature',limits))
            if last:
                self.figure(fig.cloud('temperature',[(i,heat_bodies,f't = {fig.times[i]:g} s') for i in (0,middle,last)],'发热组件温度演变',
                    '三个实际保存时刻使用相同组件、相机和色标，未对场值做时间插值。','temperature-sequence',limits),240)
            self.figure(fig.cloud('temperature_gradient',[(last,heat_bodies,'发热组件局部')],'温度梯度幅值','积分点梯度幅值投影到节点后显示，单位为 K/m。','gradient'),265)
            self.figure(fig.cloud('heat_flux',[(last,heat_bodies,'发热组件局部')],'热流密度幅值','积分点热流向量幅值投影到节点后显示；色图表示大小，不代表流线。','heat-flux'),265)
        if 'stress' in st['hotspots']:
            h=st['hotspots']['stress'];body=h['body_id'];i=h['frame']
            self.page(d['node']['label']+' / 应力与变形')
            self.figure(fig.cloud('stress',[(i,None,'整机'),(i,[body],f'危险组件 {body}')],'应力分布与原始极值位置',
                (f"t={fig.times[i]:g} s；" if last else '静力单帧结果；')+f"原始积分点极值 {h['value']:.6g} MPa，云图使用节点投影，色标上限为原始峰值。",'stress',[0,h['value']]))
            h=st['hotspots']['displacement'];i=h['frame'];body=h['body_id']
            self.figure(fig.cloud('displacement',[(i,None,'整机'),(i,[body],f'最大位移组件 {body}')],'总位移 / 变形显示 ×30',
                (f"t={fig.times[i]:g} s；" if last else '静力单帧结果；')+f"几何变形放大 30 倍，色标与统计仍为真实位移，最大值 {h['value']:.6g} mm。",'displacement',[0,h['value']],30))
        if last:
            self.page(d['node']['label']+' / 真实时间历程')
            self.figure(fig.history(),530)
            self.figure(fig.component_temperatures(),300)
            self.heading('保存帧数值表')
            cols=[(k,label) for k,label in [('temperature_max_C','最高温度 / °C'),('stress_max_MPa','原始应力 / MPa'),('displacement_max_mm','位移 / mm'),('native_pressure_max_MPa','原生压力 / MPa')] if k in st['frames'][0]]
            sample=st['frames'] if len(st['frames'])<=25 else [st['frames'][i] for i in sorted(set(np.linspace(0,last,25,dtype=int)))]
            self.story.append(self.table([['时间 / s']+[label for _,label in cols]]+[[number(v['time_s'])]+[number(v.get(k)) for k,_ in cols] for v in sample]))
            self.text('完整逐帧数值见 report-assets/'+d['node']['key']+'/history.csv；超过 25 帧时本表抽列 25 个已保存时刻。')
        if c.get('contacts') and st['contact_pairs']:
            self.page(d['node']['label']+' / 接触与装配间隙')
            bodies=sorted({p['slave']['body_id'] for p in c['contacts']})
            self.figure(fig.cloud('contact_pressure',[(last,[b],f'从侧组件 {b}') for b in bodies[:3]],'接触压力 / 从面局部',
                '仅显示参与接触的从侧组件，避免外壳遮挡；显示值为从面向节点的投影，原生压力峰值另见指标表。','contact-pressure',[0,s.get('maximum_contact_pressure_Pa',0)*1e-6]))
            self.figure(fig.cloud('contact_gap',[(last,[b],f'从侧组件 {b}') for b in bodies[:3]],'接触间隙 / 从面局部',
                '接触投影场的正值表示张开，负值包含罚函数穿透；与转定子几何探针是不同定义。','contact-gap'))
            self.figure(fig.contact_history(),390)
            if any(v.get('sample_status')=='no_mapped_samples' for values in st['contact_pairs'].values() for v in values):
                self.text('部分保存帧没有有效从面映射样本，压力/间隙标记为未记录。这与已知接触压力为零不同；原生接触面积和求解诊断另行保留。')
        if any('wear_max_um' in row or 'wear_final_only_um' in row for row in st['frames']):
            self.page(d['node']['label']+' / 累计磨损')
            self.figure(fig.cloud('wear_depth',[(last,None,'最终已保存累计磨损')],'磨损深度','Archard 模型的累计表面磨损量，非裂纹扩展。','wear-depth'))
            self.figure(fig.wear_history(),300)
            self.text('按保存的接触压力、实际滑移、材料硬度及磨损系数计算；没有逐帧磨损数据的旧结果仅展示末值，不构造历程。')
        if s.get('clearance_probes'):
            self.page(d['node']['label']+' / 间隙探针历程')
            self.figure(fig.clearance(),455)
            rows=[['探针','名义间隙 / μm','全历程最小 / μm','末帧最小 / 最大 / μm']]
            for name,p in s['clearance_probes'].items():
                rows.append([name,number(p.get('initial_geometry_gap_m'),factor=1e6),number(min(p['minimum_gap_m']),factor=1e6),
                    number(p['minimum_gap_m'][-1],factor=1e6)+' / '+number(p['maximum_gap_m'][-1],factor=1e6)])
            self.story.append(self.table(rows,[165,95,100,150]))
            for name,p in s['clearance_probes'].items():
                i=int(np.argmin(p['minimum_gap_m']))
                self.text(name+f"：最小间隙发生于 {p['times_s'][i]:g} s，为 {p['minimum_gap_m'][i]*1e6:.6g} μm；末帧为 {p['minimum_gap_m'][-1]*1e6:.6g} μm。来源："+p['source'])
        fatigue=s.get('fatigue')
        if fatigue:
            self.fatigue(d)

    def fatigue(self,d):
        c,s,fig=d['case'],d['summary'],d['figures'];fatigue=s['fatigue'];hot=fatigue.get('hotspot') or {}
        cfg=c.get('fatigue',{});method=cfg.get('method','未记录')
        self.page(d['node']['label']+' / 疲劳评估')
        self.text('寿命与损伤依据原始应力及保存的材料标定，显示裂纹萌生危险位置，未计算裂纹形状或扩展；未评估项不补为零损伤。')
        rows=[['评估项','保存的结果'],['算法','带符号等效应力、雨流计数、Goodman 修正、Miner 累积损伤' if method=='sn_goodman' else 'Findley 临界平面法'],
            ['目标循环数',number(cfg.get('target_cycles'),'次')],['评估组件',', '.join(map(str,cfg.get('body_ids',[]))) or '按工况范围'],
            ['寿命定义',{'initiation':'裂纹萌生','failure':'试样失效'}.get(hot.get('life_definition',fatigue.get('life_definition')),'由输入曲线规定')]]
        if hot:
            life_status=hot.get('life_status','未记录')
            rows.extend([['寿命状态',{'below_defined_endurance_limit':'低于给定疲劳极限，本模型不预测有限寿命','finite':'有限寿命','not_evaluated':'未评估'}.get(life_status,life_status)],['预测寿命',number(hot.get('life_cycles'),'次') if hot.get('life_cycles') is not None else '没有有限寿命数值；按保存的寿命状态解释'],
                ['目标循环数下损伤',number(hot.get('damage'))],['危险位置',f"组件 {hot.get('body_id')} / 单元 {hot.get('element')} / 积分点 {hot.get('integration_point')}"],
                ['未变形坐标 / mm',', '.join(number(x,factor=1000) for x in hot.get('position_m',[]))]])
            if hot.get('critical_plane_normal') is not None:rows.extend([['材料坐标系临界平面法向',', '.join(number(x) for x in hot['critical_plane_normal'])],['Findley 参数',number(hot.get('fatigue_parameter_Pa'),'MPa',1e-6)]])
        else:rows.append(['危险位置','没有有效寿命评估位置；见缺项/适用性诊断'])
        self.story.append(self.table(rows,[145,365]))
        if fatigue.get('life_basis')=='last_completed_wear_block_held_fixed':self.text('该寿命以最后已计算磨损块的几何保持不变为条件，不外推后续磨损演变。')
        accumulated=fatigue.get('accumulated_hotspot')
        if accumulated:self.text('已模拟 '+number(fatigue.get('accumulated_cycles'),'次循环')+'；累计 Miner 损伤 '+number(accumulated.get('damage'))+'。')
        failures=fatigue.get('failures',{})
        if failures:self.story.append(self.table([['未评估原因','积分点数量']]+[[reason,str(count)] for reason,count in failures.items()],[395,115]))
        self.heading('材料标定与适用条件')
        materials=c.get('component_materials') or {'全部':c['material']};seen=set()
        for body,material in materials.items():
            findley=material.get('findley');curve=findley.get('curve') if findley and method=='findley' else material.get('sn_curve')
            if not curve:continue
            key=digest(curve)
            if key in seen:continue
            seen.add(key);self.text(material['name']+'；来源：'+curve.get('source','未提供')+'；'+('软件演示曲线' if curve.get('demonstration') else '按输入来源保留，报告不增加实物验证声明'))
            if findley and method=='findley':self.text('Findley k='+number(findley.get('k'))+'；标定条件：'+{'bulk':'普通材料','rolling_contact':'滚动接触'}.get(findley.get('calibration'),'未记录'))
            self.story.append(self.table([['循环数 N','曲线应力 / MPa']]+[[number(p['x']),number(p['y'],factor=1e-6)] for p in curve['points']],[230,280]))
            self.figure(fig.fatigue_curve(curve,key[:12]),240)
        history=c.get('history')
        if history:
            self.heading('载荷历史与循环口径')
            self.text('历史重复次数 '+number(history.get('repetitions'))+'；每段历史代表 '+number(history.get('cycles_per_history'),'次循环')+'；周期首尾闭合：'+('是' if history.get('periodic') else '否')+'。载荷比例乘以边界表中对应的基准载荷。')
            self.figure(fig.load_history(),270)
        cycles=hot.get('stress_cycles') or []
        if cycles:
            self.heading('危险点雨流计数与均值修正')
            rows=[['幅值 / MPa','均值 / MPa','修正后 / MPa','计数']]
            rows += [[number(v.get('amplitude_Pa'),factor=1e-6),number(v.get('mean_Pa'),factor=1e-6),number(v.get('corrected_Pa'),factor=1e-6),number(v.get('count'))] for v in cycles]
            self.story.append(self.table(rows))
            with (fig.dest/'stress-cycles.csv').open('w',encoding='utf-8-sig',newline='') as out:
                keys=list(dict.fromkeys(k for v in cycles for k in v));writer=csv.DictWriter(out,fieldnames=keys);writer.writeheader();writer.writerows(cycles)
        self.text('尖角、固定边缘及接触边缘处的应力峰值仍需局部收敛复核；缺少曲线或超出弹性适用范围的位置不输出推测寿命。')
        for field in ('fatigue_damage','fatigue_life'):
            try:self.figure(fig.cloud(field,[(len(fig.times)-1,None,'累计评估结果')],{'fatigue_damage':'目标循环数下损伤','fatigue_life':'材料曲线定义的寿命'}[field],'基于原始积分点评估；图为显示投影，没有裂纹生长时间轴。',field),270)
            except KeyError:self.text({'fatigue_damage':'疲劳损伤','fatigue_life':'疲劳寿命'}[field]+'原始场未保存。')

    def diagnostics(self,d):
        self.page(d['node']['label']+' / 数值质量与限制')
        verification=d.get('verification')
        if verification:self.text('独立验证证据与当前文件：'+('已核对配置、摘要和原始场哈希' if verification['status']=='verified' else verification.get('detail','证据不匹配')))
        else:self.text('未绑定独立验证记录。以下列出已保存的求解与平衡检查，不据此宣称实物验证或网格独立性通过。')
        self.story.append(self.table([['检查项','实际值或状态','要求','判断']]+validation_rows(d['summary'],d['audit'],d['node']),[160,140,110,100]))
        self.figure(d['figures'].convergence(d['audit']),300)
        steps=[v for v in d['audit'] if isinstance(v,dict) and v.get('iterations') and 'time_s' in v
               and isinstance(v['iterations'],list) and isinstance(v['iterations'][-1],dict)
               and 'temperature_change_K' in v['iterations'][-1]]
        if steps:
            rows=[['物理步 / s','实际 dt / s','迭代数','末次 ΔT / K','末次位移相对变化','末次热流相对变化']]
            for v in steps:
                a=v['iterations'][-1];rows.append([number(v.get('time_s')),number(v.get('dt_s')),str(len(v['iterations'])),
                    number(a.get('temperature_change_K')),number(a.get('displacement_relative')),number(a.get('heatflow_relative'))])
            self.story.append(self.table(rows,[72,72,54,98,107,107]))
            self.text(f"共 {len(steps)} 个有迭代记录的物理步；记录中的减步次数合计 {sum(v.get('cutbacks',0) for v in steps)}。初始状态另存，不属于上述增量迭代。")
        for check in (d['node'].get('validation') or {}).get('checks',[]):self.text('绑定步长比较：'+json.dumps(check,ensure_ascii=False))
        if d['summary'].get('solve_failure'):self.text('失败诊断：'+json.dumps(d['summary']['solve_failure'],ensure_ascii=False))
        self.text('报告中平衡检查来自原始 summary.json/audit.json。局部网格、时间步比较、实物材料或实验数据尚未具备的项目，不以“计算完成”替代。')
        self.text('接触表面边缘与理想固定区域的应力峰值不能直接作为已验证寿命或设计许用值；本报告没有进行低周塑性、润滑油膜或裂纹扩展计算。')

    def build(self):
        self.load()
        try:self.materials()
        except Exception as exc:self.errors.append({'stage':'materials','detail':str(exc)})
        for i,d in enumerate(self.loaded,3):
            self.active_study=d['node']['study_id']
            for stage,render in [('configuration',lambda:self.configuration(d,i)),('results',lambda:self.results(d)),('diagnostics',lambda:self.diagnostics(d))]:
                try:render()
                except Exception as exc:
                    self.errors.append({'study_id':self.active_study,'stage':stage,'detail':str(exc)})
                    self.heading('该章节未完整生成');self.text(str(exc)+'；其他独立章节与原始文件仍保留。')
            path=self.assets/d['node']['key'];stats=d['stats']
            (path/'statistics.json').write_text(json.dumps(stats,ensure_ascii=False,indent=2),encoding='utf-8')
            if stats['frames']:
                keys=list(dict.fromkeys(k for row in stats['frames'] for k in row))
                with (path/'history.csv').open('w',encoding='utf-8-sig',newline='') as f:
                    writer=csv.DictWriter(f,fieldnames=keys);writer.writeheader();writer.writerows(stats['frames'])
        for n in self.run['nodes']:
            if not any(d['node']['key']==n['key'] for d in self.loaded):
                self.page(n['label']+' / 无可用结果');self.text(STATUS.get(n['status'],n['status'])+'；'+n.get('detail','原始结果未保存，不能绘制或补算数值。'))
        chapters=self.story;self.story=[];self.active_study=None
        self.introduction();self.story.extend(chapters)
        def page(canvas,doc):
            canvas.setCreator('ThermoForge Studio '+APP_VERSION+' / '+VERSION)
            canvas.saveState();canvas.setStrokeColor(HexColor('#c9dadd'));canvas.line(42,39,553,39)
            canvas.setFont(self.font,8);canvas.setFillColor(HexColor('#657881'));canvas.drawString(42,25,'ThermoForge Studio '+APP_VERSION+' / 详细图文报告 / '+VERSION)
            canvas.drawRightString(553,25,str(doc.page));canvas.restoreState()
        pending=self.root/(Path(self.filename).stem+'.pending.pdf')
        document=Document(str(pending),pagesize=A4,leftMargin=42,rightMargin=42,topMargin=39,bottomMargin=52,
            title=self.run.get('title','多物理场')+' - 详细图文报告',author='ThermoForge Studio')
        passes=document.multiBuild(self.story,onFirstPage=page,onLaterPages=page)
        pending.replace(self.root/self.filename)
        evidence={'version':VERSION,'app_version':APP_VERSION,'run_id':self.run['id'],'figure_count':self.figure_number,
            'generated_by':'ThermoForge Studio','generator':'forge.reporting.document.Report',
            'template_sha256':digest({p.name:file_hash(p) for p in Path(__file__).parent.glob('*.py')}),
            'data_origin':'platform_saved_analysis_run','appendix':False,'toc':document.toc_entries,'layout_passes':passes,
            'sources':[{'study_id':d['node']['study_id'],'raw_sha256':file_hash(d['source']/'fields.h5'),
                'config_sha256':file_hash(d['source']/'config.json'),'summary_sha256':file_hash(d['source']/'summary.json'),'display_component_aliases':d['aliases']} for d in self.loaded],
            'figures':self.records,'complete':not self.errors and len(self.loaded)==len(self.run['nodes']),'diagnostics':self.errors,
            'statistics_basis':'native nodal temperature/displacement, raw integration-point stress; contact projection explicitly separate'}
        (self.root/'report-evidence.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding='utf-8')
        return evidence


def build_report(run,root):
    return Report(run,root).build()


def build_diagnostic_report(run,root,detail):
    """Last-resort partial deliverable if a malformed chapter cannot be laid out."""
    report=Report(run,root);report.page('分析交付诊断',include_in_toc=False)
    report.text('ThermoForge Studio 已保存部分计算或导出结果，详细报告生成失败。以下内容没有补造求解数值。')
    report.contents();report.page('01  交付状态与原因');report.text(detail)
    report.story.append(report.table([['工况','状态','原始研究']]+[[n['label'],STATUS.get(n.get('status'),n.get('status')),n.get('study_id','未生成')] for n in run.get('nodes',[])],[230,100,180]))
    report.page('02  已保存结果与恢复');report.text('完整原始场、配置快照和诊断仍按实际存在的文件收录到数据包。可单独重试报告导出，不必重复求解。')
    path=Path(root)/'summary.pending.pdf';doc=Document(str(path),pagesize=A4,leftMargin=42,rightMargin=42,topMargin=42,bottomMargin=42,author='ThermoForge Studio',title='部分交付诊断')
    doc.multiBuild(report.story);path.replace(Path(root)/'summary.pdf')
    evidence={'version':VERSION,'app_version':APP_VERSION,'complete':False,'generated_by':'ThermoForge Studio','appendix':False,'toc':doc.toc_entries,'diagnostics':[{'stage':'report','detail':detail}]}
    (Path(root)/'report-evidence.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding='utf-8')
    return evidence
