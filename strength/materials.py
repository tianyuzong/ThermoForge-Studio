"""Material values resolved by element, with no assembly-wide implicit assignment."""
import numpy as np

def bodies(mesh):
    return mesh.get('body_ids',np.ones(len(mesh['tets']),dtype=np.int64))

def material_for(case,body):
    return case.component_materials.get(str(int(body)),case.material)

def property_array(mesh,case,name,missing=np.nan):
    ids=bodies(mesh);out=np.empty(len(ids))
    for body in np.unique(ids):
        value=getattr(material_for(case,body),name)
        out[ids==body]=missing if value is None else value
    return out

def selection_mask(mesh,selection,evidence):
    from .geometry import resolve_selection
    mask=np.isin(mesh['surface_ids'],resolve_selection(selection,evidence))
    if selection.body_id is not None and 'face_body_ids' in mesh:
        mask &= mesh['face_body_ids']==selection.body_id
    return mask
