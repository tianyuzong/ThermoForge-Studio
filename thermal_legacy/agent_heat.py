"""Heat-source edits: bind each description to an operation and source first."""
import copy
import re

from thermal_legacy.agent_rules import NUMBER, POWER, ORDINALS, clauses, time_window

CENTER = r'中心|中间|中部|中央|正中'
ADD = re.compile(r'(?:新增|新建|新加|添加|增加|再加|另加|加上|加|放置|放)\s*(?:(?:[一二三四五六七八九十\d]+\s*)?[个处])?\s*(?:新的?|独立的?|单独的?)?\s*(?:' + NUMBER + r'\s*(?:kW|W|千瓦|瓦)\s*的?)?\s*(?:点|面)?热源', re.I)
ORDINAL = re.compile(r'第\s*([一二三四五六七八九十\d]+)\s*个\s*(?:(?:点|面)?热源|(?=放|设|改|移|功率|从|在))')
CLEAR = re.compile(r'(?:清空|清除|删除|移除)\s*(?:(?:之前|以前|原有|已有|旧|全部|所有|现有|的)\s*)*(?:点|面)?热源|(?:已有|原有|全部|所有)热源\s*(?:全部)?(?:清空|清除|删除)')
MOVE = r'移到|移至|移动到|挪到|放在|放到|位置(?:改|设)|搬到|(?:向|往)[左右上下前后].{0,3}(?:挪|移)|[xyz]\s*(?:=|为|在)'
EDIT_FIELD = MOVE + r'|半径|开始|启动|开启|打开|关闭|结束|停止|启停|命名'
PRESERVE = r'保持.*不变|保持原样|不动|别动|不要改|不修改'


def _ordinal(value):
    return int(value)-1 if value.isdigit() else ORDINALS.get(value, -1)


def _positive(match, text):
    return bool(match) and not re.search(r'(?:不要|不用|无需|不必|不再|禁止|不)\s*(?:再)?$', text[:match.start()])


def _groups(text, sources):
    groups = []
    active = None
    for part in clauses(text):
        add_match = ADD.search(part)
        adding = _positive(add_match, part)
        ordinal = ORDINAL.search(part)
        named = next((i for i, h in enumerate(sources) if h.get('name') and
                      re.search(r'[“"「]' + re.escape(h['name']) + r'[”"」]', part)), None)
        if named is None and not adding and re.search(r'[“"「][^”"」]+[”"」]', part) and re.search(r'把|将|删除|移除', part):
            named = -1
        target = _ordinal(ordinal.group(1)) if ordinal else named
        clear_match = CLEAR.search(part)
        clearing = _positive(clear_match, part) and target is None
        deleting = target is not None and _positive(re.search(r'删除|移除|去掉', part), part)
        preserving = bool(re.search(PRESERVE, part)) and not POWER.search(part) and not re.search(MOVE, part)
        if preserving:
            continue
        if clearing:
            groups.append(dict(action='clear', target=None, parts=[]))
            active = None
            part = part[clear_match.end():]
            if not part.strip('。 ，,；;'):
                continue
        # Ordinal/named edits start a new scope, so later corrections override
        # only that heater. Clauses with coordinates/time/radius stay attached.
        starts = adding or target is not None or active is None
        if active is not None and POWER.search(part) and any(POWER.search(p) for p in active['parts']):
            directional = re.search(r'顶部|底部|左侧|右侧|前侧|后侧|外表面|组件|部件|' + CENTER, part)
            starts |= bool(directional and not re.search(r'改为|改成|调到|调整|还是|改口', part))
        if starts:
            if not (adding or target is not None or POWER.search(part) or re.search(r'热源|点源', part)):
                continue
            active = dict(action='delete' if deleting else 'add' if adding else 'edit', target=target, parts=[])
            groups.append(active)
        active['parts'].append(part)
    return groups


