"""Deterministic result statistics and C3D10 point/path interpolation."""
import json
import numpy as np
import h5py
from .fem import IP_BARY, EDGES, tet_shape, nodal_stress_for_display
from .materials import bodies, property_array, material_for, selection_mask
from .schemas import LoadCase, ResultQuery
from .result_catalog import FIELDS, derive, displacement_fields, finite_json
from .store import write_json

def integration_geometry(mesh):
    P=mesh['points'][mesh['tets']]; coords=[];weights=[]
    for bary in IP_BARY:
        n,d=tet_shape(bary[1:]);coords.append(np.einsum('i,eic->ec',n,P))
        weights.append(np.linalg.det(np.einsum('eic,ij->ecj',P,d))/24)
    w=np.stack(weights,axis=1)
    if np.any(w<=0): raise ValueError('后处理积分发现非正 Jacobian')
    return np.stack(coords,axis=1),w

def fields_for(mesh,case,data):
    properties=[property_array(mesh,case,k)[:,None] for k in ('young_modulus_Pa','poisson_ratio','yield_strength_Pa')]
    raw=derive(data['stresses'],data['strains'],*properties,case.strength_criterion,case.target_safety_factor)
    # No averaging across components: the mesh retains separate component nodes.
    nodal_properties=[]
    for key in ('young_modulus_Pa','poisson_ratio','yield_strength_Pa'):
        a=np.full(len(mesh['points']),np.nan)
        for body in np.unique(bodies(mesh)):
            val=getattr(material_for(case,body),key)
            a[np.unique(mesh['tets'][bodies(mesh)==body])]=np.nan if val is None else val
        nodal_properties.append(a)
    display=derive(nodal_stress_for_display(mesh,data['stresses']),nodal_stress_for_display(mesh,data['strains']),
        *nodal_properties,case.strength_criterion,case.target_safety_factor)
    display.update(displacement_fields(data['displacements']))
    return raw,display

def statistics(values,positions,weights,ids,location):
    v=np.asarray(values).ravel();p=np.asarray(positions).reshape(-1,3);w=np.asarray(weights).ravel()
    valid=np.isfinite(v);count=int(valid.sum())
    if not count:return {'status':'undefined','reason':'所选区域没有有定义的结果（缺少强度或分母为零）','count':0,'undefined_count':len(v)}
    def extremum(which):
        indices=np.flatnonzero(valid);k=indices[which(v[valid])]
        result={'value':float(v[k]),'position_m':p[k].tolist()}
        if location=='node':result['node']=int(np.asarray(ids).ravel()[k])+1
        else:result.update(element=int(np.asarray(ids).ravel()[k])+1,integration_point=int(k%4)+1)
        return result
    return {'status':'ok','minimum':extremum(np.argmin),'maximum':extremum(np.argmax),
        'mean':float(np.average(v[valid],weights=w[valid])),'count':count,'undefined_count':int((~valid).sum()),
        'mean_basis':'节点算术平均' if location=='node' else '原始积分点体积加权平均'}

def summarize(mesh,case,data,raw):
    positions,w=integration_geometry(mesh);stats={};nn=len(mesh['points']);ee=len(mesh['tets'])
    for key,values in raw.items():
        stats[key]=statistics(values,positions,w,np.repeat(np.arange(ee),4),'integration_point')
    for key,values in displacement_fields(data['displacements']).items():
        stats[key]=statistics(values,mesh['points'],np.ones(nn),np.arange(nn),'node')
    return {'result_schema_version':2,'strength_criterion':case.strength_criterion,'field_statistics':stats,
        'strain_energy_J':float(np.sum(raw['energy_density']*w)),
        'energy_basis':'0.5 sigma:epsilon，C3D10 四点积分；张量剪切项计两次',
        'field_catalog':list(FIELDS.values())}

