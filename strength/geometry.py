"""Deterministic CAD evidence, named selections and quadratic tetrahedral mesh."""
import hashlib, json, math, shutil
from contextlib import contextmanager
from pathlib import Path
import numpy as np
import gmsh
from .store import write_json
from .config import MAX_DOFS
from .gmsh_lock import GMSH_LOCK

@contextmanager
def session():
    with GMSH_LOCK:
        gmsh.initialize([], readConfigFiles=False, interruptible=False)
        gmsh.option.setNumber('General.Terminal', 0)
        # Repeatable meshes matter when comparing load-only changes.
        gmsh.option.setNumber('General.NumThreads', 1)
        gmsh.option.setNumber('Mesh.RandomSeed', 1)
        gmsh.option.setString('Geometry.OCCTargetUnit', 'M')
        try: yield
        finally: gmsh.finalize()

def make_demo(path, kind='bracket'):
    with session():
        occ=gmsh.model.occ
        if kind=='bar': occ.addBox(0,0,0,.1,.01,.01)
        elif kind=='components':
            occ.addBox(0,0,0,.1,.01,.01);occ.addBox(0,.03,0,.1,.01,.01)
        elif kind=='beam': occ.addBox(0,0,0,.2,.02,.01)
        else:
            # L bracket: 100 x 60 x 60 mm, 8 mm wall, R6 internal root.
            a=occ.addBox(0,-.03,0,.1,.06,.008)
            b=occ.addBox(0,-.03,.008,.008,.06,.052)
            volumes,_=occ.fuse([(3,a)],[(3,b)])
            occ.synchronize()
            roots=[]
            for _,e in gmsh.model.getEntities(1):
                bb=gmsh.model.getBoundingBox(1,e)
                if abs((bb[0]+bb[3])/2-.008)<1e-6 and abs((bb[2]+bb[5])/2-.008)<1e-6 and bb[4]-bb[1]>.05: roots.append(e)
            if roots: volumes=occ.fillet([t for _,t in volumes], roots, [.006])
            holes=[(3,occ.addCylinder(x,y,-.001,0,0,.010,.004)) for x in (.025,.075) for y in (-.02,.02)]
            occ.cut(volumes,holes)
        occ.synchronize(); gmsh.write(str(path))

def load_geometry(folder):
    meta=json.loads((folder/'source.json').read_text(encoding='utf-8'))
    file=folder/meta['file']
    if file.suffix.lower()=='.stl':
        import trimesh
        surface=trimesh.load(file,force='mesh',process=True)
        if not surface.is_watertight: raise ValueError('STL 非封闭实体：请修复孔洞后重新导入')
        if not surface.is_winding_consistent or surface.volume<=0: raise ValueError('STL 表面法向不一致或朝内，请在 CAD 中修复后导入')
        if len(surface.split(only_watertight=False))!=1: raise ValueError('首版仅支持一个连通工件，请拆分 STL')
        scale={'mm':.001,'cm':.01,'m':1.,'inch':.0254}.get(meta.get('units'))
        if scale is None: raise ValueError('STL 必须选择明确单位')
        gmsh.merge(str(file)); gmsh.model.mesh.removeDuplicateNodes()
        # Scale mesh before surface parametrization. STL has no declared units.
        tags,xyz,_=gmsh.model.mesh.getNodes()
        for tag,p in zip(tags,np.asarray(xyz).reshape(-1,3)*scale): gmsh.model.mesh.setNode(int(tag),p,[])
        gmsh.model.mesh.classifySurfaces(math.radians(40),True,True,math.pi)
        gmsh.model.mesh.createGeometry()
        surfaces=[t for _,t in gmsh.model.getEntities(2)]
        loop=gmsh.model.geo.addSurfaceLoop(surfaces)
        gmsh.model.geo.addVolume([loop]); gmsh.model.geo.synchronize()
    else:
        gmsh.model.occ.importShapes(str(file)); gmsh.model.occ.synchronize()
        volumes=gmsh.model.getEntities(3)
        if len(volumes)>1 and meta.get('body_tag') is not None:
            selected=meta.get('body_tag')
            if selected not in [t for _,t in volumes]:
                raise ValueError('模型包含多个实体；请通过 body_tag 选择一个实体。可选：'+','.join(str(t) for _,t in volumes))
            gmsh.model.occ.remove([(d,t) for d,t in volumes if t!=selected],recursive=True)
            gmsh.model.occ.synchronize()
    volumes=gmsh.model.getEntities(3)
    if not volumes: raise ValueError('模型必须包含有效的三维实体')
    return meta

