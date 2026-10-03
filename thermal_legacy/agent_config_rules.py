"""Scoped extraction of global settings, material properties and boundaries."""
import copy
import re

from thermal_legacy.schemas import PRESETS

NUMBER = r'[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?'
SET = r'\s*(?:(?:先|最终)?(?:设(?:置)?为|设成|改为|改成|调整为|调整到|调到|均为|都为|均设为|都设为|取|用|采用|是|为|=|:|：))?\s*'
TIME = r'小时|分钟|秒|min|h|s'
LENGTH = r'mm|毫米|cm|厘米|m|米'
TEMP = r'°?C|℃|K'
KEEP = r'保持.*不变|保持原样|不修改|不要改|别改'
MATERIAL_FIELDS = {
    'k': (r'(?<!间隙)(?:导热系数|热导率|\bk\b)', 'conductivity'),
    'rho': (r'密度|\brho\b|ρ', 'density'),
    'cp': (r'比热容?|\bcp\b', 'specific_heat'),
    'thermal_expansion_CTE_per_K': (r'(?:线|热)?膨胀系数|\bCTE\b', 'expansion'),
    'young_modulus_Pa': (r'弹性模量|杨氏模量|\byoung_modulus_Pa\b','stress'),
    'poisson_ratio': (r'泊松比|\bpoisson_ratio\b','plain'),
    'yield_strength_Pa': (r'屈服强度|\byield_strength_Pa\b','stress'),
    'tensile_strength_Pa': (r'抗拉强度|拉伸强度|\btensile_strength_Pa\b','stress'),
    'compressive_strength_Pa': (r'抗压强度|压缩强度|\bcompressive_strength_Pa\b','stress'),
    'reference_temperature_C': (r'物性参考温度|材料参考温度|\breference_temperature_C\b','temperature'),
    'valid_min_C': (r'物性有效最低温度|\bvalid_min_C\b','temperature'),
    'valid_max_C': (r'物性有效最高温度|\bvalid_max_C\b','temperature'),
    'service_min_C': (r'材料最低使用温度|材料使用温度下限|\bservice_min_C\b','temperature'),
    'service_max_C': (r'材料最高使用温度|材料使用温度上限|\bservice_max_C\b','temperature'),
    'glass_transition_C': (r'玻璃化转变温度|\bTg\b|\bglass_transition_C\b','temperature'),
}
PHASE_FIELDS = {'melting_C':(r'起熔温度|熔化温度|熔点','temperature'),
                'mushy_C':(r'相变温区(?:宽度)?|糊状区(?:宽度)?','difference'),
                'latent_J_kg':(r'(?:相变)?潜热','latent')}
ROOT_FIELDS = {
    'initial_C': (r'(?:初始|起始)温度','temperature'),
    'ambient_C': (r'(?<!辐射)(?:环境温度|室温)','temperature'),
    'radiation_ambient_C': (r'辐射(?:环境|背景)?温度','temperature'),
    'default_h': (r'(?:(?:全局|默认)\s*)?(?:对流)?换热系数|对流系数|\bh\b','coefficient'),
    'emissivity': (r'(?:全局)?发射率','plain'),
    'air_gap_k_W_mK': (r'(?:空气)?间隙(?:导热系数|热导率)','conductivity'),
    'air_gap_max_m': (r'(?:最大|最长)(?:空气)?间隙(?:距离)?|(?:空气)?间隙(?:最大距离|上限)','length'),
    'contact_resistance_m2K_W': (r'(?:面积)?接触热阻','resistance'),
    'duration_s': (r'(?:仿真|计算|运行)(?:总时长|时长|时间|持续)?|总时长','time'),
    'dt_s': (r'(?:计算|时间)?步长','time'),
    'save_s': (r'(?:保存|输出)(?:时间)?间隔','time'),
    'mesh_size_m': (r'网格(?:尺寸|大小)?|\bmesh\b','length'),
}
UNIT = dict(time=TIME,length=LENGTH,temperature=TEMP,difference=TEMP,
            stress=r'GPa|MPa|kPa|Pa|吉帕|兆帕|帕',
            power=r'kW|W|千瓦|瓦',
            conductivity=r'kW\s*/?\s*\(?m|W\s*/?\s*\(?m',
            density=r'g\s*/\s*cm[³3]|kg\s*/\s*m[³3]',
            specific_heat=r'kJ\s*/|J\s*/',latent=r'kJ\s*/\s*kg|J\s*/\s*kg',
            expansion=r'ppm\s*/\s*K|1\s*/\s*K|/\s*K',
            resistance=r'mm[²2]\s*[·*]?\s*K\s*/\s*W|m[²2]\s*[·*]?\s*K\s*/\s*W')
