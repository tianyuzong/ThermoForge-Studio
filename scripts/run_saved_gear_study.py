"""Submit a natural-language torque scan of a saved, explicitly configured gear case."""
import argparse,uuid,json
import httpx

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--project-id',required=True)
parser.add_argument('--torques',nargs='+',type=float,default=[5,10,15])
parser.add_argument('--driver-body',type=int,default=1)
parser.add_argument('--url',default='http://127.0.0.1:8876')
args=parser.parse_args()
if not 1<=len(args.torques)<=12:parser.error('Use 1 to 12 cases')
client=httpx.Client(base_url=args.url+'/api/v1',trust_env=False,timeout=60)
r=client.get('/projects/'+args.project_id);r.raise_for_status();case=r.json().get('current_case') or {}
if case.get('assembly_mode')!='gear_contact':parser.error('Save a configured gear_contact case in this project first')
if not any(h['body_id']==args.driver_body and not h['rotation_locked'] for h in case['gear_contact']['hubs']):parser.error('Driver body must have an unlocked shaft')
prompt=f'沿用当前已保存齿轮接触工况。使用GPU，将组件 {args.driver_body} 的轴端输入扭矩分别设为 '+','.join(map(str,args.torques))+' N·m，各建一个工况并自动运行比较。仅修改该轴扭矩与工况名称，其余材料、轴孔、接触、支撑、网格和假设保留。报告注明教学假设及未完成的接触和网格验证。'
r=client.post('/agent/tasks',json={'project_id':args.project_id,'prompt':prompt,'provider':'cli','execution_mode':'auto','compute_backend':'cudss_cuda','request_id':uuid.uuid4().hex});r.raise_for_status()
print(json.dumps({'task_id':r.json()['id'],'status':r.json()['status'],'url':args.url},ensure_ascii=True))
