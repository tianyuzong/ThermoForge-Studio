"""Schema-derived agent contract and complete, human-readable parameter diffs."""
from thermal_legacy.schemas import Simulation

LABELS = {
    'SurfaceBox':dict(min_m='选区盒最小坐标 (m)',max_m='选区盒最大坐标 (m)'),
    'AmbientPoint':dict(time_s='时刻 (s)',ambient_C='环境温度 (°C)'),
    'SurfaceEvaluation':dict(name='评估面名称',faces='评估面',surface_box='评估范围',maximum_C='接触面温度限值 (°C)',flatness_limit_m='接触面翘曲限值 (m)',reference_power_W='热阻参考功率 (W)'),
    'Simulation': dict(model_id='模型标识（只读）', name='算例名称', base_material='基础材料',
        regions='材料区域', component_materials='组件材料', heat_sources='热源', cooling='局部散热',
        structural='热弹性分析', design_limits='设计限值', environment_only='纯环境温变工况',
        initial_from_job='续算来源算例',ambient_profile='环境温度时间曲线',surface_evaluation='接触面专项评估',
        analysis_mode='分析类型', initial_C='初始温度 (°C)', ambient_C='全局环境温度 (°C)',
        radiation_ambient_C='全局辐射环境温度 (°C)', default_h='默认换热系数 (W/(m²·K))',
        heat_convection='热源面参与默认对流', radiation_enabled='全局辐射', emissivity='全局发射率',
        air_gap_enabled='空气间隙耦合', air_gap_k_W_mK='空气间隙导热系数 (W/(m·K))',
        air_gap_max_m='最大空气间隙 (m)', contact_resistance_m2K_W='面积接触热阻 (m²·K/W)',
        duration_s='仿真总时长 (s)', dt_s='时间步长 (s)', save_s='保存间隔 (s)', mesh_size_m='网格尺寸 (m)'),
    'Material': dict(name='材料名称', k='导热系数 (W/(m·K))', rho='密度 (kg/m³)', cp='比热容 (J/(kg·K))',
        thermal_expansion_CTE_per_K='线膨胀系数 (1/K)', phase_change='相变',young_modulus_Pa='弹性模量 (Pa)',
        poisson_ratio='泊松比',yield_strength_Pa='屈服强度 (Pa)',category='材料类别',strength_criterion='强度判据',
        tensile_strength_Pa='抗拉强度 (Pa)',compressive_strength_Pa='抗压强度 (Pa)',
        reference_temperature_C='物性参考温度 (°C)',valid_min_C='物性有效最低温度 (°C)',valid_max_C='物性有效最高温度 (°C)',
        service_min_C='材料最低使用温度 (°C)',service_max_C='材料最高使用温度 (°C)',glass_transition_C='玻璃化转变温度 (°C)',
        data_source='材料数据来源',property_notes='物性说明'),
    'Structural': dict(mode='支撑模式',reference_C='无应力参考温度 (°C)',supports='固定支撑'),
    'StructuralSupport': dict(name='支撑名称',faces='固定面',axes='固定方向'),
    'DesignLimits': dict(minimum_C='允许最低温度 (°C)',maximum_C='允许最高温度 (°C)',
        maximum_displacement_m='允许最大位移 (m)',strength_safety_factor='强度安全系数'),
    'PhaseChange': dict(melting_C='起熔温度 (°C)', mushy_C='相变温区宽度 (K)', latent_J_kg='潜热 (J/kg)'),
    'Region': dict(name='区域名称', min_m='区域下界 XYZ (m)', max_m='区域上界 XYZ (m)', material='区域材料'),
    'ComponentMaterial': dict(component_id='组件索引（从0开始）', material='组件材料'),
    'Heat': dict(surface_box='精确受热范围',name='热源名称', source_type='热源类型', placement='放置方式', power_W='总功率 (W)',
        start_s='开始时间 (s)', end_s='结束时间 (s)', faces='受热面', position_m='点位置 XYZ (m)',
        radius_m='作用半径 (m)', power_profile='功率曲线', thermostat='温控'),
    'PowerPoint': dict(time_s='曲线时刻 (s)', power_W='曲线功率 (W)'),
    'Thermostat': dict(target_C='目标温度 (°C)', hysteresis_C='温控滞回 (K)',
        min_power_W='最小功率 (W)', max_power_W='最大功率 (W)'),
    'Cooling': dict(surface_box='精确散热范围',name='散热区名称', h='局部换热系数 (W/(m²·K))', ambient_C='局部环境温度 (°C)',
        faces='散热选区', radiation='局部辐射', emissivity='局部发射率'),
}