class ResultArchive:
    def __init__(self,folder):
        self.folder=folder
        cfg=json.loads((folder/'config.json').read_text(encoding='utf-8'))
        self.case=LoadCase.model_validate(cfg['case']);self.config=cfg
        with h5py.File(folder/'fields.h5') as f:
            self.mesh={k:f[k][:] for k in ('points','tets','faces','surface_ids','owners','body_ids') if k in f}
            self.data={k:f[k][:] for k in ('displacements','stresses','strains','reaction')}
        self.mesh['face_body_ids']=bodies(self.mesh)[self.mesh['owners']]
        self.raw,self.display=fields_for(self.mesh,self.case,self.data)
        self.ip,self.weights=integration_geometry(self.mesh)
        self.summary=json.loads((folder/'summary.json').read_text(encoding='utf-8'))
        self.evidence=None;self.bounds=None

    def element_mask(self,q):
        mask=np.ones(len(self.mesh['tets']),bool)
        if q.body_id is not None:mask &= bodies(self.mesh)==q.body_id
        if q.selection:
            if self.evidence is None:
                # Geometry evidence remains bound to this job's original model.
                from .config import DATA
                self.evidence=json.loads((DATA/'models'/self.config['model_id']/'geometry.json').read_text(encoding='utf-8'))
            owner=self.mesh['owners'][selection_mask(self.mesh,q.selection,self.evidence)]
            mask &= np.isin(np.arange(len(mask)),owner)
        if not mask.any():raise ValueError('结果查询选区为空或组件不存在')
        return mask

    def locate(self,point,body=None):
        P=self.mesh['points'][self.mesh['tets']];point=np.asarray(point,float)
        if self.bounds is None:
            # Bernstein control hull bounds also contain curved quadratic elements.
            control=P.copy()
            for k,(a,b) in enumerate(EDGES,4):control[:,k]=2*P[:,k]-.5*(P[:,a]+P[:,b])
            self.bounds=(control.min(1),control.max(1))
        lo,hi=self.bounds;tol=max(np.ptp(self.mesh['points'],axis=0).max()*1e-9,1e-12)
        mask=((point>=lo-tol)&(point<=hi+tol)).all(1)
        if body is not None:mask &= bodies(self.mesh)==body
        found=[]
        for ei in np.flatnonzero(mask):
            p=P[ei]
            try:
                q=np.linalg.solve((p[1:4]-p[0]).T,point-p[0])
                for _ in range(20):
                    n,d=tet_shape(q);res=n@p-point
                    if np.linalg.norm(res)<=tol:break
                    q-=np.linalg.solve(p.T@d,res)
                n,_=tet_shape(q)
                if np.linalg.norm(n@p-point)<=tol and min(*q,1-q.sum())>=-1e-8:
                    found.append((int(ei),q,n))
            except np.linalg.LinAlgError:continue
        if len({int(bodies(self.mesh)[i]) for i,_,_ in found})>1:
            raise ValueError('点位于多个组件的交界，请指定组件')
        return found[0] if found else None

    def probe(self,q,point=None):
        p=self.mesh['points'];u=self.data['displacements'];field=q.field
        if field not in FIELDS:raise ValueError('未知结果场：'+field)
        if point is None and q.node_id is not None:
            ni=q.node_id-1
            if ni<0 or ni>=len(p):raise ValueError('节点编号超出范围')
            if q.body_id and ni not in np.unique(self.mesh['tets'][bodies(self.mesh)==q.body_id]):raise ValueError('节点不属于指定组件')
            xyz=p[ni];disp=u[ni];value=self.display[field][ni]
            basis='节点位移；应力/应变为单元外推后的体积加权节点显示值'
            element=None
        else:
            xyz=np.asarray(point if point is not None else q.point_m)
            located=self.locate(xyz,q.body_id)
            if located is None:return {'status':'outside','position_m':xyz.tolist(),'value':None,'reason':'采样点在模型外，不吸附或外推到模型外'}
            element,coord,n=located;disp=n@u[self.mesh['tets'][element]]
            alpha=np.r_[1-coord.sum(),coord]@np.linalg.inv(IP_BARY)
            stress=alpha@self.data['stresses'][element];strain=alpha@self.data['strains'][element]
            m=material_for(self.case,bodies(self.mesh)[element])
            vals=derive(stress,strain,m.young_modulus_Pa,m.poisson_ratio,np.nan if m.yield_strength_Pa is None else m.yield_strength_Pa,self.case.strength_criterion,self.case.target_safety_factor)
            vals.update(displacement_fields(disp));value=vals[field]
            basis='C3D10 位移插值；应力/应变由所属单元原始积分点插值（未跨单元平滑）'
        return finite_json({'status':'ok' if np.isfinite(value) else 'undefined','position_m':xyz,'displacement_m':disp,
            'deformed_position_m':xyz+disp,'field':field,'value':value,'unit':FIELDS[field]['unit'],
            'element':None if element is None else element+1,'basis':basis})

    def query(self,q):
        q=ResultQuery.model_validate(q) if isinstance(q,dict) else q
        meta={'name':q.name,'kind':q.kind,'field':q.field}
        if q.kind=='statistics':
            if q.field not in FIELDS:raise ValueError('未知结果场：'+q.field)
            mask=self.element_mask(q);nodes=np.unique(self.mesh['tets'][mask])
            if FIELDS[q.field]['location']=='node':
                r=statistics(self.display[q.field][nodes],self.mesh['points'][nodes],np.ones(len(nodes)),nodes,'node')
            else:r=statistics(self.raw[q.field][mask],self.ip[mask],self.weights[mask],np.repeat(np.flatnonzero(mask),4),'integration_point')
            r.update(unit=FIELDS[q.field]['unit'],scope='选面相邻体单元' if q.selection else '指定组件' if q.body_id else '整体')
        elif q.kind=='probe':r=self.probe(q)
        elif q.kind=='path':
            points=np.linspace(q.point_m,q.end_m,q.samples)
            samples=[self.probe(q,point) for point in points]
            for point,row in zip(points,samples):row['distance_m']=float(np.linalg.norm(point-np.asarray(q.point_m)))
            r={'status':'ok' if all(x['status']=='ok' for x in samples) else 'partial','samples':samples,
                'unit':FIELDS[q.field]['unit'],'basis':'所属单元插值曲线；跨单元可不连续；模型外点留空'}
        elif q.kind=='relative':
            a=self.probe(q,q.point_m);b=self.probe(q,q.end_m)
            if 'displacement_m' not in a or 'displacement_m' not in b:r={'status':'undefined','reason':'相对位移端点必须位于模型内','endpoints':[a,b]}
            else:
                du=np.array(b['displacement_m'])-a['displacement_m']
                r={'status':'ok','relative_displacement_m':du,'magnitude_m':float(np.linalg.norm(du)),'endpoints':[a,b]}
                if q.direction is not None:r['directional_displacement_m']=float(du@np.asarray(q.direction)/np.linalg.norm(q.direction))
        elif q.kind=='reactions':
            origin=np.asarray(q.moment_origin_m or self.case.moment_origin_m or self.summary['balance']['moment_origin_m'])
            nodes=np.unique(self.mesh['tets'][self.element_mask(q)])
            force=self.data['reaction'][nodes];r={'status':'ok','reaction_N':force.sum(0),
                'moment_Nm':np.cross(self.mesh['points'][nodes]-origin,force).sum(0),'moment_origin_m':origin,
                'basis':'约束自由度反力去重；选面查询包含所选面相邻单元的节点反力'}
            if q.selection:
                face_mask=selection_mask(self.mesh,q.selection,self.evidence);nodes=np.unique(self.mesh['faces'][face_mask]);force=self.data['reaction'][nodes]
                r.update(reaction_N=force.sum(0),moment_Nm=np.cross(self.mesh['points'][nodes]-origin,force).sum(0),basis='所选面节点约束自由度反力去重')
        else:
            sample=self.probe(q);loads=[l for l in self.summary['loads'] if l['name']==q.load_name]
            if len(loads)!=1:raise ValueError('刚度查询须唯一匹配一个载荷名称')
            direction=np.asarray(q.direction);direction/=np.linalg.norm(direction)
            f=float(np.asarray(loads[0]['resultant_N'])@direction)
            delta=float(np.asarray(sample['displacement_m'])@direction) if 'displacement_m' in sample else None
            ok=delta is not None and abs(delta)>1e-15 and abs(f)>1e-12
            r={'status':'ok' if ok else 'undefined','load_N':f,'displacement_m':delta,
                'stiffness_N_m':f/delta if ok else None,'compliance_m_N':delta/f if ok else None,
                'reason':None if ok else '载荷或方向位移近零，或探针在模型外','probe':sample,
                'basis':'指定载荷投影 / 指定点方向位移；响应包含该工况全部载荷与给定位移'}
        return finite_json({**meta,**r})

