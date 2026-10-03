import copy
import json
import pytest
from pydantic import ValidationError

import thermal_legacy.agent as agent
import thermal_legacy.agent_skill as agent_skill
from thermal_legacy.agent_conversation import user_prompts
from thermal_legacy.schemas import AgentRequest
from test_agent_skill import blocks


def conversation(*turns, applied=0):
    return dict(history=[dict(role='user' if i % 2 == 0 else 'assistant', content=t)
                         for i, t in enumerate(turns)], applied_message_count=applied)


def review(**fields):
    return dict(patch=[dict(path='/'+k, value_json=json.dumps(v)) for k,v in fields.items()], questions=[])


def test_schema_bounds_and_single_word_replies():
    request=AgentRequest(model_id='blocks',prompt='是',**conversation('添加热源','是否外置？'))
    assert request.conversation()['history'][1]['role']=='assistant'
    for extra in [dict(applied_message_count=4),dict(applied_message_count=1),
                  dict(history=[dict(role='assistant',content='是')]),dict(history=[dict(role='system',content='x')])]:
        with pytest.raises(ValidationError):
            AgentRequest(model_id='blocks',prompt='是',**{**conversation('x','y'),**extra})


def test_only_unresolved_user_messages_are_replayed():
    c=conversation('新增20W顶部热源','已完成','热源放到内部中心','中心在空腔，是否外置？',applied=2)
    assert user_prompts('外置',c)==['热源放到内部中心','外置']


def test_pending_center_and_time_survive_reply_without_accepting_assistant_suggestion(blocks):
    cfg,_=blocks
    original=copy.deepcopy(cfg)
    initial='在整个模型内部中心添加一个3000W点热源，作用半径2mm，0秒开启，180秒关闭。仿真总时长180秒，步长1秒，每5秒保存。'
    c=conversation(initial,'中心位于空腔，是否外置？建议将仿真时长改成600秒。')
    p=agent_skill.prepare(agent,'blocks','仿真总时长300秒',cfg,c)
    h=p['candidate']['heat_sources']
    assert len(h)==1 and h[0]['power_W']==3000 and h[0]['position_m']==[0,0,0]
    assert h[0]['placement']=='embedded' and h[0]['end_s']==180
    assert p['candidate']['duration_s']==300 and p['candidate']['save_s']==5
    assert p['public']['geometry']['center_in_solid'] is False
    assert p['public']['conversation']==c and cfg==original
    # Only duration was answered: still no runnable draft.
    pending=dict(patch=[],questions=['中心仍在空腔，请确定放置方式。'])
    result,questions=agent_skill.compile_review(agent,p,json.dumps(pending))
    assert result is None and questions
    c['history'] += [dict(role='user',content='仿真总时长300秒'),dict(role='assistant',content=questions[0])]
    p=agent_skill.prepare(agent,'blocks','同意改用外置热源',cfg,c)
    edits=review(**{'heat_sources/0/placement':'external'})
    completed,questions=agent_skill.compile_review(agent,p,json.dumps(edits))
    assert not questions and len(completed['heat_sources'])==1
    assert completed['heat_sources'][0]['power_W']==3000
    assert completed['heat_sources'][0]['end_s']==180
    assert completed['duration_s']==300


def test_successful_checkpoint_prevents_duplicate_add_and_repeated_delete(blocks):
    cfg,_=blocks
    cfg=agent.plan_request('blocks','顶部20W热源',cfg)['config']
    c=conversation('添加一个顶部20W热源','已新增',applied=2)
    p=agent_skill.prepare(agent,'blocks','基础材料比热容改为800 J/(kg·K)',cfg,c)
    assert len(p['candidate']['heat_sources'])==1
    assert p['candidate']['base_material']['cp']==800
    c=conversation('删除第一个热源','已删除',applied=2)
    p=agent_skill.prepare(agent,'blocks','初始温度30°C',cfg,c)
    assert p['candidate']['heat_sources']==cfg['heat_sources']


def test_local_followup_updates_other_parameter_groups(blocks):
    cfg,_=blocks
    first=agent.plan_request('blocks','顶部20W热源，仿真300秒',cfg)
    c=conversation('顶部20W热源，仿真300秒','已生成',applied=2)
    result=agent.plan_request('blocks','环境温度30°C，网格尺寸3mm',first['config'],c)
    assert result['ok'],result['questions']
    assert result['config']['ambient_C']==30 and result['config']['mesh_size_m']==.003
    assert len(result['config']['heat_sources'])==1 and result['config']['duration_s']==300
    ambiguous=agent.plan_request('blocks','是',first['config'],c)
    assert not ambiguous['ok'] and ambiguous['error_code']=='clarification'


def test_api_conversation_can_ask_and_then_compile_answer(blocks, monkeypatch):
    cfg,_=blocks
    monkeypatch.setenv('OPENAI_API_KEY','test-key')
    replies=[dict(patch=[],questions=['请说明热源位置。']),review()]
    requests=[]
    class Response:
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def read(self): return json.dumps({'output_text':json.dumps(replies.pop(0))}).encode()
    def urlopen(request,**kwargs):
        requests.append(json.loads(request.data))
        return Response()
    monkeypatch.setattr(agent.urllib.request,'urlopen',urlopen)
    first=agent._codex_api_plan_request('blocks','添加20W热源',cfg,conversation())
    assert not first['ok'] and first['error_code']=='clarification'
    second=agent._codex_api_plan_request('blocks','热源放在顶部',cfg,conversation('添加20W热源',first['questions'][0]))
    assert second['ok'],second['questions']
    assert second['config']['heat_sources'][0]['faces']
    assert json.loads(requests[1]['input'][1]['content'])['conversation']['history'][0]['content']=='添加20W热源'
    assert requests[1]['text']['format']['strict'] is True


def test_invalid_manual_configuration_can_be_repaired_but_not_submitted(blocks):
    cfg,_=blocks
    cfg['dt_s']=0
    cfg['heat_sources']=[dict(power_W=20,faces=[])]
    p=agent_skill.prepare(agent,'blocks','热源放在顶部',cfg,conversation())
    assert p['candidate']['dt_s']==0
    with pytest.raises(ValueError):
        agent_skill.compile_review(agent,p,json.dumps(review()))
    c=conversation('热源放在顶部','计算步长必须大于0，请指定步长。')
    p=agent_skill.prepare(agent,'blocks','步长改成2秒',cfg,c)
    completed,questions=agent_skill.compile_review(agent,p,json.dumps(review()))
    assert not questions and completed['dt_s']==2
    assert completed['heat_sources'][0]['faces']
    assert cfg['dt_s']==0 and cfg['heat_sources'][0]['faces']==[]
