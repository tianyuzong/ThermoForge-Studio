import copy
import json
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np

from thermal_legacy.runtime import MODELS, ROOT, read_json
from thermal_legacy.schemas import PRESETS, Simulation


def _number(text, pattern):
    match = re.search(pattern, text, re.I)
    return float(match.group(1)) if match else None


def _duration(text):
    matches=list(re.finditer(r'(?:仿真|计算|持续|加热|运行)?\s*(\d+(?:\.\d+)?)\s*(小时|h|分钟|min|秒|s)', text, re.I))
    if not matches:
        return None
    values=[]
    for match in matches:
        value=float(match.group(1));unit=match.group(2).lower()
        values.append(value*(3600 if unit in ('小时','h') else 60 if unit in ('分钟','min') else 1))
    duration=max(values)
    return duration if duration>0 else None


def _display_surface(model_id):
    display = read_json(MODELS / model_id / 'display.json')
    points = np.asarray(display['points'], dtype=float).reshape(-1, 3)
    faces = np.asarray(display['faces'], dtype=np.int64).reshape(-1, 3)
    tri = points[faces]
    centers = tri.mean(axis=1)
    normals = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    lengths = np.linalg.norm(normals, axis=1)
    normals /= np.maximum(lengths[:, None], 1e-30)
    return centers, normals


def _face_selection(model_id, text, triangles):
    selector = _selection_from_text(text)
    if selector is None:
        return None, None
    selections = _surface_selections(model_id, [selector])
    chosen = selections.get(selector)
    if not chosen or not chosen['faces']:
        raise ValueError(f'当前模型没有可用的“{selector}”外表面，请手动刷选或更改选区。')
    return chosen['faces'], f'已选择 {chosen["name"]}（{chosen["face_count"]} 个三角面）'


def _selection_from_text(text):
    from thermal_legacy.scenario_cases import box_from_text,box_token
    box=box_from_text(text)
    if box:return box_token(box)
    lower = text.lower()
    component = re.search(r'(?:组件|部件)\s*(\d+)', text)
    coordinate = re.search(r'(?<![A-Za-z])([xyz])\s*(?:=|为|在)\s*(-?\d+(?:\.\d+)?)\s*(mm|毫米|cm|厘米|m|米)', text, re.I)
    directions = [
        (r'\+z|顶部|顶面|上表面|上方', 'top'), (r'-z|底部|底面|下表面|下方', 'bottom'),
        (r'\+x|右侧|右边', 'right'), (r'-x|左侧|左边', 'left'),
        (r'\+y|后侧|后面|后方', 'back'), (r'-y|前侧|前面|前方', 'front'),
    ]
    side = next((value for pattern, value in directions if re.search(pattern, lower)), None)
    if component:
        return f'component:{int(component.group(1))}' + (f':{side}' if side else '')
    if coordinate and not side:
        axis, value, unit = coordinate.groups()
        value = float(value) * (.001 if unit.lower() in ('mm', '毫米') else .01 if unit.lower() in ('cm', '厘米') else 1)
        return f'{axis.lower()}={value:g}'
    if side:
        return side
    if re.search(r'整个外表面|全部外表面|所有表面|全表面', text, re.I):
        return 'all_outer'
    return None


