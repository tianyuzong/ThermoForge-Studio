from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator,field_validator
import math

class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)

class Material(Strict):
    name: str = '自定义材料'
    young_modulus_Pa: float = Field(gt=0)
    poisson_ratio: float = Field(ge=0, lt=0.49)
    density_kg_m3: float = Field(gt=0)
    yield_strength_Pa: float | None = Field(default=None, gt=0)
    source: str = '用户输入'

DEMO_MATERIAL = Material(name='演示钢材（假设值）', young_modulus_Pa=210e9,
    poisson_ratio=0.3, density_kg_m3=7850, yield_strength_Pa=235e6,
    source='内置教学/软件验证假设；不代表特定牌号或认证材料数据').model_dump()
DEMO_ALUMINUM = Material(name='演示铝材（假设值）',young_modulus_Pa=70e9,poisson_ratio=.33,
    density_kg_m3=2700,yield_strength_Pa=150e6,
    source='内置教学/软件验证假设；不代表特定牌号或认证材料数据').model_dump()

class Selection(Strict):
    body_id: int | None = None
    kind: Literal['named', 'faces', 'direction', 'holes', 'box'] = 'named'
    name: str | None = None
    face_ids: list[int] = Field(default_factory=list)
    direction: Literal['+x', '-x', '+y', '-y', '+z', '-z'] | None = None
    bounds_m: list[list[float]] | None = None

class Support(Strict):
    name: str = '约束'
    selection: Selection
    components: list[Literal['x', 'y', 'z']] = Field(default_factory=lambda: ['x','y','z'], min_length=1)
    displacement_m: list[float] = Field(default_factory=lambda: [0.,0.,0.], min_length=3, max_length=3)

class Load(Strict):
    body_id: int | None = None
    name: str = '载荷'
    kind: Literal['force', 'pressure', 'gravity']
    selection: Selection | None = None
    vector: list[float] = Field(default_factory=lambda: [0.,0.,0.], min_length=3, max_length=3)
    pressure_Pa: float = 0

class MeshSpec(Strict):
    size_m: float | None = Field(default=None, gt=0)
    local_selections: list[Selection] = Field(default_factory=list)
    local_size_m: float | None = Field(default=None, gt=0)
    convergence: bool = False
    evaluation_selection: Selection | None = None

class StressRegion(Strict):
    name: str = Field(min_length=1, max_length=100)
    selection: Selection
    measure: Literal['principal_max', 'normal'] = 'principal_max'
    direction: list[float] | None = Field(default=None, min_length=3, max_length=3)

    @model_validator(mode='after')
    def check_definition(self):
        if self.selection.body_id is None or self.selection.body_id < 1:
            raise ValueError('局部应力评估必须指定一个组件')
        if self.measure == 'normal':
            if self.direction is None or not math.isfinite(math.hypot(*self.direction)) or math.hypot(*self.direction) < 1e-12:
                raise ValueError('正应力评估必须指定非零全局方向')
        elif self.direction is not None:
            raise ValueError('最大主应力不使用指定方向')
        return self

class ComponentMaterialAssignment(Strict):
    body_id: int = Field(gt=0)
    material: Material

class GearHub(Strict):
    body_id: int = Field(gt=0)
    bore: Selection
    center_m: list[float] = Field(min_length=3,max_length=3)
    rotation_locked: bool = False
    torque_Nm: float = 0

class GearContactPair(Strict):
    slave_body: int = Field(gt=0)
    master_body: int = Field(gt=0)

class GearContact(Strict):
    hubs: list[GearHub] = Field(min_length=2,max_length=12)
    pairs: list[GearContactPair] = Field(min_length=1,max_length=12)
    search_distance_m: float = Field(default=.00015,gt=0,le=.002)
    max_contact_points: int = Field(default=1800,ge=10,le=4000)
    assumptions: str = Field(min_length=1)

    @model_validator(mode='after')
    def check_graph(self):
        ids=[h.body_id for h in self.hubs]
        if len(set(ids))!=len(ids):raise ValueError('每个齿轮只允许一个刚性轴孔')
        if not any(h.rotation_locked for h in self.hubs):raise ValueError('准静态齿轮必须指定锁止输出轴')
        if not any(not h.rotation_locked and h.torque_Nm for h in self.hubs):raise ValueError('请给自由旋转输入轴指定非零扭矩')
        if any(h.rotation_locked and h.torque_Nm for h in self.hubs):raise ValueError('锁止轴的阻力矩由求解给出，不能同时输入指定扭矩')
        seen=set();connected={h.body_id for h in self.hubs if h.rotation_locked}
        for p in self.pairs:
            key=tuple(sorted((p.slave_body,p.master_body)))
            if p.slave_body==p.master_body or not set(key)<=set(ids) or key in seen:raise ValueError('接触对重复或组件编号无效')
            seen.add(key)
        for _ in ids:
            for a,b in seen:
                if a in connected or b in connected:connected.update((a,b))
        if connected!=set(ids):raise ValueError('所有齿轮必须通过接触对连接到输出轴')
        return self

