import os,subprocess,sys
import pytest
from strength import store


def test_stale_revision_preserves_saved_record():
    record=store.put('test_counter',{'id':store.uid(),'value':0})
    current=store.mutate('test_counter',record['id'],lambda old:{**old,'value':1},record['revision'])
    with pytest.raises(store.RevisionConflict):store.mutate('test_counter',record['id'],lambda old:{**old,'value':99},record['revision'])
    assert store.get('test_counter',record['id'])==current


def test_concurrent_process_transactions_do_not_lose_updates():
    record=store.put('test_counter',{'id':store.uid(),'value':0})
    script="from strength import store; import sys; [(store.mutate('test_counter',sys.argv[1],lambda v:{**v,'value':v['value']+1})) for _ in range(30)]"
    children=[]
    try:
        for _ in range(3):
            children.append(subprocess.Popen([sys.executable,'-c',script,record['id']],stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                env={**os.environ,'STRENGTH_DATA':str(store.DATA)},creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0)))
        for child in children:
            out,err=child.communicate(timeout=45)
            assert child.returncode==0,err.decode(errors='replace')
    finally:
        for child in children:
            if child.poll() is None:child.kill();child.wait(timeout=5)
    result=store.get('test_counter',record['id'])
    assert result['value']==90 and result['revision']==record['revision']+90


def test_project_filter_happens_before_limit_and_offset():
    project='wanted-'+store.uid();other='other-'+store.uid()
    ids=[]
    for i in range(4):
        sid=store.uid();ids.append(sid);store.put('study',{'id':sid,'project_id':project,'name':f'wanted {i}'})
    for i in range(20):store.put('study',{'id':store.uid(),'project_id':other,'name':'unrelated'})
    assert [r['id'] for r in store.listing('study',2,project_id=project)]==ids[::-1][:2]
    assert [r['id'] for r in store.listing('study',2,offset=2,project_id=project)]==ids[::-1][2:]
    assert len(store.listing('study',10,project_id=project,search='wanted 1'))==1
    for p in (project,other):store.put('analysis_run',{'id':store.uid(),'origin_project_id':p})
    assert store.listing('analysis_run',1,project_id=project)[0]['origin_project_id']==project