def _position_from_prompt(model_id, text, faces=None, initial=None):
    model=read_json(MODELS/model_id/'metadata.json')
    bounds = model['bounds_m']
    component = re.search(r'(?:组件|部件)\s*(\d+)', text)
    if component:
        record = next((r for r in model.get('components', []) if r['component_id'] == int(component.group(1))-1), None)
        if not record or not record.get('bounds_m'):
            raise ValueError('无法确定该组件的位置，请指定坐标。')
        bounds = record['bounds_m']
    lo=np.asarray(bounds[0],dtype=float);hi=np.asarray(bounds[1],dtype=float)
    center=(lo+hi)/2
    position=np.asarray(initial,dtype=float).copy() if initial is not None else center.copy()
    found=False
    for match in re.finditer(r'(?<![A-Za-z])([xyz])\s*(?:=|为|在)\s*(-?\d+(?:\.\d+)?)\s*(mm|毫米|cm|厘米|m|米)',text,re.I):
        axis='xyz'.index(match.group(1).lower());value=float(match.group(2));unit=match.group(3).lower()
        position[axis]=value*(.001 if unit in ('mm','毫米') else .01 if unit in ('cm','厘米') else 1);found=True
    if found:return position.tolist()
    if re.search(r'中心|中部|中间|正中|中央',text):
        if re.search(r'之间|两(?:个|处|侧|边)', text):
            raise ValueError('关系位置需要明确参照对象，不能直接使用整个模型中心。')
        selector = _selection_from_text(text)
        if selector and (selector in ('top','bottom','left','right','front','back') or selector.count(':') == 2):
            import trimesh
            selected = _surface_selections(model_id, [selector])[selector]['faces']
            if not selected:
                raise ValueError('指定表面没有可用于定位点热源的三角面。')
            display = read_json(MODELS/model_id/'display.json')
            surface = trimesh.Trimesh(vertices=np.asarray(display['points']).reshape(-1,3),
                faces=np.asarray(display['faces']).reshape(-1,3)[selected], process=False)
            point, _, _ = trimesh.proximity.closest_point_naive(surface, [surface.centroid])
            return point[0].tolist()
        return center.tolist()
    offset = re.search(r'(?:向|往)(左|右|上|下|前|后)(?:侧|边|方)?\s*(?:移动|平移|挪动|挪|移)\s*(-?\d+(?:\.\d+)?)\s*(mm|毫米|cm|厘米|m|米)', text, re.I)
    if offset:
        if initial is None:
            raise ValueError('相对移动需要已有点热源的坐标，请先指定位置。')
        direction, value, unit = offset.groups()
        axis, sign = {'左':(0,-1),'右':(0,1),'前':(1,-1),'后':(1,1),'下':(2,-1),'上':(2,1)}[direction]
        position[axis] += sign*float(value)*(.001 if unit.lower() in ('mm','毫米') else .01 if unit.lower() in ('cm','厘米') else 1)
        return position.tolist()
    if faces:
        centers,_=_display_surface(model_id)
        return centers[np.asarray(faces,dtype=int)].mean(axis=0).tolist()
    return None


def _point_in_solid(model_id, position):
    """Check a point against the closed display solid; None means unverified."""
    import trimesh
    display = read_json(MODELS / model_id / 'display.json')
    mesh = trimesh.Trimesh(vertices=np.asarray(display['points']).reshape(-1, 3),
                           faces=np.asarray(display['faces']).reshape(-1, 3), process=False)
    if not mesh.is_watertight:
        return None
    if bool(mesh.contains([position])[0]):
        return True
    # Boundary points are usable too; ray containment alone excludes them.
    _, distances, _ = trimesh.proximity.closest_point_naive(mesh, [position])
    return bool(distances[0] <= max(float(mesh.extents.max()) * 1e-8, 1e-10))


def make_plan(model_id, prompt, current):
    from thermal_legacy.agent_rules import make_plan as extract_rules
    import sys
    return extract_rules(sys.modules[__name__], model_id, prompt, current)


def plan_request(model_id, prompt, current, conversation=None):
    if not prompt or not prompt.strip():
        return dict(ok=False, config=current, changes=[], warnings=[], questions=['请描述材料、热源功率、时长和受热面。'])
    from thermal_legacy.agent_conversation import rule_candidate
    import sys
    result = rule_candidate(sys.modules[__name__], model_id, prompt.strip(), current, conversation)
    if conversation and conversation.get('history'):
        if not result['ok'] or result['config'] == current:
            result['ok'] = False
            result['questions'].append('本地规则无法确定这次补充是否完整解决了前文要求；请明确参数及作用对象，或切换 Codex 后重试这条补充。')
        result['changes'] = _codex_changes(result['config'], current) if result['ok'] else []
    if not result['ok']:
        result['error_code'] = 'clarification'
    return result