def surface_mesh():
    tags,xyz,_=gmsh.model.mesh.getNodes(); points=np.asarray(xyz).reshape(-1,3)
    index={int(t):i for i,t in enumerate(tags)}
    faces=[]; ids=[]; records=[]
    boundary=gmsh.model.getBoundary(gmsh.model.getEntities(3),oriented=True)
    signs={abs(t):1 if t>0 else -1 for d,t in boundary if d==2}
    owners={}
    for _,body in gmsh.model.getEntities(3):
        for d,s in gmsh.model.getBoundary([(3,body)],oriented=False):
            if d==2: owners.setdefault(s,[]).append(body)
    for _,s in gmsh.model.getEntities(2):
        types,_,nodes=gmsh.model.mesh.getElements(2,s)
        local=[]
        for ty,nd in zip(types,nodes):
            n=gmsh.model.mesh.getElementProperties(int(ty))[3]
            tris=np.asarray(nd).reshape(-1,n)[:,:3]
            local.extend([[index[int(x)] for x in tr] for tr in tris])
        if not local: continue
        tri=np.asarray(local); pp=points[tri]
        # Gmsh's volume boundary triangles already carry the oriented mesh winding.
        cross=np.cross(pp[:,1]-pp[:,0],pp[:,2]-pp[:,0])
        area=np.linalg.norm(cross,axis=1)/2; total=area.sum()
        norm=cross/(2*area[:,None]); center=np.average(pp.mean(axis=1),axis=0,weights=area)
        avg=(cross.sum(axis=0)/2)/total
        surf_type=gmsh.model.getType(2,s)
        hole=False
        if surf_type=='Cylinder':
            # Complete concave cylindrical walls have inward radial normals.
            radial=np.sum(norm*(pp.mean(axis=1)-center),axis=1)
            hole=np.linalg.norm(avg)<.1 and np.average(radial,weights=area)<0
        record={'id':s,'body_ids':owners.get(s,[]),'name':('孔壁' if hole else surf_type)+f' {s}', 'type':surf_type,'is_hole':bool(hole),
            'area_m2':float(total),'centroid_m':center.tolist(),'normal':avg.tolist(),
            'bounds_m':[pp.reshape(-1,3).min(axis=0).tolist(),pp.reshape(-1,3).max(axis=0).tolist()]}
        records.append(record); faces.extend(tri.tolist()); ids.extend([s]*len(tri))
    used=np.unique(np.asarray(faces)); remap=np.full(len(points),-1); remap[used]=np.arange(len(used))
    return {'points':points[used].tolist(),'triangles':remap[np.asarray(faces)].tolist(),'surface_ids':ids},records

