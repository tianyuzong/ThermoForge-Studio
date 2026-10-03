from thermal_legacy.runtime import *
import argparse, traceback
p=argparse.ArgumentParser();p.add_argument('kind',choices=['model','solve']);p.add_argument('id');args=p.parse_args()
folder=(MODELS if args.kind=='model' else JOBS)/args.id
try:
    if args.kind=='model':
        from thermal_legacy.geometry import prepare_model
        prepare_model(folder)
    else:
        from thermal_legacy.solver import solve_job
        solve_job(folder)
except Exception as error:
    traceback.print_exc()
    progress(folder,'failed',0,str(error))
    raise SystemExit(1)
