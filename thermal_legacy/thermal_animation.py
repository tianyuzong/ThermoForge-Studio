"""Cached animation views and MP4 exports from completed FEM temperature fields."""
import base64,gzip,hashlib,html,json,os,subprocess,sys,tempfile,threading,time,uuid
from pathlib import Path
import numpy as np
from thermal_legacy.runtime import ROOT,JOBS,MODELS,read_json,write_json
from thermal_legacy.compute_policy import worker_environment

LOCK=threading.RLock()
PROCESSES={}
TEMPLATE=ROOT/'static/thermal-animation-template.html'
ACTIVE={'pending','rendering','encoding'}


def fingerprint(job):
    files=[job/name for name in ('config.json','result.json','mesh.npz','temperatures.npy')]
    files += [TEMPLATE,Path(__file__),ROOT/'static/vendor/three.module.js',ROOT/'static/vendor/OrbitControls.js']
    records=[(str(p.name),p.stat().st_size,p.stat().st_mtime_ns) for p in files]
    return hashlib.sha256(json.dumps(records).encode()).hexdigest()


def load_fields(job):
    if read_json(job/'status.json').get('phase')!='completed':raise ValueError('请等待仿真计算完成后再查看热扩散动画')
    cfg=read_json(job/'config.json');result=read_json(job/'result.json')
    if cfg.get('analysis_mode','transient')=='steady':raise ValueError('稳态结果只有平衡状态，不生成热扩散时间动画')
    times=np.asarray(result['times_s'],dtype=float)
    if len(times)<2 or not np.isfinite(times).all() or np.any(np.diff(times)<=0):raise ValueError('瞬态保存时刻必须有限且严格递增，并包含至少两帧')
    with np.load(job/'mesh.npz') as m:points=m['points'];boundary=m['boundary_triangles']
    temperatures=np.load(job/'temperatures.npy',mmap_mode='r')
    if temperatures.shape!=(len(times),len(points)) or not np.isfinite(temperatures).all():raise ValueError('完整节点温度场与几何或保存时刻不匹配')
    if points.ndim!=2 or points.shape[1]!=3 or not np.isfinite(points).all():raise ValueError('网格顶点无效')
    if boundary.ndim!=2 or boundary.shape[1]!=3 or not boundary.size or boundary.dtype.kind not in 'iu' or boundary.min()<0 or boundary.max()>=len(points):raise ValueError('有限元外表面索引无效')
    stats=result['stats']
    if len(stats)!=len(times) or any(abs(row['time_s']-t)>1e-7 for row,t in zip(stats,times)):raise ValueError('统计结果与保存时刻不匹配')
    keys=('time_s','maximum_C','minimum_C','average_C')
    stats=[{k:float(row[k]) for k in keys} for row in stats]
    if not np.isfinite([[row[k] for k in keys] for row in stats]).all():raise ValueError('温度统计存在无效值')
    surface=result.get('summary',{}).get('surface_evaluation') or {}
    contact=surface.get('rows')
    if contact is not None and (len(contact)!=len(times) or any(abs(row['time_s']-t)>1e-7 for row,t in zip(contact,times))):raise ValueError('接触面指标与保存时刻不匹配')
    ids,inverse=np.unique(boundary,return_inverse=True)
    model=read_json(MODELS/cfg['model_id']/'metadata.json') if (MODELS/cfg['model_id']/'metadata.json').exists() else {}
    lo=float(temperatures.min());hi=float(temperatures.max())
    low=5*np.floor(lo/5);high=5*np.ceil(hi/5)
    if high<=low:low-=.5;high+=.5
    delta=max(row['maximum_C']-row['minimum_C'] for row in stats)
    from matplotlib import colormaps
    meta=dict(job_id=job.name,parent_job_id=cfg.get('initial_from_job'),name=cfg['name'],times=times.tolist(),
              nodes=len(ids),triangles=len(boundary),stats=stats,contact=contact,
              evaluation_name=surface.get('name','评估面'),temperature_limit=surface.get('temperature_limit_C'),
              temperature_limits=[float(low),float(high)],delta_limit=max(1.,float(np.ceil(delta))),
              colormap=colormaps['turbo'](np.linspace(0,1,256))[:,:3].tolist(),
              dimensions_mm=(np.ptp(points,axis=0)*1000).tolist(),model_name=model.get('name',cfg['model_id']))
    return cfg,meta,(points[ids]*1000).astype('<f4'),inverse.reshape(-1,3).astype('<u4'),np.asarray(temperatures[:,ids],dtype='<f4')


def initial_note(cfg):
    parent=cfg.get('initial_from_job')
    if parent:
        path=JOBS/parent/'config.json'
        name=read_json(path).get('name',parent) if path.exists() else parent
        return '继承“'+name+'”的完整末帧温度场。'
    return f"工件初始温度 {cfg['initial_C']:g} ℃；独立瞬态工况。"


