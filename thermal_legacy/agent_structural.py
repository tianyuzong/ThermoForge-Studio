"""Scoped conversational edits for pre-manufacturing thermal assessment."""
import re
from thermal_legacy.agent_config_rules import value, _flag


def update_structural(engine,model_id,prompt,cfg,questions):
    from thermal_legacy.agent_rules import clauses
    needs_scope=False;scope_given=cfg.get('structural') is not None
    for part in clauses(prompt):
        if re.search(r'关闭|取消|禁用',part) and re.search(r'热应力|热弹性|结构分析',part):
            cfg['structural']=None;continue
        if re.search(r'纯环境(?:温变|试验|工况)|无热源(?:的)?(?:高低温|环境)',part) and not re.search(r'取消纯环境|关闭纯环境',part):
            cfg['environment_only']=True;cfg['heat_sources']=[]
        if re.search(r'取消纯环境|关闭纯环境',part):cfg['environment_only']=False
        enable=bool(re.search(r'启用|开启|计算|分析|预测',part) and re.search(r'热应力|热弹性|热变形',part))
        support=bool(re.search(r'固定|支撑|约束',part) and not re.search(r'无约束|不固定|取消|清除',part))
        free=bool(re.search(r'自由(?:膨胀|热弹性|状态)|无约束',part))
        if enable or support or free:
            if cfg.get('structural') is None:cfg['structural']={'mode':'free','reference_C':cfg.get('initial_C',25),'supports':[]}
            if enable and not (support or free):
                needs_scope=True
            if support or free:scope_given=True
        st=cfg.get('structural')
        if st is not None:
            ref=value(part,r'无应力参考温度|应力自由温度|结构参考温度|reference_C','temperature')
            if ref is not None:st['reference_C']=ref
            if free:st['mode']='free';st['supports']=[]
            if re.search(r'清空|清除|删除',part) and re.search(r'固定|支撑',part):st['supports']=[]
            if support:
                selection=engine._selection_from_text(part)
                if selection:
                    faces=engine._surface_selections(model_id,engine._requested_selections(part)).get(selection,{}).get('faces',[])
                    if not faces:questions.append('固定面选区为空，请指定当前模型上的有效支撑位置。')
                    else:
                        axis_match=re.search(r'(?:仅|只|沿|固定|约束|方向[为是=:：]?)\s*([xyzXYZ]{1,3})(?:方向|轴)?',part)
                        axes=''.join(a for a in 'xyz' if a in axis_match.group(1).lower()) if axis_match else 'xyz'
                        st['mode']='constrained'
                        record={'name':selection+' 固定支撑','faces':faces,'axes':axes}
                        records=st.setdefault('supports',[])
                        index=next((i for i,r in enumerate(records) if r['faces']==faces),None)
                        if index is None:records.append(record)
                        else:records[index]=record
                elif re.search(r'固定面|固定支撑|受约束|底部固定|顶部固定',part):
                    st['mode']='constrained'
                    if not st.get('supports'):questions.append('请说明固定支撑位于哪个面，例如底面固定。')
        for key,alias,kind in [('minimum_C',r'允许最低温度|温度下限','temperature'),('maximum_C',r'允许最高温度|温度上限','temperature'),
                               ('maximum_displacement_m',r'允许最大(?:热)?位移|位移限值','length'),('strength_safety_factor',r'强度安全系数|屈服安全系数','plain')]:
            if re.search(r'材料.*使用温度|物性有效',part):continue
            number=value(part,alias,kind)
            if number is not None:
                if cfg.get('design_limits') is None:cfg['design_limits']={}
                cfg['design_limits'][key]=number
        if re.search(r'清除|取消|关闭',part) and '设计限值' in part:cfg['design_limits']=None
    if needs_scope and not scope_given:
        questions.append('请明确结构为自由状态，还是提供固定面和固定方向。')