CURVE_POINT = re.compile('('+NUMBER+r')\s*('+TIME+r')\s*(?:[:：=]|时(?:功率)?(?:为)?)?\s*('+NUMBER+r')\s*(kW|W|千瓦|瓦)',re.I)


def normalize(text):
    text=text.replace('摄氏度','°C').replace('摄氏','°C').replace('℃','°C')
    text=re.sub(r'负(?=\d)','-',text)
    text=re.sub(r'(\d+(?:\.\d+)?)\s*[×*]\s*10\s*\^?\s*([+-]?\d+)', r'\1e\2', text)
    def split_clear(match):
        verb,body=match.groups()
        parts=re.split(r'、|和|与|及',body)
        if len(parts)>1 and all(re.search(r'热源|散热|对流|组件|材料',part) for part in parts):
            return '；'.join(verb+part for part in parts)
        return match.group()
    return re.sub(r'(清空|清除|删除)([^，,。;；\n]+)',split_clear,text)


def value(text, alias, kind):
    unit=UNIT.get(kind)
    before=r'(?:\s*从\s*'+NUMBER+(r'\s*(?:'+unit+')?' if unit else '')+r')?'
    pattern=r'(?:'+alias+r')'+before+SET+'('+NUMBER+')'+(r'\s*('+unit+')?' if unit else '')
    matches=list(re.finditer(pattern,text,re.I))
    if not matches: return None
    match=matches[-1]; number=float(match.group(1)); suffix=(match.group(2) or '').lower().replace(' ','') if unit else ''
    if kind=='time': number*=3600 if suffix in ('小时','h') else 60 if suffix in ('分钟','min') else 1
    if kind=='length': number*=.001 if suffix in ('mm','毫米') else .01 if suffix in ('cm','厘米') else 1
    if kind=='temperature' and suffix=='k': number-=273.15
    if kind in ('specific_heat','latent') and suffix.startswith('kj'): number*=1000
    if kind=='density' and suffix.startswith('g/'): number*=1000
    if kind=='conductivity' and suffix.startswith('kw'): number*=1000
    if kind=='power' and suffix in ('kw','千瓦'): number*=1000
    if kind=='expansion' and suffix.startswith('ppm'): number*=1e-6
    if kind=='resistance' and suffix.startswith('mm'): number*=1e-6
    if kind=='stress': number*=1e9 if suffix in ('gpa','吉帕') else 1e6 if suffix in ('mpa','兆帕') else 1e3 if suffix=='kpa' else 1
    return number


def _flag(text, feature):
    from thermal_legacy.agent_rules import feature_value
    result=feature_value(text,feature)
    trailing=re.search(r'(?:'+feature+r')\s*(?:设为|改为|设置为)?\s*(开启|启用|打开|关闭|禁用|取消)',text)
    if trailing: result=trailing.group(1) in ('开启','启用','打开')
    return result


