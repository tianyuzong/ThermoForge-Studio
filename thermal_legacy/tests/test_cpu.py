from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from thermal_legacy.analysis import run_cpu
from thermal_legacy.schemas import CpuSimulation


def _request(cooler_type):
    return CpuSimulation(cooler_type=cooler_type, duration_s=60, dt_s=1, cpu_power_W=125)


def test_cpu_workbench_returns_transient_heat_balance_for_air_and_water():
    air=run_cpu(_request('air'))
    water=run_cpu(_request('water'))
    assert len(air['times_s'])==61
    assert air['maximum_cpu_C']>25
    assert water['maximum_cpu_C']<air['maximum_cpu_C']
    assert abs(air['thermal_balance_error_J'])<1e-6
    assert abs(water['thermal_balance_error_J'])<1e-6