def _response_text(payload):
    """Extract text from the Responses API's output content blocks."""
    direct = payload.get('output_text')
    if isinstance(direct, str) and direct.strip():
        return direct
    chunks = []
    for item in payload.get('output') or []:
        for content in item.get('content') or []:
            if content.get('type') in ('output_text', 'text') and isinstance(content.get('text'), str):
                chunks.append(content['text'])
    return '\n'.join(chunks).strip()


def _json_from_text(text):
    """Parse JSON while tolerating a fenced block from a model."""
    value = text.strip()
    if value.startswith('```'):
        value = re.sub(r'^```(?:json)?\s*', '', value, flags=re.I)
        value = re.sub(r'\s*```$', '', value)
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        start, end = value.find('{'), value.rfind('}')
        if start >= 0 and end > start:
            return json.loads(value[start:end + 1])
        raise


def _surface_selections(model_id, requested=()):
    """Resolve shared semantic selectors without asking a model to invent IDs."""
    folder = MODELS / model_id
    from thermal_legacy.host_geometry import surface
    points, faces, outer, _ = surface(folder)
    tri = points[faces]
    centers = tri.mean(axis=1)
    cross = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    lengths = np.linalg.norm(cross, axis=1)
    normals = cross / np.maximum(lengths[:, None], 1e-30)
    valid = outer & (lengths > 0)
    selections = {}

    def add(key, name, mask):
        ids = np.flatnonzero(mask).tolist()
        selections[key] = dict(name=name, faces=ids, face_count=len(ids),
                               area_m2=float(lengths[mask].sum() / 2))

    def directions(prefix, label, mask):
        for key, name, axis, sign in (
            ('top', '顶部 (+Z)', 2, 1), ('bottom', '底部 (-Z)', 2, -1),
            ('right', '右侧 (+X)', 0, 1), ('left', '左侧 (-X)', 0, -1),
            ('back', '后侧 (+Y)', 1, 1), ('front', '前侧 (-Y)', 1, -1),
        ):
            selected = np.zeros(len(faces), dtype=bool)
            if np.any(mask):
                values = centers[mask, axis]
                edge = values.max() if sign > 0 else values.min()
                tolerance = max(float(np.ptp(values)) * .03, 1e-10)
                selected = mask & (np.abs(centers[:, axis] - edge) <= tolerance) & (normals[:, axis] * sign > .35)
            add(prefix + key, label + name, selected)

    add('all_outer', '全部外表面', valid)
    directions('', '', valid)
    metadata = read_json(folder / 'metadata.json')
    shell_path = folder / 'shells.npz'
    shell_ids = None
    if shell_path.is_file():
        with np.load(shell_path) as data:
            shell_ids = data['shell_id'].copy()
        if len(shell_ids) != len(faces):
            shell_ids = None
    for record in metadata.get('components', []):
        index = int(record['component_id'])
        if shell_ids is not None:
            mask = valid & (shell_ids == index)
        elif record.get('bounds_m'):
            low, high = np.asarray(record['bounds_m'], dtype=float)
            epsilon = max(float(np.ptp(points, axis=0).max()) * 1e-8, 1e-10)
            mask = valid & np.all((centers >= low-epsilon) & (centers <= high+epsilon), axis=1)
        else:
            continue
        key = f'component:{index+1}'
        add(key, f'组件 {index+1} 外表面', mask)
        directions(key + ':', f'组件 {index+1} ', mask)
    for selector in requested:
        from thermal_legacy.scenario_cases import token_box,clipped_quadrature
        box=token_box(selector or '')
        if box:
            parents,_,weights=clipped_quadrature(tri,box)
            mask=np.zeros(len(faces),bool);mask[np.unique(parents)]=True;mask &= valid
            add(selector,'空间裁剪选区',mask)
            selections[selector].update(surface_box=box,area_m2=float(weights[valid[parents]].sum()))
            continue
        match = re.fullmatch(r'([xyz])=(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)', selector or '')
        if not match:
            continue
        axis = 'xyz'.index(match.group(1))
        value = float(match.group(2))
        tolerance = max(float(np.ptp(centers[valid, axis])) * .03, 1e-10) if np.any(valid) else 1e-10
        add(selector, f'{match.group(1).upper()}={value:g} m 附近外表面',
            valid & (np.abs(centers[:, axis] - value) <= tolerance))
    return selections


