import time,uuid,json,threading
import pytest
from strength import store,service
from strength.schemas import DEMO_MATERIAL

def project(client,models):
    p=client.post('/api/v1/projects',json={'name':'验收项目'}).json()
    store.put('model',models['bracket']);store.update('project',p['id'],model_id='bracket',material=DEMO_MATERIAL)
    return p['id']

def wait(client,id,timeout=90):
    end=time.time()+timeout
    while time.time()<end:
        t=client.get('/api/v1/agent/tasks/'+id).json()
        if t['status'] in service.TERMINAL|{'needs_input'}: return t
        time.sleep(.1)
    pytest.fail('任务超时')

def send(client,p,prompt,**kw):
    data={'project_id':p,'prompt':prompt,'provider':'local','request_id':uuid.uuid4().hex,**kw}
    response=client.post('/api/v1/agent/tasks',json=data);assert response.status_code==202,response.text
    return response.json(),data

def test_sentence_runs_two_real_cases_and_followup(client,models):
    p=project(client,models)
    t,data=send(client,p,'安装孔固定，顶面向下施加 1000 N，再比较 1500 N 工况。')
    same=client.post('/api/v1/agent/tasks',json=data).json();assert same['id']==t['id']
    t=wait(client,t['id']);assert t['status']=='completed',t
    assert len(t['jobs'])==2
    a,b=[j['summary']['maximum_von_mises_Pa'] for j in t['jobs']];assert abs(b/a-1.5)<1e-4
    assert any(e['stage']=='tool' for e in t['events'])
    message={'prompt':'把刚才的力改成 2000 N，再与上一版比较。','request_id':uuid.uuid4().hex}
    child=client.post('/api/v1/agent/tasks/'+t['id']+'/messages',json=message).json()
    assert child['id']!=t['id'];c=wait(client,child['id']);assert c['status']=='completed',c
    assert len(c['comparison'])==3
    assert len(client.get('/api/v1/agent/tasks/'+t['id']).json()['jobs'])==2
    j=c['jobs'][0]; assert client.get('/api/v1/jobs/'+j['id']+'/files/report.pdf').content.startswith(b'%PDF')
    assert client.get('/api/v1/jobs/'+j['id']+'/slice?axis=0&position=.004&field=stress').status_code==200

def test_clarification_resumes_automatically(client,models):
    p=project(client,models);t,_=send(client,p,'安装孔固定，使用已保存的材料。')
    t=wait(client,t['id']);assert t['status']=='needs_input';assert not t['job_ids']
    r=client.post('/api/v1/agent/tasks/'+t['id']+'/messages',json={'prompt':'顶面向下 1000 N。','request_id':uuid.uuid4().hex})
    assert r.status_code==202;r=wait(client,t['id']);assert r['status']=='completed',r
    assert len(r['job_ids'])==1

def test_draft_start_and_message_idempotency(client,models):
    p=project(client,models);t,_=send(client,p,'先给方案：安装孔固定，顶面向下 1000 N，暂不运行。')
    t=wait(client,t['id']);assert t['status']=='draft';assert not t['job_ids']
    payload={'prompt':'开始计算','request_id':uuid.uuid4().hex}
    a=client.post('/api/v1/agent/tasks/'+t['id']+'/messages',json=payload)
    b=client.post('/api/v1/agent/tasks/'+t['id']+'/messages',json=payload)
    assert a.json()['id']==b.json()['id'];t=wait(client,t['id']);assert t['status']=='completed';assert len(t['jobs'])==1

def test_cancel_queued_task_does_not_launch_solver(client,models):
    p=project(client,models)
    service.SLOT.acquire()
    try:
        t,_=send(client,p,'安装孔固定，顶面向下 1000 N。')
        end=time.time()+10
        while time.time()<end:
            t=client.get('/api/v1/agent/tasks/'+t['id']).json()
            if t['status']=='queued':break
            time.sleep(.05)
        assert t['status']=='queued'
        t=client.post('/api/v1/agent/tasks/'+t['id']+'/cancel').json();assert t['status']=='cancelled'
    finally: service.SLOT.release()
    time.sleep(.3)
    t=client.get('/api/v1/agent/tasks/'+t['id']).json();assert all(j['status']=='cancelled' for j in t['jobs'])

def test_empty_selection_and_cross_site_write(client,models):
    p=project(client,models)
    assert client.post('/api/v1/models/bracket/selections',json={'name':'无效','face_ids':[99999]}).status_code==422
    assert client.post('/api/v1/projects',json={'name':'blocked'},headers={'Origin':'https://evil.example'}).status_code==403
    assert client.get('/api/v1/jobs/not-a-job/files/analysis.inp').status_code==404
