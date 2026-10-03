from pathlib import Path
import os
import json

ROOT = Path(__file__).resolve().parent.parent
# Local overrides are deliberately separate from versioned scientific inputs.
LOCAL = ROOT / 'local-settings.json'
SETTINGS = json.loads(LOCAL.read_text(encoding='utf-8-sig')) if LOCAL.exists() else {}
for key, value in SETTINGS.items():
    if key in ('STRENGTH_CPU_THREADS', 'STRENGTH_MAX_DOFS', 'STRENGTH_CODEX_COMMAND', 'STRENGTH_CODEX_TIMEOUT_S'):
        os.environ.setdefault(key, str(value))
CPU_THREADS = int(os.environ.get('STRENGTH_CPU_THREADS', '6'))
if not 1 <= CPU_THREADS <= (os.cpu_count() or 1):
    raise ValueError('CPU 线程数须在 1 到逻辑核心数之间')
DATA = Path(os.environ.get('STRENGTH_DATA', str(ROOT / 'data'))).resolve()
CCX = Path(os.environ.get('STRENGTH_CCX', str(ROOT / 'vendor' / 'ccx.exe'))).resolve()
MAX_DOFS = int(os.environ.get('STRENGTH_MAX_DOFS', '300000'))
for name in ('models', 'jobs', 'tasks'):
    (DATA / name).mkdir(parents=True, exist_ok=True)
