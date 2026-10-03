"""Spatial patches, continuation and surface engineering metrics."""
import re
import numpy as np

NUMBER=r'[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?'
BOX=re.compile(r'选区盒\s*[（(]\s*('+NUMBER+r'(?:\s*[,，]\s*'+NUMBER+r'){5})\s*[)）]\s*(mm|cm|m|毫米|厘米|米)',re.I)


def box_from_text(text):
    match=BOX.search(text)
    if not match:return None
    a=[float(x) for x in re.split('[,，]',match[1])];scale={'mm':.001,'毫米':.001,'cm':.01,'厘米':.01,'m':1,'米':1}[match[2].lower()]
    return {'min_m':[a[i]*scale for i in (0,2,4)],'max_m':[a[i]*scale for i in (1,3,5)]}


def box_token(box):
    return 'box:'+','.join(f'{v:.12g}' for pair in zip(box['min_m'],box['max_m']) for v in pair)


def token_box(token):
    if not token.startswith('box:'):return None
    values=[float(v) for v in token[4:].split(',')]
    if len(values)!=6:raise ValueError('选区盒需要6个坐标')
    return {'min_m':values[::2],'max_m':values[1::2]}


def box_inside(points,box):
    if not box:return np.ones(np.shape(points)[:-1],bool)
    eps=max(float(np.max(np.abs(points)))*1e-7,1e-10)
    return np.all((points>=np.array(box['min_m'])-eps)&(points<=np.array(box['max_m'])+eps),axis=-1)


def clipped_quadrature(triangles,box):
    """Integrate P1 functions exactly on an axis-aligned clipped triangle.

    Returns parent triangle, barycentric quadrature coordinates and area weights.
    Three-point quadrature also integrates convection's quadratic shape products.
    """
    parents=[];barys=[];weights=[]
    eps=max(float(np.max(np.abs(triangles)))*1e-7,1e-10) if np.size(triangles) else 1e-10
    quadrature=np.array([[2/3,1/6,1/6],[1/6,2/3,1/6],[1/6,1/6,2/3]])
    lo=np.array(box['min_m']) if box else np.full(3,-np.inf)
    hi=np.array(box['max_m']) if box else np.full(3,np.inf)
    candidates=np.flatnonzero(np.all(triangles.max(axis=1)>=lo-eps,axis=1)&np.all(triangles.min(axis=1)<=hi+eps,axis=1))
    for parent in candidates:
        tri=triangles[parent];poly=[(tri[i],np.eye(3)[i]) for i in range(3)]
        if box:
            for axis in range(3):
                for edge,sign in ((lo[axis],1),(hi[axis],-1)):
                    new=[]
                    for i,(p,b) in enumerate(poly):
                        q,c=poly[(i+1)%len(poly)];inside=(p[axis]-edge)*sign>=-eps;next_inside=(q[axis]-edge)*sign>=-eps
                        if inside:new.append((p,b))
                        if inside!=next_inside:
                            t=(edge-p[axis])/(q[axis]-p[axis]);new.append((p+t*(q-p),b+t*(c-b)))
                    poly=new
                    if not poly:break
                if not poly:break
        for i in range(1,len(poly)-1):
            p=np.array([poly[j][0] for j in (0,i,i+1)]);b=np.array([poly[j][1] for j in (0,i,i+1)])
            area=np.linalg.norm(np.cross(p[1]-p[0],p[2]-p[0]))/2
            if area>1e-20:
                parents.extend([parent]*3);barys.extend(quadrature@b);weights.extend([area/3]*3)
    return np.asarray(parents,int),np.asarray(barys,float).reshape(-1,3),np.asarray(weights,float)


def update_case_parameters(engine,model_id,prompt,cfg,questions):
    from thermal_legacy.agent_config_rules import value
    from thermal_legacy.agent_rules import clauses
    # Parentheses protect coordinate lists from ordinary comma clause splitting.
    name=re.search(r'算例名称\s*(?:设为|为)?\s*[“"]([^”"]+)[”"]',prompt)
    if name:cfg['name']=name[1]
    if re.search(r'清除续算|取消续算|清除旧结果绑定',prompt):cfg['initial_from_job']=None
    match=re.search(r'从算例\s*[“"]([a-zA-Z0-9_-]+)[”"]\s*(?:的末帧)?(?:温度场)?续算',prompt)
    if match:cfg['initial_from_job']=match[1]
    if '清空环境温度曲线' in prompt:cfg['ambient_profile']=[]
    if '环境温度曲线' in prompt and '清空环境温度曲线' not in prompt:
        curve_text=prompt.split('环境温度曲线',1)[1].split('。',1)[0]
        pairs=re.findall('('+NUMBER+r')\s*秒\s*[:：]\s*('+NUMBER+r')\s*(?:摄氏度|°C|℃)',curve_text)
        if pairs:cfg['ambient_profile']=[{'time_s':float(t),'ambient_C':float(c)} for t,c in pairs]
        else:questions.append('环境温度曲线请写成“0秒:20摄氏度，1800秒:负40摄氏度”等采样点。')
    if '清除接触面评估' in prompt:cfg['surface_evaluation']=None
    # Explicit evaluation box may be independent of the heater (unpowered cycles).
    for line in prompt.split('。'):
        if '评估接触面' not in line:continue
        box=box_from_text(line)
        if box:
            selection=engine._surface_selections(model_id,[box_token(box)])[box_token(box)]
            if not selection['faces']:questions.append('接触面评估选区为空');continue
            cfg['surface_evaluation']=dict(name='模块接触面',faces=selection['faces'],surface_box=box)
    evaluation=cfg.get('surface_evaluation')
    if evaluation is not None:
        for key,alias,kind in [('maximum_C','接触面温度限值','temperature'),('flatness_limit_m','接触面翘曲限值','length'),('reference_power_W','热阻参考功率','power')]:
            n=value(prompt,alias,kind)
            if n is not None:evaluation[key]=n


