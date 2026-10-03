import argparse, traceback, json, sys
from pathlib import Path
from .store import write_json

def main():
    p=argparse.ArgumentParser(); p.add_argument('action',choices=['inspect','solve','demo','gpu-session']); p.add_argument('folder'); p.add_argument('--kind',default='bracket'); a=p.parse_args()
    folder=Path(a.folder)
    try:
        if a.action=='gpu-session':
            from .solver import run_job
            from .gpu_solver import clear_cache
            from .config import DATA
            try:
                for line in sys.stdin:
                    job=Path(json.loads(line)['folder']).resolve()
                    if job.parent!=(DATA/'jobs').resolve():raise ValueError('GPU 作业目录无效')
                    try:run_job(job)
                    except Exception as e:
                        traceback.print_exc();clear_cache();write_json(job/'status.json',{'status':'failed','detail':str(e)})
            finally:clear_cache()
        elif a.action=='solve':
            from .solver import run_job
            run_job(folder)
        else:
            from .geometry import inspect_model,make_demo
            if a.action=='demo':
                make_demo(folder/'source.step',a.kind)
                write_json(folder/'source.json',{'file':'source.step','original_name':{'bracket':'L形安装支架','bar':'拉杆验证','beam':'悬臂梁验证','components':'双组件材料示例'}[a.kind]+'.step','units':'m'})
            inspect_model(folder)
    except Exception as e:
        traceback.print_exc()
        write_json(folder/'status.json',{'status':'failed','detail':str(e)})
        raise SystemExit(1)

if __name__=='__main__': main()
