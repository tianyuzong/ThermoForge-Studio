from thermal_legacy.runtime import *
import math, hashlib
import numpy as np
import gmsh, trimesh
from strength.gmsh_lock import GMSH_LOCK

SCALES={'mm':.001,'cm':.01,'m':1.}

def inspect_stl_quality(mesh):
    """Return topology and surface-quality diagnostics for an STL mesh.

    Trimesh has useful boolean properties, but exposing the counts makes an
    import failure actionable (for example, distinguishing a four-edge hole
    from a non-manifold junction).  This function intentionally does not
    mutate the mesh; callers may still run the existing cleanup pipeline.
    """
    faces = np.asarray(mesh.faces)
    vertices = np.asarray(mesh.vertices)
    face_count = int(len(faces))
    vertex_count = int(len(vertices))
    if not face_count or not vertex_count:
        return {
            'status': 'error', 'vertices': vertex_count, 'triangles': face_count,
            'watertight': False, 'winding_consistent': False,
            'boundary_edges': 0, 'non_manifold_edges': 0,
            'degenerate_triangles': face_count, 'invalid_normals': 0,
            'inverted_components': 0, 'connected_components': 0,
            'warnings': ['STL 不包含有效顶点或三角形'],
        }

    try:
        areas = np.asarray(mesh.area_faces, dtype=float)
    except Exception:
        areas = np.zeros(face_count, dtype=float)
    scale = max(float(np.ptp(vertices, axis=0).max()), 1e-12)
    degenerate = int(np.count_nonzero(~np.isfinite(areas) | (areas <= scale * scale * 1e-14)))

    invalid_normals = 0
    try:
        normals = np.asarray(mesh.face_normals, dtype=float)
        invalid_normals = int(np.count_nonzero(~np.isfinite(normals).all(axis=1) |
                                                 (np.linalg.norm(normals, axis=1) <= 1e-12)))
    except Exception:
        invalid_normals = face_count

    boundary_edges = 0
    non_manifold_edges = 0
    try:
        edge_counts = np.bincount(np.asarray(mesh.edges_unique_inverse, dtype=np.int64))
        boundary_edges = int(np.count_nonzero(edge_counts == 1))
        non_manifold_edges = int(np.count_nonzero(edge_counts > 2))
    except Exception:
        # Keep the import diagnostic useful even for a malformed mesh object.
        boundary_edges = -1
        non_manifold_edges = -1

    try:
        winding_consistent = bool(mesh.is_winding_consistent)
    except Exception:
        winding_consistent = False
    try:
        watertight = bool(mesh.is_watertight)
    except Exception:
        watertight = False
    try:
        components = trimesh.graph.connected_components(mesh.face_adjacency, nodes=np.arange(face_count), min_len=1)
        component_count = int(len(components))
        inverted_components = 0
        for ids in components:
            part = mesh.submesh([ids], append=True, repair=False)
            if part.is_watertight and float(part.volume) < 0:
                inverted_components += 1
    except Exception:
        component_count = 0
        inverted_components = 0

    warnings = []
    if boundary_edges > 0:
        warnings.append(f'存在 {boundary_edges} 条开口边界（可能有孔洞或未封口）')
    if non_manifold_edges > 0:
        warnings.append(f'存在 {non_manifold_edges} 条非流形边')
    if degenerate > 0:
        warnings.append(f'存在 {degenerate} 个退化三角形')
    if invalid_normals > 0:
        warnings.append(f'存在 {invalid_normals} 个无效面法向')
    if not winding_consistent:
        warnings.append('面法向方向不一致（可能包含反法向）')
    if inverted_components > 0:
        warnings.append(f'存在 {inverted_components} 个反向封闭组件')
    fatal = (not watertight) or non_manifold_edges > 0 or invalid_normals > 0
    return {
        'status': 'error' if fatal else ('warning' if warnings else 'ok'),
        'vertices': vertex_count, 'triangles': face_count,
        'watertight': watertight, 'winding_consistent': winding_consistent,
        'boundary_edges': boundary_edges, 'non_manifold_edges': non_manifold_edges,
        'degenerate_triangles': degenerate, 'invalid_normals': invalid_normals,
        'inverted_components': inverted_components, 'connected_components': component_count,
        'warnings': warnings,
    }

