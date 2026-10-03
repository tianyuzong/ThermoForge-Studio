"""Application-only CUPTI kernel trace, no ETW/system tracing or counters.

Nsight Systems CLI on Windows requires an administrator. This is a separate
unprivileged CUPTI Activity API capture, not an Nsight report or timing baseline.
"""
import ctypes as C,os,sys,json,hashlib,csv
from collections import defaultdict
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import numpy as np
from strength.cuda_runtime import runtime
from strength.gpu_solver import solve_gpu,clear_cache
from strength.schemas import LoadCase,DEMO_MATERIAL
from strength.fem import assemble_loads,build_constraints

P=C.c_void_p;U=C.c_uint32;Q=C.c_uint64;Z=C.c_size_t
class KernelPrefix(C.Structure):
    _fields_=[('kind',U),('cache',C.c_uint8),('shared_config',C.c_uint8),('registers',C.c_uint16),
        ('partition_requested',U),('partition_executed',U),('start',Q),('end',Q),('completed',Q),
        ('device',U),('context',U),('stream',U)]+[(k,C.c_int32) for k in ('grid_x','grid_y','grid_z','block_x','block_y','block_z','static_shared','dynamic_shared')]+[
        ('local_per_thread',U),('local_total',U),('correlation',U),('grid_id',C.c_int64),('name',C.c_char_p)]

def main():
    r=runtime();toolkit=Path(os.environ['CUDA_PATH']);dll=toolkit/'extras'/'CUPTI'/'lib64'/'cupti64_2024.3.0.dll'
    lib=C.WinDLL(str(dll));buffers={};records=[];errors=[];dropped=[0]
    request_type=C.WINFUNCTYPE(None,C.POINTER(P),C.POINTER(Z),C.POINTER(Z))
    complete_type=C.WINFUNCTYPE(None,P,U,P,Z,Z)
    lib.cuptiActivityGetNextRecord.argtypes=[P,Z,C.POINTER(P)]
    lib.cuptiActivityGetNumDroppedRecords.argtypes=[P,U,C.POINTER(Z)]
    lib.cuptiActivityRegisterCallbacks.argtypes=[request_type,complete_type]
    def check(code):
        if code:raise RuntimeError(f'CUPTI error {code}')
    @request_type
    def request(buffer,size,maximum):
        b=C.create_string_buffer(8*1024*1024);address=C.addressof(b);buffers[address]=b
        buffer[0]=address;size[0]=C.sizeof(b);maximum[0]=0
    @complete_type
    def complete(context,stream,buffer,size,valid):
        try:
            record=P()
            while True:
                code=lib.cuptiActivityGetNextRecord(buffer,valid,C.byref(record))
                if code:break
                k=C.cast(record,C.POINTER(KernelPrefix)).contents
                if k.kind in (3,10):records.append({'name':k.name.decode(errors='replace'),'start_ns':int(k.start),'end_ns':int(k.end),'device':int(k.device),'context':int(k.context),'stream':int(k.stream),'correlation':int(k.correlation),'grid':[k.grid_x,k.grid_y,k.grid_z],'block':[k.block_x,k.block_y,k.block_z]})
            n=Z();check(lib.cuptiActivityGetNumDroppedRecords(context,stream,C.byref(n)));dropped[0]+=n.value
        except Exception as e:errors.append(str(e))
        finally:buffers.pop(int(buffer),None)
    check(lib.cuptiActivityRegisterCallbacks(request,complete));check(lib.cuptiActivityEnable(10))
    work=ROOT/'.runtime'/'gpu-benchmark';folder=work/'500000'
    mesh=dict(np.load(folder/'mesh.npz'));geo=json.loads((work/'bracket'/'geometry.json').read_text(encoding='utf-8'))
    case=LoadCase(material=DEMO_MATERIAL,supports=[{'selection':{'kind':'named','name':'安装孔'}}],loads=[{'kind':'force','selection':{'kind':'named','name':'顶面'},'vector':[0,0,-1000]}])
    force,_=assemble_loads(mesh,case,geo);fixed,_=build_constraints(mesh,case,geo)
    data,execution=solve_gpu(mesh,case,force,fixed);clear_cache();r.sync()
    check(lib.cuptiActivityFlushAll(1));check(lib.cuptiActivityDisable(10))
    names={x['name'] for x in records};valid=not errors and not dropped[0] and {'gradients','assemble','recover'}.issubset(names) and any('cudss' in name for name in names) and all(x['end_ns']>=x['start_ns']>0 for x in records)
    result={'capture':'CUPTI Activity API 12.6, application-only','nsight_capture':False,'verified':valid,'errors':errors,'dropped_records':dropped[0],
        'mesh_sha256':hashlib.sha256((folder/'mesh.npz').read_bytes()).hexdigest(),'execution':execution,'kernel_count':len(records),'records':records}
    path=ROOT/'docs'/'GPU_KERNEL_TRACE.json';path.write_text(json.dumps(result,ensure_ascii=False),encoding='utf-8')
    totals=defaultdict(lambda:[0,0])
    for rec in records:totals[rec['name']][0]+=1;totals[rec['name']][1]+=rec['end_ns']-rec['start_ns']
    with (ROOT/'docs'/'GPU_KERNEL_SUMMARY.csv').open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.writer(f);w.writerow(['kernel','launches','total_gpu_ms'])
        for name,(count,ns) in sorted(totals.items(),key=lambda x:x[1][1],reverse=True):w.writerow([name,count,ns/1e6])
    print(json.dumps({'verified':valid,'kernel_count':len(records),'kernel_names':len(names),'errors':errors,'dropped':dropped[0]}))
    if not valid:raise RuntimeError('Kernel activity evidence incomplete')

if __name__=='__main__':main()
