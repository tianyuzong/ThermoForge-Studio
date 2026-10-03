"""Engineering reports with traceable FE figures and explicit assessment scope."""
import csv
import datetime
import html
import json
import os
import zipfile
from pathlib import Path

import numpy as np
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, Image, KeepTogether
from reportlab.platypus.tableofcontents import TableOfContents

from .report_data import REPORT_VERSION, read_json, report_evidence
from .report_figures import create_figures, comparison_figure, hotspot_components
from .store import write_json
from .result_catalog import FIELDS
from .report_locale import report_text, URL, STATUS as EXPORT_STATUS

STATUS = {'within_limit': '在已设限值内', 'exceeded': '超出限值', 'not_evaluated': '未评估'}
CONVERGENCE = {'not_verified': '未验证', 'converged': '达到本平台变化阈值', 'needs_review': '需要复核'}
LOAD_KIND = {'force': '总力', 'pressure': '压力', 'gravity': '重力'}
CRITERION = {'von_mises': '米塞斯等效应力', 'tresca': '特雷斯卡最大剪应力'}
BACKEND = {'calculix_cpu': '处理器求解', 'cudss_cuda': '显卡稀疏直接求解',
           'calculix_cuda': '显卡扩展求解'}
QUERY_STATUS = {'ok': '有效', 'partial': '部分点无定义', 'undefined': '无定义',
                'outside': '模型外', 'unavailable': '不可用'}
INK = colors.black
WIDTH = 479.28


def font_name(bold=False):
    if bold:
        if 'StudioCNBold' in pdfmetrics.getRegisteredFontNames():
            return 'StudioCNBold'
        for candidate in ['C:/Windows/Fonts/msyhbd.ttc',
                          '/usr/share/fonts/truetype/noto/NotoSansSC-Bold.ttf',
                          '/usr/share/fonts/truetype/noto/NotoSerifSC-Bold.ttf']:
            if Path(candidate).exists():
                pdfmetrics.registerFont(TTFont('StudioCNBold', candidate, subfontIndex=0))
                return 'StudioCNBold'
        raise RuntimeError('报告标题缺少中文粗体字体，请安装微软雅黑粗体或 Noto Sans SC Bold。')
    if 'StudioCN' in pdfmetrics.getRegisteredFontNames():
        return 'StudioCN'
    path = Path('C:/Windows/Fonts/simsun.ttc')
    if path.exists():
        pdfmetrics.registerFont(TTFont('StudioCN', str(path), subfontIndex=0))
        return 'StudioCN'
    if 'STSong-Light' not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(UnicodeCIDFont('STSong-Light'))
    return 'STSong-Light'


def number(value, scale=1, digits=5):
    return '未提供' if value is None else f'{value*scale:.{digits}g}'


def vector(value, scale=1):
    return '(' + ', '.join(number(float(v), scale, 5) for v in value) + ')'


def selection_text(selection):
    """Keep large resolved face lists readable; full IDs remain in config.json."""
    ids=selection.get('face_ids',[])
    scope=('C'+str(selection['body_id'])+' / ') if selection.get('body_id') else ''
    if ids:
        return scope+'面 '+str(ids[:12])+(f' 等，共 {len(ids)} 个面' if len(ids)>12 else '')
    return scope+str(selection.get('name') or selection.get('direction') or selection.get('kind') or '见配置')


def conclusion(summary):
    """A numerical pass alone cannot produce an unconditional design approval."""
    if not summary['balance']['passed']:
        return '数值平衡未通过，当前结果不能用于强度或刚度合格判定。'
    check = summary['checks']['strength']['status']
    if check == 'within_limit':
        text = '在本次材料、载荷和支撑假设下，原始积分点静态屈服安全系数达到已设目标。'
    elif check == 'exceeded':
        text = '本次静态强度计算超出已设限值，需要复核材料、结构和实际工况。'
    else:
        text = '缺少完整材料屈服数据，静态强度尚未评估。'
    displacement = summary['checks']['displacement']['status']
    text += {'not_evaluated': '未设置位移限值，刚度不作合格判定。',
             'within_limit': '位移在用户设置限值内；该检查不等同于运行间隙或干涉校核。',
             'exceeded': '位移超出用户设置限值。'}[displacement]
    if summary['convergence']['status'] != 'converged':
        text += '网格收敛尚未通过，峰值和裕量需进一步复核。'
    if summary.get('contact'):
        text += '接触疲劳、齿根弯曲强度及运行干涉尚未评估，不能据此断言不会点蚀、断齿或卡齿。'
    if summary.get('bonding'):
        text += '这是理想粘结装配试算；连接不允许分离、滑移或相对转动，不能代表真实滚动轴承或螺栓连接性能。'
    text += '尚未完成同条件 Ansys 数值对照。'
    return text


def contents_description(title, summary):
    """Describe what the section contains without inventing an assessment."""
    for label, description in [
        ('概述与本次结论', '本工况的静力分析范围、主要结果与未评估项目。'),
        ('模型与材料参数', '参与计算的组件、材料参数、来源及几何尺寸。'),
        ('网格划分与质量证据', '实体网格规模、质量检查和加密设置。'),
        ('边界条件与载荷', '本次计算施加的载荷、固定约束和作用位置。'),
        ('装配连接与传力证据', '理想粘结接口的传力、位移连续性及模型限制。'),
        ('整体位移与弹性变形', '静力载荷下的总位移云图、组件变形与峰值位置。'),
        ('弹性位移与逐组件结果', '扣除轴端刚体转角后的弹性位移及组件结果。'),
        ('等效应力与组件静强度', '原始积分点应力与组件屈服评估；缺少屈服数据时标为未评估。'),
        ('齿面接触压力与传力', '当前静态位置的离散接触压力和法向传力。'),
        ('接触区局部采样可视化', '实际接触采样点与压力分布，不代表接触疲劳仿真。'),
        ('局部应力与齿根评估状态', '组件应力热点与齿根专项校核的已评估范围。'),
        ('局部危险位置', '指定组件的局部应力云图和原始积分点热点。'),
        ('指定选区局部应力', '选面相邻实体单元的原始积分点应力统计。'),
        ('轴孔邻域与支撑反力', '轴孔邻近应力及约束反力，不作轴承接触强度结论。'),
        ('安全系数与数值核验', '材料强度评估、整体平衡及三档网格变化检查。' if summary.get('convergence',{}).get('levels') else '材料强度评估、整体平衡与网格验证状态；未执行三档检查时明确标注。'),
        ('支撑反力与反力矩', '支撑反力、指定原点的反力矩及总弹性应变能。'),
        ('结果分析与讨论', '应力集中、变形特征和本工况的适用限制。'),
        ('优化与后续验证建议', '基于当前结果提出后续工作，不代表这些仿真已实施。'),
        ('多工况结果比较', '对照实际已求解工况的应力、位移和评估状态。'),
        ('求解与数据追溯', '求解后端、线程、耗时和原始数据标识。'),
        ('完整静力结果目录', '位移、应力、应变等指标，以及实际执行的探针和路径查询。'),
    ]:
        if label in title:return description
    if title.strip().startswith('7'):return '汇总本次已计算结果和仍未验证的结论边界。'
    return ''


