"""Cache cleanup must not require optional contact or optimization modules."""
import builtins
import sys
from types import SimpleNamespace
from strength import gpu_solver


def test_clear_cache_without_contact_import(monkeypatch):
    monkeypatch.delitem(sys.modules, 'strength.gear_contact', raising=False)
    calls = []
    monkeypatch.setattr(gpu_solver, '_cached', SimpleNamespace(close=lambda: calls.append('closed')))
    original_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if 'gear_contact' in name or name.startswith('scipy.optimize'):
            raise AssertionError('Optional contact dependencies must stay unloaded')
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, '__import__', guarded_import)
    gpu_solver.clear_cache()
    assert calls == ['closed']
    assert gpu_solver._cached is None


def test_clear_cache_includes_loaded_contact_module(monkeypatch):
    calls = []
    monkeypatch.setattr(gpu_solver, '_cached', None)
    monkeypatch.setitem(sys.modules, 'strength.gear_contact',
                        SimpleNamespace(clear_cache=lambda: calls.append('contact')))
    gpu_solver.clear_cache()
    assert calls == ['contact']
