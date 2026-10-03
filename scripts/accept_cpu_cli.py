"""Real, opt-in Codex CLI + CPU end-to-end acceptance. Requires a running service."""
import json,time,uuid,sys
from pathlib import Path
import httpx
sys.stdout.reconfigure(encoding='utf-8')
ROOT=Path(__file__).resolve().parents[1]
client=httpx.Client(base_url='http://127.0.0.1:8876/api/v1',timeout=120,trust_env=False)
record={'started_at':time.time(),'scenarios':[]}
target=ROOT/'.runtime'/'acceptance-cli.json'
def save():target.write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
def post(url,payload=None):
    r=client.post(url,json=payload or {});r.raise_for_status();return r.json()
def wait(task,label):
    end=time.time()+1200;last=None
    while time.time()<end:
        r=client.get('/agent/tasks/'+task['id']);r.raise_for_status();task=r.json()
        state=task['status']
        if state!=last:print(label,state,task.get('detail','')[:130],flush=True);last=state
        if state in ('completed','partial','failed','needs_input','cancelled','interrupted'):
            row={'label':label,'task_id':task['id'],'status':state,'job_ids':task.get('job_ids',[]),
                'feedback_status':task.get('feedback',{}).get('status'),'questions':task.get('questions',[]),
                'jobs':[{'id':j['id'],'summary':{k:j.get('summary',{}).get(k) for k in ('maximum_von_mises_Pa','maximum_displacement_m','minimum_safety_factor','strain_energy_J','balance','convergence','execution')}} for j in task.get('jobs',[])]}
            record['scenarios'].append(row);save();return task
        time.sleep(2)
    raise TimeoutError(label)
def task(pid,text,label):return wait(post('/agent/tasks',{'project_id':pid,'prompt':text,'provider':'cli','request_id':uuid.uuid4().hex}),label)
def follow(t,text,label):return wait(post('/agent/tasks/'+t['id']+'/messages',{'prompt':text,'request_id':uuid.uuid4().hex}),label)
def complete(t):assert t['status']=='completed',t.get('detail');assert t.get('feedback',{}).get('status')=='completed',t.get('feedback')
try:
    h=client.get('/health').json();assert h['solver_available'] and h['cli_available'];record['health']=h
    p=post('/projects',{'name':'CPU 与 Codex 端到端验收'});record['project_id']=p['id'];save()
    post('/projects/'+p['id']+'/demo')
    first=task(p['id'],'使用项目已保存的演示钢材，固定四个安装孔的孔壁，在顶面沿全局 -Z 分别施加 1000 N 和 1500 N 总力，网格尺寸 8 mm，使用 CPU，比较应力、位移、安全系数与应变能并生成报告。','双工况')
    complete(first);assert len(first['jobs'])==2
    a,b=first['jobs'];ratio=b['summary']['maximum_von_mises_Pa']/a['summary']['maximum_von_mises_Pa'];assert abs(ratio-1.5)<1e-4
    changed=follow(first,'把最后一个工况的力改成 2000 N，其他条件不变，只运行这个新工况并与前面比较。','连续修改');complete(changed)
    explained=follow(changed,'解释刚才的结果和适用范围，不要重新求解。','结果解释');complete(explained);assert not explained['job_ids']
    queried=follow(changed,'查询上一工况最大位移节点处的总位移、位移向量和变形后坐标，不重新求解。','点探针');complete(queried);assert queried['feedback']['queries']
    conv=follow(changed,'对当前 2000 N 工况做三档网格收敛分析，保留材料、约束、载荷与 8 mm 初始网格，使用 CPU。','三档收敛');complete(conv)
    assert len(conv['jobs'][0]['summary']['convergence']['levels'])==3
    p2=post('/projects',{'name':'Codex 补问续算验收'});post('/projects/'+p2['id']+'/demo')
    missing=task(p2['id'],'固定安装孔，沿用已保存材料，使用 CPU。我还没有指定载荷，请告诉我还需要哪些受力参数。','缺参数补问')
    assert missing['status']=='needs_input' and not missing['job_ids']
    resumed=follow(missing,'顶面沿全局 -Z 施加 1000 N 总力，网格尺寸 8 mm，自动运行并解释结果。','补充后续算');complete(resumed)
    record['passed']=True
except Exception as e:
    record['passed']=False;record['error']=str(e);raise
finally:
    record['finished_at']=time.time();save()
