"""Prepare deterministic candidates and compile the Codex skill's small edits."""
import copy
import json
import re

from pydantic import ValidationError
from thermal_legacy.schemas import Heat, Simulation


def skill_root(engine):
    return engine.ROOT / '.agents' / 'skills' / 'thermal-config'


def draft_current(model_id, current):
    value = {**copy.deepcopy(current or {}), 'model_id': model_id}
    try:
        return Simulation.model_validate(value).model_dump(mode='json')
    except ValidationError:
        # A manually entered zero step or a heater awaiting its selection is
        # editable input, not a runnable configuration. Keep invalid/missing
        # required values for clarification; add only ordinary schema defaults.
        original = Simulation(model_id=model_id).model_dump(mode='json')
        original.update(value)
        for heat in original.get('heat_sources', []):
            if isinstance(heat, dict):
                for key, field in Heat.model_fields.items():
                    if key not in heat and not field.is_required():
                        heat[key] = field.get_default(call_default_factory=True)
        return original


def prepare(engine, model_id, prompt, current, conversation=None):
    if current and current.get('model_id', model_id) != model_id:
        raise ValueError('当前配置属于其他模型。')
    original = draft_current(model_id, current)
    from thermal_legacy.agent_conversation import rule_candidate, user_prompts
    candidate = rule_candidate(engine, model_id, prompt, original, conversation)
    geometry_request = '\n'.join(user_prompts(prompt, conversation))
    selectors = engine._surface_selections(model_id, engine._requested_selections(geometry_request))
    public_selectors = {key: {'face_count': value['face_count'], 'area_m2': value.get('area_m2'), 'surface_box':value.get('surface_box')} for key, value in selectors.items()
                        if not key.startswith('component:') or int(key.split(':')[1]) in
                        {int(n) for n in re.findall(r'(?:组件|部件)\s*(\d+)', geometry_request)}}

    def compact(config, prefix):
        result = copy.deepcopy(config)
        groups=[(field,result.get(field, [])) for field in ('heat_sources','cooling')]
        if result.get('surface_evaluation'):groups.append(('surface_evaluation',[result['surface_evaluation']]))
        groups.append(('structural/supports',(result.get('structural') or {}).get('supports',[])))
        for field, entries in groups:
            for index, group in enumerate(entries):
                faces = group.pop('faces', [])
                if not faces:
                    continue
                token = next((name for name, value in selectors.items() if value['faces'] == faces), None)
                if token is None:
                    token = f'{prefix}:{field}:{index}'
                    selectors[token] = dict(faces=faces, face_count=len(faces), name='保留现有选区')
                public_selectors[token] = {'face_count': len(faces),'area_m2':selectors[token].get('area_m2'),'surface_box':selectors[token].get('surface_box')}
                group['surface_selection'] = token
        return result

    metadata = engine.read_json(engine.MODELS / model_id / 'metadata.json')
    public = dict(request=prompt, model_id=model_id,
                  geometry=dict(dimensions_m=metadata.get('dimensions_m'), bounds_m=metadata.get('bounds_m'),
                                components=[{k: r[k] for k in ('component_id', 'name', 'bounds_m') if k in r}
                                            for r in metadata.get('components', [])]),
                  current=compact(original, 'current'), candidate=compact(candidate['config'], 'candidate'),
                  available_selections=public_selectors,
                  rule_questions=candidate.get('questions', []), rule_warnings=candidate.get('warnings', []))
    if conversation is not None:
        public['conversation'] = copy.deepcopy(conversation)
    from thermal_legacy.agent_parameters import parameter_contract
    public['parameter_contract'] = parameter_contract()
    from thermal_legacy.host_geometry import model_evidence,capabilities
    public['host_geometry_evidence']=model_evidence(engine.MODELS/model_id)
    public['host_capabilities']=capabilities()
    if (conversation or {}).get('workflow_context'):
        public['workflow_context']=copy.deepcopy(conversation['workflow_context'])
    if re.search(r'中心|中间|中部|中央|正中', geometry_request):
        center = [(a+b)/2 for a,b in zip(*metadata['bounds_m'])]
        public['geometry'].update(center_m=center, center_in_solid=engine._point_in_solid(model_id, center))
        for component in public['geometry']['components']:
            if component.get('bounds_m') and str(component['component_id']+1) in re.findall(r'(?:组件|部件)\s*(\d+)', geometry_request):
                center = [(a+b)/2 for a,b in zip(*component['bounds_m'])]
                component.update(center_m=center, center_in_solid=engine._point_in_solid(model_id, center))
    return dict(model_id=model_id, original=original, candidate=candidate['config'],
                selectors=selectors, public=public, warnings=candidate.get('warnings', []))