def parameter_contract():
    """Keep optional/nested fields discoverable even when current values are null."""
    schema = Simulation.model_json_schema()
    models = {'Simulation': schema, **schema['$defs']}
    result = {}
    def compact(value):
        if isinstance(value, list):
            return [compact(v) for v in value]
        if isinstance(value, dict):
            return {k: (v.rsplit('/',1)[-1] if k=='$ref' else compact(v))
                    for k,v in value.items() if k not in ('title','description','default')}
        return value
    for name, model in models.items():
        fields = {}
        for key, field in model['properties'].items():
            if key == 'faces':
                fields['surface_selection'] = dict(type='string', meaning='使用 available_selections 中的非空键，由本地映射面编号')
            else:
                fields[key] = {**compact(field), 'meaning': LABELS.get(name,{}).get(key,key)}
        result[name] = dict(fields=fields, required=[('surface_selection' if key=='faces' else key) for key in model.get('required',[])])
    result['Simulation']['rules']=['duration_s/dt_s <= 5000','duration_s/save_s <= 300',
        '热源 end_s > start_s；面热源须有非空选区，点或嵌入热源须有位置。',
        '确认运行须至少一个热源；运行网格尺寸不得小于模型最长边/130；参数不满足时询问，不得改写用户值。']
    return result


def validation_message(error):
    if not callable(getattr(error,'errors',None)):
        return str(error).split('\n')[0]
    messages=[]
    for item in error.errors(include_url=False)[:3]:
        path=' / '.join(str(v) for v in item['loc'])
        messages.append((path+'：' if path else '')+item['msg'].removeprefix('Value error, '))
    return '；'.join(messages)


def describe_changes(config, current=None):
    old = current or {}
    schema = Simulation.model_json_schema()
    models = {'Simulation':schema, **schema['$defs']}
    changes=[]
    enum_labels=dict(point='点热源',surface='表面',embedded='嵌入',external='外置',steady='稳态',transient='瞬态')
    def render(value, key=''):
        if value is None: return '已清除'
        if value is True: return '开启'
        if value is False: return '关闭'
        if key=='faces': return f'{len(value)} 个三角面'
        if isinstance(value,list): return '['+', '.join(render(v) for v in value)+']'
        if isinstance(value,(int,float)): return f'{value:g}'
        return enum_labels.get(value,value) if isinstance(value,str) else str(value)
    def ref(field):
        if '$ref' in field: return field['$ref'].rsplit('/',1)[-1]
        return next((ref(v) for v in field.get('anyOf',[]) if '$ref' in v),None)
    def walk(new,before,model,prefix=''):
        for key,field in models[model]['properties'].items():
            if key=='model_id' or key not in new: continue
            value=new[key]; previous=before.get(key) if isinstance(before,dict) else None
            if value==previous: continue
            label=prefix+LABELS[model][key]
            nested=ref(field)
            item_ref=ref(field.get('items',{}))
            if nested and isinstance(value,dict):
                walk(value,previous,nested,label+' / ')
            elif item_ref and isinstance(value,list):
                previous=previous if isinstance(previous,list) else []
                if not value:
                    changes.append(label+'：已清空')
                for index,item in enumerate(value):
                    earlier=previous[index] if index<len(previous) else None
                    if item==earlier: continue
                    entity=label+f' {index+1}'+(f'“{item["name"]}”' if item.get('name') else '')
                    if item_ref=='Heat':
                        target=(f'{len(item.get("faces",[]))} 个三角面' if item['source_type']=='surface' and item['placement']!='embedded'
                                else f'点位置 {item.get("position_m")} m')
                        changes.append(f'{entity}：{item["power_W"]:g} W，{item["start_s"]:g}–{item["end_s"]:g} s，{target}')
                    walk(item,earlier,item_ref,entity+' / ')
                if len(previous)>len(value) and value:
                    changes.append(label+f'数量：{len(value)}（已移除多余项）')
            else:
                changes.append(label+'：'+render(value,key))
    walk(config,old,'Simulation')
    return changes or ['当前参数无需修改']
