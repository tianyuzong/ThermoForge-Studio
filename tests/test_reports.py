import copy
import json
import zipfile

import h5py
import numpy as np
import pytest

from strength import store
from strength.report import conclusion, make_task_report
from strength.report_data import report_evidence
from test_physics import axial_case, solve


@pytest.fixture(scope='module')
def report_job(models):
    return solve('bar', axial_case())


def test_report_evidence_uses_raw_fields_and_includes_figures(report_job):
    summary, folder = report_job
    evidence = json.loads((folder/'report-evidence.json').read_text('utf-8'))
    with h5py.File(folder/'fields.h5') as f:
        peak = evidence['components'][0]['stress_peak']
        assert peak['value_Pa'] == pytest.approx(f['von_mises'][peak['element']-1,peak['integration_point']-1])
    assert evidence['evaluations']['displacement'] == 'not_evaluated'
    assert evidence['external_validation']['status'] == 'not_verified'
    assert evidence['mesh_quality']['minimum'] > 0
    with zipfile.ZipFile(folder/'result.zip') as z:
        assert 'report-evidence.json' in z.namelist()
        assert 'report-figures/02-mesh.png' in z.namelist()
        assert 'report-figures/06-stress.png' in z.namelist()


def test_report_rejects_summary_field_mismatch(report_job):
    summary, folder = report_job
    bad=copy.deepcopy(summary);bad['maximum_von_mises_Pa']*=1.01
    with pytest.raises(ValueError,match='摘要应力'):report_evidence(folder,bad)
    bad=copy.deepcopy(summary);bad['maximum_displacement_m']*=1.01
    with pytest.raises(ValueError,match='摘要位移'):report_evidence(folder,bad)


def test_report_never_converts_missing_checks_into_approval(report_job):
    summary,_=report_job
    value=copy.deepcopy(summary);value['contact']={'experimental':True}
    text=conclusion(value)
    assert '刚度不作合格判定' in text and '不能据此断言' in text and '尚未完成同条件 Ansys' in text
    value['balance']['passed']=False
    assert '不能用于强度或刚度合格判定' in conclusion(value)
    value['balance']['passed']=True;value['checks']['strength']['status']='not_evaluated'
    assert '静态强度尚未评估' in conclusion(value)


def test_report_routes_and_task_pdf(client,report_job,tmp_path):
    summary,folder=report_job
    job={'id':folder.name,'status':'completed','summary':summary}
    store.put('job',job)
    r=client.get(f'/api/v1/jobs/{folder.name}/report')
    assert r.status_code==200 and r.json()['available']
    image=r.json()['figures'][0]['url']
    assert client.get(image).content.startswith(b'\x89PNG')
    assert client.get(f'/api/v1/jobs/{folder.name}/report-figures/config.json').status_code==404
    assert client.get(f'/api/v1/jobs/{folder.name}/report-figures/unknown.png').status_code==404
    make_task_report(tmp_path,[job])
    assert (tmp_path/'report.pdf').read_bytes().startswith(b'%PDF')


def test_contents_descriptions_distinguish_checks_from_completed_simulations():
    from strength.report import contents_description
    assert '未执行三档' in contents_description('4.5 安全系数与数值核验',{})
    assert '三档网格变化检查' in contents_description('4.5 安全系数与数值核验',{'convergence':{'levels':[{}, {}, {}]}})
    assert '不代表这些仿真已实施' in contents_description('6 优化与后续验证建议',{})
    assert '缺少屈服数据时标为未评估' in contents_description('4.2 等效应力与组件静强度',{})


def test_long_contents_converges_with_unique_links_and_final_pages(tmp_path):
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import Paragraph, PageBreak
    from reportlab.platypus.tableofcontents import TableOfContents
    from strength.report import ReportDocument, font_name
    body=ParagraphStyle('BodyCN',fontName=font_name(),fontSize=12,leading=22,wordWrap='CJK')
    heading=ParagraphStyle('SectionCN',parent=body)
    toc=TableOfContents(levelStyles=[body],dotsMinLevel=0)
    story=[Paragraph('目录',body),toc]
    for i in range(45):
        story.extend([PageBreak(),Paragraph('重复章节：位移与应力结果',heading),Paragraph(str(i),body)])
    doc=ReportDocument(str(tmp_path/'long-toc.pdf'))
    doc.multiBuild(story)
    entries=toc._entries
    assert entries==toc._lastEntries and len(entries)==45
    assert len({e[3] for e in entries})==45
    assert entries[0][2]>2  # TOC itself spans multiple pages.
    assert [e[2] for e in entries]==list(range(entries[0][2],doc.page+1))