def review_instructions(engine):
    text = (skill_root(engine) / 'SKILL.md').read_text(encoding='utf-8')
    instructions = text.split('<!-- prepared-review -->', 1)[1].split('<!-- /prepared-review -->', 1)[0].strip()
    return ('宿主已加载下列 thermal-config skill 说明并完成规则提取、几何计算。这是纯文本核对阶段：'
            '不要调用工具、读取或写入文件、运行脚本或再次加载技能；只输出修正 JSON。\n'
            + instructions + '\n宿主新增热弹性能力：structural={mode:free或constrained,reference_C:无应力温度,supports:[{name,surface_selection,axes}]}。'
            '固定面用非空surface_selection映射，axes可为x/y/z/xy/xz/yz/xyz。材料必须有young_modulus_Pa和poisson_ratio，线膨胀系数应按需求明确设置。'
            'design_limits支持minimum_C、maximum_C、maximum_displacement_m、strength_safety_factor，屈服强度在材料yield_strength_Pa；缺少限值只能表示未评估，不得承诺安全。'
            'environment_only=true明确表示无热源的高低温环境试验，可保留空heat_sources，但必须有换热边界。'
            '材料支持metal/polymer/glass/ceramic/elastomer/composite/other类别；玻璃和陶瓷使用strength_criterion=principal，抗拉/抗压强度为tensile_strength_Pa/compressive_strength_Pa，不得用弯曲强度替代抗拉强度。'
            '高分子预设为参考示例，保留data_source和property_notes；修改物性后注明原始来源不等同当前输入。service_min_C/max_C是使用温区，valid_min_C/max_C是物性有效温区，glass_transition_C是Tg，不能互相替代，也不要把Tg配置成潜热熔点。'
            '热弹性求解支持小变形、各向同性、常物性及固定面，不支持塑性、蠕变、疲劳寿命、机械接触。温度/位移/应力场及评估报告由宿主自动输出，无需新增输出字段。'
            '请使用host_geometry_evidence和host_capabilities中的已核验证据，不要重复索要已提供的选区面积和宿主能力。'
            '稳态热源有效条件包含结束时刻，即start_s<=duration_s<=end_s。现有热源0到600秒在600秒稳态标签有效，不能擅自延长到610秒。'
            '环境温度曲线的全部转折点由宿主自动加入输出时刻，无需额外保存字段。多工况由独立工作流调度器负责；当前仅配置正在核对的一个工况。'
            '若workflow_context.phase=planning且deferred_initial_from非空，宿主已记录对计划内父工况的温度场依赖。此时initial_from_job暂为null是合法的待执行草案；实际父结果完成后，宿主会再次调用Agent绑定真实ID并校验网格和温度场。不要在计划阶段向用户索要尚未产生的结果ID，不要声称当前已经继承了温度场。'
            '计划阶段的“清除旧结果绑定”仅清理当前配置中的initial_from_job，不取消workflow_context.deferred_initial_from记录的计划依赖。'
            '精确box选区的area_m2为实际裁剪面积；不能以bottom为空否定其他有效选区。')


def prompt_text(engine, prepared):
    return (review_instructions(engine) + '\n输入数据：\n'
            + json.dumps(prepared['public'], ensure_ascii=False, separators=(',', ':')))


def _check_no_faces(value):
    if isinstance(value, dict):
        if 'faces' in value:
            raise ValueError('面编号只能由本地选区映射生成；请使用 surface_selection。')
        for item in value.values():
            _check_no_faces(item)
    elif isinstance(value, list):
        for item in value:
            _check_no_faces(item)


def apply_patch_value(config, path, value):
    if not isinstance(path, str) or not path.startswith('/'):
        raise ValueError('参数路径必须以 / 开头。')
    keys = [part.replace('~1', '/').replace('~0', '~') for part in path[1:].split('/')]
    if keys[0] not in Simulation.model_fields or keys[0] == 'model_id' or 'faces' in keys:
        raise ValueError('不允许修改该参数路径：' + path)
    _check_no_faces(value)
    target = config
    for key in keys[:-1]:
        if isinstance(target, list):
            if not key.isdigit() or int(key) >= len(target):
                raise ValueError('参数数组位置不存在：' + path)
            target = target[int(key)]
        elif isinstance(target, dict) and key in target:
            target = target[key]
        else:
            raise ValueError('参数路径不存在：' + path)
    last = keys[-1]
    if isinstance(target, list):
        if last == '-':
            target.append(value)
        elif last.isdigit() and int(last) < len(target):
            target[int(last)] = value
        else:
            raise ValueError('参数数组位置不存在：' + path)
    elif isinstance(target, dict):
        target[last] = value
        if last == 'surface_selection':
            target.pop('faces', None)
        if last == 'source_type' and value == 'point':
            target['faces'] = []
    else:
        raise ValueError('参数路径不可修改：' + path)


def compile_review(engine, prepared, text):
    review = engine._json_from_text(text)
    if not isinstance(review, dict) or set(review) != {'patch', 'questions'}:
        raise ValueError('Skill 必须返回 patch 和 questions。')
    if not isinstance(review['patch'], list) or not isinstance(review['questions'], list):
        raise ValueError('patch 与 questions 必须为数组。')
    if len(review['patch']) > 64 or any(not isinstance(q, str) for q in review['questions']):
        raise ValueError('Skill 返回了无效修正或问题。')
    config = copy.deepcopy(prepared['candidate'])
    for edit in review['patch']:
        if not isinstance(edit, dict) or set(edit) != {'path', 'value_json'} or not isinstance(edit['value_json'], str):
            raise ValueError('每项修正必须包含 path 和 value_json。')
        value = json.loads(edit['value_json'])
        apply_patch_value(config, edit['path'], value)
    if review['questions']:
        return None, review['questions']
    for group in [*config.get('heat_sources', []), *config.get('cooling', []), *(config.get('structural') or {}).get('supports',[]), *([config['surface_evaluation']] if config.get('surface_evaluation') else [])]:
        selector = group.pop('surface_selection', None)
        if selector is not None:
            selection = prepared['selectors'].get(selector)
            if not selection or not selection['faces']:
                raise ValueError(f'当前模型没有可用的“{selector}”选区。')
            group['faces'] = selection['faces'].copy()
            if selection.get('surface_box'):group['surface_box']=selection['surface_box']
    # Reuse the same final Pydantic and geometry validation as other providers.
    validated = engine._validated_codex_text(json.dumps(config), prepared['original'], prepared['model_id'])
    return validated, []