def inspect_model(folder):
    with session():
        meta=load_geometry(folder)
        lo=np.array(gmsh.model.getBoundingBox(-1,-1)[:3]); hi=np.array(gmsh.model.getBoundingBox(-1,-1)[3:])
        span=float(max(hi-lo))
        if span<=0 or span>100: raise ValueError('模型尺寸异常，请核对单位')
        gmsh.option.setNumber('Mesh.MeshSizeMax',span/24)
        gmsh.option.setNumber('Mesh.MeshSizeMin',span/120)
        gmsh.option.setNumber('Mesh.MeshSizeFromCurvature',24)
        gmsh.model.mesh.generate(2)
        display,records=surface_mesh()
        named={}
        names={'+x':'右侧面','-x':'左侧面','+y':'后侧面','-y':'前侧面','+z':'顶面','-z':'底面'}
        for direction,name in names.items():
            found=direction_faces(records,direction)
            if found: named[name]=found
        holes=[r['id'] for r in records if r['is_hole']]
        if holes: named['安装孔']=holes; named['孔壁']=holes
        named['全部表面']=[r['id'] for r in records]
        # Actual tessellated bounds avoid OCC tolerance inflation.
        p=np.asarray(display['points']); bounds=[p.min(axis=0).tolist(),p.max(axis=0).tolist()]
        volume=None
        components=[]
        for _,body in gmsh.model.getEntities(3):
            local=[r for r in records if body in r['body_ids']]
            selections={name:direction_faces(local,direction) for direction,name in names.items()}
            selections['全部表面']=[r['id'] for r in local]
            components.append({'id':body,'name':f'组件 {body}','face_ids':selections['全部表面'],
                'named_selections':{k:v for k,v in selections.items() if v}})
            local_holes=[r['id'] for r in local if r['is_hole']]
            if local_holes: components[-1]['named_selections']['安装孔']=local_holes
        if (folder/meta['file']).suffix.lower()!='.stl': volume=sum(gmsh.model.occ.getMass(3,t) for _,t in gmsh.model.getEntities(3))
        evidence={'id':folder.name,'source_sha256':hashlib.sha256((folder/meta['file']).read_bytes()).hexdigest(),
            'name':meta.get('original_name',meta['file']),'units':'m','bounds_m':bounds,'dimensions_m':(p.max(0)-p.min(0)).tolist(),
            'volume_m3':volume,'components':components,'surfaces':records,'named_selections':named,'default_mesh_size_m':span/18,
            'source_format':Path(meta['file']).suffix.lower(),'warning':'STL 几何精度受原始三角面限制' if Path(meta['file']).suffix.lower()=='.stl' else None}
        write_json(folder/'geometry.json',evidence); write_json(folder/'display.json',display)
        return evidence

def direction_faces(records,direction):
    axis='xyz'.index(direction[-1]); sign=1 if direction[0]=='+' else -1
    candidates=[r for r in records if r['normal'][axis]*sign>.98]
    if not candidates: return []
    extreme=max(r['centroid_m'][axis]*sign for r in candidates)
    return [r['id'] for r in candidates if abs(r['centroid_m'][axis]*sign-extreme)<1e-7]

def resolve_selection(selection,evidence):
    q=selection.model_dump() if hasattr(selection,'model_dump') else selection
    records=evidence['surfaces']; valid={r['id'] for r in records}
    named=evidence['named_selections']
    if q.get('body_id') is not None:
        component=next((c for c in evidence.get('components',[]) if c['id']==q['body_id']),None)
        if component is None: raise ValueError('选区引用了未知组件')
        valid=set(component['face_ids']); records=[r for r in records if r['id'] in valid]
        named={**{k:[i for i in v if i in valid] for k,v in named.items()},**component['named_selections']}
    if q['kind']=='named':
        aliases={'上表面':'顶面','上面':'顶面','下表面':'底面','下面':'底面','安装孔孔壁':'安装孔','四个安装孔':'安装孔'}
        name=aliases.get(q.get('name'),q.get('name'))
        ids=named.get(name,[])
    elif q['kind']=='faces': ids=q.get('face_ids',[])
    elif q['kind']=='holes': ids=[r['id'] for r in records if r['is_hole']]
    elif q['kind']=='direction':
        if not q.get('direction'): raise ValueError('方向选区缺少明确的全局方向')
        ids=direction_faces(records,q['direction'])
    else:
        box=np.asarray(q.get('bounds_m'),dtype=float)
        if box.shape!=(2,3) or not np.isfinite(box).all() or np.any(box[1]<box[0]): raise ValueError('选区盒需要两个合法 XYZ 坐标')
        ids=[r['id'] for r in records if np.all(np.asarray(r['centroid_m'])>=box[0]-1e-9) and np.all(np.asarray(r['centroid_m'])<=box[1]+1e-9)]
    if not ids or not set(ids)<=valid: raise ValueError('选区无法唯一定位或为空：'+str(q)+ '。请指定已有命名选区或在三维视图选面。')
    return sorted(set(ids))