def update_sources(engine, model_id, text, cfg, changes, warnings, questions):
    sources = cfg.setdefault('heat_sources', [])
    groups = _groups(text, sources)
    allow_create = not sources
    implicit_slot = 0
    for group in groups:
        action, target = group['action'], group['target']
        if action == 'clear':
            sources.clear()
            allow_create = True
            implicit_slot = 0
            changes.append('已清空已有热源')
            continue
        context = '，'.join(group['parts'])
        matches = list(POWER.finditer(context))
        adding = action == 'add'
        if adding:
            slot = len(sources)
        elif target is not None:
            slot = target
        elif not matches and len(sources) > 1:
            if re.search(EDIT_FIELD, context):
                questions.append('请说明要修改哪个热源，可指定序号或带引号的热源名称。')
            continue
        else:
            slot = implicit_slot
            implicit_slot += 1
        if slot < 0 or (slot >= len(sources) and not adding and not (allow_create and slot == len(sources))):
            questions.append('要修改或删除的热源序号/名称不存在。')
            continue
        if action == 'delete':
            if slot < len(sources):
                changes.append(f'已删除热源“{sources[slot].get("name", slot+1)}”')
                sources.pop(slot)
            else:
                questions.append('要删除的热源不存在。')
            continue
        creating = slot >= len(sources)
        if creating and not matches:
            questions.append('已识别新增热源；请补充新热源的功率（W），不会沿用或修改旧热源的功率。')
            continue
        if not creating and not matches and not re.search(EDIT_FIELD, context):
            continue
        source = copy.deepcopy(sources[slot]) if not creating else dict(
            name=f'热源 {slot+1}', source_type='surface', placement='surface',
            start_s=0, end_s=cfg.get('duration_s', 3600), faces=[])
        if matches:
            match = matches[-1]
            source['power_W'] = float(match['value']) * (1000 if match['unit'].lower() in ('kw', '千瓦') else 1)
        name = re.search(r'(?:命名为|名称设为|叫做)\s*[“"「]([^”"」]+)[”"」]', context)
        if name:
            source['name'] = name.group(1)
        # Names are identifiers, not new geometry instructions.
        context = re.sub(r'[“"「][^”"」]+[”"」]', '', context)
        center = bool(re.search(CENTER, context))
        surface = bool(re.search(r'面热源|外表面|表面|顶部|底部|左侧|右侧|前侧|后侧', context))
        point = bool(re.search(r'点热源|点源|point|嵌入|内部', context, re.I))
        coordinate = bool(re.search(r'(?<![A-Za-z])[xyz]\s*(?:=|为|在)', context, re.I))
        if center and surface and not point:
            questions.append('已识别表面中央的局部热源；请指定受热范围或手动刷选，不能直接替换为整个表面。')
            continue
        if center and not surface or point or coordinate and re.search(MOVE + r'|位置|坐标', context):
            source['source_type'] = 'point'
            if center and not surface or re.search(r'内部|嵌入', context):
                source['placement'] = 'embedded'
        elif surface:
            source['source_type'] = 'surface'
            source['placement'] = 'surface'
        if re.search(r'外置|外部|external', context, re.I):
            source['placement'] = 'external'
        radius = re.search(r'(?:影响|作用|热源)?半径\s*(?:设为|改为|改成|为|=|:|：)?\s*(' + NUMBER + r')\s*(mm|毫米|cm|厘米|m|米)', context, re.I)
        if radius:
            unit = radius.group(2).lower()
            source['radius_m'] = float(radius.group(1)) * (.001 if unit in ('mm','毫米') else .01 if unit in ('cm','厘米') else 1)
        start, end = time_window(context)
        if start is not None:
            source['start_s'] = start
        if end is not None:
            source['end_s'] = end
        point_like = source['source_type'] == 'point' or source['placement'] == 'embedded'
        try:
            position = engine._position_from_prompt(model_id, context, initial=source.get('position_m')) if point_like else None
            selected, note = (None, None) if position is not None else engine._face_selection(model_id, context, 0)
            if point_like:
                if position is None and selected:
                    position = engine._position_from_prompt(model_id, context, selected)
                if position is not None:
                    source['position_m'] = position
                    changes.append(f'热源 {slot+1} 位置: {position} m（点热源）')
                source['faces'] = []
                if center and not surface:
                    warnings.append('“中间/中心”按模型或指定组件的包围盒中心定位，作为局部点热源；请核对位置和作用半径。')
            elif selected is not None:
                source['faces'] = selected
                source['position_m'] = None
                from thermal_legacy.scenario_cases import box_from_text
                source['surface_box'] = box_from_text(context)
                changes.append(note)
        except ValueError as error:
            questions.append(str(error))
        if creating:
            sources.append(source)
        else:
            sources[slot] = source
        changes.append(f'{"新增" if creating else "修改"}热源 {slot+1}: {source["power_W"]:g} W，{source["start_s"]:g}–{source["end_s"]:g} s')
