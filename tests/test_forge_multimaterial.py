"""Independent series-bar oracles for distinct thermal/mechanical interfaces."""
import numpy as np
import pytest
from forge.schemas import AnalysisCase
from forge.thermal import HeatSystem
from forge.mechanics import solve_static
from test_forge_numerics import material

def test_dissimilar_bonded_bar_thermal_resistance_and_expansion(tmp_path):
    import gmsh
    from strength.geometry import session,inspect_model,build_mesh
    from strength.store import write_json
    folder=tmp_path/'cad';folder.mkdir()
    with session():
        for x in (0,.05):gmsh.model.occ.addBox(x,0,0,.05,.01,.01)
        gmsh.model.occ.synchronize();gmsh.write(str(folder/'source.step'))
    write_json(folder/'source.json',{'file':'source.step','units':'m'});geo=inspect_model(folder)
    sel=lambda b,d:dict(kind='direction',body_id=b,direction=d)
    m1=material();m1.poisson_ratio=0.
    m2=m1.model_copy(deep=True);m2.young_modulus_Pa=100e9;m2.conductivity_W_mK=10
    connection=dict(name='两材料理想结合',slave=sel(2,'-x'),master=sel(1,'+x'),max_gap_m=1e-6,assumptions='解析杆截面相同，界面理想连接')
    case=AnalysisCase(name='异材串联杆解析基准',analysis_type='thermo_mechanical',material=m1,component_materials={'1':m1,'2':m2},mesh={'size_m':.009},
        supports=[dict(name='左端轴向',selection=sel(1,'-x'),components=['x']),dict(name='右端轴向',selection=sel(2,'+x'),components=['x']),
            *[dict(name=axis+'基准',selection=sel(1,'-'+axis),components=[axis]) for axis in 'yz']],
        bonded_connections=[connection],thermal_connections=[connection],
        thermal={'reference_C':25,'boundaries':[dict(name='冷端',kind='temperature',selection=sel(1,'-x'),value=0),dict(name='热端',kind='temperature',selection=sel(2,'+x'),value=100)]})
    path=tmp_path/'mesh.npz';build_mesh(folder,case,path);mesh=dict(np.load(path))
    system=HeatSystem(mesh,case,geo);T,audit=system.solve(np.full(len(mesh['points']),25.),0)
    x=mesh['points'][:,0];flux=100/(.05/40+.05/10)
    exact_T=np.where(x<=.05,flux*x/40,flux*(.05/40+(x-.05)/10))
    assert np.max(abs(T-exact_T))/100<.01
    gradient,heatflux=system.gradients(T)
    assert np.max(abs(heatflux[...,0]+flux))/flux<.01
    # Separate uniform-temperature mechanical oracle: both ends restrained,
    # nu=0 and equal alpha make transverse thermal strains compatible at the
    # interface, so the series-bar solution is exact in 3D despite different E.
    frame=solve_static(mesh,case,geo,tmp_path/'solve',temperature=np.full(len(x),75.))
    stress=-50*12e-6*.1/(.05/200e9+.05/100e9)
    e1=stress/200e9+12e-6*50;e2=stress/100e9+12e-6*50
    exact_u=np.where(x<=.05,e1*x,e1*.05+e2*(x-.05))
    assert np.max(abs(frame['displacements'][:,0]-exact_u))/np.max(abs(exact_u))<.01
    assert np.max(abs(frame['stresses'][...,0]-stress))/abs(stress)<.02
    assert frame['balance']['passed']
