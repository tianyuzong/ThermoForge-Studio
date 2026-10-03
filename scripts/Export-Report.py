"""Export a completed study's report and compact provenance into portable examples."""
import argparse,json,shutil,sys,hashlib
from pathlib import Path
root=Path(__file__).resolve().parents[1];sys.path.insert(0,str(root))
from forge.report import report_pdf
from forge.schemas import AnalysisCase
p=argparse.ArgumentParser();p.add_argument('study');p.add_argument('--name',required=True);args=p.parse_args()
if not args.study.replace('-','').isalnum() or not args.name.replace('-','').isalnum():raise SystemExit('Use an alphanumeric identifier')
source=root/'data'/'studies'/args.study;target=root/'examples'/'reports'/args.name
config=json.loads((source/'config.json').read_text(encoding='utf-8'))
summary=json.loads((source/'summary.json').read_text(encoding='utf-8'))
report_pdf(source,AnalysisCase.model_validate(config['case']),summary)
target.mkdir(parents=True,exist_ok=True)
if not (source/'verification.json').exists() and (target/'verification.json').exists():(target/'verification.json').unlink()
for name in ('report.pdf','summary.json','config.json','audit.json','metrics.csv','raw-fields.sha256.json','verification.json'):
    if (source/name).is_file():shutil.copy2(source/name,target/name)
manifest={'source_study_id':args.study,'files':{f.name:hashlib.sha256(f.read_bytes()).hexdigest() for f in target.iterdir() if f.is_file() and f.name!='manifest.json'},'raw_fields':'Complete HDF5 remains in the local study; compact source distribution includes its hash, not the large raw field array.'}
(target/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
print(str(target/'report.pdf'))
