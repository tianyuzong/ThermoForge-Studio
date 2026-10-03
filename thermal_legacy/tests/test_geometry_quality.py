from pathlib import Path
import sys

import trimesh

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from thermal_legacy.geometry import inspect_stl_quality


def test_closed_stl_quality_is_clean():
    quality = inspect_stl_quality(trimesh.creation.box())
    assert quality['status'] == 'ok'
    assert quality['watertight'] is True
    assert quality['boundary_edges'] == 0
    assert quality['non_manifold_edges'] == 0
    assert quality['warnings'] == []


def test_open_stl_reports_hole_boundary_and_normal_state():
    mesh = trimesh.creation.box()
    mesh.update_faces([i for i in range(len(mesh.faces)) if i != 0])
    quality = inspect_stl_quality(mesh)
    assert quality['watertight'] is False
    assert quality['boundary_edges'] > 0
    assert any('开口边界' in warning for warning in quality['warnings'])
    assert quality['status'] == 'error'