def update_materials(prompt, cfg, model, questions):
    from thermal_legacy.agent_rules import clauses, material
    active=None;region_scope=False;phase_requested={}
    for part in clauses(prompt):
        if re.search(KEEP,part): continue
        if re.search(r'(?:清空|清除|删除).*(?:组件材料|材料覆盖)',part):
            target=re.search(r'(?:组件|部件)\s*(\d+)',part)
            if target: cfg['component_materials']=[r for r in cfg.get('component_materials',[]) if r['component_id']!=int(target.group(1))-1]
            else: cfg['component_materials']=[]
            active=None;continue
        if re.search(r'(?:清空|清除|删除).*材料区域',part): cfg['regions']=[];active=None;continue
        if re.search(r'材料区域|区域材料',part):
            if not region_scope: questions.append('材料区域需要核对范围与材料，请使用 Codex 模式或在界面中配置。')
            region_scope=True;active=None;continue
        if re.search(r'基础材料|全局材料|(?:组件|部件)\s*\d+',part): region_scope=False
        if region_scope: continue
        if re.search(r'空气|间隙|接触|散热|对流|热源|网格|环境温度|仿真|步长',part): active=None;continue
        component=re.search(r'(?:组件|部件)\s*(\d+)',part)
        explicit_base=bool(re.search(r'基础材料|全局材料|整个模型.*材料|统一.*材料',part))
        preset=material(part)
        material_text=bool(re.search(r'材料|密度|比热|导热系数|热导率|膨胀系数|相变|起熔|熔点|潜热|弹性模量|杨氏模量|泊松比|强度|物性|玻璃化|\bTg\b',part,re.I))
        if component and (material_text or preset):
            index=int(component.group(1))-1
            if index not in {r['component_id'] for r in model.get('components',[])}:
                questions.append(f'当前模型没有组件 {index+1}。');active=None;continue
            records=cfg.setdefault('component_materials',[])
            record=next((r for r in records if r['component_id']==index),None)
            if record is None:
                record=dict(component_id=index,material=copy.deepcopy(cfg['base_material']));records.append(record)
            if preset: record['material']=preset
            active=record['material']
        elif explicit_base or material_text and active is None or preset and re.search(r'材料|设为|使用|用|换成',part):
            if preset: cfg['base_material']=preset
            active=cfg.setdefault('base_material',copy.deepcopy(PRESETS[0]))
        if active is None: continue
        if re.search(r'自定义材料',part): active['name']='自定义材料'
        title=re.search(r'(?:材料名称|命名为|名称设为)'+SET+r'[“"]([^”"]+)[”"]',part)
        if title: active['name']=title.group(1)
        category=re.search(r'材料类别'+SET+r'(金属|高分子|玻璃|陶瓷|弹性体|复合材料|其他)',part)
        if category:
            active['category']={'金属':'metal','高分子':'polymer','玻璃':'glass','陶瓷':'ceramic','弹性体':'elastomer','复合材料':'composite','其他':'other'}[category.group(1)]
            if active['category'] in ('glass','ceramic'): active['strength_criterion']='principal'
        criterion=re.search(r'强度判据'+SET+r'(主应力|von_mises|von\s*Mises|屈服|不评估|none|principal)',part,re.I)
        if criterion: active['strength_criterion']='principal' if criterion.group(1).lower() in ('主应力','principal') else 'none' if criterion.group(1).lower() in ('不评估','none') else 'von_mises'
        for key,alias in [('data_source','材料数据来源'),('property_notes','物性说明')]:
            match=re.search(alias+SET+r'[“"]([^”"]+)[”"]',part)
            if match: active[key]=match.group(1)
        for key,(alias,kind) in MATERIAL_FIELDS.items():
            if key not in ('k','rho','cp','thermal_expansion_CTE_per_K') and re.search(r'(?:清除|清空|取消)(?:'+alias+')',part,re.I):
                active[key]=None;continue
            number=value(part,alias,kind)
            if number is not None:
                if active.get('data_source') and active.get(key)!=number and '已按对话修改' not in active.get('property_notes',''):
                    active['property_notes']='已按对话修改部分物性；来源指原始参考资料，当前输入不再等同原表数据。'+active.get('property_notes','')
                active[key]=number
        phase_flag=_flag(part,r'相变')
        if phase_flag is not None: phase_requested[id(active)]=(active,phase_flag)
        if phase_flag is False:
            active['phase_change']=None
            if re.search(r'所有材料|全部材料',part):
                for m in [cfg['base_material'],*[r['material'] for r in cfg.get('component_materials',[])],*[r['material'] for r in cfg.get('regions',[])]]: m['phase_change']=None
        else:
            for key,(alias,kind) in PHASE_FIELDS.items():
                number=value(part,alias,kind)
                if number is not None:
                    if active.get('phase_change') is None: active['phase_change']={}
                    active['phase_change'][key]=number
    active_materials=[cfg['base_material'],*[r['material'] for r in cfg.get('component_materials',[])]]
    for material,enabled in phase_requested.values():
        if enabled and any(material is m for m in active_materials) and material.get('phase_change') is None:
            questions.append('启用相变需要起熔温度、相变温区宽度和潜热。')


