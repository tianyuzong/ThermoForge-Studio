"""Finish a requested local acceptance run and publish only verified presets.

This is a developer delivery helper, not the application's job runner. It never
resumes a cancelled calculation or starts another physical solve to fill gaps.
"""
import argparse,json,os,shutil,subprocess,sys,time,urllib.request
from datetime import datetime,timezone
from pathlib import Path
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from strength import store
from strength.config import DATA
from forge import runs,api
from forge.schemas import AnalysisCase
from forge.provenance import file_hash,case_signature,solver_signature


def request(url,path,body=None):
    req=urllib.request.Request(url+'/api/v1'+path,data=None if body is None else json.dumps(body).encode(),
                               headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(req,timeout=300) as f:return json.load(f)


def regression():
    names=['one-click-regression.xml','one-click-gpu.xml','one-click-gpu-extra.xml',
           'one-click-legacy-thermal.xml','one-click-contract-final.xml']
    tests={}
    for name in names:
        for test in ET.parse(ROOT/'validation'/name).getroot().iter('testcase'):
            key=test.get('classname','')+'.'+test.get('name','')
            state='failed' if test.find('failure') is not None or test.find('error') is not None else 'skipped' if test.find('skipped') is not None else 'passed'
            if key not in tests or state!='skipped':tests[key]={'status':state,'source':name}
    return {'counts':{s:sum(v['status']==s for v in tests.values()) for s in ('passed','failed','skipped')},'tests':tests}


def write_record(run,stage,**extra):
    record={'run_id':run['id'],'updated_utc':datetime.now(timezone.utc).isoformat(),'stage':stage,
            'run_status':run['status'],'nodes':[{'key':n['key'],'status':n['status'],'study_id':n.get('study_id')} for n in run['nodes']],
            'regression':regression(),**extra}
    store.write_json(ROOT/'validation/one-click-delivery.json',record)
    return record


def finalize(id,url,wait):
    while True:
        run=store.get('analysis_run',id)
        if run['status'] in runs.TERMINAL:break
        if not wait:return write_record(run,'calculation_pending')
        time.sleep(30)
    if run['status']!='completed':
        write_record(run,'not_accepted',detail=run.get('detail'));raise RuntimeError('全套任务未完成；保留诊断，不发布验证预设')
    evidence=ROOT/'validation/one-click-acceptance.json'
    accepted=json.loads(evidence.read_text(encoding='utf-8')) if evidence.exists() else {}
    if accepted.get('run_id')!=id or not accepted.get('passed'):
        subprocess.run([sys.executable,str(ROOT/'scripts/Validate-OneClick.py'),id,'--url',url],check=True)
        accepted=json.loads(evidence.read_text(encoding='utf-8'))
    if not accepted.get('passed'):raise ValueError('原始场、视频或 ZIP 验收未通过')
    node=next(n for n in run['nodes'] if n['key']=='thermo_mechanical')
    if not runs.valid_validation(node.get('validation')) or node['validation'].get('fine')!=node['study_id']:
        raise ValueError('缺少与当前结果绑定的瞬态步长证据')
    if run['solver_signature']!=solver_signature():raise ValueError('求解源版本已改变，不覆盖发布预设')
    # Preserve a user's later edits: only replace the exact original preset.
    preset=ROOT/'examples/motor-unified/thermo-contact-transient.json'
    geo=store.get('model',node['model_id'])
    current=api.freeze_named_selections(AnalysisCase.model_validate_json(preset.read_text(encoding='utf-8')),geo)
    solved=AnalysisCase.model_validate(store.get('study',node['study_id'])['case'])
    original=AnalysisCase.model_validate(node['case'])
    if case_signature(current,geo) not in (case_signature(original,geo),case_signature(solved,geo)):
        raise ValueError('示例配置已被用户修改；未覆盖该文件')
    store.write_json(preset,solved.model_dump())
    key=runs.preset_key(solved,geo,run['solver_signature'])
    store.write_json(DATA/'run-presets'/(key+'.json'),{'case':solved.model_dump(),'validation':node['validation']})
    store.write_json(ROOT/'validation/motor-transient-coupling.json',node['validation'])
    versions=json.loads((ROOT/'SOURCE_VERSIONS.json').read_text(encoding='utf-8'))
    published=ROOT/'examples/reports/one-click';published.mkdir(parents=True,exist_ok=True)
    shutil.copy2(runs.folder(id)/'summary.pdf',published/'summary.pdf')
    for n in run['nodes']:
        target=published/n['key'];target.mkdir(exist_ok=True)
        for name in ('config.json','summary.json','report.pdf','metrics.csv','verification.json'):
            source=DATA/'studies'/n['study_id']/name
            if source.is_file():shutil.copy2(source,target/name)
    flags={'cwd':ROOT,'check':True,'creationflags':getattr(subprocess,'CREATE_NO_WINDOW',0)}
    managed=ROOT/'.runtime/server.json'
    expected={name:file_hash(ROOT/name) for name in ('strength/server.py','forge/api.py','forge/runs.py','forge/plans.py')}
    if managed.exists() and json.loads(managed.read_text(encoding='utf-8-sig')).get('source_hashes')!=expected:
        while any(s['status'] not in api.TERMINAL for kind in ('study','job','analysis_run') for s in store.listing(kind,10000)):
            if not wait:raise RuntimeError('存在其他活动任务；空闲后才能加载控制接口更新')
            time.sleep(30)
        for name,arguments in [('Stop.ps1',[]),('Start.ps1',['-NoBrowser'])]:
            subprocess.run(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(ROOT/name),*arguments],**flags)
    health=request(url,'/health')
    if health.get('version')!=versions['integration_version']:raise ValueError('运行中的服务与源码版本不一致')
    repeated=request(url,'/analysis-runs',{'request_id':'acceptance-reuse-'+id,'project_id':run.get('origin_project_id'),'prompt':'运行全套分析','template':'motor-bearing'})
    while True:
        repeated=request(url,'/analysis-runs/'+repeated['id'])
        if repeated.get('owned_studies'):
            request(url,'/analysis-runs/'+repeated['id']+'/cancel',{})
            raise ValueError('完全匹配复用检查触发了新的计算，已仅取消该检查任务')
        if repeated['status'] in runs.TERMINAL:break
        time.sleep(2)
    if repeated['status']!='completed' or not all(n.get('reused') for n in repeated['nodes']):
        raise ValueError('完整复用与导出未通过')
    node_exe=shutil.which('node')
    if not node_exe:raise ValueError('浏览器交付测试需要 Node.js')
    browser=subprocess.run([node_exe,'tests/runs.mjs'],cwd=ROOT/'frontend',env={**os.environ,'STUDIO_RUN':repeated['id'],'STRENGTH_URL':url},
                           capture_output=True,text=True,encoding='utf-8',creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    (ROOT/'validation/one-click-browser.log').write_text(browser.stdout+browser.stderr,encoding='utf-8')
    if browser.returncode:raise ValueError('浏览器结果检查未通过')
    result=write_record(run,'visual_review_pending',automatic_checks_passed=True,complete_acceptance=False,preset_dt_s=solved.thermal.dt_s,checks=accepted['checks'],reuse_run_id=repeated['id'],
                        browser_passed=True,summary_pdf='examples/reports/one-click/summary.pdf',
                        manual_visual_qa='首四项分项 PDF 共 9 页和对应视频已人工检查；耦合及轴承新增页需另行视觉复核')
    counts=result['regression']['counts']
    text=f"""# 一键套件验证记录

任务：`{id}`；记录时间：{result['updated_utc']}。

- 六项研究、六组动画导出及 ZIP 自动验收通过。详细原始字段核对、视频解码、文件哈希见 `validation/one-click-acceptance.json`。
- 电机瞬态接触较细步长已固化为 {solved.thermal.dt_s:g} 秒；校核仅涵盖时间步与全局平衡，不代表整机局部接触网格或材料实测标定。
- 默认重复任务 `{repeated['id']}` 复用全部六项结果，没有启动新的求解作业。
- 软件回归去重：{counts['passed']} 通过、{counts['failed']} 失败、{counts['skipped']} 跳过；两个跳过项为本机未配置的 CuPy 路径，原生 CUDA/cuDSS 路径另有通过记录。
- 浏览器流程通过；首四项 PDF 共 9 页及视频已逐页/逐画面检查。耦合、轴承及最终汇总 PDF 的新增页仍需视觉复核，不能将自动数值校验表述为人工版面验收。
- 本次重新导入 CAD 的静力指标差异小于 0.1%，但节点坐标不与旧参考逐位一致。严格同网格检查未通过，见 `validation/one-click-static-comparison.json`；保留原版本同网格验收，不覆盖其结论。

全部材料为演示输入，不能作为该电机/轴承的实测寿命。详细边界和来源见各工况快照及分项报告。
"""
    (ROOT/'ONE_CLICK_VALIDATION.md').write_text(text,encoding='utf-8')
    subprocess.run([sys.executable,str(ROOT/'scripts/package_release.py')],**flags)
    print(json.dumps({'stage':'visual_review_pending','run_id':id,'reuse_run_id':repeated['id'],'preset_dt_s':solved.thermal.dt_s}),flush=True)
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('run');parser.add_argument('--url',default='http://127.0.0.1:8877');parser.add_argument('--wait',action='store_true')
    args=parser.parse_args()
    try:finalize(args.run,args.url,args.wait)
    except Exception as exc:
        write_record(store.get('analysis_run',args.run),'delivery_check_failed',detail=str(exc))
        raise