class ResultQuery(Strict):
    name: str = Field(default='结果查询', min_length=1, max_length=100)
    kind: Literal['statistics', 'probe', 'path', 'relative', 'reactions', 'stiffness'] = 'statistics'
    field: str = Field(default='stress', min_length=1, max_length=80)
    body_id: int | None = Field(default=None, gt=0)
    selection: Selection | None = None
    node_id: int | None = Field(default=None, gt=0)
    point_m: list[float] | None = Field(default=None, min_length=3, max_length=3)
    end_m: list[float] | None = Field(default=None, min_length=3, max_length=3)
    samples: int = Field(default=51, ge=2, le=501)
    direction: list[float] | None = Field(default=None, min_length=3, max_length=3)
    load_name: str | None = None
    moment_origin_m: list[float] | None = Field(default=None, min_length=3, max_length=3)

    @model_validator(mode='after')
    def valid_query(self):
        if self.kind in ('probe','stiffness') and (self.node_id is None) == (self.point_m is None):
            raise ValueError('点查询须指定节点编号或坐标，且只能指定一种')
        if self.kind in ('path','relative') and (self.point_m is None or self.end_m is None):
            raise ValueError('路径或相对位移须指定两个端点')
        if self.kind == 'path' and math.dist(self.point_m,self.end_m) < 1e-15:
            raise ValueError('路径长度不能为零')
        if self.direction is not None and math.hypot(*self.direction) < 1e-12:
            raise ValueError('方向向量不能为零')
        if self.kind == 'stiffness' and (not self.load_name or self.direction is None):
            raise ValueError('等效刚度须指定载荷名称和测量方向')
        return self

class BondedConnection(Strict):
    name: str = Field(min_length=1, max_length=120)
    slave: Selection
    master: Selection
    max_gap_m: float = Field(default=1e-5, gt=0, le=.002)
    assumptions: str = Field(min_length=1, max_length=2000)

    @model_validator(mode='after')
    def separate_bodies(self):
        if self.slave.body_id is None or self.master.body_id is None or self.slave.body_id==self.master.body_id:
            raise ValueError('粘结连接必须指定两个不同组件的真实选区')
        return self

class LoadCase(Strict):
    schema_version: Literal[1, 2] = 2
    name: str = Field(default='静力分析', min_length=1, max_length=160)
    material: Material
    component_materials: dict[str, Material] = Field(default_factory=dict)
    assembly_mode: Literal['unconfigured', 'independent','gear_contact','bonded'] = 'unconfigured'
    bonded_connections: list[BondedConnection] = Field(default_factory=list, max_length=500)
    gear_contact: GearContact | None = None
    supports: list[Support] = Field(default_factory=list)
    loads: list[Load] = Field(default_factory=list)
    mesh: MeshSpec = Field(default_factory=MeshSpec)
    stress_regions: list[StressRegion] = Field(default_factory=list, max_length=12)
    target_safety_factor: float = Field(default=1.5, ge=1)
    displacement_limit_m: float | None = Field(default=None, gt=0)
    strength_criterion: Literal['von_mises','tresca'] = 'von_mises'
    moment_origin_m: list[float] | None = Field(default=None, min_length=3, max_length=3)
    result_requests: list[ResultQuery] = Field(default_factory=list, max_length=24)

    @field_validator('component_materials',mode='before')
    @classmethod
    def component_assignment_list(cls,value):
        # The Agent uses a typed array because strict structured output cannot
        # generate arbitrary dictionary keys. The persisted UI/API form is a map.
        if isinstance(value,list):
            assignments=[ComponentMaterialAssignment.model_validate(a) for a in value]
            if len({a.body_id for a in assignments})!=len(assignments):raise ValueError('同一组件不能重复指定材料')
            return {str(a.body_id):a.material for a in assignments}
        return value

    @model_validator(mode='after')
    def check_excitation(self):
        if self.assembly_mode=='bonded' and not self.bonded_connections:
            raise ValueError('整体粘结模式须定义连接选区与间隙容限')
        if self.assembly_mode!='bonded' and self.bonded_connections:
            raise ValueError('粘结连接与装配计算方式不一致')
        if self.assembly_mode=='gear_contact':
            if self.gear_contact is None:raise ValueError('齿轮接触工况需要轴孔、扭矩与接触对')
            if self.supports or self.loads:raise ValueError('当前齿轮接触模式只支持轴孔约束与轴端扭矩，不能叠加普通支撑或面载荷')
            return self
        if self.gear_contact is not None:raise ValueError('齿轮接触配置与计算方式不一致')
        if not self.supports:raise ValueError('请提供真实支撑')
        nonzero = any(abs(s.displacement_m['xyz'.index(a)]) > 0 for s in self.supports for a in s.components)
        for load in self.loads:
            if load.kind != 'gravity' and load.selection is None:
                raise ValueError('面载荷必须指定作用面')
            if load.kind != 'gravity' and load.body_id is not None and load.selection.body_id!=load.body_id:
                raise ValueError('面载荷的组件必须与作用选区组件一致')
            nonzero |= abs(load.pressure_Pa) > 0 if load.kind == 'pressure' else any(abs(x)>0 for x in load.vector)
        if not nonzero:
            raise ValueError('请提供非零载荷或给定位移')
        return self

