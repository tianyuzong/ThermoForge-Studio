"""Read-only previews rendered from the imported CAD tessellation, never results."""
import hashlib,io,json
from functools import lru_cache
from pathlib import Path
import numpy as np
from PIL import Image,ImageDraw,PngImagePlugin
from fastapi import APIRouter,HTTPException,Request,Response
from strength import store
from strength.config import DATA
from .integrity import file_digest

router=APIRouter(prefix='/api/v1')
WIDTH,HEIGHT=192,128
RENDER_VERSION='cad-isometric-1'


def project_mesh(points,width=WIDTH,height=HEIGHT,padding=10):
    """Use an orthographic camera with uniform scale and complete model bounds."""
    points=np.asarray(points,dtype=float)
    if points.ndim!=2 or points.shape[1]!=3 or len(points)<3 or not np.isfinite(points).all():raise ValueError('CAD 显示坐标无效')
    centered=points-(points.min(axis=0)+points.max(axis=0))/2
    view=np.array([1.4,-1.8,1.2]);view/=np.linalg.norm(view)
    right=np.cross([0.,0.,1.],view);right/=np.linalg.norm(right);up=np.cross(view,right)
    xy=np.column_stack((centered@right,-centered@up));lo=xy.min(axis=0);hi=xy.max(axis=0);span=hi-lo
    if span.max()<=1e-15:raise ValueError('CAD 几何没有可显示尺寸')
    scale=min((width-2*padding)/max(span[0],1e-15),(height-2*padding)/max(span[1],1e-15))
    xy=(xy-(lo+hi)/2)*scale+[width/2,height/2]
    return xy,centered@view,view


@lru_cache(maxsize=64)
def render_thumbnail(path,source_hash,display_hash,version=RENDER_VERSION):
    raw=Path(path).read_bytes()
    if hashlib.sha256(raw).hexdigest()!=display_hash:raise ValueError('读取缩略图期间 CAD 显示数据变化')
    display=json.loads(raw)
    points=np.asarray(display.get('points'),dtype=float);indices=np.asarray(display.get('triangles'))
    xy,depth,view=project_mesh(points)
    if indices.ndim!=2 or indices.shape[1]!=3 or not len(indices) or indices.dtype.kind not in 'iu':raise ValueError('CAD 三角面索引无效')
    if indices.min()<0 or indices.max()>=len(points):raise ValueError('CAD 三角面超出节点范围')
    faces=points[indices];normals=np.cross(faces[:,1]-faces[:,0],faces[:,2]-faces[:,0]);area=np.linalg.norm(normals,axis=1)
    valid=area>max(float(area.max())*1e-14,1e-30)
    if not valid.any():raise ValueError('CAD 三角面全部退化')
    selected=np.flatnonzero(valid);selected=selected[np.argsort(depth[indices[selected]].mean(axis=1),kind='stable')]
    normals=np.divide(normals,area[:,None],out=np.zeros_like(normals),where=area[:,None]>0)
    light=np.array([-.4,-.6,1.]);light/=np.linalg.norm(light)
    illumination=.48+.52*np.abs(normals@light)
    pixels=xy*2
    image=Image.new('RGB',(WIDTH*2,HEIGHT*2),(240,246,247));draw=ImageDraw.Draw(image)
    # Render the actual mesh, including small components. No facet sampling that
    # could erase a thin shaft or leave holes in the model silhouette.
    base=np.array([108.,151.,162.])
    for index in selected:
        color=tuple(np.clip(base*illumination[index]+32,0,255).astype(int))
        draw.polygon([tuple(p) for p in pixels[indices[index]]],fill=color)
    image=image.resize((WIDTH,HEIGHT),Image.Resampling.LANCZOS)
    metadata=PngImagePlugin.PngInfo();metadata.add_text('SourceCADSHA256',source_hash);metadata.add_text('DisplaySHA256',display_hash)
    metadata.add_text('Description','Orthographic preview of imported CAD triangles; not a simulation result')
    output=io.BytesIO();image.save(output,format='PNG',pnginfo=metadata,optimize=True)
    return output.getvalue()


@router.get('/projects/{id}/thumbnail')
def thumbnail(id:str,request:Request):
    try:
        project=store.get('project',id);mid=project.get('model_id')
        if not mid:raise ValueError('项目尚未导入 CAD')
        models=(DATA/'models').resolve();folder=(models/mid).resolve()
        if not folder.is_relative_to(models) or folder==models:raise ValueError('模型目录无效')
        model=store.get('model',mid);source=json.loads((folder/'source.json').read_text(encoding='utf-8'))
        cad=(folder/source['file']).resolve()
        if not cad.is_relative_to(folder):raise ValueError('模型源文件路径无效')
        source_hash=file_digest(cad)
        if source_hash!=model.get('source_sha256'):raise ValueError('模型源文件校验不匹配，请重新导入')
        display=folder/'display.json';display_hash=file_digest(display)
        key=hashlib.sha256((RENDER_VERSION+source_hash+display_hash).encode()).hexdigest();etag='"'+key+'"'
        headers={'ETag':etag,'Cache-Control':'private, max-age=0, must-revalidate','X-Content-Type-Options':'nosniff'}
        if request.headers.get('if-none-match')==etag:return Response(status_code=304,headers=headers)
        payload=render_thumbnail(str(display),source_hash,display_hash)
        return Response(payload,media_type='image/png',headers=headers)
    except (KeyError,OSError,ValueError,TypeError) as exc:
        raise HTTPException(404,'没有可用的真实 CAD 缩略图：'+str(exc)) from exc