def surface_metrics(mesh,T,U,times,cfg,display):
    setting=cfg.get('surface_evaluation')
    if not setting:return None
    from thermal_legacy.solver import project
    import trimesh
    boundary=mesh['boundary_triangles'];tri=mesh['points'][boundary]
    surface=trimesh.Trimesh(vertices=display['points'],faces=display['faces'],process=False)
    _,_,ids=project(surface,tri.mean(axis=1));selected=np.zeros(len(display['faces']),bool);selected[setting['faces']]=True
    candidates=np.arange(len(tri)) if setting.get('surface_box') else np.flatnonzero(selected[ids]);parent,bary,weight=clipped_quadrature(tri[candidates],setting.get('surface_box'))
    if not len(parent):raise ValueError('接触面在体网格上为空')
    cells=candidates[parent];nodes=boundary[cells];xyz=np.einsum('qij,qi->qj',mesh['points'][nodes],bary)
    area=weight.sum();mean=(xyz*weight[:,None]).sum(axis=0)/area
    # The plane is fitted with area weights, removing rigid translation/tilt.
    _,_,vh=np.linalg.svd((xyz-mean)*np.sqrt(weight[:,None]/area),full_matrices=False);normal=vh[-1]
    basis=vh[:2].T;xy=(xyz-mean)@basis;A=np.column_stack([xy,np.ones(len(xyz))]);Aw=A*np.sqrt(weight[:,None]/area)
    rows=[];power=setting.get('reference_power_W',0)
    for f,t in enumerate(times):
        values=np.einsum('qi,qi->q',T[f,nodes],bary)
        # Include clipped polygon vertices using limits of quadratic quadrature.
        # Each integration triangle's vertices are recovered from its 3 Gauss points.
        temps=2*values.reshape(-1,3)-values.reshape(-1,3).mean(axis=1)[:,None]
        temperature_min=float(temps.min());temperature_max=float(temps.max())
        average=float(weight@values/area)
        ambient=float(np.interp(t,[p['time_s'] for p in cfg['ambient_profile']],[p['ambient_C'] for p in cfg['ambient_profile']])) if cfg.get('ambient_profile') else cfg['ambient_C']
        row=dict(time_s=float(t),minimum_C=temperature_min,maximum_C=temperature_max,average_C=average,delta_C=temperature_max-temperature_min,
                 thermal_resistance_K_W=(average-ambient)/power if power else None)
        if U is not None:
            moved=xyz+np.einsum('qic,qi->qc',U[f,nodes],bary)
            moved_mean=(moved*weight[:,None]).sum(axis=0)/area
            _,_,fitted=np.linalg.svd((moved-moved_mean)*np.sqrt(weight[:,None]/area),full_matrices=False)
            residual=(moved-moved_mean)@fitted[-1]
            vertices=2*residual.reshape(-1,3)-residual.reshape(-1,3).mean(axis=1)[:,None]
            row['flatness_m']=float(np.ptp(vertices))
        rows.append(row)
    limit=setting.get('maximum_C');crossing=None
    for i,row in enumerate(rows):
        if limit is not None and row['maximum_C']>=limit:
            crossing={'bracket_s':[rows[max(0,i-1)]['time_s'],row['time_s']]}
            if i and row['maximum_C']!=rows[i-1]['maximum_C']:
                prev=rows[i-1];crossing['linear_estimate_s']=prev['time_s']+(limit-prev['maximum_C'])/(row['maximum_C']-prev['maximum_C'])*(row['time_s']-prev['time_s'])
            else:crossing['linear_estimate_s']=row['time_s']
            break
    return dict(name=setting['name'],area_m2=float(area),rows=rows,maximum_C=max(r['maximum_C'] for r in rows),
        maximum_flatness_m=max((r.get('flatness_m',0) for r in rows)) if U is not None else None,
        temperature_limit_C=limit,flatness_limit_m=setting.get('flatness_limit_m'),first_limit_crossing=crossing,
        method='P1 clipped-surface integration; area-weighted least-squares deformed plane; normal residual peak-to-valley')
