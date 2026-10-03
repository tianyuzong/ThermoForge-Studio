import importlib.util
import json
from pathlib import Path
import pytest


def load_script(name):
    spec=importlib.util.spec_from_file_location(name,Path(__file__).parents[1]/'scripts'/f'{name}.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


def prepared(tmp_path,monkeypatch):
    module=load_script('build_manifest');monkeypatch.setattr(module,'ROOT',tmp_path)
    for path in ['frontend/src/App.tsx','frontend/public/logo.svg','frontend/dist/index.html','frontend/dist/assets/app.js',
                 'forge/worker.py','thermal_legacy/solver.py','integrations/api.py','scripts/install.py','vendor/ccx.exe',
                 *('frontend/'+name for name in module.FRONTEND_CONFIGS)]:
        p=tmp_path/path;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(path)
    saved=module.manifest();(tmp_path/'BUILD-MANIFEST.json').write_text(json.dumps(saved));return module


@pytest.mark.parametrize('path',['frontend/src/App.tsx','frontend/public/logo.svg','frontend/tsconfig.json','frontend/dist/assets/app.js',
                               'forge/worker.py','thermal_legacy/solver.py','integrations/api.py','scripts/install.py','vendor/ccx.exe'])
def test_release_manifest_rejects_changed_inputs_runtime_and_built_output(tmp_path,monkeypatch,path):
    module=prepared(tmp_path,monkeypatch);assert module.verify()['version']==module.VERSION
    (tmp_path/path).write_text('changed')
    with pytest.raises(ValueError,match='manifest mismatch'):module.verify()


def test_release_manifest_rejects_added_dist_member_and_missing_index(tmp_path,monkeypatch):
    module=prepared(tmp_path,monkeypatch);extra=tmp_path/'frontend/dist/assets/unrecorded.js';extra.write_text('unrecorded')
    with pytest.raises(ValueError,match='frontend_dist'):module.verify()
    (tmp_path/'frontend/dist/index.html').unlink()
    with pytest.raises(ValueError,match='missing index.html'):module.manifest()


def test_release_manifest_rejects_changed_runtime_attestation(tmp_path,monkeypatch):
    module=prepared(tmp_path,monkeypatch);monkeypatch.setattr(module.importlib.metadata,'version',lambda name:'changed-runtime')
    with pytest.raises(ValueError,match='packages'):module.verify()


def test_source_package_uses_explicit_metadata_allowlist_and_excludes_private_data(tmp_path):
    module=load_script('package_release')
    allowed=['SOURCE_VERSIONS.json','BUILD-MANIFEST.json','local-settings.example.json','.mcp.json','forge/api.py','frontend/package.json','frontend/dist/assets/app.js','examples/demo/case.json']
    private=['personal-credentials.json','local-settings.json','LOCAL-CONNECTION.JSON','data/models/private.step','.runtime/debug.log',
             '.venv/lib/module.py','forge/client.p12','forge/.env.production','frontend/local-auth.json','thermal_legacy/data/jobs/private.json']
    for name in allowed+private:
        path=tmp_path/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('fixture')
    assert {p.relative_to(tmp_path).as_posix() for p in module.files(tmp_path)}==set(allowed)
