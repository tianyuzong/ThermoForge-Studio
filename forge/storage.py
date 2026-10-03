"""Read-only inventory and explicit retention classes; never removes user data."""
from collections import defaultdict
from pathlib import Path
from fastapi import APIRouter
from strength.config import DATA,ROOT
from strength import store

router=APIRouter(prefix='/api/v1/storage')


def retention(path):
    if path.name in ('fields.h5','config.json','snapshot.json','verification.json','raw-fields.sha256.json','publication.json','manifest.json') or path.suffix in ('.step','.stp','.stl'):
        return 'original_and_evidence'
    if path.name in ('physical-steps.h5','checkpoint.json','last-converged.npz','completed-stage.json') or path.suffix in ('.rout','.rin'):
        return 'recovery_required'
    if path.name in ('result-index.json','display.json') or path.suffix in ('.mp4','.png','.zip'):
        return 'regenerable_output'
    return 'diagnostic_or_unclassified'


def inventory(root=DATA):
    root=Path(root).resolve();groups=defaultdict(lambda:{'files':0,'bytes':0});large=[];total=0;count=0
    for p in root.rglob('*'):
        if p.is_symlink() or not p.is_file():continue
        try:size=p.stat().st_size
        except OSError:continue
        category=retention(p);groups[category]['files']+=1;groups[category]['bytes']+=size;count+=1;total+=size
        if size>=100*1024**2:large.append({'path':p.relative_to(root).as_posix(),'bytes':size,'retention':category})
    return {'root':str(root),'files':count,'bytes':total,'categories':dict(groups),'large_files':sorted(large,key=lambda x:x['bytes'],reverse=True),
        'policy':{'automatic_deletion':False,'original_and_evidence':'永久保留，归档须用户明确选择','recovery_required':'计算停止后仍保留，明确放弃恢复后才可清理','regenerable_output':'可重新生成；本接口只预览，不删除','diagnostic_or_unclassified':'保留，分类审阅后处理'},'sampled_at':store.now()}


@router.get('/inventory')
def read_inventory():return inventory()