def init_gmsh():
    GMSH_LOCK.acquire()
    try:gmsh.initialize(interruptible=False)
    except BaseException:GMSH_LOCK.release();raise
    gmsh.option.setNumber('General.Terminal',1)
    gmsh.option.setNumber('General.Verbosity',2)
    gmsh.option.setNumber('General.NumThreads',4)
    gmsh.option.setNumber('Mesh.MaxNumThreads3D',4)
    gmsh.option.setString('Geometry.OCCTargetUnit','M')
    gmsh.model.add('thermal_part')


def finalize_gmsh():
    try:gmsh.finalize()
    finally:GMSH_LOCK.release()

def import_step(folder, meta, fragment_bodies=False):
    gmsh.model.occ.importShapes(str(folder/meta['source']))
    gmsh.model.occ.synchronize()
    volumes=gmsh.model.getEntities(3)
    if not volumes: raise ValueError('STEP 没有封闭实体，无法生成体积网格。请导出实体 STEP。')
    scale=meta.get('step_scale',1)
    if scale!=1: gmsh.model.occ.dilate(volumes,0,0,0,scale,scale,scale)
    # Fragmenting every imported body can destroy small or self-intersecting
    # CAD edges. Preserve the source topology by default; region boxes are
    # fragmented explicitly later when a material partition is requested.
    if fragment_bodies and len(volumes)>1: gmsh.model.occ.fragment(volumes[:1],volumes[1:])
    gmsh.model.occ.synchronize()
    return gmsh.model.getEntities(3)

def shell_tags(volumes):
    outer=set()
    for _,v in volumes:
        _,loops=gmsh.model.occ.getSurfaceLoops(v)
        if not len(loops): continue
        sizes=[sum(gmsh.model.occ.getMass(2,int(s)) for s in loop) for loop in loops]
        outer.update(int(s) for s in loops[int(np.argmax(sizes))])
    return outer

def extract_surface(outer):
    tags,xyz,_=gmsh.model.mesh.getNodes()
    lookup=np.full(int(max(tags))+1,-1,dtype=np.int64);lookup[tags]=np.arange(len(tags))
    faces=[];flags=[]
    orientations={abs(s):1 if s>0 else -1 for _,s in gmsh.model.getBoundary(gmsh.model.getEntities(3),combined=True,oriented=True)}
    for _,surface in gmsh.model.getEntities(2):
        types,_,nodes=gmsh.model.mesh.getElements(2,surface)
        for kind,ns in zip(types,nodes):
            if kind==2:
                f=lookup[ns].reshape(-1,3)
                if orientations.get(surface,1)<0:f=f[:,[0,2,1]]
                faces.append(f);flags.extend([surface in outer]*len(f))
    return np.asarray(xyz).reshape(-1,3),np.vstack(faces),np.asarray(flags)

def write_display(folder, points, faces, outer):
    used=np.unique(faces);remap=np.full(len(points),-1,dtype=np.int64);remap[used]=np.arange(len(used))
    points=points[used];faces=remap[faces]
    np.savez_compressed(folder/'display.npz',points=points,faces=faces,is_outer=outer)
    write_json(folder/'display.json',{'points':points.astype(np.float32).ravel().tolist(),'faces':faces.ravel().tolist(),'is_outer':outer.astype(int).tolist()})
    return points,faces

