"""An Agent creates case prompts; the existing configuration Agent sets physics."""
import json, os, subprocess, tempfile, urllib.request
from pathlib import Path
from pydantic import Field, model_validator
from thermal_legacy.schemas import Strict
from thermal_legacy import agent
from thermal_legacy.host_geometry import capabilities,model_evidence


class Case(Strict):
    key: str = Field(pattern=r'^[a-z][a-z0-9_]{0,39}$')
    name: str = Field(min_length=1,max_length=160)
    prompt: str = Field(min_length=1,max_length=12000)
    inherit_from: str | None
    initial_from: str | None


class Blueprint(Strict):
    name: str = Field(min_length=1,max_length=160)
    cases: list[Case] = Field(min_length=1,max_length=12)
    questions: list[str]
    limitations: list[str]

    @model_validator(mode='after')
    def dependencies(self):
        for case in self.cases:
            if case.key=='current':raise ValueError('current保留用于表示当前配置，不能作为工况标识')
            if case.inherit_from=='current':case.inherit_from=None
        keys=[c.key for c in self.cases]
        if len(keys)!=len(set(keys)):raise ValueError('工况标识重复')
        for case in self.cases:
            for ref in (case.inherit_from,case.initial_from):
                if ref is not None and ref not in keys:raise ValueError('未知的依赖工况：'+ref)
        ordered=[];seen=set();remaining=list(self.cases)
        while remaining:
            ready=[c for c in remaining if all(r is None or r in seen for r in (c.inherit_from,c.initial_from))]
            if not ready:raise ValueError('工况之间存在循环依赖')
            for c in ready:ordered.append(c);seen.add(c.key);remaining.remove(c)
        self.cases=ordered
        return self


class WorkflowRequest(Strict):
    model_id: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,60}$')
    prompt: str = Field(min_length=1,max_length=24000)
    config: dict = Field(default_factory=dict)


def structured_request(prompt,schema):
    """Reuse configured CLI/API transport; no model tools or shell access."""
    provider=os.environ.get('THERMAL_CODEX_PROVIDER','cli').strip().lower()
    if provider not in ('cli','api','auto'):raise ValueError('无效的Codex连接方式')
    executable=agent._find_codex_cli() if provider!='api' else None
    if executable:
        with tempfile.TemporaryDirectory(prefix='thermal-plan-') as temp:
            target=Path(temp)/'schema.json';target.write_text(json.dumps(schema),encoding='utf-8')
            args=[executable,'exec','--ephemeral','--skip-git-repo-check','--sandbox','read-only','--json','--color','never','--output-schema',str(target),'-']
            for feature in ('shell_tool','unified_exec','apps','multi_agent','browser_use','computer_use'):
                args[2:2]=['--disable',feature]
            args[2:2]=['-c','mcp_servers.node_repl.enabled=false','-c','web_search="disabled"']
            if os.environ.get('THERMAL_CODEX_MODEL'):args[2:2]=['--model',os.environ['THERMAL_CODEX_MODEL']]
            try:
                response=subprocess.run(args,input=prompt,text=True,encoding='utf-8',capture_output=True,cwd=agent.ROOT,
                    timeout=agent._codex_cli_timeout(),env=agent._codex_cli_env(),creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
            except subprocess.TimeoutExpired as error:raise ValueError('多工况计划生成超时，可保留原要求后重试') from error
            if response.returncode:raise ValueError(agent._cli_error_detail(response.stdout,response.stderr))
            return agent._json_from_text(agent._cli_response_text(response.stdout))
    if provider=='cli':raise ValueError('未找到已配置的Codex CLI')
    key=os.environ.get('OPENAI_API_KEY','').strip()
    if not key:raise ValueError('未配置Codex API连接')
    body=dict(model=os.environ.get('OPENAI_MODEL','gpt-5.2'),input=prompt,
              text=dict(format=dict(type='json_schema',name='thermal_workflow',schema=schema,strict=False)))
    req=urllib.request.Request(os.environ.get('OPENAI_BASE_URL','https://api.openai.com/v1').rstrip('/')+'/responses',
        data=json.dumps(body,ensure_ascii=False).encode(),headers={'Authorization':'Bearer '+key,'Content-Type':'application/json'},method='POST')
    try:
        with urllib.request.urlopen(req,timeout=agent._codex_cli_timeout()) as r:return agent._json_from_text(agent._response_text(json.load(r)))
    except Exception as error:raise ValueError('多工况计划API请求失败，请检查连接') from error


def plan(request,trace_folder=None):
    context=dict(request=request.prompt,current=request.config,
                 geometry=model_evidence(agent.MODELS/request.model_id),capabilities=capabilities())
    # Hide the very large display-face arrays; physics stays with config Agent.
    def compact(value):
        if isinstance(value,dict):return {k:(f'{len(v)}个已选面' if k=='faces' else compact(v)) for k,v in value.items()}
        if isinstance(value,list):return [compact(v) for v in value]
        return value
    instructions='''你是Thermal Studio多工况计划Agent。只输出符合Schema的JSON，不调用工具。
把用户完整需求拆成有顺序依赖的独立算例，每项提供给配置Agent的自然语言prompt。物理参数只能由后续配置Agent生成和校验。
inherit_from表示继承前面工况配置；initial_from表示继承前面已完成算例的完整末帧温度场，两者不同。无依赖写null。
严格保留功率、单位、精确选区、材料、边界、网格、时长、步长与保存间隔。比较8/6mm或2/5秒步长必须分别建算例。
初始工况可以使用current；后续独立环境循环要明确清除续算和热源，并设置初温和曲线。正常稳态热源应在稳态求解标签时刻仍有效。
坐标区域写成“选区盒(xmin,xmax,ymin,ymax,zmin,zmax)mm”，由几何工具求交，不要写面编号。
每项prompt只包含该工况参数；共享参数通过inherit_from继承。不得把全部工况原文塞进单项prompt。
不要将设计限值设成定温边界或温控。没有参数依据不得假定风冷系数、材料强度或螺栓约束。
能计算的内容全部安排；工具不支持的CFD、塑性、疲劳寿命或机械接触写入limitations，不阻止其他独立工况。
峰值和同位置应力应变范围在已保存帧统计；环境曲线全部转折点由宿主自动保存，报告自动导出，不需要虚构输出字段。
geometry是当前模型的实际证据。未验证另一源文件等价时不得声称等价；若用户明确允许当前模型则使用它。
questions只用于尚缺的必需信息，已给参数和宿主提供的证据不重复追问。不要在生成计划阶段声称完成了计算。
'''
    text=instructions+json.dumps(compact(context),ensure_ascii=False)
    schema=Blueprint.model_json_schema()
    raw=structured_request(text,schema)
    if trace_folder:(trace_folder/'blueprint-raw.json').write_text(json.dumps(raw,ensure_ascii=False,indent=2),encoding='utf-8')
    try:return Blueprint.model_validate(raw)
    except ValueError as error:
        # Repair malformed dependency/schema output, not missing engineering input.
        repaired=structured_request(text+'\n上次计划未通过宿主格式/依赖校验：'+str(error)+'\n请只修正格式或依赖标识，保留物理需求。上次输出：'+json.dumps(raw,ensure_ascii=False),schema)
        if trace_folder:(trace_folder/'blueprint-repaired.json').write_text(json.dumps(repaired,ensure_ascii=False,indent=2),encoding='utf-8')
        return Blueprint.model_validate(repaired)
