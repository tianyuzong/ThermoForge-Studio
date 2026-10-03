"""Compatibility routes for immutable P1 thermal studies and their replay.

The legacy solver remains namespaced. Heavy jobs share the same process slot
as new studies and original structural jobs; cancellation kills the whole tree.
"""
import os,sys,subprocess,threading
from pathlib import Path
from fastapi.routing import APIRoute
from fastapi.responses import FileResponse
from starlette.staticfiles import StaticFiles
from strength import service
from strength.config import DATA,ROOT


class QueuedProcess:
    def __init__(self,kind,id,legacy):
        self.process=None;self.code=None;self.cancelled=threading.Event();self.lock=threading.RLock()
        threading.Thread(target=self.run,args=(kind,id,legacy),daemon=True).start()
    def poll(self):return self.code
    def run(self,kind,id,legacy):
        acquired=False;folder=(legacy.MODELS if kind=='model' else legacy.JOBS)/id
        try:
            while not service.SLOT.acquire(timeout=.2):
                if self.cancelled.is_set():return
            acquired=True
            with self.lock:
                if self.cancelled.is_set():return
                with (folder/'worker.log').open('wb') as log:
                    env=legacy.worker_environment() if kind=='solve' else os.environ.copy()
                    env['THERMAL_DATA']=str(legacy.DATA);env['PYTHONIOENCODING']='utf-8'
                    self.process=subprocess.Popen([sys.executable,'-m','thermal_legacy.worker',kind,id],cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,
                        env=env,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            self.code=self.process.wait()
        except Exception as exc:
            legacy.progress(folder,'failed',0,str(exc));self.code=1
        finally:
            if self.cancelled.is_set():legacy.progress(folder,'cancelled',0,'已取消；已完成文件保留');self.code=-1
            if acquired:service.SLOT.release()
    def terminate(self):
        with self.lock:
            self.cancelled.set()
            if self.process and self.process.poll() is None:service.agent.terminate_tree(self.process)
    def wait(self,timeout=10):
        if self.process:return self.process.wait(timeout=timeout)
        self.code=-1;return self.code


def install(app):
    os.environ.setdefault('THERMAL_DATA',str(DATA/'legacy-thermal'))
    os.environ.setdefault('THERMAL_CUDA_BACKEND','native_cudss')
    from thermal_legacy import server as legacy
    def spawn(kind,id):legacy.processes[id]=QueuedProcess(kind,id,legacy)
    legacy.spawn=spawn
    for route in legacy.app.routes:
        if isinstance(route,APIRoute):app.router.routes.append(route)
    static=ROOT/'thermal_legacy'/'static'
    # Preserve old absolute asset URLs while the main React app stays at '/'.
    for path in static.iterdir():
        if path.is_dir():app.mount('/'+path.name,StaticFiles(directory=path),name='legacy-'+path.name)
        elif path.name!='index.html':
            def asset(path=path):return FileResponse(path)
            app.add_api_route('/'+path.name,asset,methods=['GET'],include_in_schema=False)
    @app.get('/legacy/thermal',include_in_schema=False)
    def viewer():return FileResponse(static/'index.html')
