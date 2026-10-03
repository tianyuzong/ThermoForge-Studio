import sys,copy,threading
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'tests')]
import numpy as np
import pytest
from fastapi import HTTPException
import thermal_legacy.workflows as workflows; import thermal_legacy.workflow_report as workflow_report; import thermal_legacy.agent as agent; import thermal_legacy.agent_skill as agent_skill
from thermal_legacy.runtime import write_json, read_json
from test_agent_skill import blocks
from test_workflows import store,persist,row,write_result


def test_deferred_result_binding_is_provided_to_config_agent(blocks):
    cfg,_=blocks
    context=dict(phase='planning',deferred_initial_from='normal')
    p=agent_skill.prepare(agent,'blocks','顶部20W热源',cfg,dict(history=[],applied_message_count=0,workflow_context=context))
    assert p['public']['workflow_context']==context
    assert '尚未产生的结果ID' in agent_skill.review_instructions(agent)


def test_double_start_does_not_create_duplicate_execution_threads(store,monkeypatch):
    wid,p,_,cfg=store;persist(store,[row('a',cfg)],phase='ready')
    geometry=dict(source_sha256='same',bounds_m=[],dimensions_m=[])
    w=read_json(p/'workflow.json');w['geometry']=geometry;write_json(p/'workflow.json',w)
    monkeypatch.setattr(workflows,'model_evidence',lambda *a:geometry)
    starts=[]
    class FakeThread:
        def __init__(self,*a,**kw):starts.append(kw)
        def start(self):pass
        def is_alive(self):return True
    monkeypatch.setattr(workflows.threading,'Thread',FakeThread)
    for _ in range(2):assert workflows.start(wid,None,None,None)['phase']=='running'
    assert len(starts)==1


def test_unready_geometry_is_not_mistaken_for_a_busy_job(store,monkeypatch):
    wid,p,_,cfg=store;persist(store,[row('a',cfg)])
    def submit(c):raise HTTPException(409,'几何尚未准备完成')
    monkeypatch.setattr(workflows.time,'sleep',lambda *a:pytest.fail('不得永久排队'))
    workflows.execute(wid,submit,None,None)
    assert read_json(p/'workflow.json')['phase']=='failed'


def test_cycle_range_compares_identical_cells_not_unrelated_global_maxima(tmp_path):
    points=np.array([[0.,0,0],[1,0,0],[0,1,0],[0,0,1],[0,0,-1]])
    cells=np.array([[0,1,2,3],[0,2,1,4]])
    np.savez(tmp_path/'mesh.npz',points=points,tets=cells)
    np.save(tmp_path/'temperatures.npy',np.full((2,5),20,dtype=np.float32))
    stress=np.zeros((2,2,6),dtype='<f4');stress[0,0,0]=100;stress[1,1,0]=100;stress.tofile(tmp_path/'stress.bin')
    cfg=dict(base_material=dict(young_modulus_Pa=69e9,poisson_ratio=.33,thermal_expansion_CTE_per_K=23e-6),structural=dict(reference_C=20))
    r=workflow_report.cycle_range(tmp_path,cfg,dict(stats=[dict(time_s=0),dict(time_s=10)]))
    assert r['equivalent_stress_tensor_range_Pa']==pytest.approx(100)
    assert r['maximum_total_normal_strain_range']==pytest.approx(100/69e9)
    assert r['strain_minimum_maximum_times_s']==[10,0]


def test_report_input_basis_preserves_actual_profile_and_contact_area():
    cfg=dict(base_material=dict(k=167,rho=2700,cp=900),ambient_profile=[dict(time_s=1800,ambient_C=-40)],
        surface_evaluation=dict(faces=[1234567],surface_box=dict(min_m=[-.04,-.018,-.004],max_m=[.04,.018,-.004])))
    text='\n'.join(workflow_report.input_summary(cfg,dict(surface_evaluation=dict(area_m2=.00288))))
    assert '1800 / -40' in text and '0.00288 m²' in text
    assert '167 W/(m·K)' in text and '-40至40' in text
    assert '1234567' not in text


def test_completed_report_retains_physics_limits_without_stale_planning_notice():
    w=dict(name='热仿真分析计划（整体确认后执行）',limitations=['本输出仅为待整体确认的执行计划，尚未进行实际求解。','疲劳寿命未完成'])
    title,limits=workflow_report.report_identity(w)
    assert title=='热仿真分析' and limits==['疲劳寿命未完成']
    assert len(w['limitations'])==2 and w['name'].endswith('（整体确认后执行）')
