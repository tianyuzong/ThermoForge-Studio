"""Bounded LLM feedback; all rendered numbers are resolved by the host."""
import json
import re
from . import agent,store
from .config import DATA
from .result_catalog import FIELDS
from .postprocess import ResultArchive

def compact_query_result(result):
    """Keep full paths in saved results; send extrema/counts to the interpreter."""
    if 'samples' not in result:return result
    good=[x for x in result['samples'] if x.get('value') is not None]
    compact={k:v for k,v in result.items() if k!='samples'}
    compact['sample_count']=len(result['samples']);compact['undefined_count']=len(result['samples'])-len(good)
    if good:compact.update(minimum=min(good,key=lambda x:x['value']),maximum=max(good,key=lambda x:x['value']))
    return compact

def format_fact(f):
    value=f['value'];unit=f['unit']
    def num(v):return '无定义 / 未评估' if v is None else f'{v:.5g}' if isinstance(v,(float,int)) else str(v)
    def vec(v,scale=1):return ' / '.join(num(x*scale) for x in v)
    statuses={'within_limit':'在已设限值内','exceeded':'超出限值','not_evaluated':'未评估','not_verified':'未验证','converged':'达到变化阈值','needs_review':'需要复核','ok':'已完成','undefined':'无定义','outside':'模型外','partial':'部分完成','unavailable':'不可用'}
    if isinstance(value,(float,int)):
        if unit=='Pa':return num(value*1e-6)+' MPa'
        if unit=='m':return num(value*1000)+' mm'
        return num(value)+(' '+unit if unit else '')
    if isinstance(value,str):return statuses.get(value,value)
    if isinstance(value,list):return '；'.join(str(v) for v in value) or '无额外提示'
    if isinstance(value,dict):
        if 'force_relative' in value:return ('通过' if value['passed'] else '未通过')+'；力相对残差 '+num(value['force_relative'])+'，力矩相对残差 '+num(value['moment_relative'])
        if 'levels' in value or value.get('status') in ('not_verified','converged','needs_review'):
            return statuses.get(value['status'],value['status'])+(f"；末两档位移变化 {value['displacement_change']:.2%}，应力变化 {value['stress_change']:.2%}" if 'displacement_change' in value else '')
        if 'node' in value and 'position_m' in value:return '节点 '+str(value['node'])+'；位置 '+vec(value['position_m'],1000)+' mm'
        lines=[]
        labels={'value':'结果','position_m':'位置 / mm','displacement_m':'位移 / mm','deformed_position_m':'变形后坐标 / mm','relative_displacement_m':'相对位移 / mm','magnitude_m':'相对位移幅值 / mm','reaction_N':'反力 / N','moment_Nm':'反力矩 / N·m','moment_origin_m':'力矩原点 / mm','mean':'平均值','minimum':'最小值','maximum':'最大值','stiffness_N_m':'等效刚度 / N·m⁻¹','compliance_m_N':'柔度 / m·N⁻¹','load_N':'载荷 / N','reason':'说明','basis':'口径'}
        if 'status' in value:lines.append(statuses.get(value['status'],value['status']))
        for key,label in labels.items():
            if key not in value:continue
            v=value[key]
            if isinstance(v,list):v=vec(v,1000 if key.endswith('_m') else 1)
            elif isinstance(v,dict):v=format_fact({'value':v,'unit':value.get('unit','')})
            elif isinstance(v,(float,int)) and key.endswith('_m') and key not in ('stiffness_N_m',):v=num(v*1000)
            else:v=num(v)
            lines.append(label+'：'+v+(' '+value.get('unit','') if key in ('value','mean') else ''))
        return '；'.join(lines)
    return num(value)

