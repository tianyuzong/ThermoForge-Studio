"""Read-only host geometry evidence and consistent semantic surface normals."""
from functools import lru_cache
from collections import deque
import hashlib
import numpy as np
import trimesh
from thermal_legacy.runtime import read_json


def orient_faces(points, faces, outer=None):
    """Orient each closed shell without changing vertices or face indices."""
    faces=np.asarray(faces,dtype=np.int64).copy()
    edges=np.concatenate([faces[:,[0,1]],faces[:,[1,2]],faces[:,[2,0]]])
    owners=np.tile(np.arange(len(faces)),3)
    ordered=np.sort(edges,axis=1)
    order=np.lexsort((ordered[:,1],ordered[:,0]));ordered=ordered[order]
    starts=np.r_[0,np.flatnonzero(np.any(ordered[1:]!=ordered[:-1],axis=1))+1,len(order)]
    counts=np.diff(starts)
    evidence=dict(closed=bool(np.all(counts==2)),boundary_edges=int(np.sum(counts==1)),non_manifold_edges=int(np.sum(counts>2)))
    if not evidence['closed']:
        return faces,{**evidence,'orientation_verified':False,'reoriented_faces':0}
    pairs=order[starts[:-1,None]+np.array([0,1])]
    adjacency=[[] for _ in faces]
    for a,b in pairs:
        same=bool(edges[a,0]==edges[b,0])
        i,j=int(owners[a]),int(owners[b]);adjacency[i].append((j,same));adjacency[j].append((i,same))
    flips=np.full(len(faces),-1,np.int8);components=[]
    for seed in range(len(faces)):
        if flips[seed]>=0:continue
        todo=deque([seed]);flips[seed]=0;component=[]
        while todo:
            i=todo.popleft();component.append(i)
            for j,same in adjacency[i]:
                expected=int(flips[i])^int(same)
                if flips[j]<0:flips[j]=expected;todo.append(j)
                elif flips[j]!=expected:raise ValueError('表面不可一致定向')
        components.append(component)
    faces[flips==1]=faces[flips==1][:,[0,2,1]]
    for ids in components:
        tri=np.asarray(points)[faces[ids]];tri=tri-tri.mean(axis=(0,1))
        volume=np.einsum('ij,ij->i',tri[:,0],np.cross(tri[:,1],tri[:,2])).sum()/6
        wanted=1 if outer is None or np.mean(np.asarray(outer)[ids])>=.5 else -1
        if volume*wanted<0:
            faces[ids]=faces[ids][:,[0,2,1]];flips[ids]^=1
    return faces,{**evidence,'orientation_verified':True,'reoriented_faces':int(flips.sum()),'shells':len(components)}


@lru_cache(maxsize=6)
def _surface(path, stamp):
    from pathlib import Path
    folder=Path(path);d=read_json(folder/'display.json')
    points=np.asarray(d['points'],float).reshape(-1,3);faces=np.asarray(d['faces'],np.int64).reshape(-1,3)
    outer=np.asarray(d.get('is_outer',np.ones(len(faces))),bool)
    fixed,evidence=orient_faces(points,faces,outer)
    return points,fixed,outer,evidence


def surface(folder):
    return _surface(str(folder), (folder/'display.json').stat().st_mtime_ns)


def model_evidence(folder):
    meta=read_json(folder/'metadata.json');points,faces,outer,topology=surface(folder)
    source=folder/meta.get('source','__missing__')
    checksum=hashlib.sha256(source.read_bytes()).hexdigest() if source.is_file() else None
    mesh=trimesh.Trimesh(points,faces,process=False)
    return dict(model_id=folder.name,name=meta.get('name'),kind=meta.get('kind'),units=meta.get('units'),
                scale_factor=meta.get('scale_factor',1),dimensions_m=np.ptp(points,axis=0).tolist(),
                bounds_m=mesh.bounds.tolist(),source_sha256=checksum,source_identity='当前已导入文件；不自动断言其他路径文件等价',
                surface_volume_m3=float(mesh.volume),topology=topology,
                normal_policy='在内存副本中一致定向，保留三角面编号和几何坐标；不改写已导入文件')


def capabilities():
    return dict(single_simulation=True,workflow=True,
        steady_heat_window='稳态热源时间窗包含两端：start_s <= duration_s <= end_s；0到600秒的热源在600秒稳态标签仍有效，不得无故延长到610秒',
        continuation='已完成父结果的完整节点温度场；同模型、材料、网格校验',
        output_times='常规保存时刻、终止时刻、ambient_profile全部转折点的并集',
        surface_metrics='裁剪面积积分；面积加权拟合变形平面，去平移倾斜后法向残差峰谷差',
        same_position_cycle=dict(
            available=True,owner='多工况宿主后处理 workflow_report.cycle_range；不需要Simulation新增输出字段',
            scope='单一均匀各向同性线弹性材料，同一网格单元、固定全局XYZ坐标系，仅比较已保存帧',
            stress='同一单元任意两保存帧的应力张量相减，再计算差张量的von Mises等效值；报告其中最大值、单元形心坐标和两帧时刻',
            elastic_strain='与上述应力张量差对应的等效偏弹性应变范围：2*(1+nu)/(3*E)*应力差等效值，属于机械弹性应变范围',
            total_strain='另报告总正应变范围：机械弹性正应变加alpha*(单元平均温度-reference_C)，逐单元、逐固定XYZ方向取保存帧最大值减最小值；报告最大范围、方向和单元形心坐标',
            default_definition='用户仅要求同位置应力应变范围时，同时提供上述机械弹性范围和总正应变范围，并在报告注明定义，不要求用户重复确认现有后处理能力',
            resource_limit='保存帧不超过120且帧数平方乘单元数不超过2e9；超过预算或材料模型不适用时标记未完成',
            exclusions='不代表连续时间峰值，不计算疲劳寿命，不以不同位置的全局极值相减'),
        peak_scope='全部保存帧，不代表连续时间峰值',
        reports=['CSV','原始三维场','中文PDF','多工况汇总'],
        unsupported=['CFD风场','塑性','蠕变','疲劳寿命','螺栓预紧与机械接触'])