class ForceUpdate(Strict):
    load_name: str = Field(min_length=1, max_length=160)
    vector: list[float] = Field(min_length=3, max_length=3)

class CaseVariant(Strict):
    """Only explicit changes; all physical conditions come from a saved case."""
    base: Literal['current_case'] = 'current_case'
    name: str = Field(min_length=1, max_length=160)
    force_updates: list[ForceUpdate] = Field(default_factory=list, max_length=24)
    mesh: MeshSpec | None = None
    result_requests: list[ResultQuery] | None = Field(default=None, max_length=24)
    stress_regions: list[StressRegion] | None = Field(default=None, max_length=12)
    moment_origin_m: list[float] | None = Field(default=None, min_length=3, max_length=3)

class AgentDecision(Strict):
    action: Literal['plan', 'clarify', 'cancel', 'explain']
    message: str
    questions: list[str]
    cases: list[LoadCase] = Field(max_length=12)
    case_variants: list[CaseVariant] = Field(default_factory=list, max_length=12)
    compare_with_previous: bool
    candidate_selections: list[Selection] = Field(default_factory=list, max_length=12)
    compute_backend: Literal['calculix_cpu','cudss_cuda'] | None = None
    result_requests: list[ResultQuery] = Field(default_factory=list, max_length=24)

    @model_validator(mode='after')
    def exclusive_case_format(self):
        if self.case_variants and (self.cases or self.action != 'plan'):
            raise ValueError('工况增量仅可用于 plan，且不能与完整 cases 混用')
        return self

class ReviewQuery(Strict):
    job_id: str
    query: ResultQuery

class Observation(Strict):
    fact_id: str
    comment: str = Field(max_length=600)

class ReviewDecision(Strict):
    action: Literal['query','finish']
    message: str = Field(max_length=1600)
    queries: list[ReviewQuery] = Field(default_factory=list, max_length=8)
    observations: list[Observation] = Field(default_factory=list, max_length=16)

class TaskRequest(Strict):
    project_id: str
    prompt: str = Field(min_length=1, max_length=24000)
    execution_mode: Literal['auto','draft'] = 'auto'
    provider: Literal['cli','api','local'] = 'cli'
    request_id: str = Field(min_length=1, max_length=100)
    compute_backend: Literal['calculix_cpu','cudss_cuda'] | None = None

class MessageRequest(Strict):
    prompt: str = Field(min_length=1, max_length=24000)
    request_id: str = Field(min_length=1, max_length=100)

class ProjectRequest(Strict):
    name: str = Field(min_length=1, max_length=100)

class ProjectPatch(Strict):
    name: str | None = None
    material: Material | None = None
    compute_backend: Literal['calculix_cpu','calculix_cuda','cudss_cuda'] | None = None
    component_materials: dict[str, Material] | None = None

class MaterialCardRequest(Strict):
    material: Material

class SavedCaseRequest(Strict):
    case: LoadCase

class NamedSelection(Strict):
    name: str = Field(min_length=1, max_length=100)
    face_ids: list[int] = Field(min_length=1)