class ReportDocument(SimpleDocTemplate):
    def beforeDocument(self):
        super().beforeDocument()
        # Stable destinations are essential: multiBuild compares successive TOCs.
        self._sections = 0

    def afterFlowable(self, flowable):
        if isinstance(flowable, Paragraph) and flowable.style.name in ('SectionCN', 'SubCN'):
            title = flowable.getPlainText()
            key = f'section-{self._sections}'
            self._sections += 1
            level = 0 if flowable.style.name == 'SectionCN' else 1
            self.canv.bookmarkPage(key)
            self.canv.addOutlineEntry(title, key, level=level)
            entry = html.escape(title)
            note = getattr(flowable, '_contents_note', '')
            if note:entry += f'<br/><font name="{font_name()}" size="11.5">{html.escape(note)}</font>'
            self.notify('TOCEntry', (level, entry, self.page, key))


def build_pdf(destination, folder, summary, evidence, figures, comparisons=()):
    s = summary; c = s['configuration']['case']; contact = s.get('contact')
    font = font_name(); bold_font = font_name(bold=True)
    body = ParagraphStyle('BodyCN', fontName=font, fontSize=12, leading=19, textColor=INK,
                          wordWrap='CJK', spaceAfter=7)
    small = ParagraphStyle('CaptionCN', parent=body, fontSize=10.5, leading=16, spaceAfter=9)
    title = ParagraphStyle('TitleCN', parent=body, fontName=bold_font, fontSize=28, leading=38, spaceAfter=13)
    heading = ParagraphStyle('SectionCN', parent=body, fontName=bold_font, fontSize=18, leading=26, spaceBefore=4, spaceAfter=12, keepWithNext=True)
    sub = ParagraphStyle('SubCN', parent=body, fontName=bold_font, fontSize=14, leading=21, spaceBefore=6, spaceAfter=8, keepWithNext=True)
    toc_title = ParagraphStyle('ContentsTitleCN', parent=heading)
    toc = TableOfContents(dotsMinLevel=0, levelStyles=[
        ParagraphStyle('ContentsSectionCN', parent=body, fontName=bold_font, fontSize=12.5,
                       leading=21, spaceBefore=7, spaceAfter=0, rightIndent=42),
        ParagraphStyle('ContentsSubCN', parent=body, fontSize=12, leading=20,
                       spaceBefore=2, spaceAfter=0, leftIndent=18, rightIndent=42)])
    cell = ParagraphStyle('TableCN', parent=body, fontSize=11, leading=16, spaceAfter=0)
    cell_heading = ParagraphStyle('TableHeadingCN', parent=cell, fontName=bold_font)
    def p(text, style=body, contents_note=None):
        # Source URLs remain clickable without exposing English URLs as labels.
        value = report_text(text); chunks = []; start = 0
        for index, match in enumerate(URL.finditer(value), 1):
            chunks.append(html.escape(value[start:match.start()]))
            chunks.append(f'<link href="{html.escape(match.group(), quote=True)}">来源链接 {index}</link>')
            start = match.end()
        chunks.append(html.escape(value[start:]))
        paragraph = Paragraph(''.join(chunks).replace('\n', '<br/>'), style)
        if style.name in ('SectionCN', 'SubCN'):
            paragraph._contents_note = contents_description(value, s) if contents_note is None else contents_note
        return paragraph
    def table(rows, widths=None, header=True):
        widths = widths or [WIDTH/len(rows[0])]*len(rows[0])
        content = [[p(v, cell_heading if (header and i == 0) or (not header and j == 0) else cell)
                    for j, v in enumerate(row)] for i, row in enumerate(rows)]
        t = Table(content, colWidths=widths, repeatRows=1 if header else 0, hAlign='LEFT')
        styles = [('VALIGN', (0,0),(-1,-1),'TOP'), ('LEFTPADDING',(0,0),(-1,-1),7),
                  ('RIGHTPADDING',(0,0),(-1,-1),7), ('TOPPADDING',(0,0),(-1,-1),8),
                  ('BOTTOMPADDING',(0,0),(-1,-1),7), ('LINEBELOW',(0,0),(-1,-1),.35,colors.HexColor('#dbe5ea'))]
        if header:
            styles += [('BACKGROUND',(0,0),(-1,0),colors.HexColor('#e5f1f3'))]
        else:
            styles += [('BACKGROUND',(0,0),(0,-1),colors.HexColor('#edf4f6'))]
        t.setStyle(TableStyle(styles))
        # Keep ordinary component tables intact; long result catalogs still
        # split across pages with repeated headers at the same readable size.
        _, height = t.wrap(WIDTH, 700)
        return KeepTogether([t]) if height <= 650 else t
    def fig(path, caption, height=None):
        from PIL import Image as PILImage
        with PILImage.open(path) as im: w,h=im.size
        # Legacy height hints must not shrink full-width figures and labels.
        width = WIDTH; height=width*h/w
        return [KeepTogether([Image(str(path), width=width, height=height, hAlign='CENTER'), Spacer(1,8), p(caption, small)])]
    def page(label):
        story.extend([PageBreak(), p(label, heading)])
    story = [p('结构力学工作台 / 工程分析报告', small),
             p('三齿轮传动结构\n有限元仿真结果报告' if contact and len(evidence['components'])==3 else '结构强度与刚度\n有限元仿真结果报告', title),
             p(s['name']),
             table([['报告版本 / 计算日期',f'V{REPORT_VERSION} / '+datetime.datetime.fromtimestamp(s['created_at']).strftime('%Y-%m-%d %H:%M')],
                    ['实际求解器',s['solver']], ['分析范围','固定位置静态接触与线弹性实体响应' if contact else '小变形线弹性静力分析']], widths=[100,WIDTH-100], header=False)]
    story += fig(figures['stress'], '封面图：实际节点平滑 von Mises 应力场。定量强度结论采用原始积分点数据。', 230)
    story += [table([['最大等效应力 / MPa','最大总位移 / mm','最小屈服安全系数'],
                     [number(s['maximum_von_mises_Pa'],1e-6),number(s['maximum_displacement_m'],1000),number(s['minimum_safety_factor'])]]),
              PageBreak(), p('目录与分析内容', toc_title), p('条目下方说明本节实际分析或展示的内容；建模说明和后续建议不代表额外完成的仿真。点击名称或页码可跳转，页码与页脚一致。', small), toc,
              PageBreak(), p('1  概述与本次结论', heading), p(conclusion(s)),
              p('研究目的：识别当前啮合位置和输入载荷下的应力集中、弹性变形、轴孔邻域响应与接触传力，作为后续网格验证和设计评估的依据。' if contact else '研究目的：评价当前工况的静态应力、变形与已设置限值，定位需要进一步复核的区域。', small)]
    page('2  模型与材料参数')
    story += fig(figures['model'], '图 1  真实有限元外表面与组件编号。C1、C2、C3 对应项目中的组件 ID；未修改原始啮合位置。' if contact else '图 1  实际模型外表面与组件编号。', 215)
    rows=[['组件 / 材料','E / GPa；泊松比','密度 / kg/m³','屈服 / MPa']]
    for comp in evidence['components']:
        m=comp['material'];rows.append([f'C{comp["body_id"]}\n{m["name"]}',f'{number(m["young_modulus_Pa"],1e-9)}；{m["poisson_ratio"]:g}',number(m['density_kg_m3']),number(m.get('yield_strength_Pa'),1e-6)])
    story += [table(rows,[160,112,103,WIDTH-375])]
    sources={}
    for comp in evidence['components']:
        sources.setdefault(comp['material']['source'],[]).append(comp['body_id'])
    for source,ids in sources.items():
        story.append(p('组件 '+', '.join('C'+str(i) for i in ids)+' 材料来源：'+source,small))
    rows=[['组件','X × Y × Z 包围尺寸 / mm','单元 / 节点']]
    for comp in evidence['components']:
        size=np.ptp(np.array(comp['bounds_m']),axis=0)*1000
        rows.append([f'C{comp["body_id"]}',' × '.join(number(x) for x in size),f'{comp["elements"]:,} / {comp["nodes"]:,}'])
    story += [table(rows,[65,230,WIDTH-295]),p('上述尺寸来自实际网格坐标。齿数、模数、压力角、加工精度与热处理状态未在当前项目中提供，不用示例参数代替实物参数。' if contact else '尺寸来自实际网格坐标；材料与边界参数来自本工况的不可变配置快照。',small),
              p('仿真假设',sub),p(contact['assumptions'] if contact else '各组件均匀、各向同性；小变形线弹性静力。未引入动态载荷、温度、塑性或疲劳模型。')]
    page('2.3  网格划分与质量证据')
    story += fig(figures['mesh'],'图 2  实际外表面网格。图中每个六节点三角面细分为四个显示三角形；体单元为 C3D10。',270)
    q=evidence['mesh_quality'];mesh=s['mesh'];ms=c['mesh']
    story += [table([['网格类型','C3D10 二次四面体；四积分点原始应力'],['规模',f'{mesh["elements"]:,} 单元 / {mesh["nodes"]:,} 节点 / {mesh["nodes"]*3:,} 自由度'],
                     ['全局实际目标尺寸',number(mesh['size_m'],1000)+' mm'],['局部加密',('尺寸 '+number(ms.get('local_size_m'),1000)+' mm；'+ '；'.join(selection_text(a) for a in ms['local_selections'][:6])+('；更多选区见 config.json' if len(ms['local_selections'])>6 else '')) if ms.get('local_selections') else '当前工况未配置局部加密'],
                     ['最小高阶 Jacobian',number(mesh.get('min_jacobian'))+' m³；来自 Gmsh 网格检查'],['角点四面体平均比',f'均值 {q["mean"]:.4f} / 最小 {q["minimum"]:.3g} / 5%分位 {q["p05"]:.4f}'],
                     ['网格收敛',CONVERGENCE[s['convergence']['status']]]],[135,WIDTH-135],False),p(q['note'],small),
              p('本报告不套用“平均质量 0.82”或“最小质量大于 0.6”等示例数字。节点/单元数量不能单独证明齿面压力与齿根应力已经收敛。',small)]
    if q.get('below_screening_005',0):
        story.append(p(f'网格复核项：{q["below_screening_005"]} 个单元的角点平均比低于 0.05（报告筛查阈值，非 Ansys 质量标准）。应定位并复核细长/扁平单元与关键区域的关系，不能只依据正 Jacobian 判定网格足够准确。',small))
    page('3  边界条件与载荷')
    story += fig(figures['boundary'],'图 3  约束接口（蓝色）与载荷/轴端标记。位置采用实际选区和轴心。',255)
    if contact:
        story += [p('所有轴心平移与倾斜锁定，孔壁与刚性轴耦合。自由轴允许绕全局 Z 的小转角；输出轴转角锁止，输出阻力矩由计算求得。这是刚性轴接口模型，未建立轴承刚度。'),
                  table([['组件','轴孔面 ID','轴端条件','输入 Tz / N·m']]+[[f'C{h["body_id"]}',str(h['bore']['face_ids']),'绕 Z 锁止' if h['rotation_locked'] else '绕 Z 自由',number(h['torque_Nm'])] for h in c['gear_contact']['hubs']],[60,115,185,WIDTH-360]),
                  p('接触设置：无摩擦、单侧压缩、小滑移、固定法向。CPU 主动集求接触力，GPU 求弹性柔度与实体响应。当前不是摩擦系数 0.15 的增广拉格朗日模型，也没有将本位置识别为整个啮合周期最危险位置。'),
                  p('输入方向遵循全局 +Z 右手定则；转角和反力矩均采用相同全局坐标。重力、摩擦、冲击载荷未施加。',small)]
    else:
        story.append(table([['约束','面 / 分量','给定位移 / mm']]+[[a['name'],selection_text(a['selection'])+' / '+','.join(a['components']),vector(a['displacement_m'],1000)] for a in c['supports']],[115,200,WIDTH-315]))
        story.append(table([['载荷','类型与作用域','输入值']]+[[a['name'],LOAD_KIND.get(a['kind'],a['kind'])+' / '+(selection_text(a['selection']) if a.get('selection') else str(a.get('body_id') or '全部组件')),number(a['pressure_Pa'],1e-6)+' MPa' if a['kind']=='pressure' else vector(a['vector'])+(' m/s²' if a['kind']=='gravity' else ' N')] for a in c['loads']],[110,235,WIDTH-345]))
        story.append(p('正压力压向实体内部；总力按选区面积形成均匀牵引，并通过二次形函数积分为一致节点载荷。',small))
    if s.get('bonding'):
        bond=s['bonding'];page('3.1  装配连接与传力证据')
        story.extend([p(bond['scope']),p(f'本次计算包含 {bond["connected_components"]} 个组件、{len(bond["interfaces"])} 个接口、{bond["tied_nodes"]} 个粘结从节点；最大相对位移兼容误差 {bond["compatibility_relative"]:.3g}。'),
            p('下表为作用于从组件的接口合力。小间隙接口采用相邻主单元形函数延拓；逐接口假设、真实面 ID、映射距离和关于全局原点的传力矩保存在 bonding.json。未找到有效映射的接口会阻止求解。',small),
            table([['接口：从 → 主','从侧节点数','接口合力 XYZ / N']]+[[a['name']+'\n'+f'C{a["slave_body"]} → C{a["master_body"]}',str(a['tied_nodes']),vector(a['transfer_to_slave_N'])] for a in bond['interfaces']],[200,65,WIDTH-265])])
    page('4.1  整体位移与弹性变形')
    story += fig(figures['displacement'],'图 4  总位移幅值。颜色单位 mm；几何未放大变形。',215)
    if 'elastic' in figures:
        page('4.1  弹性位移与逐组件结果')
        story += fig(figures['elastic'],'图 5  弹性位移幅值，已扣除齿隙闭合所需的轴端刚体转角。两图色标独立。',215)
    story.append(table([['组件','最大总位移 / mm','最大弹性位移 / mm']]+[[f'C{a["body_id"]}',number(a['maximum_displacement_m'],1000),number(a['maximum_elastic_displacement_m'],1000)] for a in evidence['components']],[75,200,WIDTH-275]))
    story += [p('位移检查：'+STATUS[s['checks']['displacement']['status']]+'。限值：'+(number(c['displacement_limit_m'],1000)+' mm' if c.get('displacement_limit_m') else '未设置')+'。最大总位移节点 '+str(s['displacement_peak']['node'])+'，坐标 '+vector(s['displacement_peak']['position_m'],1000)+' mm。',small)]
    if contact:story.append(p('总位移包含轴转角，不能直接与齿侧间隙比较后断言“不干涉”。运行干涉还需明确间隙定义，并检查相对运动与啮合周期。',small))
    page('4.2  等效应力与组件静强度')
    story += fig(figures['stress'],'图 6  节点平均 von Mises 应力云图。图例极值属于显示场，表格极值来自未平滑积分点，两者可以不同。',285)
    story.append(table([['组件','原始峰值 / MPa','最小屈服安全系数','检查状态']]+[[f'C{a["body_id"]}',number(a['maximum_von_mises_Pa'],1e-6),number(a['minimum_safety_factor']),STATUS[a['strength_status']]] for a in evidence['components']],[65,125,145,WIDTH-335]))
    story += [p('全局峰值：'+number(s['maximum_von_mises_Pa'],1e-6)+' MPa；单元 '+str(s['stress_peak']['element'])+' / 积分点 '+str(s['stress_peak']['integration_point'])+'；坐标 '+vector(s['stress_peak']['position_m'],1000)+' mm。'),
              p('评价方法：各组件使用自己的屈服强度与原始积分点 '+CRITERION[c.get('strength_criterion','von_mises')]+' 判据计算安全系数；目标为 '+number(c['target_safety_factor'])+'。最大主应力、局部等效应力和齿根截面弯曲应力是不同指标。',small)]
    if contact:
        page('4.3  齿面接触压力与传力')
        story += fig(figures['contact'],'图 7  离散接触压力的表面加权显示。零值区域表示当前未映射到活跃接触压力；显示图不能替代接触积分点原始记录。',275)
        story.append(table([['接触对','法向力 / N','采样峰值 / MPa','活跃 / 候选点']]+[[f'C{a["slave_body"]}-C{a["master_body"]}',number(a['normal_force_N']),number(a['maximum_pressure_Pa'],1e-6),f'{a["active_points"]} / {a["candidate_points"]}'] for a in contact['pairs']],[80,125,135,WIDTH-340]))
        story += [p(contact['pressure_basis']),p('许用接触应力：未提供。接触压力网格收敛和 Hertz 基准：未完成。因此本节只报告当前离散模型的压力与传力，接触强度和点蚀寿命均未评估。'),
                  p('接触初始最小采样间隙 '+number(contact['minimum_initial_sample_gap_m'],1000)+' mm；最终最小采样间隙 '+number(contact['minimum_final_sample_gap_m'],1000)+' mm。这些是接触采样值，不是整个 CAD 的全局干涉检查。',small)]
        for offset in range(0,len(figures['contact_pairs'])):
            page('4.3  接触区局部采样可视化')
            for i,path in enumerate(figures['contact_pairs'][offset:offset+1],start=offset):
                pair=contact['pairs'][i]
                story+=fig(path,f'图 8.{i+1}  C{pair["slave_body"]}-C{pair["master_body"]}：横轴为局部切向坐标，纵轴为全局 Z。彩点为活跃积分采样，灰点为未接触候选；点大小仅用于显示。',245)
            story.append(p('本图直接绘制 contact.json 中的积分采样点和压力，没有插值成连续 Hertz 分布。不同接触对使用各自色标。',small))
    for offset in range(0,len(figures['hotspots'])):
        body_id=figures.get('hotspot_body_ids',[a['body_id'] for a in evidence['components']])[offset]
        page(('4.4  局部应力与齿根评估状态' if contact else '4.3  局部危险位置')+f' · 组件{body_id}')
        for i,path in enumerate(figures['hotspots'][offset:offset+1],start=offset):
            bid=figures.get('hotspot_body_ids',[a['body_id'] for a in evidence['components']])[i]
            comp=next(a for a in evidence['components'] if a['body_id']==bid);peak=comp['stress_peak']
            story+=fig(path,f'图 9.{i+1}  C{comp["body_id"]} 峰值附近放大。原始峰值 {number(peak["value_Pa"],1e-6)} MPa；单元 {peak["element"]} / 积分点 {peak["integration_point"]}；位置 {vector(peak["position_m"],1000)} mm。圆点为积分点投影，可能位于表面以下。',220)
        if contact: story += [p('齿根弯曲应力：未评估。',sub),p(evidence['root_bending_note'],small),p('需要在真实齿根选区中定义提取方向/截面、校核方法及材料弯曲许用值，再对局部网格做收敛检查。未使用示例的 320 MPa 许用值。',small)]
    for i, region in enumerate(evidence.get('stress_regions', [])):
        page('4.4  指定选区局部应力')
        story += [p(region['name'],sub),p(f'C{region["body_id"]} · {region["label"]} · 真实面 ID：'+selection_text({'face_ids':region['face_ids']})),
                  p('全局单位方向：'+str(region['direction']) if region['direction'] else '主应力为应力张量的最大特征值，不需要指定方向。',small)]
        story += fig(figures['stress_regions'][i], '指定面着色，灰色为其余模型背景。颜色取相邻单元四个积分点的算术平均；最大/最小值来自未平均的原始积分点。',280)
        rows=[['指标','数值 / MPa','位置 / mm','单元 / 积分点']]
        for key,label in [('maximum','最大值'),('minimum','最小值')]:
            peak=region[key]
            rows.append([label,number(peak['value_Pa']/1e6),vector(peak['position_m'],1000),f'{peak["element"]} / {peak["integration_point"]}'])
        story += [table(rows,[65,95,WIDTH-240,80]),p(f'选区相邻单元 {region["element_count"]} 个，统计积分点 {region["integration_point_count"]} 个。',small),p(region['note'],small),p('齿根弯曲强度判定：未评估。此处不借用屈服安全系数作为弯曲疲劳安全系数。',small)]
    if contact:
        page('4.4  轴孔邻域与支撑反力')
        story.append(table([['组件','轴孔邻近积分点峰值 / MPa','单元 / 积分点']]+[[f'C{a["body_id"]}',number(a['bore_adjacent_peak']['value_Pa'],1e-6) if a['bore_adjacent_peak'] else '未定义评估选区',f'{a["bore_adjacent_peak"]["element"]} / {a["bore_adjacent_peak"]["integration_point"]}' if a['bore_adjacent_peak'] else '-'] for a in evidence['components']],[65,250,WIDTH-315]))
        story += [p('提取范围：配置中轴孔表面相邻体单元的四积分点。刚性耦合边缘可能产生网格敏感峰值，该数值不等同于实际轴承接触应力。',small),
                  table([['组件','轴端反力 (X,Y,Z) / N','轴端力矩 (X,Y,Z) / N·m','绕 Z 转角 / rad']]+[[f'C{h["body_id"]}',vector(h['reaction_N']),vector(h['shaft_torque_Nm']),number(h['rotation_rad'])] for h in contact['shaft_results']],[50,160,165,WIDTH-375]),
                  p('输入轴反力矩包含电机传入的扭矩；自由惰轮 Z 轴合力矩应接近零；锁止输出轴的阻力矩由平衡求得。轴端力矩关于各自轴心统计，整体平衡关于统一参考原点统计。',small)]
    page('4.5  安全系数与数值核验')
    if 'safety' in figures:
        story+=fig(figures['safety'],'图 10  节点显示屈服安全系数，色标上限截为 10。最终最小安全系数仍取原始积分点；图中截断不影响评估。',230)
    story += [table([['核验项目','结果','判定依据'],['静态屈服安全系数',number(s['minimum_safety_factor']),'目标 ≥ '+number(c['target_safety_factor'])],
                     ['整体力平衡',f'{s["balance"]["force_relative"]:.3e}','相对残差 ≤ 1e-4'],['整体力矩平衡',f'{s["balance"]["moment_relative"]:.3e}','相对残差 ≤ 1e-4'],
                     ['自由度内力残差',f'{s["balance"]["free_dof_relative"]:.3e}','相对残差 ≤ 1e-4'],['网格收敛',CONVERGENCE[s['convergence']['status']],'后两档位移变化 ≤ 2%，应力变化 ≤ 5%'],
                     ['Ansys 同条件对照','尚未验证','需要相同模型、材料、载荷、约束、接触及结果提取口径']],[135,130,WIDTH-265])]
    if contact:story.append(p(f'轴扭矩相对残差：{contact["torque_relative_residual"]:.3e}；接触互补误差：{contact["complementarity_max_Nm"]:.3e} N·m。数值方程满足不等同于接触离散和物理假设已经验证。',small))
    if 'levels' in s['convergence']:
        story.append(table([['网格尺寸 / mm','位移 / mm','评估应力 / MPa','全局峰值 / MPa','应变能 / J']]+[[number(a['mesh']['size_m'],1000),number(a['displacement_m'],1000),number(a['stress_Pa'],1e-6),number(a['global_peak_Pa'],1e-6),number(a.get('strain_energy_J'))] for a in s['convergence']['levels']]))
    if not contact and s.get('supports'):
        page('4.6  支撑反力与反力矩')
        origin=s['balance']['moment_origin_m']
        story += [p('统一力矩参考点 (X, Y, Z)：'+vector(origin,1000)+' mm。反力按约束自由度去重；重叠约束自由度归入先出现的支撑。',small),
            table([['支撑','反力 (X,Y,Z) / N','反力矩 (X,Y,Z) / N·m']]+[[r.get('name','支撑'),vector(r['reaction_N']),vector(r['moment_Nm'])] for r in s['supports']],[95,192,WIDTH-287]),
            p('总反力：'+vector(s['balance']['reaction_N'])+' N。'),
            p('总弹性应变能：'+number(s.get('strain_energy_J'))+' J。积分采用原始积分点应力与弹性应变，包含张量剪切项。',small)]
    page('5  结果分析与讨论')
    danger=min([a for a in evidence['components'] if a['minimum_safety_factor'] is not None],key=lambda a:a['minimum_safety_factor'],default=None)
    if danger:
        story.append(p(f'本次静态屈服裕量最低的组件为 C{danger["body_id"]}，最小安全系数 {number(danger["minimum_safety_factor"])}。'))
        critical=s.get('safety_factor_minimum')
        if critical:story.append(p(f'所选判据安全系数最低位置：单元 {critical["element"]} / 积分点 {critical["integration_point"]}；坐标 '+vector(critical['position_m'],1000)+' mm。该位置可能与最大 von Mises 应力位置不同。',small))
    story += [p('应力峰值受几何细节、约束方式、接触离散和局部网格影响。当前结果描述一个明确的静态位置，不能覆盖完整啮合周期中的动载、载荷分配或最危险位置。' if contact else '应力峰值可能受固定边缘、尖角和局部网格影响。当前结果只适用于本次材料与边界条件。')]
    for warning in s['warnings']: story.append(p('• '+warning,small))
    story += [p('6  优化与后续验证建议',heading),p('1. 根据局部峰值坐标确认几何区域，保持载荷与支撑不变，对该处和相邻传力区域做局部加密与三档收敛。'),
              p('2. 优先补齐实际材料/热处理数据、设计位移限值和真实轴承/连接条件；不要只提高材料屈服值来改变报告结论。'),
              p('3. 若峰值经确认位于齿根圆角，可建立圆角参数变体比较应力与刚度；几何未确认前，不将该建议作为已证明有效的优化。' if contact else '3. 在确认应力集中来源后，对圆角、厚度或支撑位置建立独立变体，以同一载荷和评价区域比较结果。')]
    if contact: story.append(p('4. 需要点蚀、疲劳或高速传动结论时，补充接触基准、啮合位置扫描、实际载荷谱及相应疲劳/动力学模型。'))
    story += [p('7  结论',heading),p(conclusion(s)),p('本报告为实际计算记录。未将教学材料当作实物牌号证明，未将未计算的接触疲劳、齿根弯曲或干涉项目写成“合格”。',small)]
    if comparisons:
        for offset in range(0,len(comparisons),4):
            group=comparisons[offset:offset+4]
            page('附录 A  多工况结果比较')
            path=Path(folder)/'report-figures'/f'comparison-{offset//4+1}.png'
            comparison_figure(path,group)
            story+=fig(path,'各图使用独立纵向分组和量纲；同一指标在本页工况之间采用统一比例。',245)
            rows=[['图中工况 / 名称','应力 / MPa','总位移 / mm','弹性位移 / mm','安全系数']]
            for i,item in enumerate(group):
                rows.append([f'工况 {i+1}\n{item["name"]}',number(item['maximum_von_mises_Pa'],1e-6),number(item['maximum_displacement_m'],1000),number(item.get('contact',{}).get('maximum_elastic_displacement_m'),1000),number(item['minimum_safety_factor'])])
            story += [table(rows,[170,77,77,80,WIDTH-404]),p('主报告详述工况：'+s['name']+'。比较表中的其他工况均为独立求解结果，不通过缩放图片或替换数值生成。',small)]
            same_mesh=len({a['mesh_sha256'] for a in group})==1
            same_geometry=len({a['geometry_sha256'] for a in group})==1
            story.append(p(f'本页工况使用相同几何：{"是" if same_geometry else "否"}；相同网格：{"是" if same_mesh else "否"}。材料和边界条件以每个工况快照为准；不同条件的比较不能单独归因于某一个参数。',small))
    page('附录 B  求解与数据追溯')
    execution=s.get('execution',{})
    story += [table([['任务 / 作业',s['configuration'].get('task_id','-')+' / '+s['job_id']],['实际后端',BACKEND.get(execution.get('backend','calculix_cpu'),execution.get('backend'))],
                     ['CPU 求解线程',str(execution.get('cpu_threads','见求解日志'))],['求解器 SHA-256',execution.get('executable_sha256','见执行记录')],
                     ['计算精度',execution.get('precision','见 CalculiX 求解日志')],['计算阶段耗时',number(execution.get('compute_s',s['elapsed_s']))+' s'],
                     ['分解复用','是' if execution.get('factorization_reused') else '否'],['几何 SHA-256',s['geometry_sha256']],['网格 SHA-256',s['mesh_sha256']],
                     ['原始结果','fields.h5：积分点应力/应变、位移、反力与显示场'],['配置与日志','config.json / summary.json / solver.log / gpu-execution.json'],
                     ['报告派生数据','report-evidence.json / report-figures/*.png / metrics.csv']],[110,WIDTH-110],False)]
    if contact: story.append(p('contact.json 保存接触候选、初始/最终间隙、法向力与压力。analysis.inp 是最终接触力对应的固定轴孔弹性参考问题，仅用于 CPU 弹性场对照，不是独立齿轮接触求解输入。'))
    story += [p('单位约定：计算采用 m、N、Pa；报告主要采用 mm、N、MPa。应力分量顺序 xx、yy、zz、xy、xz、yz。'),
              p('图像方法：直接投影保存的有限元外表面，按相机深度绘制三角形；局部视图和色标均来自实际场数据。积分点极值与平滑显示值严格区分。'),
              p('报告渲染版本 '+REPORT_VERSION+'；生成时间 '+datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')+'。重新生成报告不改变原始材料、载荷、网格或计算结果。',small)]
    if s.get('field_statistics'):
        page('附录：完整静力结果目录')
        story += [p('应力和应变统计使用原始积分点；位移使用节点。积分点平均值按实际积分体积加权。无定义的值不以零或任意上限替代。',small),
            p('强度判据：'+CRITERION[c.get('strength_criterion','von_mises')]+'；总弹性应变能：'+number(s.get('strain_energy_J'))+' J。'),
            p('张量剪应变与工程剪应变区分输出：XY 工程剪应变等于 XY 张量剪应变的两倍；等效弹性应变使用材料泊松比。',small)]
        rows=[['指标 / 单位','最小值','最大值','平均值']]
        for key,stat in s['field_statistics'].items():
            meta=FIELDS[key];scale=meta['factor']
            rows.append([meta['label']+' / '+(meta['display_unit'] or '无量纲'),
                number(stat.get('minimum',{}).get('value'),scale),number(stat.get('maximum',{}).get('value'),scale),number(stat.get('mean'),scale)])
        story.append(table(rows,[200,93,93,WIDTH-386]))
        for result in s.get('result_queries',[]):
            note={'probe':'按已指定测点提取结果；坐标点采用单元插值。',
                  'path':'沿指定直线路径提取插值结果；模型外采样留空。',
                  'stiffness':'指定载荷与方向位移的比值，响应包含该工况全部载荷。',
                  'relative':'两个指定测点之间的相对位移。',
                  'reactions':'所选支撑或区域的反力与关于指定原点的反力矩。'}.get(result.get('kind'),'')
            if result.get('status') in ('outside','undefined','unavailable'):
                note='查询未取得有效结果；本节记录原因，不作为已完成仿真指标。'
            story += [p(result['name'],sub,contents_note=note),p(result.get('basis',result.get('scope',result.get('reason',''))),small)]
            if 'samples' in result:
                rows=[['路径距离 / mm','结果 / '+FIELDS[result['field']]['display_unit'],'状态']]
                for sample in result['samples']:
                    rows.append([number(sample['distance_m'],1000),number(sample.get('value'),FIELDS[result['field']]['factor']),QUERY_STATUS.get(sample['status'],sample['status'])])
                story.append(table(rows,[140,180,WIDTH-320]))
            else:
                from .feedback import format_fact
                story.append(p(format_fact({'value':result,'unit':result.get('unit','')}),small))
    def footer(cv,doc):
        cv.setStrokeColor(colors.HexColor('#d7e5e9'));cv.line(58,800,537,800)
        cv.setFont(font,9.5);cv.setFillColor(INK)
        cv.drawString(58,813,'结构力学工作台 / 结构有限元分析')
        cv.drawRightString(537,813,'版本 '+REPORT_VERSION+' · '+s['job_id'][:8])
        cv.drawString(58,25,'实际求解结果 | 教学假设与未评估项目见正文')
        cv.drawRightString(537,25,str(doc.page))
    target=Path(destination);temporary=target.with_suffix('.building.pdf')
    try:
        ReportDocument(str(temporary),pagesize=(595.28,841.89),leftMargin=58,rightMargin=58,
                       topMargin=54,bottomMargin=48,title=report_text(s['name'])+' - 结构有限元分析',author='结构力学工作台').multiBuild(story,onFirstPage=footer,onLaterPages=footer)
        os.replace(temporary,target)
    finally:
        if temporary.exists():temporary.unlink()


def write_metrics(folder,s,evidence):
    if s.get('field_statistics'):
        with (Path(folder)/'field-statistics.csv').open('w',encoding='utf-8-sig',newline='') as f:
            w=csv.writer(f);w.writerow(['指标','国际单位','最小值','最大值','平均值','最小位置 / m','最大位置 / m','平均方式','状态'])
            for key,stat in s['field_statistics'].items():
                lo,hi=stat.get('minimum',{}),stat.get('maximum',{})
                w.writerow([report_text(FIELDS[key]['label']),FIELDS[key]['unit'],lo.get('value'),hi.get('value'),stat.get('mean'),lo.get('position_m'),hi.get('position_m'),stat.get('mean_basis'),EXPORT_STATUS.get(stat['status'],stat['status'])])
    with (Path(folder)/'metrics.csv').open('w',encoding='utf-8-sig',newline='') as f:
        writer=ChineseCSVWriter(csv.writer(f));writer.writerow(['指标','数值','单位','状态'])
        writer.writerows([['最大等效应力',s['maximum_von_mises_Pa']/1e6,'MPa',STATUS[s['checks']['strength']['status']]],
                         ['最大位移',s['maximum_displacement_m']*1000,'mm',STATUS[s['checks']['displacement']['status']]],
                         ['最小屈服安全系数',s['minimum_safety_factor'],'',STATUS[s['checks']['strength']['status']]],
                         ['总弹性应变能',s.get('strain_energy_J'),'J','已计算' if s.get('strain_energy_J') is not None else '历史结果未保存'],
                         ['力平衡相对残差',s['balance']['force_relative'],'',''],['力矩平衡相对残差',s['balance']['moment_relative'],'','']])
        for support in s.get('supports',[]):
            prefix=support.get('name','支撑')+' '
            for index,axis in enumerate('XYZ'):
                writer.writerow([prefix+axis+' 反力',support['reaction_N'][index],'N','约束自由度去重'])
                writer.writerow([prefix+axis+' 反力矩',support['moment_Nm'][index],'N*m','参考点 '+str(support.get('moment_origin_m',s['balance']['moment_origin_m']))])
        for comp in evidence['components']:
            prefix=f'C{comp["body_id"]} '
            writer.writerows([[prefix+'最大等效应力',comp['maximum_von_mises_Pa']/1e6,'MPa',STATUS[comp['strength_status']]],
                             [prefix+'总位移',comp['maximum_displacement_m']*1000,'mm',''],[prefix+'弹性位移',comp['maximum_elastic_displacement_m']*1000,'mm',''],
                             [prefix+'屈服安全系数',comp['minimum_safety_factor'],'',STATUS[comp['strength_status']]]])
            if comp['bore_adjacent_peak']:writer.writerow([prefix+'轴孔邻近积分点峰值',comp['bore_adjacent_peak']['value_Pa']/1e6,'MPa','区域指标，未作轴承强度判定'])
        for pair in s.get('contact',{}).get('pairs',[]):
            writer.writerow([f'接触对 C{pair["slave_body"]}-C{pair["master_body"]} 采样压力峰值',pair['maximum_pressure_Pa']/1e6,'MPa','未完成接触收敛与许用评估'])
        for region in evidence.get('stress_regions', []):
            for key,label in [('maximum','最大值'),('minimum','最小值')]:
                writer.writerow([f'C{region["body_id"]} {region["name"]} {region["label"]}{label}',region[key]['value_Pa']/1e6,'MPa','选面邻域原始积分点，非分离弯曲应力'])
        if s.get('contact'):
            writer.writerows([['齿根弯曲强度','','','未评估'],['接触疲劳/点蚀','','','未评估'],['运行干涉','','','未评估']])


def archive_result(folder,model_folder):
    folder=Path(folder);target=folder/'result.zip';tmp=folder/'result.building.zip'
    try:
        with zipfile.ZipFile(tmp,'w',compression=zipfile.ZIP_DEFLATED) as z:
            for path in folder.rglob('*'):
                if path.is_file() and path.name not in ('result.zip','result.building.zip','status.json','worker.log') and '.building.' not in path.name:
                    z.write(path,str(path.relative_to(folder)))
            for path in Path(model_folder).iterdir():
                if path.is_file() and path.name!='worker.log':z.write(path,'model/'+path.name)
        os.replace(tmp,target)
    finally:
        if tmp.exists():tmp.unlink()


def make_report(folder,s,display,model_folder,*,archive=True,comparisons=()):
    folder=Path(folder)
    evidence=report_evidence(folder,s)
    contact=read_json(folder/'contact.json') if s.get('contact') else None
    figures=create_figures(folder,display,s,evidence,contact)
    write_json(folder/'report-evidence.json',evidence)
    build_pdf(folder/'report.pdf',folder,s,evidence,figures,comparisons)
    write_metrics(folder,s,evidence)
    if archive:archive_result(folder,model_folder)
    return evidence


def saved_report_figures(folder, evidence, summary):
    """Locate generated figures for PDF-only refreshes and task comparisons."""
    d=Path(folder)/'report-figures';ev=evidence;main=summary
    figures={'model':d/'01-model.png','mesh':d/'02-mesh.png','boundary':d/'03-boundary.png',
             'displacement':d/'04-displacement.png','stress':d/'06-stress.png',
             'hotspots':[d/f'07-hotspot-{c["body_id"]}.png' for c in hotspot_components(ev)],
             'hotspot_body_ids':[c['body_id'] for c in hotspot_components(ev)]}
    for key,name in [('elastic','05-elastic.png'),('safety','08-safety.png'),('contact','09-contact.png')]:
        if (d/name).exists():figures[key]=d/name
    if main.get('contact'):figures['contact_pairs']=[d/f'10-contact-pair-{i+1}.png' for i in range(len(main['contact']['pairs']))]
    figures['stress_regions']=[d/f'11-region-{i+1}.png' for i in range(len(ev.get('stress_regions',[])))]
    return figures


def make_task_report(folder,jobs):
    """Reuse verified figures; main scope is the latest successful job."""
    from .config import DATA
    if not jobs:return
    summaries=[read_json(DATA/'jobs'/j['id']/'summary.json') for j in jobs if j.get('status')=='completed']
    if not summaries:return
    main=summaries[-1];source=DATA/'jobs'/main['job_id']
    ep=source/'report-evidence.json'
    if not ep.exists() or read_json(ep).get('report_version') != REPORT_VERSION:
        make_report(source,main,read_json(source/'display.json'),DATA/'models'/main['configuration']['model_id'],archive=False)
    ev=read_json(ep);figures=saved_report_figures(source,ev,main)
    folder=Path(folder);folder.mkdir(exist_ok=True,parents=True);(folder/'report-figures').mkdir(exist_ok=True)
    build_pdf(folder/'report.pdf',folder,main,ev,figures,comparisons=summaries)


class ChineseCSVWriter:
    def __init__(self, writer): self.writer = writer
    def writerow(self, row):
        self.writer.writerow([report_text(cell) if isinstance(cell, str) else cell for cell in row])
    def writerows(self, rows):
        for row in rows: self.writerow(row)


def write_comparison_csv(path, rows):
    columns = [('job_id','作业编号'),('name','工况名称'),('stress_MPa','最大等效应力 / MPa'),
               ('displacement_mm','最大位移 / mm'),('safety_factor','最小屈服安全系数'),
               ('strength','强度检查'),('displacement','位移检查')]
    with Path(path).open('w', encoding='utf-8-sig', newline='') as stream:
        writer = ChineseCSVWriter(csv.writer(stream))
        writer.writerow([label for _, label in columns])
        writer.writerows([[row.get(key) for key, _ in columns] for row in rows])


def write_task_markdown(path, message, jobs):
    links = '\n'.join(f'- [{report_text(j["name"])}：查看分析报告](/api/v1/jobs/{j["id"]}/files/report.pdf)' for j in jobs)
    Path(path).write_text('# 仿真任务报告\n\n'+report_text(message)+'\n\n'+links, encoding='utf-8')
