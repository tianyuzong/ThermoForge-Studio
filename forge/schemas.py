"""Shared, SI-valued multiphysics contracts. Temperature inputs are Celsius."""
from typing import Literal
import math
from pydantic import Field, model_validator
from strength.schemas import Strict, Selection, Support, Load, MeshSpec, BondedConnection


class CurvePoint(Strict):
    x: float
    y: float


class SNCurve(Strict):
    points: list[CurvePoint] = Field(min_length=2)
    endurance_limit_Pa: float | None = Field(default=None, gt=0)
    reference_R: float = -1
    life_definition: Literal['initiation', 'failure'] = 'initiation'
    source: str = Field(min_length=1)
    demonstration: bool = False

    @model_validator(mode='after')
    def ordered(self):
        if any(p.x <= 0 or p.y <= 0 for p in self.points):
            raise ValueError('S-N 曲线循环数和应力必须为正')
        if any(a.x >= b.x or a.y <= b.y for a,b in zip(self.points,self.points[1:])):
            raise ValueError('S-N 曲线循环数须递增、应力须递减')
        if self.endurance_limit_Pa and self.endurance_limit_Pa > self.points[-1].y:
            raise ValueError('疲劳极限不能高于曲线末端应力')
        return self


class FindleyMaterial(Strict):
    k: float = Field(ge=0, le=2)
    curve: SNCurve
    calibration: Literal['bulk', 'rolling_contact'] = 'bulk'


class PhaseChange(Strict):
    solidus_C: float = Field(ge=-273.15)
    interval_K: float = Field(gt=0)
    latent_J_kg: float = Field(gt=0)


class Material(Strict):
    name: str = Field(min_length=1)
    density_kg_m3: float = Field(gt=0)
    young_modulus_Pa: float | None = Field(default=None, gt=0)
    poisson_ratio: float | None = Field(default=None, gt=-1, lt=.499)
    yield_strength_Pa: float | None = Field(default=None, gt=0)
    tensile_strength_Pa: float | None = Field(default=None, gt=0)
    conductivity_W_mK: float | None = Field(default=None, gt=0)
    specific_heat_J_kgK: float | None = Field(default=None, gt=0)
    thermal_expansion_per_K: float | None = None
    sn_curve: SNCurve | None = None
    findley: FindleyMaterial | None = None
    hardness_Pa: float | None = Field(default=None, gt=0)
    wear_coefficient: float | None = Field(default=None, ge=0)
    valid_temperature_C: list[float] | None = Field(default=None,min_length=2,max_length=2)
    phase_change: PhaseChange | None = None
    source: str = Field(min_length=1)

    @model_validator(mode='after')
    def temperature_range(self):
        if self.valid_temperature_C and (self.valid_temperature_C[0]<-273.15 or self.valid_temperature_C[0]>self.valid_temperature_C[1]):
            raise ValueError('材料适用温度区间必须按下限、上限排列，且不低于绝对零度')
        return self


class Thermostat(Strict):
    target_C: float = Field(ge=-273.15)
    hysteresis_K: float = Field(gt=0)
    minimum_W: float = Field(default=0,ge=0)
    maximum_W: float = Field(gt=0)
    sensor: Selection

    @model_validator(mode='after')
    def ordered_power(self):
        if self.minimum_W>self.maximum_W:raise ValueError('温控最小功率不得大于最大功率')
        return self


class ThermalBoundary(Strict):
    name: str = '热边界'
    kind: Literal['temperature','convection','radiation','surface_power','heat_flux','body_power']
    selection: Selection | None = None
    body_id: int | None = Field(default=None,gt=0)
    value: float
    ambient_C: float = Field(default=25,ge=-273.15)
    profile: list[CurvePoint] = Field(default_factory=list)
    ambient_profile: list[CurvePoint] = Field(default_factory=list)
    thermostat: Thermostat | None = None
    start_s: float = Field(default=0,ge=0)
    end_s: float | None = Field(default=None,gt=0)

    @model_validator(mode='after')
    def valid(self):
        if self.kind=='body_power':
            if self.body_id is None:raise ValueError('体热源须指定组件')
        elif self.selection is None:raise ValueError('热边界须指定真实选面')
        if self.kind=='radiation' and not 0 <= self.value <= 1:raise ValueError('发射率须在 0 到 1 之间')
        if self.kind=='convection' and self.value<0:raise ValueError('对流系数不能为负')
        if self.kind=='temperature' and self.value < -273.15:raise ValueError('温度低于绝对零度')
        if any(a.x>=b.x for a,b in zip(self.profile,self.profile[1:])):raise ValueError('时间曲线须严格递增')
        if any(a.x>=b.x for a,b in zip(self.ambient_profile,self.ambient_profile[1:])):raise ValueError('环境温度时间曲线须严格递增')
        for label,curve in [('边界',self.profile),('环境温度',self.ambient_profile)]:
            for i,point in enumerate(curve):
                prefix=f'{self.name}：{label}曲线第 {i+1} 点（t={point.x:g}s）'
                if point.x<0:raise ValueError(prefix+'时间不能为负')
                if label=='环境温度' or self.kind=='temperature':
                    if point.y < -273.15:raise ValueError(prefix+'温度低于绝对零度')
                elif self.kind=='radiation' and not 0<=point.y<=1:raise ValueError(prefix+'发射率须在 0 到 1 之间')
                elif self.kind=='convection' and point.y<0:raise ValueError(prefix+'对流系数不能为负')
        if self.end_s is not None and self.end_s<=self.start_s:raise ValueError('热源结束时刻须晚于开始时刻')
        if self.thermostat and self.kind not in ('body_power','surface_power'):raise ValueError('温控仅用于总功率热源')
        return self


