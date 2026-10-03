import copy
import json

from strength import feedback, store
from strength.schemas import ReviewDecision, Observation


def test_saved_paths_are_compact_and_raw_results_are_unchanged(monkeypatch):
    path={'name':'轴表面应力路径','kind':'path','field':'stress','status':'partial','unit':'Pa',
          'basis':'所属单元插值；模型外点留空',
          'samples':[{'status':'ok','value':i*100.,'position_m':[i*.001,0.,0.]} for i in range(61)]}
    path['samples'][30]={'status':'outside','value':None,'position_m':[.03,0.,0.]}
    summary={'maximum_von_mises_Pa':6000.,'maximum_displacement_m':1e-6,
             'checks':{'strength':{'status':'not_evaluated'}},'balance':{'passed':True},
             'convergence':{'status':'not_verified'},'warnings':[],
             'displacement_peak':{'node':1,'position_m':[0,0,0]},'result_queries':[path]}
    original=copy.deepcopy(summary)
    def review(context,*args):
        fact=next(f for f in context['facts'] if f['label']==path['name'])
        value=fact['value']
        assert 'samples' not in value
        assert value['sample_count']==61 and value['undefined_count']==1
        assert value['minimum']['value']==0 and value['maximum']['value']==6000
        assert value['status']=='partial' and value['basis']==path['basis']
        assert len(json.dumps(value))<len(json.dumps(path))/4
        return ReviewDecision(action='finish',message='路径包含模型外采样，保留未定义状态。',
                              observations=[Observation(fact_id=fact['id'],comment='路径极值来自已保存的插值结果。')])
    monkeypatch.setattr(feedback.agent,'review',review)
    result=feedback.interpret({'id':store.uid(),'provider':'cli','conversation':[]},
        [{'id':'test-job','name':'测试工况','summary':summary}],[],lambda:False,lambda *args:None)
    assert result['status']=='completed'
    assert summary==original


def test_all_outside_path_does_not_invent_extrema():
    result=feedback.compact_query_result({'status':'partial','samples':[{'status':'outside','value':None}]})
    assert result=={'status':'partial','sample_count':1,'undefined_count':1}