def load_stl(folder, meta):
    mesh=trimesh.load(folder/meta['source'],force='mesh',process=True)
    quality=inspect_stl_quality(mesh)
    mesh.update_faces(mesh.nondegenerate_faces());mesh.remove_unreferenced_vertices();mesh.merge_vertices()
    mesh.vertices*=SCALES[meta['units']]*meta.get('scale_factor',1.)
    if not len(mesh.faces): raise ValueError('STL 中没有有效三角形')
    components=trimesh.graph.connected_components(mesh.face_adjacency,nodes=np.arange(len(mesh.faces)),min_len=1)
    shells=[mesh.submesh([ids],append=True,repair=False) for ids in components]
    watertight=all(s.is_watertight for s in shells)
    depth=np.zeros(len(shells),dtype=int);parents=np.full(len(shells),-1,dtype=int)
    if watertight:
        for i,s in enumerate(shells):
            containers=[j for j,t in enumerate(shells) if i!=j and t.contains(s.vertices[:1])[0]]
            depth[i]=len(containers)
            if containers: parents[i]=min(containers,key=lambda j:abs(shells[j].volume))
    outer=np.zeros(len(mesh.faces),dtype=bool);shell_id=np.zeros(len(mesh.faces),dtype=int)
    for i,ids in enumerate(components): outer[ids]=depth[i]%2==0;shell_id[ids]=i
    mesh.export(folder/'clean.stl')
    np.savez_compressed(folder/'shells.npz',shell_id=shell_id,depth=depth,parents=parents)
    components_meta=[]
    for i,ids in enumerate(components):
        bounds=np.asarray([mesh.vertices[mesh.faces[ids]].min(axis=(0,1)),mesh.vertices[mesh.faces[ids]].max(axis=(0,1))])
        components_meta.append(dict(component_id=i,name=f'组件 {i+1}',triangles=int(len(ids)),bounds_m=bounds.tolist(),closed=bool(shells[i].is_watertight)))
    meta['components']=components_meta
    meta['geometry_quality']=quality
    return mesh.vertices,np.asarray(mesh.faces),outer,watertight

def prepare_model(folder):
    meta=read_json(folder/'metadata.json');progress(folder,'running',10,'正在读取几何')
    if meta['kind']=='stl':
        points,faces,outer,valid=load_stl(folder,meta)
        meta['mesh_ready']=valid
        quality=meta.get('geometry_quality',{})
        quality_warnings=quality.get('warnings',[])
        if valid:
            meta['warning']='；'.join(quality_warnings)
        else:
            detail='；'.join(quality_warnings) or '存在开口或非流形结构'
            meta['warning']=f'STL 几何质量检查未通过：{detail}。可预览；运行前请修复或使用对应 STEP。'
    else:
        init_gmsh()
        try:
            volumes=import_step(folder,meta);outer_tags=shell_tags(volumes)
            meta['components']=[dict(component_id=i,name=f'组件 {i+1}',cad_volume=int(v)) for i,(_,v) in enumerate(volumes)]
            bbox=np.asarray(gmsh.model.getBoundingBox(-1,-1));size=float(max(bbox[3:]-bbox[:3]))/30
            gmsh.option.setNumber('Mesh.MeshSizeMax',size)
            gmsh.option.setNumber('Mesh.MeshSizeMin',size*.15)
            gmsh.option.setNumber('Mesh.MeshSizeFromCurvature',20)
            progress(folder,'running',40,'正在生成可选取的三角形表面')
            gmsh.model.mesh.generate(2)
            points,faces,outer=extract_surface(outer_tags)
            meta['mesh_ready']=True;meta['warning']=''
        except Exception as error:
            meta.update(state='failed',mesh_ready=False,warning=f'STEP 表面网格失败：{error}')
            write_json(folder/'metadata.json',meta)
            raise
        finally:finalize_gmsh()
    points,faces=write_display(folder,points,faces,outer)
    bounds=np.array([points.min(0),points.max(0)])
    meta.update(bounds_m=bounds.tolist(),dimensions_m=(bounds[1]-bounds[0]).tolist(),vertices=len(points),triangles=len(faces),cavity_triangles=int((~outer).sum()),state='ready')
    write_json(folder/'metadata.json',meta);progress(folder,'completed',100,'几何已就绪')