def physics_note(cfg):
    sources=cfg.get('heat_sources',[])
    if not sources:text='无热源，按给定环境条件进行温变计算。'
    elif any(s.get('power_profile') or s.get('thermostat') for s in sources):text=f'{len(sources)} 个热源按已计算的功率曲线或温控条件工作。'
    else:text=f"热源总设定功率 {sum(s['power_W'] for s in sources):g} W，启停区间沿用已计算配置。"
    text+=('环境温度按给定曲线变化。' if cfg.get('ambient_profile') else f"环境温度 {cfg['ambient_C']:g} ℃。")
    return text


def prepare_preview(job):
    job=Path(job)
    with LOCK:
        signature=fingerprint(job);dest=job/'thermal-animation';dest.mkdir(exist_ok=True)
        manifest=dest/'manifest.json';target=dest/'animation.html'
        if target.exists() and manifest.exists() and read_json(manifest).get('signature')==signature:return target
        cfg,meta,points,faces,fields=load_fields(job)
        def encoded(a):return base64.b64encode(gzip.compress(a.tobytes(),compresslevel=6)).decode()
        payload=dict(points=encoded(points),faces=encoded(faces),temperatures=encoded(fields))
        def safe_json(value):return json.dumps(value,ensure_ascii=False,separators=(',',':'),allow_nan=False).replace('<','\\u003c')
        title=cfg['name']+' · 热扩散过程'
        subtitle=meta['model_name']+'　'+' × '.join(f'{v:.3g}' for v in meta['dimensions_mm'])+' mm'
        replacements={'__TITLE__':html.escape(title),'__SUBTITLE__':html.escape(subtitle),
                      '__INITIAL_NOTE__':html.escape(initial_note(cfg)), '__PHYSICS_NOTE__':html.escape(physics_note(cfg)),
                      '__SAVE_NOTE__':html.escape(f"计算步长 {cfg['dt_s']:g} s，播放 {len(meta['times'])} 个实际保存帧；保留所有原始保存时刻。"),
                      '__META__':safe_json(meta),'__BINARY__':safe_json(payload),
                      '__THREE__':base64.b64encode((ROOT/'static/vendor/three.module.js').read_bytes()).decode(),
                      '__ORBIT__':base64.b64encode((ROOT/'static/vendor/OrbitControls.js').read_bytes()).decode()}
        text=TEMPLATE.read_text(encoding='utf8')
        for key,value in replacements.items():text=text.replace(key,value)
        text+='\n<!-- Three.js license:\n'+(ROOT/'static/vendor/THREE-LICENSE.txt').read_text(encoding='utf8')+'\n-->\n'
        pending=dest/'animation.pending.html';pending.write_text(text,encoding='utf8');pending.replace(target)
        write_json(manifest,dict(signature=signature,job_id=job.name,frames=len(meta['times']),times_s=meta['times'],
                                absolute_limits_C=meta['temperature_limits'],relative_limit_C=meta['delta_limit']))
        return target


def process_alive(pid):
    if not pid:return False
    if os.name=='nt':
        import ctypes
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.OpenProcess.restype=ctypes.c_void_p
        kernel.GetExitCodeProcess.argtypes=[ctypes.c_void_p,ctypes.POINTER(ctypes.c_ulong)]
        kernel.CloseHandle.argtypes=[ctypes.c_void_p]
        handle=kernel.OpenProcess(0x1000,False,int(pid))
        if not handle:return False
        try:
            code=ctypes.c_ulong();return bool(kernel.GetExitCodeProcess(handle,ctypes.byref(code))) and code.value==259
        finally:kernel.CloseHandle(handle)
    try:os.kill(int(pid),0);return True
    except ProcessLookupError:return False
    except PermissionError:return True


def export_status(job):
    dest=job/'thermal-animation';p=dest/'export.json'
    state=read_json(p) if p.exists() else dict(phase='idle',progress=0,detail='尚未导出视频')
    signature=fingerprint(job)
    if state.get('signature') and state['signature']!=signature:return dict(phase='idle',progress=0,detail='结果或动画版本已更新，需要重新导出')
    if state['phase']=='completed' and not (dest/'animation.mp4').exists():return dict(phase='idle',progress=0,detail='视频文件不存在，可重新导出')
    if state['phase'] in ACTIVE and (not process_alive(state.get('pid')) or time.time()-state.get('updated_at',0)>1800):
        state.update(phase='failed',detail='视频导出被中断，可以重新导出')
    return state