def update_parameters(engine, model_id, prompt, cfg, questions):
    from thermal_legacy.agent_rules import clauses
    model=engine.read_json(engine.MODELS/model_id/'metadata.json')
    cfg.setdefault('base_material',copy.deepcopy(PRESETS[0]))
    update_materials(prompt,cfg,model,questions)
    unsupported=[(r'固定温度边界|恒温边界|定温边界','当前三维求解器没有固定温度边界，不能用对流系数代替。'),
        (r'CFD|流场|流速|压力场','当前三维求解器不求解流场；空气间隙导热不能代替 CFD。'),
        (r'各向异性|(?:随|依赖|相关).*温度.*(?:导热|物性)|温度相关物性','当前材料参数只支持常数各向同性物性及指定相变模型。'),
        (r'视角因子|视因子','当前辐射模型没有表面对表面的视角因子参数。')]
    unsupported.append((r'塑性(?:变形|分析|求解)|蠕变|疲劳寿命|机械接触|摩擦接触', '当前热弹性模型只支持小变形线弹性与固定支撑，不能计算塑性、蠕变、疲劳寿命或机械接触。'))
    for part in clauses(prompt):
        for pattern,message in unsupported:
            match=re.search(pattern,part,re.I)
            if match and not re.search(r'不要|不需要|无需|不用|不使用|不启用|不求解',part[:match.start()]):
                if message not in questions: questions.append(message)
    if re.search(r'接触热阻[^，,。;；\n]*\d\s*K\s*/\s*W',prompt,re.I):
        questions.append('接触热阻给的是 K/W，而当前参数需要 m²·K/W；请给出接触面积或面积接触热阻。')
    active_cooling=None
    for part in clauses(prompt):
        if re.search(KEEP,part): continue
        if re.search(r'(?:清空|清除|删除|移除).*(?:散热区|局部散热|局部对流)',part):
            target=engine._selection_from_text(part)
            if target and target!='all_outer':
                selection=engine._surface_selections(model_id,[target]).get(target)
                if selection: cfg['cooling']=[r for r in cfg.get('cooling',[]) if r['faces']!=selection['faces']]
            else: cfg['cooling']=[]
            active_cooling=None;continue
        is_boundary=bool(re.search(r'对流|散热|换热|辐射|发射率|环境温度|绝热|\bh\b',part,re.I))
        selector=engine._selection_from_text(part) if is_boundary and not re.search(r'热源|受热面|组件材料',part) else None
        global_scope=bool(re.search(r'全局|默认|全部外表面|所有外表面|整个外表面|(?:其余|其他).*表面',part))
        if selector and selector!='all_outer' and not global_scope:
            selections=engine._surface_selections(model_id,[selector]); selected=selections.get(selector)
            if not selected or not selected['faces']:
                questions.append('局部散热没有可用选区：'+selector);active_cooling=None;continue
            cooling=cfg.setdefault('cooling',[])
            active_cooling=next((r for r in cooling if r['faces']==selected['faces']),None)
            if active_cooling is None:
                active_cooling=dict(name=selected['name']+'散热',faces=selected['faces'],h=cfg.get('default_h',10),
                    ambient_C=cfg.get('ambient_C',25),radiation=cfg.get('radiation_enabled',False),emissivity=cfg.get('emissivity',.8))
                cooling.append(active_cooling)
            from thermal_legacy.scenario_cases import box_from_text
            active_cooling['surface_box']=box_from_text(part)
        elif global_scope or not is_boundary:
            active_cooling=None
        if active_cooling is not None and is_boundary:
            for key,alias,kind in [('h',r'换热系数|对流系数|\bh\b','coefficient'),('ambient_C',r'环境温度|室温','temperature'),('emissivity',r'发射率','plain')]:
                number=value(part,alias,kind)
                if number is not None: active_cooling[key]=number
            flag=_flag(part,r'辐射')
            if flag is not None: active_cooling['radiation']=flag
            if _flag(part,r'对流|散热') is False: active_cooling['h']=0
            if '绝热' in part: active_cooling.update(h=0,radiation=False)
            continue
        if re.search(r'局部环境温度|局部发射率|局部辐射',part):
            questions.append('请说明局部边界参数适用于哪个散热选区。');continue
        if re.search(r'(?:组件\s*\d+|局部).*初始温度',part):
            questions.append('当前三维配置只支持统一初始温度，不能设置组件或局部初始温度。');continue
        for key,(alias,kind) in ROOT_FIELDS.items():
            number=value(part,alias,kind)
            if number is not None: cfg[key]=number
        both=re.search(r'初始温度(?:和|与|、)环境温度'+SET+'('+NUMBER+r')\s*('+TEMP+')?',part,re.I)
        if both:
            number=float(both.group(1))-(273.15 if (both.group(2) or '').lower()=='k' else 0)
            cfg['initial_C']=cfg['ambient_C']=number
        every=re.search(r'每\s*('+NUMBER+r')\s*('+TIME+r')\s*(?:保存|输出)',part,re.I)
        if every:
            from thermal_legacy.agent_rules import seconds
            cfg['save_s']=seconds(*every.groups())
        for key,feature in [('radiation_enabled',r'辐射'),('air_gap_enabled',r'空气间隙|间隙耦合')]:
            flag=_flag(part,feature)
            if flag is not None: cfg[key]=flag
        if _flag(part,r'接触热阻') is False: cfg['contact_resistance_m2K_W']=0
        if re.search(r'热源面|受热面',part) and re.search(r'对流|散热|包括|包含|排除',part):
            cfg['heat_convection']=not bool(re.search(r'不参与|不包括|不包含|排除|不散热',part))
        if re.search(r'全局|默认|全部外表面|所有外表面',part) and _flag(part,r'对流|散热') is False:
            cfg['default_h']=0;cfg['heat_convection']=False
        if global_scope and '绝热' in part:
            cfg['default_h']=0;cfg['radiation_enabled']=False
        mode=list(re.finditer(r'稳态|steady|瞬态|transient',part,re.I))
        if mode:
            cfg['analysis_mode']='steady' if mode[-1].group().lower() in ('稳态','steady') else 'transient'
        title=re.search(r'(?:算例名称|算例命名为|算例叫)'+SET+r'[“"]([^”"]+)[”"]',part)
        if title: cfg['name']=title.group(1)