def _requested_selections(prompt):
    from thermal_legacy.scenario_cases import BOX,box_from_text,box_token
    tokens = [box_token(box_from_text(m.group())) for m in BOX.finditer(prompt)]
    for match in re.finditer(r'(?<![A-Za-z])[xyz]\s*(?:=|为|在)\s*-?\d+(?:\.\d+)?\s*(?:mm|毫米|cm|厘米|m|米)', prompt, re.I):
        token = _selection_from_text(match.group())
        if token:
            tokens.append(token)
    return tokens


def _validate_geometry(model_id, config):
    metadata = read_json(MODELS / model_id / 'metadata.json')
    for group in [*config.get('heat_sources', []), *config.get('cooling', []), *(config.get('structural') or {}).get('supports',[]), *([config['surface_evaluation']] if config.get('surface_evaluation') else [])]:
        if group.get('faces') and max(group['faces']) >= metadata['triangles']:
            raise ValueError('Agent 返回了当前模型不存在的面编号，请重新选取。')
        if group.get('placement') == 'embedded' and group.get('position_m') and metadata.get('bounds_m'):
            low, high = np.asarray(metadata['bounds_m'])
            position = np.asarray(group['position_m'])
            if np.any(position < low - 1e-9) or np.any(position > high + 1e-9):
                raise ValueError('嵌入热源的位置超出模型范围。')
            if _point_in_solid(model_id, position) is False:
                raise ValueError(f'嵌入热源位置 {position.tolist()} m 位于空腔或实体外部；请指定实体内位置，或明确采用外置热源。')
    ids = {r['component_id'] for r in metadata.get('components', [])}
    if any(r['component_id'] not in ids for r in config.get('component_materials', [])):
        raise ValueError('材料设置引用了当前模型不存在的组件。')


def _codex_context(model_id, prompt, current):
    metadata = read_json(MODELS / model_id / 'metadata.json')
    geometry = {
        'name': metadata.get('name'),
        'kind': metadata.get('kind'),
        'dimensions_m': metadata.get('dimensions_m'),
        'triangles': metadata.get('triangles'),
        'components': metadata.get('components', []),
        'surface_selections': [dict(id=key, **{k: v for k, v in selection.items() if k != 'faces'})
                               for key, selection in _surface_selections(model_id, _requested_selections(prompt)).items()],
        'selection_rule': '轴向选区取最外侧 3% 范围内、法向朝向该方向的外表面三角面；编号由后端映射。',
    }
    schema = Simulation.model_json_schema()
    # The assistant uses semantic selectors; the solver still receives only
    # ordinary Simulation fields with resolved triangle IDs.
    selector = dict(type='string', enum=[x['id'] for x in geometry['surface_selections']])
    for name in ('Heat', 'Cooling', 'StructuralSupport', 'SurfaceEvaluation'):
        schema['$defs'][name]['properties']['surface_selection'] = selector
    schema['$defs']['Cooling']['required'] = [key for key in schema['$defs']['Cooling']['required'] if key != 'faces']
    schema['$defs']['StructuralSupport']['required'] = [key for key in schema['$defs']['StructuralSupport']['required'] if key != 'faces']
    schema['$defs']['SurfaceEvaluation']['required'] = [key for key in schema['$defs']['SurfaceEvaluation']['required'] if key != 'faces']
    schema['properties']['heat_sources']['minItems'] = 0 if (current or {}).get('environment_only') or re.search(r'纯环境|无热源.*(?:环境|高低温)',prompt) else 1
    schema['required'] = list(dict.fromkeys([*schema.get('required', []), 'heat_sources']))
    return schema, geometry


def _codex_prompt(model_id, prompt, current, prepared=None):
    from thermal_legacy import agent_skill
    import sys
    engine = sys.modules[__name__]
    return agent_skill.prompt_text(engine, prepared or agent_skill.prepare(engine, model_id, prompt, current))


