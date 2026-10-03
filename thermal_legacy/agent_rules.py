"""Deterministic parameter extraction shared by the local parser and Codex skill.

Rules produce a candidate, not a claim that arbitrary natural language was fully
understood. The skill reviews that candidate against the original request.
"""
import copy
import re

from thermal_legacy.schemas import PRESETS, Simulation

NUMBER = r'[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?'
TIME_UNIT = r'小时|分钟|秒|min|h|s'
POWER = re.compile(r'(?<![\d.eE])(?P<value>' + NUMBER + r')\s*(?P<unit>kW|千瓦|W|瓦)(?![A-Za-z])(?!(?:\s*[/／]))', re.I)
MATERIALS = [('不锈钢|stainless', 4), ('碳钢|steel', 3),
             ('铝|aluminium|aluminum', 1), ('铜|copper', 0), ('铁|iron', 2)]
from thermal_legacy.material_catalog import NONMETAL_ALIASES
MATERIALS = [(pattern,index+5) for pattern,index in NONMETAL_ALIASES] + MATERIALS
ORDINALS = {value:index for index,value in enumerate(('一','二','三','四','五','六','七','八','九','十','十一','十二','十三','十四','十五','十六'))}
COMMAND = re.compile(r'不(?:再)?(?:启用|开启|使用|考虑)|不要|关闭|禁用|取消|启用|开启|打开|使用|考虑')


def seconds(value, unit):
    return float(value) * (3600 if unit.lower() in ('小时', 'h') else 60 if unit.lower() in ('分钟', 'min') else 1)


def duration(text):
    match = re.search(r'(?:仿真|计算|运行)(?:总时长|时长|时间|持续)?\s*(?:设为|为|=|:|：)?\s*(' + NUMBER + r')\s*(' + TIME_UNIT + r')', text, re.I)
    if not match:
        match = re.search(r'(?:总时长|仿真时长)\s*(?:设为|为|=|:|：)?\s*(' + NUMBER + r')\s*(' + TIME_UNIT + r')', text, re.I)
    return seconds(*match.groups()) if match else None


def time_window(text):
    match = re.search(r'(?:从|在)?\s*(' + NUMBER + r')\s*(' + TIME_UNIT + r')?\s*(?:到|至|[-~～–—])\s*(' + NUMBER + r')\s*(' + TIME_UNIT + r')', text, re.I)
    if match:
        a, u, b, v = match.groups()
        return seconds(a, u or v), seconds(b, v)
    start = re.search(r'(' + NUMBER + r')\s*(' + TIME_UNIT + r')\s*(?:后|时)?\s*(?:开始|启动|开启|打开)', text, re.I)
    end = re.search(r'(' + NUMBER + r')\s*(' + TIME_UNIT + r')\s*(?:后|时)?\s*(?:停止|结束|关闭|关掉)', text, re.I)
    if not start:
        start = re.search(r'(?:开始|启动|开启)(?:时间)?\s*(?:设为|改为|为|=)?\s*(' + NUMBER + r')\s*(' + TIME_UNIT + r')', text, re.I)
    if not end:
        end = re.search(r'(?:结束|停止|关闭)(?:时间)?\s*(?:设为|改为|为|=)?\s*(' + NUMBER + r')\s*(' + TIME_UNIT + r')', text, re.I)
    return (seconds(*start.groups()) if start else None, seconds(*end.groups()) if end else None)


def clauses(text):
    from thermal_legacy.scenario_cases import BOX
    boxes=[]
    def protect(match):
        boxes.append(match.group());return f'§BOX{len(boxes)-1}§'
    protected=BOX.sub(protect,text)
    return [re.sub(r'§BOX(\d+)§',lambda m:boxes[int(m[1])],part.strip())
            for part in re.split(r'[，,。;；\n]+',protected) if part.strip()]


def heat_context(text):
    # Cooling clauses must not change a heater's selection or power.
    text = re.sub(r'(?:并且|同时|并|而)(?=[^，,。;；\n]{0,30}(?:对流|散热|换热系数))', '；', text)
    from thermal_legacy.agent_config_rules import heat_only_text
    return '，'.join(part for part in clauses(heat_only_text(text))
                    if not re.search(r'算例名称|评估接触面|接触面温度限值|接触面翘曲限值|热阻参考功率|从算例|对流|散热|换热系数|环境温度|辐射|固定|支撑|热应力|热弹性|位移限值|允许最大位移|纯环境|无热源', part)
                    and not (re.search(r'(?:组件|部件)\s*\d+', part)
                             and material(part) and not POWER.search(part)))


def feature_value(text, feature):
    value = None
    for clause in clauses(text):
        for match in re.finditer(feature, clause, re.I):
            commands = list(COMMAND.finditer(clause[:match.start()]))
            if commands:
                word = commands[-1].group()
                value = word in ('启用', '开启', '打开', '使用', '考虑')
    return value


def material(text):
    for pattern, index in MATERIALS:
        if re.search(pattern, text, re.I):
            return copy.deepcopy(PRESETS[index])
    return None


def make_plan(engine, model_id, prompt, current):
    model = engine.read_json(engine.MODELS / model_id / 'metadata.json')
    cfg = copy.deepcopy(current or {})
    cfg['model_id'] = model_id
    changes, warnings, questions = [], [], []
    from thermal_legacy.agent_config_rules import normalize, update_parameters
    prompt = normalize(prompt)
    update_parameters(engine, model_id, prompt, cfg, questions)

    heat_text = heat_context(prompt)
    from thermal_legacy.agent_heat import update_sources
    update_sources(engine, model_id, heat_text, cfg, changes, warnings, questions)
    sources = cfg['heat_sources']

    from thermal_legacy.agent_config_rules import update_heat_controls
    update_heat_controls(prompt, cfg, questions)
    from thermal_legacy.agent_structural import update_structural
    update_structural(engine, model_id, prompt, cfg, questions)
    from thermal_legacy.scenario_cases import update_case_parameters
    update_case_parameters(engine, model_id, prompt, cfg, questions)

    if not cfg['heat_sources'] and not cfg.get('environment_only'):
        questions.append('草案缺少热源：请指定功率与受热面，或点热源位置。')
    if cfg.get('mesh_size_m', .035) < max(model['dimensions_m']) / 130:
        warnings.append('已保留指定网格尺寸；较细网格可能增加计算时间和内存。')
    try:
        validated = Simulation.model_validate(cfg).model_dump(mode='json')
        engine._validate_geometry(model_id, validated)
    except (ValueError, KeyError) as error:
        from thermal_legacy.agent_parameters import validation_message
        questions.append('参数组合需要调整：' + validation_message(error))
        validated = cfg
    if not questions:
        changes = engine._codex_changes(validated, current or {})
    return dict(ok=not questions, config=validated, changes=changes, warnings=warnings, questions=questions)