def classify_stl_volume(folder):
    display=np.load(folder/'display.npz');shells=np.load(folder/'shells.npz')
    original=trimesh.Trimesh(vertices=display['points'],faces=display['faces'],process=False)
    gmsh.merge(str(folder/'clean.stl'))
    # Keep the STL as discrete topology. Parametrizing a complex, but closed,
    # triangle shell can fail on high-genus or multi-component meshes.
    gmsh.model.mesh.createTopology(exportDiscrete=True)
    shell_surfaces={i:[] for i in range(len(shells['depth']))}
    for _,surface in gmsh.model.getEntities(2):
        types,_,elems=gmsh.model.mesh.getElements(2,surface)
        element_nodes=next(ns for ty,ns in zip(types,elems) if ty==2)
        nodes=element_nodes[:3]
        center=np.mean([gmsh.model.mesh.getNode(int(n))[0] for n in nodes],axis=0)
        _,_,idx=trimesh.proximity.closest_point(original,center[None,:])
        shell_surfaces[int(shells['shell_id'][idx[0]])].append(surface)
    loops={i:gmsh.model.geo.addSurfaceLoop(ss) for i,ss in shell_surfaces.items() if ss}
    for i,depth in enumerate(shells['depth']):
        if depth%2==0:
            children=[loops[j] for j,parent in enumerate(shells['parents']) if parent==i and shells['depth'][j]==depth+1]
            gmsh.model.geo.addVolume([loops[i]]+children)
    gmsh.model.geo.synchronize()
    return {s for i,ss in shell_surfaces.items() if shells['depth'][i]%2==0 for s in ss}