def _validated_codex_text(text, current, model_id=None):
    if not text:
        raise ValueError('Codex 未返回文本结果')
    returned = _json_from_text(text)
    if not isinstance(returned, dict):
        raise ValueError('配置必须是 JSON 对象。')
    config = {**copy.deepcopy(current or {}), **returned}
    expected_model = model_id or (current or {}).get('model_id')
    if expected_model and config.get('model_id', expected_model) != expected_model:
        raise ValueError('Agent 返回了其他模型的配置，请为当前模型重新生成。')
    if expected_model:
        config['model_id'] = expected_model
    if not config.get('heat_sources') and not config.get('environment_only'):
        raise ValueError('草案未生成热源。请明确功率与受热面，或指定点热源位置；当前草案不能运行。')
    selections = None
    for group in [*config.get('heat_sources', []), *config.get('cooling', []), *(config.get('structural') or {}).get('supports',[]), *([config['surface_evaluation']] if config.get('surface_evaluation') else [])]:
        selector = group.pop('surface_selection', None)
        if selector is None:
            continue
        if selections is None:
            selections = _surface_selections(config['model_id'], [group.get('surface_selection') for group in [*config.get('heat_sources', []), *config.get('cooling', [])]] + [selector])
        if selector not in selections or not selections[selector]['faces']:
            raise ValueError(f'当前模型没有可用的“{selector}”受热/散热选区，请手动刷选后重新生成。')
        selected = selections[selector]['faces']
        if group.get('faces') and sorted(set(group['faces'])) != selected:
            raise ValueError('Agent 的选区名称与面编号冲突，请重新生成或手动刷选。')
        group['faces'] = selected
        if selections[selector].get('surface_box'):group['surface_box']=selections[selector]['surface_box']
    validated = Simulation.model_validate(config)
    if model_id:
        _validate_geometry(model_id, validated.model_dump(mode='json'))
    return validated.model_dump(mode='json')


def _codex_changes(config, current=None):
    from thermal_legacy.agent_parameters import describe_changes
    return describe_changes(config, current)


def _find_codex_cli():
    configured = os.environ.get('THERMAL_CODEX_COMMAND', '').strip()
    if configured:
        path = Path(configured).expanduser()
        if path.is_file():
            return str(path)
        found = shutil.which(configured)
        if found:
            return found
    for name in ('codex.exe', 'codex'):
        found = shutil.which(name)
        if found:
            return found
    local_app_data = os.environ.get('LOCALAPPDATA', '')
    if local_app_data:
        candidates = sorted(Path(local_app_data).glob('Programs/OpenAI Codex CLI/*/bin/codex.exe'), reverse=True)
        if candidates:
            return str(candidates[0])
    return None


def _cli_response_text(stdout):
    """Extract the final agent message from Codex CLI JSONL events."""
    chunks = []
    for line in (stdout or '').splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        item = event.get('item') if isinstance(event, dict) else None
        if isinstance(event, dict) and event.get('type') == 'item.completed' and isinstance(item, dict) and item.get('type') == 'agent_message' and isinstance(item.get('text'), str):
            chunks.append(item['text'])
    return chunks[-1].strip() if chunks else ''


def _codex_cli_env():
    """Give this child the user's proxy settings without changing the parent.

    The Windows browser uses WinINET proxy settings, while the CLI needs proxy
    environment variables. Explicit proxy environment settings take precedence.
    Read the registry for each request so a running service sees proxy changes.
    """
    env = os.environ.copy()
    explicit = {key.lower() for key in env if key.lower() in
                ('http_proxy', 'https_proxy', 'all_proxy')}
    registry_proxies = getattr(urllib.request, 'getproxies_registry', lambda: {})
    if not explicit:
        for scheme, address in registry_proxies().items():
            if scheme in ('http', 'https') and address:
                env[f'{scheme.upper()}_PROXY'] = address
    exclusions = env.get('no_proxy', env.get('NO_PROXY', '')).split(',')
    exclusions = [entry.strip() for entry in exclusions if entry.strip()]
    for host in ('127.0.0.1', 'localhost', '::1'):
        if host not in exclusions:
            exclusions.append(host)
    env['NO_PROXY'] = env['no_proxy'] = ','.join(exclusions)
    return env