class ConductancePoint(Strict):
    pressure_Pa: float = Field(ge=0)
    conductance_W_m2K: float = Field(ge=0)
    temperature_C: float | None = Field(default=None,ge=-273.15)


class ContactPair(Strict):
    name: str
    slave: Selection
    master: Selection
    friction: float = Field(default=0,ge=0,le=2)
    penalty_Pa_m: float = Field(default=1e15,gt=0)
    tangential_stiffness_Pa_m: float = Field(default=1e14,gt=0)
    search_distance_m: float = Field(default=.001,gt=0)
    initial_offset_m: float = 0
    thermal: bool = False
    conductance: list[ConductancePoint] = Field(default_factory=list)
    gap_conductivity_W_mK: float | None = Field(default=None,gt=0)
    minimum_gap_m: float = Field(default=1e-6,gt=0)
    maximum_gap_m: float = Field(default=.005,gt=0)
    wear: bool = False

    @model_validator(mode='after')
    def valid(self):
        if self.slave.body_id is None or self.master.body_id is None or self.slave.body_id==self.master.body_id:
            raise ValueError('接触对须指定不同组件')
        if self.thermal and (not self.conductance or self.gap_conductivity_W_mK is None):
            raise ValueError('热接触须给定闭合热导和张开间隙导热系数')
        temperatures={p.temperature_C for p in self.conductance}
        if None in temperatures and len(temperatures)>1:raise ValueError('热导表的温度坐标须全部给出或全部省略')
        for temperature in temperatures:
            group=[p for p in self.conductance if p.temperature_C==temperature]
            if any(a.pressure_Pa>=b.pressure_Pa for a,b in zip(group,group[1:])):
                raise ValueError('每个温度下热导表的压力坐标须递增')
        if self.maximum_gap_m<self.minimum_gap_m:raise ValueError('最大间隙必须不小于最小间隙')
        return self


class LoadHistory(Strict):
    times_s: list[float] = Field(min_length=2,max_length=100000)
    factors: dict[str,list[float]]
    repetitions: float = Field(default=1,gt=0)
    cycles_per_history: float = Field(default=1,gt=0,description='本段历史对应的标称循环数；多级载荷谱须明确其循环基数')
    periodic: bool = True
    stabilization_cycles: int = Field(default=0,ge=0,le=10,description='接触预循环次数；只对最后一个完整周期评估寿命')

    @model_validator(mode='after')
    def valid(self):
        if any(len(v)!=len(self.times_s) for v in self.factors.values()):
            raise ValueError('每条载荷历史必须与时间轴等长')
        if any(a>=b for a,b in zip(self.times_s,self.times_s[1:])):raise ValueError('时间轴须严格递增')
        if any(not math.isfinite(v) or v<0 for v in self.times_s):raise ValueError('时间须为有限非负数')
        if any(not math.isfinite(v) for values in self.factors.values() for v in values):raise ValueError('载荷历史必须有限')
        if self.periodic and any(abs(v[0]-v[-1])>1e-10 for v in self.factors.values()):
            raise ValueError('周期载荷首尾必须闭合')
        return self


class Motion(Strict):
    name: str
    body_id: int = Field(gt=0)
    selection: Selection
    center_m: list[float] = Field(min_length=3,max_length=3)
    axis: list[float] = Field(min_length=3,max_length=3)
    angles_rad: list[float] = Field(min_length=2)
    translations_m: list[list[float]] = Field(default_factory=list)
    components: list[Literal['x','y','z']] = Field(default_factory=lambda:['x','y','z'],min_length=1)

    @model_validator(mode='after')
    def valid(self):
        if self.selection.body_id!=self.body_id:raise ValueError('运动选区须属于运动组件')
        if math.hypot(*self.axis)<1e-12:raise ValueError('转轴不能为零')
        if self.translations_m and (len(self.translations_m)!=len(self.angles_rad) or any(len(v)!=3 for v in self.translations_m)):
            raise ValueError('平移与转角历史须等长，每个平移须为三维向量')
        return self


