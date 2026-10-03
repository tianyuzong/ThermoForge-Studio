import json
import shutil
import numpy as np
import pytest
from strength.result_catalog import derive,finite_json,FIELDS
from strength.postprocess import ResultArchive,compare_results
from strength.schemas import ResultQuery,LoadCase,ReviewDecision,Observation
from strength import backends,store,agent
from strength.feedback import interpret
from strength.config import DATA
from test_physics import axial_case,solve

def test_tensor_fields_shear_hydrostatic_and_nulls():
    E=210e9;nu=.3;G=E/(2*(1+nu));tau=12e6
    s=np.array([[10e6,0,0,0,0,0],[0,0,0,tau,0,0],[20e6,20e6,20e6,0,0,0],[0,0,0,0,0,0]])
    e=s/(2*G);e[:,:3]-=nu/E*s[:,:3].sum(1)[:,None]
    v=derive(s,e,E,nu,235e6)
    assert v['stress'][1]==pytest.approx(np.sqrt(3)*tau)
    assert v['principal_max'][1]==pytest.approx(tau)
    assert v['principal_min'][1]==pytest.approx(-tau)
    assert v['maximum_shear'][1]==pytest.approx(tau)
    assert v['stress_intensity'][1]==pytest.approx(2*tau)
    assert v['safety_factor_von_mises'][1]==pytest.approx(235e6/(np.sqrt(3)*tau))
    assert v['safety_factor_tresca'][1]==pytest.approx(235e6/(2*tau))
    assert v['engineering_shear_xy'][1]==pytest.approx(tau/G)
    assert v['maximum_shear_strain'][1]==pytest.approx(tau/G)
    np.testing.assert_allclose(v['equivalent_elastic_strain'],v['stress']/E,atol=1e-18)
    assert v['energy_density'][1]==pytest.approx(tau**2/(2*G))
    assert v['energy_density'][2]>0 and v['equivalent_elastic_strain'][2]==pytest.approx(0,abs=1e-18)
    assert finite_json(v)['triaxiality'][2] is None
    assert finite_json(v)['safety_factor'][3] is None
    assert finite_json(derive(s,e,E,nu,np.nan))['safety_factor']==[None]*4

@pytest.fixture(scope='module')
def bar_result(models):
    case=axial_case();case.moment_origin_m=[.02,.03,.04]
    case.result_requests=[ResultQuery(name='轴向刚度',kind='stiffness',field='displacement_x',point_m=[.1,.005,.005],direction=[1,0,0],load_name='载荷')]
    s,p=solve('bar',case)
    return s,p

def test_real_bar_energy_probe_path_and_reaction_origin(bar_result):
    s,p=bar_result;a=ResultArchive(p);exact=1000*.1/(1e-4*210e9)
    assert s['strain_energy_J']==pytest.approx(1000*exact/2,rel=.01)
    point=a.query(ResultQuery(kind='probe',field='displacement_x',point_m=[.05,.005,.005]))
    assert point['value']==pytest.approx(exact/2,rel=.01)
    np.testing.assert_allclose(np.asarray(point['deformed_position_m'])-point['position_m'],point['displacement_m'],atol=1e-17)
    path=a.query(ResultQuery(kind='path',field='stress_xx',point_m=[.01,.005,.005],end_m=[.09,.005,.005],samples=11))
    assert path['status']=='ok'
    np.testing.assert_allclose([x['value'] for x in path['samples']],1e7,rtol=.01)
    rel=a.query(ResultQuery(kind='relative',point_m=[0,.005,.005],end_m=[.1,.005,.005]))
    assert rel['relative_displacement_m'][0]==pytest.approx(exact,rel=.01)
    outside=a.query(ResultQuery(kind='probe',point_m=[.2,0,0]));assert outside['status']=='outside' and outside['value'] is None
    mixed=a.query(ResultQuery(kind='path',point_m=[.05,.005,.005],end_m=[.15,.005,.005],samples=11))
    assert mixed['status']=='partial' and mixed['samples'][-1]['value'] is None
    first=a.query(ResultQuery(kind='reactions',moment_origin_m=[0,0,0]));delta=np.array([.02,.03,.04])
    second=a.query(ResultQuery(kind='reactions',moment_origin_m=delta.tolist()))
    np.testing.assert_allclose(second['moment_Nm'],np.asarray(first['moment_Nm'])-np.cross(delta,first['reaction_N']),atol=1e-8)
    assert s['result_queries'][0]['stiffness_N_m']==pytest.approx(210e9*1e-4/.1,rel=.01)
    assert (p/'field-statistics.csv').exists() and (p/'result-queries.json').exists()