CONTROL_FIELDS = {'target_C':(r'目标温度|设定温度|\btarget_C\b','temperature'),
    'hysteresis_C':(r'(?:温控)?(?:滞回|回差)(?:温差)?|\bhysteresis_C\b','difference'),
    'min_power_W':(r'(?:最小|最低)功率|\bmin_power_W\b','power'), 'max_power_W':(r'(?:最大|最高)功率|\bmax_power_W\b','power')}
CONTROL_TERMS = r'温控|目标温度|设定温度|滞回|回差|最[大小高低]功率|\b(?:thermostat|target_C|hysteresis_C|min_power_W|max_power_W)\b'


def heat_only_text(prompt):
    """Keep control-curve watts and thermostat limits out of nominal power."""
    from thermal_legacy.agent_rules import clauses
    kept=[];advanced=False
    for part in clauses(prompt):
        if re.search(r'功率曲线|'+CONTROL_TERMS,part,re.I): advanced=True;continue
        if advanced and (CURVE_POINT.search(part) or re.search(r'目标温度|滞回|功率上限|功率下限',part)): continue
        advanced=False
        kept.append(part)
    return '，'.join(kept)


def update_heat_controls(prompt,cfg,questions):
    from thermal_legacy.agent_rules import clauses, ORDINALS, seconds
    # Accept quoted schema keys copied from a thermostat JSON example.
    prompt=re.sub(r'["\x27`]\s*('+ '|'.join(CONTROL_FIELDS) +r')\s*["\x27`]',r'\1',prompt,flags=re.I)
    target=None;mode=None;requested={}
    for part in clauses(prompt):
        if re.search(KEEP+r'|保留.*(?:温控|功率曲线)',part): continue
        ordinal=re.search(r'第([一二三四五六七八九十\d]+)个热源',part)
        if ordinal:
            token=ordinal.group(1);target=int(token)-1 if token.isdigit() else ORDINALS.get(token,-1);mode=None
        named=next((i for i,h in enumerate(cfg['heat_sources']) if h.get('name') and ('“'+h['name']+'”' in part or '"'+h['name']+'"' in part)),None)
        if named is not None: target=named;mode=None
        kind='power_profile' if '功率曲线' in part else 'thermostat' if re.search(CONTROL_TERMS,part,re.I) else None
        if kind:
            mode=kind
            if target is None:
                if len(cfg['heat_sources'])==1: target=0
                elif not re.search(r'全部|所有',part):
                    questions.append('请说明功率曲线或温控适用于哪个热源。');mode=None;continue
        if mode is None: continue
        targets=list(range(len(cfg['heat_sources']))) if re.search(r'全部|所有',part) else [target]
        if any(i is None or i<0 or i>=len(cfg['heat_sources']) for i in targets):
            questions.append('功率曲线或温控引用了不存在的热源。');mode=None;continue
        feature=r'功率曲线' if mode=='power_profile' else r'温控'
        flag=_flag(part,feature)
        for index in targets:
            source=cfg['heat_sources'][index]
            if flag is not None: requested[(index,mode)]=flag
            if flag is False:
                source[mode]=[] if mode=='power_profile' else None;continue
            if mode=='thermostat':
                found={key:value(part,alias,kind) for key,(alias,kind) in CONTROL_FIELDS.items()}
                for key,number in found.items():
                    if number is not None:
                        if source.get('thermostat') is None: source['thermostat']={}
                        source['thermostat'][key]=number
            else:
                points=list(CURVE_POINT.finditer(part))
                if points:
                    if '功率曲线' in part: source['power_profile']=[]
                    for point in points:
                        t,u,p,w=point.groups();source.setdefault('power_profile',[]).append(dict(time_s=seconds(t,u),power_W=float(p)*(1000 if w.lower() in ('kw','千瓦') else 1)))
        if not kind and not re.search(r'目标温度|滞回|功率',part) and not CURVE_POINT.search(part): mode=None
    for (index,kind),enabled in requested.items():
        if enabled and not cfg['heat_sources'][index].get(kind):
            questions.append(f'热源 {index+1} 启用'+('温控需要目标温度、滞回和最大功率。' if kind=='thermostat' else '功率曲线需要时间与功率采样点。'))