class ThermalSettings(Strict):
    initial_C: float = Field(default=25,ge=-273.15)
    reference_C: float = Field(default=25,ge=-273.15)
    duration_s: float = Field(default=3600,gt=0)
    dt_s: float = Field(default=10,gt=0)
    save_s: float = Field(default=30,gt=0)
    initial_from_study: str | None = Field(default=None,pattern=r'^[a-zA-Z0-9_-]{1,100}$')
    boundaries: list[ThermalBoundary] = Field(default_factory=list)


class Axle(Strict):
    name:str
    body_id:int=Field(gt=0)
    selection:Selection
    center_m:list[float]=Field(min_length=3,max_length=3)
    axis:Literal['x','y','z']='z'
    angles_rad:list[float]|None=None
    torque_Nm:float=0
    @model_validator(mode='after')
    def valid(self):
        if self.selection.body_id!=self.body_id:raise ValueError('刚性轴孔必须属于对应组件')
        if self.angles_rad is not None and self.torque_Nm:raise ValueError('不能同时指定轴转角和轴扭矩')
        return self


class FatigueSettings(Strict):
    method: Literal['sn_goodman','findley'] = 'sn_goodman'
    body_ids: list[int] = Field(default_factory=list)
    target_cycles: float = Field(default=1e6,gt=0)
    plane_count: int = Field(default=162,ge=24,le=4096)


class CouplingSettings(Strict):
    transient: bool = True
    temperature_tolerance_K: float = Field(default=.01,gt=0)
    displacement_relative_tolerance: float = Field(default=1e-4,gt=0)
    heatflow_relative_tolerance: float = Field(default=1e-3,gt=0)
    relaxation: float = Field(default=.5,gt=0,le=1)
    max_iterations: int = Field(default=30,ge=2,le=200)
    max_cutbacks: int = Field(default=6,ge=0,le=12)


class MechanicalLoad(Load):
    kind: Literal['force','pressure','gravity','torque']
    torque_Nm: list[float] = Field(default_factory=lambda:[0.,0.,0.],min_length=3,max_length=3)
    center_m: list[float] | None = Field(default=None,min_length=3,max_length=3)

    @model_validator(mode='after')
    def torque_valid(self):
        if self.kind=='torque' and (self.selection is None or self.center_m is None):raise ValueError('扭矩必须给出作用选面和轴心')
        return self


class MeshRegion(Strict):
    center_m:list[float]=Field(min_length=3,max_length=3)
    radius_m:float=Field(gt=0)
    size_m:float=Field(gt=0)
    transition_m:float=Field(gt=0)


class SharedMesh(MeshSpec):
    regions:list[MeshRegion]=Field(default_factory=list,max_length=32)


class ComputeResources(Strict):
    cpu_threads:int=Field(default=6,ge=1,le=64)
    max_dofs:int=Field(default=300000,ge=1000)
    memory_limit_GiB:float|None=Field(default=None,gt=0)
    time_limit_s:float=Field(default=86400,gt=0,le=604800)


