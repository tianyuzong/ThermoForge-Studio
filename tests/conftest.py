import os,sys,uuid
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
# Keep each run isolated; a short root avoids Windows MAX_PATH in nested checkouts.
TEST_ROOT=Path(os.environ.get('STRENGTH_TEST_ROOT', str(ROOT/'.runtime'/'test-runs')))
os.environ['STRENGTH_DATA']=str(TEST_ROOT/uuid.uuid4().hex)
os.environ['THERMAL_DATA']=str(Path(os.environ['STRENGTH_DATA'])/'legacy-thermal')
import pytest

@pytest.fixture(autouse=True)
def isolated_records():
    """Keep reusable CAD files, but never leak another test's database rows."""
    from strength import store
    with store.db() as connection:
        snapshot={table:connection.execute('SELECT * FROM '+table).fetchall() for table in ('entities','requests','events')}
    yield
    with store.db() as connection:
        connection.execute('BEGIN IMMEDIATE')
        for table,rows in snapshot.items():
            connection.execute('DELETE FROM '+table)
            if rows:connection.executemany('INSERT INTO '+table+' VALUES('+','.join('?' for _ in rows[0])+')',[tuple(r) for r in rows])

def pytest_addoption(parser):
    parser.addoption('--gpu',action='store_true',help='Run hardware GPU regressions (requires validated CUDA/cuDSS)')

def pytest_collection_modifyitems(config,items):
    if config.getoption('--gpu'):return
    hardware={'test_cudss_multiple_rhs','test_component_materials_cpu_gpu'}
    for item in items:
        if item.path.name=='test_gpu.py' or item.originalname in hardware:
            item.add_marker(pytest.mark.skip(reason='GPU hardware test: opt in with --gpu; CPU validation does not require CUDA'))
from strength.config import DATA
from strength.geometry import make_demo,inspect_model
from strength.store import write_json

@pytest.fixture(scope='session')
def models():
    result={}
    for kind in ('bar','beam','bracket'):
        folder=DATA/'models'/kind; folder.mkdir()
        make_demo(folder/'source.step',kind); write_json(folder/'source.json',{'file':'source.step','units':'m'})
        result[kind]=inspect_model(folder)
    return result

@pytest.fixture(scope='session')
def client():
    from fastapi.testclient import TestClient
    from strength.server import app
    with TestClient(app) as c: yield c