def execute_requests(folder,case):
    if not case.result_requests:return []
    archive=ResultArchive(folder);out=[]
    for q in case.result_requests:
        try:out.append(archive.query(q))
        except ValueError as e:out.append({'name':q.name,'kind':q.kind,'status':'unavailable','reason':str(e)})
    write_json(folder/'result-queries.json',out)
    return out

def compare_results(jobs):
    """Scalar envelopes are valid across different meshes; no nodal subtraction."""
    envelopes={};curves={}
    for j in jobs:
        s=j['summary']
        for field,stat in s.get('field_statistics',{}).items():
            if stat.get('status')!='ok':continue
            row=envelopes.setdefault(field,{'unit':FIELDS[field]['unit']})
            for key in ('minimum','maximum'):
                if key not in row or (stat[key]['value']<row[key]['value'] if key=='minimum' else stat[key]['value']>row[key]['value']):
                    row[key]={**stat[key],'job_id':j['id'],'case_name':j['name']}
        for q,result in zip(j['case'].get('result_requests',[]),s.get('result_queries',[])):
            if q['kind']=='stiffness' and result['status']=='ok':
                # Never connect unlike geometry/material/support/probe configurations.
                case=j['case'];identity={k:case.get(k) for k in ('material','component_materials','supports','assembly_mode','bonded_connections')}
                load_identity=[]
                for load in case['loads']:
                    item=dict(load)
                    if load['name']==q['load_name']:
                        if load['kind']=='pressure':item['pressure_Pa']='varied'
                        else:
                            v=np.asarray(load['vector'],float);length=np.linalg.norm(v)
                            item['vector_direction']=(v/length).round(12).tolist() if length else [0,0,0]
                            item.pop('vector',None)
                    load_identity.append(item)
                identity.update(geometry=s.get('geometry_sha256'),query=q,loads=load_identity)
                key=json.dumps(identity,sort_keys=True,ensure_ascii=False)
                curves.setdefault(key,{'name':q['name'],'basis':'独立线弹性静力工况采样，非加载历史','points':[]})['points'].append(
                    {'job_id':j['id'],'case_name':j['name'],'load_N':result['load_N'],'displacement_m':result['displacement_m']})
    return {'envelopes':envelopes,'load_displacement_curves':[v for v in curves.values() if len(v['points'])>=2]}