def validate_case(case,evidence):
    components=evidence.get('components',[])
    valid={str(c['id']) for c in components}
    if set(case.component_materials)-valid: raise ValueError('材料配置引用了其他模型的组件，请重新选择材料')
    if len(components)>1:
        missing=[c['name'] for c in components if str(c['id']) not in case.component_materials]
        if missing: raise ValueError('以下组件尚未指定材料：'+ '、'.join(missing))
    for load in case.loads:
        if load.body_id is not None and str(load.body_id) not in valid: raise ValueError('载荷引用了未知组件')
    mappings=[]
    for group in [*case.supports,*[l for l in case.loads if l.kind!='gravity']]:
        ids=resolve_selection(group.selection,evidence)
        mappings.append({'name':group.name,'face_ids':ids,'area_m2':sum(r['area_m2'] for r in evidence['surfaces'] if r['id'] in ids)})
    for s in case.mesh.local_selections: resolve_selection(s,evidence)
    if case.mesh.evaluation_selection: resolve_selection(case.mesh.evaluation_selection,evidence)
    for region in case.stress_regions:
        if str(region.selection.body_id) not in valid:
            raise ValueError('局部应力评估引用了未知组件')
        resolve_selection(region.selection,evidence)
    if case.assembly_mode=='bonded':
        from .bonding import validate_graph
        validate_graph(case,evidence)
    if case.assembly_mode=='gear_contact':
        cfg=case.gear_contact
        if {str(h.body_id) for h in cfg.hubs}!=valid:raise ValueError('每个组件均须定义一个轴孔')
        for h in cfg.hubs:
            if h.bore.body_id!=h.body_id:raise ValueError('轴孔选区必须属于对应齿轮')
            resolve_selection(h.bore,evidence)
    return mappings

def validate_execution(case,evidence):
    validate_case(case,evidence)
    if len(evidence.get('components',[]))>1 and case.assembly_mode not in ('independent','gear_contact','bonded'):
        raise ValueError('组合件连接尚未定义。可显式选择组件独立计算（互不传力）或定义理想粘结接口（CPU）；不能自动假定连接。组件配置已保留。')

def build_mesh(folder,case,destination,size_factor=1.,max_dofs=None):
    max_dofs=MAX_DOFS if max_dofs is None else max_dofs
    evidence=json.loads((folder/'geometry.json').read_text(encoding='utf-8'))
    spec={'source':evidence['source_sha256'],'size':case.mesh.size_m or evidence['default_mesh_size_m'],
        'local_size':case.mesh.local_size_m,'local_faces':sorted({i for s in case.mesh.local_selections for i in resolve_selection(s,evidence)}),
        'factor':size_factor,'gmsh':gmsh.__version__,'revision':4}
    if getattr(case.mesh,'regions',[]):spec['regions']=[r.model_dump() for r in case.mesh.regions]
    key=hashlib.sha256(json.dumps(spec,sort_keys=True).encode()).hexdigest()
    cache=folder/'meshes'/key[:24]; cache.mkdir(parents=True,exist_ok=True)
    if (cache/'info.json').exists() and (cache/'mesh.npz').exists():
        info=json.loads((cache/'info.json').read_text(encoding='utf-8'))
        if info['dofs']>max_dofs: raise ValueError('缓存网格超过当前自由度限制；未执行求解')
        if info.get('cache_key')==key and hashlib.sha256((cache/'mesh.npz').read_bytes()).hexdigest()==info['sha256']:
            shutil.copyfile(cache/'mesh.npz',destination)
            return {**info,'cache_reused':True}
    info=_generate_mesh(folder,case,destination,size_factor,max_dofs)
    info.update(sha256=hashlib.sha256(destination.read_bytes()).hexdigest(),cache_key=key,cache_reused=False)
    shutil.copyfile(destination,cache/'mesh.npz'); write_json(cache/'info.json',info)
    return info


