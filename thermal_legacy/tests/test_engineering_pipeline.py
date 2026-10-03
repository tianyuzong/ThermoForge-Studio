import json,zipfile
import numpy as np
import pytest
import trimesh,h5py
import thermal_legacy.agent as agent; import thermal_legacy.solver as solver
from thermal_legacy.schemas import Simulation
from test_thermoelastic import cube


@pytest.mark.parametrize('ambient,mode',[(85,'constrained'),(-40,'free')])
def test_agent_to_thermal_stress_export_and_report(tmp_path,monkeypatch,ambient,mode):
    monkeypatch.setenv('THERMAL_DEVICE','cpu')
    mesh=cube(4);mesh['minimum_quality']=.8
    folder=tmp_path/'models'/'block';folder.mkdir(parents=True)
    surface=trimesh.Trimesh(vertices=mesh['points'],faces=mesh['boundary_triangles'],process=False)
    tri=surface.vertices[surface.faces]
    inward=np.einsum('ij,ij->i',np.cross(tri[:,1]-tri[:,0],tri[:,2]-tri[:,0]),tri.mean(axis=1)-surface.vertices.mean(axis=0))<0
    surface.faces[inward]=surface.faces[inward,::-1]
    display=dict(points=surface.vertices,faces=surface.faces,is_outer=np.ones(len(surface.faces),bool))
    np.savez(folder/'display.npz',**display)
    (folder/'display.json').write_text(json.dumps(dict(points=display['points'].ravel().tolist(),faces=display['faces'].ravel().tolist(),is_outer=display['is_outer'].tolist())),encoding='utf-8')
    (folder/'metadata.json').write_text(json.dumps(dict(name='block.stl',kind='stl',state='ready',mesh_ready=True,triangles=len(surface.faces),bounds_m=surface.bounds.tolist(),dimensions_m=surface.extents.tolist(),components=[dict(component_id=0)])),encoding='utf-8')
    monkeypatch.setattr(agent,'MODELS',folder.parent);monkeypatch.setattr(solver,'MODELS',folder.parent)
    monkeypatch.setattr(solver,'build_mesh',lambda *a:mesh)
    prompt=(f'纯环境温变工况。基础材料弹性模量200GPa，泊松比0.3，屈服强度250MPa，线膨胀系数12ppm/K。'
        f'初始温度20摄氏度，环境温度{ambient}摄氏度，默认换热系数20。启用热应力分析，'
        +('底面固定。' if mode=='constrained' else '自由热弹性状态。')+
        '无应力参考温度20摄氏度。仿真总时长60秒，计算步长1秒，每10秒保存，网格尺寸4mm。'
        '允许最低温度-45摄氏度，允许最高温度80摄氏度，允许最大位移0.2mm，强度安全系数1.5。')
    result=agent.plan_request('block',prompt,Simulation(model_id='block').model_dump())
    assert result['ok'],result['questions']
    job=tmp_path/'job';job.mkdir()
    (job/'config.json').write_text(json.dumps(result['config']),encoding='utf-8')
    solver.solve_job(job)
    manifest=json.loads((job/'result.json').read_text(encoding='utf-8'))
    assert manifest['structural']['mode']==mode
    assert manifest['structural']['maximum_relative_equilibrium_residual']<1e-6
    assert manifest['frames']==7
    last=manifest['stats'][-1]
    assert last['average_C']>20 if ambient>20 else last['average_C']<20
    assert last['maximum_displacement_m']>0 and last['maximum_von_mises_Pa']>=0
    assert manifest['summary']['components'][0]['final_average_C']==pytest.approx(last['average_C'],abs=1e-5)
    assert np.fromfile(job/'surface-displacement.bin',dtype='<f4').size==manifest['frames']*manifest['vertices']*3
    assert np.fromfile(job/'surface-von-mises.bin',dtype='<f4').size==manifest['frames']*manifest['vertices']
    assert '最大自由热位移' not in (job/'report.md').read_text(encoding='utf-8')
    assert (job/'report.pdf').stat().st_size>1000
    report=(job/'report.md').read_text(encoding='utf-8')
    for required in ('温度分布及变化','热变形结果','热应力结果','高低温工况覆盖','位置 XYZ','数据来源','尚未量化'):
        assert required in report
    figures=json.loads((job/'report-assets/manifest.json').read_text(encoding='utf-8'))
    for field in ('temperature','displacement','stress'):
        assert 0<=figures[field]['time_s']<=60
        assert (job/figures[field]['cloud']).stat().st_size>10000
        assert (job/figures[field]['curve']).stat().st_size>10000
    with h5py.File(job/'export/thermal-fields.h5') as f:
        assert f['stress/6'].shape==(len(mesh['tets']),6)
        assert f['von_mises/6'].shape==(len(mesh['tets']),)
    with zipfile.ZipFile(job/'result.zip') as z:assert 'assessment.json' in z.namelist()
    assessment=manifest['assessment']
    assert assessment['checks'][0]['limit']==-45
    assert assessment['checks'][1]['limit']==80