def build_mesh(folder, cfg, job):
    meta=read_json(folder/'metadata.json')
    if not meta.get('mesh_ready'):raise ValueError(meta.get('warning','几何不封闭'))
    size=cfg['mesh_size_m'];span=max(meta['dimensions_m'])
    if size < span/130:raise ValueError('网格过细：首版单次目标尺寸不得小于最长边的 1/130。')
    # The approved portrait ships with a checked mesh for a quick first run.
    cached=folder/'checked_mesh.npz'
    if cached.exists() and not cfg['regions'] and abs(size-.035)<1e-10:
        return dict(np.load(cached))
    init_gmsh()
    try:
        if meta['kind']=='step':
            volumes=import_step(folder,meta)
            if cfg['regions']:
                tools=[]
                for r in cfg['regions']:
                    low=np.asarray(r['min_m']);extent=np.asarray(r['max_m'])-low
                    tools.append((3,gmsh.model.occ.addBox(*low,*extent)))
                fragments,maps=gmsh.model.occ.fragment(volumes,tools)
                keep={tuple(e) for group in maps[:len(volumes)] for e in group if e[0]==3}
                discard=[v for v in fragments if v[0]==3 and tuple(v) not in keep]
                if discard:gmsh.model.occ.remove(discard,recursive=True)
                gmsh.model.occ.synchronize();volumes=gmsh.model.getEntities(3)
            outer=shell_tags(volumes)
        else:outer=classify_stl_volume(folder)
        gmsh.option.setNumber('Mesh.MeshSizeMin',size*.12)
        gmsh.option.setNumber('Mesh.MeshSizeMax',size)
        gmsh.option.setNumber('Mesh.MeshSizeFromCurvature',16)
        gmsh.option.setNumber('Mesh.Algorithm',6)
        gmsh.option.setNumber('Mesh.Algorithm3D',1)
        progress(job,'running',20,'正在生成四面体网格')
        gmsh.model.mesh.generate(3)
        # Separate STEP solids may share exact coordinates without sharing CAD
        # topology. Merge those nodes so heat can cross a touching interface.
        try: gmsh.model.mesh.removeDuplicateNodes()
        except Exception: pass
        # Gmsh's native tetrahedral optimizer avoids the Windows Netgen DLL
        # access violation observed on the validated heatsink STL.
        gmsh.model.mesh.optimize('')
        tags,xyz,_=gmsh.model.mesh.getNodes();points=np.asarray(xyz).reshape(-1,3)
        lookup=np.full(int(max(tags))+1,-1,dtype=np.int64);lookup[tags]=np.arange(len(tags))
        types,ids,nodes=gmsh.model.mesh.getElements(3)
        tets=np.vstack([lookup[ns].reshape(-1,4) for ty,ns in zip(types,nodes) if ty==4])
        if len(points)>180000 or len(tets)>1000000:raise ValueError('网格超过本地首版规模上限，请增大网格尺寸。')
        # Select only true exterior/void boundary entities of the combined solid.
        surfaces=gmsh.model.getBoundary(gmsh.model.getEntities(3),combined=True,oriented=False)
        faces=[];flags=[]
        for _,s in surfaces:
            ty,_,ns=gmsh.model.mesh.getElements(2,s)
            for kind,n in zip(ty,ns):
                if kind==2:
                    f=lookup[n].reshape(-1,3);faces.append(f);flags.extend([s in outer]*len(f))
        faces=np.vstack(faces)
        used=np.unique(tets);remap=np.full(len(points),-1,dtype=np.int64);remap[used]=np.arange(len(used))
        points=points[used];tets=remap[tets];faces=remap[faces]
        if (faces<0).any():raise ValueError('表面和体积网格不匹配，请调整网格尺寸。')
        vol=np.abs(np.linalg.det(points[tets[:,1:]]-points[tets[:,0]][:,None,:]))/6
        if not np.all(vol>0):raise ValueError('网格存在零体积单元')
        quality=np.concatenate([gmsh.model.mesh.getElementQualities(ii,'minSICN') for ty,ii in zip(types,ids) if ty==4])
        thermal_components=connected_tet_components(tets,points)
        component_id=thermal_components
        records=meta.get('components') or []
        if records:
            # Keep the IDs exposed by the model metadata stable when Gmsh
            # merges tiny touching shells during volume meshing.
            for thermal in np.unique(thermal_components):
                cells=thermal_components==thermal;box=np.asarray([points[tets[cells]].min(axis=(0,1)),points[tets[cells]].max(axis=(0,1))])
                def mismatch(record):
                    bounds=np.asarray(record.get('bounds_m',box));center_distance=np.linalg.norm(bounds.mean(0)-box.mean(0));size_distance=np.linalg.norm((bounds[1]-bounds[0])-(box[1]-box[0]))
                    return float(center_distance+0.35*size_distance)
                chosen=min(records,key=mismatch);component_id[cells]=int(chosen['component_id'])
        result=dict(points=points,tets=tets,boundary_triangles=faces,is_outer=np.asarray(flags),tet_volumes=vol,minimum_quality=np.array(quality.min()),component_id=component_id)
        np.savez_compressed(job/'mesh.npz',**result)
        return result
    finally:finalize_gmsh()

def project(mesh, points, batch=4000):
    nearest=[];dist=[];ids=[]
    for start in range(0,len(points),batch):
        c,d,i=trimesh.proximity.closest_point(mesh,points[start:start+batch]);nearest.append(c);dist.append(d);ids.append(i)
    return np.vstack(nearest),np.concatenate(dist),np.concatenate(ids)

def connected_tet_components(tets, points):
    """Label tetrahedra that share a node; labels are ordered bottom to top."""
    parent=np.arange(len(tets),dtype=np.int32)
    def find(value):
        while parent[value]!=value:
            parent[value]=parent[parent[value]];value=parent[value]
        return value
    owners={}
    for i,tet in enumerate(tets):
        for node in tet:
            other=owners.get(int(node))
            if other is not None:
                a,b=find(i),find(other)
                if a!=b:parent[b]=a
            owners[int(node)]=i
    roots=np.asarray([find(i) for i in range(len(tets))],dtype=np.int32)
    centers=points[tets].mean(axis=1);unique=np.unique(roots)
    order=sorted(unique.tolist(),key=lambda root:(float(centers[roots==root,2].min()),float(centers[roots==root,0].min())))
    remap={root:i for i,root in enumerate(order)}
    return np.asarray([remap[int(root)] for root in roots],dtype=np.int32)