def _generate_mesh(folder,case,destination,size_factor=1.,max_dofs=MAX_DOFS):
    evidence=json.loads((folder/'geometry.json').read_text(encoding='utf-8'))
    size=(case.mesh.size_m or evidence['default_mesh_size_m'])/size_factor
    minimum=(case.mesh.local_size_m/size_factor if case.mesh.local_size_m else size/2) if case.mesh.local_selections else size
    volume=evidence.get('volume_m3') or float(np.prod(evidence['dimensions_m']))
    estimated=60*volume/minimum**3
    if max_dofs<=MAX_DOFS and estimated>max_dofs*1.2: raise ValueError(f'网格资源预检：保守估计 {int(estimated):,} 自由度，超过本机配置限制 {max_dofs:,}；请调整网格尺寸或 STRENGTH_MAX_DOFS。未自动粗化。')
    if max_dofs>MAX_DOFS:
        # The old 60*V/h^3 bound overestimates thin, curved CAD by >2x.
        # Preflight mesher RAM separately; enforce the exact DOF cap below.
        from .cuda_runtime import host_available
        if estimated*2048>host_available()*.8:raise MemoryError('网格生成预计超过可用主机内存预算；未粗化网格')
    with session():
        load_geometry(folder)
        gmsh.option.setNumber('Mesh.MeshSizeMax',size)
        gmsh.option.setNumber('Mesh.MeshSizeMin',size/5)
        gmsh.option.setNumber('Mesh.MeshSizeFromCurvature',20)
        gmsh.option.setNumber('Mesh.ElementOrder',2)
        gmsh.option.setNumber('Mesh.SecondOrderLinear',0)
        gmsh.option.setNumber('Mesh.MeshSizeExtendFromBoundary',1)
        fields=[]
        if case.mesh.local_selections:
            ids=sorted({i for s in case.mesh.local_selections for i in resolve_selection(s,evidence)})
            distance=gmsh.model.mesh.field.add('Distance'); gmsh.model.mesh.field.setNumbers(distance,'FacesList',ids)
            field=gmsh.model.mesh.field.add('Threshold')
            for name,value in [('InField',distance),('SizeMin',minimum),('SizeMax',size),('DistMin',size),('DistMax',size*3)]: gmsh.model.mesh.field.setNumber(field,name,value)
            fields.append(field)
        regions=getattr(case.mesh,'regions',[])
        for region in regions:
            field=gmsh.model.mesh.field.add('Ball')
            for name,value in [('XCenter',region.center_m[0]),('YCenter',region.center_m[1]),('ZCenter',region.center_m[2]),
                ('Radius',region.radius_m),('Thickness',region.transition_m),('VIn',region.size_m/size_factor),('VOut',size)]:
                gmsh.model.mesh.field.setNumber(field,name,value)
            fields.append(field)
        if regions:gmsh.option.setNumber('Mesh.MeshSizeMin',min(size/5,min(r.size_m/size_factor for r in regions)))
        if fields:
            combined=gmsh.model.mesh.field.add('Min');gmsh.model.mesh.field.setNumbers(combined,'FieldsList',fields);gmsh.model.mesh.field.setAsBackgroundMesh(combined)
        try: gmsh.model.mesh.generate(3)
        except Exception:
            gmsh.model.mesh.clear(); gmsh.option.setNumber('Mesh.Optimize',1); gmsh.model.mesh.generate(3)
        # Reject an oversized mesh before the expensive high-order optimization.
        # This does not alter its requested mesh settings.
        preliminary_tags=gmsh.model.mesh.getNodes()[0]
        if len(preliminary_tags)*3>max_dofs:
            component_counts=[]
            for _,body in gmsh.model.getEntities(3):
                element_types,element_tags,node_tags=gmsh.model.mesh.getElements(3,body)
                count=len(np.unique(np.concatenate(node_tags))) if len(node_tags) else 0
                component_counts.append({'body_id':body,'nodes':count,'dofs':count*3,
                    'elements':sum(len(a) for a in element_tags)})
            write_json(destination.with_suffix('.preflight.json'),{'status':'resource_limit','nodes':len(preliminary_tags),
                'dofs':len(preliminary_tags)*3,'max_dofs':max_dofs,'requested_size_m':size,
                'components':component_counts,'mesh_changed_automatically':False})
            raise ValueError(f'网格 {len(preliminary_tags)*3:,} 自由度超过限制 {max_dofs:,}；未自动粗化。')
        # Optimization is a repair step, not a requirement for already valid
        # curved C3D10 elements. On large assemblies the unconditional barrier
        # optimizer can run for tens of minutes despite a valid starting mesh.
        gauss,_=gmsh.model.mesh.getIntegrationPoints(11,'Gauss5')
        _,initial_jac,_=gmsh.model.mesh.getJacobians(11,gauss)
        if not np.isfinite(initial_jac).all() or min(initial_jac)<=0:
            gmsh.model.mesh.optimize('HighOrder')
        tags,xyz,_=gmsh.model.mesh.getNodes(); points=np.asarray(xyz).reshape(-1,3)
        if len(points)*3>max_dofs: raise ValueError(f'网格 {len(points)*3:,} 自由度超过限制 {max_dofs:,}；请自行调整网格或 STRENGTH_MAX_DOFS。未自动粗化。')
        lut={int(t):i for i,t in enumerate(tags)}
        types,elem_tags,nodes=gmsh.model.mesh.getElements(3)
        tets=[]; tet_tags=[]
        for ty,et,nd in zip(types,elem_tags,nodes):
            if ty!=11: raise ValueError('求解只接受 C3D10 十节点四面体')
            # Gmsh swaps edge (2,4) and edge (3,4) relative to Abaqus/CalculiX.
            order=[0,1,2,3,4,5,6,7,9,8]
            tets.extend([[lut[int(t)] for t in row[order]] for row in np.asarray(nd).reshape(-1,10)])
            tet_tags.extend(et)
        if not tets: raise ValueError('无法生成体网格')
        tets=np.asarray(tets,dtype=np.int64)
        tag_body={}
        for _,body in gmsh.model.getEntities(3):
            for tags_for_type in gmsh.model.mesh.getElements(3,body)[1]:
                tag_body.update({int(tag):body for tag in tags_for_type})
        body_ids=np.array([tag_body[int(tag)] for tag in tet_tags],np.int64)
        gauss,_=gmsh.model.mesh.getIntegrationPoints(11,'Gauss5')
        _,jac,_=gmsh.model.mesh.getJacobians(11,gauss)
        if not np.isfinite(jac).all() or min(jac)<=0: raise ValueError('二次网格含非正 Jacobian，请调整局部网格尺寸')
        sf=[]; surface_ids=[]
        for _,s in gmsh.model.getEntities(2):
            types,_,nd=gmsh.model.mesh.getElements(2,s)
            for ty,n in zip(types,nd):
                if ty!=9: raise ValueError('表面网格必须为六节点三角形')
                sf.extend([[lut[int(x)] for x in row] for row in np.asarray(n).reshape(-1,6)])
                surface_ids.extend([s]*(len(n)//6))
        sf=np.asarray(sf,dtype=np.int64)
        face_map={}
        for e,t in enumerate(tets):
            for local in ((0,1,2),(0,3,1),(1,3,2),(2,3,0)): face_map.setdefault(tuple(sorted(t[list(local)])),[]).append(e)
        records=[(face,sid,e) for face,sid in zip(sf,surface_ids) for e in face_map[tuple(sorted(face[:3]))]]
        sf=np.array([r[0] for r in records]); surface_ids=np.array([r[1] for r in records]); owners=np.array([r[2] for r in records])
        # Independent components never share a displacement DOF, even if their
        # source CAD has a common topological face. No implicit bonded contact.
        if len(np.unique(body_ids))>1:
            new_points=[];new_tets=np.empty_like(tets);new_faces=np.empty_like(sf)
            for body in np.unique(body_ids):
                elems=body_ids==body;nodes=np.unique(tets[elems]);mapping=np.full(len(points),-1,int)
                mapping[nodes]=np.arange(len(new_points),len(new_points)+len(nodes))
                new_points.extend(points[nodes]);new_tets[elems]=mapping[tets[elems]]
                face_mask=body_ids[owners]==body;new_faces[face_mask]=mapping[sf[face_mask]]
            points=np.array(new_points);tets=new_tets;sf=new_faces
        if points.size>max_dofs: raise ValueError('按独立组件划分后的网格超过自由度限制')
        np.savez_compressed(destination,points=points,tets=tets,faces=sf,surface_ids=np.asarray(surface_ids),owners=owners,
            body_ids=body_ids,face_body_ids=body_ids[owners],size_m=size,min_jacobian=float(min(jac)))
        return {'nodes':len(points),'elements':len(tets),'dofs':len(points)*3,'size_m':size,'min_jacobian':float(min(jac)), 'gmsh_version':gmsh.__version__}
