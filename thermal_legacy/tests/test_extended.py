from pathlib import Path
import sys

import numpy as np
from scipy.sparse import csr_matrix

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from thermal_legacy.analysis import run_calibration, run_cycle
from thermal_legacy.schemas import CalibrationRequest, RefrigerationCycle, Simulation
from thermal_legacy.solver import integrate


def test_extended_simulation_schema_and_calibration():
    cfg = Simulation(
        model_id='test',
        analysis_mode='steady',
        radiation_enabled=True,
        contact_resistance_m2K_W=1e-5,
        base_material=dict(name='phase', k=10, rho=1000, cp=1000, phase_change=dict(melting_C=50, mushy_C=2, latent_J_kg=100000)),
    )
    assert cfg.analysis_mode == 'steady'
    request = CalibrationRequest(mode='fit_h', mass_kg=1, cp_J_kgK=1000, area_m2=1, power_W=100, measurements=[{'time_s': 0, 'temperature_C': 25}, {'time_s': 60, 'temperature_C': 30}, {'time_s': 120, 'temperature_C': 34}])
    result = run_calibration(request)
    assert result['h_W_m2K'] > 0
    assert result['rmse_C'] < 2


def test_radiation_transient_keeps_energy_audit_consistent():
    cfg = Simulation(model_id='test', duration_s=4, dt_s=1, save_s=2, initial_C=25, ambient_C=25, radiation_enabled=True, radiation_ambient_C=25, heat_sources=[dict(name='heater', power_W=2, start_s=0, end_s=4, faces=[0])]).model_dump()
    frames, times, stats, energy = integrate(csr_matrix(np.eye(2)), csr_matrix((2, 2)), csr_matrix((2, 2)), np.zeros(2), [np.ones(2)], cfg, rad_area=np.ones(2), rad_ambient=np.full(2, 25.), rad_eps=np.full(2, .8))
    assert frames.shape == (3, 2)
    assert abs(energy['energy_balance_error_J']) < 1e-6


def test_refrigeration_cycle_returns_cop_and_power():
    result = run_cycle(RefrigerationCycle(evaporating_C=5, condensing_C=45, cooling_capacity_W=1000))
    assert result['cop'] > 1
    assert result['total_power_W'] > 0
    assert result['heating_rejection_W'] > result['cooling_capacity_W']