class AnalysisCase(Strict):
    schema_version: Literal[3] = 3
    name: str = Field(min_length=1,max_length=160)
    analysis_type: Literal['static','thermal_steady','thermal_transient','fatigue','contact_cycle','contact_fatigue','thermo_mechanical']
    geometry_sha256: str | None = None
    material: Material
    component_materials: dict[str,Material] = Field(default_factory=dict)
    mesh: SharedMesh = Field(default_factory=SharedMesh)
    resources:ComputeResources=Field(default_factory=ComputeResources)
    supports: list[Support] = Field(default_factory=list)
    loads: list[MechanicalLoad] = Field(default_factory=list)
    bonded_connections: list[BondedConnection] = Field(default_factory=list)
    thermal_connections: list[BondedConnection] = Field(default_factory=list)
    contacts: list[ContactPair] = Field(default_factory=list)
    thermal: ThermalSettings = Field(default_factory=ThermalSettings)
    fatigue: FatigueSettings = Field(default_factory=FatigueSettings)
    history: LoadHistory | None = None
    motions: list[Motion] = Field(default_factory=list)
    axles:list[Axle]=Field(default_factory=list)
    clearance_probes:list['ClearanceProbe']=Field(default_factory=list)
    coupling: CouplingSettings = Field(default_factory=CouplingSettings)
    cycles_per_wear_block: int = Field(default=1,ge=1)
    wear_blocks: int = Field(default=1,ge=1,le=100)
    assumptions: list[str] = Field(default_factory=list)

    @model_validator(mode='after')
    def valid(self):
        thermal=self.analysis_type.startswith('thermal') or self.analysis_type=='thermo_mechanical'
        mechanical=self.analysis_type not in ('thermal_steady','thermal_transient')
        for m in [self.material,*self.component_materials.values()]:
            if thermal and (m.conductivity_W_mK is None or m.specific_heat_J_kgK is None):
                raise ValueError('热分析需要每个材料的导热系数和比热')
            if mechanical and (m.young_modulus_Pa is None or m.poisson_ratio is None):
                raise ValueError('结构分析需要每个材料的弹性模量和泊松比')
            if self.analysis_type=='thermo_mechanical' and m.thermal_expansion_per_K is None:
                raise ValueError('热结构分析需要明确的热膨胀系数')
        if self.analysis_type in ('fatigue','contact_cycle','contact_fatigue') and self.history is None:
            raise ValueError('循环分析需要完整载荷时间历程')
        if self.analysis_type in ('contact_cycle','contact_fatigue') and not self.contacts:
            raise ValueError('接触循环需要接触对')
        cycling=self.analysis_type in ('contact_cycle','contact_fatigue')
        if self.motions and not cycling:raise ValueError('选面运动历史须在周期接触研究中执行')
        if self.analysis_type=='fatigue' and self.contacts:raise ValueError('含接触的疲劳须使用接触疲劳工况，不能线性叠加')
        for pair in self.contacts:
            if pair.initial_offset_m!=0:raise ValueError('初始间隙／过盈须体现在 CAD 几何中，不能使用未验证的接触偏移')
            if pair.wear:
                if not cycling:raise ValueError('磨损演变须在周期接触研究中执行；热结构研究不执行磨损几何更新')
                wear_material=self.component_materials.get(str(pair.slave.body_id),self.material)
                if wear_material.hardness_Pa is None or wear_material.wear_coefficient is None:raise ValueError('磨损计算需要从侧材料的硬度和磨损系数')
        if self.history:
            if (self.analysis_type=='contact_fatigue' or self.analysis_type=='fatigue' and self.fatigue.method=='findley') and self.history.cycles_per_history!=1:
                raise ValueError('Findley 每段历史必须为一个完整基本周期，cycles_per_history 须为 1；分级载荷谱请使用适用的比例 S-N 方法')
            names={l.name for l in self.loads}
            if set(self.history.factors)-names:raise ValueError('载荷历史引用了不存在的载荷名称')
            if any(len(m.angles_rad)!=len(self.history.times_s) for m in self.motions):raise ValueError('运动历史须与载荷时间轴等长')
            if any(a.angles_rad is not None and len(a.angles_rad)!=len(self.history.times_s) for a in self.axles):raise ValueError('轴转角须与载荷时间轴等长')
        if len({a.body_id for a in self.axles})!=len(self.axles):raise ValueError('每个组件只允许一个刚性轴孔')
        if {a.body_id for a in self.axles}&{m.body_id for m in self.motions}:raise ValueError('同一组件不能同时使用刚性轴孔与选面运动')
        if self.axles and self.analysis_type=='fatigue':raise ValueError('旋转轴孔须使用显式接触周期，不能进行线性单位载荷叠加')
        if len({l.name for l in self.loads})!=len(self.loads):raise ValueError('载荷名称必须唯一')
        if len({p.name for p in self.contacts})!=len(self.contacts):raise ValueError('接触名称必须唯一')
        if any('/' in p.name or '\\' in p.name for p in self.contacts):raise ValueError('接触名称不能包含路径分隔符')
        if len({p.name for p in self.clearance_probes})!=len(self.clearance_probes) or any('/' in p.name or '\\' in p.name for p in self.clearance_probes):raise ValueError('间隙探针名称必须唯一且不能包含路径分隔符')
        return self


def material_for(case,body):
    return case.component_materials.get(str(int(body)),case.material)


def coupling_mode(case):
    """Describe the configured dependency graph, not just mechanical contacts."""
    value=case.model_dump() if hasattr(case,'model_dump') else case
    if value.get('analysis_type')!='thermo_mechanical':return 'not_applicable'
    return 'two_way_contact' if any(p.get('thermal') for p in value.get('contacts',[])) else 'one_way_thermal_structure'


class ClearanceProbe(Strict):
    name:str
    inner:Selection
    outer:Selection
    search_distance_m:float=Field(default=.002,gt=0)
    nominal_gap_m:float|None=None
    source:str=Field(min_length=1)


AnalysisCase.model_rebuild()
