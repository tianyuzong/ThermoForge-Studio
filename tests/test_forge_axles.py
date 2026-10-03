import numpy as np
import pytest
from strength.config import DATA
from strength.geometry import build_mesh
from forge.schemas import AnalysisCase
from forge.mechanics import solve_static
from test_forge_numerics import material


def test_torque_driven_free_axle_reaction_and_rotation(models,tmp_path):
    geo=models['bar'];sel=lambda d:{'kind':'direction','body_id':1,'direction':d}
    case=AnalysisCase(name='自由转轴扭矩传递',analysis_type='static',material=material(),mesh={'size_m':.004},
        supports=[{'name':'右端固定','selection':sel('+x')}],
        axles=[{'name':'扭矩轴','body_id':1,'selection':sel('-x'),'center_m':[0,.005,.005],'axis':'x','torque_Nm':2}])
    path=tmp_path/'mesh.npz';build_mesh(DATA/'models'/geo['id'],case,path);mesh=dict(np.load(path))
    frame=solve_static(mesh,case,geo,tmp_path/'solve')
    shaft=frame['axles']['扭矩轴'];theta=shaft['rotation_vector_rad'][0]
    # Saint-Venant torsion constant for a square section; clamped end warping
    # gives a small compliance reduction on this finite 10:1 bar.
    exact=2*.1/(200e9/(2*1.3)*.140577*.01**4)
    assert theta>0 and abs(theta/exact-1)<.08
    assert frame['balance']['passed']


def test_free_uniform_thermal_expansion(models,tmp_path):
    geo=models['bar'];sel=lambda d:{'kind':'direction','body_id':1,'direction':d}
    case=AnalysisCase(name='自由膨胀',analysis_type='thermo_mechanical',material=material(),mesh={'size_m':.012},
        supports=[{'name':axis+'基准面','selection':sel('-'+axis),'components':[axis]} for axis in 'xyz'])
    path=tmp_path/'mesh.npz';build_mesh(DATA/'models'/geo['id'],case,path);mesh=dict(np.load(path))
    frame=solve_static(mesh,case,geo,tmp_path/'solve',temperature=np.full(len(mesh['points']),75.))
    exact=12e-6*50*mesh['points']
    assert np.max(np.abs(frame['displacements']-exact))/exact.max()<.01
    assert np.max(np.abs(frame['stresses']))<100.