def start_export(job):
    with LOCK:
        prepare_preview(job);state=export_status(job)
        if state['phase'] in ACTIVE or state['phase']=='completed':return state
        for p in JOBS.glob('*/thermal-animation/export.json'):
            other=read_json(p)
            if other.get('phase') in ACTIVE and process_alive(other.get('pid')) and time.time()-other.get('updated_at',0)<1800:
                raise RuntimeError('另一个视频正在导出，请稍后重试；动画预览仍可使用')
        dest=job/'thermal-animation';signature=fingerprint(job)
        manifest=read_json(dest/'manifest.json')
        if manifest['frames']>600:raise ValueError('当前视频导出支持最多600个保存帧；完整帧数据仍可在交互动画中播放')
        token=uuid.uuid4().hex
        with (dest/'worker.log').open('wb') as log:
            process=subprocess.Popen([sys.executable,str(ROOT/'thermal_animation.py'),'export',job.name,token],cwd=ROOT,
                                     stdout=log,stderr=subprocess.STDOUT,env=worker_environment(),
                                     creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        PROCESSES[job.name]=process
        state=dict(phase='pending',progress=0,detail='准备导出MP4视频',signature=signature,pid=process.pid,updated_at=time.time())
        write_json(dest/'export.json',state)
        # The venv launcher and Python worker can have different PIDs on Windows.
        # A separate launch token transfers status ownership after this write.
        write_json(dest/'launch.json',dict(token=token))
        return state


def frame_durations(times,seconds=15.):
    values=np.asarray(times,float)
    if len(values)<2 or np.any(np.diff(values)<=0):raise ValueError('导出时刻必须严格递增')
    return (np.diff(values)*seconds/(values[-1]-values[0])).tolist()+[2.]


def render_video(job):
    dest=job/'thermal-animation';signature=fingerprint(job)
    def update(phase,progress,detail,**extra):
        write_json(dest/'export.json',dict(phase=phase,progress=progress,detail=detail,signature=signature,
                                         pid=os.getpid(),updated_at=time.time(),**extra))
    try:
        preview=prepare_preview(job);manifest=read_json(dest/'manifest.json');times=manifest['times_s']
        if len(times)>600:raise ValueError('视频导出最多支持600个保存帧')
        update('rendering',2,'正在打开三维动画并读取真实温度场')
        from playwright.sync_api import sync_playwright
        import imageio_ffmpeg
        with tempfile.TemporaryDirectory(prefix='render-',dir=dest) as directory:
            frames=Path(directory).resolve()
            if not frames.is_relative_to(dest.resolve()):raise ValueError('视频缓存路径无效')
            with sync_playwright() as browser_api:
                channel=os.environ.get('THERMAL_VIDEO_BROWSER','msedge' if os.name=='nt' else 'chromium')
                try:browser=browser_api.chromium.launch(channel=channel,headless=True)
                except Exception as error:raise RuntimeError('无法启动视频渲染浏览器。请安装Microsoft Edge，或设置THERMAL_VIDEO_BROWSER=chromium并运行python -m playwright install chromium。') from error
                try:
                    page=browser.new_page(viewport={'width':1440,'height':1020},device_scale_factor=1)
                    page.goto(preview.resolve().as_uri());page.wait_for_function('window.thermalReady',timeout=90000)
                    page.evaluate("document.body.classList.add('capture')")
                    for i in range(len(times)):
                        page.evaluate('(i)=>window.setThermalFrame(i)',i)
                        page.screenshot(path=str(frames/f'{i:04d}.png'),full_page=True)
                        if i%5==0:update('rendering',5+int(75*(i+1)/len(times)),f'正在绘制实际保存帧 {i+1}/{len(times)}')
                finally:browser.close()
            durations=frame_durations(times);lines=['ffconcat version 1.0']
            for i,duration in enumerate(durations):lines.extend([f"file '{i:04d}.png'",f'duration {duration:.12f}'])
            lines.append(f"file '{len(times)-1:04d}.png'")
            concat=frames/'frames.txt';concat.write_text('\n'.join(lines)+'\n',encoding='ascii')
            update('encoding',85,'正在编码MP4视频，保持真实时刻的相对间隔')
            pending=dest/'animation.pending.mp4'
            command=[imageio_ffmpeg.get_ffmpeg_exe(),'-hide_banner','-loglevel','error','-y','-f','concat','-safe','1','-i',str(concat),
                     '-vf','fps=24,pad=ceil(iw/2)*2:ceil(ih/2)*2,format=yuv420p','-c:v','libx264','-preset','medium','-crf','18','-threads','2',
                     '-movflags','+faststart','-an',str(pending)]
            subprocess.run(command,check=True,timeout=300,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
            if not pending.exists() or pending.stat().st_size<100:raise ValueError('视频输出不完整')
            if fingerprint(job)!=signature:raise ValueError('导出期间结果或动画版本发生变化，请重试')
            pending.replace(dest/'animation.mp4')
        update('completed',100,'MP4视频已生成',frames=len(times),simulation_start_s=times[0],simulation_end_s=times[-1],duration_s=17.)
    except Exception as error:
        update('failed',0,str(error)[:1000]);raise


if __name__=='__main__':
    if len(sys.argv)!=4 or sys.argv[1]!='export' or not sys.argv[2].isalnum() or not sys.argv[3].isalnum():raise SystemExit('invalid export request')
    for attempt in range(100):
        record=JOBS/sys.argv[2]/'thermal-animation/launch.json'
        if record.exists() and read_json(record).get('token')==sys.argv[3]:break
        time.sleep(.05)
    else:raise SystemExit('export owner was not recorded')
    render_video(JOBS/sys.argv[2])