def _codex_cli_timeout():
    raw = os.environ.get('THERMAL_CODEX_TIMEOUT_S', '180')
    try:
        seconds = int(raw)
    except ValueError as error:
        raise ValueError('THERMAL_CODEX_TIMEOUT_S 必须是 15–600 之间的整数秒数。') from error
    if not 15 <= seconds <= 600:
        raise ValueError('THERMAL_CODEX_TIMEOUT_S 必须是 15–600 之间的整数秒数。')
    return seconds


def _cli_diagnostic_text(value):
    # TimeoutExpired may contain bytes even when subprocess uses text=True.
    if isinstance(value, bytes):
        value = value.decode('utf-8', errors='replace')
    text = value or ''
    text = re.sub(r'\x1b\[[0-9;]*m', '', text)
    text = re.sub(r'(?i)Bearer\s+\S+', 'Bearer [redacted]', text)
    return re.sub(r'\bsk-[A-Za-z0-9_-]+', '[redacted]', text)


def _cli_error_detail(stdout, stderr):
    """Prefer structured CLI errors over startup warnings on stderr."""
    stdout = _cli_diagnostic_text(stdout)
    stderr = _cli_diagnostic_text(stderr)
    for line in reversed((stdout or '').splitlines()):
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            if event.get('type') == 'error' and isinstance(event.get('message'), str):
                return event['message']
            error = event.get('error')
            if isinstance(error, dict) and isinstance(error.get('message'), str):
                return error['message']
    for line in reversed(stderr.splitlines()):
        if any(term in line.lower() for term in ('request timed out', 'stream disconnected', 'connection refused', 'error:')):
            return line.strip()[-500:]
    return (stderr or stdout or '').strip()[-800:]


def _codex_cli_plan_request(model_id, prompt, current, executable, conversation=None):
    from thermal_legacy import agent_skill
    import sys
    started = time.perf_counter()
    try:
        prepared = agent_skill.prepare(sys.modules[__name__], model_id, prompt, current, conversation)
        cli_prompt = _codex_prompt(model_id, prompt, current, prepared)
    except (OSError, ValueError, KeyError) as error:
        return dict(ok=False, config=current or {}, changes=[], warnings=[],
                    questions=[f'无法读取模型元数据：{error}'], mode='codex', provider='cli')
    args = [executable, 'exec', '--ephemeral', '--skip-git-repo-check', '--sandbox', 'read-only', '--json', '--color', 'never',
            '--output-schema', str(agent_skill.skill_root(sys.modules[__name__]) / 'references' / 'review-schema.json'), '-']
    # All deterministic work is already done by the host. These child-only
    # overrides avoid shell/MCP detours for a pure structured-text review.
    for feature in ('shell_tool', 'unified_exec', 'apps', 'multi_agent', 'browser_use', 'computer_use'):
        args[2:2] = ['--disable', feature]
    args[2:2] = ['-c', 'mcp_servers.node_repl.enabled=false', '-c', 'web_search="disabled"']
    # Let the official CLI use the model/profile selected in ~/.codex unless
    # the service explicitly overrides it for this bridge.
    model = os.environ.get('THERMAL_CODEX_MODEL', '').strip()
    if model:
        args[2:2] = ['--model', model]
    try:
        timeout = _codex_cli_timeout()
    except ValueError as error:
        return dict(ok=False, config=current or {}, changes=[], warnings=[],
                    questions=[str(error)], mode='codex', provider='cli', error_code='configuration')
    try:
        completed = subprocess.run(
            args, input=cli_prompt, text=True, encoding='utf-8', capture_output=True,
            cwd=str(ROOT), timeout=timeout, env=_codex_cli_env(),
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
        )
    except subprocess.TimeoutExpired as error:
        detail = _cli_error_detail(error.stdout, error.stderr)
        network_timeout = any(term in detail.lower() for term in ('request timed out', 'stream disconnected'))
        message = (f'Codex 上游连接超时，重试后仍未在 {timeout} 秒内完成。请检查网络或系统代理。'
                   if network_timeout else f'Codex 配置生成超过 {timeout} 秒，尚未收到完整结果。请稍后重试。')
        return dict(ok=False, config=current or {}, changes=[], warnings=[],
                    questions=[message], mode='codex', provider='cli', error_code='timeout', diagnostic=detail)
    except OSError as error:
        return dict(ok=False, config=current or {}, changes=[], warnings=[],
                    questions=[f'无法启动本机 Codex CLI：{error}'], mode='codex', provider='cli', error_code='startup')
    if completed.returncode != 0:
        detail = _cli_error_detail(completed.stdout, completed.stderr)
        return dict(ok=False, config=current or {}, changes=[], warnings=[],
                    questions=[f'本机 Codex CLI 请求失败（退出码 {completed.returncode}）：{detail}'],
                    mode='codex', provider='cli', error_code='cli_failed')
    try:
        config, questions = agent_skill.compile_review(sys.modules[__name__], prepared, _cli_response_text(completed.stdout))
        if questions:
            return dict(ok=False, config=current or {}, changes=[], warnings=[], questions=questions,
                        mode='codex', provider='cli', workflow='thermal-config', error_code='clarification')
    except Exception as error:
        from thermal_legacy.agent_parameters import validation_message
        return dict(ok=False, config=current or {}, changes=[], warnings=[],
                    questions=[f'Codex 返回的配置无法通过 Simulation 校验：{validation_message(error)}'],
                    mode='codex', provider='cli', error_code='invalid_config')
    metrics = dict(elapsed_s=round(time.perf_counter() - started, 3), input_chars=len(cli_prompt),
                   output_chars=len(_cli_response_text(completed.stdout)), tool_calls=0)
    for line in completed.stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict) and event.get('type') == 'turn.completed':
            metrics['usage'] = event.get('usage', {})
        if isinstance(event, dict) and event.get('type') == 'item.completed':
            item_type = event.get('item', {}).get('type')
            if item_type not in ('agent_message', 'reasoning', 'error'):
                metrics['tool_calls'] += 1
    return dict(ok=True, config=config, changes=_codex_changes(config, prepared['original']),
                warnings=prepared['warnings'], questions=[], mode='codex', provider='cli', model=model or None,
                workflow='thermal-config', metrics=metrics)