def interpret(task,jobs,failed,cancelled,progress,requests=()):
    if task['provider']=='local':return {'status':'not_requested','message':'本地规则模式不调用 Codex 结果分析','observations':[],'queries':[]}
    facts=[];query_results=[];seen=set();archives={};allowed={j['id']:j for j in jobs}
    def add(jid,label,value,unit='',path=''):
        facts.append({'id':f'f{len(facts)+1:04d}','job_id':jid,'label':label,'value':value,'unit':unit,'source':path})
    for j in jobs:
        s=j['summary'];jid=j['id']
        for key,label,unit in [('maximum_von_mises_Pa','最大原始积分点等效应力','Pa'),('maximum_displacement_m','最大位移','m'),
            ('minimum_safety_factor','最小所选判据安全系数',''),('strain_energy_J','总应变能','J')]:
            if key in s:add(jid,label,s[key],unit,'summary.'+key)
        add(jid,'强度判定',s['checks']['strength']['status'],path='summary.checks.strength.status')
        add(jid,'平衡检查',s['balance'],path='summary.balance')
        add(jid,'网格收敛',s['convergence'],path='summary.convergence')
        add(jid,'适用范围提示',s['warnings'],path='summary.warnings')
        add(jid,'最大位移位置',s['displacement_peak'],path='summary.displacement_peak')
        if s.get('bonding'):
            bond=s['bonding']
            add(jid,'理想粘结位移兼容相对误差',bond['compatibility_relative'],path='summary.bonding.compatibility_relative')
            add(jid,'理想粘结从节点数',bond['tied_nodes'],path='summary.bonding.tied_nodes')
            add(jid,'理想粘结模型范围',bond['scope'],path='summary.bonding.scope')
            for i,pair in enumerate(bond['interfaces']):
                add(jid,'接口传力：'+pair['name'],{'reaction_N':pair['transfer_to_slave_N'],
                    'moment_Nm':pair['transfer_moment_about_origin_Nm'],'moment_origin_m':pair['moment_origin_m'],
                    'basis':'理想接口作用于从组件的合力与关于指定原点的力矩，不是支撑反力或滚动接触载荷'},path=f'summary.bonding.interfaces.{i}')
        for q in s.get('result_queries',[]):add(jid,q['name'],compact_query_result(q),path='summary.result_queries')
    def query(jid,q):
        if jid not in allowed:raise ValueError('不能查询本任务之外的作业')
        key=json.dumps([jid,q.model_dump()],sort_keys=True)
        if key in seen:raise ValueError('查询已执行，请引用现有事实')
        seen.add(key)
        if jid not in archives:archives[jid]=ResultArchive(DATA/'jobs'/jid)
        result=archives[jid].query(q);query_results.append({'job_id':jid,'request':q.model_dump(),'result':result})
        # Paths retain every sample on disk/UI, but send compact facts to the model.
        add(jid,q.name,compact_query_result(result),path='feedback.query.'+str(len(query_results)-1))
    for q in requests:
        for jid in allowed:
            try:query(jid,q)
            except ValueError as e:add(jid,q.name,{'status':'unavailable','reason':str(e)})
    context={'conversation':task['conversation'][-8:],'jobs':[{'id':j['id'],'name':j['name']} for j in jobs],
        'failed':failed,'result_catalog':list(FIELDS.values()),'facts':facts,'query_errors':[]}
    feedback_folder=DATA/'tasks'/task['id']/'feedback'
    result={'status':'failed','message':'尚未完成结果解释','observations':[],'queries':query_results,'facts':facts}
    try:
        for turn in range(3):
            if cancelled():raise InterruptedError('任务已取消')
            progress('analyzing',f'Codex 正在核对真实结果（{turn+1}/3）')
            context['remaining_rounds']=3-turn
            decision=agent.review(context,task['provider'],feedback_folder/str(turn+1),cancelled)
            if cancelled():raise InterruptedError('任务已取消')
            fact_map={f['id']:f for f in facts}
            if re.search(r'\d',decision.message+' '.join(o.comment for o in decision.observations)):
                context['query_errors']=['解释文字不能直接包含数值，请通过 fact_id 引用'];continue
            if any(o.fact_id not in fact_map for o in decision.observations):
                context['query_errors']=['存在未知 fact_id'];continue
            if decision.action=='query':
                context['query_errors']=[]
                for request in decision.queries:
                    try:query(request.job_id,request.query)
                    except ValueError as e:context['query_errors'].append(str(e))
                continue
            if jobs and not decision.observations:
                context['query_errors']=['必须引用至少一条真实结果事实'];continue
            observations=[{'comment':o.comment,'fact':fact_map[o.fact_id]} for o in decision.observations]
            lines=[decision.message]
            for item in observations:
                f=item['fact']
                lines.append(f"{item['comment']}\n依据 [{allowed[f['job_id']]['name']} / {f['label']}]：{format_fact(f)}")
            result.update(status='completed',message='\n\n'.join(lines),observations=observations,rounds=turn+1)
            break
        else:result.update(status='limited',message='已达到结果反馈轮数上限；真实结果、查询和确定性报告已保留。',rounds=3)
    except InterruptedError:raise
    except Exception as e:result.update(status='failed',message='数值结果已完成；Codex 结果解释未完成：'+str(e))
    store.write_json(DATA/'tasks'/task['id']/'feedback.json',result)
    return result
