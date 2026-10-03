"""Content-addressed scientific provenance, independent of UI/report changes."""
import hashlib,json,importlib.metadata
from functools import lru_cache
from pathlib import Path
from strength.config import ROOT,CCX


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def file_hash(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def solver_signature():
    excluded={'api.py','assistant.py','imports.py','examples.py','report.py','verification.py','runs.py','plans.py','run_exports.py','run_validation.py','provenance.py','result_index.py','workspace.py','exports.py','storage.py','thumbnails.py','version.py'}
    sources={str(p.relative_to(ROOT)):file_hash(p) for p in (ROOT/'forge').glob('*.py') if p.name not in excluded}
    for name in ('geometry','fem','bonding','solver','schemas'):
        p=ROOT/'strength'/(name+'.py');sources[str(p.relative_to(ROOT))]=file_hash(p)
    packages={p:importlib.metadata.version(p) for p in ('numpy','scipy','h5py','gmsh')}
    return digest({'sources':sources,'packages':packages,'calculix':file_hash(CCX)})


def case_signature(case,geometry):
    value=case.model_dump() if hasattr(case,'model_dump') else dict(case)
    value.pop('name',None)
    return digest({'case':value,'geometry':{k:geometry.get(k) for k in ('source_sha256','components','surfaces')}})