def _codex_api_plan_request(model_id, prompt, current, conversation=None):
    api_key = os.environ.get('OPENAI_API_KEY', '').strip()
    if not api_key:
        return dict(ok=False, config=current or {}, changes=[], warnings=[],
                    questions=['Codex API 模式需要设置 OPENAI_API_KEY；当前未配置。'],
                    mode='codex', provider='api')
    model = os.environ.get('OPENAI_MODEL', 'gpt-5.2').strip() or 'gpt-5.2'
    base_url = os.environ.get('OPENAI_BASE_URL', 'https://api.openai.com/v1').strip().rstrip('/')
    try:
        schema, geometry = _codex_context(model_id, prompt, current)
    except (FileNotFoundError, json.JSONDecodeError) as error:
        return dict(ok=False, config=current or {}, changes=[], warnings=[],
                    questions=[f'无法读取模型元数据：{error}'], mode='codex', provider='api')
    instructions = (
        '你是 Thermal Studio 的仿真配置助手。根据用户描述生成完整、可执行的 Simulation JSON 配置。'
        '只输出一个 JSON 对象，不要 Markdown、解释或额外字段。必须符合给定 JSON Schema；'
        '保留当前配置中未被用户修改的字段与 model_id。通常须有有效热源；明确的纯环境温变工况可设置environment_only=true并使用空heat_sources。'
        '新增或修改受热面时使用 geometry.surface_selections 中非空选区的 id，写入 surface_selection 字段，'
        '例如顶部热源使用 surface_selection="top"，由本地后端生成 faces；不要猜测面编号。'
        '热源与散热面分别处理，全部外表面对流使用 default_h 与 heat_convection。用户确认由网页处理，只返回配置。'
    )
    user_input = {
        'model_id': model_id,
        'geometry': geometry,
        'current_config': current or {},
        'request': prompt.strip(),
        'simulation_schema': schema,
    }
    prepared = None
    if conversation is not None:
        from thermal_legacy import agent_skill
        import sys
        try:
            prepared = agent_skill.prepare(sys.modules[__name__], model_id, prompt, current, conversation)
        except (OSError, ValueError, KeyError) as error:
            return dict(ok=False, config=current or {}, changes=[], warnings=[], questions=[str(error)],
                        mode='codex', provider='api', error_code='invalid_config')
        instructions = agent_skill.review_instructions(sys.modules[__name__])
        user_input = prepared['public']
        schema = json.loads((agent_skill.skill_root(sys.modules[__name__]) / 'references' / 'review-schema.json').read_text(encoding='utf-8'))
    body = json.dumps({
        'model': model,
        'input': [
            {'role': 'system', 'content': instructions},
            {'role': 'user', 'content': json.dumps(user_input, ensure_ascii=False)},
        ],
        'text': {
            'format': {
                'type': 'json_schema',
                'name': 'thermal_config_review' if prepared is not None else 'simulation_config',
                # Pydantic defaults are optional in its generated schema;
                # non-strict structured output keeps those defaults valid,
                # while Simulation.model_validate below remains authoritative.
                'strict': prepared is not None,
                'schema': schema,
            }
        },
    }, ensure_ascii=False).encode('utf-8')
    request = urllib.request.Request(
        f'{base_url}/responses', data=body,
        headers={'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'},
        method='POST',
    )
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            payload = json.loads(response.read().decode('utf-8'))
    except urllib.error.HTTPError as error:
        detail = error.read().decode('utf-8', errors='replace')[:500]
        return dict(ok=False, config=current or {}, changes=[], warnings=[],
                    questions=[f'Codex 请求失败（HTTP {error.code}）：{detail}'], mode='codex', provider='api')
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        return dict(ok=False, config=current or {}, changes=[], warnings=[],
                    questions=[f'无法连接 Codex API：{error}'], mode='codex', provider='api')
    try:
        text = _response_text(payload)
        if not text:
            raise ValueError('Responses API 未返回文本结果')
        if prepared is not None:
            config, questions = agent_skill.compile_review(sys.modules[__name__], prepared, text)
            if questions:
                return dict(ok=False, config=current or {}, changes=[], warnings=[], questions=questions,
                            mode='codex', provider='api', workflow='thermal-config', error_code='clarification')
        else:
            config = _validated_codex_text(text, current, model_id)
    except Exception as error:
        return dict(ok=False, config=current or {}, changes=[], warnings=[],
                    questions=[f'Codex 返回的配置无法通过 Simulation 校验：{str(error).split(chr(10))[0]}'],
                    mode='codex', provider='api', error_code='invalid_config')
    return dict(ok=True, config=config, changes=_codex_changes(config, current),
                warnings=prepared['warnings'] if prepared else [], questions=[], mode='codex', provider='api', model=model,
                workflow='thermal-config' if prepared else 'legacy')


def codex_plan_request(model_id, prompt, current, conversation=None):
    """Generate a validated Simulation config using the local Codex client by default.

    Set THERMAL_CODEX_PROVIDER=api to use the legacy Responses API. ``auto``
    uses the local client when available and otherwise requires an API key.
    """
    provider = os.environ.get('THERMAL_CODEX_PROVIDER', 'cli').strip().lower() or 'cli'
    if provider not in ('cli', 'api', 'auto'):
        return dict(ok=False, config=current or {}, changes=[], warnings=[],
                    questions=['THERMAL_CODEX_PROVIDER 必须是 cli、api 或 auto。'], mode='codex')
    if provider in ('cli', 'auto'):
        executable = _find_codex_cli()
        if executable:
            return _codex_cli_plan_request(model_id, prompt, current, executable, conversation)
        if provider == 'cli':
            return dict(ok=False, config=current or {}, changes=[], warnings=[],
                        questions=['未找到本机 Codex CLI（codex.exe）。请安装官方 Codex CLI，或设置 THERMAL_CODEX_PROVIDER=api。'],
                        mode='codex', provider='cli')
    return _codex_api_plan_request(model_id, prompt, current, conversation)