def test_v2_routes_and_legacy_query_immutability(client,bar_result):
    s,p=bar_result;cfg=json.loads((p/'config.json').read_text('utf-8'))
    j={'id':p.name,'status':'completed','summary':s,'case':cfg['case'],'name':'拉杆','project_id':'v2-test'};store.put('job',j)
    before=(p/'fields.h5').read_bytes();config_before=(p/'config.json').read_bytes()
    assert {'stress_xy','engineering_shear_xy','energy_density','safety_factor_tresca','displacement_z'} <= {f['key'] for f in client.get('/api/v1/results/catalog').json()['fields']}
    r=client.post(f'/api/v1/jobs/{p.name}/results/query',json={'kind':'statistics','field':'stress_xx'})
    assert r.status_code==200 and r.json()['mean']==pytest.approx(1e7,rel=.01)
    r=client.post(f'/api/v1/jobs/{p.name}/results/query',json={'kind':'probe','node_id':9999999});assert r.status_code==422
    r=client.post(f'/api/v1/jobs/{p.name}/results/query',json={'field':'../../config'});assert r.status_code==422
    assert (p/'fields.h5').read_bytes()==before and (p/'config.json').read_bytes()==config_before
    old=cfg['case'];old['schema_version']=1
    for k in ('strength_criterion','moment_origin_m','result_requests'):old.pop(k,None)
    loaded=LoadCase.model_validate(old);assert loaded.strength_criterion=='von_mises' and loaded.result_requests==[]
    store.update('job',p.name,status='failed')
    assert client.post(f'/api/v1/jobs/{p.name}/results/query',json={}).status_code==422
    store.update('job',p.name,status='completed')

def test_cpu_health_does_not_initialize_cuda(client,monkeypatch):
    monkeypatch.setattr(backends,'gpu_diagnostic',lambda:(_ for _ in ()).throw(AssertionError('CUDA should not start')))
    assert client.get('/api/v1/health').json()['execution']['cpu_threads']==6

def test_legacy_hdf5_can_derive_new_fields_without_rewriting(bar_result,tmp_path):
    import h5py
    _,source=bar_result;folder=tmp_path/'legacy';folder.mkdir()
    for name in ('fields.h5','config.json','summary.json'):shutil.copyfile(source/name,folder/name)
    cfg=json.loads((folder/'config.json').read_text('utf-8'));cfg['case']['schema_version']=1
    for key in ('strength_criterion','moment_origin_m','result_requests'):cfg['case'].pop(key,None)
    (folder/'config.json').write_text(json.dumps(cfg),encoding='utf-8')
    with h5py.File(folder/'fields.h5','a') as f:
        for key in list(f):
            if key.startswith('ip_') or key.startswith('display_') and key[8:] not in ('stress','displacement','strain','principal_max','principal_min','safety_factor'):del f[key]
        for key in ('result_schema_version','field_catalog','strain_shear','strain_energy_J','reaction_moment_origin_m'):
            if key in f.attrs:del f.attrs[key]
    before={p.name:p.read_bytes() for p in folder.iterdir()}
    a=ResultArchive(folder);q=a.query({'kind':'probe','field':'equivalent_elastic_strain','point_m':[.05,.005,.005]})
    assert q['status']=='ok' and q['value']==pytest.approx(1e7/210e9,rel=.01)
    assert before=={p.name:p.read_bytes() for p in folder.iterdir()}

def test_result_feedback_references_and_bounded_failure(bar_result,monkeypatch):
    s,p=bar_result;j={'id':p.name,'name':'拉杆','summary':s};task={'id':store.uid(),'provider':'cli','conversation':[{'role':'user','text':'解释结果'}]}
    calls=[]
    def review(context,*args):
        calls.append(context)
        return ReviewDecision(action='finish',message='结果已经核对。',queries=[],observations=[Observation(fact_id=context['facts'][0]['id'],comment='这是原始应力峰值。')])
    monkeypatch.setattr(agent,'review',review)
    r=interpret(task,[j],[],lambda:False,lambda *a:None)
    assert r['status']=='completed' and r['observations'][0]['fact']['value']==s['maximum_von_mises_Pa']
    monkeypatch.setattr(agent,'review',lambda *a:ReviewDecision(action='finish',message='最大应力为999。',queries=[],observations=[]))
    r=interpret(task,[j],[],lambda:False,lambda *a:None);assert r['status']=='limited' and r['rounds']==3
    monkeypatch.setattr(agent,'review',lambda *a:(_ for _ in ()).throw(TimeoutError('test timeout')))
    r=interpret(task,[j],[],lambda:False,lambda *a:None);assert r['status']=='failed'
    assert (p/'report.pdf').exists()


def test_bonded_feedback_uses_host_compatibility_and_interface_facts(bar_result,monkeypatch):
    import copy
    s,p=bar_result;s=copy.deepcopy(s)
    s['bonding']={'compatibility_relative':3e-8,'tied_nodes':12,'scope':'理想粘结，仅供试算',
      'interfaces':[{'name':'轴至轴承','transfer_to_slave_N':[0,0,1000],
        'transfer_moment_about_origin_Nm':[0,10,0],'moment_origin_m':[0,0,0]}]}
    def review(context,*args):
        compat=next(f for f in context['facts'] if f['source']=='summary.bonding.compatibility_relative')
        transfer=next(f for f in context['facts'] if f['source']=='summary.bonding.interfaces.0')
        assert compat['value']==3e-8 and transfer['value']['reaction_N']==[0,0,1000]
        return ReviewDecision(action='finish',message='已核对接口传力与位移兼容性。',queries=[],
            observations=[Observation(fact_id=compat['id'],comment='这是宿主计算的兼容误差。'),Observation(fact_id=transfer['id'],comment='这是理想接口对从组件的作用。')])
    monkeypatch.setattr(agent,'review',review)
    task={'id':store.uid(),'provider':'cli','conversation':[{'role':'user','text':'解释连接校核'}]}
    result=interpret(task,[{'id':p.name,'name':'粘结测试','summary':s}],[],lambda:False,lambda *a:None)
    assert result['status']=='completed'
    assert '3e-08' in result['message'] and '1000' in result['message']
